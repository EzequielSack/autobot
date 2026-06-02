"""
╔══════════════════════════════════════════════════════════╗
║         EZBOT v2.2 — Bot de Trading para Bybit           ║
║         6 Sensores: BB + RSI + ATR + EMA + VOL + ADX    ║
║         Autor: Canal @ezequielsack                       ║
║                                                          ║
║  Parámetros validados por análisis cuantitativo:         ║
║  • Riesgo: 0.5% | PF backtest: 2.83 | Sharpe: 6.46     ║
║  • Drawdown controlado: -13% esperado                    ║
║  • Walk-forward consistente (no sobreoptimizado)         ║
║                                                          ║
║  ⚠️  DISCLAIMER: No es asesoramiento financiero.         ║
║  El trading en futuros tiene riesgo real de pérdida      ║
║  total del capital. Usá solo lo que puedas perder.       ║
╚══════════════════════════════════════════════════════════╝
"""

import time
import logging
import os
from datetime import datetime, timezone
from pybit.unified_trading import HTTP
import pandas as pd
import numpy as np
from secure_env import load_secure_env
from config import PARES_CONFIG, MAX_POSICIONES_ABIERTAS, DAILY_LOSS_LIMIT, REAL_TRADING, PAPER_TRADING

# ─── CONFIGURACIÓN ────────────────────────────────────────
load_secure_env()

API_KEY    = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")
TESTNET    = os.getenv("TESTNET", "true").lower() == "true"

# Parámetros del bot
SYMBOLS        = list(PARES_CONFIG.keys())   # BTC (real) + ETH + SOL (paper)
LEVERAGE       = 3
RISK_PER_TRADE = 0.005               # 0.5% — óptimo según análisis cuantitativo
TIMEFRAME      = "5"                 # 5 minutos — ejecución
TIMEFRAME_HTF  = "60"               # 1 hora — filtro de tendencia
LOOP_SLEEP     = 60                  # segundos entre ciclos

# ── Sensor 1: Bollinger Bands (el elástico) ──────────────
BB_PERIOD = 20
BB_STD    = 2.0

# ── Sensor 2: RSI (el termómetro) ────────────────────────
RSI_PERIOD     = 14
RSI_OVERSOLD   = 30                  # señales más selectivas
RSI_OVERBOUGHT = 70

# ── Sensor 3: ATR (el viento) ────────────────────────────
ATR_PERIOD  = 14
ATR_SL_MULT = 1.2                    # SL ajustado — validado en backtests
ATR_TP_MULT = 4.2                    # ratio 1:3.5 — mejor PF

# ── Sensor 4: EMA en 1h (el río) ─────────────────────────
EMA_HTF_PERIOD = 50

# ── Sensor 5: Volumen (la cancha) ────────────────────────
VOL_PERIOD = 20
VOL_MULT   = 1.5                     # filtro estricto — menos señales falsas

# ── Sensor 6: ADX (la tormenta) ──────────────────────────
ADX_PERIOD    = 14
ADX_THRESHOLD = 25

# ─── FILTROS HORARIOS Y DIARIOS ───────────────────────────
# Análisis cuantitativo identificó horas y días malos:
SESSION_START = 8    # 08:00 UTC
SESSION_END   = 22   # 22:00 UTC
# Horas con PF < 1 dentro de la sesión — se saltan
HORAS_MALAS   = {10, 12, 13, 19, 21}   # 12h agregado: 0% win rate, -$104.80 en backtest
# Días malos: Viernes=4, Sábado=5 (0=Lunes)
DIAS_MALOS    = {4, 5}              # Viernes y Sábado

# ─── CIRCUIT BREAKER ──────────────────────────────────────
MAX_PERDIDAS_CONSECUTIVAS = 4       # pausa tras 4 pérdidas seguidas
PAUSA_MINUTOS             = 180     # 3 horas de pausa

