"""EPACAL - aplicación de escritorio para calibrar modelos EPANET.

Interfaz moderna (CustomTkinter): barra lateral con los pasos, tarjetas, modo
claro/oscuro, indicadores de resultados y frente de Pareto interactivo.
Los colores se definen en ``THEME`` (acento rojo corporativo de ACCIONA).
"""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from typing import Dict, List, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk
import numpy as np
import yaml

APP = "EPACAL"
VERSION = "1.2.0"

# ------------------------------------------------------------------ tema
THEME = {
    "accent": "#E30613",          # rojo ACCIONA
    "accent_hover": "#B5000F",
    "accent_soft": ("#FDE6E8", "#3A1416"),
    "bg": ("#F3F4F6", "#111214"),
    "card": ("#FFFFFF", "#1C1D21"),
    "border": ("#E5E7EB", "#2C2E33"),
    "ink": ("#111827", "#F3F4F6"),
    "muted": ("#6B7280", "#9CA3AF"),
    "sidebar": ("#16171A", "#0B0C0E"),
    "sidebar_ink": "#E5E7EB",
    "sidebar_muted": "#8B8F98",
}
FONT = "Segoe UI" if sys.platform.startswith("win") else "DejaVu Sans"
MONO = "Cascadia Mono" if sys.platform.startswith("win") else "DejaVu Sans Mono"

TYPES = ["PRESSURE", "FLOW", "LEVEL", "HEAD"]
TYPE_LABEL = {"PRESSURE": "Presión", "FLOW": "Caudal", "LEVEL": "Nivel de tanque",
              "HEAD": "Altura piezométrica"}
PARAM_TYPES = ["roughness", "valve_setting", "minor_loss", "reservoir_head", "elevation_offset",
               "tank_level", "tank_diameter", "demand_multiplier", "emitter_multiplier"]
PARAM_LABEL = {"roughness": "Rugosidad", "valve_setting": "Consigna válvula",
               "minor_loss": "Pérd. menores", "reservoir_head": "Nivel embalse (m)",
               "elevation_offset": "Cota (m)", "tank_level": "Nivel inicial tanque",
               "tank_diameter": "Diámetro tanque", "demand_multiplier": "Mult. demanda",
               "emitter_multiplier": "Mult. emisores"}
PRESETS = {"Rápida": "rapida", "Pareto": "pareto", "Exhaustiva": "exhaustiva"}
PRESET_HELP = {"rapida": "Para en cuanto una solución cumple todas las reglas (≈2 min).",
               "pareto": "Frente de Pareto completo para elegir la calibración (≈5 min).",
               "exhaustiva": "Frente más denso y más generaciones (≈15 min)."}
OBJ_LABEL = {"PRESSURE": "Error de presión", "FLOW": "Error de caudal", "LEVEL": "Error de nivel",
             "CAMBIO": "Cambio respecto al modelo original"}
PAGES = [("datos", "1", "Datos", "Modelo y medidas de campo"),
         ("ajustes", "2", "Ajustes", "Pérdidas, parámetros y reglas de aceptación"),
         ("calibrar", "3", "Calibrar", "Ejecución y progreso"),
         ("resultados", "4", "Resultados", "Frente de Pareto: elija su calibración"),
         ("avanzado", "⚙", "Avanzado", "Configuración YAML y plugins")]


def user_home() -> str:
    d = os.path.join(os.path.expanduser("~"), "Documents", APP)
    os.makedirs(os.path.join(d, "plugins"), exist_ok=True)
    return d


def open_path(path: Optional[str]) -> None:
    if not path:
        return
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


class Cancelled(Exception):
    pass


def _c(v):
    """Color según el modo de apariencia actual."""
    if isinstance(v, tuple):
        return v[1] if ctk.get_appearance_mode() == "Dark" else v[0]
    return v


# ------------------------------------------------------------------ widgets
class EditableTree(ttk.Treeview):
    """Tabla con edición en celda (doble clic); ``checks`` se alternan con un clic."""

    def __init__(self, master, columns, headings, widths, choices=None, checks=(),
                 readonly=(), **kw):
        super().__init__(master, columns=columns, show="headings", style="Epa.Treeview", **kw)
        self.choices = choices or {}
        self.checks = set(checks)
        self.readonly = set(readonly)
        for c, h, w in zip(columns, headings, widths):
            self.heading(c, text=h)
            self.column(c, width=w, anchor="w", stretch=True)
        self.bind("<Double-1>", self._edit)
        self.bind("<Button-1>", self._toggle, add="+")

    def _cell(self, event):
        row, col = self.identify_row(event.y), self.identify_column(event.x)
        if not row or not col:
            return None, None
        return row, self["columns"][int(col[1:]) - 1]

    def _toggle(self, event):
        row, col = self._cell(event)
        if row and col in self.checks:
            self.set(row, col, "○" if self.set(row, col) == "●" else "●")
            return "break"

    def _edit(self, event):
        row, col = self._cell(event)
        if not row or col in self.checks or col in self.readonly:
            return
        x, y, w, h = self.bbox(row, col)
        val = self.set(row, col)
        if col in self.choices:
            ed = ttk.Combobox(self, values=self.choices[col], state="readonly")
            ed.set(val)
        else:
            ed = ttk.Entry(self)
            ed.insert(0, val)
        ed.place(x=x, y=y, width=w, height=h)
        ed.focus_set()

        def done(_=None):
            self.set(row, col, ed.get())
            ed.destroy()
        ed.bind("<Return>", done)
        ed.bind("<FocusOut>", done)
        ed.bind("<Escape>", lambda _: ed.destroy())
        if isinstance(ed, ttk.Combobox):
            ed.bind("<<ComboboxSelected>>", done)


def card(master, title: Optional[str] = None, subtitle: Optional[str] = None):
    f = ctk.CTkFrame(master, fg_color=THEME["card"], corner_radius=14, border_width=1,
                     border_color=THEME["border"])
    if title:
        ctk.CTkLabel(f, text=title, font=(FONT, 15, "bold"), text_color=THEME["ink"],
                     anchor="w").pack(fill="x", padx=18, pady=(14, 0))
    if subtitle:
        ctk.CTkLabel(f, text=subtitle, font=(FONT, 12), text_color=THEME["muted"], anchor="w",
                     justify="left", wraplength=900).pack(fill="x", padx=18, pady=(2, 0))
    return f


def primary(master, text, command, **kw):
    return ctk.CTkButton(master, text=text, command=command, fg_color=THEME["accent"],
                         hover_color=THEME["accent_hover"], text_color="#FFFFFF",
                         corner_radius=10, height=38, font=(FONT, 13, "bold"), **kw)


def secondary(master, text, command, **kw):
    return ctk.CTkButton(master, text=text, command=command, fg_color="transparent",
                         border_width=1, border_color=THEME["border"], text_color=THEME["ink"],
                         hover_color=THEME["accent_soft"], corner_radius=10, height=36,
                         font=(FONT, 12), **kw)


