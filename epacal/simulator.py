"""Evaluador: aplica un vector de parámetros, simula y compara con lo observado.

Se diseña para ejecutarse en paralelo: cada proceso trabajador abre su propia
copia del modelo EPANET (``init_worker``) y reutiliza el proyecto en memoria.
"""
from __future__ import annotations

import math
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import numpy as np

from . import engine as en
from .metrics import pooled_objective
from .observations import Observations
from .parameters import Parameter, apply_parameters

PENALTY = 1e6
LEVEL_PROP = -1        # marcador interno: nivel de tanque calculado como H − cota


@dataclass
class EvalSpec:
    """Todo lo necesario (y serializable) para reconstruir un evaluador."""
    inp_file: str
    params: List[Parameter]
    obs: Observations
    objectives: List[str]           # tipos de sensor que son objetivos (FLOW, PRESSURE…)
    metric: str = "rmse"
    huber_delta: float = 1.0
    lib_path: Optional[str] = None
    kind_scale: Optional[Dict[str, float]] = None   # escala para residuos de LM
    kind_weight: Optional[Dict[str, float]] = None
    sim_offset: int = 0             # s entre t=0 del INP y el inicio de las observaciones
    balance: Optional[object] = None  # LossBalance: mantiene el objetivo de pérdidas
    rules: Optional[list] = None      # reglas de aceptación (para el modo goal: rules)


class Evaluator:
    def __init__(self, spec: EvalSpec):
        self.spec = spec
        self.prj = en.EpanetProject(spec.inp_file, spec.lib_path)
        obs = spec.obs
        self.obs = obs
        # índices EPANET de los sensores
        self.getters = []
        for s in obs.sensors:
            if s.is_node:
                idx = self.prj.node_index(s.element)
                s.index = idx
                if s.kind == "HEAD":
                    prop = en.EN_HEAD
                elif s.kind == "LEVEL" and self.prj.node_type(idx) == en.EN_TANK:
                    # nivel = cota piezométrica − cota de solera (EN_TANKLEVEL devuelve el
                    # nivel INICIAL durante la simulación, tanto en EPANET 2.2 como en 2.3)
                    prop = LEVEL_PROP
                else:
                    prop = en.EN_PRESSURE
                self.getters.append((True, idx, prop, False))
            else:
                idx = self.prj.link_index(s.element)
                s.index = idx
                prop = en.EN_VELOCITY if s.kind == "VELOCITY" else en.EN_FLOW
                self.getters.append((False, idx, prop, s.use_abs))
        # malla temporal (se guardan los tiempos originales para escribir el INP)
        self._orig_times = {p: self.prj.get_time(p) for p in
                            (en.EN_DURATION, en.EN_HYDSTEP, en.EN_REPORTSTEP, en.EN_REPORTSTART)}
        step = obs.step
        hyd = self.prj.get_time(en.EN_HYDSTEP)
        hyd = math.gcd(hyd, step) if hyd > 0 else step
        self.prj.set_time(en.EN_HYDSTEP, hyd)
        self.prj.set_time(en.EN_REPORTSTEP, step)
        self.prj.set_time(en.EN_REPORTSTART, 0)
        dur = int(obs.times[-1]) + spec.sim_offset
        self.prj.set_time(en.EN_DURATION, dur)
        self.t_index = {int(t) + spec.sim_offset: k for k, t in enumerate(obs.times)}
        self.record_step = math.gcd(step, spec.sim_offset) if spec.sim_offset else step
        self.kind_rows = {k: obs.rows_of(k) for k in obs.kinds}
        self.weights = np.array([s.weight for s in obs.sensors])
        sig = [s.sigma for s in obs.sensors]
        self.sigma = np.array([np.nan if v is None else v for v in sig])

        if spec.balance is not None:
            spec.balance.bind(self.prj)

    def close(self):
        self.prj.close()

    def save_inp(self, path: str) -> None:
        """Guarda el estado actual (parámetros, emisores y patrones re-equilibrados)
        con los tiempos originales del INP."""
        cur = {p: self.prj.get_time(p) for p in self._orig_times}
        for p, v in self._orig_times.items():
            self.prj.set_time(p, v)
        self.prj.save_inp(path)
        for p, v in cur.items():
            self.prj.set_time(p, v)

    def _rebalance(self) -> bool:
        bal = self.spec.balance
        bal.reset(self.prj)
        ok = True
        for _ in range(bal.n_inner):
            buf = bal.new_leak_buffer()
            try:
                ok &= self.prj.run_eps(bal.step, lambda t: bal.record(self.prj, t, buf))
            except en.EpanetError:
                return False
            bal.update(self.prj, buf)
        return ok

    # ------------------------------------------------------------ simulación
    def simulate(self, x: Optional[Sequence[float]] = None, extra=None):
        """Devuelve (sim[n_sensores, n_tiempos], ok). ``extra(prj, t, k)`` permite
        recoger magnitudes adicionales en cada instante de la malla."""
        if x is not None:
            apply_parameters(self.prj, self.spec.params, np.asarray(x, float))
        if self.spec.balance is not None:
            self._rebalance()
        n_s, n_t = len(self.obs.sensors), len(self.obs.times)
        sim = np.full((n_s, n_t), np.nan)
        getters, prj, t_index = self.getters, self.prj, self.t_index

        def cb(t):
            k = t_index.get(t)
            if k is None:
                return
            for j, (is_node, idx, prop, use_abs) in enumerate(getters):
                if prop == LEVEL_PROP:
                    v = prj.get_node(idx, en.EN_HEAD) - prj.get_node(idx, en.EN_ELEVATION)
                else:
                    v = prj.get_node(idx, prop) if is_node else prj.get_link(idx, prop)
                sim[j, k] = abs(v) if use_abs else v
            if extra is not None:
                extra(prj, t, k)

        try:
            ok = prj.run_eps(self.record_step, cb)
        except en.EpanetError:
            return sim, False
        return sim, ok

    # -------------------------------------------------------------- objetivos
    def objectives(self, sim: np.ndarray, mask: Optional[np.ndarray] = None) -> np.ndarray:
        mask = self.obs.calib_mask if mask is None else mask
        out = []
        for kind in self.spec.objectives:
            rows = self.kind_rows.get(kind, np.array([], int))
            if len(rows) == 0:
                out.append(0.0)
                continue
            o = self.obs.values[rows][:, mask]
            s = sim[rows][:, mask]
            sg = self.sigma[rows]
            sg = None if np.isnan(sg).any() else sg
            v = pooled_objective(o, s, self.weights[rows], self.spec.metric, sg,
                                 self.spec.huber_delta)
            out.append(PENALTY if not np.isfinite(v) else v)
        return np.array(out)

    def rules_status(self, sim: np.ndarray) -> tuple:
        """(reglas cumplidas, total, cumplimiento medio) en el periodo de calibración."""
        from .metrics import evaluate_rules, rules_score
        rr = evaluate_rules(self.spec.rules or [], self.obs.sensors, self.obs.values, sim,
                            self.obs.calib_mask)
        return sum(r.passed for r in rr), len(rr), rules_score(rr)

    def residuals(self, sim: np.ndarray, x: Optional[np.ndarray] = None) -> np.ndarray:
        """Vector de residuos escalados para mínimos cuadrados (Levenberg-Marquardt).
        sum(r^2) = sum_k  w_k * (RMSE_k / escala_k)^2  (+ regularización)."""
        mask = self.obs.calib_mask
        parts = []
        for kind in self.spec.objectives:
            rows = self.kind_rows.get(kind, np.array([], int))
            if len(rows) == 0:
                continue
            o = self.obs.values[rows][:, mask]
            s = sim[rows][:, mask]
            e = s - o
            sg = self.sigma[rows]
            if not np.isnan(sg).any():
                e = e / sg[:, None]
                scale = 1.0
            else:
                scale = (self.spec.kind_scale or {}).get(kind, 1.0)
            w = np.broadcast_to(self.weights[rows][:, None], e.shape)
            valid = np.isfinite(o)
            n = max(valid.sum(), 1)
            kw = (self.spec.kind_weight or {}).get(kind, 1.0)
            r = np.where(valid, e, 0.0) * np.sqrt(w * kw / n) / scale
            r = np.where(np.isfinite(r), r, PENALTY ** 0.5 / np.sqrt(n))
            parts.append(r[valid].ravel())
        if x is not None:
            reg = [np.sqrt(p.prior_weight) * (p.to_unit(v) - p.to_unit(p.initial))
                   for p, v in zip(self.spec.params, x) if p.prior_weight > 0]
            if reg:
                parts.append(np.array(reg))
        return np.concatenate(parts) if parts else np.zeros(1)


