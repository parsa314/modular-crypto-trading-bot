"""Public canonical-contract checks; network and orders never needed."""

import json
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from research_bot.contracts import (
    Decision,
    ExecutionMode,
    ResearchStatus,
    SignalEvidence,
)
from research_bot.course.bitpin import BitpinReadOnlyAdapter
from research_bot.course.contracts import (
    BookSnapshot,
    MarketMetadata,
    UnsupportedCapability,
)
from research_bot.course.hypotheses import (
    GridInventoryStateMachine,
    MovingAverageCrossoverHypothesis,
    RSI_MA_ConfluenceHypothesis,
    candidates_from_signals,
    course_experiment_manifest,
    course_hypothesis_registry,
)
from research_bot.course.simulation import OfflineOrderSimulator
from research_bot.course.stateful import GridConfig, GridLevel, GridStrategyStateMachine
from research_bot.exchanges.bitpin_adapter import BitpinResearchGateway
from research_bot.execution import NoOrderTransport, PaperExecutionEngine
from research_bot.orchestrator import ResearchTradingOrchestrator
from research_bot.risk import RiskSnapshot
from research_bot.trial_registry_v17 import load_trials

NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)


def candles():
    times = pd.date_range(end=NOW, periods=180, freq="4h")
    price = np.linspace(100, 120, 180)
    return pd.DataFrame(
        {
            "close_time": times,
            "available_at": times,
            "open": price,
            "high": price + 1,
            "low": price - 1,
            "close": price,
            "volume": 10,
        }
    )


def grid_fixture():
    market = MarketMetadata(
        "FIXTURE",
        "BTC/USDT",
        "BTC",
        "USDT",
        Decimal(".01"),
        Decimal(".01"),
        Decimal(".01"),
        "USDT",
    )
    book = BookSnapshot(
        market,
        NOW,
        NOW,
        NOW,
        NOW,
        "1",
        ((Decimal(100), Decimal(1)),),
        ((Decimal(101), Decimal(1)),),
        "fixture",
    )
    sim = OfflineOrderSimulator({"USDT": Decimal(100), "BTC": Decimal(".2")})
    machine = GridStrategyStateMachine(
        sim, GridConfig(range_preregistration_ref="FIXTURE_PROTOCOL")
    )
    machine.prepare(
        book,
        (GridLevel("BUY", Decimal(".1"), Decimal(95)),),
        NOW,
        range_confirmed=True,
        range_evidence_ref="FIXTURE_RANGE",
        range_evidence_available_at=NOW,
    )
    return machine, book


def generate(generator, frame=None):
    return generator.generate(
        candles() if frame is None else frame,
        symbol="BTC/USDT",
        venue="FIXTURE",
        decision_time=NOW,
    )


@pytest.mark.parametrize(
    "kind", [MovingAverageCrossoverHypothesis, RSI_MA_ConfluenceHypothesis]
)
def test_rules_emit_existing_contract_with_immutable_metadata_and_unknown_edge(kind):
    frame = candles()
    original = frame.copy(deep=True)
    signal = generate(kind(enabled=True), frame)[0]
    assert type(signal) is SignalEvidence and signal.decision is Decision.NO_TRADE
    assert signal.confidence == 0 and signal.expected_return == 0
    assert signal.evidence[0].status is ResearchStatus.HYPOTHESIS
    assert signal.evidence[0].available_at <= signal.timestamp
    assert signal.metadata["earliest_execution_time"] > signal.timestamp
    with pytest.raises(TypeError):
        signal.metadata["capital_requirements"] = ()
    with pytest.raises(TypeError):
        signal.metadata["course_candidate"]["confidence"] = 1
    pd.testing.assert_frame_equal(frame, original)
    assert candidates_from_signals([signal])[0].event_id == signal.metadata["event_id"]


@pytest.mark.parametrize(
    "kind", [MovingAverageCrossoverHypothesis, RSI_MA_ConfluenceHypothesis]
)
def test_disabled_rules_and_future_data_boundary(kind):
    assert generate(kind()) == []
    future = candles()
    future.loc[len(future) - 1, "available_at"] = NOW + timedelta(seconds=1)
    with pytest.raises(ValueError, match="POINT_IN_TIME"):
        generate(kind(enabled=True), future)


def test_grid_is_a_pure_view_of_existing_inventory_machine():
    machine, book = grid_fixture()
    original = machine.export_state()
    assert generate(GridInventoryStateMachine(machine), book) == []
    signal = generate(GridInventoryStateMachine(machine, enabled=True), book)[0]
    assert signal.metadata["capital_requirements"]
    assert signal.metadata["invalidation_conditions"] == (
        "BREAKOUT",
        "REGIME_CHANGE",
        "INVENTORY_LIMIT",
    )
    assert signal.decision is Decision.NO_TRADE
    assert machine.export_state() == original
    assert generate(GridInventoryStateMachine(machine, enabled=True), book)[0] == signal


