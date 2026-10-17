"""Closed H4 market bars from Binance public websocket or verified M15 replay.

NO key, secret, signed request, trading endpoint, or real-order method.
"""
from __future__ import annotations
from collections import deque
from dataclasses import dataclass
from datetime import datetime,timezone
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.parse import urlencode
import asyncio,hashlib,json,math,time

H4=14_400_000

@dataclass(frozen=True)
class Bar:
    symbol:str
    time_ms:int
    open:float
    high:float
    low:float
    close:float
    volume:float
    quote_volume:float

def validated(b):
    if b.time_ms%H4!=0 or min(b.open,b.high,b.low,b.close)<=0 or min(b.volume,b.quote_volume)<0:
        raise RuntimeError("INVALID_H4_BAR")
    if not all(math.isfinite(float(x)) for x in (b.open,b.high,b.low,b.close,b.volume,b.quote_volume)):
        raise RuntimeError("NONFINITE_H4_BAR")
    if b.high<max(b.open,b.low,b.close) or b.low>min(b.open,b.high,b.close):
        raise RuntimeError("INVALID_H4_OHLC")
    return b

def parse_klines(symbol,rows):
    return [validated(Bar(symbol,int(a[0]),*[float(a[j]) for j in (1,2,3,4,5,7)]))
            for a in rows]

def public_json(url):
    with urlopen(Request(url,headers={"User-Agent":"ResearchPaperBot/1.0"}),timeout=25) as r:
        return json.loads(r.read())

def check_contiguous(bars):
    if len(bars)<56:raise RuntimeError("INSUFFICIENT_H4_HISTORY")
    if any(b.time_ms-a.time_ms!=H4 for a,b in zip(bars[:-1],bars[1:])):
        raise RuntimeError("EXCHANGE_H4_GAP")

class LivePublicFeed:
    def __init__(self,cfg):
        self.cfg=cfg
        self.history={}
        self.bootstrap_last={}
        self.quotes={}

    def bootstrap(self):
        now=int(time.time()*1000)
        for s in self.cfg.symbols:
            endpoint="https://data-api.binance.vision/api/v3/klines?"+urlencode({
                "symbol":s,"interval":"4h","limit":self.cfg.warmup_bars+3})
            rows=public_json(endpoint)
            if not isinstance(rows,list):raise RuntimeError("PUBLIC_REST_BAD_RESPONSE")
            complete=[b for b in parse_klines(s,rows) if b.time_ms+H4<=now]
            history=complete[-self.cfg.warmup_bars:]
            if len(history)!=self.cfg.warmup_bars:raise RuntimeError("NO_H4_BOOTSTRAP")
            check_contiguous(history)
            if now-(history[-1].time_ms+H4)>H4+120000:
                raise RuntimeError("STALE_PUBLIC_HISTORY")
            self.history[s]=deque(history,maxlen=self.cfg.warmup_bars+5)
            self.bootstrap_last[s]=history[-1].time_ms
        if len(set(self.bootstrap_last.values()))!=1:
            raise RuntimeError("REST_SNAPSHOTS_OUT_OF_SYNC")

    async def events(self):
        import websockets
        streams="/".join(f"{s.lower()}@kline_4h" for s in self.cfg.symbols)
        address=f"wss://data-stream.binance.vision:443/stream?streams={streams}"
        closed={}
        first_ticks={}
        close_dispatched=set()
        tick_dispatched=set()
        last=dict(self.bootstrap_last)
        last_beat=time.monotonic()
        # Rotate connections every 20h, carry forward candle/tick state.
        while True:
            connected_at=time.monotonic()
            async with websockets.connect(address,ping_interval=20,ping_timeout=20,close_timeout=5,
                                          max_size=2**21) as ws:
                while True:
                    raw=await asyncio.wait_for(ws.recv(),timeout=self.cfg.max_stream_idle_seconds)
                    message=json.loads(raw)
                    k=message.get("data",{}).get("k")
                    if not k:continue
                    s=str(k.get("s",""))
                    if s not in self.cfg.symbols or k.get("i")!="4h":
                        raise RuntimeError("UNEXPECTED_BINANCE_KLINE")
                    t=int(k["t"])
                    if t%H4:raise RuntimeError("OFF_GRID_BINANCE_EVENT")
                    if abs(int(message.get("data",{}).get("E",0))-int(time.time()*1000))>120000:
                        raise RuntimeError("STALE_OR_MISDATED_WS_EVENT")
                    if k.get("x") is True:
                        if t<=last[s]:continue
                        if t!=last[s]+H4:raise RuntimeError("MISSING_H4_CLOSED_BAR")
                        b=validated(Bar(s,t,*[float(k[j]) for j in ("o","h","l","c","v","q")]))
                        last[s]=t
                        closed.setdefault(t,{})[s]=b
                        if len(closed[t])==len(self.cfg.symbols) and t not in close_dispatched:
                            close_dispatched.add(t)
                            yield "CLOSE",t,dict(closed.pop(t))
                            next_t=t+H4
                            if len(first_ticks.get(next_t,{}))==len(self.cfg.symbols) and next_t not in tick_dispatched:
                                tick_dispatched.add(next_t)
                                yield "OPEN_TICK",next_t,dict(first_ticks.pop(next_t))
                    elif k.get("x") is False:
                        # Do not queue bootstrap's already-traded current candle,
                        # and never recreate a consumed first-tick event.
                        if t in tick_dispatched:continue
                        if t-H4<=max(self.bootstrap_last.values()) and t-H4 not in close_dispatched:continue
                        if t<min(last.values())+H4:continue
                        ticks=first_ticks.setdefault(t,{})
                        px=float(k["c"])
                        if not math.isfinite(px) or px<=0:raise RuntimeError("BAD_PUBLIC_TICK")
                        ticks.setdefault(s,px)
                        if len(ticks)==len(self.cfg.symbols) and t-H4 in close_dispatched and t not in tick_dispatched:
                            tick_dispatched.add(t)
                            yield "OPEN_TICK",t,dict(first_ticks.pop(t))
                    if time.monotonic()-last_beat>=30:
                        last_beat=time.monotonic()
                        yield "HEARTBEAT",int(time.time()*1000),{}
                    if len(closed)>2 or len(first_ticks)>2:
                        raise RuntimeError("INCOMPLETE_CANDLE_OR_TICK_UNIVERSE")
                    if time.monotonic()-connected_at>=20*3600 and not closed and not first_ticks:
                        break # Preempt Binance's mandatory 24h WS disconnect.

