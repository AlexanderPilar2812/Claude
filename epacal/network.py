"""Lectura de la topología/atributos de la red y selección de familias (grupos).

Una *familia* es un conjunto de líneas o nodos que comparten un parámetro de
calibración (p. ej. todas las tuberías de PVC antiguas comparten rugosidad).
Se seleccionan combinando criterios (todos deben cumplirse = AND):

    tag:          etiqueta de la sección [TAGS] del INP (str o lista)
    catalog:      catálogo del comentario de Giswater (última palabra tras ';',
                  p. ej. PVC100, HG50, VCORTE_HIDRANTE) (str o lista)
    catalog_regex: expresión regular sobre ese catálogo (p. ej. "^(HG|HF|HD)")
    link_type:    PIPE, TCV, PRV, ... (str o lista)
    node_type:    JUNCTION, RESERVOIR, TANK
    ids:          lista explícita de IDs
    ids_file:     CSV con columnas id,group  (+ clave ``group``)
    id_regex:     expresión regular sobre el ID
    roughness:    [min, max]   (rugosidad original)
    setting:      [min, max]   (consigna inicial de válvulas)
    exclude_ids:  lista de IDs a excluir
    diameter:     [min, max]
    length:       [min, max]
    elevation:    [min, max]   (nodos)
    dma:          nombre de sector (requiere ``dmas`` en la configuración)
    all:          true  -> todos los elementos del tipo
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from . import engine as en


def parse_tags(inp_file: str) -> Dict[str, Dict[str, str]]:
    """Devuelve {'NODE': {id: tag}, 'LINK': {id: tag}} desde la sección [TAGS]."""
    tags: Dict[str, Dict[str, str]] = {"NODE": {}, "LINK": {}}
    section = None
    with open(inp_file, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.split(";", 1)[0].strip()
            if not line:
                continue
            if line.startswith("["):
                section = line.upper()
                continue
            if section == "[TAGS]":
                parts = line.split()
                if len(parts) >= 3 and parts[0].upper() in tags:
                    tags[parts[0].upper()][parts[1]] = parts[2]
    return tags


_NODE_SECTIONS = {"[JUNCTIONS]", "[RESERVOIRS]", "[TANKS]"}
_LINK_SECTIONS = {"[PIPES]", "[VALVES]", "[PUMPS]"}


def parse_catalog(inp_file: str) -> Dict[str, Dict[str, str]]:
    """Catálogo de cada elemento a partir del comentario de línea que exporta
    Giswater (``... ;sector prz dma dqa mins CATALOGO``): última palabra."""
    cat: Dict[str, Dict[str, str]] = {"NODE": {}, "LINK": {}}
    section = None
    with open(inp_file, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            st = raw.strip()
            if not st:
                continue
            if st.startswith("["):
                section = st.split()[0].upper()
                continue
            if st.startswith(";") or ";" not in st:
                continue
            data, comment = st.split(";", 1)
            parts, ctoks = data.split(), comment.split()
            if not parts or not ctoks:
                continue
            if section in _NODE_SECTIONS:
                cat["NODE"][parts[0]] = ctoks[-1]
            elif section in _LINK_SECTIONS:
                cat["LINK"][parts[0]] = ctoks[-1]
    return cat


def read_mapping_csv(path: str) -> Dict[str, str]:
    """CSV de dos columnas (id, valor). Admite separador ',' o ';' y cabecera."""
    out: Dict[str, str] = {}
    with open(path, "r", encoding="utf-8-sig") as fh:
        sample = fh.read(2048)
        fh.seek(0)
        delim = ";" if sample.count(";") > sample.count(",") else ","
        for i, row in enumerate(csv.reader(fh, delimiter=delim)):
            if len(row) < 2:
                continue
            if i == 0 and row[0].strip().lower() in ("id", "node", "link", "node_id",
                                                     "link_id", "elemento", "feature_id"):
                continue
            out[row[0].strip()] = row[1].strip()
    return out


@dataclass
class NetworkInfo:
    inp_file: str
    node_ids: List[str]
    link_ids: List[str]
    node_type: np.ndarray        # código EN_*
    link_type: np.ndarray
    node1: np.ndarray            # índice 0-based
    node2: np.ndarray
    length: np.ndarray
    diameter: np.ndarray
    roughness: np.ndarray
    init_setting: np.ndarray
    minor_loss: np.ndarray
    elevation: np.ndarray
    emitter: np.ndarray
    base_demand: np.ndarray      # suma de demandas base por nodo
    tags: Dict[str, Dict[str, str]]
    catalog: Dict[str, Dict[str, str]]
    flow_units: str
    headloss: int
    demand_model: str
    node_pos: Dict[str, int] = field(default_factory=dict)
    link_pos: Dict[str, int] = field(default_factory=dict)
    node_dma: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_project(cls, prj: en.EpanetProject) -> "NetworkInfo":
        nn, nl = prj.n_nodes, prj.n_links
        node_ids = [prj.node_id(i) for i in range(1, nn + 1)]
        link_ids = [prj.link_id(i) for i in range(1, nl + 1)]
        ntype = np.array([prj.node_type(i) for i in range(1, nn + 1)])
        ltype = np.array([prj.link_type(i) for i in range(1, nl + 1)])
        n1, n2 = np.zeros(nl, int), np.zeros(nl, int)
        for i in range(nl):
            a, b = prj.link_nodes(i + 1)
            n1[i], n2[i] = a - 1, b - 1
        bd = np.zeros(nn)
        for i in range(nn):
            if ntype[i] == en.EN_JUNCTION:
                for k in range(1, prj.num_demands(i + 1) + 1):
                    bd[i] += prj.base_demand(i + 1, k)
        info = cls(
            inp_file=prj.inp_file,
            node_ids=node_ids, link_ids=link_ids, node_type=ntype, link_type=ltype,
            node1=n1, node2=n2,
            length=prj.link_values(en.EN_LENGTH),
            diameter=prj.link_values(en.EN_DIAMETER),
            roughness=prj.link_values(en.EN_ROUGHNESS),
            init_setting=prj.link_values(en.EN_INITSETTING),
            minor_loss=prj.link_values(en.EN_MINORLOSS),
            elevation=prj.node_values(en.EN_ELEVATION),
            emitter=prj.node_values(en.EN_EMITTER),
            base_demand=bd,
            tags=parse_tags(prj.inp_file),
            catalog=parse_catalog(prj.inp_file),
            flow_units=prj.flow_units(),
            headloss=int(prj.get_option(en.EN_HEADLOSSFORM)),
            demand_model=prj.demand_model()[0],
        )
        info.node_pos = {k: i for i, k in enumerate(node_ids)}
        info.link_pos = {k: i for i, k in enumerate(link_ids)}
        return info

    # ----------------------------------------------------------------- DMAs
    def set_dmas(self, dmas_cfg: Optional[dict]) -> None:
        """Asigna cada nodo a un sector. ``dmas_cfg`` admite:
        {'file': 'nodos_dma.csv'}  (id_nodo, dma)
        {'by_tag': true}           (la etiqueta [TAGS] del nodo es el sector)
        {'NOMBRE': {'select': {...}}, ...}
        """
        self.node_dma = {}
        if not dmas_cfg:
            return
        if "file" in dmas_cfg:
            self.node_dma.update(read_mapping_csv(dmas_cfg["file"]))
        if dmas_cfg.get("by_tag"):
            self.node_dma.update(self.tags["NODE"])
        for name, spec in dmas_cfg.items():
            if name in ("file", "by_tag"):
                continue
            if isinstance(spec, dict) and "select" in spec:
                for i in self.select_nodes(spec["select"]):
                    self.node_dma[self.node_ids[i]] = name

    def dma_names(self) -> List[str]:
        return sorted(set(self.node_dma.values()))

    def dma_nodes(self, name: str) -> np.ndarray:
        """Índices 0-based de las *juntas* del sector."""
        if name.upper() == "ALL":
            return np.where(self.node_type == en.EN_JUNCTION)[0]
        return np.array([self.node_pos[n] for n, d in self.node_dma.items()
                         if d == name and n in self.node_pos
                         and self.node_type[self.node_pos[n]] == en.EN_JUNCTION], dtype=int)

    # ------------------------------------------------------------ selección
    @staticmethod
    def _as_list(v) -> List[str]:
        if v is None:
            return []
        if isinstance(v, (list, tuple, set)):
            return [str(x) for x in v]
        return [str(v)]

    def _ids_from_file(self, crit: dict) -> Optional[set]:
        if "ids_file" not in crit:
            return None
        mapping = read_mapping_csv(crit["ids_file"])
        grp = crit.get("group")
        return {k for k, v in mapping.items() if grp is None or v == str(grp)}

    def select_links(self, crit: dict) -> np.ndarray:
        n = len(self.link_ids)
        mask = np.ones(n, bool)
        if not crit:
            return np.arange(n)
        if "tag" in crit:
            wanted = set(self._as_list(crit["tag"]))
            mask &= np.array([self.tags["LINK"].get(l) in wanted for l in self.link_ids])
        if "catalog" in crit:
            wanted = set(self._as_list(crit["catalog"]))
            mask &= np.array([self.catalog["LINK"].get(l) in wanted for l in self.link_ids])
        if "catalog_regex" in crit:
            rx = re.compile(crit["catalog_regex"])
            mask &= np.array([bool(rx.search(self.catalog["LINK"].get(l, "")))
                              for l in self.link_ids])
        if "link_type" in crit:
            wanted = {t.upper() for t in self._as_list(crit["link_type"])}
            mask &= np.array([en.LINK_TYPES[t] in wanted or
                              (en.LINK_TYPES[t] == "CVPIPE" and "PIPE" in wanted)
                              for t in self.link_type])
        if "ids" in crit:
            wanted = set(self._as_list(crit["ids"]))
            mask &= np.array([l in wanted for l in self.link_ids])
        ids_f = self._ids_from_file(crit)
        if ids_f is not None:
            mask &= np.array([l in ids_f for l in self.link_ids])
        if "id_regex" in crit:
            rx = re.compile(crit["id_regex"])
            mask &= np.array([bool(rx.search(l)) for l in self.link_ids])
        if "exclude_ids" in crit:
            bad = set(self._as_list(crit["exclude_ids"]))
            mask &= np.array([l not in bad for l in self.link_ids])
        for key, arr in (("roughness", self.roughness), ("diameter", self.diameter),
                         ("length", self.length), ("setting", self.init_setting)):
            if key in crit:
                lo, hi = crit[key]
                mask &= (arr >= float(lo)) & (arr <= float(hi))
        if "dma" in crit:
            wanted = set(self._as_list(crit["dma"]))
            d1 = np.array([self.node_dma.get(self.node_ids[i]) in wanted for i in self.node1])
            d2 = np.array([self.node_dma.get(self.node_ids[i]) in wanted for i in self.node2])
            mask &= d1 | d2
        return np.where(mask)[0]

    def select_nodes(self, crit: dict) -> np.ndarray:
        n = len(self.node_ids)
        mask = np.ones(n, bool)
        if not crit:
            return np.arange(n)
        if "tag" in crit:
            wanted = set(self._as_list(crit["tag"]))
            mask &= np.array([self.tags["NODE"].get(x) in wanted for x in self.node_ids])
        if "catalog" in crit:
            wanted = set(self._as_list(crit["catalog"]))
            mask &= np.array([self.catalog["NODE"].get(x) in wanted for x in self.node_ids])
        if "catalog_regex" in crit:
            rx = re.compile(crit["catalog_regex"])
            mask &= np.array([bool(rx.search(self.catalog["NODE"].get(x, "")))
                              for x in self.node_ids])
        if "node_type" in crit:
            wanted = {t.upper() for t in self._as_list(crit["node_type"])}
            mask &= np.array([en.NODE_TYPES[t] in wanted for t in self.node_type])
        if "ids" in crit:
            wanted = set(self._as_list(crit["ids"]))
            mask &= np.array([x in wanted for x in self.node_ids])
        ids_f = self._ids_from_file(crit)
        if ids_f is not None:
            mask &= np.array([x in ids_f for x in self.node_ids])
        if "id_regex" in crit:
            rx = re.compile(crit["id_regex"])
            mask &= np.array([bool(rx.search(x)) for x in self.node_ids])
        if "elevation" in crit:
            lo, hi = crit["elevation"]
            mask &= (self.elevation >= float(lo)) & (self.elevation <= float(hi))
        if "dma" in crit:
            wanted = set(self._as_list(crit["dma"]))
            mask &= np.array([self.node_dma.get(x) in wanted for x in self.node_ids])
        return np.where(mask)[0]

    # ------------------------------------------------------------- pesos
    def node_pipe_length(self) -> np.ndarray:
        """Mitad de la longitud de las tuberías conectadas a cada nodo."""
        w = np.zeros(len(self.node_ids))
        pipes = (self.link_type == en.EN_PIPE) | (self.link_type == en.EN_CVPIPE)
        np.add.at(w, self.node1[pipes], 0.5 * self.length[pipes])
        np.add.at(w, self.node2[pipes], 0.5 * self.length[pipes])
        return w
