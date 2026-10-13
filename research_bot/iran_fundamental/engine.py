"""Uploaded disclosure claims -> PIT snapshot -> QC -> Persian dossier.

The first vertical slice deliberately does not certify source authenticity or
invent forecasts. Uploaded values are provisional assumptions. Source-backed
automated Codal extraction and economic recommendations require a later adapter.
"""
from __future__ import annotations

from decimal import Decimal, localcontext
from datetime import datetime, timezone
from html import escape
from typing import Any, Callable

from .calculations import ratio
from .contracts import Bundle, ResearchRequest, SCALES, aware, fingerprint
from .storage import ResearchStore

CHAPTERS = (
    "۱. خلاصه اجرایی", "۲. اقتصاد کلان و صنعت", "۳. صورت‌های مالی و کیفیت سود",
    "۴. نسبت‌ها و مقایسه رقبا", "۵. ارزش‌گذاری", "۶. ریسک‌ها و کاتالیزورها", "۷. تصمیم کمیته سرمایه‌گذاری",
)
METHODS = {"INDUSTRIAL": "FCFF_DCF", "SERVICES": "FCFF_DCF", "BANK": "RESIDUAL_INCOME", "INSURANCE": "RESIDUAL_INCOME", "HOLDING": "NAV_SOTP", "PROPERTY": "PROJECT_NAV"}


