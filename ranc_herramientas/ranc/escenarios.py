"""Herramienta 7 · Escenarios de explotación en lote.

A partir del modelo calibrado corre: funcionamiento normal (instantes de caudal mínimo, medio y
punta), incendio en los hidrantes indicados, contingencias por cierre de tuberías y el horizonte
de planeación. Cada escenario se hace sobre una copia del INP; el original no se toca.
Criterios por defecto: presión de servicio ≥ 15 mca, estática ≤ 50 mca (70 en puntos aislados),
velocidad ≤ 3,0 m/s (Norma Técnica AyA 2017) y 32 L/s por hidrante en zona residencial.
"""
from __future__ import annotations
import tempfile
from dataclasses import dataclass
from pathlib import Path
import numpy as np
import pandas as pd
from .inp import Inp
from .simular import simular

DEF = dict(presion_min=15.0, presion_max=50.0, presion_max_aislada=70.0, velocidad_max=3.0, fmd=1.2,
           incendio_caudal_ls=32.0, incendio_instante="qmd", presion_residual_min=10.5)


@dataclass
class Escenarios:
    par: dict
    resumen: pd.DataFrame
    detalle: dict
    avisos: list


def _nodos_demanda(I):
    n = {f[0] for _, f in I.filas("JUNCTIONS") if len(f) > 2 and float(f[2]) != 0}
    return n | {f[0] for _, f in I.filas("DEMANDS") if float(f[1]) != 0}


def _estado(nombre, instante, S, t, nd, P, extra=None):
    p = S.presion.loc[t, nd]; v = S.velocidad.loc[t].abs()
    d = dict(Escenario=nombre, Instante=instante, **{"t (h)": t, "Caudal de entrada (m³/h)": float(S.entrada[t]),
             "Presión mínima (mca)": float(p.min()), "Nodo de presión mínima": p.idxmin(), "Presión media (mca)": float(p.mean()),
             "Presión máxima (mca)": float(p.max()), f"Nodos bajo {P['presion_min']:g} mca": int((p < P["presion_min"]).sum()),
             "Nodos sin servicio": int((p <= 0).sum()), f"Nodos sobre {P['presion_max']:g} mca": int((p > P["presion_max"]).sum()),
             f"Nodos sobre {P['presion_max_aislada']:g} mca": int((p > P["presion_max_aislada"]).sum()),
             "Velocidad máxima (m/s)": float(v.max()), "Tubería de velocidad máxima": v.idxmax()})
    d.update(extra or {})
    return d, p.sort_values().head(15).rename("Presión (mca)").rename_axis("Nodo").reset_index()


def _correr(I, tmp, nombre):
    f = Path(tmp) / f"{nombre}.inp"; I.guardar(f); S = simular(f)
    fuentes = [f_[0] for _, f_ in I.filas("RESERVOIRS")] + [f_[0] for _, f_ in I.filas("TANKS")]
    S.entrada = -S.demanda[[c for c in fuentes if c in S.demanda]].sum(axis=1)
    return S


