"""Synthetic software fixtures; no historical data or performance evidence."""
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd
from research_bot.baseline_trade_contracts import SourceIdentity,ResearchExitPolicy,evaluate_baseline_contract
from research_bot.strategies.common import StrategySignal


class BaselineContractTests(unittest.TestCase):
    def setUp(self):
        start=datetime(2024,1,1,tzinfo=timezone.utc)
        times=[start+timedelta(hours=i) for i in range(100)]
        self.frame=pd.DataFrame({"timestamp":times,"available_at":[t+timedelta(hours=1) for t in times],
            "open":[100.0]*100,"high":[101.0]*100,"low":[99.0]*100,"close":[100.0]*100,"volume":[1.0]*100})
        self.now=times[-1]+timedelta(hours=1)
        self.source=SourceIdentity("SYNTHETIC","BTC/USDT","SPOT","H1","FIXTURE","a"*64,"HISTORICAL_CLOSE_ASSUMED")
        self.policy=ResearchExitPolicy("FIXTURE_NOT_PREREG",start-timedelta(days=1),Decimal("2"),2,8)

    def evaluate(self, style, *, frame=None, now=None, policy="fixture"):
        return evaluate_baseline_contract(style=style,frame=self.frame if frame is None else frame,
            as_of=self.now if now is None else now,source=self.source,policy=self.policy if policy=="fixture" else policy)

    def structure_fixture(self):
        # Past-only liquidity sweep, then a bullish break. The prior opposing
        # candle is the original SMC baseline's OB proxy, not order-flow proof.
        self.frame.loc[93,"low"]=98.0
        self.frame.loc[95,["open","close"]]=[100.5,100.0]
        self.frame.loc[99,["open","high","low","close"]]=[101.0,103.0,100.0,102.0]

    def brooks_fixture(self):
        for i in range(100):
            c=100.0+i*.1
            self.frame.loc[i,["open","high","low","close"]]=[c-.06,c+.2,c-.2,c]
        self.frame.loc[99,["open","high","low","close"]]=[109.6,111.1,109.5,111.0]

    def test_real_unchanged_detectors_make_explicit_research_contracts(self):
        self.structure_fixture()
        for style,expected_stop in [("ICT","98.0"),("SMC","99.0")]:
            out=self.evaluate(style)
            self.assertEqual(out.status,"CANDIDATE",(style,out.reason))
            c=out.candidate
            self.assertEqual(c["stop_rule"]["level"],expected_stop)
            self.assertFalse(c["execution_eligible"])
            self.assertFalse(c["trigger"]["score_is_probability"])
            self.assertFalse(c["features"]["score_is_probability"])
            self.assertEqual(c["symbol"],"BTC/USDT")
            self.assertEqual(c["timeframe"],"H1")
            self.assertEqual(c["decision_at"],self.now.isoformat())
            self.assertEqual(c["available_at"],self.now.isoformat())
            self.assertEqual(c["expiry"],(self.now+timedelta(hours=2)).isoformat())
            self.assertEqual(c["setup_id"],c["evidence_id"])
            self.assertIsNone(c["entry_rule"]["price"])
            self.assertIsNone(c["target_rule"]["price"])
            self.assertEqual(c["target_rule"]["multiple"],"2")
            self.assertEqual(c["timeout"]["count"],8)
            self.assertEqual(c["source_provenance"]["availability"],"HISTORICAL_CLOSE_ASSUMED")
            self.assertFalse(c["policy_freeze_independently_verified"])
        self.brooks_fixture()
        out=self.evaluate("AL_BROOKS")
        self.assertEqual(out.status,"CANDIDATE",out.reason)
        self.assertEqual(out.candidate["stop_rule"]["level"],"109.5")

    def test_future_mutation_cannot_change_candidates_or_evidence(self):
        self.structure_fixture()
        before={style:self.evaluate(style) for style in ("ICT","SMC")}
        future=self.frame.iloc[-1].copy();future["timestamp"]=self.now
        future["available_at"]=self.now+timedelta(hours=1)
        future[["open","high","low","close"]]=[1000,1001,999,1000]
        appended=pd.concat([self.frame,pd.DataFrame([future])],ignore_index=True)
        for style in before:self.assertEqual(before[style],self.evaluate(style,frame=appended))
        # A future bar that cannot yet be closed must not poison an earlier
        # decision merely because its availability or OHLC fields are absent.
        future["available_at"]=None
        future["close"]=None
        incomplete=pd.concat([self.frame,pd.DataFrame([future])],ignore_index=True)
        for style in before:self.assertEqual(before[style],self.evaluate(style,frame=incomplete))
        self.brooks_fixture();before_b=self.evaluate("AL_BROOKS")
        future=self.frame.iloc[-1].copy();future["timestamp"]=self.now
        future["available_at"]=self.now+timedelta(hours=1)
        future[["open","high","low","close"]]=[1,2,.5,1]
        self.assertEqual(before_b,self.evaluate("AL_BROOKS",frame=pd.concat([self.frame,pd.DataFrame([future])],ignore_index=True)))

    def test_policy_absent_late_or_bad_never_generates_candidate(self):
        self.structure_fixture()
        self.assertEqual(self.evaluate("ICT",policy=None).reason,"RESEARCH_POLICY_UNAVAILABLE")
        future=ResearchExitPolicy("LATE",self.now+timedelta(seconds=1),Decimal("2"),2,8)
        self.assertEqual(self.evaluate("ICT",policy=future).reason,"POLICY_NOT_YET_AVAILABLE")
        with self.assertRaisesRegex(ValueError,"EXACT_POSITIVE_DECIMAL_REQUIRED"):
            ResearchExitPolicy("FLOAT",self.now,2.0,2,8)

    def test_gap_and_stale_fail_closed(self):
        self.structure_fixture()
        self.assertEqual(self.evaluate("ICT",frame=self.frame.drop(index=50)).reason,"DATA_UNAVAILABLE_OR_INVALID")
        self.assertEqual(self.evaluate("SMC",now=self.now+timedelta(minutes=16)).reason,"STALE_SOURCE")

    def test_late_latest_candle_is_not_used(self):
        self.structure_fixture()
        later=self.frame.copy()
        later.loc[99,"available_at"]=self.now+timedelta(seconds=1)
        for style in ("ICT","SMC","AL_BROOKS"):
            self.assertEqual(self.evaluate(style,frame=later).reason,"STALE_SOURCE")

    def test_candidate_availability_includes_delayed_consumed_sweep(self):
        self.structure_fixture()
        delayed=self.frame.copy()
        delayed.loc[93,"available_at"]=self.now+timedelta(minutes=5)
        out=self.evaluate("ICT",frame=delayed,now=self.now+timedelta(minutes=10))
        self.assertEqual(out.status,"CANDIDATE",out.reason)
        self.assertEqual(out.candidate["available_at"],(self.now+timedelta(minutes=5)).isoformat())
        self.assertEqual(out.candidate["timeframe_context"]["bar_available_at"],self.now.isoformat())

    def test_candidate_availability_includes_later_policy_freeze(self):
        self.structure_fixture()
        later_policy=ResearchExitPolicy("LATER_POLICY",self.now+timedelta(minutes=7),Decimal("2"),2,8)
        out=self.evaluate("ICT",now=self.now+timedelta(minutes=10),policy=later_policy)
        self.assertEqual(out.status,"CANDIDATE",out.reason)
        self.assertEqual(out.candidate["available_at"],(self.now+timedelta(minutes=7)).isoformat())

    def test_spot_sell_never_becomes_short(self):
        signal=StrategySignal("ICT","SELL",2,self.now)
        with patch.dict("research_bot.baseline_trade_contracts._DETECTORS",{"ICT":SimpleNamespace(evaluate=lambda *args,**kwargs:signal)}):
            self.assertEqual(self.evaluate("ICT").reason,"SHORT_NOT_SUPPORTED_SPOT")

    def test_different_policy_versions_change_candidate_identity(self):
        self.structure_fixture()
        original=self.evaluate("ICT")
        alternative=ResearchExitPolicy("OTHER",self.policy.frozen_at,Decimal("2"),2,8)
        self.assertNotEqual(original.candidate["setup_id"],self.evaluate("ICT",policy=alternative).candidate["setup_id"])

    def test_m15_closed_bar_contract_uses_unchanged_detector(self):
        self.structure_fixture()
        start=datetime(2024,1,1,tzinfo=timezone.utc)
        self.frame["timestamp"]=[start+timedelta(minutes=15*i) for i in range(100)]
        self.frame["available_at"]=self.frame["timestamp"]+timedelta(minutes=15)
        source=SourceIdentity("SYNTHETIC","BTC/USDT","SPOT","M15","FIXTURE","a"*64,"HISTORICAL_CLOSE_ASSUMED")
        decision=evaluate_baseline_contract(style="ICT",frame=self.frame,as_of=self.frame.available_at.iloc[-1].to_pydatetime(),source=source,policy=self.policy)
        self.assertEqual(decision.status,"CANDIDATE")
        self.assertEqual(decision.candidate["timeframe_context"]["timeframe"],"M15")


if __name__ == "__main__": unittest.main()
