# Photoacoustic DAQ — Tang Nano 9K

Sistema de adquisición de datos para fotoacústica pulsada. Captura señales
acústicas post-láser con un FPGA Tang Nano 9K, las transmite a Python via
UART a 3 Mbaud y las visualiza en tiempo casi-real.

---

## Arquitectura del sistema

```
Raspberry Pi ──► trigger_rpi  (pin 77) ─┐
Botón S2     ──► trigger_manual (pin 3) ─┤ OR lógico
                                         │
                              ┌──────────▼──────────┐
              ADC (8-bit) ───►│   FPGA Tang Nano 9K  │──► UART 3 Mbaud ──► Python
              D4–D11          │   27 MHz, GW1NR-9C   │
                              └─────────────────────-┘
                                         │
                              Láser driver (10–30 ns)
                                         │
                                   Muestra / tejido
                                         │
                              Sensor ultrasónico 2 MHz
```

---

## Parámetros operacionales

| Parámetro | Valor |
|---|---|
| Reloj FPGA | 27 MHz |
| Frecuencia de muestreo ADC | 27 MSPS |
| Período de muestreo | 37 ns |
| Muestras por ráfaga (`BURST_SIZE`) | 1350 |
| Ventana de captura | 50 µs |
| UART baud rate | 3 Mbaud (8N1) |
| Tiempo TX por ráfaga | 4.5 ms |
| Tasa efectiva de captura | ≈ 220 ráfagas/s |
| Frecuencia de trigger RPi | 5 kHz (período 200 µs) |

---

## Análisis técnico: qué puede y qué no puede medirse

### Escenario A — Fotoreceptor óptico (prueba de hardware)

El circuito driver del láser genera descargas en modo avalancha de **10–30 ns**
de duración. Un fotoreceptor rápido convierte el pulso óptico a una señal eléctrica
de ancho equivalente.

#### Limitación fundamental: submuestreo

| Parámetro | Valor |
|---|---|
| Duración del pulso óptico | 10–30 ns |
| Período de muestreo ADC | 37 ns |
| Frecuencia de Nyquist para 30 ns | 2 / 30 ns = **66 MSPS mínimo** |
| Frecuencia de muestreo disponible | 27 MSPS |

A 27 MSPS el pulso queda **submuestreado**: en el mejor caso el ADC captura
1 muestra coincidente con el pico. La probabilidad de que esa muestra caiga
en el máximo del pulso es baja y no reproducible entre disparos.

```
Pulso óptico (30 ns):  ▁▁▁▁████▁▁▁▁
Muestras ADC (37 ns):  |    |    |    |
                   solo 0 o 1 punto coincide
```

**Lo que SÍ puede medirse con fotoreceptor a 27 MSPS:**
- Presencia/ausencia del disparo láser (detección de evento)
- Energía integrada del pulso (si el fotoreceptor es más lento que el pulso)
- Señales ópticas con decaimiento > 200 ns: reflexión difusa en tejido,
  fluorescencia con tiempo de vida largo

**Lo que NO puede medirse:**
- Forma de onda del pulso (rise time, fall time, estructura temporal)
- Jitter sub-nanosegundo entre disparos
- Perfil temporal de la descarga de avalancha

**Requisito para caracterizar el pulso directamente:**
≥ 100 MSPS, o osciloscopio con ancho de banda > 300 MHz.

---

### Escenario B — Sensor ultrasónico 2 MHz (operación fotoacústica)

Este es el **escenario correcto** para el que está diseñado el sistema.

El pulso láser (10–30 ns) deposita energía en el tejido. La expansión
termoelástica genera una onda acústica que viaja a ≈1500 m/s y es detectada
por el transductor piezoeléctrico de 2 MHz.

#### Por qué 27 MSPS es adecuado para el sensor de 2 MHz

| Parámetro | Valor |
|---|---|
| Frecuencia central del sensor | 2 MHz |
| Ancho de banda típico (−6 dB) | 1–4 MHz |
| Nyquist mínimo | 8 MSPS |
| Muestreo disponible | 27 MSPS |
| Factor de oversampling | **13.5×** sobre frecuencia central |

Con 13.5× de oversampling la señal acústica queda perfectamente
reconstruida y el ruido de cuantización se distribuye en una banda mucho
más amplia que la señal útil, mejorando el SNR efectivo.

#### Cobertura de profundidad (ventana 50 µs)

Con velocidad del sonido en tejido blando ≈ 1500 m/s:

| Tiempo de vuelo | Profundidad (ida y vuelta) |
|---|---|
| 1.3 µs | 1 mm |
| 6.7 µs | 5 mm |
| 13.3 µs | 10 mm |
| 26.7 µs | 20 mm |
| 50 µs | **37.5 mm** (límite de ventana) |

#### Resolución axial teórica

```
λ = v / f = 1500 m/s / 2×10⁶ Hz = 0.75 mm
Resolución axial ≈ λ/2 = 0.375 mm
```

#### ADC de 8 bits (resolución actual)

Con 8 bits: 2⁸ = 256 niveles → rango dinámico ≈ 48 dB.
Para aplicaciones de imagen fotoacústica de investigación se prefieren
12 bits (72 dB). Los pines FPGA para la expansión ya están reservados.

---

## Restricción UART y estrategia de adquisición

### Por qué no se puede capturar cada disparo de 5 kHz

