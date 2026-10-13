# Guía de instalación y uso (Windows)

## Opción recomendada: aplicación de escritorio (instalador)

1. Abra el repositorio en GitHub → **Releases** → **EPACAL 1.1.0 (Windows)** y
   descargue **`EPACAL-Setup-1.1.0.exe`**.
2. Ejecútelo. No pide permisos de administrador ni necesita Python. Si Windows
   SmartScreen avisa ("aplicación no reconocida"), pulse *Más información → Ejecutar
   de todas formas*: el instalador no está firmado digitalmente.
3. Abra **EPACAL** desde el escritorio o el menú Inicio.

Uso de la aplicación:

| Pestaña | Qué se hace |
|---|---|
| **1. Datos** | elegir el INP, añadir los ficheros de medidas (presión, caudal, nivel) y la carpeta de resultados; pulsar **Analizar modelo** |
| **2. Ajustes** | % de pérdidas, parámetros a calibrar (detectados solos desde el INP; clic en ✓ para activar, doble clic para editar los límites), objetivos, tipo de calibración y reglas de aceptación |
| **3. Calibrar** | botón **Calibrar**, progreso y registro en vivo; se puede **Cancelar** |
| **4. Resultados** | frente de Pareto: elegir una solución y **Guardar INP…**; **Abrir informe** |
| **Avanzado** | configuración completa (YAML) editable y carpeta de plugins |

En `Documentos\EPACAL\ejemplos` vienen ZP02EC y el caso del tanque, listos para
abrir con *Archivo → Abrir configuración*.

### Ampliar EPACAL por código (plugins)

Copie ficheros `.py` en `Documentos\EPACAL\plugins`. Como plantilla puede usar
`ejemplo_plugin.py.txt`, que viene en esa carpeta: renómbrelo a `.py`. Un plugin
puede añadir:

- **objetivos nuevos** (`register_objective`), por ejemplo el error de presión nocturno;
- **tipos de parámetro nuevos** sobre cualquier propiedad de EPANET (`register_parameter_type`);
- **acciones al terminar** (`register_result_hook`): exportar a Excel, a una base de datos…

No hace falta recompilar: se cargan al abrir la aplicación y la pestaña Avanzado
indica si se cargaron bien. Para cambios mayores, el código fuente completo está en
este repositorio. Cada cambio subido a la rama recompila el instalador automáticamente
(GitHub Actions → *Compilar instalador Windows*).

## Opción alternativa: con Python (para desarrollo)

## 1. Instalar Python (solo la primera vez)

1. Entre en <https://www.python.org/downloads/windows/> y descargue **Python 3.12**,
   con el instalador *Windows installer (64-bit)*.
2. Al ejecutar el instalador, **marque la casilla "Add python.exe to PATH"** en la
   primera pantalla y pulse *Install Now*.

No hace falta instalar EPANET aparte. La biblioteca oficial de **EPANET 2.2** viene
con el paquete `wntr`, que se instala en el paso 3.

## 2. Descargar epacal

**Opción A: ZIP, sin instalar nada más**

1. Entre en GitHub con su cuenta y abra el repositorio `alexanderpilar2812/claude`.
2. Cambie a la rama `claude/epanet-calibration-algorithm-v0cgd7`.
3. Pulse **Code → Download ZIP** y descomprímalo, por ejemplo en `C:\epacal`.

**Opción B: con Git**

```bat
git clone -b claude/epanet-calibration-algorithm-v0cgd7 https://github.com/alexanderpilar2812/claude.git C:\epacal
```

Para recibir actualizaciones más adelante: `cd C:\epacal` y `git pull`.

## 3. Instalar (solo la primera vez)

Haga doble clic en **`instalar.bat`**, dentro de la carpeta descomprimida.
Crea un entorno aislado (`.venv`) e instala los paquetes. Tarda unos minutos y al
final debe aparecer:

```
OK: EPANET 2.2 listo
```

## 4. Preparar un sector

