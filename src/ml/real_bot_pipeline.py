"""Executable EMA+ML experiment and the approved event stream for PPO.

References: López de Prado (2018), Advances in Financial Machine Learning,
chapters3/7. Prices are observed; no v1–v6 signals enter this pipeline.
"""
from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from purgedcv import PurgedKFold

from src.strategies import PrimarySignal
from .feature_engineer import CausalRegime, atr, rsi, validate_bars
from .meta_labeler import PRIMARY_FEATURES, PrimaryMetaLabeler, new_model, precision_top_decile

FEATURE_NAMES = PRIMARY_FEATURES
CADENCE = pd.Timedelta(minutes=15)
OUT = Path("data/real_bot")


def primary_outcome(bars, signal, end=None):
    """Next exact opening,2ATR/1ATR,20candles; pessimistic OHLC ties."""
    entry = int(signal["bar_index"]) + 1
    if entry >= len(bars) or bars.timestamp.iloc[entry] != signal["available_at"]:
        return None
    limit = pd.Timestamp(end) if end is not None else bars.available_at.iloc[-1]
    if bars.available_at.iloc[entry] > limit:
        return None
    price = float(bars.open.iloc[entry])
    width = float(signal["atr"])
    stop, target = price - width, price + 2 * width
    reason, label, ambiguous = "timeout", None, False
    exit_time = None
    for offset in range(20):
        j = entry + offset
        if j < len(bars) and bars.timestamp.iloc[j] != signal["available_at"] + offset * CADENCE:
            if bars.timestamp.iloc[j] > limit:
                raise ValueError("No observed liquidation quote before the evaluation boundary after a holding-data gap")
            # An outage cannot be sold at a preceding close retroactively.
            # Exit at the first genuinely observed later opening quote; the
            # unknown barrier ordering across the gap is not a binary label.
            reason, fill = "data_gap", float(bars.open.iloc[j])
            exit_time = bars.timestamp.iloc[j]
            break
        if j >= len(bars) or bars.available_at.iloc[j] > limit or bars.timestamp.iloc[j] != signal["available_at"] + offset * CADENCE:
            if offset == 0:
                return None
            j -= 1
            reason = "split_end" if end is not None and j + 1 < len(bars) and bars.available_at.iloc[j + 1] > limit else "censored"
            fill = float(bars.close.iloc[j])
            break
        row = bars.iloc[j]
        if row.low <= stop:
            reason, label, fill, ambiguous = "loss", 0, float(min(stop, row.open)), bool(row.high >= target)
            break
        if row.high >= target:
            reason, label, fill = "profit", 1, target
            break
        fill = float(row.close)
    exit_time = bars.available_at.iloc[j] if exit_time is None else exit_time
    return {"entry_index": entry, "exit_index": j, "entry_price": price, "exit_price": fill,
            "entry_time": bars.timestamp.iloc[entry], "exit_time": exit_time,
            "label_end": exit_time, "label": label, "label_reason": reason,
            "ambiguous": ambiguous, "gross_return": fill / price - 1,
            "net_return": (fill / price) * (1 - .0015) / (1 + .0015) - 1}


def build_primary_events(bars, daily_btc):
    bars = validate_bars(bars)
    daily_btc = validate_bars(daily_btc)
    regime = CausalRegime.fit(daily_btc, warmup_end="2019-01-01T00:00Z")
    posterior = regime.filter(daily_btc)
    names = ["regime_range", "regime_trend", "regime_volatile"]
    daily_state = pd.DataFrame({"available_at": daily_btc.available_at,
                                "regime": posterior[names].to_numpy().argmax(axis=1)})
    current = pd.merge_asof(bars[["available_at"]], daily_state, on="available_at", direction="backward")
    width = atr(bars)
    time_index = pd.DatetimeIndex(bars.timestamp)
    rolling_atr = pd.Series(width.to_numpy(), index=time_index).rolling("20D", closed="left", min_periods=96).mean()
    rolling_volume = pd.Series(bars.volume.to_numpy(), index=time_index).rolling("20D", closed="left", min_periods=96).mean()
    fast, slow = bars.close.ewm(span=20, adjust=False).mean(), bars.close.ewm(span=50, adjust=False).mean()
    macd = bars.close.ewm(span=12, adjust=False).mean() - bars.close.ewm(span=26, adjust=False).mean()
    features = pd.DataFrame({"bar_index": np.arange(len(bars)), "available_at": bars.available_at,
        "timestamp": bars.timestamp, "atr": width, "atr_pct": width / bars.close,
        "atr_ratio": width.to_numpy() / rolling_atr.to_numpy(),
        "volume_ratio": bars.volume.to_numpy() / rolling_volume.replace(0, np.nan).to_numpy(),
        "rsi": rsi(bars.close) / 100,
        "macd_hist": (macd - macd.ewm(span=9, adjust=False).mean()) / width,
        "regime": current.regime,
        "hour_of_day": bars.available_at.dt.hour + bars.available_at.dt.minute / 60,
        "signal_confidence": ((fast - slow).abs() / width).clip(0, 1)})
    features = features[PrimarySignal.raw_entries(bars)].replace([np.inf, -np.inf], np.nan).dropna(subset=FEATURE_NAMES)
    rows = []
    for event in features.to_dict("records"):
        outcome = primary_outcome(bars, event)
        if outcome is not None:
            rows.append(event | outcome)
    events = pd.DataFrame(rows).reset_index(drop=True)
    events["event_id"] = np.arange(len(events))
    events["symbol"] = "BTCUSDT"
    events["label"] = events.label.astype("Int8")
    OUT.mkdir(parents=True, exist_ok=True)
    with open("models/primary_regime_hmm.pkl", "wb") as handle:
        pickle.dump(regime, handle)
    return events, bars


