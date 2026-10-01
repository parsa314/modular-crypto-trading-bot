"""Cash and risk accounting fixtures; no fixture is empirical market evidence."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from research_bot.v58.portfolio import PortfolioConfig, simulate_synthetic_portfolio


def candles(rows=None):
    rows = rows or [(100.0, 102.0, 98.0, 101.0)]
    frame = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    frame.insert(0, "timestamp", pd.date_range("2025-01-01", periods=len(frame), freq="4h", tz="UTC"))
    frame["volume"] = 1000.0
    return frame


def intent(event_id="e1", symbol="BTC", at=None, entry=100.0, stop=90.0,
           target=115.0, horizon=12, utility=0.01, **extra):
    return {
        "event_id": event_id, "venue": "synthetic", "symbol": symbol,
        "decision_at": at if at is not None else pd.Timestamp("2025-01-01", tz="UTC"),
        "entry_price": entry, "stop_price": stop, "target_price": target,
        "horizon_bars": horizon, "expected_utility": utility, **extra,
    }


def test_manual_cash_fees_and_terminal_liquidation():
    result = simulate_synthetic_portfolio({"BTC": candles()}, [intent()])
    # Independent cash calculation: $25 total loss budget at the $90 stop,
    # with 0.12% charged separately on entry and exit notionals.
    quantity = 25.0 / (10.0 + 0.0012 * (100.0 + 90.0))
    costs = quantity * (100.0 + 101.0) * 0.0012
    final = 10000.0 + quantity - costs
    assert result["summary"]["final_equity"] == pytest.approx(final)
    assert result["summary"]["total_costs"] == pytest.approx(costs)
    assert result["summary"]["trade_count"] == 1
    assert result["fills"][0]["quantity"] == pytest.approx(quantity)
    assert result["fills"][1]["reason"] == "TERMINAL_LIQUIDATION"
    assert result["fills"][1]["timestamp"] == "2025-01-01T04:00:00+00:00"
    assert result["equity_curve"][-1]["cash"] == pytest.approx(final)
    assert result["equity_curve"][-1]["positions"] == {}
    assert result["classification"] == "SYNTHETIC_ENGINEERING_ONLY"
    assert result["empirical_training"] is result["paper_execution"] is result["live_execution"] is False
    assert result["broad_promotion"] is False
    assert result["unavailable"]["correlation_estimates"] is None
    assert result["unavailable"]["market_impact"] is None
    assert result["summary"]["cvar_95"] is None
    json.dumps(result, allow_nan=False)


def test_stop_first_preserves_total_risk_budget_including_both_costs():
    result = simulate_synthetic_portfolio(
        {"BTC": candles([(100.0, 120.0, 80.0, 110.0)])}, [intent()])
    assert result["summary"]["final_equity"] == pytest.approx(9975.0)
    exit_fill = result["fills"][1]
    assert exit_fill["price"] == 90.0
    assert exit_fill["reason"] == "STOP"
    assert exit_fill["intrabar_ambiguity"] is True


def test_same_open_rank_cash_sharing_and_outcomes_cannot_finance_admissions():
    symbols = ("AAA", "BBB", "CCC")
    bars = {symbol: candles([(100.0, 100.2, 99.8, 100.0)]) for symbol in symbols}
    decisions = [intent(symbol, symbol, stop=99.9, target=100.15, utility=utility)
                 for symbol, utility in zip(symbols, (0.01, 0.03, 0.02))]
    result = simulate_synthetic_portfolio(bars, decisions)
    assert [d["event_id"] for d in result["decisions"]] == ["BBB", "CCC", "AAA"]
    assert [d["admitted"] for d in result["decisions"]] == [True, True, False]
    assert result["decisions"][2]["reason"] == "NO_PORTFOLIO_CAPACITY"
    after_admission = next(row for row in result["equity_curve"] if row["phase"] == "AFTER_ADMISSIONS")
    assert after_admission["gross_exposure"] <= 0.70 + 1e-12
    assert max(position["weight"] for position in after_admission["positions"].values()) <= 0.35 + 1e-12
    assert after_admission["cash"] > 0
    # Every symbol hits both barriers. A different future intrabar path does
    # not alter the same-open allocation or liberate cash for the third order.
    changed = {symbol: candles([(100.0, 101.0, 100.0, 100.5)]) for symbol in symbols}
    assert simulate_synthetic_portfolio(changed, decisions)["decisions"] == result["decisions"]
    assert simulate_synthetic_portfolio(bars, decisions[::-1]) == result


def test_no_asset_preemption_and_nonpositive_utility_abstention():
    frame = candles([(100.0, 101.0, 99.0, 100.0)] * 2)
    decisions = [intent(), intent("later", at=frame.iloc[1].timestamp, utility=10.0),
                 intent("zero", "ETH", utility=0), intent("no-trade", "ETH", admission_reason="HIGH_ENTROPY")]
    result = simulate_synthetic_portfolio({"BTC": frame, "ETH": frame}, decisions)
    reasons = {d["event_id"]: d["reason"] for d in result["decisions"]}
    assert reasons["later"] == "ASSET_POSITION_OPEN"
    assert reasons["zero"] == "NON_POSITIVE_EXPECTED_UTILITY"
    assert reasons["no-trade"] == "HIGH_ENTROPY"
    assert result["summary"]["trade_count"] == 1


def test_strategy_only_baseline_bypasses_forecast_gate_but_retains_cash_risk_gates():
    frame = candles([(100.0, 102.0, 98.0, 101.0)])
    strategy = intent(utility=0, policy="STRATEGY_ONLY")
    model = intent(utility=0, policy="ML_UTILITY")
    baseline = simulate_synthetic_portfolio({"BTC": frame}, [strategy])
    assert baseline["decisions"][0]["admitted"] is True
    assert baseline["decisions"][0]["policy"] == "STRATEGY_ONLY"
    assert simulate_synthetic_portfolio({"BTC": frame}, [model])["decisions"][0]["admitted"] is False
    assert baseline["decisions"][0]["stop_risk"] <= 25.0 + 1e-10
    with pytest.raises(ValueError, match="policy"):
        simulate_synthetic_portfolio({"BTC": frame}, [intent(policy="FUTURE_ORACLE")])


def test_gap_drawdown_latches_and_all_risk_reducing_exits_remain_allowed():
    frame = candles([(100.0, 100.5, 99.5, 100.0), (50.0, 50.5, 49.5, 50.0)])
    symbols = ("BTC", "ETH", "SOL", "XRP", "DOGE")
    decisions = [intent(symbol, symbol, stop=99.0, target=101.5) for symbol in symbols[:3]]
    decisions += [intent("safe", "XRP", stop=1.0, target=200.0),
                  intent("after-gap", "DOGE", at=frame.iloc[1].timestamp, entry=50.0, stop=49.0, target=51.5)]
    result = simulate_synthetic_portfolio({symbol: frame for symbol in symbols}, decisions)
    assert result["summary"]["drawdown_kill_triggered"] is True
    after_gap = next(d for d in result["decisions"] if d["event_id"] == "after-gap")
    assert after_gap["admitted"] is False
    assert after_gap["reason"] == "DRAWDOWN_KILL"
    gaps = [fill for fill in result["fills"] if fill["reason"] == "GAP_STOP"]
    assert len(gaps) == 3
    assert all(fill["price"] == 50.0 and fill["timestamp"] == frame.iloc[1].timestamp.isoformat() for fill in gaps)
    assert any(fill["reason"] == "TERMINAL_LIQUIDATION" and fill["event_id"] == "safe" for fill in result["fills"])
    assert result["equity_curve"][-1]["positions"] == {}


def test_gap_target_does_not_create_an_unrealizable_peak_or_false_kill():
    btc = candles([(100.0, 100.5, 99.5, 100.0), (200.0, 200.5, 199.5, 200.0)])
    eth = candles([(100.0, 100.5, 99.5, 100.0)] * 2)
    decisions = [intent("target-gap", "BTC", stop=99.0, target=101.5),
                 intent("later", "ETH", at=btc.iloc[1].timestamp)]
    result = simulate_synthetic_portfolio({"BTC": btc, "ETH": eth}, decisions)
    assert result["summary"]["drawdown_kill_triggered"] is False
    assert next(row for row in result["decisions"] if row["event_id"] == "later")["admitted"] is True
    target_fill = next(fill for fill in result["fills"] if fill["reason"] == "GAP_TARGET")
    assert target_fill["price"] == 101.5
    assert result["summary"]["max_drawdown"] < 0.001
    assert result["summary"]["max_drawdown"] == pytest.approx(max(row["drawdown"] for row in result["equity_curve"]))


def test_risk_engine_cvar_veto_is_independent_of_positive_model_utility():
    frame = candles([(100.0, 100.1, 99.0, 100.05)] * 21)
    decisions = [intent(f"e{i}", at=row.timestamp, stop=99.5, target=100.75, horizon=1)
                 for i, row in enumerate(frame.itertuples())]
    result = simulate_synthetic_portfolio({"BTC": frame}, decisions, PortfolioConfig(max_cvar95=0.001))
    assert result["summary"]["trade_count"] == 20
    assert result["summary"]["drawdown_kill_triggered"] is False
    assert result["decisions"][-1]["reason"] == "CVAR_95_BREACH"
    assert result["summary"]["cvar_95"] == pytest.approx(0.0025)


def test_duplicate_identity_rejected_even_when_first_is_abstained():
    with pytest.raises(ValueError, match="duplicate"):
        simulate_synthetic_portfolio({"BTC": candles()}, [intent(utility=-1), intent()])


@pytest.mark.parametrize("kwargs", [
    {"risk_per_trade": 0.0026}, {"max_asset_weight": 0.36}, {"max_gross_exposure": 0.71},
    {"drawdown_kill": 0.051}, {"max_cvar95": 0.04}, {"risk_per_trade": 0},
    {"initial_equity": float("nan")}, {"round_trip_cost_bps": -1},
    {"round_trip_cost_bps": 20000}, {"initial_equity": True},
    {"initial_equity": None}, {"risk_per_trade": "invalid"},
])
def test_config_cannot_relax_frozen_risk_limits(kwargs):
    with pytest.raises(ValueError):
        PortfolioConfig(**kwargs)


@pytest.mark.parametrize("mutation", ["future_time", "missing_next_open", "naive_time", "wrong_entry", "bad_stop", "bad_target", "nan_utility", "short", "bad_horizon", "malformed_bars", "different_clocks", "non_synthetic"])
def test_invalid_inputs_fail_closed(mutation):
    frame = candles([(100.0, 102.0, 98.0, 101.0)] * 2)
    bars = {"BTC": frame}
    decision = intent()
    if mutation == "future_time":
        decision["decision_at"] = frame.iloc[-1].timestamp + pd.Timedelta(hours=4)
    elif mutation == "missing_next_open":
        decision["decision_at"] += pd.Timedelta(hours=1)
    elif mutation == "naive_time":
        decision["decision_at"] = pd.Timestamp("2025-01-01")
    elif mutation == "wrong_entry":
        decision["entry_price"] = 100.01
    elif mutation == "bad_stop":
        decision["stop_price"] = float("nan")
    elif mutation == "bad_target":
        decision["target_price"] = 99.0
    elif mutation == "nan_utility":
        decision["expected_utility"] = float("nan")
    elif mutation == "short":
        decision["direction"] = "SHORT"
    elif mutation == "bad_horizon":
        decision["horizon_bars"] = True
    elif mutation == "malformed_bars":
        frame.loc[0, "high"] = 1.0
    elif mutation == "different_clocks":
        bars["ETH"] = frame.copy()
        bars["ETH"]["timestamp"] += pd.Timedelta(hours=4)
    elif mutation == "non_synthetic":
        decision["venue"] = "kraken"
    with pytest.raises((ValueError, PermissionError)):
        simulate_synthetic_portfolio(bars, [decision])


def test_empty_decision_set_preserves_cash_and_strict_json():
    result = simulate_synthetic_portfolio({"BTC": candles()}, [])
    assert result["summary"]["final_equity"] == 10000.0
    assert result["summary"]["trade_count"] == 0
    assert result["summary"]["total_costs"] == 0
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("config", [False, 0, "", []])
def test_falsey_invalid_config_is_not_silently_defaulted(config):
    with pytest.raises(ValueError, match="config"):
        simulate_synthetic_portfolio({"BTC": candles()}, [], config)


def test_manipulated_config_is_revalidated_before_simulation():
    config = PortfolioConfig()
    object.__setattr__(config, "risk_per_trade", 0.10)
    with pytest.raises(ValueError, match="risk_per_trade"):
        simulate_synthetic_portfolio({"BTC": candles()}, [], config)
