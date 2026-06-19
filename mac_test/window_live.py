"""
Visualizador de VENTANA en vivo (pyqtgraph) — captura en ráfaga por trigger.

Para el firmware de la etapa 6 (bringup/stage6_burst): cada trigger envía una
ventana de 270 muestras @ 27 MSPS (10 µs). Muestra:

  - Panel superior: ventana completa (10 µs), forma de onda cruda.
  - Panel inferior: ZOOM al inicio (0–2 µs, donde vive el evento ~350 ns) con
    promedio coherente de las últimas N ventanas — saca señales de pocos LSB
    (tu pulso ~150 mV ≈ 15 códigos) del ruido (mejora √N).

Robusto: auto-reconexión + estado de enlace (LIVE / SIN TRIGGER / LINK DOWN).
Frame: [0xAA 0x55 0xAA 0x55] + 270×uint16 LE (10-bit).

    pipenv run python mac_test/window_live.py
"""

import os
import sys
import time
from collections import deque

import numpy as np
import serial

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from serial_helper import find_uart_port  # noqa: E402

import pyqtgraph as pg  # noqa: E402
from pyqtgraph.Qt import QtCore, QtWidgets  # noqa: E402

BAUD = 115_200
HDR = b"\xAA\x55\xAA\x55"
N = 270
FRAME_BYTES = N * 2
FS_MHZ = 27.0
PERIOD_US = 1.0 / FS_MHZ
TIME_US = np.arange(N) * PERIOD_US        # 0..~10 µs
ZOOM_US = 2.0
ZOOM_N = int(ZOOM_US / PERIOD_US)         # ~54 muestras
AVG_N = 32
POLL_MS = 20
LSB_VOLTS = 10.0 / 1024   # frontend ±5 V, 10 bits → 9.77 mV/LSB
MID = 512                 # 0 V ≈ media escala


class WindowView(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DAQ — Ventana de captura en vivo")
        self.resize(1000, 720)
        lay = QtWidgets.QVBoxLayout(self)

        header = QtWidgets.QHBoxLayout()
        self.status = QtWidgets.QLabel("○ conectando…")
        self.status.setStyleSheet("font-size:15px; font-weight:bold;")
        self.readout = QtWidgets.QLabel("--- V")
        self.readout.setStyleSheet(
            "font-size:34px; font-weight:bold; color:#00d0ff;")
        self.readout.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        header.addWidget(self.status)
        header.addStretch()
        header.addWidget(self.readout)
        lay.addLayout(header)

        pg.setConfigOptions(antialias=True)

        self.p_full = pg.PlotWidget()
        self.p_full.setBackground("#0b0f14")
        self.p_full.showGrid(x=True, y=True, alpha=0.25)
        self.p_full.setTitle("Ventana completa (10 µs)")
        self.p_full.setLabel("left", "ADC (códigos)")
        self.p_full.setLabel("bottom", "Tiempo", units="µs")
        self.p_full.setYRange(0, 1023)
        self.c_full = self.p_full.plot(pen=pg.mkPen("#00d0ff", width=1))
        lay.addWidget(self.p_full, stretch=2)

        self.p_zoom = pg.PlotWidget()
        self.p_zoom.setBackground("#0b0f14")
        self.p_zoom.showGrid(x=True, y=True, alpha=0.25)
        self.p_zoom.setTitle(f"Zoom 0–{ZOOM_US:.0f} µs · promedio coherente ×{AVG_N}")
        self.p_zoom.setLabel("left", "ADC (códigos)")
        self.p_zoom.setLabel("bottom", "Tiempo", units="µs")
        self.c_zoom_raw = self.p_zoom.plot(pen=pg.mkPen("#475569", width=1),
                                           name="última")
        self.c_zoom_avg = self.p_zoom.plot(pen=pg.mkPen("#facc15", width=2),
                                           name="promedio")
        lay.addWidget(self.p_zoom, stretch=1)

        self.buf = bytearray()
        self.ser = None
        self.avg = deque(maxlen=AVG_N)
        self.silent_since = None

        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update)
        self.timer.start(POLL_MS)

    def _status(self, t, c):
        self.status.setText(t)
        self.status.setStyleSheet(f"font-size:15px; font-weight:bold; color:{c};")

    def _open(self):
        try:
            port = find_uart_port(verbose=False)
            s = serial.Serial(port, BAUD, timeout=0, exclusive=True)
            s.reset_input_buffer()
            self.ser = s
            self._status(f"● conectado: {port}", "#4ade80")
        except (serial.SerialException, OSError):
            self.ser = None

    def update(self):
        if self.ser is None:
            self._status("✗ LINK DOWN — reconectando…", "#f87171")
            self._open()
            return
        try:
            n = self.ser.in_waiting
            if n:
                self.buf.extend(self.ser.read(n))
        except (OSError, serial.SerialException):
            self._status("✗ LINK DOWN — reconectando…", "#f87171")
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None
            self.buf.clear()
            return

        frame = None
        total = 4 + FRAME_BYTES
        while True:
            i = self.buf.find(HDR)
            if i < 0:
                del self.buf[: -3 or len(self.buf)]
                break
            if len(self.buf) < i + total:
                del self.buf[:i]
                break
            frame = bytes(self.buf[i + 4 : i + total])
            del self.buf[: i + total]

        if frame is None:
            if self.silent_since is None:
                self.silent_since = time.time()
            dt = time.time() - self.silent_since
            self._status(f"○ SIN TRIGGER ({dt:.0f}s)", "#facc15")
            return

        self.silent_since = None
        data = (np.frombuffer(frame, dtype="<u2") & 0x03FF).astype(float)
        self.c_full.setData(TIME_US, data)

        # Voltímetro: mediana de la ventana → voltios (robusto a picos del evento)
        volts = (float(np.median(data)) - MID) * LSB_VOLTS
        self.readout.setText(f"{volts:+.3f} V")

        z = data[:ZOOM_N]
        self.avg.append(z)
        avg = np.mean(self.avg, axis=0)
        self.c_zoom_raw.setData(TIME_US[:ZOOM_N], z)
        self.c_zoom_avg.setData(TIME_US[:ZOOM_N], avg)
        # autoescala fina alrededor del promedio (señal de pocos códigos)
        c = float(avg.mean())
        span = max(8.0, float(avg.max() - avg.min()) * 1.5 + 4)
        self.p_zoom.setYRange(c - span, c + span)
        self._status(f"● LIVE  ventana  min/max={int(data.min())}/{int(data.max())}",
                     "#4ade80")


def main():
    app = QtWidgets.QApplication(sys.argv)
    w = WindowView()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
