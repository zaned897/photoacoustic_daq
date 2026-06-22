# Bring-up por etapas — Photoacoustic DAQ

Validación incremental del FPGA. Cada etapa prueba **una sola capa** y no se
avanza hasta que la anterior pasa. Cada carpeta es autocontenida (su propio
`top.v` y `.cst`); ábrela en VSCode y haz Build & Flash desde ahí.

## Por qué así

Después de perseguir un fallo de "0 bytes" a través de PLL, triggers, niveles
de voltaje y cables a la vez, separamos las variables. Si la etapa N pasa y la
N+1 falla, el problema está exactamente en lo que la N+1 agregó.

## Etapas

| # | Carpeta | Valida | Cómo se comprueba | UART |
|---|---|---|---|---|
| 0 | `stage0_heartbeat/` | Reloj + configuración del bitstream | LED parpadea a ~0.8 Hz | no |
| 1 | `stage1_trigger/`   | Entrada de trigger + detección de flanco | S2 alterna el LED; pin 77 enciende otro LED | no |
| 2 | `stage2_uart_link/` | Enlace pin 17 → FTDI (el nudo del día) | Host recibe un contador conocido | **sí** |
| 3 | `stage3_adc_voltmeter/` | Lectura ADC en vivo (voltímetro) | Nivel DC correcto en el host | sí |
| 4 | `stage4_internal_trigger/` | Captura disparada por reloj interno 5 kHz | Lectura DC, ahora por evento | sí |
| 5 | `stage5_external_trigger/` | Captura por pin 77 / S2 | Datos solo con trigger; LED de trigger | sí |
| 6 | `stage6_burst/`     | Ráfaga de 270 muestras @ 27 MSPS (10 µs) | Forma de onda de la ventana | sí |
| 7 | `stage7_burst54/`   | Ráfaga @ 54 MSPS vía PLL (18.5 ns/muestra) | El doble de puntos sobre el evento | sí |
| 8 | `stage8_burst54_12b/` | Ráfaga @ 54 MSPS + **12 bits** (LSB 2.44 mV) | 4× resolución vertical | sí |

Todas las etapas validadas (✅). Visualizador para 6–8: `mac_test/window_live.py`
(ajusta `BITS` y `FS_MHZ` según la etapa flasheada). Pendiente: etapa 9
(pre-trigger) para centrar eventos coincidentes con el disparo.
Resumen completo: [`docs/PROGRESO.md`](../docs/PROGRESO.md).

## Criterio de avance

Una etapa "pasa" cuando el comportamiento observado coincide **exactamente**
con el esperado descrito en su propio README. Anota el resultado antes de
pasar a la siguiente.

## Reloj

Todas las etapas corren con el **cristal de 27 MHz directo** (pin 52), sin PLL.
El salto a 54 MHz vía PLL se reintroduce como una etapa propia, ya con el
enlace y la captura validados a 27 MHz.
