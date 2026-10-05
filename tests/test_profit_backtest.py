from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from research_bot.research.profit_backtest import BacktestConfig, run_backtest, run_baselines


def bars(n=12):
    return pd.DataFrame({"timestamp": pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC"),
                         "open": 100., "high": 101., "low": 99., "close": 100.,
                         "volume": 10., "probability": .9, "atr": 1.})


def test_next_open_cost_accounting_matches_hand_calculation():
    f = bars(3)
    f.loc[2, "probability"] = np.nan
    c = BacktestConfig(fee_bps=10, slippage_bps=5)
    r = run_backtest(f, c)
    buy_price = 100 * 1.0005
    sell_price = 100 * .9995
    q = 100 / (buy_price * 1.001 - 98 * .9995 * .999)
    expected_loss = q * (buy_price * 1.001 - sell_price * .999)
    assert r.trades.iloc[0].entry_timestamp == f.timestamp.iloc[1]
    assert r.metrics["final_equity"] == pytest.approx(10000 - expected_loss)
    assert r.metrics["fees_paid"] == pytest.approx(q * (buy_price + sell_price) * .001)
    assert r.metrics["max_drawdown"] == pytest.approx(expected_loss / 10000)
    assert r.trades.iloc[0].quantity * buy_price * 1.001 <= 8000


def test_changing_future_candle_does_not_change_earlier_fills():
    a = bars()
    b = a.copy()
    b.loc[8:, ["open", "high", "low", "close"]] *= 2
    ra, rb = run_backtest(a), run_backtest(b)
    cutoff = a.timestamp.iloc[7]
    pd.testing.assert_frame_equal(ra.events.loc[ra.events.timestamp <= cutoff].reset_index(drop=True),
                                  rb.events.loc[rb.events.timestamp <= cutoff].reset_index(drop=True))


def test_gap_stop_uses_adverse_open_and_drawdown_latch_survives_new_day():
    f = bars(72)
    f.loc[2:, ["open", "high", "low", "close"]] = [50, 51, 49, 50]
    r = run_backtest(f)
    assert r.trades.iloc[0].exit_reference_price == 50
    assert r.metrics["drawdown_halts"] == 1
    assert len(r.trades) == 1
    assert r.equity.quantity.iloc[3:].eq(0).all()


def test_missing_predictions_do_not_disable_protective_stops():
    f = bars(4)
    f.loc[1:, "probability"] = np.nan
    f.loc[2, "low"] = 95
    r = run_backtest(f, BacktestConfig(fee_bps=0, slippage_bps=0))
    assert len(r.trades) == 1
    assert r.trades.iloc[0].exit_reason == "ATR_STOP"
    assert r.trades.iloc[0].pnl_net == pytest.approx(-100)


def test_daily_limit_latches_until_next_utc_day():
    f = bars(48)
    # Small stop and repeat losses can reach the daily budget.
    f.loc[[2, 4, 6, 8], "low"] = 97
    r = run_backtest(f, BacktestConfig(fee_bps=0, slippage_bps=0))
    halts = r.events.loc[r.events.event == "DAILY_HALT"]
    assert len(halts) >= 1
    first = halts.timestamp.iloc[0]
    entries = r.events.loc[(r.events.event == "ENTRY") & (r.events.timestamp > first)]
    assert entries.timestamp.iloc[0].date() > first.date()


def test_cash_baseline_has_no_fake_sharpe_or_profit_factor():
    r = run_baselines(bars())["cash"]
    assert r.metrics["total_return"] == 0
    assert r.metrics["sharpe"] is None
    assert r.metrics["profit_factor"] is None
    assert r.metrics["trade_count"] == 0


def test_buy_hold_uses_same_costs_and_liquidates():
    c = BacktestConfig()
    r = run_baselines(bars())["buy_hold_80pct"]
    assert len(r.trades) == 1
    assert r.metrics["net_return"] < 0
    assert r.equity.quantity.iloc[-1] == 0


@pytest.mark.parametrize("mutation", ["gap", "negative", "infinite", "probability"])
def test_malformed_market_data_fails_closed(mutation):
    f = bars()
    if mutation == "gap":
        f = f.drop(index=4)
    elif mutation == "negative":
        f.loc[2, "open"] = -1
    elif mutation == "infinite":
        f.loc[2, "high"] = np.inf
    else:
        f.loc[2, "probability"] = 1.1
    with pytest.raises(ValueError):
        run_backtest(f)


def test_configuration_rejects_nonfinite_or_borrowing():
    with pytest.raises(ValueError):
        replace(BacktestConfig(), max_exposure=2)
    with pytest.raises(ValueError):
        replace(BacktestConfig(), fee_bps=float("nan"))
