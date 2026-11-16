"""Herramienta 4 · Patrones, archivos .dat del calibrador y CSV para Giswater.

Convenciones del equipo:
- t = 0 a las 00:00 del día siguiente al inicio de la campaña; valores en horas exactas cada 15 min.
- Patrón de demanda ABSOLUTO en CMH (las demandas base de los nodos son pesos que suman 1).
- CSV de Giswater: fila 1 `pattern_id;descripción;expl_id`; filas de valores con hasta 18 factores; decimal con punto.
- .dat del calibrador BGEO: `ID<TAB>tiempo_h<TAB>valor`, 4 decimales, sin cabecera, un archivo por magnitud; caudal en CMH.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import numpy as np
import pandas as pd


@dataclass
class Patrones:
    cfg: dict
    demanda: pd.DataFrame          # t, h, medido_cmh, perfil_cmh, valor_cmh, origen
    perfiles: pd.DataFrame         # hora del día, laborable, fin de semana, todos
    tanques: dict                  # id_patron -> DataFrame
    dat: dict                      # magnitud -> DataFrame(id, h, valor)
    avisos: list


def perfil_dia_tipo(t: pd.Series, v: pd.Series) -> pd.DataFrame:
    d = pd.DataFrame({"min": t.dt.hour * 60 + t.dt.minute, "finde": t.dt.dayofweek >= 5, "v": v}).dropna()
    todos = d.groupby("min").v.median(); lab = d[~d.finde].groupby("min").v.median(); fin = d[d.finde].groupby("min").v.median()
    return pd.DataFrame({"todos": todos, "laborable": lab, "fin_de_semana": fin})


def patron_tanque(t, lectura_m, cota_sensor, forma="absoluta", cota_solera=None):
    """Cota de lámina = cota del sensor + lectura. 'absoluta' para Head = 1; 'multiplicador' sobre la cota de solera."""
    cota = cota_sensor + np.asarray(lectura_m, float)
    if forma == "absoluta": return pd.DataFrame({"t": t, "cota_lamina": cota, "factor": np.round(cota, 4)})
    if not cota_solera: raise ValueError("La forma 'multiplicador' necesita cota_solera")
    return pd.DataFrame({"t": t, "cota_lamina": cota, "factor": np.round(cota / cota_solera, 7)})


def generar(res) -> Patrones:
    cfg = res.cfg; mo = cfg.get("modelo") or {}; paso = cfg["_param"]["paso_min"]; A = []
    ini, fin = cfg["_ventana"]
    t0 = pd.Timestamp(mo["t0"]) if mo.get("t0") else (ini.normalize() + pd.Timedelta(days=1))
    if t0 != t0.normalize(): A.append(("Media", f"t = 0 ({t0:%d/%m %H:%M}) no cae a las 00:00."))
    dur = float(mo.get("duracion_h", 168)); n = int(dur * 60 / paso)
    eje = pd.DataFrame({"t": pd.date_range(t0, periods=n, freq=f"{paso}min")}); eje["h"] = np.arange(n) * paso / 60
    m = res.malla
    # --- demanda
    q = eje.merge(m[["t", "Q_sector"]], on="t", how="left"); q["medido_cmh"] = q.Q_sector * 3.6
    per = perfil_dia_tipo(m.t, m.Q_sector * 3.6)
    mn = q.t.dt.hour * 60 + q.t.dt.minute; finde = q.t.dt.dayofweek >= 5
    lab = mn.map(per.laborable); fs = mn.map(per.fin_de_semana); td = mn.map(per.todos)
    q["perfil_cmh"] = np.where(finde, fs.fillna(td), lab.fillna(td))
    q["valor_cmh"] = q.medido_cmh.fillna(q.perfil_cmh)
    q["origen"] = np.where(q.medido_cmh.notna(), "medido", np.where(finde & fs.isna(), "perfil de todos los días", np.where(finde, "perfil de fin de semana", "perfil laborable")))
    faltan = int(q.medido_cmh.isna().sum())
    if faltan: A.append(("Media" if faltan / n < 0.15 else "Alta", f"{faltan} de {n} valores del patrón de demanda ({faltan / n:.0%}) no tienen medición y se rellenan con el perfil del día tipo."))
    if (finde & fs.isna()).any(): A.append(("Media", "No hay fin de semana medido; los sábados y domingos del patrón usan el perfil de todos los días."))
    if q.valor_cmh.isna().any(): A.append(("Alta", "Quedan valores vacíos en el patrón de demanda."))
    pid = (mo.get("patron_demanda") or {}).get("id", "PAT-DEM")
    if len(pid) > 16: A.append(("Alta", f"El id de patrón '{pid}' supera los 16 caracteres que admite Giswater."))
    # --- tanques
    tanques = {}
    for tq in mo.get("tanques") or []:
        s = eje.merge(m[["t", tq["punto"]]], on="t", how="left")
        vac = int(s[tq["punto"]].isna().sum())
        if vac: A.append(("Alta", f"El patrón del tanque {tq['id_patron']} tiene {vac} valores sin nivel medido; se interpolan."))
        s[tq["punto"]] = s[tq["punto"]].interpolate(limit_direction="both")
        pt = patron_tanque(s.t, s[tq["punto"]], tq["cota_sensor"], tq.get("forma", "absoluta"), tq.get("cota_solera")); pt["h"] = eje.h
        tanques[tq["id_patron"]] = pt
    # --- .dat
    mapeo = mo.get("mapeo") or {}; dat = {}
    for p in res.puntos.values():
        mid = str(mapeo.get(p.id, p.id))
        if p.id not in mapeo: A.append(("Media", f"El punto {p.id} no tiene id de modelo en el mapeo; el .dat lleva el id de campo."))
        s = eje.merge(m[["t", p.id]], on="t", how="left").dropna(subset=[p.id])
        val = s[p.id] * (3.6 if p.magnitud == "caudal" else 1.0)
        dat.setdefault(p.magnitud, []).append(pd.DataFrame({"id": mid, "h": s.h.values, "valor": val.values}))
    dat = {k: pd.concat(v, ignore_index=True) for k, v in dat.items()}
    A.sort(key=lambda a: {"Alta": 0, "Media": 1, "Info": 2}[a[0]])
    cfg["_t0"] = t0
    return Patrones(cfg, q[["t", "h", "medido_cmh", "perfil_cmh", "valor_cmh", "origen"]], per.reset_index(), tanques, dat, A)


def texto_csv_giswater(pid, descripcion, expl_id, valores, por_fila=18, dec=4) -> str:
    lin = [";".join([pid, descripcion, str(expl_id)] + [""] * (por_fila - 2))]
    v = [f"{x:.{dec}f}" for x in valores]
    for i in range(0, len(v), por_fila): lin.append(";".join([pid] + v[i:i + por_fila]))
    return "\n".join(lin) + "\n"


def texto_epanet(pid, valores, por_fila=6, dec=4) -> str:
    v = [f"{x:.{dec}f}" for x in valores]
    return "\n".join(f"{pid:<16} " + " ".join(f"{x:>12}" for x in v[i:i + por_fila]) for i in range(0, len(v), por_fila)) + "\n"


def texto_dat(df) -> str:
    return "".join(f"{r.id}\t{r.h:.4f}\t{r.valor:.4f}\n" for r in df.itertuples())


def exportar(pt: Patrones, carpeta) -> dict:
    from openpyxl import Workbook
    from . import estilo as E
    import unicodedata
    cfg = pt.cfg; mo = cfg.get("modelo") or {}; carpeta = Path(carpeta) / "modelo"; carpeta.mkdir(parents=True, exist_ok=True)
    pd_ = mo.get("patron_demanda") or {}; pid = pd_.get("id", "PAT-DEM"); desc = pd_.get("descripcion", "Patron de demanda CMH"); ex = mo.get("expl_id", "")
    asc = lambda s: unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    sal = {}
    f = carpeta / f"patron_{pid}_giswater.csv"; f.write_text(texto_csv_giswater(pid, asc(desc), ex, pt.demanda.valor_cmh), encoding="ascii"); sal["csv_demanda"] = f
    f = carpeta / f"patron_{pid}_epanet.txt"; f.write_text(texto_epanet(pid, pt.demanda.valor_cmh), encoding="ascii"); sal["epanet_demanda"] = f
    for tid, d in pt.tanques.items():
        dec = 7 if d.factor.max() < 10 else 4
        f = carpeta / f"patron_{tid}_giswater.csv"; f.write_text(texto_csv_giswater(tid, "Nivel de tanque", ex, d.factor, dec=dec), encoding="ascii"); sal[f"csv_{tid}"] = f
        f = carpeta / f"patron_{tid}_epanet.txt"; f.write_text(texto_epanet(tid, d.factor, dec=dec), encoding="ascii")
    nombres = {"caudal": "calib_caudal.dat", "presion": "calib_presion.dat", "nivel": "calib_nivel.dat"}
    for mag, d in pt.dat.items():
        f = carpeta / nombres.get(mag, f"calib_{mag}.dat"); f.write_text(texto_dat(d), encoding="ascii"); sal[f"dat_{mag}"] = f
    # Excel de cálculo
    wb = Workbook(); ws = wb.active; ws.title = "Patron_demanda"; t0 = cfg["_t0"]
    q = pt.demanda
    E.titulo(ws, f"Patrón de demanda {pid}", f"Absoluto en m³/h · t = 0 el {t0:%d/%m/%Y %H:%M} · {len(q)} valores · media {q.valor_cmh.mean():.4f} m³/h".replace(".", ","))
    d = q.copy(); d.columns = ["FechaHora", "t (h)", "Medido (m³/h)", "Perfil del día tipo (m³/h)", "Valor del patrón (m³/h)", "Origen"]
    E.tabla(ws, 4, d, anchos=[17, 9, 14, 16, 16, 26], formatos={"FechaHora": "dd/mm/yyyy hh:mm", "t (h)": "0.00", "Medido (m³/h)": "0.0000",
                                                                 "Perfil del día tipo (m³/h)": "0.0000", "Valor del patrón (m³/h)": "0.0000"}, gris=True)
    ws = wb.create_sheet("Dia_tipo"); E.titulo(ws, "Perfiles del día tipo", "Mediana por hora del día de los valores medidos, en m³/h")
    p = pt.perfiles.copy(); p["min"] = p["min"].map(lambda x: f"{int(x) // 60:02d}:{int(x) % 60:02d}"); p.columns = ["Hora", "Todos los días", "Laborable", "Fin de semana"]
    E.tabla(ws, 4, p, anchos=[9, 14, 14, 14], formatos={c: "0.0000" for c in p.columns[1:]})
    for tid, d in pt.tanques.items():
        ws = wb.create_sheet(("Tanque_" + tid)[:31]); E.titulo(ws, f"Patrón de tanque {tid}")
        x = d[["t", "h", "cota_lamina", "factor"]].copy(); x.columns = ["FechaHora", "t (h)", "Cota de lámina (msnm)", "Factor del patrón"]
        E.tabla(ws, 3, x, anchos=[17, 9, 18, 18], formatos={"FechaHora": "dd/mm/yyyy hh:mm", "Cota de lámina (msnm)": "0.000", "Factor del patrón": "0.0000000"}, gris=True)
    ws = wb.create_sheet("DAT"); E.titulo(ws, "Archivos .dat del calibrador", "Solo valores medidos y válidos; el caudal va en m³/h")
    r = []
    for mag, d in pt.dat.items():
        for i, g in d.groupby("id", sort=False):
            r.append({"Magnitud": mag, "Id en el .dat": i, "Registros": len(g), "t inicial (h)": g.h.min(), "t final (h)": g.h.max(),
                      "Media": g.valor.mean(), "Mínimo": g.valor.min(), "Máximo": g.valor.max()})
    E.tabla(ws, 4, pd.DataFrame(r), anchos=[11, 16, 10, 11, 11, 11, 11, 11], formatos={c: "0.00" for c in ("t inicial (h)", "t final (h)", "Media", "Mínimo", "Máximo")})
    ws = wb.create_sheet("Avisos"); E.titulo(ws, "Avisos")
    a = pd.DataFrame(pt.avisos, columns=["Gravedad", "Aviso"]); E.tabla(ws, 3, a, anchos=[10, 120], ajustar=True)
    for w in wb.worksheets: w.sheet_view.showGridLines = False
    f = carpeta / f"Calculo_patrones_{pid}.xlsx"; wb.save(f); sal["excel"] = f
    return sal
