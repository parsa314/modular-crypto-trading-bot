import multiprocessing
import os
import sqlite3

import pytest

from research_bot.live_ledger import LiveLedger


IDENTITY = {
    "exchange": "fixture", "symbol": "BTC/USDT", "account_mode": "spot",
    "account_id_hash": "public-account-fingerprint", "model_sha256": "a" * 64,
}


def _crash_after_claim(path, identity):
    with LiveLedger(path, identity) as ledger:
        with ledger.session_lock():
            assert ledger.claim_decision("closed-bar-1", {"timestamp_ms": 1000})
            assert ledger.claim_order("client-1", {"symbol": "BTC/USDT", "amount": 1.0})
            os._exit(27)


def _claim_worker(path, identity, event, queue):
    try:
        with LiveLedger(path, identity) as ledger:
            event.wait(10)
            queue.put(("ok", ledger.claim_order("same-id", {"amount": 1.0})))
    except Exception as exc:
        queue.put(("error", type(exc).__name__))


def _lock_worker(path, identity, queue):
    try:
        with LiveLedger(path, identity) as ledger:
            with ledger.session_lock():
                queue.put("acquired")
    except RuntimeError:
        queue.put("locked")


def _update_worker(path, identity, event, queue, status, filled):
    with LiveLedger(path, identity) as ledger:
        event.wait(10)
        try:
            ledger.update_order("shared-order", status, {"filled": filled})
            queue.put("updated")
        except ValueError:
            queue.put("rejected")


def _candle(timestamp=0, close=100.0):
    return {"timestamp_ms": timestamp, "open": 100.0, "high": 110.0,
            "low": 90.0, "close": close, "volume": 2.0}


def test_restart_preserves_claims_identity_and_risk_state(tmp_path):
    path = tmp_path / "live.sqlite"
    intent = {"symbol": "BTC/USDT", "amount": 1.0}
    with LiveLedger(path, IDENTITY) as ledger:
        assert ledger.get_state() == {}
        assert ledger.claim_decision("d1", {"timestamp_ms": 1})
        assert ledger.claim_order("o1", intent)
        ledger.set_state({"peak_equity": 1000.0, "last_bar_ms": 1, "halted": False})
    with LiveLedger(path, dict(reversed(list(IDENTITY.items())))) as ledger:
        assert not ledger.claim_decision("d1", {"timestamp_ms": 1})
        assert not ledger.claim_order("o1", {"amount": 2.0})
        order = ledger.get_order("o1")
        assert order["id"] == "o1"
        assert order["intent"] == intent
        assert order["state"] == "SUBMITTING"
        assert order["result"]["filled"] == 0.0
        assert ledger.get_state() == {"peak_equity": 1000.0, "last_bar_ms": 1, "halted": False}


@pytest.mark.parametrize("field", ["exchange", "symbol", "account_mode", "account_id_hash", "model_sha256"])
def test_changed_identity_fails_closed(tmp_path, field):
    path = tmp_path / "live.sqlite"
    with LiveLedger(path, IDENTITY):
        pass
    changed = {**IDENTITY, field: "different"}
    with pytest.raises(ValueError, match="identity"):
        LiveLedger(path, changed)
    with LiveLedger(path, IDENTITY) as ledger:
        assert ledger.claim_order("safe-after-rejection", {})


def test_unknown_keeps_exchange_id_fills_and_remains_pending(tmp_path):
    with LiveLedger(tmp_path / "live.sqlite", IDENTITY) as ledger:
        assert ledger.claim_order("o1", {})
        ledger.update_order("o1", "OPEN", {"id": "exchange-1", "filled": 0.0})
        ledger.update_order("o1", "PARTIAL", {"filled": 0.4})
        ledger.update_order("o1", "UNKNOWN", {"reason": "timeout"})
        pending = ledger.pending_orders()
        assert pending[0]["state"] == "UNKNOWN"
        assert pending[0]["result"] == {"id": "exchange-1", "filled": 0.4, "reason": "timeout"}
        assert not ledger.claim_order("o1", {})
        ledger.update_order("o1", "FILLED", {"filled": 1.0})
        assert ledger.pending_orders() == []


