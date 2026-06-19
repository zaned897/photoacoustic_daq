"""
PHOTOACOUSTIC DAQ — Modo voltímetro (strip chart en vivo).

Companion del firmware "modo voltímetro" (top.v slow streaming):
el FPGA envía 100 muestras/s a 115200 baud, 4 bytes por muestra:

    [0xA5][0x5A][high][low]   con dato de 10 bits = (high << 8) | low

Este script plotea los últimos WINDOW_S segundos como gráfica rodante,
con eje derecho en voltios (frontend ±5 V del módulo AD9226_01).

    pipenv run python mac_test/voltmeter.py
"""

import os
import sys
import time
from collections import deque

import matplotlib.pyplot as plt
import numpy as np
import serial
from matplotlib.animation import FuncAnimation

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from serial_helper import find_uart_port  # noqa: E402

BAUD_RATE = 115_200
SYNC = b"\xA5\x5A"
WINDOW_S = 20.0
RATE_HZ = 100
ADC_MAX = 1023
LSB_VOLTS = 10.0 / 1024  # frontend ±5 V → 9.77 mV por LSB (10 bits)
MID_CODE = 512


def code_to_volts(code: float) -> float:
    return (code - MID_CODE) * LSB_VOLTS


def main() -> None:
    port = find_uart_port()
    try:
        ser = serial.Serial(port, BAUD_RATE, timeout=0.2, exclusive=True)
        ser.reset_input_buffer()
        print(f"Conectado a: {port} @ {BAUD_RATE} baud")
    except serial.SerialException as error:
        print(f"ERROR de puerto: {error}")
        sys.exit(1)

    maxlen = int(WINDOW_S * RATE_HZ)
    times: deque = deque(maxlen=maxlen)
    codes: deque = deque(maxlen=maxlen)
    t0 = time.time()

    plt.style.use("dark_background")
    fig, ax = plt.subplots(figsize=(12, 6))
    fig.suptitle(
        "DAQ — modo voltímetro (100 S/s @ 115200 baud)",
        fontsize=13, fontweight="bold", color="#4ade80",
    )
    ax.set_xlabel("Tiempo (s)", fontsize=11, color="#bdc3c7")
    ax.set_ylabel("Código ADC (10 bits)", fontsize=11, color="#bdc3c7")
    ax.set_ylim(-10, ADC_MAX + 10)
    ax.grid(True, linestyle="--", alpha=0.25)

    ax_v = ax.secondary_yaxis(
        "right", functions=(code_to_volts, lambda v: v / LSB_VOLTS + MID_CODE)
    )
    ax_v.set_ylabel("Voltaje de entrada (V)", fontsize=10, color="#f39c12")
    ax_v.tick_params(axis="y", colors="#f39c12")

    (line,) = ax.plot([], [], color="#00f2ff", lw=1.2)
    status = ax.text(
        0.02, 0.96, "esperando datos…",
        transform=ax.transAxes, fontsize=10, family="monospace",
        verticalalignment="top", color="white",
        bbox=dict(boxstyle="round", facecolor="#1e293b", alpha=0.85),
    )

    state = {"buffer": bytearray(), "n_samples": 0, "usb_lost": False}

    def update(_frame):
        try:
            pending = ser.in_waiting
            if pending:
                state["buffer"].extend(ser.read(pending))
        except OSError:
            if not state["usb_lost"]:
                state["usb_lost"] = True
                print("[voltmeter] ⚠ USB desconectado — reinicia el monitor.")
                status.set_text("USB DESCONECTADO")
                status.set_color("#f87171")
            return (line, status)

        buf = state["buffer"]
        while True:
            idx = buf.find(SYNC)
            if idx < 0:
                # conserva el último byte por si es la mitad de un sync
                del buf[:-1]
                break
            if len(buf) < idx + 4:
                del buf[:idx]
                break
            high, low = buf[idx + 2], buf[idx + 3]
            del buf[: idx + 4]
            if high > 0x03:
                continue  # corrupto — el resync por header lo absorbe
            code = (high << 8) | low
            times.append(time.time() - t0)
            codes.append(code)
            state["n_samples"] += 1

        if not codes:
            return (line, status)

        t_arr = np.array(times)
        line.set_data(t_arr, np.array(codes))
        ax.set_xlim(max(0.0, t_arr[-1] - WINDOW_S), max(WINDOW_S, t_arr[-1]))

        last = codes[-1]
        status.set_text(
            f"código : {last:4d}\n"
            f"voltaje: {code_to_volts(last):+6.3f} V\n"
            f"muestras: {state['n_samples']}"
        )
        return (line, status)

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
