"""Opt-in authenticated research routes; no unauthenticated mail relay."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hmac
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from .contracts import ResearchRequest, email
from .delivery import EmailTransport, deliver
from .engine import FundamentalEngine
from .storage import ResearchStore


@dataclass(frozen=True)
class APISettings:
    database_path: str
    api_token: str
    recipient: str | None

    def __post_init__(self) -> None:
        if not self.database_path or self.database_path == ":memory:": raise ValueError("persistent research DB path required")
        if len(self.api_token) < 32 or "\n" in self.api_token or "\r" in self.api_token: raise ValueError("32-character research API token required")
        email(self.recipient)


class AnalysisInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=40)
    as_of: datetime | None = None
    idempotency_key: str = Field(min_length=1, max_length=120)
    scope: str = "SEPARATE"
    send_email: bool = True
    evidence_bundle: dict[str, Any]


def build_router(settings: APISettings, transport: EmailTransport | None = None) -> APIRouter:
    def authenticate(authorization: str | None = Header(default=None)) -> None:
        expected = "Bearer " + settings.api_token
        if authorization is None or not hmac.compare_digest(authorization.encode(), expected.encode()):
            raise HTTPException(status_code=401, detail="RESEARCH_AUTH_REQUIRED")

    router = APIRouter(prefix="/research/iran-equities", tags=["Iran fundamental research"], dependencies=[Depends(authenticate)])

    @router.post("/analyses")
    def analyze(body: AnalysisInput) -> dict:
        if body.send_email and settings.recipient is None:
            raise HTTPException(status_code=409, detail="RECIPIENT_NOT_CONFIGURED; use send_email=false for preview")
        try:
            request = ResearchRequest(body.symbol, body.as_of or datetime.now(timezone.utc), body.idempotency_key, settings.recipient if body.send_email else None, body.scope, body.as_of is None)
            with ResearchStore(settings.database_path) as store:
                report = FundamentalEngine(store).analyze(request, body.evidence_bundle)
                current = store.get_delivery(report["job_id"])
                if transport is not None and current["state"] == "QUEUED" and report["evidence_state"] != "ENGINEERING_FIXTURE":
                    try:
                        current = deliver(store, report["job_id"], transport)
                    except ValueError as exc:
                        if str(exc) != "DELIVERY_ALREADY_CLAIMED_OR_NOT_QUEUED":
                            raise
                        current = store.get_delivery(report["job_id"])
                return {"report": report, "delivery": current, "transport_configured": transport is not None}
        except (ValueError, KeyError, TypeError) as exc:
            message = str(exc)
            raise HTTPException(status_code=409 if "IDEMPOTENCY" in message else 422, detail="INVALID_RESEARCH_INPUT: " + message[:180]) from None

    @router.get("/analyses/{job_id}")
    def read_report(job_id: str) -> dict:
        with ResearchStore(settings.database_path) as store:
            try: return {"report": store.get_report(job_id), "delivery": store.get_delivery(job_id)}
            except KeyError: raise HTTPException(status_code=404, detail="REPORT_NOT_FOUND") from None

    @router.get("/analyses/{job_id}/html", response_class=HTMLResponse)
    def read_html(job_id: str) -> str:
        with ResearchStore(settings.database_path) as store:
            try: return FundamentalEngine(store).render_html(job_id)
            except KeyError: raise HTTPException(status_code=404, detail="REPORT_NOT_FOUND") from None

    @router.get("/analyses/{job_id}/delivery")
    def read_delivery(job_id: str) -> dict:
        with ResearchStore(settings.database_path) as store:
            try: return store.get_delivery(job_id)
            except KeyError: raise HTTPException(status_code=404, detail="REPORT_NOT_FOUND") from None

    @router.post("/analyses/{job_id}/deliver")
    def send_report(job_id: str) -> dict:
        if transport is None: raise HTTPException(status_code=409, detail="EMAIL_TRANSPORT_NOT_CONFIGURED")
        with ResearchStore(settings.database_path) as store:
            try: return deliver(store, job_id, transport)
            except KeyError: raise HTTPException(status_code=404, detail="REPORT_NOT_FOUND") from None
            except ValueError as exc: raise HTTPException(status_code=409, detail=str(exc)) from None

    return router
