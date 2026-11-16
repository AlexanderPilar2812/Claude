"""Herramienta 6 · Calibración de pérdidas con emisores (FAVAD).

Reparte emisores por nodo en proporción a la longitud de tubería que le corresponde (mitad de cada
tubo conectado), ajusta su magnitud para que el volumen fugado en la ventana sea el objetivo y resta
la fuga al patrón de demanda, de modo que el caudal de entrada del modelo siga siendo el medido.
Supone la convención del proyecto: pesos que suman 1 y patrón de demanda absoluto en m³/h.
Sirve como alternativa o como control de Loss management.
"""
from __future__ import annotations
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import numpy as np
import pandas as pd
from .inp import Inp, horas
from .simular import simular


@dataclass
class Perdidas:
    inp: Inp
    resumen: dict
    emisores: pd.DataFrame
    patron: pd.DataFrame
    iteraciones: pd.DataFrame
    avisos: list


def _patron(I, pid):
    v = []
    for _, f in I.filas("PATTERNS"):
        if f[0] == pid: v += [float(x) for x in f[1:]]
    return np.array(v)


def _fijar_patron(I, pid, valores, por_fila=6):
    lin = I.sec["PATTERNS"]; pos = None; nuevas = []
    for i, l in enumerate(lin):
        d = l.split(";", 1)[0].split()
        if d and d[0] == pid:
            if pos is None: pos = len(nuevas)
            continue
        nuevas.append(l)
    bloque = [f" {pid:<16} " + " ".join(f"{x:.4f}" for x in valores[i:i + por_fila]) for i in range(0, len(valores), por_fila)]
    pos = len(nuevas) if pos is None else pos
    I.sec["PATTERNS"] = nuevas[:pos] + bloque + nuevas[pos:]


