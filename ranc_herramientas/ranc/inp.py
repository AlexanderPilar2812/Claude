"""Lectura y escritura de archivos INP de EPANET conservando el texto original.

Las secciones se guardan como listas de líneas tal cual vienen; las herramientas cambian solo
las líneas que necesitan, de modo que un INP exportado de Giswater conserva su estructura.
"""
from __future__ import annotations
import re
from collections import OrderedDict
from pathlib import Path


class Inp:
    def __init__(self, texto: str, codificacion="utf-8"):
        self.codificacion = codificacion
        self.pre: list[str] = []
        self.sec: "OrderedDict[str, list[str]]" = OrderedDict()
        act = None
        for lin in texto.splitlines():
            m = re.match(r"\s*\[([A-Za-z_]+)\]", lin)
            if m:
                act = m.group(1).upper(); self.sec.setdefault(act, []); continue
            (self.pre if act is None else self.sec[act]).append(lin)

    @classmethod
    def leer(cls, ruta):
        crudo = Path(ruta).read_bytes()
        for cod in ("utf-8-sig", "latin-1"):
            try: return cls(crudo.decode(cod), cod)
            except UnicodeDecodeError: continue

    def filas(self, nombre):
        """Filas de datos de una sección: [(índice de línea, [campos])], sin comentarios ni vacías."""
        out = []
        for i, lin in enumerate(self.sec.get(nombre.upper(), [])):
            dato = lin.split(";", 1)[0].strip()
            if dato: out.append((i, dato.split()))
        return out

    def opciones(self, nombre="OPTIONS"):
        d = {}
        for _, f in self.filas(nombre):
            d[" ".join(f[:-1]).upper()] = f[-1]
        return d

    def fijar_campo(self, nombre, i_linea, i_campo, nuevo):
        lin = self.sec[nombre.upper()][i_linea]
        cuerpo, sep, com = lin.partition(";")
        partes = re.split(r"(\s+)", cuerpo); k = -1
        for j, p in enumerate(partes):
            if p.strip():
                k += 1
                if k == i_campo: partes[j] = str(nuevo); break
        else:
            partes.append(" " + str(nuevo))
        self.sec[nombre.upper()][i_linea] = "".join(partes) + sep + com

    def fijar_opcion(self, clave, valor, nombre="OPTIONS"):
        for i, f in self.filas(nombre):
            if " ".join(f[:-1]).upper() == clave.upper():
                self.fijar_campo(nombre, i, len(f) - 1, valor); return
        self.sec.setdefault(nombre, []).append(f" {clave:<20}{valor}")

    def texto(self) -> str:
        out = list(self.pre)
        for n, lineas in self.sec.items():
            out.append(f"[{n}]"); out.extend(lineas)
        return "\n".join(out) + "\n"

    def guardar(self, ruta, ascii_=True):
        t = self.texto()
        if ascii_:
            import unicodedata
            t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
        Path(ruta).write_text(t, encoding="ascii" if ascii_ else "utf-8", newline="\r\n")


def horas(txt: str) -> float:
    """'168', '168:00', '0:15', '15 MIN' -> horas."""
    t = txt.strip().upper()
    if ":" in t:
        p = [float(x) for x in t.split(":")]
        return p[0] + p[1] / 60 + (p[2] / 3600 if len(p) > 2 else 0)
    return float(t)
