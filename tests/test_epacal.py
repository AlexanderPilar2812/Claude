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
    with open(os.path.join(ROOT, "examples", "caso_sintetico", "config.yaml")) as fh:
        cfg = yaml.safe_load(fh)
    cfg.update(over)
    p = os.path.join(d, "config_test.yaml")
    with open(p, "w") as fh:
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
    with open(p) as fh:
        cfg = yaml.safe_load(fh)
    cfg["hydraulic"]["optimizer"] = {"pop_size": 16, "n_gen": 8, "seed": 2, "stall_gens": 4}
    cfg["hydraulic"]["local_search"] = {"enabled": True, "max_iter": 6}
    cfg["workflow"] = {"mode": "full", "coupled_rounds": 1}
    with open(p, "w") as fh:
        yaml.safe_dump(cfg, fh)
    res = run(p, None, os.path.join(caso, "res_full"), log=lambda m: None)
    h = res["hydraulic"]
    metal = dict(zip([q.name for q in h.params], h.x_final))["METAL_OLD:roughness"]
    assert 1.2 < metal < 2.8          # verdad = 1.90
    assert h.obj_final[1] < 0.5 * h.obj_initial[1]
    assert os.path.exists(res["report"]) and os.path.exists(res["inp"])
