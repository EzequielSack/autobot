"""
╔══════════════════════════════════════════════════════════╗
║         EZBOT v2.0 — Dashboard en consola                ║
║         Balance | Posiciones | Últimas ops | Win Rate    ║
║         Se actualiza cada 30 segundos                    ║
╚══════════════════════════════════════════════════════════╝
"""

import os
import time
import logging
from datetime import datetime
import pandas as pd
from pybit.unified_trading import HTTP
from secure_env import load_secure_env
from config import PARES_CONFIG, REAL_TRADING, PAPER_TRADING
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich.text import Text
from rich import box
from rich.live import Live
from rich.layout import Layout

# ─── CONFIGURACIÓN ────────────────────────────────────────
load_secure_env()

API_KEY    = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")
TESTNET    = os.getenv("TESTNET", "true").lower() == "true"

SYMBOLS          = list(PARES_CONFIG.keys())   # BTC + ETH + SOL
REFRESH_SECONDS  = 30
TRADES_CSV       = "data/trades.csv"

# ─── LOGGING ──────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.FileHandler("logs/dashboard.log")]
)
log = logging.getLogger("dashboard")

# ─── RICH CONSOLE ─────────────────────────────────────────
console = Console()

# ─── CONEXIÓN BYBIT ───────────────────────────────────────
session = HTTP(
    testnet=TESTNET,
    api_key=API_KEY,
    api_secret=API_SECRET,
)


# ══════════════════════════════════════════════════════════
#   FUNCIONES DE DATOS (mismas que bot.py)
# ══════════════════════════════════════════════════════════

def get_balance() -> float:
    """Retorna el balance disponible en USDT."""
    try:
        resp    = session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        balance = float(resp["result"]["list"][0]["coin"][0]["availableToWithdraw"])
        return balance
    except Exception as e:
        log.error(f"Error obteniendo balance: {e}")
        return 0.0


def get_wallet_info() -> dict:
    """Retorna balance total, disponible y PnL no realizado."""
    try:
        resp = session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        coin = resp["result"]["list"][0]["coin"][0]
        return {
            "disponible":  float(coin.get("availableToWithdraw", 0)),
            "equity":      float(coin.get("equity", 0)),
            "pnl_abierto": float(coin.get("unrealisedPnl", 0)),
        }
    except Exception as e:
        log.error(f"Error obteniendo wallet: {e}")
        return {"disponible": 0.0, "equity": 0.0, "pnl_abierto": 0.0}


def get_open_positions() -> list:
    """Retorna todas las posiciones abiertas."""
    posiciones = []
    try:
        for symbol in SYMBOLS:
            resp      = session.get_positions(category="linear", symbol=symbol)
            positions = resp["result"]["list"]
            for pos in positions:
                if float(pos.get("size", 0)) > 0:
                    posiciones.append(pos)
    except Exception as e:
        log.error(f"Error obteniendo posiciones: {e}")
    return posiciones


def get_last_price(symbol: str) -> float:
    """Obtiene el último precio de mercado."""
    try:
        resp = session.get_tickers(category="linear", symbol=symbol)
        return float(resp["result"]["list"][0]["lastPrice"])
    except Exception:
        return 0.0


# ══════════════════════════════════════════════════════════
#   LEER HISTORIAL DE TRADES DEL CSV
# ══════════════════════════════════════════════════════════

def leer_trades_csv() -> pd.DataFrame:
    """Lee el archivo data/trades.csv con manejo de errores."""
    if not os.path.exists(TRADES_CSV):
        return pd.DataFrame()
    try:
        df = pd.read_csv(TRADES_CSV)
        return df
    except Exception as e:
        log.error(f"Error leyendo {TRADES_CSV}: {e}")
        return pd.DataFrame()


def calcular_win_rate(df: pd.DataFrame) -> tuple[float, int, int]:
    """
    Calcula el win rate a partir del CSV de trades.
    Solo cuenta filas donde la columna 'resultado' está completa.
    """
    if df.empty or "resultado" not in df.columns:
        return 0.0, 0, 0

    df_con_resultado = df[df["resultado"].notna() & (df["resultado"] != "")]
    if df_con_resultado.empty:
        return 0.0, 0, 0

    total_cerradas = len(df_con_resultado)
    ganadas        = (df_con_resultado["resultado"].str.upper() == "WIN").sum()
    win_rate       = ganadas / total_cerradas * 100
    return round(win_rate, 1), int(ganadas), total_cerradas


