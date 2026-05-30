"""
PHOTOACOUSTIC DAQ — Monitor en tiempo real con PyQtGraph.

Reescrito desde matplotlib para alcanzar >100 FPS reales con ráfagas de
1350 muestras @ 27 MSPS. PyQtGraph usa Qt + OpenGL bajo el capó.

Arquitectura de threading:
- SerialReader (QThread): bloquea en ser.read(), drena buffer apilado,
  emite un Signal con la última muestra completa.
- DAQMonitor (UI thread): solo recibe el Signal y redibuja. Nunca toca
  el puerto serie directamente → cero contención GIL/UI ↔ I/O.

Controles del gráfico (nativos de PyQtGraph):
    - Rueda del mouse        : zoom
    - Click derecho + arrastrar : zoom rectangular
    - Click izq. + arrastrar : pan
    - Click derecho          : menú (autorrange, exportar, etc.)
    - Tecla 'A'              : autorrange
"""

import os
import sys
import time

import numpy as np
import pyqtgraph as pg
import serial
from PySide6 import QtCore, QtWidgets

# Permite importar serial_helper cuando el script se ejecuta directamente.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from serial_helper import find_uart_port  # noqa: E402

# --- Configuración ---
SERIAL_PORT = find_uart_port()
BAUD_RATE = 3_000_000
SAMPLE_SIZE = 1350  # muestras por ráfaga
FS_MHZ = 27.0
BIT_DEPTH = 10  # Fase 2: D11..D2 del AD9226
BYTES_PER_SAMPLE = 2  # 10 bits empaquetados en uint16 LE
FRAME_BYTES = SAMPLE_SIZE * BYTES_PER_SAMPLE  # = 2700 bytes/ráfaga
FRAME_HEADER = b"\xAA\x55\xAA\x55"  # preámbulo de sync — debe coincidir con top.v
VERSION_BYTES = 2                    # 2 bytes de versión big-endian tras el header
EXPECTED_FW_VERSION = 0x0002          # firmware que esperamos correr (bump con cada release)
C_TISSUE = 1540.0
F_SENSOR = 2.0

# --- Derivados ---
PERIOD_US = 1.0 / FS_MHZ
TIME_AXIS = np.linspace(0, SAMPLE_SIZE * PERIOD_US, SAMPLE_SIZE)
DIST_AXIS_CM = TIME_AXIS * 1e-6 * C_TISSUE * 100
LAMBDA_MM = (C_TISSUE / (F_SENSOR * 1e6)) * 1000
MAX_DEPTH_CM = DIST_AXIS_CM[-1]
ADC_MAX = (1 << BIT_DEPTH) - 1  # 1023 para 10 bits