def calibrar(ruta_inp, objetivo_m3=None, pct=None, n1=1.5, patron=None, nodos=None, ventana_h=None, tol=0.005, max_iter=10) -> Perdidas:
    I = Inp.leer(ruta_inp); A = []
    T = I.opciones("TIMES"); dur = horas(T.get("DURATION", "168")); paso = horas(T.get("HYDRAULIC TIMESTEP", "0:15")); ppat = horas(T.get("PATTERN TIMESTEP", "0:15"))
    J = {f[0]: f for _, f in I.filas("JUNCTIONS")}
    # pesos y patrón de demanda
    peso = {}; pats = Counter()
    en_demands = {f[0] for _, f in I.filas("DEMANDS")}        # una entrada en [DEMANDS] sustituye a la columna de [JUNCTIONS]
    for k, f in J.items():
        if k not in en_demands and len(f) > 2 and float(f[2]) != 0: peso[k] = float(f[2]); pats[f[3] if len(f) > 3 else ""] += 1
    for _, f in I.filas("DEMANDS"):
        if float(f[1]) != 0: peso[f[0]] = peso.get(f[0], 0) + float(f[1]); pats[f[2] if len(f) > 2 else ""] += 1
    pid = patron or pats.most_common(1)[0][0]
    otros = [p for p in pats if p != pid]
    if otros: A.append(("Media", f"Hay demandas con otros patrones ({', '.join(otros)}); no se tocan y su caudal no entra en el reparto."))
    suma = sum(peso.values()); mult = float(I.opciones().get("DEMAND MULTIPLIER", 1))
    if abs(suma - 1) > 1e-3: A.append(("Alta", f"Los pesos suman {suma:.6f}; el caudal de entrada no coincide con el patrón.".replace(".", ",", 1)))
    if abs(mult - 1) > 1e-9: A.append(("Alta", f"DEMAND MULTIPLIER = {mult}; ponerlo en 1 antes de calibrar pérdidas."))
    q0 = _patron(I, pid)
    if not len(q0): raise ValueError(f"No se encontró el patrón de demanda {pid}")
    # longitud asociada a cada nodo
    Ln = {}
    for _, f in I.filas("PIPES"):
        for n in (f[1], f[2]):
            if n in J: Ln[n] = Ln.get(n, 0.0) + float(f[3]) / 2
    if nodos is not None: Ln = {k: v for k, v in Ln.items() if k in set(nodos)}
    ids = sorted(Ln); Lv = np.array([Ln[k] for k in ids])
    # instantes
    h0, h1 = ventana_h or (0.0, dur)
    def ejes(S):
        t = S.presion.index.values; m = (t >= h0 - 1e-9) & (t < h1 - 1e-9); return t, m
    def q_tot(t): return q0[(np.floor(t / ppat + 1e-9).astype(int)) % len(q0)] * suma * mult
    tmp = Path(tempfile.mkdtemp()); S = simular(ruta_inp); t, m = ejes(S)
    v_tot = float((q_tot(t[m]) * paso).sum())
    if objetivo_m3 is None:
        if pct is None: raise ValueError("Indicar objetivo_m3 o pct")
        objetivo_m3 = pct * v_tot
    its = []; C = np.zeros(len(ids)); q_nuevo = q0.copy(); recorte = 0
    for it in range(1, max_iter + 1):
        Pn = S.presion[ids].values.clip(min=0) ** n1                     # (t, nodo)
        K = objetivo_m3 / ((Pn[m] * Lv).sum() * paso)                    # con las presiones de la última corrida
        C = K * Lv
        fuga_t = (Pn * C).sum(axis=1)                                    # m³/h en cada instante simulado
        # patrón nuevo = caudal total − fuga, promediando por paso de patrón
        idx = (np.floor(t / ppat + 1e-9).astype(int)); n_p = len(q0); qn = q0.copy().astype(float)
        fp = pd.Series(fuga_t[:-1] if t[-1] >= dur - 1e-9 else fuga_t).groupby(idx[:len(fuga_t) - (1 if t[-1] >= dur - 1e-9 else 0)] % n_p).mean()
        for k, f in fp.items(): qn[k] = q0[k] - f / (suma * mult)
        recorte = int((qn < 0).sum()); qn = qn.clip(min=0)
        I2 = Inp(I.texto(), I.codificacion); _fijar_patron(I2, pid, qn)
        I2.fijar_opcion("EMITTER EXPONENT", f"{n1:g}")
        I2.sec["EMITTERS"] = [";Nodo             Coeficiente"] + [f" {k:<16} {c:.8f}" for k, c in zip(ids, C)] + [""]
        f_tmp = tmp / f"it{it}.inp"; I2.guardar(f_tmp)
        S = simular(f_tmp); t, m = ejes(S)
        Pn2 = S.presion[ids].values.clip(min=0) ** n1
        v_fuga = float((Pn2[m] * C).sum() * paso)
        cons = float((qn[(idx % n_p)][m] * suma * mult * paso).sum())
        err = v_fuga / objetivo_m3 - 1
        its.append(dict(iteracion=it, K=K, fuga_m3=v_fuga, error=err, consumo_m3=cons, entrada_m3=cons + v_fuga, entrada_vs_medido=(cons + v_fuga) / v_tot - 1))
        q_nuevo = qn
        if abs(err) < tol and abs((cons + v_fuga) / v_tot - 1) < tol: break
    if recorte: A.append(("Alta", f"En {recorte} pasos la fuga supera al caudal medido y el consumo se recorta a 0; el objetivo de pérdidas es demasiado alto para ese horario."))
    if abs(its[-1]["error"]) >= tol: A.append(("Media", f"No converge en {max_iter} iteraciones; error final de {its[-1]['error']:+.2%}."))
    Pm = S.presion[ids].values[m].clip(min=0)
    em = pd.DataFrame({"nodo": ids, "longitud_m": Lv, "coeficiente": C, "presion_media": Pm.mean(axis=0), "fuga_m3": (Pm ** n1 * C).sum(axis=0) * paso})
    em["fuga_media_m3h"] = em.fuga_m3 / (h1 - h0)
    Pn2 = S.presion[ids].values.clip(min=0) ** n1; fuga_t = (Pn2 * C).sum(axis=1)
    n_p = len(q0); tt = np.arange(n_p) * ppat
    fuga_p = pd.Series(fuga_t).groupby((np.floor(t / ppat + 1e-9).astype(int)) % n_p).mean().reindex(range(n_p)).values
    pat = pd.DataFrame({"h": tt, "original": q0, "sin_fugas": q_nuevo, "fuga": fuga_p})
    noche = (tt % 24 < 4); dia_ = ~noche
    res = dict(patron=pid, n1=n1, ventana_h=(h0, h1), volumen_entrada_m3=v_tot, objetivo_m3=objetivo_m3, fuga_m3=its[-1]["fuga_m3"],
               pct_fuga=its[-1]["fuga_m3"] / v_tot, consumo_m3=its[-1]["consumo_m3"], iteraciones=len(its), nodos_con_emisor=len(ids),
               coef_total=float(C.sum()), fuga_media_m3h=its[-1]["fuga_m3"] / (h1 - h0), fuga_noche_m3h=float(np.nanmean(fuga_p[noche])),
               fuga_dia_m3h=float(np.nanmean(fuga_p[dia_])), longitud_km=float(Lv.sum() / 1000))
    I_fin = Inp(I.texto(), I.codificacion); _fijar_patron(I_fin, pid, q_nuevo); I_fin.fijar_opcion("EMITTER EXPONENT", f"{n1:g}")
    I_fin.sec["EMITTERS"] = [";Nodo             Coeficiente"] + [f" {k:<16} {c:.8f}" for k, c in zip(ids, C)] + [""]
    A.sort(key=lambda a: {"Alta": 0, "Media": 1, "Info": 2}[a[0]])
    return Perdidas(I_fin, res, em, pat, pd.DataFrame(its), A)


