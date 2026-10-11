import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from research_bot.course.contracts import (
    BookSnapshot,
    HypothesisCandidate,
    MarketMetadata,
)
from research_bot.decision import DecisionEngine
from research_bot.execution import NoOrderTransport, PaperExecutionEngine
from research_bot.orchestrator import ResearchTradingOrchestrator
from research_bot.risk import RiskEngine, RiskSnapshot
from research_bot.trial_registry_v17 import load_trials

NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)


def candidate(identity="one", **kwargs):
    values = {
        "event_id": identity,
        "strategy_id": "research",
        "strategy_version": "1",
        "symbol": "BTC/USDT",
        "venue": "fixture",
        "direction": 1,
        "decision_time": NOW,
        "earliest_execution_time": NOW + timedelta(seconds=1),
        "feature_snapshot_id": "data",
        "config_hash": "cfg",
        "expected_edge": Decimal(".01"),
        "expected_cost": Decimal(".0024"),
        "uncertainty": Decimal(".001"),
        "confidence": Decimal(".8"),
        "regime": "RANGE",
        "invalidation_conditions": ("TIMEOUT",),
        "data_quality": "VALID",
        "evidence_refs": ("fixture",),
        "capital_requirements": (("USDT", Decimal(100)),),
        "evidence_available_at": NOW,
        "source_observed_at": NOW,
    }
    values.update(kwargs)
    return HypothesisCandidate(**values)


def risk(**kwargs):
    values = {
        "equity": 10000,
        "peak_equity": 10000,
        "gross_exposure": 0,
        "asset_weight": 0,
        "turnover": 0,
        "spread_bps": 2,
        "slippage_bps": 2,
        "recent_returns": (0.001,) * 30,
    }
    values.update(kwargs)
    return RiskSnapshot(**values)


def batch(tmp_path, candidates=None, engine=None, **kwargs):
    values = {
        "candidates": candidates or [candidate()],
        "decision_time": NOW,
        "risk_snapshot": risk(),
        "run_id": "engineering_fixture",
        "git_commit": "a" * 40,
        "dataset_hash": "fixture_hash",
        "artifact_dir": tmp_path,
        "capital_available": {"USDT": Decimal(1000)},
        "capital_prices": {"USDT": Decimal(1)},
        "capital_price_available_at": {"USDT": NOW},
    }
    values.update(kwargs)
    return (engine or ResearchTradingOrchestrator()).process_research_candidates(
        **values
    )


def test_default_transport_has_no_network_submission_capability(monkeypatch):
    monkeypatch.setenv("BITPIN_TOKEN", "fixture_never_read")
    ex = ResearchTradingOrchestrator().execution_engine
    assert isinstance(ex, NoOrderTransport)
    assert not any(
        hasattr(ex, name) for name in ("place_order", "submit", "order_send", "client")
    )
    with pytest.raises(RuntimeError, match="RESEARCH_ONLY"):
        ex.execute(object())


def test_existing_forecast_default_is_research_even_when_risk_approves():
    outcome = ResearchTradingOrchestrator().process_forecast(
        asset="BTC",
        symbol="BTC/USDT",
        timestamp=NOW,
        expected_return=0.01,
        expected_cost=0.001,
        risk_penalty=0,
        uncertainty_penalty=0,
        confidence=0.9,
        currently_long=False,
        risk_snapshot=risk(),
        reference_price=100,
        quantity=1,
        client_order_id="fixture",
    )
    assert outcome.risk.approved and outcome.fill is None
    assert outcome.status == "EXECUTION_DENIED_RESEARCH_ONLY"


def test_batch_uses_existing_risk_and_registry_without_orders(tmp_path):
    rows = batch(tmp_path)
    assert rows[0]["arbitration_decision"] == "SELECTED_RESEARCH_ONLY"
    assert rows[0]["execution_authorization"] == "DENIED_RESEARCH_ONLY"
    assert rows[0]["simulated_order_id"] is None and rows[0]["outcome"] == "NO_TRADE"
    assert len(load_trials(tmp_path / "trials.jsonl")) == 1
    assert batch(tmp_path) == rows
    assert len(load_trials(tmp_path / "trials.jsonl")) == 1
    artifact = next(tmp_path.glob("*.json"))
    assert json.loads(artifact.read_text())["event_id"] == "one"


def test_revised_event_and_same_event_duplicate_are_rejected(tmp_path):
    batch(tmp_path)
    with pytest.raises(ValueError, match="REVISION"):
        batch(tmp_path, [candidate(confidence=Decimal(".9"))])
    with pytest.raises(ValueError, match="DUPLICATE"):
        batch(tmp_path, [candidate(), candidate()])
    with pytest.raises(ValueError, match="REVISION"):
        batch(tmp_path, [candidate(confidence=Decimal(".95"))], run_id="other_run")


def test_future_availability_and_same_close_execution_are_invalid():
    with pytest.raises(ValueError, match="POINT_IN_TIME"):
        candidate(evidence_available_at=NOW + timedelta(seconds=1))
    with pytest.raises(ValueError, match="SAME_CLOSE"):
        candidate(earliest_execution_time=NOW)