# --------------------------------------------------------------- paralelismo
_WORKER: Optional[Evaluator] = None


def init_worker(spec: EvalSpec):
    global _WORKER
    _WORKER = Evaluator(spec)


def _worker_eval(args):
    x, want = args
    sim, ok = _WORKER.simulate(x)
    out = {"ok": ok}
    if "objectives" in want:
        out["objectives"] = _WORKER.objectives(sim)
    if "residuals" in want:
        out["residuals"] = _WORKER.residuals(sim, np.asarray(x))
    if "sim" in want:
        out["sim"] = sim
    if "rules" in want:
        out["rules"] = _WORKER.rules_status(sim)
    return out


class Pool:
    """Ejecuta evaluaciones en serie o en paralelo con la misma interfaz."""

    def __init__(self, spec: EvalSpec, n_workers: int = 0):
        self.spec = spec
        if n_workers is None or n_workers <= 0:
            n_workers = max(1, (os.cpu_count() or 2) - 1)
        self.n_workers = n_workers
        self.local = Evaluator(spec)
        self.exe = None
        if n_workers > 1:
            self.exe = ProcessPoolExecutor(max_workers=n_workers, initializer=init_worker,
                                           initargs=(spec,))
        self.n_evals = 0

    def map(self, X: Sequence[Sequence[float]], want=("objectives",)) -> List[dict]:
        X = [np.asarray(x, float) for x in X]
        self.n_evals += len(X)
        if self.exe is None or len(X) == 1:
            res = []
            for x in X:
                sim, ok = self.local.simulate(x)
                d = {"ok": ok}
                if "objectives" in want:
                    d["objectives"] = self.local.objectives(sim)
                if "residuals" in want:
                    d["residuals"] = self.local.residuals(sim, x)
                if "sim" in want:
                    d["sim"] = sim
                if "rules" in want:
                    d["rules"] = self.local.rules_status(sim)
                res.append(d)
            return res
        chunk = max(1, len(X) // (self.n_workers * 4))
        return list(self.exe.map(_worker_eval, [(x, tuple(want)) for x in X], chunksize=chunk))

    def close(self):
        if self.exe is not None:
            self.exe.shutdown()
        self.local.close()
