"""Synthetic test data; no assertion of market performance."""
from datetime import datetime,timedelta,timezone
import unittest
import pandas as pd
from research_bot.strategy_regime_012 import FixedRegimeParameters, fixed_regime


class RegimeTests(unittest.TestCase):
    def setUp(self):
        start=datetime(2024,1,1,tzinfo=timezone.utc)
        ts=[start+timedelta(hours=i) for i in range(96)]
        self.frame=pd.DataFrame({"timestamp":ts,"available_at":[t+timedelta(hours=1) for t in ts],
            "open":[100.0]*96,"high":[101.0]*96,"low":[99.0]*96,"close":[100.0]*96,"volume":[1.0]*96})
        self.as_of=ts[95]
        self.params=FixedRegimeParameters("SYNTHETIC_FIXED",10,1.0,0.015)

    def test_range_high_vol_and_unavailable_liquidity(self):
        r=fixed_regime(self.frame,timeframe="H1",as_of=self.as_of,parameters=self.params)
        self.assertEqual((r.trend,r.volatility,r.liquidity_stress),("RANGE","HIGH","DATA_UNAVAILABLE"))
        self.assertEqual(r.parameter_id,"SYNTHETIC_FIXED")

    def test_future_bar_does_not_change_regime(self):
        before=fixed_regime(self.frame,timeframe="H1",as_of=self.as_of,parameters=self.params)
        self.frame.loc[95,["open","high","low","close"]]=[900,1000,800,950]
        self.assertEqual(before,fixed_regime(self.frame,timeframe="H1",as_of=self.as_of,parameters=self.params))

    def test_missing_parameter_and_future_bar_rejected(self):
        with self.assertRaisesRegex(ValueError,"INVALID_REGIME_PARAMETERS"):
            FixedRegimeParameters("",10,1.0,0.01)
        self.frame.loc[50,"available_at"]=self.frame.loc[50,"timestamp"]+timedelta(minutes=1)
        with self.assertRaisesRegex(ValueError,"BAR_NOT_CLOSED"):
            fixed_regime(self.frame,timeframe="H1",as_of=self.as_of,parameters=self.params)


if __name__ == "__main__": unittest.main()
