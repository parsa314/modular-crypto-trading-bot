from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from .config import V59Config
from .contracts import (
    Direction,
    ModelPrediction,
    PortfolioState,
    Regime,
    SignalCandidate,
    UncertaintyAssessment,
)
from .hashing import canonical_json, stable_hash
from .orchestrator import V59DecisionOrchestrator
from .registry import ComponentRegistry
from .think_tank import StageFinding, prioritize


def _demo(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    decision_at = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    entry_at = decision_at + timedelta(minutes=5)
    candidate = SignalCandidate(
        event_id=stable_hash({"demo": "v59-phase1"}),
        strategy_id="C10_03_CLOUD_BREAK_FVG_CONTINUATION",
        strategy_family="CONFLUENCE10",
        symbol="BTC/USDT",
        venue="synthetic",
        direction=Direction.LONG,
        decision_at=decision_at,
        entry_time=entry_at,
        entry_price=100.0,
        stop_price=99.0,
        target_price=102.0,
        horizon_bars=12,
        regime=Regime.TREND_UP,
        regime_confidence=0.80,
        feature_snapshot_id=stable_hash({"feature": 1}),
        data_version=stable_hash({"data": 1}),
        strategy_version="V59_PHASE1",
        source_hash=stable_hash({"source": "synthetic-demo"}),
        confirmations=("ICHIMOKU", "ICT", "SMC", "AL_BROOKS"),
    )
    prediction = ModelPrediction(
        event_id=candidate.event_id,
        model_id="LOGISTIC",
        model_version="V59_BASELINE_1",
        available_at=decision_at,
        probabilities={"TP": 0.70, "SL": 0.20, "TIMEOUT": 0.10},
        calibrated=True,
        calibration_id=stable_hash({"calibration": "demo"}),
        entropy=0.60,
        shift_score=1.0,
    )
    uncertainty = UncertaintyAssessment(
        event_id=candidate.event_id,
        prediction_set=("TP",),
        conformity_score=0.90,
        empirical_coverage=0.90,
        abstain=False,
        reason="DEMO_CONFIDENT",
    )
    portfolio = PortfolioState(
        timestamp=entry_at,
        equity=10_000.0,
        cash=10_000.0,
        peak_equity=10_000.0,
        gross_exposure=0.0,
        asset_exposure={},
        recent_returns=(),
    )
    engine = V59DecisionOrchestrator(V59Config())
    final = engine.evaluate(
        candidate=candidate,
        prediction=prediction,
        uncertainty=uncertainty,
        portfolio=portfolio,
    )
    engine.ledger.write_immutable(output / "decision_ledger.json")
    registry = ComponentRegistry().snapshot()
    (output / "component_registry.json").write_bytes(canonical_json(registry) + b"\n")
    think_tank = prioritize(
        (
            StageFinding(
                "REAL_MARKET_DATA_PIPELINE",
                "HIGH",
                "Phase 1 kernel is integrity-focused; market adapters remain to be rebuilt under point-in-time contracts.",
            ),
            StageFinding(
                "REALISTIC_EXECUTION",
                "HIGH",
                "Spread, impact, latency and partial-fill evidence are not yet wired into V59.",
            ),
            StageFinding(
                "DEEP_MODEL_TOURNAMENT",
                "MEDIUM",
                "Deep/foundation models remain candidates until simple baselines and data integrity pass.",
            ),
        )
    )
    (output / "think_tank_phase1.json").write_bytes(canonical_json(think_tank) + b"\n")
    summary = {
        "classification": "V59_PHASE1_RESEARCH_KERNEL",
        "final_decision": asdict(final),
        "ledger_verified": engine.ledger.verify(),
        "ledger_hash": engine.ledger.ledger_hash,
        "registry_hash": registry["registry_hash"],
        "think_tank_decision": think_tank["decision"],
        "think_tank_next_priority": think_tank["next_priority"],
        "paper_execution": False,
        "live_execution": False,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="V59 research-only kernel")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("integrity-demo", help="run deterministic V59 phase-1 decision kernel")
    demo.add_argument("--output", type=Path, required=True)
    sub.add_parser("registry", help="print the V59 component registry")
    args = parser.parse_args()

    if args.command == "integrity-demo":
        summary = _demo(args.output)
        print(
            "V59_PHASE1_RESEARCH_KERNEL "
            f"action={summary['final_decision']['action']} "
            f"ledger={summary['ledger_hash']} "
            f"next={summary['think_tank_next_priority']}"
        )
        return 0
    if args.command == "registry":
        print(json.dumps(ComponentRegistry().snapshot(), sort_keys=True, indent=2))
        return 0
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
