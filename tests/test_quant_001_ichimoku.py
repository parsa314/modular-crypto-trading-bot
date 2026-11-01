"""Synthetic OHLCV fixtures test causality, not performance or execution."""
from datetime import datetime,timedelta,timezone
import unittest
import pandas as pd

from research_bot.quant_001_ichimoku import DataIdentity, VERSION, evaluate_kijun_pullback


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2024,2,1,tzinfo=timezone.utc)
        self.h1=self._bars(1)
        self.h4=self._bars(4)
        self.h1.loc[99,["open","high","low","close"]]=[109.7,110.5,108.0,110.0]
        self.i1=DataIdentity("SYNTHETIC","BTC/USDT","H1","FIXTURE","a"*64,"HISTORICAL_CLOSE_ASSUMED")
        self.i4=DataIdentity("SYNTHETIC","BTC/USDT","H4","FIXTURE","b"*64,"HISTORICAL_CLOSE_ASSUMED")

    def _bars(self, hours):
        times=[self.now-timedelta(hours=hours*(100-i)) for i in range(100)]
        close=[100+i*(0.1 if hours==1 else 1.0) for i in range(100)]
        return pd.DataFrame({"timestamp":times,"available_at":[t+timedelta(hours=hours) for t in times],
            "open":[c-.1 for c in close],"high":[c+.2 for c in close],"low":[c-.2 for c in close],
            "close":close,"volume":[10.0]*100})

    def evaluate(self, **kwargs):
        return evaluate_kijun_pullback(h1=kwargs.get("h1",self.h1),h4=kwargs.get("h4",self.h4),
            h1_source=kwargs.get("i1",self.i1),h4_source=kwargs.get("i4",self.i4),
            as_of=kwargs.get("as_of",self.now))

    def test_full_contract_and_no_execution(self):
        result=self.evaluate()
        self.assertIsNotNone(result)
        out=result.as_dict()
        self.assertEqual(out["version"],VERSION)
        self.assertEqual(len(out["setup_id"]),64)
        self.assertFalse(out["execution_eligible"])
        self.assertEqual(out["evidence_at"],self.now.isoformat())
        self.assertEqual(out["entry_rule"]["kind"],"FIRST_H1_OPEN_STRICTLY_AFTER_DECISION")
        self.assertIsNone(out["entry_rule"]["price"])
        self.assertEqual(out["target_rule"]["multiple"],"2")
        self.assertEqual(out["timeout"]["count"],8)
        self.assertEqual(out["stop_rule"]["level"],out["invalidation"]["level"])
        self.assertIn("UNVERIFIED_EXECUTION_POLICY",out["cancellation_rule"])
        self.assertEqual(out["source_provenance"]["H4"]["availability"],"HISTORICAL_CLOSE_ASSUMED")

    def test_no_trigger_means_no_setup(self):
        self.h1.loc[99,"low"]=109.5
        self.assertIsNone(self.evaluate())

    def test_no_h4_trend_means_no_setup(self):
        self.h4.loc[99,["open","high","low","close"]]=[80.0,81.0,79.0,80.0]
        self.assertIsNone(self.evaluate())

    def test_future_bars_do_not_change_any_setup_evidence(self):
        before=self.evaluate()
        future1=self.h1.iloc[-1].copy()
        future1["timestamp"]=self.now
        future1["available_at"]=self.now+timedelta(hours=1)
        future1[["open","high","low","close"]]=[200,201,199,200]
        future4=self.h4.iloc[-1].copy()
        future4["timestamp"]=self.now
        future4["available_at"]=self.now+timedelta(hours=4)
        future4[["open","high","low","close"]]=[2,3,1,2]
        h1=pd.concat([self.h1,pd.DataFrame([future1])],ignore_index=True)
        h4=pd.concat([self.h4,pd.DataFrame([future4])],ignore_index=True)
        self.assertEqual(before,self.evaluate(h1=h1,h4=h4))

    def test_h4_not_available_at_decision_is_never_used(self):
        self.h4.loc[99,"available_at"]=self.now+timedelta(minutes=1)
        before=self.evaluate()
        self.h4.loc[99,["open","high","low","close"]]=[2,3,1,2]
        self.assertEqual(before,self.evaluate())
        self.assertIsNotNone(before)
        self.assertLess(datetime.fromisoformat(before.timeframe_context["h4_available_at"]),self.now)

    def test_stale_or_incomplete_context_is_rejected(self):
        with self.assertRaisesRegex(ValueError,"STALE_CONTEXT"):
            self.evaluate(as_of=self.now+timedelta(minutes=16))
        broken=self.h4.copy()
        broken.loc[99,"available_at"]=broken.loc[99,"timestamp"]+timedelta(hours=3)
        with self.assertRaisesRegex(ValueError,"BAR_NOT_CLOSED"):
            self.evaluate(h4=broken)
        with self.assertRaisesRegex(ValueError,"NONCONTIGUOUS"):
            self.evaluate(h1=self.h1.drop(index=50))

    def test_source_identity_is_bound(self):
        other=DataIdentity("OTHER","BTC/USDT","H4","FIXTURE","b"*64,"HISTORICAL_CLOSE_ASSUMED")
        with self.assertRaisesRegex(ValueError,"CROSS_FEED_INSTRUMENT_MISMATCH"):
            self.evaluate(i4=other)
        with self.assertRaisesRegex(ValueError,"INVALID_SOURCE_SHA256"):
            DataIdentity("SYNTHETIC","BTC/USDT","H1","FIXTURE","bad","HISTORICAL_CLOSE_ASSUMED")


if __name__ == "__main__": unittest.main()
