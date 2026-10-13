# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: genera dist/EPACAL/ (EPACAL.exe + bibliotecas).
#   pyinstaller packaging/epacal.spec --noconfirm
import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
sys.path.insert(0, ROOT)

# biblioteca oficial EPANET 2.2 (se toma de wntr en la máquina de compilación)
import wntr
base = os.path.join(os.path.dirname(wntr.__file__), "epanet", "libepanet")
dll = {"win32": os.path.join(base, "windows-x64", "epanet22.dll"),
       "darwin": os.path.join(base, "darwin-x64", "libepanet22.dylib")}.get(
    sys.platform, os.path.join(base, "linux-x64", "libepanet22.so"))

a = Analysis(
    [os.path.join(ROOT, "epacal_app.py")],
    pathex=[ROOT],
    binaries=[(dll, "epanet")],
    datas=collect_data_files("customtkinter"),
    hiddenimports=collect_submodules("epacal") + ["customtkinter", "darkdetect", "scipy.optimize",
                                                  "matplotlib.backends.backend_tkagg", "matplotlib.backends.backend_agg",
                                                  "yaml", "pandas", "numpy", "openpyxl"],
    excludes=["wntr", "networkx", "epyt", "pytest", "IPython", "jupyter", "notebook",
              "PyQt5", "PyQt6", "PySide2", "PySide6", "sphinx", "examples", "tests"],
    noarchive=False,
)
pyz = PYZ(a.pure)
icon = os.path.join(ROOT, "packaging", "epacal.ico")
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="EPACAL",
    console=False,                 # aplicación de ventanas
    icon=icon if os.path.exists(icon) else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="EPACAL")