def test_pending_orders_contains_every_unresolved_state(tmp_path):
    with LiveLedger(tmp_path / "live.sqlite", IDENTITY) as ledger:
        for state in ("SUBMITTING", "UNKNOWN", "OPEN", "PARTIAL", "FILLED", "CANCELLED", "REJECTED"):
            assert ledger.claim_order(state, {})
            if state != "SUBMITTING":
                ledger.update_order(state, state, {"filled": 1.0 if state in ("PARTIAL", "FILLED") else 0.0})
        assert [row["id"] for row in ledger.pending_orders()] == ["SUBMITTING", "UNKNOWN", "OPEN", "PARTIAL"]
        assert ledger.get_order("missing") is None


@pytest.mark.parametrize("terminal", ["FILLED", "CANCELLED", "REJECTED"])
def test_terminal_state_cannot_regress(tmp_path, terminal):
    with LiveLedger(tmp_path / "live.sqlite", IDENTITY) as ledger:
        ledger.claim_order("o1", {})
        filled = 1.0 if terminal == "FILLED" else 0.0
        ledger.update_order("o1", terminal, {"filled": filled})
        with pytest.raises(ValueError, match="transition"):
            ledger.update_order("o1", "OPEN", {"filled": filled})
        ledger.update_order("o1", terminal, {"filled": filled, "confirmed": True})
        assert ledger.get_order("o1")["state"] == terminal
        assert ledger.pending_orders() == []


def test_filled_quantity_and_partial_state_cannot_regress(tmp_path):
    with LiveLedger(tmp_path / "live.sqlite", IDENTITY) as ledger:
        ledger.claim_order("o1", {})
        ledger.update_order("o1", "PARTIAL", {"filled": 0.5})
        for state, filled in (("PARTIAL", 0.4), ("UNKNOWN", -1), ("OPEN", 0.5), ("SUBMITTING", 0.5)):
            with pytest.raises(ValueError):
                ledger.update_order("o1", state, {"filled": filled})
        assert ledger.get_order("o1")["result"]["filled"] == 0.5
        with pytest.raises(KeyError):
            ledger.update_order("missing", "UNKNOWN", {})


def test_crash_leaves_submitting_claim_and_releases_process_lock(tmp_path):
    path = tmp_path / "live.sqlite"
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=_crash_after_claim, args=(str(path), IDENTITY))
    process.start()
    process.join(15)
    assert process.exitcode == 27
    with LiveLedger(path, IDENTITY) as ledger:
        with ledger.session_lock():
            assert ledger.pending_orders()[0]["state"] == "SUBMITTING"
            assert not ledger.claim_order("client-1", {})
            assert not ledger.claim_decision("closed-bar-1", {})


def test_concurrent_claims_have_only_one_winner(tmp_path):
    path = tmp_path / "live.sqlite"
    with LiveLedger(path, IDENTITY):
        pass
    context = multiprocessing.get_context("spawn")
    event, queue = context.Event(), context.Queue()
    workers = [context.Process(target=_claim_worker, args=(str(path), IDENTITY, event, queue)) for _ in range(4)]
    for worker in workers:
        worker.start()
    event.set()
    results = [queue.get(timeout=15) for _ in workers]
    for worker in workers:
        worker.join(15)
        assert worker.exitcode == 0
    assert all(kind == "ok" for kind, _ in results)
    assert sorted(value for _, value in results) == [False, False, False, True]


def test_exclusive_session_lock_blocks_process_and_same_process_runner(tmp_path):
    path = tmp_path / "live.sqlite"
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    with LiveLedger(path, IDENTITY) as first, LiveLedger(path, IDENTITY) as second:
        with first.session_lock():
            with pytest.raises(RuntimeError, match="lock"):
                with second.session_lock():
                    pytest.fail("second runner entered")
            with pytest.raises(RuntimeError, match="lock"):
                with first.session_lock():
                    pytest.fail("nested session entered")
            process = context.Process(target=_lock_worker, args=(str(path), IDENTITY, queue))
            process.start()
            assert queue.get(timeout=15) == "locked"
            process.join(15)
            assert process.exitcode == 0
        with second.session_lock():
            assert second.claim_decision("new-session", {})


def test_hardlink_aliases_cannot_use_separate_sqlite_wal_histories(tmp_path):
    path, alias = tmp_path / "live.sqlite", tmp_path / "alias.sqlite"
    with LiveLedger(path, IDENTITY) as ledger:
        ledger.claim_order("o1", {})
        os.link(path, alias)
        with pytest.raises(ValueError, match="hardlink"):
            LiveLedger(alias, IDENTITY)
        with pytest.raises(RuntimeError, match="hardlink"):
            with ledger.session_lock():
                pytest.fail("hardlinked journal acquired a runner lock")
        with pytest.raises(RuntimeError, match="hardlink"):
            ledger.claim_order("o2", {})
        alias.unlink()
        assert ledger.get_order("o1")["state"] == "SUBMITTING"


