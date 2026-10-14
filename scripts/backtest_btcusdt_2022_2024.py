"""Research-only 2022-24 BTCUSDT M15: actual archived prices, no order API.

0.1% fee EACH side, 0.05% slippage EACH side, one FULL bar latency
(signal after candle close t, wait bar t+1, fill at open t+2).
Original four-arm strategies and fixed 7/12 confluence are NOT optimized.
"""
from __future__ import annotations
import argparse,hashlib,json
from pathlib import Path
import numpy as np
import pandas as pd
from research_bot.strategies import evaluate_all,combine

PER_YEAR=365*96
COST=0.0015

def read_verified(data:Path,manifest_file:Path):
    m=json.loads(manifest_file.read_text())
    if hashlib.sha256(data.read_bytes()).hexdigest()!=m["output_sha256"]:
        raise ValueError("DATASET_HASH_MISMATCH")
    archives=m.get("publisher_zip_checksums",[])
    if len(archives)!=36 or any(len(z["zip_sha256"])!=64 for z in archives):
        raise ValueError("NOT_36_PUBLISHER_VERIFIED_MONTHS")
    if sum(z["rows"] for z in archives)!=m["rows"]:
        raise ValueError("SOURCE_COUNTS_MISMATCH")
    df=pd.read_parquet(data)
    if len(df)!=m["rows"] or len(df)!=105216-m["missing_bars"]:
        raise ValueError("GAPS_NOT_ACCOUNTED_FOR")
    if df.timestamp.iloc[0]!=pd.Timestamp("2022-01-01T00:00:00Z") or df.timestamp.iloc[-1]>pd.Timestamp("2024-12-31T23:45:00Z"):
        raise ValueError("WRONG_HISTORICAL_INTERVAL")
    if not df.timestamp.is_monotonic_increasing or not df.timestamp.is_unique:
        raise ValueError("DUPLICATE_OR_UNSORTED_TIMES")
    if df.timestamp.dt.tz is None or df.available_at.dt.tz is None:
        raise ValueError("NAIVE_TIME")
    if not ((df.available_at-df.timestamp)==pd.Timedelta(minutes=15)).all():
        raise ValueError("FORMING_BAR_OR_AVAILABILITY_UNKNOWN")
    p=df[["open","high","low","close","volume"]].to_numpy(float)
    if not np.isfinite(p).all() or (p[:,:4]<=0).any() or (p[:,4]<0).any():
        raise ValueError("INVALID_MARKET_OHLCV")
    if not (df.high>=df[["open","low","close"]].max(axis=1)).all() or not (df.low<=df[["open","high","close"]].min(axis=1)).all():
        raise ValueError("INCONSISTENT_HIGH_LOW")
    return df,m

def upper_bound_candidates(x:pd.DataFrame):
    """Necessary conditions for 3 of 4 signals; final actual engine rechecks."""
    h,l,c,o=x.high,x.low,x.close,x.open
    midpoint=lambda n:(h.rolling(n).max()+l.rolling(n).min())/2
    ten,kij=midpoint(9),midpoint(26)
    a=((ten+kij)/2).shift(26)
    b=midpoint(52).shift(26)
    hi=pd.concat([a,b],axis=1).max(axis=1)
    lo=pd.concat([a,b],axis=1).min(axis=1)
    ich=(
        ((c>hi)&(ten>kij)&(
          (c.shift(1)<=hi.shift(1)) | (ten.shift(1)<=kij.shift(1)) |
          ((c.shift(1)<=kij.shift(1))&(c>kij))))
        | ((c<lo)&(ten<kij)&(
          (c.shift(1)>=lo.shift(1)) | (ten.shift(1)>=kij.shift(1)) |
          ((c.shift(1)>=kij.shift(1))&(c<kij))))
    )
    lq=l.shift(1).rolling(20).min()
    hq=h.shift(1).rolling(20).max()
    sweep=((l<lq)&(c>lq))|((h>hq)&(c<hq))
    ict=sweep.shift(1).rolling(8).max().fillna(0).astype(bool)
    smc=((c>h.shift(1).rolling(12).max())&(c>o))|((c<l.shift(1).rolling(12).min())&(c<o))
    ema=c.ewm(span=20,adjust=False).mean()
    candle=(h-l).replace(0,np.nan)
    body=(c-o).abs()/candle
    clv=(c-l)/candle
    brooks=((c>ema)&(ema>ema.shift(4))&(c>o)&(body>=.58)&(clv>=.78))|(
        (c<ema)&(ema<ema.shift(4))&(c<o)&(body>=.58)&(clv<=.22))
    # No new setup may consume a 90-bar window containing an exchange gap.
    contiguous=(x.timestamp.diff()==pd.Timedelta(minutes=15))
    valid=contiguous.astype(int).rolling(89,min_periods=89).sum()==89
    return (((ich.astype(int)+ict.astype(int)+smc.astype(int)+brooks.astype(int))>=3)&valid).to_numpy(bool)