class SerialReader(QtCore.QThread):
    """Lee ráfagas del puerto serie en un thread dedicado.

    Emite `frame_ready` con la muestra más reciente cuando completa
    SAMPLE_SIZE bytes. Drena automáticamente cualquier backlog para que
    la UI nunca trabaje con datos viejos.

    Si el puerto se pierde (USB desconectado), emite `error` con el
    mensaje y termina el thread.
    """

    frame_ready = QtCore.Signal(np.ndarray)
    error = QtCore.Signal(str)

    def __init__(
        self, ser: serial.Serial, parent: QtCore.QObject | None = None
    ) -> None:
        super().__init__(parent)
        self.ser = ser
        self._running = True

    def run(self) -> None:
        # Patrón acumulador + búsqueda de header: lee lo que esté disponible
        # y busca el preámbulo FRAME_HEADER (0xAA 0x55 0xAA 0x55) que el FPGA
        # inserta antes de cada ráfaga. Cualquier byte basura/desfasado se
        # descarta automáticamente al no coincidir con el patrón.
        MIN_EMIT_INTERVAL = 1.0 / 30  # 30 Hz max emits
        HDR_LEN = len(FRAME_HEADER)
        FRAME_TOTAL = HDR_LEN + VERSION_BYTES + FRAME_BYTES
        MAX_BUFFER = 100_000  # cap de seguridad: si no aparece header, recortar
        last_emit = 0.0
        buffer = bytearray()
        version_announced = False
        print(f"[reader] thread iniciado, buscando header {FRAME_HEADER.hex()}")
        loop_count = 0
        while self._running:
            try:
                pending = self.ser.in_waiting
                to_read = pending if pending > 0 else 1
                chunk = self.ser.read(to_read)
                if chunk:
                    buffer.extend(chunk)

                loop_count += 1
                if loop_count % 5000 == 0:
                    print(
                        f"[reader] alive: buffer={len(buffer)} B, "
                        f"in_waiting={self.ser.in_waiting}"
                    )

                # Cap de seguridad: si el header nunca aparece, no acumular sin fin.
                if len(buffer) > MAX_BUFFER:
                    # Mantener solo los últimos HDR_LEN-1 bytes por si el
                    # header está parcial al final.
                    del buffer[: -(HDR_LEN - 1)]

                # Procesa todos los frames disponibles
                while True:
                    idx = buffer.find(FRAME_HEADER)
                    if idx < 0:
                        break  # no hay header completo aún
                    # Descarta basura previa al header
                    if idx > 0:
                        del buffer[:idx]
                    # ¿Hay header + version + frame completo?
                    if len(buffer) < FRAME_TOTAL:
                        break
                    # Extrae versión y frame
                    ver_hi = buffer[HDR_LEN]
                    ver_lo = buffer[HDR_LEN + 1]
                    fw_version = (ver_hi << 8) | ver_lo
                    data_start = HDR_LEN + VERSION_BYTES
                    frame_bytes = bytes(buffer[data_start : data_start + FRAME_BYTES])
                    del buffer[:FRAME_TOTAL]

                    if not version_announced:
                        match = "OK" if fw_version == EXPECTED_FW_VERSION else "MISMATCH"
                        print(
                            f"[reader] FW_VERSION recibido: 0x{fw_version:04X} "
                            f"(esperado 0x{EXPECTED_FW_VERSION:04X}) → {match}"
                        )
                        version_announced = True

                    now = time.monotonic()
                    if now - last_emit < MIN_EMIT_INTERVAL:
                        continue
                    last_emit = now

                    data = np.frombuffer(frame_bytes, dtype="<u2") & 0x03FF
                    self.frame_ready.emit(data)
            except serial.SerialException as e:
                self.error.emit(str(e))
                break
            except Exception as e:  # noqa: BLE001
                self.error.emit(f"Unexpected: {e}")
                break

    def stop(self) -> None:
        self._running = False


