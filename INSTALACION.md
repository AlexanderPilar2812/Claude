# Guía de instalación y uso (Windows)

## Opción recomendada: aplicación de escritorio (instalador)

1. Abra el repositorio en GitHub → **Releases** → **EPACAL 1.3.0 (Windows)** y
   descargue **`EPACAL-Setup-1.3.0.exe`**.
2. Ejecútelo. No pide permisos de administrador ni necesita Python. Si Windows
   SmartScreen avisa ("aplicación no reconocida"), pulse *Más información → Ejecutar
   de todas formas*: el instalador no está firmado digitalmente.
3. Abra **EPACAL** desde el escritorio o el menú Inicio.

Uso de la aplicación:

| Pestaña | Qué se hace |
|---|---|
| **1. Datos** | elegir el INP, añadir los ficheros de medidas (presión, caudal, nivel) y la carpeta de resultados; pulsar **✦ Asistente inteligente** (o **Análisis rápido**) |
| **2. Diagnóstico** | lo que el asistente encontró: sesgos, amplitud, sensores sospechosos, parámetros que no se pueden separar |
| **3. Ajustes** | % de pérdidas, parámetros a calibrar (detectados solos desde el INP; clic en ✓ para activar, doble clic para editar los límites), objetivos, tipo de calibración y reglas de aceptación |
| **4. Calibrar** | botón **Calibrar**, progreso y registro en vivo; se puede **Cancelar** |
| **5. Resultados** | frente de Pareto: elegir una solución y **Guardar INP…**; **Abrir informe** |
| **Avanzado** | configuración completa (YAML) editable y carpeta de plugins |

### Asistente inteligente: qué calibrar, decidido desde el INP y las medidas

En BGEO o Giswater el usuario elige a mano qué grupos calibrar. EPACAL puede hacerlo
solo. Al pulsar **✦ Asistente inteligente** (1–3 min):

1. **Simula el modelo original** frente a las medidas y diagnostica el error:
   - sesgo común de presión, que apunta al nivel del depósito o a una consigna;
   - un sensor con un sesgo distinto al resto, que apunta a su cota o a una válvula cerrada;
   - oscilación diaria amortiguada o exagerada, que apunta a rugosidad o válvulas;
   - error en el caudal de entrada, que apunta a la demanda o a las pérdidas.
2. **Reparte la energía disipada** (caudal × pérdida de carga) por material, válvula y
   zona. Un material que apenas disipa energía no puede corregir presiones, así que no
   se calibra. Los materiales que sí la disipan se separan en red principal (≥ 150 mm)
   y de distribución.
3. **Mide la influencia real** de cada candidato en los sensores, con el parámetro en
   sus extremos, y la compara con el ruido de medida: 0,15 m en presión, 0,05 m en
   nivel y 2 % en caudal. Solo se proponen los que superan 2 veces el ruido.
4. **Agrupa o separa** los candidatos:
   - une los grupos que producen la misma respuesta (indistinguibles);
   - divide una familia grande en zonas si las zonas responden distinto.
5. **Detecta parámetros confundidos**, como el nivel del embalse y la válvula de
   entrada cuando tienen el mismo efecto, y fija uno para que la calibración no
   «invente» valores.

Los parámetros descartados quedan en la tabla, desactivados, con su **motivo**, por
ejemplo «disipa el 0,0 % de la energía» o «efecto 0,8 veces el ruido». Se pueden
reactivar con un clic.

Resultados de validación:

- **ZP02EC**: de 26 candidatos propone 3: la TCV 3934058_n2a, la TCV de entrada y el
  nivel del depósito. Con ellos se llega al mismo error (1,12 m) que con la selección
  manual de 6 grupos.
- **Caso del tanque**: recupera los valores reales sin ninguna selección manual
  (rugosidad 1,90; TCV 18; nivel inicial 3,0 m; diámetro 24 m).

### Elegir los elementos de cada grupo a mano (como en BGEO/Giswater)

Con **Análisis rápido**, EPACAL propone grupos por catálogo, sin medir su influencia: material del catálogo,
TCV estranguladas, embalses y tanques. Para crear un grupo propio, vaya a **Ajustes →
＋ Nuevo grupo desde selección…**. Se abre un selector con el mapa de la red:

1. **Tipo de elemento**: tuberías, válvulas, nudos o tanques/embalses.
2. **Catálogo / material**: casillas con cada catálogo del INP (PVC100, PEAD150, HG50…).
3. **Atributos**: rango de diámetro y de rugosidad.
4. **Lista de IDs**: pegada o cargada desde un CSV (por ejemplo, exportado de QGIS).
5. **Mapa**: dibuje un **rectángulo** o un **lazo**, en modo *Reemplazar*, *Añadir* o
   *Quitar*. Con *Navegar* y la barra inferior puede hacer zoom y desplazarse.
6. **Parámetro del grupo**: rugosidad, consigna, cota, multiplicador…, con sus
   límites y su escala.

Los filtros se combinan entre sí (Y) y el contador muestra cuántos elementos quedan.
Con «Quitar estos elementos de otros grupos» marcado, cada elemento pertenece a un solo
grupo del mismo parámetro: por ejemplo, el PVC de una zona sale del grupo PVC general.
Para cambiar un grupo existente, selecciónelo en la tabla y pulse **Editar selección…**.

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