```
Tiempo TX por ráfaga : 1350 bytes × 10 bits / 3 Mbaud = 4.5 ms
Período del trigger  : 200 µs (5 kHz)
Disparos perdidos    : 4.5 ms / 200 µs ≈ 22 disparos por ráfaga transmitida
Tasa efectiva        : 1000 ms / 4.5 ms ≈ 220 ráfagas/s
```

Para capturar cada uno de los 5000 disparos por segundo se necesitaría:

```
Baud requerido = 1350 bytes × 10 bits / 200 µs = 67.5 Mbaud
```

Esto está fuera del alcance de UART estándar. Se requeriría USB High Speed
(480 Mbps) o una interfaz PCIe/Ethernet para operación en tiempo real a 5 kHz.

### Por qué 220 ráfagas/s es suficiente para fotoacústica

El **signal averaging** es la técnica estándar en fotoacústica para mejorar el SNR:

```
SNR mejora ∝ √N   donde N = número de promedios
```

| Promedios (N) | Mejora SNR | Tiempo de adquisición |
|---|---|---|
| 100 | +10 dB | 0.45 s |
| 1 000 | +15 dB | 4.5 s |
| 10 000 | +20 dB | 45 s |

Para prototipos e investigación, acumular 1000 disparos en 4.5 segundos
es completamente aceptable. Los sistemas comerciales de PA promedian entre
100 y 10 000 disparos.

---

## Sistema de triggers

Ambas fuentes están activas simultáneamente con un OR lógico en el FPGA:

| Fuente | Pin FPGA | Nivel activo | Uso |
|---|---|---|---|
| Botón S2 (`trigger_manual`) | 3 | Flanco de subida (liberación) | Debug en banco, disparo único manual |
| RPi GPIO (`trigger_rpi`) | 77 | Flanco de subida del pulso | Operación automática a 5 kHz |

El FPGA ignora cualquier nuevo trigger mientras esté en estado `CAPTURE`
o `SENDING`. Python recibe ráfagas completas de 1350 bytes sin interrupciones.

---

## Mapa de pines ADC — Diseño futuro-proof para 12 bits

El ADC de 12 bits expone D0–D11. Actualmente se leen los 8 MSBs (D4–D11).
Los pines FPGA están reservados para conectar los bits restantes sin mover
ningún cable existente:

```
Header izquierdo Tang Nano 9K (de arriba hacia abajo):

 Pos  Pin  Estado       Señal
 ─────────────────────────────────────────────────────
  5   25   Conectado    D11 (MSB)          ┐
  6   26   Conectado    D9                 │
  7   27   Conectado    D7                 │  Columna
  8   28   Conectado    D5                 │  derecha
  9   29   RESERVADO    D3 → expansión 10-bit  del ADC
 10   30   RESERVADO    D1 → expansión 12-bit  │
 11   33   Conectado    CLK                ┘
 12   34   Conectado    D10                ┐
 13   40   Conectado    D8                 │
 14   35   Conectado    D6                 │  Columna
 15   41   Conectado    D4 (LSB actual)    │  izquierda
 16   42   RESERVADO    D2 → expansión 12-bit  del ADC
 17   51   RESERVADO    D0 → expansión 12-bit  │
                                           ┘
```

**Nota de instalación:** el ADC debe montarse girado 180° respecto al FPGA
para que D11 quede en la fila superior y todos los cables queden paralelos
sin cruzarse.

### Hoja de ruta de expansión

| Etapa | Bus Verilog | Pines nuevos | Cambios necesarios |
|---|---|---|---|
| **Actual (8-bit)** | `[7:0]` | — | — |
| 10-bit | `[9:0]` | 29 (D3), 30 (D1) | Cables + ampliar bus + `.cst` |
| 12-bit | `[11:0]` | 42 (D2), 51 (D0) | Cables + ampliar bus + `.cst` |

---

## Requisitos de hardware

| Componente | Especificación mínima |
|---|---|
| FPGA | Tang Nano 9K (GW1NR-9C) |
| ADC | Paralelo 8-bit, 3.3V LVCMOS, ≥ 27 MSPS |
| Sensor | Ultrasónico piezoeléctrico 2 MHz |
| Adaptador UART | **FTDI FT232R o FT4232H** (soporta 3 Mbaud) |
| Raspberry Pi | Cualquier modelo con GPIO 3.3V |

> **Advertencia:** Los adaptadores USB-UART basados en CH340G o CP2102
> típicamente no soportan 3 Mbaud. Usar FTDI.

---

## Instalación Python

```bash
pip install pyserial numpy matplotlib pyqtgraph PyQt6
```

Ajustar `SERIAL_PORT` en los scripts según el sistema operativo:

| OS | Puerto típico |
|---|---|
| macOS | `/dev/cu.usbserial-XXXX` |
| Linux | `/dev/ttyUSB0` |
| Windows | `COM3` (ver Administrador de dispositivos) |

---

## Archivos del proyecto

| Archivo | Descripción |
|---|---|
| `mac_test/top.v` | Módulo top FPGA: captura, triggers duales, UART |
| `mac_test/uart_tx.v` | Transmisor UART 8N1 parametrizable |
| `mac_test/pins.cst` | Asignación de pines Tang Nano 9K |
| `mac_test/monitor_daq.py` | Visualizador Matplotlib con métricas físicas |
| `mac_test/gui_daq.py` | Visualizador PyQt6/pyqtgraph en tiempo real |
| `mac_test/test_raw.py` | Debug: lectura cruda del puerto serial |