def test_symlink_paths_resolve_to_the_same_lock_and_wal(tmp_path):
    path, alias = tmp_path / "live.sqlite", tmp_path / "alias.sqlite"
    with LiveLedger(path, IDENTITY) as first:
        alias.symlink_to(path)
        with LiveLedger(alias, IDENTITY) as second:
            assert first.path == second.path
            first.claim_order("o1", {})
            assert not second.claim_order("o1", {})
            with first.session_lock():
                with pytest.raises(RuntimeError, match="lock"):
                    with second.session_lock():
                        pytest.fail("symlink alias acquired a second runner lock")


def test_replaced_path_cannot_lock_a_different_inode(tmp_path):
    path = tmp_path / "live.sqlite"
    moved = tmp_path / "moved.sqlite"
    with LiveLedger(path, IDENTITY) as ledger:
        path.rename(moved)
        path.touch()
        with pytest.raises(RuntimeError, match="replaced"):
            with ledger.session_lock():
                pytest.fail("runner locked a different database inode")
        with pytest.raises(RuntimeError, match="replaced"):
            ledger.get_state()


def test_concurrent_terminal_update_never_regresses_to_open(tmp_path):
    path = tmp_path / "live.sqlite"
    with LiveLedger(path, IDENTITY) as ledger:
        ledger.claim_order("shared-order", {})
    context = multiprocessing.get_context("spawn")
    event, queue = context.Event(), context.Queue()
    workers = [context.Process(target=_update_worker, args=(str(path), IDENTITY, event, queue, state, filled))
               for state, filled in (("OPEN", 0.0), ("FILLED", 1.0))]
    for worker in workers:
        worker.start()
    event.set()
    outcomes = [queue.get(timeout=15) for _ in workers]
    for worker in workers:
        worker.join(15)
        assert worker.exitcode == 0
    assert "updated" in outcomes
    with LiveLedger(path, IDENTITY) as ledger:
        assert ledger.get_order("shared-order")["state"] == "FILLED"
        assert ledger.get_order("shared-order")["result"]["filled"] == 1.0


def test_session_exception_releases_lock_and_close_blocks_further_use(tmp_path):
    path = tmp_path / "live.sqlite"
    with LiveLedger(path, IDENTITY) as ledger:
        with pytest.raises(ValueError):
            with ledger.session_lock():
                raise ValueError("caller failed")
        with LiveLedger(path, IDENTITY) as another:
            with another.session_lock():
                pass
    ledger.close()
    with pytest.raises(RuntimeError, match="closed"):
        ledger.get_state()


@pytest.mark.parametrize("payload", [{"secret": "do-not-persist"}, {"nested": {"apiKey": "do-not-persist"}}, {"filled": float("nan")}, {1: "non-string-key"}])
def test_secret_or_invalid_metadata_never_persists(tmp_path, payload):
    path = tmp_path / "live.sqlite"
    with LiveLedger(path, IDENTITY) as ledger:
        for operation in (lambda: ledger.claim_decision("d1", payload),
                          lambda: ledger.claim_order("o1", payload),
                          lambda: ledger.set_state(payload)):
            with pytest.raises(ValueError):
                operation()
        assert ledger.get_order("o1") is None
        assert ledger.get_state() == {}
        assert ledger.claim_order("valid", {})
        with pytest.raises(ValueError):
            ledger.update_order("valid", "UNKNOWN", payload)
    assert b"do-not-persist" not in path.read_bytes()


def test_commit_failure_rolls_back_claim_and_allows_clean_retry(tmp_path):
    with LiveLedger(tmp_path / "live.sqlite", IDENTITY) as ledger:
        def deny_commit(action, first, second, database, trigger):
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_TRANSACTION and first == "COMMIT" else sqlite3.SQLITE_OK
        ledger._connection.set_authorizer(deny_commit)
        with pytest.raises(sqlite3.DatabaseError):
            ledger.claim_order("o1", {})
        ledger._connection.set_authorizer(None)
        assert ledger.get_order("o1") is None
        assert ledger.claim_order("o1", {})