def load_primary_events():
    events = pd.read_parquet(OUT / "primary_events.parquet")
    bars = pd.read_parquet("data/btc_usdt_m15_2022_2024.parquet")
    return events, validate_bars(bars)


def fit_primary_model(train_events):
    train = train_events.sort_values(["available_at", "event_id"]).reset_index(drop=True)
    cv = PurgedKFold(5, prediction_times=train.available_at, evaluation_times=train.label_end,
                     purge_horizon=5 * CADENCE, embargo=5 * CADENCE)
    oof = pd.Series(np.nan, index=train.event_id, name="ml_probability")
    folds = []
    for fold, (fit_ids, held_ids) in enumerate(cv.split(train), 1):
        labeled_fit = train.iloc[fit_ids][train.label.iloc[fit_ids].notna()]
        held = train.iloc[held_ids]
        estimator = new_model("xgboost")
        estimator.fit(labeled_fit[FEATURE_NAMES], labeled_fit.label.astype(int))
        probability = estimator.predict_proba(held[FEATURE_NAMES])[:, 1]
        oof.loc[held.event_id] = probability
        labeled_held = held.label.notna().to_numpy()
        folds.append({"fold": fold, "train_rows": len(fit_ids), "test_rows": len(held_ids),
                      **precision_top_decile(held.loc[labeled_held, "label"], probability[labeled_held])})
    threshold = float(np.quantile(oof, .9))
    estimator = new_model("xgboost")
    labeled_train = train[train.label.notna()]
    estimator.fit(labeled_train[FEATURE_NAMES], labeled_train.label.astype(int))
    return PrimaryMetaLabeler(estimator, threshold), threshold, oof, folds


def score_primary_events(model, events):
    return pd.Series(model.predict_proba(events), index=events.event_id, name="ml_probability")


