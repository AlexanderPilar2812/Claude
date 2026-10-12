# epacal: calibración de modelos EPANET (hidráulica + pérdidas)

Herramienta en Python para calibrar modelos EPANET a partir de datos de campo.
Hace lo mismo que los módulos de calibración de Giswater y añade varias cosas:

| | Giswater | epacal |
|---|---|---|
| Calibración hidráulica | NSGA-II | NSGA-II, después refinamiento por gradiente (TRF/Levenberg-Marquardt) |
| Parámetros | rugosidad y consigna de válvulas por familia | rugosidad, consigna de válvulas, pérdidas menores, multiplicador de demanda, nivel del embalse, desplazamiento de cota, multiplicador de emisores y área de fuga FAVAD |
| Identificabilidad | no | sensibilidad compuesta escalada (CSS). Congela los parámetros que los sensores no pueden estimar e indica dónde faltan medidas |
| Incertidumbre | no | IC95 de cada parámetro y matriz de correlación |
| Validación | sobre los mismos datos | calibración y validación en periodos temporales separados |
| Datos de campo | sin filtrar | filtro de Hampel para picos espurios y remuestreo a la malla de simulación |
| Elección de la solución | punto del frente | entre las soluciones que más reglas cumplen se toma el codo, TOPSIS o una ponderación |
| Pérdidas | emisores con 6 pruebas fijas (Cal_Perd_2: 20,53 % logrado frente al 18 % pedido) | secante hasta la tolerancia (18,01 % con objetivo 18 % en 7 iteraciones) |
| Objetivo de pérdidas | % de volumen | % de volumen, **caudal mínimo nocturno (MNF)**, caudal medio o volumen |
| Ajuste del patrón | resta la fuga media | instante a instante, con la fuga simulada en cada paso (la fuga depende de la presión). El balance entrada = consumo + fugas cierra con un error menor del 0,05 % |
| Reparto de fugas | por nudo | por longitud, uniforme, por demanda o desde fichero, con factores por familia (p. ej. metal antiguo ×2). También FAVAD por tubería (EPANET 2.3) |
| Pérdidas ↔ hidráulica | procesos separados | rondas acopladas: pérdidas → hidráulica → pérdidas → … |
| Varios sectores | sí | sí. Si un patrón lo comparten varios sectores, se crea una copia por sector |

## Instalación

```bash
pip install -r requirements.txt     # numpy, pandas, scipy, matplotlib, pyyaml, epyt
```

`epyt` trae la biblioteca nativa de EPANET 2.3 para Windows, Linux y macOS.
Para usar otra versión, defina la variable `EPANET_LIB` con la ruta a `epanet2.dll`/`.so`
o ponga `epanet_lib:` en la configuración.

## Uso

```bash
python -m epacal mi_config.yaml                    # pérdidas + hidráulica (acoplado)
python -m epacal mi_config.yaml --modo losses      # sólo pérdidas
python -m epacal mi_config.yaml --modo hydraulic   # sólo hidráulica
```

Caso de demostración con solución conocida:

```bash
python examples/generar_caso_sintetico.py examples/caso_sintetico
python -m epacal examples/caso_sintetico/config.yaml
```

Para el caso real hay una plantilla con las mismas familias y reglas que los
informes de Giswater: `examples/plantilla_ZP01_La_Finca.yaml`.

## Datos de entrada

### 1. Observaciones de campo (`observados.csv`, formato largo)

```
sensor,type,element,timestamp,value
VN3588290,PRESSURE,VN3588290,2026-07-09 00:00:00,24.31
3926361,FLOW,3926361,2026-07-09 00:00:00,38.2
```

- `type`: `PRESSURE`, `HEAD`, `LEVEL`, `FLOW` o `VELOCITY` (también acepta `PRESION` y `CAUDAL`).
- `element`: ID del nodo (presión) o de la línea (caudal) en el INP.
- El caudal va en las unidades del INP. Por defecto se compara en valor absoluto.

También se admite el formato ancho (`format: wide`): una columna por sensor y un
`sensors_file` con el tipo y el elemento de cada sensor.