# ══════════════════════════════════════════════════════════
#   CONSTRUCCIÓN DE TABLAS RICH
# ══════════════════════════════════════════════════════════

def tabla_balance(wallet: dict) -> Panel:
    """Panel superior con balance y estado."""
    modo  = "[bold red]🔴 MAINNET[/]" if not TESTNET else "[bold yellow]🧪 TESTNET[/]"
    color_pnl = "green" if wallet["pnl_abierto"] >= 0 else "red"
    signo_pnl = "+" if wallet["pnl_abierto"] >= 0 else ""

    contenido = (
        f"  [bold]Modo:[/] {modo}   "
        f"[bold]Equity:[/] [cyan]${wallet['equity']:.2f} USDT[/]   "
        f"[bold]Disponible:[/] [cyan]${wallet['disponible']:.2f} USDT[/]   "
        f"[bold]PnL abierto:[/] [{color_pnl}]{signo_pnl}${wallet['pnl_abierto']:.2f}[/]"
    )
    return Panel(contenido, title="💰 EZBOT v2.0 — Balance", border_style="cyan")


def tabla_posiciones(posiciones: list) -> Table:
    """Tabla de posiciones abiertas con PnL en vivo."""
    tabla = Table(
        title="📈 Posiciones Abiertas",
        box=box.SIMPLE_HEAVY,
        border_style="bright_blue",
        header_style="bold bright_blue",
        show_lines=True,
    )
    tabla.add_column("Símbolo",   style="bold white",  width=12)
    tabla.add_column("Lado",      style="bold",         width=8)
    tabla.add_column("Qty",       justify="right",      width=12)
    tabla.add_column("Entrada",   justify="right",      width=12)
    tabla.add_column("Precio Actual", justify="right",  width=14)
    tabla.add_column("PnL Actual", justify="right",     width=14)
    tabla.add_column("SL",        justify="right",      width=12)
    tabla.add_column("TP",        justify="right",      width=12)
    tabla.add_column("Leverage",  justify="center",     width=10)

    if not posiciones:
        tabla.add_row(
            "[dim]—[/]", "[dim]Sin posiciones[/]",
            "—", "—", "—", "—", "—", "—", "—"
        )
    else:
        for pos in posiciones:
            symbol        = pos.get("symbol", "")
            side          = pos.get("side", "")
            qty           = pos.get("size", "0")
            entry_price   = float(pos.get("avgPrice", 0))
            pnl           = float(pos.get("unrealisedPnl", 0))
            sl_precio     = pos.get("stopLoss", "—")
            tp_precio     = pos.get("takeProfit", "—")
            leverage      = pos.get("leverage", "—")
            precio_actual = get_last_price(symbol)

            color_side = "green" if side == "Buy" else "red"
            emoji_side = "🟢 LONG" if side == "Buy" else "🔴 SHORT"
            color_pnl  = "green" if pnl >= 0 else "red"
            signo_pnl  = "+" if pnl >= 0 else ""

            tabla.add_row(
                symbol,
                f"[{color_side}]{emoji_side}[/]",
                qty,
                f"${entry_price:,.2f}",
                f"${precio_actual:,.2f}",
                f"[{color_pnl}]{signo_pnl}${pnl:.4f}[/]",
                f"${float(sl_precio):,.2f}" if sl_precio != "—" else "—",
                f"${float(tp_precio):,.2f}" if tp_precio != "—" else "—",
                f"{leverage}x",
            )

    return tabla


