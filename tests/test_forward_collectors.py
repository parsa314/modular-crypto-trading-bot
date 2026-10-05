from hashlib import sha256
from io import BytesIO
import json
from types import SimpleNamespace
from urllib.error import HTTPError
from zipfile import ZipFile

import pytest

from scripts import download_v19_forward_artifacts as harvest
from scripts import export_production_evidence_v15 as legacy


def archive(members):
    out = BytesIO()
    with ZipFile(out, "w") as z:
        for name, body in members:
            z.writestr(name, body)
    return out.getvalue()


def test_redirect_never_forwards_github_credentials(monkeypatch):
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        if len(calls) == 1:
            return SimpleNamespace(status_code=302, headers={"Location": "https://storage.example/blob?signature=private"})
        return SimpleNamespace(status_code=200, headers={}, content=b"zip bytes")
    monkeypatch.setattr(harvest.requests, "get", get)
    assert harvest._request_bytes("https://api.github.com/artifact", "test-token") == b"zip bytes"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer test-token"
    assert "Authorization" not in calls[1][1]["headers"]
    assert all(call[1]["allow_redirects"] is False for call in calls)


@pytest.mark.parametrize("location", ["http://storage.example/blob", "https://user:pass@storage.example/blob", "file:///tmp/blob"])
def test_insecure_redirect_rejected(monkeypatch, location):
    monkeypatch.setattr(harvest.requests, "get", lambda *a, **k: SimpleNamespace(status_code=302, headers={"Location": location}))
    with pytest.raises(ValueError, match="UNSAFE"):
        harvest._request_bytes("https://api.github.com/artifact", "token")


def test_verified_artifact_download_and_digest_failure(tmp_path, monkeypatch):
    raw = archive([("snapshot.json", '{"version":"v0.19"}')])
    row = {"id": 42, "archive_download_url": f"{harvest.API}/repos/o/r/actions/artifacts/42/zip",
           "digest": "sha256:" + sha256(raw).hexdigest()}
    monkeypatch.setattr(harvest, "list_forward_artifacts", lambda *a, **k: [row])
    monkeypatch.setattr(harvest, "_request_bytes", lambda *a: raw)
    result = harvest.harvest("o/r", tmp_path / "valid", token="token", created_after="2026-09-10T00:00:00Z")
    assert result["downloaded_artifact_count"] == 1
    assert result["downloaded"][0]["digest_verified"] is True
    row["digest"] = "sha256:" + "0" * 64
    result = harvest.harvest("o/r", tmp_path / "invalid", token="token", created_after="2026-09-10T00:00:00Z")
    assert result["downloaded_artifact_count"] == 0
    assert result["errors"][0]["reason"] == "ARTIFACT_DIGEST_MISMATCH_OR_MISSING"
    assert not list((tmp_path / "invalid").iterdir())


@pytest.mark.parametrize("members", [[("../bad.json", "{}")], [("a/x.json", "{}"), ("b/x.json", "{}")]])
def test_unsafe_or_ambiguous_zip_rejected(members):
    with pytest.raises(ValueError):
        harvest._safe_json_members(archive(members))


def test_harvest_errors_do_not_leak_signed_urls_or_tokens(tmp_path, monkeypatch):
    row = {"id": 42, "archive_download_url": f"{harvest.API}/repos/o/r/actions/artifacts/42/zip"}
    monkeypatch.setattr(harvest, "list_forward_artifacts", lambda *a, **k: [row])
    def fail(*a):
        raise RuntimeError("https://storage.example/?signature=private Bearer secret")
    monkeypatch.setattr(harvest, "_request_bytes", fail)
    result = harvest.harvest("o/r", tmp_path, token="secret", created_after="2026-09-10T00:00:00Z")
    assert result["errors"] == [{"artifact_id": 42, "reason": "RuntimeError"}]


def test_paper_off_never_fetches_or_fabricates_observations(monkeypatch):
    calls = []
    def get(url):
        calls.append(url)
        if url.endswith("/health"):
            return {"execution_mode": "RESEARCH_ONLY", "live_execution": False}
        if url.endswith("/research/status"):
            return {"live_execution": False}
        if url.endswith("/paper/status"):
            return {"paper_execution_enabled": False, "live_execution": False}
        pytest.fail("Legacy routes must not be requested while PAPER is off")
    monkeypatch.setattr(legacy, "fetch_json", get)
    raw, result = legacy.collect_endpoints("https://example.org")
    assert len(calls) == 3
    assert "observations" not in raw
    assert result["status"] == "DEPRECATED_BY_CURRENT_GOVERNANCE"
    assert result["economic_metrics"] is None
    assert result["countable_forward_evidence"] is False


def test_missing_deployment_contract_is_infrastructure_block(monkeypatch):
    def get(url):
        raise HTTPError(url, 404, "Not Found", {}, None)
    monkeypatch.setattr(legacy, "fetch_json", get)
    _, result = legacy.collect_endpoints("https://example.org")
    assert result["status"] == "BLOCKED"
    assert result["http_status"] == 404
    assert result["countable_forward_evidence"] is False


def test_permanent_404_is_not_retried(monkeypatch):
    calls = []
    def fail(request, **kwargs):
        calls.append(request)
        raise HTTPError(request.full_url, 404, "Not Found", {}, None)
    monkeypatch.setattr(legacy, "urlopen", fail)
    with pytest.raises(HTTPError):
        legacy.fetch_json("https://example.org/missing")
    assert len(calls) == 1
