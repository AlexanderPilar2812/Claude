"""Herramienta 3 · Auditor de INP.

Revisa un INP exportado de Giswater contra los errores que ya han aparecido en el proyecto y,
si se pide, escribe una copia corregida con los arreglos que no admiten duda.
No necesita el motor de EPANET.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import statistics as st
import pandas as pd
from .inp import Inp, horas

ESPERADO = {"UNITS": "CMH", "HEADLOSS": "D-W"}
PASO = 0.25; DURACION = 168.0


@dataclass
class Auditoria:
    inp: Inp
    hallazgos: pd.DataFrame
    resumen: dict
    correcciones: list


def _f(x, d=None):
    try: return float(x)
    except (TypeError, ValueError): return d


def auditar(ruta, n1: float | None = None, corregir=False) -> Auditoria:
    I = Inp.leer(ruta); H = []; C = []; R = {}
    def h(grav, sec, elem, txt, arreglo=""): H.append(dict(Gravedad=grav, Sección=sec, Elementos=elem, Hallazgo=txt, Corrección=arreglo))
    lista = lambda ids, n=12: ", ".join(map(str, ids[:n])) + (f" y {len(ids) - n} más" if len(ids) > n else "")
    # --- codificación
    raros = sorted({c for c in I.texto() if ord(c) > 127})
    if raros:
        h("Alta", "Archivo", "", f"Caracteres con tilde o no ASCII ({' '.join(raros[:10])}); el calibrador falla con error UTF-8.", "Se guarda en ASCII" if corregir else "")
        if corregir: C.append("Texto pasado a ASCII")
    # --- elementos
    J = {f[0]: (i, f) for i, f in I.filas("JUNCTIONS")}; RS = {f[0]: (i, f) for i, f in I.filas("RESERVOIRS")}; TK = {f[0]: (i, f) for i, f in I.filas("TANKS")}
    P = {f[0]: (i, f) for i, f in I.filas("PIPES")}; V = {f[0]: (i, f) for i, f in I.filas("VALVES")}; B = {f[0]: (i, f) for i, f in I.filas("PUMPS")}
    nodos = set(J) | set(RS) | set(TK); enlaces = {**P, **V, **B}
    for nom, sec in (("JUNCTIONS", "JUNCTIONS"), ("PIPES", "PIPES"), ("VALVES", "VALVES")):
        ids = [f[0] for _, f in I.filas(sec)]; dup = sorted({x for x in ids if ids.count(x) > 1}) if len(ids) != len(set(ids)) else []
        if dup: h("Alta", nom, lista(dup), f"{len(dup)} identificadores duplicados.")
    R.update(nodos=len(J), embalses=len(RS), tanques=len(TK), tuberias=len(P), valvulas=len(V), bombas=len(B))
    # --- opciones y tiempos
    O = I.opciones(); T = I.opciones("TIMES")
    for k, v in ESPERADO.items():
        if O.get(k, "").upper() != v: h("Alta", "OPTIONS", k, f"{k} = {O.get(k, 'sin definir')}; el protocolo de exportación pide {v}.")
    dm = _f(O.get("DEMAND MULTIPLIER"), 1.0)
    if abs(dm - 1) > 1e-9:
        h("Alta", "OPTIONS", "DEMAND MULTIPLIER", f"DEMAND MULTIPLIER = {dm}: el modelo demanda un {abs(dm - 1):.1%} {'más' if dm > 1 else 'menos'} que el caudal del patrón.", "Puesto en 1.0" if corregir else "")
        if corregir: I.fijar_opcion("DEMAND MULTIPLIER", "1.0"); C.append("DEMAND MULTIPLIER = 1.0")
    ee = _f(O.get("EMITTER EXPONENT"), 0.5)
    if n1 is not None and abs(ee - n1) > 1e-9:
        h("Media", "OPTIONS", "EMITTER EXPONENT", f"EMITTER EXPONENT = {ee}, distinto del N1 = {n1} de la línea base.")
    R["modelo_demanda"] = O.get("DEMAND MODEL", "DDA")
    dur = horas(T.get("DURATION", "0"))
    if abs(dur - DURACION) > 1e-6: h("Media", "TIMES", "DURATION", f"Duración de {dur:g} h; el protocolo pide {DURACION:g} h.")
    pasos = {}
    for k in ("HYDRAULIC TIMESTEP", "PATTERN TIMESTEP", "REPORT TIMESTEP"):
        pasos[k] = horas(T.get(k, "1:00"))
        if abs(pasos[k] - PASO) > 1e-6: h("Media", "TIMES", k, f"{k} = {T.get(k, 'sin definir')}; el protocolo pide 0:15.")
    R.update(duracion_h=dur)
    # --- cotas
    cota = {k: _f(f[1], 0.0) for k, (_, f) in J.items()}
    cero = [k for k, z in cota.items() if z == 0]
    if cero: h("Alta", "JUNCTIONS", lista(cero), f"{len(cero)} nodos con cota 0; reasignar desde el MDT.")
    ady = {n: [] for n in nodos}; faltan = []
    for k, (_, f) in enlaces.items():
        a, b = f[1], f[2]
        for x in (a, b):
            if x not in nodos: faltan.append(f"{k}→{x}")
        if a in nodos and b in nodos:
            L = _f(f[3]) if k in P else 0.0
            ady[a].append((b, k, L)); ady[b].append((a, k, L))
    if faltan: h("Alta", "Enlaces", lista(faltan), f"{len(faltan)} extremos de enlace apuntan a nodos que no existen.")
    salto = []
    for n, z in cota.items():
        vec = [cota[m] for m, k, L in ady[n] if m in cota and cota[m] != 0 and L is not None and L < 60]
        if z != 0 and len(vec) >= 1 and abs(z - st.median(vec)) > 30: salto.append(n)
    if salto: h("Alta", "JUNCTIONS", lista(salto), f"{len(salto)} nodos con más de 30 m de diferencia de cota respecto de sus vecinos a menos de 60 m; posible error de muestreo del MDT.")
    enteros = [k for k, z in cota.items() if z != 0 and float(z).is_integer()]
    if enteros and len(enteros) > 0.02 * max(len(cota), 1): h("Info", "JUNCTIONS", lista(enteros), f"{len(enteros)} nodos con cota entera exacta; conviene revisar si vienen del MDT.")
    # --- demandas
    col = {k: _f(f[2], 0.0) if len(f) > 2 else 0.0 for k, (_, f) in J.items()}
    dem = {}
    for _, f in I.filas("DEMANDS"): dem.setdefault(f[0], []).append(_f(f[1], 0.0))
    s_col = sum(col.values()); s_dem = sum(sum(v) for v in dem.values()); total = s_dem + sum(v for k, v in col.items() if k not in dem)
    R.update(suma_columna_junctions=s_col, suma_seccion_demands=s_dem, demanda_base_total=total, nodos_con_demanda=len({k for k, v in col.items() if v} | {k for k, v in dem.items() if any(v)}))
    if total == 0: h("Alta", "DEMANDS", "", "La demanda base total es 0; el modelo no consume y la convergencia relativa no funciona.")
    elif 0.8 < total < 1.2 and abs(total - 1) > 1e-3: h("Alta", "DEMANDS", "", f"Los pesos de demanda suman {total:.6f}, no 1; el caudal del modelo se desvía un {total - 1:+.2%} del patrón.")
    if s_dem > 0 and s_col == 0: h("Media", "DEMANDS", "", "Las demandas están en la sección [DEMANDS] y la columna de [JUNCTIONS] está en cero; Loss management parece leer solo la columna.")
    multi = [k for k, v in dem.items() if len(v) > 1]
    doble = [k for k in dem if col.get(k)]
    if doble: h("Media", "DEMANDS", lista(doble), f"{len(doble)} nodos tienen demanda en [JUNCTIONS] y también en [DEMANDS]; EPANET usa solo la de [DEMANDS].")
    if multi: h("Info", "DEMANDS", lista(multi), f"{len(multi)} nodos con más de una categoría de demanda; EPANET las suma, pero una herramienta que lea solo la primera verá menos demanda.")
    huerf = [k for k in dem if k not in J]
    if huerf: h("Alta", "DEMANDS", lista(huerf), f"{len(huerf)} demandas asignadas a nodos que no están en [JUNCTIONS].")
    # --- patrones
    pat = {}
    for _, f in I.filas("PATTERNS"): pat.setdefault(f[0], []).extend(_f(x, 0.0) for x in f[1:])
    usados_dem = {f[3] for _, (_, f) in J.items() if len(f) > 3} | {f[2] for _, f in I.filas("DEMANDS") if len(f) > 2}
    if O.get("PATTERN"): usados_dem.add(O["PATTERN"])
    usados_emb = {f[2] for _, (_, f) in RS.items() if len(f) > 2}
    ptp = pasos["PATTERN TIMESTEP"]
    for p in sorted((usados_dem | usados_emb) - set(pat)):
        if p not in ("1", ""): h("Alta", "PATTERNS", p, f"El patrón {p} se usa pero no está definido.")
    for p in sorted(usados_dem & set(pat)):
        n = len(pat[p]); cub = n * ptp
        if cub + 1e-6 < dur and abs(cub - 24) > 1e-6: h("Media", "PATTERNS", p, f"El patrón de demanda {p} tiene {n} valores ({cub:g} h) y la simulación dura {dur:g} h; EPANET lo repite desde el inicio.")
        elif abs(cub - 24) < 1e-6 and dur > 24: h("Info", "PATTERNS", p, f"El patrón de demanda {p} es de 24 h y se repite los {dur / 24:g} días.")
        R.setdefault("patrones_demanda", {})[p] = dict(valores=n, media=sum(pat[p]) / n if n else 0)
    for p in sorted(usados_emb & set(pat)):
        n = len(pat[p]); cub = n * ptp
        if cub + 1e-6 < dur: h("Media", "PATTERNS", p, f"El patrón de nivel {p} cubre {cub:g} h de las {dur:g} h; al final se repite el principio.")
    # --- embalses y tanques
    for k, (_, f) in RS.items():
        c = _f(f[1]); p = f[2] if len(f) > 2 else None
        if p and p in pat and c is not None:
            lo, hi = min(pat[p]) * c, max(pat[p]) * c
            h("Info", "RESERVOIRS", k, f"Embalse con carga {c:g} y patrón {p}: la cota varía entre {lo:.3f} y {hi:.3f} msnm." + (" Carga unitaria con cota absoluta en el patrón." if c == 1 else ""))
        elif c is not None and not p: h("Info", "RESERVOIRS", k, f"Embalse de cota fija {c:g} msnm, sin patrón de nivel.")
    # --- tuberías
    cortas = [k for k, (_, f) in P.items() if (_f(f[3], 1) or 0) < 0.5]
    if cortas: h("Info", "PIPES", lista(cortas), f"{len(cortas)} tuberías de menos de 0,5 m.")
    dmal = [k for k, (_, f) in P.items() if not (1 < (_f(f[4], 0) or 0) < 900)]
    if dmal: h("Alta", "PIPES", lista(dmal), f"{len(dmal)} tuberías con diámetro fuera de rango (≤ 1 mm o ≥ 900 mm).")
    R["longitud_km"] = sum(_f(f[3], 0) or 0 for _, f in P.values()) / 1000
    rug = {}
    for _, f in P.values(): rug[f[5]] = rug.get(f[5], 0) + 1
    R["rugosidades"] = dict(sorted(rug.items(), key=lambda x: -x[1])[:8])
    # --- válvulas
    estado = {f[0]: (i, f[1].upper()) for i, f in I.filas("STATUS")}
    vd = []
    for k, (i, f) in V.items():
        d = _f(f[3], 0) or 0
        if not (1 < d < 900):
            dv = [(_f(P[e][1][4], 0) or 0) for n in (f[1], f[2]) for _, e, _ in ady.get(n, []) if e in P]
            dv = [x for x in dv if 1 < x < 900]; nuevo = max(dv) if dv else None
            vd.append(k)
            if corregir and nuevo: I.fijar_campo("VALVES", i, 3, f"{nuevo:g}"); C.append(f"Válvula {k}: diámetro {d:g} → {nuevo:g} mm (tubería adyacente)")
    if vd: h("Alta", "VALVES", lista(vd), f"{len(vd)} válvulas con diámetro fuera de rango (0,001 o 999 mm); rompen la calibración y dan velocidades absurdas.", "Diámetro tomado de la tubería adyacente" if corregir else "")
    anul = [k for k, (_, f) in V.items() if f[4].upper() in ("PRV", "PSV", "FCV", "PBV") and estado.get(k, (0, ""))[1] == "OPEN"]
    if anul:
        h("Alta", "STATUS", lista(anul), f"{len(anul)} válvulas reguladoras con OPEN en [STATUS]: la consigna queda anulada.", "Línea de [STATUS] comentada" if corregir else "")
        if corregir:
            for k in anul:
                i = estado[k][0]; I.sec["STATUS"][i] = ";" + I.sec["STATUS"][i] + "   ; anulaba la consigna"; C.append(f"Válvula {k}: quitado OPEN de [STATUS]")
    sincons = [k for k, (_, f) in V.items() if f[4].upper() == "PRV" and (_f(f[5], 0) or 0) <= 0]
    if sincons: h("Alta", "VALVES", lista(sincons), f"{len(sincons)} VRP sin consigna.")
    huer = [k for k in estado if k not in enlaces]
    if huer: h("Alta", "STATUS", lista(huer), f"{len(huer)} elementos citados en [STATUS] que no existen como enlace; puede ser una válvula que no se exportó y la red queda partida.")
    R["valvulas_por_tipo"] = {}
    for _, f in V.values(): R["valvulas_por_tipo"][f[4].upper()] = R["valvulas_por_tipo"].get(f[4].upper(), 0) + 1
    R["valvulas_cerradas"] = sum(1 for k, (_, s) in estado.items() if s == "CLOSED")
    # --- conectividad
    fuentes = set(RS) | set(TK)
    def alcanzables(sin_cerradas):
        visto = set(fuentes); cola = list(fuentes)
        while cola:
            n = cola.pop()
            for m, k, _ in ady.get(n, []):
                if sin_cerradas:
                    cerr = estado.get(k, (0, ""))[1] == "CLOSED" or (k in P and len(P[k][1]) > 7 and P[k][1][7].upper() == "CLOSED")
                    if cerr: continue
                if m not in visto: visto.add(m); cola.append(m)
        return visto
    a1 = alcanzables(False); suel = sorted(set(J) - a1)
    if not fuentes: h("Alta", "Red", "", "El modelo no tiene embalses ni tanques.")
    if suel: h("Alta", "Red", lista(suel), f"{len(suel)} nodos sin conexión con ninguna fuente; la red está partida.")
    a2 = alcanzables(True); ais = sorted(set(J) & a1 - a2); ais_dem = [n for n in ais if col.get(n) or any(dem.get(n, []))]
    if ais: h("Media" if ais_dem else "Info", "Red", lista(ais), f"{len(ais)} nodos quedan aislados por elementos cerrados; {len(ais_dem)} de ellos tienen demanda.")
    # --- controles, reglas, emisores
    if I.filas("RULES"): h("Media", "RULES", "", "Hay reglas en [RULES]; los controles de bombas y válvulas deben ir en Controls, o el calibrador da Error 200.")
    R["controles"] = len(I.filas("CONTROLS")); R["emisores"] = len(I.filas("EMITTERS")); R["exponente_emisor"] = ee
    df = pd.DataFrame(H, columns=["Gravedad", "Sección", "Elementos", "Hallazgo", "Corrección"])
    df["Hallazgo"] = df.Hallazgo.str.replace(r"(?<=\d)\.(?=\d)", ",", regex=True)       # decimales con coma
    df["o"] = df.Gravedad.map({"Alta": 0, "Media": 1, "Info": 2}); df = df.sort_values("o", kind="stable").drop(columns="o").reset_index(drop=True)
    return Auditoria(I, df, R, C)


def exportar(a: Auditoria, ruta_inp, carpeta=None) -> dict:
    from openpyxl import Workbook
    from . import estilo as E
    ruta_inp = Path(ruta_inp); carpeta = Path(carpeta) if carpeta else ruta_inp.parent; carpeta.mkdir(parents=True, exist_ok=True)
    wb = Workbook(); ws = wb.active; ws.title = "Hallazgos"
    n = a.hallazgos.Gravedad.value_counts()
    E.titulo(ws, f"Auditoría del INP · {ruta_inp.name}", f"{n.get('Alta', 0)} hallazgos altos, {n.get('Media', 0)} medios y {n.get('Info', 0)} informativos")
    d = a.hallazgos.copy(); d.insert(0, "N°", range(1, len(d) + 1))
    E.tabla(ws, 4, d, anchos=[5, 10, 13, 44, 90, 34], ajustar=True)
    ws = wb.create_sheet("Resumen"); E.titulo(ws, "Resumen del modelo")
    filas = []
    for k, v in a.resumen.items():
        if isinstance(v, dict): v = "; ".join(f"{x}: {y if not isinstance(y, dict) else ', '.join(f'{p} {q:g}' for p, q in y.items())}" for x, y in v.items())
        filas.append({"Dato": k.replace("_", " ").capitalize(), "Valor": v})
    E.tabla(ws, 3, pd.DataFrame(filas), anchos=[34, 90], ajustar=True)
    if a.correcciones:
        ws = wb.create_sheet("Correcciones"); E.titulo(ws, "Correcciones aplicadas en la copia corregida")
        E.tabla(ws, 3, pd.DataFrame({"N°": range(1, len(a.correcciones) + 1), "Corrección": a.correcciones}), anchos=[5, 110])
    for w in wb.worksheets: w.sheet_view.showGridLines = False
    sal = {"excel": carpeta / f"Auditoria_{ruta_inp.stem}.xlsx"}; wb.save(sal["excel"])
    if a.correcciones:
        sal["inp"] = carpeta / f"{ruta_inp.stem}_corregido.inp"; a.inp.guardar(sal["inp"])
    return sal
