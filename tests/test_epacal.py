"""Pruebas rápidas: python -m pytest -q tests"""
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from epacal.metrics import evaluate_rules, series_metrics  # noqa: E402
from epacal.observations import Sensor  # noqa: E402
from epacal.optim.nsga2 import fast_non_dominated_sort, hypervolume, nsga2  # noqa: E402
from epacal.optim.selection import choose  # noqa: E402


def test_non_dominated_sort():
    F = np.array([[1, 4], [2, 2], [4, 1], [3, 3], [5, 5]], float)
    fr = fast_non_dominated_sort(F)
    assert set(fr[0]) == {0, 1, 2}
    assert set(fr[1]) == {3}
    assert set(fr[2]) == {4}


def test_constraint_domination():
    F = np.array([[0, 0], [1, 1]], float)
    CV = np.array([1.0, 0.0])          # el primero (mejor) no es factible
    fr = fast_non_dominated_sort(F, CV)
    assert list(fr[0]) == [1]


def test_nsga2_zdt1():
    def zdt1(U):
        f1 = U[:, 0]
        g = 1 + 9 * U[:, 1:].mean(axis=1)
        f2 = g * (1 - np.sqrt(f1 / g))
        return np.column_stack([f1, f2]), np.zeros(len(U))

    r = nsga2(zdt1, 5, pop_size=40, n_gen=60, seed=3, stall_gens=100, log=lambda m: None)
    hv = hypervolume(r.F, np.array([1.1, 1.1]))
    assert hv > 0.80   # óptimo teórico ≈ 0.8517


def test_rules_and_metrics():
    obs = np.array([[10.0, 10, 10, 10], [100, 100, 100, 100]])
    sim = np.array([[10.5, 11, 16, 10], [110, 120, 100, 100]])
    sensors = [Sensor("p", "PRESSURE", "n1"), Sensor("q", "FLOW", "l1")]
    rules = [{"name": "P", "kind": "PRESSURE", "error": "absolute", "threshold": 5,
              "timesteps_pct": 75, "points_pct": 100},
             {"name": "Q", "kind": "FLOW", "error": "relative", "threshold": 15,
              "timesteps_pct": 80, "points_pct": 100}]
    r = evaluate_rules(rules, sensors, obs, sim)
    assert r[0].passed and r[0].obs_ok == 3
    assert not r[1].passed and r[1].obs_ok == 3
    m = series_metrics(obs[0], sim[0])
    assert m["n"] == 4 and m["rmse"] > 0


def test_choose_prefers_rules():
    F = np.array([[1.0, 5.0], [2.0, 2.0], [5.0, 1.0]])
    k = choose(F, "knee", rules_passed=np.array([0, 1, 2]))
    assert k == 2
    assert choose(F, "knee") == 1


@pytest.fixture(scope="module")
def caso(tmp_path_factory):
    from examples.generar_caso_sintetico import main
    d = tmp_path_factory.mktemp("caso")
    main(str(d))
    return d


