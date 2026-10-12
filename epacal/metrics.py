"""Métricas de bondad de ajuste y reglas de aceptación (estilo Giswater, ampliadas)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np


def _valid(obs, sim):
    m = np.isfinite(obs) & np.isfinite(sim)
    return obs[m], sim[m]


def series_metrics(obs: np.ndarray, sim: np.ndarray) -> Dict[str, float]:
    o, s = _valid(np.asarray(obs, float), np.asarray(sim, float))
    if len(o) == 0:
        return {k: np.nan for k in ("n", "rmse", "mae", "bias", "rme_pct", "nse", "kge",
                                     "r2", "max_abs")}
    e = s - o
    rmse = float(np.sqrt(np.mean(e ** 2)))
    var = np.var(o)
    nse = float(1 - np.sum(e ** 2) / (np.sum((o - o.mean()) ** 2))) if var > 0 else np.nan
    so, ss = np.std(o), np.std(s)
    r = float(np.corrcoef(o, s)[0, 1]) if so > 0 and ss > 0 else np.nan
    if np.isfinite(r) and o.mean() != 0:
        kge = 1 - np.sqrt((r - 1) ** 2 + (ss / so - 1) ** 2 + (s.mean() / o.mean() - 1) ** 2)
    else:
        kge = np.nan
    denom = np.where(np.abs(o) > 1e-9, np.abs(o), np.nan)
    return {
        "n": int(len(o)),
        "rmse": rmse,
        "mae": float(np.mean(np.abs(e))),
        "bias": float(np.mean(e)),
        "rme_pct": float(np.nanmean(np.abs(e) / denom) * 100),
        "nse": nse,
        "kge": float(kge),
        "r2": float(r ** 2) if np.isfinite(r) else np.nan,
        "max_abs": float(np.max(np.abs(e))),
    }


def huber(e: np.ndarray, delta: float) -> np.ndarray:
    a = np.abs(e)
    return np.where(a <= delta, 0.5 * e ** 2, delta * (a - 0.5 * delta))


def pooled_objective(obs: np.ndarray, sim: np.ndarray, weights: np.ndarray,
                     metric: str = "rmse", sigma: Optional[np.ndarray] = None,
                     huber_delta: float = 1.0) -> float:
    """Objetivo agregado de un tipo de sensor (filas = sensores)."""
    e = sim - obs
    if sigma is not None:
        e = e / sigma[:, None]
    w = np.broadcast_to(weights[:, None], e.shape)
    m = np.isfinite(e)
    if not m.any():
        return np.nan
    e, w = e[m], w[m]
    if metric == "rmse":
        return float(np.sqrt(np.sum(w * e ** 2) / np.sum(w)))
    if metric == "mae":
        return float(np.sum(w * np.abs(e)) / np.sum(w))
    if metric == "huber":
        return float(np.sum(w * huber(e, huber_delta)) / np.sum(w))
    if metric == "rme":
        o = obs[m] if sigma is None else (obs / sigma[:, None])[m]
        d = np.where(np.abs(o) > 1e-9, np.abs(o), np.nan)
        return float(np.nansum(w * np.abs(e) / d) / np.sum(w) * 100)
    raise ValueError(f"Métrica desconocida: {metric}")


@dataclass
class RuleResult:
    name: str
    kind: str
    error: str
    threshold: float
    timesteps_pct: float
    points_pct: float
    points_ok: int
    points_total: int
    obs_ok: int
    obs_total: int
    passed: bool
    per_sensor: Dict[str, float]   # % de instantes dentro de tolerancia


def evaluate_rules(rules: List[dict], sensors, obs: np.ndarray, sim: np.ndarray,
                   mask: Optional[np.ndarray] = None) -> List[RuleResult]:
    """Regla: un sensor (punto) es válido si >= timesteps_pct % de sus instantes
    tienen error <= threshold; la regla pasa si >= points_pct % de los puntos lo son."""
    out = []
    for r in rules:
        if not r.get("active", True):
            continue
        kind = r["kind"].upper()
        kind = {"CAUDAL": "FLOW", "PRESION": "PRESSURE"}.get(kind, kind)
        rows = [i for i, s in enumerate(sensors) if s.kind == kind]
        rel = r.get("error", "absolute").lower().startswith("rel")
        thr = float(r["threshold"])
        ok_pts, ok_obs, tot_obs, per = 0, 0, 0, {}
        for i in rows:
            o, s = obs[i], sim[i]
            m = np.isfinite(o) & np.isfinite(s)
            if mask is not None:
                m &= mask
            if not m.any():
                continue
            e = np.abs(s[m] - o[m])
            if rel:
                e = e / np.maximum(np.abs(o[m]), 1e-9) * 100
            good = e <= thr
            pct = 100.0 * good.mean()
            per[sensors[i].sensor_id] = pct
            ok_obs += int(good.sum())
            tot_obs += int(m.sum())
            if pct >= float(r.get("timesteps_pct", 0)):
                ok_pts += 1
        n_pts = len(per)
        passed = n_pts > 0 and 100.0 * ok_pts / n_pts >= float(r.get("points_pct", 100))
        out.append(RuleResult(r.get("name", kind), kind, "RELATIVE" if rel else "ABSOLUTE",
                              thr, float(r.get("timesteps_pct", 0)),
                              float(r.get("points_pct", 100)), ok_pts, n_pts, ok_obs,
                              tot_obs, passed, per))
    return out


def rules_score(results: List[RuleResult]) -> float:
    """Fracción media de cumplimiento (0..1) — usada para desempatar soluciones."""
    if not results:
        return 1.0
    return float(np.mean([r.obs_ok / max(r.obs_total, 1) for r in results]))