# ─── LOGGING ──────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
os.makedirs("data", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(
            f"logs/ezbot_{datetime.now().strftime('%Y%m%d')}.log",
            encoding="utf-8"
        ),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("ezbot")

# ─── CONEXIÓN BYBIT ───────────────────────────────────────
session = HTTP(
    testnet=TESTNET,
    api_key=API_KEY,
    api_secret=API_SECRET,
)

# ─── ESTADO DEL CIRCUIT BREAKER ───────────────────────────
estado = {
    "perdidas_consecutivas": 0,
    "pausa_hasta":           None,   # datetime UTC o None
}


# ══════════════════════════════════════════════════════════
#   FUNCIONES DE DATOS
# ══════════════════════════════════════════════════════════

def get_klines(symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
    """Obtiene velas históricas de Bybit."""
    try:
        resp = session.get_kline(
            category="linear",
            symbol=symbol,
            interval=interval,
            limit=limit
        )
        data = resp["result"]["list"]
        df = pd.DataFrame(data, columns=[
            "timestamp", "open", "high", "low", "close", "volume", "turnover"
        ])
        df = df.astype({
            "open": float, "high": float,
            "low": float, "close": float, "volume": float
        })
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms")
        df = df.sort_values("timestamp").reset_index(drop=True)
        return df
    except Exception as e:
        log.error(f"Error obteniendo klines {symbol} {interval}: {e}")
        return pd.DataFrame()


def get_balance() -> float:
    """Retorna el balance disponible en USDT (robusto ante campos vacíos)."""
    try:
        resp = session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        acc  = resp["result"]["list"][0]
        # Intentar balance total de la cuenta primero
        for k in ("totalAvailableBalance", "totalEquity"):
            v = acc.get(k, "")
            if v not in ("", None):
                try:
                    return float(v)
                except (ValueError, TypeError):
                    pass
        # Fallback: buscar USDT en la lista de monedas
        for coin in acc.get("coin", []):
            if coin.get("coin") == "USDT":
                for k in ("availableToWithdraw", "walletBalance", "equity"):
                    v = coin.get(k, "")
                    if v not in ("", None):
                        try:
                            return float(v)
                        except (ValueError, TypeError):
                            pass
        return 0.0
    except Exception as e:
        log.error(f"Error obteniendo balance: {e}")
        return 0.0


def get_open_position(symbol: str) -> dict:
    """Retorna la posición abierta en un símbolo, si existe."""
    try:
        resp      = session.get_positions(category="linear", symbol=symbol)
        positions = resp["result"]["list"]
        for pos in positions:
            if float(pos.get("size", 0)) > 0:
                return pos
        return {}
    except Exception as e:
        log.error(f"Error obteniendo posición {symbol}: {e}")
        return {}


def get_ultimas_ops_cerradas(symbol: str, limit: int = 10) -> list:
    """Obtiene las últimas operaciones cerradas para el circuit breaker."""
    try:
        resp = session.get_closed_pnl(
            category="linear",
            symbol=symbol,
            limit=limit
        )
        return resp["result"]["list"]
    except Exception as e:
        log.error(f"Error obteniendo ops cerradas {symbol}: {e}")
        return []


# ══════════════════════════════════════════════════════════
#   LOS 6 SENSORES
# ══════════════════════════════════════════════════════════

def calc_bollinger(df: pd.DataFrame) -> pd.DataFrame:
    """EL ELÁSTICO — detecta extremos del rango."""
    df["bb_mid"]   = df["close"].rolling(BB_PERIOD).mean()
    df["bb_std"]   = df["close"].rolling(BB_PERIOD).std()
    df["bb_upper"] = df["bb_mid"] + BB_STD * df["bb_std"]
    df["bb_lower"] = df["bb_mid"] - BB_STD * df["bb_std"]
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
    return df


def calc_rsi(df: pd.DataFrame) -> pd.DataFrame:
    """EL TERMÓMETRO — RSI <30 sobreventa, >70 sobrecompra."""
    delta = df["close"].diff()
    gain  = delta.clip(lower=0).rolling(RSI_PERIOD).mean()
    loss  = (-delta.clip(upper=0)).rolling(RSI_PERIOD).mean()
    rs    = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    return df


def calc_atr(df: pd.DataFrame) -> pd.DataFrame:
    """EL VIENTO — mide volatilidad real para SL y TP automáticos."""
    df["tr"] = np.maximum(
        df["high"] - df["low"],
        np.maximum(
            abs(df["high"] - df["close"].shift(1)),
            abs(df["low"]  - df["close"].shift(1))
        )
    )
    df["atr"] = df["tr"].rolling(ATR_PERIOD).mean()
    return df


def get_htf_trend(symbol: str) -> str:
    """
    EL RÍO — EMA 50 en 1h para saber la tendencia mayor.
    Solo operamos a favor del río.
    Retorna: 'BULLISH', 'BEARISH' o 'NEUTRAL'
    """
    df_1h = get_klines(symbol, TIMEFRAME_HTF, limit=100)
    if df_1h.empty or len(df_1h) < EMA_HTF_PERIOD:
        return "NEUTRAL"
    ema50     = df_1h["close"].ewm(span=EMA_HTF_PERIOD, adjust=False).mean().iloc[-1]
    precio_1h = df_1h["close"].iloc[-1]
    if precio_1h > ema50 * 1.001:
        return "BULLISH"
    elif precio_1h < ema50 * 0.999:
        return "BEARISH"
    return "NEUTRAL"


def calc_volume_filter(df: pd.DataFrame) -> pd.DataFrame:
    """LA CANCHA — confirma participación real del mercado."""
    df["vol_avg"] = df["volume"].rolling(VOL_PERIOD).mean()
    df["vol_ok"]  = df["volume"] >= df["vol_avg"] * VOL_MULT
    return df


def calc_adx(df: pd.DataFrame) -> pd.DataFrame:
    """LA TORMENTA — fuerza de la tendencia. ADX>25 → reducir posición."""
    high  = df["high"]
    low   = df["low"]
    close = df["close"]

    plus_dm  = high.diff()
    minus_dm = low.diff().abs()
    plus_dm  = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)

    tr = np.maximum(
        high - low,
        np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1)))
    )
    atr_adx  = pd.Series(tr).rolling(ADX_PERIOD).mean()
    plus_di  = 100 * plus_dm.rolling(ADX_PERIOD).mean()  / atr_adx
    minus_di = 100 * minus_dm.rolling(ADX_PERIOD).mean() / atr_adx
    dx       = (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)) * 100
    df["adx"] = dx.rolling(ADX_PERIOD).mean()
    return df


