"""Causal and accounting checks for the locked v3 rules on invented prices."""

import math

import numpy as np
import pandas as pd
import pytest

from research_bot.cross_sectional_v3 import V3DataInvalid, decide, rebalance_at_open, simulate_development


def invented_history(positive: int = 10, *, delisted: bool = False):
    dates = pd.date_range("2022-09-01", periods=122, freq="D", tz="UTC")
    symbols = ["BTCUSDT", "ETHUSDT"] + [f"COIN{i:02d}USDT" for i in range(48)]
    if delisted:
        symbols.append("FTTUSDT")
    catalog = pd.DataFrame({
        "symbol": symbols,
        "listed_at": [pd.Timestamp("2022-01-01", tz="UTC")] * len(symbols),
        "delisted_at": [pd.NaT] * 50 + ([pd.Timestamp("2022-12-01", tz="UTC")] if delisted else []),
        "source": ["invented fixture"] * len(symbols),
    })
    rows = []
    for i, symbol in enumerate(symbols):
        slope = 0.002 + i * 0.00001 if i < positive else -0.002 - i * 0.00001
        for j, date in enumerate(dates):
            rows.append({
                "date": date, "symbol": symbol,
                "open": 100 * math.exp(slope * j),
                "close": 100 * math.exp(slope * (j + 1)),
                "quote_volume": 100_000_000 - i * 100_000,
            })
    return pd.DataFrame(rows), catalog, dates[-1] - pd.Timedelta(days=(dates[-1].weekday() + 1) % 7)


@pytest.mark.parametrize("positive,expected_n,expected_invested", [
    (0, 0, 0.0), (1, 1, 0.1), (4, 4, 0.3), (7, 7, 0.7), (10, 10, 1.0),
])
def test_cash_mode_exact_branches(positive, expected_n, expected_invested):
    bars, catalog, sunday = invented_history(positive)
    out = decide(bars, catalog, sunday, catalog_complete=True)
    assert out.positive_count == expected_n
    assert len(out.targets) == expected_n
    assert sum(out.targets.values()) == pytest.approx(expected_invested)
    assert out.cash_weight == pytest.approx(1 - expected_invested)
    assert out.execution == sunday + pd.Timedelta(days=2)
    if expected_n:
        assert len(set(round(x, 12) for x in out.targets.values())) == 1


def test_future_prices_do_not_change_decision_and_test_sunday_is_sealed():
    bars, catalog, sunday = invented_history(7)
    original = decide(bars, catalog, sunday, catalog_complete=True)
    future = bars[bars.date > sunday].copy()
    future["close"] *= 1_000
    mutated = pd.concat([bars[bars.date <= sunday], future], ignore_index=True)
    assert decide(mutated, catalog, sunday, catalog_complete=True) == original
    with pytest.raises(V3DataInvalid, match="Test-3 is sealed"):
        decide(bars, catalog, pd.Timestamp("2024-01-07", tz="UTC"), catalog_complete=True)


def test_catalog_attestation_and_delisted_symbol_are_not_inferred_from_panel():
    bars, catalog, sunday = invented_history(10, delisted=True)
    with pytest.raises(V3DataInvalid, match="SURVIVORSHIP_UNRESOLVED"):
        decide(bars, catalog, sunday, catalog_complete=False)
    out = decide(bars, catalog, sunday, catalog_complete=True)
    assert "FTTUSDT" not in out.universe
    assert out.catalog_sha256
    truncated_catalog = catalog[catalog.symbol != "FTTUSDT"]
    with pytest.raises(V3DataInvalid, match="absent from historical catalog"):
        decide(bars, truncated_catalog, sunday, catalog_complete=True)


def test_cost_tier_uses_trailing_mean_not_median_or_today_rank():
    bars, catalog, sunday = invented_history(10)
    out = decide(bars, catalog, sunday, catalog_complete=True)
    assert out.cost_bps["BTCUSDT"] == 15
    assert out.cost_bps["ETHUSDT"] == 15
    assert out.cost_bps["COIN02USDT"] == 25
    assert out.cost_bps["COIN47USDT"] == 50
    # A surge on the next day cannot promote a lower-liquidity coin today.
    later = bars.copy()
    later.loc[(later.date > sunday) & (later.symbol == "COIN47USDT"), "quote_volume"] = 1e14
    assert decide(later, catalog, sunday, catalog_complete=True).cost_bps == out.cost_bps


