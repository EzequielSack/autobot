"""
comparar_pares.py — Análisis comparativo BTC vs ETH vs SOL post-backtest.
Corre después de backtest.py, lee los CSVs generados por par.
"""
import pandas as pd
import numpy as np
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

CAPITAL    = 300.0
PARES      = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
RISK_W     = {"BTCUSDT": 0.50, "ETHUSDT": 0.30, "SOLUSDT": 0.20}

resultados = {}

for par in PARES:
    path = f"data/bt_{par.lower()}.csv"
    try:
        df = pd.read_csv(path)
    except FileNotFoundError:
        print(f"[{par}] CSV no encontrado — saltando")
        continue

    df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True)
    df["exit_time"]  = pd.to_datetime(df["exit_time"],  utc=True)
    df["hora"]       = df["entry_time"].dt.hour
    df["weekday"]    = df["entry_time"].dt.day_name()

    dias_bt   = (df.entry_time.max() - df.entry_time.min()).days or 1
    total     = len(df)
    wins      = (df.resultado == "WIN").sum()
    losses    = (df.resultado == "LOSS").sum()
    bes       = (df.resultado == "BE").sum()
    wr        = wins / total * 100 if total else 0
    pnl_total = df.pnl_neto.sum()
    pnl_dia   = pnl_total / dias_bt

    # Drawdown máximo
    equity      = pd.Series([CAPITAL] + list(df.capital_post))
    rolling_max = equity.cummax()
    max_dd      = ((equity - rolling_max) / rolling_max * 100).min()

    # Profit factor
    g = df[df.pnl_neto > 0].pnl_neto.sum()
    p = abs(df[df.pnl_neto < 0].pnl_neto.sum())
    pf = round(g / p, 3) if p > 0 else float("inf")

    # EV por trade ajustado por risk_weight
    ev_trade = df.pnl_neto.mean()

    resultados[par] = {
        "dias_bt":       dias_bt,
        "total":         total,
        "wins":          int(wins),
        "losses":        int(losses),
        "bes":           int(bes),
        "win_rate":      round(wr, 1),
        "profit_factor": pf,
        "pnl_total":     round(pnl_total, 2),
        "pnl_dia":       round(pnl_dia, 2),
        "max_dd":        round(max_dd, 2),
        "ev_trade":      round(ev_trade, 2),
        "trades_dia":    round(total / dias_bt, 3),
        "df":            df,
    }

    print(f"\n{'='*58}")
    print(f"  {par}  —  {dias_bt} dias  |  RISK_WEIGHT: {RISK_W[par]}")
    print(f"{'='*58}")
    print(f"  Trades totales : {total}  ({total/dias_bt:.2f}/dia)")
    print(f"  WIN/LOSS/BE    : {wins}/{losses}/{bes}  |  Win Rate: {wr:.1f}%")
    print(f"  PnL total      : USD {pnl_total:.2f}  ({pnl_total/CAPITAL*100:.1f}%)")
    print(f"  PnL/dia medio  : USD {pnl_dia:.2f}")
    print(f"  Max Drawdown   : {max_dd:.1f}%")
    print(f"  Profit Factor  : {pf}")
    print(f"  EV/trade medio : USD {ev_trade:.2f}")

    # Peores horas
    hora_stats = df.groupby("hora").agg(
        trades=("pnl_neto", "count"),
        pnl=("pnl_neto", "sum"),
        wr=("resultado", lambda x: (x=="WIN").mean()*100)
    ).reset_index()
    peores = hora_stats.nsmallest(4, "pnl")
    print(f"\n  Peores horas UTC:")
    for _, r in peores.iterrows():
        print(f"    {int(r.hora):2d}h  {int(r.trades):3d} trades  WR {r.wr:.0f}%  PnL USD {r.pnl:.2f}")

    # Peores días de semana
    day_stats = df.groupby("weekday").agg(
        trades=("pnl_neto", "count"),
        pnl=("pnl_neto", "sum"),
        wr=("resultado", lambda x: (x=="WIN").mean()*100)
    ).reset_index()
    peores_dias = day_stats.nsmallest(2, "pnl")
    print(f"\n  Peores dias:")
    for _, r in peores_dias.iterrows():
        print(f"    {r.weekday:10s}  {int(r.trades):3d} trades  WR {r.wr:.0f}%  PnL USD {r.pnl:.2f}")

# ── CUADRO COMPARATIVO ────────────────────────────────────────────────────────
print(f"\n\n{'='*65}")
print("  CUADRO COMPARATIVO BTC vs ETH vs SOL")
print(f"{'='*65}")
print(f"  {'Métrica':<24} {'BTCUSDT':>12} {'ETHUSDT':>12} {'SOLUSDT':>12}")
print(f"  {'-'*60}")

metricas_tabla = [
    ("Trades/dia",       "trades_dia",    "{:.3f}"),
    ("Win Rate %",       "win_rate",      "{:.1f}%"),
    ("Profit Factor",    "profit_factor", "{:.3f}"),
    ("PnL/dia USD",      "pnl_dia",       "{:.2f}"),
    ("Max Drawdown %",   "max_dd",        "{:.1f}%"),
    ("EV/trade USD",     "ev_trade",      "{:.2f}"),
    ("PnL total USD",    "pnl_total",     "{:.2f}"),
]

for label, key, fmt in metricas_tabla:
    fila = f"  {label:<24}"
    for par in PARES:
        if par in resultados:
            val = resultados[par][key]
            fila += f" {fmt.format(val):>12}"
        else:
            fila += f" {'N/A':>12}"
    print(fila)

# ── RECOMENDACIÓN ─────────────────────────────────────────────────────────────
print(f"\n{'='*65}")
print("  RECOMENDACIÓN")
print(f"{'='*65}")

umbrales = {"pf_min": 1.5, "max_dd_max": -25.0, "wr_min": 18.0}

for par in PARES:
    if par not in resultados:
        continue
    r = resultados[par]
    problemas = []
    if r["profit_factor"] < umbrales["pf_min"]:
        problemas.append(f"PF {r['profit_factor']} < {umbrales['pf_min']}")
    if r["max_dd"] < umbrales["max_dd_max"]:
        problemas.append(f"Drawdown {r['max_dd']}% < {umbrales['max_dd_max']}%")
    if r["win_rate"] < umbrales["wr_min"]:
        problemas.append(f"Win Rate {r['win_rate']}% < {umbrales['wr_min']}%")

    if not problemas:
        veredicto = "APTO PARA REAL TRADING"
        emoji     = "✅"
    else:
        veredicto = "MANTENER EN PAPER"
        emoji     = "⚠️ "
    print(f"  {emoji} {par}: {veredicto}")
    if problemas:
        for p in problemas: print(f"       - {p}")