# ------------------------------------------------------------------ aplicación
class App(ctk.CTk):
    def __init__(self):
        ctk.set_appearance_mode("Light")
        super().__init__(fg_color=THEME["bg"])
        self.title(f"{APP} — Calibración de redes EPANET")
        self.geometry("1320x840")
        self.minsize(1100, 700)
        self.q: "queue.Queue" = queue.Queue()
        self.worker: Optional[threading.Thread] = None
        self.cancel = threading.Event()
        self.info = None
        self.params: List[dict] = []
        self.last_out: Optional[str] = None
        self.n_gen = 15
        self.pareto_df = None
        self.canvas = None
        self.t0 = time.time()
        self._style_tables()

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._sidebar()
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(1, weight=1)
        self._header()
        self.pages: Dict[str, ctk.CTkFrame] = {
            key: (ctk.CTkScrollableFrame(self.content, fg_color="transparent")
                  if key == "ajustes" else ctk.CTkFrame(self.content, fg_color="transparent"))
            for key, *_ in PAGES}
        self._page_data()
        self._page_settings()
        self._page_run()
        self._page_results()
        self._page_advanced()
        self.status = tk.StringVar(value="Elija el modelo INP y los ficheros de medidas para empezar.")
        ctk.CTkLabel(self.content, textvariable=self.status, font=(FONT, 11),
                     text_color=THEME["muted"], anchor="w").grid(row=2, column=0, sticky="we",
                                                                padx=28, pady=(0, 10))
        self.show("datos")
        self.after(150, self._poll)

    # ------------------------------------------------------------- estilos
    def _style_tables(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        bg, ink, bd = _c(THEME["card"]), _c(THEME["ink"]), _c(THEME["border"])
        st.configure("Epa.Treeview", background=bg, fieldbackground=bg, foreground=ink,
                     rowheight=30, font=(FONT, 10), borderwidth=0, relief="flat")
        st.configure("Epa.Treeview.Heading", background=_c(THEME["bg"]),
                     foreground=_c(THEME["muted"]), font=(FONT, 10, "bold"), relief="flat",
                     borderwidth=0, padding=(6, 6))
        st.map("Epa.Treeview", background=[("selected", _c(THEME["accent_soft"]))],
               foreground=[("selected", ink)])
        st.map("Epa.Treeview.Heading", background=[("active", bd)])
        st.layout("Epa.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

    def toggle_mode(self):
        ctk.set_appearance_mode("Dark" if self.v_dark.get() else "Light")
        self._style_tables()
        if self.pareto_df is not None:
            self.draw_pareto()

    # ------------------------------------------------------------- estructura
    def _sidebar(self):
        sb = ctk.CTkFrame(self, fg_color=THEME["sidebar"], corner_radius=0, width=250)
        sb.grid(row=0, column=0, sticky="nsw")
        sb.grid_propagate(False)
        sb.pack_propagate(False)
        logo = ctk.CTkFrame(sb, fg_color="transparent")
        logo.pack(fill="x", padx=22, pady=(26, 30))
        ctk.CTkFrame(logo, width=10, height=44, corner_radius=3,
                     fg_color=THEME["accent"]).pack(side="left")
        tbox = ctk.CTkFrame(logo, fg_color="transparent")
        tbox.pack(side="left", padx=12)
        ctk.CTkLabel(tbox, text=APP, font=(FONT, 22, "bold"), text_color="#FFFFFF",
                     anchor="w").pack(anchor="w")
        ctk.CTkLabel(tbox, text="Calibración de redes · EPANET 2.2", font=(FONT, 11),
                     text_color=THEME["sidebar_muted"], anchor="w").pack(anchor="w")
        self.nav = {}
        for key, num, label, _ in PAGES:
            row = ctk.CTkFrame(sb, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=3)
            bar = ctk.CTkFrame(row, width=4, height=28, corner_radius=2, fg_color="transparent")
            bar.pack(side="left", padx=(0, 6))
            btn = ctk.CTkButton(row, text=f"  {num}    {label}", anchor="w", height=42,
                                fg_color="transparent", hover_color="#26282D",
                                text_color=THEME["sidebar_ink"], font=(FONT, 14),
                                corner_radius=10, command=lambda k=key: self.show(k))
            btn.pack(side="left", fill="x", expand=True)
            self.nav[key] = (btn, bar)
        bottom = ctk.CTkFrame(sb, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", padx=22, pady=20)
        self.v_dark = tk.BooleanVar(value=False)
        ctk.CTkSwitch(bottom, text="Modo oscuro", variable=self.v_dark, command=self.toggle_mode,
                      progress_color=THEME["accent"], text_color=THEME["sidebar_ink"],
                      font=(FONT, 12)).pack(anchor="w", pady=(0, 14))
        for txt, cmd in (("Ejemplos y plugins", lambda: open_path(user_home())), ("Ayuda", self.help)):
            ctk.CTkButton(bottom, text=txt, anchor="w", fg_color="transparent",
                          hover_color="#26282D", text_color=THEME["sidebar_muted"],
                          font=(FONT, 12), command=cmd).pack(fill="x")
        ctk.CTkLabel(bottom, text=f"v{VERSION}", text_color=THEME["sidebar_muted"],
                     font=(FONT, 10)).pack(anchor="w", pady=(10, 0))

    def _header(self):
        h = ctk.CTkFrame(self.content, fg_color="transparent")
        h.grid(row=0, column=0, sticky="we", padx=28, pady=(22, 8))
        self.v_title, self.v_sub = tk.StringVar(), tk.StringVar()
        ctk.CTkLabel(h, textvariable=self.v_title, font=(FONT, 26, "bold"),
                     text_color=THEME["ink"], anchor="w").pack(anchor="w")
        ctk.CTkLabel(h, textvariable=self.v_sub, font=(FONT, 13), text_color=THEME["muted"],
                     anchor="w").pack(anchor="w")

    def show(self, key: str):
        for p in self.pages.values():
            p.grid_forget()
        self.pages[key].grid(row=1, column=0, sticky="nsew", padx=28, pady=(4, 10))
        for k, (btn, bar) in self.nav.items():
            on = k == key
            btn.configure(fg_color="#26282D" if on else "transparent",
                          font=(FONT, 14, "bold" if on else "normal"))
            bar.configure(fg_color=THEME["accent"] if on else "transparent")
        for k, _, label, sub in PAGES:
            if k == key:
                self.v_title.set(label)
                self.v_sub.set(sub)

    def help(self):
        messagebox.showinfo("Cómo se usa EPACAL", (
            "1 · Datos: elija el INP y añada las medidas (.dat de EPANET: id  hora  valor) "
            "indicando presión, caudal o nivel. Pulse «Analizar modelo».\n\n"
            "2 · Ajustes: % de pérdidas, parámetros (● activa/desactiva, doble clic edita), "
            "tipo de calibración, objetivos y reglas de aceptación.\n\n"
            "3 · Calibrar: pulse «Calibrar». Puede cancelar en cualquier momento.\n\n"
            "4 · Resultados: haga clic en un punto del frente o en una fila y pulse "
            "«Guardar INP». ★ = recomendada.\n\n"
            f"Plugins (.py) en {os.path.join(user_home(), 'plugins')}"))

    # ------------------------------------------------------------- 1. datos
    def _page_data(self):
        p = self.pages["datos"]
        p.grid_columnconfigure(0, weight=1)
        p.grid_rowconfigure(1, weight=1)
        self.v_inp, self.v_out = tk.StringVar(), tk.StringVar()
        self.v_name = tk.StringVar(value="Calibracion")

        c1 = card(p, "Modelo EPANET", "INP exportado desde Giswater o EPANET 2.2.")
        c1.grid(row=0, column=0, sticky="we", pady=(0, 14))
        r = ctk.CTkFrame(c1, fg_color="transparent")
        r.pack(fill="x", padx=18, pady=14)
        r.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(r, textvariable=self.v_inp, height=38, corner_radius=10,
                     font=(FONT, 12)).grid(row=0, column=0, sticky="we")
        secondary(r, "Examinar…", self.pick_inp, width=120).grid(row=0, column=1, padx=(10, 0))
        ctk.CTkLabel(r, text="Proyecto", font=(FONT, 12), text_color=THEME["muted"]).grid(
            row=0, column=2, padx=(24, 8))
        ctk.CTkEntry(r, textvariable=self.v_name, width=200, height=38, corner_radius=10,
                     font=(FONT, 12)).grid(row=0, column=3)

        c2 = card(p, "Medidas de campo", "Ficheros .dat de calibración (id  hora  valor) o .csv "
                  "(sensor, type, element, timestamp, value). Los IDs deben existir en el INP.")
        c2.grid(row=1, column=0, sticky="nsew", pady=(0, 14))
        body = ctk.CTkFrame(c2, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=18, pady=14)
        self.tv_obs = EditableTree(body, ("file", "type", "info"), ("Fichero", "Tipo", "Sensores"),
                                   (640, 170, 220),
                                   choices={"type": [TYPE_LABEL[k] for k in TYPES]},
                                   readonly=("file", "info"), height=6)
        self.tv_obs.pack(side="left", fill="both", expand=True)
        bf = ctk.CTkFrame(body, fg_color="transparent")
        bf.pack(side="left", fill="y", padx=(14, 0))
        for k in ("PRESSURE", "FLOW", "LEVEL"):
            secondary(bf, f"＋  {TYPE_LABEL[k]}", lambda k=k: self.add_obs(k), width=170).pack(
                fill="x", pady=4)
        secondary(bf, "Quitar", self.del_obs, width=170).pack(fill="x", pady=(16, 4))

        c3 = card(p)
        c3.grid(row=2, column=0, sticky="we")
        r3 = ctk.CTkFrame(c3, fg_color="transparent")
        r3.pack(fill="x", padx=18, pady=16)
        r3.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(r3, text="Resultados en", font=(FONT, 13, "bold"),
                     text_color=THEME["ink"]).grid(row=0, column=0, padx=(0, 12))
        ctk.CTkEntry(r3, textvariable=self.v_out, height=38, corner_radius=10,
                     font=(FONT, 12)).grid(row=0, column=1, sticky="we")
        secondary(r3, "Examinar…", self.pick_out, width=120).grid(row=0, column=2, padx=10)
        primary(r3, "Analizar modelo  →", self.analyze, width=200).grid(row=0, column=3)
        self.summary = ctk.CTkFrame(c3, fg_color="transparent", height=4)
        self.summary.pack(fill="x", padx=18, pady=(0, 14))

    def chip(self, master, label, value):
        f = ctk.CTkFrame(master, fg_color=THEME["bg"], corner_radius=10)
        ctk.CTkLabel(f, text=value, font=(FONT, 15, "bold"), text_color=THEME["ink"]).pack(
            anchor="w", padx=12, pady=(8, 0))
        ctk.CTkLabel(f, text=label, font=(FONT, 11), text_color=THEME["muted"]).pack(
            anchor="w", padx=12, pady=(0, 8))
        return f

    def pick_inp(self):
        f = filedialog.askopenfilename(title="Modelo EPANET",
                                       filetypes=[("EPANET", "*.inp"), ("Todos", "*.*")])
        if f:
            self.v_inp.set(f)
            base = os.path.splitext(os.path.basename(f))[0]
            self.v_name.set(re.sub(r"[^A-Za-z0-9_]", "_", base))
            if not self.v_out.get():
                self.v_out.set(os.path.join(os.path.dirname(f), f"resultados_{base}"))

    def pick_out(self):
        d = filedialog.askdirectory(title="Carpeta de resultados")
        if d:
            self.v_out.set(d)

    def add_obs(self, kind, files=None):
        fs = files or filedialog.askopenfilenames(
            title=f"Medidas de {TYPE_LABEL[kind].lower()}",
            filetypes=[("Calibración EPANET", "*.dat"), ("CSV", "*.csv"), ("Todos", "*.*")])
        from .autoconfig import read_dat_summary
        for f in fs:
            info = ""
            if f.lower().endswith(".dat"):
                try:
                    ids, hmax = read_dat_summary(f)
                    info = f"{len(ids)} sensor(es) · {hmax:.0f} h"
                except Exception as exc:
                    info = f"error: {exc}"
            self.tv_obs.insert("", "end", values=(f, TYPE_LABEL[kind], info))

    def del_obs(self):
        for i in self.tv_obs.selection():
            self.tv_obs.delete(i)

    def obs_files(self):
        inv = {v: k for k, v in TYPE_LABEL.items()}
        return [(self.tv_obs.set(i, "file"), inv.get(self.tv_obs.set(i, "type"), "PRESSURE"))
                for i in self.tv_obs.get_children()]

    def analyze(self):
        inp = self.v_inp.get()
        if not os.path.isfile(inp):
            messagebox.showerror(APP, "Elija primero un fichero INP válido.")
            return
        try:
            from . import autoconfig as ac
            from . import engine as en
            self.info = ac.analyze(inp, en._find_library(None, "2.2"))
        except Exception as exc:
            messagebox.showerror(APP, f"No se pudo leer el modelo:\n{exc}")
            return
        i = self.info
        kinds = sorted({k for _, k in self.obs_files()})
        for w in self.summary.winfo_children():
            w.destroy()
        fams = i["materials"] or i["tags"]
        chips = [("nudos", f"{i['n_nodes']:,}".replace(",", ".")),
                 ("tuberías", f"{i['n_pipes']:,}".replace(",", ".")),
                 ("familias", str(len(fams))), ("TCV estranguladas", str(len(i["tcv_throttled"]))),
                 ("embalses / tanques", f"{len(i['reservoirs'])} / {len(i['tanks'])}"),
                 ("pérdidas de carga", "D-W" if i["headloss"] == 1 else "H-W"),
                 ("duración", f"{i['duration_h']:.0f} h"),
                 ("medidas", " · ".join({"PRESSURE": "P", "FLOW": "Q", "LEVEL": "N", "HEAD": "H"}[k]
                                        for k in kinds) or "—")]
        for j, (lab, val) in enumerate(chips):
            self.chip(self.summary, lab, val).grid(row=0, column=j, padx=(0, 8), sticky="w")
        self.params = ac.propose_parameters(i)
        self.fill_params()
        from .autoconfig import read_dat_summary
        n_flow = sum(len(read_dat_summary(f)[0]) for f, k in self.obs_files()
                     if k == "FLOW" and f.lower().endswith(".dat"))
        objs = ac.default_objectives(kinds, bool(i["tanks"]), n_flow)
        for k, v in self.obj_vars.items():
            v.set(k in objs)
        self.fill_rules(ac.default_rules(kinds))
        self.status.set("Modelo analizado: revise «Ajustes» y pulse Calibrar.")

    # ------------------------------------------------------------- 2. ajustes
    def _page_settings(self):
        p = self.pages["ajustes"]
        p.grid_columnconfigure((0, 1), weight=1)
        p.grid_rowconfigure(1, weight=1)
        c1 = card(p, "Pérdidas reales", "Emisores repartidos por longitud y ajuste del patrón de consumo.")
        c1.grid(row=0, column=0, sticky="nsew", padx=(0, 7), pady=(0, 14))
        self.v_loss_on = tk.BooleanVar(value=True)
        self.v_loss, self.v_expo = tk.StringVar(value="17"), tk.StringVar(value="0.5")
        b = ctk.CTkFrame(c1, fg_color="transparent")
        b.pack(fill="x", padx=18, pady=14)
        ctk.CTkSwitch(b, text="Calibrar pérdidas", variable=self.v_loss_on,
                      progress_color=THEME["accent"], font=(FONT, 13)).grid(
            row=0, column=0, columnspan=2, sticky="w")
        for j, (lab, var) in enumerate((("Pérdidas del sector (%)", self.v_loss),
                                        ("Exponente de emisores N1", self.v_expo))):
            ctk.CTkLabel(b, text=lab, font=(FONT, 12), text_color=THEME["muted"]).grid(
                row=1, column=j, sticky="w", pady=(12, 2), padx=(0, 20))
            ctk.CTkEntry(b, textvariable=var, width=120, height=36, corner_radius=10,
                         font=(FONT, 13)).grid(row=2, column=j, sticky="w", padx=(0, 20))

        c2 = card(p, "Calibración")
        c2.grid(row=0, column=1, sticky="nsew", padx=(7, 0), pady=(0, 14))
        b2 = ctk.CTkFrame(c2, fg_color="transparent")
        b2.pack(fill="x", padx=18, pady=14)
        self.v_preset_lab = tk.StringVar(value="Pareto")
        self.v_preset_help = tk.StringVar(value=PRESET_HELP["pareto"])
        ctk.CTkSegmentedButton(
            b2, values=list(PRESETS), variable=self.v_preset_lab, selected_color=THEME["accent"],
            selected_hover_color=THEME["accent_hover"], unselected_color=("#6B7280", "#3F4248"),
            unselected_hover_color=("#4B5563", "#55585F"), fg_color=("#6B7280", "#3F4248"),
            text_color="#FFFFFF", font=(FONT, 13), height=34,
            command=lambda v: self.v_preset_help.set(PRESET_HELP[PRESETS[v]])).grid(
            row=0, column=0, columnspan=2, sticky="w")
        ctk.CTkLabel(b2, textvariable=self.v_preset_help, font=(FONT, 11),
                     text_color=THEME["muted"]).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 8))
        self.obj_vars = {}
        for j, k in enumerate(["PRESSURE", "FLOW", "LEVEL", "CAMBIO"]):
            v = tk.BooleanVar(value=k in ("PRESSURE", "CAMBIO"))
            self.obj_vars[k] = v
            ctk.CTkCheckBox(b2, text=OBJ_LABEL[k], variable=v, fg_color=THEME["accent"],
                            hover_color=THEME["accent_hover"], font=(FONT, 12), corner_radius=6
                            ).grid(row=2 + j // 2, column=j % 2, sticky="w", pady=3, padx=(0, 16))
        self.v_valid = tk.StringVar(value="24")
        vr = ctk.CTkFrame(b2, fg_color="transparent")
        vr.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ctk.CTkLabel(vr, text="Horas finales para validar", font=(FONT, 12),
                     text_color=THEME["muted"]).pack(side="left")
        ctk.CTkEntry(vr, textvariable=self.v_valid, width=70, height=32, corner_radius=8).pack(
            side="left", padx=8)

        c3 = card(p, "Parámetros a calibrar", "● activa / desactiva · doble clic para editar tipo, modo y "
                  "límites · cree grupos propios seleccionando elementos en el mapa")
        c3.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(0, 14))
        tb = ctk.CTkFrame(c3, fg_color="transparent")
        tb.pack(fill="x", padx=18, pady=(10, 0))
        primary(tb, "＋  Nuevo grupo desde selección…", lambda: self.open_selector(None), width=250).pack(side="left")
        secondary(tb, "Editar selección…", self.edit_selection, width=150).pack(side="left", padx=8)
        secondary(tb, "Quitar grupo", self.remove_group, width=120).pack(side="left")
        f3 = ctk.CTkFrame(c3, fg_color="transparent")
        f3.pack(fill="both", expand=True, padx=18, pady=14)
        self.tv_par = EditableTree(
            f3, ("use", "name", "type", "mode", "n", "lo", "hi", "log"),
            ("", "Familia / elemento", "Tipo", "Modo", "Elementos", "Mínimo", "Máximo", "Escala"),
            (40, 250, 180, 110, 105, 100, 100, 80),
            choices={"type": [PARAM_LABEL[t] for t in PARAM_TYPES],
                     "mode": ["absolute", "multiplier", "offset"], "log": ["log", "lineal"]},
            checks=("use",), readonly=("n",), height=8)
        self.tv_par.pack(fill="both", expand=True)

        c4 = card(p, "Reglas de aceptación",
                  "Umbral en cada paso de tiempo · % de pasos y % de sensores que deben cumplir")
        c4.grid(row=2, column=0, columnspan=2, sticky="nsew")
        f4 = ctk.CTkFrame(c4, fg_color="transparent")
        f4.pack(fill="both", expand=True, padx=18, pady=14)
        self.tv_rules = EditableTree(
            f4, ("name", "kind", "error", "thr", "tpct", "ppct"),
            ("Regla", "Variable", "Error", "Umbral (m o %)", "% pasos", "% sensores"),
            (170, 170, 120, 130, 100, 100),
            choices={"kind": [TYPE_LABEL[k] for k in TYPES], "error": ["absoluto", "relativo"]},
            height=3)
        self.tv_rules.pack(side="left", fill="both", expand=True)
        rb = ctk.CTkFrame(f4, fg_color="transparent")
        rb.pack(side="left", fill="y", padx=(14, 0))
        secondary(rb, "＋ Añadir", lambda: self.tv_rules.insert(
            "", "end", values=("NUEVA", "Presión", "absoluto", "2", "80", "80")), width=120).pack(pady=3)
        secondary(rb, "Quitar", lambda: [self.tv_rules.delete(i) for i in self.tv_rules.selection()],
                  width=120).pack(pady=3)

    # ---- grupos definidos por el usuario (selector con mapa)
    def open_selector(self, iid: Optional[str]):
        if not self.info:
            messagebox.showinfo(APP, "Pulse antes «Analizar modelo» en Datos.")
            return
        try:
            self.params = self.read_params()
        except Exception as exc:
            messagebox.showerror(APP, str(exc))
            return
        from .selector import ElementSelector
        initial = None
        if iid is not None:
            p = self.params[int(iid)]
            net = self.info["net"]
            kind = {"roughness": "pipes", "minor_loss": "pipes", "leak_area": "pipes",
                    "valve_setting": "valves", "reservoir_head": "sources", "tank_level": "sources",
                    "tank_diameter": "sources"}.get(p["type"], "junctions")
            if kind in ("pipes", "valves"):
                ids = [net.link_ids[i] for i in net.select_links(p["select"])]
            else:
                ids = [net.node_ids[i] for i in net.select_nodes(p["select"])]
            initial = dict(p, kind=kind, ids=ids)

        def done(grp):
            if grp.pop("exclusive", False):
                for j, q in enumerate(self.params):
                    if (iid is None or j != int(iid)) and q["type"] == grp["type"]:
                        sel = dict(q["select"])
                        sel["exclude_ids"] = sorted(set(sel.get("exclude_ids", [])) | set(grp["ids"]))
                        q["select"] = sel
            for k in ("kind", "catalogs", "ids"):
                grp.pop(k, None)
            for q in self.params:
                q["n"] = self.count_elements(q)
            if iid is None:
                self.params.append(grp)
            else:
                self.params[int(iid)] = grp
            self.fill_params()
            self.status.set(f"Grupo {grp['name']}: {grp['n']} elementos, {PARAM_LABEL.get(grp['type'], grp['type'])}.")
        ElementSelector(self, self.info["net"], self.v_inp.get(), THEME, FONT, done, PARAM_LABEL, initial)

    def count_elements(self, p: dict) -> int:
        """Número de elementos que resuelve la selección de un grupo (para la tabla)."""
        from . import engine as en
        net = self.info["net"]
        t = p["type"]
        if t in ("roughness", "minor_loss", "leak_area", "valve_setting"):
            idx = net.select_links(p["select"])
            ok = [en.EN_PIPE, en.EN_CVPIPE] if t != "valve_setting" else list(en.VALVE_TYPES)
            if t == "minor_loss":
                ok = ok + list(en.VALVE_TYPES)
            return int(np.isin(net.link_type[idx], ok).sum())
        idx = net.select_nodes(p["select"])
        if t in ("reservoir_head",):
            return int((net.node_type[idx] == en.EN_RESERVOIR).sum())
        if t in ("tank_level", "tank_diameter"):
            return int((net.node_type[idx] == en.EN_TANK).sum())
        return int((net.node_type[idx] == en.EN_JUNCTION).sum())

    def edit_selection(self):
        sel = self.tv_par.selection()
        if not sel:
            messagebox.showinfo(APP, "Seleccione un grupo en la tabla.")
            return
        self.open_selector(sel[0])

    def remove_group(self):
        sel = self.tv_par.selection()
        if not sel:
            return
        self.params = self.read_params()
        for iid in sorted((int(i) for i in sel), reverse=True):
            del self.params[iid]
        self.fill_params()

    def fill_params(self):
        self.tv_par.delete(*self.tv_par.get_children())
        for i, p in enumerate(self.params):
            self.tv_par.insert("", "end", iid=str(i), values=(
                "●" if p["use"] else "○", p["name"], PARAM_LABEL.get(p["type"], p["type"]),
                p["mode"], p["n"], p["bounds"][0], p["bounds"][1], "log" if p["log"] else "lineal"))

    def read_params(self) -> List[dict]:
        inv = {v: k for k, v in PARAM_LABEL.items()}
        out = []
        for iid in self.tv_par.get_children():
            p = dict(self.params[int(iid)])
            p["use"] = self.tv_par.set(iid, "use") == "●"
            p["name"] = self.tv_par.set(iid, "name")
            p["type"] = inv.get(self.tv_par.set(iid, "type"), p["type"])
            p["mode"] = self.tv_par.set(iid, "mode")
            p["bounds"] = [float(self.tv_par.set(iid, "lo")), float(self.tv_par.set(iid, "hi"))]
            p["log"] = self.tv_par.set(iid, "log") == "log"
            if p["use"] and p["bounds"][0] >= p["bounds"][1]:
                raise ValueError(f"Parámetro {p['name']}: el mínimo debe ser menor que el máximo")
            if p["use"] and p["log"] and p["bounds"][0] <= 0:
                raise ValueError(f"Parámetro {p['name']}: con escala log el mínimo debe ser > 0")
            out.append(p)
        return out

    def fill_rules(self, rules):
        self.tv_rules.delete(*self.tv_rules.get_children())
        for r in rules:
            self.tv_rules.insert("", "end", values=(
                r["name"], TYPE_LABEL.get(r["kind"], r["kind"]),
                "relativo" if r["error"].startswith("rel") else "absoluto",
                r["threshold"], r["timesteps_pct"], r["points_pct"]))

    def read_rules(self):
        inv = {v: k for k, v in TYPE_LABEL.items()}
        out = []
        for iid in self.tv_rules.get_children():
            v = [self.tv_rules.set(iid, c) for c in ("name", "kind", "error", "thr", "tpct", "ppct")]
            out.append({"name": v[0], "kind": inv.get(v[1], v[1]),
                        "error": "relative" if v[2].startswith("rel") else "absolute",
                        "threshold": float(v[3]), "timesteps_pct": float(v[4]),
                        "points_pct": float(v[5])})
        return out

    # ------------------------------------------------------------- configuración
    def build_cfg(self) -> dict:
        if self.v_use_yaml.get():
            return yaml.safe_load(self.txt_yaml.get("1.0", "end"))
        if not self.info:
            raise ValueError("Pulse antes «Analizar modelo» en Datos.")
        if not self.obs_files():
            raise ValueError("Añada al menos un fichero de medidas.")
        objs = [k for k, v in self.obj_vars.items() if v.get()]
        if not objs:
            raise ValueError("Elija al menos un objetivo.")
        from . import autoconfig as ac
        out = self.v_out.get() or os.path.join(os.path.dirname(self.v_inp.get()), "resultados_epacal")
        return ac.build_config(
            self.v_name.get() or "Calibracion", self.v_inp.get(), self.obs_files(), out,
            self.read_params(), self.read_rules(), objs,
            float(self.v_loss.get()) if self.v_loss_on.get() else None,
            PRESETS[self.v_preset_lab.get()], float(self.v_expo.get()),
            float(self.v_valid.get() or 0), plugin_dirs=[os.path.join(user_home(), "plugins")])

    def open_yaml(self, path: Optional[str] = None):
        f = path or filedialog.askopenfilename(title="Configuración",
                                               filetypes=[("YAML", "*.yaml *.yml"), ("Todos", "*.*")])
        if not f:
            return
        from .workflow import load_config
        cfg = load_config(f)
        base = os.path.dirname(os.path.abspath(f))

        def absp(q):
            return q if os.path.isabs(q) else os.path.join(base, q)
        cfg["inp"] = absp(cfg["inp"])
        cfg["output_dir"] = absp(cfg.get("output_dir", "resultados"))
        for src in cfg.get("observations", {}).get("files", []):
            src["file"] = absp(src["file"])
        for k in ("file", "sensors_file"):
            if k in cfg.get("observations", {}):
                cfg["observations"][k] = absp(cfg["observations"][k])
        self.txt_yaml.delete("1.0", "end")
        self.txt_yaml.insert("1.0", yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
        self.v_use_yaml.set(True)
        self.v_inp.set(cfg["inp"])
        self.v_out.set(cfg["output_dir"])
        self.v_name.set(cfg.get("name", "Calibracion"))
        self.tv_obs.delete(*self.tv_obs.get_children())
        for src in cfg.get("observations", {}).get("files", []):
            self.tv_obs.insert("", "end", values=(src["file"], TYPE_LABEL.get(
                str(src.get("type", "PRESSURE")).upper(), "Presión"), ""))
        self.status.set(f"Configuración cargada de {f}: se usará tal cual (Avanzado).")
        self.show("avanzado")

    def save_yaml(self):
        try:
            cfg = self.build_cfg()
        except Exception as exc:
            messagebox.showerror(APP, str(exc))
            return
        f = filedialog.asksaveasfilename(defaultextension=".yaml", filetypes=[("YAML", "*.yaml")])
        if f:
            with open(f, "w", encoding="utf-8") as fh:
                yaml.safe_dump(cfg, fh, allow_unicode=True, sort_keys=False)
            self.status.set(f"Configuración guardada en {f}")

    # ------------------------------------------------------------- 3. calibrar
    def _page_run(self):
        p = self.pages["calibrar"]
        p.grid_columnconfigure(0, weight=1)
        p.grid_rowconfigure(1, weight=1)
        top = card(p)
        top.grid(row=0, column=0, sticky="we", pady=(0, 14))
        r = ctk.CTkFrame(top, fg_color="transparent")
        r.pack(fill="x", padx=18, pady=18)
        self.b_run = primary(r, "▶   Calibrar", self.start, width=180)
        self.b_run.configure(height=46, font=(FONT, 15, "bold"))
        self.b_run.pack(side="left")
        self.b_cancel = secondary(r, "■  Cancelar", self.stop, width=130, state="disabled")
        self.b_cancel.pack(side="left", padx=12)
        info = ctk.CTkFrame(r, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True, padx=12)
        self.v_phase = tk.StringVar(value="Listo para calibrar")
        self.v_elapsed = tk.StringVar(value="")
        ctk.CTkLabel(info, textvariable=self.v_phase, font=(FONT, 15, "bold"),
                     text_color=THEME["ink"], anchor="w").pack(fill="x")
        ctk.CTkLabel(info, textvariable=self.v_elapsed, font=(FONT, 12),
                     text_color=THEME["muted"], anchor="w").pack(fill="x")
        self.pb = ctk.CTkProgressBar(top, height=10, corner_radius=5, progress_color=THEME["accent"])
        self.pb.set(0)
        self.pb.pack(fill="x", padx=18, pady=(0, 18))
        lc = card(p, "Registro")
        lc.grid(row=1, column=0, sticky="nsew")
        self.txt_log = ctk.CTkTextbox(lc, font=(MONO, 11), corner_radius=10, wrap="none",
                                      fg_color=THEME["bg"], text_color=THEME["ink"])
        self.txt_log.pack(fill="both", expand=True, padx=18, pady=14)

    def log(self, msg: str):
        self.q.put(("log", msg))

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        try:
            cfg = self.build_cfg()
        except Exception as exc:
            messagebox.showerror(APP, str(exc))
            return
        out = cfg.get("output_dir") or "resultados"
        os.makedirs(out, exist_ok=True)
        cfg_path = os.path.join(out, "proyecto_epacal.yaml")
        with open(cfg_path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(cfg, fh, allow_unicode=True, sort_keys=False)
        self.n_gen = int(cfg.get("hydraulic", {}).get("optimizer", {}).get("n_gen", 15))
        self.txt_log.delete("1.0", "end")
        self.pb.set(0)
        self.cancel.clear()
        self.b_run.configure(state="disabled")
        self.b_cancel.configure(state="normal")
        self.show("calibrar")
        self.last_out = out
        self.t0 = time.time()

        def work():
            from .workflow import run
            from .simulator import shutdown_all

            def lg(m):
                if self.cancel.is_set():
                    raise Cancelled()
                self.log(f"[{time.time() - self.t0:7.1f}s] {m}")
            try:
                self.q.put(("done", run(cfg_path, None, None, lg)))
            except Cancelled:
                shutdown_all()
                self.q.put(("cancelled", None))
            except Exception as exc:
                shutdown_all()
                self.q.put(("error", f"{exc}\n\n{traceback.format_exc()}"))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()
        self._tick()
        self.status.set(f"Calibrando… configuración guardada en {cfg_path}")

    def _tick(self):
        if self.worker and self.worker.is_alive():
            s = int(time.time() - self.t0)
            self.v_elapsed.set(f"Tiempo transcurrido {s // 60:02d}:{s % 60:02d}")
            self.after(1000, self._tick)

    def stop(self):
        self.cancel.set()
        self.v_phase.set("Cancelando…")

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self.txt_log.insert("end", payload + "\n")
                    self.txt_log.see("end")
                    self._progress(payload)
                    continue
                self.b_run.configure(state="normal")
                self.b_cancel.configure(state="disabled")
                if kind == "done":
                    self.pb.set(1)
                    self.v_phase.set("Calibración terminada ✓")
                    self.status.set(f"Resultados en {payload['out_dir']}")
                    self.load_results(payload["out_dir"])
                    self.show("resultados")
                elif kind == "cancelled":
                    self.v_phase.set("Calibración cancelada")
                else:
                    self.v_phase.set("Error en la calibración")
                    self.txt_log.insert("end", "\nERROR:\n" + payload)
                    messagebox.showerror(APP, payload.split("\n\n")[0])
        except queue.Empty:
            pass
        self.after(150, self._poll)

    def _progress(self, line: str):
        steps = [("[pérdidas]", .05, "Calibrando pérdidas…"), ("CONVERGIDO", .12, "Pérdidas calibradas"),
                 ("sensibilidad", .15, "Análisis de sensibilidad…"),
                 ("NSGA-II", .18, "Optimización NSGA-II…"),
                 ("frente de Pareto:", .82, "Evaluando el frente…"),
                 ("refinamiento local", .88, "Refinamiento local…"),
                 ("INP del frente", .94, "Guardando los INP del frente…"),
                 ("Informe:", .99, "Generando informe…")]
        for key, val, txt in steps:
            if key in line:
                self.pb.set(max(self.pb.get(), val))
                self.v_phase.set(txt)
        m = re.search(r"gen\s+(\d+)\s+\|", line)
        if m:
            g = int(m.group(1))
            self.pb.set(max(self.pb.get(), .18 + .64 * g / max(self.n_gen, 1)))
            self.v_phase.set(f"Optimización NSGA-II · generación {g} de {self.n_gen}")

    # ------------------------------------------------------------- 4. resultados
    def _page_results(self):
        p = self.pages["resultados"]
        p.grid_columnconfigure(0, weight=3)
        p.grid_columnconfigure(1, weight=2)
        p.grid_rowconfigure(2, weight=1)
        bar = ctk.CTkFrame(p, fg_color="transparent")
        bar.grid(row=0, column=0, columnspan=2, sticky="we", pady=(0, 12))
        primary(bar, "Guardar INP de la solución", self.save_selected, width=220).pack(side="left")
        secondary(bar, "Abrir informe", self.open_report, width=140).pack(side="left", padx=8)
        secondary(bar, "Abrir carpeta", lambda: open_path(self.last_out), width=130).pack(side="left")
        secondary(bar, "Cargar otra carpeta…", self.pick_results, width=170).pack(side="right")
        self.kpis = ctk.CTkFrame(p, fg_color="transparent")
        self.kpis.grid(row=1, column=0, columnspan=2, sticky="we", pady=(0, 12))
        self.kpi_widgets = []
        for j in range(4):
            self.kpis.grid_columnconfigure(j, weight=1)
            c = card(self.kpis)
            c.grid(row=0, column=j, sticky="we", padx=(0 if j == 0 else 6, 0 if j == 3 else 6))
            v, lab, s = tk.StringVar(value="—"), tk.StringVar(value=""), tk.StringVar(value="")
            ctk.CTkLabel(c, textvariable=lab, font=(FONT, 12), text_color=THEME["muted"],
                         anchor="w").pack(fill="x", padx=16, pady=(12, 0))
            ctk.CTkLabel(c, textvariable=v, font=(FONT, 26, "bold"), text_color=THEME["ink"],
                         anchor="w").pack(fill="x", padx=16)
            ctk.CTkLabel(c, textvariable=s, font=(FONT, 11), text_color=THEME["muted"],
                         anchor="w").pack(fill="x", padx=16, pady=(0, 12))
            self.kpi_widgets.append((v, lab, s))
        cc = card(p, "Frente de Pareto", "Clic en un punto para seleccionar la solución · ★ recomendada")
        cc.grid(row=2, column=0, sticky="nsew", padx=(0, 7))
        self.chart_host = ctk.CTkFrame(cc, fg_color="transparent")
        self.chart_host.pack(fill="both", expand=True, padx=12, pady=10)
        tc = card(p, "Soluciones")
        tc.configure(width=520, height=420)
        tc.pack_propagate(False)
        tc.grid(row=2, column=1, sticky="nsew", padx=(7, 0))
        th = ctk.CTkFrame(tc, fg_color="transparent")
        th.pack(fill="both", expand=True, padx=12, pady=10)
        self.tv_res = ttk.Treeview(th, show="headings", style="Epa.Treeview")
        sx = ttk.Scrollbar(th, orient="horizontal", command=self.tv_res.xview)
        self.tv_res.configure(xscrollcommand=sx.set)
        self.tv_res.pack(fill="both", expand=True)
        sx.pack(fill="x")
        self.tv_res.bind("<<TreeviewSelect>>", lambda e: self.draw_pareto())

    def pick_results(self):
        d = filedialog.askdirectory(title="Carpeta de resultados de EPACAL")
        if d:
            self.load_results(d)

    def load_results(self, out_dir: str):
        import pandas as pd
        self.last_out = out_dir
        f = os.path.join(out_dir, "frente_pareto.csv")
        self.tv_res.delete(*self.tv_res.get_children())
        if not os.path.exists(f):
            self.status.set(f"No hay frente de Pareto en {out_dir}")
            return
        df = pd.read_csv(f)
        self.pareto_df = df
        first = ["solucion", "recomendada", "reglas_cumplidas", "reglas_validacion"]
        objc = [c for c in df.columns
                if c.endswith(("_rmse", "_mae", "_huber", "_rme", "_0a1", "_validacion"))]
        cols = [c for c in first if c in df.columns]
        cols += [c for c in objc if c not in cols]
        cols += [c for c in df.columns if c not in cols and c != "inp"]
        self.tv_res["columns"] = cols
        names = {"solucion": ("Sol.", 52), "recomendada": ("★", 34),
                 "reglas_cumplidas": ("Reglas", 64), "reglas_validacion": ("Valid.", 60)}
        for c in cols:
            txt, w = names.get(c, (c.replace("_rmse", "").replace("_", " "), 118))
            self.tv_res.heading(c, text=txt)
            self.tv_res.column(c, width=w, minwidth=w, anchor="w", stretch=False)
        rec = 0
        for i, row in df.iterrows():
            vals = []
            for c in cols:
                v = row[c]
                if c == "recomendada":
                    rec = i if bool(v) else rec
                    v = "★" if bool(v) else ""
                elif isinstance(v, float):
                    v = f"{v:.4g}"
                vals.append(v)
            self.tv_res.insert("", "end", iid=str(i), values=vals)
        self.tv_res.selection_set(str(rec))
        self._kpis(out_dir, df)
        self.draw_pareto()

    def _kpis(self, out_dir, df):
        import pandas as pd
        vals = [("Pérdidas", "—", "sin calibración de pérdidas")]
        lp = os.path.join(out_dir, "resumen_perdidas_dma.csv")
        if os.path.exists(lp):
            s = pd.read_csv(lp).iloc[0]
            vals = [("Pérdidas logradas", f"{s['logrado']:.2f} %",
                     f"objetivo {s['objetivo']:g} % · {s['volumen_perdidas_m3']:.0f} m³")]
        j = {}
        jp = os.path.join(out_dir, "resumen_hidraulica.json")
        if os.path.exists(jp):
            with open(jp, encoding="utf-8") as fh:
                j = json.load(fh)
        oi, of = j.get("objetivos_inicial", {}), j.get("objetivos_final", {})
        k = next(iter(oi), None)
        if k:
            unit = {"PRESSURE": "m", "LEVEL": "m"}.get(k, "")
            vals.append((f"{OBJ_LABEL.get(k, k)} (RMSE)", f"{of[k]:.2f} {unit}",
                         f"modelo original {oi[k]:.2f} {unit}"))
        else:
            vals.append(("Error", "—", ""))
        rc, rv = j.get("reglas_calibracion", []), j.get("reglas_validacion", [])
        vals.append(("Reglas de aceptación", f"{sum(r['pasa'] for r in rc)}/{len(rc)}",
                     f"validación {sum(r['pasa'] for r in rv)}/{len(rv)}" if rv
                     else "sin periodo de validación"))
        vals.append(("Soluciones en el frente", str(len(df)),
                     f"{j.get('simulaciones', '—')} simulaciones · {j.get('segundos', 0):.0f} s"))
        for (v, lab, s), (a, b, c) in zip(self.kpi_widgets, vals):
            lab.set(a)
            v.set(b)
            s.set(c)

    def draw_pareto(self):
        df = self.pareto_df
        if df is None:
            return
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.figure import Figure
        objc = [c for c in df.columns if c.endswith(("_rmse", "_mae", "_huber", "_rme", "_0a1"))]
        if len(objc) < 2:
            objc = (objc + objc)[:2] if objc else [df.columns[1], df.columns[1]]
        x, y = df[objc[0]].to_numpy(float), df[objc[1]].to_numpy(float)
        bg, ink, mut, bd = _c(THEME["card"]), _c(THEME["ink"]), _c(THEME["muted"]), _c(THEME["border"])
        if self.canvas is None:
            self.fig = Figure(figsize=(6, 4), dpi=100)
            self.ax = self.fig.add_subplot(111)
            self.canvas = FigureCanvasTkAgg(self.fig, master=self.chart_host)
            self.canvas.get_tk_widget().pack(fill="both", expand=True)
            self.canvas.mpl_connect("pick_event", self._on_pick)
        ax = self.ax
        ax.clear()
        self.fig.patch.set_facecolor(bg)
        ax.set_facecolor(bg)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        for s in ("left", "bottom"):
            ax.spines[s].set_color(bd)
        ax.tick_params(colors=mut, labelsize=9)
        ax.grid(True, color=bd, linewidth=0.7)
        o = np.argsort(x)
        ax.plot(x[o], y[o], color=mut, lw=1, alpha=.45, zorder=1)
        dot = "#4B5563" if ctk.get_appearance_mode() == "Light" else "#9CA3AF"
        ax.scatter(x, y, s=70, color=dot, edgecolor=bg, linewidth=1.5, picker=6, zorder=2)
        if "recomendada" in df:
            r = np.where(df["recomendada"].astype(bool).to_numpy())[0]
            ax.scatter(x[r], y[r], s=280, marker="*", color=THEME["accent"], edgecolor=bg, zorder=3)
        sel = self.tv_res.selection()
        if sel:
            i = int(sel[0])
            ax.scatter([x[i]], [y[i]], s=340, facecolor="none", edgecolor=THEME["accent"],
                       linewidth=2.2, zorder=4)
            ax.annotate(str(df.loc[i, "solucion"]), (x[i], y[i]), xytext=(9, 9),
                        textcoords="offset points", color=ink, fontsize=10, fontweight="bold")
        lab = {"CAMBIO_0a1": "Cambio respecto al modelo original (0–1)"}
        ax.set_xlabel(lab.get(objc[0], objc[0].replace("_", " ")), color=mut)
        ax.set_ylabel(lab.get(objc[1], objc[1].replace("_", " ")), color=mut)
        self.fig.tight_layout()
        self.canvas.draw_idle()

    def _on_pick(self, event):
        if event.ind is not None and len(event.ind):
            i = int(event.ind[0])
            self.tv_res.selection_set(str(i))
            self.tv_res.see(str(i))

    def open_report(self):
        if self.last_out:
            f = os.path.join(self.last_out, "informe_calibracion.html")
            if os.path.exists(f):
                webbrowser.open("file:///" + os.path.abspath(f).replace("\\", "/"))

    def save_selected(self):
        sel = self.tv_res.selection()
        if not sel or self.pareto_df is None:
            messagebox.showinfo(APP, "Seleccione una solución (fila o punto del frente).")
            return
        row = self.pareto_df.iloc[int(sel[0])]
        src = os.path.join(self.last_out, str(row.get("inp", "")))
        if not os.path.isfile(src):
            messagebox.showerror(APP, f"No se encuentra {src}")
            return
        name = f"{os.path.basename(self.last_out)}_{str(row['solucion']).replace('#', 'sol')}.inp"
        f = filedialog.asksaveasfilename(defaultextension=".inp", initialfile=name,
                                         filetypes=[("EPANET", "*.inp")])
        if f:
            shutil.copyfile(src, f)
            self.status.set(f"Solución {row['solucion']} guardada en {f}")

    # ------------------------------------------------------------- avanzado
    def _page_advanced(self):
        p = self.pages["avanzado"]
        p.grid_columnconfigure(0, weight=1)
        p.grid_rowconfigure(1, weight=1)
        bar = ctk.CTkFrame(p, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="we", pady=(0, 12))
        secondary(bar, "Generar desde el formulario", self.form_to_yaml, width=210).pack(side="left")
        secondary(bar, "Abrir configuración…", self.open_yaml, width=170).pack(side="left", padx=8)
        secondary(bar, "Guardar configuración…", self.save_yaml, width=180).pack(side="left")
        self.v_use_yaml = tk.BooleanVar(value=False)
        ctk.CTkSwitch(bar, text="Calibrar con este YAML", variable=self.v_use_yaml,
                      progress_color=THEME["accent"], font=(FONT, 12)).pack(side="right")
        c = card(p, "Configuración completa (YAML)",
                 "Todas las opciones del calibrador; la misma que usa la línea de comandos.")
        c.grid(row=1, column=0, sticky="nsew", pady=(0, 12))
        self.txt_yaml = ctk.CTkTextbox(c, font=(MONO, 11), corner_radius=10, wrap="none",
                                       fg_color=THEME["bg"], text_color=THEME["ink"])
        self.txt_yaml.pack(fill="both", expand=True, padx=18, pady=14)
        from . import plugins
        plugins.load_plugins([os.path.join(user_home(), "plugins")])
        lp = plugins.loaded()
        pc = card(p, "Plugins", "Ficheros .py en " + os.path.join(user_home(), "plugins") +
                  " · objetivos, parámetros y acciones al terminar, sin recompilar")
        pc.grid(row=2, column=0, sticky="we")
        txt = ("   ".join(f"{'●' if v == 'ok' else '✖'} {os.path.basename(k)}" for k, v in lp.items())
               if lp else "Sin plugins cargados. Renombre ejemplo_plugin.py.txt a .py para probar.")
        r = ctk.CTkFrame(pc, fg_color="transparent")
        r.pack(fill="x", padx=18, pady=14)
        ctk.CTkLabel(r, text=txt, font=(FONT, 12), text_color=THEME["ink"]).pack(side="left")
        secondary(r, "Abrir carpeta", lambda: open_path(os.path.join(user_home(), "plugins")),
                  width=130).pack(side="right")

    def form_to_yaml(self):
        use = self.v_use_yaml.get()
        self.v_use_yaml.set(False)
        try:
            cfg = self.build_cfg()
        except Exception as exc:
            self.v_use_yaml.set(use)
            messagebox.showerror(APP, str(exc))
            return
        self.txt_yaml.delete("1.0", "end")
        self.txt_yaml.insert("1.0", yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))


def main(selftest: Optional[str] = None):
    """``selftest``: abre la ventana, comprueba EPANET y escribe un informe en ese fichero."""
    try:
        app = App()
    except Exception:
        if selftest:
            with open(selftest, "w", encoding="utf-8") as fh:
                fh.write("GUI ERROR\n" + traceback.format_exc())
            sys.exit(1)
        raise
    if selftest:
        def check():
            from . import engine as en
            from . import plugins
            lib = en._find_library(None, "2.2")
            with open(selftest, "w", encoding="utf-8") as fh:
                fh.write(f"GUI OK {VERSION}\nEPANET {en.library_version(lib)} {lib}\n"
                         f"plugins {plugins.loaded()}\n")
            app.destroy()
        app.after(2500, check)
    app.mainloop()


if __name__ == "__main__":
    main()
