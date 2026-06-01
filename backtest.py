"""
╔══════════════════════════════════════════════════════════╗
║         EZBOT v2.0 — Backtesting                         ║
║         6 Sensores: BB + RSI + ATR + EMA + VOL + ADX    ║
║         Datos: Bybit público (sin API key)               ║
║         Período: últimos 6 meses, 5min                   ║
╚══════════════════════════════════════════════════════════╝
"""

import os
import time
import logging
import requests
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")   # sin GUI — guarda directo a archivo
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta, timezone

# ─── PARÁMETROS v2.1 (OPTIMIZADOS) ───────────────────────
SYMBOLS        = ["BTCUSDT"]   # ETH eliminado — profit factor insuficiente
TIMEFRAME      = "5"
TIMEFRAME_HTF  = "60"
LEVERAGE       = 3
RISK_PER_TRADE = 0.01          # MEJORA 1: 1% (antes 2%) → reduce drawdown ~50%
CAPITAL_INICIAL = 300.0

# Bollinger Bands
BB_PERIOD = 20
BB_STD    = 2.0

# RSI — MEJORA 5: umbrales más extremos → señales más confiables
RSI_PERIOD     = 14
RSI_OVERSOLD   = 30            # antes 35
RSI_OVERBOUGHT = 70            # antes 65

# ATR — MEJORA: SL más ajustado + TP más amplio → ratio 1:3.5
ATR_PERIOD  = 14
ATR_SL_MULT = 1.2   # antes 1.5 → perdés menos por trade perdedor
ATR_TP_MULT = 4.2   # antes 3.0 → ganás más por trade ganador

# EMA 1h
EMA_HTF_PERIOD = 50

# Volumen — MEJORA: filtro más estricto → menos señales falsas
VOL_PERIOD = 20
VOL_MULT   = 1.5   # antes 1.2

# ADX
ADX_PERIOD    = 14
ADX_THRESHOLD = 25

# MEJORA 3: Filtro horario — solo operar en mercado activo (UTC)
SESSION_START = 8    # 08:00 UTC (Londres abre)
SESSION_END   = 22   # 22:00 UTC (Nueva York cierra)

# MEJORA 4: Circuit breaker — pausa tras N pérdidas consecutivas
MAX_PERDIDAS_CONSECUTIVAS = 3
PAUSA_CIRCUIT_BREAKER     = 48  # velas de 5min = 4 horas

# ─── ENDPOINT PÚBLICO DE BYBIT (sin API key) ──────────────
BYBIT_BASE = "https://api.bybit.com"

# ─── LOGGING ──────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
os.makedirs("data", exist_ok=True)

import sys
_stream_handler = logging.StreamHandler(stream=open(sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False))
_stream_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(f"logs/backtest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log", encoding="utf-8"),
        _stream_handler,
    ]
)
log = logging.getLogger("backtest")


# ══════════════════════════════════════════════════════════
#   DESCARGA DE DATOS HISTÓRICOS (ENDPOINT PÚBLICO BYBIT)
# ══════════════════════════════════════════════════════════

