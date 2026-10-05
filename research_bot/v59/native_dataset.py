"""Outcome-only transformation of causal candidates into audited event labels.

Labels and realized returns never enter feature snapshots or expected utility.
This is an event dataset, not a capital-account portfolio backtest. Overlapping
events cannot be compounded as a claim of investable portfolio performance.
"""
from dataclasses import asdict

import numpy as np
import pandas as pd

from .hashing import stable_hash
from .market_data import timeframe_delta
from .native_features import add_native_features
from .native_strategy import MODEL_FEATURES, NativeStrategyConfig, code_hash, scan_native


def build_native_events(frame: pd.DataFrame, *, venue: str, symbol: str, data_version: str,
                        as_of, config: NativeStrategyConfig | None = None) -> tuple[pd.DataFrame, dict]:
    cfg = config or NativeStrategyConfig()
    candidates, snapshots = scan_native(frame, venue=venue, symbol=symbol, data_version=data_version,
                                         as_of=as_of, config=cfg)
    delta = timeframe_delta(cfg.timeframe)
    bars = frame.copy()
    bars.timestamp = pd.to_datetime(bars.timestamp, utc=True)
    bars = bars.loc[bars.timestamp + delta <= pd.Timestamp(as_of)].reset_index(drop=True)
    # Audit every outcome input too: future path need not be causal but must be valid.
    add_native_features(bars, timeframe=cfg.timeframe)
    indexes = {t: i for i, t in enumerate(bars.timestamp)}
    records, exclusions = [], []
    for candidate in candidates:
        entry = indexes.get(pd.Timestamp(candidate.entry_time))
        if entry is None:
            exclusions.append({"event_id": candidate.event_id, "reason": "NO_OBSERVED_ENTRY"})
            continue
        reference = candidate.entry_price
        entry_price = float(bars.open.iloc[entry])
        if not candidate.stop_price < entry_price < candidate.target_price:
            exclusions.append({"event_id": candidate.event_id, "reason": "ENTRY_OUTSIDE_BARRIERS"})
            continue
        outcome, exit_price, exit_index = None, None, None
        available = min(len(bars), entry + candidate.horizon_bars)
        for j in range(entry, available):
            bar = bars.iloc[j]
            # STOP_FIRST makes same-bar TP/SL ambiguity conservative.
            if bar.open <= candidate.stop_price:
                outcome, exit_price, exit_index = "SL", float(bar.open), j
                break
            if bar.open >= candidate.target_price:
                outcome, exit_price, exit_index = "TP", candidate.target_price, j
                break
            if bar.low <= candidate.stop_price:
                outcome, exit_price, exit_index = "SL", candidate.stop_price, j
                break
            if bar.high >= candidate.target_price:
                outcome, exit_price, exit_index = "TP", candidate.target_price, j
                break
        if outcome is None:
            if entry + candidate.horizon_bars > len(bars):
                exclusions.append({"event_id": candidate.event_id, "reason": "RIGHT_CENSORED"})
                continue
            exit_index = entry + candidate.horizon_bars - 1
            outcome, exit_price = "TIMEOUT", float(bars.close.iloc[exit_index])
        label_end = bars.timestamp.iloc[exit_index] + delta
        snapshot = snapshots[candidate.feature_snapshot_id]
        loss = (reference - candidate.stop_price) / reference
        reward = (candidate.target_price - reference) / reference
        record = {"event_id": candidate.event_id, "timestamp": pd.Timestamp(candidate.decision_at),
                  "decision_at": pd.Timestamp(candidate.decision_at), "entry_time": pd.Timestamp(candidate.entry_time),
                  "information_start": pd.Timestamp(candidate.decision_at), "information_end": label_end,
                  "feature_available_at": pd.Timestamp(snapshot["available_at"]),
                  "strategy_id": candidate.strategy_id, "strategy_arm": candidate.strategy_id,
                  "venue": venue, "symbol": symbol, "regime": candidate.regime.value,
                  "label": outcome, "reward_fraction": reward, "loss_fraction": loss,
                  # Conservative ex-ante assumption, NOT realized timeout loss.
                  "timeout_loss_fraction": loss, "realized_gross_return": exit_price / entry_price - 1,
                  "entry_fill_reference": entry_price, "exit_fill_reference": exit_price,
                  "reference_entry_is_fill": False, "data_hash": candidate.source_hash,
                  "dataset_version": data_version, "code_hash": snapshot["code_hash"],
                  "feature_snapshot_id": candidate.feature_snapshot_id,
                  **snapshot["features"]}
        records.append(record)
    events = pd.DataFrame(records)
    hashes = [{**row, **{k: pd.Timestamp(row[k]).isoformat() for k in
                        ("timestamp", "decision_at", "entry_time", "information_start", "information_end", "feature_available_at")}}
              for row in records]
    report = {"classification": "NATIVE_EVENT_DATASET_NOT_PORTFOLIO", "config": asdict(cfg),
              "rows": len(events), "candidate_count": len(candidates), "excluded": exclusions,
              "dataset_hash": stable_hash(hashes), "code_hash": code_hash(),
              "feature_columns": list(MODEL_FEATURES), "execution_authorized": False,
              "regime_inference": "UNAVAILABLE_DEFAULT_FINANCE_GATE_WILL_VETO",
              "economic_portfolio_metrics": None}
    return events, report
