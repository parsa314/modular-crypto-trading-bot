import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pandas as pd
import pytest

from paper_trading.data_feed import BinancePublicFeed, ParquetFeed, Snapshot
from paper_trading.logger import single_instance
from paper_trading.main import PaperEngine, load_config
from paper_trading.paper_broker import OrderBook, PaperBroker, liquidity_tiers
from paper_trading.portfolio import Portfolio
from paper_trading.strategy_runner import StrategyRunner, rsi14


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2022, 2, 7, tzinfo=timezone.utc)


def book(now=NOW, source="fixture", depth=10000):
    return OrderBook(((99.0, depth),), ((101.0, depth),), now, source)


def history(symbols=("BTCUSDT",), now=NOW):
    return pd.DataFrame([{"date": d, "symbol": s, "open": 100., "close": 100., "quote_volume": 10_000_000. + i}
                         for i, s in enumerate(symbols)
                         for d in pd.date_range(pd.Timestamp(now).normalize() - pd.Timedelta(days=40), periods=40, freq="D")])


def config():
    c = load_config(ROOT / "src/paper_trading/config.replay.yaml")
    c["strategy"]["name"] = "hybrid"
    return c


def snapshot(now=NOW, price=100.):
    return Snapshot(now, {"BTCUSDT": price}, {"BTCUSDT": 10_000_000.}, {"BTCUSDT": now}, history(now=now), "fixture")


def test_cash_cost_basis_realized_pnl_and_accounting():
    p = Portfolio.create(1000)
    p.fill("BTCUSDT", 1, 100, .1, 100)
    p.marks["BTCUSDT"] = 110
    assert p.equity() == pytest.approx(1009.9)
    assert p.snapshot()["unrealized_pnl"] == pytest.approx(9.9)
    p.fill("BTCUSDT", -1, 110, .11, 110)
    assert p.cash == pytest.approx(1009.79)
    assert p.realized_pnl == pytest.approx(9.79)
    assert not p.positions


def test_depth_execution_fee_position_cap_and_no_mutation_on_failure():
    p = Portfolio.create(1000)
    broker = PaperBroker()
    result, trades = broker.rebalance(p, {"BTCUSDT": .1}, {"BTCUSDT": book()}, {"BTCUSDT": 1}, {"BTCUSDT": 6e6}, NOW)
    expected_qty = 100 / 100.1101  # q*100 = .1*(1000 - q*(1 + .101))
    assert trades[0]["quantity"] == pytest.approx(expected_qty)
    assert trades[0]["fill_price"] == 101
    assert trades[0]["fee"] == pytest.approx(expected_qty * 101 * .001)
    assert result.snapshot()["weights"]["BTCUSDT"] == pytest.approx(.1)
    assert p.cash == 1000 and not p.positions
    sold, exits = broker.rebalance(result, {}, {"BTCUSDT": book()}, {"BTCUSDT": 1}, {"BTCUSDT": 6e6}, NOW)
    assert exits[0]["fill_price"] == 99
    assert not sold.positions
    with pytest.raises(ValueError, match="INSUFFICIENT"):
        broker.rebalance(p, {"BTCUSDT": .1}, {"BTCUSDT": book(depth=.01)}, {"BTCUSDT": 1}, {"BTCUSDT": 6e6}, NOW)
    with pytest.raises(ValueError, match="MIN_LIQUIDITY"):
        broker.rebalance(p, {"BTCUSDT": .1}, {"BTCUSDT": book()}, {"BTCUSDT": 1}, {"BTCUSDT": 5e6}, NOW)
    with pytest.raises(ValueError, match="max-position"):
        broker.rebalance(p, {"BTCUSDT": .11}, {"BTCUSDT": book()}, {"BTCUSDT": 1}, {"BTCUSDT": 6e6}, NOW)
    assert p.cash == 1000 and not p.positions


def test_tiers_follow_closed_mean_volume_and_reject_gaps():
    symbols = [f"COIN{i:02}USDT" for i in range(22)] + ["BTCUSDT", "ETHUSDT"]
    h = history(symbols)
    tiers = liquidity_tiers(h, symbols)
    assert tiers["BTCUSDT"] == tiers["ETHUSDT"] == 1
    assert tiers["COIN21USDT"] == 2
    assert tiers["COIN00USDT"] == 3
    h.loc[h.symbol == "COIN00USDT", "quote_volume"] = 1e10
    assert liquidity_tiers(h, symbols)["COIN00USDT"] == 2
    with pytest.raises(ValueError, match="required|gap"):
        liquidity_tiers(h[h.date != h.date.max()], symbols + ["MISSINGUSDT"])


