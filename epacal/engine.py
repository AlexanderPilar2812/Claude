"""Envoltorio ctypes mínimo y rápido sobre la API "por proyecto" de EPANET 2.2
(compatible también con 2.3).

La versión por defecto es **EPANET 2.2**. La biblioteca nativa se busca, por orden:
  1. argumento ``lib_path`` (``epanet_lib`` en la configuración)
  2. variable de entorno ``EPANET_LIB``
  3. según la versión pedida (``epanet_version``: "2.2" | "2.3" | "auto"):
       2.2 -> la copia que trae ``wntr``  (pip install wntr: epanet22.dll/.so/.dylib)
       2.3 -> la copia que trae ``epyt``  (pip install epyt)
  4. el nombre genérico del sistema (``epanet2``)

Diferencias que se resuelven aquí para que el resto del código sea igual con
ambas versiones:
  * 2.2 no tiene EN_getnodevalues/EN_getlinkvalues -> bucle por elemento.
  * 2.2 no tiene EN_EMITTERFLOW/EN_DEMANDFLOW -> el caudal de emisor se calcula
    como K·signo(p)·|p|^γ y el consumo como EN_DEMAND − emisor (en 2.2
    EN_DEMAND incluye el caudal de emisor).
  * Las fugas FAVAD por tubería (EN_LEAK_AREA) sólo existen en 2.3.

Cada ``EpanetProject`` mantiene el modelo abierto en memoria: las simulaciones
repetidas (miles en una calibración) sólo cambian parámetros y vuelven a
resolver, sin releer el INP.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import platform
import sys
from typing import Dict, Optional, Sequence

import numpy as np

# --- Constantes (epanet2_enums.h) ------------------------------------------
# Propiedades de nodo
EN_ELEVATION = 0
EN_BASEDEMAND = 1
EN_PATTERN = 2
EN_EMITTER = 3
EN_TANKLEVEL = 8      # al escribir: nivel inicial del tanque
EN_TANKDIAM = 17
EN_MINLEVEL = 20
EN_MAXLEVEL = 21
EN_DEMAND = 9
EN_HEAD = 10
EN_PRESSURE = 11
EN_EMITTERFLOW = 29
EN_LEAKAGEFLOW = 30
EN_DEMANDFLOW = 31
EN_FULLDEMAND = 32
# Propiedades de línea
EN_DIAMETER = 0
EN_LENGTH = 1
EN_ROUGHNESS = 2
EN_MINORLOSS = 3
EN_INITSTATUS = 4
EN_INITSETTING = 5
EN_FLOW = 8
EN_VELOCITY = 9
EN_HEADLOSS = 10
EN_STATUS = 11
EN_SETTING = 12
EN_LEAK_AREA = 26
EN_LEAK_EXPAN = 27
# Parámetros de tiempo
EN_DURATION = 0
EN_HYDSTEP = 1
EN_PATTERNSTEP = 3
EN_PATTERNSTART = 4
EN_REPORTSTEP = 5
EN_REPORTSTART = 6
EN_STARTTIME = 10
# Conteos
EN_NODECOUNT = 0
EN_TANKCOUNT = 1
EN_LINKCOUNT = 2
EN_PATCOUNT = 3
# Tipos de nodo
EN_JUNCTION = 0
EN_RESERVOIR = 1
EN_TANK = 2
# Tipos de línea
EN_CVPIPE = 0
EN_PIPE = 1
EN_PUMP = 2
EN_PRV = 3
EN_PSV = 4
EN_PBV = 5
EN_FCV = 6
EN_TCV = 7
EN_GPV = 8
# Opciones
EN_TRIALS = 0
EN_ACCURACY = 1
EN_EMITEXPON = 3
EN_DEMANDMULT = 4
EN_HEADLOSSFORM = 7
# Flags initH
EN_NOSAVE = 0
EN_INITFLOW = 10

NODE_TYPES = {EN_JUNCTION: "JUNCTION", EN_RESERVOIR: "RESERVOIR", EN_TANK: "TANK"}
LINK_TYPES = {
    EN_CVPIPE: "CVPIPE", EN_PIPE: "PIPE", EN_PUMP: "PUMP", EN_PRV: "PRV",
    EN_PSV: "PSV", EN_PBV: "PBV", EN_FCV: "FCV", EN_TCV: "TCV", EN_GPV: "GPV",
}
VALVE_TYPES = {EN_PRV, EN_PSV, EN_PBV, EN_FCV, EN_TCV, EN_GPV}
FLOW_UNITS = {0: "CFS", 1: "GPM", 2: "MGD", 3: "IMGD", 4: "AFD", 5: "LPS",
              6: "LPM", 7: "MLD", 8: "CMH", 9: "CMD", 10: "CMS"}
# Factor para pasar el caudal del modelo a m3/s
FLOW_TO_M3S = {"CFS": 0.0283168, "GPM": 6.30902e-5, "MGD": 0.0438126,
               "IMGD": 0.0526168, "AFD": 0.0142764, "LPS": 1e-3,
               "LPM": 1.0 / 60000.0, "MLD": 1000.0 / 86400.0,
               "CMH": 1.0 / 3600.0, "CMD": 1.0 / 86400.0, "CMS": 1.0}

_MAXID = 64  # EN_MAXID es 31, pero se deja holgura


class EpanetError(RuntimeError):
    pass


def _lib_bundled(version: str) -> Optional[str]:
    """Biblioteca empaquetada con la aplicación (ejecutable de PyInstaller o carpeta
    ``epacal/lib`` junto al código)."""
    names = {"2.2": ["epanet22.dll", "libepanet22.so", "libepanet22.dylib"],
             "2.3": ["epanet2.dll", "libepanet2.so", "libepanet2.dylib"]}[version]
    roots = []
    if getattr(sys, "frozen", False):
        roots.append(os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)),
                                  "epanet"))
    roots.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
    for r in roots:
        for n in names:
            cand = os.path.join(r, n)
            if os.path.exists(cand):
                return cand
    return None


def _lib_from_package(version: str) -> Optional[str]:
    system = platform.system()
    if version == "2.2":
        try:
            import wntr  # type: ignore
        except ImportError:
            return None
        base = os.path.join(os.path.dirname(wntr.__file__), "epanet", "libepanet")
        cand = {"Windows": os.path.join(base, "windows-x64", "epanet22.dll"),
                "Darwin": os.path.join(base, "darwin-x64", "libepanet22.dylib")}.get(
            system, os.path.join(base, "linux-x64", "libepanet22.so"))
        if system == "Darwin" and platform.machine() == "arm64":
            arm = os.path.join(base, "darwin-arm", "libepanet22.dylib")
            cand = arm if os.path.exists(arm) else cand
    else:
        try:
            import epyt  # type: ignore
        except ImportError:
            return None
        base = os.path.join(os.path.dirname(epyt.__file__), "libraries")
        cand = {"Windows": os.path.join(base, "win", "epanet2.dll"),
                "Darwin": os.path.join(base, "mac", "libepanet2.dylib")}.get(
            system, os.path.join(base, "glnx", "libepanet2.so"))
    return cand if os.path.exists(cand) else None


def _find_library(lib_path: Optional[str] = None, version: Optional[str] = None) -> str:
    if lib_path:
        return lib_path
    env = os.environ.get("EPANET_LIB")
    if env:
        return env
    version = str(version or os.environ.get("EPANET_VERSION", "2.2")).lower()
    order = {"2.2": ["2.2"], "2.3": ["2.3"], "auto": ["2.2", "2.3"]}.get(version)
    if order is None:
        raise EpanetError(f"epanet_version no válida: {version} (use 2.2, 2.3 o auto)")
    for v in order:
        found = _lib_bundled(v) or _lib_from_package(v)
        if found:
            return found
    found = ctypes.util.find_library("epanet22") or ctypes.util.find_library("epanet2")
    if found:
        return found
    raise EpanetError(
        f"No se encontró la biblioteca de EPANET {version}. Para EPANET 2.2 instale "
        "'wntr' (pip install wntr), o defina EPANET_LIB / 'epanet_lib' con la ruta a "
        "epanet2.dll (64 bits) / libepanet2.so")


_LIB_CACHE: Dict[str, ctypes.CDLL] = {}


def load_library(lib_path: Optional[str] = None, version: Optional[str] = None) -> ctypes.CDLL:
    path = _find_library(lib_path, version)
    if path not in _LIB_CACHE:
        if sys.platform.startswith("win"):
            lib = ctypes.WinDLL(path)  # convención stdcall en Windows
        else:
            lib = ctypes.CDLL(path)
        _LIB_CACHE[path] = lib
    return _LIB_CACHE[path]


class EpanetProject:
    """Proyecto EPANET abierto en memoria."""

    def __init__(self, inp_file: str, lib_path: Optional[str] = None,
                 rpt_file: str = "", out_file: str = "", version: Optional[str] = None):
        self.lib = load_library(lib_path, version)
        v = ctypes.c_int()
        self.lib.EN_getversion(ctypes.byref(v))
        self.version = v.value                       # 20200, 20300, ...
        self.has_bulk = hasattr(self.lib, "EN_getnodevalues")
        self.inp_file = inp_file
        self.ph = ctypes.c_void_p()
        self._chk(self.lib.EN_createproject(ctypes.byref(self.ph)))
        self._hyd_open = False
        err = self.lib.EN_open(self.ph, inp_file.encode(),
                               (rpt_file or os.devnull).encode(), out_file.encode())
        if err > 100:  # los avisos (<100) no impiden trabajar
            msg = self._errmsg(err)
            self.lib.EN_deleteproject(self.ph)
            raise EpanetError(f"Error {err} al abrir {inp_file}: {msg}")
        self.n_nodes = self.count(EN_NODECOUNT)
        self.n_links = self.count(EN_LINKCOUNT)
        self._nbuf = (ctypes.c_double * self.n_nodes)()
        self.is_junction = np.array([self.node_type(i) == EN_JUNCTION
                                     for i in range(1, self.n_nodes + 1)])
        self._lbuf = (ctypes.c_double * self.n_links)()

    # ------------------------------------------------------------------ util
    def _errmsg(self, code: int) -> str:
        buf = ctypes.create_string_buffer(256)
        self.lib.EN_geterror(code, buf, 255)
        return buf.value.decode(errors="replace")

    def _chk(self, err: int, ctx: str = "") -> None:
        if err > 100:
            raise EpanetError(f"EPANET error {err} {ctx}: {self._errmsg(err)}")

    def close(self) -> None:
        if self.ph:
            if self._hyd_open:
                self.lib.EN_closeH(self.ph)
                self._hyd_open = False
            self.lib.EN_close(self.ph)
            self.lib.EN_deleteproject(self.ph)
            self.ph = ctypes.c_void_p()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    # ------------------------------------------------------------ consultas
    def count(self, obj: int) -> int:
        v = ctypes.c_int()
        self._chk(self.lib.EN_getcount(self.ph, obj, ctypes.byref(v)))
        return v.value

    def node_index(self, node_id: str) -> int:
        v = ctypes.c_int()
        self._chk(self.lib.EN_getnodeindex(self.ph, str(node_id).encode(), ctypes.byref(v)),
                  f"(nodo '{node_id}')")
        return v.value

    def link_index(self, link_id: str) -> int:
        v = ctypes.c_int()
        self._chk(self.lib.EN_getlinkindex(self.ph, str(link_id).encode(), ctypes.byref(v)),
                  f"(línea '{link_id}')")
        return v.value

    def node_id(self, idx: int) -> str:
        buf = ctypes.create_string_buffer(_MAXID)
        self._chk(self.lib.EN_getnodeid(self.ph, idx, buf))
        return buf.value.decode()

    def link_id(self, idx: int) -> str:
        buf = ctypes.create_string_buffer(_MAXID)
        self._chk(self.lib.EN_getlinkid(self.ph, idx, buf))
        return buf.value.decode()

    def node_type(self, idx: int) -> int:
        v = ctypes.c_int()
        self._chk(self.lib.EN_getnodetype(self.ph, idx, ctypes.byref(v)))
        return v.value

    def link_type(self, idx: int) -> int:
        v = ctypes.c_int()
        self._chk(self.lib.EN_getlinktype(self.ph, idx, ctypes.byref(v)))
        return v.value

    def link_nodes(self, idx: int) -> tuple:
        a, b = ctypes.c_int(), ctypes.c_int()
        self._chk(self.lib.EN_getlinknodes(self.ph, idx, ctypes.byref(a), ctypes.byref(b)))
        return a.value, b.value

    def get_node(self, idx: int, prop: int) -> float:
        v = ctypes.c_double()
        self._chk(self.lib.EN_getnodevalue(self.ph, idx, prop, ctypes.byref(v)))
        return v.value

    def set_node(self, idx: int, prop: int, value: float) -> None:
        self._chk(self.lib.EN_setnodevalue(self.ph, idx, prop, ctypes.c_double(value)),
                  f"(set nodo {idx} prop {prop})")

    def get_link(self, idx: int, prop: int) -> float:
        v = ctypes.c_double()
        self._chk(self.lib.EN_getlinkvalue(self.ph, idx, prop, ctypes.byref(v)))
        return v.value

    def set_link(self, idx: int, prop: int, value: float) -> None:
        self._chk(self.lib.EN_setlinkvalue(self.ph, idx, prop, ctypes.c_double(value)),
                  f"(set línea {idx} prop {prop})")

    def node_values(self, prop: int) -> np.ndarray:
        """Valores de todos los nodos (orden de índice 1..N) en un vector."""
        if self.has_bulk:
            self._chk(self.lib.EN_getnodevalues(self.ph, prop, self._nbuf))
            return np.frombuffer(self._nbuf, dtype=np.float64).copy()
        v = ctypes.c_double()
        out = np.empty(self.n_nodes)
        get, ph, ref = self.lib.EN_getnodevalue, self.ph, ctypes.byref(v)
        for i in range(self.n_nodes):
            get(ph, i + 1, prop, ref)
            out[i] = v.value
        return out

    def link_values(self, prop: int) -> np.ndarray:
        if self.has_bulk:
            self._chk(self.lib.EN_getlinkvalues(self.ph, prop, self._lbuf))
            return np.frombuffer(self._lbuf, dtype=np.float64).copy()
        v = ctypes.c_double()
        out = np.empty(self.n_links)
        get, ph, ref = self.lib.EN_getlinkvalue, self.ph, ctypes.byref(v)
        for i in range(self.n_links):
            get(ph, i + 1, prop, ref)
            out[i] = v.value
        return out

    def node_values_at(self, prop: int, idx1: np.ndarray) -> np.ndarray:
        """Valores de un subconjunto de nodos (índices EPANET 1-based)."""
        if self.has_bulk:
            return self.node_values(prop)[np.asarray(idx1) - 1]
        v = ctypes.c_double()
        out = np.empty(len(idx1))
        get, ph, ref = self.lib.EN_getnodevalue, self.ph, ctypes.byref(v)
        for k, i in enumerate(idx1):
            get(ph, int(i), prop, ref)
            out[k] = v.value
        return out

    # ---- magnitudes de resultados independientes de la versión
    def emitter_flows(self, coef: Optional[np.ndarray] = None,
                      pressure: Optional[np.ndarray] = None) -> np.ndarray:
        """Caudal de emisor de cada nodo en el instante actual."""
        if self.version >= 20300:
            return self.node_values(EN_EMITTERFLOW)
        coef = self.node_values(EN_EMITTER) if coef is None else coef
        p = self.node_values(EN_PRESSURE) if pressure is None else pressure
        gamma = self.get_option(EN_EMITEXPON)
        return np.where((coef > 0) & self.is_junction,
                        coef * np.sign(p) * np.abs(p) ** gamma, 0.0)

    def leakage_flows(self) -> np.ndarray:
        """Fugas FAVAD de tuberías asignadas a nodos (0 en EPANET 2.2)."""
        if self.version >= 20300:
            return self.node_values(EN_LEAKAGEFLOW)
        return np.zeros(self.n_nodes)

    def consumer_demands(self, emitter: Optional[np.ndarray] = None) -> np.ndarray:
        """Consumo de abonados servido (sin emisores ni fugas)."""
        if self.version >= 20300:
            return self.node_values(EN_DEMANDFLOW)
        emitter = self.emitter_flows() if emitter is None else emitter
        # en 2.2 EN_DEMAND de embalses/depósitos es su caudal aportado (negativo)
        return np.where(self.is_junction, self.node_values(EN_DEMAND) - emitter, 0.0)

    def get_time(self, param: int) -> int:
        v = ctypes.c_long()
        self._chk(self.lib.EN_gettimeparam(self.ph, param, ctypes.byref(v)))
        return int(v.value)

    def set_time(self, param: int, value: int) -> None:
        self._chk(self.lib.EN_settimeparam(self.ph, param, ctypes.c_long(int(value))))

    def get_option(self, opt: int) -> float:
        v = ctypes.c_double()
        self._chk(self.lib.EN_getoption(self.ph, opt, ctypes.byref(v)))
        return v.value

    def set_option(self, opt: int, value: float) -> None:
        self._chk(self.lib.EN_setoption(self.ph, opt, ctypes.c_double(value)))

    def flow_units(self) -> str:
        v = ctypes.c_int()
        self._chk(self.lib.EN_getflowunits(self.ph, ctypes.byref(v)))
        return FLOW_UNITS.get(v.value, str(v.value))

    def demand_model(self) -> tuple:
        t, a, b, c = ctypes.c_int(), ctypes.c_double(), ctypes.c_double(), ctypes.c_double()
        self._chk(self.lib.EN_getdemandmodel(self.ph, ctypes.byref(t), ctypes.byref(a),
                                             ctypes.byref(b), ctypes.byref(c)))
        return ("PDA" if t.value == 1 else "DDA"), a.value, b.value, c.value

    # ---------------------------------------------------------- demandas
    def num_demands(self, node: int) -> int:
        v = ctypes.c_int()
        self._chk(self.lib.EN_getnumdemands(self.ph, node, ctypes.byref(v)))
        return v.value

    def base_demand(self, node: int, k: int) -> float:
        v = ctypes.c_double()
        self._chk(self.lib.EN_getbasedemand(self.ph, node, k, ctypes.byref(v)))
        return v.value

    def set_base_demand(self, node: int, k: int, value: float) -> None:
        self._chk(self.lib.EN_setbasedemand(self.ph, node, k, ctypes.c_double(value)))

    def demand_pattern(self, node: int, k: int) -> int:
        v = ctypes.c_int()
        self._chk(self.lib.EN_getdemandpattern(self.ph, node, k, ctypes.byref(v)))
        return v.value

    def set_demand_pattern(self, node: int, k: int, pat: int) -> None:
        self._chk(self.lib.EN_setdemandpattern(self.ph, node, k, pat))

    # ---------------------------------------------------------- patrones
    def pattern_index(self, pid: str) -> int:
        v = ctypes.c_int()
        err = self.lib.EN_getpatternindex(self.ph, str(pid).encode(), ctypes.byref(v))
        return v.value if err == 0 else 0

    def pattern_id(self, idx: int) -> str:
        buf = ctypes.create_string_buffer(_MAXID)
        self._chk(self.lib.EN_getpatternid(self.ph, idx, buf))
        return buf.value.decode()

    def get_pattern(self, idx: int) -> np.ndarray:
        n = ctypes.c_int()
        self._chk(self.lib.EN_getpatternlen(self.ph, idx, ctypes.byref(n)))
        out = np.empty(n.value)
        v = ctypes.c_double()
        for p in range(n.value):
            self._chk(self.lib.EN_getpatternvalue(self.ph, idx, p + 1, ctypes.byref(v)))
            out[p] = v.value
        return out

    def set_pattern(self, idx: int, values: Sequence[float]) -> None:
        arr = (ctypes.c_double * len(values))(*[float(x) for x in values])
        self._chk(self.lib.EN_setpattern(self.ph, idx, arr, len(values)))

    def add_pattern(self, pid: str, values: Sequence[float]) -> int:
        idx = self.pattern_index(pid)
        if idx == 0:
            self._chk(self.lib.EN_addpattern(self.ph, str(pid).encode()), f"(patrón {pid})")
            idx = self.pattern_index(pid)
        self.set_pattern(idx, values)
        return idx

    # ------------------------------------------------------- hidráulica
    def open_hydraulics(self) -> None:
        if not self._hyd_open:
            self._chk(self.lib.EN_openH(self.ph))
            self._hyd_open = True

    def run_eps(self, step: int, callback) -> bool:
        """Simulación en periodo extendido.

        ``callback(t)`` se llama en cada instante múltiplo de ``step`` (s).
        Devuelve False si algún paso no converge (código de aviso 1 o error).
        """
        self.open_hydraulics()
        lib, ph = self.lib, self.ph
        t = ctypes.c_long()
        tstep = ctypes.c_long(1)
        ok = True
        self._chk(lib.EN_initH(ph, EN_NOSAVE))
        while True:
            err = lib.EN_runH(ph, ctypes.byref(t))
            if err > 100:
                raise EpanetError(f"runH error {err}: {self._errmsg(err)}")
            if err == 1:  # sistema desequilibrado
                ok = False
            if t.value % step == 0:
                callback(int(t.value))
            err = lib.EN_nextH(ph, ctypes.byref(tstep))
            if err > 100:
                raise EpanetError(f"nextH error {err}: {self._errmsg(err)}")
            if tstep.value <= 0:
                break
        return ok

    def save_inp(self, path: str) -> None:
        self._chk(self.lib.EN_saveinpfile(self.ph, path.encode()), f"(guardar {path})")


def library_version(lib_path: Optional[str] = None, version: Optional[str] = None) -> int:
    lib = load_library(lib_path, version)
    v = ctypes.c_int()
    lib.EN_getversion(ctypes.byref(v))
    return v.value
