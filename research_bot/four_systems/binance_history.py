"""Locked Binance public BTC_USDT 1h historical context, never order transport."""
from __future__ import annotations
from datetime import datetime
from hashlib import sha256
import json
import math
from pathlib import Path
import time
from typing import Callable
from urllib.error import HTTPError,URLError
from urllib.parse import urlencode
from urllib.request import Request,urlopen
import numpy as np
import pandas as pd
from .features import validate_bars as validate_internal

BINANCE_URL="https://api.binance.com/api/v3/klines"
INTERVAL_MS=3600000
BASIS="DECLARED_CLOSE_PLUS_LAG_NOT_OBSERVED"

def utc(value: str | datetime | pd.Timestamp) -> pd.Timestamp:
    t=pd.Timestamp(value)
    if pd.isna(t) or t.tzinfo is None:
        raise ValueError("timezone-aware UTC time required")
    return t.tz_convert("UTC")

def integer(value: object) -> int:
    if isinstance(value,(bool,np.bool_)):
        raise ValueError("boolean provider timestamp invalid")
    result=int(value)
    if str(result)!=str(value):
        raise ValueError("integer millisecond provider timestamp required")
    return result

def public_page(params: dict) -> list:
    url=BINANCE_URL+"?"+urlencode(params)
    for attempt in range(3):
        try:
            with urlopen(Request(url,headers={"User-Agent":"thesis-public-research/1.0"}),timeout=30) as response:
                body=response.read(1024*1024+1)
            if len(body)>1024*1024:
                raise ValueError("oversized public response")
            result=json.loads(body)
            if not isinstance(result,list):
                raise ValueError("Binance kline response must be an array")
            return result
        except HTTPError as error:
            if error.code not in (429,500,502,503,504) or attempt==2:
                raise
        except (URLError,TimeoutError):
            if attempt==2:raise
        time.sleep(2**attempt)
    raise RuntimeError("retry state exhausted")

def convert(raw: list[list], *, as_of: datetime, delay_s: float=0) -> pd.DataFrame:
    if isinstance(delay_s,bool) or not math.isfinite(delay_s) or not 0<=delay_s<3600:
        raise ValueError("modeled delay must be finite and within one hour")
    if not raw:raise ValueError("empty provider dataset")
    rows=[]
    for k in raw:
        if not isinstance(k,list) or len(k)!=12:
            raise ValueError("exact twelve-field Binance kline required")
        opened=integer(k[0]);provider_close=integer(k[6])
        if opened%INTERVAL_MS or provider_close!=opened+INTERVAL_MS-1:
            raise ValueError("provider open/close times do not define an aligned 1h bar")
        # Binance close_time is inclusive :59:59.999; canonical close is next :00.
        closed=pd.to_datetime(opened+INTERVAL_MS,unit="ms",utc=True)
        available=closed+pd.Timedelta(seconds=delay_s)
        if available>utc(as_of):
            raise ValueError("unclosed or not-yet-available bar rejected")
        for value in k[1:6]:
            if isinstance(value,(bool,np.bool_)):
                raise ValueError("boolean OHLCV rejected")
        rows.append({"timestamp_close":closed,"available_at":available,
            **dict(zip(("open","high","low","close","volume"),k[1:6])),"symbol":"BTC_USDT",
            "availability_basis":BASIS,"data_class":"HISTORICAL_BACKFILL"})
    frame=pd.DataFrame(rows)
    for field in ("open","high","low","close","volume"):
        frame[field]=pd.to_numeric(frame[field],errors="raise")
    validate(frame)
    return frame

def validate(frame: pd.DataFrame) -> pd.DataFrame:
    required={'timestamp_close','available_at','symbol','open','high','low','close','volume'}
    if not required.issubset(frame.columns) or frame.empty:
        raise ValueError('nonempty closed OHLCV with required columns needed')
    if "timestamp_close" not in frame:
        raise ValueError("explicit timestamp_close required; open-time schema rejected")
    if not frame.symbol.eq("BTC_USDT").all():
        raise ValueError("registered market is BTC_USDT only")
    if "timestamp" in frame:
        if not pd.to_datetime(frame.timestamp,utc=True).eq(pd.to_datetime(frame.timestamp_close,utc=True)).all():
            raise ValueError("conflicting timestamp aliases")
        normalized=frame.drop(columns="timestamp_close")
    else:
        normalized=frame.rename(columns={"timestamp_close":"timestamp"})
    return validate_internal(normalized)

