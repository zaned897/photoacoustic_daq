# Etapa 2 — Enlace UART (aislado)

**Valida:** SOLO el camino pin 17 → FTDI → host. Sin trigger, sin ADC, sin PLL.
Este es el nudo que perseguimos todo el día (los "0 bytes").

## Qué hace el FPGA
- Transmite un **contador de 8 bits** (0,1,2,…,255,0,…) continuo a 115200 baud.
- Parpadea un LED (pin 10) a ~0.8 Hz como prueba de vida.

## Build & Flash
Abre esta carpeta en VSCode → Build & Flash. Incluye `top.v` + `uart_tx.v`
+ `uart_link.cst`.

## Validar
```bash
pipenv run python bringup/stage2_uart_link/validate.py
```

## Criterio de PASA
- El LED parpadea (FPGA vivo) **y** el validador imprime:
  `✅ secuencia OK  ~11000 B/s  sin saltos`

## Lectura de resultados
| Salida | Significado | Acción |
|---|---|---|
| `✅ secuencia OK` | Enlace y baud perfectos | Avanzar a etapa 3 |
| `⚠ N saltos` | Llegan bytes pero corruptos | Baud marginal o ruido; bajar baud / revisar cableado |
| `── 0 bytes ──` pero LED parpadea | FPGA vivo, **enlace muerto** | Problema físico pin 17→FTDI RX, o GND, o puerto equivocado |
| LED apagado/fijo + 0 bytes | FPGA no configuró | Reflashear, power-cycle |

## Por qué 115200 y no 1 Mbaud
115200 es el baud más robusto y es el que funcionó con el voltímetro. Si esto
pasa, subimos `BAUD_RATE` en `top.v` y `BAUD` en `validate.py` a 1_000_000 y
repetimos — así sabemos si el problema (si lo hay) aparece solo a alta velocidad.
