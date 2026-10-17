"""Actual H4 DONCHIAN paper invariants; public-only and real archived replay."""
from __future__ import annotations
import asyncio,dataclasses,json,math
from collections import deque
from datetime import datetime,timezone,timedelta
from pathlib import Path
import pytest

from bot.config import read_config
from bot.data_feed import Bar,H4,ReplayFeed,validated
from bot.strategy import decide
from bot.broker import fill_targets
from bot.state import State
from bot.risk import mark_and_veto
from bot.main import run

ROOT=Path(__file__).resolve().parents[1]

def cfg():
    return read_config(ROOT/"src/bot/config.replay.yaml")

def fixture_history(length=80,trend=False):
    start=1704067200000
    bars=[]
    for i in range(length):
        price=100. if i<length-1 or not trend else 115.
        bars.append(validated(Bar("BTCUSDT",start+i*H4,
                  100.,max(price,100.)+1,99.,price,100.,2e6)))
    return deque(bars)

def test_closed_55_bar_breakout_opens_ten_percent():
    s=State()
    s.marks["BTCUSDT"]=115.
    c=cfg()
    targets,signals=decide({"BTCUSDT":fixture_history(trend=True)},s,c)
    assert signals["BTCUSDT"]["action"]=="ENTER"
    assert targets["BTCUSDT"]==.1

def test_no_breakout_stays_cash():
    targets,signals=decide({"BTCUSDT":fixture_history()},State(),cfg())
    assert targets["BTCUSDT"]==0.
    assert signals["BTCUSDT"]["action"]=="FLAT"

def test_20_low_exit():
    s=State(cash=900,positions={"BTCUSDT":1.},marks={"BTCUSDT":100.})
    data=fixture_history()
    b=data.pop()
    data.append(dataclasses.replace(b,low=79.,close=80.,high=101.))
    decision,event=decide({"BTCUSDT":data},s,cfg())
    assert event["BTCUSDT"]["action"]=="EXIT"
    assert decision["BTCUSDT"]==0.

def test_paper_fill_has_fee_slippage_and_hard_cap():
    s=State()
    s.marks["BTCUSDT"]=100.
    c=cfg()
    trades=fill_targets(s,{"BTCUSDT":.1},{"BTCUSDT":100.},{"BTCUSDT":1e9},c)
    assert len(trades)==1 and trades[0]["side"]=="BUY"
    assert trades[0]["paper_only"] is True
    assert trades[0]["commission_usdt"]>0
    assert s.positions["BTCUSDT"]*100/s.nav()<=.1
    assert s.trades==1
    trades2=fill_targets(s,{"BTCUSDT":0.},{"BTCUSDT":100.},{"BTCUSDT":1e9},c)
    assert any(x["side"]=="SELL" for x in trades2)
    assert s.positions.get("BTCUSDT",0)<=1e-12

def test_hold_does_not_churn_and_price_growth_trims_to_hard_limit():
    s=State()
    s.marks["BTCUSDT"]=100.
    c=cfg()
    buy=fill_targets(s,{"BTCUSDT":.10},{"BTCUSDT":100.},{"BTCUSDT":1e9},c)
    assert len(buy)==1
    before_cash,before_qty=s.cash,s.positions["BTCUSDT"]
    weight=before_qty*100/s.nav()
    assert fill_targets(s,{"BTCUSDT":weight},{"BTCUSDT":100.},{"BTCUSDT":1e9},c)==[]
    assert s.cash==before_cash and s.positions["BTCUSDT"]==before_qty
    s.marks["BTCUSDT"]=120
    now_weight=s.positions["BTCUSDT"]*120/s.nav()
    assert now_weight>c.max_position
    trim=fill_targets(s,{"BTCUSDT":c.max_position},{"BTCUSDT":120.},{"BTCUSDT":1e9},c)
    assert trim and trim[0]["side"]=="SELL"
    assert s.positions["BTCUSDT"]*120/s.nav()<=c.max_position+1e-5


def test_risk_veto_daily():
    c=cfg()
    s=State(cash=900.,positions={"BTCUSDT":1.},marks={"BTCUSDT":100.})
    event=mark_and_veto(s,{"BTCUSDT":30.},datetime(2024,1,1,tzinfo=timezone.utc),c)
    assert event["risk_veto"] and s.halt_reason=="DAILY_DRAWDOWN"
    with pytest.raises(RuntimeError,match="RISK_VETO"):
        fill_targets(s,{"BTCUSDT":.1},{"BTCUSDT":30.},{"BTCUSDT":1e9},c)

def test_state_atomic_persist_restore(tmp_path):
    p=tmp_path/"state.json"
    s=State(cash=800,positions={"BTCUSDT":2},marks={"BTCUSDT":100})
    s.persist(p)
    other=State.restore(p)
    assert other.nav()==1000
    assert other.positions==s.positions
    contents=json.loads(p.read_text())
    contents["nav"]=-200
    p.write_text(json.dumps(contents))
    with pytest.raises(RuntimeError,match="CHECKSUM"):State.restore(p)

def test_reject_credentials_and_real_mode(tmp_path):
    config=tmp_path/"unsafe.yaml"
    config.write_text("mode: live\nsymbols: [BTCUSDT]\n")
    with pytest.raises(ValueError,match="PAPER_FEED_ONLY"):read_config(config)
    config.write_text("mode: replay\nsymbols: [BTCUSDT]\napi_key: secret\n")
    with pytest.raises(ValueError,match="DISALLOWED_CONFIG_FIELDS"):read_config(config)

def test_replay_history_is_real_and_verified():
    src=ReplayFeed(cfg())
    assert src.replay_source_sha and len(src.replay_source_sha)==64
    assert len(src.selected)==cfg().warmup_bars+cfg().replay_bars+1
    assert all(src.selected[i].time_ms-src.selected[i-1].time_ms==H4
               for i in range(1,len(src.selected)))

def test_actual_real_archive_end_to_end_and_idempotent_restart(tmp_path):
    c=dataclasses.replace(cfg(),log_dir=tmp_path/"audit",
                          state_path=tmp_path/"audit"/"state.json")
    first=asyncio.run(run(c))
    assert first["status"]=="REPLAY_COMPLETE"
    assert first["errors"]==0
    assert math.isfinite(first["nav"]) and first["nav"]>0
    logs=list((tmp_path/"audit").glob("*.jsonl"))
    events=[json.loads(s) for p in logs for s in p.read_text().splitlines()]
    assert sum(e["event"]=="H4_SIGNAL" for e in events)==c.replay_bars
    assert any(e["event"]=="REPLAY_COMPLETE" for e in events)
    trade_count=sum(e["event"]=="PAPER_FILL" for e in events)
    assert first["trades"]==trade_count
    second=asyncio.run(run(c))
    assert second["trades"]==first["trades"]
    assert second["nav"]==first["nav"]
    events2=[json.loads(s) for p in logs for s in p.read_text().splitlines()]
    assert sum(e["event"]=="PAPER_FILL" for e in events2)==trade_count
    assert sum(e["event"]=="H4_SIGNAL" for e in events2)==c.replay_bars

def test_public_source_has_zero_credentialled_order_calls():
    source=(ROOT/"src/bot/data_feed.py").read_text()
    assert "data-api.binance.vision" in source
    assert "data-stream.binance.vision" in source
    for banned in ["apiKey","api_secret","/api/v3/order","/fapi/v1/order"]:
        assert banned not in source
