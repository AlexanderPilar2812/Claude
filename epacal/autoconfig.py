"""Propuesta automática de configuración a partir del INP y de los datos de campo.

La usa la aplicación de escritorio: el usuario sólo elige ficheros y, si quiere,
retoca la tabla de parámetros y las reglas.
"""
from __future__ import annotations

import os
import re
from collections import Counter
from typing import List, Optional, Tuple

import numpy as np

from . import engine as en
from .network import NetworkInfo

HW, DW = 0, 1   # EN_HEADLOSSFORM: 0 = H-W, 1 = D-W, 2 = C-M

PRESETS = {
    "rapida":     {"goal": "rules",  "pop_size": 16, "n_gen": 15, "min_gens": 3, "stall_gens": 5},
    "pareto":     {"goal": "pareto", "pop_size": 24, "n_gen": 15, "stall_gens": 5, "max_front": 15},
    "exhaustiva": {"goal": "pareto", "pop_size": 40, "n_gen": 30, "stall_gens": 8, "max_front": 25},
}


def _material(cat: str) -> Optional[str]:
    m = re.match(r"^([A-Za-zÁÉÍÓÚÑáéíóúñ]+)", cat or "")
    return m.group(1).upper() if m else None


def read_dat_summary(path: str) -> Tuple[List[str], float]:
    """IDs y última hora de un fichero de calibración .dat (id hora valor)."""
    ids, hmax = [], 0.0
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            tok = line.split(";", 1)[0].split()
            if len(tok) >= 3:
                try:
                    h = float(tok[1].replace(",", "."))
                except ValueError:
                    continue
                if not ids or ids[-1] != tok[0]:
                    if tok[0] not in ids:
                        ids.append(tok[0])
                hmax = max(hmax, h)
    return ids, hmax


def analyze(inp: str, lib: Optional[str] = None) -> dict:
    """Resumen del modelo para la interfaz y para proponer parámetros."""
    with en.EpanetProject(inp, lib) as prj:
        net = NetworkInfo.from_project(prj)
        tanks = []
        for i in np.where(net.node_type == en.EN_TANK)[0]:
            tanks.append({"id": net.node_ids[i],
                          "min": prj.get_node(int(i) + 1, en.EN_MINLEVEL),
                          "max": prj.get_node(int(i) + 1, en.EN_MAXLEVEL),
                          "init": prj.get_node(int(i) + 1, en.EN_TANKLEVEL),
                          "diam": prj.get_node(int(i) + 1, en.EN_TANKDIAM)})
        dur = prj.get_time(en.EN_DURATION)
    pipes = np.where(np.isin(net.link_type, [en.EN_PIPE, en.EN_CVPIPE]))[0]
    cats = net.catalog["LINK"]
    mats = Counter(_material(cats.get(net.link_ids[i])) for i in pipes)
    mats.pop(None, None)
    tags = Counter(net.tags["LINK"].get(net.link_ids[i]) for i in pipes)
    tags.pop(None, None)
    tcv = np.where(net.link_type == en.EN_TCV)[0]
    tcv_thr = [i for i in tcv if net.init_setting[i] > 0]
    prv = np.where(np.isin(net.link_type, [en.EN_PRV, en.EN_PSV]))[0]
    res = [net.node_ids[i] for i in np.where(net.node_type == en.EN_RESERVOIR)[0]]
    return {"net": net, "n_nodes": len(net.node_ids), "n_links": len(net.link_ids),
            "n_pipes": len(pipes), "materials": dict(mats), "tags": dict(tags),
            "tcv_throttled": [net.link_ids[i] for i in tcv_thr], "n_tcv": len(tcv),
            "prv": [net.link_ids[i] for i in prv], "reservoirs": res, "tanks": tanks,
            "headloss": net.headloss, "flow_units": net.flow_units,
            "demand_model": net.demand_model, "duration_h": dur / 3600}


