from __future__ import annotations

import argparse
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path, PurePosixPath
import urllib.parse
import urllib.request
import zipfile


API = "https://api.github.com"
ARTIFACT_PREFIX = "v21-forward-common-anchor-"


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
        "User-Agent": "modular-crypto-trading-bot-v21-phase-q",
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
        "User-Agent": "modular-crypto-trading-bot-v21-phase-q",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as response:
        return response.read()


def _safe_json_members(blob: bytes) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        for info in zf.infolist():
            name = PurePosixPath(info.filename)
            if info.is_dir() or name.suffix.lower() != ".json":
                continue
            if name.is_absolute() or ".." in name.parts:
                continue
            out.append((name.name, zf.read(info)))
    return out


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


def harvest(repo: str, output_dir: Path, *, token: str | None, created_after: str) -> dict:
    artifacts = list_forward_artifacts(repo, token=token, created_after=created_after)
    output_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[dict] = []
    errors: list[dict] = []
    json_files = 0
    for artifact in artifacts:
        aid = int(artifact["id"])
        try:
            blob = _request_bytes(str(artifact["archive_download_url"]), token)
            members = _safe_json_members(blob)
            if not members:
                errors.append({"artifact_id": aid, "reason": "NO_JSON_MEMBERS"})
                continue
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
                "digest": artifact.get("digest"),
                "workflow_run": artifact.get("workflow_run"),
                "saved_files": saved,
            })
        except Exception as exc:
            errors.append({"artifact_id": aid, "reason": f"{type(exc).__name__}:{exc}"})
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=os.getenv("GITHUB_REPOSITORY"))
    parser.add_argument("--created-after", default="2026-09-10T18:00:00Z")
    parser.add_argument("--output-dir", default="artifacts/v21/source_snapshots")
    parser.add_argument("--manifest", default="artifacts/v21/artifact_harvest_manifest.json")
    args = parser.parse_args()
    if not args.repo or "/" not in args.repo:
        raise SystemExit("--repo owner/name is required")
    result = harvest(
        args.repo,
        Path(args.output_dir),
        token=os.getenv("GITHUB_TOKEN"),
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
