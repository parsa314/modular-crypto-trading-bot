"""Deterministic software fixtures, not real exchange evidence."""
from datetime import datetime, timedelta, timezone
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from research_bot.forward_observer_002 import Collector, validate_bars


UTC = timezone.utc
BASE = datetime(2026, 1, 1, tzinfo=UTC)


def payload(times, *, confirm='1'):
    return json.dumps({'code':'0','data':[[str(int(t.timestamp()*1000)), '100', '102', '98', '100', '5', '0', '0', confirm] for t in times]}).encode()


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'journal.sqlite'
        self.collector = Collector(self.path, poll_seconds=60)

    def tearDown(self):
        self.collector.close()
        self.tmp.cleanup()

    def ingest(self, times, at):
        return self.collector.ingest('BTC/USDT', '15m', payload(times), at, 'SYNTHETIC_TEST')

    def test_bootstrap_duplicate_and_first_seen(self):
        r = self.ingest([BASE], BASE+timedelta(minutes=15))
        self.assertEqual(r['HISTORICAL_BOOTSTRAP'], 1)
        self.assertEqual(self.ingest([BASE], BASE+timedelta(minutes=16))['DUPLICATE'], 1)
        # Every poll required to preserve evidence coverage across formation.
        for minute in range(17, 30):
            self.ingest([BASE], BASE+timedelta(minutes=minute))
        r = self.ingest([BASE, BASE+timedelta(minutes=15)], BASE+timedelta(minutes=30))
        self.assertEqual(r['FIRST_OBSERVED_NEW_CLOSED_BAR'], 1)

    def test_restart_cannot_claim_first_seen(self):
        self.ingest([BASE], BASE+timedelta(minutes=15))
        self.collector.close()
        self.collector = Collector(self.path, poll_seconds=60)
        r = self.ingest([BASE, BASE+timedelta(minutes=15)], BASE+timedelta(minutes=30))
        self.assertEqual(r['BACKFILL_AFTER_GAP'], 1)
        self.assertEqual(r['FIRST_OBSERVED_NEW_CLOSED_BAR'], 0)

    def test_disconnect_resets_coverage(self):
        self.ingest([BASE], BASE+timedelta(minutes=15))
        self.collector.fail(BASE+timedelta(minutes=20),'OKX','SYNTHETIC_DISCONNECT')
        r = self.ingest([BASE, BASE+timedelta(minutes=15)], BASE+timedelta(minutes=30))
        self.assertEqual(r['BACKFILL_AFTER_GAP'], 1)

    def test_duplicate_conflict_rolls_back_transaction(self):
        self.ingest([BASE], BASE+timedelta(minutes=15))
        bad = json.loads(payload([BASE]))
        bad['data'][0][4] = '101'
        with self.assertRaisesRegex(ValueError, 'CONFLICTING_DUPLICATE_BAR'):
            self.collector.ingest('BTC/USDT','15m',json.dumps(bad).encode(),BASE+timedelta(minutes=16),'SYNTHETIC_TEST')
        with self.collector.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM collector_bars').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM collector_raw').fetchone()[0],1)

    def test_second_worker_excluded(self):
        with self.assertRaisesRegex(ValueError, 'COLLECTOR_ALREADY_RUNNING'):
            Collector(self.path)

    def test_independent_process_cannot_acquire_same_journal(self):
        child = subprocess.run([sys.executable, '-c',
            'import sys; from research_bot.forward_observer_002 import Collector; Collector(sys.argv[1])',
            str(self.path)], capture_output=True, text=True, timeout=10)
        self.assertNotEqual(child.returncode, 0)
        self.assertIn('COLLECTOR_ALREADY_RUNNING', child.stderr)

    def test_closed_gap_future_nonfinite(self):
        with self.assertRaisesRegex(ValueError, 'NO_CLOSED_BARS'):
            validate_bars(json.loads(payload([BASE],confirm='0'))['data'],'15m',BASE+timedelta(minutes=15))
        with self.assertRaisesRegex(ValueError, 'MARKET_GAP'):
            validate_bars(json.loads(payload([BASE,BASE+timedelta(minutes=30)]))['data'],'15m',BASE+timedelta(minutes=45))
        with self.assertRaisesRegex(ValueError, 'FUTURE_OR_MISALIGNED'):
            validate_bars(json.loads(payload([BASE+timedelta(hours=1)]))['data'],'15m',BASE)
        bad = json.loads(payload([BASE]))['data']; bad[0][2]='NaN'
        with self.assertRaisesRegex(ValueError, 'INVALID_OHLCV'):
            validate_bars(bad,'15m',BASE+timedelta(minutes=15))

    def test_stale_quote_rejected(self):
        raw=json.dumps({'code':'0','data':[{'bidPx':'100','askPx':'101','ts':str(int(BASE.timestamp()*1000))}]}).encode()
        with self.assertRaisesRegex(ValueError, 'INVALID_OR_STALE_QUOTE'):
            self.collector.quote('BTC/USDT','SYNTHETIC_TEST',raw,BASE+timedelta(minutes=2))

    def test_mtf_incomplete_fails_closed(self):
        self.ingest([BASE], BASE+timedelta(minutes=15))
        at = BASE+timedelta(minutes=15)
        quote=json.dumps({'code':'0','data':[{'bidPx':'100','askPx':'100.1','ts':str(int(at.timestamp()*1000))}]}).encode()
        self.collector.quote('BTC/USDT','SYNTHETIC_TEST',quote,at)
        with self.assertRaisesRegex(ValueError, 'MTF_INSUFFICIENT_HISTORY'):
            self.collector.decision('BTC/USDT',at)

    def test_pending_hour_survives_restart(self):
        self.collector.ingest('BTC/USDT','1H',payload([BASE]),BASE+timedelta(hours=1),'SYNTHETIC_TEST')
        for minute in range(61, 120):
            self.collector.ingest('BTC/USDT','1H',payload([BASE]),BASE+timedelta(minutes=minute),'SYNTHETIC_TEST')
        self.collector.ingest('BTC/USDT','1H',payload([BASE,BASE+timedelta(hours=1)]),BASE+timedelta(hours=2),'SYNTHETIC_TEST')
        self.assertEqual(len(self.collector.pending()), 1)
        self.collector.close()
        self.collector = Collector(self.path, poll_seconds=60)
        self.assertEqual(len(self.collector.pending()), 1)

    def test_complete_mtf_context_allows_four_arm_observation(self):
        at = BASE + timedelta(hours=16, minutes=1)
        for period, minutes in (('15m',15),('1H',60),('4H',240)):
            start = BASE+timedelta(hours=16)-timedelta(minutes=minutes*100)
            times = [start+timedelta(minutes=minutes*i) for i in range(100)]
            self.collector.ingest('BTC/USDT',period,payload(times),at,'SYNTHETIC_TEST')
        quote=json.dumps({'code':'0','data':[{'bidPx':'100','askPx':'100.1','ts':str(int(at.timestamp()*1000))}]}).encode()
        self.collector.quote('BTC/USDT','SYNTHETIC_TEST',quote,at)
        result = self.collector.decision('BTC/USDT',at)
        self.assertEqual(result['action'], 'NO_TRADE')
        self.assertEqual(result['paper_orders'], 0)
        self.assertEqual(set(result['strategies']), {'ICHIMOKU','ICT','SMC','AL_BROOKS'})


if __name__ == '__main__':
    unittest.main()
