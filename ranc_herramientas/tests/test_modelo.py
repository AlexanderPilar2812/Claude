"""Auditor de INP, evaluador de calibración, pérdidas y escenarios sobre la red sintética."""
import sys
from pathlib import Path
import numpy as np
import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ)); sys.path.insert(0, str(Path(__file__).parent))
from red_prueba import inp_texto
from ranc import auditor_inp as AU
from ranc.inp import Inp

wntr = pytest.importorskip("wntr")
from ranc.simular import simular
from ranc import calibracion as CA, perdidas as PE, escenarios as ES


@pytest.fixture(scope="module")
def red(tmp_path_factory):
    d = tmp_path_factory.mktemp("red"); b = d / "buena.inp"; m = d / "mala.inp"
    b.write_text(inp_texto(), encoding="utf-8"); m.write_text(inp_texto(True), encoding="utf-8")
    return d, b, m


def test_inp_ida_y_vuelta(red):
    d, b, m = red; I = Inp.leer(b); f = d / "copia.inp"; I.guardar(f)
    assert [x.split() for x in Inp.leer(f).texto().splitlines()] == [x.split() for x in I.texto().splitlines()]


def test_auditor_red_limpia(red):
    a = AU.auditar(red[1])
    assert not (a.hallazgos.Gravedad == "Alta").any() and abs(a.resumen["demanda_base_total"] - 1) < 1e-4


def test_auditor_encuentra_errores(red):
    a = AU.auditar(red[2], n1=1.5); t = " | ".join(a.hallazgos.Hallazgo)
    for clave in ("no ASCII", "DEMAND MULTIPLIER = 1,1", "cota 0", "no 1", "diámetro fuera de rango", "consigna queda anulada",
                  "no existen como enlace", "sin conexión con ninguna fuente", "EMITTER EXPONENT", "columna de [JUNCTIONS] está en cero",
                  "más de una categoría", "menos de 0,5 m"):
        assert clave in t, clave


def test_auditor_corrige(red):
    d, b, m = red; a = AU.auditar(m, corregir=True); s = AU.exportar(a, m, d)
    t = " | ".join(AU.auditar(s["inp"]).hallazgos.Hallazgo)
    for arreglado in ("no ASCII", "DEMAND MULTIPLIER", "diámetro fuera de rango", "consigna queda anulada"): assert arreglado not in t
    assert "cota 0" in t                                # lo que no admite arreglo automático sigue señalado


def test_evaluador(red):
    d, b, m = red; S = simular(b); rng = np.random.default_rng(1)
    with open(d / "p.dat", "w") as f:
        for n, sesgo in (("N3", 0), ("N8", 0.4), ("N12", -0.6), ("N18", 0.2), ("N24", 7.0)):
            for h, v in S.presion[n].iloc[:-1].items(): f.write(f"{n}\t{h:.4f}\t{v + sesgo + rng.normal(0, 0.4):.4f}\n")
    with open(d / "q.dat", "w") as f:
        for h, v in S.caudal["P0"].iloc[:-1].items(): f.write(f"P0\t{h:.4f}\t{v * (1 + rng.normal(0, 0.03)):.4f}\n")
    ev = CA.evaluar(b, {"presion": d / "p.dat", "caudal": d / "q.dat"}); p = ev.puntos.set_index("punto")
    assert p.cumple[["N3", "N8", "N12", "N18", "P0"]].all() and not p.cumple["N24"]
    assert abs(p.sesgo["N24"] + 7.0) < 0.1 and p.rmse["N3"] < 0.6
    assert ev.resumen.set_index("Criterio").loc["Piezométrica", "Puntos que cumplen"] == 4
    assert CA.exportar(ev, "prueba", d)["excel"].exists()


def test_perdidas_cumplen_objetivo_y_balance(red):
    d, b, m = red; p = PE.calibrar(b, pct=0.30, n1=1.5); r = p.resumen
    assert abs(r["pct_fuga"] - 0.30) < 0.003 and not p.avisos
    s = PE.exportar(p, b, d); S0 = simular(b); S1 = simular(s["inp"])
    assert (S1.caudal["P0"] - S0.caudal["P0"]).abs().max() < 0.05          # el caudal de entrada sigue siendo el medido
    I = Inp.leer(s["inp"]); assert I.opciones()["EMITTER EXPONENT"] == "1.5" and len(I.filas("EMITTERS")) == r["nodos_con_emisor"]
    assert abs(p.emisores.fuga_m3.sum() - r["fuga_m3"]) < 0.5


def test_perdidas_objetivo_imposible_avisa(red):
    p = PE.calibrar(red[1], pct=0.95, n1=1.5)
    assert any("recorta" in t for g, t in p.avisos)


def test_escenarios(red):
    d, b, m = red
    es = ES.correr(b, {"incendio": [{"nodo": "N24"}, {"nodo": "N6"}], "contingencia": [{"cerrar": "PL1"}, {"cerrar": "NOEXISTE"}], "horizonte": {"factor": 1.2}})
    r = es.resumen; act = r[r.Escenario == "Estado actual"].set_index("Instante")
    assert act.loc["Caudal mínimo", "Caudal de entrada (m³/h)"] < act.loc["Caudal medio", "Caudal de entrada (m³/h)"] < act.loc["Caudal punta", "Caudal de entrada (m³/h)"]
    inc = r[r.Escenario.str.startswith("Incendio")].set_index("Escenario")
    assert inc.loc["Incendio en N6 (32 L/s)", "Cumple"] == "Sí" and abs(inc.loc["Incendio en N6 (32 L/s)", "Caudal de incendio servido (L/s)"] - 32) < 0.2
    assert inc.loc["Incendio en N24 (32 L/s)", "Cumple"] == "No"            # extremo de red en DN75
    hz = r[r.Escenario.str.startswith("Horizonte")].set_index("Instante")
    assert abs(hz.loc["Caudal punta", "Caudal de entrada (m³/h)"] / act.loc["Caudal punta", "Caudal de entrada (m³/h)"] - 1.2) < 0.01
    assert any("NOEXISTE" in t for g, t in es.avisos)
    assert ES.exportar(es, "prueba", d).exists()
