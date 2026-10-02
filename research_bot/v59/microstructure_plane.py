from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timezone
from typing import Any, Protocol

from .artifacts import ArtifactRef, ImmutableArtifactStore, bundle_hash
from .hashing import stable_hash
from .microstructure import OrderBookSnapshot, PITMetric


@dataclass(frozen=True)
class OrderBookProviderResult:
    provider_id: str
    snapshot: OrderBookSnapshot
    raw_payload: bytes
    provider_metadata: dict[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, str) or not self.provider_id.strip():
            raise ValueError("provider_id is required")
        if not isinstance(self.raw_payload, bytes) or not self.raw_payload:
            raise ValueError("raw_payload evidence is required")


class ReadOnlyOrderBookProvider(Protocol):
    provider_id: str

    def fetch_order_book(self, *, symbol: str, limit: int) -> OrderBookProviderResult:
        ...


@dataclass(frozen=True)
class MicrostructureBundle:
    run_id: str
    snapshot_hash: str
    raw_artifact: ArtifactRef
    manifest_artifact: ArtifactRef
    bundle_hash: str


def ingest_order_book(
    *,
    provider: ReadOnlyOrderBookProvider,
    symbol: str,
    limit: int,
    store: ImmutableArtifactStore,
    run_id: str,
) -> MicrostructureBundle:
    if not isinstance(run_id, str) or not run_id.strip() or run_id != run_id.strip():
        raise ValueError("run_id must be canonical")
    result = provider.fetch_order_book(symbol=symbol, limit=limit)
    if result.snapshot.symbol != symbol:
        raise ValueError("order-book provider symbol relabeling is forbidden")
    raw_ref = store.write_bytes(f"{run_id}/raw/order_book_payload.bin", result.raw_payload)
    snapshot = result.snapshot
    manifest = {
        "classification": "V59_PROSPECTIVE_ORDER_BOOK_EVIDENCE",
        "run_id": run_id,
        "provider_id": result.provider_id,
        "venue": snapshot.venue,
        "symbol": snapshot.symbol,
        "observed_at": snapshot.observed_at.astimezone(timezone.utc).isoformat(),
        "snapshot_hash": snapshot.snapshot_hash,
        "best_bid": snapshot.best_bid,
        "best_ask": snapshot.best_ask,
        "spread_bps": snapshot.spread_bps,
        "bid_depth_10bps": snapshot.side_depth_notional(side="bid"),
        "ask_depth_10bps": snapshot.side_depth_notional(side="ask"),
        "provider_metadata": result.provider_metadata,
        "raw_artifact": asdict(raw_ref),
        "paper_execution": False,
        "live_execution": False,
    }
    manifest_ref = store.write_json(f"{run_id}/order_book_manifest.json", manifest)
    return MicrostructureBundle(
        run_id=run_id,
        snapshot_hash=snapshot.snapshot_hash,
        raw_artifact=raw_ref,
        manifest_artifact=manifest_ref,
        bundle_hash=bundle_hash([raw_ref, manifest_ref]),
    )


def metric_feature_snapshot(metrics: list[PITMetric], *, decision_at) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for metric in metrics:
        metric.validate_for(decision_at)
        if metric.metric in result:
            raise ValueError("duplicate PIT metric name")
        result[metric.metric] = {
            "value": metric.value,
            "effective_at": metric.effective_at.astimezone(timezone.utc).isoformat(),
            "available_at": metric.available_at.astimezone(timezone.utc).isoformat(),
            "observed_at": metric.observed_at.astimezone(timezone.utc).isoformat(),
            "source": metric.source,
            "source_hash": metric.source_hash,
            "metric_hash": metric.metric_hash,
        }
    result["feature_snapshot_hash"] = stable_hash(result)
    return result


@dataclass(frozen=True)
class MetricProviderResult:
    provider_id: str
    metrics: tuple[PITMetric, ...]
    raw_payload: bytes
    provider_metadata: dict[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, str) or not self.provider_id.strip():
            raise ValueError("provider_id is required")
        if not self.metrics:
            raise ValueError("metrics are required")
        if not isinstance(self.raw_payload, bytes) or not self.raw_payload:
            raise ValueError("raw_payload evidence is required")


class ReadOnlyMetricProvider(Protocol):
    provider_id: str

    def fetch_metrics(self, *, symbol: str) -> MetricProviderResult:
        ...


def ingest_metrics(
    *,
    provider: ReadOnlyMetricProvider,
    symbol: str,
    store: ImmutableArtifactStore,
    run_id: str,
) -> dict[str, Any]:
    result = provider.fetch_metrics(symbol=symbol)
    raw_ref = store.write_bytes(f"{run_id}/raw/metric_payload.bin", result.raw_payload)
    metrics = []
    for metric in result.metrics:
        if symbol.replace("/", "").upper() not in metric.entity.replace("/", "").upper():
            raise ValueError("metric provider entity relabeling is forbidden")
        metrics.append(
            {
                **asdict(metric),
                "effective_at": metric.effective_at.astimezone(timezone.utc).isoformat(),
                "available_at": metric.available_at.astimezone(timezone.utc).isoformat(),
                "observed_at": metric.observed_at.astimezone(timezone.utc).isoformat(),
                "metric_hash": metric.metric_hash,
            }
        )
    manifest = {
        "classification": "V59_PROSPECTIVE_DERIVATIVES_EVIDENCE",
        "run_id": run_id,
        "provider_id": result.provider_id,
        "symbol": symbol,
        "metrics": metrics,
        "provider_metadata": result.provider_metadata,
        "raw_artifact": asdict(raw_ref),
        "paper_execution": False,
        "live_execution": False,
    }
    manifest_ref = store.write_json(f"{run_id}/metric_manifest.json", manifest)
    return {
        "run_id": run_id,
        "metric_count": len(metrics),
        "raw_sha256": raw_ref.sha256,
        "manifest_sha256": manifest_ref.sha256,
        "bundle_hash": bundle_hash([raw_ref, manifest_ref]),
    }
