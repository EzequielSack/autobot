"""
config.py — Configuración central del bot multi-par.
Modificar solo con análisis cuantitativo previo.

PARÁMETROS POR PAR (RSI, SL, TP, VOL, BB):
- BTCUSDT: validados en 1 año de backtest (PF 2.40, DD -16%)
- ETHUSDT: optimizados Jun 2026 (PF 1.50, DD -22%, 177 trades)
- SOLUSDT: optimizados Jun 2026 (PF 1.40, DD -22%, 251 trades, WR 50.6%)
"""

# ── Pares y configuración por par ─────────────────────────────────────────────
# Cada par tiene sus parámetros propios optimizados específicamente.
# Esto evita que un cambio para mejorar ETH/SOL perjudique a BTC (y viceversa).
PARES_CONFIG = {
    "BTCUSDT": {
        "priority": 1, "risk_weight": 0.50, "min_score": 60,
        # Parámetros validados (no tocar sin re-validación)
        "rsi_oversold": 30, "rsi_overbought": 70,
        "sl_mult": 1.2, "tp_mult": 4.2,
        "vol_mult": 1.5, "bb_min_width": 0.010,
    },
    "ETHUSDT": {
        "priority": 2, "risk_weight": 0.30, "min_score": 65,
        # Optimizados (equilibrio volumen + PF + drawdown)
        "rsi_oversold": 25, "rsi_overbought": 70,
        "sl_mult": 1.5, "tp_mult": 3.5,
        "vol_mult": 1.8, "bb_min_width": 0.012,
    },
    "SOLUSDT": {
        "priority": 3, "risk_weight": 0.20, "min_score": 75,
        # Optimizados (más volátil → TP corto + WR alto)
        "rsi_oversold": 25, "rsi_overbought": 65,
        "sl_mult": 1.5, "tp_mult": 2.5,
        "vol_mult": 1.8, "bb_min_width": 0.012,
    },
}

# ── Modo de operación ─────────────────────────────────────────────────────────
# REAL_TRADING: pares que colocan órdenes reales en Bybit
# PAPER_TRADING: pares simulados (log sin orden)
#
# Decisión Jun 2026: los 3 pares pasan a REAL.
# Backtests optimizados muestran PF > 1.4 y drawdown -22% individual.
# El bot opera UNA sola posición a la vez (MAX_POSICIONES_ABIERTAS = 1) →
# elige el mejor par por score en cada ciclo, no opera los 3 simultáneamente.
REAL_TRADING  = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
PAPER_TRADING = []

# ── Límites de exposición ─────────────────────────────────────────────────────
MAX_POSICIONES_ABIERTAS = 1        # solo una posición abierta a la vez
DAILY_LOSS_LIMIT        = 0.04     # pausa si pierde 4% del capital en el día
