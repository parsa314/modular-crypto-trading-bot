"""Strict 2022-24 Binance Vision BTCUSDT spot 15m downloader; research-only."""
import argparse, hashlib, io, json, re, time, urllib.request, zipfile
from pathlib import Path
import numpy as np
import pandas as pd

ROOT="https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/15m"
COLUMNS=["open_time","open","high","low","close","volume","close_time","quote_volume","num_trades","taker_base","taker_quote","ignore"]
START=pd.Timestamp("2022-01-01T00:00:00Z")
END=pd.Timestamp("2025-01-01T00:00:00Z")

def download(url):
    failures=[]
    for attempt in range(3):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"Noncommercial Academic Research 1.0"})
            with urllib.request.urlopen(req,timeout=55) as res: data=res.read()
            if not data:raise ValueError("EMPTY_DOWNLOAD")
            return data
        except Exception as exc:
            failures.append(str(exc))
            time.sleep(attempt+1)
    raise RuntimeError(f"SOURCE_NOT_RETRIEVABLE {url} errors={failures}")

def fetch(month):
    name=f"BTCUSDT-15m-{month}.zip"
    url=f"{ROOT}/{name}"
    raw=download(url)
    checksum=download(url+".CHECKSUM").decode().strip()
    m=re.fullmatch(r"([a-fA-F0-9]{64})\s+\*?([^\s]+)",checksum)
    if not m or m.group(2)!=name:
        raise ValueError(f"INVALID_PUBLISHER_CHECKSUM {name}")
    actual=hashlib.sha256(raw).hexdigest()
    if actual.lower()!=m.group(1).lower():
        raise ValueError(f"SHA256_MISMATCH {name}")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members=[f for f in archive.namelist() if f.lower().endswith(".csv")]
        if len(members)!=1 or archive.testzip() is not None:
            raise ValueError(f"CORRUPT_OR_AMBIGUOUS_ZIP {name}")
        frame=pd.read_csv(io.BytesIO(archive.read(members[0])),header=None)
    if frame.shape[1]!=12:raise ValueError(f"BAD_KLINE_SCHEMA {name}")
    if not str(frame.iloc[0,0]).isdigit():
        if str(frame.iloc[0,0]).strip().lower().replace(" ","_")!="open_time":
            raise ValueError(f"BAD_HEADER {name}")
        frame=frame.iloc[1:].reset_index(drop=True)
    frame.columns=COLUMNS
    for col in COLUMNS:frame[col]=pd.to_numeric(frame[col],errors="raise")
    t=frame.open_time.astype("int64")
    a=pd.Timestamp(month+"-01",tz="UTC")
    finish=a+pd.offsets.MonthBegin(1)
    first=int(a.timestamp()*1000)
    last=int(finish.timestamp()*1000)-900000
    count=int((finish-a).total_seconds()/900)
    if len(frame)>count or int(t.iloc[0])<first or int(t.iloc[-1])>last:
        raise ValueError(f"MONTH_OUT_OF_BOUNDS {name}")
    if not (t.diff().iloc[1:]>0).all() or not (t.diff().iloc[1:]%900000==0).all():
        raise ValueError(f"NONMONOTONIC_OR_OFF_GRID_CANDLES {name}")
    bad_grid=int((((t-first)%900000)!=0).sum())
    bad_close=int(((frame.close_time-t)!=899999).sum())
    if bad_grid or bad_close:
        print("PUBLISHER_ARCHIVE_CLOCK_ANOMALY",name,
              "offgrid_open_rows",bad_grid,"nonstandard_close_rows",bad_close,flush=True)
    missing=int(count-len(frame))
    if missing:
        print(f"VERIFIED_ARCHIVE_HAS_GAPS {name} expected={count} observed={len(frame)} missing={missing}",flush=True)
    p=frame[["open","high","low","close","volume"]].astype(float)
    if not np.isfinite(p.to_numpy()).all() or (p[["open","high","low","close"]]<=0).any().any() or (p.volume<0).any():
        raise ValueError(f"NONFINITE_OR_NONPOSITIVE_DATA {name}")
    if not (p.high>=p[["open","low","close"]].max(axis=1)).all() or not (p.low<=p[["open","high","close"]].min(axis=1)).all():
        raise ValueError(f"OHLC_INVARIANT {name}")
    p.insert(0,"timestamp",pd.to_datetime(t,unit="ms",utc=True))
    p.insert(1,"available_at",p.timestamp+pd.Timedelta(minutes=15))
    proof={"month":month,"url":url,"checksum_url":url+".CHECKSUM","zip_sha256":actual,"rows":len(frame),"expected_rows":count,"missing_bars":missing,"offgrid_open_rows":bad_grid,"nonstandard_close_rows":bad_close}
    print("VERIFIED_MONTH",month,count,actual,flush=True)
    return p,proof

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,default=Path("data/btc_usdt_m15_2022_2024.parquet"))
    parser.add_argument("--manifest",type=Path,default=Path("data/btc_usdt_m15_2022_2024.manifest.json"))
    args=parser.parse_args()
    parts=[];proof=[]
    for year in [2022,2023,2024]:
        for month in range(1,13):
            f,record=fetch(f"{year}-{month:02d}")
            parts.append(f);proof.append(record)
    frame=pd.concat(parts,ignore_index=True)
    expected=105216
    missing=sum(row["missing_bars"] for row in proof)
    if len(frame)!=expected-missing:
        raise ValueError("COMPLETE_36_ARCHIVES_NOT_ACCOUNTED_FOR")
    if not frame.timestamp.is_unique or not (frame.timestamp.diff().iloc[1:]>pd.Timedelta(0)).all():
        raise ValueError("NONMONOTONIC_OR_DUPLICATE_ALL_MONTHS")
    non_m15_intervals=int((frame.timestamp.diff().iloc[1:]%pd.Timedelta(minutes=15)!=pd.Timedelta(0)).sum())
    if non_m15_intervals:
        print("OFFGRID_INTERBAR_INTERVALS",non_m15_intervals,flush=True)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    frame.to_parquet(args.output,index=False,compression="zstd")
    h=hashlib.sha256(args.output.read_bytes()).hexdigest()
    manifest={"source":ROOT,"venue":"Binance Spot","symbol":"BTCUSDT","timeframe":"15m","period":"2022-01-01/2024-12-31 UTC","rows":len(frame),"expected_rows":expected,"missing_bars":missing,"duplicate_bars":0,"offgrid_interbar_intervals":non_m15_intervals,"output_sha256":h,
    "publisher_zip_checksums":proof,"license":"Binance Vision CC BY-NC-SA 4.0 (noncommercial research ONLY)","license_url":"https://github.com/binance/binance-public-data/blob/master/TERMS_AND_CONDITIONS.md","candle_available_at":"open + 15min (additional distribution latency unknown)","production_or_live_authorization":False}
    args.manifest.write_text(json.dumps(manifest,indent=2)+"\n")
    print("VERIFIED_DATASET",json.dumps({"rows":len(frame),"missing_bars":missing,"sha256":h,"path":str(args.output)}),flush=True)

if __name__=="__main__":main()