def test_daily_and_total_drawdown_are_persistent_and_strict():
    p = Portfolio.create(1000)
    p.fill("BTCUSDT", 1, 100, 0, 100)
    p.mark({"BTCUSDT": 100}, NOW)
    p.mark({"BTCUSDT": 50}, NOW + timedelta(hours=1))
    assert p.halt_reason is None  # exactly 5% NAV
    p.mark({"BTCUSDT": 49}, NOW + timedelta(hours=2))
    assert p.halt_reason == "DAILY_DRAWDOWN_LIMIT"
    restored = Portfolio.from_dict(p.to_dict())
    assert restored.halt_reason == p.halt_reason
    q = Portfolio.create(1000)
    q.fill("BTCUSDT", 5, 100, 0, 100)
    q.mark({"BTCUSDT": 100}, NOW)
    q.mark({"BTCUSDT": 69}, NOW + timedelta(days=1))
    assert q.halt_reason == "TOTAL_DRAWDOWN_LIMIT"


def test_wilder_rsi_and_hourly_strategy_cash_caps():
    assert rsi14(pd.Series(range(1, 31))) == 100
    assert rsi14(pd.Series(range(30, 0, -1))) == 0
    assert rsi14(pd.Series([100] * 30)) == 50
    runner = StrategyRunner(config(), ROOT)
    target = runner.targets(snapshot(), Portfolio.create(1000))
    assert target == {"BTCUSDT": .1}
    assert runner.rebalance_slot(snapshot()) == "hybrid:2022-02-07"
    assert runner.rebalance_slot(snapshot(NOW + timedelta(days=1))) is None


def test_auto_does_not_invent_v3_verdict(tmp_path):
    c = config()
    c["strategy"]["name"] = "auto"
    c["strategy"]["result_file"] = "result.json"
    assert StrategyRunner(c, tmp_path).name == "observe"
    (tmp_path / "result.json").write_text('{"verdict":"FAIL"}')
    assert StrategyRunner(c, tmp_path).name == "hybrid"
    (tmp_path / "result.json").write_text('{"outcome":"FAIL_DATA_AND_FROZEN_WINDOW_GATE","economic_validation_measured":false}')
    assert StrategyRunner(c, tmp_path).name == "hybrid"


@pytest.mark.parametrize("route", ["/api/v3/account", "/api/v3/order", "/api/v3/userDataStream"])
def test_account_and_order_routes_are_unavailable(route):
    with pytest.raises(ValueError, match="only public market-data"):
        BinancePublicFeed(["BTCUSDT"])._get(route)


def test_v3_paper_targets_match_locked_research_rule(tmp_path):
    from research_bot.cross_sectional_v3 import decide
    symbols = [f"COIN{i:02}USDT" for i in range(50)]
    now = NOW + timedelta(days=1)  # Tuesday, previous Sunday is the signal date
    days = pd.date_range(pd.Timestamp(now) - pd.Timedelta(days=100), periods=100, freq="D")
    rows = [{"symbol": s, "date": day, "open": 100 * (1 + .0001 * (i + 1)) ** j,
             "close": 100 * (1 + .0001 * (i + 1)) ** (j + 1), "quote_volume": 1e8 + i}
            for i, s in enumerate(symbols) for j, day in enumerate(days)]
    panel = pd.DataFrame(rows)
    catalog = [{"symbol": s, "listed_at": "2021-01-01", "delisted_at": None, "source": "fixture"} for s in symbols]
    (tmp_path / "result.json").write_text('{"verdict":"PASS"}')
    (tmp_path / "catalog.json").write_text(json.dumps({"complete": True, "rows": catalog}))
    c = config()
    c["universe"] = symbols
    c["strategy"].update(name="v3", result_file="result.json", catalog_file="catalog.json")
    snap = Snapshot(now, {s: 100. for s in symbols}, {s: 1e8 for s in symbols}, {s: now for s in symbols}, panel, "fixture")
    expected = decide(panel, pd.DataFrame(catalog), pd.Timestamp(now) - pd.Timedelta(days=2), catalog_complete=True)
    assert StrategyRunner(c, tmp_path).targets(snap, Portfolio.create(1000)) == expected.targets


class FixtureFeed:
    def __init__(self, rows=(), error=None):
        self.rows, self.error, self.book_calls = rows, error, 0

    async def snapshots(self):
        for row in self.rows:
            yield row
        if self.error:
            raise self.error

    async def order_book(self, symbol, snap):
        self.book_calls += 1
        return OrderBook(((snap.prices[symbol], 10000),), ((snap.prices[symbol], 10000),), snap.timestamp, "fixture")


