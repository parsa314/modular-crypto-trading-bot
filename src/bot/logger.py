"""UTC wall-clock audit files; durable appended JSONL."""
from __future__ import annotations
from datetime import datetime,timezone
import json,os

def log(cfg,event,**fields):
    now=datetime.now(timezone.utc)
    cfg.log_dir.mkdir(parents=True,exist_ok=True)
    dest=cfg.log_dir/(now.date().isoformat()+".jsonl")
    line=json.dumps({"wall_time_utc":now.isoformat(),"event":event,**fields},
                    sort_keys=True,allow_nan=False,default=str)+"\n"
    fd=os.open(str(dest),os.O_CREAT|os.O_WRONLY|os.O_APPEND,0o600)
    try:
        os.write(fd,line.encode("utf-8"));os.fsync(fd)
    finally:
        os.close(fd)
