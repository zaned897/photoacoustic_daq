import time

import matplotlib.pyplot as plt
import numpy as np
import serial
from matplotlib.animation import FuncAnimation
from mpl_toolkits.axes_grid1.inset_locator import mark_inset


# --- Configuración Constantes ---
SERIAL_PORT = '/dev/cu.usbserial-101'
BAUD_RATE = 3_000_000   # 3 Mbaud — requiere adaptador FTDI (FT232R/FT4232H)
SAMPLE_SIZE = 1350      # 27 MSPS × 50 µs = 1350 muestras por ráfaga
FS_MHZ = 27.0
BIT_DEPTH = 8
C_TISSUE = 1540.0
F_SENSOR = 2.0          # Sensor ultrasónico fotoacústico (MHz)

# --- Cálculos Derivados ---
PERIOD_US = 1.0 / FS_MHZ
TIME_AXIS = np.linspace(0, SAMPLE_SIZE * PERIOD_US, SAMPLE_SIZE)
DIST_AXIS = (TIME_AXIS * 1e-6 * C_TISSUE) * 100
LAMBDA_MM = (C_TISSUE / (F_SENSOR * 1e6)) * 1000
MAX_DEPTH_CM = DIST_AXIS[-1]


def setup_serial() -> serial.Serial:
    """Configura y retorna la conexión serial."""
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=2)
        ser.reset_input_buffer()
        print(f"✅ Conectado a: {SERIAL_PORT}")
        return ser
    except serial.SerialException as error:
        print(f"❌ ERROR: {error}")
        exit(1)


def time_to_dist(time_val: float) -> float:
    """Convierte microsegundos a centímetros de profundidad."""
    return time_val * 1e-6 * C_TISSUE * 100


def dist_to_time(dist_val: float) -> float:
    """Convierte centímetros de profundidad a microsegundos."""
    return dist_val / (C_TISSUE * 100) * 1e6


def main() -> None:
    """Punto de entrada principal para la visualización del DAQ."""
    ser = setup_serial()

    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(13, 7))
    plt.subplots_adjust(right=0.70, top=0.85, bottom=0.15)

    fig.suptitle(
        "PHOTOACOUSTIC DAQ | PROTOTYPE v1",
        fontsize=18,
        fontweight='bold',
        color='#4ade80'
    )
    ax.set_title(
        f"Sensor: {F_SENSOR} MHz | Sampling: {FS_MHZ} MSPS | "
        f"Window: {MAX_DEPTH_CM:.2f} cm",
        fontsize=10,
        color='gray'
    )

    ax.set_xlabel(r"Time of Flight ($\mu s$)", fontsize=12, color='#bdc3c7')
    ax.set_ylabel("Amplitude (ADC LSB)", fontsize=12, color='#bdc3c7')
    ax.set_ylim(-10, 300)
    ax.set_xlim(0, max(TIME_AXIS))
    ax.grid(True, which='both', linestyle='--', alpha=0.2)

    ax_top = ax.secondary_xaxis('top')
    ax_top.set_functions((time_to_dist, dist_to_time))
    ax_top.set_xlabel('Depth in Tissue (cm)', fontsize=11, color='#f39c12')
    ax_top.tick_params(axis='x', colors='#f39c12')

    line_main, = ax.plot([], [], color='#00f2ff', linewidth=1.0, label='Data')

    axins = ax.inset_axes([0.55, 0.55, 0.40, 0.40])
    axins.set_facecolor('#0f172a')
    axins.grid(True, linestyle=':', color='gray', alpha=0.3)
    line_zoom, = axins.plot(
        [], [], color='#ff0055', linewidth=1.5, marker='o', markersize=3
    )

    zoom_start = 10.0
    zoom_width = 2.0
    axins.set_xlim(zoom_start, zoom_start + zoom_width)
    axins.set_ylim(-5, 260)
    axins.set_title(
        f"ROI Zoom ({zoom_start}-{zoom_start+zoom_width} $\mu s$)",
        fontsize=9,
        color='#ff0055'
    )

    mark_inset(ax, axins, loc1=2, loc2=4, fc="none", ec="0.5", linestyle="--")

    col_x = 0.73
    fig.text(
        col_x, 0.88, "PHYSICS METRICS",
        fontsize=12, fontweight='bold', color='white'
    )
    fig.text(
        col_x, 0.875, "______________",
        fontsize=12, fontweight='bold', color='#4ade80'
    )

    props = dict(boxstyle='round', facecolor='#1e293b', alpha=0.8)
    info_text = (
        f"Sound Speed: {C_TISSUE:.0f} m/s\n"
        f"Transducer:  {F_SENSOR} MHz\n"
        f"Wavelength:  {LAMBDA_MM:.3f} mm\n"
        f"Theo. Res.:  {LAMBDA_MM/2:.3f} mm\n"
        f"Max Depth:   {MAX_DEPTH_CM:.2f} cm\n"
        f"Time Win:    {TIME_AXIS[-1]:.1f} $\mu s$"
    )
    fig.text(
        col_x, 0.70, info_text,
        fontsize=10, family='monospace', color='#bdc3c7', bbox=props
    )

    fig.text(
        col_x, 0.60, "REAL-TIME STATUS",
        fontsize=12, fontweight='bold', color='white'
    )
    fig.text(
        col_x, 0.595, "________________",
        fontsize=12, fontweight='bold', color='#4ade80'
    )

    t_fps = fig.text(
        col_x, 0.55, "FPS:     --",
        fontsize=11, family='monospace', color='white'
    )
    t_rate = fig.text(
        col_x, 0.52, "Through: -- kB/s",
        fontsize=11, family='monospace', color='white'
    )
    t_check = fig.text(
        col_x, 0.48, "Signal:  WAITING",
        fontsize=10, fontweight='bold', color='gray'
    )

    state = {'last_time': time.time()}

    def update(_frame: int) -> tuple:
        if ser.in_waiting >= SAMPLE_SIZE:
            try:
                raw = ser.read(SAMPLE_SIZE)
                data = np.array(list(raw))

                line_main.set_data(TIME_AXIS, data)
                line_zoom.set_data(TIME_AXIS, data)

                curr_time = time.time()
                dt_val = curr_time - state['last_time']
                if dt_val > 0:
                    fps = 1.0 / dt_val
                    kbps = (SAMPLE_SIZE * fps) / 1000.0
                else:
                    fps = 0.0
                    kbps = 0.0
                state['last_time'] = curr_time

                diffs = np.diff(data)
                errors = np.sum((diffs != 1) & (diffs != -255))

                t_fps.set_text(f"FPS:     {fps:4.1f}")
                t_rate.set_text(f"Through: {kbps:4.1f} kB/s")

                if errors <= 10:
                    t_check.set_text("[OK] SIGNAL VALID")
                    t_check.set_color('#4ade80')
                else:
                    t_check.set_text("[!] NOISY / LOSS")
                    t_check.set_color('#ef4444')

                ser.reset_input_buffer()
            except Exception:
                pass

        return line_main, line_zoom

    _ani = FuncAnimation(
        fig, update, interval=1, blit=False, cache_frame_data=False
    )
    plt.show()
    ser.close()


if __name__ == '__main__':
    main()