def _fetch_klines_chunk(symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    """Descarga un chunk de hasta 1000 velas del endpoint público de Bybit."""
    url    = f"{BYBIT_BASE}/v5/market/kline"
    params = {
        "category": "linear",
        "symbol":   symbol,
        "interval": interval,
        "start":    start_ms,
        "end":      end_ms,
        "limit":    1000,
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("retCode") != 0:
        raise ValueError(f"Bybit API error: {data.get('retMsg')}")
    return data["result"]["list"]


def get_klines_historico(symbol: str, interval: str, dias: int = 180) -> pd.DataFrame:
    """
    Descarga datos históricos del endpoint público de Bybit
    paginando en bloques de 1000 velas.

    Args:
        symbol:   Ej. 'BTCUSDT'
        interval: '5' para 5 minutos, '60' para 1 hora
        dias:     Cantidad de días hacia atrás (default: 180 = 6 meses)

    Returns:
        DataFrame ordenado cronológicamente con OHLCV
    """
    log.info(f"📥 Descargando {symbol} {interval}min — últimos {dias} días...")

    ahora_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    inicio_ms = ahora_ms - dias * 24 * 60 * 60 * 1000

    # Bybit devuelve las velas más recientes primero, paginamos hacia atrás
    end_ms   = ahora_ms
    todos    = []
    intentos = 0

    while end_ms > inicio_ms:
        intentos += 1
        try:
            chunk = _fetch_klines_chunk(symbol, interval, inicio_ms, end_ms)
        except Exception as e:
            log.error(f"Error en chunk {intentos}: {e}")
            time.sleep(2)
            continue

        if not chunk:
            break

        todos.extend(chunk)

        # El timestamp más antiguo del chunk → siguiente end
        timestamps = [int(row[0]) for row in chunk]
        end_ms     = min(timestamps) - 1

        log.info(f"  Chunk {intentos:>3} | velas acumuladas: {len(todos):>6}")
        time.sleep(0.2)   # respetar rate limits

        # Seguridad: si ya tenemos más de 100k velas, paramos
        if len(todos) > 100_000:
            log.warning("Límite de 100k velas alcanzado — cortando descarga")
            break

    if not todos:
        log.error(f"No se obtuvieron datos para {symbol}")
        return pd.DataFrame()

    df = pd.DataFrame(todos, columns=[
        "timestamp", "open", "high", "low", "close", "volume", "turnover"
    ])
    df = df.astype({
        "open":   float,
        "high":   float,
        "low":    float,
        "close":  float,
        "volume": float,
    })
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms", utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)

    # Eliminar duplicados que pueden aparecer en los bordes de los chunks
    df = df.drop_duplicates(subset="timestamp")

    log.info(f"✅ {symbol}: {len(df)} velas descargadas | "
             f"{df['timestamp'].iloc[0].date()} → {df['timestamp'].iloc[-1].date()}")
    return df


# ══════════════════════════════════════════════════════════
#   LOS 6 SENSORES — IDÉNTICOS AL BOT.PY
# ══════════════════════════════════════════════════════════

def calc_bollinger(df: pd.DataFrame) -> pd.DataFrame:
    df["bb_mid"]   = df["close"].rolling(BB_PERIOD).mean()
    df["bb_std"]   = df["close"].rolling(BB_PERIOD).std()
    df["bb_upper"] = df["bb_mid"] + BB_STD * df["bb_std"]
    df["bb_lower"] = df["bb_mid"] - BB_STD * df["bb_std"]
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
    return df


def calc_rsi(df: pd.DataFrame) -> pd.DataFrame:
    delta = df["close"].diff()
    gain  = delta.clip(lower=0).rolling(RSI_PERIOD).mean()
    loss  = (-delta.clip(upper=0)).rolling(RSI_PERIOD).mean()
    rs    = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    return df


def calc_atr(df: pd.DataFrame) -> pd.DataFrame:
    df["tr"] = np.maximum(
        df["high"] - df["low"],
        np.maximum(
            abs(df["high"] - df["close"].shift(1)),
            abs(df["low"]  - df["close"].shift(1))
        )
    )
    df["atr"] = df["tr"].rolling(ATR_PERIOD).mean()
    return df


def calc_volume_filter(df: pd.DataFrame) -> pd.DataFrame:
    df["vol_avg"] = df["volume"].rolling(VOL_PERIOD).mean()
    df["vol_ok"]  = df["volume"] >= df["vol_avg"] * VOL_MULT
    return df


def calc_adx(df: pd.DataFrame) -> pd.DataFrame:
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


def calc_htf_ema(df_1h: pd.DataFrame) -> pd.Series:
    """Calcula EMA 50 sobre el DataFrame de 1h."""
    return df_1h["close"].ewm(span=EMA_HTF_PERIOD, adjust=False).mean()


def add_all_sensors(df: pd.DataFrame) -> pd.DataFrame:
    df = calc_bollinger(df)
    df = calc_rsi(df)
    df = calc_atr(df)
    df = calc_volume_filter(df)
    df = calc_adx(df)
    return df


# ══════════════════════════════════════════════════════════
#   FILTRO DE TENDENCIA 1H — VECTORIZADO PARA BACKTEST
# ══════════════════════════════════════════════════════════

def build_htf_trend_series(df_1h: pd.DataFrame, df_5m: pd.DataFrame) -> pd.Series:
    """
    Para cada vela de 5min, determina la tendencia según la EMA 50 en 1h
    usando solo datos disponibles en ese momento (sin look-ahead).

    Retorna una Series alineada al índice del df_5m.
    """
    df_1h = df_1h.copy()
    df_1h["ema50"]  = calc_htf_ema(df_1h)
    df_1h["trend"]  = "NEUTRAL"
    df_1h.loc[df_1h["close"] > df_1h["ema50"] * 1.001, "trend"] = "BULLISH"
    df_1h.loc[df_1h["close"] < df_1h["ema50"] * 0.999, "trend"] = "BEARISH"
    df_1h = df_1h.set_index("timestamp")

    # Reindexar al timeframe de 5min: cada vela 5m toma el último valor 1h disponible
    trend_reindexed = df_1h["trend"].reindex(df_5m["timestamp"], method="ffill")
    trend_reindexed.index = df_5m.index
    return trend_reindexed.fillna("NEUTRAL")


# ══════════════════════════════════════════════════════════
#   MOTOR DE BACKTEST
# ══════════════════════════════════════════════════════════

def get_signal_bt(df: pd.DataFrame, i: int, htf_trend: str) -> tuple[str, float]:
    """
    Evalúa los 6 sensores en la vela i del backtest.
    Lógica idéntica a get_signal() del bot.py.
    """
    min_candles = max(BB_PERIOD, RSI_PERIOD, ATR_PERIOD, ADX_PERIOD, VOL_PERIOD) + 5
    if i < min_candles:
        return "NONE", 1.0

    last = df.iloc[i]
    prev = df.iloc[i - 1]

    precio   = last["close"]
    bb_lower = last["bb_lower"]
    bb_upper = last["bb_upper"]
    bb_width = last["bb_width"]
    rsi      = last["rsi"]
    atr      = last["atr"]
    vol_ok   = last["vol_ok"]
    adx      = last["adx"]

    # Filtros globales
    if pd.isna(bb_width) or bb_width < 0.01:
        return "NONE", 1.0
    if pd.isna(atr) or atr < precio * 0.001:
        return "NONE", 1.0
    if not vol_ok:
        return "NONE", 1.0

    pos_mult = 0.5 if (not pd.isna(adx) and adx > ADX_THRESHOLD) else 1.0

    # Señal LONG
    long_bb  = prev["close"] > bb_lower and precio <= bb_lower
    long_rsi = (not pd.isna(rsi)) and rsi <= RSI_OVERSOLD
    long_htf = htf_trend == "BULLISH"

    if long_bb and long_rsi and long_htf:
        return "LONG", pos_mult

    # Señal SHORT
    short_bb  = prev["close"] < bb_upper and precio >= bb_upper
    short_rsi = (not pd.isna(rsi)) and rsi >= RSI_OVERBOUGHT
    short_htf = htf_trend == "BEARISH"

    if short_bb and short_rsi and short_htf:
        return "SHORT", pos_mult

    return "NONE", 1.0


def calc_position_size_bt(capital: float, atr: float, precio: float, mult: float) -> float:
    """Idéntico a calc_position_size() del bot.py."""
    riesgo_usdt = capital * RISK_PER_TRADE * mult
    stop_dist   = atr * ATR_SL_MULT
    qty_raw     = (riesgo_usdt * LEVERAGE) / stop_dist
    return max(round(qty_raw, 6), 0.000001)


def simular_operaciones(df: pd.DataFrame, htf_trend_series: pd.Series, symbol: str) -> list:
    """
    Recorre el DataFrame vela a vela simulando las operaciones.
    Respeta la regla de "una posición a la vez" por símbolo.

    Returns:
        Lista de dicts con el resultado de cada operación.
    """
    capital       = CAPITAL_INICIAL
    operaciones          = []
    en_posicion          = False
    trade_actual         = {}
    perdidas_consecutivas = 0
    velas_pausa          = 0   # contador de velas restantes de pausa (circuit breaker)

    for i in range(len(df)):
        row       = df.iloc[i]
        precio    = row["close"]
        high_vela = row["high"]
        low_vela  = row["low"]
        hora_utc  = pd.to_datetime(row["timestamp"]).hour

        # MEJORA 3: Filtro horario — solo operar en sesión activa
        en_sesion = SESSION_START <= hora_utc < SESSION_END

        # MEJORA 4: Circuit breaker — contar velas de pausa
        if velas_pausa > 0:
            velas_pausa -= 1

        # ── Gestión de posición abierta ────────────────────
        if en_posicion:
            sl   = trade_actual["sl"]
            tp   = trade_actual["tp"]
            side = trade_actual["side"]

            cerrado = False
            if side == "LONG":
                if low_vela  <= sl:
                    precio_cierre = sl
                    resultado     = "LOSS"
                    cerrado       = True
                elif high_vela >= tp:
                    precio_cierre = tp
                    resultado     = "WIN"
                    cerrado       = True
            else:  # SHORT
                if high_vela >= sl:
                    precio_cierre = sl
                    resultado     = "LOSS"
                    cerrado       = True
                elif low_vela  <= tp:
                    precio_cierre = tp
                    resultado     = "WIN"
                    cerrado       = True

            if cerrado:
                qty         = trade_actual["qty"]
                entry_price = trade_actual["entry_price"]

                if side == "LONG":
                    pnl_bruto = (precio_cierre - entry_price) * qty * LEVERAGE
                else:
                    pnl_bruto = (entry_price - precio_cierre) * qty * LEVERAGE

                # Comisiones Bybit: 0.055% maker + 0.055% taker ≈ 0.11% round-trip
                comision = entry_price * qty * 0.00055 + precio_cierre * qty * 0.00055
                pnl_neto = pnl_bruto - comision

                capital += pnl_neto

                operaciones.append({
                    "symbol":        symbol,
                    "side":          side,
                    "entry_time":    trade_actual["entry_time"],
                    "exit_time":     row["timestamp"],
                    "entry_price":   entry_price,
                    "exit_price":    precio_cierre,
                    "qty":           qty,
                    "sl":            sl,
                    "tp":            tp,
                    "pnl_bruto":     round(pnl_bruto, 4),
                    "comision":      round(comision, 4),
                    "pnl_neto":      round(pnl_neto, 4),
                    "resultado":     resultado,
                    "capital_post":  round(capital, 4),
                    "rsi_entrada":   trade_actual["rsi"],
                    "adx_entrada":   trade_actual["adx"],
                    "htf_trend":     trade_actual["htf_trend"],
                    "pos_mult":      trade_actual["pos_mult"],
                })

                en_posicion  = False
                trade_actual = {}

                # Actualizar circuit breaker
                if resultado == "LOSS":
                    perdidas_consecutivas += 1
                    if perdidas_consecutivas >= MAX_PERDIDAS_CONSECUTIVAS:
                        velas_pausa = PAUSA_CIRCUIT_BREAKER
                        perdidas_consecutivas = 0
                else:
                    perdidas_consecutivas = 0

        # ── Buscar nueva señal si no hay posición ──────────
        if not en_posicion and capital > 10 and en_sesion and velas_pausa == 0:
            htf_trend    = htf_trend_series.iloc[i]
            signal, mult = get_signal_bt(df, i, htf_trend)

            if signal != "NONE":
                atr = row["atr"]
                if pd.isna(atr) or atr == 0:
                    continue

                qty = calc_position_size_bt(capital, atr, precio, mult)

                stop_dist = atr * ATR_SL_MULT
                tp_dist   = atr * ATR_TP_MULT

                if signal == "LONG":
                    sl_precio = precio - stop_dist
                    tp_precio = precio + tp_dist
                else:
                    sl_precio = precio + stop_dist
                    tp_precio = precio - tp_dist

                trade_actual = {
                    "side":        signal,
                    "entry_time":  row["timestamp"],
                    "entry_price": precio,
                    "qty":         qty,
                    "sl":          sl_precio,
                    "tp":          tp_precio,
                    "rsi":         round(row["rsi"], 2) if not pd.isna(row["rsi"]) else None,
                    "adx":         round(row["adx"], 2) if not pd.isna(row["adx"]) else None,
                    "htf_trend":   htf_trend,
                    "pos_mult":    mult,
                }
                en_posicion = True

    return operaciones


# ══════════════════════════════════════════════════════════
#   MÉTRICAS DEL BACKTEST
# ══════════════════════════════════════════════════════════

def calcular_metricas(operaciones: list) -> dict:
    """Calcula las métricas estándar de performance."""
    if not operaciones:
        return {}

    df_ops = pd.DataFrame(operaciones)

    total      = len(df_ops)
    ganadas    = (df_ops["resultado"] == "WIN").sum()
    perdidas   = (df_ops["resultado"] == "LOSS").sum()
    win_rate   = ganadas / total * 100

    ganancias_sum  = df_ops[df_ops["pnl_neto"] > 0]["pnl_neto"].sum()
    perdidas_sum   = abs(df_ops[df_ops["pnl_neto"] < 0]["pnl_neto"].sum())
    profit_factor  = ganancias_sum / perdidas_sum if perdidas_sum > 0 else float("inf")

    retorno_total  = df_ops["pnl_neto"].sum()
    retorno_pct    = retorno_total / CAPITAL_INICIAL * 100

    # Máximo drawdown
    equity = pd.Series([CAPITAL_INICIAL] + list(df_ops["capital_post"]))
    rolling_max = equity.cummax()
    drawdown    = (equity - rolling_max) / rolling_max * 100
    max_dd      = drawdown.min()

    # Sharpe ratio (diario simplificado)
    df_ops["fecha"] = pd.to_datetime(df_ops["exit_time"]).dt.date
    pnl_diario      = df_ops.groupby("fecha")["pnl_neto"].sum()
    if len(pnl_diario) > 1 and pnl_diario.std() != 0:
        sharpe = (pnl_diario.mean() / pnl_diario.std()) * np.sqrt(252)
    else:
        sharpe = 0.0

    promedio_ganancia = df_ops[df_ops["pnl_neto"] > 0]["pnl_neto"].mean() if ganadas > 0 else 0
    promedio_perdida  = df_ops[df_ops["pnl_neto"] < 0]["pnl_neto"].mean() if perdidas > 0 else 0

    return {
        "total_operaciones":   total,
        "ganadas":             int(ganadas),
        "perdidas":            int(perdidas),
        "win_rate_pct":        round(win_rate, 2),
        "profit_factor":       round(profit_factor, 3),
        "retorno_total_usdt":  round(retorno_total, 4),
        "retorno_total_pct":   round(retorno_pct, 2),
        "max_drawdown_pct":    round(max_dd, 2),
        "sharpe_ratio":        round(sharpe, 3),
        "promedio_ganancia":   round(promedio_ganancia, 4),
        "promedio_perdida":    round(promedio_perdida, 4),
        "capital_final":       round(CAPITAL_INICIAL + retorno_total, 4),
    }


# ══════════════════════════════════════════════════════════
#   GRÁFICO EQUITY CURVE
# ══════════════════════════════════════════════════════════

def generar_equity_curve(operaciones_total: list):
    """Genera y guarda la curva de equity en data/equity_curve.png."""
    if not operaciones_total:
        log.warning("Sin operaciones para graficar")
        return

    df_ops  = pd.DataFrame(operaciones_total)
    df_ops  = df_ops.sort_values("exit_time").reset_index(drop=True)

    equity  = [CAPITAL_INICIAL] + list(df_ops["capital_post"])
    tiempos = [df_ops["entry_time"].iloc[0]] + list(df_ops["exit_time"])
    tiempos = pd.to_datetime(tiempos)

    # Máximo drawdown para sombreado
    equity_s     = pd.Series(equity)
    rolling_max  = equity_s.cummax()
    drawdown_s   = (equity_s - rolling_max) / rolling_max * 100

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8),
                                    gridspec_kw={"height_ratios": [3, 1]},
                                    sharex=True)
    fig.patch.set_facecolor("#0d1117")
    for ax in (ax1, ax2):
        ax.set_facecolor("#161b22")
        ax.tick_params(colors="#c9d1d9")
        ax.spines[:].set_color("#30363d")

    # Panel superior: equity curve
    color_linea = "#3fb950" if equity[-1] >= CAPITAL_INICIAL else "#f85149"
    ax1.plot(tiempos, equity, color=color_linea, linewidth=1.5, label="Equity")
    ax1.axhline(CAPITAL_INICIAL, color="#6e7681", linewidth=1,
                linestyle="--", label=f"Capital inicial ${CAPITAL_INICIAL}")
    ax1.fill_between(tiempos, CAPITAL_INICIAL, equity,
                     where=[e >= CAPITAL_INICIAL for e in equity],
                     alpha=0.15, color="#3fb950")
    ax1.fill_between(tiempos, CAPITAL_INICIAL, equity,
                     where=[e < CAPITAL_INICIAL for e in equity],
                     alpha=0.15, color="#f85149")

    # Marcar operaciones ganadoras y perdedoras
    wins  = df_ops[df_ops["resultado"] == "WIN"]
    loses = df_ops[df_ops["resultado"] == "LOSS"]
    if not wins.empty:
        ax1.scatter(pd.to_datetime(wins["exit_time"]),
                    wins["capital_post"], color="#3fb950",
                    s=20, zorder=5, alpha=0.7, label=f"WIN ({len(wins)})")
    if not loses.empty:
        ax1.scatter(pd.to_datetime(loses["exit_time"]),
                    loses["capital_post"], color="#f85149",
                    s=20, zorder=5, alpha=0.7, label=f"LOSS ({len(loses)})")

    ax1.set_ylabel("Capital (USDT)", color="#c9d1d9")
    ax1.set_title("EZBOT v2.0 — Equity Curve (Backtest 6 meses)",
                  color="#c9d1d9", fontsize=13, pad=10)
    ax1.legend(loc="upper left", facecolor="#21262d",
               edgecolor="#30363d", labelcolor="#c9d1d9", fontsize=8)
    ax1.grid(color="#21262d", linewidth=0.5)

    # Panel inferior: drawdown
    ax2.fill_between(tiempos, 0, list(drawdown_s),
                     color="#f85149", alpha=0.4, label="Drawdown %")
    ax2.set_ylabel("Drawdown %", color="#c9d1d9")
    ax2.set_xlabel("Fecha", color="#c9d1d9")
    ax2.legend(loc="lower left", facecolor="#21262d",
               edgecolor="#30363d", labelcolor="#c9d1d9", fontsize=8)
    ax2.grid(color="#21262d", linewidth=0.5)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax2.xaxis.set_major_locator(mdates.MonthLocator())
    plt.xticks(rotation=30, color="#c9d1d9")

    plt.tight_layout()
    ruta = "data/equity_curve.png"
    plt.savefig(ruta, dpi=150, bbox_inches="tight", facecolor="#0d1117")
    plt.close()
    log.info(f"📊 Equity curve guardada en {ruta}")


