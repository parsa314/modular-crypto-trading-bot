from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pandas as pd

from .artifacts import ImmutableArtifactStore
from .config import V59Config
from .contracts import (
    Direction,
    ModelPrediction,
    PortfolioState,
    Regime,
    SignalCandidate,
    UncertaintyAssessment,
)
from .data_plane import ingest_ohlcv
from .fixtures import (
    DeterministicFixtureProvider,
    DeterministicOrderBookProvider,
    DeterministicDerivativeMetricProvider,
    fixture_request,
)
from .execution import ExecutionAssumptions, LiquiditySnapshot, estimate_execution_cost
from .hashing import canonical_json, stable_hash
from .market_data import MarketDataRequest, normalize_and_audit
from .microstructure_plane import ingest_metrics, ingest_order_book
from .multitimeframe import build_timeframe_views
from .orchestrator import V59DecisionOrchestrator
from .registry import ComponentRegistry
from .source_registry import SourceRegistry
from .think_tank import StageFinding, prioritize


def _iso_utc(value: str) -> datetime:
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
        raise ValueError("time values must be timezone-aware UTC ISO-8601")
    return stamp.to_pydatetime()


def _integrity_demo(output: Path) -> dict:
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
        "final_decision": {**asdict(final), "status": final.status.value},
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


def _data_fixture_demo(output: Path) -> dict:
    store = ImmutableArtifactStore(output)
    request = fixture_request()
    provider = DeterministicFixtureProvider()
    bundle = ingest_ohlcv(
        provider=provider,
        request=request,
        store=store,
        run_id="fixture-ingest",
    )
    raw = provider.fetch_ohlcv(request)
    frame, quality = normalize_and_audit(raw, request)
    views, metadata = build_timeframe_views(
        frame,
        source_timeframe="1m",
        targets=("5m", "15m"),
    )
    for timeframe, view in views.items():
        store.write_bytes(
            f"fixture-ingest/multitimeframe/{timeframe}.csv",
            view.to_csv(index=False).encode("utf-8"),
        )
    source_registry = SourceRegistry().snapshot()
    store.write_json("fixture-ingest/source_registry.json", source_registry)
    stage_review = prioritize(
        (
            StageFinding(
                "REALISTIC_EXECUTION_ECONOMICS",
                "BLOCKER",
                "Market-data integrity exists; spread, slippage, impact, latency and partial-fill modeling are the next blocking realism layer.",
            ),
            StageFinding(
                "DERIVATIVES_AND_ORDER_BOOK_EVIDENCE",
                "HIGH",
                "Stage 2 generic OHLCV is spot-only; derivatives, depth and venue-specific microstructure require typed source adapters.",
            ),
            StageFinding(
                "MODEL_STRATEGY_TOURNAMENT",
                "MEDIUM",
                "Model promotion remains premature before execution-economics evidence is wired.",
            ),
        )
    )
    store.write_json("fixture-ingest/think_tank_stage2.json", stage_review)
    summary = {
        "classification": "V59_STAGE2_DATA_PLANE",
        "dataset_id": bundle.dataset_id,
        "dataset_hash": bundle.dataset_hash,
        "quality_hash": bundle.quality_hash,
        "rows": bundle.rows,
        "quality_pass": bundle.quality_pass,
        "timeframes": [asdict(row) for row in metadata],
        "source_registry_hash": source_registry["registry_hash"],
        "think_tank_next_priority": stage_review["next_priority"],
        "paper_execution": False,
        "live_execution": False,
    }
    store.write_json("fixture-ingest/stage2_summary.json", summary)
    return summary