def propose_parameters(info: dict) -> List[dict]:
    """Lista de parámetros propuestos (lo que muestra la tabla de la aplicación)."""
    net: NetworkInfo = info["net"]
    out = []
    dw = info["headloss"] == DW
    pipes = np.where(np.isin(net.link_type, [en.EN_PIPE, en.EN_CVPIPE]))[0]

    def rough_bounds(r):
        if dw:
            return ([0.0005, 0.1] if r < 0.05 else [0.01, 5.0]), True
        return [max(40.0, r - 50), min(150.0, r + 20)], False

    if info["materials"]:
        for mat, n in sorted(info["materials"].items(), key=lambda kv: -kv[1]):
            sel = [i for i in pipes if _material(net.catalog["LINK"].get(net.link_ids[i])) == mat]
            r = float(np.median(net.roughness[sel]))
            b, lg = rough_bounds(r)
            out.append({"use": True, "name": mat, "type": "roughness", "mode": "absolute",
                        "select": {"catalog_regex": f"^{re.escape(mat)}(?![A-Za-z])", "link_type": "PIPE"},
                        "n": n, "bounds": b, "log": lg})
    elif info["tags"]:
        for tag, n in sorted(info["tags"].items(), key=lambda kv: -kv[1]):
            sel = [i for i in pipes if net.tags["LINK"].get(net.link_ids[i]) == tag]
            b, lg = rough_bounds(float(np.median(net.roughness[sel])))
            out.append({"use": True, "name": tag, "type": "roughness", "mode": "absolute",
                        "select": {"tag": tag, "link_type": "PIPE"}, "n": n, "bounds": b, "log": lg})
    else:  # sin catálogo ni etiquetas: familias por valor de rugosidad
        vals = Counter(np.round(net.roughness[pipes], 6))
        for v, n in vals.most_common(4):
            b, lg = rough_bounds(float(v))
            out.append({"use": True, "name": f"RUG_{v:g}", "type": "roughness", "mode": "absolute",
                        "select": {"roughness": [float(v) * 0.999, float(v) * 1.001], "link_type": "PIPE"},
                        "n": n, "bounds": b, "log": lg})
    if info["tcv_throttled"]:
        out.append({"use": True, "name": "TCV_ESTRANGULADAS", "type": "valve_setting",
                    "mode": "multiplier", "select": {"link_type": "TCV", "setting": [1e-6, 1e9]},
                    "n": len(info["tcv_throttled"]), "bounds": [0.05, 5.0], "log": True})
    if info["prv"]:
        out.append({"use": False, "name": "PRV_CONSIGNA", "type": "valve_setting", "mode": "offset",
                    "select": {"link_type": ["PRV", "PSV"]}, "n": len(info["prv"]),
                    "bounds": [-5.0, 5.0], "log": False})
    for r in info["reservoirs"]:
        out.append({"use": True, "name": f"NIVEL_{r}", "type": "reservoir_head", "mode": "offset",
                    "select": {"ids": [r]}, "n": 1, "bounds": [-3.0, 3.0], "log": False})
    for t in info["tanks"]:
        lo, hi = max(t["min"], 0.0), t["max"]
        out.append({"use": True, "name": f"NIVEL_INI_{t['id']}", "type": "tank_level",
                    "mode": "absolute", "select": {"ids": [t["id"]]}, "n": 1,
                    "bounds": [round(lo, 3), round(hi, 3)], "log": False})
        out.append({"use": True, "name": f"DIAM_{t['id']}", "type": "tank_diameter",
                    "mode": "multiplier", "select": {"ids": [t["id"]]}, "n": 1,
                    "bounds": [0.7, 1.3], "log": False})
    return out


