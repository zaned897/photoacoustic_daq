// =============================================================================
// ETAPA 4 — TRIGGER INTERNO 5 kHz (captura disparada)
// Igual que la etapa 3 (voltímetro) pero la captura ya NO es libre: un
// generador interno de 5 kHz dispara cada muestra. Es el cambio de
// arquitectura a "capturar por evento", con un trigger simulado por nosotros.
//
// 5 kHz = periodo 200 µs (27e6/5000 = 5400 ciclos).
// Como enviar 4 bytes a 115200 tarda ~347 µs, los triggers que caen mientras
// se envía se IGNORAN — exactamente como ignoraremos el trigger del láser
// durante la transmisión. Tasa efectiva limitada por UART (~2.9k muestras/s).
//
// Lectura: pipenv run python mac_test/raw_console.py
// =============================================================================
module top (
    input  wire sys_clk,
    input  wire sys_rst_n,
    input  wire trigger_manual,
    input  wire trigger_rpi,
    (* keep = "true" *) input wire [11:0] adc_data_raw,
    (* keep = "true" *) input wire        adc_otr,
    output wire adc_clk,
    output wire uart_tx,
    output wire led_busy
);
    assign adc_clk = ~sys_clk;

    (* IOB = "true" *) reg [9:0] adc_reg;
    always @(posedge sys_clk) adc_reg <= adc_data_raw[11:2];

    // ─── Trigger interno 5 kHz (simula el disparo del láser) ──────────────────
    reg [12:0] trig_cnt = 0;
    reg        trig = 0;
    always @(posedge sys_clk) begin
        trig <= 0;
        if (trig_cnt >= 13'd5399) begin trig_cnt <= 0; trig <= 1; end
        else trig_cnt <= trig_cnt + 1'b1;
    end

    // ─── Captura disparada: en cada trigger (si está libre), envía 1 muestra ──
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

    /* verilator lint_off UNUSED */
    wire _u = trigger_manual | trigger_rpi | adc_otr;
    /* verilator lint_on UNUSED */

    uart_tx_module #(.CLK_FREQ(27_000_000), .BAUD_RATE(115_200)) uart_inst (
        .clk(sys_clk), .rst_n(sys_rst_n), .tx_start(tx_start),
        .tx_data(tx_byte), .uart_tx(uart_tx), .tx_busy(tx_busy)
    );
endmodule