def add_all_sensors(df: pd.DataFrame) -> pd.DataFrame:
    df = calc_bollinger(df)
    df = calc_rsi(df)
    df = calc_atr(df)
    df = calc_volume_filter(df)
    df = calc_adx(df)
    return df


# ══════════════════════════════════════════════════════════
#   FILTROS DE SESIÓN Y CIRCUIT BREAKER
# ══════════════════════════════════════════════════════════

def en_sesion_valida() -> bool:
    """
    Verifica que estemos en un horario y día válido para operar.
    Basado en análisis cuantitativo de PF por hora y día.
    """
    ahora    = datetime.now(timezone.utc)
    hora_utc = ahora.hour
    dia_sem  = ahora.weekday()   # 0=Lunes, 6=Domingo

    # Verificar día
    if dia_sem in DIAS_MALOS:
        log.info(f"📅 Día malo ({ahora.strftime('%A')}) — sin operar hoy")
        return False

    # Verificar sesión horaria
    if not (SESSION_START <= hora_utc < SESSION_END):
        log.info(f"🕐 Fuera de sesión ({hora_utc}h UTC) — esperando {SESSION_START}h")
        return False

    # Verificar hora mala dentro de la sesión
    if hora_utc in HORAS_MALAS:
        log.info(f"⚠️  Hora mala identificada ({hora_utc}h UTC) — saltando")
        return False

    return True


def verificar_circuit_breaker() -> bool:
    """
    Verifica si el circuit breaker está activo.
    Retorna True si podemos operar, False si estamos en pausa.
    """
    if estado["pausa_hasta"] is None:
        return True

    ahora = datetime.now(timezone.utc)
    if ahora >= estado["pausa_hasta"]:
        estado["pausa_hasta"] = None
        estado["perdidas_consecutivas"] = 0
        log.info("✅ Circuit breaker: pausa terminada — operando normal")
        return True

    minutos_restantes = (estado["pausa_hasta"] - ahora).seconds // 60
    log.info(f"⏸️  Circuit breaker activo — pausa por {minutos_restantes} minutos más")
    return False


