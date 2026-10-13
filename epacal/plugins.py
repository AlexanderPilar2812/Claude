"""Extensiones por código (plugins).

Cualquier fichero ``*.py`` dentro de una carpeta de plugins se carga al iniciar
(la aplicación busca en ``Documentos/EPACAL/plugins`` y en ``plugins/`` junto al
programa; en la configuración se pueden añadir más con ``plugin_dirs:``).

Un plugin importa este módulo y registra lo que necesite:

    from epacal.plugins import (register_objective, register_parameter_type,
                                register_result_hook)

    # 1) Objetivo nuevo: se usa en  hydraulic.objectives: [PRESION_NOCHE, ...]
    @register_objective("PRESION_NOCHE")
    def presion_noche(ev, sim, mask):
        '''RMSE de presión sólo entre las 0 y las 6 h.'''
        import numpy as np
        horas = (ev.obs.times / 3600) % 24
        m = mask & (horas < 6)
        filas = ev.obs.rows_of("PRESSURE")
        e = sim[filas][:, m] - ev.obs.values[filas][:, m]
        return float(np.sqrt(np.nanmean(e ** 2)))

    # 2) Tipo de parámetro nuevo sobre una propiedad de EPANET
    register_parameter_type("diameter", element="link", prop=0,   # EN_DIAMETER
                            default_mode="multiplier", link_types=["PIPE"])

    # 3) Acción al terminar (exportar a Excel, enviar a una base de datos, ...)
    @register_result_hook
    def exportar(resultado, cfg, out_dir):
        ...

Los plugins se cargan también en los procesos de simulación en paralelo, por lo
que los objetivos personalizados funcionan igual que los integrados.
"""
from __future__ import annotations

import glob
import importlib.util
import os
import sys
import traceback
from typing import Callable, Dict, List, Optional

OBJECTIVES: Dict[str, Callable] = {}
RESULT_HOOKS: List[Callable] = []
PARAMETER_TYPES: Dict[str, dict] = {}
_LOADED: Dict[str, str] = {}          # ruta -> estado ("ok" | mensaje de error)


def register_objective(name: str):
    """Decorador: ``fn(evaluador, sim, mascara) -> float`` (menor = mejor)."""
    def deco(fn):
        OBJECTIVES[name.upper()] = fn
        return fn
    return deco


def register_result_hook(fn: Callable):
    """``fn(resultado: dict, cfg: dict, out_dir: str)`` al terminar la calibración."""
    RESULT_HOOKS.append(fn)
    return fn


def register_parameter_type(name: str, element: str, prop: int,
                            default_mode: str = "absolute",
                            link_types: Optional[List[str]] = None,
                            node_types: Optional[List[str]] = None):
    """Nuevo tipo de parámetro que escribe la propiedad ``prop`` de EPANET
    (EN_*) en líneas (``element="link"``) o nodos (``element="node"``)."""
    from . import parameters as P
    PARAMETER_TYPES[name] = dict(element=element, prop=prop, default_mode=default_mode,
                                 link_types=link_types, node_types=node_types)
    if element == "link":
        P.LINK_PARAMS[name] = prop
    else:
        P.NODE_PARAMS[name] = prop
    P.DEFAULT_MODE[name] = default_mode


def default_dirs() -> List[str]:
    dirs = []
    docs = os.path.join(os.path.expanduser("~"), "Documents", "EPACAL", "plugins")
    dirs.append(docs)
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else \
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dirs.append(os.path.join(base, "plugins"))
    return dirs


def load_plugins(extra_dirs: Optional[List[str]] = None, log: Callable[[str], None] = None) -> Dict[str, str]:
    """Carga (una sola vez por proceso) los plugins de las carpetas indicadas."""
    dirs = list(default_dirs()) + list(extra_dirs or [])
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for path in sorted(glob.glob(os.path.join(d, "*.py"))):
            path = os.path.abspath(path)
            if path in _LOADED or os.path.basename(path).startswith("_"):
                continue
            name = "epacal_plugin_" + os.path.splitext(os.path.basename(path))[0]
            try:
                spec = importlib.util.spec_from_file_location(name, path)
                mod = importlib.util.module_from_spec(spec)
                sys.modules[name] = mod
                spec.loader.exec_module(mod)
                _LOADED[path] = "ok"
                if log:
                    log(f"  plugin cargado: {path}")
            except Exception:
                _LOADED[path] = traceback.format_exc(limit=3)
                if log:
                    log(f"  ERROR al cargar el plugin {path}:\n{_LOADED[path]}")
    return dict(_LOADED)


def loaded() -> Dict[str, str]:
    return dict(_LOADED)
