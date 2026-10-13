"""Asistente de calibración: analiza el INP y las medidas y propone QUÉ calibrar.

Pasos (criterios de un ingeniero de calibración):
  1. Diagnóstico del modelo original frente a las medidas (sesgos, amplitud,
     correlación por sensor) -> hallazgos con su interpretación física.
  2. Disipación de energía en la red (|Q·hf| medio por línea): sólo tiene sentido
     calibrar la rugosidad de las tuberías que pierden carga.
  3. Candidatos: material (catálogo Giswater, etiquetas o valor de rugosidad),
     separando red principal (diámetro grande) y distribución; válvulas que
     pierden carga de forma apreciable (individualmente); embalses; tanques.
  4. Influencia real de cada candidato en los sensores, simulando sus extremos:
     los que no superan el ruido de medida se descartan con su motivo.
  5. Fusión de candidatos indistinguibles (respuesta muy correlacionada) y
     división espacial de familias grandes cuando zonas distintas responden
     de forma distinta.
"""
from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from . import engine as en
from .autoconfig import _material, analyze as analyze_inp
from .network import NetworkInfo, parse_coordinates
from .observations import load_observations
from .parameters import build_parameters
from .simulator import EvalSpec, Evaluator, Pool

NOISE = {"PRESSURE": 0.15, "HEAD": 0.15, "LEVEL": 0.05}    # m
FLOW_NOISE_REL = 0.02                                      # 2 %


def _kmeans(X: np.ndarray, k: int, seed: int = 0, iters: int = 30) -> np.ndarray:
    rng = np.random.default_rng(seed)
    C = X[rng.choice(len(X), k, replace=False)]
    lab = np.zeros(len(X), int)
    for _ in range(iters):
        d = ((X[:, None, :] - C[None, :, :]) ** 2).sum(-1)
        lab = d.argmin(1)
        newC = np.array([X[lab == j].mean(0) if (lab == j).any() else C[j] for j in range(k)])
        if np.allclose(newC, C):
            break
        C = newC
    return lab


def _rough_bounds(r: float, dw: bool) -> Tuple[list, bool]:
    if dw:
        return ([0.0005, 0.1] if r < 0.05 else [0.01, 5.0]), True
    return [max(40.0, r - 50), min(150.0, r + 20)], False


