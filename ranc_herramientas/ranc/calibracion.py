"""Herramienta 5 · Evaluador de calibración.

Corre el INP y compara con los .dat de campaña (los mismos que usa el calibrador).
Criterios del pliego (§8.4.4.7): ±5,0 m en piezométrica, ±15 % en caudales y ±2,0 m en niveles.
Un punto cumple si al menos `pasos_min` de sus pasos de 15 min quedan dentro de la tolerancia;
el modelo cumple si lo hace al menos `puntos_min` de los puntos. Ambos umbrales se pueden cambiar.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import numpy as np
import pandas as pd
from .simular import simular, Sim
from .inp import Inp

DEF = dict(tol_presion_m=5.0, tol_caudal=0.15, tol_nivel_m=2.0, pasos_min=0.80, puntos_min=0.80)


@dataclass
class Evaluacion:
    par: dict
    puntos: pd.DataFrame
    series: dict
    resumen: pd.DataFrame
    avisos: list


def leer_dat(ruta) -> pd.DataFrame:
    d = pd.read_csv(ruta, sep="\t", header=None, names=["id", "h", "valor"], dtype={"id": str})
    return d


def _metricas(obs, sim, tol, relativa):
    e = sim - obs; n = len(e)
    den = float(((obs - obs.mean()) ** 2).sum())
    dentro = (e.abs() <= tol * obs.abs()) if relativa else (e.abs() <= tol)
    return dict(n=n, medido=float(obs.mean()), modelo=float(sim.mean()), sesgo=float(e.mean()), mae=float(e.abs().mean()),
                rmse=float(np.sqrt((e ** 2).mean())), nse=float(1 - (e ** 2).sum() / den) if den > 0 else np.nan,
                r=float(obs.corr(sim)) if obs.std() > 0 and sim.std() > 0 else np.nan, pasos_dentro=float(dentro.mean()),
                amplitud_medida=float(obs.quantile(0.95) - obs.quantile(0.05)), amplitud_modelo=float(sim.quantile(0.95) - sim.quantile(0.05)))


def evaluar(ruta_inp, dat: dict, par: dict | None = None, sim: Sim | None = None) -> Evaluacion:
    """dat = {'presion': ruta, 'caudal': ruta, 'nivel': ruta} (las que haya)."""
    P = {**DEF, **(par or {})}; S = sim or simular(ruta_inp); A = [("Media", a) for a in S.avisos[:5]]
    I = Inp.leer(ruta_inp); embalses = {f[0] for _, f in I.filas("RESERVOIRS")}; cota = {f[0]: float(f[1]) for _, f in I.filas("TANKS")}
    filas = []; series = {}
    cfgm = {"presion": (S.presion, P["tol_presion_m"], False, "mca"), "caudal": (S.caudal, P["tol_caudal"], True, "m³/h"),
            "nivel": (None, P["tol_nivel_m"], False, "m")}
    for mag, ruta in dat.items():
        if not ruta: continue
        d = leer_dat(ruta); tabla, tol, rel, uni = cfgm[mag]
        for pid, g in d.groupby("id", sort=False):
            g = g.set_index(g.h.round(4)).valor
            if mag == "nivel":
                if pid in embalses:
                    A.append(("Info", f"El nivel de {pid} es condición de contorno (embalse con patrón); no se evalúa como resultado.")); continue
                if pid not in S.carga: A.append(("Alta", f"El id {pid} del .dat de nivel no existe en el modelo.")); continue
                s = S.carga[pid] - cota.get(pid, 0.0)
            else:
                if pid not in tabla: A.append(("Alta", f"El id {pid} del .dat de {mag} no existe en el modelo.")); continue
                s = tabla[pid]
            s = s.set_axis(s.index.to_series().round(4)); com = g.index.intersection(s.index)
            fuera = len(g) - len(com)
            if fuera: A.append(("Info", f"{pid}: {fuera} registros del .dat caen fuera del tiempo simulado."))
            if len(com) < 10: A.append(("Alta", f"{pid}: menos de 10 pasos comunes entre el .dat y la simulación.")); continue
            o, x = g[com], s[com]
            if mag == "caudal" and x.mean() < 0 < o.mean(): x = -x; A.append(("Info", f"{pid}: el enlace está orientado al revés; se compara con el signo cambiado."))
            m = _metricas(o, x, tol, rel); m.update(magnitud=mag, punto=pid, unidad=uni, cumple=m["pasos_dentro"] >= P["pasos_min"])
            filas.append(m); series[(mag, pid)] = pd.DataFrame({"medido": o, "modelo": x})
    pt = pd.DataFrame(filas)
    res = []
    for mag, nom, tol in (("presion", "Piezométrica", f"±{P['tol_presion_m']:g} m"), ("caudal", "Caudales", f"±{P['tol_caudal']:.0%}"), ("nivel", "Niveles de tanque", f"±{P['tol_nivel_m']:g} m")):
        g = pt[pt.magnitud == mag] if len(pt) else pt
        if not len(g): continue
        frac = g.cumple.mean()
        res.append({"Criterio": nom, "Tolerancia": tol, "Puntos": len(g), "Puntos que cumplen": int(g.cumple.sum()), "Fracción": frac,
                    "RMSE medio": g.rmse.mean(), "Cumple": "Sí" if frac >= P["puntos_min"] else "No"})
    if len(pt):
        gp = pt[pt.magnitud == "presion"]
        for r in gp.itertuples():
            if abs(r.sesgo) > 2: A.append(("Media", f"{r.punto}: sesgo de {r.sesgo:+.2f} m; revisar cota del nodo, enlace del punto o pérdida de carga aguas arriba.".replace(".", ",", 1)))
        if len(gp) >= 3 and (gp.sesgo > 0).all(): A.append(("Media", "El modelo da más presión que la medida en todos los puntos: falta pérdida de carga (fugas sin cargar, válvulas parcialmente cerradas o cota del tanque alta)."))
        if len(gp) >= 3 and (gp.sesgo < 0).all(): A.append(("Media", "El modelo da menos presión que la medida en todos los puntos: revisar la cota de referencia del tanque."))
        amp = gp[gp.amplitud_medida > 0.5]
        if len(amp) and (amp.amplitud_modelo / amp.amplitud_medida).median() > 1.5: A.append(("Media", "La amplitud día-noche de la presión del modelo supera en más del 50 % a la medida."))
        if len(amp) and (amp.amplitud_modelo / amp.amplitud_medida).median() < 0.67: A.append(("Media", "La amplitud día-noche de la presión del modelo es menor que la medida; es típico de un modelo sin fugas dependientes de la presión."))
        if (pt.magnitud == "caudal").any(): A.append(("Info", "Si el caudal comparado es el de entrada y su patrón viene de la misma medición, el ajuste de caudal es circular y no valida el modelo."))
    A.sort(key=lambda a: {"Alta": 0, "Media": 1, "Info": 2}[a[0]])
    return Evaluacion(P, pt, series, pd.DataFrame(res), A)


def exportar(ev: Evaluacion, nombre, carpeta) -> dict:
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from openpyxl import Workbook
    from openpyxl.drawing.image import Image
    from . import estilo as E
    carpeta = Path(carpeta); carpeta.mkdir(parents=True, exist_ok=True)
    wb = Workbook(); ws = wb.active; ws.title = "Cumplimiento"
    E.titulo(ws, f"Evaluación de la calibración · {nombre}", f"Un punto cumple con al menos el {ev.par['pasos_min']:.0%} de sus pasos dentro de tolerancia; el modelo, con el {ev.par['puntos_min']:.0%} de los puntos")
    E.tabla(ws, 4, ev.resumen, anchos=[20, 12, 9, 12, 10, 12, 9], formatos={"Fracción": "0%", "RMSE medio": "0.00"})
    r = 6 + len(ev.resumen)
    a = pd.DataFrame(ev.avisos, columns=["Gravedad", "Aviso"]); E.tabla(ws, r, a, anchos=[20, 12, 9, 12, 10, 12, 9]); ws.freeze_panes = None
    for i in range(len(a)): ws.merge_cells(start_row=r + 1 + i, end_row=r + 1 + i, start_column=2, end_column=12)
    ws = wb.create_sheet("Por_punto"); E.titulo(ws, "Métricas por punto")
    p = ev.puntos[["magnitud", "punto", "unidad", "n", "medido", "modelo", "sesgo", "mae", "rmse", "nse", "r", "pasos_dentro", "amplitud_medida", "amplitud_modelo", "cumple"]].copy()
    p["cumple"] = np.where(p.cumple, "Sí", "No")
    p.columns = ["Magnitud", "Punto", "Unidad", "Pasos", "Media medida", "Media modelo", "Sesgo", "MAE", "RMSE", "NSE", "r", "Pasos dentro de tolerancia",
                 "Amplitud medida (P95–P5)", "Amplitud modelo (P95–P5)", "Cumple"]
    E.tabla(ws, 3, p, anchos=[10, 16, 8, 8, 11, 11, 9, 9, 9, 9, 9, 13, 13, 13, 9],
            formatos={**{c: "0.00" for c in ("Media medida", "Media modelo", "Sesgo", "MAE", "RMSE", "Amplitud medida (P95–P5)", "Amplitud modelo (P95–P5)")},
                      "NSE": "0.000", "r": "0.000", "Pasos dentro de tolerancia": "0.0%"})
    # figura
    ks = list(ev.series); n = len(ks); sal = {}
    if n:
        col = 3; fil = int(np.ceil(n / col)); fig, axs = plt.subplots(fil, col, figsize=(11, 2.3 * fil), squeeze=False)
        for ax, k in zip(axs.ravel(), ks):
            s = ev.series[k]; ax.plot(s.index, s.medido, color="#58595B", lw=0.8, label="Medido"); ax.plot(s.index, s.modelo, color="#D3161E", lw=0.8, label="Modelo")
            ax.set_title(f"{k[1]} ({k[0]})", fontsize=8, loc="left"); ax.tick_params(labelsize=7); ax.spines[["top", "right"]].set_visible(False)
        for ax in axs.ravel()[n:]: ax.axis("off")
        axs[0, 0].legend(fontsize=7, frameon=False); fig.supxlabel("Tiempo de simulación (h)", fontsize=8); fig.tight_layout()
        f = carpeta / f"calibracion_{nombre}.png"; fig.savefig(f, dpi=140); plt.close(fig); sal["figura"] = f
        ws = wb.create_sheet("Graficos"); E.titulo(ws, "Medido frente a modelo"); im = Image(str(f)); im.width = int(im.width * 0.65); im.height = int(im.height * 0.65); ws.add_image(im, "A3")
    for w in wb.worksheets: w.sheet_view.showGridLines = False
    sal["excel"] = carpeta / f"Evaluacion_calibracion_{nombre}.xlsx"; wb.save(sal["excel"])
    return sal