Cree una carpeta por sector, por ejemplo `C:\epacal\casos\ZP02EC\`, con estos ficheros:

| Fichero | Contenido |
|---|---|
| `Mod_Teo_EC.inp` | INP exportado desde Giswater |
| `calibr_caudal_*.dat` | caudal observado: `id  hora  valor` (formato de calibración de EPANET) |
| `calibr_presion_*.dat` | presiones observadas, en el mismo formato |
| `config.yaml` | ajustes, objetivos y reglas (copie el de `casos\ZP02EC\` como plantilla) |

En `config.yaml` se cambia normalmente:

- `inp:` el nombre del INP.
- `observations: files:` los ficheros `.dat`.
- `groups:` las familias, por el catálogo del comentario de Giswater (`^PVC`, `^PEAD`, `^(HG|HF|HD)`…).
- `losses: … target: {type: percent, value: 17}`: el **% de pérdidas** del sector.
- `hydraulic: parameters:` qué se calibra y entre qué límites.
- `acceptance:` las **reglas de aceptación**: umbral por paso de tiempo y % de cumplimiento.

## 5. Ejecutar la calibración

- Doble clic en **`calibrar.bat`** para calibrar `casos\ZP02EC\config.yaml`.
- Para otro sector, **arrastre su `config.yaml` sobre `calibrar.bat`**.
- También desde una consola (`cmd`) en la carpeta de epacal:

```bat
.venv\Scripts\activate
python -m epacal casos\ZP02EC\config.yaml
```

ZP02EC (1 529 nudos) tarda unos 5–6 minutos con un ordenador de 4 núcleos.

## 6. Elegir la calibración en el frente de Pareto

Los resultados quedan en `casos\ZP02EC\resultados\`:

| Fichero / carpeta | Para qué sirve |
|---|---|
| `informe_calibracion.html` | informe completo: ábralo con el navegador |
| `frente_pareto.csv` | una fila por solución: errores en calibración y validación, reglas cumplidas, parámetros |
| `pareto\solucion_01.inp` … `solucion_NN.inp` | **un INP completo por solución del frente** (pérdidas del 17 % incluidas) |
| `<nombre>_CALIBRADO.inp` | la solución recomendada (★), refinada |

Cómo elegir:

1. Abra el informe y vaya a **"Frente de Pareto"**. Cada punto es una calibración:
   - **PRESSURE**: error de presión frente a los sensores (menor = mejor ajuste);
   - **CAMBIO**: cuánto se modifica el modelo original (0 = sin cambios).
2. En la tabla, compruebe que la solución cumple las reglas (**reglas calib.** y
   **reglas valid.**). Revise también que los valores (nivel del depósito,
   consignas de las TCV) sean físicamente creíbles para su red.
3. Tome el INP de esa solución en `pareto\` (o en los enlaces del informe),
   ábralo en EPANET 2.2 o impórtelo en Giswater, y úselo para sus escenarios:
   válvulas nuevas, cambios de consigna, tramos nuevos…

Criterio práctico: si una solución con **poco CAMBIO** ya cumple las reglas, es
preferible a otra con un ajuste algo mejor que modifica mucho el modelo. En
ZP02EC, la **#10** solo corrige el nivel del depósito (−1,15 m) y deja las
válvulas como estaban (error de 1,21 m). La **#1** corrige además la apertura de
las TCV (error de 1,12 m).

## Problemas frecuentes

| Mensaje | Solución |
|---|---|
| `No se encontró Python` | reinstale Python marcando "Add python.exe to PATH" |
| `No se encontró la biblioteca de EPANET` | ejecute de nuevo `instalar.bat` (instala `wntr`) |
| `nodo 'X' no existe` | un sensor del `.dat` no coincide con un ID del INP |
| tarda demasiado | en `config.yaml` baje `pop_size`/`n_gen`, o use `goal: rules` (para en cuanto se cumplen las reglas, ~2 min) |
