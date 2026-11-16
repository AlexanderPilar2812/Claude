"""Lectores de archivos crudos por equipo.

Cada lector devuelve una Serie con:
  df   -> columnas t (fecha-hora local), valor (L/s o mca), valido (bool), motivo (texto)
  meta -> equipo, magnitud, unidad, serie, sitio y lo que el archivo declare
El formato se detecta por el contenido, no por el nombre del archivo.
"""
from __future__ import annotations
import io, re
from dataclasses import dataclass, field
from pathlib import Path
import numpy as np
import pandas as pd

MBAR_A_MCA = 0.0101972


@dataclass
class Serie:
    df: pd.DataFrame
    meta: dict = field(default_factory=dict)


def _texto(ruta) -> str:
    crudo = Path(ruta).read_bytes()
    for cod in ("utf-8-sig", "latin-1"):
        try:
            return crudo.decode(cod)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"No se pudo decodificar {ruta}")


def detectar(txt: str) -> str:
    cab = txt[:3000]
    if "Datetime [UTC]" in cab:
        return "hwm"
    if "TELID" in cab:
        return "telid"
    if "Caudal volum" in cab or cab.lstrip().startswith('"sep=;"'):
        return "fluxdiag"
    if cab.startswith("Fichero") and "Caudal[l/s]" in cab:
        return "rpf400"
    if re.match(r"\d{1,2}/\d{1,2}/\d{4},\d{1,2}:\d{2}:\d{2} [AP]M,", cab):
        return "mdy_ampm"
    raise ValueError("Formato de archivo no reconocido")


def _base(t, v, valido=None, motivo=None) -> pd.DataFrame:
    df = pd.DataFrame({"t": pd.to_datetime(t), "valor": pd.to_numeric(v, errors="coerce")})
    df["valido"] = True if valido is None else np.asarray(valido, bool)
    df["motivo"] = "" if motivo is None else motivo
    sin = df.valor.isna()
    df.loc[sin & (df.motivo == ""), "motivo"] = "sin lectura"
    df.loc[sin, "valido"] = False
    return df.sort_values("t", kind="stable").reset_index(drop=True)


def leer_fluxdiag(txt) -> Serie:
    """Caudalímetro ultrasónico con exportación FluxDiag. Caudal en L/s."""
    lin = txt.splitlines()
    ini = next(i for i, l in enumerate(lin) if re.match(r"\d{2}/\d{2}/\d{4} \d", l))
    cab = next(l for l in lin[:ini] if "Caudal volum" in l).split(";")
    col = next(i for i, c in enumerate(cab) if "Caudal volum" in c)
    uni = lin[ini - 2].split(";")[col] if ini >= 2 else "l/s"
    t, v, mot = [], [], []
    for l in lin[ini:]:
        p = l.split(";")
        if len(p) <= col or not p[0]:
            continue
        t.append(p[0]); x = p[col].strip()
        try:
            v.append(float(x.replace(",", "."))); mot.append("")
        except ValueError:
            v.append(np.nan); mot.append(f"equipo: {x or 'vacío'}")
    df = _base(pd.to_datetime(t, format="%d/%m/%Y %H:%M:%S"), v, motivo=mot)
    if uni.strip().lower() in ("m3/h", "m³/h"):
        df["valor"] /= 3.6
    return Serie(df, {"equipo": "Caudalímetro FluxDiag", "magnitud": "caudal", "unidad": "L/s"})


def leer_rpf400(txt) -> Serie:
    """Senapsis RPF400 (cabecera Fichero / Nombre Sitio). Caudal en L/s."""
    lin = txt.splitlines()
    ini = next(i for i, l in enumerate(lin) if l.startswith("Fecha["))
    meta = {"equipo": "Senapsis RPF400", "magnitud": "caudal", "unidad": "L/s"}
    for l in lin[:ini]:
        p = [x.strip() for x in l.split(",")]
        if p[0] == "Nombre Sitio": meta["sitio"] = p[1]
        if p[0] == "Tuberia Diam. Ext.": meta["diam_ext_mm"] = float(p[1])
        if p[0] == "Espesor de Pared": meta["espesor_mm"] = float(p[1])
        if p[0] == "Material Tuberia": meta["material"] = p[1]
        if p[0] == "Intervalo": meta["intervalo_s"] = float(p[1])
    d = pd.read_csv(io.StringIO("\n".join(lin[ini:])), dtype=str)
    err = d.iloc[:, -1].str.strip()
    malo = err.ne("0000") & err.ne("0")
    df = _base(pd.to_datetime(d.iloc[:, 0], format="%d/%m/%y %H:%M:%S"), d.iloc[:, 1],
               valido=~malo, motivo=np.where(malo, "equipo: error " + err, ""))
    return Serie(df, meta)