def advise(inp: str, obs_files: List[Tuple[str, str]], lib: Optional[str] = None,
           log: Callable[[str], None] = print, workers: int = 0,
           diameter_split: float = 150.0) -> dict:
    """Devuelve {'params': [...], 'findings': [...], 'sensors': [...], 'info': info}."""
    info = analyze_inp(inp, lib)
    net: NetworkInfo = info["net"]
    dw = info["headloss"] == 1
    findings: List[dict] = []

    def find(level, title, text):
        findings.append({"nivel": level, "titulo": title, "texto": text})

    obs = None
    if obs_files:
        try:
            obs = load_observations({"files": [{"file": f, "type": t} for f, t in obs_files],
                                     "start": "2026-01-01 00:00:00", "step": 900, "warmup": 3600,
                                     "outlier_filter": {"method": "hampel", "window": 6, "n_sigma": 3.5}},
                                    ".")
        except Exception as exc:
            find("error", "No se pudieron leer las medidas", str(exc))
    with en.EpanetProject(inp, lib) as prj:
        missing = []
        if obs is not None:
            for s in obs.sensors:
                ok = (s.element in net.node_pos) if s.is_node else (s.element in net.link_pos)
                if not ok:
                    missing.append(s.sensor_id)
        if missing:
            find("error", "Sensores que no existen en el INP", ", ".join(missing[:20]))
            keep = [i for i, s in enumerate(obs.sensors) if s.sensor_id not in missing]
            obs.sensors = [obs.sensors[i] for i in keep]
            obs.values = obs.values[keep]

    # ------------------------------------------------------------ 1. diagnóstico
    log("Asistente: simulando el modelo original…")
    kinds = obs.kinds if obs is not None and obs.sensors else []
    spec0 = EvalSpec(inp_file=inp, params=[], obs=obs, objectives=kinds or ["PRESSURE"],
                     lib_path=lib) if kinds else None
    sens_rows = []
    nl = len(net.link_ids)
    E = np.zeros(nl)
    hf_peak = np.zeros(nl)
    if spec0 is not None:
        ev = Evaluator(spec0)
        acc = {"n": 0}

        def grab(p, t, k):
            q = p.link_values(en.EN_FLOW)
            h = p.link_values(en.EN_HEADLOSS)
            E[:] += np.abs(q * h)
            np.maximum(hf_peak, np.abs(h), out=hf_peak)
            acc["n"] += 1
        sim0, ok0 = ev.simulate(None, extra=grab)
        ev.close()
        E /= max(acc["n"], 1)
        for i, s in enumerate(obs.sensors):
            o, m = obs.values[i], sim0[i]
            v = np.isfinite(o) & np.isfinite(m)
            if v.sum() < 5:
                continue
            e = m[v] - o[v]
            so, sm = np.std(o[v]), np.std(m[v])
            r = float(np.corrcoef(o[v], m[v])[0, 1]) if so > 0 and sm > 0 else np.nan
            sens_rows.append({"sensor": s.sensor_id, "tipo": s.kind, "sesgo": float(e.mean()),
                              "rmse": float(np.sqrt((e ** 2).mean())),
                              "amplitud": float(sm / so) if so > 0 else np.nan, "r": r,
                              "media_obs": float(o[v].mean())})
        _diagnose(sens_rows, find)
    else:
        log("Asistente: sin medidas, sólo análisis topológico")
        with en.EpanetProject(inp, lib) as prj:
            acc = {"n": 0}

            def cb(t):
                q = prj.link_values(en.EN_FLOW)
                h = prj.link_values(en.EN_HEADLOSS)
                E[:] += np.abs(q * h)
                acc["n"] += 1
            prj.run_eps(3600, cb)
            E /= max(acc["n"], 1)

    # ------------------------------------------------------------ 2-3. candidatos
    Etot = E.sum() or 1.0
    pipes = np.where(np.isin(net.link_type, [en.EN_PIPE, en.EN_CVPIPE]))[0]
    valves = np.where(np.isin(net.link_type, list(en.VALVE_TYPES)))[0]
    cands: List[dict] = []
    excluded: List[dict] = []

    def fam_of(i):
        lid = net.link_ids[i]
        return (_material(net.catalog["LINK"].get(lid)) or net.tags["LINK"].get(lid)
                or f"RUG_{net.roughness[i]:g}")

    fams: Dict[str, List[int]] = {}
    for i in pipes:
        fams.setdefault(fam_of(i), []).append(int(i))
    for fam, idx in sorted(fams.items(), key=lambda kv: -E[kv[1]].sum()):
        idx = np.array(idx)
        share = E[idx].sum() / Etot
        r0 = float(np.median(net.roughness[idx]))
        b, lg = _rough_bounds(r0, dw)
        if share < 0.01:
            excluded.append({"name": fam, "type": "roughness", "n": len(idx), "share": share,
                             "ids": [net.link_ids[i] for i in idx], "bounds": b, "log": lg,
                             "mode": "absolute",
                             "reason": f"disipa sólo el {100 * share:.1f} % de la energía: su rugosidad "
                                       "no puede cambiar las presiones"})
            continue
        big = idx[net.diameter[idx] >= diameter_split]
        small = idx[net.diameter[idx] < diameter_split]
        sb, ss = E[big].sum() / Etot, E[small].sum() / Etot
        if share >= 0.10 and len(big) >= 5 and len(small) >= 5 and min(sb, ss) >= 0.03:
            for part, nm, lab in ((big, f"{fam}_PRINCIPAL", f"D ≥ {diameter_split:g} mm"),
                                  (small, f"{fam}_DISTRIB", f"D < {diameter_split:g} mm")):
                cands.append({"name": nm, "type": "roughness", "mode": "absolute", "log": lg,
                              "bounds": b, "ids": [net.link_ids[i] for i in part], "kind": "pipes",
                              "share": E[part].sum() / Etot,
                              "reason": f"{fam} {lab}: disipa el {100 * E[part].sum() / Etot:.0f} % de la energía"})
        else:
            cands.append({"name": fam, "type": "roughness", "mode": "absolute", "log": lg, "bounds": b,
                          "ids": [net.link_ids[i] for i in idx], "kind": "pipes", "share": share,
                          "reason": f"familia {fam}: disipa el {100 * share:.0f} % de la energía"})
    for i in valves:
        lid = net.link_ids[i]
        h = hf_peak[i]
        sh = E[i] / Etot
        if h >= 0.2 or sh >= 0.01:
            st = net.init_setting[i]
            vt = en.LINK_TYPES[net.link_type[i]]
            if vt == "TCV":
                mode, b, lg = ("multiplier", [0.05, 5.0], True) if st > 0 else ("absolute", [0.0, 50.0], False)
            elif vt in ("PRV", "PSV"):
                # la consigna no puede quedar negativa (p. ej. VRP de entrada a tanque de 2,45 m)
                mode, b, lg = "offset", [-min(5.0, max(st - 0.1, 0.0)), 5.0], False
            else:
                mode, b, lg = "multiplier", [0.5, 2.0], False
            cands.append({"name": f"VALV_{lid}", "type": "valve_setting", "mode": mode, "log": lg,
                          "bounds": b, "ids": [lid], "kind": "valves", "share": sh,
                          "reason": f"{vt} con pérdida de carga de hasta {h:.2f} m "
                                    f"({100 * sh:.1f} % de la energía)"})
        elif net.link_type[i] == en.EN_TCV and net.init_setting[i] > 0:
            excluded.append({"name": f"VALV_{lid}", "type": "valve_setting", "n": 1, "share": sh,
                             "ids": [lid], "bounds": [0.05, 5.0], "log": True, "mode": "multiplier",
                             "reason": f"TCV con consigna {net.init_setting[i]:g} pero pérdida de carga "
                                       f"despreciable ({h:.2f} m)"})
    for r in info["reservoirs"]:
        cands.append({"name": f"NIVEL_{r}", "type": "reservoir_head", "mode": "offset", "log": False,
                      "bounds": [-3.0, 3.0], "ids": [r], "kind": "sources", "share": 1.0,
                      "reason": "nivel/cota de referencia del embalse (fija la presión de toda la red)"})
    lvl_ids = {s.element for s in obs.sensors if s.kind == "LEVEL"} if obs is not None else set()
    for t in info["tanks"]:
        meas = t["id"] in lvl_ids
        cands.append({"name": f"NIVEL_INI_{t['id']}", "type": "tank_level", "mode": "absolute",
                      "log": False, "bounds": [round(max(t["min"], 0), 3), round(t["max"], 3)],
                      "ids": [t["id"]], "kind": "sources", "share": 1.0,
                      "reason": "nivel inicial del tanque" + (" (hay sensor de nivel)" if meas else "")})
        if meas:
            cands.append({"name": f"DIAM_{t['id']}", "type": "tank_diameter", "mode": "multiplier",
                          "log": False, "bounds": [0.5, 2.0], "ids": [t["id"]], "kind": "sources",
                          "share": 1.0, "reason": "área del tanque: fija la velocidad de llenado/vaciado"})

    # ------------------------------------------------------------ 4-5. influencia
    params_out = []
    if spec0 is not None and cands:
        log(f"Asistente: probando la influencia de {len(cands)} candidatos en los sensores…")
        resp = _responses(inp, net, obs, kinds, cands, lib, workers)
        # división espacial de la familia de tuberías más influyente
        coords, verts = parse_coordinates(inp)
        npres = len(obs.rows_of("PRESSURE")) if "PRESSURE" in kinds else 0
        rough = [c for c in cands if c["type"] == "roughness" and c["infl"] > 0]
        if rough and npres >= 4 and coords:
            top = max(rough, key=lambda c: c["infl"])
            if len(top["ids"]) >= 100:
                mids = []
                for lid in top["ids"]:
                    i = net.link_pos[lid]
                    a, b2 = net.node_ids[net.node1[i]], net.node_ids[net.node2[i]]
                    mids.append(np.mean([coords.get(a, (np.nan, np.nan)),
                                         coords.get(b2, (np.nan, np.nan))], axis=0))
                mids = np.array(mids)
                okm = np.all(np.isfinite(mids), axis=1)
                if okm.sum() > 50:
                    lab = _kmeans(mids[okm], 2)
                    ids_ok = [x for x, k in zip(top["ids"], okm) if k]
                    zones = [{**top, "name": f"{top['name']}_Z{z + 1}",
                              "ids": [x for x, l2 in zip(ids_ok, lab) if l2 == z]} for z in range(2)]
                    zresp = _responses(inp, net, obs, kinds, zones, lib, workers)
                    va, vb = zresp[0]["vec"], zresp[1]["vec"]
                    corr = float(np.corrcoef(va, vb)[0, 1]) if va.std() > 0 and vb.std() > 0 else 1.0
                    if corr < 0.85 and min(z["infl"] for z in zones) > z_thr(kinds):
                        for z in zones:
                            z["reason"] = (f"{top['reason']}; zona {z['name'][-2:]}: los sensores distinguen "
                                           f"zonas (correlación {corr:.2f})")
                        cands = [c for c in cands if c is not top] + zones
                        resp = [r for r in resp if r is not top] + zresp
                        find("info", f"Familia {top['name']} dividida en 2 zonas",
                             f"Los sensores responden de forma distinta a cada zona (correlación {corr:.2f}): "
                             "conviene calibrar su rugosidad por separado.")
        # fusión de candidatos indistinguibles
        merged = _merge_correlated(cands, find)
        _confounded(merged, excluded, find, z_thr(kinds))
        for c in merged:
            thr = z_thr(kinds)
            if c["infl"] < thr:
                excluded.append({"name": c["name"], "type": c["type"], "n": len(c["ids"]),
                                 "share": c.get("share", 0), "ids": c["ids"], "bounds": c["bounds"],
                                 "log": c["log"], "mode": c["mode"],
                                 "reason": f"efecto en los sensores {c.get('infl_m', 0):.2f} m, sólo "
                                           f"{c['infl']:.1f} veces el ruido de medida (< {thr:g}): "
                                           "no identificable"})
            else:
                params_out.append(c)
    else:
        params_out = cands

    out = []
    for c in params_out:
        out.append({"use": True, "name": re.sub(r"[^A-Za-z0-9_]", "_", c["name"]), "type": c["type"],
                    "mode": c["mode"], "select": {"ids": c["ids"]}, "n": len(c["ids"]),
                    "bounds": c["bounds"], "log": c["log"],
                    "reason": c["reason"] + (f" · efecto en sensores {c.get('infl_m', 0):.2f} m "
                                             f"({c['infl']:.0f}× el ruido)" if "infl" in c else "")})
    for c in excluded:
        out.append({"use": False, "name": re.sub(r"[^A-Za-z0-9_]", "_", c["name"]), "type": c["type"],
                    "mode": c["mode"], "select": {"ids": c["ids"]}, "n": c["n"], "bounds": c["bounds"],
                    "log": c["log"], "reason": "DESCARTADO: " + c["reason"]})
    n_use = sum(p["use"] for p in out)
    find("ok", f"{n_use} parámetros propuestos",
         f"De {n_use + len(excluded)} candidatos analizados se proponen {n_use}; "
         f"{len(excluded)} se descartan con su motivo (aparecen desactivados en la tabla).")
    log(f"Asistente: {n_use} parámetros propuestos, {len(excluded)} descartados")
    return {"params": out, "findings": findings, "sensors": sens_rows, "info": info,
            "energy_share": {k: float(E[np.array(v)].sum() / Etot) for k, v in fams.items()}}


