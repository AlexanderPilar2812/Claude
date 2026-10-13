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
    # la consola de Windows (cp1252/cp850) no admite todos los caracteres de los mensajes
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="epacal", description="Calibración de modelos EPANET")
    ap.add_argument("config", help="fichero YAML/JSON de configuración")
    ap.add_argument("--modo", choices=["full", "hydraulic", "losses"], default=None)
    ap.add_argument("--salida", default=None, help="carpeta de resultados")
    ap.add_argument("--epanet", choices=["2.2", "2.3", "auto"], default=None,
                    help="versión de EPANET (por defecto la de la configuración, o 2.2)")
    ap.add_argument("--log", default=None, help="guardar también el registro en este fichero")
    a = ap.parse_args(argv)
    t0 = time.time()
    fh = open(a.log, "w", encoding="utf-8") if a.log else None

    def log(msg):
        line = f"[{time.time() - t0:7.1f}s] {msg}"
        if sys.stdout is not None:     # en el ejecutable sin consola stdout es None
            print(line, flush=True)
        if fh:
            fh.write(line + "\n")
            fh.flush()

    try:
        run(a.config, a.modo, a.salida, log, epanet_version=a.epanet)
    except Exception as exc:
        log(f"ERROR: {exc}")
        raise
    finally:
        if fh:
            fh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