def correr(ruta_inp, cfg: dict | None = None) -> Escenarios:
    cfg = cfg or {}; P = {**DEF, **(cfg.get("criterios") or {})}; A = []; R = []; D = {}
    I0 = Inp.leer(ruta_inp); nd = sorted(_nodos_demanda(I0)); tmp = tempfile.mkdtemp()
    S = _correr(I0, tmp, "actual"); A += [("Media", "Estado actual: " + a) for a in S.avisos[:3]]
    q = S.entrada.iloc[:-1] if len(S.entrada) > 1 else S.entrada
    t_min, t_max = float(q.idxmin()), float(q.idxmax()); t_med = float((q - q.mean()).abs().idxmin()); t_qmd = float((q - P["fmd"] * q.mean()).abs().idxmin())
    inst = {"Caudal mínimo": t_min, "Caudal medio": t_med, "Caudal punta": t_max}
    for nom, t in inst.items():
        d, det = _estado("Estado actual", nom, S, t, nd, P); R.append(d); D[f"Actual · {nom}"] = det
    # presión estática: máxima de la simulación por nodo
    pmax = S.presion[nd].max()
    if (pmax > P["presion_max_aislada"]).any(): A.append(("Media", f"{int((pmax > P['presion_max_aislada']).sum())} nodos superan {P['presion_max_aislada']:g} mca en algún momento (máximo {pmax.max():.1f} mca en {pmax.idxmax()}).".replace(".", ",", 1)))
    # incendio
    for h in cfg.get("incendio") or []:
        nodo = h["nodo"]; ql = float(h.get("caudal_ls", P["incendio_caudal_ls"])); I = Inp(I0.texto(), I0.codificacion)
        if nodo not in S.presion: A.append(("Alta", f"Incendio: el nodo {nodo} no existe en el modelo.")); continue
        I.sec.setdefault("PATTERNS", []).append(" RANC-UNO         1.0")
        # una línea en [DEMANDS] sustituye a la demanda de [JUNCTIONS]: se copia antes la demanda base del nodo
        if not any(f[0] == nodo for _, f in I.filas("DEMANDS")):
            jf = next((f for _, f in I.filas("JUNCTIONS") if f[0] == nodo), None)
            if jf and len(jf) > 2 and float(jf[2]) != 0:
                I.sec.setdefault("DEMANDS", []).append(f" {nodo:<16} {jf[2]}   {jf[3] if len(jf) > 3 else ''}   ;demanda base")
        I.sec.setdefault("DEMANDS", []).append(f" {nodo:<16} {ql * 3.6:.4f}   RANC-UNO   ;incendio")
        Sx = _correr(I, tmp, f"incendio_{nodo}")
        t = {"qmd": t_qmd, "punta": t_max, "medio": t_med}[h.get("instante", P["incendio_instante"])]
        pr = float(Sx.presion.loc[t, nodo]); servido = float(Sx.demanda.loc[t, nodo]) - float(S.demanda.loc[t, nodo])
        d, det = _estado(f"Incendio en {nodo} ({ql:g} L/s)", {"qmd": "QMD + incendio", "punta": "Punta + incendio", "medio": "Medio + incendio"}[h.get("instante", P["incendio_instante"])],
                         Sx, t, nd, P, {"Presión en el hidrante (mca)": pr, "Caudal de incendio servido (L/s)": servido / 3.6,
                                        "Cumple": "Sí" if pr >= P["presion_residual_min"] and servido / 3.6 >= 0.98 * ql and float(Sx.presion.loc[t, nd].min()) >= P["presion_min"] else "No"})
        R.append(d); D[f"Incendio {nodo}"] = det
    # contingencias
    for k, c in enumerate(cfg.get("contingencia") or [], 1):
        I = Inp(I0.texto(), I0.codificacion); cerr = c["cerrar"] if isinstance(c["cerrar"], list) else [c["cerrar"]]
        enl = {f[0] for s in ("PIPES", "VALVES", "PUMPS") for _, f in I.filas(s)}; mal = [x for x in cerr if x not in enl]
        if mal: A.append(("Alta", f"Contingencia {k}: los enlaces {', '.join(mal)} no existen.")); continue
        for x in cerr: I.sec.setdefault("STATUS", []).append(f" {x:<16} CLOSED")
        nom = c.get("nombre", "Cierre de " + ", ".join(cerr))
        try:
            Sx = _correr(I, tmp, f"contingencia_{k}")
        except Exception as e:
            A.append(("Alta", f"Contingencia '{nom}': la simulación no corre ({str(e)[:80]}).")); continue
        d, det = _estado(f"Contingencia: {nom}", "Caudal punta", Sx, t_max, nd, P); R.append(d); D[f"Contingencia {k}"] = det
    # horizonte
    hz = cfg.get("horizonte")
    if hz:
        I = Inp(I0.texto(), I0.codificacion); fac = float(hz["factor"]); m0 = float(I.opciones().get("DEMAND MULTIPLIER", 1)); I.fijar_opcion("DEMAND MULTIPLIER", f"{m0 * fac:g}")
        Sx = _correr(I, tmp, "horizonte"); nomh = f"Horizonte {hz.get('anos', 20)} años (demanda × {fac:g})"
        for nom, t in inst.items():
            d, det = _estado(nomh, nom, Sx, t, nd, P); R.append(d); D[f"Horizonte · {nom}"] = det
        A.append(("Info", "El horizonte multiplica toda la demanda, fugas incluidas si están en el patrón; con emisores cargados solo crece el consumo."))
    res = pd.DataFrame(R)
    cb = f"Nodos bajo {P['presion_min']:g} mca"
    for r in res.itertuples(index=False):
        rd = dict(zip(res.columns, r))
        if rd[cb] > 0 and rd["Escenario"] == "Estado actual": A.append(("Media", f"Estado actual, {rd['Instante'].lower()}: {rd[cb]} nodos por debajo de {P['presion_min']:g} mca (mínimo en {rd['Nodo de presión mínima']})."))
        if rd["Velocidad máxima (m/s)"] > P["velocidad_max"]: A.append(("Media", f"{rd['Escenario']}, {rd['Instante'].lower()}: velocidad de {rd['Velocidad máxima (m/s)']:.2f} m/s en {rd['Tubería de velocidad máxima']}.".replace(".", ",", 1)))
    A.sort(key=lambda a: {"Alta": 0, "Media": 1, "Info": 2}[a[0]])
    return Escenarios(P, res, D, A)


