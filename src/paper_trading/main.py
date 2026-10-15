"""24/7 PUBLIC-DATA-ONLY paper engine; no exchange private methods."""
from __future__ import annotations
import argparse,asyncio,json,sys
from collections import deque
from datetime import datetime,timezone,timedelta

from .config import load
from .data_feed import ParquetReplay,BinancePublicWebsocket,HOUR
from .strategy_runner import propose
from .portfolio import Portfolio
from .paper_broker import rebalance
from .logger import AuditLog

def assert_clock(batch,history,symbols):
    if set(batch)!=set(symbols):raise RuntimeError("INCOMPLETE_UNIVERSE_HOUR")
    times={bar.time for bar in batch.values()}
    if len(times)!=1:raise RuntimeError("MISALIGNED_HOURLY_MARKET")
    for symbol in symbols:
        prior=history[symbol][-1].time if history[symbol] else None
        if prior is not None and batch[symbol].time-prior!=HOUR:
            raise RuntimeError("MISSING_OR_DUPLICATE_HOURLY_CANDLE")
    return next(iter(times))+HOUR

async def run(cfg):
    audit=AuditLog(cfg.log_dir)
    state=Portfolio.load(cfg.state_path,cfg.initial_cash)
    if state.halted:
        raise RuntimeError("PERSISTED_KILL_SWITCH_LATCHED_MANUAL_REVIEW_REQUIRED")
    if cfg.mode=="parquet":
        feed=ParquetReplay(cfg)
    else:
        feed=BinancePublicWebsocket(cfg)
        feed.bootstrap()
    history={s:deque(feed.history[s],maxlen=cfg.warmup_hours+3)
             for s in cfg.symbols}
    # LIVE feed will start AFTER last REST bootstrap candle.
    last_clock=None
    try:
        if cfg.mode=="binance_ws" and len({history[s][-1].time for s in cfg.symbols})!=1:
            raise RuntimeError("INCONSISTENT_BOOTSTRAP_SNAPSHOTS")
        async for batch in feed.batches():
            at=assert_clock(batch,history,cfg.symbols)
            for s in cfg.symbols:history[s].append(batch[s])
            if state.last_hour and at.isoformat()<=state.last_hour:
                continue  # replay restart: history warm-up only, never replay orders
            if state.last_hour and at-datetime.fromisoformat(state.last_hour)!=HOUR:
                raise RuntimeError("RUNTIME_RESUME_DATA_GAP")
            marks={s:bar.close for s,bar in batch.items()}
            measured=state.mark(marks,at,cfg)
            # A completed hour earlier produced the pending TARGET. Execute
            # only after the subsequent complete hour (no same-bar fills).
            if state.pending_time and at.isoformat()>=state.pending_time:
                if not state.halted:
                    snapshot=propose(history,state.rsi_active,cfg)
                    tiers=dict(snapshot.levels)
                    for held in set(state.positions)|set(state.pending):
                        tiers.setdefault(held,50) # exit illiquid old holds at worst tier
                    paper_fills=rebalance(state,state.pending,marks,tiers,cfg)
                    for row in paper_fills:
                        audit.emit(at,"PAPER_FILL",**row,simulated_only=True)
                    measured=state.mark(marks,at,cfg)
                state.pending={}
                state.pending_time=""
            # Signals run EVERY CLOSED HOUR, but target rebalance is weekly.
            proposal=propose(history,state.rsi_active,cfg)
            audit.emit(at,"HOURLY_SIGNAL",strategy=cfg.strategy,
                       signal=proposal.scores,eligible=proposal.eligible,
                       status=proposal.note,portfolio=state.snapshot())
            if at.hour==0:
                audit.emit(at,"DAILY_UTC_STATUS",portfolio=state.snapshot(),risk=measured)
                if at.weekday()==0 and not state.halted:
                    state.pending=proposal.weights
                    state.pending_time=(at+HOUR).isoformat()
                    audit.emit(at,"WEEKLY_REBALANCE_SCHEDULED",targets=proposal.weights,
                               decision_as_of=at.isoformat(),
                               execute_after=(at+HOUR).isoformat(),
                               note="fill using price of next completed hourly candle")
            state.last_hour=at.isoformat()
            state.write_atomic(cfg.state_path)
            last_clock=at
            if state.halted:
                audit.emit(at,"KILL_SWITCH",reason=state.halt_reason,portfolio=state.snapshot())
                raise RuntimeError("RISK_STOP_"+state.halt_reason)
    except BaseException as exc:
        at=datetime.now(timezone.utc)
        # Explicitly latch every unavailable/invalid-data failure; do not
        # reconnect, repeat old fills or continue trading on stale data.
        state.halted=True
        if not state.halt_reason:state.halt_reason="DATA_OR_ENGINE_EXCEPTION"
        state.write_atomic(cfg.state_path)
        audit.emit(at,"FATAL_HALTED",reason=str(exc),portfolio=state.snapshot(),
                   no_exchange_orders=True)
        raise
    return state.snapshot()

def main():
    ap=argparse.ArgumentParser(description="Uncredentialed PAPER simulation only")
    ap.add_argument("--config",default="config.yaml")
    args=ap.parse_args()
    cfg=load(args.config)
    state=asyncio.run(run(cfg))
    print(json.dumps({"status":"REPLAY_FINISHED_NO_LIVE_ORDER", "state":state},
                     sort_keys=True,default=str))

if __name__=="__main__":main()
