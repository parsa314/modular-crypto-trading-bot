"""Private-path verification uses a deterministic fake venue, never API keys."""
from dataclasses import replace
from types import SimpleNamespace
import copy

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("gymnasium")

from research_bot.ensemble_env import TradingEnvConfig
from research_bot.live_exchange import BelowMinimumOrder, Quote
from research_bot.live_ledger import LiveLedger
from research_bot.live_runtime import (LiveConfig, LiveController, SafetyStop,
                                      order_plan, risk_marks, settlement_matches)


BAR = 14_400_000
ORIGIN = 1_750_000_000_000


class Policy:
    fingerprint = "fixture-model"
    config = TradingEnvConfig()
    synthetic = False
    bar_seconds = BAR / 1000
    market_identity = {"exchange_id": "coinex", "symbol": "BTC/USDT", "timeframe": "4h"}

    def __init__(self, action=1):
        self.action, self.calls, self.callback = action, [], None

    def observation(self, history, **account):
        self.calls.append((copy.deepcopy(history), account))
        return np.zeros((30, 26), dtype=np.float32)

    def predict(self, observation):
        if self.callback:
            self.callback()
        return self.action


class Venue:
    exchange_id, symbol = "coinex", "BTC/USDT"

    def __init__(self, clock, mode="live"):
        self.mode, self.clock = mode, clock
        self.balances = {"base_total": 0., "base_free": 0., "quote_total": 100., "quote_free": 100., "other_total": {}}
        self.frame = pd.DataFrame({"timestamp": pd.to_datetime(np.arange(301)*BAR+ORIGIN, unit="ms", utc=True),
            "open": 100., "high": 101., "low": 99., "close": 100., "volume": 1000.})
        self.n = 299
        self.prices, self.orders, self.receipts = [], [], {}
        self.open_orders = []
        self.balance_calls, self.quote_calls, self.find_calls = 0, 0, 0
        self.prepare_hook, self.balance_hook = None, None
        self.fail_submit, self.partial, self.below_minimum = False, False, False
        self.hidden_receipt = False

    def fetch_quote(self, now, max_age):
        self.quote_calls += 1
        price = self.prices.pop(0) if self.prices else 100.
        if isinstance(price, Exception):
            raise price
        return Quote(price, price, now)

    def fetch_closed_candles(self, timeframe, limit, now):
        return self.frame.iloc[self.n-limit:self.n].copy()

    def fetch_balances(self):
        if self.mode == "dry-run":
            raise AssertionError("dry-run private balance request")
        self.balance_calls += 1
        if self.balance_hook:
            self.balance_hook(self.balance_calls)
        return copy.deepcopy(self.balances)

    def fetch_account_open_orders(self):
        if self.mode == "dry-run":
            raise AssertionError("dry-run private order request")
        return copy.deepcopy(self.open_orders)

    def prepare_order(self, side, quantity, price, ceiling):
        if self.below_minimum:
            raise BelowMinimumOrder("below venue minimum")
        if self.prepare_hook:
            self.prepare_hook()
        return {"symbol": self.symbol, "side": side, "amount": quantity, "price": price, "max_order_quote": ceiling}

    def submit_order(self, intent, client_id):
        self.orders.append(copy.deepcopy(intent))
        filled = intent["amount"] * (.5 if self.partial else 1)
        cost = filled*intent["price"]
        fee = cost*.001
        sign = 1 if intent["side"] == "buy" else -1
        self.balances["base_total"] += sign*filled
        self.balances["base_free"] = self.balances["base_total"]
        self.balances["quote_total"] -= sign*cost+fee
        self.balances["quote_free"] = self.balances["quote_total"]
        receipt = {"id": str(len(self.orders)), "clientOrderId": client_id, "symbol": self.symbol,
            "side": intent["side"], "amount": intent["amount"], "filled": filled,
            "remaining": intent["amount"]-filled, "status": "PARTIAL" if self.partial else "FILLED",
            "cost": cost, "fees": [{"currency": "USDT", "cost": fee}]}
        self.receipts[client_id] = receipt
        if self.fail_submit:
            raise TimeoutError("accepted before disconnect")
        return copy.deepcopy(receipt)

    def find_order(self, client_id, known_id=None):
        self.find_calls += 1
        return None if self.hidden_receipt else copy.deepcopy(self.receipts.get(client_id))


