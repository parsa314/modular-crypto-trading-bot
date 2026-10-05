from __future__ import annotations

from dataclasses import dataclass, asdict
import re


V54_UNIVERSE = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT")
V54_EXPERIMENT = "V54_REAL_COINEX_FEATURE_AUDIT"
V54_SOURCE = "CoinEx public spot OHLCV"


def valid_digest(value, length):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{" + str(length) + r"}", value) is not None and len(set(value)) > 1


def canonical_universe(symbols):
    if (not isinstance(symbols, (list, tuple)) or len(symbols) != len(V54_UNIVERSE)
            or any(not isinstance(x, str) for x in symbols) or set(symbols) != set(V54_UNIVERSE)):
        raise ValueError("V54 requires the complete frozen five-symbol universe")
    return V54_UNIVERSE


@dataclass(frozen=True)
class V54CompletionPolicy:
    min_completed_symbols: int = 3
    require_source_commit: bool = True
    require_dataset_manifest_per_symbol: bool = True
    require_cross_symbol_families: bool = True

    def __post_init__(self):
        if (type(self.min_completed_symbols) is not int or not 3 <= self.min_completed_symbols <= 5
                or self.require_source_commit is not True or self.require_dataset_manifest_per_symbol is not True
                or self.require_cross_symbol_families is not True):
            raise ValueError("V54 completion provenance checks cannot be disabled")


def assess_v54_completion(report: dict, policy: V54CompletionPolicy | None = None) -> dict:
    pol = policy or V54CompletionPolicy()
    reasons: list[str] = []
    if report.get("experiment") != V54_EXPERIMENT:
        reasons.append("EXPERIMENT_IDENTITY_INVALID")
    try:
        canonical_universe(report.get("symbols_requested"))
    except ValueError:
        reasons.append("REQUESTED_UNIVERSE_INVALID")

    if report.get("paper_execution") is not False:
        reasons.append("PAPER_EXECUTION_NOT_FALSE")
    if report.get("live_execution") is not False:
        reasons.append("LIVE_EXECUTION_NOT_FALSE")

    completed = list(report.get("symbols_completed") or [])
    if (any(not isinstance(x, str) for x in completed) or len(set(completed)) != len(completed)
            or not set(completed).issubset(V54_UNIVERSE)):
        reasons.append("COMPLETED_UNIVERSE_INVALID")
        completed = [x for x in completed if isinstance(x, str) and x in V54_UNIVERSE]
    if len(completed) < pol.min_completed_symbols:
        reasons.append("INSUFFICIENT_COMPLETED_SYMBOLS")

    source_commit = report.get("source_commit")
    if pol.require_source_commit and not source_commit:
        reasons.append("SOURCE_COMMIT_MISSING")
    elif not valid_digest(source_commit, 40):
        reasons.append("SOURCE_COMMIT_INVALID")

    manifests = report.get("dataset_manifests") or {}
    if pol.require_dataset_manifest_per_symbol:
        for symbol in completed:
            manifest = manifests.get(symbol)
            if not manifest:
                reasons.append(f"MANIFEST_MISSING:{symbol}")
                continue
            if not isinstance(manifest, dict):
                reasons.append(f"MANIFEST_INVALID:{symbol}")
                continue
            if (manifest.get("protocol") != "v0.54" or manifest.get("symbol") != symbol
                    or manifest.get("source") != V54_SOURCE or manifest.get("paper_execution") is not False
                    or manifest.get("live_execution") is not False):
                reasons.append(f"MANIFEST_IDENTITY_OR_SAFETY_INVALID:{symbol}")
            for key in ("frame_sha256", "schema_sha256"):
                if not valid_digest(manifest.get(key), 64):
                    reasons.append(f"MANIFEST_HASH_INVALID:{symbol}:{key}")
            if type(manifest.get("rows")) is not int or manifest["rows"] <= 0:
                reasons.append(f"MANIFEST_ROWS_INVALID:{symbol}")
            for key in ("frame_sha256", "schema_sha256", "rows", "decision_start", "decision_end"):
                if manifest.get(key) in (None, "", 0):
                    reasons.append(f"MANIFEST_FIELD_MISSING:{symbol}:{key}")

    per_symbol = report.get("per_symbol") or {}
    for symbol in completed:
        audit = per_symbol.get(symbol)
        if not audit:
            reasons.append(f"AUDIT_MISSING:{symbol}")
            continue
        if audit.get("paper_execution") is not False or audit.get("live_execution") is not False:
            reasons.append(f"UNSAFE_SYMBOL_AUDIT:{symbol}")
        if not audit.get("variants") or not audit.get("family_value"):
            reasons.append(f"EMPTY_SYMBOL_AUDIT:{symbol}")

    cross = report.get("cross_symbol_evidence") or {}
    families = cross.get("families") or {}
    if pol.require_cross_symbol_families and not families:
        reasons.append("CROSS_SYMBOL_EVIDENCE_MISSING")
    if cross.get("paper_execution") is not False or cross.get("live_execution") is not False:
        reasons.append("CROSS_SYMBOL_SAFETY_INVALID")

    complete = not reasons
    return {
        "protocol": "v0.54",
        "engineering_status": "V54_ENGINEERING_COMPLETE",
        "empirical_status": "V54_EMPIRICAL_COMPLETE" if complete else "V54_EMPIRICAL_INCOMPLETE",
        "complete": complete,
        "reasons": reasons,
        "completed_symbols": completed,
        "promotion_candidates": sorted(
            family for family, values in families.items() if bool(values.get("promotion_candidate", False))
        ),
        "paired_return_supported": sorted(
            family for family, values in families.items() if bool(values.get("paired_return_support", False))
        ),
        "execution_authorized": False,
        "paper_execution": False,
        "live_execution": False,
        "policy": asdict(pol),
    }
