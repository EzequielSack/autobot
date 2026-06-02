"""
optimizar_eth_sol.py — Grid search para ETH y SOL.
Objetivo: equilibrio volumen alto + Profit Factor sólido + drawdown moderado.
Usa los datos cacheados (1 año, 5m + 60m). No descarga nada.
"""
import io, sys, pickle, itertools
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import pandas as pd
import numpy as np

LEVERAGE        = 3
CAPITAL_INICIAL = 300.0
RISK_PER_TRADE  = 0.005
BB_PERIOD = 20
RSI_PERIOD = 14
ATR_PERIOD = 14
EMA_HTF_PERIOD = 50
VOL_PERIOD = 20
ADX_PERIOD = 14
COM = 0.00055   # comisión Bybit por lado

# ── Sensores ──────────────────────────────────────────────
def add_sensors(df, bb_std):
    df = df.copy()
    df["bb_mid"]   = df["close"].rolling(BB_PERIOD).mean()
    df["bb_sd"]    = df["close"].rolling(BB_PERIOD).std()
    df["bb_upper"] = df["bb_mid"] + bb_std * df["bb_sd"]
    df["bb_lower"] = df["bb_mid"] - bb_std * df["bb_sd"]
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
    delta = df["close"].diff()
    gain  = delta.clip(lower=0).rolling(RSI_PERIOD).mean()
    loss  = (-delta.clip(upper=0)).rolling(RSI_PERIOD).mean()
    df["rsi"] = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))
    df["tr"] = np.maximum(df["high"]-df["low"],
               np.maximum(abs(df["high"]-df["close"].shift(1)),
                          abs(df["low"]-df["close"].shift(1))))
    df["atr"] = df["tr"].rolling(ATR_PERIOD).mean()
    df["vol_avg"] = df["volume"].rolling(VOL_PERIOD).mean()
    h,l,c = df["high"],df["low"],df["close"]
    pdm=h.diff(); mdm=l.diff().abs()
    pdm=pdm.where((pdm>mdm)&(pdm>0),0); mdm=mdm.where((mdm>pdm)&(mdm>0),0)
    tr=np.maximum(h-l,np.maximum(abs(h-c.shift(1)),abs(l-c.shift(1))))
    atr_=pd.Series(tr).rolling(ADX_PERIOD).mean()
    pdi=100*pdm.rolling(ADX_PERIOD).mean()/atr_
    mdi=100*mdm.rolling(ADX_PERIOD).mean()/atr_
    dx=(abs(pdi-mdi)/(pdi+mdi).replace(0,np.nan))*100
    df["adx"]=dx.rolling(ADX_PERIOD).mean()
    return df

def htf_series(df_1h, df_5m):
    h=df_1h.copy()
    h["ema"]=h["close"].ewm(span=EMA_HTF_PERIOD,adjust=False).mean()
    h["trend"]="NEUTRAL"
    h.loc[h["close"]>h["ema"]*1.001,"trend"]="BULLISH"
    h.loc[h["close"]<h["ema"]*0.999,"trend"]="BEARISH"
    h=h.set_index("timestamp")
    s=h["trend"].reindex(df_5m["timestamp"],method="ffill")
    s.index=df_5m.index
    return s.fillna("NEUTRAL")

# ── Simulación parametrizada ──────────────────────────────
def simular(df, htf, p):
    rsi_os, rsi_ob = p["rsi_os"], p["rsi_ob"]
    sl_m, tp_m     = p["sl"], p["tp"]
    vol_m          = p["vol"]
    adx_th         = p["adx"]
    bb_min         = p["bb_min"]
    cap = CAPITAL_INICIAL
    ops=[]; en=False; tr={}
    arr = df.to_dict("records")
    n=len(arr)
    mc = max(BB_PERIOD,RSI_PERIOD,ATR_PERIOD,ADX_PERIOD,VOL_PERIOD)+5
    htfv = htf.values
    for i in range(mc, n):
        row=arr[i]; prev=arr[i-1]
        precio=row["close"]; hi=row["high"]; lo=row["low"]
        if en:
            sl=tr["sl"]; tp=tr["tp"]; side=tr["side"]
            cerr=False; res=None; pc=None
            if side=="LONG":
                if lo<=sl: pc=sl;res="LOSS";cerr=True
                elif hi>=tp: pc=tp;res="WIN";cerr=True
            else:
                if hi>=sl: pc=sl;res="LOSS";cerr=True
                elif lo<=tp: pc=tp;res="WIN";cerr=True
            if cerr:
                qty=tr["qty"]; ep=tr["ep"]
                pnl=(pc-ep)*qty*LEVERAGE if side=="LONG" else (ep-pc)*qty*LEVERAGE
                com=ep*qty*COM+pc*qty*COM
                cap+=pnl-com
                ops.append({"r":res,"pnl":pnl-com,"cap":cap})
                en=False; tr={}
                continue
        if not en and cap>10:
            bw=row["bb_width"]; rsi=row["rsi"]; atr=row["atr"]; adx=row["adx"]
            if pd.isna(bw) or bw<bb_min: continue
            if pd.isna(atr) or atr<precio*0.001: continue
            if row["volume"]<row["vol_avg"]*vol_m: continue
            mult=0.5 if (not pd.isna(adx) and adx>adx_th) else 1.0
            t=htfv[i]
            sig=None
            if prev["close"]>row["bb_lower"] and precio<=row["bb_lower"] and (not pd.isna(rsi)) and rsi<=rsi_os and t=="BULLISH":
                sig="LONG"
            elif prev["close"]<row["bb_upper"] and precio>=row["bb_upper"] and (not pd.isna(rsi)) and rsi>=rsi_ob and t=="BEARISH":
                sig="SHORT"
            if not sig: continue
            sd=atr*sl_m
            qty=max((cap*RISK_PER_TRADE*mult*LEVERAGE)/sd,1e-6)
            ep=precio
            if sig=="LONG": sl=ep-sd; tp=ep+atr*tp_m
            else: sl=ep+sd; tp=ep-atr*tp_m
            tr={"side":sig,"ep":ep,"qty":qty,"sl":sl,"tp":tp}
            en=True
    return ops

