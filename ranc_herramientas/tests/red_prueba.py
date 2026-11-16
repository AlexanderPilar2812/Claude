"""Red sintética con las convenciones del proyecto (CMH, D-W, pesos que suman 1, patrón absoluto,
embalse con carga unitaria y cota en el patrón). Sirve para probar las herramientas que necesitan un INP.
Con errores=True se inyectan los fallos de exportación que ya han aparecido en los sectores."""
import math


def inp_texto(errores=False, fugas_pct=0.0) -> str:
    n = 24; pesos = [1 / n] * n
    if errores: pesos[3] = pesos[3] * 0.3                                  # los pesos dejan de sumar 1
    cot = [1478 - 1.6 * i for i in range(n)]
    dia = [18 + 14 * math.exp(-((h - 7) / 2.2) ** 2) + 10 * math.exp(-((h - 19) / 2.8) ** 2) - 9 * math.exp(-((h - 2.5) / 2.5) ** 2) for h in [k / 4 for k in range(96)]]
    dem = [dia[k % 96] * (1.06 if (k // 96) in (4, 5) else 1.0) for k in range(672)]
    niv = [1500 + 2.0 + 1.2 * math.sin(2 * math.pi * (k / 96 - 0.2)) for k in range(672)]
    L = ["[TITLE]", "Red de prueba RANC" + (" - versión con errores de exportación" if errores else ""), "", "[JUNCTIONS]", ";ID  Elev  Demand  Pattern"]
    L.append(" A1  1482.000  0  ;"); L.append(" A2  1482.000  0  ;")
    for i in range(n):
        z = 0.0 if (errores and i == 10) else cot[i]
        if errores: L.append(f" N{i + 1}  {z:.3f}  0  ;")
        else: L.append(f" N{i + 1}  {z:.3f}  {pesos[i]:.6f}  PDEM  ;")
    L += [" B1  1458.000  0  ;", " B2  1458.000  0  ;"]
    if errores: L.append(" SUELTO  1450.000  0  ;")
    L += ["", "[RESERVOIRS]", ";ID  Head  Pattern", " T1  1  PNIV  ;", "", "[TANKS]", "", "[PIPES]", ";ID  N1  N2  Length  Diam  Rough  MLoss  Status"]
    L.append(" P0  T1  A1  12  150  0.01  0  OPEN  ;"); L.append(" P1  A2  N1  80  150  0.01  0  OPEN  ;")
    for i in range(1, 12): L.append(f" PA{i}  N{i}  N{i + 1}  {0.2 if (errores and i == 5) else 95}  {150 if i < 6 else 100}  0.01  0  OPEN  ;")
    L.append(" PB0  N12  B1  20  100  0.01  0  OPEN  ;"); L.append(" PB1  B2  N13  20  100  0.01  0  OPEN  ;")
    for i in range(13, 24): L.append(f" PA{i}  N{i}  N{i + 1}  90  {100 if i < 19 else 75}  0.01  0  OPEN  ;")
    L.append(" PL1  N3  N9  260  75  0.01  0  OPEN  ;"); L.append(" PL2  N15  N21  240  75  0.01  0  OPEN  ;")
    L += ["", "[PUMPS]", "", "[VALVES]", ";ID  N1  N2  Diam  Type  Setting  MLoss"]
    L.append(f" V1  A1  A2  {0.001 if errores else 150}  TCV  5  0  ;"); L.append(" V2  B1  B2  100  PRV  22  0  ;")
    L += ["", "[DEMANDS]"]
    if errores:
        for i in range(n): L.append(f" N{i + 1}  {pesos[i]:.6f}  PDEM  ;")
        L.append(f" N7  {0.004:.6f}  PDEM  ;segunda categoría")
    L += ["", "[STATUS]"]
    if errores: L += [" V2  OPEN", " V9  CLOSED"]
    L += ["", "[PATTERNS]"]
    for nom, val in (("PDEM", dem), ("PNIV", niv)):
        for i in range(0, 672, 12): L.append(f" {nom}  " + "  ".join(f"{x:.4f}" for x in val[i:i + 12]))
    L += ["", "[CURVES]", "", "[CONTROLS]", "", "[RULES]", "", "[ENERGY]", "", "[EMITTERS]", "", "[QUALITY]", "", "[REACTIONS]", "", "[TIMES]",
          " Duration  168", " Hydraulic Timestep  0:15", " Quality Timestep  0:05", " Pattern Timestep  0:15", " Pattern Start  0:00", " Report Timestep  0:15",
          " Report Start  0:00", " Start ClockTime  12 am", " Statistic  NONE", "", "[REPORT]", " Status  No", " Summary  No", "", "[OPTIONS]",
          " Units  CMH", " Headloss  D-W", " Specific Gravity  1", " Viscosity  1", " Trials  40", " Accuracy  0.001", " Unbalanced  Continue 10", " Pattern  1",
          f" Demand Multiplier  {1.1 if errores else 1.0}", " Emitter Exponent  0.5", " Demand Model  PDA", " Minimum Pressure  0", " Required Pressure  10",
          " Pressure Exponent  0.5", "", "[COORDINATES]"]
    for i in range(n): L.append(f" N{i + 1}  {100 * (i % 12)}  {0 if i < 12 else -150}")
    L += [" T1  -200  0", " A1  -150  0", " A2  -100  0", " B1  1150  -60", " B2  1150  -110", "", "[END]"]
    return "\n".join(L) + "\n"
