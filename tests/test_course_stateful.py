"""Deterministic offline fixtures, no API key or exchange dependency."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from research_bot.course.contracts import (
    BookSnapshot,
    MarketMetadata,
    UnsupportedCapability,
)
from research_bot.course.simulation import OfflineOrderSimulator, SimOrderRequest
from research_bot.course.stateful import (
    ArbitrageReplayPlan,
    ConversionLeg,
    GridConfig,
    GridLevel,
    GridStrategyStateMachine,
    TriangularArbitrageEngine,
    quote_conversion,
)

NOW = datetime(2026, 10, 10, tzinfo=UTC)


def market(base="BTC", quote="USDT", step="0.01"):
    return MarketMetadata("FIXTURE", base + "/" + quote, base, quote, D("0.01"), D(step),
                          D("0.01"), "TOMAN" if quote == "IRT" else quote)


def book(metadata=None, bid="100", ask="101", depth="100", *, sequence="1", time=NOW):
    return BookSnapshot(metadata or market(), time, time, time, time, sequence,
                        ((D(bid), D(depth)),), ((D(ask), D(depth)),), "synthetic-fixture-" + sequence)


def order(sim, identity="o", side="BUY", qty="0.10", snapshot=None, **kwargs):
    snapshot = snapshot or book()
    request = SimOrderRequest(identity, snapshot.market, side, D(qty), **kwargs)
    sim.create(request, NOW)
    sim.approve(identity, snapshot, NOW, risk_approved=True)
    sim.submit(identity, NOW)
    return request


def triangle(fee="0.01"):
    return (ConversionLeg(book(market("USDT", "IRT", "0.01"), bid="9.9", ask="10"), "BUY", D(fee)),
            ConversionLeg(book(market("BTC", "USDT", "0.001"), bid="1.9", ask="2"), "BUY", D(fee)),
            ConversionLeg(book(market("BTC", "IRT", "0.001"), bid="22", ask="23"), "SELL", D(fee)))


def grid(sim=None):
    sim = sim or OfflineOrderSimulator({"USDT": D(100), "BTC": D("0.2")})
    config = GridConfig(range_preregistration_ref="SYNTHETIC_TEST_PROTOCOL", breakout_lower=D(80),
                        breakout_upper=D(130), max_inventory_base=D(1))
    result = GridStrategyStateMachine(sim, config)
    result.prepare(book(), (GridLevel("BUY", D("0.1"), D(95)), GridLevel("SELL", D("0.1"), D(110))),
                   NOW, range_confirmed=True, range_evidence_ref="RANGE_FIXTURE",
                   range_evidence_available_at=NOW)
    return result


def test_research_simulator_has_no_transport_and_approval_required():
    sim = OfflineOrderSimulator({"USDT": D(100)})
    assert sim.mode == "OFFLINE_REPLAY_NOT_PAPER"
    assert not hasattr(sim, "place_order") and not hasattr(sim, "transport")
    sim.create(SimOrderRequest("o", market(), "BUY", D("0.1")), NOW)
    assert sim.approve("o", book(), NOW, risk_approved=False) == "REJECTED"
    assert sim.balances == {"USDT": D(100)}


def test_order_lifecycle_ack_is_not_fill_and_partial_depth_depleted():
    sim = OfflineOrderSimulator({"USDT": D(100)})
    limited = book(depth="0.05")
    order(sim, qty="0.1", snapshot=limited)
    assert sim.orders["o"]["state"] == "ACKNOWLEDGED"
    assert sim.balances["USDT"] == D(100)
    assert sim.fill("o", limited, NOW) == "PARTIALLY_FILLED"
    assert sim.orders["o"]["filled"] == D("0.05")
    assert sim.fill("o", limited, NOW) == "PARTIALLY_FILLED"
    assert sim.orders["o"]["filled"] == D("0.05")
    order(sim, "another", qty="0.01", snapshot=limited)
    assert sim.fill("another", limited, NOW) == "ACKNOWLEDGED"
    newer = book(depth="0.10", sequence="2")
    assert sim.fill("o", newer, NOW) == "FILLED"
    sim.assert_invariants()


def test_insufficient_balance_inventory_precision_and_invalid_money():
    sim = OfflineOrderSimulator({"USDT": D(1)})
    order(sim)
    assert sim.orders["o"]["state"] == "REJECTED"
    sell = OfflineOrderSimulator({"BTC": D("0.01")})
    order(sell, side="SELL", qty="0.1")
    assert sell.orders["o"]["state"] == "REJECTED"
    with pytest.raises(ValueError, match="PRECISION"):
        SimOrderRequest("bad", market(), "BUY", D("0.015"))
    for invalid in ("NaN", "Infinity", "-1"):
        with pytest.raises(ValueError):
            OfflineOrderSimulator({"USDT": D(invalid)})


def test_limit_requires_evidence_never_ohlc_touch_and_witness_not_reused():
    sim = OfflineOrderSimulator({"USDT": D(100)})
    order(sim, order_type="LIMIT", limit_price=D(95), fee_rate=D("0.01"), fee_currency="USDT")
    with pytest.raises(UnsupportedCapability, match="QUEUE"):
        sim.fill("o", book(), NOW)
    assert sim.fill("o", book(), NOW, queue_evidence_id="trade-confirmed", queue_fill_quantity=D("0.05")) == "PARTIALLY_FILLED"
    first = sim.export_state()
    sim.fill("o", book(), NOW, queue_evidence_id="trade-confirmed", queue_fill_quantity=D("0.05"))
    assert sim.export_state() == first
    assert sim.balances["USDT"] == D(100) - D("0.05") * D(95) * D("1.01")
    assert sim.balances["BTC"] == D("0.05")


def test_quote_and_output_fee_accounting_sell_bid_buy_ask():
    sim = OfflineOrderSimulator({"USDT": D(100), "BTC": D(1)})
    order(sim, fee_rate=D("0.01"))
    sim.fill("o", book(), NOW)
    assert sim.balances["USDT"] == D("89.9")
    assert sim.balances["BTC"] == D("1.099")
    order(sim, "sell", side="SELL", qty="0.1", fee_rate=D("0.01"))
    sim.fill("sell", book(), NOW)
    assert sim.balances["USDT"] == D("99.8")
    assert sim.balances["BTC"] == D("0.999")


def test_cancel_expiry_and_idempotent_order_collision_restart():
    sim = OfflineOrderSimulator({"USDT": D(100)})
    request = order(sim)
    count = len(sim.events)
    assert sim.create(request, NOW) == "ACKNOWLEDGED"
    assert len(sim.events) == count
    with pytest.raises(ValueError, match="COLLISION"):
        sim.create(SimOrderRequest("o", market(), "BUY", D("0.2")), NOW)
    restored = OfflineOrderSimulator.from_state(sim.export_state())
    assert restored.export_state() == sim.export_state()
    assert restored.cancel("o", NOW, expired=True) == "EXPIRED"
    assert restored.reserved["USDT"] == 0
    assert restored.fill("o", book(), NOW) == "EXPIRED"
    tampered = sim.export_state()
    tampered["payload"]["balances"]["USDT"] = "999"
    with pytest.raises(ValueError, match="INTEGRITY"):
        OfflineOrderSimulator.from_state(tampered)


def test_deterministic_replay_hash_and_wallet_conservation_multiple_partials():
    def replay():
        sim = OfflineOrderSimulator({"USDT": D(1000)})
        order(sim, qty="1", fee_rate=D("0.01"))
        for index in range(4):
            sim.fill("o", book(depth="0.25", sequence=str(index)), NOW)
            sim.assert_invariants()
        assert sim.balances["USDT"] + sim.orders["o"]["notional"] == 1000
        assert sim.balances["BTC"] + sim.orders["o"]["fees"]["BTC"] == 1
        return sim.export_state()
    assert replay() == replay()


def test_point_in_time_latency_stale_and_nonmonotonic_no_mutation():
    sim = OfflineOrderSimulator({"USDT": D(100)})
    order(sim, latency=timedelta(seconds=1))
    checkpoint = sim.export_state()
    with pytest.raises(ValueError, match="LATENCY"):
        sim.fill("o", book(), NOW)
    assert sim.export_state() == checkpoint
    with pytest.raises(ValueError, match="STALE"):
        sim.fill("o", book(), NOW + timedelta(seconds=11))
    with pytest.raises(ValueError, match="UNAVAILABLE"):
        sim.fill("o", book(time=NOW + timedelta(seconds=2)), NOW + timedelta(seconds=1))
    assert sim.export_state() == checkpoint
    with pytest.raises(ValueError, match="NONMONOTONIC"):
        sim.cancel("o", NOW - timedelta(seconds=1))
    assert sim.export_state() == checkpoint


def test_grid_full_capital_reservation_inventory_and_acknowledgement():
    item = grid()
    assert item.to_candidate().confidence is None
    assert item.to_candidate().to_signal_evidence().decision.value == "NO_TRADE"
    assert item.reserve(book(), NOW, risk_approved=True) == "RESERVED"
    assert item.simulator.reserved["USDT"] == D("9.5095")
    assert item.simulator.reserved["BTC"] == D("0.1")
    assert item.activate(NOW) == "ACTIVE"
    assert item.simulator.balances["BTC"] == D("0.2")
    assert all(item.simulator.orders[key]["filled"] == 0 for key in item.order_ids)


def test_grid_whole_plan_reservation_rolls_back_not_half_active():
    item = grid(OfflineOrderSimulator({"USDT": D(100), "BTC": D("0.01")}))
    before = item.simulator.export_state()
    with pytest.raises(ValueError, match="CAPITAL_OR_INVENTORY"):
        item.reserve(book(), NOW, risk_approved=True)
    assert item.state == "HALTED"
    assert item.simulator.export_state() == before


def test_grid_partial_inventory_conservation_recenter_and_regime_halt():
    item = grid()
    item.reserve(book(), NOW, risk_approved=True)
    item.activate(NOW)
    item.record_fill(item.order_ids[0], book(), NOW, queue_evidence_id="fill1", quantity=D("0.05"))
    assert item.state == "PARTIALLY_FILLED"
    assert item.simulator.balances["BTC"] == D("0.25")
    assert item.recenter(NOW) == "CANCELLED"
    assert item.simulator.reserved["USDT"] == 0 and item.simulator.reserved["BTC"] == 0
    assert any(e["state"] == "REBALANCING" for e in item.events)
    halted = grid()
    halted.reserve(book(), NOW, risk_approved=True)
    halted.activate(NOW)
    assert halted.check_market(book(), NOW, range_still_valid=False) == "HALTED"
    assert halted.simulator.reserved == {"USDT": D(0), "BTC": D(0)}


def test_grid_range_evidence_level_concentration_and_restart():
    with pytest.raises(ValueError, match="PREREGISTRATION"):
        GridConfig()
    item = grid()
    item.reserve(book(), NOW, risk_approved=True)
    item.activate(NOW)
    restart = GridStrategyStateMachine.from_state(item.export_state())
    assert restart.export_state() == item.export_state()
    assert restart.to_candidate() == item.to_candidate()
    restart.record_fill(restart.order_ids[0], book(), NOW, queue_evidence_id="allbuy", quantity=D("0.1"))
    restart.record_fill(restart.order_ids[1], book(), NOW, queue_evidence_id="allsell", quantity=D("0.1"))
    assert restart.state == "COMPLETED"
    with pytest.raises(ValueError, match="POINT_IN_TIME"):
        GridStrategyStateMachine(item.simulator, item.config).prepare(book(), (GridLevel("BUY", D("0.1"), D(95)),),
            NOW, range_confirmed=True, range_evidence_ref="future", range_evidence_available_at=NOW + timedelta(seconds=1))


def test_triangle_bid_ask_fee_units_depth_precision_residual_and_candidate():
    legs = triangle()
    engine = TriangularArbitrageEngine()
    hypothesis = engine.evaluate(legs, D(100), NOW)
    assert hypothesis.quotes[0].gross_output == D(10)
    assert hypothesis.quotes[0].net_output == D("9.90")
    assert hypothesis.quotes[1].net_output == D("4.9005")
    assert hypothesis.executable_proceeds == D("106.72200")
    assert hypothesis.expected_net_edge == D("6.62200")
    assert dict(hypothesis.residual_inventory)["BTC"] == D("0.0005")
    assert dict(hypothesis.fees_by_currency) == {"BTC": D("0.0495"), "IRT": D("1.078"), "USDT": D("0.10")}
    assert hypothesis.atomicity.atomic is False
    candidate = hypothesis.to_candidate()
    assert candidate.confidence is None and candidate.direction == 0
    assert candidate.to_signal_evidence().decision.value == "NO_TRADE"
    q = quote_conversion(ConversionLeg(legs[0].book, "SELL", D(0)), D(10))
    assert q.gross_output == D(99)


def test_triangle_graph_direction_depth_stale_skew_and_fee_rejection():
    engine = TriangularArbitrageEngine()
    legs = triangle()
    assert len(engine.discover_cycles(tuple(leg.book for leg in legs), "IRT", fee_rate=D("0.01"))) == 2
    with pytest.raises(ValueError, match="DIRECTION"):
        engine.evaluate((legs[1], legs[0], legs[2]), D(100), NOW)
    with pytest.raises(ValueError, match="INSUFFICIENT_DEPTH"):
        engine.evaluate((ConversionLeg(book(legs[0].book.market, bid="9.9", ask="10", depth="1"), "BUY", D(0)), *legs[1:]), D(100), NOW)
    with pytest.raises(ValueError, match="STALE"):
        engine.evaluate(legs, D(100), NOW + timedelta(seconds=11))
    skewed = ConversionLeg(book(legs[0].book.market, bid="9.9", ask="10", time=NOW - timedelta(seconds=2)), "BUY", D(0))
    with pytest.raises(ValueError, match="SKEW"):
        engine.evaluate((skewed, *legs[1:]), D(100), NOW)
    with pytest.raises(ValueError, match="FEE_CONVENTION"):
        ConversionLeg(legs[0].book, "BUY", D("0.001"), "IRT")


def test_triangle_actual_filled_size_three_legs_restart_and_no_secured_claim():
    engine = TriangularArbitrageEngine()
    hypothesis = engine.evaluate(triangle(), D(100), NOW)
    plan = ArbitrageReplayPlan(hypothesis, OfflineOrderSimulator({"IRT": D(100), "USDT": D(999)}))
    at = NOW + timedelta(seconds=1)
    assert plan.execute_leg(triangle()[0].book, at, risk_approved=True) == "AWAITING_NEXT_LEG"
    assert plan.amount == D("9.90")
    restart = ArbitrageReplayPlan.from_state(plan.export_state())
    assert restart.export_state() == plan.export_state()
    restart.execute_leg(triangle()[1].book, at + timedelta(seconds=1), risk_approved=True)
    restart.execute_leg(triangle()[2].book, at + timedelta(seconds=2), risk_approved=True)
    assert restart.state == "COMPLETED"
    assert restart.plan_inventory()["IRT"] == hypothesis.executable_proceeds
    assert restart.simulator.balances["USDT"] == 999
    assert not hasattr(restart, "profit_secured")


def test_triangle_failed_second_leg_quarantines_downstream_and_unwind_cost():
    hypothesis = TriangularArbitrageEngine().evaluate(triangle(), D(100), NOW)
    plan = ArbitrageReplayPlan(hypothesis, OfflineOrderSimulator({"IRT": D(500), "USDT": D(999)}))
    at = NOW + timedelta(seconds=1)
    plan.execute_leg(triangle()[0].book, at, risk_approved=True)
    assert plan.execute_leg(triangle()[1].book, at + timedelta(seconds=1), risk_approved=True, reject=True) == "HALTED"
    before = plan.export_state()
    assert plan.execute_leg(triangle()[2].book, at, risk_approved=True) == "HALTED"
    assert plan.export_state() == before
    unwind = plan.assess_unwind((ConversionLeg(triangle()[0].book, "SELL", D("0.01")),), at)
    assert unwind["status"] == "HYPOTHETICAL_UNWIND_NOT_EXECUTED"
    assert unwind["estimated_proceeds"] == D("97.0299")
    assert unwind["estimated_shortfall"] == D("2.9701")
    assert plan.simulator.balances["IRT"] == 400


def test_triangle_partial_leg_records_unhedged_inventory_and_does_not_follow_next():
    hypothesis = TriangularArbitrageEngine().evaluate(triangle(), D(100), NOW)
    plan = ArbitrageReplayPlan(hypothesis, OfflineOrderSimulator({"IRT": D(100)}))
    reduced = book(triangle()[0].book.market, bid="9.9", ask="10", depth="1")
    assert plan.execute_leg(reduced, NOW + timedelta(seconds=1), risk_approved=True) == "HALTED"
    assert plan.failure_reason == "PARTIAL_LEG_UNHEDGED_INVENTORY"
    assert plan.plan_inventory() == {"IRT": D(90), "USDT": D("0.99")}
    assert len(plan.order_ids) == 1


def test_limit_and_grid_replay_after_restart_conserves_fees_and_witnesses():
    item = grid()
    item.reserve(book(), NOW, risk_approved=True)
    item.activate(NOW)
    item.record_fill(item.order_ids[0], book(), NOW, queue_evidence_id="one", quantity=D("0.03"))
    clone = GridStrategyStateMachine.from_state(item.export_state())
    before = clone.export_state()
    clone.record_fill(clone.order_ids[0], book(), NOW, queue_evidence_id="one", quantity=D("0.03"))
    assert clone.export_state() == before
    assert clone.simulator.orders[clone.order_ids[0]]["filled"] == D("0.03")
    clone.record_fill(clone.order_ids[0], book(), NOW, queue_evidence_id="two", quantity=D("0.07"))
    clone.simulator.assert_invariants()
    assert clone.simulator.balances["BTC"] == D("0.3")


def test_reobserved_sequence_does_not_replenish_depth_or_allow_content_revision():
    from dataclasses import replace
    sim = OfflineOrderSimulator({"USDT": D(100)})
    snap = book(depth="0.05")
    order(sim, qty="0.1", snapshot=snap)
    sim.fill("o", snap, NOW)
    reobserved = replace(snap, observed_at=NOW + timedelta(seconds=1),
                         received_at=NOW + timedelta(seconds=1), available_at=NOW + timedelta(seconds=1))
    sim.fill("o", reobserved, NOW + timedelta(seconds=1))
    assert sim.orders["o"]["filled"] == D("0.05")
    checkpoint = sim.export_state()
    revised = replace(reobserved, asks=((D(101), D(999)),))
    with pytest.raises(ValueError, match="CONTENT_COLLISION"):
        sim.fill("o", revised, NOW + timedelta(seconds=1))
    assert sim.export_state() == checkpoint


def test_bad_tick_and_persisted_state_invariants_fail_closed():
    from dataclasses import replace

    from research_bot.course.contracts import digest
    sim = OfflineOrderSimulator({"USDT": D(100)})
    order(sim)
    with pytest.raises(ValueError, match="TICK"):
        sim.fill("o", replace(book(), asks=((D("101.001"), D(1)),)), NOW)
    artifact = sim.export_state()
    artifact["payload"]["orders"]["o"]["filled"] = "999"
    artifact["sha256"] = digest(artifact["payload"])
    with pytest.raises(ValueError, match="OVERFILLED"):
        OfflineOrderSimulator.from_state(artifact)
    artifact = sim.export_state()
    artifact["payload"]["events"][0]["event_id"] = "invented"
    artifact["sha256"] = digest(artifact["payload"])
    with pytest.raises(ValueError, match="SEQUENCE_INTEGRITY"):
        OfflineOrderSimulator.from_state(artifact)


def test_grid_inventory_breakout_level_limit_and_time_fail_closed():
    sim = OfflineOrderSimulator({"USDT": D(100), "BTC": D("0.99")})
    cfg = GridConfig(range_preregistration_ref="fixture", max_levels=1,
                     breakout_lower=D(80), breakout_upper=D(130), max_inventory_base=D(1))
    item = GridStrategyStateMachine(sim, cfg)
    with pytest.raises(ValueError, match="CONCENTRATION"):
        item.prepare(book(), (GridLevel("BUY", D("0.1"), D(95)),), NOW,
                     range_confirmed=True, range_evidence_ref="fixture", range_evidence_available_at=NOW)
    with pytest.raises(ValueError, match="LEVEL_LIMIT"):
        item.prepare(book(), (GridLevel("BUY", D("0.1"), D(95)), GridLevel("SELL", D("0.1"), D(110))), NOW,
                     range_confirmed=True, range_evidence_ref="fixture", range_evidence_available_at=NOW)
    active = grid()
    active.reserve(book(), NOW, risk_approved=True)
    active.activate(NOW)
    checkpoint = active.export_state()
    with pytest.raises(ValueError, match="NONMONOTONIC"):
        active.record_fill(active.order_ids[0], book(), NOW - timedelta(seconds=1),
                           queue_evidence_id="bad", quantity=D("0.1"))
    assert active.export_state() == checkpoint
    assert active.check_market(book(bid="79", ask="80"), NOW, range_still_valid=True) == "HALTED"
    active.simulator.assert_invariants()


def test_arbitrage_minimum_interleg_latency_and_single_risk_buffer():
    hypothesis = TriangularArbitrageEngine().evaluate(triangle(), D(100), NOW)
    candidate = hypothesis.to_candidate()
    assert candidate.expected_edge - candidate.uncertainty == hypothesis.expected_net_edge / hypothesis.initial_capital
    plan = ArbitrageReplayPlan(hypothesis, OfflineOrderSimulator({"IRT": D(100)}))
    at = NOW + timedelta(seconds=1)
    plan.execute_leg(triangle()[0].book, at, risk_approved=True)
    checkpoint = plan.export_state()
    with pytest.raises(ValueError, match="LATENCY"):
        plan.execute_leg(triangle()[1].book, at, risk_approved=True)
    assert plan.export_state() == checkpoint
