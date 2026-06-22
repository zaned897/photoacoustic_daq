"""
Voltímetro en vivo OPTIMIZADO (pyqtgraph) — strip chart en tiempo casi-real.

Rápido (pyqtgraph/OpenGL-friendly) y robusto:
  - Auto-reconexión si el USB-C cae (no se cuelga, no hay que reabrir).
  - Indicador de estado del enlace: LIVE / SIN TRIGGER / LINK DOWN.
  - Lectura numérica grande del voltaje + gráfica rodante de los últimos seg.

Protocolo: [0xA5][0x5A][hi][lo], 10-bit, 115200 baud (firmware voltímetro /
etapas 3-5 del bring-up).

    pipenv run python mac_test/voltmeter_live.py
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
SYNC = b"\xA5\x5A"
LSB_VOLTS = 10.0 / 1024          # frontend ±5 V, 10 bits → 9.77 mV/LSB
MID = 512
WINDOW_S = 20.0                  # ventana visible de la gráfica
POLL_MS = 30                     # refresco (~33 FPS)


def code_to_volts(code: float) -> float:
    return (code - MID) * LSB_VOLTS


class Voltmeter(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DAQ — Voltímetro en vivo")
        self.resize(1000, 560)

        layout = QtWidgets.QVBoxLayout(self)

        # Cabecera: estado del enlace + lectura numérica grande
        header = QtWidgets.QHBoxLayout()
        self.status = QtWidgets.QLabel("○ conectando…")
        self.status.setStyleSheet("font-size:16px; font-weight:bold;")
        self.readout = QtWidgets.QLabel("--- V")
        self.readout.setStyleSheet("font-size:40px; font-weight:bold; color:#00d0ff;")
        self.readout.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        header.addWidget(self.status)
        header.addStretch()
        header.addWidget(self.readout)
        layout.addLayout(header)

        # Gráfica
        pg.setConfigOptions(antialias=True)
        self.plot = pg.PlotWidget()
        self.plot.setBackground("#0b0f14")
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("left", "Voltaje de entrada", units="V")
        self.plot.setLabel("bottom", "Tiempo", units="s")
        self.plot.setYRange(-5.2, 5.2)
        self.curve = self.plot.plot(pen=pg.mkPen("#00d0ff", width=2))
        layout.addWidget(self.plot)

        # Estado de datos
        maxlen = int(WINDOW_S / (POLL_MS / 1000)) * 40
        self.t = deque(maxlen=maxlen)
        self.v = deque(maxlen=maxlen)
        self.t0 = time.time()
        self.buf = bytearray()
        self.ser = None
        self.silent_since = None

        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update)
        self.timer.start(POLL_MS)

    def _open(self):
        try:
            port = find_uart_port(verbose=False)
            s = serial.Serial(port, BAUD, timeout=0, exclusive=True)
            s.reset_input_buffer()
            self.ser = s
            self._set_status(f"● conectado: {port}", "#4ade80")
        except (serial.SerialException, OSError):
            self.ser = None

    def _set_status(self, text, color):
        self.status.setText(text)
        self.status.setStyleSheet(f"font-size:16px; font-weight:bold; color:{color};")

    def update(self):
        if self.ser is None:
            self._set_status("✗ LINK DOWN — reconectando…", "#f87171")
            self._open()
            return

        try:
            n = self.ser.in_waiting
            if n:
                self.buf.extend(self.ser.read(n))
        except (OSError, serial.SerialException):
            self._set_status("✗ LINK DOWN — reconectando…", "#f87171")
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None
            self.buf.clear()
            return

        got = False
        while True:
            i = self.buf.find(SYNC)
            if i < 0:
                del self.buf[:-1]
                break
            if len(self.buf) < i + 4:
                del self.buf[:i]
                break
            hi, lo = self.buf[i + 2], self.buf[i + 3]
            del self.buf[: i + 4]
            if hi <= 0x03:
                self.t.append(time.time() - self.t0)
                self.v.append(code_to_volts((hi << 8) | lo))
                got = True

        if got:
            self.silent_since = None
            tv = np.fromiter(self.t, dtype=float)
            vv = np.fromiter(self.v, dtype=float)
            tmax = tv[-1]
            mask = tv >= tmax - WINDOW_S
            self.curve.setData(tv[mask], vv[mask])
            self.plot.setXRange(max(0, tmax - WINDOW_S), max(WINDOW_S, tmax))
            self._set_status("● LIVE", "#4ade80")
            self.readout.setText(f"{vv[-1]:+.3f} V")
        else:
            if self.silent_since is None:
                self.silent_since = time.time()
            dt = time.time() - self.silent_since
            self._set_status(f"○ SIN TRIGGER ({dt:.0f}s)", "#facc15")


def main():
    app = QtWidgets.QApplication(sys.argv)
    w = Voltmeter()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
