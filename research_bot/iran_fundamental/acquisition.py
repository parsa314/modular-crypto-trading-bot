"""Bounded official-source acquisition; raw capture is not financial verification."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

HOSTS = frozenset({"codal.ir", "www.codal.ir", "search.codal.ir",
                   "tsetmc.com", "www.tsetmc.com", "cdn.tsetmc.com"})
MAX_BYTES = 4_000_000


def validate_url(url: str) -> str:
    if not isinstance(url, str) or len(url) > 4096 or any(c.isspace() for c in url):
        raise ValueError("INVALID_SOURCE_URL")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in HOSTS
            or parsed.username is not None or parsed.password is not None
            or parsed.port not in (None, 443) or parsed.fragment):
        raise ValueError("OFFICIAL_HTTPS_SOURCE_REQUIRED")
    return url


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, "REDIRECT_FORBIDDEN", headers, fp)


def fetch_bytes(url: str) -> tuple[bytes, str]:
    request = Request(validate_url(url), headers={"User-Agent": "IranResearch/0.1"})
    with build_opener(NoRedirect()).open(request, timeout=15) as response:
        if response.status != 200:
            raise HTTPError(url, response.status, "UNEXPECTED_STATUS", response.headers, None)
        body = response.read(MAX_BYTES + 1)
        if not body or len(body) > MAX_BYTES:
            raise ValueError("EMPTY_OR_OVERSIZED_SOURCE")
        return body, response.headers.get("Content-Type", "application/octet-stream")


def capture_source(database: str, request_key: str, url: str,
                   fetcher: Callable[[str], tuple[bytes, str]] = fetch_bytes) -> dict:
    """Persist a raw observation. A blocked receipt needs a new key for retry.

    fetcher is a test seam; injected results never acquire VERIFIED status.
    A write transaction serializes replay and capture for this small collector.
    """
    validate_url(url)
    if not isinstance(request_key, str) or not 1 <= len(request_key) <= 120:
        raise ValueError("REQUEST_KEY_REQUIRED")
    if not database or database == ":memory:":
        raise ValueError("PERSISTENT_DATABASE_REQUIRED")
    Path(database).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database, timeout=30)
    try:
        connection.execute("""CREATE TABLE IF NOT EXISTS iran_source_captures (
            request_key TEXT PRIMARY KEY, url TEXT NOT NULL,
            receipt_json TEXT NOT NULL, raw_body BLOB)""")
        connection.execute("BEGIN IMMEDIATE")
        previous = connection.execute(
            "SELECT url, receipt_json FROM iran_source_captures WHERE request_key=?",
            (request_key,)).fetchone()
        if previous:
            if previous[0] != url:
                raise ValueError("IDEMPOTENCY_URL_CONFLICT")
            return json.loads(previous[1])
        started = datetime.now(timezone.utc).isoformat()
        body = None
        receipt = {"schema_version": "1.0.0", "url": url, "requested_at": started,
                   "evidence_state": "ENGINEERING", "status": "BLOCKED",
                   "financial_verification": "UNVERIFIED", "publication_at": None,
                   "sha256": None, "byte_count": 0, "failure_code": None}
        try:
            raw, media_type = fetcher(url)
            if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BYTES:
                raise ValueError("EMPTY_OR_OVERSIZED_SOURCE")
            if not isinstance(media_type, str) or len(media_type) > 512:
                raise ValueError("INVALID_CONTENT_TYPE")
            body = raw
            receipt.update(status="RAW_CAPTURED_UNVERIFIED",
                           sha256=hashlib.sha256(raw).hexdigest(), byte_count=len(raw),
                           content_type=media_type)
        except HTTPError as exc:
            receipt["failure_code"] = f"HTTP_{exc.code}"
        except (URLError, OSError, TimeoutError):
            receipt["failure_code"] = "TRANSPORT_UNAVAILABLE"
        except ValueError:
            receipt["failure_code"] = "INVALID_SOURCE_RESPONSE"
        receipt["retrieved_at"] = datetime.now(timezone.utc).isoformat() if body else None
        receipt["completed_at"] = datetime.now(timezone.utc).isoformat()
        connection.execute("INSERT INTO iran_source_captures VALUES (?,?,?,?)",
                           (request_key, url, json.dumps(receipt, sort_keys=True), body))
        connection.commit()
        return receipt
    finally:
        connection.close()
