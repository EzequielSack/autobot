"""
spot_grid_bot.py — Grid CONSERVADOR de BTC en SPOT (sin apalancamiento).

Filosofía: "BTC como base sólida". Compra escalonada en las caídas y vende
los rebotes. Al ser SPOT puro:
  - NO hay apalancamiento ni liquidación posible.
  - Si BTC cae y se queda abajo, simplemente quedás ACUMULANDO BTC barato,
    no tenés una pérdida líquida forzada.

Mecánica:
  - Se siembran sólo órdenes de COMPRA debajo del precio (tenemos USDT, no BTC).
  - Cuando una compra se ejecuta → coloca una VENTA un nivel arriba (vende lo
    que acaba de comprar más barato).
  - Cuando esa venta se ejecuta → coloca una COMPRA un nivel abajo. Y así.
  - Cap de inventario: deja de comprar al alcanzar el BTC máximo presupuestado.

Fees spot = 0.10% maker. Por eso el spacing es ANCHO (1%): 1% − 0.2% RT = 0.8%
neto por ciclo. Menos operaciones, pero cada una segura y rentable.

⚠️  TESTNET=False = dinero real. Verificá en DRY_RUN primero.
"""

import os
import time
import logging
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from pybit.unified_trading import HTTP

from secure_env import load_secure_env

# ═══ CONFIG ═══════════════════════════════════════════════════════════════════
DRY_RUN          = False         # True = verifica sin operar
SYMBOL           = "BTCUSDT"
SPACING_PCT      = 0.010         # 1.0% entre niveles (ancho, por fee spot 0.1%)
NIVELES_COMPRA   = 6             # 6 compras escalonadas: -1% .. -6%
QTY_BTC          = Decimal("0.0005")   # ≈ $33 por nivel
MAX_INVENTARIO   = Decimal("0.0030")   # tope ≈ $198 — no comprar más que esto
LOOP_SLEEP       = 30
MAKER_FEE_SPOT   = 0.001         # 0.10%