def trade_stream(events, bars, probabilities, threshold, start, end):
    """Nonoverlapping admitted events, including timeouts; labels never admit trades."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    candidates = events[(events.available_at >= start) & (events.available_at < end)].copy()
    candidates["ml_probability"] = candidates.event_id.map(probabilities)
    candidates = candidates[candidates.ml_probability >= threshold].sort_values(["available_at", "event_id"])
    previous_exit, trades = start, []
    for signal in candidates.to_dict("records"):
        if signal["available_at"] < previous_exit:
            continue
        outcome = primary_outcome(bars, signal, end=end)
        if outcome is None:
            continue
        # A delisting/missing bar closes the executable trade at its last real
        # observation; its unknown barrier class never enters classifier fitting.
        row = signal | outcome
        marks = bars.iloc[row["entry_index"]:row["exit_index"] + 1]
        marks = marks[marks.available_at <= row["exit_time"]]
        row["mark_times"] = marks.available_at.to_list()
        row["mark_returns"] = (marks.close.to_numpy(float) / (row["entry_price"] * 1.0015) - 1).tolist()
        if row["mark_times"] and row["mark_times"][-1] == row["exit_time"]:
            row["mark_returns"][-1] = row["net_return"]
        else:
            row["mark_times"].append(row["exit_time"])
            row["mark_returns"].append(row["net_return"])
        trades.append(row)
        previous_exit = row["exit_time"]
    return pd.DataFrame(trades, columns=[*events.columns, "ml_probability", "mark_times", "mark_returns"])


def execute_primary_events(events, bars, probabilities, threshold, start, end):
    trades = trade_stream(events, bars, probabilities, threshold, start, end)
    nav = pd.Series(1., index=pd.date_range(start, end, freq="15min", inclusive="both"))
    capital, gains = 1., []
    for row in trades.to_dict("records"):
        quantity = capital / (row["entry_price"] * 1.0015)
        # Point-in-time intratrade marking, then actual barrier liquidation.
        nav.loc[row["entry_time"]:] = capital
        for mark_time, mark_return in zip(row["mark_times"], row["mark_returns"]):
            nav.loc[mark_time] = capital * (1 + mark_return)
        after = capital * (1 + row["net_return"])
        nav.loc[row["exit_time"]:] = after
        gains.append(after - capital)
        capital = after
    nav = nav.ffill()
    daily = nav.set_axis(nav.index - pd.Timedelta(nanoseconds=1)).resample("1D").last()
    returns = daily.pct_change(fill_method=None).dropna()
    volatility = float(returns.std(ddof=1))
    peak = np.maximum.accumulate(np.r_[1., nav.to_numpy()])
    pnl = np.asarray(gains)
    metrics = {"sharpe": float(np.sqrt(365) * returns.mean() / volatility) if volatility > 1e-14 else None,
               "return": float(capital - 1), "max_drawdown": float((1 - np.r_[1., nav.to_numpy()] / peak).max()),
               "num_trades": len(trades), "win_rate": float((pnl > 0).mean()) if len(pnl) else None,
               "profit_factor": float(pnl[pnl > 0].sum() / -pnl[pnl < 0].sum()) if (pnl < 0).any() else None,
               "timeouts": int(trades.label_reason.eq("timeout").sum()) if len(trades) else 0,
               "daily_observations": len(returns)}
    return metrics, trades, nav


def run_primary_training():
    bars = pd.read_parquet("data/btc_usdt_m15_2022_2024.parquet")
    daily = pd.read_parquet("data/ai_universe/bars/BTCUSDT.parquet")
    events, bars = build_primary_events(bars, daily)
    events.to_parquet(OUT / "primary_events.parquet", index=False)
    train_end, val_end = pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2025-01-01", tz="UTC")
    train = events[(events.available_at < train_end) & (events.label_end < train_end)]
    validation = events[(events.available_at >= train_end) & (events.label_end <= val_end)]
    model, threshold, oof, folds = fit_primary_model(train)
    pval = score_primary_events(model, validation)
    labeled_val = validation[validation.label.notna()]
    precision = precision_top_decile(labeled_val.label, pval.reindex(labeled_val.event_id))
    raw, _, raw_nav = execute_primary_events(events, bars, pd.Series(1., index=events.event_id), 0., train_end, val_end)
    filtered, val_trades, ml_nav = execute_primary_events(events, bars, pval, threshold, train_end, val_end)
    # Every held event receives a cross-fitted prediction, including timeouts;
    # only the classifier's fitted targets omit nonbinary outcomes.
    train_trades = trade_stream(train, bars, oof, threshold, pd.Timestamp("2022-01-01", tz="UTC"), train_end)
    train_trades["split"], train_trades["probability_source"] = "train", "purged_oof"
    val_trades["split"], val_trades["probability_source"] = "val", "frozen_train_model"
    approved = pd.concat([train_trades, val_trades], ignore_index=True)
    approved.to_parquet(OUT / "approved_signals.parquet", index=False)
    pd.DataFrame({"raw": raw_nav, "ml": ml_nav}).to_parquet(OUT / "ml_validation_equity.parquet")
    pd.DataFrame({"event_id": oof.index, "ml_probability": oof.to_numpy()}).to_parquet(OUT / "train_oof.parquet", index=False)
    passed = precision["precision"] > .60
    model.save("models/meta_labeler_v1.json", {"seed": 42, "train": "2022-2023", "validation": "2024",
                                             "precision_top_decile": precision["precision"], "criterion_passed": passed,
                                             "demo_paper_only": True, "source_strategy": "primary_ema20_50"})
    result = {"phase": 2, "strategy": "primary_ema20_50", "model": "XGBoost", "features": FEATURE_NAMES,
              "feature_rows": len(events), "binary_labels": int(events.label.notna().sum()),
              "train_binary_labels": int(train.label.notna().sum()), "validation_binary_labels": len(labeled_val),
              "train": "2022-2023", "validation": "2024", "purged_kfold": folds,
              "purge_bars": 5, "embargo_bars": 5, "actual_label_interval_purge": True,
              "precision_top_decile": precision["precision"], "top_decile": precision,
              "threshold": threshold, "threshold_source": "training OOF 90th percentile",
              "raw": raw, "ml_filtered": filtered,
              "approved_rl_train_trades": len(train_trades), "approved_rl_val_trades": len(val_trades),
              "rl_input": str(OUT / "approved_signals.parquet"),
              "verdict": "PASS" if passed else "FAIL", "seed": 42,
              "cost_bps_each_side": 15, "regime_calibration": "BTC daily before2019; forward-only daily posterior as-of each M15 close",
              "labeling": {"profit_atr": 2, "stop_atr": 1, "horizon_bars": 20, "bar_minutes": 15,
                           "timeout": "excluded from binary labels, retained in frozen-model validation execution",
                           "double_touch": "loss", "entry": "next exact observed open"},
              "source_sha256": hashlib.sha256(Path("data/btc_usdt_m15_2022_2024.parquet").read_bytes()).hexdigest(),
              "model_sha256": hashlib.sha256(Path("models/meta_labeler_v1.json").read_bytes()).hexdigest(),
              "approved_signals_sha256": hashlib.sha256((OUT / "approved_signals.parquet").read_bytes()).hexdigest(),
              "limitations": ["Single BTC M15 experiment; no Forex performance claim",
                              "2024 was previously inspected; this is a retrospective engineering experiment",
                              "RL training sees cross-fitted ML-approved events including timeouts; classifier fitting uses binary outcomes only",
                              "Meta-model criterion failure is reported but does not cancel user-requested PPO training"]}
    Path("results/ml_filtered_backtest.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps(result, allow_nan=False), flush=True)
    return result
