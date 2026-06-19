// =============================================================================
// UART TX — 8N1, sin FIFO
//
// Transmite un byte por cada pulso de tx_start (debe durar exactamente 1 ciclo).
// tx_busy permanece HIGH durante toda la transmisión y vuelve a LOW al terminar.
//
// Parámetros:
//   CLK_FREQ  (Hz)  : Frecuencia del reloj del sistema. Default: 27 MHz.
//   BAUD_RATE (bps) : Velocidad de transmisión.          Default: 3 Mbaud.
//
// Con CLK_FREQ=27_000_000 y BAUD_RATE=3_000_000:
//   CLK_PER_BIT = 27_000_000 / 3_000_000 = 9   (divisor exacto, error de baud = 0%)
//   Duración por bit  = 9 ciclos / 27 MHz = 333 ns
//   Duración por byte = 10 bits × 333 ns  = 3.33 µs
//   Duración ráfaga   = 1350 bytes × 3.33 µs = 4.5 ms
//
// Trama 8N1: [START=0][D0][D1][D2][D3][D4][D5][D6][D7][STOP=1]
// =============================================================================
module uart_tx_module #(
    parameter CLK_FREQ  = 27_000_000,
    parameter BAUD_RATE = 3_000_000
)(
    input  wire       clk,
    input  wire       rst_n,
    input  wire       tx_start,
    input  wire [7:0] tx_data,
    output reg        tx_busy,
    output reg        uart_tx
);

    localparam CLK_PER_BIT = CLK_FREQ / BAUD_RATE;
    localparam IDLE = 0, START = 1, DATA = 2, STOP = 3;

    reg [1:0]  state;
    reg [15:0] clk_cnt;
    reg [2:0]  bit_idx;
    reg [7:0]  data_reg;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state   <= IDLE;
            uart_tx <= 1;
            tx_busy <= 0;
            clk_cnt <= 0;
            bit_idx <= 0;
        end else begin
            case (state)
                IDLE: begin
                    uart_tx <= 1;
                    clk_cnt <= 0;
                    bit_idx <= 0;
                    if (tx_start) begin
                        state    <= START;
                        tx_busy  <= 1;
                        data_reg <= tx_data;
                    end else begin
                        tx_busy <= 0;
                    end
                end
                START: begin
                    uart_tx <= 0;
                    if (clk_cnt < CLK_PER_BIT - 1) begin
                        clk_cnt <= clk_cnt + 1;
                    end else begin
                        clk_cnt <= 0;
                        state   <= DATA;
                    end
                end
                DATA: begin
                    uart_tx <= data_reg[bit_idx];
                    if (clk_cnt < CLK_PER_BIT - 1) begin
                        clk_cnt <= clk_cnt + 1;
                    end else begin
                        clk_cnt <= 0;
                        if (bit_idx < 7) begin
                            bit_idx <= bit_idx + 1;
                        end else begin
                            bit_idx <= 0;
                            state   <= STOP;
                        end
                    end
                end
                STOP: begin
                    uart_tx <= 1;
                    if (clk_cnt < CLK_PER_BIT - 1) begin
                        clk_cnt <= clk_cnt + 1;
                    end else begin
                        clk_cnt <= 0;
                        state   <= IDLE;
                        tx_busy <= 0;
                    end
                end
            endcase
        end
    end

endmodule
