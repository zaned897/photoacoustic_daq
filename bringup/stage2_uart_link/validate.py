"""
ETAPA 2 — validador del enlace UART.

Lee el contador continuo que envía el FPGA y verifica que incrementa de 1 en 1.
Imprime un veredicto claro cada segundo.

    pipenv run python bringup/stage2_uart_link/validate.py

PASA si: "secuencia OK" con miles de bytes/s y 0 saltos.
"""

import os
import sys
import time

import serial

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "mac_test"))
from serial_helper import find_uart_port  # noqa: E402

BAUD = 115_200  # debe coincidir con top.v de esta etapa


def main() -> None:
    port = find_uart_port()
    try:
        ser = serial.Serial(port, BAUD, timeout=0.2, exclusive=True)
        ser.reset_input_buffer()
        print(f"Conectado: {port} @ {BAUD} baud — Ctrl+C para salir\n")
    except serial.SerialException as error:
        print(f"ERROR de puerto: {error}")
        sys.exit(1)

    prev = None
    try:
        while True:
            t0 = time.time()
            total = 0
            jumps = 0
            first = None
            while time.time() - t0 < 1.0:
                chunk = ser.read(4096)
                if not chunk:
                    continue
                total += len(chunk)
                if first is None:
                    first = chunk[0]
                for b in chunk:
                    if prev is not None and b != (prev + 1) & 0xFF:
                        jumps += 1
                    prev = b

            stamp = time.strftime("%H:%M:%S")
            if total == 0:
                print(f"{stamp}  ── 0 bytes ── enlace mudo (revisa pin 17/GND/baud)")
            elif jumps == 0:
                print(
                    f"{stamp}  ✅ secuencia OK  {total:6d} B/s  sin saltos "
                    f"(enlace y baud correctos)"
                )
            else:
                pct = 100 * jumps / max(total, 1)
                print(
                    f"{stamp}  ⚠ {total:6d} B/s  {jumps} saltos ({pct:.1f}%) "
                    f"— bytes corruptos (ruido/baud marginal)"
                )
    except KeyboardInterrupt:
        print("\nfin.")
    finally:
        if ser.is_open:
            ser.close()


if __name__ == "__main__":
    main()
