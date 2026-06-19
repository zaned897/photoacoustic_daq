// =============================================================================
// ETAPA 0 — HEARTBEAT
// Valida lo más básico: que el bitstream se cargue y el reloj de 27 MHz corra.
// Sin UART, sin ADC, sin trigger. Solo un contador que parpadea un LED.
//
// PASA SI: el LED parpadea a ~0.8 Hz (≈0.62 s encendido, 0.62 s apagado).
//   Si parpadea → el FPGA configura y el cristal de 27 MHz oscila.
//   Si queda fijo o apagado → problema de configuración/alimentación.
//
// Cálculo: 27e6 / 2^25 = 0.805 Hz de conmutación en cnt[24].
// =============================================================================
module top (
    input  wire sys_clk,   // 27 MHz, pin 52
    output wire led        // pin 10, activo-bajo (LOW = encendido)
);
    reg [24:0] cnt = 0;
    always @(posedge sys_clk) cnt <= cnt + 1'b1;

    // cnt[24] conmuta a ~0.8 Hz. Activo-bajo: invertimos para que "encendido"
    // coincida con cnt[24]=1 (irrelevante para el test, solo estético).
    assign led = ~cnt[24];
endmodule
