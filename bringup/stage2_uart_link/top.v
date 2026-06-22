// =============================================================================
// ETAPA 2 — ENLACE UART (aislado)
// Valida SOLO el camino pin 17 → FTDI → host. Sin trigger, sin ADC, sin PLL.
//
// Transmite un contador de 8 bits (0,1,2,…,255,0,…) de forma continua, lo más
// rápido que el UART permita. El host verifica que la secuencia incrementa de
// uno en uno: si lo hace, el enlace y el baud son correctos.
//
// Además: un LED hace heartbeat (~0.8 Hz). Así, aunque el UART esté mudo,
// el LED confirma que el FPGA configuró y el reloj corre — separa "FPGA
// muerto" de "UART muerto" de un vistazo.
//
// BAUD = 115200 (el más robusto; es el que funcionó con el voltímetro).
//   Una vez que esto pase, subimos a 1 Mbaud como sub-prueba.
//
// PASA SI: el host recibe un contador que incrementa de 1 en 1 sin huecos.
// =============================================================================
module top (
    input  wire sys_clk,   // 27 MHz, pin 52
    output wire uart_tx,   // pin 17 → FTDI RX
    output wire led        // pin 10, heartbeat (activo-bajo)
);
    // ─── Heartbeat: prueba de vida del reloj/config ───────────────────────────
    reg [24:0] hb = 0;
    always @(posedge sys_clk) hb <= hb + 1'b1;
    assign led = ~hb[24];

    // ─── Transmisor de contador continuo ──────────────────────────────────────
    reg  [7:0] tx_data = 0;
    reg        tx_start = 0;
    wire       tx_busy;

    always @(posedge sys_clk) begin
        tx_start <= 0;
        // En cuanto el UART queda libre, lanza el siguiente valor del contador.
        if (!tx_busy && !tx_start) begin
            tx_data  <= tx_data + 1'b1;
            tx_start <= 1'b1;
        end
    end

    uart_tx_module #(
        .CLK_FREQ  (27_000_000),
        .BAUD_RATE (115_200)
    ) uart_inst (
        .clk     (sys_clk),
        .rst_n   (1'b1),
        .tx_start(tx_start),
        .tx_data (tx_data),
        .uart_tx (uart_tx),
        .tx_busy (tx_busy)
    );
endmodule