def test_causal_tie_and_capital_conflicts_have_auditable_reasons(tmp_path):
    rows = batch(tmp_path, [candidate("z"), candidate("a")])
    assert rows[0]["event_id"] == "a"
    assert rows[1]["arbitration_decision"] == "CONFLICT_REJECTED"
    assert "SAME_ASSET_CONFLICT" in rows[1]["rejection_reasons"]
    other = batch(tmp_path / "capital", capital_available={"USDT": Decimal(1)})
    assert "CAPITAL_RESERVATION_CONFLICT" in other[0]["rejection_reasons"]


def test_global_risk_projection_counts_multiple_assets(tmp_path):
    rows = batch(
        tmp_path,
        [
            candidate("a", capital_requirements=(("USDT", Decimal(2000)),)),
            candidate(
                "b", symbol="ETH/USDT", capital_requirements=(("USDT", Decimal(2000)),)
            ),
        ],
        capital_available={"USDT": Decimal(5000)},
        risk_snapshot=risk(gross_exposure=0.65),
    )
    assert rows[0]["arbitration_decision"] == "SELECTED_RESEARCH_ONLY"
    assert "MAX_GROSS_EXPOSURE_BREACH" in rows[1]["rejection_reasons"]


def test_unknown_forecast_short_and_missing_tail_evidence_abstain(tmp_path):
    for idx, c in enumerate(
        [
            candidate(expected_edge=None, confidence=None),
            candidate(direction=-1),
            candidate(evidence_available_at=None, source_observed_at=None),
        ]
    ):
        row = batch(tmp_path / str(idx), [c])[0]
        assert row["arbitration_decision"] == "INELIGIBLE"
    row = batch(tmp_path / "risk", risk_snapshot=risk(recent_returns=()))[0]
    assert "INSUFFICIENT_TAIL_RISK_EVIDENCE" in row["rejection_reasons"]


def test_cannot_inject_execution_engine_into_course_pipeline(tmp_path):
    with pytest.raises(RuntimeError, match="NO_ORDER"):
        batch(
            tmp_path,
            engine=ResearchTradingOrchestrator(execution_engine=PaperExecutionEngine()),
        )


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), float("-inf")])
def test_nonfinite_forecast_regression(bad):
    with pytest.raises(ValueError, match="NONFINITE"):
        DecisionEngine().evaluate(
            asset="BTC",
            timestamp=NOW,
            expected_return=bad,
            expected_cost=0,
            risk_penalty=0,
            uncertainty_penalty=0,
            confidence=0.9,
        )


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), float("-inf")])
def test_invalid_tail_history_regression(bad):
    decision = RiskEngine().evaluate(risk(recent_returns=(bad,) * 30))
    assert not decision.approved and decision.kill_switch


def test_decimal_units_book_validation_and_price_quantization():
    with pytest.raises(ValueError, match="TOMAN"):
        MarketMetadata(
            "fixture",
            "BTC/IRT",
            "BTC",
            "IRT",
            Decimal(1),
            Decimal(".01"),
            Decimal(1),
            "RIAL",
        )
    market = MarketMetadata(
        "fixture",
        "BTC/USDT",
        "BTC",
        "USDT",
        Decimal(".01"),
        Decimal(".01"),
        Decimal(1),
        "USDT",
    )
    with pytest.raises(ValueError, match="PRECISION"):
        market.validate_order(Decimal(".001"), Decimal(100))
    book = BookSnapshot(
        market,
        NOW,
        NOW,
        NOW,
        NOW,
        "1",
        ((Decimal(99), Decimal(1)),),
        ((Decimal(101), Decimal(1)),),
        "fixture",
    )
    with pytest.raises(ValueError, match="STALE"):
        book.validate_at(NOW + timedelta(seconds=11))
    with pytest.raises(ValueError, match="SEQUENCE"):
        replace(book, sequence_id=None).validate_at(NOW)


@pytest.mark.parametrize("alias", ["btc/USDT", "BTC/usdt", " BTC/USDT"])
def test_case_or_whitespace_alias_cannot_bypass_asset_concentration(tmp_path, alias):
    rows = batch(
        tmp_path,
        [
            candidate("a", capital_requirements=(("USDT", Decimal(2000)),)),
            candidate(
                "b", symbol=alias, capital_requirements=(("USDT", Decimal(2000)),)
            ),
        ],
        capital_available={"USDT": Decimal(5000)},
    )
    assert rows[0]["arbitration_decision"] == "SELECTED_RESEARCH_ONLY"
    assert rows[1]["arbitration_decision"] == "INELIGIBLE"
    assert "NONCANONICAL_BASE_QUOTE" in rows[1]["rejection_reasons"]


def test_external_reserved_currency_requires_own_pit_valuation(tmp_path):
    with pytest.raises(ValueError, match="INVALID_CAPITAL_RESERVATION"):
        batch(
            tmp_path,
            capital_available={"USDT": Decimal(1000), "BTC": Decimal(1)},
            capital_reserved={"BTC": Decimal(".1")},
            capital_prices={"USDT": Decimal(1), "BTC": Decimal(100)},
        )


def test_same_base_across_venues_and_quote_currencies_conflicts(tmp_path):
    rows = batch(
        tmp_path, [candidate("a"), candidate("b", symbol="BTC/IRT", venue="other")]
    )
    assert rows[0]["arbitration_decision"] == "SELECTED_RESEARCH_ONLY"
    assert rows[1]["arbitration_decision"] == "CONFLICT_REJECTED"
    assert "SAME_ASSET_CONFLICT" in rows[1]["rejection_reasons"]
