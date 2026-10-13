"""Independent numerical and disk/API oracles; no real company performance."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from research_bot.iran_fundamental.contracts import Bundle, ResearchRequest
from research_bot.iran_fundamental.calculations import dcf, margin_of_safety, ttm
from research_bot.iran_fundamental.engine import FundamentalEngine
from research_bot.iran_fundamental.storage import ResearchStore
from research_bot.iran_fundamental.contracts import number
from research_bot.iran_fundamental.delivery import deliver


NOW = datetime(2026, 10, 13, tzinfo=timezone.utc)


def sample_bundle() -> dict:
    """Explicit engineering input, never an official disclosure."""
    return {
        "schema_version": "1.0.0", "evidence_state": "ENGINEERING_FIXTURE",
        "issuer": {"issuer_id": "fixture-issuer", "symbol": "آزمون", "name": "شرکت مصنوعی", "industry": "INDUSTRIAL", "share_class": "ORDINARY"},
        "documents": [{"document_id": "annual-1", "source_url": "https://www.codal.ir/Reports/fixture", "publication_at": "2026-03-01T00:00:00+00:00", "period_start": "2025-01-01", "period_end": "2025-12-31", "scope": "SEPARATE", "report_type": "ANNUAL", "unit": "MILLION_IRR", "raw_text": "دارایی=1000\nبدهی=400\nحقوق=600\nسود=200\nنقد=150\nسهام=100000000\n"}],
        "facts": [{"variable": var, "value": val, "document_id": "annual-1", "source_quote": quote, "unit": unit} for var, val, quote, unit in [
            ("total_assets", "1000", "دارایی=1000", "MILLION_IRR"),
            ("total_liabilities", "400", "بدهی=400", "MILLION_IRR"),
            ("total_equity", "600", "حقوق=600", "MILLION_IRR"),
            ("attributable_net_income", "200", "سود=200", "MILLION_IRR"),
            ("cfo", "150", "نقد=150", "MILLION_IRR"),
            ("weighted_average_shares", "100000000", "سهام=100000000", "SHARES"),
        ]],
    }


class FundamentalTests(unittest.TestCase):
    def test_independent_numerical_oracles(self):
        self.assertEqual(ttm(Decimal(100), Decimal(70), Decimal(50)), Decimal(120))
        self.assertEqual(margin_of_safety(Decimal(100), Decimal(80)), (Decimal("0.2"), Decimal("0.25")))
        self.assertEqual(dcf([Decimal(110)], Decimal("0.10"), Decimal("0"), Decimal(20), Decimal(10), Decimal(10))["equity_per_share"], Decimal(107))

    def test_nonfinite_units_and_witness_fail_closed(self):
        for value in ("NaN", "Infinity", "1e9999", "True"):
            b = sample_bundle(); b["facts"][0]["value"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                Bundle.from_dict(b)
        b = sample_bundle(); b["facts"][0]["unit"] = "MYSTERY"
        with self.assertRaises(ValueError): Bundle.from_dict(b)
        b = sample_bundle(); b["facts"][0]["source_quote"] = "not in report"
        with self.assertRaises(ValueError): Bundle.from_dict(b)

    def test_model_consistency(self):
        with self.assertRaises(ValueError):
            dcf([Decimal(100)], Decimal("0.1"), Decimal("0.1"), Decimal(0), Decimal(0), Decimal(1))
        with self.assertRaises(ValueError): margin_of_safety(Decimal(0), Decimal(80))

    def test_storage_replay_and_engine_report(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / "research.sqlite")
            request = ResearchRequest("آزمون", NOW, "fixture-key", "owner@example.com")
            with ResearchStore(db) as store:
                engine = FundamentalEngine(store)
                first = engine.analyze(request, sample_bundle())
                second = engine.analyze(request, sample_bundle())
                self.assertEqual(first, second)
                self.assertEqual(first["decision"], "INSUFFICIENT_INFORMATION")
                self.assertEqual(first["delivery_state"], "QUEUED")
                self.assertEqual(first["calculations"]["eps_irr"], "2")
                self.assertEqual(len(first["chapters"]), 7)
                self.assertEqual(len(first["appendices"]), 6)
                self.assertIn('dir="rtl"', engine.render_html(first["job_id"]))
            with ResearchStore(db) as reopened:
                self.assertEqual(reopened.get_report(first["job_id"]), first)
                with self.assertRaises(ValueError):
                    FundamentalEngine(reopened).analyze(ResearchRequest("دیگر", NOW, "fixture-key", "owner@example.com"), sample_bundle())

    def test_late_revision_cannot_rewrite_historical_snapshot(self):
        original = sample_bundle()
        revised = sample_bundle()
        revised["documents"][0]["publication_at"] = "2026-10-14T00:00:00+00:00"
        with tempfile.TemporaryDirectory() as d, ResearchStore(str(Path(d)/"db.sqlite")) as store:
            r = FundamentalEngine(store).analyze(ResearchRequest("آزمون", NOW, "late", None), revised)
            self.assertIn("NO_ADMISSIBLE_DOCUMENTS", r["quality_reasons"])
            self.assertEqual(r["calculations"], {})
            self.assertEqual(r["delivery_state"], "NOT_REQUESTED")

    def test_balance_and_identity_gates(self):
        for mutate, reason in [
            (lambda b: b["facts"].pop(2), "BALANCE_INPUTS_UNAVAILABLE"),
            (lambda b: b["issuer"].update(symbol="دیگر"), "ISSUER_MISMATCH"),
        ]:
            b=sample_bundle(); mutate(b)
            with tempfile.TemporaryDirectory() as d, ResearchStore(str(Path(d)/"db.sqlite")) as store:
                r=FundamentalEngine(store).analyze(ResearchRequest("آزمون",NOW,reason,None),b)
                self.assertIn(reason,r["quality_reasons"])
                self.assertEqual(r["valuation"],None)

    def test_extraction_timestamp_is_not_backdated(self):
        extraction = datetime(2026,10,15,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as d, ResearchStore(str(Path(d)/"db.sqlite")) as store:
            r=FundamentalEngine(store,clock=lambda: extraction).analyze(ResearchRequest("آزمون",NOW,"clock",None),sample_bundle())
            self.assertEqual(r["facts"][0]["Extraction_Date"],extraction.isoformat())
            self.assertEqual(r["as_of"],NOW.isoformat())

    def test_scopes_and_duplicate_conflicts(self):
        b=sample_bundle(); b["documents"][0]["scope"]="CONSOLIDATED"
        with tempfile.TemporaryDirectory() as d, ResearchStore(str(Path(d)/"db.sqlite")) as store:
            r=FundamentalEngine(store).analyze(ResearchRequest("آزمون",NOW,"scope",None),b)
            self.assertEqual(r["facts"],[])
        b=sample_bundle(); b["facts"].append(b["facts"][0].copy())
        with self.assertRaises(ValueError): Bundle.from_dict(b)
        b=sample_bundle(); b["documents"][0]["source_url"]="https://localhost/private"
        with self.assertRaises(ValueError): Bundle.from_dict(b)

    def test_negative_persian_values_and_balance_breach(self):
        self.assertEqual(number("(۱٬۲۰۰)"),Decimal(-1200))
        b=sample_bundle(); b["facts"][2]["value"]="603"; b["facts"][2]["source_quote"]="حقوق=603"
        b["documents"][0]["raw_text"]=b["documents"][0]["raw_text"].replace("حقوق=600","حقوق=603")
        with tempfile.TemporaryDirectory() as d, ResearchStore(str(Path(d)/"db.sqlite")) as store:
            r=FundamentalEngine(store).analyze(ResearchRequest("آزمون",NOW,"breach",None),b)
            self.assertIn("BALANCE_MISMATCH",r["quality_reasons"])

    def test_fixture_and_uncertain_delivery_do_not_send_again(self):
        class TimeoutTransport:
            calls=0
            def send(self,*args):
                self.calls+=1
                raise TimeoutError("ambiguous boundary")
        with tempfile.TemporaryDirectory() as d, ResearchStore(str(Path(d)/"db.sqlite")) as store:
            r=FundamentalEngine(store).analyze(ResearchRequest("آزمون",NOW,"fixture", "owner@example.com"),sample_bundle())
            sender=TimeoutTransport()
            with self.assertRaises(ValueError): deliver(store,r["job_id"],sender)
            self.assertEqual(sender.calls,0)
            b=sample_bundle(); b["evidence_state"]="UPLOADED_UNVERIFIED"
            r=FundamentalEngine(store).analyze(ResearchRequest("آزمون",NOW,"upload", "owner@example.com"),b)
            self.assertEqual(deliver(store,r["job_id"],sender)["state"],"DELIVERY_UNCERTAIN")
            with self.assertRaises(ValueError): deliver(store,r["job_id"],sender)
            self.assertEqual(sender.calls,1)

    def test_concurrent_idempotency_is_transactional(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as d:
            db=str(Path(d)/"research.sqlite")
            with ResearchStore(db): pass
            def run(_):
                with ResearchStore(db) as store:
                    return FundamentalEngine(store).analyze(ResearchRequest("آزمون",NOW,"same-request", "owner@example.com"),sample_bundle())
            with ThreadPoolExecutor(max_workers=4) as workers:
                reports=list(workers.map(run,range(8)))
            self.assertTrue(all(r==reports[0] for r in reports))
            with ResearchStore(db) as store:
                self.assertEqual(store.connection.execute("SELECT count(*) FROM iran_email_outbox").fetchone()[0],1)

    def test_consolidated_cash_conversion_does_not_ignore_minority(self):
        b=sample_bundle(); b["documents"][0]["scope"]="CONSOLIDATED"
        with tempfile.TemporaryDirectory() as d, ResearchStore(str(Path(d)/"db.sqlite")) as store:
            r=FundamentalEngine(store).analyze(ResearchRequest("آزمون",NOW,"minority",None,"CONSOLIDATED"),b)
            self.assertEqual(r["calculations"]["eps_irr"],"2")
            self.assertNotIn("cash_conversion_ratio",r["calculations"])

    def test_distinct_explicit_requests_have_distinct_jobs(self):
        with tempfile.TemporaryDirectory() as d, ResearchStore(str(Path(d)/"db.sqlite")) as store:
            engine=FundamentalEngine(store)
            first=engine.analyze(ResearchRequest("آزمون",NOW,"request-one",None),sample_bundle())
            second=engine.analyze(ResearchRequest("آزمون",NOW,"request-two",None),sample_bundle())
            self.assertNotEqual(first["job_id"],second["job_id"])

    def test_cli_replay_preserves_exact_artifact_bytes(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            bundle=root/"evidence.json"
            bundle.write_text(json.dumps(sample_bundle(),ensure_ascii=False))
            command=[sys.executable,"-m","research_bot.iran_fundamental","--symbol","آزمون","--bundle",str(bundle),"--database",str(root/"db.sqlite"),"--output",str(root/"reports"),"--idempotency-key","cli-oracle"]
            first=subprocess.run(command,capture_output=True,text=True,check=True,timeout=15)
            location=Path(json.loads(first.stdout)["artifacts"])
            original={p.name:p.read_bytes() for p in location.iterdir()}
            second=subprocess.run(command,capture_output=True,text=True,check=True,timeout=15)
            self.assertEqual(first.stdout,second.stdout)
            self.assertEqual(original,{p.name:p.read_bytes() for p in location.iterdir()})

    def test_html_and_csv_untrusted_text_is_inert(self):
        from research_bot.iran_fundamental.__main__ import csv_cell
        self.assertEqual(csv_cell("=HYPERLINK(\"x\")"),"'=HYPERLINK(\"x\")")
        self.assertEqual(csv_cell("  +malicious"),"'  +malicious")
        self.assertEqual(csv_cell("200"),"200")
        b=sample_bundle(); b["documents"][0]["raw_text"]+="<script>alert(1)</script> 1000"
        b["facts"][0]["source_quote"]="<script>alert(1)</script> 1000"
        with tempfile.TemporaryDirectory() as d, ResearchStore(str(Path(d)/"db.sqlite")) as store:
            engine=FundamentalEngine(store)
            r=engine.analyze(ResearchRequest("آزمون",NOW,"html",None),b)
            html=engine.render_html(r["job_id"])
            self.assertNotIn("<script>",html)
            self.assertIn("&lt;script&gt;",html)

    def test_cash_bridge_and_future_asof(self):
        b=sample_bundle()
        for variable,value in [("cash_start","10"),("cash_end","100"),("cfi","-40"),("cff","-20"),("cash_reconciliation_adjustment","0")]:
            quote=f"{variable}={value}"
            b["documents"][0]["raw_text"]+=quote+"\n"
            b["facts"].append({"variable":variable,"value":value,"unit":"MILLION_IRR","source_quote":quote,"document_id":"annual-1"})
        with tempfile.TemporaryDirectory() as d, ResearchStore(str(Path(d)/"db.sqlite")) as store:
            engine=FundamentalEngine(store,clock=lambda:NOW)
            report=engine.analyze(ResearchRequest("آزمون",NOW,"cash",None),b)
            self.assertEqual(report["calculations"]["cash_bridge_error_irr"],"0")
            self.assertNotIn("CASH_BRIDGE_MISMATCH",report["quality_reasons"])
            with self.assertRaisesRegex(ValueError,"AS_OF_IN_FUTURE"):
                engine.analyze(ResearchRequest("آزمون",datetime(2027,1,1,tzinfo=timezone.utc),"future",None),b)

    def test_invalid_timestamp_and_exact_scaled_decimal(self):
        b=sample_bundle(); b["documents"][0]["publication_at"]=123
        with self.assertRaises(ValueError): Bundle.from_dict(b)
        from research_bot.iran_fundamental.contracts import FactClaim
        precise=Decimal("12345678901234567890.1234567890123456789")
        self.assertEqual(str(FactClaim("revenue",precise,"BILLION_IRR","d","q").canonical_value),"12345678901234567890123456789.0123456789000000000")


class ASGITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import asyncio
        # Keep restricted runtimes' selector responsive to threadpool completion.
        # All HTTP calls use in-process ASGI, never external sockets.
        async def tick():
            while True:
                await asyncio.sleep(0.01)
        self.tick = asyncio.create_task(tick())

    async def asyncTearDown(self):
        import asyncio
        self.tick.cancel()
        try:
            await self.tick
        except asyncio.CancelledError:
            pass

    def client(self, app):
        import httpx
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://research.test")


class FundamentalAPITests(ASGITests):
    async def test_api_auth_preview_idempotency_and_recipient_binding(self):
        from fastapi import FastAPI
        from research_bot.iran_fundamental.api import APISettings, build_router
        with tempfile.TemporaryDirectory() as d:
            token="test-only-not-a-secret-credential"+"0"*32
            app=FastAPI(); app.include_router(build_router(APISettings(str(Path(d)/"db.sqlite"),token,"owner@example.com")))
            async with self.client(app) as client:
                headers={"Authorization":"Bearer "+token}
                body={"symbol":"آزمون","as_of":NOW.isoformat(),"idempotency_key":"api","send_email":False,"evidence_bundle":sample_bundle()}
                self.assertEqual((await client.post("/research/iran-equities/analyses",json=body)).status_code,401)
                first=await client.post("/research/iran-equities/analyses",json=body,headers=headers)
                self.assertEqual(first.status_code,200,first.text)
                data=first.json(); job=data["report"]["job_id"]
                self.assertEqual(data["delivery"]["state"],"NOT_REQUESTED")
                self.assertEqual((await client.post("/research/iran-equities/analyses",json=body,headers=headers)).json(),data)
                self.assertEqual((await client.get(f"/research/iran-equities/analyses/{job}")).status_code,401)
                self.assertEqual((await client.get(f"/research/iran-equities/analyses/{job}/html",headers=headers)).status_code,200)
                self.assertEqual((await client.post(f"/research/iran-equities/analyses/{job}/deliver",headers=headers)).status_code,409)
                body["recipient"]="stranger@example.com"
                self.assertEqual((await client.post("/research/iran-equities/analyses",json=body,headers=headers)).status_code,422)

    async def test_on_demand_delivery_receipt_and_no_duplicate(self):
        from fastapi import FastAPI
        from research_bot.iran_fundamental.api import APISettings, build_router
        class ReceiptTransport:
            calls=0
            def send(self,recipient,subject,html,report):
                self.calls+=1
                assert recipient=="owner@example.com"
                assert "اطلاعات ناکافی" in subject
                return "provider-fixture-id"
        with tempfile.TemporaryDirectory() as d:
            token="test-only-not-a-secret-credential"+"0"*32; sender=ReceiptTransport()
            app=FastAPI(); app.include_router(build_router(APISettings(str(Path(d)/"db.sqlite"),token,"owner@example.com"),sender))
            b=sample_bundle(); b["evidence_state"]="UPLOADED_UNVERIFIED"
            body={"symbol":"آزمون","idempotency_key":"latest-api","evidence_bundle":b}
            async with self.client(app) as client:
                headers={"Authorization":"Bearer "+token}
                first=await client.post("/research/iran-equities/analyses",json=body,headers=headers)
                self.assertEqual(first.status_code,200,first.text)
                self.assertEqual(first.json()["delivery"]["state"],"SENT")
                replay=await client.post("/research/iran-equities/analyses",json=body,headers=headers)
                self.assertEqual(replay.json(),first.json())
                self.assertEqual(sender.calls,1)


class ExistingServiceCompatibilityTests(ASGITests):
    async def test_default_crypto_surface_unchanged(self):
        from research_bot.service import app
        async with self.client(app) as client:
            self.assertEqual((await client.get("/health")).json()["latest_completed_experiment"],"v0.50")
            self.assertEqual((await client.get("/research/status")).json()["latest_completed_decision"],"V50_NONOVERLAP_FAILURE_SUPPORTED")
            self.assertEqual((await client.get("/research/status")).json()["kraken_holdout"],"SEALED")
            self.assertEqual((await client.post("/decision/evaluate",json={})).status_code,423)
            self.assertEqual((await client.post("/research/iran-equities/analyses",json={})).status_code,404)


if __name__ == "__main__": unittest.main()
