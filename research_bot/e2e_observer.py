"""Offline/online observation increment. No broker or order submission.

Financial gate remains blocked: this module cannot create simulated orders.
Historical data must not be relabelled as prospective first-seen data.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from .coinex_public import COINEX_BASE_URL, fetch_coinex_klines
from .strategies import al_brooks_strategy, ichimoku_strategy, ict_strategy, smc_strategy

STRATEGY_SHA = '81071995fd7c5a27830d92780801cd0783e108d9'
ARMS = {'ICHIMOKU': ichimoku_strategy, 'ICT': ict_strategy,
        'SMC': smc_strategy, 'AL_BROOKS': al_brooks_strategy}


def _utc(value):
    if value is None or pd.isna(value):
        raise ValueError('MISSING_TIMESTAMP')
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        raise ValueError('TIMEZONE_REQUIRED')
    return ts.tz_convert('UTC')


def explain_candidates(results, *, as_of):
    """Describe detector agreement without treating overlap as independence.

    This is observation metadata, not an economic forecast or order approval.
    """
    now = _utc(as_of)
    if set(results) != set(ARMS):
        raise ValueError('EXACT_FOUR_STRATEGIES_REQUIRED')
    supporters = {'BUY': [], 'SELL': []}
    for family, signal in results.items():
        if signal.get('style') != family or signal.get('action') not in ('BUY', 'SELL', 'HOLD'):
            raise ValueError('INVALID_STRATEGY_OUTPUT')
        if _utc(signal.get('decision_at')) > now:
            raise ValueError('FUTURE_STRATEGY_OUTPUT')
        if signal['action'] in supporters:
            supporters[signal['action']].append(family)
    groups = {}
    for side, families in supporters.items():
        # ICT and SMC are related OHLC structure proxies; group them for
        # disclosure, not as a statistically measured correlation estimate.
        groups[side] = sorted({'ICT_SMC_STRUCTURE' if x in ('ICT', 'SMC') else x for x in families})
    return {'supporters': supporters, 'evidence_groups': groups,
            'conflict': bool(supporters['BUY'] and supporters['SELL']),
            'independence_status': 'NOT_ESTABLISHED',
            'score_semantics': 'ORDINAL_NOT_PROBABILITY'}


def evaluate(frame, *, as_of, symbol, source, max_staleness='2h'):
    """Evaluate four existing exploratory arms using only valid closed H1 bars."""
    now = _utc(as_of)
    if symbol not in ('BTC/USDT', 'ETH/USDT') or not source:
        raise ValueError('INVALID_SYMBOL_OR_SOURCE')
    x = frame.copy()
    for field in ('timestamp', 'available_at'):
        if field not in x:
            raise ValueError('MISSING_TIMESTAMP')
        x[field] = x[field].map(_utc)
    if x.timestamp.duplicated().any() or not x.timestamp.is_monotonic_increasing:
        raise ValueError('DUPLICATE_OR_UNORDERED_BARS')
    if not (x.timestamp == x.timestamp.dt.floor('h')).all():
        raise ValueError('BAR_ALIGNMENT')
    if not x.timestamp.diff().dropna().eq(pd.Timedelta(hours=1)).all():
        raise ValueError('BAR_GAP')
    if (x.available_at < x.timestamp + pd.Timedelta(hours=1)).any():
        raise ValueError('BAR_AVAILABLE_BEFORE_CLOSE')
    x = x.loc[(x.available_at <= now) & (x.timestamp + pd.Timedelta(hours=1) <= now)].copy()
    if len(x) < 90:
        raise ValueError('INSUFFICIENT_CAUSAL_BARS')
    if now - (x.timestamp.iloc[-1] + pd.Timedelta(hours=1)) > pd.Timedelta(max_staleness):
        raise ValueError('STALE_BAR')
    results = {name: asdict(module.evaluate(x, as_of=now.to_pydatetime()))
               for name, module in ARMS.items()}
    analysis = explain_candidates(results, as_of=now)
    # Detector output is research evidence only. The source timestamp alone
    # cannot establish an executable setup or identify a calibrated model.
    data_available_at = x.available_at.max()
    signals = []
    for name, signal in results.items():
        score = signal.get('score')
        if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 3:
            raise ValueError('INVALID_ORDINAL_SCORE')
        signals.append({'strategy_id': name, 'symbol': symbol, 'timeframe': 'H1',
                        'action': signal['action'], 'decision_at': now.isoformat(),
                        'detector_timestamp': _utc(signal['decision_at']).isoformat(),
                        'available_at': now.isoformat(),
                        'latest_input_available_at': data_available_at.isoformat(),
                        'evidence': signal.get('evidence', {}), 'ordinal_score': score,
                        'contract_status': 'INCOMPLETE_SETUP' if signal['action'] != 'HOLD' else 'HOLD',
                        'invalidation_reason': 'NOT_SPECIFIED_BY_DETECTOR',
                        'provenance': source, 'strategy_version': STRATEGY_SHA})
    reasons = ['ML_NOT_PROMOTED', 'FINANCIAL_GATE_BLOCKED', 'RISK_NOT_EVALUATED', 'COST_NOT_VERIFIED']
    if analysis['conflict']:
        reasons.append('CONFLICTING_SIGNALS')
    # A research score is never a calibrated probability or permission to trade.
    decision = {'symbol': symbol, 'source': source, 'as_of': now.isoformat(),
                'bar_open': x.timestamp.iloc[-1].isoformat(), 'strategy_sha': STRATEGY_SHA,
                'strategies': results, 'candidate_analysis': analysis,
                'research_signals': signals,
                'trade_candidate': {'status': 'NOT_EVALUATED', 'reason': 'COST_AND_SETUP_NOT_VERIFIED'},
                'execution_authorization': {'status': 'DENIED', 'reason': 'FINANCIAL_GATE_BLOCKED'},
                'action': 'NO_TRADE', 'reason_codes': reasons,
                'risk_status': 'NOT_EVALUATED', 'cost_status': 'NOT_VERIFIED',
                'execution_mode': 'OBSERVATION_ONLY', 'paper_orders': 0}
    return json.loads(json.dumps(decision, sort_keys=True, default=str, allow_nan=False))


def store_decision(path, decision):
    payload = json.dumps(decision, sort_keys=True, separators=(',', ':'), allow_nan=False)
    key = hashlib.sha256(payload.encode()).hexdigest()
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS observations (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        db.execute('INSERT OR IGNORE INTO observations VALUES (?, ?)', (key, payload))
    return key


def live_observe(symbol, db_path):
    """Capture raw public CoinEx responses; historical bootstrap is not first-seen."""
    received = []
    seen = set()
    def request(path, params, timeout):
        url = COINEX_BASE_URL + path + '?' + urlencode(params)
        with urlopen(Request(url, headers={'User-Agent': 'parsa-observation/1'}), timeout=timeout) as response:
            raw = response.read()
        at = datetime.now(timezone.utc)
        payload = json.loads(raw)
        if payload.get('code') != 0:
            raise ValueError('COINEX_API_ERROR')
        rows = payload.get('data') or []
        timestamps = [r.get('created_at') for r in rows]
        if len(timestamps) != len(set(timestamps)):
            raise ValueError('DUPLICATE_PROVIDER_BARS')
        if any(ts in seen for ts in timestamps):
            raise ValueError('DUPLICATE_PROVIDER_BARS_ACROSS_RESPONSES')
        seen.update(timestamps)
        digest = hashlib.sha256(raw).hexdigest()
        with sqlite3.connect(db_path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS raw_responses (hash TEXT PRIMARY KEY, endpoint TEXT, received_at TEXT, payload BLOB)')
            db.execute('INSERT OR IGNORE INTO raw_responses VALUES (?,?,?,?)', (digest, url, at.isoformat(), raw))
        received.append((at, digest))
        return payload
    frame = fetch_coinex_klines(symbol=symbol, period='1hour', bars=120, request_fn=request)
    if not received:
        raise ValueError('NO_PROVIDER_RESPONSE')
    observed = max(at for at, _ in received)
    frame['available_at'] = pd.Timestamp(observed)
    # Remove forming bars before checking the availability rule.
    frame = frame.loc[frame.timestamp + pd.Timedelta(hours=1) <= pd.Timestamp(observed)].copy()
    decision = evaluate(frame, as_of=observed, symbol=symbol, source='HISTORICAL_BOOTSTRAP_PUBLIC_REST')
    decision.update(raw_hashes=[h for _, h in received], first_seen_forward=False)
    decision['decision_id'] = store_decision(db_path, decision)
    return decision


def okx_observe(symbol, db_path):
    """Read OKX public spot history; this bootstrap is not first-seen forward."""
    market = symbol.replace('/', '-')
    url = 'https://www.okx.com/api/v5/market/candles?' + urlencode(
        {'instId': market, 'bar': '1H', 'limit': 100})
    with urlopen(Request(url, headers={'User-Agent': 'parsa-observation/1'}), timeout=20) as response:
        raw = response.read()
    observed = datetime.now(timezone.utc)
    payload = json.loads(raw)
    if payload.get('code') != '0' or not isinstance(payload.get('data'), list):
        raise ValueError('OKX_API_ERROR')
    digest = hashlib.sha256(raw).hexdigest()
    with sqlite3.connect(db_path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS raw_responses (hash TEXT PRIMARY KEY, endpoint TEXT, received_at TEXT, payload BLOB)')
        db.execute('INSERT OR IGNORE INTO raw_responses VALUES (?,?,?,?)', (digest, url, observed.isoformat(), raw))
    rows = []
    for item in payload['data']:
        if len(item) < 9 or item[8] not in ('0', '1'):
            raise ValueError('INVALID_OKX_CANDLE')
        if item[8] == '1':
            rows.append({'timestamp': pd.to_datetime(int(item[0]), unit='ms', utc=True),
                         'open': item[1], 'high': item[2], 'low': item[3],
                         'close': item[4], 'volume': item[5], 'available_at': observed})
    if not rows:
        raise ValueError('NO_CLOSED_CANDLES')
    frame = pd.DataFrame(rows).sort_values('timestamp').reset_index(drop=True)
    decision = evaluate(frame, as_of=observed, symbol=symbol, source='OKX_HISTORICAL_BOOTSTRAP_PUBLIC_REST')
    decision.update(raw_hashes=[digest], first_seen_forward=False)
    decision['decision_id'] = store_decision(db_path, decision)
    return decision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbol', choices=('BTC/USDT', 'ETH/USDT'), default='BTC/USDT')
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--venue', choices=('coinex', 'okx'), default='coinex')
    args = parser.parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    exit_code = 0
    try:
        result = (live_observe if args.venue == 'coinex' else okx_observe)(args.symbol, args.db)
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
        exit_code = 2
        result = {'status': 'NOT_CONNECTED_OR_INVALID_DATA', 'error_type': type(exc).__name__,
                  'error': str(exc), 'execution_mode': 'OBSERVATION_ONLY',
                  'observed_at': datetime.now(timezone.utc).isoformat()}
        result['decision_id'] = store_decision(args.db, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