def metricas(ops):
    if len(ops)<5: return None
    df=pd.DataFrame(ops)
    total=len(df); wins=(df["r"]=="WIN").sum()
    wr=wins/total*100
    g=df[df["pnl"]>0]["pnl"].sum(); p=abs(df[df["pnl"]<0]["pnl"].sum())
    pf=g/p if p>0 else 99
    eq=pd.Series([CAPITAL_INICIAL]+list(df["cap"]))
    dd=((eq-eq.cummax())/eq.cummax()*100).min()
    ret=df["pnl"].sum()
    return {"trades":total,"wr":round(wr,1),"pf":round(pf,3),
            "dd":round(dd,1),"ret":round(ret,0)}

# ── Score equilibrio: volumen + PF + bajo drawdown ────────
def score(m):
    if not m or m["trades"]<40: return -999   # exigir volumen mínimo
    if m["pf"]<1.2: return -999                 # piso de rentabilidad
    # premia PF y volumen, penaliza drawdown
    return (m["pf"]*40) + (min(m["trades"],200)*0.15) - (abs(m["dd"])*1.2)

# ── Grid ──────────────────────────────────────────────────
GRID = {
    "rsi_os":[25,30,35], "rsi_ob":[65,70,75],
    "sl":[1.0,1.2,1.5], "tp":[2.5,3.5,4.2],
    "vol":[1.2,1.5,1.8], "adx":[25], "bb_min":[0.008,0.012],
}

def optimizar(par):
    print(f"\n{'='*60}\n  OPTIMIZANDO {par.upper()}\n{'='*60}")
    df5=pickle.load(open(f"data/cache_{par}_5m.pkl","rb"))
    df1=pickle.load(open(f"data/cache_{par}_60m.pkl","rb"))
    keys=list(GRID.keys())
    combos=list(itertools.product(*[GRID[k] for k in keys]))
    print(f"  Probando {len(combos)} combinaciones sobre {len(df5)} velas...")
    # Pre-calcular sensores por bb_std (acá bb_std fijo=2.0, varía bb_min como filtro)
    base=add_sensors(df5,2.0)
    htf=htf_series(df1,base)
    resultados=[]
    for n,combo in enumerate(combos):
        p=dict(zip(keys,combo))
        ops=simular(base,htf,p)
        m=metricas(ops)
        if m:
            resultados.append((score(m),p,m))
        if (n+1)%50==0: print(f"    {n+1}/{len(combos)}...")
    resultados.sort(key=lambda x:x[0],reverse=True)
    print(f"\n  TOP 5 configuraciones (equilibrio volumen+PF+drawdown):")
    print(f"  {'PF':>5} {'Trades':>7} {'WR%':>6} {'DD%':>7} {'Ret$':>7}  Parámetros")
    for sc,p,m in resultados[:5]:
        cfg=f"RSI{p['rsi_os']}/{p['rsi_ob']} SL{p['sl']} TP{p['tp']} VOL{p['vol']} bb{p['bb_min']}"
        print(f"  {m['pf']:>5} {m['trades']:>7} {m['wr']:>6} {m['dd']:>7} {m['ret']:>7.0f}  {cfg}")
    return resultados[0] if resultados else None

if __name__=="__main__":
    mejores={}
    for par in ["ethusdt","solusdt"]:
        best=optimizar(par)
        if best: mejores[par]=best
    print(f"\n{'='*60}\n  MEJORES CONFIGURACIONES ENCONTRADAS\n{'='*60}")
    for par,(sc,p,m) in mejores.items():
        print(f"\n  {par.upper()}:")
        print(f"    PF {m['pf']} | {m['trades']} trades | WR {m['wr']}% | DD {m['dd']}% | Ret ${m['ret']:.0f}")
        print(f"    {p}")
