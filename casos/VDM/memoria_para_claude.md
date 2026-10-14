# Memoria · Depuración de campañas de medición (EPANET) · ZP-05 Vista del Mar

Pegar este texto en Claude (Ajustes → Memoria, o en las instrucciones de un Proyecto).

## Contexto
- Trabajo en calibración de modelos EPANET 2.2 de sectores de agua potable (formato ACCIONA).
- Herramienta propia: EPACAL (repositorio AlexanderPilar2812/Claude, rama claude/epanet-calibration-algorithm-v0cgd7), módulo `epacal/campaign.py`.
- Caso ZP-05 Vista del Mar: campaña 08/09–16/09/2026; ventana de calibración 12/09 00:00 – 15/09 23:45. INP de trabajo Mod_VDM_teo_v4.inp; hora 0 del INP = 10/09/2026 00:15 (ventana = horas 47,75 a 143,50).
- En casi toda la red hubo aire en las reductoras (VRP): oscilación rápida en muchos sensores.

## Regla 1 · Offset de cero (separado del suavizado)
- Offset = lectura del logger al aire antes de instalarlo (presión real 0). Corregido = dato inicial − offset.
- Tomar la meseta estable justo antes de instalar, no la bajada de ≈ −0,55 m que tienen todos los loggers a las 08:45 del 08/09 (probable traslado).
- P9 (−1,59 m), P10 (−1,63 m) y P11 (−1,32 m) tienen un cero mucho más bajo que el resto (≈ −0,27 m). Es real: P10 con offset coincide con P6 (misma zona, VRP 3949725) en el modelo teórico (diferencia −0,13 m); sin offset quedaría 1,8 m por debajo. Mantener esos offsets.
- Posible causa: equipos de presión absoluta con referencia a nivel del mar (−160 mbar ≈ presión atmosférica a ~1.500 m).
- Pendiente de revisar: el offset de P8 (y quizá P1, P4, P5) cae en la bajada de 08:45 y no en la meseta estable.

## Regla 2 · Suavizado (envolvente por oscilación rápida)
- Un día se suaviza si el salto mediano entre lecturas de 15 min supera **1 m** (un sensor estable salta 0,2–0,4 m: P2, P6, P7, P10).
- Media recortada (TRIMMEAN 50 %) del dato corregido, ventana graduada:
  - 2 h (±4 lecturas) si el salto es de 1 a 3 m → P4, P9, L1
  - 3 h (±6) si es de 3 a 6 m → P3, P5, P8, L2, L3
  - 4 h (±8) si supera 6 m → P1, P11, N_Meneses (usar solo la media horaria, tolerancia mayor)
- Tras suavizar, el salto residual debe quedar en 0,2–0,5 m, como un sensor estable.

## Otras marcas de depuración
0 válido · 1 envolvente · 2 interpolado (pico o hueco ≤ 1 h) · 3 perfil típico (hueco 1–6 h) · 4 balance de masa (nivel de tanque) · 9 excluido (sin instalar, corte, sensor fuera del agua).

## Formato de entrega (Excel ACCIONA)
- Hoja por sensor: Fecha | Dato inicial | Marca | Reemplazo | Corregido | Dato depurado | Motivo, con fórmulas (Corregido = B − offset; Depurado según la marca).
- Dos hojas de gráficos separadas:
  - «Gráficos suavizado»: corregido (con offset, azul) frente a depurado (rojo). Solo se ve el efecto del suavizado.
  - «Gráficos offset»: dato inicial (gris) frente a corregido (azul). Solo se ve el desplazamiento del cero.
- Eje Y ajustado a los datos; filas fuera de la ventana en gris (solo para la media móvil del borde).
- .dat de calibración: `id<TAB>hora<TAB>valor`, presión llevada a la cota del nudo del INP (cota GNSS), caudal en m³/h, solo marcas 0–3; Q2 excluido (lee un 26 % de más).

## Criterios de aceptación
- Presión: ±5 m en ≥ 80 % de los instantes, para ≥ 80 % de los sensores.
- Caudal: ±15 % en ≥ 75 % de los instantes, para el 100 % de los medidores.

## Estado de la calibración (ventana 12–15/09, sin tocar patrones ni rugosidades)
- VRP 3949706 consigna −5,7 m respecto a la teórica (≈ 29,3 m); VRP 3949722 25,6 m; TCV 3949364 K ≈ 2180; demanda zona VRP 3949706 ×0,79, resto del sector ×1,02; fuga Durazno como emisor C = 0,577 en el nudo 3949431; nivel inicial del tanque Durazno 2,3 m (boya 2,34 m).
- Validación a 15 min: 8 de 14 sensores de presión cumplen; caudales Q1, Q3 y Q4 cumplen.
- P4 y P9 rozan el 80 % (78 % y 76 %): de noche miden 3–4 m menos que el modelo y de día más; es un problema de amplitud del modelo, no de ruido. P1, P3 y P11 no cumplen.

## Preferencias
- Responder en español, con tablas simples y explicaciones cortas.
- Separar siempre el efecto del offset del efecto del suavizado al mostrar datos depurados.
