"""Calibración de pérdidas reales (fugas) y ajuste de patrones de demanda.

Idea física
-----------
El caudal que entra a un sector (DMA) es  Q_in(t) = C(t) + L(t)
  C(t): consumo de los abonados (demanda base × patrón)
  L(t): fugas, dependientes de la presión  q_i = K_i · p_i^N1  (emisores)
        o, con EPANET 2.3, fugas FAVAD distribuidas por tubería.

Si el modelo original reparte TODO el caudal inyectado como demanda (lo
habitual cuando el patrón se construye con el caudalímetro de entrada), al
añadir fugas hay que *quitar* esa misma cantidad del patrón, instante a
instante, para que el caudal total siga cuadrando con la medida.

Algoritmo (por sector, todos los sectores a la vez)
---------------------------------------------------
  1. Pesos de reparto w_i por nudo (longitud de tubería conectada, uniforme,
     proporcional a la demanda o a un factor por familia/edad/material).
  2. K_i = k_d · w_i.  k_d es la única incógnita escalar del sector.
  3. Iteración:
       a. simular -> L_d(t), C_d(t) (por periodo de patrón)
       b. objetivo: % de volumen de pérdidas, caudal nocturno mínimo (MNF),
          caudal medio de fugas o volumen;  k_d <- secante/multiplicativo
       c. patrón:  m'(p) = m(p) · max(Q_in(p) − L(p), 0) / C(p)
  4. Hasta |logrado − objetivo| < tolerancia y patrón estable.

Diferencias frente a Giswater: converge con secante (no un número fijo de
pruebas), corrige el patrón instante a instante con la fuga simulada en ese
instante (la fuga depende de la presión, mayor de noche), admite objetivo
por MNF, varios sectores, reparto ponderado por material/edad y fugas FAVAD.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from . import engine as en
from .network import NetworkInfo, read_mapping_csv


@dataclass
class DMAState:
    name: str
    nodes: np.ndarray                 # índices 0-based de juntas
    weights: np.ndarray               # suma 1 (emisores) o factor por tubería
    pipes: np.ndarray                 # tuberías del sector (modo pipe_leakage)
    target: dict
    k: float = 0.0
    history: List[tuple] = field(default_factory=list)   # (k, logrado)
    patterns: Dict[int, int] = field(default_factory=dict)  # original -> ajustado
    inflow: Optional[np.ndarray] = None      # Q_in por periodo de patrón
    achieved: float = float("nan")
    target_value: float = float("nan")


def _clock_hours(t: np.ndarray, start_clock: int) -> np.ndarray:
    return ((t + start_clock) % 86400) / 3600.0


def _in_window(h: np.ndarray, win) -> np.ndarray:
    a, b = float(win[0]), float(win[1])
    return (h >= a) & (h < b) if a <= b else (h >= a) | (h < b)


def run_losses(cfg: dict, inp_file: str, base_dir: str, out_dir: str,
               log: Callable[[str], None] = print, tag: str = "perdidas") -> dict:
    lc = cfg["losses"]
    lib = cfg.get("epanet_lib")
    os.makedirs(out_dir, exist_ok=True)
    prj = en.EpanetProject(inp_file, lib)
    net = NetworkInfo.from_project(prj)
    net.set_dmas(cfg.get("dmas"))
    method = lc.get("method", "emitters").lower()
    if method == "pipe_leakage" and en.library_version(lib) < 20300:
        raise RuntimeError("pipe_leakage requiere EPANET >= 2.3")
    n_nodes = prj.n_nodes
    f2m3 = en.FLOW_TO_M3S[net.flow_units]

    # ---------------------------------------------------------- tiempos
    if "duration" in lc:
        prj.set_time(en.EN_DURATION, int(float(lc["duration"]) * 3600))
    dur = prj.get_time(en.EN_DURATION)
    pstep = prj.get_time(en.EN_PATTERNSTEP) or 3600
    pstart = prj.get_time(en.EN_PATTERNSTART)
    clock0 = prj.get_time(en.EN_STARTTIME)
    hstep = prj.get_time(en.EN_HYDSTEP) or pstep
    rec = math.gcd(hstep, pstep)
    prj.set_time(en.EN_HYDSTEP, rec)
    times = np.arange(0, dur + 1, rec)
    n_t = len(times)
    dt = np.full(n_t, rec, float)
    if n_t > 1:
        dt[0] = dt[-1] = rec / 2.0   # regla del trapecio
    else:
        dt[:] = 1.0

    # ---------------------------------------------------------- opciones físicas
    expo = float(lc.get("emitter_exponent", prj.get_option(en.EN_EMITEXPON)))
    prj.set_option(en.EN_EMITEXPON, expo)

    # ---------------------------------------------------------- sectores
    dma_cfg = lc.get("dmas") or {"ALL": {"target": lc.get("target", {"type": "percent",
                                                                     "value": 15})}}
    length_w = net.node_pipe_length()
    factor = np.ones(n_nodes)
    pipe_factor = np.ones(len(net.link_ids))
    for fam, fac in (lc.get("allocation_factors") or {}).items():
        grp = (cfg.get("groups", {}).get(fam) or {})
        crit = grp.get("select", grp) if isinstance(grp, dict) else None
        if not crit:
            raise ValueError(f"allocation_factors: grupo '{fam}' no definido")
        li = net.select_links(crit)
        pipe_factor[li] *= float(fac)
        # a los nudos se les asigna el factor medio (ponderado por longitud) de sus tuberías
        num = np.zeros(n_nodes)
        den = np.zeros(n_nodes)
        for i in li:
            for nd in (net.node1[i], net.node2[i]):
                num[nd] += 0.5 * net.length[i] * float(fac)
                den[nd] += 0.5 * net.length[i]
        sel = den > 0
        factor[sel] *= (num[sel] + (length_w[sel] - den[sel])) / length_w[sel].clip(1e-9)
    custom = read_mapping_csv(os.path.join(base_dir, lc["allocation_file"])) \
        if lc.get("allocation_file") else None

    dmas: List[DMAState] = []
    for name, dc in dma_cfg.items():
        nodes = net.dma_nodes(name)
        if len(nodes) == 0:
            raise ValueError(f"Sector '{name}' sin nudos (revise 'dmas' en la configuración)")
        alloc = dc.get("allocation", lc.get("allocation", "length")).lower()
        if alloc == "uniform":
            w = np.ones(len(nodes))
        elif alloc == "demand":
            w = np.abs(net.base_demand[nodes])
        elif alloc == "file":
            w = np.array([float(custom.get(net.node_ids[i], 0)) for i in nodes])
        else:
            w = length_w[nodes].copy()
        w = w * factor[nodes]
        if w.sum() <= 0:
            w = np.ones(len(nodes))
        nodeset = set(nodes.tolist())
        pipes = np.array([i for i in range(len(net.link_ids))
                          if net.link_type[i] in (en.EN_PIPE, en.EN_CVPIPE)
                          and (net.node1[i] in nodeset or net.node2[i] in nodeset)], int)
        dmas.append(DMAState(name=name, nodes=nodes, weights=w / w.sum(), pipes=pipes,
                             target=dict(dc.get("target", lc.get("target", {})))))
    node_dma = np.full(n_nodes, -1)
    for j, d in enumerate(dmas):
        node_dma[d.nodes] = j

    # ---------------------------------------------------------- patrones por sector
    adjust = bool(lc.get("pattern_adjust", True))
    demand_refs = {}   # (node,k) -> pattern idx
    for i in range(n_nodes):
        if net.node_type[i] != en.EN_JUNCTION:
            continue
        for k in range(1, prj.num_demands(i + 1) + 1):
            demand_refs[(i, k)] = prj.demand_pattern(i + 1, k)
    n_per = max(1, int(math.ceil(max(dur, 1) / pstep)))
    orig_patterns: Dict[int, np.ndarray] = {}
    if adjust:
        for j, d in enumerate(dmas):
            used = {p for (i, k), p in demand_refs.items() if node_dma[i] == j}
            for p in sorted(used):
                outside = any(pp == p and node_dma[i] != j for (i, k), pp in demand_refs.items())
                if p == 0:
                    vals = np.ones(n_per)
                    pid = None
                else:
                    vals = prj.get_pattern(p)
                    pid = prj.pattern_id(p)
                orig_patterns.setdefault(p, vals.copy())
                if p == 0 or outside:
                    new_id = (f"{(pid or 'UNO')[:20]}_{d.name[:10]}")
                    newp = prj.add_pattern(new_id, vals)
                    for (i, k), pp in demand_refs.items():
                        if pp == p and node_dma[i] == j:
                            prj.set_demand_pattern(i + 1, k, newp)
                    d.patterns[p] = newp
                    orig_patterns[newp] = vals.copy()
                else:
                    d.patterns[p] = p
                log(f"  sector {d.name}: patrón {pid or '(sin patrón)'} -> "
                    f"{prj.pattern_id(d.patterns[p])}")

    # ---------------------------------------------------------- simulación
    def set_leaks():
        for d in dmas:
            if method == "pipe_leakage":
                for i in d.pipes:
                    prj.set_link(int(i) + 1, en.EN_LEAK_AREA, d.k * pipe_factor[i])
                    if "leak_expansion" in lc:
                        prj.set_link(int(i) + 1, en.EN_LEAK_EXPAN, float(lc["leak_expansion"]))
            else:
                for i, w in zip(d.nodes, d.weights):
                    prj.set_node(int(i) + 1, en.EN_EMITTER, d.k * w)

    def simulate():
        nd = len(dmas)
        L = np.zeros((nd, n_t))
        C = np.zeros((nd, n_t))
        P = np.zeros((nd, n_t))
        node_vol = np.zeros(n_nodes)
        pos = {int(t): k for k, t in enumerate(times)}
        masks = [np.isin(np.arange(n_nodes), d.nodes) for d in dmas]

        def cb(t):
            k = pos.get(t)
            if k is None:
                return
            leak = prj.node_values(en.EN_EMITTERFLOW)
            if method == "pipe_leakage":
                leak = leak + prj.node_values(en.EN_LEAKAGEFLOW)
            full = prj.node_values(en.EN_FULLDEMAND)
            pres = prj.node_values(en.EN_PRESSURE)
            node_vol[:] += leak * dt[k] * f2m3
            for j, m in enumerate(masks):
                L[j, k] = leak[m].sum()
                C[j, k] = full[m].sum()
                P[j, k] = pres[m].mean()

        ok = prj.run_eps(rec, cb)
        return L, C, P, node_vol, ok

    per_idx = (((times + pstart) // pstep).astype(int))   # periodo absoluto

    def per_period(series_2d, plen):
        """Media por índice de patrón (ciclando con la longitud del patrón)."""
        out = np.zeros((series_2d.shape[0], plen))
        cnt = np.zeros(plen)
        idx = per_idx % plen
        np.add.at(cnt, idx, dt)
        for j in range(series_2d.shape[0]):
            np.add.at(out[j], idx, series_2d[j] * dt)
        return out / np.where(cnt > 0, cnt, 1), cnt > 0

    # simulación inicial sin fugas -> caudal de entrada de referencia
    for d in dmas:
        d.k = 0.0
    set_leaks()
    L0, C0, P0, _, ok0 = simulate()
    inflow_t = C0.copy()          # por defecto: la demanda original ES el caudal de entrada
    ref = lc.get("reference_inp")
    if ref and os.path.abspath(ref) != os.path.abspath(inp_file):
        # el modelo de entrada ya trae patrones ajustados (ronda > 1): el caudal de
        # entrada y los patrones "originales" se toman del modelo de referencia
        with en.EpanetProject(ref, lib) as rp:
            rp.set_time(en.EN_DURATION, dur)
            rp.set_time(en.EN_HYDSTEP, rec)
            for i in range(1, rp.n_nodes + 1):
                if rp.node_type(i) == en.EN_JUNCTION:
                    rp.set_node(i, en.EN_EMITTER, 0.0)
            ref_pos = np.array([rp.node_index(net.node_ids[i]) - 1 for i in range(n_nodes)])
            masks = [np.isin(np.arange(rp.n_nodes), ref_pos[d.nodes]) for d in dmas]
            pos = {int(t): k for k, t in enumerate(times)}

            def cb_ref(t):
                k = pos.get(t)
                if k is not None:
                    full = rp.node_values(en.EN_FULLDEMAND)
                    for j, m in enumerate(masks):
                        inflow_t[j, k] = full[m].sum()

            rp.run_eps(rec, cb_ref)
            for p_adj in list(orig_patterns):
                if p_adj == 0:
                    continue
                ridx = rp.pattern_index(prj.pattern_id(p_adj))
                if ridx:
                    orig_patterns[p_adj] = rp.get_pattern(ridx)
        log(f"  caudal de entrada y patrones originales tomados de {os.path.basename(ref)}")
    for j, d in enumerate(dmas):
        f = dma_cfg[d.name].get("inflow_file")
        if f:
            s = pd.read_csv(os.path.join(base_dir, f), sep=None, engine="python")
            s.columns = [c.lower() for c in s.columns]
            ts = pd.to_datetime(s.iloc[:, 0])
            start = pd.Timestamp(lc.get("start") or cfg.get("observations", {}).get("start")
                                 or ts.min().floor("D"))
            secs = (ts - start).dt.total_seconds().to_numpy()
            vals = s.iloc[:, 1].astype(float).to_numpy()
            o = np.argsort(secs)
            inflow_t[j] = np.interp(times, secs[o], vals[o])
            log(f"  sector {d.name}: caudal de entrada medido ({f}), media "
                f"{inflow_t[j].mean():.3f} {net.flow_units}")
    vol_in = (inflow_t * dt).sum(axis=1)

    # objetivos
    hours = _clock_hours(times, clock0)
    for j, d in enumerate(dmas):
        t = d.target
        typ = t.get("type", "percent").lower()
        if typ == "percent":
            d.target_value = float(t["value"]) / 100.0
        elif typ == "mnf":
            win = t.get("night_window", [2, 4])
            m = _in_window(hours, win)
            mnf = float(t["mnf"]) if "mnf" in t else float(inflow_t[j][m].min()
                                                            if t.get("use_min", False)
                                                            else inflow_t[j][m].mean())
            lnc = float(t.get("legit_night_use", 0.0))
            if "legit_night_use_per_conn" in t:
                lnc += float(t["legit_night_use_per_conn"]) * float(t.get("connections", 0))
            d.target_value = max(mnf - lnc, 0.0)
            log(f"  sector {d.name}: MNF {mnf:.3f} − consumo nocturno legítimo {lnc:.3f} "
                f"= fuga nocturna objetivo {d.target_value:.3f} {net.flow_units}")
        elif typ == "flow":
            d.target_value = float(t["value"])
        elif typ == "volume":
            d.target_value = float(t["value"])
        else:
            raise ValueError(f"Tipo de objetivo de pérdidas desconocido: {typ}")
        # arranque: k inicial con presión media inicial
        pm = max(float(P0[j].mean()), 1.0)
        qref = {"percent": d.target_value * vol_in[j] / max(dt.sum(), 1),
                "mnf": d.target_value, "flow": d.target_value,
                "volume": d.target_value / f2m3 / max(dt.sum(), 1)}[typ]
        if method == "pipe_leakage":
            # caudal ≈ Cd·A·sqrt(2gh); A[m2] = área·L/100·1e-6 ; aproximación inicial
            Ltot = float((net.length[d.pipes] * pipe_factor[d.pipes]).sum()) or 1.0
            q_m3s = qref * f2m3
            d.k = q_m3s / (0.6 * Ltot / 100 * 1e-6 * math.sqrt(2 * 9.81 * pm))
        else:
            d.k = float(lc.get("initial_k", qref / pm ** expo))

    def achieved(j, L):
        typ = dmas[j].target.get("type", "percent").lower()
        if typ == "percent":
            return float((L[j] * dt).sum() / max(vol_in[j], 1e-12))
        if typ == "mnf":
            m = _in_window(hours, dmas[j].target.get("night_window", [2, 4]))
            return float(L[j][m].mean())
        if typ == "flow":
            return float((L[j] * dt).sum() / dt.sum())
        return float((L[j] * dt).sum() * f2m3)  # volume (m3)

    tol = float(lc.get("tolerance", 0.001))
    tol_pat = float(lc.get("pattern_tolerance", 0.002))
    max_iter = int(lc.get("max_iter", 40))
    log(f"[{tag}] método {method}, exponente {expo}, {len(dmas)} sector(es), "
        f"tolerancia {tol}, máx. iteraciones {max_iter}")
    converged = False
    it = 0
    for it in range(1, max_iter + 1):
        set_leaks()
        L, C, P, node_vol, ok = simulate()
        max_err, max_pchg = 0.0, 0.0
        for j, d in enumerate(dmas):
            a = achieved(j, L)
            d.achieved = a
            d.history.append((d.k, a))
            rel = abs(a - d.target_value) / max(abs(d.target_value), 1e-12)
            err = abs(a - d.target_value) if d.target.get("type", "percent") == "percent" else rel
            max_err = max(max_err, err)
            # actualización de k (secante con salvaguarda; si no, multiplicativa)
            if a > 0:
                k_new = d.k * d.target_value / a
                if len(d.history) >= 2:
                    (k1, a1), (k2, a2) = d.history[-2], d.history[-1]
                    if abs(a2 - a1) > 1e-14 and abs(k2 - k1) > 1e-14:
                        ks = k2 + (d.target_value - a2) * (k2 - k1) / (a2 - a1)
                        if 0.2 * d.k < ks < 5 * d.k:
                            k_new = ks
                d.k = max(k_new, 0.0)
            else:
                d.k = d.k * 2 if d.k > 0 else 1e-4
            # ajuste del patrón
            if adjust:
                for p_orig, p_adj in d.patterns.items():
                    cur = prj.get_pattern(p_adj)
                    plen = len(cur)
                    Lp, have = per_period(L[j:j + 1], plen)
                    Cp, _ = per_period(C[j:j + 1], plen)
                    Qp, _ = per_period(inflow_t[j:j + 1], plen)
                    target_c = np.maximum(Qp[0] - Lp[0], 0.0)
                    ratio = np.where(Cp[0] > 1e-12, target_c / np.maximum(Cp[0], 1e-12), 1.0)
                    ratio = np.where(have, ratio, 1.0)
                    new = cur * ratio
                    chg = np.max(np.abs(new - cur) / np.maximum(np.abs(cur), 1e-9)) if plen else 0
                    max_pchg = max(max_pchg, float(chg))
                    prj.set_pattern(p_adj, new)
        txt = " | ".join(f"{d.name}: {(d.achieved * 100 if d.target.get('type', 'percent') == 'percent' else d.achieved):.4g}"
                         f" (obj {(d.target_value * 100 if d.target.get('type', 'percent') == 'percent' else d.target_value):.4g})"
                         for d in dmas)
        log(f"  iter {it:2d}: {txt} | cambio patrón {max_pchg * 100:.3f} %")
        if max_err < tol and max_pchg < tol_pat:
            converged = True
            # el último set de k/patrón ya es el bueno: rehacer k con el logrado
            for d in dmas:
                d.k = d.history[-1][0]
            break

    set_leaks()
    L, C, P, node_vol, ok = simulate()
    for j, d in enumerate(dmas):
        d.achieved = achieved(j, L)

    # ---------------------------------------------------------- salidas
    base = os.path.splitext(os.path.basename(cfg.get("name", "calibracion")))[0]
    out_inp = os.path.join(out_dir, f"{base}_calibrado_perdidas.inp")
    prj.save_inp(out_inp)
    emit = prj.node_values(en.EN_EMITTER)
    rows = []
    for j, d in enumerate(dmas):
        for i in d.nodes:
            rows.append({"junction_id": net.node_ids[i], "dma": d.name,
                         "coef_emisor": emit[i], "volumen_fuga_m3": node_vol[i]})
    ndf = pd.DataFrame(rows).sort_values("volumen_fuga_m3", ascending=False)
    ndf.to_csv(os.path.join(out_dir, "emisores_por_nudo.csv"), index=False)

    summ = []
    pat_rows = []
    for j, d in enumerate(dmas):
        vin = vol_in[j] * f2m3
        vl = float((L[j] * dt).sum() * f2m3)
        typ = d.target.get("type", "percent")
        summ.append({"dma": d.name, "objetivo_tipo": typ,
                     "objetivo": d.target_value * (100 if typ == "percent" else 1),
                     "logrado": d.achieved * (100 if typ == "percent" else 1),
                     "perdidas_pct_volumen": 100 * vl / max(vin, 1e-12),
                     "volumen_entrada_m3": vin, "volumen_perdidas_m3": vl,
                     "volumen_consumo_m3": float((C[j] * dt).sum() * f2m3),
                     "nudos": len(d.nodes), "nudos_con_fuga": int((emit[d.nodes] > 0).sum())
                     if method == "emitters" else len(d.nodes),
                     "k_total": d.k, "presion_media": float(P[j].mean()),
                     "iteraciones": len(d.history)})
        for p_orig, p_adj in d.patterns.items():
            adj = prj.get_pattern(p_adj)
            org = orig_patterns.get(p_adj, orig_patterns.get(p_orig))
            Lp, _ = per_period(L[j:j + 1], len(adj))
            for k in range(len(adj)):
                pat_rows.append({"dma": d.name, "patron": prj.pattern_id(p_adj),
                                 "periodo": k + 1, "original": org[k] if k < len(org) else np.nan,
                                 "ajustado": adj[k], "fuga_media": Lp[0, k]})
    sdf = pd.DataFrame(summ)
    sdf.to_csv(os.path.join(out_dir, "resumen_perdidas_dma.csv"), index=False)
    pdf = pd.DataFrame(pat_rows)
    pdf.to_csv(os.path.join(out_dir, "patrones_original_vs_ajustado.csv"), index=False)
    ts = pd.DataFrame({"t_s": times})
    for j, d in enumerate(dmas):
        ts[f"{d.name}|entrada"] = inflow_t[j]
        ts[f"{d.name}|fugas"] = L[j]
        ts[f"{d.name}|consumo"] = C[j]
        ts[f"{d.name}|presion_media"] = P[j]
    ts.to_csv(os.path.join(out_dir, "balance_temporal_perdidas.csv"), index=False)
    prj.close()
    res = {"inp": out_inp, "converged": converged, "iterations": it, "method": method,
           "exponent": expo, "summary": sdf, "nodes": ndf, "patterns": pdf, "timeseries": ts,
           "flow_units": net.flow_units, "history": {d.name: d.history for d in dmas}}
    with open(os.path.join(out_dir, "resumen_perdidas.json"), "w", encoding="utf-8") as fh:
        json.dump({"inp": out_inp, "convergido": converged, "iteraciones": it,
                   "metodo": method, "exponente": expo,
                   "sectores": sdf.to_dict("records")}, fh, indent=2, ensure_ascii=False,
                  default=float)
    for _, r in sdf.iterrows():
        log(f"  {r['dma']}: objetivo {r['objetivo']:.4g} -> logrado {r['logrado']:.4g} | "
            f"pérdidas {r['volumen_perdidas_m3']:.2f} m3 de {r['volumen_entrada_m3']:.2f} m3 "
            f"({r['perdidas_pct_volumen']:.2f} %)")
    log(f"[{tag}] {'CONVERGIDO' if converged else 'NO convergido'} en {it} iteraciones. "
        f"INP: {out_inp}")
    return res
