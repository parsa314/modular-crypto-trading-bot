from __future__ import annotations

from fastapi.testclient import TestClient

from research_bot.service import app


client = TestClient(app)


def test_health_is_research_only():
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
    assert body["live_execution"] is False
    assert body["paper_execution"] is False
    assert body["kraken_holdout"] == "SEALED"


def test_high_confidence_decision_cannot_bypass_research_firewall():
    payload = {
        "asset": "BTC",
        "symbol": "BTC/USDT",
        "expected_return": 0.012,
        "expected_cost": 0.0012,
        "risk_penalty": 0.001,
        "uncertainty_penalty": 0.001,
        "confidence": 0.80,
        "currently_long": False,
        "equity": 10000,
        "peak_equity": 10000,
        "gross_exposure": 0.0,
        "asset_weight": 0.0,
        "turnover": 0.0,
        "spread_bps": 4,
        "slippage_bps": 2,
        "reference_price": 100000,
        "quantity": 0.02,
        "client_order_id": "service-test-btc-001",
        "recent_returns": [0.001] * 30,
    }
    response = client.post("/decision/evaluate", json=payload)
    assert response.status_code == 423
    assert "locked" in response.json()["detail"]
    assert client.get("/paper/fills").json()["items"] == []
    assert client.get("/paper/observations").json()["items"] == []
    assert client.get("/research/latest").json()["candidate_promotion_allowed"] is False