# ══════════════════════════════════════════════════════════
#   EXPORTAR RESULTADOS A CSV
# ══════════════════════════════════════════════════════════

def exportar_csv(operaciones: list):
    """Exporta todas las operaciones simuladas a data/backtest_results.csv."""
    if not operaciones:
        log.warning("Sin operaciones para exportar")
        return

    df_ops = pd.DataFrame(operaciones)
    ruta   = "data/backtest_results.csv"
    df_ops.to_csv(ruta, index=False)
    log.info(f"📄 Resultados exportados a {ruta} ({len(df_ops)} operaciones)")


# ══════════════════════════════════════════════════════════
#   IMPRIMIR RESUMEN EN CONSOLA
# ══════════════════════════════════════════════════════════

def imprimir_resumen(metricas: dict, symbol: str = ""):
    titulo = f"BACKTEST EZBOT v2.0{' — ' + symbol if symbol else ' — TODOS LOS PARES'}"
    sep    = "═" * 55
    log.info(f"\n{sep}")
    log.info(f"  {titulo}")
    log.info(sep)
    log.info(f"  Período        : últimos 6 meses | 5 minutos")
    log.info(f"  Capital inicial: ${CAPITAL_INICIAL} USDT | Leverage {LEVERAGE}x")
    log.info(f"  Riesgo/trade   : {RISK_PER_TRADE*100}%")
    log.info(f"  RSI umbrales   : {RSI_OVERSOLD}/{RSI_OVERBOUGHT}")
    log.info(f"  Sesion horaria : {SESSION_START}:00 - {SESSION_END}:00 UTC")
    log.info(f"  Circuit breaker: pausa tras {MAX_PERDIDAS_CONSECUTIVAS} pérdidas consecutivas")
    log.info(sep)
    log.info(f"  Total operaciones : {metricas.get('total_operaciones', 0)}")
    log.info(f"  Ganadas           : {metricas.get('ganadas', 0)}")
    log.info(f"  Perdidas          : {metricas.get('perdidas', 0)}")
    log.info(f"  Win Rate          : {metricas.get('win_rate_pct', 0)}%")
    log.info(f"  Profit Factor     : {metricas.get('profit_factor', 0)}")
    log.info(f"  Retorno Total     : ${metricas.get('retorno_total_usdt', 0)} "
             f"({metricas.get('retorno_total_pct', 0)}%)")
    log.info(f"  Máx. Drawdown     : {metricas.get('max_drawdown_pct', 0)}%")
    log.info(f"  Sharpe Ratio      : {metricas.get('sharpe_ratio', 0)}")
    log.info(f"  Prom. Ganancia    : ${metricas.get('promedio_ganancia', 0)}")
    log.info(f"  Prom. Pérdida     : ${metricas.get('promedio_perdida', 0)}")
    log.info(f"  Capital Final     : ${metricas.get('capital_final', 0)}")
    log.info(sep)


