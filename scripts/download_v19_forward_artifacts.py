from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
import os
from pathlib import Path, PurePosixPath
import urllib.parse
import urllib.request
import zipfile

import requests


API = "https://api.github.com"
ARTIFACT_PREFIX = "v19-forward-microstructure-"


def _utc(value: str) -> datetime:
    text = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _request_json(url: str, token: str | None) -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
        "User-Agent": "modular-crypto-trading-bot-phase-q",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _request_bytes(url: str, token: str | None) -> bytes:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
        "User-Agent": "modular-crypto-trading-bot-phase-q",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    # urllib's default redirect handler carries Authorization to the signed
    # storage URL. Follow each redirect explicitly, never carrying credentials
    # outside api.github.com. Do not log signed URLs or response bodies.
    current = url
    for _ in range(5):
        parsed = urllib.parse.urlsplit(current)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("UNSAFE_ARTIFACT_REDIRECT")
        request_headers = headers if parsed.hostname == "api.github.com" else {"User-Agent": headers["User-Agent"]}
        response = requests.get(current, headers=request_headers, timeout=120, allow_redirects=False)
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location")
            if not location:
                raise ValueError("ARTIFACT_REDIRECT_WITHOUT_LOCATION")
            current = urllib.parse.urljoin(current, location)
            continue
        if response.status_code != 200:
            raise ValueError(f"ARTIFACT_HTTP_{response.status_code}")
        if len(response.content) > 32 * 1024 * 1024:
            raise ValueError("ARTIFACT_TOO_LARGE")
        return response.content
    raise ValueError("ARTIFACT_REDIRECT_LIMIT")


def list_forward_artifacts(repo: str, *, token: str | None, created_after: str) -> list[dict]:
    cutoff = _utc(created_after)
    selected: list[dict] = []
    page = 1
    while True:
        query = urllib.parse.urlencode({"per_page": 100, "page": page})
        payload = _request_json(f"{API}/repos/{repo}/actions/artifacts?{query}", token)
        rows = payload.get("artifacts") or []
        if not rows:
            break
        for row in rows:
            name = str(row.get("name") or "")
            created = row.get("created_at")
            if not name.startswith(ARTIFACT_PREFIX) or row.get("expired") or not created:
                continue
            if _utc(created) < cutoff:
                continue
            selected.append(row)
        if len(rows) < 100:
            break
        page += 1
    selected.sort(key=lambda x: (x.get("created_at") or "", int(x.get("id") or 0)))
    return selected


def _safe_json_members(blob: bytes) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        total_size = 0
        seen = set()
        for info in zf.infolist():
            name = PurePosixPath(info.filename)
            if info.is_dir() or name.suffix.lower() != ".json":
                continue
            if name.is_absolute() or ".." in name.parts:
                raise ValueError("UNSAFE_ZIP_PATH")
            total_size += info.file_size
            if total_size > 32 * 1024 * 1024 or name.name in seen:
                raise ValueError("AMBIGUOUS_OR_OVERSIZED_ZIP")
            seen.add(name.name)
            out.append((name.name, zf.read(info)))
    return out


def harvest(repo: str, output_dir: Path, *, token: str | None, created_after: str) -> dict:
    artifacts = list_forward_artifacts(repo, token=token, created_after=created_after)
    output_dir.mkdir(parents=True, exist_ok=True)
    downloaded = []
    errors = []
    json_files = 0
    for artifact in artifacts:
        aid = int(artifact["id"])
        try:
            expected_url = f"{API}/repos/{repo}/actions/artifacts/{aid}/zip"
            if artifact.get("archive_download_url") != expected_url:
                raise ValueError("ARTIFACT_REPOSITORY_MISMATCH")
            blob = _request_bytes(expected_url, token)
            digest = "sha256:" + sha256(blob).hexdigest()
            if artifact.get("digest") != digest:
                raise ValueError("ARTIFACT_DIGEST_MISMATCH_OR_MISSING")
            members = _safe_json_members(blob)
            if not members:
                errors.append({"artifact_id": aid, "reason": "NO_JSON_MEMBERS"})
                continue
            # Validate every member before persisting any part of an artifact.
            for _, data in members:
                if not isinstance(json.loads(data), dict):
                    raise ValueError("INVALID_SNAPSHOT_OBJECT")
            saved = []
            for original_name, data in members:
                target = output_dir / f"artifact-{aid}-{original_name}"
                target.write_bytes(data)
                saved.append(str(target))
                json_files += 1
            downloaded.append({
                "artifact_id": aid,
                "name": artifact.get("name"),
                "created_at": artifact.get("created_at"),
                "digest": digest,
                "digest_verified": True,
                "workflow_run": artifact.get("workflow_run"),
                "saved_files": saved,
            })
        except Exception as exc:
            safe_reason = str(exc) if isinstance(exc, ValueError) and str(exc).isupper() and " " not in str(exc) else type(exc).__name__
            errors.append({"artifact_id": aid, "reason": safe_reason})
    return {
        "repo": repo,
        "created_after": created_after,
        "artifact_prefix": ARTIFACT_PREFIX,
        "matching_artifact_count": len(artifacts),
        "downloaded_artifact_count": len(downloaded),
        "json_file_count": json_files,
        "downloaded": downloaded,
        "errors": errors,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", default=os.getenv("GITHUB_REPOSITORY"))
    p.add_argument("--created-after", default="2026-09-10T13:00:00Z")
    p.add_argument("--output-dir", default="artifacts/v20/source_snapshots")
    p.add_argument("--manifest", default="artifacts/v20/artifact_harvest_manifest.json")
    args = p.parse_args()
    if not args.repo or "/" not in args.repo:
        raise SystemExit("--repo owner/name is required")

    token = os.getenv("GITHUB_TOKEN")
    result = harvest(
        args.repo,
        Path(args.output_dir),
        token=token,
        created_after=args.created_after,
    )
    result["generated_at"] = datetime.now(timezone.utc).isoformat()
    manifest = Path(args.manifest)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({
        "matching_artifact_count": result["matching_artifact_count"],
        "downloaded_artifact_count": result["downloaded_artifact_count"],
        "json_file_count": result["json_file_count"],
        "errors": len(result["errors"]),
        "manifest": str(manifest),
    }, indent=2))


if __name__ == "__main__":
    main()
