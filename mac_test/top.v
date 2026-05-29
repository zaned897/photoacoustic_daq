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
// EXPANSIÓN A 10/12 BITS — CABLEADO FIJO, SOLO SE EDITA HDL
// ─────────────────────────────────────────────────────────
//   El bus físico adc_data_raw[11:0] llega completo al FPGA (los 12 pines
//   D0–D11 están cableados, ver pins.cst). En Fase 1 solo usamos los 8 MSBs
//   para mantener el protocolo UART de 1 byte/muestra. Los bits no usados
//   se sintetizan como entradas en alta impedancia (pull-down) y se descartan.
//
//   Fase 1 (actual) : ADC_WIDTH = 8  → wire [7:0] adc_sample = adc_data_raw[11:4]
//   Fase 2 (10-bit) : ADC_WIDTH = 10 → empaquetar 2 bytes/muestra en UART
//   Fase 3 (12-bit) : ADC_WIDTH = 12 → idem 2 bytes/muestra
//
//   También está cableado adc_otr (pin 51). En Fase 1 se ignora; en el futuro
//   puede usarse para marcar ráfagas saturadas y descartarlas en Python.
// =============================================================================

module top (
    input  wire                sys_clk,
    input  wire                sys_rst_n,
    input  wire                trigger_manual,    // Botón S2 — pull-up, flanco activo en liberación
    input  wire                trigger_rpi,       // Tren de pulsos 5 kHz desde RPi (activo-alto)
    // (* keep *) evita que Yosys optimice los bits que no se usan en Fase 1
    // (bits 0..3 del bus). Sin esto, nextpnr falla al aplicar IO_LOC en pins.cst
    // porque los puertos físicos quedarían fuera de la netlist sintetizada.
    (* keep = "true" *) input  wire [11:0] adc_data_raw,  // Bus físico completo D11..D0 del AD9226
    (* keep = "true" *) input  wire        adc_otr,       // Out-of-Range del AD9226 (sin uso F1)
    output wire                adc_clk,           // Reloj del ADC — fase invertida de sys_clk
    output wire                uart_tx,
    output wire                led_busy           // Activo-bajo HW: LED encendido cuando state != IDLE
);

    // ─── SELECCIÓN DE BITS EFECTIVOS (Fase 1: 8 MSBs del AD9226) ──────────────
    // Cambiar a adc_data_raw[11:2] para 10 bits o [11:0] para 12 bits.
    // Atención: pasar a >8 bits requiere ampliar la RAM y el protocolo UART
    // (2 bytes por muestra), no basta con cambiar esta línea.
    wire [7:0] adc_data = adc_data_raw[11:4];

    // 27 MSPS × 50 µs = 1350 muestras
    // Cubre señal acústica de 0 a 37.5 mm de profundidad (v_sonido = 1500 m/s)
    parameter BURST_SIZE = 1350;

    // ─── AUTO-TRIGGER INTERNO ────────────────────────────────────────────────
    // Genera un trigger periódico sin necesidad de S2 o trigger_rpi externo.
    // Útil para monitoreo continuo (ej. caracterizar un potenciómetro a 50 Hz).
    // Poner AUTO_TRIGGER_HZ = 0 para desactivar (vuelve al comportamiento
    // original: solo trigger por S2 o por pulso externo en pin 77).
    //
    //   50 Hz → ventana de 20 ms → 15.5 ms libres entre capturas
    //   10 Hz → ventana de 100 ms → 95.5 ms libres
    //   100 Hz → ventana de 10 ms → 5.5 ms libres (límite cercano al TX UART)
    parameter AUTO_TRIGGER_HZ = 20;
    localparam [25:0] AUTO_TRIG_MAX =
        (AUTO_TRIGGER_HZ == 0) ? 26'h3FFFFFF : (27_000_000 / AUTO_TRIGGER_HZ - 1);

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

    // Contador del auto-trigger interno (ver parámetro AUTO_TRIGGER_HZ arriba).
    reg [25:0] auto_trig_cnt = 0;
    reg        auto_trig_pulse = 0;

    always @(posedge sys_clk) begin
        auto_trig_pulse <= 0;
        if (AUTO_TRIGGER_HZ != 0) begin
            if (auto_trig_cnt >= AUTO_TRIG_MAX) begin
                auto_trig_cnt   <= 0;
                auto_trig_pulse <= 1;
            end else begin
                auto_trig_cnt <= auto_trig_cnt + 1;
            end
        end
    end

    // NOTA: trigger_rpi temporalmente deshabilitado porque el pin 77 capta
    // ruido capacitivo del bus ADC (líneas D0..D11 conmutando a 27 MHz a pocos
    // mm del pin) y dispara triggers espurios → FSM se queda en CAPTURE/SENDING
    // continuamente → satura el USB → Python no alcanza → plot congelado.
    // Para reactivar: agregar pull-down externo de 10kΩ entre pin 77 y GND.
    wire trigger_event = manual_posedge | auto_trig_pulse;
    /* verilator lint_off UNUSED */ wire _unused_rpi = rpi_posedge; /* verilator lint_on UNUSED */

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
