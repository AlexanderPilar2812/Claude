"""Calibración hidráulica multiobjetivo (NSGA-II + refinamiento por gradiente).

Flujo:
 1. Simulación del modelo original (línea base) y métricas.
 2. Cribado de sensibilidad: los parámetros que no influyen en las medidas
    se congelan (reduce la dimensión y evita valores sin sentido físico).
 3. NSGA-II sobre los parámetros libres -> frente de Pareto (un objetivo por
    tipo de sensor: RMSE de caudal, RMSE de presión, ...).
 4. Cada solución del frente se valida contra las reglas de aceptación; se
    elige la de compromiso (codo / TOPSIS / ponderada) entre las que más reglas
    cumplen.
 5. Refinamiento local (TRF/Levenberg-Marquardt) desde esa solución.
 6. Diagnóstico final: métricas por sensor en calibración y validación,
    incertidumbre y correlación de parámetros, sesgo por sensor.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from . import engine as en
from .metrics import evaluate_rules, rules_score, series_metrics
from .network import NetworkInfo
from .observations import Observations, load_observations
from .optim import choose, jacobian, nsga2, refine, sensitivity_report
from .parameters import Parameter, apply_parameters, build_parameters
from .simulator import PENALTY, EvalSpec, Pool


@dataclass
class HydraulicResult:
    params: List[Parameter]
    x_initial: np.ndarray
    x_final: np.ndarray
    x_pareto_choice: np.ndarray
    pareto_X: np.ndarray            # físico
    pareto_F: np.ndarray
    pareto_rules: List[list]
    choice_index: int
    objectives: List[str]
    obj_initial: np.ndarray
    obj_final: np.ndarray
    sim_initial: np.ndarray
    sim_final: np.ndarray
    obs: Observations
    rules_final: list
    rules_valid: list
    rules_initial: list
    sensor_metrics: pd.DataFrame
    sensitivity_initial: Optional[dict]
    sensitivity_final: Optional[dict]
    frozen: List[str]
    history: List[dict]
    n_evals: int
    elapsed: float
    modified: pd.DataFrame
    param_table: pd.DataFrame
    outputs: Dict[str, str] = field(default_factory=dict)


def sensor_table(obs: Observations, sim0: np.ndarray, sim_f: np.ndarray) -> pd.DataFrame:
    rows = []
    for i, s in enumerate(obs.sensors):
        for period, m in (("calibracion", obs.calib_mask), ("validacion", ~obs.calib_mask)):
            if not m.any():
                continue
            mi = series_metrics(obs.values[i, m], sim0[i, m])
            mf = series_metrics(obs.values[i, m], sim_f[i, m])
            rows.append({"sensor": s.sensor_id, "tipo": s.kind, "elemento": s.element,
                         "periodo": period, "n": mf["n"],
                         "rmse_inicial": mi["rmse"], "rmse_final": mf["rmse"],
                         "mae_final": mf["mae"], "sesgo_inicial": mi["bias"],
                         "sesgo_final": mf["bias"], "rme_pct_final": mf["rme_pct"],
                         "nse_final": mf["nse"], "kge_final": mf["kge"], "r2_final": mf["r2"],
                         "outliers_filtrados": obs.n_outliers.get(s.sensor_id, 0)})
    return pd.DataFrame(rows)


def original_baseline(cfg: dict, inp_file: str, obs: Observations, objectives: List[str]):
    """Simula un INP con sus valores originales (para comparar contra el modelo de partida)."""
    from .simulator import Evaluator
    hc = cfg.get("hydraulic", {})
    lib = cfg.get("epanet_lib")
    spec = EvalSpec(inp_file=inp_file, params=[], obs=obs, objectives=objectives,
                    metric=hc.get("metric", "rmse"), huber_delta=float(hc.get("huber_delta", 1.0)),
                    lib_path=lib, sim_offset=int(cfg.get("observations", {}).get("sim_offset", 0)))
    ev = Evaluator(spec)
    sim, ok = ev.simulate()
    f = ev.objectives(sim)
    ev.close()
    return sim, f


def _phys(params, u_free, free, x_fixed):
    x = x_fixed.copy()
    k = 0
    for j, p in enumerate(params):
        if free[j]:
            x[j] = p.from_unit(u_free[k])
            k += 1
    return x


def run_hydraulic(cfg: dict, inp_file: str, base_dir: str, out_dir: str,
                  log: Callable[[str], None] = print, obs: Optional[Observations] = None,
                  tag: str = "hidraulica") -> HydraulicResult:
    t0 = time.time()
    hc = cfg.get("hydraulic", {})
    os.makedirs(out_dir, exist_ok=True)
    lib = cfg.get("epanet_lib")

    prj = en.EpanetProject(inp_file, lib)
    net = NetworkInfo.from_project(prj)
    net.set_dmas(cfg.get("dmas"))
    params = build_parameters(hc["parameters"], cfg.get("groups", {}), net, prj)
    prj.close()
    if obs is None:
        obs = load_observations(cfg["observations"], base_dir)
    objectives = [k.upper() for k in hc.get("objectives", obs.kinds)]
    objectives = [{"CAUDAL": "FLOW", "PRESION": "PRESSURE"}.get(k, k) for k in objectives]
    rules = cfg.get("acceptance", [])

    log(f"[{tag}] red: {len(net.node_ids)} nodos, {len(net.link_ids)} líneas | "
        f"sensores: {len(obs.sensors)} ({', '.join(obs.kinds)}) | instantes: {len(obs.times)} "
        f"(calibración {obs.calib_mask.sum()}, validación {(~obs.calib_mask).sum()})")
    for p in params:
        log(f"  parámetro {p.name}: {p.ptype}/{p.mode} sobre {len(p.elements)} elementos, "
            f"rango [{p.lower:g}, {p.upper:g}]{' log' if p.log else ''}, inicial {p.initial:g}")

    spec = EvalSpec(inp_file=inp_file, params=params, obs=obs, objectives=objectives,
                    metric=hc.get("metric", "rmse"), huber_delta=float(hc.get("huber_delta", 1.0)),
                    lib_path=lib, kind_weight={k.upper(): float(v) for k, v in
                                               (hc.get("weights") or {}).items()},
                    sim_offset=int(cfg.get("observations", {}).get("sim_offset", 0)))
    x0 = np.array([p.initial for p in params])

    # ---- línea base (en serie, también fija la escala de los residuos)
    pool = Pool(spec, 1)
    sim0, ok0 = pool.local.simulate(x0)
    f0 = pool.local.objectives(sim0)
    scale = {k: (f if (np.isfinite(f) and f > 0 and f < PENALTY) else 1.0)
             for k, f in zip(objectives, f0)}
    scale.update({k.upper(): float(v) for k, v in (hc.get("kind_scale") or {}).items()})
    spec.kind_scale = scale
    pool.close()
    log("  modelo original: " + ", ".join(f"{k}={v:.4g}" for k, v in zip(objectives, f0))
        + ("" if ok0 else "  (¡aviso: no converge en algún paso!)"))

    pool = Pool(spec, int(cfg.get("workers", 0)))
    log(f"  procesos de simulación en paralelo: {pool.n_workers}")
    n_p = len(params)

    # tamaño de cada bloque de residuos (uno por tipo de sensor), para poder
    # re-escalarlos sin reiniciar los procesos trabajadores
    segs = []
    for kind in objectives:
        rows = obs.rows_of(kind)
        if len(rows):
            segs.append((kind, int(np.isfinite(obs.values[rows][:, obs.calib_mask]).sum())))
    lm_scale = dict(scale)

    def rescale(r):
        r = r.copy()
        a = 0
        for kind, n in segs:
            r[a:a + n] *= scale[kind] / lm_scale[kind]
            a += n
        return r

    def residual_batch_unit(U_list, free, x_fixed):
        X = [_phys(params, u, free, x_fixed) for u in U_list]
        return [rescale(d["residuals"]) for d in pool.map(X, want=("residuals",))]

    # ---- 2. sensibilidad inicial / congelación
    free = np.ones(n_p, bool)
    sens0, frozen = None, []
    sc = hc.get("sensitivity", {"enabled": True, "freeze_below": 0.01})
    if sc.get("enabled", True) and n_p > 0:
        u0 = np.array([p.to_unit(v) for p, v in zip(params, x0)])
        J, r = jacobian(lambda B: residual_batch_unit(B, np.ones(n_p, bool), x0), u0,
                        h=float(sc.get("step", 0.05)))
        sens0 = sensitivity_report(J, r, [p.name for p in params])
        thr = float(sc.get("freeze_below", 0.01))
        for j, p in enumerate(params):
            log(f"  sensibilidad {p.name}: CSS relativa {sens0['css_rel'][j]:.3f}")
            if sens0["css_rel"][j] < thr:
                free[j] = False
                frozen.append(p.name)
        if frozen:
            log(f"  parámetros congelados (no identificables con los sensores actuales): {frozen}")
    if not free.any():
        free[:] = True
        frozen = []

    # ---- 3. NSGA-II
    oc = hc.get("optimizer", {})

    def evaluate(U):
        X = [_phys(params, u, free, x0) for u in U]
        res = pool.map(X)
        F = np.array([d["objectives"] for d in res])
        CV = np.array([0.0 if d["ok"] else 1.0 for d in res])
        F = np.where(np.isfinite(F), F, PENALTY)
        return F, CV

    u_seed = np.array([[p.to_unit(v) for p, v, fr in zip(params, x0, free) if fr]])
    extra_seeds = []
    for s in oc.get("seeds", []):  # semillas definidas por el usuario {nombre: valor}
        x = x0.copy()
        for j, p in enumerate(params):
            if p.name in s:
                x[j] = float(s[p.name])
        extra_seeds.append([p.to_unit(v) for p, v, fr in zip(params, x, free) if fr])
    seeds = np.vstack([u_seed] + ([np.array(extra_seeds)] if extra_seeds else []))
    log(f"[{tag}] NSGA-II: población {oc.get('pop_size', 40)}, generaciones {oc.get('n_gen', 40)}, "
        f"{int(free.sum())} variables")
    res = nsga2(evaluate, int(free.sum()), pop_size=int(oc.get("pop_size", 40)),
                n_gen=int(oc.get("n_gen", 40)), seeds=seeds, seed=int(oc.get("seed", 1)),
                eta_c=float(oc.get("eta_c", 15)), eta_m=float(oc.get("eta_m", 20)),
                stall_gens=int(oc.get("stall_gens", 12)), max_evals=oc.get("max_evals"),
                log=log)
    feas = np.all(res.F < PENALTY, axis=1)
    PU, PF = res.X[feas], res.F[feas]
    order = np.argsort(PF[:, 0])
    PU, PF = PU[order], PF[order]
    max_front = int(oc.get("max_front", 30))
    if len(PU) > max_front:  # submuestreo uniforme del frente
        keep = np.unique(np.linspace(0, len(PU) - 1, max_front).round().astype(int))
        PU, PF = PU[keep], PF[keep]
    PX = np.array([_phys(params, u, free, x0) for u in PU])

    # ---- 4. reglas de aceptación sobre cada solución del frente
    sims = [d["sim"] for d in pool.map(list(PX), want=("sim",))]
    pr = [evaluate_rules(rules, obs.sensors, obs.values, s, obs.calib_mask) for s in sims]
    n_pass = np.array([sum(r.passed for r in rr) for rr in pr])
    score = np.array([rules_score(rr) for rr in pr])
    k = choose(PF, hc.get("selection", "knee"), hc.get("selection_weights"), n_pass, score)
    x_choice = PX[k]
    log(f"  frente de Pareto: {len(PX)} soluciones; elegida #{k + 1} "
        f"({', '.join(f'{o}={v:.4g}' for o, v in zip(objectives, PF[k]))}, "
        f"reglas {n_pass[k]}/{len(rules)})")

    # ---- 5. refinamiento local
    x_final = x_choice.copy()
    lc = hc.get("local_search", {"enabled": True})
    if lc.get("enabled", True):
        # cada objetivo se escala con su valor en la solución elegida: el gradiente
        # mejora ambos a la vez sin desplazarse a otro punto del frente
        lm_scale.update({o: max(float(v), 1e-9) for o, v in zip(objectives, PF[k])})
        u_c = np.array([p.to_unit(v) for p, v in zip(params, x_choice)])
        u_ref, _ = refine(lambda B: residual_batch_unit(B, np.ones(n_p, bool), x0), u_c,
                          max_iter=int(lc.get("max_iter", 12)), h=float(lc.get("step", 0.01)),
                          log=log, free=free)
        x_ref = np.array([p.from_unit(u) for p, u in zip(params, u_ref)])
        d = pool.map([x_choice, x_ref], want=("sim", "objectives"))
        r_c = evaluate_rules(rules, obs.sensors, obs.values, d[0]["sim"], obs.calib_mask)
        r_r = evaluate_rules(rules, obs.sensors, obs.values, d[1]["sim"], obs.calib_mask)
        wts = spec.kind_weight or {}
        wsum = lambda f: sum(wts.get(o, 1.0) * (f[i] / lm_scale[o]) ** 2
                             for i, o in enumerate(objectives))
        better = (sum(r.passed for r in r_r) >= sum(r.passed for r in r_c)
                  and wsum(d[1]["objectives"]) <= wsum(d[0]["objectives"]))
        if better and d[1]["ok"]:
            x_final = x_ref
            log("  se acepta la solución refinada")
        else:
            log("  la solución refinada no mejora el compromiso: se mantiene la del frente")

    # ---- 6. diagnóstico final
    d = pool.map([x_final], want=("sim", "objectives"))[0]
    sim_f, f_final = d["sim"], d["objectives"]
    sens_f = None
    lm_scale = dict(scale)
    if sc.get("enabled", True):
        u_f = np.array([p.to_unit(v) for p, v in zip(params, x_final)])
        J, r = jacobian(lambda B: residual_batch_unit(B, np.ones(n_p, bool), x0), u_f,
                        h=float(sc.get("step", 0.05)) / 2)
        sens_f = sensitivity_report(J, r, [p.name for p in params])
    pool.close()

    rules_init = evaluate_rules(rules, obs.sensors, obs.values, sim0, obs.calib_mask)
    rules_fin = evaluate_rules(rules, obs.sensors, obs.values, sim_f, obs.calib_mask)
    rules_val = evaluate_rules(rules, obs.sensors, obs.values, sim_f, ~obs.calib_mask) \
        if (~obs.calib_mask).any() else []

    sm = sensor_table(obs, sim0, sim_f)

    # tabla de parámetros con incertidumbre
    ptab = []
    for j, p in enumerate(params):
        sd_u = sens_f["sd_unit"][j] if sens_f else np.nan
        u = p.to_unit(x_final[j])
        lo = p.from_unit(u - 1.96 * sd_u) if np.isfinite(sd_u) else np.nan
        hi = p.from_unit(u + 1.96 * sd_u) if np.isfinite(sd_u) else np.nan
        ptab.append({"parametro": p.name, "tipo": p.ptype, "modo": p.mode,
                     "elementos": len(p.elements), "inicial": x0[j], "calibrado": x_final[j],
                     "solucion_pareto": x_choice[j], "limite_inf": p.lower,
                     "limite_sup": p.upper, "ic95_inf": lo, "ic95_sup": hi,
                     "css_rel": sens_f["css_rel"][j] if sens_f else np.nan,
                     "congelado": p.name in frozen,
                     "en_limite": bool(min(abs(u), abs(1 - u)) < 0.01)})
    ptab = pd.DataFrame(ptab)

    # objetos modificados
    mod = []
    for p, v in zip(params, x_final):
        vals = p.element_values(v)
        for i, nv, ov in zip(p.elements, vals, p.base):
            if not np.isclose(nv, ov, rtol=1e-9, atol=1e-12):
                fid = net.link_ids[i] if p.is_link else net.node_ids[i]
                etype = (en.LINK_TYPES[net.link_type[i]] if p.is_link
                         else en.NODE_TYPES[net.node_type[i]])
                mod.append({"feature_id": fid, "tipo": etype, "parametro": p.ptype,
                            "familia": p.name, "valor_calibrado": nv, "valor_original": ov})
    mod = pd.DataFrame(mod, columns=["feature_id", "tipo", "parametro", "familia",
                                     "valor_calibrado", "valor_original"])

    result = HydraulicResult(
        params=params, x_initial=x0, x_final=x_final, x_pareto_choice=x_choice,
        pareto_X=PX, pareto_F=PF, pareto_rules=pr, choice_index=k, objectives=objectives,
        obj_initial=f0, obj_final=f_final, sim_initial=sim0, sim_final=sim_f, obs=obs,
        rules_final=rules_fin, rules_valid=rules_val, rules_initial=rules_init,
        sensor_metrics=sm, sensitivity_initial=sens0, sensitivity_final=sens_f,
        frozen=frozen, history=res.history, n_evals=pool.n_evals + res.n_evals,
        elapsed=time.time() - t0, modified=mod, param_table=ptab)
    write_hydraulic_outputs(result, cfg, inp_file, out_dir, lib, log)
    log(f"[{tag}] terminado en {result.elapsed:.0f} s, {result.n_evals} simulaciones")
    return result


def write_hydraulic_outputs(r: HydraulicResult, cfg, inp_file, out_dir, lib, log):
    base = os.path.splitext(os.path.basename(cfg.get("name", "calibracion")))[0]
    out_inp = os.path.join(out_dir, f"{base}_calibrado_hidraulica.inp")
    with en.EpanetProject(inp_file, lib) as prj:
        apply_parameters(prj, r.params, r.x_final)
        prj.save_inp(out_inp)
    r.outputs["inp"] = out_inp
    p = lambda n: os.path.join(out_dir, n)
    r.modified.to_csv(p("objetos_modificados.csv"), index=False)
    r.param_table.to_csv(p("parametros_calibrados.csv"), index=False)
    r.sensor_metrics.to_csv(p("metricas_por_sensor.csv"), index=False)
    pf = pd.DataFrame(r.pareto_F, columns=[f"{o}_{cfg.get('hydraulic', {}).get('metric', 'rmse')}"
                                           for o in r.objectives])
    for j, prm in enumerate(r.params):
        pf[prm.name] = r.pareto_X[:, j]
    pf["reglas_cumplidas"] = [sum(x.passed for x in rr) for rr in r.pareto_rules]
    pf["elegida"] = [i == r.choice_index for i in range(len(pf))]
    pf.index = [f"#{i + 1}" for i in range(len(pf))]
    pf.to_csv(p("frente_pareto.csv"), index_label="solucion")
    ts = r.obs.timestamps()
    rows = {}
    for i, s in enumerate(r.obs.sensors):
        rows[(s.sensor_id, "observado")] = r.obs.values[i]
        rows[(s.sensor_id, "inicial")] = r.sim_initial[i]
        rows[(s.sensor_id, "calibrado")] = r.sim_final[i]
    sv = pd.DataFrame(rows, index=ts)
    sv.columns = [f"{a}|{b}" for a, b in sv.columns]
    sv.to_csv(p("series_observado_vs_simulado.csv"), index_label="timestamp")
    r.outputs.update({"modificados": p("objetos_modificados.csv"),
                      "parametros": p("parametros_calibrados.csv"),
                      "metricas": p("metricas_por_sensor.csv"),
                      "pareto": p("frente_pareto.csv"),
                      "series": p("series_observado_vs_simulado.csv")})
    summary = {
        "objetivos": r.objectives,
        "objetivos_inicial": dict(zip(r.objectives, map(float, r.obj_initial))),
        "objetivos_final": dict(zip(r.objectives, map(float, r.obj_final))),
        "parametros": {prm.name: float(v) for prm, v in zip(r.params, r.x_final)},
        "congelados": r.frozen,
        "reglas_calibracion": [{"regla": x.name, "pasa": x.passed,
                                "puntos_ok": f"{x.points_ok}/{x.points_total}",
                                "obs_ok": f"{x.obs_ok}/{x.obs_total}"} for x in r.rules_final],
        "reglas_validacion": [{"regla": x.name, "pasa": x.passed,
                               "puntos_ok": f"{x.points_ok}/{x.points_total}",
                               "obs_ok": f"{x.obs_ok}/{x.obs_total}"} for x in r.rules_valid],
        "simulaciones": r.n_evals, "segundos": r.elapsed, "inp": out_inp,
    }
    with open(p("resumen_hidraulica.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False)
    r.outputs["resumen"] = p("resumen_hidraulica.json")
    log(f"  INP calibrado: {out_inp}")
