"""Daily UTC audit trail, append-only JSONL with fsync."""
from __future__ import annotations
from datetime import datetime,timezone
from pathlib import Path
import json,os

class AuditLog:
    def __init__(self,root:Path):
        self.root=root
        root.mkdir(parents=True,exist_ok=True)
    def emit(self,at:datetime,kind:str,**data):
        when=at.astimezone(timezone.utc)
        p=self.root/(when.date().isoformat()+".jsonl")
        payload={"at":when.isoformat(),"type":kind,**data}
        line=json.dumps(payload,sort_keys=True,allow_nan=False,default=str)+"\n"
        fd=os.open(str(p),os.O_CREAT|os.O_APPEND|os.O_WRONLY,0o600)
        try:
            os.write(fd,line.encode("utf-8"))
            os.fsync(fd)
        finally:os.close(fd)
