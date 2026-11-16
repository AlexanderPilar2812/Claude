"""Ejecución de un INP con el motor de EPANET (a través de WNTR) sin reescribir el archivo."""
from __future__ import annotations
import tempfile, warnings
from dataclasses import dataclass
from pathlib import Path
import pandas as pd


@dataclass
class Sim:
    presion: pd.DataFrame      # mca, índice en horas, columnas = nodos
    carga: pd.DataFrame        # msnm
    demanda: pd.DataFrame      # m³/h por nodo (positivo = sale de la red)
    caudal: pd.DataFrame       # m³/h por enlace
    velocidad: pd.DataFrame    # m/s
    avisos: list


def simular(ruta_inp) -> Sim:
    try:
        import wntr
    except ImportError as e:
        raise RuntimeError("Falta el paquete wntr (motor de EPANET). Instalar con: pip install wntr") from e
    ruta_inp = Path(ruta_inp)
    with tempfile.TemporaryDirectory() as tmp, warnings.catch_warnings():
        warnings.simplefilter("ignore")
        rpt, out = str(Path(tmp) / "s.rpt"), str(Path(tmp) / "s.bin")
        en = wntr.epanet.toolkit.ENepanet(version=2.2)
        en.ENopen(str(ruta_inp), rpt, out)
        try:
            en.ENsolveH(); en.ENsaveH() if hasattr(en, "ENsaveH") else None; en.ENreport()
        finally:
            en.ENclose()
        r = wntr.epanet.io.BinFile().read(out)
        txt = Path(rpt).read_text(errors="ignore")
    avisos = sorted({l.strip() for l in txt.splitlines() if "WARNING" in l.upper()})
    h = lambda df: df.set_axis(df.index / 3600.0)
    return Sim(h(r.node["pressure"]), h(r.node["head"]), h(r.node["demand"]) * 3600, h(r.link["flowrate"]) * 3600,
               h(r.link["velocity"]), avisos[:20])