@pytest.fixture
def harness(tmp_path):
    config = LiveConfig("coinex", "BTC/USDT", "4h", 100, 35, .05, .12,
                        kill_file=str(tmp_path/"STOP"))
    clock = SimpleNamespace(now=ORIGIN+299*BAR+5000)
    venue, policy = Venue(clock), Policy()
    with LiveLedger(tmp_path/"live.db", {"mode": "live", "model": policy.fingerprint}) as ledger:
        controller = LiveController(venue, ledger, policy, config, mode="live", clock_ms=lambda: clock.now)
        yield SimpleNamespace(config=config, clock=clock, venue=venue, policy=policy, ledger=ledger, controller=controller)


def fund_position(h, *, cash=65., quantity=.35):
    h.policy.action = 0
    assert h.controller.cycle()["status"] == "NO_ORDER"
    h.venue.balances.update(quote_total=cash, quote_free=cash, base_total=quantity, base_free=quantity)
    state = h.ledger.get_state()
    state.update(account={"base_total": quantity, "quote_total": cash}, target_weight=.35)
    h.ledger.set_state(state)
    h.venue.n += 1
    h.clock.now += BAR


def test_live_submit_then_restart_does_not_repeat_same_bar(harness):
    h = harness
    first = h.controller.cycle()
    assert first["status"] == "ORDER_RECORDED"
    assert len(h.venue.orders) == 1
    # New controller simulates process restart using the durable ledger.
    restarted = LiveController(h.venue, h.ledger, h.policy, h.config, mode="live", clock_ms=lambda: h.clock.now)
    assert restarted.cycle()["reason"] == "BAR_ALREADY_CONSUMED"
    assert len(h.venue.orders) == 1
    assert not h.ledger.get_state().get("settlement_pending")


def test_accept_then_timeout_reconciles_without_duplicate(harness):
    h = harness
    h.venue.fail_submit = True
    assert h.controller.cycle()["reason"] == "SUBMIT_UNRESOLVED"
    assert h.ledger.pending_orders()[0]["state"] == "UNKNOWN"
    h.venue.hidden_receipt = True
    assert h.controller.cycle()["reason"] == "SUBMIT_UNRESOLVED"
    h.venue.hidden_receipt = False
    assert h.controller.cycle()["reason"] == "BAR_ALREADY_CONSUMED"
    assert len(h.venue.orders) == 1


def test_crash_after_venue_accept_before_local_ack_is_recovered(harness, monkeypatch):
    h = harness
    update = h.ledger.update_order
    monkeypatch.setattr(h.ledger, "update_order", lambda *args: (_ for _ in ()).throw(SystemExit(99)))
    with pytest.raises(SystemExit):
        h.controller.cycle()
    assert h.ledger.pending_orders()[0]["state"] == "SUBMITTING"
    monkeypatch.setattr(h.ledger, "update_order", update)
    assert h.controller.cycle()["reason"] == "BAR_ALREADY_CONSUMED"
    assert len(h.venue.orders) == 1


def test_partial_blocks_until_terminal_and_settled(harness):
    h = harness
    h.venue.partial = True
    first = h.controller.cycle()
    assert first["order_status"] == "PARTIAL"
    assert h.controller.cycle()["reason"] == "ORDER_PENDING"
    h.venue.receipts[first["client_order_id"]]["status"] = "CANCELLED"
    assert h.controller.cycle()["reason"] == "BAR_ALREADY_CONSUMED"
    assert len(h.venue.orders) == 1
    assert h.ledger.get_state()["account"]["base_total"] > 0


def test_terminal_missing_cost_refreshes_later(harness):
    h = harness
    submit = h.venue.submit_order
    def incomplete(*args):
        receipt = submit(*args)
        receipt["cost"] = None
        return receipt
    h.venue.submit_order = incomplete
    h.controller.cycle()
    assert h.controller.cycle()["reason"] == "BAR_ALREADY_CONSUMED"
    assert h.venue.find_calls == 1
    assert len(h.venue.orders) == 1


