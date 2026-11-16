"""Herramienta 1 · Campaña de medición.

Lee los crudos de cada equipo, revisa mallas de tiempo, huecos, offsets, datos no
válidos, cortes de servicio y relojes, lleva todo a una malla común de 15 min y,
si se le indica, compara lo que se pegó en un Excel contra el crudo.
No aplica suavizado: la serie de salida solo lleva offset, invalidación e interpolación
a la malla, y cada cosa queda marcada en su columna.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import numpy as np
import pandas as pd
import yaml
from .lectores import leer, Serie

DEF = dict(paso_min=15, umbral_instalado_mca=5.0, q_negativo_ls=1.0, factor_q_max=5.0,
           hueco_interp_min=20, relleno_presion_min=90, fraccion_corte=0.2, corte_min_mca=2.0,
           desfase_max_h=6, tolerancia_reloj_min=30)


@dataclass
class Punto:
    id: str
    magnitud: str
    cfg: dict
    crudo: pd.DataFrame
    meta: dict
    diag: dict = field(default_factory=dict)


@dataclass
class Resultado:
    cfg: dict
    puntos: dict
    malla: pd.DataFrame
    eventos: pd.DataFrame
    relojes: pd.DataFrame
    pegado: pd.DataFrame
    noches: pd.DataFrame
    hallazgos: list


# ------------------------------------------------------------------ utilidades
def _hhmm(td) -> str:
    s = int(round(pd.Timedelta(td).total_seconds())); sg = "-" if s < 0 else "+"; s = abs(s)
    return f"{sg}{s // 3600} h {s % 3600 // 60:02d} min"


def _tramos(mask: pd.Series):
    """Tramos contiguos True de una serie booleana indexada por tiempo -> [(ini, fin, n)]."""
    m = mask.fillna(False).astype(bool).values; idx = mask.index
    out, i = [], 0
    while i < len(m):
        if m[i]:
            j = i
            while j + 1 < len(m) and m[j + 1]: j += 1
            out.append((idx[i], idx[j], j - i + 1)); i = j + 1
        else:
            i += 1
    return out


def _n_mallas(seg, paso_s):
    """Cantidad de mallas de tiempo distintas (separadas más de 60 s y con al menos 3 registros)."""
    c = seg.value_counts(); vals = sorted(v for v, n in c.items() if n >= 3)
    grupos = []
    for v in vals:
        if grupos and (v - grupos[-1] <= 60 or paso_s - (v - grupos[0]) <= 60): grupos[-1] = v
        else: grupos.append(v)
    return max(1, len(grupos))


def diag_malla(df: pd.DataFrame, paso_s: float) -> dict:
    t = df.t; d = t.diff().dt.total_seconds().dropna()
    modal = float(d.round().mode().iloc[0]) if len(d) else np.nan
    seg = (t.dt.minute * 60 + t.dt.second) % int(paso_s)
    huecos = []
    for i in np.where(d.values > 1.5 * modal)[0]:
        a, b = t.iloc[i], t.iloc[i + 1]
        huecos.append((a, b, int(round((b - a).total_seconds() / modal)) - 1))
    return dict(n=len(df), inicio=t.iloc[0], fin=t.iloc[-1], intervalo_s=modal,
                desfase_malla_s=int(seg.mode().iloc[0]), mallas_distintas=_n_mallas(seg, paso_s),
                huecos=huecos, faltantes=sum(h[2] for h in huecos),
                duplicados=int(t.duplicated().sum()))


def ventana_instalado(df, umbral, n=4):
    """Primer y último instante con n registros seguidos por encima del umbral."""
    ok = (df.valor > umbral).rolling(n).sum() == n
    if not ok.any(): return None, None
    i0 = int(np.argmax(ok.values)) - n + 1
    i1 = len(ok) - 1 - int(np.argmax(((df.valor > umbral)[::-1].rolling(n).sum() == n).values)) + n - 1
    return df.t.iloc[i0], df.t.iloc[min(i1, len(df) - 1)]


def offset_en_seco(df, t0, t1, margen=2, limite=3.0):
    """Lectura del sensor al aire antes de instalarlo y después de retirarlo."""
    antes = df[df.t < t0].valor.iloc[:-margen] if (df.t < t0).sum() > margen else pd.Series(dtype=float)
    desp = df[df.t > t1].valor.iloc[margen:] if (df.t > t1).sum() > margen else pd.Series(dtype=float)
    seco = pd.concat([antes, desp]); seco = seco[seco.abs() < limite]
    if len(seco) < 4: return None
    return dict(offset=-float(seco.median()), n=len(seco), minimo=float(seco.min()), maximo=float(seco.max()),
                antes=None if antes.empty else float(antes.median()), despues=None if desp.empty else float(desp.median()))


def a_malla(t, v, malla, hueco_max_s):
    """Interpolación lineal en el tiempo. Queda vacío si los vecinos distan más de hueco_max_s."""
    ts = pd.DatetimeIndex(t).as_unit("ns").asi8 // 10**9; g = pd.DatetimeIndex(malla).as_unit("ns").asi8 // 10**9
    v = np.asarray(v, float); out = np.full(len(g), np.nan)
    if len(ts) == 0: return out
    j = np.searchsorted(ts, g)
    for k, (gg, jj) in enumerate(zip(g, j)):
        if jj < len(ts) and ts[jj] == gg: out[k] = v[jj]; continue
        if jj == 0 or jj >= len(ts): continue
        a, b = ts[jj - 1], ts[jj]
        if b - a <= hueco_max_s: out[k] = v[jj - 1] + (v[jj] - v[jj - 1]) * (gg - a) / (b - a)
    return out


def desfase_correlacion(a: pd.Series, b: pd.Series, max_pasos: int, signo=1):
    """Desfase (en pasos) de b respecto de a que maximiza la correlación de las variaciones."""
    da, db = a.diff(), signo * b.diff()
    res = {}
    for k in range(-max_pasos, max_pasos + 1):
        x = pd.concat([da, db.shift(-k)], axis=1).dropna()
        res[k] = x.iloc[:, 0].corr(x.iloc[:, 1]) if len(x) > 50 else np.nan
    s = pd.Series(res)
    if s.notna().sum() == 0: return None
    k = int(s.idxmax())
    return dict(pasos=k, r=float(s[k]), r0=float(s[0]))


# ------------------------------------------------------------------ pegado en Excel
def _col(letra): 
    from openpyxl.utils import column_index_from_string
    return column_index_from_string(letra)


def auditar_pegado(crudo: pd.DataFrame, ruta_xlsx, hoja, col_tiempo, col_valor, fila_inicio, factor=1.0, paso_s=900):
    """Busca en el crudo cada valor pegado en el Excel y devuelve tramos con su desfase."""
    import openpyxl
    ws = openpyxl.load_workbook(ruta_xlsx, data_only=True, read_only=True)[hoja]
    ct, cv = _col(col_tiempo), _col(col_valor)
    filas = [(i, r[ct - 1], r[cv - 1]) for i, r in enumerate(
        ws.iter_rows(min_row=fila_inicio, max_col=max(ct, cv), values_only=True), start=fila_inicio)]
    rv = crudo.valor.values; rt = crudo.t.values
    mapa = {}
    for i, x in enumerate(rv):
        if not np.isnan(x): mapa.setdefault(round(float(x), 6), []).append(i)
    reg, prev = [], None
    for fila, te, v in filas:
        if te is None or not isinstance(v, (int, float)): continue
        te = pd.Timestamp(te).round("s")
        c = mapa.get(round(v / factor, 6), [])
        if not c: idx = None
        elif prev is not None and prev + 1 in c: idx = prev + 1
        elif len(c) == 1: idx = c[0]
        else: idx = min(c, key=lambda i: abs((pd.Timestamp(rt[i]) - te).total_seconds()))
        reg.append((fila, te, idx)); prev = idx if idx is not None else prev
    des = [None if idx is None else int(round((te - pd.Timestamp(rt[idx])).total_seconds() / 60)) for _, te, idx in reg]
    def corridas(x):
        out, i = [], 0
        while i < len(x):
            j = i
            while j + 1 < len(x) and x[j + 1] == x[i]: j += 1
            out.append((i, j)); i = j + 1
        return out
    for i, j in corridas(des):          # coincidencias sueltas de menos de 4 valores no cuentan
        if des[i] is not None and j - i + 1 < 4:
            for k in range(i, j + 1): des[k] = None
    tram = []
    for i, j in corridas(des):
        idx = reg[i][2] if des[i] is not None else None
        tram.append(dict(fila_ini=reg[i][0], fila_fin=reg[j][0], n=j - i + 1, t_excel_ini=reg[i][1],
                         t_crudo_ini=None if idx is None else pd.Timestamp(rt[idx]), desfase_min=des[i]))
    df = pd.DataFrame(tram)
    if df.empty: return df
    df = df[(df.n >= 4) | df.desfase_min.notna()].reset_index(drop=True)
    def est(r):
        if pd.isna(r.desfase_min): return "sin correspondencia en el crudo"
        return "correcto" if abs(r.desfase_min) * 60 <= paso_s / 2 else "desfasado"
    df["estado"] = df.apply(est, axis=1)
    return df


# ------------------------------------------------------------------ proceso
def procesar(ruta_yaml) -> Resultado:
    ruta_yaml = Path(ruta_yaml); base = ruta_yaml.parent
    cfg = yaml.safe_load(ruta_yaml.read_text(encoding="utf-8"))
    P = {**DEF, **(cfg.get("parametros") or {})}
    paso = pd.Timedelta(minutes=P["paso_min"]); paso_s = paso.total_seconds()
    H = []  # hallazgos: (gravedad, punto, texto)
    puntos = {}
    for pc in cfg["puntos"]:
        arch = pc["archivo"] if isinstance(pc["archivo"], list) else [pc["archivo"]]
        ser = [leer(base / a, pc.get("formato")) for a in arch]
        crudo = pd.concat([s.df for s in ser]).drop_duplicates("t").sort_values("t").reset_index(drop=True)
        meta = dict(ser[0].meta); meta["archivo"] = ", ".join(s.meta["archivo"] for s in ser)
        mag = pc.get("magnitud", meta.get("magnitud"))
        p = Punto(pc["id"], mag, pc, crudo, meta)
        p.diag = diag_malla(crudo, paso_s)
        puntos[p.id] = p

    # --- validez y offset por punto
    for p in puntos.values():
        d = p.crudo; dg = p.diag
        d["corregido"] = d.valor
        if p.magnitud == "caudal":
            base_ok = d.valido & d.valor.ge(0)
            qmax = p.cfg.get("q_max_ls") or P["factor_q_max"] * float(d.valor[base_ok].quantile(0.95))
            neg = d.valido & (d.valor < -P["q_negativo_ls"]); alto = d.valido & (d.valor > qmax)
            d.loc[neg, ["valido", "motivo"]] = [False, "caudal negativo"]
            d.loc[alto, ["valido", "motivo"]] = [False, f"mayor de {qmax:.0f} L/s"]
            peq = d.valido & (d.valor < 0)
            d.loc[peq, "corregido"] = 0.0; d.loc[peq, "motivo"] = "negativo pequeño llevado a 0"
            dg.update(q_max=qmax, n_equipo=int(d.motivo.str.startswith("equipo").sum()), n_neg=int(neg.sum()),
                      n_alto=int(alto.sum()), n_peq=int(peq.sum()),
                      peor_neg=float(d.valor[neg].min()) if neg.any() else None)
            # llenado tras un tramo sin dato
            inv = ~d.valido; ini_valido = d.valido & inv.shift(1, fill_value=False) & inv.shift(2, fill_value=False)
            med = float(d.corregido[d.valido].median())
            dg["llenados"] = [(r.t, r.corregido) for r in d[ini_valido & (d.corregido > 1.5 * med)].itertuples()]
        else:
            t0, t1 = ventana_instalado(d, P["umbral_instalado_mca"])
            dg.update(instalado=(t0, t1))
            off = None
            if "offset_mca" in p.cfg:
                off = dict(offset=float(p.cfg["offset_mca"]), n=0, manual=True)
            elif t0 is not None:
                off = offset_en_seco(d, t0, t1)
            dg["offset"] = off
            o = off["offset"] if off else 0.0
            d["corregido"] = d.valor + o
            dentro = (d.t >= t0) & (d.t <= t1) if t0 is not None else pd.Series(False, index=d.index)
            fuera = ~dentro
            d.loc[fuera, ["valido", "motivo"]] = [False, "sensor fuera de la red"]
            negp = dentro & (d.corregido < 0)
            d.loc[negp, "corregido"] = 0.0; d.loc[negp, "motivo"] = "presión negativa llevada a 0"
            dg["n_neg"] = int(negp.sum()); dg["n_neg_crudo"] = int((dentro & (d.valor < 0)).sum())

    # --- malla común
    ven = cfg.get("ventana") or {}
    ini = pd.Timestamp(ven["inicio"]) if ven.get("inicio") else max(p.crudo.t[p.crudo.valido].min() for p in puntos.values()).ceil(paso)
    fin = pd.Timestamp(ven["fin"]) if ven.get("fin") else min(p.crudo.t[p.crudo.valido].max() for p in puntos.values()).floor(paso)
    malla = pd.DataFrame({"t": pd.date_range(ini, fin, freq=paso)})
    G = malla.t
    for p in puntos.values():
        d = p.crudo; v = d[d.valido]
        hueco = max(P["hueco_interp_min"] * 60, 1.34 * p.diag["intervalo_s"])
        malla[p.id] = a_malla(v.t, v.corregido, G, hueco)
        malla[p.id + "_crudo"] = a_malla(d.t, d.valor, G, hueco)
        malla[p.id + "_marca"] = ""
        vacio = malla[p.id].isna()
        mot = pd.merge_asof(malla[["t"]], d[~d.valido][["t", "motivo"]].rename(columns={"t": "tm"}), left_on="t",
                            right_on="tm", direction="nearest", tolerance=pd.Timedelta(seconds=p.diag["intervalo_s"])).motivo
        malla.loc[vacio, p.id + "_marca"] = mot[vacio].fillna("sin registro").values
        if p.magnitud != "caudal":
            lim = int(P["relleno_presion_min"] // P["paso_min"])
            dentro = (G >= p.diag["instalado"][0]) & (G <= p.diag["instalado"][1]) if p.diag["instalado"][0] is not None else False
            s = malla.set_index("t")[p.id]
            rell = s.interpolate("time", limit=lim, limit_area="inside")
            # no rellenar tramos más largos que el límite
            for a, b, n in _tramos(s.isna()):
                if n > lim: rell.loc[a:b] = np.nan
            nuevo = vacio.values & rell.notna().values & np.asarray(dentro) & (malla[p.id + "_marca"] == "sin registro").values
            malla.loc[nuevo, p.id] = rell.values[nuevo]; malla.loc[nuevo, p.id + "_marca"] = "interpolado (hueco del registro)"
            p.diag["n_interp"] = int(nuevo.sum())
        p.diag["cobertura"] = float(malla[p.id].notna().mean())

    # --- cortes de servicio (sobre toda la extensión común de los crudos, no solo la ventana)
    t_ev0 = min(p.crudo.t.min() for p in puntos.values()).floor(paso); t_ev1 = max(p.crudo.t.max() for p in puntos.values()).ceil(paso)
    GE = pd.date_range(t_ev0, t_ev1, freq=paso); caido = pd.DataFrame(index=GE); activo = pd.DataFrame(index=GE)
    for p in puntos.values():
        d = p.crudo
        hueco = max(P["hueco_interp_min"] * 60, 1.34 * p.diag["intervalo_s"])
        if p.magnitud == "caudal":
            s = pd.Series(a_malla(d[d.valido].t, d[d.valido].corregido, GE, hueco), index=GE)
            act = (GE >= d.t.min()) & (GE <= d.t.max())
            caido[p.id] = (s.isna() | (s <= 0.05)) & act
        else:
            t0, t1 = p.diag["instalado"]
            if t0 is None: caido[p.id] = False; activo[p.id] = False; continue
            s = pd.Series(a_malla(d.t, d.corregido, GE, hueco), index=GE)
            act = (GE >= t0) & (GE <= t1)
            ref = float(s[act].median()); p.diag["mediana"] = ref
            caido[p.id] = (s < max(P["corte_min_mca"], P["fraccion_corte"] * ref)) & act
        activo[p.id] = act
    npres = [p.id for p in puntos.values() if p.magnitud != "caudal"]
    cuenta = caido.sum(axis=1)
    ev = []
    generales = [(a, b) for a, b, n in _tramos(cuenta >= 2) if n >= 2]
    # unir tramos generales separados por menos de 1 h
    uni = []
    for a, b in generales:
        if uni and (a - uni[-1][1]) <= pd.Timedelta(hours=1): uni[-1] = (uni[-1][0], b)
        else: uni.append((a, b))
    for k, (a, b) in enumerate(uni, 1):
        for pid in caido.columns:
            s = caido[pid]; tr = [x for x in _tramos(s) if x[1] >= a and x[0] <= b]
            if not tr: continue
            tr = max(tr, key=lambda x: (min(x[1], b) - max(x[0], a)))
            fin_reg = tr[1] + paso
            ev.append(dict(evento=f"Corte {k}", tipo="general", punto=pid, magnitud=puntos[pid].magnitud,
                           caida=tr[0], retorno=fin_reg, duracion_h=tr[2] * P["paso_min"] / 60))
    usados = [(e["punto"], e["caida"], e["retorno"]) for e in ev]
    for pid in caido.columns:
        for a, b, n in _tramos(caido[pid]):
            if n < 2: continue
            if any(u[0] == pid and not (b < u[1] or a > u[2]) for u in usados): continue
            ev.append(dict(evento="Evento local", tipo="local", punto=pid, magnitud=puntos[pid].magnitud,
                           caida=a, retorno=b + paso, duracion_h=n * P["paso_min"] / 60))
    eventos = pd.DataFrame(ev)

    # --- relojes
    rel = []
    ref_id = next((p.id for p in puntos.values() if p.meta.get("reloj_de_red") and p.magnitud != "caudal"), None)
    S = {}
    for p in puntos.values():
        d = p.crudo; hueco = max(P["hueco_interp_min"] * 60, 1.34 * p.diag["intervalo_s"])
        S[p.id] = pd.Series(a_malla(d[d.valido].t, d[d.valido].corregido, GE, hueco), index=GE)
    ref_s = S[ref_id] if ref_id else pd.concat([S[i] for i in npres], axis=1).median(axis=1) if npres else None
    maxp = int(P["desfase_max_h"] * 60 // P["paso_min"])
    for p in puntos.values():
        fila = dict(punto=p.id, referencia=ref_id or "mediana de presiones", metodo_correlacion="", desfase_corr_min=None, r=None, r0=None,
                    retorno_vs_ref_min=None, veredicto="sin evidencia")
        if p.id == ref_id: fila["veredicto"] = "referencia (reloj de red)"; rel.append(fila); continue
        if ref_s is not None:
            c = desfase_correlacion(ref_s, S[p.id], maxp, signo=-1 if p.magnitud == "caudal" else 1)
            if c: fila.update(desfase_corr_min=c["pasos"] * P["paso_min"], r=c["r"], r0=c["r0"],
                              metodo_correlacion="caudal contra −presión" if p.magnitud == "caudal" else "presión contra presión")
        if not eventos.empty and ref_id:
            g = eventos[eventos.tipo == "general"]; difs = []
            for _, e in g[g.punto == p.id].iterrows():
                r = g[(g.evento == e.evento) & (g.punto == ref_id)]
                if len(r): difs.append((e.retorno - r.iloc[0].retorno).total_seconds() / 60 if p.magnitud != "caudal"
                                       else (e.caida - r.iloc[0].caida).total_seconds() / 60)
            if difs: fila["retorno_vs_ref_min"] = float(np.median(difs))
        tol = P["tolerancia_reloj_min"]
        ev_ok = fila["retorno_vs_ref_min"] is not None and abs(fila["retorno_vs_ref_min"]) <= tol
        ev_mal = fila["retorno_vs_ref_min"] is not None and abs(fila["retorno_vs_ref_min"]) > 2 * tol
        co = fila["desfase_corr_min"]; fuerte = fila["r"] is not None and fila["r"] >= 0.3 and (fila["r"] - fila["r0"]) >= 0.15
        if ev_ok: fila["veredicto"] = "reloj correcto (coincide en los cortes)"
        elif ev_mal: fila["veredicto"] = f"revisar reloj: difiere {_hhmm(pd.Timedelta(minutes=fila['retorno_vs_ref_min']))} en los cortes"
        elif co is not None and co != 0 and fuerte and abs(co) > tol: fila["veredicto"] = f"posible desfase de {_hhmm(pd.Timedelta(minutes=co))} por correlación"
        elif co is not None and abs(co) <= tol and fila["r"] is not None and fila["r"] >= 0.3: fila["veredicto"] = "reloj correcto (correlación)"
        rel.append(fila)
    relojes = pd.DataFrame(rel)

    # --- presión ponderada
    pesos = {p.id: float(p.cfg["peso"]) for p in puntos.values() if p.magnitud != "caudal" and p.cfg.get("peso")}
    if pesos:
        w = np.array(list(pesos.values())); ids = list(pesos)
        malla["PMP"] = (malla[ids].values * w).sum(1) / w.sum()
        malla["PMP_sin_offset"] = (malla[[i + "_crudo" for i in ids]].values * w).sum(1) / w.sum()
        malla.loc[malla[ids].isna().any(axis=1), "PMP_sin_offset"] = np.nan
    # --- caudal del sector
    qs = [p for p in puntos.values() if p.magnitud == "caudal"]
    if qs:
        tot = sum((1 if p.cfg.get("rol", "entrada") == "entrada" else -1) * malla[p.id] for p in qs)
        malla["Q_sector"] = tot

    # --- noches
    noches = pd.DataFrame()
    if qs:
        m = malla[["t", "Q_sector"]].copy(); m["dia"] = m.t.dt.normalize(); mn = m.t.dt.hour * 60 + m.t.dt.minute
        w = m[mn < 240]
        noches = w.groupby("dia").Q_sector.agg(n="count", media="mean", minimo="min").reset_index()
        noches = noches[noches.n >= 12]
        if len(noches):
            med = noches.media.median(); noches["relacion"] = noches.media / med
            noches["aviso"] = np.where((noches.relacion < 0.5) | (noches.relacion > 1.5), "anómala", "")

    # --- pegado
    peg = []
    for v in cfg.get("verificar_pegado") or []:
        p = puntos[v["punto"]]
        t = auditar_pegado(p.crudo, base / v["excel"], v["hoja"], v["col_tiempo"], v["col_valor"], v["fila_inicio"],
                           v.get("factor", 1.0), paso_s)
        if not t.empty:
            t.insert(0, "punto", p.id); t.insert(1, "hoja", f"{v['hoja']}!{v['col_valor']}"); peg.append(t)
    pegado = pd.concat(peg, ignore_index=True) if peg else pd.DataFrame()

    # --- hallazgos
    for p in puntos.values():
        dg = p.diag
        if dg["desfase_malla_s"] > 5:
            H.append(("Info", p.id, f"Registra a los {dg['desfase_malla_s'] // 60:02d}:{dg['desfase_malla_s'] % 60:02d} de cada cuarto de hora; se interpola a la malla exacta."))
        if dg["mallas_distintas"] > 1:
            H.append(("Media", p.id, "El registro cambia de malla de tiempo a mitad del archivo (el equipo se reinició)."))
        for a, b, n in dg["huecos"]:
            H.append(("Media", p.id, f"Hueco en el registro entre {a:%d/%m %H:%M} y {b:%d/%m %H:%M}: faltan {n} registros."))
        if dg["duplicados"]:
            H.append(("Media", p.id, f"{dg['duplicados']} marcas de tiempo duplicadas."))
        if p.magnitud == "caudal":
            if dg["n_equipo"]: H.append(("Media", p.id, f"{dg['n_equipo']} registros marcados como fallo por el equipo."))
            if dg["n_neg"]: H.append(("Alta", p.id, f"{dg['n_neg']} caudales negativos fuera de rango (el peor, {dg['peor_neg']:.1f} L/s); se invalidan."))
            if dg["n_alto"]: H.append(("Alta", p.id, f"{dg['n_alto']} caudales por encima de {dg['q_max']:.0f} L/s; se invalidan."))
            for t, q in dg["llenados"]:
                H.append(("Info", p.id, f"Pico de {q:.1f} L/s el {t:%d/%m %H:%M}, al volver el caudal tras un tramo sin dato (llenado de tubería)."))
        else:
            off = dg["offset"]
            if dg["instalado"][0] is None:
                H.append(("Alta", p.id, "El sensor nunca supera el umbral de instalación; revisar el archivo."))
            elif off is None:
                H.append(("Info", p.id, "Sin lectura en seco antes ni después; el offset no se puede comprobar con el archivo."))
            elif abs(off["offset"]) >= 0.1 and not off.get("manual"):
                H.append(("Media", p.id, f"Lectura en seco de {-off['offset']:.3f} m ({off['n']} registros, entre {off['minimo']:.3f} y {off['maximo']:.3f}); se suma un offset de {off['offset']:+.3f} m."))
            if off and not off.get("manual") and off.get("antes") is not None and off.get("despues") is not None and abs(off["antes"] - off["despues"]) > 0.3:
                H.append(("Media", p.id, f"La lectura en seco cambia de {off['antes']:.2f} m antes a {off['despues']:.2f} m después: posible deriva del sensor."))
            if dg.get("n_neg_crudo"):
                H.append(("Info", p.id, f"{dg['n_neg_crudo']} lecturas negativas con el sensor instalado (cortes); tras el offset quedan {dg['n_neg']} y se llevan a 0."))
            if dg.get("n_interp"):
                H.append(("Info", p.id, f"{dg['n_interp']} valores de la malla interpolados por huecos del registro."))
        if dg["cobertura"] < 0.999:
            H.append(("Media" if dg["cobertura"] > 0.9 else "Alta", p.id, f"Cubre el {dg['cobertura']:.1%} de la ventana {ini:%d/%m %H:%M} – {fin:%d/%m %H:%M}."))
    if not eventos.empty:
        for evn, g in eventos[eventos.tipo == "general"].groupby("evento"):
            H.append(("Alta", "Sector", f"{evn}: sin servicio del {g.caida.min():%d/%m %H:%M} al {g.retorno.max():%d/%m %H:%M} ({', '.join(g.punto)})."))
        for _, e in eventos[eventos.tipo == "local"].iterrows():
            H.append(("Media", e.punto, f"Caída solo en este punto del {e.caida:%d/%m %H:%M} al {e.retorno:%d/%m %H:%M} ({e.duracion_h:.2f} h)."))
    for _, r in relojes.iterrows():
        if r.veredicto.startswith(("revisar", "posible")): H.append(("Alta", r.punto, "Reloj: " + r.veredicto + "."))
    for _, r in (pegado.iterrows() if not pegado.empty else []):
        if r.estado == "desfasado":
            H.append(("Alta", r.punto, f"Pegado en {r.hoja}, filas {r.fila_ini}–{r.fila_fin}: el Excel está {_hhmm(pd.Timedelta(minutes=r.desfase_min))} respecto del crudo ({r.n} valores)."))
        elif r.estado.startswith("sin"):
            H.append(("Media", r.punto, f"Pegado en {r.hoja}, filas {r.fila_ini}–{r.fila_fin}: {r.n} valores que no están en el crudo entregado."))
    for _, r in (noches.iterrows() if len(noches) else []):
        if r.aviso: H.append(("Media", "Sector", f"Noche del {r.dia:%d/%m}: caudal medio 00:00–04:00 de {r.media:.2f} L/s, {r.relacion:.0%} de la mediana de las noches."))
    import re
    H = [(g, p_, re.sub(r"(?<=\d)\.(?=\d)", ",", t)) for g, p_, t in H]      # decimales con coma en los textos
    orden = {"Alta": 0, "Media": 1, "Info": 2}
    H.sort(key=lambda h: orden[h[0]])
    cfg["_ventana"] = (ini, fin); cfg["_param"] = P; cfg["_base"] = str(base)
    return Resultado(cfg, puntos, malla, eventos, relojes, pegado, noches, H)
