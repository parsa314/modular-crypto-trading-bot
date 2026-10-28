"""Evidence-backed release veto and rollback planning. No broker/Git mutations."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import re


class GateVerdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    INCONCLUSIVE = "INCONCLUSIVE"
    ROLLBACK_REQUIRED = "ROLLBACK_REQUIRED"


REQUIRED_GATES = (
    "architecture", "engineering", "postgres", "public_feed", "broker_demo",
    "reconciliation", "crash_recovery", "scientific", "independent_economics",
)
CRITICAL_INCIDENTS = frozenset({
    "CRITICAL_TEST_FAILURE", "INTERFACE_BREAK", "DEMO_DISCONNECTED",
    "DUPLICATE_ORDER", "RISK_VIOLATION", "CRASH_RECOVERY_FAILED",
})
OBSERVED_GATES = frozenset({"public_feed", "broker_demo", "reconciliation", "crash_recovery"})


def assess_release(manifest: dict, root: Path, *, source_sha: str) -> dict:
    """A PASS is revision-bound, evidence-bound and never authorizes LIVE."""
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("source_sha must identify an exact Git revision")
    root = root.resolve()
    results: dict[str, dict] = {}
    supplied = manifest.get("gates", {})
    for name in REQUIRED_GATES:
        item = supplied.get(name)
        verdict, reasons = GateVerdict.BLOCKED, ["EVIDENCE_NOT_SUPPLIED"]
        if isinstance(item, dict):
            try:
                verdict = GateVerdict(item.get("status"))
                reasons = [str(item.get("reason", ""))]
            except ValueError:
                verdict, reasons = GateVerdict.INCONCLUSIVE, ["INVALID_GATE_STATUS"]
            if verdict is GateVerdict.PASS:
                try:
                    if item.get("source_sha") != source_sha:
                        raise ValueError("REVISION_MISMATCH")
                    path = (root / item["evidence_path"]).resolve()
                    if not path.is_relative_to(root) or not path.is_file():
                        raise ValueError("EVIDENCE_PATH_NOT_LOCAL")
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    if digest != item.get("sha256"):
                        raise ValueError("EVIDENCE_HASH_MISMATCH")
                    observed = datetime.fromisoformat(item["observed_at"])
                    if observed.tzinfo is None or observed.utcoffset() is None:
                        raise ValueError("EVIDENCE_TIMESTAMP_NAIVE")
                    if observed > datetime.now(timezone.utc):
                        raise ValueError("EVIDENCE_TIMESTAMP_FUTURE")
                    if name in OBSERVED_GATES and item.get("kind") != "OBSERVED_OPERATIONAL":
                        raise ValueError("OPERATIONAL_OBSERVATION_REQUIRED")
                    if name in {"scientific", "independent_economics"} and item.get("kind") != "INDEPENDENT_RESEARCH":
                        raise ValueError("INDEPENDENT_RESEARCH_REQUIRED")
                    if name in OBSERVED_GATES or name in {"scientific", "independent_economics"}:
                        payload = json.loads(path.read_text(encoding="utf-8"))
                        if not isinstance(payload, dict) or payload.get("fixture") is True or "SYNTHETIC" in str(payload.get("evidence_class", "")):
                            raise ValueError("FIXTURE_CANNOT_ESTABLISH_EXTERNAL_EVIDENCE")
                    reasons = []
                except (ValueError, KeyError, OSError, TypeError) as exc:
                    verdict, reasons = GateVerdict.INCONCLUSIVE, [str(exc)]
        results[name] = {"status": verdict.value, "reasons": reasons}

    if manifest.get("working_tree_dirty", False):
        results["engineering"] = {"status": "INCONCLUSIVE", "reasons": ["UNCOMMITTED_SOURCE_NOT_A_RELEASE_REVISION"]}

    incidents = set(manifest.get("incidents", []))
    rollback = bool(incidents & CRITICAL_INCIDENTS)
    states = {item["status"] for item in results.values()}
    priority = [GateVerdict.ROLLBACK_REQUIRED, GateVerdict.FAIL, GateVerdict.BLOCKED, GateVerdict.INCONCLUSIVE]
    verdict = GateVerdict.ROLLBACK_REQUIRED if rollback else next((v for v in priority if v.value in states), GateVerdict.PASS)
    return {
        "evidence_state": "ENGINEERING", "source_sha": source_sha,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "decision": verdict.value, "gates": results,
        "deployment_allowed": verdict is GateVerdict.PASS,
        "new_entries_allowed": verdict is GateVerdict.PASS,
        "live_allowed": False,
        "requires_existing_position_reconciliation": rollback,
        "runtime_state_changed": False,
    }


def rollback_plan(kind: str, *, last_healthy_sha: str | None,
                  positions_reconciled: bool, scientific_candidate_failed: bool = False) -> dict:
    """Plan only; a code rollback never substitutes for position management."""
    if kind not in {"ENGINEERING", "SCIENTIFIC", "ARCHITECTURE"}:
        raise ValueError("unknown rollback domain")
    if last_healthy_sha is not None and not re.fullmatch(r"[0-9a-f]{40}", last_healthy_sha):
        raise ValueError("invalid rollback SHA")
    ready = kind != "ENGINEERING" or (last_healthy_sha is not None and positions_reconciled)
    return {
        "kind": kind, "status": "PASS" if ready else "BLOCKED",
        "new_entries_allowed": False, "runtime_state_changed": False,
        "target_sha": last_healthy_sha,
        "reconcile_existing_positions_first": kind == "ENGINEERING",
        "candidate_promotion_allowed": False if scientific_candidate_failed else None,
        "preserve_failure_evidence": True, "reuse_spent_test_sample": False,
        "fallback_scope": {"market": "SPOT", "symbols": ["BTCUSDT", "ETHUSDT"], "timeframe": "4h"} if kind == "ARCHITECTURE" else None,
        "actions": {
            "ENGINEERING": ["HALT_DEPLOYMENT", "HALT_NEW_ENTRIES", "RECONCILE", "PRESERVE_LOGS", "RESTORE_VERIFIED_RELEASE", "ROOT_CAUSE", "RETEST"],
            "SCIENTIFIC": ["REMOVE_FAILED_CANDIDATE", "PRESERVE_ACCEPTED_SCIENCE", "PRESERVE_NEGATIVE_RESULTS", "REGISTER_NEW_HYPOTHESIS"],
            "ARCHITECTURE": ["LIMIT_EXECUTABLE_SCOPE", "PRESERVE_MARKET_INTERFACES", "KEEP_OTHER_MARKETS_ON_ROADMAP"],
        }[kind],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = assess_release(json.loads(args.manifest.read_text()), args.root, source_sha=args.source_sha)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation preserves each prior acceptance decision.
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
        handle.write("\n")
    print(json.dumps({"decision": result["decision"], "report": str(args.output)}))
    return 0 if result["decision"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