def z_thr(kinds) -> float:
    """Relación señal/ruido mínima para considerar identificable un parámetro."""
    return 2.0


def _responses(inp, net, obs, kinds, cands, lib, workers) -> List[dict]:
    """Respuesta de los sensores a los extremos de cada candidato.
    Añade a cada candidato 'infl' (m, máximo |Δ| medio por sensor) y 'vec' (firma)."""
    groups = {f"G{j}": {"select": {"ids": c["ids"]}} for j, c in enumerate(cands)}
    pcfg = [{"group": f"G{j}", "type": c["type"], "mode": c["mode"], "bounds": c["bounds"],
             "log": c["log"], "name": f"G{j}"} for j, c in enumerate(cands)]
    with en.EpanetProject(inp, lib) as prj:
        params = build_parameters(pcfg, groups, net, prj)
    spec = EvalSpec(inp_file=inp, params=params, obs=obs, objectives=kinds, lib_path=lib)
    x0 = np.array([p.initial for p in params])
    X = [x0]
    for j, p in enumerate(params):
        for v in (p.lower, p.upper):
            x = x0.copy()
            x[j] = v
            X.append(x)
    # perturbación local (10 % del rango) para medir correlaciones entre parámetros
    for j, p in enumerate(params):
        u = p.to_unit(x0[j])
        x = x0.copy()
        x[j] = p.from_unit(u + 0.1 if u <= 0.9 else u - 0.1)
        X.append(x)
    pool = Pool(spec, workers if len(X) > 4 else 1)
    try:
        sims = [d["sim"] for d in pool.map(X, want=("sim",))]
    finally:
        pool.close()
    s0 = sims[0]
    # ruido de medida por sensor (m, o relativo en caudal)
    noise = []
    for i, sn in enumerate(obs.sensors):
        if sn.kind == "FLOW":
            noise.append(FLOW_NOISE_REL * max(float(np.nanmean(np.abs(s0[i]))), 1e-6))
        else:
            noise.append(NOISE.get(sn.kind, 0.15))
    noise = np.array(noise)
    rows_p = [i for i, sn in enumerate(obs.sensors) if sn.kind in ("PRESSURE", "HEAD", "LEVEL")]
    for j, c in enumerate(cands):
        best, vec, meters = 0.0, None, 0.0
        for s in (sims[1 + 2 * j], sims[2 + 2 * j]):
            d = s - s0
            mabs = np.nanmean(np.abs(d), axis=1)            # |Δ| medio por sensor
            snr = float(np.nanmax(mabs / noise))
            if snr >= best:
                best = snr
                meters = float(np.nanmax(mabs[rows_p])) if rows_p else 0.0
                vec = np.nan_to_num((d / noise[:, None]).ravel())
        c["infl"], c["vec"], c["infl_m"] = best, vec, meters
        dl = sims[1 + 2 * len(cands) + j] - s0
        c["vloc"] = np.nan_to_num((dl / noise[:, None]).ravel())
    return cands