def test_post_cost_rebalance_conserves_nav_and_charges_each_side():
    cash, shares, cost, orders = rebalance_at_open(
        1_000.0, {}, {"BTCUSDT": 100.0, "ALTUSDT": 10.0},
        {"BTCUSDT": 0.5, "ALTUSDT": 0.3}, {"BTCUSDT": 15, "ALTUSDT": 50},
    )
    assert cash > 0
    assert cash + shares["BTCUSDT"] * 100 + shares["ALTUSDT"] * 10 + cost == pytest.approx(1_000)
    assert cost == pytest.approx(orders["BTCUSDT"] * .0015 + orders["ALTUSDT"] * .005)
    cash2, shares2, exit_cost, exits = rebalance_at_open(
        cash, shares, {"BTCUSDT": 100.0, "ALTUSDT": 10.0}, {},
        {"BTCUSDT": 15, "ALTUSDT": 50},
    )
    assert shares2 == {}
    assert exits["BTCUSDT"] > 0 and exits["ALTUSDT"] > 0
    assert cash2 == pytest.approx(1_000 - cost - exit_cost)


def test_eligibility_uses_only_90_closed_days_and_81_strictly_liquid():
    bars, catalog, sunday = invented_history(10)
    symbol = "COIN20USDT"
    prior = (bars.symbol == symbol) & (bars.date <= sunday) & (bars.date > sunday - pd.Timedelta(days=90))
    chosen = bars.index[prior][:10]
    bars.loc[chosen, "quote_volume"] = 1_000_000  # strict >, so 80/90 remain
    result = decide(bars, catalog, sunday, catalog_complete=True)
    assert symbol not in result.universe
    assert len(result.universe) == 49


def test_three_books_share_window_and_pay_entry_exit_costs():
    bars, catalog, _ = invented_history(10)
    result = simulate_development(
        bars, catalog, start="2022-12-20T00:00:00Z", end="2023-01-01T00:00:00Z", catalog_complete=True,
    )
    assert result.equity.index.min() == pd.Timestamp("2022-12-20", tz="UTC")
    assert result.equity.index.max() == pd.Timestamp("2022-12-31", tz="UTC")
    assert set(result.equity) == {"strategy", "equal", "btc"}
    assert len(result.decisions) == 2
    assert result.decisions[0].sunday == pd.Timestamp("2022-12-18", tz="UTC")
    assert result.orders.query("book == 'btc' and side == 'buy'").shape[0] == 1
    assert set(result.orders.query("reason == 'split_liquidation'").book) == {"strategy", "equal", "btc"}
    assert (result.equity > 0).all().all()
    with pytest.raises(V3DataInvalid, match="development dates"):
        simulate_development(bars, catalog, start="2024-01-01T00:00:00Z", end="2024-02-01T00:00:00Z", catalog_complete=True)


def test_unpriced_historical_delisting_is_written_down_instead_of_dropped():
    bars, catalog, _ = invented_history(10, delisted=True)
    catalog.loc[catalog.symbol == "FTTUSDT", "delisted_at"] = pd.Timestamp("2022-12-22", tz="UTC")
    bars.loc[bars.symbol == "FTTUSDT", "quote_volume"] = 200_000_000
    ftt = bars.symbol == "FTTUSDT"
    bars.loc[ftt, "open"] = 100 * np.exp(0.01 * np.arange(int(ftt.sum())))
    bars.loc[ftt, "close"] = bars.loc[ftt, "open"] * math.exp(0.01)
    bars = bars[~((bars.symbol == "FTTUSDT") & (bars.date >= pd.Timestamp("2022-12-22", tz="UTC")))]
    result = simulate_development(bars, catalog, start="2022-12-20T00:00:00Z", end="2022-12-25T00:00:00Z", catalog_complete=True)
    written_off = result.orders.query("side == 'writeoff' and symbol == 'FTTUSDT'")
    assert set(written_off.book) == {"strategy", "equal"}
    assert (written_off.date == pd.Timestamp("2022-12-22", tz="UTC")).all()
