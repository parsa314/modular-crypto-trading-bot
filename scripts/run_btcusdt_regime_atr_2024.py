"""BTCUSDT M15 real-data HMM + ATR exit scientific challenger.

Research-only. Fit HMM and optimize threshold on 2022-23. Freeze and
report ALL 2024 holdout outcomes incl failure/NULL. Historical P3 remains
a permanently preserved negative baseline; we never overwrite its JSON.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from research_bot.regime_detector import fit_hmm_2022_2023,atr_series
from research_bot.regime_exit_lab import simulate_atr_exits,optimize_on_training
from research_bot.regime_statistics import cscv_pbo_6,deflated_sharpe_2024
from research_bot.strategies import (
    evaluate_all,combine,evaluate_ichimoku,evaluate_ict,
    evaluate_smc,evaluate_al_brooks,
)
from scripts.backtest_btcusdt_2022_2024 import (
    read_verified,upper_bound_candidates,simulate as legacy_simulate,
)

CUTOFF=pd.Timestamp("2024-01-01T00:00:00Z")
THRESHOLDS=(6,7,8,9)


def individual_causal_flags(df:pd.DataFrame)->dict[str,np.ndarray]:
    """Fast necessary conditions only; actual strategy modules validate votes."""
    h,l,c,o=df.high,df.low,df.close,df.open
    mid=lambda n:(h.rolling(n).max()+l.rolling(n).min())/2
    ten,kij=mid(9),mid(26)
    ca=((ten+kij)/2).shift(26)
    cb=mid(52).shift(26)
    top=pd.concat([ca,cb],axis=1).max(axis=1)
    bottom=pd.concat([ca,cb],axis=1).min(axis=1)
    ich=((c>top)&(ten>kij)&((c.shift(1)<=top.shift(1))|
           (ten.shift(1)<=kij.shift(1))|((c.shift(1)<=kij.shift(1))&(c>kij))))|(
          (c<bottom)&(ten<kij)&((c.shift(1)>=bottom.shift(1))|
           (ten.shift(1)>=kij.shift(1))|((c.shift(1)>=kij.shift(1))&(c<kij))))
    low20=l.shift(1).rolling(20).min()
    high20=h.shift(1).rolling(20).max()
    sweep=((l<low20)&(c>low20))|((h>high20)&(c<high20))
    ict=sweep.shift(1).rolling(9).max().fillna(0).astype(bool)
    smc=((c>h.shift(1).rolling(12).max())&(c>o))|(
          (c<l.shift(1).rolling(12).min())&(c<o))
    ema=c.ewm(span=20,adjust=False).mean()
    span=(h-l).replace(0,np.nan)
    body=(c-o).abs()/span
    clv=(c-l)/span
    br=((c>ema)&(ema>ema.shift(4))&(c>o)&(body>=.58)&(clv>=.78))|(
         (c<ema)&(ema<ema.shift(4))&(c<o)&(body>=.58)&(clv<=.22))
    contiguous=(df.timestamp.diff()==pd.Timedelta(minutes=15))
    valid=(contiguous.astype(int).rolling(89,min_periods=89).sum()==89)
    return {"ICHIMOKU":(ich&valid).to_numpy(bool),
            "ICT":(ict&valid).to_numpy(bool),
            "SMC":(smc&valid).to_numpy(bool),
            "AL_BROOKS":(br&valid).to_numpy(bool)}


def compute_signals_and_regime_profiles(df:pd.DataFrame,regime:np.ndarray):
    """Four real engines run on candidate bars, each arm separately on 2024."""
    n=len(df)
    prefilter=upper_bound_candidates(df)
    individual=individual_causal_flags(df)
    evaluators={"ICHIMOKU":evaluate_ichimoku,"ICT":evaluate_ict,
                "SMC":evaluate_smc,"AL_BROOKS":evaluate_al_brooks}
    chosen={k:np.zeros(n,dtype=np.int8) for k in THRESHOLDS}
    original_target=np.zeros(n,dtype=np.int8)
    all_arms={s:[] for s in evaluators}
    cached=0
    from research_bot.regime_exit_lab import atr_wilder
    atr=atr_wilder(df)
    pct=atr/df.close
    training=(df.timestamp<CUTOFF)
    q30,q70=np.nanquantile(pct[training].to_numpy(float),[.30,.70])
    volume_reference=df.volume.shift(1).rolling(20,min_periods=20).mean()
    volume_ok=(df.volume>1.5*volume_reference).to_numpy(bool)
    atr_ok=((pct>=q30)&(pct<=q70)).fillna(False).to_numpy(bool)
    # Entire-history candidate evaluation for parameter registry; family-only
    # evaluations in 2024 are included even when no consensus existed.
    last=0
    style_counts={s:0 for s in evaluators}
    heldout=np.flatnonzero(~training.to_numpy())
    first_2024=int(heldout[0])
    for i in range(89,n):
        if not prefilter[i] and (i<first_2024 or not any(individual[s][i] for s in evaluators)):
            original_target[i]=last
            continue
        section=df.iloc[i-89:i+1]
        cutoff=section.available_at.iloc[-1].to_pydatetime()
        if prefilter[i]:
            votes=evaluate_all(section,as_of=cutoff)
            cached+=1
            initial=combine(votes,as_of=cutoff,strong_threshold=7)
            if initial.action=="BUY":last=1
            if initial.action=="SELL":last=0
            if i+2<n and volume_ok[i] and atr_ok[i]:
                for threshold in THRESHOLDS:
                    decision=combine(votes,as_of=cutoff,strong_threshold=float(threshold))
                    if decision.action=="BUY":
                        chosen[threshold][i+2]=1
        else:
            votes={}
        original_target[i]=last
        # Separate individual family events, only on heldout 2024.
        if i>=first_2024 and i+22<n and all(
            (df.timestamp.iloc[i+j]-df.timestamp.iloc[i]==pd.Timedelta(minutes=15*j))
            for j in (1,2,22)
        ):
            for style,evaluator in evaluators.items():
                if not individual[style][i]:
                    continue
                sig=votes.get(style)
                if sig is None:
                    sig=evaluator(section,as_of=cutoff)
                if sig.action=="HOLD":continue
                style_counts[style]+=1
                enter=float(df.open.iloc[i+2])
                leave=float(df.open.iloc[i+22])
                direction=1 if sig.action=="BUY" else -1
                # Diagnostic directional 20-bar trade with two-way costs;
                # bearish SELL is *hypothetical short*, NOT executable spot.
                forward_net=direction*(leave/enter-1)-.003
                all_arms[style].append((str(regime[i]),sig.action,float(forward_net)))
        if i%20000==0:
            print("EVALUATED_CANDIDATES_UP_TO",i,"of",n,"consensus_evals",cached,flush=True)
    original=np.zeros(n,dtype=np.int8)
    original[2:]=original_target[:-2]
    profiles={}
    for style,records in all_arms.items():
        profiles[style]={}
        for label in ("TREND","RANGE","VOLATILE"):
            subset=[v for reg,side,v in records if reg==label]
            if subset:
                profiles[style][label]={
                    "count":len(subset),
                    "mean_hypothetical_20bar_net":float(np.mean(subset)),
                    "win_rate_hypothetical_20bar":float(np.mean(np.asarray(subset)>0)),
                    "buy_events":sum(1 for reg,side,_ in records if reg==label and side=="BUY"),
                    "sell_events_hypothetical_short":sum(1 for reg,side,_ in records if reg==label and side=="SELL"),
                }
            else:
                profiles[style][label]={"count":0,"mean_hypothetical_20bar_net":None,
                                       "win_rate_hypothetical_20bar":None,
                                       "buy_events":0,"sell_events_hypothetical_short":0}
    return chosen,original,profiles,{
        "raw_consensus_candidate_bars":cached,
        "2024_family_event_counts":style_counts,
        "train_atr_percentile_30_of_pct":float(q30),
        "train_atr_percentile_70_of_pct":float(q70),
        "high_volume_filter":">1.5x mean prior 20 completed volumes",
        "atr_filter":"ATR(14)/price, thresholds fitted strictly 2022-23",
        "preselection_disclaimer":"Fast vector filters are necessary conditions, verify positives with actual Python strategy code",
    }


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--data",type=Path,required=True)
    parser.add_argument("--manifest",type=Path,required=True)
    parser.add_argument("--output",type=Path,default=Path("results/btcusdt_m15_regime_atr_2024.json"))
    parser.add_argument("--regimes-output",type=Path,default=Path("results/btcusdt_m15_2022_2024_regimes.parquet"))
    args=parser.parse_args()
    df,manifest=read_verified(args.data,args.manifest)
    train=(df.timestamp<CUTOFF).to_numpy(bool)
    heldout=~train
    fit=fit_hmm_2022_2023(df)
    reg=fit.causal_inference(df)
    reg["timestamp"]=df.timestamp.to_numpy()
    reg["split"]=np.where(train,"2022_2023_MODEL_FITTING_DIAGNOSTIC",
                          "2024_FORWARD_FILTERED_HOLDOUT")
    args.regimes_output.parent.mkdir(parents=True,exist_ok=True)
    reg.to_parquet(args.regimes_output,index=False)
    names=reg.regime.to_numpy(str)
    choices,old,profiles,meta=compute_signals_and_regime_profiles(df,names)
    threshold,train_metrics=optimize_on_training(df,choices,names,train)
    # Never choose based on 2024. Without sufficient training evidence,
    # default old threshold 7 for auditing and mark selection INVALID.
    selected=threshold if threshold is not None else 7
    split_idx=int(np.flatnonzero(heldout)[0])
    year=df.iloc[split_idx:].reset_index(drop=True)
    chosen=choices[selected][split_idx:]
    held_reg=names[split_idx:]
    revised,updated_trades,nav=simulate_atr_exits(year,chosen,held_reg)
    exit_only,_,exit_nav=simulate_atr_exits(year,choices[7][split_idx:],held_reg)
    # Exit-only comparison uses the original 7/12 threshold but same filters.
    # Reconstruct legacy original long/flat signal and no ATR exits on 2024.
    legacy,_=legacy_simulate(year.open.to_numpy(float),old[split_idx:])
    buyhold=np.ones(len(year),dtype=np.int8)
    buyhold[:2]=0
    buyhold_metrics,_=legacy_simulate(year.open.to_numpy(float),buyhold)
    train_returns={}
    test_returns={}
    for k in THRESHOLDS:
        _,_,tr_nav=simulate_atr_exits(df.iloc[:split_idx],choices[k][:split_idx],
                                      names[:split_idx])
        train_returns[k]=tr_nav[1:]/tr_nav[:-1]-1
        _,_,te_nav=simulate_atr_exits(year,choices[k][split_idx:],held_reg)
        test_returns[k]=te_nav[1:]/te_nav[:-1]-1
    pbo=cscv_pbo_6(train_returns)
    dsr=deflated_sharpe_2024(test_returns[selected],train_returns)
    # Strict approvals: PBO/DSR MUST be measured, OOS > BuyHold and legacy,
    # and <20% DD guard. Conservative independent gate on trade count.
    approved=bool(threshold is not None and pbo.get("valid") and dsr.get("valid")
                  and pbo["pbo"]<=.5 and dsr["dsr"]>=.95
                  and revised["net_return"]>buyhold_metrics["net_return"]
                  and revised["net_return"]>legacy["net_return"]
                  and revised["trade_count"]>=20
                  and revised["max_drawdown"]<.20)
    by_regime={
        v:{"2024_bars":int(((names==v)&heldout).sum()),
           "2024_fraction":float(((names==v)&heldout).sum()/heldout.sum())}
        for v in ("TREND","RANGE","VOLATILE","UNAVAILABLE")
    }
    report={
      "verdict":"PASS_TO_STAGE5_REVIEW_NOT_LIVE" if approved else "REJECT_NO_WALK_FORWARD_NO_RL",
      "origin":"Verified Binance Spot BTCUSDT 2022-2024 source dataset from PR #135",
      "raw_data_sha256":manifest["output_sha256"],
      "regimes_parquet_sha256":hashlib.sha256(args.regimes_output.read_bytes()).hexdigest(),
      "HMM":{"n_states":3,"trained_rows":fit.fitted_rows,
             "training_end_exclusive":"2024-01-01","seed":314,
             "state_names":fit.mapping,
             "2024_observation_method":"forward-only filtered posteriors, NO full-sequence Viterbi",
             "2022_2023_labels_are_in_sample":True,
             "2024_regime_distribution":by_regime},
      "individual_style_regime_2024":profiles,
      "style_report_warning":"Each signal is a directionally signed isolated hypothetical 20-bar event, not a deployable spot portfolio. SELL is hypothetical short; event returns overlap and are non-independent.",
      "entry_filters":meta,
      "threshold_selection":{"train_period":"2022-01-01..2023-12-31",
                             "heldout_period":"2024-01-01..2024-12-31",
                             "metric":"maximum TRAIN annualized Sharpe, min 10 trades",
                             "candidate_thresholds":list(THRESHOLDS),
                             "train_metrics":{str(k):v for k,v in train_metrics.items()},
                             "selected":selected,
                             "selection_valid":threshold is not None},
      "exit_protocol":{"atr_multiplier":2,"fixed_start_stop":"entry - 2*ATR14 from known signal candle",
                       "target_1R_fraction":.5,"target_2R_fraction":.3,
                       "trailing_remaining_fraction":.2,
                       "trailing_after_2R":"max(previous stop, last closed high - 2x closed ATR), next-bar effective",
                       "timeout":"after 20 complete bars without reaching 1R, sell at next open",
                       "collision":"STOP_FIRST including gaps at adverse open",
                       "fee_side_bps":10,"slippage_side_bps":5,
                       "per_trade_initial_equity_risk_budget":.0025,
                       "spot_notional_cap":1,
                       "execution_delay_bars":2},
      "2024_results":{"revised_filtered_exit":revised,
                      "exit_only_fixed7_same_filters":exit_only,
                      "original_legacy_no_new_filters_or_exit":legacy,
                      "buy_and_hold":buyhold_metrics,
                      "updated_trades_sample":updated_trades[:10]},
      "statistics":{"PBO_six_group_training_CSCV":pbo,
                    "DSR_2024_heldout":dsr,
                    "reject_if_PBO_gt_point5":True,
                    "reject_if_DSR_lt_point95":True,
                    "pbo_cpcv_full_event_purge_established":False},
      "stage5":{"executed":False,"reason":"Must pass 2024 OOS plus PBO and DSR gates first",
                "2000_BTCUSDT_not_available":True,
                "additional_history_needed_for_2yr_train_6mo_test":True},
      "limitations":[
          "Market regime HMM fit entirely on 2022-23 and forward-filtered 2024",
          "One-year heldout 2024 observed once: no multi-asset confirmation",
          "HMM source data includes five real unfilled March 2023 gaps",
          "No outside broker fills, no intrabar sequencing beyond STOP_FIRST conservative convention",
          "Risk-sized revised versus full-capital original baseline is not a like-for-like isolated exit-effect test",
          "CSCV PBO six-block train is diagnostic; not purged CPCV for unbounded trailing trade horizons",
          "All outcome targets depend on backtest assumptions; no live authorization"
      ]}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,allow_nan=False,indent=2,ensure_ascii=False)+"\n")
    print("HMM_2024_RESEARCH",report["verdict"],"selected_threshold",selected,
          "revised_net",revised["net_return"],"legacy_net",legacy["net_return"],
          "bh_net",buyhold_metrics["net_return"],
          "PBO",pbo.get("pbo"),"DSR",dsr.get("dsr"),flush=True)


if __name__=="__main__":
    main()
