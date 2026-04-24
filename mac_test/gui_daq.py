import sys
import numpy as np
import pyqtgraph as pg
import serial
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer

# --- Constantes ---
SERIAL_PORT = '/dev/cu.usbserial-101'
BAUD_RATE = 3_000_000   # 3 Mbaud — requiere adaptador FTDI (FT232R/FT4232H)
SAMPLE_SIZE = 1350      # 27 MSPS × 50 µs = 1350 muestras por ráfaga
FS_MHZ = 27.0
C_TISSUE = 1540.0

PERIOD_US = 1.0 / FS_MHZ
TIME_AXIS = np.linspace(0, SAMPLE_SIZE * PERIOD_US, SAMPLE_SIZE)


class DAQWindow(pg.GraphicsLayoutWidget):
    """Ventana principal del DAQ usando pyqtgraph."""

    def __init__(self) -> None:
        super().__init__(title="Photoacoustic DAQ")
        self.resize(1000, 600)
        
        # Configuración de la gráfica
        self.plot_item = self.addPlot(title="A-Scan en Tiempo Real")
        self.plot_item.setLabel('bottom', "Tiempo de Vuelo", units="us")
        self.plot_item.setLabel('left', "Amplitud", units="ADC LSB")
        self.plot_item.setYRange(-10, 260)
        self.plot_item.showGrid(x=True, y=True, alpha=0.3)
        
        self.curve = self.plot_item.plot(pen=pg.mkPen('#00f2ff', width=1.5))
        
        # Configuración del puerto serial
        self.ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0)
        self.ser.reset_input_buffer()
        self.buffer = bytearray()
        
        # Timer para lectura asíncrona (10 ms)
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_data)
        self.timer.start(10)

    def update_data(self) -> None:
        """Lee fragmentos seriales y grafica al completar SAMPLE_SIZE."""
        try:
            bytes_waiting = self.ser.in_waiting
            if bytes_waiting > 0:
                self.buffer.extend(self.ser.read(bytes_waiting))
                
            # Extraer paquetes de 1024 bytes
            while len(self.buffer) >= SAMPLE_SIZE:
                raw_packet = self.buffer[:SAMPLE_SIZE]
                self.buffer = self.buffer[SAMPLE_SIZE:]  # Descartar lo leído
                
                data = np.array(raw_packet, dtype=np.uint8)
                self.curve.setData(TIME_AXIS, data)
                
        except Exception as error:
            print(f"Error de lectura: {error}")

    def closeEvent(self, event) -> None:
        """Cierra el puerto de forma segura."""
        self.ser.close()
        event.accept()


def main() -> None:
    """Inicia la aplicación PyQt6."""
    app = QApplication(sys.argv)
    window = DAQWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()