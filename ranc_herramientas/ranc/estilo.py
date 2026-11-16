"""Formato corporativo Acciona para los Excel y las figuras."""
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

ROJO, ROJO_OSC, GRIS, ROSA = "D3161E", "A5121A", "58595B", "F2DCDD"
_l = Side(style="thin", color="BFBFBF"); BORDE = Border(left=_l, right=_l, top=_l, bottom=_l)


def fuente(**k): return Font(name="Calibri", **k)


def titulo(ws, texto, sub=None):
    ws["A1"] = texto; ws["A1"].font = fuente(bold=True, size=14, color=ROJO_OSC)
    if sub: ws["A2"] = sub; ws["A2"].font = fuente(italic=True, size=10, color=GRIS)


def tabla(ws, fila, df, anchos=None, formatos=None, ajustar=False, gris=False):
    """Escribe un DataFrame con cabecera roja (o gris) y filas alternas en rosado."""
    for j, c in enumerate(df.columns, 1):
        x = ws.cell(fila, j, c); x.font = fuente(bold=True, color="FFFFFF")
        x.fill = PatternFill("solid", fgColor=GRIS if gris else ROJO); x.border = BORDE
        x.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
    relleno = PatternFill("solid", fgColor=ROSA)
    for i, reg in enumerate(df.itertuples(index=False), 1):
        for j, v in enumerate(reg, 1):
            if v is not None and not isinstance(v, str):
                try:
                    if v != v: v = None          # NaN / NaT
                except Exception: pass
            if hasattr(v, "to_pydatetime"): v = v.to_pydatetime()
            x = ws.cell(fila + i, j, v); x.font = fuente(size=10); x.border = BORDE
            x.alignment = Alignment(wrap_text=ajustar, vertical="top")
            if i % 2 == 0: x.fill = relleno
            f = (formatos or {}).get(df.columns[j - 1])
            if f: x.number_format = f
    from openpyxl.utils import get_column_letter
    for j, w in enumerate(anchos or [], 1): ws.column_dimensions[get_column_letter(j)].width = w
    ws.freeze_panes = ws.cell(fila + 1, 1)
