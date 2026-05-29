"""
PHOTOACOUSTIC DAQ — Monitor con matplotlib (simple, estable).

Versión adaptada de la implementación matplotlib original (commit bd9fb69)
para 10-bit. Funciona en cualquier plataforma sin dependencias de Qt:

    pipenv run python mac_test/monitor_daq_simple.py

Trade-off vs monitor_daq.py (PyQtGraph):
  - matplotlib: ~5-10 FPS, MUY estable, sin freezes, sin OpenGL.
  - PyQtGraph:  100+ FPS, pero requiere Qt y a veces se cuelga en macOS.

Para uso normal y debugging matplotlib es más que suficiente. PyQtGraph
solo importa si quieres ver señales rápidas en vivo.
"""

import os
import sys
import time

import matplotlib.pyplot as plt
import numpy as np
import serial
from matplotlib.animation import FuncAnimation

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from serial_helper import find_uart_port  # noqa: E402


# --- Configuración ---
SERIAL_PORT = find_uart_port()
BAUD_RATE = 3_000_000
SAMPLE_SIZE = 1350
FS_MHZ = 27.0
BIT_DEPTH = 10
BYTES_PER_SAMPLE = 2
FRAME_BYTES = SAMPLE_SIZE * BYTES_PER_SAMPLE  # 2700
C_TISSUE = 1540.0
F_SENSOR = 2.0

# --- Derivados ---
PERIOD_US = 1.0 / FS_MHZ
TIME_AXIS = np.linspace(0, SAMPLE_SIZE * PERIOD_US, SAMPLE_SIZE)
LAMBDA_MM = (C_TISSUE / (F_SENSOR * 1e6)) * 1000
MAX_DEPTH_CM = TIME_AXIS[-1] * 1e-6 * C_TISSUE * 100
ADC_MAX = (1 << BIT_DEPTH) - 1


def time_to_dist(time_val: float) -> float:
    return time_val * 1e-6 * C_TISSUE * 100


def dist_to_time(dist_val: float) -> float:
    return dist_val / (C_TISSUE * 100) * 1e6


def main() -> None:
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
        try:
            ser.set_low_latency_mode(True)
        except (NotImplementedError, OSError):
            pass
        ser.reset_input_buffer()
        print(f"Conectado a: {SERIAL_PORT}")
    except serial.SerialException as error:
        print(f"ERROR de puerto: {error}")
        sys.exit(1)

    plt.style.use("dark_background")
    fig, ax = plt.subplots(figsize=(12, 6))
    plt.subplots_adjust(top=0.88, bottom=0.12)

    fig.suptitle(
        "PHOTOACOUSTIC DAQ — matplotlib monitor",
        fontsize=14,
        fontweight="bold",
        color="#4ade80",
    )
    ax.set_title(
        f"{FS_MHZ} MSPS · {BIT_DEPTH} bits · ventana {TIME_AXIS[-1]:.1f} µs "
        f"({MAX_DEPTH_CM:.2f} cm) · sensor {F_SENSOR} MHz",
        fontsize=9,
        color="gray",
    )
    ax.set_xlabel("Time of Flight (µs)", fontsize=11, color="#bdc3c7")
    ax.set_ylabel("Amplitude (ADC LSB)", fontsize=11, color="#bdc3c7")
    ax.set_ylim(-10, ADC_MAX + 10)
    ax.set_xlim(0, TIME_AXIS[-1])
    ax.grid(True, linestyle="--", alpha=0.2)

    ax_top = ax.secondary_xaxis("top", functions=(time_to_dist, dist_to_time))
    ax_top.set_xlabel("Depth in tissue (cm)", fontsize=10, color="#f39c12")
    ax_top.tick_params(axis="x", colors="#f39c12")

    (line,) = ax.plot(TIME_AXIS, np.zeros(SAMPLE_SIZE), color="#00f2ff", lw=1)

    status = ax.text(
        0.02,
        0.96,
        "",
        transform=ax.transAxes,
        fontsize=9,
        family="monospace",
        verticalalignment="top",
        color="white",
        bbox=dict(boxstyle="round", facecolor="#1e293b", alpha=0.85),
    )

    state = {
        "buffer": bytearray(),
        "last_time": time.time(),
        "fps_smoothed": 0.0,
    }

    def update(_frame):
        # Patrón acumulador (como en monitor_daq.py)
        pending = ser.in_waiting
        if pending:
            state["buffer"].extend(ser.read(pending))

        if len(state["buffer"]) < FRAME_BYTES:
            return (line, status)

        # Procesa solo el frame más reciente; descarta backlog.
        while len(state["buffer"]) >= 2 * FRAME_BYTES:
            del state["buffer"][:FRAME_BYTES]

        frame_bytes = bytes(state["buffer"][:FRAME_BYTES])
        del state["buffer"][:FRAME_BYTES]

        data = (np.frombuffer(frame_bytes, dtype="<u2") & 0x03FF).astype(np.int32)

        # Interpolar outliers a 0
        zero_idx = np.where(data == 0)[0]
        for i in zero_idx:
            left = data[i - 1] if i > 0 else data[i + 1]
            right = data[i + 1] if i < len(data) - 1 else data[i - 1]
            data[i] = (int(left) + int(right)) // 2

        line.set_ydata(data)

        now = time.time()
        dt = now - state["last_time"]
        state["last_time"] = now
        if dt > 0:
            fps = 1.0 / dt
            state["fps_smoothed"] = 0.8 * state["fps_smoothed"] + 0.2 * fps

        status.set_text(
            f"FPS    : {state['fps_smoothed']:5.1f}\n"
            f"min/max: {int(data.min())}/{int(data.max())}\n"
            f"mean   : {float(data.mean()):6.2f}\n"
            f"ceros  : {len(zero_idx)}"
        )

        return (line, status)

    # interval=50 ms → max 20 FPS, suficiente para visualizar y barato en CPU.
    _ani = FuncAnimation(
        fig, update, interval=50, blit=False, cache_frame_data=False
    )

    try:
        plt.show()
    finally:
        if ser.is_open:
            ser.close()


if __name__ == "__main__":
    main()