def actualizar_circuit_breaker(resultado: str):
    """Actualiza el contador de pérdidas consecutivas."""
    if resultado == "LOSS":
        estado["perdidas_consecutivas"] += 1
        log.info(f"📉 Pérdidas consecutivas: {estado['perdidas_consecutivas']}/{MAX_PERDIDAS_CONSECUTIVAS}")
        if estado["perdidas_consecutivas"] >= MAX_PERDIDAS_CONSECUTIVAS:
            estado["pausa_hasta"] = datetime.now(timezone.utc).replace(
                microsecond=0
            )
            from datetime import timedelta
            estado["pausa_hasta"] = datetime.now(timezone.utc) + timedelta(minutes=PAUSA_MINUTOS)
            log.warning(
                f"🚨 CIRCUIT BREAKER ACTIVADO — "
                f"pausa de {PAUSA_MINUTOS} minutos hasta "
                f"{estado['pausa_hasta'].strftime('%H:%M UTC')}"
            )
    else:
        if estado["perdidas_consecutivas"] > 0:
            log.info(f"✅ Trade ganador — reseteando contador de pérdidas")
        estado["perdidas_consecutivas"] = 0


# ══════════════════════════════════════════════════════════
#   LÓGICA DE SEÑAL
# ══════════════════════════════════════════════════════════

def get_signal(df: pd.DataFrame, htf_trend: str, symbol: str = "BTCUSDT") -> tuple[str, float]:
    """
    Evalúa los 6 sensores al mismo tiempo usando parámetros específicos del par.
    Cada par (BTC/ETH/SOL) tiene sus umbrales propios en PARES_CONFIG.

    Retorna: (señal, multiplicador_posicion)
    señal = 'LONG', 'SHORT' o 'NONE'
    multiplicador = 1.0 (normal) o 0.5 (tormenta ADX)
    """
    # Cargar parámetros del par (cae a defaults globales si no está en config)
    cfg = PARES_CONFIG.get(symbol, {})
    rsi_os    = cfg.get("rsi_oversold",   RSI_OVERSOLD)
    rsi_ob    = cfg.get("rsi_overbought", RSI_OVERBOUGHT)
    bb_min    = cfg.get("bb_min_width",   0.010)
    vol_mult  = cfg.get("vol_mult",       VOL_MULT)

    min_candles = max(BB_PERIOD, RSI_PERIOD, ATR_PERIOD, ADX_PERIOD, VOL_PERIOD) + 5
    if df.empty or len(df) < min_candles:
        return "NONE", 1.0

    last = df.iloc[-1]
    prev = df.iloc[-2]

    precio   = last["close"]
    bb_lower = last["bb_lower"]
    bb_upper = last["bb_upper"]
    bb_width = last["bb_width"]
    rsi      = last["rsi"]
    atr      = last["atr"]
    # Filtro de volumen reevaluado con vol_mult específico del par
    vol_ok   = last["volume"] >= last["vol_avg"] * vol_mult
    adx      = last["adx"]

    # ── Filtros (umbrales por par) ────────────────────────
    if bb_width < bb_min:
        log.info("⏳ BB: bandas comprimidas — esperando expansión")
        return "NONE", 1.0

    atr_min = precio * 0.001
    if atr < atr_min:
        log.info("⏳ ATR: volatilidad insuficiente")
        return "NONE", 1.0

    if not vol_ok:
        log.info("⏳ Volumen: insuficiente — posible trampa")
        return "NONE", 1.0

    # ── Multiplicador por tormenta (ADX) ──────────────────
    pos_mult = 0.5 if adx > ADX_THRESHOLD else 1.0
    if pos_mult == 0.5:
        log.info(f"⚡ ADX alto ({adx:.1f}) — posición reducida al 50%")

    # ── Señal LONG (RSI específico del par) ───────────────
    long_bb  = prev["close"] > bb_lower and precio <= bb_lower
    long_rsi = rsi <= rsi_os
    long_htf = htf_trend == "BULLISH"

    if long_bb and long_rsi and long_htf:
        log.info(
            f"✅ SEÑAL LONG | precio: {precio:.2f} | "
            f"BB_lower: {bb_lower:.2f} | RSI: {rsi:.1f} | "
            f"Tendencia 1h: {htf_trend} | ADX: {adx:.1f}"
        )
        return "LONG", pos_mult

    # ── Señal SHORT (RSI específico del par) ──────────────
    short_bb  = prev["close"] < bb_upper and precio >= bb_upper
    short_rsi = rsi >= rsi_ob
    short_htf = htf_trend == "BEARISH"

    if short_bb and short_rsi and short_htf:
        log.info(
            f"✅ SEÑAL SHORT | precio: {precio:.2f} | "
            f"BB_upper: {bb_upper:.2f} | RSI: {rsi:.1f} | "
            f"Tendencia 1h: {htf_trend} | ADX: {adx:.1f}"
        )
        return "SHORT", pos_mult

    return "NONE", 1.0


