// =============================================================================
// PHOTOACOUSTIC DAQ — MÓDULO TOP
// FPGA  : Tang Nano 9K (GW1NR-9C)
// Reloj : 27 MHz (oscilador interno)
//
// DESCRIPCIÓN GENERAL
// ───────────────────
// Captura ráfagas del ADC al detectar un flanco de subida en cualquiera de
// los dos canales de trigger, almacena las muestras en Block RAM y las envía
// al host Python via UART 8N1 a 3 Mbaud.
//
//   [IDLE] ──trigger_event──► [CAPTURE: 1350 muestras @ 27 MSPS]
//                                             │ 50 µs
//                                             ▼
//                               [SENDING: UART 3 Mbaud, 4.5 ms]
//                                             │
//                                             ▼
//                                          [IDLE]
//
// FUENTES DE TRIGGER (OR lógico, ambas activas simultáneamente)
// ─────────────────────────────────────────────────────────────
//   trigger_manual : Botón S2 del Tang Nano 9K — para pruebas manuales en banco
//   trigger_rpi    : Tren de pulsos 5 kHz desde Raspberry Pi (GPIO 3.3V)
//
// TIMING OPERACIONAL — SENSOR ULTRASÓNICO 2 MHz
// ──────────────────────────────────────────────
//   Fs ADC        = 27 MSPS  →  período = 37 ns por muestra
//   Oversampling  = 27 / (2 × 2 MHz) = 6.75× sobre Nyquist del sensor
//   BURST_SIZE    = 1350 muestras  →  ventana de captura = 50 µs
//   Profundidad   = v × t / 2 = 1500 m/s × 50 µs / 2 ≈ 37.5 mm máximo
//
//   TX 1350 bytes @ 3 Mbaud = 4.5 ms por ráfaga
//   Período RPi   = 200 µs  →  1 de cada ≈22 disparos es capturado
//   Tasa efectiva ≈ 220 ráfagas/s  (Python acumula para signal averaging)
//
// LIMITACIÓN: DETECCIÓN DIRECTA DEL PULSO LÁSER CON FOTORECEPTOR
// ──────────────────────────────────────────────────────────────
//   Pulso láser = 10–30 ns.  Período de muestreo ADC = 37 ns.
//   Nyquist exige Fs ≥ 2/T_pulso → 66 MSPS para un pulso de 30 ns.
//   A 27 MSPS el pulso queda SUBMUESTREADO: se captura como mucho 1 punto
//   en el pico. Solo puede medirse presencia/energía, NO la forma de onda.
//   Para caracterizar la forma del pulso se requiere ≥100 MSPS.
//
// EXPANSIÓN A 10/12 BITS
// ──────────────────────
//   adc_data[7:0] = D11:D4 del ADC (8 MSBs del conversor de 12 bits).
//   Los pines FPGA 29, 30, 42, 51 están reservados para D3, D1, D2, D0.
//   Expansión futura: ampliar bus a [9:0] (10-bit) o [11:0] (12-bit)
//   y añadir cables a los pines reservados. Ver pins.cst.
// =============================================================================

