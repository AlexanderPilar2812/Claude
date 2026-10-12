"""Definición de parámetros de calibración (variables de decisión).

Cada parámetro actúa sobre una familia de elementos:

    type                  elemento  efecto
    --------------------  --------  --------------------------------------------
    roughness             línea     rugosidad (absoluta o multiplicador)
    valve_setting         línea     consigna inicial de válvula (TCV: coef. K)
    minor_loss            línea     coeficiente de pérdidas menores
    leak_area             línea     área de fuga FAVAD (EPANET 2.3)
    demand_multiplier     nodo      multiplica las demandas base
    emitter_multiplier    nodo      multiplica los coeficientes de emisor
    reservoir_head        nodo      desplaza el nivel del embalse (offset, m)
    elevation_offset      nodo      desplaza la cota (corrige cotas de sensores)

``mode``: ``absolute`` (el valor se asigna tal cual) o ``multiplier`` (valor
original × x) u ``offset`` (original + x).  ``log: true`` explora el rango en
escala logarítmica (recomendado para rugosidad Darcy-Weisbach).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import numpy as np

from . import engine as en
from .network import NetworkInfo

LINK_PARAMS = {"roughness": en.EN_ROUGHNESS, "valve_setting": en.EN_INITSETTING,
               "minor_loss": en.EN_MINORLOSS, "leak_area": en.EN_LEAK_AREA}
NODE_PARAMS = {"demand_multiplier": None, "emitter_multiplier": en.EN_EMITTER,
               "reservoir_head": en.EN_ELEVATION, "elevation_offset": en.EN_ELEVATION}
DEFAULT_MODE = {"roughness": "absolute", "valve_setting": "absolute",
                "minor_loss": "absolute", "leak_area": "absolute",
                "demand_multiplier": "multiplier", "emitter_multiplier": "multiplier",
                "reservoir_head": "offset", "elevation_offset": "offset"}


@dataclass
class Parameter:
    name: str
    ptype: str
    mode: str
    lower: float
    upper: float
    log: bool
    elements: np.ndarray          # índices 0-based
    base: np.ndarray              # valores originales de esos elementos
    initial: float                # valor inicial del parámetro
    prior_weight: float = 0.0     # regularización de Tikhonov hacia `initial`
    base_demands: list = field(default_factory=list)  # [(node, k, valor)]

    @property
    def is_link(self) -> bool:
        return self.ptype in LINK_PARAMS

    # ---- espacio normalizado [0,1] <-> físico
    def to_unit(self, v: float) -> float:
        if self.log:
            return (np.log(v) - np.log(self.lower)) / (np.log(self.upper) - np.log(self.lower))
        return (v - self.lower) / (self.upper - self.lower)

    def from_unit(self, u: float) -> float:
        u = min(max(u, 0.0), 1.0)
        if self.log:
            return float(np.exp(np.log(self.lower) + u * (np.log(self.upper) - np.log(self.lower))))
        return float(self.lower + u * (self.upper - self.lower))

    def element_values(self, x: float) -> np.ndarray:
        if self.mode == "absolute":
            return np.full(len(self.elements), x)
        if self.mode == "multiplier":
            return self.base * x
        if self.mode == "offset":
            return self.base + x
        raise ValueError(self.mode)


def build_parameters(cfgs: List[dict], groups: dict, net: NetworkInfo,
                     prj: en.EpanetProject) -> List[Parameter]:
    params: List[Parameter] = []
    for c in cfgs:
        ptype = c["type"].lower()
        if ptype not in LINK_PARAMS and ptype not in NODE_PARAMS:
            raise ValueError(f"Tipo de parámetro desconocido: {ptype}")
        gname = c.get("group")
        crit = c.get("select") or (groups.get(gname, {}) or {}).get("select", groups.get(gname))
        if crit is None:
            raise ValueError(f"Grupo '{gname}' no definido")
        if ptype in LINK_PARAMS:
            idx = net.select_links(crit)
            if ptype == "valve_setting":
                idx = idx[np.isin(net.link_type[idx], list(en.VALVE_TYPES))]
            elif ptype in ("roughness", "leak_area"):
                idx = idx[np.isin(net.link_type[idx], [en.EN_PIPE, en.EN_CVPIPE])]
            prop = LINK_PARAMS[ptype]
            base = np.array([prj.get_link(int(i) + 1, prop) for i in idx])
        else:
            idx = net.select_nodes(crit)
            if ptype == "reservoir_head":
                idx = idx[net.node_type[idx] == en.EN_RESERVOIR]
            elif ptype in ("demand_multiplier", "emitter_multiplier"):
                idx = idx[net.node_type[idx] == en.EN_JUNCTION]
            if ptype == "demand_multiplier":
                base = net.base_demand[idx]
            else:
                base = np.array([prj.get_node(int(i) + 1, NODE_PARAMS[ptype]) for i in idx])
        if len(idx) == 0:
            raise ValueError(f"El parámetro '{c.get('name', gname)}' no selecciona ningún elemento")
        mode = c.get("mode", DEFAULT_MODE[ptype]).lower()
        lo, hi = (float(v) for v in c["bounds"])
        if mode == "absolute":
            init = float(np.median(base))
        elif mode == "multiplier":
            init = 1.0
        else:
            init = 0.0
        init = float(c.get("initial", min(max(init, lo), hi)))
        log = bool(c.get("log", False))
        if log and lo <= 0:
            raise ValueError(f"Escala log requiere límite inferior > 0 ({gname})")
        p = Parameter(name=c.get("name", f"{gname}:{ptype}"), ptype=ptype, mode=mode,
                      lower=lo, upper=hi, log=log, elements=np.asarray(idx, int),
                      base=np.asarray(base, float), initial=init,
                      prior_weight=float(c.get("prior_weight", 0.0)))
        if ptype == "demand_multiplier":
            for i in idx:
                for k in range(1, prj.num_demands(int(i) + 1) + 1):
                    p.base_demands.append((int(i) + 1, k, prj.base_demand(int(i) + 1, k)))
        params.append(p)
    return params


def apply_parameters(prj: en.EpanetProject, params: List[Parameter], x: np.ndarray) -> None:
    """Escribe en el proyecto EPANET los valores físicos ``x``."""
    for p, v in zip(params, x):
        if p.ptype == "demand_multiplier":
            fac = v if p.mode == "multiplier" else 1.0
            for node, k, bd in p.base_demands:
                prj.set_base_demand(node, k, bd * fac)
            continue
        vals = p.element_values(v)
        if p.is_link:
            prop = LINK_PARAMS[p.ptype]
            for i, val in zip(p.elements, vals):
                prj.set_link(int(i) + 1, prop, float(val))
        else:
            prop = NODE_PARAMS[p.ptype]
            for i, val in zip(p.elements, vals):
                prj.set_node(int(i) + 1, prop, float(val))
