"""Balance de pérdidas embebido en la calibración hidráulica.

Problema: si las pérdidas se calibran con una rugosidad y luego la calibración
hidráulica cambia la rugosidad, cambian las presiones y, con ellas, las fugas
(q = K·p^γ). El modelo deja de cumplir el objetivo de pérdidas y el caudal de
entrada deja de cuadrar, y el optimizador acaba compensándolo con valores
falsos de rugosidad.

Solución: para CADA candidato de la calibración hidráulica se re-equilibran,
por sector, el factor de los emisores ``s_d`` y el patrón de consumo ``r_d(p)``
para que:
    * fugas / entrada = objetivo   (o MNF, caudal o volumen)
    * consumo + fugas = entrada    (en cada periodo del patrón)
Se hace con ``n_inner`` simulaciones de corrección antes de la simulación que
se compara con las observaciones. Las pérdidas son un *requisito* que todo
candidato cumple, no un paso previo que la calibración hidráulica deshace.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from . import engine as en
from .network import NetworkInfo


@dataclass
class DMABalance:
    name: str
    nodes: np.ndarray            # índices 1-based EPANET de las juntas del sector
    base_coef: np.ndarray        # coeficientes de emisor calibrados (0 = sin emisor)
    target_type: str
    target_value: float          # fracción (percent) o caudal/volumen
    night_mask: Optional[np.ndarray]
    inflow: np.ndarray           # caudal de entrada de referencia en la malla
    patterns: Dict[int, np.ndarray] = field(default_factory=dict)  # idx -> valores base
    base_by_pat: Dict[int, float] = field(default_factory=dict)    # idx -> suma de bases
    pat_order: List[int] = field(default_factory=list)              # columnas de B
    B: Optional[np.ndarray] = None   # demanda base por nudo y patrón (n_nodos × n_pat)


@dataclass
class LossBalance:
    dmas: List[DMABalance]
    step: int                    # s de la malla de balance
    times: np.ndarray
    dt: np.ndarray
    pstep: int
    pstart: int
    demand_mult: float
    f2m3: float
    n_inner: int = 2
    pda: Optional[tuple] = None   # (pmin, preq, pexp) si el modelo es PDA

    # ---------------------------------------------------------------- uso
    def reset(self, prj: en.EpanetProject) -> None:
        for d in self.dmas:
            for i, c in zip(d.nodes, d.base_coef):
                prj.set_node(int(i), en.EN_EMITTER, float(c))
            for pi, v in d.patterns.items():
                prj.set_pattern(pi, v)
        self._state = [{"s": 1.0, "r": {pi: np.ones(len(v)) for pi, v in d.patterns.items()}}
                       for d in self.dmas]

    def new_leak_buffer(self) -> np.ndarray:
        """Buffers (fugas, consumo servido) por sector e instante."""
        n = (len(self.dmas), len(self.times))
        return np.zeros(n), np.zeros(n)

    def _pat_values(self, j: int, t: int) -> np.ndarray:
        d, st = self.dmas[j], self._state[j]
        per = (t + self.pstart) // self.pstep
        out = np.empty(len(d.pat_order))
        for c, pi in enumerate(d.pat_order):
            if pi == 0:
                out[c] = 1.0
            else:
                v = d.patterns[pi]
                q = per % len(v)
                out[c] = v[q] * st["r"][pi][q]
        return out

    def record(self, prj: en.EpanetProject, t: int, buf) -> None:
        if t % self.step:
            return
        k = t // self.step
        L, C = buf
        if k >= L.shape[1]:
            return
        gamma = self._gamma
        for j, d in enumerate(self.dmas):
            p = prj.node_values_at(en.EN_PRESSURE, d.nodes)
            c = d.base_coef * self._state[j]["s"]
            L[j, k] = float(np.sum(c * np.sign(p) * np.abs(p) ** gamma))
            req = (d.B @ self._pat_values(j, t)) * self.demand_mult
            if self.pda is not None:
                pmin, preq, pexp = self.pda
                f = np.clip((p - pmin) / max(preq - pmin, 1e-9), 0.0, 1.0) ** pexp
                req = req * f
            C[j, k] = float(req.sum())

    def requested(self, j: int) -> np.ndarray:
        d, st = self.dmas[j], self._state[j]
        per = (self.times + self.pstart) // self.pstep
        out = np.zeros(len(self.times))
        for pi, b in d.base_by_pat.items():
            if pi == 0:
                out += b
                continue
            v = d.patterns[pi] * st["r"][pi]
            out += b * v[(per % len(v)).astype(int)]
        return out * self.demand_mult

    def achieved(self, j: int, L: np.ndarray) -> float:
        d = self.dmas[j]
        if d.target_type == "percent":
            return float((L[j] * self.dt).sum() / max((d.inflow * self.dt).sum(), 1e-12))
        if d.target_type == "mnf":
            return float(L[j][d.night_mask].mean())
        if d.target_type == "flow":
            return float((L[j] * self.dt).sum() / self.dt.sum())
        return float((L[j] * self.dt).sum() * self.f2m3)

    def update(self, prj: en.EpanetProject, buf) -> None:
        """Corrige factor de emisores y patrón a partir de fugas y consumo servido."""
        L, Cs = buf
        per = ((self.times + self.pstart) // self.pstep).astype(int)
        for j, d in enumerate(self.dmas):
            st = self._state[j]
            a = self.achieved(j, L)
            fac = d.target_value / a if a > 0 else 1.0
            fac = min(max(fac, 0.33), 3.0)
            st["s"] *= fac
            Lnew = L[j] * fac            # fugas esperadas con el nuevo factor
            C = Cs[j]
            for pi, base in d.patterns.items():
                plen = len(base)
                idx = per % plen
                num = np.zeros(plen)
                den = np.zeros(plen)
                np.add.at(num, idx, np.maximum(d.inflow - Lnew, 0.0) * self.dt)
                np.add.at(den, idx, C * self.dt)
                ratio = np.where(den > 1e-12, num / np.maximum(den, 1e-12), 1.0)
                st["r"][pi] = st["r"][pi] * np.clip(ratio, 0.5, 2.0)
                prj.set_pattern(pi, base * st["r"][pi])
            for i, c in zip(d.nodes, d.base_coef):
                prj.set_node(int(i), en.EN_EMITTER, float(c * st["s"]))

    def bind(self, prj: en.EpanetProject) -> None:
        self._gamma = prj.get_option(en.EN_EMITEXPON)


def build_balance(prj: en.EpanetProject, net: NetworkInfo, losses_cfg: dict, duration: int,
                  step: int, reference_inp: Optional[str], lib: Optional[str],
                  n_inner: int = 2, cfg: Optional[dict] = None,
                  base_dir: str = ".") -> Optional[LossBalance]:
    """Construye el balance desde un INP ya calibrado en pérdidas."""
    from .losses import requested_demand, measured_inflow, _clock_hours, _in_window

    dma_cfg = losses_cfg.get("dmas") or {"ALL": {"target": losses_cfg.get("target", {})}}
    times = np.arange(0, duration + 1, step)
    dt = np.full(len(times), float(step))
    if len(times) > 1:
        dt[0] = dt[-1] = step / 2.0
    emit = prj.node_values(en.EN_EMITTER)
    groups, dmas = [], []
    clock0 = prj.get_time(en.EN_STARTTIME)
    hours = _clock_hours(times, clock0)
    for name, dc in dma_cfg.items():
        nodes0 = net.dma_nodes(name)
        if len(nodes0) == 0 or not (emit[nodes0] > 0).any():
            continue
        t = dict(dc.get("target", losses_cfg.get("target", {})))
        typ = t.get("type", "percent").lower()
        groups.append(net.dma_nodes(name))
        d = DMABalance(name=name, nodes=nodes0 + 1, base_coef=emit[nodes0], target_type=typ,
                       target_value=0.0, night_mask=None, inflow=np.zeros(len(times)))
        if typ == "mnf":
            d.night_mask = _in_window(hours, t.get("night_window", [2, 4]))
        rows = []
        for i in nodes0:
            row = {}
            for k in range(1, prj.num_demands(int(i) + 1) + 1):
                pi = prj.demand_pattern(int(i) + 1, k)
                bd = prj.base_demand(int(i) + 1, k)
                row[pi] = row.get(pi, 0.0) + bd
                d.base_by_pat[pi] = d.base_by_pat.get(pi, 0.0) + bd
                if pi and pi not in d.patterns:
                    d.patterns[pi] = prj.get_pattern(pi)
            rows.append(row)
        d.pat_order = sorted(d.base_by_pat)
        d.B = np.array([[r.get(pi, 0.0) for pi in d.pat_order] for r in rows])
        dmas.append((d, t))
    if not dmas:
        return None
    # caudal de entrada de referencia: demanda solicitada del modelo ORIGINAL
    if reference_inp:
        with en.EpanetProject(reference_inp, lib) as rp:
            infl = requested_demand(rp, groups, times, net.node_ids)
    else:
        infl = requested_demand(prj, groups, times)
    out = []
    for (d, t), q in zip(dmas, infl):
        meas = measured_inflow(cfg, dma_cfg[d.name], base_dir, times) if cfg else None
        if meas is not None:
            q = q.copy()
            q[np.isfinite(meas)] = meas[np.isfinite(meas)]
        d.inflow = q
        typ = d.target_type
        if typ == "percent":
            d.target_value = float(t["value"]) / 100.0
        elif typ == "mnf":
            mnf = float(t["mnf"]) if "mnf" in t else float(q[d.night_mask].mean())
            d.target_value = max(mnf - float(t.get("legit_night_use", 0.0)), 0.0)
        else:
            d.target_value = float(t["value"])
        out.append(d)
    model, pmin, preq, pexp = prj.demand_model()
    return LossBalance(dmas=out, step=step, times=times, dt=dt,
                       pda=(pmin, preq, pexp) if model == "PDA" else None,
                       pstep=prj.get_time(en.EN_PATTERNSTEP) or 3600,
                       pstart=prj.get_time(en.EN_PATTERNSTART),
                       demand_mult=prj.get_option(en.EN_DEMANDMULT),
                       f2m3=en.FLOW_TO_M3S[net.flow_units], n_inner=n_inner)
