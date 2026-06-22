// =============================================================================
// ETAPA 8 — CAPTURA EN RÁFAGA @ 54 MSPS, 12 BITS COMPLETOS
// Como la etapa 7 pero usa los 12 bits del AD9226 (adc_data_raw[11:0]).
// D0 (pin 25) y D1 (pin 28) ya están cableados → solo cambia el HDL.
//
//   Resolución: 12 bits → 4096 niveles, LSB = 10 V / 4096 = 2.44 mV
//   (4× más fino que 10 bits). 0 V ≈ código 2048.
//   N = 270 @ 54 MSPS → ventana 5 µs. Frame: header + 270×uint16 LE.
//   El high byte = {4'b0, mem[11:8]} ≤ 0x0F → nunca choca con 0xAA/0x55,
//   así que el header de sync sigue siendo inequívoco.
//
// Lectura: pipenv run python mac_test/window_live.py  (BITS=12, FS_MHZ=54)
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

    wire clk;
    wire pll_lock;
    rPLL #(
        .FCLKIN("27"), .IDIV_SEL(0), .FBDIV_SEL(1), .ODIV_SEL(8),
        .DYN_SDIV_SEL(2), .PSDA_SEL("0000"), .DUTYDA_SEL("1000"),
        .CLKOUT_FT_DIR(1'b1), .CLKOUTP_FT_DIR(1'b1),
        .CLKOUT_DLY_STEP(0), .CLKOUTP_DLY_STEP(0),
        .CLKFB_SEL("internal"), .CLKOUT_BYPASS("false"),
        .CLKOUTP_BYPASS("false"), .CLKOUTD_BYPASS("false"),
        .CLKOUTD_SRC("CLKOUT"), .CLKOUTD3_SRC("CLKOUT"), .DEVICE("GW1NR-9C")
    ) pll (
        .CLKOUT(clk), .LOCK(pll_lock), .CLKOUTP(), .CLKOUTD(), .CLKOUTD3(),
        .RESET(1'b0), .RESET_P(1'b0), .CLKIN(sys_clk), .CLKFB(1'b0),
        .FBDSEL(6'b0), .IDSEL(6'b0), .ODSEL(6'b0),
        .PSDA(4'b0), .DUTYDA(4'b0), .FDLY(4'b0)
    );

    assign adc_clk = ~clk;

    // 12 bits completos
    (* IOB = "true" *) reg [11:0] adc_reg;
    always @(posedge clk) adc_reg <= adc_data_raw[11:0];

    (* ram_style = "block" *) reg [11:0] mem [0:N-1];
    reg [11:0] mem_rd;
    reg [9:0]  ptr;
    reg        wr_en;
    always @(posedge clk) begin
        if (wr_en) mem[ptr] <= adc_reg;
        mem_rd <= mem[ptr];
    end

    reg m1, m2, r1, r2;
    always @(posedge clk) begin
        m1 <= trigger_manual; m2 <= m1;
        r1 <= trigger_rpi;    r2 <= r1;
    end
    wire trig = (m1 & ~m2) | (r1 & ~r2);

    reg [22:0] evt = 0;
    always @(posedge clk) begin
        if (trig) evt <= 23'd5_400_000;
        else if (evt != 0) evt <= evt - 1'b1;
    end
    assign led_trig = ~(evt != 0);

    reg [24:0] hb = 0; always @(posedge clk) hb <= hb + 1'b1;
    wire hb_led = ~hb[24];

    localparam IDLE=0, CAPTURE=1, PREP=2, HDR=3, SEND=4;
    reg [2:0] st = IDLE;
    reg [1:0] hidx;
    reg       byte_idx;
    reg       tx_start = 0;
    reg [7:0] tx_byte;
    wire      tx_busy;

    assign led_busy = (st == IDLE) ? hb_led : 1'b0;

    always @(posedge clk or negedge sys_rst_n) begin
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
                                tx_byte <= {4'b0, mem_rd[11:8]};  // 12-bit high
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

    uart_tx_module #(.CLK_FREQ(54_000_000), .BAUD_RATE(115_200)) uart_inst (
        .clk(clk), .rst_n(sys_rst_n), .tx_start(tx_start),
        .tx_data(tx_byte), .uart_tx(uart_tx), .tx_busy(tx_busy)
    );
endmodule
