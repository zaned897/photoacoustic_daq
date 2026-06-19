# Etapa 1 — Detección de trigger (sin UART)

**Valida:** que las señales de trigger llegan al FPGA y que la detección de
flanco funciona. Todo es visual — no interviene el puerto serial.

## LEDs
| LED | Pin | Comportamiento |
|---|---|---|
| `led_s2`  | 10 | **Alterna** en cada pulsación de S2 (cambia al soltar) |
| `led_rpi` | 11 | **Refleja el nivel** del pin 77 (trigger externo): HIGH→encendido |
| `led_evt` | 13 | **Destella ~0.1 s** ante cada flanco de subida de cualquier trigger |

## Criterio de PASA
1. **S2:** cada vez que presionas y **sueltas** S2, `led_s2` cambia de estado
   (encendido↔apagado), un cambio limpio por pulsación.
2. **Trigger externo:** al activar el Arduino (vía divisor 1k/2.2k, GND común
   en pin 77), `led_rpi` se enciende y `led_evt` destella con cada flanco.
   - Con PWM continuo, `led_evt` destella rápido / se ve casi fijo y `led_rpi`
     se ve encendido (el pin pasa más tiempo en HIGH).

## Si falla
- **led_s2 no cambia al soltar S2:** ¿estás pulsando S2 (pin 3) y no el botón
  de reset? Verifica que la etapa 0 pasó (reloj vivo).
- **led_rpi no responde al Arduino:** revisa el divisor, el nivel real en
  pin 77 con multímetro (debe ir 0 V↔~3.3 V), y la GND común.

## Qué NO prueba
UART, ADC ni captura. Solo: las entradas de trigger se leen y los flancos se
detectan. Es el cimiento del disparo de captura.