def _merge_correlated(cands: List[dict], find) -> List[dict]:
    out = list(cands)
    changed = True
    while changed:
        changed = False
        rough = [c for c in out if c["type"] == "roughness" and c.get("vec") is not None]
        for a in range(len(rough)):
            for b in range(a + 1, len(rough)):
                A, B = rough[a], rough[b]
                va, vb = A.get("vloc", A["vec"]), B.get("vloc", B["vec"])
                if va.std() == 0 or vb.std() == 0:
                    continue
                r = float(np.corrcoef(va, vb)[0, 1])
                if r > 0.97 and A["bounds"] == B["bounds"]:
                    M = {**A, "name": f"{A['name']}+{B['name']}", "ids": A["ids"] + B["ids"],
                         "infl": max(A["infl"], B["infl"]), "vec": A["vec"] + B["vec"],
                         "vloc": A.get("vloc", A["vec"]) + B.get("vloc", B["vec"]),
                         "share": A.get("share", 0) + B.get("share", 0),
                         "reason": f"{A['reason']} + {B['reason']}; se unen porque los sensores no los "
                                   f"distinguen (correlación {r:.2f})"}
                    out = [c for c in out if c is not A and c is not B] + [M]
                    find("info", f"Unidos {A['name']} y {B['name']}",
                         f"Los sensores responden igual a ambos (correlación {r:.2f}): calibrarlos por "
                         "separado daría valores arbitrarios.")
                    changed = True
                    break
            if changed:
                break
    return out


