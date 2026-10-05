from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from research_bot.forward_evidence_v15 import build_forward_evidence, evidence_markdown


DEFAULT_BASE_URL = "https://thesis-trading-bot-v08-production.up.railway.app"


def fetch_json(url: str, retries: int = 5, timeout: int = 30) -> dict:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            req = Request(url, headers={"User-Agent": "modular-crypto-trading-bot-v15-evidence/1.0"})
            with urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"unexpected HTTP status {resp.status} for {url}")
                return json.loads(resp.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            last_error = exc
            if isinstance(exc, HTTPError) and exc.code in {400, 401, 403, 404, 410, 423}:
                raise
            if attempt + 1 < retries:
                time.sleep(2 + attempt * 2)
    raise RuntimeError(f"failed to fetch {url}: {last_error}")


def collect_endpoints(base: str, limit: int = 500) -> tuple[dict, dict]:
    """Preflight the current deployment contract before touching legacy routes.

    A disabled service is a valid operational state, never a zero-return paper
    experiment. A missing endpoint remains an infrastructure block. Neither
    state produces an old v15 snapshot that downstream maturity could count.
    """
    raw = {}
    status = {"schema_version": 1, "captured_at": datetime.now(timezone.utc).isoformat(),
              "collector": "V15_LEGACY_CONTRACT_PREFLIGHT", "status": "BLOCKED",
              "countable_forward_evidence": False, "execution_authorized": False,
              "live_promotion": "PROHIBITED", "economic_metrics": None}
    endpoints = {"health": "/health", "research": "/research/status", "paper": "/paper/status"}
    current = None
    try:
        for current, path in endpoints.items():
            raw[current] = fetch_json(base + path)
        if (raw["health"].get("execution_mode") == "RESEARCH_ONLY"
                and raw["paper"].get("paper_execution_enabled") is False
                and all(raw[name].get("live_execution") is False for name in endpoints)):
            status.update(status="DEPRECATED_BY_CURRENT_GOVERNANCE", reason="PAPER_OFF")
            return raw, status
        for current, path in {"observations": f"/paper/observations?limit={limit}",
                              "fills": f"/paper/fills?limit={limit}"}.items():
            raw[current] = fetch_json(base + path)
    except Exception as exc:
        status.update(reason="DEPLOYMENT_API_CONTRACT_UNAVAILABLE", failed_endpoint=current,
                      error_type=type(exc).__name__, http_status=getattr(exc, "code", None))
        return raw, status
    status.update(status="LEGACY_ENDPOINTS_AVAILABLE")
    return raw, status


def main() -> int:
    parser = argparse.ArgumentParser(description="Export v0.15 forward production-paper evidence")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--output-dir", default="artifacts/v15_forward_evidence")
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise FileExistsError("Collector output must be empty; preserve earlier evidence")

    raw, collection_status = collect_endpoints(base, args.limit)
    (output_dir / "v15_collection_status.json").write_text(
        json.dumps(collection_status, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")
    if collection_status["status"] != "LEGACY_ENDPOINTS_AVAILABLE":
        print(json.dumps(collection_status, sort_keys=True))
        return 0 if collection_status["status"] == "DEPRECATED_BY_CURRENT_GOVERNANCE" else 2

    evidence = build_forward_evidence(
        health=raw["health"],
        research=raw["research"],
        paper=raw["paper"],
        observations_payload=raw["observations"],
        fills_payload=raw["fills"],
    )

    (output_dir / "v15_forward_evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    (output_dir / "v15_forward_evidence.md").write_text(evidence_markdown(evidence), encoding="utf-8")
    (output_dir / "raw_production_endpoints.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )

    print(json.dumps({
        "evidence_state": evidence["scientific_state"]["evidence_state"],
        "live_promotion": evidence["scientific_state"]["live_promotion"],
        "safety_contract_ok": evidence["safety_contract"]["ok"],
        "observations": evidence["forward_counts"]["observations_total"],
        "fills": evidence["forward_counts"]["fills_total"],
        "equity_points": evidence["forward_counts"]["equity_points_total"],
        "cumulative_return": evidence["paper_account"]["cumulative_return"],
        "current_drawdown": evidence["paper_account"]["current_drawdown"],
    }, sort_keys=True))

    if not evidence["safety_contract"]["ok"]:
        return 2
    if evidence["scientific_state"]["live_promotion"] != "PROHIBITED":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
