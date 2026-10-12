"""Datos observados en campo: lectura, limpieza y alineación temporal.

Formatos admitidos
------------------
* ``long`` (recomendado): columnas ``sensor, type, element, timestamp, value``
  (``type`` = PRESSURE | HEAD | FLOW | LEVEL; ``element`` = ID del nodo o línea
  del INP; si falta ``element`` se usa ``sensor``).
* ``wide``: columna ``timestamp`` + una columna por sensor, más un fichero
  ``sensors_file`` con columnas ``sensor, type, element[, weight, sigma, abs]``.

Opcionalmente ``sensors_file`` también puede acompañar al formato largo para
fijar pesos/incertidumbres por sensor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

NODE_KINDS = {"PRESSURE", "HEAD", "LEVEL"}
LINK_KINDS = {"FLOW", "VELOCITY"}
ALIASES = {"PRESION": "PRESSURE", "PRESIÓN": "PRESSURE", "CAUDAL": "FLOW",
           "NIVEL": "LEVEL", "ALTURA": "HEAD", "CARGA": "HEAD", "VELOCIDAD": "VELOCITY"}


@dataclass
class Sensor:
    sensor_id: str
    kind: str            # PRESSURE / HEAD / LEVEL / FLOW / VELOCITY
    element: str         # ID en el INP
    weight: float = 1.0
    sigma: Optional[float] = None   # incertidumbre de la medida (unidades del sensor)
    use_abs: bool = True            # caudal: comparar en valor absoluto
    index: int = 0                  # índice EPANET (1-based), se rellena luego

    @property
    def is_node(self) -> bool:
        return self.kind in NODE_KINDS


@dataclass
class Observations:
    sensors: List[Sensor]
    times: np.ndarray            # s desde el inicio de la simulación
    values: np.ndarray           # (n_sensores, n_tiempos) con NaN
    start: pd.Timestamp
    step: int
    calib_mask: np.ndarray       # (n_tiempos,) bool: True = calibración
    n_outliers: Dict[str, int] = field(default_factory=dict)

    @property
    def kinds(self) -> List[str]:
        return sorted({s.kind for s in self.sensors})

    def rows_of(self, kind: str) -> np.ndarray:
        return np.array([i for i, s in enumerate(self.sensors) if s.kind == kind], int)

    def timestamps(self) -> pd.DatetimeIndex:
        return self.start + pd.to_timedelta(self.times, unit="s")


def _norm_kind(k: str) -> str:
    k = str(k).strip().upper()
    return ALIASES.get(k, k)


def hampel(x: np.ndarray, window: int = 7, n_sigma: float = 3.0) -> np.ndarray:
    """Máscara de valores atípicos (filtro de Hampel con mediana móvil).

    La dispersión de referencia es la mayor entre la MAD local y la MAD global de
    los residuos: así los escalones legítimos (cambios de patrón cada 15 min,
    maniobras) no se confunden con picos espurios de los registradores."""
    s = pd.Series(x)
    med = s.rolling(2 * window + 1, center=True, min_periods=1).median()
    res = (s - med).abs()
    mad_loc = res.rolling(2 * window + 1, center=True, min_periods=1).median()
    mad_glob = float(np.nanmedian(res))
    thr = n_sigma * 1.4826 * np.maximum(mad_loc, mad_glob) * 2.0
    out = (res > thr) & (thr > 0)
    return out.to_numpy()


def _read_sensor_meta(path: Optional[str]) -> Dict[str, dict]:
    if not path:
        return {}
    df = pd.read_csv(path, sep=None, engine="python")
    df.columns = [c.strip().lower() for c in df.columns]
    meta = {}
    for _, r in df.iterrows():
        meta[str(r["sensor"]).strip()] = {k: r[k] for k in df.columns if k != "sensor"
                                          and not (isinstance(r[k], float) and np.isnan(r[k]))}
    return meta


def load_observations(cfg: dict, base_dir: str = ".") -> Observations:
    import os

    def p(x):
        return x if (x is None or os.path.isabs(x)) else os.path.join(base_dir, x)

    fmt = cfg.get("format", "long").lower()
    tsfmt = cfg.get("timestamp_format")
    meta = _read_sensor_meta(p(cfg.get("sensors_file")))

    if "files" in cfg:
        # varias fuentes, p. ej. ficheros de calibración de EPANET (.dat):
        #   files: [{file: caudal.dat, type: FLOW}, {file: presion.dat, type: PRESSURE}]
        # formato .dat: "id  hora  valor" (separado por espacios/tabuladores;
        # una línea con sólo el id inicia un bloque, como en EPANET)
        start0 = pd.Timestamp(cfg.get("start", "2000-01-01 00:00:00"))
        parts = []
        for src in cfg["files"]:
            kind = _norm_kind(src.get("type", "PRESSURE"))
            rows, cur = [], None
            with open(p(src["file"]), "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.split(";", 1)[0].strip()
                    if not line:
                        continue
                    tok = line.split()
                    try:
                        if len(tok) >= 3:
                            cur = tok[0]
                            h, v = float(tok[1].replace(",", ".")), float(tok[2].replace(",", "."))
                        elif len(tok) == 2 and cur is not None:
                            h, v = float(tok[0].replace(",", ".")), float(tok[1].replace(",", "."))
                        else:
                            cur = tok[0]
                            continue
                    except ValueError:
                        continue          # cabeceras
                    rows.append((cur, kind, cur, start0 + pd.Timedelta(hours=h), v))
            parts.append(pd.DataFrame(rows, columns=["sensor", "type", "element",
                                                     "timestamp", "value"]))
        df = pd.concat(parts, ignore_index=True)
        cfg = dict(cfg, start=str(start0))
        fmt = "_frame"
    if fmt in ("long", "_frame"):
        if fmt == "long":
            df = pd.read_csv(p(cfg["file"]), sep=None, engine="python")
        df.columns = [c.strip().lower() for c in df.columns]
        if "element" not in df.columns:
            df["element"] = df["sensor"]
        if "type" not in df.columns:
            df["type"] = df["sensor"].map(lambda s: meta.get(str(s), {}).get("type", "PRESSURE"))
        df["timestamp"] = pd.to_datetime(df["timestamp"], format=tsfmt)
        df["sensor"] = df["sensor"].astype(str).str.strip()
        df["element"] = df["element"].astype(str).str.strip()
        df["type"] = df["type"].map(_norm_kind)
        df = df.dropna(subset=["value"])
        sensor_defs = (df[["sensor", "type", "element"]].drop_duplicates("sensor")
                       .set_index("sensor").to_dict("index"))
        series = {s: g.set_index("timestamp")["value"].astype(float).sort_index()
                  for s, g in df.groupby("sensor")}
    elif fmt == "wide":
        df = pd.read_csv(p(cfg["file"]), sep=None, engine="python")
        tcol = cfg.get("timestamp_column", df.columns[0])
        df[tcol] = pd.to_datetime(df[tcol], format=tsfmt)
        df = df.set_index(tcol).sort_index()
        sensor_defs, series = {}, {}
        for c in df.columns:
            sid = str(c).strip()
            m = meta.get(sid, {})
            if not m:
                raise ValueError(f"Sensor '{sid}' sin metadatos en sensors_file")
            sensor_defs[sid] = {"type": _norm_kind(m.get("type", "PRESSURE")),
                                "element": str(m.get("element", sid)).strip()}
            series[sid] = pd.to_numeric(df[c], errors="coerce").dropna()
    else:
        raise ValueError(f"Formato de observaciones desconocido: {fmt}")

    if "start" in cfg:
        start = pd.Timestamp(cfg["start"])
    else:
        start = min(s.index.min() for s in series.values()).floor("h")
    if start.tzinfo is not None:
        start = start.tz_convert(None)
    end = pd.Timestamp(cfg["end"]) if "end" in cfg else max(s.index.max() for s in series.values())
    step = int(cfg.get("step", 900))

    # filtro de atípicos + remuestreo a la malla de simulación
    filt = cfg.get("outlier_filter", {"method": "hampel", "window": 7, "n_sigma": 3.5})
    grid = pd.date_range(start, end, freq=f"{step}s")
    sensors, rows, n_out = [], [], {}
    for sid, s in series.items():
        if s.index.tz is not None:
            s = s.tz_convert(None)
        s = s[(s.index >= start - pd.Timedelta(seconds=step)) &
              (s.index <= end + pd.Timedelta(seconds=step))]
        if filt and filt.get("method", "none") == "hampel" and len(s) > 5:
            bad = hampel(s.to_numpy(), int(filt.get("window", 7)), float(filt.get("n_sigma", 3.5)))
            n_out[sid] = int(bad.sum())
            s = s[~bad]
        # media por intervalo centrado en cada instante de la malla
        binned = s.groupby(((s.index - start) / pd.Timedelta(seconds=step)).round().astype(int)).mean()
        vals = np.full(len(grid), np.nan)
        for k, v in binned.items():
            if 0 <= k < len(grid):
                vals[k] = v
        d = sensor_defs[sid]
        m = meta.get(sid, {})
        kind = _norm_kind(d["type"])
        sensors.append(Sensor(
            sensor_id=sid, kind=kind, element=str(d["element"]),
            weight=float(m.get("weight", 1.0)),
            sigma=float(m["sigma"]) if "sigma" in m else None,
            use_abs=bool(int(m["abs"])) if "abs" in m else kind == "FLOW"))
        rows.append(vals)
    values = np.vstack(rows) * 1.0
    # factores de conversión de unidades opcionales por tipo
    for kind, fac in (cfg.get("unit_factors") or {}).items():
        values[[i for i, s in enumerate(sensors) if s.kind == _norm_kind(kind)]] *= float(fac)

    times = ((grid - start) / pd.Timedelta(seconds=1)).astype(int).to_numpy()
    calib = np.ones(len(times), bool)
    if "validation_start" in cfg:
        calib = grid < pd.Timestamp(cfg["validation_start"])
    elif float(cfg.get("validation_fraction", 0)) > 0:
        ncal = int(round(len(times) * (1 - float(cfg["validation_fraction"]))))
        calib[ncal:] = False
    warmup = int(cfg.get("warmup", 0))  # s iniciales descartados (estabilización)
    keep = times >= warmup
    return Observations(sensors=sensors, times=times[keep], values=values[:, keep],
                        start=start, step=step, calib_mask=calib[keep], n_outliers=n_out)
