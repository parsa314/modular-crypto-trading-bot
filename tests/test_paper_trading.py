"""Offline-only paper infrastructure: invariants, actual parquet, zero orders."""
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from pathlib import Path
import asyncio,json
from collections import deque
import pytest

from paper_trading.config import load
from paper_trading.data_feed import Bar,ParquetReplay
from paper_trading.strategy_runner import propose,rsi14
from paper_trading.paper_broker import rebalance
from paper_trading.portfolio import Portfolio
from paper_trading.logger import AuditLog
from paper_trading.main import run

ROOT=Path(__file__).resolve().parents[1]
UTC=timezone.utc

def cfg():
    return load(ROOT/"config.replay.yaml")

def make(sym,n,drift):
    start=datetime(2024,1,1,tzinfo=UTC)
    v=[]
    for i in range(n):
        price=100+drift*i
        v.append(Bar(sym,start+timedelta(hours=i),
                     price,price*1.01,price*.99,price,
                     50000,2e7,"official_quote"))
    return deque(v,maxlen=800)

def test_allows_read_only_only_and_rejects_private_fields(tmp_path):
    f=tmp_path/"config.yaml"
    f.write_text("mode: binance_ws\nstrategy: momentum_rsi_fallback\napi_key: SECRET\n")
    with pytest.raises(ValueError,match="UNSUPPORTED_CONFIG_FIELDS"):load(f)
    f.write_text("mode: binance_ws\nstrategy: cross_sectional_v3\n")
    with pytest.raises(ValueError,match="V3_NOT_VALIDATED"):load(f)
    f.write_text("mode: live\n")
    with pytest.raises(ValueError,match="ONLY_PARQUET"):load(f)

def test_config_caps_are_immutable_hard_limits(tmp_path):
    f=tmp_path/"bad.yaml"
    for key,value in [("max_position_weight",".11"),
                      ("max_daily_drawdown",".051"),
                      ("max_total_drawdown",".151"),
                      ("min_quote_volume_24h","1000")]:
        f.write_text(f"{key}: {value}\n")
        with pytest.raises(ValueError,match="INVALID_"+key):load(f)

def test_rsi_reacts_to_sustained_trend():
    assert rsi14([float(i) for i in range(1,100)]) >70
    assert rsi14([float(i) for i in range(100,1,-1)]) <30

def test_no_cross_sectional_quintile_with_one_coin():
    c=cfg()
    out=propose({"BTCUSDT":make("BTCUSDT",721,.1)}, {},c)
    assert out.weights=={}
    assert "fewer than five" in out.note

def test_strategy_50_50_and_ten_percent_hard_ceiling():
    c=replace(cfg(),symbols=("BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT"))
    x={s:make(s,721,.02*(j+1)) for j,s in enumerate(c.symbols)}
    active={s:True for s in c.symbols}
    proposal=propose(x,active,c)
    assert len(proposal.eligible)==5
    assert set(proposal.weights).issubset(c.symbols)
    assert all(0<=w<=.10 for w in proposal.weights.values())
    assert sum(proposal.weights.values())<=1

def test_broker_fees_are_real_cash_deductions_but_slip_is_estimated():
    c=cfg()
    state=Portfolio.new(50000.)
    state.marks={"BTCUSDT":100.}
    fills=rebalance(state,{"BTCUSDT":.09},{"BTCUSDT":100.},{"BTCUSDT":15},c)
    assert len(fills)==1 and fills[0]["side"]=="BUY"
    assert fills[0]["fee_usdt"]>0
    assert fills[0]["estimated_slippage_bps"]==pytest.approx(5)
    assert state.cash<50000.
    assert state.positions["BTCUSDT"]>0
    assert state.positions["BTCUSDT"]*100/state.nav()<=.10
    second=rebalance(state,{},{"BTCUSDT":100.},{"BTCUSDT":15},c)
    assert second and second[0]["side"]=="SELL"
    assert state.nav()<50000.  # roundtrip frictions

def test_daily_and_total_kill_switch_persist(tmp_path):
    c=cfg();p=Portfolio.new(100.)
    p.cash=0.;p.positions={"BTCUSDT":1.}
    t=datetime(2024,1,1,0,tzinfo=UTC)
    assert not p.mark({"BTCUSDT":100.},t,c)["halted"]
    event=p.mark({"BTCUSDT":80.},t+timedelta(hours=1),c)
    assert event["halted"]
    assert p.halt_reason=="TOTAL_DRAWDOWN_EXCEEDED_15PCT"
    location=tmp_path/"state.json"
    p.write_atomic(location)
    assert Portfolio.load(location,1).halted
    with pytest.raises(ValueError,match="RISK_HALT"):
        rebalance(p,{"BTCUSDT":.1},{"BTCUSDT":80.},{"BTCUSDT":15},c)

def test_daily_drawdown_halts_before_total_drawdown():
    c=cfg();p=Portfolio.new(100.)
    p.cash=0;p.positions={"BTCUSDT":1}
    t=datetime(2024,1,1,tzinfo=UTC)
    p.mark({"BTCUSDT":100},t,c)
    x=p.mark({"BTCUSDT":94},t+timedelta(hours=1),c)
    assert x["halted"] and p.halt_reason=="DAILY_DRAWDOWN_EXCEEDED_5PCT"

def test_snapshot_daily_jsonl(tmp_path):
    log=AuditLog(tmp_path)
    t=datetime(2024,1,1,0,tzinfo=UTC)
    log.emit(t,"HOURLY_SIGNAL",portfolio={"cash":1})
    log.emit(t+timedelta(hours=2),"DAILY_UTC_STATUS",nav=1)
    path=tmp_path/"2024-01-01.jsonl"
    messages=[json.loads(line) for line in path.read_text().splitlines()]
    assert [v["type"] for v in messages]==["HOURLY_SIGNAL","DAILY_UTC_STATUS"]
    assert messages[0]["portfolio"]["cash"]==1

def test_replay_uses_all_real_publisher_checked_months():
    f=ParquetReplay(cfg())
    assert len(f.events)==168
    assert len(f.history["BTCUSDT"])==720
    assert all(b.volume_origin=="conservative_quote_lower_bound" for b in f.events)
    assert all(f.events[i].time-f.events[i-1].time==timedelta(hours=1)
               for i in range(1,len(f.events)))

def test_end_to_end_offline_replay_never_places_fake_cross_sectional_trades(tmp_path):
    c=replace(cfg(),log_dir=tmp_path/"logs",state_path=tmp_path/"logs/state.json")
    outcome=asyncio.run(run(c))
    assert outcome["halted"] is False
    assert outcome["cash"]==pytest.approx(50000.)
    assert outcome["positions"]=={}
    assert (c.state_path).is_file()
    events=[json.loads(line) for f in sorted(c.log_dir.glob("*.jsonl"))
            for line in f.read_text().splitlines()]
    assert sum(x["type"]=="HOURLY_SIGNAL" for x in events)==168
    assert any(x["type"]=="DAILY_UTC_STATUS" for x in events)
    assert not any(x["type"]=="PAPER_FILL" for x in events)
    # Re-running the identical historic tape MUST NOT create duplicate signals.
    outcome2=asyncio.run(run(c))
    assert outcome2["last_hour"]==outcome["last_hour"]
    new_events=[json.loads(line) for f in sorted(c.log_dir.glob("*.jsonl"))
            for line in f.read_text().splitlines()]
    assert len(new_events)==len(events)
