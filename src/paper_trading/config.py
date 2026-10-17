"""Validated configuration. Deliberately rejects credentials and live execution."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import yaml

_ALLOWED = {"mode","symbols","data_path","manifest_path","replay_hours","initial_cash",
            "max_position_weight","max_daily_drawdown","max_total_drawdown",
            "min_quote_volume_24h","fee_bps","momentum_days","rsi_period",
            "warmup_hours","log_dir","state_path","strategy"}
@dataclass(frozen=True)
class Config:
    mode:str
    symbols:tuple[str,...]
    data_path:Path
    manifest_path:Path
    replay_hours:int
    initial_cash:float
    max_position_weight:float
    max_daily_drawdown:float
    max_total_drawdown:float
    min_quote_volume_24h:float
    fee_bps:float
    momentum_days:int
    rsi_period:int
    warmup_hours:int
    log_dir:Path
    state_path:Path
    strategy:str

def load(path:str|Path)->Config:
    p=Path(path).expanduser().resolve()
    raw=yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw,dict):raise ValueError("CONFIG_NOT_MAPPING")
    bad=set(raw)-_ALLOWED
    if bad:raise ValueError("UNSUPPORTED_CONFIG_FIELDS: "+",".join(sorted(bad)))
    mode=raw.get("mode","parquet")
    if mode not in {"parquet","binance_ws"}:raise ValueError("ONLY_PARQUET_OR_READ_ONLY_WS")
    strategy=raw.get("strategy","momentum_rsi_fallback")
    if strategy!="momentum_rsi_fallback":
        raise ValueError("V3_NOT_VALIDATED_ONLY_FROZEN_MOMENTUM_RSI_FALLBACK")
    symbols=tuple(str(s).upper() for s in raw.get("symbols",["BTCUSDT"]))
    if not symbols or len(symbols)>20 or len(set(symbols))!=len(symbols) or any(
        not s.isalnum() or not s.endswith("USDT") for s in symbols
    ):raise ValueError("INVALID_USDT_UNIVERSE")
    def number(k,default,minimum,maximum):
        v=raw.get(k,default)
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not minimum<=v<=maximum:
            raise ValueError("INVALID_"+k)
        return float(v)
    def integer(k,default,minimum,maximum):
        v=raw.get(k,default)
        if type(v) is not int or not minimum<=v<=maximum:raise ValueError("INVALID_"+k)
        return v
    def resolve(k,default):
        q=Path(raw.get(k,default)).expanduser()
        return q.resolve() if q.is_absolute() else (p.parent/q).resolve()
    return Config(
        mode, symbols,resolve("data_path","data/btc_usdt_m15_2022_2024.parquet"),
        resolve("manifest_path","data/btc_usdt_m15_2022_2024.manifest.json"),
        integer("replay_hours",168,1,10000),
        number("initial_cash",50000,1,1e12),
        number("max_position_weight",.10,0.00001,.10),
        number("max_daily_drawdown",.05,0.00001,.05),
        number("max_total_drawdown",.15,0.00001,.15),
        number("min_quote_volume_24h",5e6,5e6,1e12),
        number("fee_bps",10,10,100),
        integer("momentum_days",28,28,28),
        integer("rsi_period",14,14,14),
        integer("warmup_hours",720,700,1000),
        resolve("log_dir","logs"),resolve("state_path","logs/paper_state.json"),strategy)