# ══════════════════════════════════════════════════════════
#   GESTIÓN DE RIESGO Y ÓRDENES
# ══════════════════════════════════════════════════════════

def set_leverage(symbol: str):
    """Configura el apalancamiento del símbolo."""
    try:
        session.set_leverage(
            category="linear",
            symbol=symbol,
            buyLeverage=str(LEVERAGE),
            sellLeverage=str(LEVERAGE)
        )
        log.info(f"⚙️  Apalancamiento: {symbol} → {LEVERAGE}x")
    except Exception as e:
        log.warning(f"Apalancamiento ya configurado o error: {e}")


def calc_position_size(balance: float, atr: float, precio: float,
                       mult: float, risk_weight: float = 1.0,
                       sl_mult: float = None) -> float:
    """
    Riesgo efectivo = RISK_PER_TRADE × mult × risk_weight (del par).
    BTC: weight 0.50 | ETH: 0.30 | SOL: 0.20
    sl_mult: específico del par (BTC 1.2 · ETH/SOL 1.5). Cae al global si None.
    """
    if sl_mult is None:
        sl_mult = ATR_SL_MULT
    riesgo_usdt = balance * RISK_PER_TRADE * mult * risk_weight
    stop_dist   = atr * sl_mult
    qty_raw     = (riesgo_usdt * LEVERAGE) / stop_dist
    qty         = round(qty_raw, 3)
    return max(qty, 0.001)


def place_order(symbol: str, side: str, qty: float, precio: float, atr: float) -> dict:
    """
    Coloca la orden con SL y TP automáticos.
    SL = 1.2 × ATR | TP = 4.2 × ATR (ratio 1:3.5)
    NO usar salidas parciales ni BE — el análisis cuantitativo
    demostró que destruyen el PF de 2.57 a 1.14.
    """
    stop_dist = atr * ATR_SL_MULT
    tp_dist   = atr * ATR_TP_MULT

    if side == "Buy":
        sl = round(precio - stop_dist, 2)
        tp = round(precio + tp_dist,   2)
    else:
        sl = round(precio + stop_dist, 2)
        tp = round(precio - tp_dist,   2)

    try:
        resp = session.place_order(
            category="linear",
            symbol=symbol,
            side=side,
            orderType="Market",
            qty=str(qty),
            stopLoss=str(sl),
            takeProfit=str(tp),
            timeInForce="GoodTillCancel",
            reduceOnly=False,
        )
        log.info(
            f"📤 Orden ejecutada | {side} {qty} {symbol} | "
            f"precio: {precio:.2f} | SL: {sl} | TP: {tp} | "
            f"Riesgo: ${round(qty * stop_dist, 2)} USDT"
        )
        return resp
    except Exception as e:
        log.error(f"❌ Error colocando orden {symbol}: {e}")
        return {}


# ══════════════════════════════════════════════════════════
#   REGISTRO DE OPERACIONES
# ══════════════════════════════════════════════════════════

def log_trade(symbol, side, qty, precio, sl, tp, adx, rsi, htf_trend, pos_mult):
    """Guarda cada operación en CSV para seguimiento y análisis."""
    stop_dist = abs(precio - sl)
    row = {
        "timestamp":    datetime.now().isoformat(),
        "symbol":       symbol,
        "side":         side,
        "qty":          qty,
        "entry_price":  precio,
        "stop_loss":    sl,
        "take_profit":  tp,
        "sl_dist_usdt": round(stop_dist, 2),
        "riesgo_usd":   round(qty * stop_dist, 2),
        "rsi_entrada":  round(rsi, 1),
        "adx_entrada":  round(adx, 1),
        "tendencia_1h": htf_trend,
        "pos_mult":     pos_mult,
        "resultado":    "",   # WIN / LOSS — completar manualmente o con trailing.py
    }
    df_log = pd.DataFrame([row])
    path   = "data/trades.csv"
    header = not os.path.exists(path)
    df_log.to_csv(path, mode="a", header=header, index=False)
    log.info("📋 Trade registrado en data/trades.csv")


