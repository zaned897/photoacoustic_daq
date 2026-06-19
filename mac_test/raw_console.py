"""
PHOTOACOUSTIC DAQ — Monitor de consola en crudo (modo voltímetro).

Cada segundo imprime una línea con:
  - bytes/s recibidos en crudo
  - hex de los primeros bytes del segundo (acotado)
  - muestras decodificadas [0xA5][0x5A][high][low] → código 10-bit y voltios

Sin matplotlib, sin ventanas: solo la verdad del puerto serial.

    pipenv run python mac_test/raw_console.py
"""

import os
import sys
import time

import serial

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from serial_helper import find_uart_port  # noqa: E402

BAUD_RATE = 115_200
SYNC = b"\xA5\x5A"
LSB_VOLTS = 10.0 / 1024  # frontend ±5 V, 10 bits → 9.77 mV/LSB
MID_CODE = 512


def main() -> None:
    port = find_uart_port()
    try:
        ser = serial.Serial(port, BAUD_RATE, timeout=0.1, exclusive=True)
        ser.reset_input_buffer()
        print(f"Conectado: {port} @ {BAUD_RATE} baud — Ctrl+C para salir\n")
    except serial.SerialException as error:
        print(f"ERROR de puerto: {error}")
        sys.exit(1)

    buf = bytearray()
    try:
        while True:
            t0 = time.time()
            raw_count = 0
            first_hex = ""
            codes = []
            while time.time() - t0 < 1.0:
                n = ser.in_waiting
                if n:
                    chunk = ser.read(n)
                    raw_count += len(chunk)
                    if not first_hex:
                        first_hex = chunk[:12].hex(" ")
                    buf.extend(chunk)
                # decodifica todas las muestras completas disponibles
                while True:
                    idx = buf.find(SYNC)
                    if idx < 0:
                        del buf[:-1]
                        break
                    if len(buf) < idx + 4:
                        del buf[:idx]
                        break
                    high, low = buf[idx + 2], buf[idx + 3]
                    del buf[: idx + 4]
                    if high <= 0x03:
                        codes.append((high << 8) | low)
                time.sleep(0.02)

            stamp = time.strftime("%H:%M:%S")
            if raw_count == 0:
                print(f"{stamp}  ── sin datos ── (UART en silencio)")
            elif not codes:
                print(
                    f"{stamp}  {raw_count:5d} B/s  raw=[{first_hex}]  "
                    f"sin muestras válidas (¿baud/firmware?)"
                )
            else:
                last = codes[-1]
                volts = (last - MID_CODE) * LSB_VOLTS
                cmin, cmax = min(codes), max(codes)
                print(
                    f"{stamp}  {raw_count:5d} B/s  muestras={len(codes):3d}  "
                    f"código={last:4d} [{cmin}–{cmax}]  →  {volts:+7.3f} V"
                )
    except KeyboardInterrupt:
        print("\nfin.")
    finally:
        if ser.is_open:
            ser.close()


if __name__ == "__main__":
    main()
