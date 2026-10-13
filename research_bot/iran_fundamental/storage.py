"""Transactional immutable report snapshots and an on-demand delivery outbox."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

from .contracts import canonical


class ResearchStore:
    def __init__(self, path: str):
        if path != ":memory:": Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=15)
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=15000")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS iran_research_reports (
                job_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
                request_hash TEXT NOT NULL, report_json TEXT NOT NULL,
                evidence_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS iran_email_outbox (
                delivery_id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE
                REFERENCES iran_research_reports(job_id), recipient TEXT NOT NULL,
                payload_hash TEXT NOT NULL, state TEXT NOT NULL,
                provider_message_id TEXT, failure_code TEXT,
                CHECK(state IN ('QUEUED','SENDING','SENT','DELIVERY_UNCERTAIN','FAILED'))
            );
        """)

    def __enter__(self) -> ResearchStore: return self
    def __exit__(self, *args: Any) -> None: self.connection.close()

    def replay(self, key: str, request_hash: str) -> dict | None:
        row = self.connection.execute("SELECT request_hash, report_json FROM iran_research_reports WHERE idempotency_key=?", (key,)).fetchone()
        if row is None: return None
        if row[0] != request_hash: raise ValueError("IDEMPOTENCY_PAYLOAD_CONFLICT")
        return json.loads(row[1])

    def save(self, key: str, request_hash: str, report: dict, evidence: dict, recipient: str | None) -> dict:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            previous = self.replay(key, request_hash)
            if previous is not None:
                self.connection.commit(); return previous
            self.connection.execute("INSERT INTO iran_research_reports VALUES (?,?,?,?,?)", (report["job_id"], key, request_hash, canonical(report), canonical(evidence)))
            if recipient:
                self.connection.execute("INSERT INTO iran_email_outbox VALUES (?,?,?,?,?,NULL,NULL)", (report["job_id"], report["job_id"], recipient, report["report_hash"], "QUEUED"))
            self.connection.commit(); return report
        except BaseException:
            self.connection.rollback(); raise

    def get_report(self, job_id: str) -> dict:
        row = self.connection.execute("SELECT report_json FROM iran_research_reports WHERE job_id=?", (job_id,)).fetchone()
        if row is None: raise KeyError("REPORT_NOT_FOUND")
        return json.loads(row[0])

    def get_delivery(self, job_id: str) -> dict:
        self.get_report(job_id)
        cursor = self.connection.execute("SELECT recipient,state,provider_message_id,failure_code FROM iran_email_outbox WHERE job_id=?", (job_id,))
        row = cursor.fetchone()
        return {"state": "NOT_REQUESTED"} if row is None else dict(zip(("recipient", "state", "provider_message_id", "failure_code"), row))

    def claim_delivery(self, job_id: str) -> dict:
        with self.connection:
            result = self.connection.execute("UPDATE iran_email_outbox SET state='SENDING' WHERE job_id=? AND state='QUEUED'", (job_id,))
        if result.rowcount != 1: raise ValueError("DELIVERY_ALREADY_CLAIMED_OR_NOT_QUEUED")
        return self.get_delivery(job_id)

    def finish_delivery(self, job_id: str, state: str, message_id: str | None = None, failure: str | None = None) -> None:
        if state not in {"SENT", "DELIVERY_UNCERTAIN", "FAILED"} or (state == "SENT" and not message_id):
            raise ValueError("valid provider receipt required")
        with self.connection:
            result = self.connection.execute("UPDATE iran_email_outbox SET state=?,provider_message_id=?,failure_code=? WHERE job_id=? AND state='SENDING'", (state, message_id, failure, job_id))
        if result.rowcount != 1: raise ValueError("INVALID_DELIVERY_TRANSITION")