def confluence_positions(x:pd.DataFrame):
    possible=upper_bound_candidates(x)
    target=np.zeros(len(x),dtype=np.int8)
    long=0
    votes={"BUY":0,"SELL":0}
    for idx in range(89,len(x)):
        if possible[idx]:
            data=x.iloc[idx-89:idx+1]
            t=data.available_at.iloc[-1].to_pydatetime()
            combined=combine(evaluate_all(data,as_of=t),as_of=t)
            if combined.action=="BUY":
                long=1;votes["BUY"]+=1
            elif combined.action=="SELL":
                long=0;votes["SELL"]+=1
        target[idx]=long
    actual=np.zeros(len(x),dtype=np.int8)
    actual[2:]=target[:-2]  # one complete intervening candle then next-bar open
    # Falsification audit: sample rejected prefilter bars and execute the full
    # original four engines; ANY positive vote here proves the shortcut unsafe.
    excluded=np.flatnonzero((~possible)&(np.arange(len(x))>=89))
    rng=np.random.default_rng(20261014)
    sample=rng.choice(excluded,size=min(100,len(excluded)),replace=False)
    for idx in sample:
        part=x.iloc[idx-89:idx+1]
        t=part.available_at.iloc[-1].to_pydatetime()
        if combine(evaluate_all(part,as_of=t),as_of=t).action!="HOLD":
            raise RuntimeError("NECESSARY_PREFILTER_FALSE_NEGATIVE")
    return actual,{"eligible_candidate_bars":int(possible.sum()),"actual_votes":votes,
                   "excluded_bar_audit_sample":len(sample),"execution_delay_bars":2}

def simulate(px:np.ndarray,pos:np.ndarray):
    """Cash/spot all-in rebalancing with two-side fee/slippage and final exit."""
    nav=np.empty(len(px));rt=np.zeros(len(px)-1)
    balance=1.0;entry=None;entry_bar=0;trades=[];exposure=0
    for i in range(len(px)-1):
        before=balance
        p=int(pos[i]);old=int(pos[i-1]) if i else 0
        if p!=old:
            balance*=1-COST
            if p:
                entry=float(px[i]);entry_bar=i
            else:
                if entry is not None:
                    pnl=float(px[i]*(1-COST)/(entry*(1+COST))-1)
                    trades.append({"hold_bars":i-entry_bar,"pnl":pnl})
                entry=None
        if p:
            balance*=float(px[i+1]/px[i]);exposure+=1
        nav[i]=balance;rt[i]=balance/before-1
    if pos[-2]:
        balance*=1-COST
        if entry is not None:
            trades.append({"hold_bars":len(px)-1-entry_bar,
                           "pnl":float(px[-1]*(1-COST)/(entry*(1+COST))-1)})
    nav[-1]=balance
    rt[-1]=nav[-1]/nav[-2]-1
    nav=np.concatenate(([1.0],nav))
    maxdd=float(-(nav/np.maximum.accumulate(nav)-1).min())
    avg=float(rt.mean());std=float(rt.std(ddof=1))
    below=float(np.sqrt(np.mean(np.minimum(rt,0)**2)))
    wins=[t["pnl"] for t in trades if t["pnl"]>0]
    losses=[-t["pnl"] for t in trades if t["pnl"]<0]
    return {
      "net_return":float(balance-1),
      "sharpe":float(np.sqrt(PER_YEAR)*avg/std) if std>1e-14 else None,
      "sortino":float(np.sqrt(PER_YEAR)*avg/below) if below>1e-14 else None,
      "max_drawdown":maxdd,
      "profit_factor":float(sum(wins)/sum(losses)) if losses else None,
      "win_rate":float(len(wins)/len(trades)) if trades else None,
      "trade_count":len(trades),"exposure_fraction":float(exposure/max(1,len(px)-1))
    },trades

