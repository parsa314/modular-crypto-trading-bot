"""Strict intake contracts. Uploaded claims never become verified observations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
from hashlib import sha256
import json
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

SCHEMA_VERSION = "1.0.0"
SCALES = {"IRR": Decimal(1), "TOMAN": Decimal(10), "MILLION_IRR": Decimal(1_000_000), "BILLION_IRR": Decimal(1_000_000_000), "SHARES": Decimal(1)}
MONEY_VARIABLES = {"total_assets", "total_liabilities", "total_equity", "net_income", "attributable_net_income", "cfo", "revenue", "receivables", "inventory", "cash_start", "cash_end", "cfi", "cff", "cash_reconciliation_adjustment", "capex", "net_debt"}
SCOPES = {"SEPARATE", "CONSOLIDATED"}
HOSTS = {"codal.ir", "www.codal.ir", "search.codal.ir", "tsetmc.com", "www.tsetmc.com", "cdn.tsetmc.com", "cbi.ir", "www.cbi.ir", "ice.ir", "www.ice.ir"}
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def normalize_symbol(value: str) -> str:
    if not isinstance(value, str): raise ValueError("symbol must be text")
    result = value.strip().translate(_DIGITS).replace("ي", "ی").replace("ك", "ک")
    if not result or len(result) > 40 or re.search(r"[\s<>/\\\x00-\x1f]", result):
        raise ValueError("invalid symbol")
    return result


def number(value: Any) -> Decimal:
    if type(value) not in (str, int): raise ValueError("decimal values must be text or integers")
    text = str(value).strip().translate(_DIGITS).replace("٬", ",").replace("٫", ".").replace("−", "-")
    if text.startswith("(") and text.endswith(")"): text = "-" + text[1:-1]
    if not re.fullmatch(r"[+-]?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?", text):
        raise ValueError("invalid/nonfinite decimal")
    result = Decimal(text.replace(",", ""))
    if not result.is_finite() or abs(result) > Decimal("1e30") or len(result.as_tuple().digits) > 40:
        raise ValueError("decimal outside supported range")
    return result


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None: raise ValueError("timezone required")
    return value.astimezone(timezone.utc)


def timestamp(value: str) -> datetime:
    if not isinstance(value, str): raise ValueError("timestamp must be ISO text")
    return aware(datetime.fromisoformat(value.replace("Z", "+00:00")))


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value: Any) -> str:
    return sha256(canonical(value).encode()).hexdigest()


def email(value: str | None) -> str | None:
    if value is None: return None
    if not isinstance(value, str) or len(value) > 254 or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", value):
        raise ValueError("invalid recipient")
    return value


@dataclass(frozen=True)
class ResearchRequest:
    symbol: str
    as_of: datetime
    idempotency_key: str
    recipient: str | None
    scope: str = "SEPARATE"
    as_of_defaulted: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        object.__setattr__(self, "as_of", aware(self.as_of))
        email(self.recipient)
        if self.scope not in SCOPES: raise ValueError("invalid statement scope")
        if not isinstance(self.idempotency_key, str) or not 1 <= len(self.idempotency_key) <= 120:
            raise ValueError("idempotency key required")

    def identity(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "as_of": "REQUEST_TIMESTAMP" if self.as_of_defaulted else self.as_of.isoformat(), "recipient": self.recipient, "scope": self.scope}


@dataclass(frozen=True)
class Document:
    document_id: str
    source_url: str
    publication_at: datetime
    period_start: date
    period_end: date
    scope: str
    report_type: str
    raw_text: str

    @property
    def raw_sha256(self) -> str:
        return sha256(self.raw_text.encode()).hexdigest()

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> Document:
        url = str(row["source_url"]); parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError("official HTTPS source URL required; no arbitrary URL fetching")
        scope = row["scope"]
        if scope not in SCOPES: raise ValueError("statement scope required")
        start, end = date.fromisoformat(row["period_start"]), date.fromisoformat(row["period_end"])
        if end < start: raise ValueError("invalid reporting period")
        raw = row["raw_text"]
        if not isinstance(raw, str) or not 1 <= len(raw.encode()) <= 1_000_000: raise ValueError("raw evidence text required/bounded")
        document_id = row["document_id"]
        if not isinstance(document_id, str) or not 1 <= len(document_id) <= 120: raise ValueError("document identity required")
        if row["report_type"] not in {"ANNUAL", "INTERIM_YTD"}: raise ValueError("unsupported financial report type")
        publication = timestamp(row["publication_at"])
        if publication.date() < end: raise ValueError("publication before period end")
        return cls(document_id, url, publication, start, end, scope, row["report_type"], raw)


@dataclass(frozen=True)
class FactClaim:
    variable: str
    value: Decimal
    unit: str
    document_id: str
    source_quote: str

    @property
    def canonical_value(self) -> Decimal:
        with localcontext() as context:
            context.prec = 64
            return self.value * SCALES[self.unit]


@dataclass(frozen=True)
class Bundle:
    issuer_id: str
    symbol: str
    name: str
    industry: str
    share_class: str
    documents: tuple[Document, ...]
    facts: tuple[FactClaim, ...]
    evidence_state: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Bundle:
        if payload.get("schema_version") != SCHEMA_VERSION: raise ValueError("unsupported evidence schema")
        if len(canonical(payload).encode()) > 4_000_000: raise ValueError("evidence pack too large")
        issuer = payload["issuer"]
        if issuer["industry"] not in {"INDUSTRIAL", "BANK", "INSURANCE", "HOLDING", "PROPERTY", "SERVICES"}: raise ValueError("industry required")
        if issuer["share_class"] != "ORDINARY": raise ValueError("ordinary equity only; rights/funds require their own protocol")
        for key in ("issuer_id", "name"):
            if not isinstance(issuer[key], str) or not 1 <= len(issuer[key]) <= 200: raise ValueError("issuer identity required")
        rows = payload["documents"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 20: raise ValueError("1..20 evidence documents required")
        documents = tuple(Document.from_dict(row) for row in rows)
        by_id = {d.document_id: d for d in documents}
        if len(by_id) != len(documents): raise ValueError("duplicate document identity")
        fact_rows = payload["facts"]
        if not isinstance(fact_rows, list) or len(fact_rows) > 1000: raise ValueError("bounded fact list required")
        facts: list[FactClaim] = []; seen: set[tuple[str, str]] = set()
        for row in fact_rows:
            variable, unit, doc_id = row["variable"], row["unit"], row["document_id"]
            if variable not in MONEY_VARIABLES | {"weighted_average_shares"}: raise ValueError("unknown fact variable")
            if unit not in SCALES or (variable == "weighted_average_shares") != (unit == "SHARES"): raise ValueError("fact unit mismatch")
            if doc_id not in by_id: raise ValueError("unknown evidence document")
            quote = row["source_quote"]
            if not isinstance(quote, str) or not quote.strip() or quote not in by_id[doc_id].raw_text: raise ValueError("source witness missing")
            value = number(row["value"])
            tokens = re.findall(r"\(?[+−-]?[۰-۹٠-٩0-9][۰-۹٠-٩0-9٬,٫.]*\)?", quote)
            witnesses: list[Decimal] = []
            for token in tokens:
                try:
                    witnesses.append(number(token))
                except ValueError:
                    continue  # Dates/section numbers are not financial values.
            if value not in witnesses: raise ValueError("quoted evidence does not contain supplied value")
            key = (doc_id, variable)
            if key in seen: raise ValueError("duplicate/conflicting fact")
            seen.add(key); facts.append(FactClaim(variable, value, unit, doc_id, quote))
        # An untrusted upload cannot assert official-source verification.
        state = "ENGINEERING_FIXTURE" if payload.get("evidence_state") == "ENGINEERING_FIXTURE" else "UPLOADED_UNVERIFIED"
        return cls(issuer["issuer_id"], normalize_symbol(issuer["symbol"]), issuer["name"], issuer["industry"], issuer["share_class"], documents, tuple(facts), state)
