"""Durable PostgreSQL journal for the existing Fake-only OMS.

No implicit DATABASE_URL discovery, credentials for exchanges, model imports,
or execution authorization. Use an explicit isolated test DSN. A dedicated PG
session owns the advisory lock across network/reconciliation cycles; transaction
row locks serialize state changes. Connection loss never causes reconnection or
lock reacquisition behind the caller's back.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading

import psycopg
from psycopg.rows import dict_row

from .ledger import (LiveLedger, _CANDLE_FIELDS, _PENDING, _STATES, _identifier,
                     _merge_order_result, _number, _positive_int, _public_json)


SCHEMA_PATH = Path(__file__).with_name('postgres_schema.sql')


def _lock_key(value):
    return int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], 'big', signed=True)


class PostgresLedger:
    """One identity-pinned journal, compatible with LiveLedger's public seam.

    Advisory locks are account-scoped (not model/journal-scoped). They coordinate
    cooperating executors against this database only. No TTL/fencing claim is
    made for actual remote exchange execution, which remains forbidden.
    """
    def __init__(self, database_url: str, journal_id: str, identity: dict):
        self.journal_id = _identifier(journal_id, 'journal_id')
        encoded = _public_json(identity)
        if not identity or identity.get('mode') != 'ENGINEERING_REPLAY':
            raise ValueError('PostgreSQL journal is ENGINEERING_REPLAY only')
        account = _identifier(identity.get('account_id'), 'account_id')
        if not isinstance(database_url, str) or not database_url.strip():
            raise ValueError('explicit database_url required')
        self._guard = threading.RLock()
        self._pid, self._closed, self._locked = os.getpid(), False, False
        self._lock_key = _lock_key('convergence-account-v1:' + account)
        self._connection = psycopg.connect(database_url, autocommit=True, row_factory=dict_row,
            connect_timeout=5, options='-c statement_timeout=5000 -c lock_timeout=2000',
            application_name='convergence-engineering-journal')
        try:
            schema = SCHEMA_PATH.read_text()
            schema_hash = hashlib.sha256(schema.encode()).hexdigest()
            with self._connection.transaction():
                self._connection.execute('SELECT pg_advisory_xact_lock(%s)', (_lock_key('convergence-schema-v1'),))
                self._connection.execute(schema)
                self._connection.execute('INSERT INTO convergence_schema VALUES(1,1,%s) ON CONFLICT DO NOTHING', (schema_hash,))
                row = self._connection.execute('SELECT * FROM convergence_schema WHERE singleton=1').fetchone()
                if row['version'] != 1 or row['schema_sha256'] != schema_hash:
                    raise ValueError('PostgreSQL schema version/hash mismatch')
                self._connection.execute('INSERT INTO convergence_journals(journal_id,account_id,identity_json) VALUES(%s,%s,%s) ON CONFLICT(journal_id) DO NOTHING',
                                         (self.journal_id, account, encoded))
                row = self._connection.execute('SELECT identity_json FROM convergence_journals WHERE journal_id=%s FOR UPDATE',
                                               (self.journal_id,)).fetchone()
                if row['identity_json'] != encoded:
                    raise ValueError('ledger identity does not match')
        except BaseException:
            self.close()
            raise

    def _ensure_open(self):
        if os.getpid() != self._pid:
            raise RuntimeError('a ledger instance cannot be shared after a process fork')
        if self._closed or self._connection.closed or self._connection.broken:
            raise RuntimeError('ledger is closed or disconnected; reconciliation required')

    @contextmanager
    def _transaction(self):
        with self._guard:
            self._ensure_open()
            with self._connection.transaction():
                # Serializes even the absent account_state case, preventing two
                # settlements from both observing the same empty previous state.
                self._connection.execute('SELECT journal_id FROM convergence_journals WHERE journal_id=%s FOR UPDATE', (self.journal_id,))
                yield self._connection

    @contextmanager
    def session_lock(self):
        # Hold the thread guard for the whole cycle: a second thread cannot use
        # a connection whose account lock is owned by the first thread.
        with self._guard:
            self._ensure_open()
            if self._locked:
                raise RuntimeError('ledger session lock is already held')
            row = self._connection.execute('SELECT pg_try_advisory_lock(%s) AS acquired', (self._lock_key,)).fetchone()
            if not row['acquired']:
                raise RuntimeError('another runner holds the ledger session lock')
            self._locked = True
            try:
                yield self
            finally:
                if not self._closed and not self._connection.closed:
                    self._connection.execute('SELECT pg_advisory_unlock(%s)', (self._lock_key,))
                self._locked = False

    def close(self):
        with self._guard:
            if os.getpid() != self._pid:
                raise RuntimeError('a ledger instance cannot be shared after a process fork')
            if not self._closed:
                self._connection.close()
                self._closed, self._locked = True, False

    def __enter__(self):
        self._ensure_open()
        return self

    def __exit__(self, *args):
        self.close()

    @property
    def identity(self):
        with self._guard:
            self._ensure_open()
            return json.loads(self._connection.execute('SELECT identity_json FROM convergence_journals WHERE journal_id=%s',
                              (self.journal_id,)).fetchone()['identity_json'])

    def claim_decision(self, decision_id, payload):
        decision_id, encoded = _identifier(decision_id, 'decision_id'), _public_json(payload)
        with self._transaction() as conn:
            return conn.execute('INSERT INTO convergence_decisions VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',
                                (self.journal_id, decision_id, encoded)).rowcount == 1

    def claim_order(self, client_order_id, intent):
        client_order_id, encoded = _identifier(client_order_id, 'client_order_id'), _public_json(intent)
        with self._transaction() as conn:
            return conn.execute("INSERT INTO convergence_orders(journal_id,client_order_id,intent_json,state,result_json) VALUES(%s,%s,%s,'SUBMITTING',%s) ON CONFLICT DO NOTHING",
                                (self.journal_id, client_order_id, encoded, _public_json({'filled': 0.}))).rowcount == 1

    def get_order(self, client_order_id):
        client_order_id = _identifier(client_order_id, 'client_order_id')
        with self._guard:
            self._ensure_open()
            return LiveLedger._order(self._connection.execute('SELECT * FROM convergence_orders WHERE journal_id=%s AND client_order_id=%s',
                                 (self.journal_id, client_order_id)).fetchone())

    def pending_orders(self):
        with self._guard:
            self._ensure_open()
            return [LiveLedger._order(r) for r in self._connection.execute('SELECT * FROM convergence_orders WHERE journal_id=%s AND state=ANY(%s) ORDER BY sequence',
                    (self.journal_id, list(_PENDING))).fetchall()]

    def get_state(self):
        with self._guard:
            self._ensure_open()
            row = self._connection.execute('SELECT state_json FROM convergence_account_state WHERE journal_id=%s', (self.journal_id,)).fetchone()
            return {} if row is None else json.loads(row['state_json'])

    def _write_state(self, conn, encoded):
        state = json.loads(encoded)
        positions = state.get('positions', {})
        if not isinstance(positions, dict):
            raise ValueError('positions must be a symbol/quantity dictionary')
        quantities = [(_identifier(s, 'symbol'), _number(q, 'position quantity')) for s, q in positions.items()]
        conn.execute('INSERT INTO convergence_account_state VALUES(%s,%s) ON CONFLICT(journal_id) DO UPDATE SET state_json=excluded.state_json', (self.journal_id, encoded))
        conn.execute('DELETE FROM convergence_positions WHERE journal_id=%s', (self.journal_id,))
        for symbol, quantity in quantities:
            conn.execute('INSERT INTO convergence_positions VALUES(%s,%s,%s)', (self.journal_id, symbol, quantity))

    def set_state(self, state):
        encoded = _public_json(state)
        with self._transaction() as conn:
            self._write_state(conn, encoded)

    def update_order(self, client_order_id, status, result, *, account_state=None, expected_account_state=None):
        client_order_id = _identifier(client_order_id, 'client_order_id')
        if status not in _STATES:
            raise ValueError('unknown order status')
        normalized = json.loads(_public_json(result))
        if (account_state is None) != (expected_account_state is None):
            raise ValueError('atomic settlement requires previous and next account state')
        encoded = None if account_state is None else _public_json(account_state)
        expected = None if expected_account_state is None else _public_json(expected_account_state)
        with self._transaction() as conn:
            if encoded is not None:
                row = conn.execute('SELECT state_json FROM convergence_account_state WHERE journal_id=%s', (self.journal_id,)).fetchone()
                if (row['state_json'] if row else '{}') != expected:
                    raise RuntimeError('account state changed before settlement')
            row = conn.execute('SELECT * FROM convergence_orders WHERE journal_id=%s AND client_order_id=%s FOR UPDATE', (self.journal_id, client_order_id)).fetchone()
            if row is None:
                raise KeyError('order ID has not been claimed')
            merged = _merge_order_result(row['state'], json.loads(row['result_json']), status, normalized)
            receipt = _public_json(merged)
            conn.execute('UPDATE convergence_orders SET state=%s,result_json=%s WHERE journal_id=%s AND client_order_id=%s', (status, receipt, self.journal_id, client_order_id))
            if merged['filled'] > 0:
                conn.execute('INSERT INTO convergence_fill_revisions VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                             (self.journal_id, client_order_id, hashlib.sha256(receipt.encode()).hexdigest(), receipt))
            if encoded is not None:
                self._write_state(conn, encoded)

    def heartbeat(self, observed_at: datetime, payload: dict):
        if not isinstance(observed_at, datetime) or observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError('heartbeat requires an aware timestamp')
        encoded = _public_json(payload)
        with self._transaction() as conn:
            row = conn.execute('SELECT observed_at FROM convergence_heartbeats WHERE journal_id=%s', (self.journal_id,)).fetchone()
            if row and observed_at <= row['observed_at']:
                raise ValueError('heartbeat timestamp must advance')
            conn.execute('INSERT INTO convergence_heartbeats VALUES(%s,%s,%s) ON CONFLICT(journal_id) DO UPDATE SET observed_at=excluded.observed_at,payload_json=excluded.payload_json',
                         (self.journal_id, observed_at.astimezone(timezone.utc), encoded))

    def append_candles(self, rows, timeframe_ms):
        timeframe_ms = _positive_int(timeframe_ms, 'timeframe_ms')
        if not isinstance(rows, list):
            raise ValueError('candles must be supplied as a list')
        candles = [LiveLedger._candle(r) for r in rows]
        if any(b['timestamp_ms']-a['timestamp_ms'] != timeframe_ms for a,b in zip(candles, candles[1:])):
            raise ValueError('candle batch must have unique sorted regular timestamps')
        with self._transaction() as conn:
            metadata = conn.execute('SELECT timeframe_ms FROM convergence_journals WHERE journal_id=%s', (self.journal_id,)).fetchone()
            if metadata['timeframe_ms'] is not None and metadata['timeframe_ms'] != timeframe_ms:
                raise ValueError('candle timeframe does not match this ledger')
            limits = conn.execute('SELECT MIN(timestamp_ms) AS first,MAX(timestamp_ms) AS last FROM convergence_candles WHERE journal_id=%s', (self.journal_id,)).fetchone()
            first, last = limits['first'], limits['last']
            for candle in candles:
                timestamp = candle['timestamp_ms']
                row = conn.execute('SELECT timestamp_ms,open,high,low,close,volume FROM convergence_candles WHERE journal_id=%s AND timestamp_ms=%s', (self.journal_id, timestamp)).fetchone()
                if row:
                    if row != candle:
                        raise ValueError('previously observed candle was revised')
                    continue
                if first is not None and timestamp < first:
                    raise ValueError('older candle history cannot be prepended')
                if last is not None and timestamp != last+timeframe_ms:
                    raise ValueError('new candle must be consecutive; gaps are forbidden')
                conn.execute('INSERT INTO convergence_candles VALUES(%s,%s,%s,%s,%s,%s,%s)',
                             (self.journal_id, *(candle[k] for k in _CANDLE_FIELDS)))
                first = timestamp if first is None else first
                last = timestamp
            if candles and metadata['timeframe_ms'] is None:
                conn.execute('UPDATE convergence_journals SET timeframe_ms=%s WHERE journal_id=%s', (timeframe_ms, self.journal_id))

    def candles(self):
        with self._guard:
            self._ensure_open()
            return self._connection.execute('SELECT timestamp_ms,open,high,low,close,volume FROM convergence_candles WHERE journal_id=%s ORDER BY timestamp_ms', (self.journal_id,)).fetchall()