def random_baseline(n,durations,seed):
    """Preserves completed trade count and hold lengths; randomizes idle gaps."""
    out=np.zeros(n,dtype=np.int8)
    if not durations:return out
    g=np.random.default_rng(seed)
    length=np.array(durations,dtype=int)
    g.shuffle(length)
    free=int(n-2-length.sum())
    if free<0:raise ValueError("MATCHED_RANDOM_NOT_FEASIBLE")
    cut=np.sort(g.integers(0,free+1,size=len(length)-1))
    idle=np.diff(np.concatenate(([0],cut,[free])))
    k=2
    for d,pause in zip(length,idle):
        k+=int(pause);out[k:k+int(d)]=1;k+=int(d)
    return out

def main():
    a=argparse.ArgumentParser()
    a.add_argument("--data",type=Path,required=True)
    a.add_argument("--manifest",type=Path,required=True)
    a.add_argument("--output",type=Path,required=True)
    cfg=a.parse_args()
    frame,provenance=read_verified(cfg.data,cfg.manifest)
    prices=frame.open.to_numpy(float)
    strategy_pos,events=confluence_positions(frame)
    model,trades=simulate(prices,strategy_pos)
    hold=np.ones(len(frame),dtype=np.int8);hold[:2]=0
    bh,_=simulate(prices,hold)
    ma20=frame.close.rolling(20).mean()
    ma50=frame.close.rolling(50).mean()
    masig=((ma20>ma50)&ma50.notna()).astype(np.int8).to_numpy()
    mapos=np.zeros(len(frame),dtype=np.int8);mapos[2:]=masig[:-2]
    ma,_=simulate(prices,mapos)
    durations=[t["hold_bars"] for t in trades]
    rand=[]
    for seed in range(20261014,20261034):
        z,_=simulate(prices,random_baseline(len(frame),durations,seed))
        rand.append(z)
    random_median={k:(float(np.median([run[k] for run in rand if run[k] is not None]))
                      if any(run[k] is not None for run in rand) else None) for k in model}
    is_winner=model["net_return"]>max(bh["net_return"],ma["net_return"],random_median["net_return"])
    verdict="WINNER_EXPLORATORY_NOT_OOS" if is_winner else "LOSER_DO_NOT_PROMOTE"
    output={
      "verdict":verdict,"data":{"venue":"Binance Spot","symbol":"BTCUSDT","interval":"15m",
       "actual_bars":len(frame),"expected_bars":105216,"missing_bars":provenance["missing_bars"],
       "first":frame.timestamp.iloc[0].isoformat(),"last":frame.timestamp.iloc[-1].isoformat(),
       "parquet_sha256":provenance["output_sha256"]},
      "protocol":{"fee_bps_per_side":10,"slippage_bps_per_side":5,
        "wait_complete_candles":1,"fill_next_open_after_wait":True,
        "short_enabled":False,"leverage":1,"OOS_or_walk_forward_verified":False,
        "signal_cutoff":"3 agreeing styles and score >= 7/12, uncalibrated ordinal"},
      "primary_confluence":model,"buy_and_hold":bh,"random_20_seeds_median":random_median,
      "random_seeds":list(range(20261014,20261034)),
      "sma20_50":ma,"diagnostics":events,
      "comparison":{"vs_buy_hold_net":model["net_return"]-bh["net_return"],
        "vs_random_median_net":model["net_return"]-random_median["net_return"],
        "vs_sma20_50_net":model["net_return"]-ma["net_return"]},
      "limitations":["Exploratory fixed window; NOT an untouched out-of-sample performance claim",
        "Publisher missing candles are not interpolated",
        "Skip candidate signals spanning one of 90 previous irregular bars",
        "Open-to-open price proxy, not order-book microstructure or real fills",
        "If no closed trades, profit factor and win rate must be null",
        "No intrabar stop or target; ordinal styles are not calibrated probabilities"]
    }
    cfg.output.parent.mkdir(parents=True,exist_ok=True)
    cfg.output.write_text(json.dumps(output,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    print("BTCUSDT_M15_RESEARCH_RESULT",verdict,"model_return",model["net_return"],
          "buy_hold",bh["net_return"],"trades",model["trade_count"],flush=True)

if __name__=="__main__":main()