`sensores.csv` (opcional) permite dar a cada sensor un peso (`weight`) y una
incertidumbre (`sigma`, en unidades del sensor).

### 2. Ajustes (*settings*)

Cada bloque está comentado en `examples/caso_sintetico/config.yaml`:

- `groups`: las familias. Se seleccionan por `tag`, `link_type`, `ids_file`,
  `id_regex`, rangos de `roughness`, `diameter`, …
- `dmas`: sectores (CSV nodo,sector, `[TAGS]` o selección).
- `losses`: método, exponente, reparto, tolerancia y **objetivo por sector**.
- `hydraulic`: parámetros con su rango (`bounds`), escala logarítmica, objetivos,
  métrica (`rmse`/`mae`/`huber`/`rme`), opciones de NSGA-II y búsqueda local.

### 3. Objetivos (*targets*) y aceptación

```yaml
acceptance:
  - {name: CAUDAL,  kind: FLOW,     error: relative, threshold: 15, timesteps_pct: 75, points_pct: 100}
  - {name: PRESION, kind: PRESSURE, error: absolute, threshold: 5,  timesteps_pct: 80, points_pct: 80}
```

Un sensor cumple si al menos `timesteps_pct` % de sus instantes tienen un error
menor o igual que `threshold`. La regla pasa si cumplen al menos `points_pct` % de
los sensores. Es el mismo criterio que usa Giswater.

## Resultados (`output_dir`)

| Fichero | Contenido |
|---|---|
| `*_CALIBRADO.inp` | modelo final: rugosidades, válvulas, emisores y patrones ajustados |
| `informe_calibracion.html` | informe con gráficos, reglas, Pareto, sensibilidad, series y objetos modificados |
| `objetos_modificados.csv` | feature_id, tipo, parámetro, valor calibrado y valor original (como en Giswater) |
| `parametros_calibrados.csv` | valor, IC95, sensibilidad, si quedó congelado o en el límite del rango |
| `frente_pareto.csv` | todas las soluciones del frente, con las reglas que cumple cada una |
| `metricas_por_sensor.csv` | RMSE, MAE, sesgo, RME %, NSE, KGE y R², en calibración y en validación |
| `series_observado_vs_simulado.csv` | series observadas, del modelo original y del calibrado |
| `emisores_por_nudo.csv`, `resumen_perdidas_dma.csv`, `patrones_original_vs_ajustado.csv`, `balance_temporal_perdidas.csv` | resultados de la calibración de pérdidas |

## Algoritmos

**Hidráulica**
1. Se simula el modelo original y se calculan sus métricas.
2. Cribado de sensibilidad con el jacobiano. Los parámetros con CSS relativa menor
   que `freeze_below` quedan fijos en su valor inicial.
3. NSGA-II con un objetivo por tipo de sensor. Población inicial por hipercubo
   latino más el modelo original, cruce SBX, mutación polinómica, dominancia con
   restricciones (una simulación que no converge cuenta como no factible) y parada
   cuando el hipervolumen deja de mejorar.
4. Se evalúan las reglas de aceptación en cada solución del frente y se elige la
   de compromiso entre las que más reglas cumplen.
5. Refinamiento por mínimos cuadrados con límites, con el jacobiano calculado en
   paralelo. Sólo se acepta si mejora.
6. Diagnóstico: IC95, correlaciones y sesgo por sensor. Un sesgo constante suele
   indicar una cota mal asignada, no una rugosidad mal calibrada.

**Pérdidas**: `K_i = k_d · w_i` en cada sector, con `k_d` resuelto por secante.
El patrón se corrige en cada periodo con `m'(p) = m(p) · (Q_entrada(p) − L(p)) / C(p)`
hasta cumplir el objetivo y la tolerancia del patrón. Por defecto, el caudal de entrada
de referencia es la demanda del modelo original, que es la misma hipótesis que usa
Giswater. Si se dispone del caudalímetro, puede usarse la serie medida (`inflow_file`).

## Pruebas

```bash
python -m pytest -q tests
```

Las pruebas incluyen NSGA-II sobre ZDT1, las reglas de aceptación, el cierre del
balance de pérdidas y la recuperación de la rugosidad verdadera en el caso sintético.
