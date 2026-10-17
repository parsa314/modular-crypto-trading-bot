"""Executable paper-only Donchian H4 engine. No signed API and no exchange order."""
from __future__ import annotations
import argparse,asyncio,fcntl,json,os,time
from collections import deque
from datetime import datetime,timezone
from .config import read_config
from .data_feed import LivePublicFeed,ReplayFeed,H4
from .state import State
from .strategy import decide
from .risk import mark_and_veto
from .broker import fill_targets
from .logger import log

async def run(config):
    config.log_dir.mkdir(parents=True,exist_ok=True)
    lock_fd=os.open(str(config.log_dir/".single-writer.lock"),os.O_CREAT|os.O_RDWR,0o600)
    try:
        try:fcntl.flock(lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError("ANOTHER_PAPER_BOT_ALREADY_RUNNING")
        state=State.restore(config.state_path)
        if state.halted:raise RuntimeError("PREVIOUS_FATAL_HALT_REQUIRES_MANUAL_REVIEW")
        feed=LivePublicFeed(config) if config.mode=="binance_ws" else ReplayFeed(config)
        try:
            feed.bootstrap() if config.mode=="binance_ws" else None
            history={s:deque(feed.history[s],maxlen=config.warmup_bars+5)
                     for s in config.symbols}
            if config.mode=="binance_ws" and state.last_close_ms and (
                state.last_close_ms!=next(iter(feed.bootstrap_last.values()))
            ):
                raise RuntimeError("RESTART_CANDLE_GAP_REQUIRES_RECONCILIATION")
            log(config,"START",mode=config.mode,assets=list(config.symbols),
                nav=state.nav(),paper_only=True,checkpoint=state.last_close_ms)
            last_health=0
            async for kind,t,payload in feed.events():
                if kind=="HEARTBEAT":
                    now=time.time()
                    if now-last_health>=30:
                        last_health=now
                        state.heartbeat_utc=datetime.now(timezone.utc).isoformat()
                        state.persist(config.state_path)
                        log(config,"HEARTBEAT",nav=state.nav(),trades=state.trades,
                            errors=state.errors,paper_only=True)
                    continue
                if kind=="CLOSE":
                    if set(payload)!=set(config.symbols):
                        raise RuntimeError("MISSING_SYMBOL_IN_CLOSED_H4_BATCH")
                    for s in config.symbols:
                        history[s].append(payload[s])
                    if t<=state.last_close_ms:
                        continue
                    if state.last_close_ms and t!=state.last_close_ms+H4:
                        raise RuntimeError("BAR_CLOSE_CLOCK_GAP")
                    measures=mark_and_veto(state,{s:b.close for s,b in payload.items()},
                                           datetime.fromtimestamp((t+H4)/1000,tz=timezone.utc),config)
                    if state.halted:
                        state.persist(config.state_path)
                        log(config,"KILL_SWITCH",reason=state.halt_reason,risk=measures,
                            market_bar_ms=t)
                        raise RuntimeError("RISK_KILL_SWITCH_"+state.halt_reason)
                    targets,signals=decide(history,state,config)
                    state.pending=targets
                    state.pending_for_ms=t+H4
                    state.last_close_ms=t
                    state.heartbeat_utc=datetime.now(timezone.utc).isoformat()
                    state.persist(config.state_path)
                    log(config,"H4_SIGNAL",market_bar_ms=t,signals=signals,
                        nav=state.nav(),position=state.positions,paper_only=True)
                    if (t+H4)%(24*3600*1000)==0:
                        log(config,"UTC_DAILY_STATUS",market_bar_ms=t,
                            state=state.snapshot(),risk=measures)
                elif kind=="OPEN_TICK":
                    if not state.pending_for_ms or t<state.pending_for_ms:
                        continue
                    if t!=state.pending_for_ms:
                        raise RuntimeError("LATE_OR_MISSING_FIRST_PUBLIC_TICK")
                    if t<=state.last_fill_ms:
                        raise RuntimeError("DUPLICATE_PAPER_FILL_BATCH")
                    if set(payload)!=set(config.symbols):
                        raise RuntimeError("INCOMPLETE_FIRST_TICK_BATCH")
                    now=datetime.fromtimestamp(t/1000,tz=timezone.utc)
                    risk=mark_and_veto(state,payload,now,config)
                    if state.halted:
                        state.persist(config.state_path)
                        raise RuntimeError("RISK_KILL_SWITCH_ON_NEXT_TICK")
                    liq={s:sum(b.quote_volume for b in list(history[s])[-6:])
                         for s in config.symbols}
                    fills=fill_targets(state,state.pending,payload,liq,config)
                    state.pending={}
                    state.pending_for_ms=0
                    state.last_fill_ms=t
                    state.heartbeat_utc=datetime.now(timezone.utc).isoformat()
                    state.persist(config.state_path)
                    for fill in fills:
                        log(config,"PAPER_FILL",market_ms=t,**fill)
                    log(config,"PAPER_CYCLE",market_ms=t,nav=state.nav(),
                        trades=state.trades,errors=state.errors,
                        positions=state.positions,cash=state.cash,risk=risk)
                else:
                    raise RuntimeError("UNEXPECTED_FEED_EVENT")
            state.heartbeat_utc=datetime.now(timezone.utc).isoformat()
            state.persist(config.state_path)
            log(config,"REPLAY_COMPLETE",nav=state.nav(),trades=state.trades,
                errors=state.errors,positions=state.positions)
            return {"status":"REPLAY_COMPLETE","nav":state.nav(),
                    "trades":state.trades,"errors":state.errors,
                    "positions":state.positions}
        except Exception as exc:
            state.halted=True
            state.errors+=1
            state.halt_reason=str(exc)[:250]
            state.heartbeat_utc=datetime.now(timezone.utc).isoformat()
            state.persist(config.state_path)
            log(config,"FATAL_HALTED",reason=state.halt_reason,nav=state.nav(),
                errors=state.errors,trades=state.trades,paper_only=True)
            raise
    finally:
        os.close(lock_fd)

def main():
    p=argparse.ArgumentParser(description="Paper-only, public-data-only H4 Donchian")
    p.add_argument("--config",default="src/bot/config.yaml")
    args=p.parse_args()
    config=read_config(args.config)
    result=asyncio.run(run(config))
    print(json.dumps(result,sort_keys=True,allow_nan=False))

if __name__=="__main__":main()