@pytest.mark.parametrize("tamper", ["size", "side", "cost", "client", "symbol"])
def test_recovered_receipt_must_match_reserved_intent(harness, tamper):
    h = harness
    h.venue.fail_submit = True
    first = h.controller.cycle()
    receipt = h.venue.receipts[first["client_order_id"]]
    if tamper == "size":
        receipt.update(amount=receipt["amount"]*10, filled=receipt["filled"]*10, cost=receipt["cost"]*10)
    elif tamper == "cost":
        receipt["cost"] *= 1.1
    else:
        receipt[{"side": "side", "client": "clientOrderId", "symbol": "symbol"}[tamper]] = "WRONG"
    assert h.controller.cycle()["reason"] == "RECONCILIATION_UNAVAILABLE"
    assert h.ledger.pending_orders()[0]["state"] == "UNKNOWN"
    assert len(h.venue.orders) == 1


def test_fresh_price_drop_cannot_buy_through_loss_limit(harness):
    h = harness
    fund_position(h)
    h.policy.action = 1
    h.venue.prices = [100., 50.]
    result = h.controller.cycle()
    assert result["status"] == "ORDER_RECORDED"
    assert h.venue.orders[-1]["side"] == "sell"
    assert h.ledger.get_state()["halted"]
    assert h.ledger.get_state()["target_weight"] == 0


def test_fresh_price_rise_recomputes_absolute_cap(harness):
    h = harness
    fund_position(h, quantity=.1, cash=90)
    h.policy.action = 1
    h.venue.prices = [100., 200.]
    h.controller.cycle()
    intent = h.venue.orders[-1]
    assert intent["side"] == "buy"
    assert (h.venue.balances["base_total"]*200) <= 35 + 1e-8
    assert h.ledger.get_state()["target_weight"] <= 35/110


def test_consumed_bar_can_trim_exposure_without_second_model_decision(harness):
    h = harness
    fund_position(h)
    h.venue.n -= 1
    h.clock.now -= BAR
    calls = len(h.policy.calls)
    h.venue.prices = [200., 200.]
    assert h.controller.cycle()["status"] == "ORDER_RECORDED"
    assert h.venue.orders[-1]["side"] == "sell"
    assert len(h.policy.calls) == calls
    assert h.venue.balances["base_total"]*200 <= 35 + 1e-8


def test_intrabar_trim_price_reversal_never_adds_exposure(harness):
    h = harness
    fund_position(h)
    h.venue.prices = [110., 100.]
    assert h.controller.cycle()["reason"] == "RISK_REDUCTION_NO_LONGER_NEEDED"
    assert not h.venue.orders
    assert h.ledger.get_state()["target_weight"] == .35


def test_partial_intrabar_trim_reconciles_before_next_unique_sell(harness):
    h = harness
    fund_position(h)
    h.venue.n -= 1
    h.clock.now -= BAR
    h.venue.partial = True
    h.venue.prices = [200., 200.]
    first = h.controller.cycle()
    assert h.controller.cycle()["reason"] == "ORDER_PENDING"
    h.venue.receipts[first["client_order_id"]]["status"] = "CANCELLED"
    h.clock.now += 10_000
    h.venue.prices = [200., 200.]
    second = h.controller.cycle()
    assert second["status"] == "ORDER_RECORDED"
    assert second["client_order_id"] != first["client_order_id"]
    assert [row["side"] for row in h.venue.orders] == ["sell", "sell"]


