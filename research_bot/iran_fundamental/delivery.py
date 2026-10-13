"""Explicit on-demand Gmail delivery; uncertain sends are never blindly retried."""
from __future__ import annotations

import base64
from email.message import EmailMessage
from email.policy import SMTP
import json
from typing import Protocol
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .contracts import email
from .engine import FundamentalEngine
from .storage import ResearchStore


class EmailTransport(Protocol):
    def send(self, recipient: str, subject: str, html: str, report: dict) -> str: ...


class DefiniteDeliveryFailure(RuntimeError):
    pass


class GmailAPITransport:
    """Runtime OAuth with Gmail send permission, separate from ChatGPT's token."""
    def __init__(self, access_token: str):
        if not access_token or "\r" in access_token or "\n" in access_token: raise ValueError("runtime Gmail OAuth token required")
        self._token = access_token

    def send(self, recipient: str, subject: str, html: str, report: dict) -> str:
        email(recipient)
        message = EmailMessage(policy=SMTP)
        message["To"] = recipient; message["Subject"] = subject
        message.set_content("گزارش پژوهشی با محدودیت‌های صریح؛ اطلاعات ناکافی برای توصیه خرید.", charset="utf-8")
        message.add_alternative(html, subtype="html", charset="utf-8")
        message.add_attachment(json.dumps(report, ensure_ascii=False, indent=2).encode(), maintype="application", subtype="json", filename="research-report.json")
        payload = json.dumps({"raw": base64.urlsafe_b64encode(message.as_bytes()).decode()}).encode()
        request = Request("https://gmail.googleapis.com/gmail/v1/users/me/messages/send", data=payload, headers={"Authorization": "Bearer " + self._token, "Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=30) as response:
                data = json.loads(response.read(100_000))
        except HTTPError as exc:
            if exc.code in {400, 401, 403, 404, 429}: raise DefiniteDeliveryFailure(f"GMAIL_HTTP_{exc.code}") from None
            raise RuntimeError("GMAIL_DELIVERY_UNCERTAIN") from None
        receipt = data.get("id")
        if not isinstance(receipt, str) or not receipt.strip() or len(receipt) > 256:
            raise RuntimeError("GMAIL_RECEIPT_UNAVAILABLE")
        return receipt


def deliver(store: ResearchStore, job_id: str, transport: EmailTransport) -> dict:
    report = store.get_report(job_id)
    if report["evidence_state"] == "ENGINEERING_FIXTURE": raise ValueError("FIXTURE_EMAIL_FORBIDDEN")
    current = store.get_delivery(job_id)
    if current["state"] == "SENT": return current
    html = FundamentalEngine(store).render_html(job_id)
    claimed = store.claim_delivery(job_id)
    try:
        receipt = transport.send(claimed["recipient"], f"تحلیل بنیادی {report['symbol']} — {report['as_of']} — اطلاعات ناکافی", html, report)
        if not isinstance(receipt, str) or not receipt.strip(): raise RuntimeError("receipt missing")
    except DefiniteDeliveryFailure:
        store.finish_delivery(job_id, "FAILED", failure="PROVIDER_REJECTED")
    except Exception:
        store.finish_delivery(job_id, "DELIVERY_UNCERTAIN", failure="RECONCILIATION_REQUIRED")
    else:
        store.finish_delivery(job_id, "SENT", message_id=receipt)
    return store.get_delivery(job_id)
