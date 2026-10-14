"""Causal long-spot exits: ATR stop, 50/30/20 1R/2R/trailing, 20-bar expiry.

M15 bar execution with STOP_FIRST for intrabar SL+TP ambiguity.
An adverse gap fills at open, never at an unrealistically favorable stop.
All new positions have 0.25% NAV initial stop risk (spot notional capped 100%).
SIMULATOR ONLY. No external orders or real account access.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd

COST=0.0015
DELAY_BARS=2
RISK_BUDGET=0.0025
YEAR_BARS=365*96


def atr_wilder(bars:pd.DataFrame,n:int=14)->pd.Series:
    prev=bars.close.shift(1)
    tr=pd.concat([(bars.high-bars.low),(bars.high-prev).abs(),
                  (bars.low-prev).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/n,adjust=False,min_periods=n).mean()


def performance(nav:np.ndarray,trades:list,exposure:int):
    from scipy.stats import skew,kurtosis
    nav=np.asarray(nav,dtype=float)
    if not np.isfinite(nav).all() or (nav<=0).any():
        raise ValueError("NONPOSITIVE_OR_INVALID_NAV")
    returns=nav[1:]/nav[:-1]-1.
    ave=float(returns.mean())
    std=float(returns.std(ddof=1)) if len(returns)>1 else 0.
    downside=float(np.sqrt(np.mean(np.minimum(returns,0)**2)))
    profits=[float(t["net_return"]) for t in trades if t["net_return"]>0]
    losses=[-float(t["net_return"]) for t in trades if t["net_return"]<0]
    return {"net_return":float(nav[-1]/nav[0]-1),
            "sharpe":float(np.sqrt(YEAR_BARS)*ave/std) if std>1e-12 else None,
            "sortino":float(np.sqrt(YEAR_BARS)*ave/downside) if downside>1e-12 else None,
            "max_drawdown":float(-(nav/np.maximum.accumulate(nav)-1).min()),
            "profit_factor":float(sum(profits)/sum(losses)) if losses else None,
            "win_rate":float(len(profits)/len(trades)) if trades else None,
            "trade_count":len(trades),"exposure_fraction":float(exposure/max(1,len(nav)-1))}


@dataclass
class OpenLot:
    entry:float
    entry_bar:int
    initial_r:float
    stop:float
    total_qty:float
    remaining:float
    entry_nav:float
    start_regime:str
    hit1:bool=False
    hit2:bool=False
    time_expiring:bool=False


def simulate_atr_exits(
    frame:pd.DataFrame,entry_at:np.ndarray,regime:np.ndarray,
    *,initial_equity:float=1.0
)->tuple[dict,list,np.ndarray]:
    """OHLCV-bar fill model; returns metrics, traceable trades and NAV series.

    Every entry_at[i] is an already decided BUY from candle i-2.
    The portfolio can hold at most one position; no overlapping entries.
    Exit targets use initial risk R=2*ATR at decision time, NOT current ATR.
    """
    n=len(frame)
    if len(entry_at)!=n or len(regime)!=n:raise ValueError("BAD_EVENT_SHAPE")
    atr=atr_wilder(frame)
    opens=frame.open.to_numpy(float)
    highs=frame.high.to_numpy(float)
    lows=frame.low.to_numpy(float)
    closes=frame.close.to_numpy(float)
    atrs=atr.to_numpy(float)
    observed=frame.timestamp.to_numpy()
    cash=float(initial_equity)
    qty=0.
    active:OpenLot|None=None
    nav=np.zeros(n+1,dtype=float);nav[0]=cash
    trades=[]
    exposure=0
    for i in range(n):
        if i>0 and observed[i]-observed[i-1]!=np.timedelta64(15,"m") and active is not None:
            # Source gap => do not infer SL/TP sequence across unobserved bars.
            cash+=qty*opens[i]*(1-COST)
            qty=0.
            trades.append({"entry_bar":active.entry_bar,"exit_bar":i,
                           "entry_regime":active.start_regime,"reason":"DATA_GAP_UNVERIFIABLE",
                           "net_return":cash/active.entry_nav-1,
                           "initial_r_pct":active.initial_r/active.entry})
            active=None
        if active is not None and active.time_expiring:
            cash+=qty*opens[i]*(1-COST)
            qty=0.
            trades.append({"entry_bar":active.entry_bar,"exit_bar":i,
                           "entry_regime":active.start_regime,"reason":"TIME_20_NOT_1R",
                           "net_return":cash/active.entry_nav-1,
                           "initial_r_pct":active.initial_r/active.entry})
            active=None
        if active is None and bool(entry_at[i]) and i>=2:
            # ATR known at signal's close i-2; no same-bar volatility lookahead.
            a=atrs[i-2]
            if np.isfinite(a) and a>0:
                p=opens[i]
                stop_dist=2*a
                notional_fraction=min(1.0,RISK_BUDGET/(stop_dist/p))
                notional=cash*notional_fraction
                if notional>0:
                    buy_qty=notional/(p*(1+COST))
                    balance_before=cash
                    cash-=notional
                    qty=buy_qty
                    active=OpenLot(entry=float(p),entry_bar=i,
                                   initial_r=float(stop_dist),stop=float(p-stop_dist),
                                   total_qty=float(buy_qty),remaining=float(buy_qty),
                                   entry_nav=balance_before,start_regime=str(regime[i-2]))
        if active is not None:
            exposure+=1
            base=active.entry
            rdist=active.initial_r
            # STOP_FIRST: even if a bar simultaneously touched SL and 1R/2R,
            # stop the entire remaining position before recording a TP.
            if lows[i]<=active.stop:
                exit_price=min(opens[i],active.stop)
                cash+=qty*exit_price*(1-COST)
                qty=0.
                trades.append({"entry_bar":active.entry_bar,"exit_bar":i,
                               "entry_regime":active.start_regime,"reason":"ATR_STOP_FIRST",
                               "net_return":cash/active.entry_nav-1,
                               "initial_r_pct":rdist/base,"hit1":active.hit1,"hit2":active.hit2})
                active=None
            else:
                if not active.hit1 and highs[i]>=base+rdist:
                    quantity=min(qty,active.total_qty*.50)
                    cash+=quantity*(base+rdist)*(1-COST)
                    qty-=quantity
                    active.hit1=True
                if active is not None and not active.hit2 and highs[i]>=base+2*rdist:
                    quantity=min(qty,active.total_qty*.30)
                    cash+=quantity*(base+2*rdist)*(1-COST)
                    qty-=quantity
                    active.hit2=True
                # The final 20% trails AFTER reaching 2R.
                # Update at bar END, only executable from next bar.
                if active.hit2 and np.isfinite(atrs[i]):
                    active.stop=max(active.stop,highs[i]-2*atrs[i])
                # Time stop is scheduled for the NEXT candle's open.
                if i-active.entry_bar+1>=20 and not active.hit1:
                    active.time_expiring=True
        nav[i+1]=cash+qty*closes[i]
    if active is not None:
        # The backtest ends at an actual observed close; never leave unrealized
        # PnL incorrectly marked as realized 100%-win capital.
        cash+=qty*closes[-1]*(1-COST)
        qty=0.
        trades.append({"entry_bar":active.entry_bar,"exit_bar":n-1,
                       "entry_regime":active.start_regime,"reason":"END_OF_BACKTEST",
                       "net_return":cash/active.entry_nav-1,
                       "initial_r_pct":active.initial_r/active.entry})
        nav[-1]=cash
    return performance(nav,trades,exposure),trades,nav


def optimize_on_training(
    frame:pd.DataFrame,
    signals_by_threshold:dict[int,np.ndarray],
    regime:np.ndarray,
    train_mask:np.ndarray,
    candidate_thresholds:tuple[int,...]=(6,7,8,9),
):
    """Freeze threshold before evaluation on 2024; no test-set optimization."""
    if set(candidate_thresholds)!=set(signals_by_threshold):
        raise ValueError("REGISTRY_OF_ALL_TRIALS_REQUIRED")
    if not train_mask.any():raise ValueError("NO_TRAINING_BARS")
    end=int(np.flatnonzero(train_mask)[-1]+1)
    training=frame.iloc[:end]
    scores={}
    for k in candidate_thresholds:
        pos=signals_by_threshold[k][:end]
        stats,events,_=simulate_atr_exits(training,pos,regime[:end])
        scores[k]=stats
    # Annualized Sharpe is unstable for few events; at least 10 completed trades
    # and positive finite Sharpe are required for eligibility.
    eligible=[k for k,x in scores.items() if x["trade_count"]>=10 and
              x["sharpe"] is not None and np.isfinite(x["sharpe"])]
    if not eligible:
        return None,scores
    winner=max(eligible,key=lambda k:(scores[k]["sharpe"],-k))
    return winner,scores
