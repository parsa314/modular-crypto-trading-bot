"""Offline, read-only SQLite V1 -> empty engineering PostgreSQL journal.

Callers must stop every SQLite writer first. flock coordinates cooperative OMS
instances; SQLite's read transaction gives a consistent WAL-aware snapshot.
The source is never edited. Unknown/pending/unprotected exposure is preserved,
not blessed: migration is not reconciliation or deployment authorization.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat

from .ledger import (LiveLedger, _STATES, _TABLES, _identifier,
                     _merge_order_result, _public_json)


def _decode(encoded):
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ValueError('duplicate JSON key in source')
            result[k] = v
        return result
    value = json.loads(encoded, object_pairs_hook=pairs)
    _public_json(value)
    return value


@contextmanager
def sqlite_snapshot(source):
    path = Path(source).absolute()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    connection = None
    try:
        observed = os.fstat(fd)
        if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
            raise ValueError('source must be an unaliased regular SQLite file')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('stop the SQLite executor before migration') from exc
        connection = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('source SQLite integrity check failed')
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if tables != _TABLES:
            raise ValueError('incompatible source schema')
        metadata = connection.execute('SELECT * FROM live_metadata').fetchall()
        if len(metadata) != 1 or metadata[0]['id'] != 1 or metadata[0]['schema_version'] != 1:
            raise ValueError('incompatible source metadata')
        identity = _decode(metadata[0]['identity_json'])
        decisions = [{**dict(r), 'payload': _decode(r['payload_json'])} for r in connection.execute('SELECT * FROM live_decisions ORDER BY rowid')]
        orders = [{'id': r['client_order_id'], 'client_order_id': r['client_order_id'],
                   'intent': _decode(r['intent_json']), 'state': r['state'], 'result': _decode(r['result_json'])}
                  for r in connection.execute('SELECT * FROM live_orders ORDER BY rowid')]
        for row in orders:
            _identifier(row['client_order_id'], 'client_order_id')
            if row['state'] not in _STATES:
                raise ValueError('invalid source order state')
            _public_json(row['intent'])
            result = _decode(_public_json(row['result']))
            _merge_order_result('SUBMITTING', {'filled': 0.}, row['state'], result)
        for row in decisions:
            _identifier(row['decision_id'], 'decision_id')
        states = connection.execute('SELECT * FROM live_state').fetchall()
        if len(states)>1 or (states and states[0]['id'] != 1):
            raise ValueError('invalid source account state')
        state = _decode(states[0]['state_json']) if states else {}
        candles = [LiveLedger._candle(dict(r)) for r in connection.execute('SELECT * FROM live_candles ORDER BY timestamp_ms')]
        snapshot = {'version': 1, 'identity': identity, 'state': state,
                    'decisions': [{'decision_id': r['decision_id'], 'payload': r['payload']} for r in decisions],
                    'orders': orders, 'candles': candles, 'timeframe_ms': metadata[0]['timeframe_ms']}
        current = path.stat()
        if (current.st_dev, current.st_ino) != (observed.st_dev, observed.st_ino):
            raise ValueError('source file changed during snapshot')
        yield snapshot
    finally:
        if connection is not None:
            connection.close()
        os.close(fd)


def migrate_sqlite(source, target):
    """All target changes commit together or none; repeated snapshot is a no-op.

    Target must be an initialized, identity-matching, otherwise empty journal.
    Existing target data is never overwritten. Source cumulative receipt history
    is unavailable: only the final receipt revision can be imported honestly.
    """
    with sqlite_snapshot(source) as snapshot, target.session_lock():
        if snapshot['identity'] != target.identity:
            raise ValueError('source/target identity mismatch')
        encoded = _public_json(snapshot)
        source_hash = hashlib.sha256(encoded.encode()).hexdigest()
        with target._transaction() as conn:
            row = conn.execute('SELECT migration_sha256 FROM convergence_journals WHERE journal_id=%s', (target.journal_id,)).fetchone()
            if row['migration_sha256']:
                if row['migration_sha256'] != source_hash:
                    raise ValueError('source changed after migration; no overwrite permitted')
                return {'status': 'ALREADY_IMPORTED', 'snapshot_sha256': source_hash}
            for table in ('convergence_decisions', 'convergence_orders', 'convergence_account_state', 'convergence_candles', 'convergence_heartbeats', 'convergence_positions'):
                if conn.execute(f'SELECT 1 FROM {table} WHERE journal_id=%s LIMIT 1', (target.journal_id,)).fetchone():
                    raise ValueError('target journal must be empty')
            for row in snapshot['decisions']:
                target.claim_decision(row['decision_id'], row['payload'])
            for row in snapshot['orders']:
                target.claim_order(row['client_order_id'], row['intent'])
                target.update_order(row['client_order_id'], row['state'], row['result'])
            if snapshot['state']:
                target.set_state(snapshot['state'])
            if snapshot['candles']:
                target.append_candles(snapshot['candles'], snapshot['timeframe_ms'])
            elif snapshot['timeframe_ms'] is not None:
                raise ValueError('source timeframe without candle history')
            conn.execute('UPDATE convergence_journals SET migration_sha256=%s WHERE journal_id=%s', (source_hash, target.journal_id))
        return {'status': 'IMPORTED', 'snapshot_sha256': source_hash,
                'orders': len(snapshot['orders']), 'decisions': len(snapshot['decisions']),
                'candles': len(snapshot['candles']), 'execution_authorized': False}