def test_new_cvar_risk_is_enforced_outside_strategy_entry_window(harness):
    h = harness
    fund_position(h)
    future = pd.DataFrame({"timestamp": pd.to_datetime(np.arange(299, 309)*BAR+ORIGIN, unit="ms", utc=True),
        "open": 100., "high": 101., "low": 79., "close": [100., 80.]*5, "volume": 1000.})
    h.venue.frame = pd.concat([h.venue.frame.iloc[:299], future], ignore_index=True)
    h.venue.n = 309
    h.clock.now = ORIGIN+309*BAR+66_000
    h.controller.config = replace(h.config, max_daily_loss=.10)
    h.venue.prices = [80., 80.]
    calls = len(h.policy.calls)
    assert h.controller.cycle()["status"] == "ORDER_RECORDED"
    assert h.venue.orders[-1]["side"] == "sell"
    assert len(h.policy.calls) == calls
    assert h.ledger.get_state()["cvar_cap"] == pytest.approx(.175)


def test_terminal_refresh_preserves_previously_reported_execution_cost(harness):
    h = harness
    first = h.controller.cycle()
    original = h.ledger.get_order(first["client_order_id"])["result"]
    h.venue.receipts[first["client_order_id"]].update(cost=None, fees=None)
    assert h.controller.cycle()["reason"] == "BAR_ALREADY_CONSUMED"
    latest = h.ledger.get_order(first["client_order_id"])["result"]
    assert latest["cost"] == original["cost"]
    assert latest["fees"] == original["fees"]


def test_growing_fill_cannot_inherit_partial_cost_or_fees(harness):
    h = harness
    h.venue.partial = True
    first = h.controller.cycle()
    receipt = h.venue.receipts[first["client_order_id"]]
    receipt.update(status="FILLED", filled=receipt["amount"], remaining=0., cost=None, fees=None)
    assert h.controller.cycle()["reason"] == "BALANCE_SETTLEMENT_PENDING"
    stored = h.ledger.get_order(first["client_order_id"])["result"]
    assert stored["cost"] is stored["fees"] is None
    assert len(h.venue.orders) == 1


def test_stale_or_wide_book_cannot_send(harness):
    h = harness
    h.venue.prices = [RuntimeError("stale public quote")]
    with pytest.raises(RuntimeError):
        h.controller.cycle()
    h.venue.fetch_quote = lambda now, age: Quote(99., 101., now)
    assert h.controller.cycle()["reason"] == "SPREAD_LIMIT"
    assert not h.venue.orders


def test_open_order_appearing_during_inference_prevents_submit(harness):
    h = harness
    h.policy.callback = lambda: h.venue.open_orders.append({"symbol": "ETH/USDT"})
    assert h.controller.cycle()["reason"] == "UNRECONCILED_OPEN_ORDER"
    assert not h.venue.orders


