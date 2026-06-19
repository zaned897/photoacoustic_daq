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
| 3 | `stage3_trigger_uart/` | Trigger → mensaje por UART | Cada disparo envía un byte conocido | sí |
| 4 | `stage4_adc_live/`  | Lectura ADC + cuantización (voltímetro) | Nivel DC correcto en el host | sí |
| 5 | `stage5_burst/`     | Captura en ventana por trigger | Ráfaga completa por evento | sí |

(Las etapas 2–5 se crean cuando lleguemos a ellas, para no adelantar diseño.)

## Criterio de avance

Una etapa "pasa" cuando el comportamiento observado coincide **exactamente**
con el esperado descrito en su propio README. Anota el resultado antes de
pasar a la siguiente.

## Reloj

Todas las etapas corren con el **cristal de 27 MHz directo** (pin 52), sin PLL.
El salto a 54 MHz vía PLL se reintroduce como una etapa propia, ya con el
enlace y la captura validados a 27 MHz.
