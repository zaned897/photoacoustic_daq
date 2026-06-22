# Progreso del proyecto — Photoacoustic DAQ (Tang Nano 9K)

Documento de evidencia de avances. Resume las funcionalidades integradas desde
el **1 de enero de 2026**, con los commits como evidencia. Última actualización:
**junio 2026**.

---

## Resumen ejecutivo

Sistema de adquisición de datos para fotoacústica pulsada sobre FPGA Tang Nano
9K (GW1NR-9C). Captura señales post-disparo del láser, las transmite a Python
por UART y las visualiza en tiempo casi-real.

**Estado actual (validado end-to-end):**

| Parámetro | Valor actual |
|---|---|
| Muestreo ADC | **54 MSPS** (PLL ×2 desde 27 MHz) — etapa 8 |
| Resolución | **12 bits** (4096 niveles, LSB = 2.44 mV) |
| Frontend analógico | AD9226, ±5 V (10 Vpp), 0 V = código 2048 |
| Ventana de captura | 270 muestras → **5 µs** (Ts = 18.5 ns) |
| Trigger | Externo (pin 77, láser/RPi) + manual (S2) |
| UART | 115200 baud, 8N1, FT2232 a bordo |
| Visualización | pyqtgraph (ventana + zoom + histórico, mV) |

---

## Línea de tiempo de hitos

### Marzo 2026 — Primer enlace y demo (commits `9dce251`…`c2d7183`)
- Primera conexión FPGA ↔ Mac por UART; lectura cruda del puerto.
- Demo funcional de adquisición; remapeo de pines del ADC.

### Abril 2026 — Arquitectura de captura (commit `bd9fb69`)
- Trigger dual (Raspberry Pi + botón manual S2).
- Pinout "future-proof" preparado para 12 bits.
- UART a 3 Mbaud, ventana de captura de 50 µs.

### Mayo 2026 — Robustez del enlace y 10 bits (commits `2a928d6`…`7f21e56`)
- Modo auto-trigger interno; monitor en vivo con pyqtgraph.
- Subida a **10 bits** con interpolación de outliers.
- Auto-detección del puerto UART (Windows/macOS/Linux).
- Correcciones de integridad en macOS: `low_latency_mode`, lector basado en
  acumulador, throttle a 30 Hz, OpenGL desactivado.
- Fallback estable con matplotlib.
- **Protocolo:** header de sync de 4 bytes + `FW_VERSION` embebida para
  verificar en runtime qué bitstream corre.

### Junio 2026 — Trigger externo y bring-up por etapas (commits `43bc157`…)
- FW v0.3: trigger por pulso externo, auto-trigger desactivado.
- **Bring-up por etapas (`bringup/`)**: validación incremental capa por capa
  tras una jornada de depuración de un enlace UART intermitente.
- **Voltímetro ADC** validado end-to-end (lectura DC en vivo).
- **Consola robusta** con auto-reconexión y estado de enlace explícito.
- **Captura en ráfaga** (ventana fotoacústica) + visualizadores pyqtgraph.
- Subida a **54 MSPS** (PLL) y **12 bits** completos (etapas 7 y 8).

---

## Bring-up por etapas (metodología y evidencia)

Cada etapa valida **una sola capa**; no se avanza hasta que la anterior pasa.
Surgió para aislar un fallo de "0 bytes" que cruzaba PLL, triggers, niveles de
voltaje y cable a la vez. Carpetas en `bringup/`:

| Etapa | Valida | Resultado |
|---|---|---|
| 0 `stage0_heartbeat` | reloj + configuración del bitstream | ✅ |
| 1 `stage1_trigger` | detección de trigger S2/pin 77 (LEDs) | ✅ |
| 2 `stage2_uart_link` | enlace UART aislado (contador) | ✅ |
| 3 `stage3_adc_voltmeter` | lectura ADC en vivo (voltímetro) | ✅ |
| 4 `stage4_internal_trigger` | captura disparada por reloj interno 5 kHz | ✅ |
| 5 `stage5_external_trigger` | captura por pin 77 / S2 + LED de trigger | ✅ |
| 6 `stage6_burst` | ráfaga de 270 muestras @ 27 MSPS (10 µs) | ✅ |
| 7 `stage7_burst54` | ráfaga @ 54 MSPS vía PLL (18.5 ns/muestra) | ✅ |
| 8 `stage8_burst54_12b` | ráfaga @ 54 MSPS + **12 bits** | ✅ |

**Hallazgo clave:** toda la lógica del FPGA (trigger, ADC, captura, PLL) quedó
validada. La intermitencia de "0 bytes" se rastreó a un **enlace USB-C/FT2232
físico marginal**, no al diseño — mitigado en software con auto-reconexión.

---

## Visualizadores (Python, `mac_test/`)

| Archivo | Uso |
|---|---|
| `window_live.py` | **Principal**: ventana completa + zoom (región arrastrable) + histórico, ejes en mV/código, promedio coherente, auto-reconexión |
| `voltmeter_live.py` | Voltímetro strip-chart optimizado (pyqtgraph) |
| `raw_console.py` | Consola robusta con estado de enlace (LIVE/SIN TRIGGER/LINK DOWN) |
| `monitor_daq.py` / `monitor_daq_simple.py` | Monitores de ráfaga (pyqtgraph / matplotlib) |
| `serial_helper.py` | Auto-detección del puerto FT2232 |

---

## Limitaciones conocidas

1. **Submuestreo de estructura fina.** A 54 MSPS (18.5 ns/muestra) NO se resuelve
   estructura de nanosegundos (pulso de 2 ns, periodo 8 ns). Solo la envolvente
   (~100–150 ns → 6–8 muestras) y la amplitud. Para detalle de ns: osciloscopio.
2. **Sin pre-trigger.** La captura arranca EN el flanco del trigger (~55–70 ns de
   latencia). Un evento eléctrico coincidente con el trigger queda pegado al
   borde izquierdo de la ventana o se pierde. **Pendiente:** buffer circular con
   pre-trigger (etapa 9).
3. **Enlace UART/cable USB-C marginal.** Dropouts intermitentes mitigados con
   auto-reconexión en software; la causa raíz es física (cable/conector).

---

## Próximos pasos

- **Pre-trigger** (etapa 9): buffer circular para centrar el evento coincidente
  con el trigger, como un osciloscopio.
- **Preamplificador** de ultrasonido (entrada alta-Z → 50 Ω) para el sensor
  fotoacústico real (señales de mV sin cargar el piezo).
- Promediado coherente acumulado para imagen fotoacústica (SNR ∝ √N).
