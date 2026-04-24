import time

import serial


SERIAL_PORT = '/dev/cu.usbserial-101'
BAUD_RATE = 3_000_000   # 3 Mbaud — debe coincidir con uart_tx.v


def main() -> None:
    """Lee datos crudos del puerto serial para aislar fallos de hardware/OS."""
    try:
        with serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1) as ser:
            ser.reset_input_buffer()
            print(f"✅ Escuchando puerto crudo: {SERIAL_PORT}")
            print("Presiona S2 en el FPGA para enviar un paquete...")

            while True:
                bytes_waiting = ser.in_waiting
                if bytes_waiting > 0:
                    # Leemos todo lo que haya llegado
                    raw_data = ser.read(bytes_waiting)
                    data_list = list(raw_data)
                    
                    print(f"📥 Llegaron {len(data_list)} bytes.")
                    if len(data_list) > 0:
                        print(f"Muestra inicial: {data_list[:10]}")
                        
                time.sleep(0.05)

    except serial.SerialException as error:
        print(f"❌ Error del puerto: {error}")
    except KeyboardInterrupt:
        print("\n⏹️ Prueba terminada.")


if __name__ == '__main__':
    main()