def collect(start: str | datetime, end: str | datetime, *, as_of: datetime,
            delay_s: float=0,sleep_s: float=.15,
            getter: Callable[[dict],list]=public_page) -> tuple[pd.DataFrame,dict]:
    if isinstance(delay_s,bool) or not math.isfinite(delay_s) or not 0<=delay_s<3600:
        raise ValueError("finite modeled lag within one hour required")
    if isinstance(sleep_s,bool) or not math.isfinite(sleep_s) or not 0<=sleep_s<=10:
        raise ValueError("bounded finite page pause required")
    first=utc(start).ceil("h")
    cutoff=min(utc(end),utc(as_of)-pd.Timedelta(seconds=delay_s)).floor("h")
    if first>=cutoff:
        raise ValueError("no closed complete bar in registered range")
    start_ms=first.value//1000000;last_open_ms=(cutoff-pd.Timedelta(hours=1)).value//1000000
    cursor=start_ms;raw=[];pages=[]
    while cursor<=last_open_ms:
        params={"symbol":"BTCUSDT","interval":"1h","startTime":cursor,"endTime":last_open_ms,"limit":1000}
        batch=getter(params)
        if not isinstance(batch,list) or not batch or len(batch)>1000:
            raise ValueError("empty/malformed/oversized page before requested range completion")
        opens=[]
        for row in batch:
            if not isinstance(row,list) or len(row)!=12:
                raise ValueError("malformed Binance kline page")
            opens.append(integer(row[0]))
        expected=[cursor+INTERVAL_MS*i for i in range(len(batch))]
        if opens!=expected or opens[-1]>last_open_ms:
            raise ValueError("page gap, duplicate, disorder, nonprogress or out-of-window data")
        raw.extend(batch)
        pages.append({"requested_start_ms":cursor,"rows":len(batch),
            "canonical_payload_sha256":sha256(json.dumps(batch,separators=(",",":"),allow_nan=False).encode()).hexdigest()})
        cursor=opens[-1]+INTERVAL_MS
        if cursor<=last_open_ms:time.sleep(sleep_s)
    frame=convert(raw,as_of=as_of,delay_s=delay_s)
    count=(last_open_ms-start_ms)//INTERVAL_MS+1
    if len(frame)!=count:
        raise ValueError("incomplete requested historical window")
    return frame,{"status":"VALIDATED_HISTORICAL_CONTEXT","source":"BINANCE_PUBLIC_SPOT_KLINES",
        "symbol":"BTC_USDT","timeframe":"1h","rows":len(frame),"first_close":str(frame.timestamp_close.iloc[0]),
        "last_close":str(frame.timestamp_close.iloc[-1]),"downloaded_at":utc(as_of).isoformat(),
        "availability_basis":BASIS,"declared_delay_s":delay_s,"prospective_evidence":False,
        "observed_historical_availability_verified":False,"RESEARCH_ONLY":True,
        "PAPER_EXECUTION":False,"LIVE_EXECUTION":False,"pages":pages}

def save(frame: pd.DataFrame, manifest: dict, path: Path) -> dict:
    validate(frame)
    data=frame.to_csv(index=False,float_format="%.17g",lineterminator="\n").encode()
    meta={**manifest,"csv_sha256":sha256(data).hexdigest()}
    sidecar=path.with_suffix(".manifest.json")
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if path.read_bytes()!=data or not sidecar.exists():
            raise FileExistsError("existing different/incomplete dataset; choose a new output")
        prior=json.loads(sidecar.read_text())
        if prior.get("csv_sha256")!=meta["csv_sha256"]:raise ValueError("existing manifest digest mismatch")
        return prior
    partial=path.with_suffix(".csv.partial")
    with partial.open("xb") as stream:stream.write(data)
    try:
        with sidecar.open("x") as stream:json.dump(meta,stream,indent=2,allow_nan=False);stream.write("\n")
        path.hardlink_to(partial)
    finally:partial.unlink(missing_ok=True)
    return meta

def read_research_csv(path: Path) -> pd.DataFrame:
    frame=pd.read_csv(path);normalized=validate(frame)
    sidecar=path.with_suffix(".manifest.json")
    basis="INPUT_DECLARED_NOT_VERIFIED"
    if sidecar.exists():
        meta=json.loads(sidecar.read_text())
        if meta.get("csv_sha256")!=sha256(path.read_bytes()).hexdigest():
            raise ValueError("historical CSV digest mismatch")
        basis=meta.get("availability_basis","UNVERIFIED")
    normalized.attrs["availability_basis"]=basis
    return normalized