def default_rules(kinds: List[str]) -> List[dict]:
    rules = []
    if "FLOW" in kinds:
        rules.append({"name": "CAUDAL", "kind": "FLOW", "error": "relative", "threshold": 15,
                      "timesteps_pct": 75, "points_pct": 100})
    if "PRESSURE" in kinds:
        rules.append({"name": "PRESION", "kind": "PRESSURE", "error": "absolute", "threshold": 5,
                      "timesteps_pct": 80, "points_pct": 80})
    if "LEVEL" in kinds:
        rules.append({"name": "NIVEL", "kind": "LEVEL", "error": "absolute", "threshold": 0.3,
                      "timesteps_pct": 80, "points_pct": 100})
    return rules


def default_objectives(kinds: List[str], has_tanks: bool, n_flow_sensors: int) -> List[str]:
    objs = [k for k in ("PRESSURE", "LEVEL") if k in kinds]
    # con un único caudalímetro de entrada y sin tanques el caudal es fijo (días tipo)
    if "FLOW" in kinds and (has_tanks or n_flow_sensors > 1):
        objs.append("FLOW")
    if len(objs) < 2:
        objs.append("CAMBIO")
    return objs


def build_config(name: str, inp: str, obs_files: List[Tuple[str, str]], out_dir: str,
                 params: List[dict], rules: List[dict], objectives: List[str],
                 losses_pct: Optional[float], preset: str = "pareto",
                 emitter_exponent: float = 0.5, validation_hours: float = 24.0,
                 epanet_version: str = "2.2", plugin_dirs: Optional[List[str]] = None) -> dict:
    """Configuración completa (la misma que usa la línea de comandos)."""
    hmax = 0.0
    for f, _ in obs_files:
        if f.lower().endswith(".dat"):
            hmax = max(hmax, read_dat_summary(f)[1])
    start = "2026-01-01 00:00:00"
    obs = {"files": [{"file": f, "type": t} for f, t in obs_files], "start": start,
           "step": 900, "warmup": 3600,
           "outlier_filter": {"method": "hampel", "window": 6, "n_sigma": 3.5}}
    if hmax > 2 * validation_hours and validation_hours > 0:
        import pandas as pd
        obs["validation_start"] = str(pd.Timestamp(start) + pd.Timedelta(hours=hmax - validation_hours))
    groups, plist = {}, []
    for p in params:
        if not p.get("use", True):
            continue
        gname = re.sub(r"[^A-Za-z0-9_]", "_", p["name"])
        groups[gname] = {"select": p["select"]}
        q = {"name": f"{gname}:{p['type']}", "group": gname, "type": p["type"],
             "mode": p.get("mode", "absolute"), "bounds": [float(p["bounds"][0]), float(p["bounds"][1])]}
        if p.get("log"):
            q["log"] = True
        plist.append(q)
    groups["TODO_EL_SECTOR"] = {"select": {"node_type": "JUNCTION"}}
    cfg = {
        "name": name, "inp": os.path.abspath(inp), "output_dir": os.path.abspath(out_dir),
        "workers": 0, "epanet_version": epanet_version, "groups": groups,
        "dmas": {"SECTOR": {"select": {"node_type": "JUNCTION"}}},
        "observations": obs,
        "hydraulic": {
            "objectives": objectives, "metric": "rmse", "parameters": plist,
            "sensitivity": {"enabled": True, "freeze_below": 0.01},
            "optimizer": dict(PRESETS.get(preset, PRESETS["pareto"]), seed=1),
            "selection": "knee", "local_search": {"enabled": True, "max_iter": 12},
            "loss_balance": {"enabled": True, "n_inner": 1}},
        "acceptance": rules,
        "workflow": {"mode": "full" if losses_pct else "hydraulic", "coupled_rounds": 1},
    }
    if plugin_dirs:
        cfg["plugin_dirs"] = plugin_dirs
    if losses_pct:
        cfg["losses"] = {"method": "emitters", "emitter_exponent": float(emitter_exponent),
                         "allocation": "length", "pattern_adjust": True, "tolerance": 0.0005,
                         "max_iter": 40,
                         "dmas": {"SECTOR": {"target": {"type": "percent",
                                                        "value": float(losses_pct)}}}}
    return cfg
