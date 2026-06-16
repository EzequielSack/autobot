"""
grid_bot.py — Grid Bot de futuros para Bybit (órdenes maker / PostOnly).

Coloca una grilla de órdenes LIMIT de compra debajo del precio y de venta
arriba. Cuando una orden se ejecuta, coloca automáticamente la opuesta un
nivel más allá → captura el spacing del grid en cada oscilación.

Uso:
    py grid_bot.py            # respeta DRY_RUN de grid_config.py

Seguridad:
    - DRY_RUN=True  -> imprime el plan de la grilla y observa el precio, SIN operar
    - PostOnly      -> siempre maker (0.02%); si cruzaría el spread, se rechaza
    - stop_pct      -> si el precio rompe el rango, cancela todo y pausa el par

⚠️  Con TESTNET=False opera con DINERO REAL. Verificá en DRY_RUN primero.
"""

import os
import time
import logging
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from pybit.unified_trading import HTTP

from secure_env import load_secure_env
import grid_config as C

# ─── Entorno ──────────────────────────────────────────────────────────────────
load_secure_env()
TESTNET = (os.getenv("TESTNET", "true").lower() == "true"
           if C.TESTNET_OVERRIDE is None else C.TESTNET_OVERRIDE)

API_KEY    = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")

# ─── Logging ──────────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(
            f"logs/grid_{datetime.now().strftime('%Y%m%d')}.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("grid")

session = HTTP(testnet=TESTNET, api_key=API_KEY, api_secret=API_SECRET)


# ══════════════════════════════════════════════════════════════════════════════
#   UTILIDADES DE MERCADO
# ══════════════════════════════════════════════════════════════════════════════

def get_instrument(symbol: str) -> dict:
    """tickSize, qtyStep y minOrderQty del símbolo."""
    r = session.get_instruments_info(category="linear", symbol=symbol)
    it = r["result"]["list"][0]
    return {
        "tick":    Decimal(it["priceFilter"]["tickSize"]),
        "step":    Decimal(it["lotSizeFilter"]["qtyStep"]),
        "min_qty": Decimal(it["lotSizeFilter"]["minOrderQty"]),
    }


def get_price(symbol: str) -> float:
    r = session.get_tickers(category="linear", symbol=symbol)
    return float(r["result"]["list"][0]["lastPrice"])


def round_to(value: Decimal, unit: Decimal) -> Decimal:
    """Redondea hacia abajo al múltiplo de `unit` (tick o step)."""
    return (value / unit).quantize(Decimal("1"), rounding=ROUND_DOWN) * unit


# ══════════════════════════════════════════════════════════════════════════════
#   CONSTRUCCIÓN DE LA GRILLA
# ══════════════════════════════════════════════════════════════════════════════

def construir_grilla(symbol: str, cfg: dict, mid: float, inst: dict) -> list[dict]:
    """
    Devuelve la lista de niveles: cada uno {price, side}.
    Compras debajo del mid, ventas arriba. Spacing uniforme.
    """
    niveles  = cfg["niveles"]
    rango    = cfg["range_pct"]
    spacing  = rango / niveles            # fracción de precio entre niveles
    tick     = inst["tick"]
    mid_d    = Decimal(str(mid))

    grilla = []
    for i in range(1, niveles + 1):
        buy_p  = round_to(mid_d * Decimal(str(1 - spacing * i)), tick)
        sell_p = round_to(mid_d * Decimal(str(1 + spacing * i)), tick)
        grilla.append({"price": buy_p,  "side": "Buy"})
        grilla.append({"price": sell_p, "side": "Sell"})
    return grilla


def resumen_economico(symbol: str, cfg: dict, mid: float):
    """Imprime la economía esperada de la grilla (DRY_RUN)."""
    spacing_pct = cfg["range_pct"] / cfg["niveles"]
    qty         = cfg["qty"]
    notional    = qty * mid
    bruto       = spacing_pct * notional
    fee_rt      = 2 * C.MAKER_FEE * notional
    neto        = bruto - fee_rt
    log.info(f"[{symbol}] mid={mid:.4f} | spacing={spacing_pct*100:.3f}% "
             f"| qty={qty} (~${notional:.2f})")
    log.info(f"[{symbol}] por ciclo  bruto=${bruto:.4f}  fees=${fee_rt:.4f}  "
             f"NETO=${neto:.4f}  ({'RENTABLE' if neto > 0 else 'NEGATIVO ⚠'})")
    log.info(f"[{symbol}] inventario máx ≈ ${notional*cfg['niveles']:.2f} "
             f"| rango ±{cfg['range_pct']*100:.1f}% | stop ±{cfg['stop_pct']*100:.1f}%")