class ReplayFeed:
    """Archive is genuine publisher-checksummed Binance BTCUSDT M15 2022-24."""
    def __init__(self,cfg):
        import pandas as pd
        self.cfg=cfg
        if cfg.symbols!=("BTCUSDT",):raise RuntimeError("BTC_ONLY_REPLAY")
        if not cfg.data_path.is_file() or not cfg.manifest_path.is_file():
            raise RuntimeError("MISSING_VERIFIED_M15_ARCHIVE")
        manifest=json.loads(cfg.manifest_path.read_text())
        if hashlib.sha256(cfg.data_path.read_bytes()).hexdigest()!=manifest.get("output_sha256"):
            raise RuntimeError("ARCHIVE_SHA256_FAILED")
        original=pd.read_parquet(cfg.data_path)
        if not {"timestamp","open","high","low","close","volume"}.issubset(original):
            raise RuntimeError("BAD_M15_ARCHIVE_SCHEMA")
        original["timestamp"]=pd.to_datetime(original.timestamp,utc=True)
        if not original.timestamp.is_monotonic_increasing or not original.timestamp.is_unique:
            raise RuntimeError("INVALID_M15_ARCHIVE_CLOCK")
        groups=original.groupby(original.timestamp.dt.floor("4h"),sort=True)
        allbars=[]
        for t,p in groups:
            if len(p)!=16 or set(p.timestamp.dt.minute)!={0,15,30,45}:continue
            if not (p.timestamp.diff().iloc[1:]==pd.Timedelta(minutes=15)).all():continue
            allbars.append(validated(Bar("BTCUSDT",int(t.timestamp()*1000),
                float(p.open.iloc[0]),float(p.high.max()),float(p.low.min()),
                float(p.close.iloc[-1]),float(p.volume.sum()),
                float((p.low*p.volume).sum()))))
        needed=cfg.warmup_bars+cfg.replay_bars+1
        if len(allbars)<needed:raise RuntimeError("REPLAY_NOT_ENOUGH_H4")
        selected=allbars[-needed:]
        check_contiguous(selected)
        self.history={"BTCUSDT":deque(selected[:cfg.warmup_bars],
                                 maxlen=cfg.warmup_bars+5)}
        self.bootstrap_last={"BTCUSDT":self.history["BTCUSDT"][-1].time_ms}
        self.selected=selected
        self.replay_source_sha=manifest["output_sha256"]

    async def events(self):
        i=self.cfg.warmup_bars
        for n in range(i,i+self.cfg.replay_bars):
            completed=self.selected[n]
            future=self.selected[n+1]
            yield "CLOSE",completed.time_ms,{"BTCUSDT":completed}
            yield "OPEN_TICK",future.time_ms,{"BTCUSDT":future.open}
            await asyncio.sleep(0)
