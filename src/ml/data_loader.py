"""Spot daily public Binance loader; historical roster proxy, no credentials."""
from __future__ import annotations
import hashlib, json, math, time
from urllib.parse import urlencode
from urllib.request import Request,urlopen
import pandas as pd

DATA_START="2019-01-01"
TRAIN_END="2023-01-01"
VALID_END="2024-01-01"
TEST_END=VALID_END  # No Test 2024-26 download or access.
EPOCH=lambda d:int(pd.Timestamp(d,tz="UTC").timestamp()*1000)
UNIVERSE = """
BTC ETH BNB SOL XRP ADA DOGE DOT LINK LTC AVAX SHIB ATOM UNI ETC XLM BCH TRX FIL AAVE
ALGO EOS XTZ DASH ZEC COMP MKR CRV SUSHI SNX YFI BAL ENJ CHZ NEAR GRT
AXS SAND MANA ICP VET HBAR THETA EGLD RUNE KAVA ONE ZIL BAT NEO
""".split()
SYMBOLS=[a+"USDT" for a in UNIVERSE]
if len(SYMBOLS)!=50 or len(set(SYMBOLS))!=50:raise RuntimeError("50_SYMBOL_REGISTRY_INVARIANT")

def read_public_klines(symbol):
    """Paginate read-only Binance daily historical klines; no account data."""
    start,stop=EPOCH(DATA_START),EPOCH(TEST_END)
    cursor=start
    found={}
    endpoints=set()
    while cursor<stop:
        q=urlencode({"symbol":symbol,"interval":"1d","startTime":cursor,
                     "endTime":stop-1,"limit":1000})
        content=None;errors=[]
        for endpoint in ("https://data-api.binance.vision","https://api.binance.com"):
            for attempt in range(2):
                try:
                    with urlopen(Request(endpoint+"/api/v3/klines?"+q,headers={
                        "User-Agent":"AcademicResearchOnly/1.0"}),timeout=35) as response:
                        content=json.loads(response.read())
                    if not isinstance(content,list):raise RuntimeError("NON_LIST_BINANCE_RESPONSE")
                    endpoints.add(endpoint)
                    break
                except Exception as exc:
                    errors.append(str(exc)[:150])
                    time.sleep(.6+attempt)
            if content is not None:break
        if content is None:raise RuntimeError(f"PUBLIC_PRICE_UNAVAILABLE {symbol} {errors[-2:]}")
        if not content:break
        for a in content:
            t=int(a[0])
            if t<start or t>=stop:continue
            row=(float(a[1]),float(a[2]),float(a[3]),float(a[4]),
                 float(a[5]),float(a[7]))
            if not all(math.isfinite(x) for x in row) or min(row[:4])<=0 or row[4]<0 or row[5]<0:
                raise ValueError(f"INVALID_OHLCV {symbol} {t}")
            if row[1]<max(row[0],row[2],row[3]) or row[2]>min(row[0],row[3]):
                raise ValueError(f"INVALID_HILO {symbol} {t}")
            if t in found and found[t]!=row:raise RuntimeError("BINANCE_DUPLICATE_CONFLICT")
            found[t]=row
        cursor2=int(content[-1][0])+86400000
        if cursor2<=cursor:raise RuntimeError("STALLED_DOWNLOAD")
        cursor=cursor2
        time.sleep(.10)
    if not found:
        return None,{"symbol":symbol,"status":"NO_HISTORY","rows":0}
    times=sorted(found)
    gaps=sum(times[i]-times[i-1]!=86400000 for i in range(1,len(times)))
    if gaps:
        # Gaps inside a known listing must not be ffilled or silently crossed.
        print("REAL_BAR_GAPS",symbol,gaps,flush=True)
    p=pd.DataFrame([found[t] for t in times],
        columns=["open","high","low","close","volume","quote_volume"])
    p["timestamp"]=pd.to_datetime(times,unit="ms",utc=True)
    p=p.set_index("timestamp")
    h=hashlib.sha256(json.dumps([(t,*found[t]) for t in times],
                       separators=(",",":")).encode()).hexdigest()
    manifest={"symbol":symbol,"first":str(p.index[0]),"last":str(p.index[-1]),
              "rows":len(p),"internal_gaps":gaps,"sha256_rows":h,
              "api_endpoints":sorted(endpoints),"status":"AVAILABLE"}
    return p,manifest