def _execution_demo(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    decision_at = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    entry_at = decision_at + timedelta(minutes=5)
    candidate = SignalCandidate(
        event_id=stable_hash({"demo": "v59-stage3"}),
        strategy_id="FVG_ICT_TSI_MTF",
        strategy_family="FVG_ICT_TSI_MTF",
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
        regime_confidence=0.85,
        feature_snapshot_id=stable_hash({"feature": "stage3"}),
        data_version=stable_hash({"data": "stage3"}),
        strategy_version="V59_STAGE3",
        source_hash=stable_hash({"source": "stage3-fixture"}),
        confirmations=("FVG", "TSI", "STRUCTURE_BREAK"),
    )
    prediction = ModelPrediction(
        event_id=candidate.event_id,
        model_id="LOGISTIC",
        model_version="V59_STAGE3_BASELINE",
        available_at=decision_at,
        probabilities={"TP": 0.70, "SL": 0.20, "TIMEOUT": 0.10},
        calibrated=True,
        calibration_id=stable_hash({"calibration": "stage3"}),
        entropy=0.60,
        shift_score=1.0,
    )
    uncertainty = UncertaintyAssessment(
        event_id=candidate.event_id,
        prediction_set=("TP",),
        conformity_score=0.90,
        empirical_coverage=0.90,
        abstain=False,
        reason="STAGE3_FIXTURE_CONFIDENT",
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
    liquidity = LiquiditySnapshot(
        symbol="BTC/USDT",
        observed_at=decision_at,
        bid=99.99,
        ask=100.01,
        depth_notional_10bps=100_000.0,
        source="stage3-fixture-orderbook",
        source_hash=stable_hash({"book": "stage3"}),
    )
    config = V59Config()
    cost = estimate_execution_cost(
        candidate=candidate,
        portfolio=portfolio,
        liquidity=liquidity,
        config=config,
        assumptions=ExecutionAssumptions(),
    )
    engine = V59DecisionOrchestrator(config)
    final = engine.evaluate(
        candidate=candidate,
        prediction=prediction,
        uncertainty=uncertainty,
        portfolio=portfolio,
        execution_cost=cost,
    )
    engine.ledger.write_immutable(output / "execution_decision_ledger.json")
    summary = {
        "classification": "V59_STAGE3_EXECUTION_ECONOMICS",
        "cost_estimate": asdict(cost),
        "final_decision": {**asdict(final), "status": final.status.value},
        "ledger_hash": engine.ledger.ledger_hash,
        "ledger_verified": engine.ledger.verify(),
        "paper_execution": False,
        "live_execution": False,
    }
    (output / "stage3_summary.json").write_text(
        json.dumps(summary, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return summary



def _microstructure_demo(output: Path) -> dict:
    store = ImmutableArtifactStore(output)
    book_bundle = ingest_order_book(
        provider=DeterministicOrderBookProvider(),
        symbol="BTC/USDT",
        limit=50,
        store=store,
        run_id="fixture-orderbook",
    )
    metric_bundle = ingest_metrics(
        provider=DeterministicDerivativeMetricProvider(),
        symbol="BTC/USDT",
        store=store,
        run_id="fixture-derivatives",
    )
    stage_review = prioritize(
        (
            StageFinding(
                "EXECUTION_MODEL_CALIBRATION",
                "BLOCKER",
                "Prospective depth and derivative evidence now exists; execution stress parameters still require calibration against forward observations/fills.",
            ),
            StageFinding(
                "MODEL_STRATEGY_TOURNAMENT",
                "HIGH",
                "Tournament infrastructure can begin after execution-model calibration protocol is frozen.",
            ),
            StageFinding(
                "PAPER_RUNTIME",
                "MEDIUM",
                "Paper runtime remains blocked until data, execution and model evidence are all connected prospectively.",
            ),
        )
    )
    store.write_json("stage4_think_tank.json", stage_review)
    summary = {
        "classification": "V59_STAGE4_MICROSTRUCTURE_EVIDENCE",
        "order_book_snapshot_hash": book_bundle.snapshot_hash,
        "order_book_bundle_hash": book_bundle.bundle_hash,
        "metric_bundle_hash": metric_bundle["bundle_hash"],
        "metric_count": metric_bundle["metric_count"],
        "think_tank_next_priority": stage_review["next_priority"],
        "paper_execution": False,
        "live_execution": False,
    }
    store.write_json("stage4_summary.json", summary)
    return summary


def _public_ohlcv(args: argparse.Namespace) -> dict:
    from .adapters.ccxt_public import CCXTPublicOHLCVProvider

    request = MarketDataRequest(
        exchange=args.exchange,
        symbol=args.symbol,
        timeframe=args.timeframe,
        market_type="spot",
        since=_iso_utc(args.since),
        until=_iso_utc(args.until),
        as_of=_iso_utc(args.as_of),
        limit=args.limit,
    )
    store = ImmutableArtifactStore(args.output)
    provider = CCXTPublicOHLCVProvider(args.exchange)
    bundle = ingest_ohlcv(
        provider=provider,
        request=request,
        store=store,
        run_id=args.run_id,
    )
    return {
        "classification": "V59_REAL_PUBLIC_MARKET_DATA",
        "dataset_id": bundle.dataset_id,
        "dataset_hash": bundle.dataset_hash,
        "quality_hash": bundle.quality_hash,
        "rows": bundle.rows,
        "quality_pass": bundle.quality_pass,
        "paper_execution": False,
        "live_execution": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="V59 research-only trading system")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("integrity-demo", help="run deterministic V59 phase-1 decision kernel")
    demo.add_argument("--output", type=Path, required=True)

    fixture = sub.add_parser("data-fixture-demo", help="run deterministic V59 stage-2 market-data pipeline")
    fixture.add_argument("--output", type=Path, required=True)

    execution = sub.add_parser("execution-demo", help="run deterministic V59 stage-3 execution economics")
    execution.add_argument("--output", type=Path, required=True)

    micro = sub.add_parser("microstructure-demo", help="run deterministic V59 stage-4 microstructure evidence")
    micro.add_argument("--output", type=Path, required=True)

    orderbook = sub.add_parser("public-orderbook", help="capture a credential-free public order-book snapshot")
    orderbook.add_argument("--exchange", required=True)
    orderbook.add_argument("--symbol", default="BTC/USDT")
    orderbook.add_argument("--limit", type=int, default=50)
    orderbook.add_argument("--run-id", required=True)
    orderbook.add_argument("--output", type=Path, required=True)

    derivatives = sub.add_parser("coinex-prospective-metrics", help="capture prospective CoinEx futures metrics")
    derivatives.add_argument("--symbol", default="BTC/USDT")
    derivatives.add_argument("--run-id", required=True)
    derivatives.add_argument("--output", type=Path, required=True)

    public = sub.add_parser("public-ohlcv", help="fetch and freeze credential-free real public spot OHLCV")
    public.add_argument("--exchange", required=True)
    public.add_argument("--symbol", default="BTC/USDT")
    public.add_argument("--timeframe", default="1m")
    public.add_argument("--since", required=True, help="UTC ISO-8601")
    public.add_argument("--until", required=True, help="UTC ISO-8601")
    public.add_argument("--as-of", required=True, help="UTC ISO-8601")
    public.add_argument("--limit", type=int, default=10_000)
    public.add_argument("--run-id", required=True)
    public.add_argument("--output", type=Path, required=True)

    sub.add_parser("registry", help="print the V59 component registry")
    sub.add_parser("source-registry", help="print the V59 read-only market source registry")
    args = parser.parse_args()

    try:
        if args.command == "integrity-demo":
            summary = _integrity_demo(args.output)
            print(
                "V59_PHASE1_RESEARCH_KERNEL "
                f"action={summary['final_decision']['action']} "
                f"ledger={summary['ledger_hash']} "
                f"next={summary['think_tank_next_priority']}"
            )
            return 0
        if args.command == "data-fixture-demo":
            summary = _data_fixture_demo(args.output)
            print(
                "V59_STAGE2_DATA_PLANE "
                f"rows={summary['rows']} "
                f"dataset={summary['dataset_hash']} "
                f"next={summary['think_tank_next_priority']}"
            )
            return 0
        if args.command == "execution-demo":
            summary = _execution_demo(args.output)
            print(
                "V59_STAGE3_EXECUTION_ECONOMICS "
                f"cost_bps={summary['cost_estimate']['total_round_trip_bps']:.6f} "
                f"action={summary['final_decision']['action']} "
                f"ledger={summary['ledger_hash']}"
            )
            return 0
        if args.command == "microstructure-demo":
            summary = _microstructure_demo(args.output)
            print(
                "V59_STAGE4_MICROSTRUCTURE_EVIDENCE "
                f"book={summary['order_book_snapshot_hash']} "
                f"metrics={summary['metric_count']} "
                f"next={summary['think_tank_next_priority']}"
            )
            return 0
        if args.command == "public-orderbook":
            from .adapters.ccxt_orderbook import CCXTPublicOrderBookProvider
            store = ImmutableArtifactStore(args.output)
            bundle = ingest_order_book(
                provider=CCXTPublicOrderBookProvider(args.exchange),
                symbol=args.symbol,
                limit=args.limit,
                store=store,
                run_id=args.run_id,
            )
            print(
                "V59_REAL_PUBLIC_ORDERBOOK "
                f"snapshot={bundle.snapshot_hash} "
                f"bundle={bundle.bundle_hash}"
            )
            return 0
        if args.command == "coinex-prospective-metrics":
            from .adapters.coinex_prospective import CoinExProspectiveDerivativesProvider
            store = ImmutableArtifactStore(args.output)
            summary = ingest_metrics(
                provider=CoinExProspectiveDerivativesProvider(),
                symbol=args.symbol,
                store=store,
                run_id=args.run_id,
            )
            print(
                "V59_PROSPECTIVE_DERIVATIVES "
                f"metrics={summary['metric_count']} "
                f"bundle={summary['bundle_hash']}"
            )
            return 0
        if args.command == "public-ohlcv":
            summary = _public_ohlcv(args)
            print(
                "V59_REAL_PUBLIC_MARKET_DATA "
                f"rows={summary['rows']} "
                f"dataset={summary['dataset_hash']}"
            )
            return 0
        if args.command == "registry":
            print(json.dumps(ComponentRegistry().snapshot(), sort_keys=True, indent=2))
            return 0
        if args.command == "source-registry":
            print(json.dumps(SourceRegistry().snapshot(), sort_keys=True, indent=2))
            return 0
    except (ValueError, RuntimeError, OSError) as exc:
        parser.exit(2, f"error: {exc}\n")
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
