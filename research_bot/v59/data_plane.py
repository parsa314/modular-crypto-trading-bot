from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timezone
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd

from .artifacts import ArtifactRef, ImmutableArtifactStore, bundle_hash
from .hashing import stable_hash
from .market_data import (
    MarketDataRequest,
    ProviderOHLCVResult,
    ReadOnlyOHLCVProvider,
    normalize_and_audit,
)


@dataclass(frozen=True)
class DatasetBundle:
    run_id: str
    dataset_id: str
    dataset_hash: str
    quality_hash: str
    raw_artifact: ArtifactRef
    normalized_artifact: ArtifactRef
    manifest_artifact: ArtifactRef
    bundle_hash: str
    rows: int
    quality_pass: bool


def _csv_bytes(frame: pd.DataFrame) -> bytes:
    buffer = StringIO()
    frame.to_csv(buffer, index=False, date_format="%Y-%m-%dT%H:%M:%S.%f%z")
    return buffer.getvalue().encode("utf-8")


def ingest_ohlcv(
    *,
    provider: ReadOnlyOHLCVProvider,
    request: MarketDataRequest,
    store: ImmutableArtifactStore,
    run_id: str,
) -> DatasetBundle:
    if not isinstance(run_id, str) or not run_id.strip() or run_id != run_id.strip():
        raise ValueError("run_id must be a canonical nonempty string")

    result = provider.fetch_ohlcv(request)
    if not isinstance(result, ProviderOHLCVResult):
        raise TypeError("provider returned an invalid OHLCV result")

    frame, quality = normalize_and_audit(result, request)
    if not quality.quality_pass:
        # Raw evidence is still retained, but no normalized dataset is promoted.
        raw_ref = store.write_bytes(f"{run_id}/raw/provider_payload.bin", result.raw_payload)
        rejection = {
            "classification": "V59_MARKET_DATA_REJECTED",
            "run_id": run_id,
            "provider_id": result.provider_id,
            "request": asdict(request),
            "provider_metadata": dict(result.provider_metadata),
            "source_descriptor": result.source_descriptor,
            "received_at": result.received_at.astimezone(timezone.utc).isoformat(),
            "raw_sha256": raw_ref.sha256,
            "quality": asdict(quality),
            "quality_hash": quality.report_hash,
        }
        store.write_json(f"{run_id}/rejection_manifest.json", rejection)
        raise ValueError(f"market data failed quality gate: {quality.reasons}")

    dataset_payload = _csv_bytes(frame)
    dataset_hash = stable_hash(
        {
            "provider_id": result.provider_id,
            "exchange": result.exchange,
            "symbol": result.symbol,
            "timeframe": result.timeframe,
            "market_type": result.market_type,
            "request_since": request.since,
            "request_until": request.until,
            "rows": frame.to_dict(orient="records"),
        }
    )
    dataset_id = f"{result.exchange}:{result.market_type}:{result.symbol}:{result.timeframe}:{dataset_hash[:16]}"

    raw_ref = store.write_bytes(f"{run_id}/raw/provider_payload.bin", result.raw_payload)
    normalized_ref = store.write_bytes(f"{run_id}/normalized/ohlcv.csv", dataset_payload)
    manifest = {
        "classification": "V59_VERIFIED_MARKET_DATA",
        "run_id": run_id,
        "dataset_id": dataset_id,
        "dataset_hash": dataset_hash,
        "provider_id": result.provider_id,
        "exchange": result.exchange,
        "symbol": result.symbol,
        "timeframe": result.timeframe,
        "market_type": result.market_type,
        "source_descriptor": result.source_descriptor,
        "provider_metadata": dict(result.provider_metadata),
        "received_at": result.received_at.astimezone(timezone.utc).isoformat(),
        "request": asdict(request),
        "quality": asdict(quality),
        "quality_hash": quality.report_hash,
        "artifacts": {
            "raw": asdict(raw_ref),
            "normalized": asdict(normalized_ref),
        },
        "paper_execution": False,
        "live_execution": False,
    }
    manifest_ref = store.write_json(f"{run_id}/dataset_manifest.json", manifest)
    refs = [raw_ref, normalized_ref, manifest_ref]
    return DatasetBundle(
        run_id=run_id,
        dataset_id=dataset_id,
        dataset_hash=dataset_hash,
        quality_hash=quality.report_hash,
        raw_artifact=raw_ref,
        normalized_artifact=normalized_ref,
        manifest_artifact=manifest_ref,
        bundle_hash=bundle_hash(refs),
        rows=len(frame),
        quality_pass=True,
    )
