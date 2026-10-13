"""Plugin de ejemplo para EPACAL.

Copie este fichero (o uno propio) en  Documentos\\EPACAL\\plugins  y se cargará al
abrir la aplicación. Úselo como plantilla para ampliar el calibrador sin
recompilar nada.

Qué añade este ejemplo
  1. Objetivo  PRESION_NOCHE : error de presión sólo de 0 a 6 h (útil para fugas).
     Úselo en Avanzado -> hydraulic.objectives: [PRESION_NOCHE, CAMBIO]
  2. Parámetro diameter      : multiplica el diámetro de las tuberías de un grupo.
     Úselo en Avanzado -> hydraulic.parameters:
        - {group: PVC, type: diameter, mode: multiplier, bounds: [0.9, 1.1]}
  3. Al terminar, escribe un resumen en  resultados\\resumen_plugin.txt
"""
import os

import numpy as np

from epacal.plugins import register_objective, register_parameter_type, register_result_hook


@register_objective("PRESION_NOCHE")
def presion_noche(ev, sim, mask):
    horas = (ev.obs.times / 3600.0) % 24
    m = mask & (horas < 6)
    filas = ev.obs.rows_of("PRESSURE")
    if len(filas) == 0 or not m.any():
        return 0.0
    e = sim[filas][:, m] - ev.obs.values[filas][:, m]
    return float(np.sqrt(np.nanmean(e ** 2)))


register_parameter_type("diameter", element="link", prop=0,       # 0 = EN_DIAMETER
                        default_mode="multiplier", link_types=["PIPE"])


@register_result_hook
def resumen(resultado, cfg, out_dir):
    h = resultado.get("hydraulic")
    with open(os.path.join(out_dir, "resumen_plugin.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"Proyecto: {cfg.get('name')}\n")
        fh.write(f"INP final: {resultado['inp']}\n")
        if h is not None:
            for o, a, b in zip(h.objectives, h.obj_initial, h.obj_final):
                fh.write(f"{o}: {a:.4f} -> {b:.4f}\n")