def _confounded(cands: List[dict], excluded: List[dict], find, thr: float) -> None:
    """Parámetros de distinto tipo con efecto casi idéntico en los sensores: el
    calibrador no puede separarlos. Si uno es el nivel de un embalse (dato que suele
    medirse por telemetría) se fija; en otro caso sólo se avisa."""
    act = [c for c in cands if c.get("vec") is not None and c["infl"] >= thr]
    for a in range(len(act)):
        for b in range(a + 1, len(act)):
            A, B = act[a], act[b]
            va, vb = A.get("vloc", A["vec"]), B.get("vloc", B["vec"])
            if A["type"] == B["type"] == "roughness" or va.std() == 0 or vb.std() == 0:
                continue
            r = abs(float(np.corrcoef(va, vb)[0, 1]))
            if r < 0.90:
                continue
            res = [c for c in (A, B) if c["type"] == "reservoir_head"]
            if res and len(res) == 1 and res[0] in cands:
                R, O = res[0], (B if res[0] is A else A)
                cands.remove(R)
                excluded.append({"name": R["name"], "type": R["type"], "n": len(R["ids"]),
                                 "share": 0, "ids": R["ids"], "bounds": R["bounds"], "log": R["log"],
                                 "mode": R["mode"],
                                 "reason": f"tiene el mismo efecto que {O['name']} (correlación {r:.2f}); "
                                           "se fija porque el nivel del embalse suele conocerse por "
                                           "telemetría. Actívelo si no es así y fije el otro."})
                find("aviso", f"{R['name']} y {O['name']} no se pueden separar",
                     f"Producen el mismo efecto en los sensores (correlación {r:.2f}). Se calibra "
                     f"{O['name']} y se deja fijo el nivel del embalse.")
            else:
                find("aviso", f"{A['name']} y {B['name']} casi no se distinguen",
                     f"Correlación {r:.2f} entre sus efectos: el resultado de uno dependerá del otro. "
                     "Si tiene un dato de campo de alguno, fíjelo.")


