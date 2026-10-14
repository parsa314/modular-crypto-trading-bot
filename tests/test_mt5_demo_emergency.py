"""Emergency only in DEMO; test exact-ticket closes, disconnection and no retry."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from research_bot.mt5_demo_emergency import attempt_latched_demo_flatten
from research_bot.mt5_direct_strategy import DirectStrategyJournal
from research_bot.mt5_global_demo_kill_switch import (
    PersistentMT5DemoKillSwitch, GlobalDemoHalt,
)

NOW=datetime(2026,10,14,10,0,tzinfo=timezone.utc)
KEY="UnitTest-Demo:1"


class SimDemo:
    connected=True
    submission_enabled=True
    config=SimpleNamespace(symbol_map={"BTC/USDT":"BTCUSD"})
    def __init__(self, *, fail=False):
        self.positions=[{"ticket":71,"symbol":"BTCUSD","magic":123}]
        self.closes=[]
        self.fail=fail
    def account_summary(self):
        return {"server":"UnitTest-Demo","login":1,"trade_mode":0}
    def bot_positions(self,symbol):
        return list(self.positions)
    def close_bot_position(self,*,ticket,venue_symbol,now):
        self.closes.append(ticket)
        if self.fail:
            raise ConnectionError("disconnect after close request: outcome unknown")
        self.positions = [x for x in self.positions if x["ticket"]!=ticket]
        return SimpleNamespace(
            ticket=ticket,venue_symbol=venue_symbol,closed_side="SELL",
            status="CLOSED_DEMO",retcode=10009,order_ticket=30,deal_ticket=31,
            requested_lots=.1,filled_lots=.1,executable_price=100.0,
            fill_price=100.,account_login=1,account_server="UnitTest-Demo",
            timestamp=now,comment="test",
        )


def latched(tmp_path):
    g=PersistentMT5DemoKillSwitch(tmp_path/"risk.db")
    with pytest.raises(GlobalDemoHalt,match="DAILY"):
        with g.admission(account_key=KEY, equity=90000,
                         day_start_equity=100000,
                         day_start_date_utc=NOW.date().isoformat(),now=NOW):
            pass
    return g


def test_flatten_requires_existing_kill_latch(tmp_path):
    g=PersistentMT5DemoKillSwitch(tmp_path/"risk.db")
    with pytest.raises(RuntimeError,match="NOT_LATCHED"):
        attempt_latched_demo_flatten(
            executor=SimDemo(),kill_switch=g,
            journal=DirectStrategyJournal(tmp_path/"journal.jsonl"),
            venue_symbols=["BTCUSD"],allow_demo_closes=True)


def test_default_emergency_is_read_only(tmp_path):
    x=SimDemo()
    output=attempt_latched_demo_flatten(
        executor=x,kill_switch=latched(tmp_path),
        journal=DirectStrategyJournal(tmp_path/"journal.jsonl"),
        venue_symbols=["BTCUSD"])
    assert output.status=="DRY_RUN_ONLY"
    assert x.closes==[]
    assert output.unresolved_tickets==(71,)


def test_latched_demo_closes_exact_owned_ticket_and_reads_back(tmp_path):
    x=SimDemo()
    journal=DirectStrategyJournal(tmp_path/"journal.jsonl")
    output=attempt_latched_demo_flatten(
        executor=x,kill_switch=latched(tmp_path),journal=journal,
        venue_symbols=["BTCUSD"],allow_demo_closes=True)
    assert output.status=="VERIFIED_BROKER_FLAT_DEMO"
    assert output.attempted_tickets==(71,)
    assert output.acknowledged_tickets==(71,)
    assert x.closes==[71]
    assert [r["record_type"] for r in journal.all_events()] == ["CLOSE_INTENT","CLOSE_RESULT"]


def test_disconnect_does_not_repeat_uncertain_emergency_close(tmp_path):
    x=SimDemo(fail=True)
    journal=DirectStrategyJournal(tmp_path/"journal.jsonl")
    g=latched(tmp_path)
    output=attempt_latched_demo_flatten(
        executor=x,kill_switch=g,journal=journal,
        venue_symbols=["BTCUSD"],allow_demo_closes=True)
    assert output.status=="NEEDS_RECONCILIATION"
    assert output.unresolved_tickets==(71,)
    again=attempt_latched_demo_flatten(
        executor=x,kill_switch=g,journal=journal,
        venue_symbols=["BTCUSD"],allow_demo_closes=True)
    assert again.status=="NEEDS_RECONCILIATION"
    assert x.closes == [71]  # no dangerous second market order


def test_real_account_and_symbol_outside_allowlist_cannot_flatten(tmp_path):
    x=SimDemo()
    g=latched(tmp_path)
    x.account_summary=lambda: {"server":"UnitTest-Demo","login":1,"trade_mode":2}
    with pytest.raises(RuntimeError,match="REAL"):
        attempt_latched_demo_flatten(
            executor=x,kill_switch=g,
            journal=DirectStrategyJournal(tmp_path/"journal.jsonl"),
            venue_symbols=["BTCUSD"],allow_demo_closes=True)
    x.account_summary=lambda: {"server":"UnitTest-Demo","login":1,"trade_mode":0}
    with pytest.raises(RuntimeError,match="ALLOWLIST"):
        attempt_latched_demo_flatten(
            executor=x,kill_switch=g,
            journal=DirectStrategyJournal(tmp_path/"journal.jsonl"),
            venue_symbols=["ETHUSD"],allow_demo_closes=True)
