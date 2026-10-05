from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from .live_readiness import LiveReadinessChecklist, evaluate_live_readiness

SERVICE_VERSION = "1.0.0-rc4"
RESEARCH_MODE = "RESEARCH_ONLY"
LATEST_COMPLETED_EXPERIMENT = "v0.50"
LATEST_COMPLETED_DECISION = "V50_NONOVERLAP_FAILURE_SUPPORTED"
NEXT_RESEARCH_QUESTION = "v0.51 prospective overlap-conflict arbitration"
KRAKEN_STATE = "SEALED"
PAPER_EXECUTION = False
LIVE_EXECUTION = False
CANONICAL_V50_RUN = 34707823108
CANONICAL_V50_HEAD = "1dd0b1fe506fc51ceec4ff8934b77090f86b6cc2"
CANONICAL_V50_ARTIFACT = 10302830689
CANONICAL_V50_DIGEST = "sha256:da5813a8c031f9da6cc940fda942efc846ca536dbd952f4930584c30732373e9"


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


# Deployment firewall. Production must remain a research/monitoring service.
if (
    _truthy_env("LIVE_EXECUTION")
    or _truthy_env("PAPER_EXECUTION")
    or _truthy_env("BOT_FORWARD_PAPER_ENABLED")
    or _truthy_env("BOT_PAPER_EXECUTION_ENABLED")
):
    raise RuntimeError(
        "Execution firewall violation: LIVE/PAPER/forward-paper flags must remain false "
        "for the canonical v0.50 research deployment."
    )

app = FastAPI(
    title="Modular Crypto Trading Bot — Thesis Research API",
    version=SERVICE_VERSION,
    description=(
        "Fail-closed academic crypto research and monitoring service. "
        "No order-submission or paper-execution endpoint is enabled."
    ),
)


def _git_commit() -> str:
    return os.getenv("RAILWAY_GIT_COMMIT_SHA", os.getenv("GITHUB_SHA", "UNKNOWN"))


def _environment() -> str:
    return os.getenv("RAILWAY_ENVIRONMENT_NAME", "UNKNOWN")


def _status_payload() -> dict:
    return {
        "principle": "Evidence Before Opinion",
        "version": SERVICE_VERSION,
        "mode": RESEARCH_MODE,
        "latest_completed_experiment": LATEST_COMPLETED_EXPERIMENT,
        "latest_completed_decision": LATEST_COMPLETED_DECISION,
        "next_research_question": NEXT_RESEARCH_QUESTION,
        "development_venues": ["coinex", "okx", "kucoin"],
        "reserved_holdout": "kraken",
        "kraken_holdout": KRAKEN_STATE,
        "paper_execution": PAPER_EXECUTION,
        "live_execution": LIVE_EXECUTION,
        "v50_provenance": {
            "workflow_run": CANONICAL_V50_RUN,
            "scientific_head": CANONICAL_V50_HEAD,
            "artifact_id": CANONICAL_V50_ARTIFACT,
            "artifact_digest": CANONICAL_V50_DIGEST,
        },
        "git_commit": _git_commit(),
        "environment": _environment(),
        "release_channel": os.getenv("BOT_RELEASE_CHANNEL", "UNKNOWN"),
        "warning": "Service availability is not profitability evidence or execution authorization.",
    }


@app.get("/")
def root() -> dict:
    return {
        "service": "modular-crypto-research-bot",
        "version": SERVICE_VERSION,
        "mode": RESEARCH_MODE,
        "latest_completed_experiment": LATEST_COMPLETED_EXPERIMENT,
        "latest_completed_decision": LATEST_COMPLETED_DECISION,
        "dashboard": "/dashboard",
        "docs": "/docs",
        "health": "/health",
        "paper_execution": False,
        "live_execution": False,
        "kraken_holdout": KRAKEN_STATE,
    }


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "modular-crypto-research-bot",
        "version": SERVICE_VERSION,
        "execution_mode": RESEARCH_MODE,
        "latest_completed_experiment": LATEST_COMPLETED_EXPERIMENT,
        "paper_execution": False,
        "live_execution": False,
        "kraken_holdout": KRAKEN_STATE,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/research/status")
def research_status() -> dict:
    return _status_payload()


