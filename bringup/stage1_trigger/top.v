// =============================================================================
// ETAPA 1 — DETECCIÓN DE TRIGGER (sin UART)
// Valida que las entradas de trigger llegan y que la detección de flanco
// funciona. Feedback puramente visual: nada de serial.
//
//   led_s2  (pin 10): ALTERNA en cada flanco de S2 (press+release).
//                     Cada vez que sueltas S2, el LED cambia de estado.
//   led_rpi (pin 11): REFLEJA el nivel del pin 77 (trigger externo).
//                     Trigger en HIGH → LED encendido; LOW → apagado.
//   led_evt (pin 13): PULSO visible (~0.1 s) en cada flanco de subida de
//                     CUALQUIER trigger — destella ante cada evento.
//
// Heartbeat de fondo no incluido: si nada destella, vuelve a la etapa 0.
//
// PASA SI:
//   - Soltar S2 alterna led_s2 de forma fiable (1 cambio por pulsación).
//   - Activar el trigger externo (Arduino vía divisor) enciende led_rpi y
//     hace destellar led_evt en cada flanco.
// =============================================================================
module top (
    input  wire sys_clk,         // 27 MHz, pin 52
    input  wire trigger_manual,  // S2, pin 3, pull-up (reposo HIGH, flanco al soltar)
    input  wire trigger_rpi,     // pin 77, pull-down (reposo LOW, flanco al subir)
    output wire led_s2,          // pin 10
    output wire led_rpi,         // pin 11
    output wire led_evt          // pin 13
);
    // Sincronizadores de 2 FF para ambas entradas asíncronas
    reg man_d1, man_d2, rpi_d1, rpi_d2;
    always @(posedge sys_clk) begin
        man_d1 <= trigger_manual; man_d2 <= man_d1;
        rpi_d1 <= trigger_rpi;    rpi_d2 <= rpi_d1;
    end
    wire man_posedge = man_d1 & ~man_d2;
    wire rpi_posedge = rpi_d1 & ~rpi_d2;

    // led_s2: toggle en cada flanco de S2 (visible y sin rebote perceptible)
    reg s2_state = 0;
    always @(posedge sys_clk)
        if (man_posedge) s2_state <= ~s2_state;

    // led_evt: estira cualquier flanco a ~0.1 s para que el ojo lo capte
    // 27e6 × 0.1 s = 2_700_000 ciclos
    reg [21:0] evt_cnt = 0;
    always @(posedge sys_clk) begin
        if (man_posedge | rpi_posedge)
            evt_cnt <= 22'd2_700_000;
        else if (evt_cnt != 0)
            evt_cnt <= evt_cnt - 1'b1;
    end

    // Activo-bajo (LOW = encendido)
    assign led_s2  = ~s2_state;
    assign led_rpi = ~rpi_d2;            // nivel sincronizado del pin 77
    assign led_evt = ~(evt_cnt != 0);
endmodule
