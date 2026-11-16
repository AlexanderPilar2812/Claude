# Herramientas RANC

Herramientas para las campañas de medición, la línea base y la modelación de los sectores del proyecto RANC.

## Estado

| # | Herramienta | Orden | Probada con |
|---|---|---|---|
| 1 | Campaña: lectura de crudos, alineación, control de calidad | `campana` | Mata de Plátano LB1 (datos reales) |
| 2 | Línea base de pérdidas | `linea_base` | Mata de Plátano LB1 (datos reales) |
| 3 | Auditor de INP | `auditar` | Red sintética con errores inyectados |
| 4 | Patrones, .dat y CSV para Giswater | `patrones` | Mata de Plátano LB1 (datos reales) |
| 5 | Evaluador de calibración | `evaluar` | Red sintética |
| 6 | Pérdidas con emisores (FAVAD) | `perdidas` | Red sintética |
| 7 | Escenarios en lote | `escenarios` | Red sintética |
| 8 | Tablas y figuras del informe | — | Pendiente |

Las herramientas 3, 5, 6 y 7 todavía no se han corrido sobre un INP real de Giswater. La red sintética
(`ejemplos/modelo/red_prueba.inp`) sigue las convenciones del proyecto, pero conviene pasarlas por La Finca
o El Carmen antes de usarlas en un entregable.

## Instalación

1. Instalar Miniconda.
2. En la carpeta del repositorio: `conda env create -f environment.yml`
3. Activar: `conda activate ranc`
4. Instalar el paquete en modo editable: `pip install -e .`
5. Comprobar: `python -m pytest` (23 pruebas; las que usan datos de un sector se omiten si la carpeta del sector no está)

Las herramientas 5, 6 y 7 necesitan `wntr`, que trae el motor de EPANET. El resto funciona sin él.

## Órdenes

```
python -m ranc campana    sector.yaml
python -m ranc linea_base sector.yaml
python -m ranc patrones   sector.yaml
python -m ranc todo       sector.yaml
python -m ranc auditar    modelo.inp --n1 1.5 --corregir
python -m ranc evaluar    modelo.inp --presion calib_presion.dat --caudal calib_caudal.dat
python -m ranc perdidas   modelo.inp --pct 0.19 --n1 1.5 --expl 901
python -m ranc escenarios modelo.inp escenarios.yaml
```

Las tres primeras leen el `sector.yaml`; las demás trabajan sobre un INP. Ninguna modifica el archivo de
entrada: las correcciones y los resultados se escriben en archivos nuevos.

## Herramienta 1 · Campaña

```
python -m ranc campana sectores/<sector>/sector.yaml
```

Deja en `sectores/<sector>/salidas/`:

- `QA_campana_<sector>.xlsx`: hallazgos, equipos, relojes, cortes, pegado, noches, serie de 15 min y gráficos.
- `serie_15min_<sector>.csv`: la serie alineada (separador `;`, decimal con coma).
- `figuras/`: los gráficos en PNG.

### Qué revisa

| Revisión | Cómo |
|---|---|
| Malla de tiempo de cada equipo | Segundo en que registra dentro del cuarto de hora; todo se interpola a 00/15/30/45 |
| Huecos y duplicados | Saltos mayores de 1,5 veces el intervalo |
| Offset de presión | Lectura con el sensor al aire antes de instalarlo y después de retirarlo |
| Datos de caudal no válidos | Fallos del equipo, negativos menores de −1 L/s, valores mayores de 5 veces el percentil 95 |
| Cortes de servicio | Presión por debajo del 20 % de su mediana (mínimo 2 mca) o caudal sin dato, en dos o más equipos |
| Relojes | Hora de caída y retorno en los cortes frente al equipo con reloj de red, y correlación de las variaciones |
| Pegado en Excel | Busca cada valor del Excel en el crudo y da el desfase por tramos de filas |
| Noches anómalas | Caudal medio 00:00–04:00 fuera del 50–150 % de la mediana |

La herramienta no suaviza. La serie de salida solo lleva offset, invalidación e interpolación a la malla, y cada valor tocado queda marcado en su columna `_marca`.

### Archivo del sector

Ver `sectores/plantilla/sector.yaml`. Campos por punto: `id`, `nombre`, `magnitud` (caudal o presion), `archivo` (uno o una lista), `rol` (entrada o salida, para caudal), `peso` (para la presión ponderada) y, opcionales, `offset_mca` y `q_max_ls` para fijarlos a mano. En `parametros` se pueden cambiar los umbrales por defecto.

### Formatos de archivo reconocidos

