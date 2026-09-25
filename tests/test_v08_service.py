from __future__ import annotations

from fastapi.testclient import TestClient

from research_bot.service import app


client = TestClient(app)


def test_health_is_research_only_and_fail_closed():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["execution_mode"] == "RESEARCH_ONLY"
    assert body["paper_execution"] is False
    assert body["live_execution"] is False


def test_status_exposes_evidence_contract_not_profit_claim():
    response = client.get("/research/status")
    assert response.status_code == 200
    body = response.json()
    assert body["principle"] == "Evidence Before Opinion"
    assert body["paper_execution"] is False
    assert body["live_execution"] is False
    assert body["latest_completed_decision"] == "V50_NONOVERLAP_FAILURE_SUPPORTED"
    assert body["kraken_holdout"] == "SEALED"


def test_decision_endpoint_is_locked_under_research_only_governance():
    payload = {
        "asset": "BTC",
        "symbol": "BTC/USDT",
        "expected_return": 0.012,
        "expected_cost": 0.0012,
        "confidence": 0.80,
    }
    response = client.post("/decision/evaluate", json=payload)
    assert response.status_code == 423
    assert "locked by thesis governance" in response.json()["detail"]