def leer_telid(txt) -> Serie:
    """Datalogger TELID 343.HTK. Presión en mbar, se convierte a mca."""
    lin = txt.splitlines()
    ini = next(i for i, l in enumerate(lin) if "Marca de tiempo" in l)
    meta = {"equipo": "TELID", "magnitud": "presion", "unidad": "mca"}
    for l in lin[:ini]:
        p = l.split(";")
        if "mero de serie" in p[0]: meta["serie"] = p[1].strip()
        if p[0] == "Producto": meta["equipo"] = "TELID " + p[1].strip()
    filas = [l.split(";") for l in lin[ini + 1:] if l.count(";") >= 6]
    d = pd.DataFrame(filas)
    t = pd.to_datetime(d[1].str.replace(",000", "", regex=False), format="%Y-%m-%d %H:%M:%S")
    v = pd.to_numeric(d[2].str.replace(",", ".", regex=False), errors="coerce")
    uni = d[3].iloc[0].strip().lower()
    if uni == "mbar": v = v * MBAR_A_MCA
    elif uni == "bar": v = v * 10.1972
    est = d[6].str.strip()
    df = _base(t, v, valido=est.eq("0"), motivo=np.where(est.eq("0"), "", "equipo: estado " + est))
    df["segmento"] = pd.to_numeric(d[0], errors="coerce").values[np.argsort(t.values, kind="stable")]
    meta["unidad_original"] = uni
    return Serie(df, meta)


def leer_hwm(txt) -> Serie:
    """Exportación de la nube HWM (CLOUD_ARC, Multilog). Hora local y UTC; reloj de red."""
    d = pd.read_csv(io.StringIO(txt), sep=";", dtype=str)
    nom = d.columns[3]
    meta = {"equipo": "HWM nube", "reloj_de_red": True,
            "sitio": nom.split(" - ")[-1].strip() if " - " in nom else ""}
    bajo = nom.lower()
    if "mca" in bajo or "presi" in bajo: meta.update(magnitud="presion", unidad="mca")
    elif "l/s" in bajo: meta.update(magnitud="caudal", unidad="L/s")
    else: meta.update(magnitud="otra", unidad=nom)
    df = _base(pd.to_datetime(d.iloc[:, 1]), d.iloc[:, 3])
    utc = pd.to_datetime(d.iloc[:, 2], utc=True).dt.tz_localize(None)
    meta["utc_menos_local_h"] = float(((utc - pd.to_datetime(d.iloc[:, 1])).dt.total_seconds() / 3600).median())
    return Serie(df, meta)


def leer_mdy_ampm(txt) -> Serie:
    """CSV sin cabecera 'm/d/aaaa,h:mm:ss AM,valor' (registradores R). Presión en mca."""
    d = pd.read_csv(io.StringIO(txt), header=None, names=["f", "h", "v"], dtype=str)
    t = pd.to_datetime(d.f + " " + d.h, format="%m/%d/%Y %I:%M:%S %p")
    return Serie(_base(t, d.v), {"equipo": "Registrador (CSV fecha, hora, valor)", "magnitud": "presion", "unidad": "mca"})


LECTORES = {"fluxdiag": leer_fluxdiag, "rpf400": leer_rpf400, "telid": leer_telid,
            "hwm": leer_hwm, "mdy_ampm": leer_mdy_ampm}


def leer(ruta, formato: str | None = None) -> Serie:
    txt = _texto(ruta)
    fmt = formato or detectar(txt)
    s = LECTORES[fmt](txt)
    s.meta.update(formato=fmt, archivo=Path(ruta).name)
    return s