| Formato | Equipo | Se reconoce por |
|---|---|---|
| `fluxdiag` | Caudalímetro ultrasónico (exportación FluxDiag) | Cabecera `"sep=;"` y columna de caudal volumétrico |
| `rpf400` | Senapsis RPF400 | Cabecera `Fichero`, `Nombre Sitio` |
| `telid` | TELID 343.HTK | Bloque de información general con el número de serie; convierte mbar a mca |
| `hwm` | Nube HWM (CLOUD_ARC, Multilog) | Columnas de hora local y UTC |
| `mdy_ampm` | Registradores R | Filas `m/d/aaaa,h:mm:ss AM,valor` sin cabecera |

Para añadir un equipo, se escribe su lector en `ranc/lectores.py` y se registra en `LECTORES`.

## Herramienta 2 · Línea base

Lee el bloque `linea_base` del `sector.yaml`. Calcula el MNF de cada noche con media móvil de 1 h en la ventana
00:00–04:00, el percentil semanal, pérdidas nocturnas, UBL, UARL e ILI. Los días para el MNF y para el caudal
medio se eligen por separado, y en los cortes de servicio el caudal cuenta como 0 para el volumen diario.
Da dos lecturas del ILI: la de la fórmula IWA con factor noche-día y la del criterio del `Modelo_CMN`
(CARL = pérdidas nocturnas, UARL × PFC). Si se indica `excel_cmn`, contrasta día a día y marca lo que difiere
más del 2 %. El Excel de salida lleva las fórmulas vivas en la hoja Resumen.

## Herramienta 3 · Auditor de INP

Revisa codificación, opciones y tiempos del protocolo de exportación, cotas en 0 o con saltos frente a los
vecinos, suma de pesos, demandas en `[DEMANDS]` frente a la columna de `[JUNCTIONS]`, patrones más cortos que
la simulación, diámetros de válvula de 0,001 o 999 mm, reguladoras anuladas por `OPEN`, elementos de `[STATUS]`
que no se exportaron, nodos sin conexión con una fuente y reglas en `[RULES]`.
Con `--corregir` escribe `<nombre>_corregido.inp` con los arreglos que no admiten duda: texto en ASCII,
`DEMAND MULTIPLIER` en 1, diámetro de válvula tomado de la tubería adyacente y `OPEN` retirado de las reguladoras.

## Herramienta 4 · Patrones y .dat

Lee el bloque `modelo` del `sector.yaml`. Genera el patrón de demanda absoluto en m³/h desde t = 0, el CSV para
Giswater (cabecera `pattern_id;descripción;expl_id` y filas de 18 factores), el bloque para `[PATTERNS]`, los
`.dat` del calibrador y el Excel de cálculo. Los huecos se rellenan con el perfil del día tipo (laborable o fin
de semana) y cada valor lleva su origen. Para tanques: `tanques: [{punto, id_patron, cota_sensor, forma, cota_solera}]`,
con `forma: absoluta` (carga unitaria) o `multiplicador` (siete decimales sobre la cota de solera).

## Herramienta 5 · Evaluador de calibración

Corre el INP y lo compara con los `.dat`. Por punto da sesgo, MAE, RMSE, NSE, correlación, pasos dentro de
tolerancia y amplitud día-noche medida y simulada. Un punto cumple con el 80 % de sus pasos dentro de ±5 m
(±15 % en caudal, ±2 m en nivel) y el modelo cumple con el 80 % de los puntos; los dos umbrales son parámetros.
Avisa de sesgos sistemáticos y de que el ajuste de caudal es circular si el patrón sale de la misma medición.

## Herramienta 6 · Pérdidas con emisores

Reparte emisores por nodo en proporción a la longitud de tubería, fija el exponente en N1 y ajusta la magnitud
hasta que el volumen fugado en la ventana sea el objetivo (`--pct` sobre el volumen de entrada del sector, o
`--m3`). Resta la fuga al patrón de demanda, así que el caudal de entrada del modelo no cambia. Entrega
`<nombre>_fugas.inp`, el CSV del patrón sin fugas para Giswater y un Excel con emisores, patrón y convergencia.
Supone pesos que suman 1 y patrón absoluto.

## Herramienta 7 · Escenarios

Ver `ejemplos/modelo/escenarios.yaml`. Corre el estado actual en los instantes de caudal mínimo, medio y punta,
un escenario por hidrante (por defecto 32 L/s sobre el caudal máximo diario), uno por avería y el horizonte de
planeación. Para cada caso da presión mínima, media y máxima, nodos bajo 15 mca y sobre 50/70 mca, velocidad
máxima y los 15 nodos más críticos. Los escenarios de incendio van sobre el modelo calibrado en estado actual.

## Datos de los sectores

El repositorio es público y no lleva datos del proyecto. Cada sector vive en su carpeta dentro de `sectores/`,
que está excluida por `.gitignore`. Para empezar uno nuevo se copia `sectores/plantilla`.
