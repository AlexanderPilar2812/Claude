"""Caso de prueba con TANQUE de cola y solución conocida (calibración de nivel).

Red: embalse R1 -> tubería PIN -> TCV de llenado TCV_LL -> tanque T1 -> PMAIN -> red.
Sensores: nivel del tanque (LEVEL), caudal de llenado (PIN), caudal de salida
(PMAIN) y 12 presiones, cada 5 min durante 4 días, con ruido.

Verdad / modelo inicial:
    diámetro del tanque      24 m   /  18 m
    nivel inicial            3.0 m  /  4.2 m
    K de la TCV de llenado   18     /  4
    rugosidad METAL_OLD      1.90   /  0.2591 mm

Uso:  python examples/generar_caso_tanque.py examples/caso_tanque
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from epacal import engine as en  # noqa: E402
from examples.generar_caso_sintetico import build  # noqa: E402

R1_HEAD = float(os.environ.get("R1_HEAD", 96.0))
TRUE = {"diam": 24.0, "init": 3.0, "tcv": 18.0, "METAL_OLD": 1.90, "PLASTIC_OLD": 0.0015}
ORIG = {"diam": 18.0, "init": 4.2, "tcv": 4.0, "METAL_OLD": 0.2591, "PLASTIC_OLD": 0.0015}


def write_inp(path, g, v, title):
    L = ["[TITLE]", title, "", "[JUNCTIONS]"]
    for n in g["nodes"]:
        L.append(f" {n}\t{g['elev'][n]:.2f}\t{g['base'][n]:.4f}\tPAT-DIA")
    L += [" NIN\t75\t0", " NLL\t75\t0", "", "[RESERVOIRS]", f" R1\t{R1_HEAD}", "",
          "[TANKS]", ";ID Elev InitLvl MinLvl MaxLvl Diam MinVol",
          f" T1\t82.0\t{v['init']}\t0.3\t7.0\t{v['diam']}\t0", "", "[PIPES]"]
    for pid, a, b, Ln, D, fam in g["pipes"]:
        if pid == "PMAIN":
            a = "T1"
        L.append(f" {pid}\t{a}\t{b}\t{Ln:.1f}\t{D}\t{v[fam]}\t0\tOpen")
    L.append(f" PIN\tR1\tNIN\t900\t250\t{v['METAL_OLD']}\t0\tOpen")
    L.append(f" PIN2\tNLL\tT1\t30\t250\t{v['METAL_OLD']}\t0\tOpen")
    L += ["", "[VALVES]"]
    for vid, a, b, D in g["valves"]:
        L.append(f" {vid}\t{a}\t{b}\t{D}\tTCV\t0\t0")
    L.append(f" TCV_LL\tNIN\tNLL\t250\tTCV\t{v['tcv']}\t0")
    L += ["", "[TAGS]"]
    for pid, a, b, Ln, D, fam in g["pipes"]:
        L.append(f" LINK\t{pid}\t{fam}")
    L += [" LINK\tPIN\tMETAL_OLD", " LINK\tPIN2\tMETAL_OLD", "", "[PATTERNS]"]
    for k in range(0, len(g["cons"]), 8):
        L.append(" PAT-DIA\t" + "\t".join(f"{x:.5f}" for x in g["cons"][k:k + 8]))
    L += ["", "[TIMES]", " Duration 96:00", " Hydraulic Timestep 0:05", " Pattern Timestep 0:15",
          " Report Timestep 0:05", " Start ClockTime 0:00", "", "[OPTIONS]", " Units CMH",
          " Headloss D-W", " Trials 40", " Accuracy 0.001", " Unbalanced Continue 10",
          " Demand Model PDA", " Minimum Pressure 0", " Required Pressure 10", "",
          "[COORDINATES]"]
    for n in g["nodes"]:
        x, y = g["coords"][n]
        L.append(f" {n}\t{x:.1f}\t{y:.1f}")
    L += [" R1\t-900\t800", " NIN\t-600\t800", " NLL\t-450\t800", " T1\t-300\t800", "", "[END]", ""]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))


def main(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    g = build(out_dir)
    rng = np.random.default_rng(11)
    g["base"] = {n: b * 0.85 for n, b in g["base"].items()}
    truth = os.path.join(out_dir, "modelo_verdad.inp")
    write_inp(truth, g, TRUE, "TANQUE - VERDAD")
    write_inp(os.path.join(out_dir, "modelo_inicial.inp"), g, ORIG, "TANQUE - SIN CALIBRAR")
    sens_p = ["J0203", "J0512", "J0807", "J0414", "J1001", "J1109", "J1213", "J1402",
              "J1508", "J1614", "J1705", "J0615"]
    prj = en.EpanetProject(truth)
    it = prj.node_index("T1")
    rec = {s: [] for s in sens_p + ["T1", "PIN", "PMAIN"]}
    T = []

    def cb(t):
        T.append(t)
        for s in sens_p:
            rec[s].append(prj.get_node(prj.node_index(s), en.EN_PRESSURE))
        rec["T1"].append(prj.get_node(it, en.EN_HEAD) - prj.get_node(it, en.EN_ELEVATION))
        for s in ("PIN", "PMAIN"):
            rec[s].append(abs(prj.get_link(prj.link_index(s), en.EN_FLOW)))

    prj.run_eps(300, cb)
    prj.close()
    lvl = np.array(rec["T1"])
    # ficheros .dat de calibración (id  hora  valor), como los de EPANET/Giswater
    def dat(name, ids, noise, rel=False):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
            for s in ids:
                v = np.array(rec[s])
                v = v * (1 + rng.normal(0, noise, len(v))) if rel else v + rng.normal(0, noise, len(v))
                for t, x in zip(T, v):
                    fh.write(f"{s}\t{t / 3600:.4f}\t{x:.4f}\n")
    dat("calibr_nivel.dat", ["T1"], 0.02)
    dat("calibr_caudal.dat", ["PIN", "PMAIN"], 0.015, rel=True)
    dat("calibr_presion.dat", sens_p, 0.15)
    with open(os.path.join(out_dir, "verdad.json"), "w", encoding="utf-8") as fh:
        json.dump({"verdad": TRUE, "inicial": ORIG,
                   "nivel_tanque_min_max": [float(lvl.min()), float(lvl.max())]}, fh, indent=2)
    print(f"Caso tanque en {out_dir}: nivel real entre {lvl.min():.2f} y {lvl.max():.2f} m")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__),
                                                            "caso_tanque"))
