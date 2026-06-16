"""
grid_config.py — Configuración del Grid Bot (futuros Bybit, órdenes maker).

ESTRATEGIA: poner una grilla de órdenes LIMIT (PostOnly = maker, fee 0.02%)
de compra debajo del precio y de venta arriba. Cada vez que el precio
oscila y cruza un nivel, se captura el spacing del grid menos fees.

Por qué FUTUROS y no SPOT:
  - Maker futuros = 0.02% | Maker spot = 0.10% (5x más caro)
  - Round-trip futuros = 0.04% → con spacing 0.25% el neto por ciclo es ~0.21%

Por qué SOL y ETH y no BTC:
  - minOrderQty BTC = 0.001 ≈ $65 por nivel → grilla muy gruesa para $500
  - SOL minQty 0.1 ≈ $7 · ETH minQty 0.01 ≈ $18 → permiten grilla fina
"""

# ── Modo de ejecución ─────────────────────────────────────────────────────────
# DRY_RUN True  → calcula y loguea la grilla, NO coloca órdenes (verificación)
# DRY_RUN False → coloca órdenes reales (¡dinero real si TESTNET=False!)
DRY_RUN = False

# TESTNET None → usa el valor de .env. True/False → fuerza el entorno aquí.
TESTNET_OVERRIDE = None

# ── Capital y seguridad global ────────────────────────────────────────────────
CAPITAL_TOTAL_USDT   = 500.0    # referencia para sizing y límites
MARGEN_BUFFER_USDT   = 200.0    # margen que NO se compromete (anti-liquidación)
LEVERAGE             = 2        # apalancamiento bajo: grid + leverage = riesgo
LOOP_SLEEP           = 15       # segundos entre reconciliaciones

# ── Definición de cada grilla por par ─────────────────────────────────────────
# range_pct : semi-ancho de la grilla respecto al precio medio (±).
# niveles   : cantidad de órdenes a CADA lado del precio (total = niveles*2).
# qty       : tamaño por orden, en unidades del activo. Debe ser >= minOrderQty
#             y múltiplo de qtyStep (SOL step 0.1 · ETH step 0.01).
# stop_pct  : si el precio se aleja este % del centro, se pausa y cancela todo
#             (protección contra tendencia que rompe el rango).
GRIDS = {
    "SOLUSDT": {
        "activo":    True,
        "range_pct": 0.020,   # ±2.0% alrededor del precio
        "niveles":   10,      # 10 compras + 10 ventas = spacing ~0.2%
        "qty":       0.1,     # ≈ $7.3 por nivel · 20 niveles ≈ $146 inventario máx
        "stop_pct":  0.035,   # pausa si se va ±3.5% del centro
    },
    "ETHUSDT": {
        "activo":    True,
        "range_pct": 0.018,   # ±1.8%
        "niveles":   8,       # 8 + 8 = 16 órdenes, spacing ~0.225%
        "qty":       0.01,    # ≈ $17.8 por nivel · 16 niveles ≈ $142 inventario máx
        "stop_pct":  0.030,
    },
    "BTCUSDT": {
        "activo":    False,   # desactivado: minQty muy grande para $500
        "range_pct": 0.015,
        "niveles":   5,
        "qty":       0.001,
        "stop_pct":  0.025,
    },
}

# ── Fees (referencia, para cálculo de rentabilidad neta) ──────────────────────
MAKER_FEE = 0.0002   # 0.02% futuros
TAKER_FEE = 0.00055  # 0.055% futuros (sólo si una orden cruza el spread)
