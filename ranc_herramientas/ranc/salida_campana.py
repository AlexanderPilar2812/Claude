"""Excel de control de calidad, figuras y CSV de la herramienta de campaña."""
from __future__ import annotations
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from openpyxl import Workbook
from openpyxl.drawing.image import Image
from openpyxl.styles import Alignment
from . import estilo as E

C_ROJO, C_GRIS, C_CLARO, C_ROSA = "#D3161E", "#58595B", "#A9AAAC", "#F2DCDD"
plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False})
FMT = mdates.DateFormatter("%d/%m\n%H:%M")


def _sombrear(ax, eventos):
    if eventos.empty: return
    for _, g in eventos[eventos.tipo == "general"].groupby("evento"):
        ax.axvspan(g.caida.min(), g.retorno.max(), color=C_ROSA, lw=0, zorder=0)


def figuras(res, carpeta: Path) -> list:
    carpeta.mkdir(parents=True, exist_ok=True)
    m = res.malla; P = res.puntos; out = []
    pres = [p for p in P.values() if p.magnitud != "caudal"]; caud = [p for p in P.values() if p.magnitud == "caudal"]
    # 1. vista general
    fig, axs = plt.subplots(2, 1, figsize=(10, 5.6), sharex=True, height_ratios=[1, 1.3])
    if "Q_sector" in m:
        axs[0].plot(m.t, m.Q_sector, color=C_ROJO, lw=1.1)
    axs[0].set_ylabel("Caudal (L/s)"); axs[0].set_title("Serie de 15 min depurada (cortes de servicio sombreados)", loc="left", fontsize=10)
    tonos = [C_GRIS, C_ROJO, "#E58A8E", "#8C1D22", C_CLARO]
    for k, p in enumerate(pres): axs[1].plot(m.t, m[p.id], lw=0.9, color=tonos[k % len(tonos)], label=f"{p.id} · {p.cfg.get('nombre', '')}")
    if "PMP" in m: axs[1].plot(m.t, m.PMP, lw=1.6, color="black", label="PMP ponderada")
    axs[1].set_ylabel("Presión (mca)"); axs[1].legend(ncol=5, fontsize=8, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.22))
    for a in axs: _sombrear(a, res.eventos); a.xaxis.set_major_formatter(FMT); a.set_xlim(m.t.iloc[0], m.t.iloc[-1])
    fig.tight_layout(); f = carpeta / "01_serie_depurada.png"; fig.savefig(f, dpi=150); plt.close(fig); out.append(f)
    # 2. efecto del offset (sin suavizado)
    con = [p for p in pres if p.diag.get("offset")]
    if con:
        fig, axs = plt.subplots(len(con), 1, figsize=(10, 2.6 * len(con)), squeeze=False)
        for ax, p in zip(axs[:, 0], con):
            d = p.crudo; o = p.diag["offset"]["offset"]
            ax.plot(d.t, d.valor, color=C_CLARO, lw=1.0, label="Crudo")
            dentro = (d.t >= p.diag["instalado"][0]) & (d.t <= p.diag["instalado"][1])
            ax.plot(d.t[dentro], d.corregido[dentro], color=C_ROJO, lw=1.0, label=f"Con offset de {o:+.3f} m (negativos a 0)")
            ax.axhline(0, color=C_GRIS, lw=0.6)
            for t in p.diag["instalado"]: ax.axvline(t, color=C_GRIS, lw=0.8, ls=":")
            ax.set_ylabel(f"{p.id} (mca)"); ax.legend(fontsize=8, frameon=False, ncol=2, loc="center"); ax.xaxis.set_major_formatter(FMT)
        axs[0, 0].set_title("Efecto del offset por sensor, sin suavizado (líneas punteadas: instalación y retiro)", loc="left", fontsize=10)
        fig.tight_layout(); f = carpeta / "02_efecto_offset.png"; fig.savefig(f, dpi=150); plt.close(fig); out.append(f)
    # 3. depuración del caudal
    for p in caud:
        d = p.crudo; fig, ax = plt.subplots(figsize=(10, 3.4))
        lim = max(p.diag["q_max"], 1)
        ax.plot(d.t, d.valor.clip(-0.15 * lim, 1.1 * lim), color=C_CLARO, lw=1.0, label="Crudo (recortado al eje)")
        v = d.corregido.where(d.valido); ax.plot(d.t, v, color=C_ROJO, lw=1.0, label="Válido")
        malos = d[~d.valido & d.valor.notna()]
        ax.scatter(malos.t, malos.valor.clip(-0.15 * lim, 1.1 * lim), s=14, color="black", marker="x", label="Invalidado", zorder=3)
        _sombrear(ax, res.eventos); ax.set_ylabel(f"{p.id} (L/s)"); ax.xaxis.set_major_formatter(FMT)
        ax.legend(fontsize=8, frameon=False, ncol=3); ax.set_title("Datos de caudal invalidados, sin suavizado", loc="left", fontsize=10)
        fig.tight_layout(); f = carpeta / f"03_caudal_{p.id}.png"; fig.savefig(f, dpi=150); plt.close(fig); out.append(f)
    return out


