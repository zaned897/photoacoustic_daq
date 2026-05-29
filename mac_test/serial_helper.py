"""Cross-platform Tang Nano 9K UART port detection.

Tang Nano 9K uses an FTDI FT2232H (VID:PID = 0403:6010) que expone dos
interfaces USB:
  - Interface 0 → JTAG (lo usa openFPGALoader para programar)
  - Interface 1 → UART (nuestro canal de datos)

Este helper localiza automáticamente la Interface 1 en Windows, macOS y
Linux. Si no encuentra el dispositivo, devuelve un fallback razonable
para el OS actual.
"""

import sys
from serial.tools import list_ports

FT2232H_VID = 0x0403
FT2232H_PID = 0x6010


def find_uart_port(verbose: bool = True) -> str:
    """Devuelve el path/nombre del puerto UART del Tang Nano 9K.

    En Windows: COMxx (Interface 0 va por WinUSB y no aparece como COM).
    En macOS:   /dev/cu.usbserial-XXX1 (sufijo '1' = Interface 1).
    En Linux:   /dev/ttyUSB1 (el segundo del par 0/1).
    """
    candidates = [
        p
        for p in list_ports.comports()
        if p.vid == FT2232H_VID and p.pid == FT2232H_PID
    ]

    port = _select_interface_one(candidates) if candidates else _fallback()

    if verbose:
        if candidates:
            print(f"[serial_helper] FTDI detectado → {port}")
        else:
            print(
                f"[serial_helper] FTDI no encontrado, usando fallback → {port}"
            )
    return port


def _select_interface_one(candidates: list) -> str:
    """Selecciona la Interface 1 de la lista de puertos FTDI candidatos."""
    if sys.platform == "darwin":
        # En macOS el sufijo del device path = serial-hex + interface_idx.
        # Interface 1 (UART) termina en '1'; Interface 0 (JTAG) en '0'.
        for p in candidates:
            if p.device.endswith("1"):
                return p.device
        # Fallback: el último alfabéticamente
        return sorted(c.device for c in candidates)[-1]

    if sys.platform == "win32":
        # En Windows, Interface 0 está claimed por WinUSB (no enumera como
        # COM). Solo Interface 1 aparece en list_ports, así que es directo.
        return candidates[0].device

    # Linux / otros
    # /dev/ttyUSBN — el más alto suele ser Interface 1.
    return sorted(c.device for c in candidates)[-1]


def _fallback() -> str:
    """Default razonable cuando no se detecta nada."""
    if sys.platform == "darwin":
        return "/dev/cu.usbserial-101"
    if sys.platform == "win32":
        return "COM14"
    return "/dev/ttyUSB1"


if __name__ == "__main__":
    # Test manual: `python mac_test/serial_helper.py`
    port = find_uart_port()
    print(f"\nPlatform: {sys.platform}")
    print(f"Port resuelto: {port}")
