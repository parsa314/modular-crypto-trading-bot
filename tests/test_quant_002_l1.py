"""SIMULATED TEST fixtures only, never claims about real execution or markets."""
from datetime import datetime,timedelta,timezone
from decimal import Decimal,localcontext
import json
import hashlib
import unittest

from research_bot.quant_002_l1 import L1Bar, L1Simulator, CostScenario

T=datetime(2023,1,1,tzinfo=timezone.utc)
SHA="a"*64


def bar(hour,op="100",hi="101",lo="99",close="100",source=SHA):
    at=T+timedelta(hours=hour)
    return L1Bar(at,at+timedelta(hours=1),Decimal(op),Decimal(hi),Decimal(lo),Decimal(close),source)


def setup(at=T+timedelta(hours=1), key="fixture-1",stop="98"):
    source=lambda tf:{"raw_sha256":SHA,"timeframe":tf,"venue":"SYNTHETIC","instrument":"BTC/USDT","source_id":"SYNTHETIC_FIXTURE"}
    body={"version":"ICH_KIJUN_PULLBACK_V1","direction":"LONG","evidence_at":at.isoformat(),
        "market_regime":{"type":"H4_ABOVE_VISIBLE_CLOUD_TENKAN_ABOVE_KIJUN","fixture_key":key},
        "timeframe_context":{"h1_available_at":at.isoformat(),"h4_available_at":at.isoformat()},
        "trigger":{"type":"H1_KIJUN_TOUCH_RECLAIM_BULLISH","bar_low":stop,
                   "bar_open":(at-timedelta(hours=1)).replace(minute=0,second=0,microsecond=0).isoformat()},"confirmation":{"type":"H4_CLOUD_TK"},
        "invalidation":{"kind":"TRIGGER_BAR_LOW","level":stop},"stop_rule":{"kind":"TRIGGER_BAR_LOW","level":stop},
        "entry_rule":{"kind":"FIRST_H1_OPEN_STRICTLY_AFTER_DECISION","max_delay_hours":2},
        "target_rule":{"kind":"ENTRY_PLUS_R_MULTIPLE","multiple":"2"},
        "timeout":{"count":8,"unit":"COMPLETED_H1_BARS_FROM_ENTRY"},
        "cancellation_rule":["NO_ELIGIBLE_ENTRY_WITHIN_2H","ENTRY_AT_OR_BELOW_INVALIDATION","STALE_OR_INVALID_DATA","RISK_VETO","UNVERIFIED_EXECUTION_POLICY"],
        "source_provenance":{"H1":source("H1"),"H4":source("H4")}}
    identity=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
    return {"setup_id":identity,**body,"execution_eligible":False,"evidence_state":"RESEARCH_HYPOTHESIS_ONLY"}


def sim(mode="stop_first",cost="0",cash="10000"):
    return L1Simulator(CostScenario("FIXTURE",Decimal(cost),Decimal("0"),Decimal("0")),cash=Decimal(cash),
                       mode=mode,source_id="SYNTHETIC_FIXTURE",source_sha256=SHA)


def to_entry(s, *, entry=None):
    s.bar(bar(0,lo="98"));s.signal(setup());s.bar(bar(1));s.bar(entry or bar(2))


