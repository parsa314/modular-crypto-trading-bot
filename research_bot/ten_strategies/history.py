"""Strict public Binance SPOT archive ingestion; backfill is never prospective."""
from __future__ import annotations
from datetime import datetime,timezone
from hashlib import sha256
from io import BytesIO
from urllib.request import Request,urlopen
from zipfile import ZipFile
from typing import Callable
import pandas as pd
from ..four_systems.features import validate_bars

BASE="https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1h/"
def download(url: str) -> bytes:
    with urlopen(Request(url,headers={"User-Agent":"thesis-public-research/1.0"}),timeout=45) as response:
        data=response.read(16*1024*1024+1)
    if len(data)>16*1024*1024:raise ValueError("oversized archive response")
    return data

def archive_month(month: str,getter: Callable[[str],bytes]=download) -> tuple[pd.DataFrame,dict]:
    period=pd.Period(month,freq="M")
    if str(period)!=month:raise ValueError("exact YYYY-MM archive month required")
    name="BTCUSDT-1h-"+month+".zip";url=BASE+name
    payload=getter(url);check=getter(url+".CHECKSUM").decode("ascii").split()
    if not check or check[0].lower()!=sha256(payload).hexdigest():raise ValueError("required provider checksum mismatch")
    with ZipFile(BytesIO(payload)) as zipped:
        names=zipped.namelist()
        if len(names)!=1 or names[0]!=name[:-4]+".csv" or zipped.getinfo(names[0]).file_size>8*1024*1024:
            raise ValueError("unexpected bounded archive CSV structure")
        raw=pd.read_csv(BytesIO(zipped.read(names[0])),header=None,dtype=str)
    if raw.empty or raw.shape[1]!=12:raise ValueError("exact twelve-column monthly klines required")
    unit="us" if period>=pd.Period("2025-01",freq="M") else "ms"
    opens=pd.to_numeric(raw[0],errors="raise");closes=pd.to_numeric(raw[6],errors="raise")
    step=3600000000 if unit=="us" else 3600000
    delta=opens+step-closes
    if not delta.isin((1,1000) if unit=="us" else (1,)).all():raise ValueError("provider inclusive close mismatch")
    opened=pd.to_datetime(opens,unit=unit,utc=True);closed=opened+pd.Timedelta(hours=1)
    if not opened.dt.strftime("%Y-%m").eq(month).all():raise ValueError("archive month mismatch")
    x=pd.DataFrame({"timestamp":closed,"available_at":closed+pd.Timedelta(seconds=2),
        "open":raw[1],"high":raw[2],"low":raw[3],"close":raw[4],"volume":raw[5],"symbol":"BTC_USDT"})
    normalized=validate_bars(x)
    first=period.start_time.tz_localize("UTC")+pd.Timedelta(hours=1)
    last=(period+1).start_time.tz_localize("UTC")
    if normalized.timestamp.iloc[0]!=first or normalized.timestamp.iloc[-1]!=last or len(normalized)!=(last-first)/pd.Timedelta(hours=1)+1:
        raise ValueError("incomplete archive month")
    return normalized,{"month":month,"archive_url":url,"archive_sha256":sha256(payload).hexdigest(),
        "checksum_verified":True,"timestamp_unit":unit,"rows":len(normalized)}

def collect_archives(start: str="2023-10",end: str="2026-09",getter: Callable[[str],bytes]=download) -> tuple[pd.DataFrame,dict]:
    periods=pd.period_range(start,end,freq="M")
    if not len(periods) or len(periods)>60:raise ValueError("nonempty bounded monthly range required")
    now=pd.Timestamp(datetime.now(timezone.utc))
    if (periods[-1]+1).start_time.tz_localize("UTC")>now:raise ValueError("incomplete current/future month forbidden")
    parts=[];sources=[]
    for period in periods:
        frame,source=archive_month(str(period),getter);parts.append(frame);sources.append(source)
    x=validate_bars(pd.concat(parts,ignore_index=True))
    return x,{"source":"BINANCE_PUBLIC_SPOT_MONTHLY_ARCHIVES","sources":sources,
        "downloaded_at":now.isoformat(),"availability_basis":"DECLARED_CLOSE_PLUS_2_SECONDS_NOT_OBSERVED",
        "observed_historical_availability_verified":False,"prospective_evidence":False,"rows":len(x),
        "RESEARCH_ONLY":True,"PAPER_EXECUTION":False,"LIVE_EXECUTION":False}
