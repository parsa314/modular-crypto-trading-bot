"""Synthetic engineering fixtures only; no market performance claims."""
import tempfile
import unittest
import json
from io import BytesIO
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
import pandas as pd

from research_bot.e2e_observer import evaluate, store_decision, okx_observe, explain_candidates


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
        self.assertEqual(result['risk_status'], 'NOT_EVALUATED')
        self.assertEqual(result['cost_status'], 'NOT_VERIFIED')
        self.assertEqual(result['candidate_analysis']['score_semantics'], 'ORDINAL_NOT_PROBABILITY')
        self.assertEqual(result['execution_authorization']['status'], 'DENIED')
        self.assertEqual(result['trade_candidate']['status'], 'NOT_EVALUATED')
        self.assertEqual(len(result['research_signals']), 4)
        for signal in result['research_signals']:
            self.assertEqual(signal['symbol'], 'BTC/USDT')
            self.assertEqual(signal['timeframe'], 'H1')
            self.assertEqual(signal['provenance'], 'SYNTHETIC_TEST')
            self.assertEqual(signal['strategy_version'], result['strategy_sha'])
            self.assertLessEqual(pd.Timestamp(signal['available_at']), pd.Timestamp(result['as_of']))

    def test_conflict_and_shared_structure_disclosed(self):
        at = pd.Timestamp('2024-01-05T04:00:00Z')
        directions = {'ICHIMOKU':'SELL','ICT':'BUY','SMC':'BUY','AL_BROOKS':'HOLD'}
        signals = {k:{'style':k,'action':v,'decision_at':at} for k,v in directions.items()}
        result = explain_candidates(signals, as_of=at)
        self.assertTrue(result['conflict'])
        self.assertEqual(result['evidence_groups']['BUY'], ['ICT_SMC_STRUCTURE'])
        self.assertEqual(result['independence_status'], 'NOT_ESTABLISHED')

    def test_future_or_missing_strategy_timestamp_rejected(self):
        at = pd.Timestamp('2024-01-05T04:00:00Z')
        signals = {k:{'style':k,'action':'HOLD','decision_at':at} for k in ('ICHIMOKU','ICT','SMC','AL_BROOKS')}
        signals['ICT']['decision_at'] = at + pd.Timedelta(seconds=1)
        with self.assertRaisesRegex(ValueError, 'FUTURE_STRATEGY_OUTPUT'):
            explain_candidates(signals, as_of=at)
        signals['ICT']['decision_at'] = None
        with self.assertRaisesRegex(ValueError, 'MISSING_TIMESTAMP'):
            explain_candidates(signals, as_of=at)

    def test_conflicting_detector_outputs_never_create_an_order(self):
        from research_bot.strategies.common import StrategySignal
        at = pd.Timestamp('2024-01-05T04:00:00Z').to_pydatetime()
        signals = {'ICHIMOKU':'SELL','ICT':'BUY','SMC':'BUY','AL_BROOKS':'HOLD'}
        arms = {name:SimpleNamespace(evaluate=lambda frame, as_of, name=name, action=action:
                    StrategySignal(style=name,action=action,score=0 if action=='HOLD' else 1,decision_at=at))
                for name, action in signals.items()}
        with patch('research_bot.e2e_observer.ARMS', arms):
            result = self.run_decision(bars())
        self.assertEqual(result['action'], 'NO_TRADE')
        self.assertEqual(result['paper_orders'], 0)
        self.assertIn('CONFLICTING_SIGNALS', result['reason_codes'])
        self.assertEqual(result['candidate_analysis']['evidence_groups']['BUY'], ['ICT_SMC_STRUCTURE'])

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

    def test_okx_public_payload_is_historical_not_forward(self):
        end = pd.Timestamp.now(tz='UTC').floor('h')
        times = pd.date_range(end=end - pd.Timedelta(hours=1), periods=100, freq='h')
        rows = [[str(int(t.timestamp()*1000)), '100', '102', '98', '100', '10', '0', '0', '1']
                for t in reversed(times)]
        raw = json.dumps({'code': '0', 'data': rows}).encode()
        with tempfile.TemporaryDirectory() as tmp, patch('research_bot.e2e_observer.urlopen', return_value=BytesIO(raw)):
            result = okx_observe('BTC/USDT', Path(tmp) / 'state.sqlite')
        self.assertFalse(result['first_seen_forward'])
        self.assertEqual(result['action'], 'NO_TRADE')
        self.assertEqual(len(result['raw_hashes']), 1)


if __name__ == '__main__':
    unittest.main()
