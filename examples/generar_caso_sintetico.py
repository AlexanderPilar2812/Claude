"""Genera un caso de prueba sintético con solución conocida.

  * modelo_verdad.inp     -> la "realidad" (rugosidades, TCV y fugas reales)
  * modelo_inicial.inp    -> el modelo sin calibrar (rugosidades de catálogo,
                             TCV sin consigna, sin fugas y con el patrón
                             construido a partir del caudalímetro de entrada)
  * observados.csv        -> medidas de campo (formato largo) con ruido,
                             picos espurios y huecos
  * sensores.csv          -> metadatos de sensores
  * caudal_entrada.csv    -> caudalímetro de entrada al sector
  * verdad.json           -> valores verdaderos para comprobar la calibración

Uso:  python examples/generar_caso_sintetico.py examples/caso_sintetico
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from epacal import engine as en  # noqa: E402

TRUE = {"PLASTIC_OLD": 0.0065, "METAL_OLD": 1.90, "TCV_OLD": 6.4}
ORIG = {"PLASTIC_OLD": 0.0015, "METAL_OLD": 0.2591, "TCV_OLD": 0.0}
LEAK_PCT_APPROX = 18.0


def build(out_dir: str, seed: int = 7):
    rng = np.random.default_rng(seed)
    nx, ny = 18, 16
    nodes, coords, elev = [], {}, {}
    for i in range(nx):
        for j in range(ny):
            nid = f"J{i:02d}{j:02d}"
            nodes.append(nid)
            coords[nid] = (i * 100 + rng.normal(0, 8), j * 100 + rng.normal(0, 8))
            elev[nid] = 18 + 1.6 * i + 0.8 * j + rng.normal(0, 1.2)
    # aristas de la malla: árbol generador + bucles aleatorios
    edges = []
    for i in range(nx):
        for j in range(ny):
            if i + 1 < nx:
                edges.append(((i, j), (i + 1, j)))
            if j + 1 < ny:
                edges.append(((i, j), (i, j + 1)))
    rng.shuffle(edges)
    parent = {}

    def find(a):
        while parent.get(a, a) != a:
            a = parent[a]
        return a

    tree, extra = [], []
    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
            tree.append((a, b))
        else:
            extra.append((a, b))
    keep = tree + extra[: int(0.45 * len(extra))]
    # subsector de la derecha (i >= 13) alimentado sólo por 2 TCV
    sub = lambda n: n[0] >= 13
    links = []
    tcv_cands = []
    for a, b in keep:
        if sub(a) != sub(b):
            tcv_cands.append((a, b))
        else:
            links.append((a, b))
    tcv_cands = sorted(tcv_cands, key=lambda e: abs(e[0][1] - 4) + abs(e[0][1] - 11))
    tcvs = [e for e in tcv_cands if e[0][1] in (4, 11) or e[1][1] in (4, 11)][:2]
    if len(tcvs) < 2:
        tcvs = tcv_cands[:2]
    # se garantizan esas dos conexiones
    nid = lambda n: f"J{n[0]:02d}{n[1]:02d}"
    pipes = []
    for k, (a, b) in enumerate(links):
        L = float(np.hypot(*(np.subtract(coords[nid(a)], coords[nid(b)])))) * rng.uniform(1.0, 1.3)
        main = (a[1] == b[1] == 8) or (a[0] == b[0] == 2)
        D = rng.choice([250, 200]) if main else rng.choice([90, 110, 125, 160], p=[.3, .3, .25, .15])
        fam = "METAL_OLD" if (a[0] < 7 and b[0] < 7) else "PLASTIC_OLD"
        pipes.append((f"P{k:04d}", nid(a), nid(b), L, D, fam))
    # entrada: embalse -> J0008 por tubería principal
    pipes.append(("PMAIN", "R1", "J0008", 350.0, 300, "METAL_OLD"))
    valves = [(f"TCV{k + 1}", nid(a), nid(b), 150) for k, (a, b) in enumerate(tcvs)]

    # patrón de consumo real (15 min, 96 valores) y demanda base
    t = np.arange(96) / 4.0
    cons = (0.55 + 0.55 * np.exp(-((t - 8.5) / 2.2) ** 2) + 0.35 * np.exp(-((t - 14) / 2.5) ** 2)
            + 0.45 * np.exp(-((t - 20.5) / 2.0) ** 2) - 0.25 * np.exp(-((t - 3.5) / 2.0) ** 2))
    cons = cons / cons.mean()
    base = {n: max(0.0, rng.gamma(2.0, 0.42)) for n in nodes}   # m3/h
    return dict(nodes=nodes, coords=coords, elev=elev, pipes=pipes, valves=valves,
                base=base, cons=cons, rng=rng)


def write_inp(path, g, rough, tcv_k, pattern, emitters=None, title="caso"):
    L = ["[TITLE]", title, "", "[JUNCTIONS]", ";ID Elev Demand Pattern"]
    for n in g["nodes"]:
        L.append(f" {n}\t{g['elev'][n]:.2f}\t{g['base'][n]:.4f}\tPAT-DIA")
    L += ["", "[RESERVOIRS]", " R1\t92.0", "", "[PIPES]", ";ID N1 N2 Length Diam Rough Minor Status"]
    for pid, a, b, Ln, D, fam in g["pipes"]:
        L.append(f" {pid}\t{a}\t{b}\t{Ln:.1f}\t{D}\t{rough[fam]}\t0\tOpen")
    L += ["", "[VALVES]", ";ID N1 N2 Diam Type Setting Minor"]
    for vid, a, b, D in g["valves"]:
        L.append(f" {vid}\t{a}\t{b}\t{D}\tTCV\t{tcv_k}\t0")
    L += ["", "[TAGS]"]
    for pid, a, b, Ln, D, fam in g["pipes"]:
        L.append(f" LINK\t{pid}\t{fam}")
    for vid, *_ in g["valves"]:
        L.append(f" LINK\t{vid}\tTCV_OLD")
    L += ["", "[PATTERNS]"]
    for k in range(0, len(pattern), 8):
        L.append(" PAT-DIA\t" + "\t".join(f"{v:.5f}" for v in pattern[k:k + 8]))
    if emitters:
        L += ["", "[EMITTERS]"] + [f" {n}\t{c:.6g}" for n, c in emitters.items()]
    L += ["", "[TIMES]", " Duration 96:00", " Hydraulic Timestep 0:05", " Pattern Timestep 0:15",
          " Report Timestep 0:05", " Start ClockTime 0:00", "",
          "[OPTIONS]", " Units CMH", " Headloss D-W", " Trials 40", " Accuracy 0.001",
          " Unbalanced Continue 10", " Demand Model PDA", " Minimum Pressure 0",
          " Required Pressure 10", " Pressure Exponent 0.5", " Emitter Exponent 0.5", "",
          "[COORDINATES]"]
    for n in g["nodes"]:
        x, y = g["coords"][n]
        L.append(f" {n}\t{x:.1f}\t{y:.1f}")
    L.append(" R1\t-150\t800")
    L += ["", "[END]", ""]
    with open(path, "w") as fh:
        fh.write("\n".join(L))


def simulate_truth(inp, sensors_p, sensors_q, step=300):
    prj = en.EpanetProject(inp)
    P = {s: [] for s in sensors_p}
    Q = {s: [] for s in sensors_q}
    T, leak, dem = [], [], []
    pi = {s: prj.node_index(s) for s in sensors_p}
    qi = {s: prj.link_index(s) for s in sensors_q}

    def cb(t):
        T.append(t)
        for s, i in pi.items():
            P[s].append(prj.get_node(i, en.EN_PRESSURE))
        for s, i in qi.items():
            Q[s].append(abs(prj.get_link(i, en.EN_FLOW)))
        leak.append(prj.node_values(en.EN_EMITTERFLOW).sum())
        dem.append(prj.node_values(en.EN_FULLDEMAND).sum())

    prj.run_eps(step, cb)
    prj.close()
    return np.array(T), P, Q, np.array(leak), np.array(dem)


def main(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    g = build(out_dir)
    rng = g["rng"]
    # fugas reales: repartidas por longitud con variabilidad lognormal (zonas peores)
    lw = {n: 0.0 for n in g["nodes"]}
    for pid, a, b, Ln, D, fam in g["pipes"]:
        f = 2.0 if fam == "METAL_OLD" else 1.0
        for n in (a, b):
            if n in lw:
                lw[n] += 0.5 * Ln * f
    noise = {n: rng.lognormal(0, 0.6) for n in g["nodes"]}
    raw = {n: lw[n] * noise[n] for n in g["nodes"]}
    tot = sum(raw.values())
    # coeficiente total calibrado para ~18 % (iteración simple)
    k_total = 2.0
    truth_inp = os.path.join(out_dir, "modelo_verdad.inp")
    sensors_p = ["J0203", "J0512", "J0807", "J0414", "J1001", "J1109", "J1213", "J1402",
                 "J1508", "J1614", "J1705", "J0615"]
    sensors_q = ["PMAIN", "TCV1"]
    for _ in range(8):
        em = {n: k_total * raw[n] / tot for n in g["nodes"]}
        write_inp(truth_inp, g, TRUE, TRUE["TCV_OLD"], g["cons"], em, "MODELO VERDAD")
        T, P, Q, leak, dem = simulate_truth(truth_inp, sensors_p, sensors_q)
        pct = 100 * leak.sum() / (leak.sum() + dem.sum())
        k_total *= LEAK_PCT_APPROX / pct
    em = {n: k_total * raw[n] / tot for n in g["nodes"]}
    write_inp(truth_inp, g, TRUE, TRUE["TCV_OLD"], g["cons"], em, "MODELO VERDAD")
    T, P, Q, leak, dem = simulate_truth(truth_inp, sensors_p, sensors_q)
    inflow = leak + dem
    true_pct = 100 * leak.sum() / inflow.sum()

    # patrón del modelo inicial: caudal de entrada medio por periodo de 15 min (sin fugas)
    per = (T // 900) % 96
    infl_per = np.array([inflow[per == k].mean() for k in range(96)])
    base_sum = sum(g["base"].values())
    model_pat = infl_per / base_sum
    write_inp(os.path.join(out_dir, "modelo_inicial.inp"), g, ORIG, ORIG["TCV_OLD"],
              model_pat, None, "MODELO SIN CALIBRAR")

    # observaciones con ruido, picos y huecos (cada 5 min)
    start = pd.Timestamp("2026-07-09 00:00:00")
    rows = []
    for s, v in P.items():
        v = np.array(v) + rng.normal(0, 0.15, len(v))
        spikes = rng.random(len(v)) < 0.004
        v[spikes] -= rng.uniform(4, 10, spikes.sum())
        for t, x in zip(T, v):
            if rng.random() < 0.02:
                continue
            rows.append((s, "PRESSURE", s, start + pd.Timedelta(seconds=int(t)), round(x, 3)))
    for s, v in Q.items():
        v = np.array(v) * (1 + rng.normal(0, 0.015, len(v)))
        for t, x in zip(T, v):
            rows.append((s, "FLOW", s, start + pd.Timedelta(seconds=int(t)), round(x, 3)))
    df = pd.DataFrame(rows, columns=["sensor", "type", "element", "timestamp", "value"])
    df.to_csv(os.path.join(out_dir, "observados.csv"), index=False)
    pd.DataFrame({"sensor": sensors_p + sensors_q,
                  "type": ["PRESSURE"] * len(sensors_p) + ["FLOW"] * len(sensors_q),
                  "element": sensors_p + sensors_q,
                  "weight": 1.0}).to_csv(os.path.join(out_dir, "sensores.csv"), index=False)
    pd.DataFrame({"timestamp": [start + pd.Timedelta(seconds=int(t)) for t in T],
                  "caudal_m3h": np.round(inflow * (1 + rng.normal(0, 0.01, len(T))), 3)}
                 ).to_csv(os.path.join(out_dir, "caudal_entrada.csv"), index=False)
    with open(os.path.join(out_dir, "verdad.json"), "w") as fh:
        json.dump({"rugosidad_y_tcv": TRUE, "originales": ORIG,
                   "perdidas_pct_real": true_pct, "k_emisores_total": k_total}, fh, indent=2)
    print(f"Caso generado en {out_dir}: fugas reales {true_pct:.2f} %, "
          f"{len(g['nodes'])} nudos, {len(g['pipes'])} tuberías, {len(g['valves'])} TCV")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__),
                                                            "caso_sintetico"))