class FundamentalEngine:
    def __init__(self, store: ResearchStore, clock: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def analyze(self, request: ResearchRequest, payload: dict[str, Any]) -> dict:
        with localcontext() as context:
            context.prec = 64
            return self._analyze(request, payload)

    def _analyze(self, request: ResearchRequest, payload: dict[str, Any]) -> dict:
        bundle = Bundle.from_dict(payload)
        request_hash = fingerprint({"idempotency_key": request.idempotency_key, "request": request.identity(), "bundle": payload})
        previous = self.store.replay(request.idempotency_key, request_hash)
        if previous is not None: return previous
        processed_at = aware(self.clock())
        if request.as_of > processed_at: raise ValueError("AS_OF_IN_FUTURE")
        reasons = ["SOURCE_VERIFICATION_PENDING", "AUDIT_NOTES_REVIEW_UNAVAILABLE", "FORECAST_ASSUMPTIONS_UNAVAILABLE", "MACRO_AND_PEERS_UNAVAILABLE"]
        identity_ok = bundle.symbol == request.symbol
        if not identity_ok: reasons.append("ISSUER_MISMATCH")
        if bundle.evidence_state == "ENGINEERING_FIXTURE": reasons.append("FIXTURE_NOT_MARKET_EVIDENCE")
        eligible = [d for d in bundle.documents if d.publication_at <= request.as_of and d.scope == request.scope]
        if not eligible: reasons.append("NO_ADMISSIBLE_DOCUMENTS")
        if eligible:
            newest = max((d.period_end, d.publication_at) for d in eligible)
            selected = [d for d in eligible if (d.period_end, d.publication_at) == newest]
            if len(selected) != 1:
                reasons.append("CONFLICTING_LATEST_DOCUMENTS"); selected = []
        else: selected = []
        doc = selected[0] if selected and identity_ok else None
        facts = [f for f in bundle.facts if doc and f.document_id == doc.document_id]
        values = {f.variable: f.canonical_value for f in facts}
        calculations: dict[str, str | None] = {}
        balance_keys = {"total_assets", "total_liabilities", "total_equity"}
        if balance_keys <= values.keys():
            balance_error = values["total_assets"] - values["total_liabilities"] - values["total_equity"]
            calculations["balance_error_irr"] = str(balance_error)
            # One reported unit per summand is the explicit rounding allowance.
            monetary_units = [f.unit for f in facts if f.variable in balance_keys]
            tolerance = sum((SCALES[u] / 2 for u in monetary_units), Decimal(0))
            if abs(balance_error) > tolerance: reasons.append("BALANCE_MISMATCH")
        else: reasons.append("BALANCE_INPUTS_UNAVAILABLE")
        cash_keys = {"cash_start", "cash_end", "cfo", "cfi", "cff", "cash_reconciliation_adjustment"}
        if cash_keys <= values.keys():
            error = values["cash_end"] - values["cash_start"] - sum((values[k] for k in ("cfo", "cfi", "cff", "cash_reconciliation_adjustment")), Decimal(0))
            calculations["cash_bridge_error_irr"] = str(error)
            tolerance = sum((SCALES[f.unit] / 2 for f in facts if f.variable in cash_keys), Decimal(0))
            if abs(error) > tolerance: reasons.append("CASH_BRIDGE_MISMATCH")
        else: reasons.append("CASH_BRIDGE_INPUTS_UNAVAILABLE")
        if {"attributable_net_income", "weighted_average_shares"} <= values.keys():
            eps = ratio(values["attributable_net_income"], values["weighted_average_shares"])
            calculations["eps_irr"] = None if eps is None else str(eps)
            if eps is None: reasons.append("INVALID_EPS_DENOMINATOR")
        income_key = "net_income" if request.scope == "CONSOLIDATED" else "attributable_net_income"
        if bundle.industry in {"INDUSTRIAL", "SERVICES"} and {"cfo", income_key} <= values.keys():
            conversion = ratio(values["cfo"], values[income_key])
            calculations["cash_conversion_ratio"] = None if conversion is None else str(conversion)
            if values[income_key] > 0 and values["cfo"] < 0: reasons.append("POSITIVE_INCOME_NEGATIVE_CFO")
        snapshot = {"issuer_id": bundle.issuer_id, "scope": request.scope, "document_id": doc.document_id if doc else None, "source_hash": doc.raw_sha256 if doc else None, "values": {k: str(v) for k,v in sorted(values.items())}}
        fact_rows = [{"Variable": f.variable, "Value": str(f.canonical_value), "Unit": "SHARES" if f.unit == "SHARES" else "IRR", "Original_Value": str(f.value), "Original_Unit": f.unit, "Period": f"{doc.period_start}/{doc.period_end}", "Report_Type": doc.report_type, "Source_URL": doc.source_url, "Publication_Date": doc.publication_at.isoformat(), "Extraction_Date": processed_at.isoformat(), "Status": "ESTIMATED", "verification": "UPLOADED_CLAIM_TREATED_AS_PROVISIONAL_ASSUMPTION", "source_quote": f.source_quote} for f in facts]
        summaries = (
            "اطلاعات ناکافی برای توصیه سرمایه‌گذاری؛ داده بارگذاری‌شده هنوز با منبع رسمی تطبیق نشده است.",
            f"صنعت اعلامی: {bundle.industry}. داده معتبر اقتصاد کلان، نرخ ارز و رقبا در این درخواست موجود نیست.",
            "محاسبات زیر فقط از ادعاهای بارگذاری‌شده‌اند؛ سود نرمال‌شده و بندهای حسابرس هنوز قابل تأیید نیستند.",
            "نسبت‌های قابل محاسبه با واحد و دوره ثبت می‌شوند؛ مقایسه رقبا و نسبت‌های فاقد معنا نتیجه قطعی نمی‌دهند.",
            f"روش مناسب پیشنهادی: {METHODS[bundle.industry]}. فقدان ورودی معتبر و پیش‌بینی، مانع اعلام ارزش منصفانه است.",
            "راستی‌آزمایی منبع، کیفیت سود، نقدینگی، بندهای حسابرس و مفروضات ارزش‌گذاری باید تکمیل شوند؛ catalyst جعل نمی‌شود.",
            "Bull: ورودی معتبر برای ادعای ارزندگی وجود ندارد. Bear: کیفیت منبع و مدل نامشخص است. Risk: اطلاعات ناکافی؛ بدون تخصیص شخصی.",
        )
        report: dict[str, Any] = {
            "schema_version": "1.0.0", "engine_version": "0.1.0", "job_id": request_hash,
            "symbol": request.symbol, "company_name": bundle.name if identity_ok else None,
            "as_of": request.as_of.isoformat(), "processed_at": processed_at.isoformat(), "evidence_state": bundle.evidence_state,
            "status": "REPORT_READY_WITH_LIMITATIONS", "decision": "INSUFFICIENT_INFORMATION",
            "fundamental_status": "UNKNOWN", "valuation": None, "normalized_net_income": None,
            "normalized_eps": None, "recommended_allocation": None,
            "snapshot_hash": fingerprint(snapshot), "quality_reasons": reasons,
            "facts": fact_rows, "calculations": calculations,
            "calculation_status": "ESTIMATED_FROM_UNVERIFIED_INPUTS",
            "chapters": [{"title": title, "text": text} for title,text in zip(CHAPTERS,summaries)],
            "appendices": {"A": [{"document_id": d.document_id,"source_url": d.source_url,"publication_at": d.publication_at.isoformat(),"period_start": str(d.period_start),"period_end": str(d.period_end),"scope": d.scope,"raw_sha256": d.raw_sha256,"verification": "UNVERIFIED_UPLOAD"} for d in eligible], "B": {"calculations": calculations,"unit": "IRR unless ratio/shares", "formulas": {"eps_irr": "attributable income IRR / weighted-average shares", "cash_conversion_ratio": f"CFO / {income_key}; positive denominator only"}}, "C": {"input_reliability": "provisional uploaded assumptions", "forecast": "UNAVAILABLE"}, "D": reasons,"E": ["هیچ شاهد کافی برای ادعای ارزندگی یا سود پایدار ارائه نشده است."],"F": ["تطبیق گزارش رسمی و آخرین اصلاحیه تا تاریخ مبنا", "بررسی دوره متناظر جریان نقد و بندهای حسابرس", "تکمیل مفروضات پیش‌بینی و مدل صنعت"]},
            "delivery_state": "QUEUED" if request.recipient else "NOT_REQUESTED",
            "live_order_routing": False,
        }
        report["report_hash"] = fingerprint(report)
        return self.store.save(request.idempotency_key, request_hash, report, payload, request.recipient)

    def render_html(self, job_id: str) -> str:
        report = self.store.get_report(job_id)
        parts = ['<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8"><title>پرونده پژوهش بنیادی</title><body>', f'<h1>تحلیل بنیادی {escape(report["symbol"])}</h1>', f'<p>مبنا: {escape(report["as_of"])}</p><p>اطلاعات ناکافی — بدون توصیه خرید</p>']
        for chapter in report["chapters"]:
            parts.extend((f'<h2>{escape(chapter["title"])}</h2>', f'<p>{escape(chapter["text"])}</p>'))
        parts.append('<h3>محاسبات مشروط به ادعاهای تأییدنشده</h3><table>')
        for name,value in report["calculations"].items(): parts.append(f'<tr><td>{escape(name)}</td><td>{escape(value if value is not None else "N/M")}</td></tr>')
        parts.append('</table><h3>قرارداد ارقام و منابع</h3><pre>')
        parts.append(escape(json_text(report["facts"])))
        parts.append('</pre>')
        for key,value in report["appendices"].items(): parts.append(f'<h2>پیوست {key}</h2><pre>{escape(json_text(value))}</pre>')
        parts.append(f'<footer>snapshot: {escape(report["snapshot_hash"])}</footer></body></html>')
        return ''.join(parts)


def json_text(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
