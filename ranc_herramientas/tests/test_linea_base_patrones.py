"""Línea base y patrones sobre Mata de Plátano LB1 (datos reales)."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

RAIZ = Path(__file__).resolve().parents[1]
DATOS = RAIZ / "sectores/mata_platano/sector.yaml"
con_datos = pytest.mark.skipif(not DATOS.exists(), reason="Los datos de Mata de Plátano no van en el repositorio público")
sys.path.insert(0, str(RAIZ))
from ranc.campana import procesar
from ranc import linea_base as LB, patrones as PT

_r = None
def res():
    global _r
    if _r is None: _r = procesar(RAIZ / "sectores/mata_platano/sector.yaml")
    return _r


@con_datos
def test_mnf_diario_coincide_con_el_cmn():
    lb = LB.calcular(res()); c = lb.comparacion
    mnf = c[c.Concepto.str.startswith("MNF del")]
    assert len(mnf) == 4 and (mnf.Diferencia.abs() < 0.02).all()          # el MNF apenas cambia con la hora corregida


@con_datos
def test_detecta_presion_mal_pegada():
    lb = LB.calcular(res()); c = lb.comparacion
    p = c[c.Concepto.str.startswith("Presión en el MNF")]
    assert (p.Estado == "difiere").all() and (p.Diferencia > 0.10).all()   # L1 corrida varias horas en el libro
    assert any("difieren" in t for g, t in lb.alertas if g == "Alta")


@con_datos
def test_indicadores_coherentes():
    i = LB.calcular(res()).ind
    assert abs(i["lnight_ls"] - (i["qmn_ls"] - i["cn_ls"])) < 1e-9
    assert abs(i["cn_lh"] - 1975.94) < 0.01
    assert 0 < i["factor_noche_dia"] < 1 and i["ili_iwa"] < i["ili_criterio_cmn"]


@con_datos
def test_excel_linea_base(tmp_path):
    import openpyxl
    r = res(); f = LB.exportar(LB.calcular(r), r, tmp_path); wb = openpyxl.load_workbook(f)
    assert {"Resumen", "Alertas", "Diario", "Comparacion"} <= set(wb.sheetnames)
    formulas = [c.value for row in wb["Resumen"].iter_rows() for c in row if isinstance(c.value, str) and c.value.startswith("=")]
    assert len(formulas) >= 14 and not any("{" in x for x in formulas)


@con_datos
def test_patron_y_formatos(tmp_path):
    pt = PT.generar(res()); q = pt.demanda
    assert len(q) == 672 and q.valor_cmh.notna().all() and q.t.iloc[0] == pd.Timestamp("2026-10-13 00:00")
    s = PT.exportar(pt, tmp_path)
    lin = s["csv_demanda"].read_text().splitlines()
    assert lin[0].split(";")[:3] == ["MP-D-7D", "Demanda Mata de Platano 7 dias CMH", "901"] and len(lin[0].split(";")) == 19
    assert len(lin) == 1 + 38 and all(len(l.split(";")) == 19 for l in lin[1:-1]) and "," not in lin[1]   # 672 = 37 × 18 + 6
    assert sum(len(l.split(";")) - 1 for l in lin[1:]) == 672
    d = s["dat_presion"].read_text().splitlines()[0].split("\t")
    assert len(d) == 3 and d[1] == "0.0000" and len(d[2].split(".")[1]) == 4
    qd = pd.read_csv(s["dat_caudal"], sep="\t", header=None)
    m = res().malla; ref = m[m.t == pd.Timestamp("2026-10-13 00:00")].Q1.iloc[0] * 3.6
    assert abs(qd.iloc[0, 2] - ref) < 1e-3                                                                 # caudal en m³/h


def test_patron_tanque():
    t = pd.date_range("2026-01-01", periods=4, freq="15min")
    a = PT.patron_tanque(t, [1.0, 1.5, 2.0, 1.2], 1410.04)
    assert list(a.factor) == [1411.04, 1411.54, 1412.04, 1411.24]
    m = PT.patron_tanque(t, [1.0, 1.5, 2.0, 1.2], 1410.04, "multiplicador", 1409.84)
    assert abs(m.factor.iloc[0] * 1409.84 - 1411.04) < 1e-3 and len(str(m.factor.iloc[0]).split(".")[1]) <= 7
