"""
Monitor de consola robusto (modo voltímetro) con AUTO-RECONEXIÓN.

Distingue claramente tres estados para que nunca confundas "enlace caído" con
"señal en cero":

    ● LIVE        — llegan muestras, muestra código→voltaje
    ○ SIN TRIGGER — puerto abierto pero 0 bytes (enlace ok o sin disparo)
    ✗ LINK DOWN   — el puerto desapareció (USB marginal); reintenta solo

Protocolo: [0xA5][0x5A][hi][lo], 10-bit, 115200 baud.

    pipenv run python mac_test/raw_console.py
"""

import os
import sys
import time

import serial

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from serial_helper import find_uart_port  # noqa: E402

BAUD = 115_200
SYNC = b"\xA5\x5A"
LSB_VOLTS = 10.0 / 1024
MID = 512


def open_port():
    """Abre el puerto, reintentando hasta que aparezca. Devuelve el serial."""
    while True:
        port = find_uart_port(verbose=False)
        try:
            ser = serial.Serial(port, BAUD, timeout=0.1, exclusive=True)
            ser.reset_input_buffer()
            print(f"● conectado: {port} @ {BAUD} baud")
            return ser
        except (serial.SerialException, OSError):
            print(f"✗ esperando puerto ({port})… reintentando")
            time.sleep(1.0)


def main() -> None:
    print("Monitor robusto — Ctrl+C para salir\n")
    ser = open_port()
    buf = bytearray()
    silent_since = None

    try:
        while True:
            t0 = time.time()
            raw = 0
            codes = []
            # ventana de 1 s; si el puerto muere, salta a reconexión
            try:
                while time.time() - t0 < 1.0:
                    n = ser.in_waiting
                    if n:
                        chunk = ser.read(n)
                        raw += len(chunk)
                        buf.extend(chunk)
                    while True:
                        i = buf.find(SYNC)
                        if i < 0:
                            del buf[:-1]
                            break
                        if len(buf) < i + 4:
                            del buf[:i]
                            break
                        hi, lo = buf[i + 2], buf[i + 3]
                        del buf[: i + 4]
                        if hi <= 0x03:
                            codes.append((hi << 8) | lo)
                    time.sleep(0.02)
            except (OSError, serial.SerialException):
                print(f"{time.strftime('%H:%M:%S')}  ✗ LINK DOWN — reconectando…")
                try:
                    ser.close()
                except Exception:
                    pass
                ser = open_port()
                buf.clear()
                continue

            stamp = time.strftime("%H:%M:%S")
            if codes:
                last = codes[-1]
                v = (last - MID) * LSB_VOLTS
                cmin, cmax = min(codes), max(codes)
                print(
                    f"{stamp}  ● LIVE   {raw:6d} B/s  n={len(codes):4d}  "
                    f"código={last:4d} [{cmin}–{cmax}]  →  {v:+7.3f} V"
                )
                silent_since = None
            else:
                if silent_since is None:
                    silent_since = time.time()
                dt = time.time() - silent_since
                print(
                    f"{stamp}  ○ SIN TRIGGER ({dt:.0f} s sin datos — "
                    f"enlace ok o sin disparo)"
                )
    except KeyboardInterrupt:
        print("\nfin.")
    finally:
        try:
            ser.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
