"""
Visualizador de VENTANA en vivo (pyqtgraph) — réplica tipo osciloscopio.

Firmware etapa 6 (bringup/stage6_burst): cada trigger envía una ventana de 270
muestras @ 27 MSPS (10 µs). Tres paneles:

  1. VENTANA COMPLETA (10 µs) en mV, con una REGIÓN arrastrable que define el
     zoom (overview + detalle, estilo osciloscopio).
  2. ZOOM en vivo de la región seleccionada: muestras discretas (símbolos),
     eje X doble (tiempo µs abajo / muestra # arriba), eje Y doble (mV izq /
     código der, con offset), promedio coherente ×N para señales pequeñas.
  3. HISTÓRICO: min/max (mV) de cada ventana en el tiempo — estabilidad del pulso.

Niveles: frontend ±5 V, 0 V = código 512, LSB = 9.766 mV.
Robusto: auto-reconexión + estado de enlace, sin parpadeo (histéresis).

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
FS_MHZ = 54.0   # etapa 7 (PLL). Para la etapa 6 (27 MSPS) poner 27.0.
PERIOD_US = 1.0 / FS_MHZ
TS_NS = PERIOD_US * 1000.0                 # ≈37.0 ns @ 27 MSPS
TIME_US = np.arange(N) * PERIOD_US         # 0..~10 µs
BITS = 12                                  # etapa 8 = 12 bits; etapa 6/7 = 10
MASK = (1 << BITS) - 1                      # 0x0FFF (12b) / 0x03FF (10b)
MID = 1 << (BITS - 1)                       # 0 V ≈ media escala (2048 / 512)
MV_PER_LSB = (10.0 / (1 << BITS)) * 1000.0  # 2.44 mV (12b) / 9.77 mV (10b)
ZOOM_US0 = 0.3                             # región de zoom inicial (0..300 ns):
                                           # enfoca la envolvente del pulso (~104 ns)
AVG_N = 32
POLL_MS = 20
HIST_S = 30.0                              # ventana del histórico
LIVE_HOLD_S = 0.7                          # histéresis del estado LIVE


def code_to_mv(code):
    return (np.asarray(code, dtype=float) - MID) * MV_PER_LSB


class CodeAxis(pg.AxisItem):
    """Eje Y derecho: reetiqueta mV como código del ADC (con offset)."""
    def tickStrings(self, values, scale, spacing):
        return [f"{int(round(v / MV_PER_LSB + MID))}" for v in values]


class SampleAxis(pg.AxisItem):
    """Eje X superior: reetiqueta tiempo (µs) como número de muestra."""
    def tickStrings(self, values, scale, spacing):
        return [f"{int(round(v / PERIOD_US))}" for v in values]


class WindowView(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DAQ — Ventana (osciloscopio) en vivo")
        self.resize(1050, 820)
        lay = QtWidgets.QVBoxLayout(self)

        header = QtWidgets.QHBoxLayout()
        self.status = QtWidgets.QLabel("○ conectando…")
        self.status.setStyleSheet("font-size:15px; font-weight:bold;")
        self.readout = QtWidgets.QLabel("min --- / max --- mV")
        self.readout.setStyleSheet(
            "font-size:24px; font-weight:bold; color:#00d0ff;")
        self.readout.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        header.addWidget(self.status)
        header.addStretch()
        header.addWidget(self.readout)
        lay.addLayout(header)

        pg.setConfigOptions(antialias=True)

        win_us = TIME_US[-1]

        # ── Panel 1: ventana completa con región de zoom ──────────────────────
        self.p_full = pg.PlotWidget()
        self.p_full.setBackground("#0b0f14")
        self.p_full.showGrid(x=True, y=True, alpha=0.25)
        self.p_full.setTitle(
            f"<b>Ventana completa</b> · {win_us:.1f} µs · {N} muestras @ "
            f"{FS_MHZ:.0f} MSPS — la franja amarilla = región del zoom (arrástrala)")
        self.p_full.setLabel("left", "Amplitud", units="mV")
        self.p_full.setLabel("bottom", "Tiempo desde el trigger", units="µs")
        self.p_full.addLegend(offset=(-10, 10), labelTextColor="#cbd5e1")
        self.c_full = self.p_full.plot(
            pen=pg.mkPen("#00d0ff", width=1), name="señal capturada")
        self.region = pg.LinearRegionItem([0.0, ZOOM_US0], brush=(250, 204, 21, 40))
        self.region.setZValue(10)
        self.p_full.addItem(self.region)
        self.region.sigRegionChanged.connect(self._sync_zoom_x)
        lay.addWidget(self.p_full, stretch=2)

        # ── Panel 2: zoom vivo de la región (ejes dobles + muestras) ──────────
        self.p_zoom = pg.PlotWidget(axisItems={
            "right": CodeAxis(orientation="right"),
            "top": SampleAxis(orientation="top"),
        })
        self.p_zoom.setBackground("#0b0f14")
        self.p_zoom.showGrid(x=True, y=True, alpha=0.25)
        self.p_zoom.setTitle(
            f"<b>Zoom de la región</b> · Ts = {TS_NS:.1f} ns ({FS_MHZ:.0f} MSPS) "
            f"· cada punto = 1 muestra · promedio coherente de {AVG_N} ventanas")
        self.p_zoom.setLabel("left", "Amplitud", units="mV")
        self.p_zoom.setLabel("right", f"Código ADC ({BITS} bits · 0 mV = {MID})")
        self.p_zoom.setLabel("bottom", "Tiempo desde el trigger", units="µs")
        self.p_zoom.setLabel("top", "N.º de muestra")
        self.p_zoom.showAxis("right")
        self.p_zoom.showAxis("top")
        self.p_zoom.addLegend(offset=(-10, 10), labelTextColor="#cbd5e1")
        self.c_zoom_raw = self.p_zoom.plot(
            pen=pg.mkPen("#475569", width=1),
            symbol="o", symbolSize=5, symbolBrush="#94a3b8",
            name="última ventana")
        self.c_zoom_avg = self.p_zoom.plot(
            pen=pg.mkPen("#facc15", width=2),
            symbol="o", symbolSize=5, symbolBrush="#facc15",
            name=f"promedio ×{AVG_N}")
        lay.addWidget(self.p_zoom, stretch=2)

        # ── Panel 3: histórico de min/max por ventana ─────────────────────────
        self.p_hist = pg.PlotWidget()
        self.p_hist.setBackground("#0b0f14")
        self.p_hist.showGrid(x=True, y=True, alpha=0.25)
        self.p_hist.setTitle(
            f"<b>Histórico</b> · pico de cada ventana (últimos {HIST_S:.0f} s) "
            "— estabilidad disparo a disparo")
        self.p_hist.setLabel("left", "Amplitud", units="mV")
        self.p_hist.setLabel("bottom", "Tiempo transcurrido", units="s")
        self.p_hist.addLegend(offset=(-10, 10), labelTextColor="#cbd5e1")
        self.c_hist_max = self.p_hist.plot(
            pen=pg.mkPen("#f87171", width=1), name="máx por ventana")
        self.c_hist_min = self.p_hist.plot(
            pen=pg.mkPen("#60a5fa", width=1), name="mín por ventana")
        lay.addWidget(self.p_hist, stretch=1)

        # Estado
        self.buf = bytearray()
        self.ser = None
        self.avg = deque(maxlen=AVG_N)
        hist_max = int(HIST_S * 40)
        self.h_t = deque(maxlen=hist_max)
        self.h_lo = deque(maxlen=hist_max)
        self.h_hi = deque(maxlen=hist_max)
        self.t0 = time.time()
        self.silent_since = None
        self.last_frame_t = 0.0

        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update)
        self.timer.start(POLL_MS)

    def _sync_zoom_x(self):
        lo, hi = self.region.getRegion()
        self.p_zoom.setXRange(lo, hi, padding=0)

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
            if time.time() - self.last_frame_t < LIVE_HOLD_S:
                return
            if self.silent_since is None:
                self.silent_since = time.time()
            dt = time.time() - self.silent_since
            self._status(f"○ SIN TRIGGER ({dt:.0f}s)", "#facc15")
            return

        self.silent_since = None
        self.last_frame_t = time.time()
        codes = np.frombuffer(frame, dtype="<u2") & MASK
        mv = code_to_mv(codes)

        self.c_full.setData(TIME_US, mv)

        self.avg.append(mv)
        avg = np.mean(self.avg, axis=0)
        self.c_zoom_raw.setData(TIME_US, mv)
        self.c_zoom_avg.setData(TIME_US, avg)

        # autoescala Y del zoom según la región visible (sobre el promedio)
        lo, hi = self.region.getRegion()
        m = (TIME_US >= lo) & (TIME_US <= hi)
        if m.any():
            seg = avg[m]
            c = float(seg.mean())
            span = max(20.0, float(seg.max() - seg.min()) * 0.6 + 10)
            self.p_zoom.setYRange(c - span, c + span)

        # histórico min/max (mV) por ventana
        t = time.time() - self.t0
        self.h_t.append(t)
        self.h_lo.append(float(mv.min()))
        self.h_hi.append(float(mv.max()))
        ht = np.fromiter(self.h_t, float)
        self.c_hist_max.setData(ht, np.fromiter(self.h_hi, float))
        self.c_hist_min.setData(ht, np.fromiter(self.h_lo, float))
        self.p_hist.setXRange(max(0, t - HIST_S), max(HIST_S, t), padding=0)

        vmin, vmax = float(mv.min()), float(mv.max())
        self.readout.setText(
            f"min {vmin:+.0f} / max {vmax:+.0f} mV   ·   Vpp {vmax - vmin:.0f} mV")
        self._status("● LIVE", "#4ade80")


def main():
    app = QtWidgets.QApplication(sys.argv)
    w = WindowView()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