# ═══ ENTORNO ══════════════════════════════════════════════════════════════════
load_secure_env()
TESTNET = os.getenv("TESTNET", "true").lower() == "true"

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(
            f"logs/spotgrid_{datetime.now().strftime('%Y%m%d')}.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("spotgrid")

session = HTTP(testnet=TESTNET, api_key=os.getenv("BYBIT_API_KEY"),
               api_secret=os.getenv("BYBIT_API_SECRET"))


# ═══ UTILIDADES ═══════════════════════════════════════════════════════════════

def get_instrument():
    it = session.get_instruments_info(category="spot", symbol=SYMBOL)["result"]["list"][0]
    return {
        "tick":      Decimal(it["priceFilter"]["tickSize"]),
        "base_prec": Decimal(it["lotSizeFilter"]["basePrecision"]),
        "min_amt":   Decimal(it["lotSizeFilter"]["minOrderAmt"]),
    }


def get_price() -> float:
    return float(session.get_tickers(category="spot", symbol=SYMBOL)
                 ["result"]["list"][0]["lastPrice"])


def get_btc_balance() -> Decimal:
    acc = session.get_wallet_balance(accountType="UNIFIED", coin="BTC")["result"]["list"][0]
    for c in acc.get("coin", []):
        if c.get("coin") == "BTC":
            v = c.get("walletBalance") or "0"
            return Decimal(v) if v not in ("", None) else Decimal("0")
    return Decimal("0")


def round_to(value: Decimal, unit: Decimal) -> Decimal:
    return (value / unit).quantize(Decimal("1"), rounding=ROUND_DOWN) * unit


def open_orders() -> dict:
    r = session.get_open_orders(category="spot", symbol=SYMBOL)["result"]["list"]
    return {o["orderId"]: o for o in r}


# ═══ ÓRDENES ══════════════════════════════════════════════════════════════════

def place_maker(side: str, qty: Decimal, price: Decimal) -> str | None:
    if DRY_RUN:
        log.info(f"[DRY] {side} {qty} BTC @ {price}  (~${float(qty)*float(price):.2f})")
        return f"dry-{side}-{price}"
    try:
        r = session.place_order(
            category="spot", symbol=SYMBOL, side=side, orderType="Limit",
            qty=str(qty), price=str(price), timeInForce="PostOnly")
        return r["result"]["orderId"]
    except Exception as e:
        log.error(f"❌ place {side} @ {price}: {e}")
        return None


def cancelar_todo():
    if DRY_RUN:
        log.info("[DRY] cancel all")
        return
    try:
        session.cancel_all_orders(category="spot", symbol=SYMBOL)
        log.info("🧹 Órdenes spot canceladas")
    except Exception as e:
        log.error(f"cancel_all: {e}")


# ═══ MOTOR ════════════════════════════════════════════════════════════════════

class SpotGrid:
    def __init__(self):
        self.inst    = get_instrument()
        self.qty     = round_to(QTY_BTC, self.inst["base_prec"])
        self.center  = get_price()
        self.spacing = self.center * SPACING_PCT
        self.activos: dict[str, dict] = {}   # orderId -> {side, price}
        self.ciclos  = 0
        self.profit  = 0.0

        notional = float(self.qty) * self.center
        if Decimal(str(notional)) < self.inst["min_amt"]:
            raise ValueError(f"qty {self.qty} (~${notional:.2f}) < min ${self.inst['min_amt']}")

    def resumen(self):
        notional = float(self.qty) * self.center
        bruto    = SPACING_PCT * notional
        fee_rt   = 2 * MAKER_FEE_SPOT * notional
        neto     = bruto - fee_rt
        log.info(f"[{SYMBOL}] precio={self.center:.2f} | spacing={SPACING_PCT*100:.2f}% "
                 f"| qty={self.qty} (~${notional:.2f})")
        log.info(f"[{SYMBOL}] por ciclo  bruto=${bruto:.4f}  fees=${fee_rt:.4f}  "
                 f"NETO=${neto:.4f}  ({'RENTABLE' if neto > 0 else 'NEGATIVO ⚠'})")
        log.info(f"[{SYMBOL}] inventario máx={MAX_INVENTARIO} BTC "
                 f"(~${float(MAX_INVENTARIO)*self.center:.2f}) | sin apalancamiento")

    def sembrar(self):
        """Sólo compras debajo del precio (tenemos USDT, no BTC)."""
        for i in range(1, NIVELES_COMPRA + 1):
            price = round_to(Decimal(str(self.center)) * Decimal(str(1 - SPACING_PCT * i)),
                             self.inst["tick"])
            oid = place_maker("Buy", self.qty, price)
            if oid:
                self.activos[oid] = {"side": "Buy", "price": price}
        log.info(f"[{SYMBOL}] grilla sembrada: {len(self.activos)} compras "
                 f"centro={self.center:.2f}")

    def _opuesta(self, nivel: dict) -> dict:
        price = nivel["price"]
        step  = Decimal(str(self.spacing))
        if nivel["side"] == "Buy":
            return {"side": "Sell", "price": round_to(price + step, self.inst["tick"])}
        return {"side": "Buy", "price": round_to(price - step, self.inst["tick"])}

    def reconciliar(self):
        if DRY_RUN:
            log.info(f"[{SYMBOL}] DRY precio={get_price():.2f}")
            return

        abiertas = set(open_orders().keys())
        llenas   = [oid for oid in self.activos if oid not in abiertas]
        inv      = get_btc_balance()

        for oid in llenas:
            nivel = self.activos.pop(oid)
            op    = self._opuesta(nivel)

            # Cap de inventario: no comprar si ya tenemos el máximo
            if op["side"] == "Buy" and inv >= MAX_INVENTARIO:
                log.info(f"[{SYMBOL}] ⏸ inventario {inv} ≥ máx {MAX_INVENTARIO} — "
                         f"no repongo compra")
                continue
            # Sólo vender si hay BTC suficiente
            if op["side"] == "Sell" and inv < self.qty:
                log.info(f"[{SYMBOL}] sin BTC suficiente para vender ({inv}) — omito")
                continue

            if nivel["side"] == "Buy":   # completó compra→venta = medio ciclo de profit
                notional = float(self.qty) * float(nivel["price"])
                self.profit += self.spacing * float(self.qty) - 2*MAKER_FEE_SPOT*notional
                self.ciclos += 1
            log.info(f"[{SYMBOL}] ✅ FILL {nivel['side']} @ {nivel['price']} → "
                     f"{op['side']} @ {op['price']} | inv={inv} BTC "
                     f"ciclos={self.ciclos} profit≈${self.profit:.4f}")
            new_oid = place_maker(op["side"], self.qty, op["price"])
            if new_oid:
                self.activos[new_oid] = op


# ═══ RUN ══════════════════════════════════════════════════════════════════════

def run():
    log.info("=" * 64)
    log.info("  SPOT GRID BTC — conservador, sin apalancamiento")
    log.info(f"  Entorno: {'TESTNET' if TESTNET else 'REAL ⚠'} | "
             f"Modo: {'DRY_RUN' if DRY_RUN else 'EN VIVO'}")
    log.info("=" * 64)

    g = SpotGrid()
    g.resumen()
    g.sembrar()

    if DRY_RUN:
        log.info("DRY_RUN: observando 3 ciclos y saliendo (sin operar).")
        for _ in range(3):
            g.reconciliar()
            time.sleep(LOOP_SLEEP)
        log.info("DRY_RUN ok. Poné DRY_RUN=False para operar.")
        return

    log.info("Loop en vivo. Ctrl+C para detener (deja órdenes y BTC acumulado).")
    try:
        while True:
            try:
                g.reconciliar()
            except Exception as e:
                log.error(f"loop: {e}")
            time.sleep(LOOP_SLEEP)
    except KeyboardInterrupt:
        log.info("⏹  Detenido. Cancelando órdenes (el BTC comprado queda en tu wallet).")
        cancelar_todo()


if __name__ == "__main__":
    run()
