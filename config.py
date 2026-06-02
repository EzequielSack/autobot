"""
config.py — Configuración central del bot multi-par.
Modificar solo con análisis cuantitativo previo.
"""

# ── Pares y pesos de riesgo ───────────────────────────────────────────────────
PARES_CONFIG = {
    "BTCUSDT": {"priority": 1, "risk_weight": 0.50, "min_score": 60},
    "ETHUSDT": {"priority": 2, "risk_weight": 0.30, "min_score": 65},
    "SOLUSDT": {"priority": 3, "risk_weight": 0.20, "min_score": 75},
}

# ── Modo de operación ─────────────────────────────────────────────────────────
# REAL_TRADING: pares que colocan órdenes reales en Bybit
# PAPER_TRADING: pares simulados (log sin orden) — ETH y SOL hasta validar
REAL_TRADING  = ["BTCUSDT"]
PAPER_TRADING = ["ETHUSDT", "SOLUSDT"]

# ── Límites de exposición ─────────────────────────────────────────────────────
MAX_POSICIONES_ABIERTAS = 1        # solo una posición abierta a la vez
DAILY_LOSS_LIMIT        = 0.04     # pausa si pierde 4% del capital en el día