@app.get("/research/latest")
def research_latest() -> dict:
    return {
        "experiment": LATEST_COMPLETED_EXPERIMENT,
        "decision": LATEST_COMPLETED_DECISION,
        "finding": (
            "The frozen earliest-first non-overlap stage satisfied the preregistered broad-harm "
            "attribution rule in v0.50. No alternative overlap arbitration rule is validated yet."
        ),
        "next_question": NEXT_RESEARCH_QUESTION,
        "candidate_promotion_allowed": False,
        "kraken_touched": False,
        "paper_execution": False,
        "live_execution": False,
    }


@app.get("/execution/readiness")
def execution_readiness() -> dict:
    decision = evaluate_live_readiness(LiveReadinessChecklist())
    return {
        "stage": decision.stage.value,
        "testnet_review_eligible": decision.testnet_review_eligible,
        "live_review_eligible": decision.live_review_eligible,
        "live_execution_authorized": decision.live_execution_authorized,
        "missing_gates": list(decision.missing_gates),
        "real_money_order_submission": False,
        "required_next_step": NEXT_RESEARCH_QUESTION,
        "warning": "Readiness is not authorization; private/live order submission remains disabled.",
    }


@app.post("/decision/evaluate")
def evaluate_forecast(payload: dict) -> dict:
    del payload
    raise HTTPException(
        status_code=423,
        detail="Decision/execution endpoint is locked by thesis governance; research-only deployment.",
    )


@app.get("/paper/status")
def paper_status() -> dict:
    return {
        "status": "DISABLED_BY_SCIENTIFIC_GATE",
        "paper_execution_enabled": False,
        "live_execution": False,
        "kraken_holdout": KRAKEN_STATE,
        "required_next_step": NEXT_RESEARCH_QUESTION,
    }


@app.post("/paper/run-once")
def paper_run_once() -> dict:
    raise HTTPException(status_code=423, detail="PAPER execution is disabled by the current scientific gate.")


@app.get("/paper/observations")
def paper_observations() -> dict:
    return {"items": [], "status": "DISABLED_BY_SCIENTIFIC_GATE", "live_execution": False}


@app.get("/paper/fills")
def paper_fills() -> dict:
    return {"items": [], "status": "DISABLED_BY_SCIENTIFIC_GATE", "live_execution": False}


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> str:
    return r'''<!doctype html><html lang="en"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/><title>Thesis Crypto Research Bot</title><style>body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;margin:0;background:#f5f7fa;color:#17202a}header{background:#111827;color:white;padding:22px 5vw}main{max-width:1100px;margin:24px auto;padding:0 20px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px}.card{background:white;border:1px solid #dfe5ec;border-radius:10px;padding:16px;box-shadow:0 1px 3px #0001}h1{font-size:22px;margin:0 0 6px}h2{font-size:16px;margin:0 0 12px}.metric{font-size:21px;font-weight:700}.muted{color:#667085;font-size:13px}.ok{color:#067647}.locked{color:#b54708}.warn{background:#fff7ed;padding:12px;border-left:4px solid #f59e0b;margin:16px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style></head><body><header><h1>Modular Crypto Trading Bot — Thesis Research Dashboard</h1><div style="color:#cbd5e1">v0.50 · Evidence Before Opinion · fail-closed</div></header><main><div class="warn">Research/monitoring only. LIVE=false · PAPER=false · Kraken SEALED. No exchange-order endpoint is enabled.</div><div class="grid"><div class="card"><h2>Service</h2><div id="health" class="metric ok">…</div><div id="version" class="muted"></div></div><div class="card"><h2>Mode</h2><div id="mode" class="metric">…</div></div><div class="card"><h2>Latest evidence</h2><div id="experiment" class="metric">…</div><div id="decision" class="muted"></div></div><div class="card"><h2>Kraken</h2><div id="kraken" class="metric locked">…</div></div></div><div class="card" style="margin-top:16px"><h2>Research status</h2><pre id="research">…</pre></div></main><script>async function refresh(){const [h,r]=await Promise.all(['/health','/research/status'].map(u=>fetch(u).then(x=>x.json())));health.textContent=h.status;version.textContent=h.version;mode.textContent=h.execution_mode;experiment.textContent=r.latest_completed_experiment;decision.textContent=r.latest_completed_decision;kraken.textContent=r.kraken_holdout;research.textContent=JSON.stringify(r,null,2)}refresh();setInterval(refresh,30000);</script></body></html>'''
