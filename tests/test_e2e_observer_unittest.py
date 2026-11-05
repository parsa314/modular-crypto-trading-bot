"""Synthetic engineering fixtures only; no market performance claims."""
import tempfile
import unittest
from pathlib import Path
import pandas as pd

from research_bot.e2e_observer import evaluate, store_decision


def bars():
    ts = pd.date_range('2024-01-01', periods=100, freq='h', tz='UTC')
    return pd.DataFrame({'timestamp': ts, 'available_at': ts + pd.Timedelta(hours=1),
                         'open': 100., 'high': 102., 'low': 98., 'close': 100., 'volume': 10.})


class ObservationTests(unittest.TestCase):
    def run_decision(self, frame):
        return evaluate(frame, as_of=pd.Timestamp('2024-01-05T04:00:00Z'),
                        symbol='BTC/USDT', source='SYNTHETIC_TEST')

    def test_four_arms_and_no_trade(self):
        result = self.run_decision(bars())
        self.assertEqual(set(result['strategies']), {'ICHIMOKU', 'ICT', 'SMC', 'AL_BROOKS'})
        self.assertEqual(result['action'], 'NO_TRADE')
        self.assertEqual(result['paper_orders'], 0)
        self.assertIn('FINANCIAL_GATE_BLOCKED', result['reason_codes'])

    def test_dedup_observation_after_restart(self):
        result = self.run_decision(bars())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.sqlite'
            self.assertEqual(store_decision(path, result), store_decision(path, result))
            import sqlite3
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('select count(*) from observations').fetchone()[0], 1)

    def test_early_availability_rejected(self):
        x = bars()
        x.loc[0, 'available_at'] = x.loc[0, 'timestamp']
        with self.assertRaisesRegex(ValueError, 'BAR_AVAILABLE_BEFORE_CLOSE'):
            self.run_decision(x)

    def test_duplicate_and_gap_rejected(self):
        x = bars()
        with self.assertRaisesRegex(ValueError, 'DUPLICATE_OR_UNORDERED_BARS'):
            self.run_decision(pd.concat([x, x.tail(1)], ignore_index=True))
        with self.assertRaisesRegex(ValueError, 'BAR_GAP'):
            self.run_decision(x.drop(index=20).reset_index(drop=True))

    def test_future_mutation_cannot_change_decision(self):
        x = bars()
        x.loc[99, 'available_at'] += pd.Timedelta(hours=2)
        first = self.run_decision(x)
        x.loc[99, ['open', 'high', 'low', 'close']] = [500., 600., 400., 500.]
        self.assertEqual(first, self.run_decision(x))

    def test_stale_bars_rejected(self):
        with self.assertRaisesRegex(ValueError, 'STALE_BAR'):
            self.run_decision(bars().iloc[:95])


if __name__ == '__main__':
    unittest.main()
