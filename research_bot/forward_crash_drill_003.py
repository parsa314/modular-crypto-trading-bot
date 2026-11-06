"""Isolated process-kill drill for the public-only forward observer.

The journal must be new. --fixture uses synthetic bars and is engineering-only.
No venue order, private API, paper fill or deployment is performed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import sqlite3
import subprocess
import sys

from .forward_observer_002 import Collector, cycle, request


def fixture_response():
    now = datetime.now(timezone.utc)
    seconds = int(now.timestamp()) // 900 * 900 - 900
    row = [str(seconds * 1000), '100', '102', '98', '100', '10', '0', '0', '1']
    return 'SYNTHETIC_TEST', json.dumps({'code': '0', 'data': [row]}).encode(), now


def child_stage(path, fixture):
    collector = Collector(path)
    endpoint, raw, observed = fixture_response() if fixture else request(
        '/api/v5/market/candles', {'instId':'BTC-USDT', 'bar':'15m', 'limit':100})
    digest = collector.stage_candle('BTC/USDT', '15m', endpoint, raw, observed)
    print(json.dumps({'child_pid':os.getpid(), 'raw_sha256':digest,
                      'received_at':observed.isoformat(), 'run_id':collector.run_id}), flush=True)
    signal.pause()  # Parent must terminate this process after staging commits.


def drill(path, fixture):
    if path.exists():
        raise ValueError('DRILL_REQUIRES_NEW_ISOLATED_DATABASE')
    path.parent.mkdir(parents=True, exist_ok=True)
    before = Collector(path)
    try:
        if fixture:
            endpoint, raw, at = fixture_response()
            before.stage_candle('BTC/USDT','15m',endpoint,raw,at)
            before.ingest('BTC/USDT','15m',raw,at,endpoint)
        else:
            cycle(before)
        with before.connect() as db:
            watermark_before = db.execute('SELECT symbol,timeframe,watermark FROM collector_streams ORDER BY symbol,timeframe').fetchall()
            bars_before = db.execute('SELECT count(*) FROM collector_bars').fetchone()[0]
    finally:
        before.close()
    cmd = [sys.executable, '-m', 'research_bot.forward_crash_drill_003', '--db', str(path), '--child']
    if fixture:
        cmd.append('--fixture')
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(60):
                raise TimeoutError('CHILD_STAGE_TIMEOUT')
        line = process.stdout.readline()
        if not line:
            raise RuntimeError('CHILD_FAILED_BEFORE_STAGING: ' + process.stderr.read())
        staged = json.loads(line)
        process.kill()  # SIGKILL: no finally block and no clean shutdown.
        exit_code = process.wait(timeout=10)
        if exit_code != -signal.SIGKILL:
            raise RuntimeError('EXPECTED_SIGKILL')
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
    recovered = Collector(path)
    try:
        with recovered.connect() as db:
            orphan = db.execute("SELECT status FROM collector_ingest_receipts WHERE run_id=? AND raw_sha256=?", (staged['run_id'], staged['raw_sha256'])).fetchone()
            record = db.execute('SELECT payload FROM collector_raw WHERE hash=?', (staged['raw_sha256'],)).fetchone()
            watermark_after = db.execute('SELECT symbol,timeframe,watermark FROM collector_streams ORDER BY symbol,timeframe').fetchall()
            bars_after = db.execute('SELECT count(*) FROM collector_bars').fetchone()[0]
            if orphan != ('UNPROCESSED_AFTER_CRASH',) or record is None:
                raise AssertionError('UNPROCESSED_RAW_NOT_DETECTED')
            if hashlib.sha256(record[0]).hexdigest() != staged['raw_sha256']:
                raise AssertionError('RAW_HASH_MISMATCH')
            if watermark_after != watermark_before or bars_after != bars_before:
                raise AssertionError('CRASH_CHANGED_COMMITTED_STATE')
        if not fixture:
            cycle(recovered)
        with recovered.connect() as db:
            duplicate = db.execute('SELECT count(*) FROM (SELECT symbol,timeframe,bar_open,count(*) n FROM collector_bars GROUP BY symbol,timeframe,bar_open HAVING n>1)').fetchone()[0]
            health = db.execute("SELECT count(*) FROM collector_health WHERE status='UNPROCESSED_RAW_AFTER_CRASH'").fetchone()[0]
            if duplicate or not health:
                raise AssertionError('RECOVERY_INVARIANT_FAILED')
            counts = db.execute('SELECT status,count(*) FROM collector_bars GROUP BY status').fetchall()
            gaps = db.execute('SELECT COALESCE(SUM(gaps),0) FROM collector_streams').fetchone()[0]
            decisions = db.execute('SELECT count(*) FROM collector_decisions').fetchone()[0]
            no_trade = db.execute("SELECT count(*) FROM collector_decisions WHERE json_extract(payload,'$.action')='NO_TRADE'").fetchone()[0]
        return {'status':'DRILL_COMPLETED', 'evidence_type':'SIMULATED_TEST' if fixture else 'PUBLIC_MARKET_CRASH_DRILL',
                'crash':'SIGKILL', 'child_exit_code':exit_code, 'staged':staged,
                'watermarks_before':watermark_before, 'watermarks_after_restart':watermark_after,
                'bars_before':bars_before, 'bars_immediately_after_crash':bars_after,
                'bar_status_counts':counts, 'duplicate_bars':duplicate, 'unprocessed_raw_events':health,
                'gaps':gaps, 'decisions':decisions, 'no_trade':no_trade,
                'paper_orders':0, 'demo_orders':0, 'live_orders':0,
                'forward_24h':'NOT_EXECUTED'}
    finally:
        recovered.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--fixture', action='store_true')
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        child_stage(args.db, args.fixture)
    else:
        print(json.dumps(drill(args.db, args.fixture), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
