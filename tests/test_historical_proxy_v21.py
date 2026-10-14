import numpy as np
import pandas as pd

from research_bot.historical_proxy_v21 import (
    FLOW_PROXY_FEATURES,
    PRICE_FEATURES,
    V21HistoricalProxyConfig,
    add_price_and_target_features,
    aggregate_5m_to_4h,
    benjamini_hochberg,
    paired_block_inference,
    parse_binance_archive_timestamp,
    portfolio_net_returns,
    walk_forward_probabilities,
)


def test_binance_timestamp_parser_handles_ms_and_us():
    ts = pd.Series([1735689600000, 1735689600000000])
    out = parse_binance_archive_timestamp(ts)
    assert out.iloc[0] == pd.Timestamp("2025-01-01T00:00:00Z")
    assert out.iloc[1] == pd.Timestamp("2025-01-01T00:00:00Z")


def _five_minute_block(symbol="BTCUSDT", start="2026-01-01T00:00:00Z", n=48):
    t = pd.date_range(start=start, periods=n, freq="5min", tz="UTC")
    price = 100 + np.linspace(0, 1, n)
    return pd.DataFrame({
        "symbol": symbol,
        "timestamp": t,
        "open": price,
        "high": price + 0.2,
        "low": price - 0.2,
        "close": price + 0.05,
        "volume": np.full(n, 10.0),
        "quote_volume": np.full(n, 1000.0),
        "trade_count": np.arange(1, n + 1) + 100,
        "taker_buy_quote": np.linspace(450.0, 550.0, n),
    })


def test_4h_aggregation_requires_all_48_contiguous_subbars():
    cfg = V21HistoricalProxyConfig()
    good = aggregate_5m_to_4h(_five_minute_block(), cfg)
    assert len(good) == 1
    assert good.iloc[0]["subbar_count"] == 48
    short = aggregate_5m_to_4h(_five_minute_block(n=47), cfg)
    assert short.empty
    gapped = _five_minute_block()
    gapped.loc[20, "timestamp"] += pd.Timedelta(minutes=5)
    gapped = gapped.drop_duplicates("timestamp")
    assert aggregate_5m_to_4h(gapped, cfg).empty


def _synthetic_4h_panel():
    cfg = V21HistoricalProxyConfig()
    times = pd.date_range("2025-09-01T00:00:00Z", "2026-09-01T00:00:00Z", freq="4h", inclusive="left")
    pieces = []
    for j, symbol in enumerate(cfg.symbols):
        x = np.arange(len(times), dtype=float)
        close = 100 * np.exp(0.0001 * x + 0.01 * np.sin(x / 7 + j))
        g = pd.DataFrame({
            "symbol": symbol,
            "decision_at": times,
            "open": close * 0.999,
            "high": close * 1.002,
            "low": close * 0.998,
            "close": close,
            "subbar_count": 48,
        })
        for k, name in enumerate(FLOW_PROXY_FEATURES):
            g[name] = np.sin(x / (5 + k) + j) * 0.1 + (k + 1) * 0.001
        pieces.append(g)
    bars = pd.concat(pieces, ignore_index=True)
    return add_price_and_target_features(bars, cfg).dropna().reset_index(drop=True)


def test_walk_forward_never_uses_label_ending_at_or_after_eval_start():
    cfg = V21HistoricalProxyConfig()
    panel = _synthetic_4h_panel()
    pred = walk_forward_probabilities(panel, model_name="logistic", feature_names=PRICE_FEATURES, config=cfg)
    assert not pred.empty
    for month, rows in pred.groupby("eval_month"):
        start = pd.Timestamp(f"{month}-01T00:00:00Z")
        train_end = pd.Timestamp(rows["train_end_target_at"].iloc[0])
        assert train_end < start


def test_portfolio_costs_initial_entry_and_terminal_flattening():
    cfg = V21HistoricalProxyConfig()
    times = pd.date_range("2026-05-01", periods=3, freq="4h", tz="UTC")
    pred = pd.DataFrame([
        {"symbol": symbol, "decision_at": t, "target_return": 0.01, "probability": 0.9}
        for t in times for symbol in cfg.symbols
    ])
    out = portfolio_net_returns(pred, cost_bps=10, config=cfg)
    # Initial gross exposure 1.0 costs 10 bps; no middle turnover; final flatten costs another 10 bps.
    assert np.isclose(out["turnover"].sum(), 2.0)
    assert np.isclose(out["explicit_cost"].sum(), 0.002)


def test_paired_block_inference_and_bh_are_deterministic_and_bounded():
    diff = pd.Series(np.linspace(-0.001, 0.002, 200))
    a = paired_block_inference(diff, reps=200, block_length=10, seed=7)
    b = paired_block_inference(diff, reps=200, block_length=10, seed=7)
    assert a == b
    assert 0 <= a["one_sided_p"] <= 1
    q = benjamini_hochberg({"a": 0.01, "b": 0.04, "c": 0.20})
    assert 0 <= q["a"] <= q["b"] <= q["c"] <= 1
