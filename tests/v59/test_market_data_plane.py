from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from research_bot.v59.artifacts import ImmutableArtifactStore
from research_bot.v59.data_plane import ingest_ohlcv
from research_bot.v59.market_data import (
    MarketDataRequest,
    ProviderOHLCVResult,
    normalize_and_audit,
    resample_causal_complete,
)


UTC = timezone.utc
START = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


def request(**changes) -> MarketDataRequest:
    kwargs = dict(
        exchange="fixture",
        symbol="BTC/USDT",
        timeframe="1m",
        market_type="spot",
        since=START,
        until=START + timedelta(minutes=6),
        as_of=START + timedelta(minutes=6),
        limit=100,
    )
    kwargs.update(changes)
    return MarketDataRequest(**kwargs)


def rows(count: int = 6):
    out = []
    for i in range(count):
        o = 100.0 + i
        out.append((START + timedelta(minutes=i), o, o + 1.0, o - 1.0, o + 0.5, 10.0 + i))
    return tuple(out)


class FixtureProvider:
    provider_id = "FIXTURE"

    def __init__(self, data=None, **identity):
        self.data = rows() if data is None else tuple(data)
        self.identity = identity

    def fetch_ohlcv(self, req):
        return ProviderOHLCVResult(
            provider_id=self.provider_id,
            exchange=self.identity.get("exchange", req.exchange),
            symbol=self.identity.get("symbol", req.symbol),
            timeframe=self.identity.get("timeframe", req.timeframe),
            market_type=self.identity.get("market_type", req.market_type),
            received_at=req.as_of,
            rows=self.data,
            raw_payload=b"fixture-provider-evidence",
            source_descriptor="fixture:ohlcv",
            provider_metadata={"wire_raw_available": True},
        )


def test_request_requires_strict_utc_and_ordered_window():
    with pytest.raises(ValueError):
        MarketDataRequest(
            exchange="fixture",
            symbol="BTC/USDT",
            timeframe="1m",
            market_type="spot",
            since=datetime(2026, 1, 1),
            until=START + timedelta(minutes=1),
            as_of=START + timedelta(minutes=1),
        )
    with pytest.raises(ValueError):
        request(until=START, as_of=START)


def test_provider_identity_cannot_be_relabelled():
    req = request()
    result = FixtureProvider(exchange="other").fetch_ohlcv(req)
    with pytest.raises(ValueError, match="exchange identity"):
        normalize_and_audit(result, req)


def test_only_closed_bars_are_admitted():
    req = request(
        until=START + timedelta(minutes=5, seconds=30),
        as_of=START + timedelta(minutes=5, seconds=30),
    )
    provider = FixtureProvider(data=rows(7))
    frame, report = normalize_and_audit(provider.fetch_ohlcv(req), req)
    assert report.quality_pass is True
    assert len(frame) == 5
    assert frame["timestamp"].max() == pd.Timestamp(START + timedelta(minutes=4))


def test_gap_is_fail_closed_and_reports_missing_bars():
    req = request()
    data = list(rows())
    del data[2]
    frame, report = normalize_and_audit(FixtureProvider(data=data).fetch_ohlcv(req), req)
    assert len(frame) == 5
    assert report.quality_pass is False
    assert report.gap_count == 1
    assert report.missing_bar_count == 1
    assert "CLOCK_GAPS" in report.reasons


def test_duplicate_timestamp_is_fail_closed():
    req = request()
    data = list(rows())
    data.append(data[-1])
    _, report = normalize_and_audit(FixtureProvider(data=data).fetch_ohlcv(req), req)
    assert report.quality_pass is False
    assert report.duplicates == 2
    assert "DUPLICATE_TIMESTAMPS" in report.reasons


def test_invalid_candle_is_fail_closed():
    req = request()
    data = list(rows())
    row = list(data[1])
    row[2] = row[1] - 2.0
    data[1] = tuple(row)
    _, report = normalize_and_audit(FixtureProvider(data=data).fetch_ohlcv(req), req)
    assert report.quality_pass is False
    assert report.invalid_candle_count == 1


def test_complete_causal_resample_requires_all_source_bars():
    frame = pd.DataFrame(rows(10), columns=["timestamp", "open", "high", "low", "close", "volume"])
    out = resample_causal_complete(frame, source_timeframe="1m", target_timeframe="5m")
    assert len(out) == 2
    assert out.iloc[0]["timestamp"] == pd.Timestamp(START)
    assert out.iloc[0]["open"] == 100.0
    assert out.iloc[0]["close"] == 104.5

    missing = frame.drop(index=[2]).reset_index(drop=True)
    out_missing = resample_causal_complete(missing, source_timeframe="1m", target_timeframe="5m")
    assert len(out_missing) == 1
    assert out_missing.iloc[0]["timestamp"] == pd.Timestamp(START + timedelta(minutes=5))


def test_ingest_writes_immutable_verified_bundle(tmp_path):
    req = request()
    store = ImmutableArtifactStore(tmp_path)
    bundle = ingest_ohlcv(provider=FixtureProvider(), request=req, store=store, run_id="run-1")
    assert bundle.quality_pass is True
    assert bundle.rows == 6
    assert (tmp_path / "run-1/raw/provider_payload.bin").exists()
    assert (tmp_path / "run-1/normalized/ohlcv.csv").exists()
    assert (tmp_path / "run-1/dataset_manifest.json").exists()
    with pytest.raises(FileExistsError):
        ingest_ohlcv(provider=FixtureProvider(), request=req, store=store, run_id="run-1")


def test_rejected_ingest_keeps_raw_evidence_and_never_promotes_normalized(tmp_path):
    req = request()
    data = list(rows())
    del data[1]
    store = ImmutableArtifactStore(tmp_path)
    with pytest.raises(ValueError, match="quality gate"):
        ingest_ohlcv(provider=FixtureProvider(data=data), request=req, store=store, run_id="bad-1")
    assert (tmp_path / "bad-1/raw/provider_payload.bin").exists()
    assert (tmp_path / "bad-1/rejection_manifest.json").exists()
    assert not (tmp_path / "bad-1/normalized/ohlcv.csv").exists()


def test_artifact_store_blocks_path_escape(tmp_path):
    store = ImmutableArtifactStore(tmp_path)
    with pytest.raises(ValueError, match="escapes"):
        store.write_bytes("../escape.bin", b"x")