def _cfg(d, **over):
    import yaml
    with open(os.path.join(ROOT, "examples", "caso_sintetico", "config.yaml"), encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    cfg.update(over)
    p = os.path.join(d, "config_test.yaml")
    with open(p, "w", encoding="utf-8") as fh:
        yaml.safe_dump(cfg, fh)
    return p


def test_losses_reach_target(caso):
    from epacal.workflow import run
    cfg = _cfg(caso)
    res = run(cfg, "losses", os.path.join(caso, "res_loss"), log=lambda m: None)
    s = res["losses"]["summary"].iloc[0]
    assert res["losses"]["converged"]
    assert abs(s["logrado"] - 18.0) < 0.1
    ts = res["losses"]["timeseries"]
    bal = ts["SECTOR_1|consumo"] + ts["SECTOR_1|fugas"]
    assert np.max(np.abs(bal - ts["SECTOR_1|entrada"]) / ts["SECTOR_1|entrada"]) < 0.01


def test_hydraulic_recovers_metal_roughness(caso):
    from epacal.workflow import run
    import yaml
    p = _cfg(caso, workers=2)
    with open(p, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    cfg["hydraulic"]["optimizer"] = {"pop_size": 16, "n_gen": 8, "seed": 2, "stall_gens": 4}
    cfg["hydraulic"]["local_search"] = {"enabled": True, "max_iter": 6}
    cfg["workflow"] = {"mode": "full", "coupled_rounds": 1}
    with open(p, "w", encoding="utf-8") as fh:
        yaml.safe_dump(cfg, fh)
    res = run(p, None, os.path.join(caso, "res_full"), log=lambda m: None)
    h = res["hydraulic"]
    metal = dict(zip([q.name for q in h.params], h.x_final))["METAL_OLD:roughness"]
    assert 1.2 < metal < 2.8          # verdad = 1.90
    assert h.obj_final[1] < 0.5 * h.obj_initial[1]
    assert os.path.exists(res["report"]) and os.path.exists(res["inp"])


def test_epanet22_matches_23(caso):
    """Los resultados derivados en 2.2 (emisor, consumo) coinciden con los de 2.3."""
    import ctypes
    from epacal import engine as en
    try:
        p22 = en.EpanetProject(os.path.join(caso, "modelo_verdad.inp"), version="2.2")
        p23 = en.EpanetProject(os.path.join(caso, "modelo_verdad.inp"), version="2.3")
    except en.EpanetError:
        pytest.skip("se necesitan las bibliotecas 2.2 (wntr) y 2.3 (epyt)")
    assert p22.version == 20200 and p23.version >= 20300
    out = []
    for p in (p22, p23):
        p.open_hydraulics()
        p.lib.EN_initH(p.ph, 0)
        t = ctypes.c_long()
        p.lib.EN_runH(p.ph, ctypes.byref(t))
        e = p.emitter_flows()
        out.append((e.sum(), p.consumer_demands(e).sum(), p.node_values(en.EN_PRESSURE)))
        p.close()
    assert abs(out[0][0] - out[1][0]) / out[1][0] < 1e-3
    assert abs(out[0][1] - out[1][1]) / out[1][1] < 1e-3
    assert np.max(np.abs(out[0][2] - out[1][2])) < 0.01


def test_tank_level_calibration(tmp_path):
    """Nivel de tanque: recupera diámetro, nivel inicial y consigna de la TCV de llenado."""
    import yaml
    from examples.generar_caso_tanque import main as gen_tanque
    from epacal.workflow import run
    gen_tanque(str(tmp_path))
    with open(os.path.join(ROOT, "examples", "caso_tanque", "config.yaml"), encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    cfg["hydraulic"]["optimizer"] = {"goal": "pareto", "pop_size": 16, "n_gen": 8, "seed": 1,
                                     "stall_gens": 4, "max_front": 6}
    cfg["hydraulic"]["local_search"] = {"enabled": True, "max_iter": 8}
    cfg["workers"] = 2
    p = os.path.join(tmp_path, "config.yaml")
    with open(p, "w", encoding="utf-8") as fh:
        yaml.safe_dump(cfg, fh)
    res = run(p, None, os.path.join(tmp_path, "res"), log=lambda m: None)
    h = res["hydraulic"]
    x = dict(zip([q.name for q in h.params], h.x_final))
    assert abs(x["TANQUE:tank_diameter"] - 24.0) < 2.0
    assert abs(x["TANQUE:tank_level"] - 3.0) < 0.2
    i = h.objectives.index("LEVEL")
    assert h.obj_final[i] < 0.1 < h.obj_initial[i]


def test_advisor_selects_tank_parameters(tmp_path):
    """Asistente: propone material, TCV de llenado y tanque; fija el embalse confundido."""
    from examples.generar_caso_tanque import main as gen_tanque
    from epacal.advisor import advise
    gen_tanque(str(tmp_path))
    obs = [(os.path.join(tmp_path, f), k) for f, k in (
        ("calibr_presion.dat", "PRESSURE"), ("calibr_caudal.dat", "FLOW"), ("calibr_nivel.dat", "LEVEL"))]
    res = advise(os.path.join(tmp_path, "modelo_inicial.inp"), obs, log=lambda m: None, workers=2)
    on = {p["name"] for p in res["params"] if p.get("use", True)}
    off = {p["name"] for p in res["params"] if not p.get("use", True)}
    assert {"VALV_TCV_LL", "NIVEL_INI_T1", "DIAM_T1"} <= on
    assert any(n.startswith("METAL") for n in on)
    assert "NIVEL_R1" in off
    assert res["findings"]


def _write_gpkg(path, pts):
    """GeoPackage mínimo de puntos (sin GDAL) para las pruebas."""
    import sqlite3
    import struct
    con = sqlite3.connect(path)
    con.execute("create table gpkg_contents (table_name text, data_type text)")
    con.execute("create table gpkg_geometry_columns (table_name text, column_name text)")
    con.execute("insert into gpkg_contents values ('pts', 'features')")
    con.execute("insert into gpkg_geometry_columns values ('pts', 'geom')")
    con.execute("create table pts (fid integer primary key, geom blob, Nombre text)")
    for name, (x, y) in pts.items():
        blob = b"GP" + bytes([0, 1]) + struct.pack("<i", 0) + struct.pack("<BIdd", 1, 1, x, y)
        con.execute("insert into pts (geom, Nombre) values (?, ?)", (blob, name))
    con.commit()
    con.close()


def test_campaign_cleaning(tmp_path):
    """Campaña sintética: offset y meseta de instalación, picos, oscilación rápida y nivel fuera del agua."""
    import openpyxl
    import pandas as pd
    from examples.generar_caso_tanque import main as gen_tanque
    from epacal import campaign as cp
    from epacal.network import parse_coordinates
    gen_tanque(str(tmp_path))
    inp = os.path.join(tmp_path, "modelo_verdad.inp")
    net = cp.Net(inp)
    ids = [net.nid.index("J0005"), net.nid.index("J0105")]
    sim = net.simulate(ids, [])
    t0 = pd.Timestamp("2026-09-08 00:00")
    idx = t0 + pd.to_timedelta(sim["t"], unit="s")
    rng = np.random.default_rng(1)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    p1 = sim["P"][:, 0] + rng.normal(0, 0.1, len(idx))
    p1[:8] = -0.3                                  # 2 h a presión atmosférica antes de instalar
    p1[[100, 200, 300]] += 25                       # picos aislados
    p2 = sim["P"][:, 1] + rng.normal(0, 0.1, len(idx))
    p2[:8] = -0.3
    fast = idx >= t0 + pd.Timedelta("2D")
    p2[fast] += rng.normal(0, 8.0, fast.sum())     # oscilación más rápida que el registro
    lvl = np.full(len(idx), -1.8)                   # sensor de nivel fuera del agua
    for name, kind, vals in (("P1", "Presión (MCA)", p1), ("P2", "Presión (MCA)", p2),
                             ("Niveles_1", "Nivel (m)", lvl)):
        ws = wb.create_sheet(name)
        ws.append(["NIVELES — TANQUE T1" if name.startswith("Niv") else name])
        ws.append(["Fecha / Hora", kind])
        for t, v in zip(idx, vals):
            ws.append([t.to_pydatetime(), float(v)])
    xls = os.path.join(tmp_path, "campana.xlsx")
    wb.save(xls)
    coords, _ = parse_coordinates(inp)
    gp = os.path.join(tmp_path, "puntos.gpkg")
    _write_gpkg(gp, {"P1": coords["J0005"], "P2": coords["J0105"], "N1": coords["T1"]})
    res = cp.run(inp, xls, [gp], os.path.join(tmp_path, "out"),
                 cfg={"sheets": {"Niveles_1": {"point": "N1"}}}, log=lambda m: None)
    S = {s.name: s for s in res["sensors"]}
    s1, s2, n = S["P1"], S["P2"], S["N_T1"]
    assert s1.element == "J0005" and s2.element == "J0105" and n.element == "T1"
    assert abs(s1.offset + 0.3) < 0.05 and s1.t_on > t0 + pd.Timedelta("1h")
    assert (s1.flag == 2).sum() >= 3 and s1.verdict.startswith("Válido")
    late = s2.flag[t0 + pd.Timedelta("2D 2h"):t0 + pd.Timedelta("3D 20h")]
    assert (late == 1).mean() > 0.9
    assert abs(s2.clean[late.index].mean() - sim["P"][:, 1][np.isin(idx, late.index)].mean()) < 1.0
    assert (n.flag == 9).mean() > 0.9 and "No usar" in n.verdict
    assert os.path.exists(res["dat"]["PRESSURE"])
    assert "J0005" in open(res["dat"]["PRESSURE"], encoding="utf-8").read()