class DAQMonitor(QtWidgets.QMainWindow):
    def __init__(self, ser: serial.Serial) -> None:
        super().__init__()
        self.ser = ser
        self.last_time = time.time()
        self.fps_smoothed = 0.0
        self.frame_count = 0

        self.setWindowTitle("Photoacoustic DAQ — Live Monitor (PyQtGraph)")
        self.resize(1300, 720)

        # --- Layout principal ---
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QHBoxLayout(central)

        # --- Panel izquierdo: gráfico ---
        # OpenGL deshabilitado en macOS (Apple deprecó OpenGL y el backend
        # de PyQtGraph se cuelga en Sequoia/Sonoma). En Windows/Linux
        # OpenGL sí acelera bien.
        use_opengl = sys.platform != "darwin"
        pg.setConfigOptions(antialias=True, useOpenGL=use_opengl)
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground("#0f172a")
        self.plot_widget.showGrid(x=True, y=True, alpha=0.2)
        self.plot_widget.setLabel("bottom", "Time of Flight", units="µs")
        self.plot_widget.setLabel("left", "Amplitude (ADC LSB)")
        self.plot_widget.setYRange(-5, ADC_MAX + 5)
        self.plot_widget.setXRange(0, TIME_AXIS[-1])

        # Eje superior con profundidad
        top_axis = pg.AxisItem("top")
        top_axis.setLabel("Depth in Tissue", units="cm", color="#f39c12")
        top_axis.setScale(C_TISSUE * 1e-4)  # µs → cm
        self.plot_widget.setAxisItems({"top": top_axis})

        self.curve = self.plot_widget.plot(
            TIME_AXIS,
            np.zeros(SAMPLE_SIZE),
            pen=pg.mkPen("#00f2ff", width=1),
        )
        layout.addWidget(self.plot_widget, stretch=4)

        # --- Panel derecho: métricas ---
        right = QtWidgets.QVBoxLayout()
        right.setSpacing(8)

        title = QtWidgets.QLabel("PHOTOACOUSTIC DAQ")
        title.setStyleSheet("color:#4ade80; font-size:18px; font-weight:bold;")
        right.addWidget(title)

        physics = QtWidgets.QLabel(
            f"Sound Speed : {C_TISSUE:.0f} m/s\n"
            f"Transducer  : {F_SENSOR} MHz\n"
            f"Wavelength  : {LAMBDA_MM:.3f} mm\n"
            f"Theo. Res.  : {LAMBDA_MM/2:.3f} mm\n"
            f"Max Depth   : {MAX_DEPTH_CM:.2f} cm\n"
            f"Time Window : {TIME_AXIS[-1]:.1f} µs\n"
            f"Sampling    : {FS_MHZ} MSPS\n"
            f"Bit Depth   : {BIT_DEPTH} bits"
        )
        physics.setStyleSheet(
            "color:#bdc3c7; font-family:Consolas,monospace; font-size:11px; "
            "background:#1e293b; padding:10px; border-radius:6px;"
        )
        right.addWidget(physics)

        right.addSpacing(15)
        status_title = QtWidgets.QLabel("REAL-TIME STATUS")
        status_title.setStyleSheet("color:#4ade80; font-size:13px; font-weight:bold;")
        right.addWidget(status_title)

        self.lbl_fps = QtWidgets.QLabel("FPS:        --")
        self.lbl_rate = QtWidgets.QLabel("Throughput: -- kB/s")
        self.lbl_minmax = QtWidgets.QLabel("Min/Max:    --/--")
        self.lbl_mean = QtWidgets.QLabel("Mean:       --")
        self.lbl_status = QtWidgets.QLabel("Signal:     WAITING")

        for lbl in (
            self.lbl_fps,
            self.lbl_rate,
            self.lbl_minmax,
            self.lbl_mean,
            self.lbl_status,
        ):
            lbl.setStyleSheet(
                "color:white; font-family:Consolas,monospace; font-size:12px;"
            )
            right.addWidget(lbl)

        right.addStretch()

        right_widget = QtWidgets.QWidget()
        right_widget.setLayout(right)
        right_widget.setFixedWidth(280)
        layout.addWidget(right_widget)

        # --- Thread de I/O ---
        self.reader = SerialReader(self.ser)
        self.reader.frame_ready.connect(self.on_frame)
        self.reader.error.connect(self.on_error)
        self.reader.start(QtCore.QThread.Priority.HighPriority)

    @QtCore.Slot(np.ndarray)
    def on_frame(self, data: np.ndarray) -> None:
        # ─── Workaround: reemplazar outliers exactos a 0 con interp. lineal ───
        # Hay un sample fijo (típicamente índice 64) que sale 0 consistentemente.
        # Causa pendiente de investigar (FPGA SENDING o FTDI USB framing).
        # Como es 1/1350 muestras (0.07%) y siempre en el mismo lugar, lo
        # tapamos para no contaminar la visualización ni las stats.
        zero_idx = np.where(data == 0)[0]
        if len(zero_idx) > 0:
            # Diagnóstico: imprime los índices afectados cada 50 frames
            if self.frame_count % 50 == 0:
                print(f"  ↳ ceros en índices: {zero_idx.tolist()}")
            # Interpolar: data[i] <- promedio de vecinos válidos
            data = data.copy()  # frombuffer da array read-only
            for i in zero_idx:
                left = data[i - 1] if i > 0 else data[i + 1]
                right = data[i + 1] if i < len(data) - 1 else data[i - 1]
                data[i] = (int(left) + int(right)) // 2

        self.curve.setData(TIME_AXIS, data)

        now = time.time()
        dt = now - self.last_time
        self.last_time = now
        if dt > 0:
            fps = 1.0 / dt
            self.fps_smoothed = 0.9 * self.fps_smoothed + 0.1 * fps
            kbps = SAMPLE_SIZE * self.fps_smoothed / 1000.0
            self.lbl_fps.setText(f"FPS:        {self.fps_smoothed:5.1f}")
            self.lbl_rate.setText(f"Throughput: {kbps:5.1f} kB/s")

        d_min = int(data.min())
        d_max = int(data.max())
        d_mean = float(data.mean())
        d_pp = d_max - d_min
        d_std = float(data.std())

        self.lbl_minmax.setText(f"Min/Max:    {d_min}/{d_max}")
        self.lbl_mean.setText(f"Mean:       {d_mean:6.2f}")

        # Log a consola throttled: 1 de cada CONSOLE_EVERY frames.
        # A 50 FPS imprimiendo cada frame, la consola de Windows bloquea
        # el event loop de Qt → congelamientos periódicos en la gráfica.
        self.frame_count += 1
        CONSOLE_EVERY = 10
        if self.frame_count % CONSOLE_EVERY == 0:
            print(
                f"#{self.frame_count:5d}  "
                f"min={d_min:3d}  max={d_max:3d}  pp={d_pp:3d}  "
                f"mean={d_mean:6.2f}  std={d_std:5.2f}  "
                f"fps={self.fps_smoothed:5.1f}"
            )

        if d_max - d_min < 5:
            self.lbl_status.setText("Signal:     [!] FLAT / NO SIGNAL")
            self.lbl_status.setStyleSheet(
                "color:#ef4444; font-family:Consolas,monospace; "
                "font-size:12px; font-weight:bold;"
            )
        elif d_max >= ADC_MAX - 1 or d_min <= 1:
            self.lbl_status.setText("Signal:     [!] SATURATED")
            self.lbl_status.setStyleSheet(
                "color:#f59e0b; font-family:Consolas,monospace; "
                "font-size:12px; font-weight:bold;"
            )
        else:
            self.lbl_status.setText("Signal:     [OK] VALID")
            self.lbl_status.setStyleSheet(
                "color:#4ade80; font-family:Consolas,monospace; "
                "font-size:12px; font-weight:bold;"
            )

    @QtCore.Slot(str)
    def on_error(self, message: str) -> None:
        self.lbl_status.setText("Signal:     [X] USB DESCONECTADO")
        self.lbl_status.setStyleSheet(
            "color:#ef4444; font-family:Consolas,monospace; "
            "font-size:12px; font-weight:bold;"
        )
        print(
            f"\nUSB perdido: {message}\nReconecta el Tang Nano y reinicia el programa."
        )

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        # Pedimos al thread que termine y esperamos a que cierre limpio
        # antes de cerrar el puerto serie (evita race con un read() en vuelo).
        self.reader.stop()
        self.reader.wait(1000)  # ms
        if self.ser.is_open:
            self.ser.close()
        super().closeEvent(event)


def main() -> None:
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.2)
        # Bajar el latency timer del FTDI (default 16 ms en macOS/Linux).
        # Sin esto el driver agrupa datos en chunks grandes y desalinea pares.
        try:
            ser.set_low_latency_mode(True)
        except (NotImplementedError, OSError):
            pass  # Windows ya respeta la config del Device Manager
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        print(f"Conectado a: {SERIAL_PORT}")
    except serial.SerialException as error:
        print(f"ERROR de puerto: {error}")
        sys.exit(1)

    app = QtWidgets.QApplication(sys.argv)
    window = DAQMonitor(ser)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
