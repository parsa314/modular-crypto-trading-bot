"""Persistent public OKX observation only; never places paper or venue orders."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
import uuid
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd
from .e2e_observer import evaluate

PERIODS = {'15m': 900, '1H': 3600, '4H': 14400}
SYMBOLS = ('BTC/USDT', 'ETH/USDT')
BASE = 'https://www.okx.com'


def stamp(ts):
    t = pd.Timestamp(ts)
    if t.tzinfo is None or pd.isna(t):
        raise ValueError('TIMEZONE_REQUIRED')
    return t.tz_convert('UTC')


def validate_bars(data, period, received):
    """Validate closed, contiguous UTC candles without silently removing duplicates."""
    received = stamp(received)
    seconds = PERIODS[period]
    rows = []
    for row in data:
        if len(row) < 9 or row[8] not in ('0', '1'):
            raise ValueError('INVALID_CANDLE')
        t = pd.to_datetime(int(row[0]), unit='ms', utc=True)
        if int(t.timestamp()) % seconds or t > received:
            raise ValueError('FUTURE_OR_MISALIGNED_TIMESTAMP')
        if row[8] == '0':
            continue
        if t + pd.Timedelta(seconds=seconds) > received:
            raise ValueError('CLOSED_BEFORE_PERIOD_END')
        try:
            o, h, l, c, volume = map(Decimal, row[1:6])
        except InvalidOperation as exc:
            raise ValueError('INVALID_OHLCV') from exc
        if not all(x.is_finite() for x in (o, h, l, c, volume)) or min(o, h, l, c) <= 0 or volume < 0 or l > min(o, c) or h < max(o, c) or l > h:
            raise ValueError('INVALID_OHLCV')
        rows.append((t, [str(o), str(h), str(l), str(c), str(volume)]))
    rows.sort(key=lambda x: x[0])
    if len({t for t, _ in rows}) != len(rows):
        raise ValueError('DUPLICATE_BAR_IN_PAYLOAD')
    if any((b[0] - a[0]).total_seconds() != seconds for a, b in zip(rows, rows[1:])):
        raise ValueError('MARKET_GAP')
    if not rows:
        raise ValueError('NO_CLOSED_BARS')
    return rows


class Collector:
    def __init__(self, path, *, poll_seconds=60, late_seconds=120, clock_skew_seconds=10):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not 1 <= poll_seconds <= 300 or not 0 < late_seconds <= 900 or not 0 < clock_skew_seconds <= 60:
            raise ValueError('INVALID_CONFIG')
        self.poll_seconds, self.late_seconds, self.clock_skew_seconds = poll_seconds, late_seconds, clock_skew_seconds
        self.run_id = str(uuid.uuid4())
        self.lock = open(str(self.path) + '.lock', 'a+')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.lock.close()
            raise ValueError('COLLECTOR_ALREADY_RUNNING') from exc
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS collector_runs(id TEXT PRIMARY KEY, started TEXT, stopped TEXT);
            CREATE TABLE IF NOT EXISTS collector_raw(hash TEXT PRIMARY KEY, endpoint TEXT NOT NULL, received_at TEXT NOT NULL, payload BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS collector_bars(symbol TEXT, timeframe TEXT, bar_open TEXT, bar_close TEXT, received_at TEXT, first_seen_at TEXT, available_at TEXT, raw_sha256 TEXT, run_id TEXT, status TEXT, ohlcv TEXT, PRIMARY KEY(symbol,timeframe,bar_open));
            CREATE TABLE IF NOT EXISTS collector_streams(symbol TEXT, timeframe TEXT, watermark TEXT, coverage_since TEXT, last_poll TEXT, gaps INTEGER DEFAULT 0, PRIMARY KEY(symbol,timeframe));
            CREATE TABLE IF NOT EXISTS collector_decisions(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS collector_pending(symbol TEXT, bar_open TEXT, PRIMARY KEY(symbol,bar_open));
            CREATE TABLE IF NOT EXISTS collector_health(id INTEGER PRIMARY KEY, observed_at TEXT, venue TEXT, status TEXT, detail TEXT);
            ''')
            db.execute('INSERT INTO collector_runs(id,started) VALUES (?,?)', (self.run_id, datetime.now(timezone.utc).isoformat()))
            # A process restart always breaks observed continuous coverage.
            db.execute('UPDATE collector_streams SET coverage_since=NULL,last_poll=NULL')

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def close(self):
        if not self.lock.closed:
            with self.connect() as db:
                db.execute('UPDATE collector_runs SET stopped=? WHERE id=?', (datetime.now(timezone.utc).isoformat(), self.run_id))
            fcntl.flock(self.lock, fcntl.LOCK_UN)
            self.lock.close()

    def fail(self, at, venue, error):
        with self.connect() as db:
            db.execute('INSERT INTO collector_health(observed_at,venue,status,detail) VALUES (?,?,?,?)', (stamp(at).isoformat(), venue, 'ERROR', str(error)))
            if venue == 'OKX':
                db.execute('UPDATE collector_streams SET coverage_since=NULL,last_poll=NULL')

    def archive(self, endpoint, raw, at):
        digest = hashlib.sha256(raw).hexdigest()
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO collector_raw VALUES (?,?,?,?)', (digest,endpoint,stamp(at).isoformat(),raw))
        return digest

    def quote(self, symbol, endpoint, raw, received):
        digest = self.archive(endpoint, raw, received)
        obj = json.loads(raw)
        if obj.get('code') != '0' or not obj.get('data'):
            raise ValueError('QUOTE_UNAVAILABLE')
        q = obj['data'][0]
        bid, ask = Decimal(q['bidPx']), Decimal(q['askPx'])
        at = pd.to_datetime(int(q['ts']), unit='ms', utc=True)
        age = (stamp(received)-at).total_seconds()
        if not bid.is_finite() or not ask.is_finite() or bid <= 0 or ask < bid or age < -self.clock_skew_seconds or age > 30:
            raise ValueError('INVALID_OR_STALE_QUOTE')
        spread = (ask-bid) * Decimal(20000) / (ask+bid)
        if spread > 100:
            raise ValueError('QUOTE_SPREAD_VETO')
        with self.connect() as db:
            db.execute('INSERT INTO collector_health(observed_at,venue,status,detail) VALUES (?,?,?,?)', (stamp(received).isoformat(),'OKX','QUOTE_OK',json.dumps({'symbol':symbol,'bid':str(bid),'ask':str(ask),'quote_at':at.isoformat(),'raw_sha256':digest,'spread_bps':str(spread)},sort_keys=True)))

    def ingest(self, symbol, period, raw, received, endpoint):
        if symbol not in SYMBOLS or period not in PERIODS:
            raise ValueError('INVALID_STREAM')
        received = stamp(received)
        payload = json.loads(raw)
        if payload.get('code') != '0' or not isinstance(payload.get('data'), list):
            raise ValueError('OKX_API_ERROR')
        bars = validate_bars(payload['data'], period, received)
        digest = hashlib.sha256(raw).hexdigest()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO collector_raw VALUES (?,?,?,?)', (digest, endpoint, received.isoformat(), raw))
            prior = db.execute('SELECT watermark,coverage_since,last_poll FROM collector_streams WHERE symbol=? AND timeframe=?', (symbol, period)).fetchone()
            watermark = stamp(prior[0]) if prior and prior[0] else None
            coverage = stamp(prior[1]) if prior and prior[1] else received
            last_poll = stamp(prior[2]) if prior and prior[2] else None
            if last_poll and (received < last_poll or (received-last_poll).total_seconds() > self.poll_seconds*2+30):
                coverage = received
            initial = watermark is None
            count = {'HISTORICAL_BOOTSTRAP': 0, 'FIRST_OBSERVED_NEW_CLOSED_BAR': 0, 'LATE_OBSERVATION': 0, 'BACKFILL_AFTER_GAP': 0, 'DUPLICATE': 0}
            gaps = 0
            for opened, values in bars:
                closed = opened + pd.Timedelta(seconds=PERIODS[period])
                previous = db.execute('SELECT ohlcv FROM collector_bars WHERE symbol=? AND timeframe=? AND bar_open=?', (symbol,period,opened.isoformat())).fetchone()
                values_json = json.dumps(values, separators=(',', ':'))
                if previous:
                    if json.loads(previous[0]) != values:
                        raise ValueError('CONFLICTING_DUPLICATE_BAR')
                    count['DUPLICATE'] += 1
                    continue
                if initial:
                    status = 'HISTORICAL_BOOTSTRAP'
                elif watermark is not None and opened <= watermark:
                    status = 'BACKFILL_AFTER_GAP'
                else:
                    if watermark is not None and (opened-watermark).total_seconds() > PERIODS[period]:
                        gaps += 1
                        coverage = received
                    status = ('BACKFILL_AFTER_GAP' if opened < coverage else
                              'LATE_OBSERVATION' if (received-closed).total_seconds() > self.late_seconds else
                              'FIRST_OBSERVED_NEW_CLOSED_BAR')
                db.execute('INSERT INTO collector_bars VALUES (?,?,?,?,?,?,?,?,?,?,?)', (symbol,period,opened.isoformat(),closed.isoformat(),received.isoformat(),received.isoformat(),received.isoformat(),digest,self.run_id,status,values_json))
                if period == '1H' and status == 'FIRST_OBSERVED_NEW_CLOSED_BAR':
                    db.execute('INSERT OR IGNORE INTO collector_pending VALUES (?,?)', (symbol,opened.isoformat()))
                count[status] += 1
                watermark = opened if watermark is None or opened > watermark else watermark
            db.execute('INSERT INTO collector_streams VALUES (?,?,?,?,?,?) ON CONFLICT(symbol,timeframe) DO UPDATE SET watermark=excluded.watermark,coverage_since=excluded.coverage_since,last_poll=excluded.last_poll,gaps=collector_streams.gaps+excluded.gaps', (symbol,period,watermark.isoformat(),coverage.isoformat(),received.isoformat(),gaps))
            db.execute('INSERT INTO collector_health(observed_at,venue,status,detail) VALUES (?,?,?,?)', (received.isoformat(),'OKX','STREAM_OK',json.dumps({'symbol':symbol,'timeframe':period,'counts':count,'gaps':gaps},sort_keys=True)))
        return count

    def pending(self):
        with self.connect() as db:
            return db.execute('SELECT symbol,bar_open FROM collector_pending ORDER BY bar_open,symbol').fetchall()

    def decision(self, symbol, at, expected_bar=None):
        at = stamp(at)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            quote = db.execute("SELECT observed_at,detail FROM collector_health WHERE venue='OKX' AND status='QUOTE_OK' ORDER BY id DESC").fetchall()
            quote = next((row for row in quote if json.loads(row[1]).get('symbol') == symbol), None)
            if not quote or (at-stamp(quote[0])).total_seconds() > 30 or at < stamp(quote[0]):
                raise ValueError('QUOTE_STALE_AT_DECISION')
            series = {}
            for period, seconds in PERIODS.items():
                rows = db.execute('SELECT bar_open,available_at,ohlcv FROM collector_bars WHERE symbol=? AND timeframe=? AND available_at<=? ORDER BY bar_open DESC LIMIT 100', (symbol,period,at.isoformat())).fetchall()
                if len(rows) < 90:
                    raise ValueError('MTF_INSUFFICIENT_HISTORY:' + period)
                rows.reverse()
                latest = stamp(rows[-1][0])
                expected_close = at.floor(f'{seconds}s')
                if latest + pd.Timedelta(seconds=seconds) != expected_close:
                    raise ValueError('MTF_STALE:' + period)
                if any((stamp(b[0])-stamp(a[0])).total_seconds() != seconds for a,b in zip(rows,rows[1:])):
                    raise ValueError('MTF_GAP:' + period)
                series[period] = rows
            h = series['1H']
            if expected_bar is not None and stamp(h[-1][0]) != stamp(expected_bar):
                raise ValueError('PENDING_EVENT_STALE')
            frame = pd.DataFrame([{'timestamp':r[0], 'available_at':r[1], **dict(zip(('open','high','low','close','volume'),json.loads(r[2])))} for r in h])
            result = evaluate(frame, as_of=at, symbol=symbol, source='OKX_OBSERVATION')
            result['mtf_scope'] = 'M15_H4_VALIDATED_CONTEXT_ONLY_H1_STRATEGIES'
            key = hashlib.sha256((symbol+'|'+h[-1][0]+'|'+result['strategy_sha']).encode()).hexdigest()
            db.execute('INSERT OR IGNORE INTO collector_decisions VALUES (?,?)', (key,json.dumps(result,sort_keys=True)))
            if expected_bar is not None:
                db.execute('DELETE FROM collector_pending WHERE symbol=? AND bar_open=?', (symbol,expected_bar))
        return result


def request(path, params):
    url = BASE + path + '?' + urlencode(params)
    with urlopen(Request(url, headers={'User-Agent':'parsa-forward-observer/1'}), timeout=20) as response:
        raw = response.read()
    return url, raw, datetime.now(timezone.utc)


def cycle(collector):
    # Server clock verification guards the availability labels.
    url, raw, received = request('/api/v5/public/time', {})
    collector.archive(url, raw, received)
    server = json.loads(raw)
    if server.get('code') != '0' or not server.get('data'):
        raise ValueError('SERVER_TIME_UNAVAILABLE')
    server_ts = pd.to_datetime(int(server['data'][0]['ts']), unit='ms', utc=True)
    if abs((stamp(received)-server_ts).total_seconds()) > collector.clock_skew_seconds:
        raise ValueError('SERVER_CLOCK_SKEW')
    for symbol in SYMBOLS:
        quote_url, quote_raw, quote_at = request('/api/v5/market/ticker', {'instId':symbol.replace('/','-')})
        collector.quote(symbol, quote_url, quote_raw, quote_at)
        for period in PERIODS:
            endpoint, payload, observed = request('/api/v5/market/candles', {'instId':symbol.replace('/','-'),'bar':period,'limit':100})
            collector.archive(endpoint, payload, observed)
            collector.ingest(symbol, period, payload, observed, endpoint)
    results = []
    for symbol, bar_open in collector.pending():
        try:
            results.append(collector.decision(symbol, datetime.now(timezone.utc), bar_open))
        except ValueError as exc:
            blocked = {'symbol':symbol,'bar_open':bar_open,'action':'NO_TRADE',
                       'reason_codes':[str(exc)],'execution_mode':'OBSERVATION_ONLY',
                       'paper_orders':0,'pending_unresolved':True}
            key = hashlib.sha256((symbol+'|'+bar_open+'|BLOCKED').encode()).hexdigest()
            with collector.connect() as db:
                db.execute('INSERT OR IGNORE INTO collector_decisions VALUES (?,?)', (key,json.dumps(blocked,sort_keys=True)))
            results.append(blocked)
    return results


def check_coinex(collector):
    """Optional provider health; never prevents the independent OKX cycle."""
    url = 'https://api.coinex.com/v2/spot/ticker?' + urlencode({'market':'BTCUSDT'})
    try:
        with urlopen(Request(url, headers={'User-Agent':'parsa-forward-observer/1'}), timeout=10) as response:
            raw = response.read()
        observed = datetime.now(timezone.utc)
        digest = collector.archive(url, raw, observed)
        if json.loads(raw).get('code') != 0:
            raise ValueError('COINEX_API_ERROR')
        with collector.connect() as db:
            db.execute('INSERT INTO collector_health(observed_at,venue,status,detail) VALUES (?,?,?,?)', (observed.isoformat(),'COINEX','PUBLIC_RESPONSE',digest))
    except Exception as exc:
        collector.fail(datetime.now(timezone.utc),'COINEX',f'{type(exc).__name__}: {exc}')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db', type=Path, required=True)
    p.add_argument('--poll-seconds', type=int, default=60)
    p.add_argument('--cycles', type=int, default=0, help='0 runs continuously')
    p.add_argument('--check-coinex', action='store_true', help='check CoinEx independently on startup')
    args = p.parse_args()
    if args.cycles < 0:
        p.error('--cycles must be nonnegative')
    c = Collector(args.db, poll_seconds=args.poll_seconds)
    failures = 0
    had_error = False
    try:
        if args.check_coinex:
            check_coinex(c)
        iteration = 0
        while not args.cycles or iteration < args.cycles:
            try:
                result = cycle(c)
                failures = 0
                print(json.dumps({'status':'OKX_OK','run_id':c.run_id,'decisions':result},sort_keys=True), flush=True)
            except Exception as exc:
                failures += 1
                had_error = True
                c.fail(datetime.now(timezone.utc), 'OKX', f'{type(exc).__name__}: {exc}')
                print(json.dumps({'status':'OKX_ERROR','error':str(exc),'run_id':c.run_id}), flush=True)
            iteration += 1
            if not args.cycles or iteration < args.cycles:
                time.sleep(min(300, args.poll_seconds * (2 ** min(failures, 3))))
    except KeyboardInterrupt:
        pass
    finally:
        c.close()
    return 2 if had_error else 0


if __name__ == '__main__':
    sys.exit(main())
