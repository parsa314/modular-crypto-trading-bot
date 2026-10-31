"""Synthetic labels test control flow; not observed executions or performance."""
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import unittest
import pandas as pd
from research_bot.strategy_labels_012 import triple_barrier_label


class TripleBarrierTests(unittest.TestCase):
    def setUp(self):
        self.entry=datetime(2024,1,1,1,tzinfo=timezone.utc)
        times=[self.entry+timedelta(hours=i) for i in range(3)]
        self.data=pd.DataFrame({"timestamp":times,"available_at":[x+timedelta(hours=1) for x in times],
                                "high":[101,103,101],"low":[99,99,99]})
        self.args=dict(decision_at=self.entry-timedelta(minutes=1),entry_at=self.entry,
            entry_price=Decimal("100"),take_profit=Decimal("102"),stop_loss=Decimal("98"),
            timeframe="H1",horizon_bars=3,direction="LONG",as_of=self.entry+timedelta(hours=3))

    def test_no_future_label_and_close_availability(self):
        label=triple_barrier_label(self.data,**{**self.args,"as_of":self.entry+timedelta(hours=1)})
        self.assertEqual(label.outcome,"PENDING")
        label=triple_barrier_label(self.data,**self.args)
        self.assertEqual((label.outcome,label.observed_bars),("TP",2))
        self.assertEqual(label.label_available_at,(self.entry+timedelta(hours=2)).isoformat())

    def test_same_bar_both_barriers_is_ambiguous(self):
        self.data.loc[1,"low"]=97
        self.assertEqual(triple_barrier_label(self.data,**self.args).outcome,"AMBIGUOUS")

    def test_timeout_and_missing_bar(self):
        self.data["high"]=[101,101,101]
        self.assertEqual(triple_barrier_label(self.data,**self.args).outcome,"TIMEOUT")
        with self.assertRaisesRegex(ValueError,"MISSING_DUPLICATE"):
            triple_barrier_label(self.data.drop(index=1),**self.args)

    def test_later_data_does_not_change_completed_label(self):
        before=triple_barrier_label(self.data,**self.args)
        after=triple_barrier_label(self.data.iloc[:2],**self.args)
        self.assertEqual(before,after)

    def test_same_time_entry_is_rejected(self):
        with self.assertRaisesRegex(ValueError,"ENTRY_MUST_FOLLOW_DECISION"):
            triple_barrier_label(self.data,**{**self.args,"decision_at":self.entry})


if __name__ == "__main__": unittest.main()
