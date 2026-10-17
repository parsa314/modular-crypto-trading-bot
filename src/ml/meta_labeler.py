"""Purged expanding-window XGBoost meta-labeling and causal event-book comparison.

Only validation 2023 is evaluated; 2024+ must never be fetched in phase 2.
"""
from __future__ import annotations
import math
from pathlib import Path
import numpy as np
import pandas as pd

from .data_loader import TRAIN_END,VALID_END
from .feature_engineer import FEATS

COST_SIDE=.0015
CATEGORIES=["strategy_id","vol_regime"]
def research_metrics(e):
    if len(e)==0:
        return {"n":0,"win_rate":None,"mean_net_per_event":None}
    return {"n":len(e),"win_rate":float(e.label.mean()),
            "mean_net_per_event":float(e.net_pnl.mean())}

def purged_folds(train):
    """Five expanding training-only splits. Events ending in purge/embargo excluded."""
    times=pd.to_datetime(train.signal_time,utc=True)
    if len(times)==0:return
    boundaries=pd.date_range(times.min(),times.max()+pd.Timedelta(days=1),periods=7)
    ends=pd.to_datetime(train.event_end,utc=True)
    for i in range(1,6):
        a,b=boundaries[i],boundaries[i+1]
        cutoff=a-pd.Timedelta(days=5)
        fit=np.flatnonzero((times<cutoff)&(ends<cutoff))
        validation=np.flatnonzero((times>=a)&(times<b))
        if len(fit)>=100 and len(validation)>=30:
            yield fit,validation

def _sharpe(nav):
    r=np.diff(np.array(nav,float))/np.array(nav[:-1],float)
    mu=float(r.mean())
    sd=float(r.std(ddof=1))
    downside=float(np.sqrt(np.mean(np.minimum(r,0)**2)))
    peak=np.maximum.accumulate(nav)
    return {
        "sharpe":float(np.sqrt(365)*mu/sd) if sd>1e-15 else None,
        "sortino":float(np.sqrt(365)*mu/downside) if downside>1e-15 else None,
        "maxdd":float(np.max(1-np.array(nav)/peak)),
        "net_return":float(nav[-1]-1),
        "n_days":len(r)
    }

def portfolio_backtest(events,market):
    """Mark every active long event to daily close, pay 15bp at entry and exit.

    Each event risks a notional 1% of prior NAV, max 10% asset, max 70% gross.
    Events can start and finish same day; exit uses their STOP_FIRST prices.
    Single fixed UTC-day bookkeeping; M15 fills use their actual event entry.
    """
    calendar=pd.date_range(TRAIN_END,VALID_END,freq="D",inclusive="left",tz="UTC")
    grouped={t:[] for t in calendar}
    e=events.sort_values(["entry_time","symbol","strategy_id"])
    for rec in e.itertuples(index=False):
        day=pd.Timestamp(rec.entry_time).floor("D")
        if day in grouped:
            grouped[day].append(rec)
    cash=1.
    opened=[];navs=[1.];n_buys=n_sells=0
    skipped=0
    for date in calendar:
        marks={}
        for s in set([x["symbol"] for x in opened])|set(str(e.symbol) for e in grouped[date]):
            if s not in market or date not in market[s].index:
                raise RuntimeError(f"DATA_GAP_DURING_EVENT_ACCOUNTING {s} {date}")
            marks[s]=float(market[s].loc[date,"close"])
        price_value=lambda:cash+sum(x["quantity"]*marks[x["symbol"]] for x in opened)
        nav_pre=price_value()
        for rec in grouped[date]:
            symbol=str(rec.symbol)
            current=sum(x["quantity"]*marks[symbol] for x in opened if x["symbol"]==symbol)
            gross=sum(x["quantity"]*marks[x["symbol"]] for x in opened)
            cash_to_use=min(.01*nav_pre,cash,
                            max(0,.10*nav_pre-current),
                            max(0,.70*nav_pre-gross))
            if cash_to_use<1e-8:
                skipped+=1
                continue
            entry=float(rec.entry_price)
            if entry<=0:raise RuntimeError("BAD_EVENT_ENTRY")
            qty=cash_to_use/(entry*(1+COST_SIDE))
            cash-=cash_to_use
            opened.append({"symbol":symbol,"quantity":qty,
                           "exit_day":pd.Timestamp(rec.event_end).floor("D"),
                           "exit_price":float(rec.exit_price)})
            n_buys+=1
        # All entries precede same-day exits. STOP_FIRST handling is in events.
        remaining=[]
        for x in opened:
            if x["exit_day"]<=date:
                cash+=x["quantity"]*x["exit_price"]*(1-COST_SIDE)
                n_sells+=1
            else:remaining.append(x)
        opened=remaining
        nav=price_value()
        if not math.isfinite(nav) or nav<=0:
            raise RuntimeError("INVALID_SIMULATED_NAV")
        navs.append(nav)
    if opened:raise RuntimeError("EVENT_EXTENDS_PAST_VALIDATION_BOUNDARY")
    return {**_sharpe(navs),"entry_fills":n_buys,
            "exit_fills":n_sells,"skipped_risk_cap":skipped,
            "cost_per_side_bps":15,
            "accounting":"1% NAV/event, <=10%/symbol, <=70% gross, mark-to-market daily; research only"}

