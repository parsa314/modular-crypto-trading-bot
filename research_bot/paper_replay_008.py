"""Offline, cash-only integration probe for historical hourly market data.

Research prototype: no broker clients, no model training, and no orders.  Four
existing exploratory strategy arms are evaluated, but their votes are not
invented probabilities or forecasts. Missing ML evidence therefore selects a
cash baseline. This is not a complete paper OMS or prospective observation.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3

import pandas as pd

from .decision import DecisionEngine
from .risk import RiskEngine, RiskLimits, RiskSnapshot
from .strategies import al_brooks_strategy, ichimoku_strategy, ict_strategy, smc_strategy
from .strategies.confluence import combine

STRATEGY_SOURCE_SHA = "ae65f99e092aa0f631675f02626003438d2f5dfe"
BASELINE_VERSION = "CASH_BASELINE_V1"


def replay(csv_path: Path, state_path: Path, *, as_of: datetime, source: str,
           fee_bps: Decimal, spread_bps: Decimal, slippage_bps: Decimal,
           initial_cash: Decimal = Decimal("10000")) -> dict:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("AS_OF_MUST_BE_TIMEZONE_AWARE")
    if not source.strip():
        raise ValueError("SOURCE_REQUIRED")
    if not all(x.is_finite() and x >= 0 for x in (fee_bps, spread_bps, slippage_bps)):
        raise ValueError("INVALID_COST_ASSUMPTION")
    if not initial_cash.is_finite() or initial_cash <= 0:
        raise ValueError("INVALID_INITIAL_CASH")
    raw = csv_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    frame = pd.read_csv(csv_path)
    if "timestamp" not in frame:
        raise ValueError("TIMESTAMP_REQUIRED")
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="raise", format="ISO8601")
    if frame["timestamp"].isna().any() or frame["timestamp"].duplicated().any() or not frame["timestamp"].is_monotonic_increasing:
        raise ValueError("DUPLICATE_OR_UNORDERED_BARS")
    if not frame["timestamp"].dt.floor("h").equals(frame["timestamp"]):
        raise ValueError("HOUR_ALIGNMENT_REQUIRED")
    frame["available_at"] = frame["timestamp"] + pd.Timedelta(hours=1)
    # Strictly discard every bar not yet closed at the requested cutoff.
    closed = frame.loc[frame["available_at"] <= pd.Timestamp(as_of)].copy()
    if len(closed) < 90:
        raise ValueError("INSUFFICIENT_CLOSED_BARS")
    if not closed["timestamp"].diff().dropna().eq(pd.Timedelta(hours=1)).all():
        raise ValueError("HOURLY_COVERAGE_GAP")
    arms = {"ICHIMOKU": ichimoku_strategy.evaluate(closed, as_of=as_of),
            "ICT": ict_strategy.evaluate(closed, as_of=as_of),
            "SMC": smc_strategy.evaluate(closed, as_of=as_of),
            "AL_BROOKS": al_brooks_strategy.evaluate(closed, as_of=as_of)}
    aggregate = combine(arms, as_of=as_of)
    # Cash is a non-predictive baseline, not an ML-derived estimate.
    decision_time = closed["available_at"].iloc[-1].to_pydatetime()
    decision = DecisionEngine().evaluate(asset="BTC", timestamp=decision_time,
        expected_return=0.0, expected_cost=float((fee_bps + spread_bps + slippage_bps) / Decimal("10000")),
        risk_penalty=0.0, uncertainty_penalty=0.0, confidence=0.0,
        metadata={"baseline": BASELINE_VERSION, "model_available": False})
    risk = RiskEngine(RiskLimits(max_drawdown=0.05, max_gross_exposure=0.70)).evaluate(
        RiskSnapshot(equity=float(initial_cash), peak_equity=float(initial_cash), gross_exposure=0.0,
                     asset_weight=0.0, turnover=0.0, spread_bps=float(spread_bps),
                     slippage_bps=float(slippage_bps)))
    event = {"execution_mode": "HISTORICAL_REPLAY_CASH_BASELINE", "evidence_state": "HISTORICAL_NON_PROSPECTIVE",
             "source": source, "data_sha256": digest, "symbol": "BTC/USDT", "timeframe": "1h",
             "first_seen_at": None, "availability_assumption": "historical candle close; first-seen not evidenced",
             "as_of": as_of.astimezone(timezone.utc).isoformat(), "closed_rows": len(closed),
             "last_closed_open": closed["timestamp"].iloc[-1].isoformat(),
             "strategy_source_sha": STRATEGY_SOURCE_SHA, "signals": {k: asdict(v) for k,v in arms.items()},
             "confluence": asdict(aggregate), "baseline": BASELINE_VERSION, "model_available": False,
             "decision": decision.decision.value, "risk_approved": risk.approved,
             "risk_reasons": list(risk.reasons), "cash": str(initial_cash), "position": "0",
             "fees": "0", "fills": 0,
             "cost_assumptions_bps_per_side": {"fee": str(fee_bps), "spread": str(spread_bps), "slippage": str(slippage_bps)},
             "limitations": ["cash-only baseline; no paper OMS or order simulation", "historical replay is not prospective", "four strategy arms are research prototypes"]}
    encoded = json.dumps(event, default=lambda x: x.isoformat() if isinstance(x, datetime) else str(x),
                         sort_keys=True, separators=(",", ":"), allow_nan=False)
    run_id = hashlib.sha256(encoded.encode()).hexdigest()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(state_path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS replay_evidence (run_id TEXT PRIMARY KEY, result TEXT NOT NULL)")
        inserted = db.execute("INSERT OR IGNORE INTO replay_evidence VALUES (?, ?)", (run_id, encoded)).rowcount == 1
    return {**json.loads(encoded), "run_id": run_id, "new_record": inserted}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--as-of", required=True, help="timezone-aware ISO timestamp")
    parser.add_argument("--source", required=True)
    parser.add_argument("--fee-bps", required=True, type=Decimal)
    parser.add_argument("--spread-bps", required=True, type=Decimal)
    parser.add_argument("--slippage-bps", required=True, type=Decimal)
    args = parser.parse_args()
    print(json.dumps(replay(args.csv, args.state, as_of=datetime.fromisoformat(args.as_of),
        source=args.source, fee_bps=args.fee_bps, spread_bps=args.spread_bps,
        slippage_bps=args.slippage_bps), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