def exportar(es: Escenarios, nombre, carpeta) -> Path:
    from openpyxl import Workbook
    from . import estilo as E
    carpeta = Path(carpeta); carpeta.mkdir(parents=True, exist_ok=True)
    wb = Workbook(); ws = wb.active; ws.title = "Resumen"
    E.titulo(ws, f"Escenarios de explotación · {nombre}", f"Presión mínima {es.par['presion_min']:g} mca · máxima {es.par['presion_max']:g}/{es.par['presion_max_aislada']:g} mca · velocidad máxima {es.par['velocidad_max']:g} m/s")
    r = es.resumen.copy(); fm = {c: "0.00" for c in r.columns if "(" in c and "Nodos" not in c}
    E.tabla(ws, 4, r, anchos=[34, 18] + [13] * (len(r.columns) - 2), formatos=fm, ajustar=True)
    ws = wb.create_sheet("Avisos"); E.titulo(ws, "Avisos"); E.tabla(ws, 3, pd.DataFrame(es.avisos, columns=["Gravedad", "Aviso"]), anchos=[10, 120], ajustar=True)
    ws = wb.create_sheet("Nodos_criticos"); E.titulo(ws, "Los 15 nodos de menor presión en cada escenario"); c = 1
    from openpyxl.utils import get_column_letter
    for k, d in es.detalle.items():
        ws.cell(3, c, k).font = E.fuente(bold=True, color=E.ROJO_OSC, size=10)
        for j, col in enumerate(d.columns):
            x = ws.cell(4, c + j, col); x.font = E.fuente(bold=True, color="FFFFFF"); x.fill = __import__("openpyxl").styles.PatternFill("solid", fgColor=E.GRIS); x.border = E.BORDE
        for i, row in enumerate(d.itertuples(index=False), 5):
            for j, v in enumerate(row):
                x = ws.cell(i, c + j, v); x.font = E.fuente(size=10); x.border = E.BORDE
                if j == 1: x.number_format = "0.00"
        ws.column_dimensions[get_column_letter(c)].width = 16; ws.column_dimensions[get_column_letter(c + 1)].width = 13; ws.column_dimensions[get_column_letter(c + 2)].width = 3
        c += 3
    for w in wb.worksheets: w.sheet_view.showGridLines = False
    f = carpeta / f"Escenarios_{nombre}.xlsx"; wb.save(f); return f
