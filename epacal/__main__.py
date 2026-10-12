"""Línea de comandos.

    python -m epacal config.yaml                 # flujo completo (pérdidas + hidráulica)
    python -m epacal config.yaml --modo hydraulic
    python -m epacal config.yaml --modo losses --salida resultados_perdidas
"""
import argparse
import sys
import time

from .workflow import run


def main(argv=None):
    ap = argparse.ArgumentParser(prog="epacal", description="Calibración de modelos EPANET")
    ap.add_argument("config", help="fichero YAML/JSON de configuración")
    ap.add_argument("--modo", choices=["full", "hydraulic", "losses"], default=None)
    ap.add_argument("--salida", default=None, help="carpeta de resultados")
    a = ap.parse_args(argv)
    t0 = time.time()

    def log(msg):
        print(f"[{time.time() - t0:7.1f}s] {msg}", flush=True)

    run(a.config, a.modo, a.salida, log)


if __name__ == "__main__":
    sys.exit(main())