def exportar(res, carpeta) -> dict:
    carpeta = Path(carpeta); carpeta.mkdir(parents=True, exist_ok=True)
    cfg = res.cfg; ini, fin = cfg["_ventana"]; P = res.puntos
    nombre = f"{cfg.get('sector', 'sector')} {cfg.get('codigo', '')}".strip()
    wb = Workbook()
    # Hallazgos
    ws = wb.active; ws.title = "Hallazgos"
    E.titulo(ws, f"Control de calidad de la campaña · {nombre}", f"Ventana {ini:%d/%m/%Y %H:%M} – {fin:%d/%m/%Y %H:%M} · malla de {cfg['_param']['paso_min']} min · sin suavizado")
    h = pd.DataFrame(res.hallazgos, columns=["Gravedad", "Punto", "Hallazgo"]); h.insert(0, "N°", range(1, len(h) + 1))
    E.tabla(ws, 4, h, anchos=[5, 10, 10, 120], ajustar=True)
    for r in range(5, 5 + len(h)):
        c = ws.cell(r, 2); c.alignment = Alignment(horizontal="center", vertical="top")
        if c.value == "Alta": c.font = E.fuente(bold=True, color=E.ROJO, size=10)
    # Equipos
    ws = wb.create_sheet("Equipos"); E.titulo(ws, "Equipos y archivos leídos")
    fil = []
    for p in P.values():
        dg = p.diag; off = dg.get("offset") or {}; ins = dg.get("instalado") or (None, None)
        fil.append({"Punto": p.id, "Nombre": p.cfg.get("nombre", ""), "Magnitud": p.magnitud, "Equipo": p.meta.get("equipo", ""),
                    "Archivo": p.meta.get("archivo", ""), "Inicio": dg["inicio"], "Fin": dg["fin"], "Registros": dg["n"],
                    "Intervalo (s)": dg["intervalo_s"], "Segundo dentro del cuarto de hora": dg["desfase_malla_s"],
                    "Registros faltantes": dg["faltantes"], "No válidos": int((~p.crudo.valido).sum()),
                    "Instalado desde": ins[0], "Instalado hasta": ins[1],
                    "Lectura en seco (m)": -off["offset"] if off else None, "Offset aplicado (m)": off.get("offset"),
                    "Cobertura de la ventana": dg["cobertura"], "Peso": p.cfg.get("peso")})
    f_fecha = "dd/mm/yyyy hh:mm"
    E.tabla(ws, 3, pd.DataFrame(fil), anchos=[8, 22, 10, 30, 40, 16, 16, 10, 10, 14, 11, 10, 16, 16, 11, 11, 11, 8],
            formatos={"Inicio": f_fecha, "Fin": f_fecha, "Instalado desde": f_fecha, "Instalado hasta": f_fecha,
                      "Lectura en seco (m)": "0.000", "Offset aplicado (m)": "0.000", "Cobertura de la ventana": "0.0%"})
    # Relojes
    ws = wb.create_sheet("Relojes"); E.titulo(ws, "Comprobación de relojes", "Se compara cada equipo con la referencia en los cortes de servicio y por correlación de las variaciones")
    r = res.relojes.rename(columns={"punto": "Punto", "referencia": "Referencia", "metodo_correlacion": "Correlación usada",
                                    "desfase_corr_min": "Desfase por correlación (min)", "r": "r en el desfase", "r0": "r sin desfase",
                                    "retorno_vs_ref_min": "Diferencia en los cortes (min)", "veredicto": "Veredicto"})
    E.tabla(ws, 4, r, anchos=[8, 20, 24, 14, 10, 10, 14, 50], formatos={"r en el desfase": "0.00", "r sin desfase": "0.00"})
    # Eventos
    ws = wb.create_sheet("Eventos"); E.titulo(ws, "Cortes de servicio y caídas locales", "Presión por debajo del umbral de corte o caudal sin dato")
    if not res.eventos.empty:
        e = res.eventos.rename(columns={"evento": "Evento", "tipo": "Tipo", "punto": "Punto", "magnitud": "Magnitud", "caida": "Caída",
                                        "retorno": "Retorno", "duracion_h": "Duración (h)"})
        E.tabla(ws, 4, e, anchos=[14, 10, 8, 10, 17, 17, 11], formatos={"Caída": f_fecha, "Retorno": f_fecha, "Duración (h)": "0.00"})
    # Pegado
    if not res.pegado.empty:
        ws = wb.create_sheet("Pegado"); E.titulo(ws, "Datos pegados en el Excel frente al crudo", "Desfase = hora de la fila del Excel menos hora del registro crudo que contiene ese valor")
        g = res.pegado.copy(); g["desfase"] = g.desfase_min.map(lambda x: "" if pd.isna(x) else f"{'-' if x < 0 else '+'}{abs(int(x)) // 60} h {abs(int(x)) % 60:02d} min")
        g = g.rename(columns={"punto": "Punto", "hoja": "Hoja y columna", "fila_ini": "Fila inicial", "fila_fin": "Fila final", "n": "Valores",
                              "t_excel_ini": "Hora en el Excel", "t_crudo_ini": "Hora en el crudo", "desfase_min": "Desfase (min)",
                              "estado": "Estado", "desfase": "Desfase"})
        E.tabla(ws, 4, g, anchos=[8, 22, 9, 9, 9, 17, 17, 11, 32, 14], formatos={"Hora en el Excel": f_fecha, "Hora en el crudo": "dd/mm/yyyy hh:mm:ss"})
    # Noches
    if len(res.noches):
        ws = wb.create_sheet("Noches"); E.titulo(ws, "Caudal nocturno por día", "Media y mínimo de 00:00 a 03:45 sobre la serie depurada, sin suavizado; sirve para detectar noches anómalas")
        n = res.noches.rename(columns={"dia": "Día", "n": "Valores", "media": "Media (L/s)", "minimo": "Mínimo (L/s)", "relacion": "Respecto de la mediana", "aviso": "Aviso"})
        E.tabla(ws, 4, n, anchos=[12, 9, 12, 12, 14, 12], formatos={"Día": "dd/mm/yyyy", "Media (L/s)": "0.000", "Mínimo (L/s)": "0.000", "Respecto de la mediana": "0%"})
    # Serie
    ws = wb.create_sheet("Serie_15min"); E.titulo(ws, "Serie alineada", "Valores depurados (offset, invalidación e interpolación a la malla). Las columnas _crudo llevan el dato original interpolado a la misma hora.")
    m = res.malla; cols = ["t"] + [c for c in ("Q_sector", "PMP", "PMP_sin_offset") if c in m]
    for p in P.values(): cols += [p.id, p.id + "_crudo", p.id + "_marca"]
    s = m[cols].rename(columns={"t": "FechaHora", "Q_sector": "Q sector (L/s)", "PMP": "PMP (mca)", "PMP_sin_offset": "PMP sin offset (mca)"})
    for c in s.columns:
        if s[c].dtype.kind == "f": s[c] = s[c].round(4)
    E.tabla(ws, 4, s, anchos=[17] + [13] * (len(cols) - 1), formatos={"FechaHora": f_fecha}, gris=True)
    # Gráficos
    figs = figuras(res, carpeta / "figuras")
    ws = wb.create_sheet("Graficos"); E.titulo(ws, "Gráficos", "El efecto del offset va en su propio gráfico; la herramienta no suaviza")
    y = 4
    for f in figs:
        im = Image(str(f)); k = 0.6; im.width = int(im.width * k); im.height = int(im.height * k); ws.add_image(im, f"A{y}"); y += im.height // 20 + 2
    for w in wb.worksheets:
        w.sheet_view.showGridLines = False; w.page_setup.orientation = "landscape"; w.page_setup.fitToWidth = 1; w.page_setup.fitToHeight = 0
        w.sheet_properties.pageSetUpPr.fitToPage = True
    import unicodedata
    slug = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode().replace(" ", "_")
    xlsx = carpeta / f"QA_campana_{slug}.xlsx"; wb.save(xlsx)
    csv = carpeta / f"serie_15min_{slug}.csv"; m[cols].to_csv(csv, index=False, sep=";", decimal=",", date_format="%d/%m/%Y %H:%M", encoding="utf-8-sig")
    return {"excel": xlsx, "csv": csv, "figuras": figs}
