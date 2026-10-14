"""Persistent same-machine broker account gate: no real MT5 is contacted."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import pytest

from research_bot.mt5_global_demo_kill_switch import (
    GlobalDemoHalt, PersistentMT5DemoKillSwitch,
)


NOW=datetime(2026,10,14,12,0,tzinfo=timezone.utc)
KEY="UnitTest-Demo:1"


def gate(tmp_path):
    return PersistentMT5DemoKillSwitch(tmp_path/"state.sqlite3")


def submit(g, *, equity="100000", anchor="100000", at=NOW,
           account_key=KEY, outcome="success"):
    with g.admission(account_key=account_key,equity=equity,
                     day_start_equity=anchor,
                     day_start_date_utc=at.date().isoformat(),now=at) as lease:
        if outcome == "error":
            lease.execution_error()
        elif outcome == "uncertain":
            lease.execution_uncertain()
        elif outcome == "success":
            lease.execution_success()


def test_two_instances_accumulate_three_consecutive_broker_failures(tmp_path):
    one, two = gate(tmp_path), gate(tmp_path)
    submit(one,outcome="error")
    submit(two,outcome="error")
    submit(one,outcome="error")
    snap=two.snapshot(account_key=KEY)
    assert snap.consecutive_execution_errors == 3
    assert snap.halted
    assert snap.reason=="THREE_CONSECUTIVE_EXECUTION_ERRORS"
    with pytest.raises(GlobalDemoHalt,match="GLOBAL_DEMO_KILL_LATCHED"):
        submit(two)


def test_interleaved_success_resets_consecutive_broker_errors(tmp_path):
    g=gate(tmp_path)
    submit(g,outcome="error")
    submit(g,outcome="error")
    submit(g,outcome="success")
    submit(g,outcome="error")
    assert g.snapshot(account_key=KEY).consecutive_execution_errors == 1


def test_drawdown_at_boundary_latches_and_survives_reconnect_and_date_change(tmp_path):
    g=gate(tmp_path)
    with pytest.raises(GlobalDemoHalt,match="DAILY_EQUITY_DRAWDOWN_10_PERCENT"):
        submit(g,equity="90000")
    assert g.snapshot(account_key=KEY).halted
    with pytest.raises(GlobalDemoHalt,match="GLOBAL_DEMO_KILL_LATCHED"):
        submit(g,at=NOW+timedelta(days=1),anchor="120000",equity="120000")


def test_equity_anchor_change_trips_account_and_cannot_reset(tmp_path):
    g=gate(tmp_path)
    submit(g)
    with pytest.raises(GlobalDemoHalt,match="INCONSISTENT_DAILY_EQUITY_ANCHOR"):
        submit(g,anchor="101000")
    assert g.snapshot(account_key=KEY).reason=="INCONSISTENT_DAILY_EQUITY_ANCHOR"
    with pytest.raises(GlobalDemoHalt,match="GLOBAL_DEMO_KILL_LATCHED"):
        submit(g)


def test_multiple_accounts_isolated_not_multiple_process_reset(tmp_path):
    g=gate(tmp_path)
    submit(g,outcome="uncertain")
    assert g.snapshot(account_key=KEY).halted
    submit(g,account_key="OtherServer:2")
    assert not g.snapshot(account_key="OtherServer:2").halted


def test_untrusted_timing_and_numbers_fail_closed(tmp_path):
    g=gate(tmp_path)
    with pytest.raises(GlobalDemoHalt,match="NAIVE"):
        submit(g,at=datetime(2026,10,14,12,0))
    with pytest.raises(GlobalDemoHalt,match="INVALID"):
        submit(g,equity="NaN")
    with pytest.raises(GlobalDemoHalt,match="INVALID"):
        submit(g,account_key="invalid")


def test_missing_execution_outcome_latches(tmp_path):
    g=gate(tmp_path)
    submit(g,outcome="none")
    assert g.snapshot(account_key=KEY).halted
    assert g.snapshot(account_key=KEY).reason=="MISSING_BROKER_EXECUTION_OUTCOME"


def test_database_is_not_memory_or_automatically_recreated_under_missing_parent(tmp_path):
    with pytest.raises(GlobalDemoHalt,match="NONPERSISTENT"):
        PersistentMT5DemoKillSwitch(":memory:")
    g=PersistentMT5DemoKillSwitch(tmp_path/"missing_parent"/"risk.sqlite3")
    with pytest.raises(GlobalDemoHalt,match="MISSING_DURABLE"):
        submit(g)


def test_concurrent_local_workers_are_serialized(tmp_path):
    g=gate(tmp_path)
    def fail(_):
        submit(g,outcome="error")
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(fail,range(3)))
    snap=g.snapshot(account_key=KEY)
    assert snap.halted and snap.consecutive_execution_errors == 3


def test_daily_anchor_refresh_does_not_reset_failure_count(tmp_path):
    g=gate(tmp_path)
    submit(g,outcome="error")
    submit(g,at=NOW+timedelta(days=1),equity="99000",anchor="99000",outcome="error")
    assert g.snapshot(account_key=KEY).consecutive_execution_errors == 2


def test_independent_python_processes_share_the_same_latched_account(tmp_path):
    """Cross-process regression: one common sqlite file and account id."""
    import subprocess
    import sys

    db = str(tmp_path / "interprocess.sqlite3")
    child_code = """
import sys
from datetime import datetime, timezone
from research_bot.mt5_global_demo_kill_switch import PersistentMT5DemoKillSwitch
gate = PersistentMT5DemoKillSwitch(sys.argv[1])
with gate.admission(
    account_key="SharedDemo:123",
    equity="100000", day_start_equity="100000",
    day_start_date_utc="2026-10-14",
    now=datetime(2026, 10, 14, tzinfo=timezone.utc)
) as lease:
    lease.execution_error()
"""
    for _ in range(3):
        subprocess.run([sys.executable, "-c", child_code, db],
                       check=True, capture_output=True, text=True, timeout=15)

    state = PersistentMT5DemoKillSwitch(db).snapshot(
        account_key="SharedDemo:123"
    )
    assert state.consecutive_execution_errors == 3
    assert state.halted
    with pytest.raises(GlobalDemoHalt, match="GLOBAL_DEMO_KILL_LATCHED"):
        submit(PersistentMT5DemoKillSwitch(db),
               account_key="SharedDemo:123")
