"""Fixed-basket v5 daily Wilder RSI(14) reversion: entry<30, exit>55 or 5 days.
Read only Binance public market data; run 2022-23 Train and 2024 Val ONLY.
Caveat: 20 frozen symbols are not audited historical top-20 membership.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from scripts.execute_v4_fixed20_trainval import SYMBOLS,download,perf,START,TRAIN_END,END,SIDE_COST

OUT=Path("results/v5_fixed20_trainval.json")

def wilder_rsi(close,n=14):
    change=close.diff()
    gain=change.clip(lower=0).ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    loss=(-change.clip(upper=0)).ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    ratio=gain/loss.replace(0,np.nan)
    result=100-100/(1+ratio)
    return result.fillna(50.).where(gain.notna())

def simulate(series):
    dates=pd.date_range(START,periods=(pd.Timestamp(END)-pd.Timestamp(START)).days,freq="D",tz="UTC")
    for s,data in series.items():
        series[s]=data.assign(rsi=wilder_rsi(data.close))
    cash=1.;q={};entry={};entrybar={};costs=0.;orders=0;closed=[];nav=[]
    for i,day in enumerate(dates):
        prices={}
        for s,df in series.items():
            if day in df.index:prices[s]=float(df.at[day,"open"])
            elif s in q:raise RuntimeError("MISSING_OBSERVED_BAR_FOR_HELD_SYMBOL_"+s+"_"+str(day))
        if i:
            prev=dates[i-1]
            for s in SYMBOLS:
                if s not in prices or prev not in series[s].index:continue
                previous_rsi=series[s].at[prev,"rsi"]
                if not np.isfinite(previous_rsi):continue
                open_price=prices[s]
                if s in q:
                    if previous_rsi>55 or i-entrybar[s]>=5:
                        qty=q.pop(s)
                        cash+=qty*open_price*(1-SIDE_COST)
                        costs+=qty*open_price*SIDE_COST
                        orders+=1
                        closed.append({"entry":str(entry[s][1].date()),"exit":str(day.date()),
                           "pnl":float(open_price*(1-SIDE_COST)/(entry[s][0]*(1+SIDE_COST))-1)})
                        entry.pop(s)
                        entrybar.pop(s)
                else:
                    if previous_rsi<30:
                        equity=cash+sum(qty*prices[s2] for s2,qty in q.items())
                        invested=min(cash,equity*.05)
                        if invested>=1e-7:
                            qty=invested/(open_price*(1+SIDE_COST))
                            cash-=invested
                            q[s]=qty
                            entry[s]=(open_price,day)
                            entrybar[s]=i
                            costs+=qty*open_price*SIDE_COST
                            orders+=1
        equity=cash
        for s,qty in q.items():
            if day not in series[s].index:
                raise RuntimeError("MARKING_MISSING_CLOSE_"+s)
            equity+=qty*float(series[s].at[day,"close"])
        if equity<=0 or not np.isfinite(equity):raise RuntimeError("INVALID_NAV")
        nav.append(float(equity))
    final=dates[-1]
    for s,qty in q.items():
        close=float(series[s].at[final,"close"])
        cash+=qty*close*(1-SIDE_COST)
        costs+=qty*close*SIDE_COST
        closed.append({"entry":str(entry[s][1].date()),"exit":str(final.date()),"pnl":float(close*(1-SIDE_COST)/(entry[s][0]*(1+SIDE_COST))-1)})
    nav[-1]=float(cash)
    return pd.Series(nav,index=dates),closed,orders,costs

def per_split(nav,records,mask):
    result=perf(nav[mask])
    lo=nav[mask].index[0]
    hi=nav[mask].index[-1]
    closed=[float(x["pnl"]) for x in records if lo<=pd.Timestamp(x["exit"],tz="UTC")<=hi]
    wins=[p for p in closed if p>0]
    losses=[-p for p in closed if p<0]
    result["Trades"]=len(closed)
    result["WinRate"]=len(wins)/len(closed) if closed else None
    result["ProfitFactor"]=sum(wins)/sum(losses) if losses else None
    return result

def main():
    df={s:download(s) for s in SYMBOLS}
    nav,closed,orders,fees=simulate(df)
    index=nav.index
    train=index<pd.Timestamp(TRAIN_END,tz="UTC")
    a=per_split(nav,closed,train)
    b=per_split(nav,closed,~train)
    btc=df["BTCUSDT"].reindex(index)
    if btc.close.isna().any():raise RuntimeError("BTC_SOURCE_INCOMPLETE")
    btc_nav=pd.Series(btc.close.to_numpy()/btc.open.iloc[0],index=index)
    baseline_a=perf(btc_nav[train]);baseline_b=perf(btc_nav[~train])
    verdict="PASS" if b["Sharpe"] is not None and b["Sharpe"]>.6 and b["NetReturn"]>baseline_b["NetReturn"] else "FAIL"
    result={"strategy":"v5 Wilder RSI(14) daily mean reversion; enter<30 exit>55 or five bars",
      "fixed_universe_survivorship_risk":True,"historical_top20_verified":False,
      "symbols":list(SYMBOLS),"train_2022_2023":a,"validation_2024":b,
      "train_BTC":baseline_a,"validation_BTC":baseline_b,
      "trades_total":len(closed),"orders_total":orders,
      "nominal_fee_plus_slippage_side_bps":15,"costs_quote_per_unit_equity":fees,
      "verdict":verdict,"test3_opened":False,
      "caveat":"Fixed 20 coin basket NOT verified exact top-20 at historical dates; thus exploratory v5 rather than canonical PIT universe."
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(result,indent=2,allow_nan=False))
    print("V5_TRAIN_SHARPE",a["Sharpe"],"V5_VAL_SHARPE",b["Sharpe"],"VAL_BTC_RETURN",baseline_b["NetReturn"],"VAL_NET_RETURN",b["NetReturn"],"VERDICT",verdict,flush=True)
if __name__=="__main__":main()