def test_resume_does_not_duplicate_rebalance_and_logs_full_state(tmp_path):
    feed = FixtureFeed([snapshot(), snapshot(NOW + timedelta(minutes=15))])
    state, logs = tmp_path / "state.json", tmp_path / "logs"
    engine = PaperEngine(config(), feed, state, logs)
    assert asyncio.run(engine.run()) == 0
    assert feed.book_calls == 1
    restored = PaperEngine(config(), feed, state, logs)
    assert asyncio.run(restored.run()) == 0
    assert feed.book_calls == 1
    events = [json.loads(line) for line in next(logs.glob("*.jsonl")).read_text().splitlines()]
    assert sum(len(e.get("trades", [])) for e in events) == 1
    assert all("positions" in e["portfolio"] and "equity" in e["portfolio"] for e in events)


@pytest.mark.parametrize("outage", ["disconnect", "stale"])
def test_outage_halts_and_restart_never_calls_feed(tmp_path, outage):
    bad = snapshot()
    if outage == "stale":
        bad.price_times["BTCUSDT"] = NOW - timedelta(minutes=3)
    feed = FixtureFeed([bad] if outage == "stale" else [], RuntimeError("API disconnected") if outage == "disconnect" else None)
    state, logs = tmp_path / "state.json", tmp_path / "logs"
    engine = PaperEngine(config(), feed, state, logs)
    assert asyncio.run(engine.run()) == 1
    assert json.loads(state.read_text())["portfolio"]["halt_reason"]
    restored = PaperEngine(config(), FixtureFeed([snapshot()]), state, logs)
    assert asyncio.run(restored.run()) == 2
    assert restored.feed.book_calls == 0


def test_unclosed_bar_rejected_before_strategy_or_broker(tmp_path):
    snap = snapshot()
    snap.history.loc[0, "date"] = pd.Timestamp(NOW)
    engine = PaperEngine(config(), FixtureFeed([snap]), tmp_path / "state", tmp_path / "logs")
    assert asyncio.run(engine.run()) == 1
    assert not engine.portfolio.positions


def test_stale_volume_cannot_assign_a_trade_tier(tmp_path):
    c = config()
    c["initial_cash"] = 1000
    c["strategy"]["name"] = "observe"
    snap = snapshot()
    snap.history["date"] -= pd.Timedelta(days=1)
    engine = PaperEngine(c, FixtureFeed([snap]), tmp_path / "state", tmp_path / "logs")
    engine.portfolio.fill("BTCUSDT", 2, 100, 0, 100)
    engine.portfolio.marks["BTCUSDT"] = 100
    assert asyncio.run(engine.run()) == 1
    assert "stale daily volume" in engine.portfolio.halt_reason
    assert engine.portfolio.positions["BTCUSDT"].quantity == 2


def test_process_lock_excludes_second_writer(tmp_path):
    with single_instance(tmp_path / "state.json"):
        with pytest.raises(RuntimeError, match="another paper process"):
            with single_instance(tmp_path / "state.json"):
                pass


def test_parquet_replay_only_exposes_closed_history(tmp_path):
    times = pd.date_range("2022-01-01", periods=38 * 96, freq="15min", tz="UTC")
    bars = pd.DataFrame({"timestamp": times, "open": 100., "close": 100., "volume": 1000.})
    path = tmp_path / "market.parquet"
    bars.to_parquet(path)

    async def exercise():
        feed = ParquetFeed(path, "BTCUSDT", "2022-02-07T00:00:00Z", "2022-02-08T00:00:00Z")
        rows = []
        async for snap in feed.snapshots():
            assert (snap.history.date < pd.Timestamp(snap.timestamp).normalize()).all()
            assert snap.volume24["BTCUSDT"] == 96 * 1000 * 100
            rows.append(snap)
        engine = PaperEngine(config(), FixtureFeed(rows), tmp_path / "state", tmp_path / "logs")
        assert await engine.run() == 0
        assert engine.portfolio.positions["BTCUSDT"].quantity > 0
        assert engine.portfolio.snapshot()["weights"]["BTCUSDT"] <= .1 + 1e-10
        return rows

    rows = asyncio.run(exercise())
    assert len(rows) == 96


def test_public_websocket_protocol_over_local_socket():
    from websockets.asyncio.server import serve

    async def exercise():
        now = datetime.now(timezone.utc)
        async def handler(socket):
            await socket.send(json.dumps([{"s": "BTCUSDT", "c": "123", "q": "9000000", "E": int(now.timestamp() * 1000)}]))
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            feed = BinancePublicFeed(["BTCUSDT"])
            feed.WS = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            async def bootstrap():
                feed.prices, feed.volumes, feed.times = {"BTCUSDT": 100.}, {"BTCUSDT": 6e6}, {"BTCUSDT": now}
                feed.history, feed.history_day = history(now=now), now.strftime("%Y-%m-%d")
            feed._bootstrap = bootstrap
            iterator = feed.snapshots()
            await anext(iterator)
            tick = await anext(iterator)
            assert tick.prices["BTCUSDT"] == 123
            assert tick.volume24["BTCUSDT"] == 9e6
            await iterator.aclose()

    asyncio.run(exercise())