def ml_experiment(events,market=None,model_dir="models"):
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import OneHotEncoder
    from sklearn.metrics import (precision_score,recall_score,f1_score,
                                 roc_auc_score,average_precision_score)
    from xgboost import XGBClassifier
    import joblib
    data=events.sort_values(["signal_time","symbol","strategy_id"]).reset_index(drop=True)
    t=pd.to_datetime(data.signal_time,utc=True)
    ending=pd.to_datetime(data.event_end,utc=True)
    cutoff_train=pd.Timestamp(TRAIN_END,tz="UTC")
    cutoff_val=pd.Timestamp(VALID_END,tz="UTC")
    train=data[(t<cutoff_train)&(ending<cutoff_train)].copy()
    valid=data[(t>=cutoff_train)&(t<cutoff_val)&(ending<cutoff_val)].copy()
    test=data[t>=cutoff_val] # MUST be empty, no 2024+ data touched
    if len(test):
        raise RuntimeError("TEST_2024_PLUS_FORBIDDEN")
    counts={"total":len(data),"train":len(train),"validation":len(valid),
            "test_unread":0,
            "by_strategy":{str(k):int(v) for k,v in data.strategy_id.value_counts().items()},
            "class_train":{str(k):int(v) for k,v in train.label.value_counts().items()},
            "class_validation":{str(k):int(v) for k,v in valid.label.value_counts().items()}}
    if len(train)<100 or len(valid)<50 or train.label.nunique()<2 or valid.label.nunique()<2:
        return counts,{"status":"INSUFFICIENT_SAMPLES_FOR_NUMERIC_MODEL",
                       "precision_top_decile":None,
                       "validation_sharpe_raw":None,
                       "validation_sharpe_filtered":None}
    def make_model(n_estimators=200):
        return XGBClassifier(n_estimators=n_estimators,max_depth=3,
                learning_rate=.04,subsample=.8,colsample_bytree=.8,
                reg_lambda=10.,objective="binary:logistic",tree_method="hist",
                n_jobs=2,random_state=126,eval_metric="logloss",
                early_stopping_rounds=25)
    xcols=FEATS+CATEGORIES
    cv=[]
    for ai,bi in purged_folds(train):
        a,b=train.iloc[ai],train.iloc[bi]
        if a.label.nunique()<2 or b.label.nunique()<2:continue
        pre=ColumnTransformer([
           ("numeric",SimpleImputer(strategy="median",keep_empty_features=True),FEATS),
           ("category",OneHotEncoder(handle_unknown="ignore"),CATEGORIES)])
        xa=pre.fit_transform(a[xcols]);xb=pre.transform(b[xcols])
        model=make_model()
        model.fit(xa,a.label,eval_set=[(xb,b.label)],verbose=False)
        score=model.predict_proba(xb)[:,1]
        top=np.argsort(score)[-max(1,math.ceil(.1*len(score))):]
        cv.append({"n_train":len(a),"n_oof":len(b),
                   "precision_top_decile":float(b.label.to_numpy()[top].mean()),
                   "best_iteration":int(model.best_iteration)})
    pre=ColumnTransformer([
        ("numeric",SimpleImputer(strategy="median",keep_empty_features=True),FEATS),
        ("category",OneHotEncoder(handle_unknown="ignore"),CATEGORIES)])
    x_train=pre.fit_transform(train[xcols])
    x_val=pre.transform(valid[xcols])
    model=make_model()
    model.fit(x_train,train.label,eval_set=[(x_val,valid.label)],verbose=False)
    probabilities=model.predict_proba(x_val)[:,1]
    # EXACT top decile. Quantile threshold alone would admit huge tie blocks
    # (e.g. saturated XGBoost scores) and corrupt the filtered comparison.
    n_top=max(1,math.ceil(.1*len(probabilities)))
    top=np.argsort(-probabilities,kind="stable")[:n_top]
    selected=np.zeros(len(probabilities),dtype=bool)
    selected[top]=True
    threshold=float(probabilities[top[-1]])
    assert selected.sum()==n_top
    labels=valid.label.to_numpy()
    top_precision=float(labels[top].mean())
    modelpath=Path(model_dir)
    modelpath.mkdir(parents=True,exist_ok=True)
    model.save_model(str(modelpath/"meta_labeler_v1.json"))
    joblib.dump(pre,modelpath/"meta_labeler_v1_encoder.joblib")
    raw=research_metrics(valid)
    filtered=research_metrics(valid.loc[selected])
    report={"status":"TRAINED_EXPLORATORY_2023_ONLY",
      "test_2024_2026":"NOT_OPENED_BY_THIS_EXPERIMENT",
      "n_train":len(train),"n_validation":len(valid),
      "train_positive_fraction":float(train.label.mean()),
      "training_purged_cv":cv,
      "validation_best_iteration":int(model.best_iteration),
      "validation_top_decile_threshold":threshold,
      "validation_precision_top_decile":top_precision,
      "validation_precision":float(precision_score(labels,selected,zero_division=0)),
      "validation_recall":float(recall_score(labels,selected,zero_division=0)),
      "validation_f1":float(f1_score(labels,selected,zero_division=0)),
      "validation_auc":float(roc_auc_score(labels,probabilities)),
      "validation_average_precision":float(average_precision_score(labels,probabilities)),
      "validation_raw":raw,"validation_filtered":filtered,
      "meets_precision_0_60":top_precision>.60,
      "disclosure":"2023 validation labels used in XGBoost early stopping; precision is model-selection validation, NOT a novel independent OOS estimate."}
    if market is not None:
        full=portfolio_backtest(valid,market)
        filt=portfolio_backtest(valid.loc[selected],market)
        report["validation_portfolio_raw"]=full
        report["validation_portfolio_filtered"]=filt
        s1,s2=full["sharpe"],filt["sharpe"]
        report["validation_sharpe_delta"]=(s2-s1 if s1 is not None and s2 is not None else None)
        report["meets_delta_sharpe_0_3"]=(report["validation_sharpe_delta"] is not None
                                             and report["validation_sharpe_delta"]>=.3)
    return counts,report
