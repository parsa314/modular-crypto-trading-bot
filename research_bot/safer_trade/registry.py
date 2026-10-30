"""Transactional append-only research registry using canonical v17 trial records."""
from __future__ import annotations

from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from research_bot.trial_registry_v17 import TrialRecord,validate_trial
from .protocol import canonical,digest


class StudyRegistry:
    def __init__(self,path: str | Path) -> None:
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self._connect() as conn:
            conn.executescript('''
            CREATE TABLE IF NOT EXISTS study_events(
              seq INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT NOT NULL UNIQUE,
              kind TEXT NOT NULL,created_at TEXT NOT NULL,payload TEXT NOT NULL,
              previous_hash TEXT NOT NULL,event_hash TEXT NOT NULL UNIQUE);
            CREATE TRIGGER IF NOT EXISTS no_event_update BEFORE UPDATE ON study_events
              BEGIN SELECT RAISE(ABORT,'append-only registry'); END;
            CREATE TRIGGER IF NOT EXISTS no_event_delete BEFORE DELETE ON study_events
              BEGIN SELECT RAISE(ABORT,'append-only registry'); END;
            ''')

    def _connect(self) -> sqlite3.Connection:
        conn=sqlite3.connect(self.path,timeout=10,isolation_level=None)
        conn.execute('PRAGMA busy_timeout=10000')
        return conn

    def append(self,event_id: str,kind: str,payload: dict[str,Any]) -> str:
        if not event_id or not kind:raise ValueError("event identity required")
        text=canonical(payload)
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            try:
                old=conn.execute('SELECT kind,payload,event_hash FROM study_events WHERE event_id=?',(event_id,)).fetchone()
                if old:
                    if old[0]!=kind or old[1]!=text:raise ValueError("immutable event identity conflict")
                    conn.commit();return old[2]
                previous=conn.execute('SELECT event_hash FROM study_events ORDER BY seq DESC LIMIT 1').fetchone()
                at=datetime.now(timezone.utc).isoformat()
                row={'event_id':event_id,'kind':kind,'created_at':at,'payload':payload,'previous_hash':previous[0] if previous else '0'*64}
                result=digest(row)
                conn.execute('INSERT INTO study_events(event_id,kind,created_at,payload,previous_hash,event_hash) VALUES (?,?,?,?,?,?)',
                             (event_id,kind,at,text,row['previous_hash'],result))
                conn.commit();return result
            except BaseException:
                conn.rollback();raise

    def register_trial(self,record: TrialRecord) -> str:
        validate_trial(record)
        return self.append('trial:'+record.trial_id,'TRIAL',record.canonical_payload())

    def verify(self,expected_head: str | None=None) -> dict[str,Any]:
        previous='0'*64
        with self._connect() as conn:rows=conn.execute('SELECT event_id,kind,created_at,payload,previous_hash,event_hash FROM study_events ORDER BY seq').fetchall()
        for event_id,kind,at,payload,parent,stored in rows:
            actual=digest({'event_id':event_id,'kind':kind,'created_at':at,'payload':json.loads(payload),'previous_hash':parent})
            if parent!=previous or actual!=stored:raise ValueError("registry hash chain corruption")
            previous=actual
        if expected_head is not None and previous!=expected_head:raise ValueError("external registry head mismatch")
        return {'status':'PASS','n_events':len(rows),'head_sha256':previous,
                'limitation':'Trusted external head required against whole-file administrator rewriting'}

    def trials(self) -> list[TrialRecord]:
        self.verify()
        with self._connect() as conn:rows=conn.execute("SELECT payload FROM study_events WHERE kind='TRIAL' ORDER BY seq").fetchall()
        return [TrialRecord(**json.loads(row[0])) for row in rows]