# ══════════════════════════════════════════════════════════════════════════════
#   ÓRDENES
# ══════════════════════════════════════════════════════════════════════════════

def set_leverage(symbol: str):
    try:
        session.set_leverage(category="linear", symbol=symbol,
                             buyLeverage=str(C.LEVERAGE), sellLeverage=str(C.LEVERAGE))
    except Exception as e:
        log.debug(f"set_leverage {symbol}: {e}")


def place_maker(symbol: str, side: str, qty: Decimal, price: Decimal) -> str | None:
    """Coloca una orden LIMIT PostOnly (maker). Devuelve orderId o None."""
    if C.DRY_RUN:
        log.info(f"[DRY] {side} {qty} {symbol} @ {price}")
        return f"dry-{side}-{price}"
    try:
        r = session.place_order(
            category="linear", symbol=symbol, side=side, orderType="Limit",
            qty=str(qty), price=str(price), timeInForce="PostOnly", reduceOnly=False)
        return r["result"]["orderId"]
    except Exception as e:
        log.error(f"❌ place {side} {symbol} @ {price}: {e}")
        return None


def cancelar_todo(symbol: str):
    if C.DRY_RUN:
        log.info(f"[DRY] cancel all {symbol}")
        return
    try:
        session.cancel_all_orders(category="linear", symbol=symbol)
        log.info(f"🧹 Órdenes canceladas en {symbol}")
    except Exception as e:
        log.error(f"cancel_all {symbol}: {e}")


def open_order_ids(symbol: str) -> set[str]:
    r = session.get_open_orders(category="linear", symbol=symbol)
    return {o["orderId"] for o in r["result"]["list"]}


def get_position(symbol: str) -> dict:
    """Posición neta abierta: {'side','size'} o {} si está plano."""
    r = session.get_positions(category="linear", symbol=symbol)
    for p in r["result"]["list"]:
        size = float(p.get("size", 0) or 0)
        if size > 0:
            return {"side": p["side"], "size": size}
    return {}