def exportar(p: Perdidas, ruta_inp, carpeta=None, expl_id="") -> dict:
    from openpyxl import Workbook
    from . import estilo as E
    from .patrones import texto_csv_giswater
    ruta_inp = Path(ruta_inp); carpeta = Path(carpeta) if carpeta else ruta_inp.parent; carpeta.mkdir(parents=True, exist_ok=True); r = p.resumen
    sal = {"inp": carpeta / f"{ruta_inp.stem}_fugas.inp"}; p.inp.guardar(sal["inp"])
    sal["csv"] = carpeta / f"patron_{r['patron']}_sin_fugas_giswater.csv"
    sal["csv"].write_text(texto_csv_giswater(r["patron"], "Demanda sin fugas CMH", expl_id, p.patron.sin_fugas), encoding="ascii")
    wb = Workbook(); ws = wb.active; ws.title = "Resumen"
    E.titulo(ws, f"Calibración de pérdidas con emisores · {ruta_inp.name}", f"FAVAD con N1 = {r['n1']:g}; emisores proporcionales a la longitud de tubería de cada nodo")
    t = pd.DataFrame([("Patrón de demanda modificado", r["patron"], ""), ("Ventana", f"{r['ventana_h'][0]:g}–{r['ventana_h'][1]:g}", "h"),
                      ("Volumen de entrada", r["volumen_entrada_m3"], "m³"), ("Fuga objetivo", r["objetivo_m3"], "m³"), ("Fuga lograda", r["fuga_m3"], "m³"),
                      ("Fuga sobre el volumen de entrada", r["pct_fuga"] * 100, "%"), ("Consumo de clientes", r["consumo_m3"], "m³"),
                      ("Fuga media", r["fuga_media_m3h"], "m³/h"), ("Fuga media de 00:00 a 04:00", r["fuga_noche_m3h"], "m³/h"),
                      ("Fuga media el resto del día", r["fuga_dia_m3h"], "m³/h"), ("Nodos con emisor", r["nodos_con_emisor"], "ud"),
                      ("Coeficiente de emisor total", r["coef_total"], "m³/h por m^N1"), ("Longitud de red con emisores", r["longitud_km"], "km"),
                      ("Iteraciones", r["iteraciones"], "")], columns=["Concepto", "Valor", "Unidad"])
    E.tabla(ws, 4, t, anchos=[40, 18, 16], formatos={"Valor": "#,##0.000"})
    fila = 6 + len(t)
    if p.avisos: E.tabla(ws, fila, pd.DataFrame(p.avisos, columns=["Gravedad", "Aviso"]), ajustar=True); ws.freeze_panes = None
    ws = wb.create_sheet("Iteraciones"); E.titulo(ws, "Convergencia")
    i = p.iteraciones.copy(); i.columns = ["Iteración", "K", "Fuga (m³)", "Error frente al objetivo", "Consumo (m³)", "Entrada (m³)", "Entrada frente al medido"]
    E.tabla(ws, 3, i, anchos=[10, 16, 14, 14, 14, 14, 14], formatos={"K": "0.000E+00", "Fuga (m³)": "#,##0.0", "Error frente al objetivo": "+0.00%;-0.00%", "Consumo (m³)": "#,##0.0", "Entrada (m³)": "#,##0.0", "Entrada frente al medido": "+0.00%;-0.00%"})
    ws = wb.create_sheet("Emisores"); E.titulo(ws, "Emisores por nodo", "Coeficiente en m³/h por m^N1, como va en la sección [EMITTERS]")
    e = p.emisores.copy(); e.columns = ["Nodo", "Longitud asociada (m)", "Coeficiente de emisor", "Presión media (mca)", "Fuga (m³)", "Fuga media (m³/h)"]
    E.tabla(ws, 4, e, anchos=[18, 14, 16, 14, 12, 14], formatos={"Longitud asociada (m)": "0.0", "Coeficiente de emisor": "0.00000000", "Presión media (mca)": "0.00", "Fuga (m³)": "0.00", "Fuga media (m³/h)": "0.0000"}, gris=True)
    ws = wb.create_sheet("Patron"); E.titulo(ws, f"Patrón {r['patron']}", "Original (caudal total), sin fugas (lo que queda como demanda) y fuga simulada, en m³/h")
    d = p.patron.copy(); d.columns = ["t (h)", "Original", "Sin fugas", "Fuga"]
    E.tabla(ws, 4, d, anchos=[9, 12, 12, 12], formatos={"t (h)": "0.00", "Original": "0.0000", "Sin fugas": "0.0000", "Fuga": "0.0000"}, gris=True)
    for w in wb.worksheets: w.sheet_view.showGridLines = False
    sal["excel"] = carpeta / f"Perdidas_{ruta_inp.stem}.xlsx"; wb.save(sal["excel"])
    return sal
