"""NON-PROSPECTIVE data/adapter smoke; no return or trading simulation."""
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd
from research_bot.strategy_research_012 import baseline_candidates


def main():
    path, manifest_path, output = map(Path,sys.argv[1:4])
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = json.loads(manifest_path.read_text())
    if digest != manifest["output_sha256"] or digest != "f7bf84ccaff343dbb627c17e6245ae6f2488f940a3ed6867efd262115ea9d401":
        raise ValueError("DATA_DIGEST_MISMATCH")
    data = pd.read_parquet(path)
    data["timestamp"] = pd.to_datetime(data["timestamp"],utc=True)
    if not data.timestamp.is_unique or not data.timestamp.is_monotonic_increasing:
        raise ValueError("INVALID_SOURCE_TIMESTAMPS")
    data = data[(data.timestamp >= "2024-12-01T00:00:00Z") & (data.timestamp < "2024-12-05T00:00:00Z")]
    rows=[]
    for stamp, group in data.groupby(data.timestamp.dt.floor("h"),sort=True):
        if len(group) != 4 or set(group.timestamp.dt.minute) != {0,15,30,45}:
            raise ValueError("MISSING_15M_BAR")
        rows.append({"timestamp":stamp,"available_at":stamp+pd.Timedelta(hours=1),
                     "open":group.open.iloc[0],"high":group.high.max(),"low":group.low.min(),
                     "close":group.close.iloc[-1],"volume":group.volume.sum()})
    if len(rows) != 96:
        raise ValueError("INCOMPLETE_HISTORICAL_WINDOW")
    frame=pd.DataFrame(rows)
    counts={"ICHIMOKU":0,"ICT":0,"SMC":0,"AL_BROOKS":0}
    for index in range(89,len(frame)):
        events=baseline_candidates(frame,symbol="BTC/USDT",timeframe="H1",
            as_of=frame.available_at.iloc[index].to_pydatetime(),source_id="BINANCE_VISION_SOURCE_COMMIT_cda9e5ff47d0f40cb62b6734c784317d2f15e405",
            source_sha256=digest,availability_kind="HISTORICAL_CLOSE_ASSUMED")
        for event in events:
            counts[event.style]+=1
    report={"evidence_state":"NON_PROSPECTIVE_ENGINEERING_SMOKE", "source_sha256":digest,
        "start":"2024-12-01T00:00:00Z", "end_exclusive":"2024-12-05T00:00:00Z",
        "availability":"HISTORICAL_CLOSE_ASSUMED", "first_seen_at":None,
        "hourly_bars":len(frame),"decisions":len(frame)-89,"baseline_event_counts":counts,
        "economic_evaluation":"NOT_EXECUTED", "win_rate":None,"pbo":None,"dsr":None,
        "final_holdout_used":False,"status":"BLOCKED"}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
    print(json.dumps(report,sort_keys=True))


if __name__ == "__main__": main()
