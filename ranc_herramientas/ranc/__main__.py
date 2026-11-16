"""Herramientas RANC.

  python -m ranc campana    sector.yaml            control de calidad de la campaña
  python -m ranc linea_base sector.yaml            línea base de pérdidas
  python -m ranc patrones   sector.yaml            patrones, .dat y CSV para Giswater
  python -m ranc todo       sector.yaml            las tres anteriores
  python -m ranc auditar    modelo.inp [--n1 1.5] [--corregir]
  python -m ranc evaluar    modelo.inp [--presion p.dat] [--caudal q.dat] [--nivel n.dat]
  python -m ranc perdidas   modelo.inp (--pct 0.19 | --m3 790) [--n1 1.5] [--expl 901]
  python -m ranc escenarios modelo.inp escenarios.yaml
"""
import argparse, sys
from pathlib import Path


def _altos(lista, i_grav=0, i_txt=-1):
    for h in lista:
        if h[i_grav] == "Alta": print("  [Alta]", h[i_txt])


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m ranc", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("orden", choices=["campana", "linea_base", "patrones", "todo", "auditar", "evaluar", "perdidas", "escenarios"])
    ap.add_argument("archivo"); ap.add_argument("extra", nargs="?")
    ap.add_argument("--salida"); ap.add_argument("--n1", type=float); ap.add_argument("--corregir", action="store_true")
    ap.add_argument("--presion"); ap.add_argument("--caudal"); ap.add_argument("--nivel")
    ap.add_argument("--pct", type=float); ap.add_argument("--m3", type=float); ap.add_argument("--expl", default="")
    a = ap.parse_args(argv); f = Path(a.archivo)
    if a.orden in ("campana", "linea_base", "patrones", "todo"):
        from .campana import procesar
        res = procesar(f); sal = Path(a.salida) if a.salida else f.parent / "salidas"
        if a.orden in ("campana", "todo"):
            from .salida_campana import exportar
            s = exportar(res, sal); n = {g: sum(1 for h in res.hallazgos if h[0] == g) for g in ("Alta", "Media", "Info")}
            print(f"Campaña: {n['Alta']} hallazgos altos, {n['Media']} medios, {n['Info']} informativos"); _altos(res.hallazgos); print("  ->", s["excel"])
        if a.orden in ("linea_base", "todo"):
            from . import linea_base as LB
            lb = LB.calcular(res); r = LB.exportar(lb, res, sal); i = lb.ind
            print(f"Línea base: MNF {i['mnf_lh']:.0f} L/h, pérdidas nocturnas {i['lnight_ls']:.3f} L/s, recuperables {i['recuperables_ls']:.3f} L/s"); _altos(lb.alertas); print("  ->", r)
        if a.orden in ("patrones", "todo"):
            from . import patrones as PT
            pt = PT.generar(res); s = PT.exportar(pt, sal); print(f"Patrones: {len(pt.demanda)} valores, media {pt.demanda.valor_cmh.mean():.3f} m³/h"); _altos(pt.avisos); print("  ->", s["excel"].parent)
    elif a.orden == "auditar":
        from . import auditor_inp as AU
        au = AU.auditar(f, n1=a.n1, corregir=a.corregir); s = AU.exportar(au, f, a.salida)
        n = au.hallazgos.Gravedad.value_counts(); print(f"Auditoría: {n.get('Alta', 0)} altos, {n.get('Media', 0)} medios, {n.get('Info', 0)} informativos")
        for r in au.hallazgos[au.hallazgos.Gravedad == "Alta"].itertuples(): print("  [Alta]", r.Hallazgo)
        for k, v in s.items(): print("  ->", v)
    elif a.orden == "evaluar":
        from . import calibracion as CA
        ev = CA.evaluar(f, {"presion": a.presion, "caudal": a.caudal, "nivel": a.nivel}); s = CA.exportar(ev, f.stem, a.salida or f.parent)
        print(ev.resumen.to_string(index=False)); _altos(ev.avisos); print("  ->", s["excel"])
    elif a.orden == "perdidas":
        from . import perdidas as PE
        p = PE.calibrar(f, objetivo_m3=a.m3, pct=a.pct, n1=a.n1 or 1.5); s = PE.exportar(p, f, a.salida, a.expl); r = p.resumen
        print(f"Pérdidas: {r['fuga_m3']:.1f} m³ ({r['pct_fuga']:.1%} de la entrada) en {r['iteraciones']} iteraciones, {r['nodos_con_emisor']} emisores"); _altos(p.avisos)
        for k, v in s.items(): print("  ->", v)
    elif a.orden == "escenarios":
        import yaml
        from . import escenarios as ES
        cfg = yaml.safe_load(Path(a.extra).read_text(encoding="utf-8")) if a.extra else {}
        es = ES.correr(f, cfg); r = ES.exportar(es, f.stem, a.salida or f.parent); print(f"Escenarios: {len(es.resumen)} casos"); _altos(es.avisos); print("  ->", r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
