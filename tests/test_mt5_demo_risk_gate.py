from datetime import datetime, timezone
from decimal import Decimal

from research_bot.mt5_demo_risk_gate import check_demo_pretrade


NOW = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)


def check(**overrides):
    arguments = dict(
        equity="100000",
        day_start_equity="100000",
        day_start_date_utc="2026-10-14",
        now=NOW,
        side="BUY",
        entry_price="60000",
        stop_price="59400",
        requested_notional="20000",
        declared_risk_fraction="0.0025",
    )
    arguments.update(overrides)
    return check_demo_pretrade(**arguments)


def test_valid_stop_cost_stress_approval():
    result = check()
    assert result.approved is True
    assert result.estimated_stop_loss_currency == Decimal("300.00")
    assert result.estimated_stop_loss_fraction == Decimal("0.003")


def test_daily_equity_drop_at_ten_percent_fails_closed():
    result = check(equity="90000")
    assert not result.approved
    assert result.kill_switch
    assert result.reasons == ("DAILY_DRAWDOWN_KILL_SWITCH",)


def test_trade_budget_counts_costs_as_well_as_stop():
    result = check(requested_notional="150000")
    assert result.reasons == ("TRADE_LOSS_BUDGET_EXCEEDED",)
    assert result.estimated_stop_loss_fraction > Decimal("0.02")


def test_missing_or_stale_baseline_cannot_send():
    for overrides in [
        {"day_start_equity": None},
        {"day_start_date_utc": None},
        {"day_start_date_utc": "2026-10-13"},
    ]:
        assert check(**overrides).reasons == ("MISSING_OR_STALE_DAY_START_EQUITY",)


def test_short_stop_direction_guard():
    assert check(side="SELL", stop_price="59400").reasons == ("INVALID_STOP_DIRECTION",)
    assert check(side="SELL", stop_price="60600").approved


def test_nan_and_infinite_inputs_fail_closed():
    for value in [float("nan"), float("inf"), "NaN", "-Infinity"]:
        assert check(equity=value).reasons == ("INVALID_PRETRADE_ACCOUNT_OR_ORDER_DATA",)


def test_declaration_cap_fails_even_if_trade_small():
    assert check(declared_risk_fraction="0.021").reasons == ("TRADE_LOSS_BUDGET_EXCEEDED",)


def test_equity_just_above_daily_ten_percent_has_no_automatic_kill():
    assert check(equity="90000.01", requested_notional="5000").approved


def test_decision_timestamp_without_timezone_is_vetoed():
    assert check(now=datetime(2026, 10, 14)).reasons == ("NAIVE_DECISION_TIMESTAMP",)
