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
BAUD_RATE = 1_000_000  # debe coincidir con top.v (FW v0.4)
SAMPLE_SIZE = 270
FS_MHZ = 54.0
BIT_DEPTH = 10
BYTES_PER_SAMPLE = 2
FRAME_BYTES = SAMPLE_SIZE * BYTES_PER_SAMPLE  # 2700
FRAME_HEADER = b"\xAA\x55\xAA\x55"  # sync preamble (debe coincidir con top.v)
VERSION_BYTES = 2
EXPECTED_FW_VERSION = 0x0007
C_TISSUE = 1540.0
F_SENSOR = 2.5

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
        # exclusive=True: si otro proceso ya tiene el puerto, fallar aquí con
        # mensaje claro en vez de repartirse los bytes en silencio (macOS).
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1, exclusive=True)
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
    fig, (ax, ax_zoom) = plt.subplots(
        2, 1, figsize=(12, 8), height_ratios=[2, 1]
    )
    plt.subplots_adjust(top=0.90, bottom=0.08, hspace=0.45)

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

    # ─── Panel de zoom: primeros µs tras el trigger ──────────────────────────
    # El evento fotoacústico (ringing ~400 ns) vive al inicio de la ventana;
    # en la vista completa de 50 µs es invisible. Aquí: frame crudo + promedio
    # coherente de los últimos AVG_N frames (el trigger determinista alinea
    # los eventos → el promedio crece la señal y suprime ruido en √N).
    ZOOM_US = 2.0
    AVG_N = 32
    zoom_n = int(ZOOM_US / PERIOD_US)
    ax_zoom.set_title(
        f"Zoom 0–{ZOOM_US:.0f} µs · promedio coherente de {AVG_N} frames",
        fontsize=9,
        color="gray",
    )
    ax_zoom.set_xlabel("Time of Flight (µs)", fontsize=10, color="#bdc3c7")
    ax_zoom.set_ylabel("ADC LSB", fontsize=10, color="#bdc3c7")
    ax_zoom.set_xlim(0, ZOOM_US)
    ax_zoom.grid(True, linestyle="--", alpha=0.2)
    (line_zoom,) = ax_zoom.plot(
        TIME_AXIS[:zoom_n], np.zeros(zoom_n),
        color="#475569", lw=0.8, label="último frame",
    )
    (line_avg,) = ax_zoom.plot(
        TIME_AXIS[:zoom_n], np.zeros(zoom_n),
        color="#facc15", lw=1.5, label=f"promedio ×{AVG_N}",
    )
    ax_zoom.legend(loc="upper right", fontsize=8)

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

    from collections import deque

    state = {
        "buffer": bytearray(),
        "last_time": time.time(),
        "fps_smoothed": 0.0,
        "avg_frames": deque(maxlen=AVG_N),
    }

    HDR_LEN = len(FRAME_HEADER)
    FRAME_TOTAL = HDR_LEN + VERSION_BYTES + FRAME_BYTES
    MAX_BUFFER = 100_000
    state["version_announced"] = False

    def update(_frame):
        # Acumulador + búsqueda de header de sync
        try:
            pending = ser.in_waiting
            if pending:
                state["buffer"].extend(ser.read(pending))
        except OSError:
            # El FTDI desapareció del USB (cable suelto / reset del puerto).
            # Avisar una sola vez y dejar la ventana abierta con el último frame.
            if not state.get("usb_lost"):
                state["usb_lost"] = True
                print(
                    "[monitor] ⚠ USB desconectado — reconecta la placa y "
                    "reinicia el monitor."
                )
                status.set_text("USB DESCONECTADO")
                status.set_color("#f87171")
            return (line, status)

        buf = state["buffer"]

        # Cap de seguridad
        if len(buf) > MAX_BUFFER:
            del buf[: -(HDR_LEN - 1)]

        # Busca el último header + version + frame disponible (descarta backlog)
        latest_frame = None
        latest_version = None
        while True:
            idx = buf.find(FRAME_HEADER)
            if idx < 0:
                break
            if idx > 0:
                del buf[:idx]
            if len(buf) < FRAME_TOTAL:
                break
            latest_version = (buf[HDR_LEN] << 8) | buf[HDR_LEN + 1]
            data_start = HDR_LEN + VERSION_BYTES
            latest_frame = bytes(buf[data_start : data_start + FRAME_BYTES])
            del buf[:FRAME_TOTAL]

        if latest_version is not None and not state["version_announced"]:
            match = "OK" if latest_version == EXPECTED_FW_VERSION else "MISMATCH"
            print(
                f"[monitor] FW_VERSION recibido: 0x{latest_version:04X} "
                f"(esperado 0x{EXPECTED_FW_VERSION:04X}) → {match}"
            )
            state["version_announced"] = True

        if latest_frame is None:
            return (line, status)

        data = (np.frombuffer(latest_frame, dtype="<u2") & 0x03FF).astype(np.int32)

        # Interpolar outliers a 0
        zero_idx = np.where(data == 0)[0]
        for i in zero_idx:
            left = data[i - 1] if i > 0 else data[i + 1]
            right = data[i + 1] if i < len(data) - 1 else data[i - 1]
            data[i] = (int(left) + int(right)) // 2

        line.set_ydata(data)

        # Panel de zoom: frame crudo + promedio coherente
        state["avg_frames"].append(data[:zoom_n].astype(np.float64))
        avg = np.mean(state["avg_frames"], axis=0)
        line_zoom.set_ydata(data[:zoom_n])
        line_avg.set_ydata(avg)
        # Autoescala del zoom alrededor del promedio (señales de pocos LSB)
        margin = max(5.0, float(avg.max() - avg.min()) * 0.5 + 2)
        center = float(avg.mean())
        ax_zoom.set_ylim(center - margin * 2, center + margin * 2)

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