def tabla_ultimas_ops(df_trades: pd.DataFrame) -> Table:
    """Tabla de las últimas 10 operaciones del CSV."""
    tabla = Table(
        title="📋 Últimas 10 Operaciones (trades.csv)",
        box=box.SIMPLE_HEAVY,
        border_style="bright_magenta",
        header_style="bold bright_magenta",
        show_lines=True,
    )
    tabla.add_column("Fecha",       width=20)
    tabla.add_column("Símbolo",     width=10)
    tabla.add_column("Lado",        width=10)
    tabla.add_column("Qty",         justify="right", width=10)
    tabla.add_column("Entrada",     justify="right", width=12)
    tabla.add_column("SL",          justify="right", width=12)
    tabla.add_column("TP",          justify="right", width=12)
    tabla.add_column("RSI",         justify="right", width=7)
    tabla.add_column("ADX",         justify="right", width=7)
    tabla.add_column("Tendencia",   width=10)
    tabla.add_column("Resultado",   width=10)

    if df_trades.empty:
        tabla.add_row("[dim]Sin datos[/]", *["—"] * 10)
    else:
        ultimas = df_trades.tail(10).iloc[::-1]  # más recientes primero
        for _, row in ultimas.iterrows():
            resultado  = str(row.get("resultado", "")).upper()
            color_res  = "green" if resultado == "WIN" else ("red" if resultado == "LOSS" else "dim")
            emoji_res  = "✅" if resultado == "WIN" else ("❌" if resultado == "LOSS" else "⏳")
            side       = str(row.get("side", ""))
            color_side = "green" if side == "Buy" else "red"

            tabla.add_row(
                str(row.get("timestamp", ""))[:19],
                str(row.get("symbol", "")),
                f"[{color_side}]{side}[/]",
                str(row.get("qty", "")),
                f"${float(row.get('entry_price', 0)):,.2f}",
                f"${float(row.get('stop_loss', 0)):,.2f}",
                f"${float(row.get('take_profit', 0)):,.2f}",
                str(row.get("rsi_entrada", "—")),
                str(row.get("adx_entrada", "—")),
                str(row.get("tendencia_1h", "—")),
                f"[{color_res}]{emoji_res} {resultado}[/]" if resultado else "[dim]Abierta[/]",
            )

    return tabla


def panel_estadisticas(win_rate: float, ganadas: int, total: int) -> Panel:
    """Panel de estadísticas acumuladas."""
    color_wr = "green" if win_rate >= 50 else "red"
    perdidas = total - ganadas

    contenido = (
        f"  [bold]Win Rate acumulado:[/] [{color_wr}]{win_rate}%[/]   "
        f"[bold]Operaciones cerradas:[/] [white]{total}[/]   "
        f"[bold]Ganadas:[/] [green]{ganadas}[/]   "
        f"[bold]Perdidas:[/] [red]{perdidas}[/]"
    )
    return Panel(contenido, title="📊 Estadísticas (del trades.csv)", border_style="magenta")


def footer_panel() -> Panel:
    """Footer con timestamp y configuración."""
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    contenido = (
        f"  [dim]Última actualización: {ahora}   "
        f"Símbolos: {', '.join(SYMBOLS)}   "
        f"Refresh: {REFRESH_SECONDS}s   "
        f"Ctrl+C para salir[/]"
    )
    return Panel(contenido, border_style="dim")


# ══════════════════════════════════════════════════════════
#   RENDER COMPLETO DEL DASHBOARD
# ══════════════════════════════════════════════════════════

def render_dashboard():
    """Construye y muestra el dashboard completo."""
    # Obtener datos
    wallet     = get_wallet_info()
    posiciones = get_open_positions()
    df_trades  = leer_trades_csv()
    win_rate, ganadas, total = calcular_win_rate(df_trades)

    # Renderizar
    console.clear()
    console.print()
    console.print(tabla_balance(wallet))
    console.print()
    console.print(tabla_posiciones(posiciones))
    console.print()
    console.print(panel_estadisticas(win_rate, ganadas, total))
    console.print()
    console.print(tabla_ultimas_ops(df_trades))
    console.print()
    console.print(footer_panel())


# ══════════════════════════════════════════════════════════
#   LOOP PRINCIPAL
# ══════════════════════════════════════════════════════════

def run():
    console.print(Panel(
        "[bold cyan]EZBOT v2.0 — Dashboard iniciando...[/]\n"
        "[dim]Conectando con Bybit...[/]",
        border_style="cyan"
    ))
    time.sleep(1)

    while True:
        try:
            render_dashboard()
        except KeyboardInterrupt:
            console.print("\n[yellow]Dashboard detenido.[/]")
            break
        except Exception as e:
            log.error(f"Error en dashboard: {e}")
            console.print(f"\n[red]Error: {e}[/]")

        try:
            time.sleep(REFRESH_SECONDS)
        except KeyboardInterrupt:
            console.print("\n[yellow]Dashboard detenido.[/]")
            break


if __name__ == "__main__":
    run()
