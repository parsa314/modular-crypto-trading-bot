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
    assert "LIVE=false" in d.text and "PAPER=false" in d.text
    assert "Thesis Research Dashboard" in d.text


def test_v1_research_status_retains_canonical_negative_v50_result():
    r = client.get("/research/status")
    assert r.status_code == 200
    body = r.json()
    assert body["latest_completed_experiment"] == "v0.50"
    assert body["latest_completed_decision"] == "V50_NONOVERLAP_FAILURE_SUPPORTED"
    assert body["v50_provenance"]["scientific_head"] == "1dd0b1fe506fc51ceec4ff8934b77090f86b6cc2"
    assert body["kraken_holdout"] == "SEALED"
    assert body["live_execution"] is False


def test_paper_run_once_is_disabled_without_explicit_environment_flag():
    r = client.post("/paper/run-once")
    assert r.status_code == 423
    assert "scientific gate" in r.json()["detail"]
    status = client.get("/paper/status").json()
    assert status["status"] == "DISABLED_BY_SCIENTIFIC_GATE"
    assert status["paper_execution_enabled"] is False
    assert status["live_execution"] is False
