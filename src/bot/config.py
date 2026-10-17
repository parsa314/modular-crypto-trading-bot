"""Strict YAML configuration: never accept order permissions or API credentials."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import math
import yaml

ROOT=Path(__file__).resolve().parents[2]
ALLOWED={"mode","symbols","data_path","manifest_path","warmup_bars",
         "replay_bars","initial_cash","max_position","daily_kill",
         "total_kill","min_quote_24h","fee_bps","slip_bps",
         "state_path","log_dir","max_stream_idle_seconds"}
@dataclass(frozen=True)
class Config:
    mode:str
    symbols:tuple[str,...]
    data_path:Path
    manifest_path:Path
    warmup_bars:int
    replay_bars:int
    initial_cash:float
    max_position:float
    daily_kill:float
    total_kill:float
    min_quote_24h:float
    fee_bps:float
    slip_bps:float
    state_path:Path
    log_dir:Path
    max_stream_idle_seconds:int

def read_config(filename:str|Path)->Config:
    raw=yaml.safe_load(Path(filename).read_text(encoding="utf-8"))
    if not isinstance(raw,dict):raise ValueError("CONFIG_MUST_BE_MAPPING")
    unknown=set(raw)-ALLOWED
    if unknown:raise ValueError("DISALLOWED_CONFIG_FIELDS:"+",".join(sorted(unknown)))
    mode=raw.get("mode","binance_ws")
    if mode not in ("binance_ws","replay"):raise ValueError("PAPER_FEED_ONLY")
    symbols=tuple(raw.get("symbols",[]))
    if not symbols or len(set(symbols))!=len(symbols) or any(
        not isinstance(s,str) or not s.isupper() or
        not s.endswith("USDT") or not s.isalnum() for s in symbols
    ):raise ValueError("INVALID_UNIVERSE")
    if mode=="binance_ws" and len(symbols)!=10:raise ValueError("LIVE_FEED_REQUIRES_EXACTLY_TEN_SYMBOLS")
    if mode=="replay" and symbols!=("BTCUSDT",):raise ValueError("REPLAY_ONLY_VERIFIED_BTC")
    def path(key,default):
        v=Path(raw.get(key,default)).expanduser()
        return v.resolve() if v.is_absolute() else (ROOT/v).resolve()
    def number(key,default,lo,hi):
        x=raw.get(key,default)
        if isinstance(x,bool) or not isinstance(x,(float,int)) or not math.isfinite(x) or not lo<=x<=hi:
            raise ValueError("INVALID_"+key)
        return float(x)
    def integer(key,default,lo,hi):
        x=raw.get(key,default)
        if type(x) is not int or not lo<=x<=hi:raise ValueError("INVALID_"+key)
        return x
    return Config(mode,symbols,path("data_path","data/btc_usdt_m15_2022_2024.parquet"),
        path("manifest_path","data/btc_usdt_m15_2022_2024.manifest.json"),
        integer("warmup_bars",80,60,500),
        integer("replay_bars",280,1,4000),
        number("initial_cash",1000.,1000.,1000.),
        number("max_position",.10,.001,.10),
        number("daily_kill",.05,.001,.05),
        number("total_kill",.15,.001,.15),
        number("min_quote_24h",5e6,5e6,1e12),
        number("fee_bps",10.,0,50.),
        number("slip_bps",5.,0,100.),
        path("state_path","logs/bot/state.json"),
        path("log_dir","logs/bot"),
        integer("max_stream_idle_seconds",90,15,300))
