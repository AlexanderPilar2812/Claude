"""Punto de entrada de la aplicación EPACAL (el que empaqueta PyInstaller).

    EPACAL.exe                         -> abre la aplicación de escritorio
    EPACAL.exe config.yaml [opciones]  -> calibra sin ventanas (mismas opciones que
                                          ``python -m epacal``; --log FICHERO guarda el registro)
"""
import multiprocessing
import sys


def main():
    multiprocessing.freeze_support()          # necesario para los procesos en paralelo
    args = sys.argv[1:]
    if args and args[0] == "--selftest-gui":
        from epacal.gui import main as gui
        gui(selftest=args[1] if len(args) > 1 else "epacal_selftest.txt")
        return
    if args and not args[0].startswith("--gui"):
        from epacal.__main__ import main as cli
        sys.exit(cli(args))
    from epacal.gui import main as gui
    gui()


if __name__ == "__main__":
    main()
