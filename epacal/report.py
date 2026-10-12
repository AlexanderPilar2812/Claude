"""Informe HTML autocontenido (gráficos PNG embebidos) de la calibración."""
from __future__ import annotations

import base64
import html
import io
import os
from datetime import datetime
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

C_OBS = "#2a78d6"      # observado
C_CAL = "#eb6834"      # calibrado
C_INI = "#8a8984"      # modelo original
C_ALT = "#1baf7a"
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"

plt.rcParams.update({
    "font.size": 9, "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2,
    "ytick.color": INK2, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.spines.top": False, "axes.spines.right": False, "axes.titlesize": 9.5,
    "axes.titleweight": "bold", "axes.titlecolor": INK, "legend.frameon": False,
    "lines.linewidth": 1.4, "figure.dpi": 110,
})


def _png(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return '<img alt="" src="data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode() + '">'


def _table(df: pd.DataFrame, max_rows: int = 400, fmt: str = "{:.4g}") -> str:
    d = df.head(max_rows).copy()
    for c in d.columns:
        if pd.api.types.is_float_dtype(d[c]):
            d[c] = d[c].map(lambda v: "" if pd.isna(v) else fmt.format(v))
    more = f'<p class="muted">… {len(df) - max_rows} filas más en el CSV.</p>' if len(df) > max_rows else ""
    return '<div class="tw">' + d.to_html(index=False, border=0, escape=True) + "</div>" + more


def _badge(ok: bool, txt_ok="CUMPLE", txt_ko="NO CUMPLE") -> str:
    return (f'<span class="badge ok">✔ {txt_ok}</span>' if ok
            else f'<span class="badge ko">✖ {txt_ko}</span>')


def _rules_table(rules) -> str:
    if not rules:
        return '<p class="muted">Sin reglas evaluadas.</p>'
    rows = [{"Regla": r.name, "Variable": r.kind, "Error": r.error,
             "Umbral": f"{r.threshold:g}{' %' if r.error == 'RELATIVE' else ''}",
             "% instantes": f"{r.timesteps_pct:g}", "% puntos": f"{r.points_pct:g}",
             "Puntos OK": f"{r.points_ok}/{r.points_total}",
             "Obs OK": f"{r.obs_ok}/{r.obs_total}", "Resultado": "PASA" if r.passed else "FALLA"}
            for r in rules]
    return _table(pd.DataFrame(rows))


# ------------------------------------------------------------------ gráficos
def fig_pareto(h) -> str:
    F = h.pareto_F
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    if F.shape[1] >= 2:
        ax.scatter(F[:, 0], F[:, 1], s=46, color=C_OBS, edgecolor="white", linewidth=1.5,
                   label="Frente de Pareto", zorder=3)
        k = h.choice_index
        ax.scatter([F[k, 0]], [F[k, 1]], s=120, color=C_CAL, edgecolor="white", linewidth=2,
                   label=f"Elegida #{k + 1}", zorder=4)
        ax.scatter([h.obj_final[0]], [h.obj_final[1]], s=90, marker="D", color=C_ALT,
                   edgecolor="white", linewidth=1.5, label="Final (refinada)", zorder=5)
        for i in range(len(F)):
            ax.annotate(f"#{i + 1}", (F[i, 0], F[i, 1]), xytext=(4, 4),
                        textcoords="offset points", fontsize=7, color=INK2)
        ax.set_xlabel(h.objectives[0])
        ax.set_ylabel(h.objectives[1])
        ax.legend(loc="upper right")
    else:
        ax.plot(F[:, 0], "o", color=C_OBS)
    ax.set_title("Frente de Pareto (objetivos a minimizar)")
    return _png(fig)


def fig_convergence(h) -> str:
    if not h.history:
        return ""
    g = [x["gen"] for x in h.history]
    fig, ax = plt.subplots(figsize=(6.4, 2.6))
    ax.plot(g, [x["hv"] for x in h.history], color=C_OBS, marker="o", ms=3)
    ax.set_xlabel("Generación")
    ax.set_ylabel("Hipervolumen")
    ax.set_title("Convergencia de NSGA-II")
    return _png(fig)


def fig_series(h, max_sensors: int = 40) -> str:
    obs = h.obs
    ts = obs.timestamps()
    n = min(len(obs.sensors), max_sensors)
    cols = 2
    rows = int(np.ceil(n / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(11, 2.4 * rows), squeeze=False)
    val_start = ts[~obs.calib_mask][0] if (~obs.calib_mask).any() else None
    for i in range(rows * cols):
        ax = axes[i // cols][i % cols]
        if i >= n:
            ax.axis("off")
            continue
        s = obs.sensors[i]
        ax.plot(ts, obs.values[i], color=C_OBS, lw=1.1, label="Observado")
        ax.plot(ts, h.sim_initial[i], color=C_INI, lw=1.0, ls=":", label="Original")
        ax.plot(ts, h.sim_final[i], color=C_CAL, lw=1.4, ls="--", label="Calibrado")
        if val_start is not None:
            ax.axvspan(val_start, ts[-1], color="#f3f2ee", zorder=0)
        ax.set_title(f"{s.kind} en {s.element}")
        ax.tick_params(axis="x", labelsize=7, rotation=0)
        ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%d/%m"))
        if i == 0:
            ax.legend(loc="best", fontsize=7)
    fig.tight_layout()
    return _png(fig)


def fig_scatter(h) -> str:
    kinds = h.obs.kinds
    fig, axes = plt.subplots(1, len(kinds), figsize=(4.2 * len(kinds), 3.8), squeeze=False)
    for ax, kind in zip(axes[0], kinds):
        rows = h.obs.rows_of(kind)
        o = h.obs.values[rows].ravel()
        s0 = h.sim_initial[rows].ravel()
        s1 = h.sim_final[rows].ravel()
        m = np.isfinite(o)
        ax.scatter(o[m], s0[m], s=6, color=C_INI, alpha=0.35, label="Original", linewidths=0)
        ax.scatter(o[m], s1[m], s=6, color=C_CAL, alpha=0.5, label="Calibrado", linewidths=0)
        lo, hi = np.nanmin(o[m]), np.nanmax(o[m])
        ax.plot([lo, hi], [lo, hi], color=INK, lw=1)
        ax.set_xlabel("Observado")
        ax.set_ylabel("Simulado")
        ax.set_title(f"{kind}: simulado vs observado")
        ax.legend(loc="upper left", fontsize=7)
    fig.tight_layout()
    return _png(fig)


def fig_sensitivity(h) -> str:
    sens = h.sensitivity_final or h.sensitivity_initial
    if not sens:
        return ""
    names = sens["names"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 0.45 * len(names) + 2))
    y = np.arange(len(names))
    a1.barh(y, sens["css_rel"], color=C_OBS, height=0.6)
    a1.set_yticks(y, names)
    a1.invert_yaxis()
    a1.set_xlim(0, 1.05)
    a1.set_xlabel("Sensibilidad compuesta escalada (relativa)")
    a1.set_title("¿Qué parámetros 'ven' los sensores?")
    C = np.array(sens["corr"])
    im = a2.imshow(C, cmap="RdBu_r", vmin=-1, vmax=1)
    a2.set_xticks(y, names, rotation=45, ha="right")
    a2.set_yticks(y, names)
    a2.grid(False)
    for i in range(len(names)):
        for j in range(len(names)):
            a2.text(j, i, f"{C[i, j]:.2f}", ha="center", va="center", fontsize=7,
                    color="white" if abs(C[i, j]) > 0.6 else INK)
    a2.set_title("Correlación entre parámetros")
    fig.colorbar(im, ax=a2, shrink=0.8)
    fig.tight_layout()
    return _png(fig)


def fig_losses(L) -> str:
    out = []
    pdf, ts = L["patterns"], L["timeseries"]
    for (dma, pat), g in pdf.groupby(["dma", "patron"]):
        fig, (a1, a2) = plt.subplots(2, 1, figsize=(8, 5), sharex=True,
                                     gridspec_kw={"height_ratios": [2, 1]})
        a1.plot(g["periodo"], g["original"], color=C_OBS, label="Original")
        a1.plot(g["periodo"], g["ajustado"], color=C_CAL, ls="--", label="Ajustado")
        a1.set_ylabel("Multiplicador")
        a1.set_title(f"Patrón {pat} — sector {dma}")
        a1.legend()
        a2.plot(g["periodo"], g["fuga_media"], color=C_ALT)
        a2.set_ylabel(f"Fuga ({L['flow_units']})")
        a2.set_xlabel("Periodo del patrón")
        a2.set_title("Caudal de fugas medio por periodo")
        fig.tight_layout()
        out.append(_png(fig))
    for dma in L["summary"]["dma"]:
        fig, ax = plt.subplots(figsize=(8, 3))
        h = ts["t_s"] / 3600
        ax.plot(h, ts[f"{dma}|entrada"], color=C_OBS, label="Entrada")
        ax.plot(h, ts[f"{dma}|consumo"], color=C_CAL, ls="--", label="Consumo abonados")
        ax.plot(h, ts[f"{dma}|fugas"], color=C_ALT, label="Fugas")
        ax.set_xlabel("Horas")
        ax.set_ylabel(L["flow_units"])
        ax.set_title(f"Balance hídrico — sector {dma}")
        ax.legend(ncol=3)
        out.append(_png(fig))
    top = L["nodes"].head(20)
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.barh(top["junction_id"].astype(str), top["coef_emisor"], color=C_OBS, height=0.6)
    ax.invert_yaxis()
    ax.set_xlabel("Coeficiente de emisor")
    ax.set_title("20 nudos con mayor coeficiente de emisor")
    out.append(_png(fig))
    return "".join(out)


CSS = """
:root{--bg:#fcfcfb;--card:#ffffff;--ink:#0b0b0b;--ink2:#52514e;--line:#e4e3df;
--accent:#2a78d6;--ok:#0a7d32;--okbg:#e7f5ec;--ko:#b42318;--kobg:#fdecea}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#1a1a19;--card:#232322;
--ink:#f0efec;--ink2:#c3c2b7;--line:#3a3a38;--accent:#3987e5;--okbg:#12351f;--kobg:#3d1714;
--ok:#6fd38f;--ko:#ff8a80}}
:root[data-theme="dark"]{--bg:#1a1a19;--card:#232322;--ink:#f0efec;--ink2:#c3c2b7;--line:#3a3a38;
--accent:#3987e5;--okbg:#12351f;--kobg:#3d1714;--ok:#6fd38f;--ko:#ff8a80}
body{background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
margin:0;padding:24px 16px}
main{max-width:1100px;margin:0 auto}
h1{font-size:26px;margin:0 0 4px}h2{font-size:18px;margin:32px 0 10px;padding-bottom:6px;
border-bottom:2px solid var(--accent)}h3{font-size:15px;margin:20px 0 8px}
.muted{color:var(--ink2)}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px}
.card .k{font-size:12px;color:var(--ink2)}.card .v{font-size:22px;font-weight:600}
.tw{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:12.5px;background:var(--card)}
th,td{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left;white-space:nowrap}
th{background:var(--accent);color:#fff;font-weight:600}
img{max-width:100%;height:auto;background:#fff;border-radius:6px;border:1px solid var(--line);margin:6px 0}
.badge{display:inline-block;padding:3px 10px;border-radius:12px;font-weight:600;font-size:13px}
.ok{background:var(--okbg);color:var(--ok)}.ko{background:var(--kobg);color:var(--ko)}
"""


def build_report(path: str, cfg: dict, hyd=None, losses: Optional[dict] = None,
                 extra_notes: Optional[list] = None) -> str:
    name = html.escape(cfg.get("name", "Calibración"))
    parts = [f"<!doctype html><html lang='es'><head><meta charset='utf-8'>"
             f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>Informe de calibración</title><style>{CSS}</style></head><body><main>",
             f"<h1>Informe de calibración — {name}</h1>",
             f"<p class='muted'>Generado {datetime.now():%Y-%m-%d %H:%M} · modelo "
             f"{html.escape(os.path.basename(cfg.get('inp', '')))}</p>"]
    for n in extra_notes or []:
        parts.append(f"<p>{html.escape(n)}</p>")

    if losses is not None:
        s = losses["summary"]
        parts.append("<h2>1. Calibración de pérdidas</h2>")
        parts.append(f"<p>Método <b>{losses['method']}</b>, exponente N1 = {losses['exponent']:g}. "
                     f"{_badge(losses['converged'], 'CONVERGIDO', 'NO CONVERGIDO')} en "
                     f"{losses['iterations']} iteraciones.</p>")
        parts.append(_table(s))
        parts.append(fig_losses(losses))
        parts.append("<h3>Nudos con mayor fuga</h3>" + _table(losses["nodes"], 50))

    if hyd is not None:
        h = hyd
        ok_all = all(r.passed for r in h.rules_final) if h.rules_final else True
        parts.append("<h2>2. Calibración hidráulica</h2>")
        cards = []
        for o, a, b in zip(h.objectives, h.obj_initial, h.obj_final):
            imp = 100 * (1 - b / a) if a > 0 else 0
            cards.append(f"<div class='card'><div class='k'>{o} ({cfg.get('hydraulic', {}).get('metric', 'rmse').upper()})</div>"
                         f"<div class='v'>{b:.3f}</div><div class='muted'>original {a:.3f} · "
                         f"mejora {imp:.0f} %</div></div>")
        cards.append(f"<div class='card'><div class='k'>Simulaciones</div><div class='v'>{h.n_evals}</div>"
                     f"<div class='muted'>{h.elapsed:.0f} s</div></div>")
        cards.append(f"<div class='card'><div class='k'>Aceptación (calibración)</div>"
                     f"<div class='v'>{sum(r.passed for r in h.rules_final)}/{len(h.rules_final)}</div>"
                     f"<div>{_badge(ok_all, 'VÁLIDA', 'NO VÁLIDA')}</div></div>")
        if h.rules_valid:
            okv = all(r.passed for r in h.rules_valid)
            cards.append(f"<div class='card'><div class='k'>Aceptación (validación)</div>"
                         f"<div class='v'>{sum(r.passed for r in h.rules_valid)}/{len(h.rules_valid)}</div>"
                         f"<div>{_badge(okv, 'VÁLIDA', 'NO VÁLIDA')}</div></div>")
        parts.append("<div class='cards'>" + "".join(cards) + "</div>")
        parts.append("<h3>Reglas de aceptación — modelo original (periodo de calibración)</h3>"
                     + _rules_table(h.rules_initial))
        parts.append("<h3>Reglas de aceptación — modelo calibrado (periodo de calibración)</h3>"
                     + _rules_table(h.rules_final))
        if h.rules_valid:
            parts.append("<h3>Reglas de aceptación — periodo de validación (datos no usados al calibrar)</h3>"
                         + _rules_table(h.rules_valid))
        parts.append("<h3>Parámetros calibrados</h3>")
        parts.append("<p class='muted'>IC95: intervalo de confianza aproximado a partir del "
                     "jacobiano. CSS: sensibilidad compuesta escalada (1 = el más influyente). "
                     "'en_limite' indica que el valor tocó el límite del rango: conviene revisar "
                     "el rango o la hipótesis física.</p>")
        parts.append(_table(h.param_table))
        if h.frozen:
            parts.append(f"<p>Parámetros congelados por baja sensibilidad: <b>{html.escape(', '.join(h.frozen))}</b>. "
                         "Los sensores actuales no permiten estimarlos; considere añadir puntos de medida.</p>")
        parts.append(fig_sensitivity(h))
        parts.append("<h3>Frente de Pareto</h3>" + fig_pareto(h) + fig_convergence(h))
        pf = pd.DataFrame(h.pareto_F, columns=h.objectives)
        pf.insert(0, "#", [f"#{i + 1}" for i in range(len(pf))])
        pf["reglas"] = [sum(r.passed for r in rr) for rr in h.pareto_rules]
        for j, p in enumerate(h.params):
            pf[p.name] = h.pareto_X[:, j]
        parts.append(_table(pf))
        parts.append("<h3>Métricas por sensor</h3>" + _table(h.sensor_metrics))
        biased = h.sensor_metrics[(h.sensor_metrics["tipo"] != "FLOW") &
                                  (h.sensor_metrics["periodo"] == "calibracion") &
                                  (h.sensor_metrics["sesgo_final"].abs() > 1.5)]
        if len(biased):
            parts.append("<p><b>Atención:</b> sensores con sesgo sistemático &gt; 1,5 m tras calibrar "
                         f"({html.escape(', '.join(biased['sensor']))}). Revise la cota del sensor, "
                         "su ubicación en el modelo o válvulas cerradas cercanas antes de forzar la "
                         "rugosidad.</p>")
        parts.append("<h3>Observado vs simulado</h3><p class='muted'>Zona sombreada: periodo de validación.</p>")
        parts.append(fig_scatter(h))
        parts.append(fig_series(h))
        parts.append(f"<h3>Objetos modificados ({len(h.modified)})</h3>" + _table(h.modified, 300))

    parts.append("</main></body></html>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))
    return path
