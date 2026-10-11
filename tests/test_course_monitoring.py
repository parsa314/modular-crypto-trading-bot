import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from research_bot.course.monitoring import PANELS, course_monitoring_summary
from research_bot.service import app

NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
client = TestClient(app)


def report(**updates):
    item = {
        "schema": "COURSE_RESEARCH_REPORT_V1", "generated_at": NOW.isoformat(),
        "execution_flags": {"research_only": True, "paper_execution": False, "live_execution": False},
        "status": "INTEGRATION_BLOCKED", "scientific_status": "SCIENTIFIC_DECISION_DEFERRED",
        "data_class": "SYNTHETIC_ENGINEERING_FIXTURE",
        "strategies": [{"strategy_id": "sma_crossover", "status": "IMPLEMENTED_TESTED"}],
        "metrics": {"hypotheses": 3, "no_trade": 3},
    }
    item.update(updates)
    return item


def publish(monkeypatch, tmp_path, payload):
    path = tmp_path / "research.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("COURSE_RESEARCH_REPORT", str(path))


def test_unconfigured_report_is_not_run_without_orders(monkeypatch):
    monkeypatch.delenv("COURSE_RESEARCH_REPORT", raising=False)
    monkeypatch.setenv("BITPIN_TOKEN", "unused-fixture")
    response = client.get("/research/course")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "NOT_RUN"
    assert body["execution_authorization"] == "DENIED_RESEARCH_ONLY"
    assert body["execution_flags"] == {"research_only": True, "paper_execution": False, "live_execution": False}
    assert set(body["panels"]) == set(PANELS)
    assert all(p["status"] == "NOT_EVALUATED" for p in body["panels"].values())


def test_valid_report_does_not_infer_missing_evidence(monkeypatch, tmp_path):
    publish(monkeypatch, tmp_path, report())
    body = course_monitoring_summary(now=NOW)
    assert body["status"] == "INTEGRATION_BLOCKED"
    assert body["report_freshness"] == "VALID"
    assert body["metrics"] == {"hypotheses": 3, "no_trade": 3}
    assert body["panels"]["model_calibration"]["status"] == "NOT_EVALUATED"
    assert body["panels"]["data_freshness"]["status"] == "NOT_EVALUATED"
    assert body["panels"]["scientific_validation"]["status"] == "SCIENTIFIC_DECISION_DEFERRED"


def test_public_projection_omits_secrets_accounts_candidates_and_xss(monkeypatch, tmp_path):
    payload = report(api_key="fixture-private-key", password="fixture-password", account_id="private-account",
                     candidates=[{"metadata": "private-position"}], status="<script>alert(1)</script>")
    payload["metrics"].update(token="fixture-token", brier="fixture-password", account_id=123)
    payload["strategies"].extend([{"strategy_id": "private-account", "status": "PASS"},
                                  {"strategy_id": "grid", "status": "fixture-password", "token": "secret"}])
    payload["panels"] = {"order_book_metrics": {"status": "VALID", "metrics": {"spread_bps": 2, "password": "secret"}},
                         "private-positions": {"status": "PASS"}}
    publish(monkeypatch, tmp_path, payload)
    body = course_monitoring_summary(now=NOW)
    encoded = json.dumps(body)
    assert all(x not in encoded for x in ("fixture-private-key", "fixture-password", "private-account",
                                         "private-position", "fixture-token", "<script>", "private-positions"))
    assert body["status"] == "NOT_EVALUATED"
    assert body["panels"]["order_book_metrics"]["metrics"] == {"spread_bps": 2}


@pytest.mark.parametrize("flags", [None, {"research_only": True, "paper_execution": True, "live_execution": False},
                                   {"research_only": 1, "paper_execution": False, "live_execution": False}])
def test_invalid_execution_claims_block_projection(monkeypatch, tmp_path, flags):
    publish(monkeypatch, tmp_path, report(execution_flags=flags))
    body = course_monitoring_summary(now=NOW)
    assert body["status"] == "BLOCKED"
    assert body["strategies"] == [] and body["metrics"] == {}
    assert body["execution_flags"]["paper_execution"] is False


@pytest.mark.parametrize("change", [{"schema": "unknown"}, {"generated_at": "not-a-date"},
                                    {"generated_at": "2026-10-10T12:00:00"},
                                    {"generated_at": (NOW+timedelta(seconds=6)).isoformat()},
                                    {"strategies": "private-string"}, {"metrics": {"brier": float("nan")}}])
def test_invalid_report_fails_closed_without_exception_details(monkeypatch, tmp_path, change):
    publish(monkeypatch, tmp_path, report(**change))
    body = course_monitoring_summary(now=NOW)
    assert body["status"] == "ERROR"
    assert body["metrics"] == {} and body["strategies"] == []
    assert str(tmp_path) not in json.dumps(body)


def test_stale_artifact_is_explicit_but_does_not_claim_market_freshness(monkeypatch, tmp_path):
    publish(monkeypatch, tmp_path, report(generated_at=(NOW-timedelta(days=1)).isoformat()))
    body = course_monitoring_summary(now=NOW)
    assert body["report_freshness"] == "STALE"
    assert body["panels"]["system_health"]["status"] == "STALE"
    assert body["panels"]["data_freshness"]["status"] == "NOT_EVALUATED"


def test_missing_duplicate_and_oversize_reports_error(monkeypatch, tmp_path):
    path = tmp_path / "missing.json"
    monkeypatch.setenv("COURSE_RESEARCH_REPORT", str(path))
    assert course_monitoring_summary(now=NOW)["status"] == "ERROR"
    path.write_text('{"schema":"COURSE_RESEARCH_REPORT_V1","schema":"duplicate"}')
    assert course_monitoring_summary(now=NOW)["status"] == "ERROR"
    monkeypatch.setattr("research_bot.course.monitoring.MAX_REPORT_BYTES", 8)
    assert course_monitoring_summary(now=NOW)["status"] == "ERROR"


def test_dashboard_uses_textcontent_and_route_has_no_write_method(monkeypatch):
    monkeypatch.delenv("COURSE_RESEARCH_REPORT", raising=False)
    html = client.get("/dashboard").text
    assert "/research/course" in html and "textContent" in html
    assert "innerHTML" not in html
    assert "Simulation results remain hypothetical" in html
    assert client.post("/research/course").status_code == 405
    assert client.post("/paper/run-once").status_code == 423
    assert client.post("/decision/evaluate", json={}).status_code == 423
    assert client.get("/health").json()["live_execution"] is False