def test_rollback_failure_closes_poisoned_connection(tmp_path):
    path = tmp_path / "live.sqlite"
    ledger = LiveLedger(path, IDENTITY)
    def deny_commit_and_rollback(action, first, second, database, trigger):
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_TRANSACTION and first in ("COMMIT", "ROLLBACK") else sqlite3.SQLITE_OK
    ledger._connection.set_authorizer(deny_commit_and_rollback)
    with pytest.raises(sqlite3.DatabaseError):
        ledger.claim_order("o1", {})
    with pytest.raises(RuntimeError, match="closed"):
        ledger.get_state()
    with LiveLedger(path, IDENTITY) as reopened:
        assert reopened.get_order("o1") is None


def test_sql_failure_does_not_change_order_state(tmp_path):
    with LiveLedger(tmp_path / "live.sqlite", IDENTITY) as ledger:
        ledger.claim_order("o1", {})
        ledger._connection.execute("CREATE TRIGGER deny_update BEFORE UPDATE ON live_orders BEGIN SELECT RAISE(ABORT, 'storage failure'); END")
        with pytest.raises(sqlite3.IntegrityError):
            ledger.update_order("o1", "OPEN", {"filled": 0.0})
        assert ledger.get_order("o1")["state"] == "SUBMITTING"


def test_candle_history_is_idempotent_append_only_and_survives_restart(tmp_path):
    path = tmp_path / "live.sqlite"
    initial = [_candle(0), _candle(1000)]
    with LiveLedger(path, IDENTITY) as ledger:
        assert ledger.candles() == []
        ledger.append_candles(initial, 1000)
        ledger.append_candles([_candle(1000), _candle(2000, 101.0)], 1000)
        ledger.append_candles(initial, 1000)
        assert ledger.candles() == initial + [_candle(2000, 101.0)]
    with LiveLedger(path, IDENTITY) as reopened:
        assert reopened.candles() == initial + [_candle(2000, 101.0)]
        with pytest.raises(ValueError, match="timeframe"):
            reopened.append_candles([_candle(3000)], 2000)


@pytest.mark.parametrize("rows", [
    [_candle(1000, 101.0), _candle(2000)],  # Revised existing prefix.
    [_candle(3000)],  # Missing bar at 2000.
    [_candle(2000), _candle(2000)],  # Duplicate timestamps within one batch.
    [_candle(3000), _candle(2000)],  # Reverse order.
    [_candle(-1000), _candle(0)],  # Prepending history after initialization.
])
def test_invalid_candle_append_is_atomic(tmp_path, rows):
    with LiveLedger(tmp_path / "live.sqlite", IDENTITY) as ledger:
        original = [_candle(0), _candle(1000)]
        ledger.append_candles(original, 1000)
        with pytest.raises(ValueError):
            ledger.append_candles(rows, 1000)
        assert ledger.candles() == original


def test_new_candle_insert_failure_rolls_back_whole_batch(tmp_path):
    with LiveLedger(tmp_path / "live.sqlite", IDENTITY) as ledger:
        ledger.append_candles([_candle(0)], 1000)
        ledger._connection.execute("CREATE TRIGGER fail_second BEFORE INSERT ON live_candles WHEN NEW.timestamp_ms = 2000 BEGIN SELECT RAISE(ABORT, 'storage failure'); END")
        with pytest.raises(sqlite3.IntegrityError):
            ledger.append_candles([_candle(1000), _candle(2000)], 1000)
        assert ledger.candles() == [_candle(0)]


def test_positive_older_candles_cannot_be_prepended(tmp_path):
    with LiveLedger(tmp_path / "live.sqlite", IDENTITY) as ledger:
        original = [_candle(1000), _candle(2000)]
        ledger.append_candles(original, 1000)
        with pytest.raises(ValueError, match="prepended"):
            ledger.append_candles([_candle(0), _candle(1000)], 1000)
        assert ledger.candles() == original


def test_foreign_database_is_rejected_without_changing_its_journal_mode(tmp_path):
    path = tmp_path / "unrelated.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE unrelated (id INTEGER)")
        before = connection.execute("PRAGMA journal_mode").fetchone()[0]
    with pytest.raises(ValueError, match="schema"):
        LiveLedger(path, IDENTITY)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == before


def test_new_journal_uses_private_permissions_and_full_sync_wal(tmp_path):
    path = tmp_path / "live.sqlite"
    with LiveLedger(path, IDENTITY) as ledger:
        assert path.stat().st_mode & 0o777 == 0o600
        assert ledger._connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert ledger._connection.execute("PRAGMA synchronous").fetchone()[0] == 2
