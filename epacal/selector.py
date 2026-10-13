"""Selector de elementos con mapa de la red (crear o editar un grupo de calibración).

Se combinan (Y lógico):
  * tipo de elemento (tuberías, válvulas, nudos, tanques/embalses)
  * catálogo / material (lista con casillas)
  * rangos de diámetro y rugosidad
  * lista de IDs pegada o cargada de un CSV
  * selección en el mapa (rectángulo o lazo; modos reemplazar / añadir / quitar)
"""
from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional, Set

import numpy as np
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

from . import engine as en
from .network import NetworkInfo, parse_coordinates, read_mapping_csv

KINDS = {"Tuberías": "pipes", "Válvulas": "valves", "Nudos": "junctions", "Tanques/embalses": "sources"}
TYPES_FOR = {"pipes": ["roughness", "minor_loss", "leak_area"],
             "valves": ["valve_setting", "minor_loss"],
             "junctions": ["demand_multiplier", "emitter_multiplier", "elevation_offset"],
             "sources": ["reservoir_head", "tank_level", "tank_diameter", "elevation_offset"]}


class ElementSelector(ctk.CTkToplevel):
    def __init__(self, master, net: NetworkInfo, inp: str, theme: dict, font: str,
                 on_done: Callable[[dict], None], param_labels: Dict[str, str],
                 initial: Optional[dict] = None):
        super().__init__(master, fg_color=theme["bg"])
        self.title("Seleccionar elementos")
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{min(1400, sw - 60)}x{min(900, sh - 90)}+20+20")
        self.minsize(1000, 620)
        self.transient(master)
        self.net, self.T, self.F = net, theme, font
        self.on_done = on_done
        self.labels = param_labels
        self.coords, self.verts = parse_coordinates(inp)
        self.map_sel: Optional[Set[str]] = None
        self.initial = initial or {}
        self._build()
        kind0 = self.initial.get("kind", "pipes")
        self.v_kind.set({v: k for k, v in KINDS.items()}[kind0])
        self.on_kind()
        if self.initial.get("ids"):
            self.map_sel = set(self.initial["ids"])
            self.v_mode.set("Añadir")
        self.refresh()
        self.after(100, self.grab_set)

    # ------------------------------------------------------------------ UI
    def _card(self, master, title):
        f = ctk.CTkFrame(master, fg_color=self.T["card"], corner_radius=14, border_width=1,
                         border_color=self.T["border"])
        ctk.CTkLabel(f, text=title, font=(self.F, 14, "bold"), text_color=self.T["ink"],
                     anchor="w").pack(fill="x", padx=14, pady=(12, 6))
        return f

    def _build(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        left = ctk.CTkScrollableFrame(self, fg_color="transparent", width=330)
        left.grid(row=0, column=0, sticky="nsw", padx=(16, 8), pady=16)

        c = self._card(left, "1 · Tipo de elemento")
        c.pack(fill="x", pady=(0, 10))
        self.v_kind = tk.StringVar(value="Tuberías")
        ctk.CTkSegmentedButton(c, values=list(KINDS), variable=self.v_kind,
                               command=lambda _: self.on_kind(), selected_color=self.T["accent"],
                               selected_hover_color=self.T["accent_hover"],
                               unselected_color=("#6B7280", "#3F4248"), fg_color=("#6B7280", "#3F4248"),
                               text_color="#FFFFFF", font=(self.F, 11)).pack(fill="x", padx=14, pady=(0, 14))

        c = self._card(left, "2 · Catálogo / material")
        c.pack(fill="x", pady=(0, 10))
        self.cat_box = ctk.CTkScrollableFrame(c, fg_color="transparent", height=170)
        self.cat_box.pack(fill="x", padx=8)
        r = ctk.CTkFrame(c, fg_color="transparent")
        r.pack(fill="x", padx=14, pady=(4, 12))
        for txt, val in (("Todos", True), ("Ninguno", False)):
            ctk.CTkButton(r, text=txt, width=80, height=26, fg_color="transparent", border_width=1,
                          border_color=self.T["border"], text_color=self.T["ink"],
                          hover_color=self.T["accent_soft"],
                          command=lambda v=val: self._all_cats(v)).pack(side="left", padx=(0, 6))
        self.cat_vars: Dict[str, tk.BooleanVar] = {}

        c = self._card(left, "3 · Atributos")
        c.pack(fill="x", pady=(0, 10))
        g = ctk.CTkFrame(c, fg_color="transparent")
        g.pack(fill="x", padx=14, pady=(0, 12))
        self.rng = {}
        for i, (key, lab) in enumerate((("diameter", "Diámetro (mm)"), ("roughness", "Rugosidad"))):
            ctk.CTkLabel(g, text=lab, font=(self.F, 11), text_color=self.T["muted"]).grid(row=i, column=0, sticky="w", pady=3)
            a, b = tk.StringVar(), tk.StringVar()
            for j, v in enumerate((a, b)):
                e = ctk.CTkEntry(g, textvariable=v, width=80, height=28, corner_radius=8,
                                 placeholder_text="mín" if j == 0 else "máx")
                e.grid(row=i, column=1 + j, padx=4)
                e.bind("<KeyRelease>", lambda _: self.refresh())
            self.rng[key] = (a, b)

        c = self._card(left, "4 · Lista de IDs (opcional)")
        c.pack(fill="x", pady=(0, 10))
        self.txt_ids = ctk.CTkTextbox(c, height=80, corner_radius=8, fg_color=self.T["bg"])
        self.txt_ids.pack(fill="x", padx=14)
        self.txt_ids.bind("<KeyRelease>", lambda _: self.refresh())
        ctk.CTkButton(c, text="Cargar CSV de IDs…", height=28, fg_color="transparent", border_width=1,
                      border_color=self.T["border"], text_color=self.T["ink"],
                      hover_color=self.T["accent_soft"], command=self.load_ids).pack(anchor="w", padx=14, pady=(6, 12))

        # mapa
        right = ctk.CTkFrame(self, fg_color=self.T["card"], corner_radius=14, border_width=1,
                             border_color=self.T["border"])
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 16), pady=16)
        top = ctk.CTkFrame(right, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(12, 0))
        ctk.CTkLabel(top, text="5 · Selección en el mapa", font=(self.F, 14, "bold"),
                     text_color=self.T["ink"]).pack(side="left")
        self.v_tool = tk.StringVar(value="Rectángulo")
        ctk.CTkSegmentedButton(top, values=["Navegar", "Rectángulo", "Lazo"], variable=self.v_tool,
                               command=lambda _: self._set_tool(), selected_color=self.T["accent"],
                               selected_hover_color=self.T["accent_hover"],
                               unselected_color=("#6B7280", "#3F4248"), fg_color=("#6B7280", "#3F4248"),
                               text_color="#FFFFFF", font=(self.F, 11)).pack(side="left", padx=14)
        self.v_mode = tk.StringVar(value="Reemplazar")
        ctk.CTkSegmentedButton(top, values=["Reemplazar", "Añadir", "Quitar"], variable=self.v_mode,
                               selected_color=self.T["accent"], selected_hover_color=self.T["accent_hover"],
                               unselected_color=("#6B7280", "#3F4248"), fg_color=("#6B7280", "#3F4248"),
                               text_color="#FFFFFF", font=(self.F, 11)).pack(side="left")
        ctk.CTkButton(top, text="Limpiar mapa", width=110, height=28, fg_color="transparent", border_width=1,
                      border_color=self.T["border"], text_color=self.T["ink"],
                      hover_color=self.T["accent_soft"], command=self.clear_map).pack(side="right")
        self.map_host = tk.Frame(right, bg="#FFFFFF")
        self.map_host.pack(fill="both", expand=True, padx=14, pady=10)
        self._map()

        # parámetro (columna izquierda)
        c = self._card(left, "6 · Parámetro del grupo")
        c.pack(fill="x", pady=(0, 10))
        g = ctk.CTkFrame(c, fg_color="transparent")
        g.pack(fill="x", padx=14, pady=(0, 12))
        self.v_name = tk.StringVar(value=self.initial.get("name", "GRUPO_1"))
        self.v_type = tk.StringVar()
        self.v_pmode = tk.StringVar(value=self.initial.get("mode", "absolute"))
        b0 = self.initial.get("bounds", ["", ""])
        self.v_lo, self.v_hi = tk.StringVar(value=str(b0[0])), tk.StringVar(value=str(b0[1]))
        self.v_log = tk.BooleanVar(value=bool(self.initial.get("log", False)))
        self.v_excl = tk.BooleanVar(value=True)
        ctk.CTkLabel(g, text="Nombre", font=(self.F, 11), text_color=self.T["muted"]).grid(row=0, column=0, sticky="w")
        ctk.CTkEntry(g, textvariable=self.v_name, height=30).grid(row=0, column=1, columnspan=3, sticky="we", pady=3)
        ctk.CTkLabel(g, text="Parámetro", font=(self.F, 11), text_color=self.T["muted"]).grid(row=1, column=0, sticky="w")
        self.om_type = ctk.CTkOptionMenu(g, variable=self.v_type, values=["—"], height=30,
                                         fg_color=self.T["accent"], button_color=self.T["accent_hover"],
                                         command=lambda _: self._defaults())
        self.om_type.grid(row=1, column=1, columnspan=3, sticky="we", pady=3)
        ctk.CTkLabel(g, text="Modo", font=(self.F, 11), text_color=self.T["muted"]).grid(row=2, column=0, sticky="w")
        ctk.CTkOptionMenu(g, variable=self.v_pmode, values=["absolute", "multiplier", "offset"], height=30,
                          fg_color=("#6B7280", "#3F4248"), button_color=("#4B5563", "#55585F")).grid(
            row=2, column=1, columnspan=3, sticky="we", pady=3)
        ctk.CTkLabel(g, text="Límites", font=(self.F, 11), text_color=self.T["muted"]).grid(row=3, column=0, sticky="w")
        ctk.CTkEntry(g, textvariable=self.v_lo, width=80, height=30, placeholder_text="mín").grid(row=3, column=1, pady=3)
        ctk.CTkEntry(g, textvariable=self.v_hi, width=80, height=30, placeholder_text="máx").grid(row=3, column=2, padx=4)
        ctk.CTkCheckBox(g, text="log", variable=self.v_log, width=40, fg_color=self.T["accent"]).grid(row=3, column=3)
        ctk.CTkCheckBox(g, text="Quitar estos elementos de otros grupos\ndel mismo parámetro",
                        variable=self.v_excl, fg_color=self.T["accent"], font=(self.F, 11)).grid(
            row=4, column=0, columnspan=4, sticky="w", pady=(8, 0))
        g.grid_columnconfigure(1, weight=1)

        bot = ctk.CTkFrame(self, fg_color=self.T["card"], corner_radius=14, border_width=1,
                           border_color=self.T["border"])
        bot.grid(row=1, column=0, columnspan=2, sticky="we", padx=16, pady=(0, 16))
        self.v_count = tk.StringVar(value="0 elementos")
        ctk.CTkLabel(bot, textvariable=self.v_count, font=(self.F, 18, "bold"),
                     text_color=self.T["accent"]).pack(side="left", padx=18, pady=12)
        ctk.CTkLabel(bot, text="Se combinan todos los filtros: tipo · catálogo · atributos · IDs · mapa",
                     font=(self.F, 11), text_color=self.T["muted"]).pack(side="left", padx=8)
        ctk.CTkButton(bot, text="Crear / actualizar grupo", height=38, fg_color=self.T["accent"],
                      hover_color=self.T["accent_hover"], font=(self.F, 13, "bold"),
                      command=self.done).pack(side="right", padx=14)
        ctk.CTkButton(bot, text="Cancelar", height=38, width=90, fg_color="transparent", border_width=1,
                      border_color=self.T["border"], text_color=self.T["ink"],
                      hover_color=self.T["accent_soft"], command=self.destroy).pack(side="right")

    # ---------------------------------------------------------------- datos
    def kind(self) -> str:
        return KINDS[self.v_kind.get()]

    def elements(self) -> List[int]:
        n = self.net
        k = self.kind()
        if k == "pipes":
            return list(np.where(np.isin(n.link_type, [en.EN_PIPE, en.EN_CVPIPE]))[0])
        if k == "valves":
            return list(np.where(np.isin(n.link_type, list(en.VALVE_TYPES)))[0])
        if k == "junctions":
            return list(np.where(n.node_type == en.EN_JUNCTION)[0])
        return list(np.where(n.node_type != en.EN_JUNCTION)[0])

    def is_link(self) -> bool:
        return self.kind() in ("pipes", "valves")

    def ident(self, i) -> str:
        return self.net.link_ids[i] if self.is_link() else self.net.node_ids[i]

    def catalog(self, i) -> str:
        n = self.net
        if self.is_link():
            lid = n.link_ids[i]
            return n.catalog["LINK"].get(lid) or n.tags["LINK"].get(lid) or en.LINK_TYPES[n.link_type[i]]
        nid = n.node_ids[i]
        return n.catalog["NODE"].get(nid) or n.tags["NODE"].get(nid) or en.NODE_TYPES[n.node_type[i]]

    def on_kind(self):
        for w in self.cat_box.winfo_children():
            w.destroy()
        from collections import Counter
        cnt = Counter(self.catalog(i) for i in self.elements())
        pre = set(self.initial.get("catalogs", [])) if self.initial.get("kind") == self.kind() else None
        self.cat_vars = {}
        for cat, nn in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0])):
            v = tk.BooleanVar(value=True if pre is None or not pre else cat in pre)
            self.cat_vars[cat] = v
            ctk.CTkCheckBox(self.cat_box, text=f"{cat}  ({nn})", variable=v, fg_color=self.T["accent"],
                            hover_color=self.T["accent_hover"], font=(self.F, 11), height=22,
                            command=self.refresh).pack(anchor="w", pady=1)
        types = TYPES_FOR[self.kind()]
        self.om_type.configure(values=[self.labels.get(t, t) for t in types])
        if self.initial.get("type") in types:
            self.v_type.set(self.labels.get(self.initial["type"], self.initial["type"]))
        else:
            self.v_type.set(self.labels.get(types[0], types[0]))
            self._defaults()
        self.map_sel = None
        self.draw_map()
        self.refresh()

    def _defaults(self):
        inv = {v: k for k, v in self.labels.items()}
        t = inv.get(self.v_type.get(), self.v_type.get())
        d = {"roughness": ("absolute", 0.0005, 0.1, True), "minor_loss": ("absolute", 0, 20, False),
             "leak_area": ("absolute", 0, 50, False), "valve_setting": ("multiplier", 0.05, 5, True),
             "demand_multiplier": ("multiplier", 0.8, 1.2, False),
             "emitter_multiplier": ("multiplier", 0.5, 2, False), "elevation_offset": ("offset", -2, 2, False),
             "reservoir_head": ("offset", -3, 3, False), "tank_level": ("absolute", 0.5, 5, False),
             "tank_diameter": ("multiplier", 0.7, 1.3, False)}.get(t)
        if d and not self.initial.get("bounds"):
            self.v_pmode.set(d[0])
            self.v_lo.set(str(d[1]))
            self.v_hi.set(str(d[2]))
            self.v_log.set(d[3])

    def _all_cats(self, val):
        for v in self.cat_vars.values():
            v.set(val)
        self.refresh()

    def load_ids(self):
        f = filedialog.askopenfilename(parent=self, title="CSV de IDs",
                                       filetypes=[("CSV / TXT", "*.csv *.txt"), ("Todos", "*.*")])
        if not f:
            return
        try:
            ids = list(read_mapping_csv(f).keys())
            if not ids:
                ids = [x for x in re.split(r"[\s,;]+", open(f, encoding="utf-8-sig").read()) if x]
        except Exception as exc:
            messagebox.showerror("Selector", str(exc), parent=self)
            return
        self.txt_ids.delete("1.0", "end")
        self.txt_ids.insert("1.0", "\n".join(ids))
        self.refresh()

    def selected(self) -> List[int]:
        cats = {c for c, v in self.cat_vars.items() if v.get()}
        n = self.net
        ids_txt = {x for x in re.split(r"[\s,;]+", self.txt_ids.get("1.0", "end")) if x}

        def rng(key):
            a, b = (self.rng[key][0].get().strip(), self.rng[key][1].get().strip())
            try:
                return (float(a) if a else -np.inf, float(b) if b else np.inf)
            except ValueError:
                return (-np.inf, np.inf)
        dlo, dhi = rng("diameter")
        rlo, rhi = rng("roughness")
        out = []
        for i in self.elements():
            if self.catalog(i) not in cats:
                continue
            if self.is_link():
                if not (dlo <= n.diameter[i] <= dhi and rlo <= n.roughness[i] <= rhi):
                    continue
            idn = self.ident(i)
            if ids_txt and idn not in ids_txt:
                continue
            if self.map_sel is not None and idn not in self.map_sel:
                continue
            out.append(i)
        return out

    def refresh(self):
        sel = self.selected()
        self.v_count.set(f"{len(sel)} elementos seleccionados")
        self._highlight({self.ident(i) for i in sel})

    # ---------------------------------------------------------------- mapa
    def _map(self):
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
        from matplotlib.figure import Figure
        self.fig = Figure(figsize=(8, 6), dpi=100)
        self.ax = self.fig.add_axes([0.01, 0.01, 0.98, 0.98])
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.map_host)
        self.toolbar = NavigationToolbar2Tk(self.canvas, self.map_host, pack_toolbar=False)
        self.toolbar.update()
        self.toolbar.pack(side="bottom", fill="x")
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.selector = None

    def _link_xy(self, i):
        n = self.net
        a, b = n.node_ids[n.node1[i]], n.node_ids[n.node2[i]]
        if a not in self.coords or b not in self.coords:
            return None
        return [self.coords[a]] + self.verts.get(n.link_ids[i], []) + [self.coords[b]]

    def draw_map(self):
        from matplotlib.collections import LineCollection
        ax = self.ax
        ax.clear()
        ax.set_axis_off()
        n = self.net
        segs = [s for s in (self._link_xy(i) for i in range(len(n.link_ids))) if s]
        ax.add_collection(LineCollection(segs, colors="#C9CDD3", linewidths=0.8, zorder=1))
        self.items = {}
        if self.is_link():
            for i in self.elements():
                s = self._link_xy(i)
                if s:
                    arr = np.array(s)
                    self.items[n.link_ids[i]] = arr.mean(axis=0)
            self.sel_coll = LineCollection([], colors="#E30613", linewidths=2.2, zorder=3)
            ax.add_collection(self.sel_coll)
            if self.kind() == "valves":
                pts = np.array(list(self.items.values())) if self.items else np.zeros((0, 2))
                ax.scatter(pts[:, 0], pts[:, 1], s=22, marker="D", color="#4B5563", zorder=2)
        else:
            for i in self.elements():
                if n.node_ids[i] in self.coords:
                    self.items[n.node_ids[i]] = np.array(self.coords[n.node_ids[i]])
            pts = np.array(list(self.items.values())) if self.items else np.zeros((0, 2))
            ax.scatter(pts[:, 0], pts[:, 1], s=6 if self.kind() == "junctions" else 60,
                       color="#6B7280", zorder=2, marker="o" if self.kind() == "junctions" else "s")
            self.sel_coll = ax.scatter([], [], s=18 if self.kind() == "junctions" else 120,
                                       color="#E30613", zorder=3)
        allxy = np.array(list(self.coords.values())) if self.coords else np.zeros((1, 2))
        x0, y0 = allxy.min(axis=0)
        x1, y1 = allxy.max(axis=0)
        mx, my = (x1 - x0) * 0.03 + 1e-6, (y1 - y0) * 0.03 + 1e-6
        ax.set_xlim(x0 - mx, x1 + mx)
        ax.set_ylim(y0 - my, y1 + my)
        ax.set_aspect("equal", adjustable="box")
        self._set_tool()
        self.canvas.draw_idle()

    def _highlight(self, ids: Set[str]):
        if not hasattr(self, "sel_coll"):
            return
        if self.is_link():
            n = self.net
            segs = [self._link_xy(n.link_pos[i]) for i in ids if i in n.link_pos]
            self.sel_coll.set_segments([s for s in segs if s])
        else:
            pts = [self.items[i] for i in ids if i in self.items]
            self.sel_coll.set_offsets(np.array(pts) if pts else np.zeros((0, 2)))
        self.canvas.draw_idle()

    def _set_tool(self):
        from matplotlib.widgets import LassoSelector, RectangleSelector
        if self.selector is not None:
            self.selector.set_active(False)
            self.selector = None
        t = self.v_tool.get()
        if t == "Rectángulo":
            self.selector = RectangleSelector(self.ax, self._on_rect, useblit=False, button=[1],
                                              interactive=False,
                                              props=dict(facecolor="#E30613", alpha=0.12, edgecolor="#E30613"))
        elif t == "Lazo":
            self.selector = LassoSelector(self.ax, self._on_lasso, button=[1],
                                          props=dict(color="#E30613", linewidth=1.5))

    def _apply(self, inside: Set[str]):
        mode = self.v_mode.get()
        cur = self.map_sel or set()
        if mode == "Añadir":
            self.map_sel = cur | inside
        elif mode == "Quitar":
            base = cur if self.map_sel is not None else set(self.items)
            self.map_sel = base - inside
        else:
            self.map_sel = inside
        self.refresh()

    def _on_rect(self, e1, e2):
        x0, x1 = sorted((e1.xdata, e2.xdata))
        y0, y1 = sorted((e1.ydata, e2.ydata))
        self._apply({k for k, (x, y) in self.items.items() if x0 <= x <= x1 and y0 <= y <= y1})

    def _on_lasso(self, verts):
        from matplotlib.path import Path
        path = Path(verts)
        keys = list(self.items)
        if not keys:
            return
        inside = path.contains_points(np.array([self.items[k] for k in keys]))
        self._apply({k for k, ok in zip(keys, inside) if ok})

    def clear_map(self):
        self.map_sel = None
        self.refresh()

    # ---------------------------------------------------------------- fin
    def done(self):
        sel = self.selected()
        if not sel:
            messagebox.showwarning("Selector", "No hay elementos seleccionados.", parent=self)
            return
        inv = {v: k for k, v in self.labels.items()}
        try:
            lo, hi = float(self.v_lo.get()), float(self.v_hi.get())
        except ValueError:
            messagebox.showerror("Selector", "Límites no válidos.", parent=self)
            return
        if lo >= hi or (self.v_log.get() and lo <= 0):
            messagebox.showerror("Selector", "El mínimo debe ser menor que el máximo (y > 0 con escala log).",
                                 parent=self)
            return
        ids = [self.ident(i) for i in sel]
        name = re.sub(r"[^A-Za-z0-9_]", "_", self.v_name.get() or "GRUPO")
        self.on_done({"use": True, "name": name, "type": inv.get(self.v_type.get(), self.v_type.get()),
                      "mode": self.v_pmode.get(), "select": {"ids": ids}, "n": len(ids),
                      "bounds": [lo, hi], "log": bool(self.v_log.get()), "kind": self.kind(),
                      "catalogs": [c for c, v in self.cat_vars.items() if v.get()],
                      "exclusive": bool(self.v_excl.get()), "ids": ids})
        self.destroy()
