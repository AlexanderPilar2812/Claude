"""Herramienta 2 · Línea base de pérdidas (método IWA de caudal mínimo nocturno).

Parte de la serie de 15 min de la herramienta de campaña. Calcula el MNF diario con media
móvil de 1 h en la ventana nocturna, el percentil semanal, las pérdidas nocturnas, UBL, UARL
e ILI, y contrasta el resultado con el libro Modelo_CMN si se indica.
Los días para el MNF y los días para el caudal medio se eligen por separado.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import numpy as np
import pandas as pd

DEF = dict(ventana_mnf=["00:00", "04:00"], percentil=20, validez_min=0.85, dia_completo_min=0.95,
           nu_lh=1.4, cnli_lh=0.2, cnlo_lh=0.1, consumo_nocturno_nr_lh=0.0, conexiones_no_residenciales=0,
           longitud_acometida_m=4, n1=1.5, icf=1.0, pfc=1.0, p0=50.0, excluir_noches=[])

CELDAS_CMN = {  # plantilla Modelo_CMN, hoja RESUMEN LINEA BASE
    "hoja": "RESUMEN LINEA BASE", "fila_dias": (20, 27), "desde": "H6", "hasta": "J6",
    "mnf_p20_lh": "D18", "p_noct": "F18", "p_media": "G18", "q_medio_lh": "J18", "cn_ls": "E49", "lnight_ls": "E50",
    "ubl_icf_ls": "E53", "recuperables_ls": "E54", "uarl_ls": "G56", "carl_ls": "G57", "ili": "G55",
    "conexiones": "C29", "longitud_red_km": "E43", "longitud_acometida_m": "E44", "n1": "E47", "icf": "E48", "pfc": "E58",
    "nu_lh": "C31", "cnli_lh": "C32", "cnlo_lh": "C33", "consumo_nocturno_nr_lh": "H34"}


@dataclass
class LineaBase:
    par: dict
    diario: pd.DataFrame
    ind: dict
    comparacion: pd.DataFrame
    alertas: list


def es(x, d=2):
    """Número con coma decimal y espacio de millares."""
    return f"{x:,.{d}f}".replace(",", " ").replace(".", ",")


def _min(hhmm): h, m = map(int, hhmm.split(":")); return h * 60 + m


def calcular(res, cfg_lb: dict | None = None) -> LineaBase:
    cfg = res.cfg; par = {**DEF, **(cfg_lb or cfg.get("linea_base") or {})}
    paso = cfg["_param"]["paso_min"]; m = res.malla.copy(); A = []
    if "Q_sector" not in m: raise ValueError("La campaña no tiene puntos de caudal")
    m["q_lh"] = m.Q_sector * 3600; m["dia"] = m.t.dt.normalize(); mn = m.t.dt.hour * 60 + m.t.dt.minute
    v0, v1 = _min(par["ventana_mnf"][0]), _min(par["ventana_mnf"][1]); n_h = int(60 // paso); n_ven = (v1 - v0) // paso
    en_ven = (mn >= v0) & (mn < v1)
    # cortes generales: el caudal se toma como 0 para el volumen diario
    q_vol = m.q_lh.copy(); en_corte = pd.Series(False, index=m.index)
    if not res.eventos.empty:
        for _, g in res.eventos[res.eventos.tipo == "general"].groupby("evento"):
            en_corte |= (m.t >= g.caida.min()) & (m.t < g.retorno.max())
    q_vol[en_corte & q_vol.isna()] = 0.0
    movil = m.q_lh.rolling(n_h, min_periods=n_h).mean()
    pmp = m.PMP if "PMP" in m else pd.Series(np.nan, index=m.index)
    p_movil = pmp.rolling(n_h, min_periods=n_h).mean()
    filas = []
    for dia, g in m.groupby("dia"):
        i = g.index; w = i[en_ven[i]]
        val = m.q_lh[w].notna().sum() / n_ven if n_ven else 0
        f = dict(dia=dia, validos_ventana=val, mnf_lh=np.nan, hora_mnf="", p_mnf=np.nan, mnf_puntual_lh=np.nan, usa_mnf=False)
        cand = w[(mn[w] >= v0 + (n_h - 1) * paso)]
        if val >= par["validez_min"] and movil[cand].notna().any():
            k = movil[cand].idxmin(); f.update(mnf_lh=float(movil[k]), p_mnf=float(p_movil[k]) if pd.notna(p_movil[k]) else np.nan,
                                              hora_mnf=f"{m.t[k - n_h + 1]:%H:%M}–{m.t[k]:%H:%M}", mnf_puntual_lh=float(m.q_lh[w].min()))
            f["usa_mnf"] = dia.strftime("%Y-%m-%d") not in [str(x) for x in par["excluir_noches"]]
        comp = q_vol[i].notna().sum() / (1440 // paso)
        f.update(cobertura_dia=comp, completo=comp >= par["dia_completo_min"],
                 q_medio_lh=float(q_vol[i].mean()) if comp >= par["dia_completo_min"] else np.nan,
                 volumen_m3=float(q_vol[i].sum() * paso / 60 / 1000) if comp >= par["dia_completo_min"] else np.nan,
                 p_media=float(pmp[i].mean()) if comp >= par["dia_completo_min"] else np.nan,
                 horas_corte=float(en_corte[i].sum() * paso / 60))
        filas.append(f)
    d = pd.DataFrame(filas)
    usa = d[d.usa_mnf]; comp = d[d.completo]
    if usa.empty: raise ValueError("Ninguna noche cumple el mínimo de datos válidos para el MNF")
    Nc = par["conexiones"]; Nnr = par["conexiones_no_residenciales"]; Lm = par["longitud_red_km"]; Lp = par["longitud_acometida_m"]
    n1 = par["n1"]
    mnf = float(np.percentile(usa.mnf_lh, par["percentil"]))
    pn = float(usa.p_mnf.mean()); pm = float(comp.p_media.mean()) if len(comp) else np.nan
    qm = float(comp.q_medio_lh.mean()) if len(comp) else np.nan
    cn = (par["nu_lh"] + par["cnli_lh"] + par["cnlo_lh"]) * (Nc - Nnr) + par["consumo_nocturno_nr_lh"]
    ln = mnf - cn
    # factor noche-día con la presión de los días completos
    if len(comp) and pn > 0:
        dc = m[m.dia.isin(comp.dia)]
        fnd = float(((dc.PMP.clip(lower=0) / pn) ** n1).mean()) if "PMP" in dc else np.nan
    else: fnd = np.nan
    ubl = (20 * Lm + 1.25 * Nc + 33.3 * Nc * Lp / 1000) * (pn / par["p0"]) ** n1
    uarl_ld = (18 * Lm + 0.8 * Nc + 25 * Nc * Lp / 1000) * pm
    ind = dict(noches_mnf=len(usa), dias_completos=len(comp), mnf_lh=mnf, qmn_ls=mnf / 3600, p_noct=pn, p_media=pm, q_medio_lh=qm,
               q_medio_ls=qm / 3600, cn_lh=cn, cn_ls=cn / 3600, lnight_lh=ln, lnight_ls=ln / 3600, factor_noche_dia=fnd,
               perdidas_medias_ls=ln / 3600 * fnd, ubl_ls=ubl / 3600, ubl_icf_ls=ubl / 3600 * par["icf"],
               recuperables_ls=ln / 3600 - ubl / 3600 * par["icf"], uarl_ls=uarl_ld / 86400, uarl_pfc_ls=uarl_ld / 86400 * par["pfc"],
               pct_noct_sobre_qmedio=ln / qm, pct_perdidas=ln * fnd / qm, rendimiento=1 - ln * fnd / qm,
               ili_criterio_cmn=(ln / 3600) / (uarl_ld / 86400 * par["pfc"]), ili_iwa=(ln / 3600 * fnd) / (uarl_ld / 86400),
               perdidas_lh_km=ln * fnd / Lm, perdidas_lh_conexion=ln * fnd / Nc,
               mnf_sin_anomalas_lh=np.nan)
    # alertas
    if len(usa) < 7: A.append(("Media", f"El MNF se calcula con {len(usa)} noches; el pliego pide 7 días de campaña."))
    if len(comp) < len(usa): A.append(("Info", f"Días completos para el caudal medio: {len(comp)}; noches para el MNF: {len(usa)}. Se eligen por separado."))
    if Nc < 3000: A.append(("Info", f"Con {es(Nc, 0)} conexiones el ILI queda por debajo del tamaño mínimo que recomienda la IWA (3 000); tomarlo como indicativo."))
    if par["pfc"] != 1: A.append(("Media", f"El libro multiplica la UARL por PFC = {es(par['pfc'])}, que no forma parte de la fórmula IWA. Con ese criterio el ILI es {es(ind['ili_criterio_cmn'])}; con la fórmula IWA y el factor noche-día es {es(ind['ili_iwa'])}."))
    if d.horas_corte.sum() > 0:
        dc_ = d[d.horas_corte > 0]; A.append(("Media", "Días con corte de servicio: " + ", ".join(f"{r.dia:%d/%m} ({es(r.horas_corte)} h)" for r in dc_.itertuples()) + ". En esas horas el caudal cuenta como 0 para el volumen diario."))
    if len(res.noches) and (res.noches.aviso != "").any():
        an = res.noches[res.noches.aviso != ""].dia
        sin = usa[~usa.dia.isin(an)]
        if len(sin) and len(sin) < len(usa):
            ind["mnf_sin_anomalas_lh"] = float(np.percentile(sin.mnf_lh, par["percentil"]))
            A.append(("Alta", f"Noches anómalas dentro del cálculo ({', '.join(f'{x:%d/%m}' for x in an)}): el MNF pasa de {es(mnf, 0)} a {es(ind['mnf_sin_anomalas_lh'], 0)} L/h si se excluyen. Confirmar antes de usarlas."))
    if ln <= 0: A.append(("Alta", "El consumo nocturno estimado supera al MNF; revisar conexiones y consumos unitarios."))
    # contraste con el Modelo_CMN
    comparacion = pd.DataFrame()
    if par.get("excel_cmn"):
        comparacion = _comparar_cmn(Path(cfg["_base"]) / par["excel_cmn"], par, d, ind, cfg, A)
    A.sort(key=lambda a: {"Alta": 0, "Media": 1, "Info": 2}[a[0]])
    return LineaBase(par, d, ind, comparacion, A)


def _comparar_cmn(ruta, par, d, ind, cfg, A):
    import openpyxl
    C = CELDAS_CMN; ws = openpyxl.load_workbook(ruta, data_only=True)[C["hoja"]]
    val = lambda c: ws[c].value
    filas = []
    def fila(concepto, cmn, herr, unidad, tol=0.02, nota=""):
        dif = None if cmn in (None, 0) or herr is None or (isinstance(herr, float) and np.isnan(herr)) else herr / cmn - 1
        est = "" if dif is None else ("coincide" if abs(dif) <= tol else "difiere")
        filas.append(dict(Concepto=concepto, Unidad=unidad, **{"Modelo_CMN": cmn, "Herramienta": herr, "Diferencia": dif, "Estado": est, "Nota": nota}))
        return est
    for k, nom, u in (("conexiones", "Conexiones", "ud"), ("longitud_red_km", "Longitud de red", "km"), ("longitud_acometida_m", "Longitud de acometida", "m"),
                      ("n1", "N1", "-"), ("icf", "ICF", "-"), ("pfc", "PFC", "-"), ("nu_lh", "NU", "L/h·ud"), ("cnli_lh", "CNLI", "L/h·ud"),
                      ("cnlo_lh", "CNLO", "L/h·ud"), ("consumo_nocturno_nr_lh", "Consumo nocturno no residencial", "L/h")):
        if fila("Parámetro: " + nom, val(C[k]), float(par[k]), u, tol=1e-6) == "difiere":
            A.append(("Alta", f"El parámetro {nom} del Modelo_CMN ({val(C[k])}) no coincide con el del sector.yaml ({par[k]})."))
    dias = {}
    for r in range(C["fila_dias"][0], C["fila_dias"][1] + 1):
        f = ws.cell(r, 1).value
        if f is not None: dias[pd.Timestamp(f).normalize()] = (ws.cell(r, 3).value, ws.cell(r, 6).value, ws.cell(r, 10).value)
    for r in d.itertuples():
        if r.dia in dias and dias[r.dia][0] is not None and not np.isnan(r.mnf_lh):
            fila(f"MNF del {r.dia:%d/%m}", dias[r.dia][0], r.mnf_lh, "L/h")
            fila(f"Presión en el MNF del {r.dia:%d/%m}", dias[r.dia][1], r.p_mnf, "mca")
        if r.dia in dias and dias[r.dia][2] is not None and r.completo:
            fila(f"Q medio del {r.dia:%d/%m}", dias[r.dia][2], r.q_medio_lh, "L/h")
    solo_cmn = [k for k, v in dias.items() if v[0] is not None and k not in set(d[d.usa_mnf].dia)]
    nota = "El libro usa noches que la herramienta no tiene: " + ", ".join(f"{k:%d/%m}" for k in solo_cmn) if solo_cmn else ""
    for k, nom, u in (("mnf_p20_lh", "MNF semanal", "L/h"), ("p_noct", "Presión nocturna", "mca"), ("p_media", "Presión media", "mca"),
                      ("q_medio_lh", "Q medio diario", "L/h"), ("cn_ls", "Consumo nocturno", "L/s"), ("lnight_ls", "Pérdidas nocturnas", "L/s"),
                      ("ubl_icf_ls", "UBL × ICF", "L/s"), ("recuperables_ls", "Pérdidas recuperables", "L/s"), ("uarl_ls", "UARL (con PFC)", "L/s"),
                      ("ili", "ILI (criterio del libro)", "-")):
        h = {"mnf_p20_lh": ind["mnf_lh"], "q_medio_lh": ind["q_medio_lh"], "uarl_ls": ind["uarl_pfc_ls"], "ili": ind["ili_criterio_cmn"]}.get(k, ind.get(k))
        fila(nom, val(C[k]), h, u, nota=nota if k == "mnf_p20_lh" else "")
    if solo_cmn: A.append(("Media", nota + ". Los totales no son comparables hasta tener la misma ventana."))
    ini, fin = cfg["_ventana"]
    for k, ref, nom in (("desde", ini, "inicio"), ("hasta", fin, "fin")):
        x = val(C[k])
        if x is not None and abs((pd.Timestamp(x).round("min") - ref).total_seconds()) > 900:
            A.append(("Info", f"El {nom} del período en el Modelo_CMN ({pd.Timestamp(x):%d/%m %H:%M}) no coincide con la ventana procesada ({ref:%d/%m %H:%M})."))
    if abs((val(C["carl_ls"]) or 0) - (val(C["lnight_ls"]) or -1)) < 1e-9:
        A.append(("Media", f"En el Modelo_CMN el CARL es igual a las pérdidas nocturnas. Con el factor noche-día ({es(ind['factor_noche_dia'], 3)}) las pérdidas medias son {es(ind['perdidas_medias_ls'], 3)} L/s."))
    c = pd.DataFrame(filas)
    nd = int((c.Estado == "difiere").sum())
    if nd: A.append(("Alta", f"{nd} valores del Modelo_CMN difieren más del 2 % de lo calculado con los crudos; ver la hoja Comparación."))
    return c


def exportar(lb: LineaBase, res, carpeta) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, PatternFill
    from . import estilo as E
    import unicodedata
    cfg = res.cfg; nombre = f"{cfg.get('sector', '')} {cfg.get('codigo', '')}".strip(); ini, fin = cfg["_ventana"]; p = lb.par; i = lb.ind
    wb = Workbook(); ws = wb.active; ws.title = "Resumen"
    E.titulo(ws, f"Línea base de pérdidas · {nombre}", f"Método IWA de caudal mínimo nocturno · ventana {ini:%d/%m/%Y %H:%M} – {fin:%d/%m/%Y %H:%M}")
    F = {}; filas = []
    def add(clave, concepto, valor, unidad, calc="", fmt="0.000"): filas.append((clave, concepto, valor, unidad, calc, fmt))
    def sec(t): filas.append((None, t, None, None, None, None))
    sec("Parámetros del sector")
    add("Nc", "Conexiones", p["conexiones"], "ud", "sector.yaml", "#,##0")
    add("Nnr", "Conexiones no residenciales", p["conexiones_no_residenciales"], "ud", "sector.yaml", "#,##0")
    add("cu", "Consumo nocturno residencial unitario", p["nu_lh"] + p["cnli_lh"] + p["cnlo_lh"], "L/h·ud", "NU + CNLI + CNLO")
    add("cnr", "Consumo nocturno no residencial", p["consumo_nocturno_nr_lh"], "L/h", "sector.yaml", "0.00")
    add("Lm", "Longitud de red", p["longitud_red_km"], "km", "sector.yaml")
    add("Lp", "Longitud media de acometida", p["longitud_acometida_m"], "m", "sector.yaml", "0.0")
    add("N1", "N1", p["n1"], "-", "sector.yaml", "0.0"); add("ICF", "ICF", p["icf"], "-", "sector.yaml", "0.0")
    add("PFC", "PFC (criterio del Modelo_CMN)", p["pfc"], "-", "sector.yaml", "0.00"); add("P0", "Presión de referencia", p["p0"], "mca", "IWA", "0")
    sec("Medición")
    add("MNF", f"MNF semanal (percentil {p['percentil']})", i["mnf_lh"], "L/h", f"Percentil de las {i['noches_mnf']} noches válidas de la hoja Diario", "#,##0")
    add("Pn", "Presión nocturna", i["p_noct"], "mca", "Media de la presión en la hora del MNF", "0.00")
    add("Pm", "Presión media", i["p_media"], "mca", f"Media de los {i['dias_completos']} días completos", "0.00")
    add("Qm", "Caudal medio diario", i["q_medio_lh"], "L/h", f"Media de los {i['dias_completos']} días completos (cortes como caudal 0)", "#,##0")
    add("FND", "Factor noche-día", i["factor_noche_dia"], "-", "Media de (P/Pnoct)^N1 en los días completos")
    sec("Pérdidas")
    add("QMN", "QMN", "={MNF}/3600", "L/s", "MNF / 3600")
    add("Cn", "Consumo nocturno", "=({cu}*({Nc}-{Nnr})+{cnr})/3600", "L/s", "Unitario × residenciales + no residencial")
    add("Ln", "Pérdidas nocturnas (Lnight)", "={QMN}-{Cn}", "L/s", "QMN − consumo nocturno")
    add("Lmed", "Pérdidas medias diarias", "={Ln}*{FND}", "L/s", "Lnight × factor noche-día")
    add("UBL", "UBL", "=(20*{Lm}+1.25*{Nc}+33.3*{Nc}*{Lp}/1000)*({Pn}/{P0})^{N1}/3600", "L/s", "(20·Lm + 1,25·Nc + 33,3·Nc·Lp/1000)·(Pnoct/50)^N1")
    add("UBLi", "UBL × ICF", "={UBL}*{ICF}", "L/s", "")
    add("Rec", "Pérdidas recuperables", "={Ln}-{UBLi}", "L/s", "Lnight − UBL × ICF")
    add("UARL", "UARL (fórmula IWA)", "=(18*{Lm}+0.8*{Nc}+25*{Nc}*{Lp}/1000)*{Pm}/86400", "L/s", "(18·Lm + 0,8·Nc + 25·Nc·Lp/1000)·Pmedia")
    sec("Indicadores")
    add("p1", "Pérdidas medias sobre caudal medio", "={Lmed}/({Qm}/3600)", "", "Con factor noche-día", "0.0%")
    add("p2", "Lnight sobre caudal medio (criterio del Modelo_CMN)", "={Ln}/({Qm}/3600)", "", "Sin factor noche-día", "0.0%")
    add("ren", "Rendimiento de red", "=1-{p1}", "", "1 − pérdidas medias / caudal medio", "0.0%")
    add("ili1", "ILI (fórmula IWA)", "={Lmed}/{UARL}", "-", "Pérdidas medias / UARL", "0.00")
    add("ili2", "ILI (criterio del Modelo_CMN)", "={Ln}/({UARL}*{PFC})", "-", "Lnight / (UARL × PFC)", "0.00")
    add("km", "Pérdidas por km de red", "={Lmed}*3600/{Lm}", "L/h·km", "", "0.0")
    add("con", "Pérdidas por conexión", "={Lmed}*3600/{Nc}", "L/h·ud", "", "0.00")
    r0 = 4
    for j, c in enumerate(["Concepto", "Valor", "Unidad", "Cómo se calcula"], 1):
        x = ws.cell(r0, j, c); x.font = E.fuente(bold=True, color="FFFFFF"); x.fill = PatternFill("solid", fgColor=E.ROJO); x.border = E.BORDE
        x.alignment = Alignment(horizontal="center")
    for k, f in enumerate(filas):
        if f[0]: F[f[0]] = f"$B${r0 + 1 + k}"
    for k, (clave, con, v, u, calc, fmt) in enumerate(filas):
        r = r0 + 1 + k
        if clave is None:
            for j in range(1, 5):
                x = ws.cell(r, j, con if j == 1 else None); x.fill = PatternFill("solid", fgColor=E.ROJO_OSC); x.font = E.fuente(bold=True, color="FFFFFF")
            continue
        if isinstance(v, str) and v.startswith("="): v = v.format(**F)
        elif isinstance(v, float) and np.isnan(v): v = None
        for j, val in enumerate([con, v, u, calc], 1):
            x = ws.cell(r, j, val); x.font = E.fuente(size=10); x.border = E.BORDE
        ws.cell(r, 2).number_format = fmt
    for c, w in zip("ABCD", [52, 14, 10, 62]): ws.column_dimensions[c].width = w
    # Alertas
    ws = wb.create_sheet("Alertas"); E.titulo(ws, "Alertas de la línea base")
    a = pd.DataFrame(lb.alertas, columns=["Gravedad", "Alerta"]); a.insert(0, "N°", range(1, len(a) + 1))
    E.tabla(ws, 3, a, anchos=[5, 10, 130], ajustar=True)
    # Diario
    ws = wb.create_sheet("Diario"); E.titulo(ws, "Valores por día", "MNF = mínimo de la media móvil de 1 h dentro de la ventana nocturna")
    d = lb.diario.copy()
    d["usa_mnf"] = np.where(d.usa_mnf, "Sí", "No"); d["completo"] = np.where(d.completo, "Sí", "No")
    d = d[["dia", "validos_ventana", "usa_mnf", "mnf_lh", "hora_mnf", "p_mnf", "mnf_puntual_lh", "cobertura_dia", "completo", "q_medio_lh", "volumen_m3", "p_media", "horas_corte"]]
    d.columns = ["Día", "Datos válidos en la ventana", "Entra en el MNF", "MNF (L/h)", "Hora del MNF", "Presión en el MNF (mca)", "Mínimo puntual (L/h)",
                 "Cobertura del día", "Día completo", "Q medio (L/h)", "Volumen (m³)", "Presión media (mca)", "Horas de corte"]
    E.tabla(ws, 4, d, anchos=[12, 12, 10, 12, 14, 12, 12, 11, 10, 12, 12, 12, 10],
            formatos={"Día": "dd/mm/yyyy", "Datos válidos en la ventana": "0%", "MNF (L/h)": "#,##0", "Presión en el MNF (mca)": "0.00",
                      "Mínimo puntual (L/h)": "#,##0", "Cobertura del día": "0%", "Q medio (L/h)": "#,##0", "Volumen (m³)": "#,##0.0",
                      "Presión media (mca)": "0.00", "Horas de corte": "0.00"})
    if not lb.comparacion.empty:
        ws = wb.create_sheet("Comparacion"); E.titulo(ws, "Contraste con el Modelo_CMN", "Diferencia = herramienta / Modelo_CMN − 1; se marca lo que difiere más del 2 %")
        E.tabla(ws, 4, lb.comparacion, anchos=[42, 9, 14, 14, 11, 11, 70], formatos={"Diferencia": "+0.0%;-0.0%", "Modelo_CMN": "#,##0.000", "Herramienta": "#,##0.000"})
        for r in range(5, 5 + len(lb.comparacion)):
            if ws.cell(r, 6).value == "difiere": ws.cell(r, 6).font = E.fuente(bold=True, color=E.ROJO, size=10)
    for w in wb.worksheets: w.sheet_view.showGridLines = False
    slug = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode().replace(" ", "_")
    carpeta = Path(carpeta); carpeta.mkdir(parents=True, exist_ok=True); ruta = carpeta / f"Linea_base_{slug}.xlsx"; wb.save(ruta)
    return ruta