module top (
    input  wire        sys_clk,
    input  wire        sys_rst_n,
    input  wire        trigger_manual,  // Botón S2 — pull-up, flanco activo en liberación
    input  wire        trigger_rpi,     // Tren de pulsos 5 kHz desde RPi (activo-alto)
    input  wire [7:0]  adc_data,        // D11:D4 del ADC (8 MSBs del conversor de 12 bits)
    output wire        adc_clk,         // Reloj del ADC — fase invertida de sys_clk
    output wire        uart_tx,
    output wire        led_busy         // Activo-bajo HW: LED encendido cuando state != IDLE
);

    // 27 MSPS × 50 µs = 1350 muestras
    // Cubre señal acústica de 0 a 37.5 mm de profundidad (v_sonido = 1500 m/s)
    parameter BURST_SIZE = 1350;

    // El ADC muestrea en el flanco de subida de adc_clk.
    // adc_clk = ~sys_clk: el ADC captura en el flanco de BAJADA de sys_clk.
    // El FPGA registra adc_data en el siguiente flanco de SUBIDA de sys_clk,
    // garantizando un margen de setup de ~18.5 ns (medio período a 27 MHz).
    assign adc_clk = ~sys_clk;

    (* ram_style = "block" *)
    reg [7:0]  memory [0:BURST_SIZE-1];
    reg [7:0]  mem_read_data;
    reg [10:0] ptr;            // 11 bits: rango 0..2047, cubre BURST_SIZE=1350
    reg        ram_write_en;

    always @(posedge sys_clk) begin
        if (ram_write_en)
            memory[ptr] <= adc_data;
        mem_read_data <= memory[ptr];
    end

    // ─── Detección de flanco de subida — dos registros de sincronización ──────
    // Elimina metaestabilidad para señales asíncronas externas (botón, RPi).
    // trigger_manual: pull-up → reposo HIGH; flanco activo = LOW→HIGH (liberación)
    // trigger_rpi:    pull-down → reposo LOW; flanco activo = LOW→HIGH (pulso RPi)
    localparam IDLE = 0, CAPTURE = 1, SENDING = 2;
    reg [1:0] state = IDLE;

    reg man_d1, man_d2, rpi_d1, rpi_d2;

    wire manual_posedge = man_d1 && !man_d2;
    wire rpi_posedge    = rpi_d1 && !rpi_d2;
    wire trigger_event  = manual_posedge | rpi_posedge;

    always @(posedge sys_clk) begin
        man_d1 <= trigger_manual;
        man_d2 <= man_d1;
        rpi_d1 <= trigger_rpi;
        rpi_d2 <= rpi_d1;
    end

    reg       tx_start     = 0;
    reg [7:0] tx_byte_latch;
    wire      tx_busy;

    // LED activo-bajo en Tang Nano 9K: pin LOW = LED encendido.
    // led_busy = 0 (LOW) cuando state != IDLE → LED ON durante captura y envío.
    assign led_busy = (state == IDLE);

    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            state        <= IDLE;
            ptr          <= 0;
            tx_start     <= 0;
            ram_write_en <= 0;
        end else begin
            case (state)

                IDLE: begin
                    ptr          <= 0;
                    ram_write_en <= 0;
                    if (trigger_event) state <= CAPTURE;
                end

                CAPTURE: begin
                    ram_write_en <= 1;
                    if (ptr == BURST_SIZE - 1) begin
                        ptr          <= 0;
                        ram_write_en <= 0;
                        state        <= SENDING;
                    end else begin
                        ptr <= ptr + 1;
                    end
                end

                // Mientras se transmite, los nuevos triggers son ignorados.
                // @ 3 Mbaud: 1350 bytes → 4.5 ms → se pierden ≈22 pulsos de la RPi.
                // Python acumula ráfagas recibidas (≈220/s) para signal averaging.
                SENDING: begin
                    ram_write_en <= 0;
                    if (!tx_busy && !tx_start) begin
                        if (ptr == BURST_SIZE) begin
                            state <= IDLE;
                        end else begin
                            tx_byte_latch <= mem_read_data;
                            tx_start      <= 1;
                            ptr           <= ptr + 1;
                        end
                    end else begin
                        tx_start <= 0;
                    end
                end

            endcase
        end
    end

    uart_tx_module #(
        .CLK_FREQ  (27_000_000),
        .BAUD_RATE (3_000_000)    // CLK_PER_BIT = 9 — divisor exacto, error de baud = 0%
    ) uart_inst (
        .clk     (sys_clk),
        .rst_n   (sys_rst_n),
        .tx_start(tx_start),
        .tx_data (tx_byte_latch),
        .uart_tx (uart_tx),
        .tx_busy (tx_busy)
    );

endmodule