class L1Tests(unittest.TestCase):
    def test_no_fill_at_or_before_decision_and_one_position(self):
        s=sim();s.bar(bar(0,lo="98"));s.signal(setup());s.bar(bar(1))
        self.assertIsNone(s.state["position"])
        s.bar(bar(2)); self.assertIsNotNone(s.state["position"])
        fill=next(e for e in s.state["events"] if e["kind"]=="FILLED")
        self.assertGreater(datetime.fromisoformat(fill["event_at"]),T+timedelta(hours=1))
        self.assertEqual(fill["source_sha256"],SHA)
        self.assertFalse(s.signal(setup(at=T+timedelta(hours=3),key="other",stop="99")))

    def test_gap_through_stop_uses_gap_open_not_stop(self):
        s=sim();to_entry(s);s.bar(bar(3,"80","81","79","80"))
        self.assertEqual(s.state["trades"][0]["exit_reason"],"GAP_THROUGH_STOP")
        self.assertEqual(Decimal(s.state["trades"][0]["exit"]),Decimal("80"))

    def test_limit_touch_not_assumed_fill(self):
        s=sim();to_entry(s,entry=bar(2,"100","105","99","100"))
        self.assertIsNotNone(s.state["position"])
        self.assertTrue(any(e["kind"]=="TARGET_TOUCH_UNMODELED" for e in s.state["events"]))
        self.assertEqual(s.state["trades"],[])

    def test_assumed_limit_fill_never_executes_below_limit_after_cost(self):
        s=L1Simulator(CostScenario("24",Decimal(10),Decimal(6),Decimal(8)),
            mode="target_touch_assumed",source_id="SYNTHETIC_FIXTURE",source_sha256=SHA)
        to_entry(s,entry=bar(2,"100","110","99","100"))
        event=next(e for e in s.state["events"] if e["kind"]=="TARGET_TOUCH_UNMODELED")
        self.assertEqual(Decimal(s.state["trades"][0]["exit"]),Decimal(event["price"]))

    def test_ambiguous_stop_and_target_remain_labeled_and_separate(self):
        a,b=sim(),sim(mode="target_touch_assumed")
        for s in (a,b): to_entry(s,entry=bar(2,"100","110","97","100"))
        self.assertTrue(all(any(e["kind"]=="AMBIGUOUS" for e in s.state["events"]) for s in (a,b)))
        self.assertEqual(a.state["trades"][0]["exit_reason"],"AMBIGUOUS_STOP_BOUND")
        self.assertEqual(b.state["trades"][0]["exit_reason"],"AMBIGUOUS_TARGET_BOUND")
        self.assertLess(Decimal(a.state["trades"][0]["pnl"]),Decimal(b.state["trades"][0]["pnl"]))

    def test_missing_bar_is_fail_closed(self):
        s=sim();s.bar(bar(0));before=s.snapshot()
        with self.assertRaisesRegex(ValueError,"MISSING_OR_OUT_OF_ORDER_BAR"): s.bar(bar(2))
        self.assertEqual(before,s.snapshot())

    def test_extreme_decimal_failure_rolls_back_all_state(self):
        s=sim();s.bar(bar(0,lo="98"));s.signal(setup());s.bar(bar(1));before=s.snapshot()
        with self.assertRaises(ArithmeticError):
            s.bar(bar(2,"9e999999","9e999999","100","9e999999"))
        self.assertEqual(before,s.snapshot())

    def test_duplicate_bar_is_idempotent_conflict_fails(self):
        s=sim();self.assertTrue(s.bar(bar(0)));before=s.snapshot()
        self.assertFalse(s.bar(bar(0)))
        self.assertEqual(before,s.snapshot())
        with self.assertRaisesRegex(ValueError,"CONFLICTING_MARKET_EVENT"):s.bar(bar(0,close="100.5"))
        self.assertEqual(before,s.snapshot())

    def test_duplicate_and_conflicting_signal(self):
        s=sim();s.bar(bar(0,lo="98"));self.assertTrue(s.signal(setup()));before=s.snapshot()
        self.assertFalse(s.signal(setup()));self.assertEqual(before,s.snapshot())
        conflict=setup(stop="97");conflict["setup_id"]=setup()["setup_id"]
        with self.assertRaisesRegex(ValueError,"CONFLICTING_SIGNAL"):s.signal(conflict)

    def test_restart_is_identical_and_snapshot_tampering_rejected(self):
        a=sim();to_entry(a);b=L1Simulator.restore(a.snapshot())
        a.bar(bar(3,"80","81","79","80"));b.bar(bar(3,"80","81","79","80"))
        self.assertEqual(a.snapshot(),b.snapshot())
        value=json.loads(a.snapshot());value["payload"]["state"]["cash"]="1"
        with self.assertRaisesRegex(ValueError,"SNAPSHOT_MISMATCH"):L1Simulator.restore(json.dumps(value))

    def test_stale_signal_and_future_availability_rejected(self):
        s=sim();s.bar(bar(0,lo="98"))
        with self.assertRaisesRegex(ValueError,"STALE_OR_FUTURE_SIGNAL"):
            s.signal(setup(at=T+timedelta(hours=1,minutes=16)))
        with self.assertRaisesRegex(ValueError,"STALE_OR_FUTURE_SIGNAL"):
            s.signal(setup(at=T+timedelta(minutes=59)))

    def test_forged_identity_and_stale_h4_fail_closed(self):
        s=sim();s.bar(bar(0,lo="98"))
        candidate=setup();candidate["trigger"]["bar_low"]="97"
        with self.assertRaisesRegex(ValueError,"SETUP_IDENTITY_MISMATCH"):s.signal(candidate)
        old=setup();old["timeframe_context"]["h4_available_at"]=(T-timedelta(days=1)).isoformat()
        immutable={k:v for k,v in old.items() if k not in {"setup_id","execution_eligible","evidence_state"}}
        old["setup_id"]=hashlib.sha256(json.dumps(immutable,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
        with self.assertRaisesRegex(ValueError,"INVALID_FROZEN_SETUP"):s.signal(old)

    def test_delayed_market_data_cannot_decide_past_open(self):
        s=sim()
        delayed=L1Bar(T,T+timedelta(hours=1,minutes=10),Decimal(100),Decimal(101),Decimal(98),Decimal(100),SHA)
        s.bar(delayed)
        s.signal(setup(at=T+timedelta(hours=1,minutes=10)))
        before=s.snapshot()
        with self.assertRaisesRegex(ValueError,"LATE_BAR_OVERLAP"): s.bar(bar(1))
        self.assertEqual(before,s.snapshot())

    def test_incomplete_setup_cannot_enter(self):
        s=sim();s.bar(bar(0,lo="98"));candidate=setup();del candidate["confirmation"]
        with self.assertRaisesRegex(ValueError,"INVALID_SETUP_CONTRACT"):s.signal(candidate)

    def test_actual_frozen_strategy_contract_is_accepted_for_review(self):
        from test_quant_001_ichimoku import SetupTests
        from research_bot.quant_001_ichimoku import DataIdentity
        fixture=SetupTests("test_full_contract_and_no_execution");fixture.setUp()
        fixture.i4=DataIdentity("SYNTHETIC","BTC/USDT","H4","FIXTURE","a"*64,"HISTORICAL_CLOSE_ASSUMED")
        candidate=fixture.evaluate().as_dict()
        s=L1Simulator(CostScenario("ZERO",Decimal(0),Decimal(0),Decimal(0)),source_id="FIXTURE",source_sha256=SHA)
        row=fixture.h1.iloc[-1]
        s.bar(L1Bar(row.timestamp.to_pydatetime(),row.available_at.to_pydatetime(),
                    Decimal(str(row.open)),Decimal(str(row.high)),Decimal(str(row.low)),Decimal(str(row.close)),SHA))
        # With no midnight anchor this is safely rejected; the full contract
        # itself must pass validation and be recorded as a signal.
        self.assertFalse(s.signal(candidate))
        self.assertTrue(any(x["kind"]=="SIGNAL" for x in s.state["events"]))

    def test_fee_shock_reduces_cash_and_is_explicit(self):
        a,b=sim(),sim(cost="50")
        for s in (a,b):
            to_entry(s);s.bar(bar(3,"80","81","79","80"))
        self.assertGreater(Decimal(b.state["total_fees"]),Decimal("0"))
        self.assertLess(Decimal(b.state["trades"][0]["pnl"]),Decimal("0"))
        self.assertEqual(Decimal(a.state["total_fees"]),Decimal("0"))

    def test_insufficient_capital_and_nonfinite_are_rejected(self):
        for value in ("0","-1","NaN"):
            with self.assertRaises(ValueError):sim(cash=value)
        with self.assertRaises(ValueError): CostScenario("BAD",Decimal("-1"),Decimal(0),Decimal(0))
        with self.assertRaisesRegex(ValueError,"EXACT_DECIMAL_REQUIRED"):
            L1Bar(T,T+timedelta(hours=1),100.0,Decimal(101),Decimal(99),Decimal(100),SHA).payload()

    def test_position_size_respects_risk_and_exposure(self):
        s=sim(cost="24");to_entry(s)
        p=s.state["position"]
        with localcontext() as ctx:
            ctx.prec=80
            q,entry,stop=map(Decimal,(p["quantity"],p["entry"],p["stop"]))
            fee=Decimal("24")/Decimal("20000")
            risk=q*(entry*(1+fee)-stop*(1-fee))
            self.assertLessEqual(risk,Decimal("25"))
            self.assertLessEqual(q*entry,Decimal("3500"))
            self.assertGreaterEqual(Decimal(s.state["cash"]),0)

    def test_decimal_rounding_never_exceeds_risk_budget(self):
        s=sim(cost="24")
        s.bar(bar(0,lo="1"));s.signal(setup(stop="1"));s.bar(bar(1))
        price="1.1440206185567010309278350515463917525773195876288659793814432989690721649484536082"
        s.bar(bar(2,price,"2","1.01",price))
        p=s.state["position"]
        with localcontext() as ctx:
            ctx.prec=500
            qty,entry,stop=map(Decimal,(p["quantity"],p["entry"],p["stop"]))
            fee=Decimal("24")/Decimal("20000")
            self.assertLessEqual(qty*(entry*(1+fee)-stop*(1-fee)),Decimal("25"))

    def test_expiry_without_fabricating_bar(self):
        s=sim();s.bar(bar(0,lo="98"));s.signal(setup());self.assertFalse(s.expire(as_of=T+timedelta(hours=3)))
        with self.assertRaisesRegex(ValueError,"EXPIRY_UNRESOLVED_MISSING_MARKET_DATA"):
            s.expire(as_of=T+timedelta(hours=3,seconds=1))
        self.assertIsNotNone(s.state["pending"])
        self.assertFalse(any(e["kind"]=="FILLED" for e in s.state["events"]))

    def test_timeout_fills_at_later_open(self):
        s=sim();to_entry(s)
        for hour in range(3,10):s.bar(bar(hour))
        self.assertTrue(s.state["position"]["timeout_pending"])
        s.bar(bar(10))
        self.assertEqual(s.state["trades"][0]["exit_reason"],"TIMEOUT_NEXT_OPEN")

    def test_post_exit_intrabar_extremes_do_not_trigger_halt(self):
        s=sim();to_entry(s)
        for hour in range(3,10):s.bar(bar(hour))
        s.bar(bar(10,"100","1000","1","100"))
        self.assertEqual(s.state["trades"][0]["exit_reason"],"TIMEOUT_NEXT_OPEN")
        self.assertFalse(s.state["daily_halt"])
        self.assertFalse(s.state["hard_halt"])

    def test_timeout_and_gap_stop_conflict_has_one_exit(self):
        s=sim();to_entry(s)
        for hour in range(3,10):s.bar(bar(hour))
        s.bar(bar(10,"90","91","89","90"))
        self.assertEqual(len(s.state["trades"]),1)
        self.assertTrue(any(x["kind"]=="CONFLICTING_EXIT_INTENTS" for x in s.state["events"]))
        self.assertEqual(Decimal(s.state["trades"][0]["exit"]),Decimal("90"))

    def test_hard_halt_latches_through_restart(self):
        s=sim();to_entry(s);s.bar(bar(3,"40","41","39","40"))
        self.assertTrue(s.state["hard_halt"])
        restored=L1Simulator.restore(s.snapshot());self.assertTrue(restored.state["hard_halt"])
        self.assertFalse(restored.signal(setup(at=T+timedelta(hours=4),key="blocked",stop="39")))


if __name__ == "__main__": unittest.main()
