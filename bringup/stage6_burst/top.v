// =============================================================================
// ETAPA 6 — CAPTURA EN RÁFAGA (ventana fotoacústica)
// Cada trigger (S2 o pin 77) captura una VENTANA de N muestras a 27 MSPS en
// Block RAM y la envía completa por UART. Es el sistema fotoacústico real:
// trigger → ventana → forma de onda.
//
//   N = 270 muestras @ 27 MSPS  →  ventana = 10 µs (37 ns/muestra)
//   Cubre el evento (~350 ns ≈ 9-10 muestras) + margen acústico ~7.5 mm.
//   Frame: [0xAA 0x55 0xAA 0x55] + 270×2 bytes (uint16 LE, 10-bit) = 544 B
//   @115200 → ~47 ms/ventana → ~21 ventanas/s.
//
// LEDs: led_busy = heartbeat; led_trig destella en cada trigger.
// Lectura: pipenv run python mac_test/window_live.py
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
    output wire led_busy,
    output wire led_trig
);
    parameter N = 270;
    assign adc_clk = ~sys_clk;

    (* IOB = "true" *) reg [9:0] adc_reg;
    always @(posedge sys_clk) adc_reg <= adc_data_raw[11:2];

    (* ram_style = "block" *) reg [9:0] mem [0:N-1];
    reg [9:0] mem_rd;
    reg [9:0] ptr;
    reg       wr_en;
    always @(posedge sys_clk) begin
        if (wr_en) mem[ptr] <= adc_reg;
        mem_rd <= mem[ptr];
    end

    // Trigger: sincronizadores + flanco
    reg m1, m2, r1, r2;
    always @(posedge sys_clk) begin
        m1 <= trigger_manual; m2 <= m1;
        r1 <= trigger_rpi;    r2 <= r1;
    end
    wire trig = (m1 & ~m2) | (r1 & ~r2);

    // LED de trigger (estira a ~0.1 s)
    reg [21:0] evt = 0;
    always @(posedge sys_clk) begin
        if (trig) evt <= 22'd2_700_000;
        else if (evt != 0) evt <= evt - 1'b1;
    end
    assign led_trig = ~(evt != 0);

    // FSM
    localparam IDLE=0, CAPTURE=1, PREP=2, HDR=3, SEND=4;
    reg [2:0] st = IDLE;
    reg [1:0] hidx;
    reg       byte_idx;
    reg       tx_start = 0;
    reg [7:0] tx_byte;
    wire      tx_busy;

    assign led_busy = (st == IDLE) ? hb_led : 1'b0;  // heartbeat cuando idle

    // heartbeat
    reg [24:0] hb = 0; always @(posedge sys_clk) hb <= hb + 1'b1;
    wire hb_led = ~hb[24];

    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            st <= IDLE; ptr <= 0; wr_en <= 0; tx_start <= 0;
            hidx <= 0; byte_idx <= 0;
        end else begin
            case (st)
                IDLE: begin
                    ptr <= 0; wr_en <= 0; byte_idx <= 0;
                    if (trig) begin st <= CAPTURE; wr_en <= 1; end
                end
                CAPTURE: begin
                    wr_en <= 1;
                    if (ptr == N-1) begin ptr <= 0; wr_en <= 0; st <= PREP; end
                    else ptr <= ptr + 1'b1;
                end
                PREP: begin st <= HDR; hidx <= 0; end
                HDR: begin
                    if (!tx_busy && !tx_start) begin
                        if (hidx == 2'd0) tx_byte <= 8'hAA;
                        else if (hidx == 2'd1) tx_byte <= 8'h55;
                        else if (hidx == 2'd2) tx_byte <= 8'hAA;
                        else tx_byte <= 8'h55;
                        tx_start <= 1;
                        if (hidx == 2'd3) begin st <= SEND; byte_idx <= 0; end
                        hidx <= hidx + 1'b1;
                    end else tx_start <= 0;
                end
                SEND: begin
                    if (!tx_busy && !tx_start) begin
                        if (ptr == N) begin st <= IDLE; end
                        else begin
                            if (byte_idx == 1'b0) begin
                                tx_byte <= mem_rd[7:0]; byte_idx <= 1'b1;
                            end else begin
                                tx_byte <= {6'b0, mem_rd[9:8]};
                                byte_idx <= 1'b0; ptr <= ptr + 1'b1;
                            end
                            tx_start <= 1;
                        end
                    end else tx_start <= 0;
                end
            endcase
        end
    end

    /* verilator lint_off UNUSED */ wire _u = adc_otr; /* verilator lint_on UNUSED */

    uart_tx_module #(.CLK_FREQ(27_000_000), .BAUD_RATE(115_200)) uart_inst (
        .clk(sys_clk), .rst_n(sys_rst_n), .tx_start(tx_start),
        .tx_data(tx_byte), .uart_tx(uart_tx), .tx_busy(tx_busy)
    );
endmodule