# ══════════════════════════════════════════════════════════
#   SCORE DE CALIDAD DE SEÑAL
# ══════════════════════════════════════════════════════════

def calc_signal_score(df: pd.DataFrame, htf_trend: str, signal: str, symbol: str) -> int:
    """
    Puntúa la calidad de una señal entre 0 y 100.
    Permite comparar señales de distintos pares y elegir la mejor.
    Solo tiene sentido cuando signal != 'NONE'.
    """
    if signal == "NONE" or df.empty or len(df) < 2:
        return 0

    last  = df.iloc[-1]
    precio = float(last["close"])
    score  = 0

    # ── BB: profundidad de penetración ───────────────────────
    bb_lower = float(last.get("bb_lower", precio))
    bb_upper = float(last.get("bb_upper", precio))
    if signal == "LONG":
        penetracion = (bb_lower - precio) / max(bb_lower, 1) * 100
    else:
        penetracion = (precio - bb_upper) / max(bb_upper, 1) * 100
    if penetracion >= 0.3:    score += 25
    elif penetracion >= 0.0:  score += 18
    else:                     score += 10

    # ── RSI: cuán extremo está el momentum ───────────────────
    rsi = float(last.get("rsi", 50))
    if signal == "LONG":
        if rsi < 20:    score += 25
        elif rsi < 25:  score += 20
        else:           score += 15   # <= 30
    else:
        if rsi > 80:    score += 25
        elif rsi > 75:  score += 20
        else:           score += 15   # >= 70

    # ── HTF/EMA: siempre confirmado si signal != NONE ────────
    score += 20

    # ── Volumen: cuánto supera el promedio ────────────────────
    vol_avg = float(last.get("vol_avg", 1)) or 1
    vol     = float(last.get("volume", 0))
    if last.get("vol_ok", False):
        ratio = vol / vol_avg
        if ratio >= 2.0:    score += 15
        elif ratio >= 1.5:  score += 12
        else:               score += 8

    # ── ADX: tendencia clara pero no agotada ──────────────────
    adx = last.get("adx", 0)
    try:
        adx = float(adx)
        if pd.isna(adx): adx = 0
    except (TypeError, ValueError):
        adx = 0
    if 20 <= adx <= 40:   score += 15
    elif adx > 40:        score += 8
    elif adx >= 15:       score += 5

    # ── ATR: volatilidad razonable ────────────────────────────
    atr = float(last.get("atr", 0)) if last.get("atr") else 0
    if atr > 0 and precio > 0:
        atr_pct = atr / precio * 100
        if atr_pct < 0.5:    score += 10
        elif atr_pct < 1.0:  score += 7
        elif atr_pct < 1.5:  score += 3
        else:                score -= 5

    # ── Penalización base por par ─────────────────────────────
    if symbol == "SOLUSDT":
        score -= 10
    elif symbol == "ETHUSDT":
        score -= 5

    return max(0, min(100, int(score)))


# ══════════════════════════════════════════════════════════
#   LOOP PRINCIPAL
# ══════════════════════════════════════════════════════════

def limpiar_logs_viejos(dias: int = 7) -> None:
    """Elimina logs con más de `dias` días de antigüedad."""
    import glob
    from datetime import timedelta
    limite = datetime.now() - timedelta(days=dias)
    for ruta in glob.glob("logs/*.log"):
        try:
            if datetime.fromtimestamp(os.path.getmtime(ruta)) < limite:
                os.remove(ruta)
                log.info(f"🧹 Log eliminado: {ruta}")
        except Exception:
            pass


