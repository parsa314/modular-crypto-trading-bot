"""Synthetic data quality fixtures; not observed market data."""
from datetime import datetime,timedelta,timezone
import unittest
import pandas as pd
from research_bot.quant_002_data import complete_bars


class DataTests(unittest.TestCase):
    def setUp(self):
        start=datetime(2023,1,1,tzinfo=timezone.utc)
        ts=[start+timedelta(minutes=15*i) for i in range(32)]
        self.raw=pd.DataFrame({"timestamp":ts,"open":[100]*32,"high":[101]*32,
                               "low":[99]*32,"close":[100]*32,"volume":[1]*32})

    def test_complete_h1_and_h4_counts(self):
        h1,failed1=complete_bars(self.raw,hours=1)
        h4,failed4=complete_bars(self.raw,hours=4)
        self.assertEqual((len(h1),len(h4),failed1,failed4),(8,2,(),()))
        self.assertEqual(h4.available_at.iloc[0],h4.timestamp.iloc[0]+timedelta(hours=4))

    def test_missing_constituent_does_not_create_bar(self):
        h1,rejected=complete_bars(self.raw.drop(index=2),hours=1)
        h4,rejected4=complete_bars(self.raw.drop(index=2),hours=4)
        self.assertEqual((len(h1),len(rejected),len(h4),len(rejected4)),(7,1,1,1))
        self.assertNotIn(self.raw.timestamp.iloc[0],list(h1.timestamp))

    def test_duplicate_and_misaligned_m15_are_rejected(self):
        with self.assertRaisesRegex(ValueError,"DUPLICATE"):
            complete_bars(pd.concat([self.raw,self.raw.iloc[[-1]]]),hours=1)
        self.raw.loc[3,"timestamp"]+=timedelta(minutes=1)
        with self.assertRaisesRegex(ValueError,"M15_MISALIGNED"):
            complete_bars(self.raw,hours=1)

    def test_invalid_constituents_never_disappear_in_aggregation(self):
        for field,value in [("open",float("nan")),("high",98),("volume",-1)]:
            bad=self.raw.copy()
            bad[field]=bad[field].astype(float)
            bad.loc[1,field]=value
            with self.assertRaises(ValueError):complete_bars(bad,hours=1)


if __name__ == "__main__": unittest.main()