# ══════════════════════════════════════════════════════════
#   MAIN
# ══════════════════════════════════════════════════════════

def run_backtest():
    log.info("╔══════════════════════════════════════════════════╗")
    log.info("║    EZBOT v2.0 — Backtesting 6 Meses             ║")
    log.info("║    BB + RSI + ATR + EMA1h + VOL + ADX           ║")
    log.info("╚══════════════════════════════════════════════════╝")

    todas_operaciones = []

    for symbol in SYMBOLS:
        log.info(f"\n{'─'*50}")
        log.info(f"  Procesando {symbol}...")
        log.info(f"{'─'*50}")

        # Descarga datos en 5min y 1h
        df_5m = get_klines_historico(symbol, interval=TIMEFRAME,     dias=730)
        df_1h = get_klines_historico(symbol, interval=TIMEFRAME_HTF, dias=735)

        if df_5m.empty or df_1h.empty:
            log.error(f"Datos insuficientes para {symbol} — saltando")
            continue

        # Aplicar los 6 sensores al DataFrame de 5min
        df_5m = add_all_sensors(df_5m)

        # Construir la serie de tendencia 1h alineada al 5min
        htf_trend_series = build_htf_trend_series(df_1h, df_5m)

        # Simular operaciones
        log.info(f"🔄 Simulando operaciones en {len(df_5m)} velas...")
        operaciones = simular_operaciones(df_5m, htf_trend_series, symbol)
        log.info(f"✅ {symbol}: {len(operaciones)} operaciones simuladas")

        # Métricas por símbolo
        metricas_sym = calcular_metricas(operaciones)
        imprimir_resumen(metricas_sym, symbol)

        todas_operaciones.extend(operaciones)

    # Métricas globales (todos los pares combinados)
    if todas_operaciones:
        log.info(f"\n{'═'*55}")
        log.info("  RESUMEN GLOBAL (BTC + ETH combinados)")
        metricas_global = calcular_metricas(todas_operaciones)
        imprimir_resumen(metricas_global)

        # Exportar y graficar
        exportar_csv(todas_operaciones)
        generar_equity_curve(todas_operaciones)
    else:
        log.warning("⚠️  No se generaron operaciones en ningún par")

    log.info("\n✅ Backtest completado.")


if __name__ == "__main__":
    run_backtest()
