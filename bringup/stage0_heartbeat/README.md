# Etapa 0 — Heartbeat

**Valida:** que el bitstream se carga y el reloj de 27 MHz corre.

## Build & Flash
Abre esta carpeta en VSCode y ejecuta la toolchain (Build & Flash), o usa tu
flujo habitual apuntando a `top.v` + `heartbeat.cst`.

## Criterio de PASA
- El LED de la placa (pin 10) **parpadea a ~0.8 Hz** (~0.6 s encendido, ~0.6 s
  apagado). Ritmo tranquilo, claramente visible.

## Si falla
- **LED fijo o apagado:** el FPGA no está tomando configuración, o no llega
  el reloj. Revisa alimentación USB, que el flash terminara en `CRC: Success`,
  y haz un power-cycle.
- **Parpadeo muy rápido o muy lento:** el reloj no es 27 MHz (improbable con
  el cristal directo).

## Qué NO prueba
Nada de UART, ADC ni trigger. Es solo el cimiento: reloj + configuración.