def run():
    limpiar_logs_viejos(dias=7)
    log.info("=" * 62)
    log.info("  AUTOBOT v3.0 — Multi-par BTC + ETH + SOL")
    log.info(f"  Modo       : {'TESTNET' if TESTNET else 'REAL'}")
    log.info(f"  Real       : {REAL_TRADING}")
    log.info(f"  Paper      : {PAPER_TRADING}")
    log.info(f"  Leverage   : {LEVERAGE}x | Riesgo base: {RISK_PER_TRADE*100}%")
    log.info(f"  SL/TP      : {ATR_SL_MULT}xATR / {ATR_TP_MULT}xATR (1:3.5)")
    log.info(f"  Sesion     : {SESSION_START}h-{SESSION_END}h UTC")
    log.info(f"  Dias off   : Viernes y Sabado")
    log.info(f"  Horas off  : {sorted(HORAS_MALAS)}")
    log.info(f"  Breaker    : {MAX_PERDIDAS_CONSECUTIVAS} perdidas -> {PAUSA_MINUTOS}min pausa")
    log.info(f"  Daily limit: -{DAILY_LOSS_LIMIT*100:.0f}% capital/dia")
    log.info("=" * 62)

    for sym in SYMBOLS:
        set_leverage(sym)

    capital_inicio_dia: float = 0.0
    fecha_actual = None
    ciclo = 0

    while True:
        ciclo += 1

        # ── Reset diario ──────────────────────────────────────
        hoy = datetime.now(timezone.utc).date()
        if hoy != fecha_actual:
            fecha_actual = hoy
            capital_inicio_dia = get_balance()
            log.info(f"Nuevo dia UTC — capital base: ${capital_inicio_dia:.2f} USDT")

        log.info(f"\n{'─'*46}")
        log.info(f"Ciclo #{ciclo} | {datetime.now().strftime('%H:%M:%S')}")
        log.info(f"{'─'*46}")

        # ── Sesion valida ─────────────────────────────────────
        if not en_sesion_valida():
            log.info(f"Proximo ciclo en {LOOP_SLEEP}s...")
            time.sleep(LOOP_SLEEP)
            continue

        # ── Circuit breaker ───────────────────────────────────
        if not verificar_circuit_breaker():
            time.sleep(LOOP_SLEEP)
            continue

        # ── Balance y daily loss limit ────────────────────────
        balance = get_balance()
        log.info(f"Balance: ${balance:.2f} USDT")

        if balance < 10:
            log.warning("Balance insuficiente — esperando...")
            time.sleep(LOOP_SLEEP * 5)
            continue

        if capital_inicio_dia > 0:
            perdida_pct = (capital_inicio_dia - balance) / capital_inicio_dia
            if perdida_pct >= DAILY_LOSS_LIMIT:
                log.warning(
                    f"DAILY LOSS LIMIT: -{perdida_pct*100:.1f}% "
                    f"(${capital_inicio_dia - balance:.2f}) — pausando hasta manana"
                )
                time.sleep(LOOP_SLEEP)
                continue

        # ── Posicion abierta en CUALQUIER par → esperar ───────
        pos_abierta = None
        for sym in SYMBOLS:
            p = get_open_position(sym)
            if p:
                pnl = float(p.get("unrealisedPnl", 0))
                log.info(f"Posicion abierta {sym} | PnL: ${pnl:.4f} — esperando cierre")
                pos_abierta = sym
                break

        if pos_abierta:
            time.sleep(LOOP_SLEEP)
            continue

        # ── Evaluar señales en todos los pares ────────────────
        candidatos = []
        btc_atr_pct: float = 0.0

        for sym in sorted(SYMBOLS, key=lambda s: PARES_CONFIG[s]["priority"]):
            htf = get_htf_trend(sym)
            log.info(f"[{sym}] Tendencia 1h: {htf}")

            if htf == "NEUTRAL":
                log.info(f"[{sym}] Score: 0 | neutral — sin evaluar")
                continue

            df = get_klines(sym, TIMEFRAME, limit=200)
            if df.empty:
                continue
            df = add_all_sensors(df)

            last = df.iloc[-1]

            # Guardar ATR% de BTC como referencia de volatilidad
            if sym == "BTCUSDT" and float(last["close"]) > 0:
                btc_atr_pct = float(last["atr"]) / float(last["close"]) * 100

            # Filtros extra para SOL
            if sym == "SOLUSDT":
                sol_atr_pct = float(last["atr"]) / float(last["close"]) * 100
                adx_sol     = float(last.get("adx", 0) or 0)
                if btc_atr_pct > 0 and sol_atr_pct > btc_atr_pct * 2:
                    log.info(
                        f"[{sym}] Score: — | ATR% {sol_atr_pct:.2f}% "
                        f"> 2x BTC {btc_atr_pct:.2f}% — volatilidad excesiva"
                    )
                    continue
                if not pd.isna(adx_sol) and adx_sol < 20:
                    log.info(f"[{sym}] Score: — | ADX {adx_sol:.1f} < 20 — tendencia insuficiente")
                    continue

            signal, pos_mult = get_signal(df, htf, sym)
            score = calc_signal_score(df, htf, signal, sym)
            min_score = PARES_CONFIG[sym]["min_score"]

            if signal != "NONE" and score >= min_score:
                candidatos.append({
                    "symbol": sym, "signal": signal, "score": score,
                    "pos_mult": pos_mult, "df": df, "htf": htf,
                })
                log.info(f"[{sym}] Score: {score:3d} | Señal: {signal} | CANDIDATO")
            else:
                razon = "sin señal" if signal == "NONE" else f"score {score} < min {min_score}"
                log.info(f"[{sym}] Score: {score:3d} | {razon}")

        # ── Sin candidatos ────────────────────────────────────
        if not candidatos:
            log.info("Sin candidatos este ciclo")
            time.sleep(LOOP_SLEEP)
            continue

        # ── Elegir mejor señal (score DESC, prioridad ASC) ────
        mejor = sorted(
            candidatos,
            key=lambda c: (-c["score"], PARES_CONFIG[c["symbol"]]["priority"])
        )[0]

        for c in candidatos:
            if c["symbol"] != mejor["symbol"]:
                log.info(
                    f"[{c['symbol']}] Score: {c['score']:3d} | omitido "
                    f"(mejor: {mejor['symbol']} score {mejor['score']})"
                )

        # ── Ejecutar o paper trade ────────────────────────────
        sym      = mejor["symbol"]
        signal   = mejor["signal"]
        pos_mult = mejor["pos_mult"]
        df       = mejor["df"]
        htf      = mejor["htf"]
        is_real  = sym in REAL_TRADING
        modo     = "REAL" if is_real else "PAPER"

        last   = df.iloc[-1]
        precio = float(last["close"])
        atr    = float(last["atr"])
        rsi    = float(last["rsi"])
        adx    = float(last.get("adx", 0) or 0)

        # SL/TP específicos del par (BTC: 1.2/4.2 · ETH: 1.5/3.5 · SOL: 1.5/2.5)
        sl_mult_par = PARES_CONFIG[sym].get("sl_mult", ATR_SL_MULT)
        tp_mult_par = PARES_CONFIG[sym].get("tp_mult", ATR_TP_MULT)

        risk_w = PARES_CONFIG[sym]["risk_weight"]
        qty    = calc_position_size(balance, atr, precio, pos_mult, risk_w, sl_mult_par)
        side   = "Buy" if signal == "LONG" else "Sell"

        stop_dist = atr * sl_mult_par
        tp_dist   = atr * tp_mult_par
        sl = round(precio - stop_dist if side == "Buy" else precio + stop_dist, 4)
        tp = round(precio + tp_dist   if side == "Buy" else precio - tp_dist,   4)
        riesgo_usdt = round(qty * stop_dist, 2)

        log.info(
            f"[{modo}] {sym} | {signal} | qty: {qty} | precio: {precio:.4f} | "
            f"SL: {sl} | TP: {tp} | riesgo: ${riesgo_usdt} | score: {mejor['score']}"
        )

        if is_real:
            resp = place_order(sym, side, qty, precio, atr)
            if resp:
                log_trade(sym, side, qty, precio, sl, tp, adx, rsi, htf, pos_mult)
        else:
            log.info(
                f"[PAPER] {sym} — orden simulada: {side} {qty} @ {precio:.4f} "
                f"SL: {sl} TP: {tp} | riesgo simulado: ${riesgo_usdt}"
            )

        log.info(f"Proximo ciclo en {LOOP_SLEEP}s...")
        time.sleep(LOOP_SLEEP)


# ─── ENTRADA ──────────────────────────────────────────────
if __name__ == "__main__":
    run()
