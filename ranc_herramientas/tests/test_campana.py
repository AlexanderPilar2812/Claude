"""Pruebas de la herramienta de campaña. Ejecutar con:  python -m pytest  (o  python tests/test_campana.py)

El caso de Mata de Plátano LB1 tiene errores conocidos; la herramienta debe encontrarlos sola.
"""
import sys
from pathlib import Path
import pandas as pd
import pytest

RAIZ = Path(__file__).resolve().parents[1]
DATOS = RAIZ / "sectores/mata_platano/sector.yaml"
con_datos = pytest.mark.skipif(not DATOS.exists(), reason="Los datos de Mata de Plátano no van en el repositorio público")
sys.path.insert(0, str(RAIZ))
from ranc.lectores import leer
from ranc.campana import procesar

_res = None
def res():
    global _res
    if _res is None: _res = procesar(RAIZ / "sectores/mata_platano/sector.yaml")
    return _res


@pytest.mark.skipif(not (RAIZ / "ejemplos/TELID_P1.CSV").exists(), reason="Los crudos de ejemplo no van en el repositorio público")
def test_lectores_ejemplo():
    t = leer(RAIZ / "ejemplos/TELID_P1.CSV")
    assert t.meta["formato"] == "telid" and t.meta["serie"] == "108440" and len(t.df) == 800
    assert abs(t.df.valor.max() - 13916 * 0.0101972) < 0.01            # mbar a mca
    q = leer(RAIZ / "ejemplos/RPF400_Q2_EntradaDurazno.CSV")
    assert q.meta["formato"] == "rpf400" and q.meta["sitio"] == "E-DURAZ" and len(q.df) == 1929
    assert int((~q.df.valido).sum()) == 18                              # los 18 errores que declara la cabecera
    h = leer(RAIZ / "ejemplos/HWM_CLOUDA_1.CSV")
    assert h.meta["formato"] == "hwm" and h.meta["reloj_de_red"] and h.meta["utc_menos_local_h"] == 6


@con_datos
def test_pegado_caudal_desfasado():
    g = res().pegado; q = g[(g.punto == "Q1") & (g.estado == "desfasado")]
    assert len(q) == 1 and q.iloc[0].desfase_min == -141 and q.iloc[0].fila_ini == 3      # 2 h 21 min antes
    assert (g[(g.punto == "Q1") & g.estado.str.startswith("sin")].n.sum()) == 260           # segunda descarga no recibida


@con_datos
def test_pegado_L1_tres_tramos():
    g = res().pegado; l = g[g.punto == "L1"]
    assert list(l.desfase_min) == [345, 285, 255] and list(l.fila_ini) == [6, 97, 417]


@con_datos
def test_pegado_R1_R2_correctos():
    g = res().pegado
    assert set(g[g.punto.isin(["R1", "R2"])].estado) == {"correcto"}


@con_datos
def test_offsets():
    P = res().puntos
    assert abs(P["R1"].diag["offset"]["offset"] - 1.312) < 0.01
    assert abs(P["R2"].diag["offset"]["offset"] - 0.050) < 0.01
    assert P["L1"].diag["offset"] is None


@con_datos
def test_huecos_L1():
    assert [h[2] for h in res().puntos["L1"].diag["huecos"]] == [4, 2]


@con_datos
def test_caudal_invalido():
    d = res().puntos["Q1"].crudo
    assert not d[d.valor < -1].valido.any() and not d[d.valor > 100].valido.any()
    assert res().puntos["Q1"].diag["n_equipo"] == 23


@con_datos
def test_cortes_y_relojes():
    e = res().eventos; g = e[e.tipo == "general"]
    c2 = g[g.caida.dt.day == 14]
    assert set(c2.punto) == {"Q1", "R1", "R2", "L1"}
    assert c2.caida.min() == pd.Timestamp("2026-10-14 10:15") and c2.retorno.max() == pd.Timestamp("2026-10-14 15:15")
    r = res().relojes.set_index("punto").veredicto
    assert all(r[p].startswith("reloj correcto") for p in ("Q1", "R1", "R2"))


@con_datos
def test_malla():
    m = res().malla
    assert (m.t.dt.second == 0).all() and (m.t.dt.minute % 15 == 0).all() and m.t.diff().dropna().nunique() == 1
    fila = m[m.t == pd.Timestamp("2026-10-14 12:00")].iloc[0]
    assert pd.isna(fila.Q1) and fila.Q1_marca.startswith("equipo")
    assert (m[["R1", "R2", "L1"]].min() >= 0).all()


if __name__ == "__main__":
    ok = 0
    for n, f in list(globals().items()):
        if n.startswith("test_"): f(); ok += 1; print("ok ", n)
    print(f"{ok} pruebas correctas")
