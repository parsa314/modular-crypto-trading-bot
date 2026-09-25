from __future__ import annotations

from fastapi.testclient import TestClient

from research_bot.service import app, SERVICE_VERSION


client = TestClient(app)


def test_v1_health_and_dashboard_are_research_only():
    h = client.get("/health")
    assert h.status_code == 200
    body = h.json()
    assert body["version"] == SERVICE_VERSION
    assert body["execution_mode"] == "RESEARCH_ONLY"
    assert body["paper_execution"] is False
    assert body["live_execution"] is False
    d = client.get("/dashboard")
    assert d.status_code == 200
    assert "Research/monitoring only" in d.text
    assert "LIVE=false" in d.text
    assert "PAPER=false" in d.text
    assert "Thesis Research Dashboard" in d.text


def test_v1_research_status_retains_canonical_v50_result():
    r = client.get("/research/status")
    assert r.status_code == 200
    body = r.json()
    assert body["latest_completed_experiment"] == "v0.50"
    assert body["latest_completed_decision"] == "V50_NONOVERLAP_FAILURE_SUPPORTED"
    assert body["paper_execution"] is False
    assert body["live_execution"] is False
    assert body["reserved_holdout"] == "kraken"


def test_paper_run_once_is_locked_by_scientific_gate():
    r = client.post("/paper/run-once")
    assert r.status_code == 423
    assert "PAPER execution is disabled" in r.json()["detail"]
