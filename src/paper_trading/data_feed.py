"""Either verified historical Parquet replay or authenticated-free public Binance WS.

All WebSocket streams and REST requests are READ ONLY. Closed UTC 1-hour
candles only. Stop immediately on a broken network stream; do not mask gaps.
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass
from datetime import datetime,timezone,timedelta
from collections import deque
import hashlib,json
from pathlib import Path
from urllib.request import urlopen,Request
from urllib.parse import urlencode
import pandas as pd

HOUR=timedelta(hours=1)
@dataclass(frozen=True)
class Bar:
    symbol:str
    time:datetime # opening time of the just-closed UTC hourly candle
    open:float
    high:float
    low:float
    close:float
    volume:float
    quote_volume_min:float
    volume_origin:str

def check(b:Bar)->Bar:
    if b.time.tzinfo is None or b.time.minute!=0 or b.time.second!=0:
        raise ValueError("UNTRUSTED_HOURLY_TIMESTAMP")
    if not all(__import__("math").isfinite(v) for v in
               (b.open,b.high,b.low,b.close,b.volume,b.quote_volume_min)):
        raise ValueError("NONFINITE_BAR")
    if min(b.open,b.high,b.low,b.close)<=0 or min(b.volume,b.quote_volume_min)<0:
        raise ValueError("NEGATIVE_OR_ZERO_BAR")
    if b.high<max(b.open,b.close,b.low) or b.low>min(b.open,b.close,b.high):
        raise ValueError("OHLC_INVARIANT")
    return b

def market_json(url:str):
    with urlopen(Request(url,headers={"User-Agent":"ResearchPaperSimulator/1.0"}),timeout=15) as response:
        return json.loads(response.read())

def _from_klines(sym,rows):
    out=[]
    for row in rows:
        t=datetime.fromtimestamp(int(row[0])/1000,tz=timezone.utc)
        out.append(check(Bar(sym,t,*[float(row[i]) for i in (1,2,3,4,5)],
                             float(row[7]),"official_quote")))
    return out

class ParquetReplay:
    def __init__(self,cfg):
        if len(cfg.symbols)!=1 or cfg.symbols[0]!="BTCUSDT":
            raise ValueError("VERIFIED_PARQUET_CONTAINS_ONLY_BTCUSDT")
        self.cfg=cfg
        data=cfg.data_path
        if not data.is_file() or not cfg.manifest_path.is_file():
            raise ValueError("VERIFIED_REPLAY_PARQUET_OR_MANIFEST_MISSING")
        manifest=json.loads(cfg.manifest_path.read_text())
        if manifest.get("output_sha256")!=hashlib.sha256(data.read_bytes()).hexdigest():
            raise ValueError("PARQUET_SHA256_MISMATCH")
        frame=pd.read_parquet(data)
        mandatory={"timestamp","open","high","low","close","volume"}
        if not mandatory.issubset(frame.columns):raise ValueError("INVALID_PARQUET_SCHEMA")
        frame["timestamp"]=pd.to_datetime(frame.timestamp,utc=True)
        if not frame.timestamp.is_monotonic_increasing or not frame.timestamp.is_unique:
            raise ValueError("NONCAUSAL_TIMESTAMP_SERIES")
        # Source is M15, but paper strategy runs each UTC HOUR. Every aggregate
        # must have exactly 4 consecutive observed 15m bars.
        grouped=frame.groupby(frame.timestamp.dt.floor("h"),sort=True)
        hourly=[]
        for t,part in grouped:
            if len(part)!=4 or set(part.timestamp.dt.minute)!={0,15,30,45}:
                # Preserve gaps; never synthesize bars.
                continue
            hourly.append(check(Bar("BTCUSDT",t.to_pydatetime(),
                                    float(part.open.iloc[0]),float(part.high.max()),
                                    float(part.low.min()),float(part.close.iloc[-1]),
                                    float(part.volume.sum()),
                                    float((part.volume*part.low).sum()),
                                    "conservative_quote_lower_bound")))
        if len(hourly)<cfg.warmup_hours+cfg.replay_hours:
            raise ValueError("NOT_ENOUGH_VALID_HOURLY_BARS")
        target=hourly[-(cfg.warmup_hours+cfg.replay_hours):]
        for i in range(1,len(target)):
            if target[i].time-target[i-1].time!=HOUR:
                raise ValueError("REPLAY_GAP_IN_SELECTED_WINDOW")
        self.history={"BTCUSDT":deque(target[:cfg.warmup_hours],maxlen=cfg.warmup_hours+3)}
        self.events=target[cfg.warmup_hours:]

    async def batches(self):
        for bar in self.events:
            yield {"BTCUSDT":bar}
            await asyncio.sleep(0)

class BinancePublicWebsocket:
    """REST bootstrap then unauthenticated market-only WebSocket; no retry on loss."""
    def __init__(self,cfg):
        self.cfg=cfg
        self.history={}
        self.last={}
    def bootstrap(self):
        now=datetime.now(timezone.utc)
        for symbol in self.cfg.symbols:
            url="https://data-api.binance.vision/api/v3/klines?"+urlencode(
                {"symbol":symbol,"interval":"1h","limit":1000})
            allbars=_from_klines(symbol,market_json(url))
            completed=[b for b in allbars if b.time+HOUR<=now]
            if len(completed)<self.cfg.warmup_hours:raise ValueError("INSUFFICIENT_REST_BOOTSTRAP")
            hist=completed[-self.cfg.warmup_hours:]
            if any(hist[i].time-hist[i-1].time!=HOUR for i in range(1,len(hist))):
                raise ValueError("BOOTSTRAP_CANDLE_GAP")
            self.history[symbol]=deque(hist,maxlen=self.cfg.warmup_hours+3)
            self.last[symbol]=hist[-1].time
    async def batches(self):
        import websockets
        streams="/".join(f"{s.lower()}@kline_1h" for s in self.cfg.symbols)
        url=f"wss://data-stream.binance.vision:443/stream?streams={streams}"
        pending={}
        last_completed=max(self.last.values())
        async with websockets.connect(url,ping_interval=25,ping_timeout=15,close_timeout=5) as ws:
            while True:
                message=json.loads(await asyncio.wait_for(ws.recv(),timeout=4200))
                k=message.get("data",{}).get("k",{})
                if not k or k.get("x") is not True:continue
                sym=str(k.get("s",""))
                if sym not in self.cfg.symbols or k.get("i")!="1h":
                    raise ValueError("UNEXPECTED_WS_STREAM")
                bar=check(Bar(sym,datetime.fromtimestamp(int(k["t"])/1000,tz=timezone.utc),
                              *[float(k[key]) for key in ("o","h","l","c","v")],
                              float(k["q"]),"official_quote"))
                # A stream may echo last bootstrapped closed candle; deduplicate.
                if bar.time<=self.last[sym]:continue
                if bar.time!=self.last[sym]+HOUR:raise RuntimeError("WS_HOURLY_DISCONNECT_OR_GAP")
                if pending and bar.time!=next(iter(pending.values())).time:
                    raise RuntimeError("MISSING_SYMBOL_CLOSED_HOURLY_BAR")
                pending[sym]=bar
                if len(pending)==len(self.cfg.symbols):
                    for x in self.cfg.symbols:self.last[x]=pending[x].time
                    yield dict(pending)
                    pending={}
