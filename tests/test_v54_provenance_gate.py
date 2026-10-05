from copy import deepcopy
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from research_bot.v54_completion import V54CompletionPolicy, V54_UNIVERSE, assess_v54_completion, canonical_universe
from scripts import run_v54_feature_audit as cli


def report():
    symbols = list(V54_UNIVERSE[:3])
    audit = {"paper_execution": False, "live_execution": False, "variants": {"ALL": {}}, "family_value": {"ICHIMOKU": {}}}
    return {"experiment": "V54_REAL_COINEX_FEATURE_AUDIT", "symbols_requested": list(V54_UNIVERSE),
            "symbols_completed": symbols, "source_commit": "f234a85028ed2d86cd3bc672e7491f72b0245b9b",
            "paper_execution": False, "live_execution": False,
            "dataset_manifests": {s: {"protocol": "v0.54", "symbol": s, "source": "CoinEx public spot OHLCV",
                                       "frame_sha256": "0123456789abcdef" * 4, "schema_sha256": "fedcba9876543210" * 4,
                                       "rows": 1000, "decision_start": "2026-01-01T00:00:00Z", "decision_end": "2026-02-01T00:00:00Z",
                                       "paper_execution": False, "live_execution": False} for s in symbols},
            "per_symbol": {s: deepcopy(audit) for s in symbols},
            "cross_symbol_evidence": {"families": {"ICHIMOKU": {}}, "paper_execution": False, "live_execution": False}}


@pytest.mark.parametrize("field,value", [("experiment", "FAKE"), ("source_commit", "abc1234"),
                                        ("source_commit", "x" * 40), ("source_commit", "0" * 40),
                                        ("symbols_requested", ["BTC/USDT"]),
                                        ("symbols_requested", ["BTC/USDT"] * 5),
                                        ("symbols_completed", ["BTC/USDT", "ETH/USDT", "UNKNOWN/USDT"]),
                                        ("symbols_completed", ["BTC/USDT"] * 3)])
def test_identity_or_universe_corruption_fails_closed(field, value):
    r = report()
    assert assess_v54_completion(r)["complete"] is True
    r[field] = value
    result = assess_v54_completion(r)
    assert result["complete"] is False
    assert result["execution_authorized"] is False


@pytest.mark.parametrize("field,value", [("protocol", "v0.53"), ("symbol", "ETH/USDT"),
                                        ("source", "SYNTHETIC"), ("paper_execution", True),
                                        ("live_execution", True), ("frame_sha256", "placeholder"),
                                        ("schema_sha256", "x" * 64), ("rows", True)])
def test_manifest_identity_hashes_and_safety_are_mandatory(field, value):
    r = report()
    r["dataset_manifests"]["BTC/USDT"][field] = value
    assert assess_v54_completion(r)["complete"] is False


def test_arbitrary_cli_universe_rejected_before_any_provider_call(monkeypatch):
    monkeypatch.setattr(cli, "build_symbol_frame", lambda *a, **k: pytest.fail("No network read allowed"))
    with pytest.raises(ValueError, match="frozen"):
        cli.run_universe(("UNKNOWN/USDT",), now=pd.Timestamp("2026-01-01T00:00:00Z"), config=None,
                         one_hour_bars=3000, four_hour_bars=1200)
    assert canonical_universe(tuple(reversed(V54_UNIVERSE))) == V54_UNIVERSE


def test_completion_policy_cannot_disable_provenance():
    with pytest.raises(ValueError):
        V54CompletionPolicy(require_source_commit=False)


def test_source_commit_uses_checkout_not_scheduler_identity(monkeypatch):
    source = "f234a85028ed2d86cd3bc672e7491f72b0245b9b"
    monkeypatch.setenv("GITHUB_SHA", "876e229a8d3e73f2e9d646d4126c8ce08e4c11b4")
    monkeypatch.delenv("SOURCE_COMMIT", raising=False)
    monkeypatch.setattr(cli.subprocess, "check_output", lambda *a, **k: source)
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    assert cli._source_commit() == source
    monkeypatch.setenv("SOURCE_COMMIT", "876e229a8d3e73f2e9d646d4126c8ce08e4c11b4")
    with pytest.raises(ValueError, match="match"):
        cli._source_commit()


def test_modified_scientific_source_cannot_claim_a_clean_commit(monkeypatch):
    monkeypatch.delenv("SOURCE_COMMIT", raising=False)
    monkeypatch.setattr(cli.subprocess, "check_output", lambda *a, **k: "f234a85028ed2d86cd3bc672e7491f72b0245b9b")
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1))
    with pytest.raises(ValueError, match="source tree"):
        cli._source_commit()