def test_grid_rejects_snapshot_substitution_and_active_plan_without_side_effects():
    machine, book = grid_fixture()
    before = machine.export_state()
    with pytest.raises(ValueError, match="SNAPSHOT_MISMATCH"):
        generate(
            GridInventoryStateMachine(machine, enabled=True),
            replace(book, sequence_id="2"),
        )
    assert machine.export_state() == before
    machine.reserve(book, NOW, risk_approved=True)
    with pytest.raises(ValueError, match="GRID_READY"):
        generate(GridInventoryStateMachine(machine, enabled=True), book)


def test_bitpin_canonical_location_reuses_client_and_has_no_constructor_network_or_orders(
    monkeypatch,
):
    calls = []

    def transport(url, timeout):
        calls.append(url)
        raise AssertionError("network must not run")

    monkeypatch.setenv("BITPIN_TOKEN", "unused-fixture")
    data = BitpinReadOnlyAdapter(transport=transport)
    gateway = BitpinResearchGateway(data, OfflineOrderSimulator({"USDT": Decimal(100)}))
    assert (
        gateway.data is data
        and gateway.execution_authorization == "DENIED_RESEARCH_ONLY"
    )
    assert isinstance(gateway.execution, NoOrderTransport)
    with pytest.raises(RuntimeError, match="RESEARCH_ONLY"):
        gateway.execution.execute(object())
    with pytest.raises(UnsupportedCapability):
        gateway.data.require_capability("authenticate")
    with pytest.raises(TypeError):
        BitpinResearchGateway(data, execution=PaperExecutionEngine())
    assert calls == []


def test_orchestrator_signals_reuse_existing_risk_registry_and_no_order(tmp_path):
    machine, book = grid_fixture()
    registry = course_hypothesis_registry(grid=machine, enabled=True)
    signals = [
        s
        for name, g in registry.items()
        for s in generate(g, book if name == "grid" else None)
    ]
    assert len(signals) == 3
    core = ResearchTradingOrchestrator()
    records = core.process_research_signals(
        signals,
        decision_time=NOW,
        risk_snapshot=RiskSnapshot(10000, 10000, 0, 0, 0, 2, 2, (0.001,) * 30),
        run_id="fixture",
        git_commit="fa31e6f5ceb09f4f14ae13fef50706f2e10e1835",
        dataset_hash="fixture",
        artifact_dir=tmp_path,
    )
    assert len(load_trials(tmp_path / "trials.jsonl")) == 3
    assert all(
        r["execution_authorization"] == "DENIED_RESEARCH_ONLY"
        and r["outcome"] == "NO_TRADE"
        for r in records
    )
    assert all(r["arbitration_decision"] == "INELIGIBLE" for r in records)
    assert (
        core.process_research_signals(
            signals,
            decision_time=NOW,
            risk_snapshot=RiskSnapshot(10000, 10000, 0, 0, 0, 2, 2, (0.001,) * 30),
            run_id="fixture",
            git_commit="fa31e6f5ceb09f4f14ae13fef50706f2e10e1835",
            dataset_hash="fixture",
            artifact_dir=tmp_path,
        )
        == records
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("confidence", 0.9),
        ("expected_return", 0.1),
        ("decision", Decision.CONFIRMED_ENTRY),
    ],
)
def test_public_signal_and_internal_envelope_cannot_disagree(field, value):
    signal = generate(MovingAverageCrossoverHypothesis(enabled=True))[0]
    with pytest.raises(ValueError, match="PROJECTION_MISMATCH"):
        candidates_from_signals([replace(signal, **{field: value})])


def test_no_ad_hoc_signal_or_candidate_override():
    signal = generate(MovingAverageCrossoverHypothesis(enabled=True))[0]
    with pytest.raises(ValueError, match="ENVELOPE"):
        candidates_from_signals([replace(signal, metadata={})])
    with pytest.raises(ValueError, match="OVERRIDE"):
        ResearchTradingOrchestrator().process_research_signals([signal], candidates=[])


def test_registry_defaults_and_existing_manifest_record_actual_parameters():
    registry = course_hypothesis_registry()
    assert set(registry) == {"sma_crossover", "rsi_moving_average"}
    assert all(generate(g) == [] for g in registry.values())
    manifest = course_experiment_manifest(
        generators=registry,
        experiment_id="fixture",
        created_at=NOW,
        git_commit="fa31e6f5ceb09f4f14ae13fef50706f2e10e1835",
        dataset_id="fixture",
        random_seed=42,
        fee_bps=10,
        slippage_bps=2,
    )
    notes = json.loads(manifest.notes)
    assert notes["parameter_records"]["sma_crossover"]["parameters"]["fast"] == 50
    assert notes["parameter_records"]["sma_crossover"]["parameters"]["slow"] == 150
    assert notes["research_status"] == "HYPOTHESIS" and not notes["live_execution"]
    assert manifest.execution_mode is ExecutionMode.BACKTEST
    assert manifest.fee_bps == 10 and manifest.slippage_bps == 2


def test_signal_serializes_through_existing_dataclass_projection_without_mutation():
    signal = generate(MovingAverageCrossoverHypothesis(enabled=True))[0]
    projection = asdict(signal)
    json.dumps(projection, default=str, allow_nan=False)
    projection["metadata"]["course_candidate"]["confidence"] = 1
    assert signal.metadata["course_candidate"]["confidence"] is None
    assert candidates_from_signals([signal])