def _diagnose(rows: List[dict], find) -> None:
    pres = [r for r in rows if r["tipo"] in ("PRESSURE", "HEAD")]
    if pres:
        b = np.array([r["sesgo"] for r in pres])
        med = float(np.median(b))
        mad = float(np.median(np.abs(b - med))) * 1.4826 + 1e-9
        same = np.mean(np.sign(b) == np.sign(med)) if med != 0 else 0
        if abs(med) > 0.5 and same >= 0.75:
            find("aviso", f"Sesgo común de presión: {med:+.2f} m",
                 "Casi todos los sensores tienen el mismo error de nivel: es típico del nivel/cota del "
                 "depósito de cabecera o de la consigna de una válvula reductora. Se propone calibrar el "
                 "nivel del embalse.")
        for r in pres:
            if abs(r["sesgo"] - med) > max(1.0, 3 * mad):
                find("aviso", f"Sensor {r['sensor']}: sesgo distinto al resto ({r['sesgo']:+.2f} m)",
                     "Revise su cota y su ubicación en el modelo (nudo asignado), o una válvula cerrada "
                     "cercana. No conviene forzar la rugosidad para compensarlo.")
        amp = np.array([r["amplitud"] for r in pres if np.isfinite(r["amplitud"])])
        if len(amp):
            ma = float(np.median(amp))
            if ma > 1.3:
                find("aviso", f"El modelo exagera la oscilación diaria (×{ma:.2f})",
                     "Las pérdidas de carga en hora punta son mayores que las reales: rugosidades o "
                     "válvulas aguas arriba demasiado restrictivas, o demanda punta sobreestimada.")
            elif ma < 0.75:
                find("aviso", f"El modelo amortigua la oscilación diaria (×{ma:.2f})",
                     "Faltan pérdidas de carga en hora punta: rugosidad baja, válvulas más cerradas en "
                     "la realidad o demanda punta subestimada.")
        for r in pres:
            if np.isfinite(r["r"]) and r["r"] < 0.5:
                find("aviso", f"Sensor {r['sensor']}: dinámica no reproducida (r = {r['r']:.2f})",
                     "La forma de la curva no se parece a la medida: consumo local importante, "
                     "válvula o bombeo no modelado, o problema del registrador.")
    for r in rows:
        if r["tipo"] == "FLOW" and r["media_obs"] != 0:
            rel = r["sesgo"] / abs(r["media_obs"]) * 100
            if abs(rel) > 5:
                find("aviso", f"Caudal {r['sensor']}: el modelo difiere un {rel:+.1f} %",
                     "Revise la demanda base / patrón (o el % de pérdidas) del sector antes de "
                     "calibrar rugosidades.")
