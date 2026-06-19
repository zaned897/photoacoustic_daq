// =============================================================================
// ETAPA 5 — TRIGGER EXTERNO (pin 77) dispara la captura
// Igual que la etapa 4 pero el disparo ya NO es interno: cada flanco de subida
// del pin 77 (tu Arduino, vía divisor) captura y envía 1 muestra. También S2.
//
// Validación: con el trigger externo activo → llegan muestras (limitado por
// UART). Al apagar el trigger → silencio. Eso prueba que la captura está
// gobernada por el evento externo, no por un reloj libre.
//
// Lectura: pipenv run python mac_test/raw_console.py
// =============================================================================
module top (
    input  wire sys_clk,
    input  wire sys_rst_n,
    input  wire trigger_manual,   // S2 (pin 3), flanco al soltar
    input  wire trigger_rpi,      // pin 77 (Arduino vía divisor), flanco al subir
    (* keep = "true" *) input wire [11:0] adc_data_raw,
    (* keep = "true" *) input wire        adc_otr,
    output wire adc_clk,
    output wire uart_tx,
    output wire led_busy,
    output wire led_trig    // destella ~0.1 s en cada trigger (visible sin UART)
);
    assign adc_clk = ~sys_clk;

    (* IOB = "true" *) reg [9:0] adc_reg;
    always @(posedge sys_clk) adc_reg <= adc_data_raw[11:2];

    // ─── Sincronizadores + detección de flanco de ambos triggers ──────────────
    reg man_d1, man_d2, rpi_d1, rpi_d2;
    always @(posedge sys_clk) begin
        man_d1 <= trigger_manual; man_d2 <= man_d1;
        rpi_d1 <= trigger_rpi;    rpi_d2 <= rpi_d1;
    end
    wire trig = (man_d1 & ~man_d2) | (rpi_d1 & ~rpi_d2);

    // ─── Captura disparada por evento externo ─────────────────────────────────
    reg [9:0]  s = 0;
    reg [1:0]  bidx = 0;
    reg        sending = 0;
    reg        tx_start = 0;
    reg [7:0]  tx_byte;
    wire       tx_busy;
    always @(posedge sys_clk) begin
        tx_start <= 0;
        if (!sending) begin
            if (trig) begin s <= adc_reg; bidx <= 0; sending <= 1; end
        end else if (!tx_busy && !tx_start) begin
            case (bidx)
                2'd0: tx_byte <= 8'hA5;
                2'd1: tx_byte <= 8'h5A;
                2'd2: tx_byte <= {6'b0, s[9:8]};
                2'd3: tx_byte <= s[7:0];
            endcase
            tx_start <= 1;
            if (bidx == 2'd3) sending <= 0;
            bidx <= bidx + 1'b1;
        end
    end

    // Heartbeat
    reg [24:0] hb = 0;
    always @(posedge sys_clk) hb <= hb + 1'b1;
    assign led_busy = ~hb[24];

    // LED de trigger: estira cada flanco a ~0.1 s para que el ojo lo vea
    reg [21:0] evt = 0;
    always @(posedge sys_clk) begin
        if (trig) evt <= 22'd2_700_000;
        else if (evt != 0) evt <= evt - 1'b1;
    end
    assign led_trig = ~(evt != 0);

    /* verilator lint_off UNUSED */
    wire _u = adc_otr;
    /* verilator lint_on UNUSED */

    uart_tx_module #(.CLK_FREQ(27_000_000), .BAUD_RATE(115_200)) uart_inst (
        .clk(sys_clk), .rst_n(sys_rst_n), .tx_start(tx_start),
        .tx_data(tx_byte), .uart_tx(uart_tx), .tx_busy(tx_busy)
    );
endmodule