def test_entry_expiring_during_inference_consumes_bar_without_order(harness):
    h = harness
    h.policy.callback = lambda: setattr(h.clock, "now", h.clock.now+61_000)
    assert h.controller.cycle()["reason"] == "ENTRY_WINDOW_EXPIRED"
    assert not h.venue.orders
    assert h.ledger.get_state()["last_bar_ms"] == int(h.venue.frame.timestamp.iloc[298].value//1_000_000)


@pytest.mark.parametrize("during", ["balances", "prepare"])
def test_quote_expiring_before_send_is_not_sent(harness, during):
    h = harness
    def expire():
        h.clock.now += 11_000
    if during == "prepare":
        h.venue.prepare_hook = expire
    else:
        h.venue.balance_hook = lambda calls: expire() if calls == 2 else None
    assert h.controller.cycle()["reason"] == "QUOTE_EXPIRED_BEFORE_SUBMIT"
    assert not h.venue.orders


def test_kill_arriving_after_prepare_rejects_buy_and_latches(harness):
    from pathlib import Path
    h = harness
    h.venue.prepare_hook = lambda: Path(h.config.kill_file).touch()
    assert h.controller.cycle()["status"] == "HALTED"
    assert not h.venue.orders
    assert h.ledger.get_state()["halted"]
    Path(h.config.kill_file).unlink()
    assert h.controller.cycle()["status"] == "HALTED"


def test_risk_exit_is_capped_and_continues_after_reconciliation(harness):
    from pathlib import Path
    h = harness
    fund_position(h)
    h.controller.config = replace(h.config, max_order_quote=10)
    Path(h.config.kill_file).touch()
    assert h.controller.cycle()["status"] == "ORDER_RECORDED"
    assert h.venue.orders[-1]["side"] == "sell"
    assert h.venue.balances["base_total"] > 0
    h.clock.now += 10_000
    assert h.controller.cycle()["status"] == "ORDER_RECORDED"
    assert len(h.venue.orders) == 2
    assert all(row["amount"]*row["price"] <= 10 for row in h.venue.orders)


def test_below_minimum_consumes_bar_and_preserves_residual(harness):
    h = harness
    h.venue.below_minimum = True
    assert h.controller.cycle()["reason"] == "BELOW_VENUE_MINIMUM"
    assert h.controller.cycle()["reason"] == "BAR_ALREADY_CONSUMED"
    assert not h.venue.orders


@pytest.mark.parametrize("change", ["deposit", "third_asset", "open_order"])
def test_unowned_account_change_latches_without_trade(harness, change):
    h = harness
    h.policy.action = 0
    h.controller.cycle()
    if change == "deposit":
        h.venue.balances.update(quote_total=101., quote_free=101.)
    elif change == "third_asset":
        h.venue.balances["other_total"] = {"BNB": 1.}
    else:
        h.venue.open_orders = [{"id": "external"}]
    assert h.controller.cycle()["status"] == "BLOCKED"
    assert h.ledger.get_state()["halted"]
    assert not h.venue.orders


def test_shared_initial_holdings_are_rejected(harness):
    h = harness
    h.venue.balances.update(base_total=.1, base_free=.1)
    with pytest.raises(SafetyStop, match="DEDICATED_FLAT"):
        h.controller.cycle()
    assert not h.venue.orders


def test_dry_run_has_no_private_calls_and_no_order_claim(tmp_path):
    clock = SimpleNamespace(now=ORIGIN+299*BAR+5000)
    venue = Venue(clock, "dry-run")
    policy = Policy()
    config = LiveConfig("coinex", "BTC/USDT", "4h", 100, 35, .05, .12, kill_file=str(tmp_path/"stop"))
    with LiveLedger(tmp_path/"preview.db", {"mode": "dry-run"}) as ledger:
        controller = LiveController(venue, ledger, policy, config, clock_ms=lambda: clock.now)
        assert controller.cycle()["status"] == "PREVIEW"
        assert not ledger.pending_orders()
        assert not venue.orders
        assert venue.balance_calls == venue.find_calls == 0


def test_immutable_history_retains_ema_origin_and_unseen_closed_peak(harness):
    h = harness
    fund_position(h)
    h.venue.frame.loc[299, ["open", "close", "high", "low"]] = [200, 200, 201, 199]
    h.venue.n = 301
    h.clock.now = ORIGIN+301*BAR+5000
    h.controller.cycle()
    history = h.ledger.candles()
    assert len(history) == 301
    assert history[0]["timestamp_ms"] == ORIGIN
    assert h.ledger.get_state()["model_peak_equity"] == pytest.approx(135)
    assert h.ledger.get_state()["halted"]


def test_closed_peak_is_marked_before_trim_and_not_revalued_after_fill(harness):
    h = harness
    fund_position(h)
    # A smaller position allows the closed peak to remain inside DD limits,
    # while the latest causal tail limit still demands a risk reduction.
    h.venue.balances.update(base_total=.1, base_free=.1, quote_total=90., quote_free=90.)
    state = h.ledger.get_state()
    state["account"] = {"base_total": .1, "quote_total": 90.}
    h.ledger.set_state(state)
    h.policy.config = replace(h.policy.config, max_cvar=.01)
    future = pd.DataFrame({"timestamp": pd.to_datetime(np.arange(299, 303)*BAR+ORIGIN, unit="ms", utc=True),
        "open": 100., "high": 201., "low": 99., "close": [200., 100.]*2, "volume": 1000.})
    h.venue.frame = pd.concat([h.venue.frame.iloc[:299], future], ignore_index=True)
    h.venue.n = 303
    h.clock.now = ORIGIN+303*BAR+5000
    first = h.controller.cycle()
    assert first["status"] == "ORDER_RECORDED"
    assert h.venue.orders[-1]["side"] == "sell"
    assert h.ledger.get_state()["model_peak_equity"] == pytest.approx(110)
    assert not h.ledger.get_state().get("halted")
    h.policy.action = 0
    h.controller.cycle()
    assert h.policy.calls[-1][1]["model_peak"] == pytest.approx(110)


def test_daily_anchor_survives_midnight_and_halt_is_sticky(harness):
    cfg = harness.config
    before = int(pd.Timestamp("2026-10-03T23:59:59Z").timestamp()*1000)
    state = risk_marks({}, 100, before, cfg)
    state = risk_marks(state, 90, before+2000, cfg)
    assert state["day_start_equity"] == 100
    assert state["halted"]
    assert "MAX_DAILY_LOSS" in state["halt_reason"]
    assert risk_marks(state, 110, before+86_400_000, cfg)["halted"]


@pytest.mark.parametrize("reported", [False, True])
def test_combined_base_quote_fees_cannot_exceed_one_ceiling(harness, reported):
    cfg = harness.config
    previous = {"base_total": 0., "quote_total": 1000.}
    actual = {"base_total": .998, "quote_total": 899.8}
    result = {"filled": 1., "cost": 100., "fees": None if not reported else [
        {"currency": "BTC", "cost": .002}, {"currency": "USDT", "cost": .2}]}
    assert not settlement_matches(actual, previous, {"side": "buy"}, result, cfg)
    actual = {"base_total": .999, "quote_total": 899.9}
    result["fees"] = None if not reported else [{"currency": "BTC", "cost": .001}, {"currency": "USDT", "cost": .1}]
    assert settlement_matches(actual, previous, {"side": "buy"}, result, cfg)


def test_post_cost_plan_respects_cash_and_target(harness):
    cfg = harness.config
    plan = order_plan(cfg, Quote(100, 100, 0), {"base_total": 0., "quote_total": 100., "base_free": 0., "quote_free": 100.}, .35)
    spent = plan["quantity"] * plan["limit_price"] * (1+cfg.max_fee_bps/10000)
    assert spent <= 100
    equity = 100-spent+plan["quantity"]*100
    assert plan["quantity"]*100/equity == pytest.approx(.35)


@pytest.mark.parametrize("field,value", [("capital_limit_quote", True), ("max_daily_loss", 1),
    ("max_drawdown", float("nan")), ("max_order_quote", 101), ("poll_seconds", 14_400),
    ("entry_grace_seconds", 14_400), ("max_fee_bps", 1000)])
def test_invalid_config_is_rejected(harness, field, value):
    with pytest.raises(ValueError):
        replace(harness.config, **{field: value})


def test_mode_market_and_synthetic_model_gates(harness):
    h = harness
    with pytest.raises(SafetyStop, match="EXECUTION_IDENTITY"):
        LiveController(h.venue, h.ledger, h.policy, h.config, mode="testnet")
    h.policy.synthetic = True
    with pytest.raises(SafetyStop, match="SYNTHETIC"):
        LiveController(h.venue, h.ledger, h.policy, h.config, mode="live")
    h.policy.synthetic = False
    h.policy.market_identity = None
    with pytest.raises(SafetyStop, match="MARKET_IDENTITY"):
        LiveController(h.venue, h.ledger, h.policy, h.config, mode="live")


@pytest.mark.parametrize("field,value", [("lookback", 223), ("cvar_window", 299)])
def test_unsupported_initial_history_is_rejected_before_cycle(harness, field, value):
    h = harness
    h.policy.config = replace(h.policy.config, **{field: value})
    with pytest.raises(SafetyStop, match="HISTORY_EXCEEDS"):
        LiveController(h.venue, h.ledger, h.policy, h.config, mode="live")


def test_stop_path_is_bound_to_original_working_directory(harness, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = replace(harness.config, kill_file="relative-stop")
    assert cfg.kill_file == str(tmp_path/"relative-stop")
    other = tmp_path/"other"
    other.mkdir()
    monkeypatch.chdir(other)
    assert replace(harness.config, kill_file="relative-stop").kill_file != cfg.kill_file
