"""Orquestación: pérdidas, hidráulica o ambas acopladas en rondas."""
from __future__ import annotations

import copy
import os
import shutil
from typing import Callable, Optional

import yaml

from .hydraulic import original_baseline, run_hydraulic, sensor_table
from .metrics import evaluate_rules
from .losses import run_losses
from .observations import load_observations
from .report import build_report


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        if path.lower().endswith(".json"):
            import json
            return json.load(fh)
        return yaml.safe_load(fh)


def _abs(base_dir, p):
    return p if (p is None or os.path.isabs(p)) else os.path.join(base_dir, p)


def run(config_path: str, mode: Optional[str] = None, out_dir: Optional[str] = None,
        log: Callable[[str], None] = print) -> dict:
    cfg = load_config(config_path)
    base_dir = os.path.dirname(os.path.abspath(config_path))
    out_dir = _abs(base_dir, out_dir or cfg.get("output_dir", "resultados"))
    os.makedirs(out_dir, exist_ok=True)
    inp = _abs(base_dir, cfg["inp"])
    # rutas relativas de grupos/dmas -> absolutas
    for sec in ("groups", "dmas"):
        for k, v in (cfg.get(sec) or {}).items():
            if isinstance(v, dict):
                sel = v.get("select", v)
                if isinstance(sel, dict) and "ids_file" in sel:
                    sel["ids_file"] = _abs(base_dir, sel["ids_file"])
        if isinstance(cfg.get(sec), dict) and "file" in cfg[sec]:
            cfg[sec]["file"] = _abs(base_dir, cfg[sec]["file"])

    wf = cfg.get("workflow", {})
    mode = (mode or wf.get("mode", "full")).lower()
    rounds = int(wf.get("coupled_rounds", 1)) if mode == "full" else 1
    hyd_res, loss_res = None, None
    current = inp
    obs = load_observations(cfg["observations"], base_dir) if mode in ("full", "hydraulic") else None

    for rnd in range(1, rounds + 1):
        rdir = out_dir if rounds == 1 else os.path.join(out_dir, f"ronda_{rnd}")
        if mode in ("full", "losses"):
            lcfg = copy.deepcopy(cfg)
            # la referencia de caudal de entrada siempre es el modelo ORIGINAL
            lcfg["losses"]["reference_inp"] = inp
            loss_res = run_losses(lcfg, current, base_dir, rdir, log,
                                  tag=f"pérdidas r{rnd}" if rounds > 1 else "pérdidas")
            current = loss_res["inp"]
        if mode in ("full", "hydraulic"):
            hcfg = copy.deepcopy(cfg)
            if rnd > 1 and hyd_res is not None:
                # siguiente ronda: se parte de la solución anterior, rangos iguales
                for pc, prm, v in zip(hcfg["hydraulic"]["parameters"], hyd_res.params,
                                      hyd_res.x_final):
                    pc["initial"] = float(v)
            hyd_res = run_hydraulic(hcfg, current, base_dir, rdir, log, obs=obs,
                                    tag=f"hidráulica r{rnd}" if rounds > 1 else "hidráulica")
            current = hyd_res.outputs["inp"]

    if hyd_res is not None and current != inp:
        # la comparación del informe es siempre contra el modelo ORIGINAL sin calibrar
        sim0, f0 = original_baseline(cfg, inp, obs, hyd_res.objectives)
        hyd_res.sim_initial, hyd_res.obj_initial = sim0, f0
        hyd_res.rules_initial = evaluate_rules(cfg.get("acceptance", []), obs.sensors,
                                               obs.values, sim0, obs.calib_mask)
        hyd_res.sensor_metrics = sensor_table(obs, sim0, hyd_res.sim_final)
        hyd_res.sensor_metrics.to_csv(os.path.join(out_dir, "metricas_por_sensor.csv"), index=False)
        log("  métricas finales comparadas con el modelo original: " + ", ".join(
            f"{o} {a:.4g} -> {b:.4g}" for o, a, b in zip(hyd_res.objectives, f0, hyd_res.obj_final)))
    final_inp = os.path.join(out_dir, f"{cfg.get('name', 'calibracion')}_CALIBRADO.inp")
    shutil.copyfile(current, final_inp)
    notes = [f"Modo: {mode}; rondas acopladas: {rounds}. INP final: {os.path.basename(final_inp)}"]
    rep = build_report(os.path.join(out_dir, "informe_calibracion.html"), cfg, hyd_res,
                       loss_res, notes)
    log(f"INP final: {final_inp}")
    log(f"Informe:   {rep}")
    return {"inp": final_inp, "report": rep, "hydraulic": hyd_res, "losses": loss_res}
