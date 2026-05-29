"""
PHOTOACOUSTIC DAQ — Monitor en tiempo real con PyQtGraph.

Reescrito desde matplotlib para alcanzar >100 FPS reales con ráfagas de
1350 muestras @ 27 MSPS. PyQtGraph usa Qt + OpenGL bajo el capó.

Controles del gráfico (nativos de PyQtGraph):
    - Rueda del mouse        : zoom
    - Click derecho + arrastrar : zoom rectangular
    - Click izq. + arrastrar : pan
    - Click derecho          : menú (autorrange, exportar, etc.)
    - Tecla 'A'              : autorrange
"""
import sys
import time

import numpy as np
import pyqtgraph as pg
import serial
from PySide6 import QtCore, QtWidgets

# --- Configuración ---
SERIAL_PORT = 'COM14'
BAUD_RATE = 3_000_000
SAMPLE_SIZE = 1350
FS_MHZ = 27.0
BIT_DEPTH = 8
C_TISSUE = 1540.0
F_SENSOR = 2.0

# --- Derivados ---
PERIOD_US = 1.0 / FS_MHZ
TIME_AXIS = np.linspace(0, SAMPLE_SIZE * PERIOD_US, SAMPLE_SIZE)
DIST_AXIS_CM = TIME_AXIS * 1e-6 * C_TISSUE * 100
LAMBDA_MM = (C_TISSUE / (F_SENSOR * 1e6)) * 1000
MAX_DEPTH_CM = DIST_AXIS_CM[-1]
ADC_MAX = (1 << BIT_DEPTH) - 1


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
        pg.setConfigOptions(antialias=True, useOpenGL=True)
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground('#0f172a')
        self.plot_widget.showGrid(x=True, y=True, alpha=0.2)
        self.plot_widget.setLabel('bottom', 'Time of Flight', units='µs')
        self.plot_widget.setLabel('left', 'Amplitude (ADC LSB)')
        self.plot_widget.setYRange(-5, ADC_MAX + 5)
        self.plot_widget.setXRange(0, TIME_AXIS[-1])

        # Eje superior con profundidad
        top_axis = pg.AxisItem('top')
        top_axis.setLabel('Depth in Tissue', units='cm', color='#f39c12')
        top_axis.setScale(C_TISSUE * 1e-4)  # µs → cm
        self.plot_widget.setAxisItems({'top': top_axis})

        self.curve = self.plot_widget.plot(
            TIME_AXIS,
            np.zeros(SAMPLE_SIZE),
            pen=pg.mkPen('#00f2ff', width=1),
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
        status_title.setStyleSheet(
            "color:#4ade80; font-size:13px; font-weight:bold;"
        )
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

        # --- Timer de polling del puerto ---
        # 1 ms: cae bajo el peor caso de llegada (cada ~5 ms entre disparos
        # consecutivos a 5 kHz si la RPi está conectada). Cuando hay datos los
        # absorbemos en bloque; cuando no, in_waiting devuelve 0 sin coste.
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.poll_serial)
        self.timer.start(1)

    def poll_serial(self) -> None:
        try:
            if self.ser.in_waiting < SAMPLE_SIZE:
                return
            raw = self.ser.read(SAMPLE_SIZE)
            # Drenar buffer: si vienen apilados varios frames, nos quedamos
            # con el más reciente para que la gráfica no se atrase.
            while self.ser.in_waiting >= SAMPLE_SIZE:
                raw = self.ser.read(SAMPLE_SIZE)
        except serial.SerialException as e:
            self.timer.stop()
            self.lbl_status.setText("Signal:     [X] USB DESCONECTADO")
            self.lbl_status.setStyleSheet(
                "color:#ef4444; font-family:Consolas,monospace; "
                "font-size:12px; font-weight:bold;"
            )
            print(f"\nUSB perdido: {e}\nReconecta el Tang Nano y reinicia el programa.")
            return
        data = np.frombuffer(raw, dtype=np.uint8)
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

        # Log a consola: 1 línea por captura — útil para caracterizar entrada
        self.frame_count += 1
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

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self.timer.stop()
        if self.ser.is_open:
            self.ser.close()
        super().closeEvent(event)


def main() -> None:
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.2)
        ser.reset_input_buffer()
        print(f"Conectado a: {SERIAL_PORT}")
    except serial.SerialException as error:
        print(f"ERROR de puerto: {error}")
        sys.exit(1)

    app = QtWidgets.QApplication(sys.argv)
    window = DAQMonitor(ser)
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
