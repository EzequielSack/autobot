"""
AUTOBOT — Backend Flask
"""

from flask import Flask, render_template, request, session, jsonify, send_file
import os, json
import pandas as pd
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

app = Flask(__name__)
app.secret_key = "autobot_secret_2024_ezequielsack"

REFERRAL = "https://partner.bybit.com/b/59453"


def make_session(api_key, api_secret, testnet=False):
    try:
        from pybit.unified_trading import HTTP
        return HTTP(testnet=testnet, api_key=api_key, api_secret=api_secret)
    except:
        return None


def get_wallet(sess):
    try:
        resp = sess.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        coin = resp["result"]["list"][0]["coin"][0]
        return {
            "equity":      round(float(coin.get("equity", 0)), 2),
            "disponible":  round(float(coin.get("availableToWithdraw", 0)), 2),
            "pnl_abierto": round(float(coin.get("unrealisedPnl", 0)), 4),
        }
    except:
        return None


def get_posiciones(sess):
    try:
        out = []
        resp = sess.get_positions(category="linear", symbol="BTCUSDT")
        for p in resp["result"]["list"]:
            if float(p.get("size", 0)) > 0:
                out.append({
                    "symbol":    p.get("symbol"),
                    "side":      p.get("side"),
                    "size":      p.get("size"),
                    "avgPrice":  p.get("avgPrice"),
                    "pnl":       round(float(p.get("unrealisedPnl", 0)), 4),
                    "stopLoss":  p.get("stopLoss", "—"),
                    "takeProfit":p.get("takeProfit", "—"),
                    "leverage":  p.get("leverage", "3"),
                })
        return out
    except:
        return []


def get_trades_csv():
    ruta = os.path.join(os.path.dirname(__file__), "..", "data", "trades.csv")
    if os.path.exists(ruta):
        try:
            df = pd.read_csv(ruta)
            return df.tail(20).to_dict("records")
        except:
            pass
    return []


def get_stats_csv():
    ruta = os.path.join(os.path.dirname(__file__), "..", "data", "trades.csv")
    if not os.path.exists(ruta):
        return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0, "equity": [300]}
    try:
        df = pd.read_csv(ruta)
        cerradas = df[df["resultado"].notna() & (df["resultado"] != "")]
        total  = len(cerradas)
        wins   = int((cerradas["resultado"].str.upper() == "WIN").sum())
        losses = int((cerradas["resultado"].str.upper() == "LOSS").sum())
        wr     = round(wins / total * 100, 1) if total > 0 else 0
        equity = [300.0] + list(df["capital_post"].dropna().astype(float)) if "capital_post" in df.columns else [300]
        return {"total": total, "wins": wins, "losses": losses, "win_rate": wr, "equity": equity}
    except:
        return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0, "equity": [300]}


# ── Rutas ────────────────────────────────────────────────

@app.route("/")
def landing():
    return render_template("landing.html", referral=REFERRAL)


@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@app.route("/descargar")
def descargar():
    """Sirve el instalador AUTOBOT.exe para descarga."""
    exe = os.path.join(os.path.dirname(__file__), "..", "dist", "AUTOBOT.exe")
    if os.path.exists(exe):
        return send_file(exe, as_attachment=True, download_name="AUTOBOT.exe")
    return "El instalador no está disponible todavía.", 404


@app.route("/api/connect", methods=["POST"])
def api_connect():
    data       = request.get_json()
    api_key    = data.get("api_key", "").strip()
    api_secret = data.get("api_secret", "").strip()
    testnet    = data.get("testnet", False)

    if not api_key or not api_secret:
        return jsonify({"ok": False, "error": "Completá los dos campos"})

    sess   = make_session(api_key, api_secret, testnet)
    wallet = get_wallet(sess) if sess else None

    if not wallet:
        return jsonify({"ok": False, "error": "API Keys incorrectas o sin permisos de trading"})

    session["api_key"]    = api_key
    session["api_secret"] = api_secret
    session["testnet"]    = testnet
    return jsonify({"ok": True, "wallet": wallet})


@app.route("/api/wallet")
def api_wallet():
    if "api_key" not in session:
        return jsonify({"ok": False, "error": "No conectado"})
    sess   = make_session(session["api_key"], session["api_secret"], session.get("testnet", False))
    wallet = get_wallet(sess) if sess else None
    if not wallet:
        return jsonify({"ok": False, "error": "Error de conexión"})
    return jsonify({"ok": True, "wallet": wallet})


@app.route("/api/posiciones")
def api_posiciones():
    if "api_key" not in session:
        return jsonify({"ok": False, "posiciones": []})
    sess      = make_session(session["api_key"], session["api_secret"], session.get("testnet", False))
    posiciones = get_posiciones(sess) if sess else []
    return jsonify({"ok": True, "posiciones": posiciones})


@app.route("/api/trades")
def api_trades():
    return jsonify({"ok": True, "trades": get_trades_csv(), "stats": get_stats_csv()})


@app.route("/api/disconnect", methods=["POST"])
def api_disconnect():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/status")
def api_status():
    return jsonify({"conectado": "api_key" in session})


if __name__ == "__main__":
    app.run(debug=True, port=5050, host="0.0.0.0")