def flatten_position(symbol: str):
    """Cierra a mercado (reduceOnly) cualquier inventario acumulado. Anti-tendencia."""
    if C.DRY_RUN:
        log.info(f"[DRY] flatten {symbol}")
        return
    pos = get_position(symbol)
    if not pos:
        log.info(f"[{symbol}] sin posición que cerrar")
        return
    close_side = "Sell" if pos["side"] == "Buy" else "Buy"
    try:
        session.place_order(
            category="linear", symbol=symbol, side=close_side, orderType="Market",
            qty=str(pos["size"]), reduceOnly=True)
        log.warning(f"[{symbol}] 🛑 posición cerrada a mercado: "
                    f"{close_side} {pos['size']} (taker)")
    except Exception as e:
        log.error(f"❌ flatten {symbol}: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#   MOTOR DEL GRID (por símbolo)
# ══════════════════════════════════════════════════════════════════════════════

class GridEngine:
    def __init__(self, symbol: str, cfg: dict):
        self.symbol  = symbol
        self.cfg     = cfg
        self.inst    = get_instrument(symbol)
        self.qty     = round_to(Decimal(str(cfg["qty"])), self.inst["step"])
        self.center  = get_price(symbol)
        self.spacing = self.center * cfg["range_pct"] / cfg["niveles"]
        # activos: orderId -> {price, side}
        self.activos: dict[str, dict] = {}
        self.ciclos_cerrados = 0
        self.profit_estimado = 0.0

        if self.qty < self.inst["min_qty"]:
            raise ValueError(
                f"{symbol}: qty {self.qty} < minOrderQty {self.inst['min_qty']}")

    def sembrar(self):
        """Coloca la grilla inicial."""
        set_leverage(self.symbol)
        grilla = construir_grilla(self.symbol, self.cfg, self.center, self.inst)
        for nivel in grilla:
            oid = place_maker(self.symbol, nivel["side"], self.qty, nivel["price"])
            if oid:
                self.activos[oid] = {"price": nivel["price"], "side": nivel["side"]}
        log.info(f"[{self.symbol}] grilla sembrada: {len(self.activos)} órdenes "
                 f"centro={self.center:.4f}")

    def _opuesta(self, nivel: dict) -> dict:
        """Orden a colocar tras un fill: opuesta, un spacing más allá."""
        price = nivel["price"]
        step  = Decimal(str(self.spacing))
        if nivel["side"] == "Buy":     # compró → vende un nivel arriba
            return {"side": "Sell", "price": round_to(price + step, self.inst["tick"])}
        else:                          # vendió → compra un nivel abajo
            return {"side": "Buy",  "price": round_to(price - step, self.inst["tick"])}

    def reconciliar(self):
        """Detecta fills y repone la orden opuesta. Devuelve False si pausó."""
        precio = get_price(self.symbol)

        # ── Stop de rango ─────────────────────────────────────────────────────
        desvio = abs(precio - self.center) / self.center
        if desvio > self.cfg["stop_pct"]:
            log.warning(f"[{self.symbol}] 🚨 precio {precio:.4f} rompió rango "
                        f"(±{desvio*100:.2f}% > {self.cfg['stop_pct']*100:.1f}%) — PAUSA")
            cancelar_todo(self.symbol)
            flatten_position(self.symbol)   # cierra inventario para no quedar expuesto
            self.activos.clear()
            return False

        if C.DRY_RUN:
            log.info(f"[{self.symbol}] DRY precio={precio:.4f} desvío={desvio*100:.2f}%")
            return True

        # ── Detectar fills ────────────────────────────────────────────────────
        abiertas = open_order_ids(self.symbol)
        llenas   = [oid for oid in self.activos if oid not in abiertas]
        for oid in llenas:
            nivel = self.activos.pop(oid)
            op    = self._opuesta(nivel)
            notional = float(self.qty) * float(nivel["price"])
            self.profit_estimado += self.spacing * float(self.qty) - 2*C.MAKER_FEE*notional
            self.ciclos_cerrados += 1
            log.info(f"[{self.symbol}] ✅ FILL {nivel['side']} @ {nivel['price']} "
                     f"→ repone {op['side']} @ {op['price']} "
                     f"| ciclos={self.ciclos_cerrados} profit≈${self.profit_estimado:.4f}")
            new_oid = place_maker(self.symbol, op["side"], self.qty, op["price"])
            if new_oid:
                self.activos[new_oid] = op
        return True


# ══════════════════════════════════════════════════════════════════════════════
#   ORQUESTADOR
# ══════════════════════════════════════════════════════════════════════════════

def run():
    log.info("=" * 64)
    log.info("  GRID BOT — futuros Bybit (maker/PostOnly)")
    log.info(f"  Entorno : {'TESTNET' if TESTNET else 'REAL ⚠'}")
    log.info(f"  Modo    : {'DRY_RUN (sin operar)' if C.DRY_RUN else 'EN VIVO'}")
    log.info(f"  Leverage: {C.LEVERAGE}x | buffer=${C.MARGEN_BUFFER_USDT}")
    log.info("=" * 64)

    activos = {s: c for s, c in C.GRIDS.items() if c["activo"]}

    # Resumen económico siempre (clave para validar en DRY_RUN)
    for sym, cfg in activos.items():
        resumen_economico(sym, cfg, get_price(sym))

    engines = []
    for sym, cfg in activos.items():
        try:
            eng = GridEngine(sym, cfg)
            eng.sembrar()
            engines.append(eng)
        except Exception as e:
            log.error(f"No se pudo iniciar {sym}: {e}")

    if not engines:
        log.error("Sin motores activos. Revisá grid_config.py")
        return

    if C.DRY_RUN:
        log.info("DRY_RUN: observando precio 5 ciclos y saliendo (sin operar).")
        for _ in range(5):
            for eng in engines:
                eng.reconciliar()
            time.sleep(C.LOOP_SLEEP)
        log.info("DRY_RUN finalizado. Si la economía es RENTABLE, "
                 "poné DRY_RUN=False para operar.")
        return

    log.info("Loop en vivo. Ctrl+C para detener.")
    try:
        while True:
            for eng in list(engines):
                try:
                    if not eng.reconciliar():
                        engines.remove(eng)
                except Exception as e:
                    log.error(f"[{eng.symbol}] error en loop: {e}")
            if not engines:
                log.warning("Todos los grids pausados por stop. Saliendo.")
                break
            time.sleep(C.LOOP_SLEEP)
    except KeyboardInterrupt:
        log.info("⏹  Detenido por usuario. Cancelando órdenes y cerrando posiciones...")
        for eng in engines:
            cancelar_todo(eng.symbol)
            flatten_position(eng.symbol)


if __name__ == "__main__":
    run()
