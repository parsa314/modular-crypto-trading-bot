from hashlib import sha256
from io import BytesIO
import json
from urllib.error import HTTPError
from zipfile import ZipFile

import numpy as np
import pandas as pd
import pytest

from research_bot.research import spot_history as history


def _row(stamp, *, unit="ms", close=101.0):
    start = pd.Timestamp(stamp)
    multiplier = 10**6 if unit == "ms" else 10**3
    end = start + pd.Timedelta(hours=1) - pd.Timedelta(multiplier, unit="ns")
    return [start.value // multiplier, 100, max(102, close), min(99, close), close, 10,
            end.value // multiplier, 1000, 25, 5, 500, 0]


def _zip(rows, filename="BTCUSDT-1h-2022-01.zip", **kwargs):
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(filename.replace(".zip", ".csv"), "\n".join(",".join(map(str, row)) for row in rows))
        if kwargs.get("extra_member"):
            archive.writestr("unexpected.csv", "untrusted")
    payload = buffer.getvalue()
    checksum = f"{sha256(payload).hexdigest()}  {filename}\n".encode()
    return payload, checksum


def _parsed(rows):
    payload, _ = _zip(rows)
    return history.parse_archive(payload, "BTCUSDT-1h-2022-01.zip", "1h")


def _assemble(frames, end="2022-01-01T03:00:00Z"):
    return history.assemble_history(frames, start="2022-01-01T00:00:00Z", end=end)


def test_checksum_is_mandatory_and_binds_filename_and_bytes():
    payload, checksum = _zip([_row("2022-01-01T00:00:00Z")])
    name = "BTCUSDT-1h-2022-01.zip"
    assert history.verify_checksum(payload, checksum, name) == sha256(payload).hexdigest()
    for bad_checksum in (b"", checksum.replace(b"2022-01", b"2022-02"), checksum + checksum):
        with pytest.raises(history.DataIntegrityError):
            history.verify_checksum(payload, bad_checksum, name)
    with pytest.raises(history.DataIntegrityError, match="mismatch"):
        history.verify_checksum(payload + b"corrupt", checksum, name)


def test_parser_supports_millisecond_and_microsecond_precision():
    frames = []
    for unit in ("ms", "us"):
        frames.append(_parsed([_row("2022-01-01T00:00:00Z", unit=unit)]))
    assert frames[0].timestamp.equals(frames[1].timestamp)
    assert frames[0].timestamp.iloc[0] == pd.Timestamp("2022-01-01T00:00:00Z")


@pytest.mark.parametrize("modification", ["eleven_columns", "thirteen_columns", "header", "wrong_month", "wrong_close_time", "fractional_timestamp", "extra_member"])
def test_parser_rejects_malformed_or_ambiguous_archives(modification):
    row = _row("2022-01-01T00:00:00Z")
    if modification == "eleven_columns":
        row.pop()
    elif modification == "thirteen_columns":
        row.append(0)
    elif modification == "header":
        row = list(history.COLUMNS)
    elif modification == "wrong_month":
        row = _row("2022-02-01T00:00:00Z")
    elif modification == "wrong_close_time":
        row[6] += 1
    elif modification == "fractional_timestamp":
        row[0] += 0.5
    payload, _ = _zip([row], extra_member=modification == "extra_member")
    with pytest.raises(history.DataIntegrityError):
        history.parse_archive(payload, "BTCUSDT-1h-2022-01.zip", "1h")


def test_missing_hour_is_reported_not_filled():
    frame, quality = _assemble([_parsed([_row("2022-01-01T00:00:00Z"), _row("2022-01-01T02:00:00Z")])])
    assert len(frame) == 2
    assert quality["valid"] is False
    assert quality["missing_bars"] == 1
    assert quality["missing_timestamps"] == ["2022-01-01T01:00:00+00:00"]
    assert quality["first_missing_timestamp"] == quality["last_missing_timestamp"] == quality["missing_timestamps"][0]
    assert quality["imputed_rows"] == quality["clipped_values"] == 0


def test_official_short_maintenance_bar_is_preserved_but_blocks_quality():
    row = _row("2022-01-01T00:00:00Z")
    row[6] -= 1_000
    frame, quality = _assemble([_parsed([row])], end="2022-01-01T01:00:00Z")
    assert len(frame) == 1
    assert quality["status"] == "BLOCKED"
    assert quality["incomplete_bar_rows"] == 1
    assert quality["imputed_rows"] == 0


def test_identical_duplicate_is_counted_but_conflicting_duplicate_rejected():
    rows = [_row("2022-01-01T00:00:00Z"), _row("2022-01-01T01:00:00Z"), _row("2022-01-01T02:00:00Z")]
    frame, quality = _assemble([_parsed(rows + [rows[0]])])
    assert len(frame) == 3
    assert quality["valid"] is True
    assert quality["identical_duplicates_removed"] == 1
    conflicting = rows[0].copy()
    conflicting[7] = 1200  # Even a conflict outside normalized OHLCV must fail.
    with pytest.raises(history.DataIntegrityError, match="conflicting duplicate"):
        _assemble([_parsed(rows + [conflicting])])


def test_bounds_exclude_end_and_report_outside_rows():
    frames = [_parsed([_row(f"2022-01-01T0{hour}:00:00Z") for hour in range(4)])]
    frame, quality = _assemble(frames)
    assert len(frame) == 3
    assert quality["rows_outside_request"] == 1
    assert frame.timestamp.max() == pd.Timestamp("2022-01-01T02:00:00Z")
    assert quality["valid"] is True
    with pytest.raises(ValueError, match="interval-aligned"):
        history.assemble_history(frames, start="2022-01-01T00:30:00Z", end="2022-01-02T00:00:00Z")


@pytest.mark.parametrize(("index", "value", "failure"), [
    (4, np.inf, "nonfinite_rows"), (1, 0, "nonpositive_price_rows"),
    (5, -1, "negative_volume_rows"), (2, 50, "ohlc_violation_rows"),
    (8, 2.5, "invalid_trade_count_rows"), (9, 11, "taker_volume_violation_rows"),
])
def test_invalid_values_fail_quality_without_fabricating_replacements(index, value, failure):
    row = _row("2022-01-01T00:00:00Z")
    row[index] = value
    frame, quality = _assemble([_parsed([row])], end="2022-01-01T01:00:00Z")
    assert quality["valid"] is False
    assert quality[failure] == 1
    assert len(frame) == 1


def test_large_real_price_move_is_retained_and_flagged():
    rows = [_row("2022-01-01T00:00:00Z"), _row("2022-01-01T01:00:00Z", close=50)]
    frame, quality = _assemble([_parsed(rows)], end="2022-01-01T02:00:00Z")
    assert quality["valid"] is True
    assert quality["outlier_flags"] == 1
    assert frame.close.iloc[-1] == 50


def test_collection_and_reloading_bind_sources_quality_and_parquet(tmp_path, monkeypatch):
    pytest.importorskip("pyarrow")
    payload, checksum = _zip([_row("2022-01-01T00:00:00Z"), _row("2022-01-01T01:00:00Z")])
    requests = []
    def fetch(url):
        requests.append(url)
        return checksum if url.endswith(".CHECKSUM") else payload
    monkeypatch.setattr(history, "_download", fetch)
    output, cache = tmp_path / "run", tmp_path / "cache"
    manifest = history.collect_history(start="2022-01-01T00:00:00Z", end="2022-01-01T02:00:00Z", output=output, cache=cache)
    assert manifest["status"] == "PASS"
    assert len(requests) == 2
    frame, loaded = history.load_validated_history(output)
    assert len(frame) == 2
    assert loaded == manifest
    assert (output / "raw" / manifest["files"][0]["filename"]).exists()
    # Reusing the pinned cache does not fetch any mutable remote content.
    monkeypatch.setattr(history, "_download", lambda url: pytest.fail("unexpected download"))
    history.collect_history(start="2022-01-01T00:00:00Z", end="2022-01-01T02:00:00Z", output=tmp_path / "cached", cache=cache)
    with pytest.raises(FileExistsError):
        history.collect_history(start="2022-01-01T00:00:00Z", end="2022-01-01T02:00:00Z", output=output)
    (output / "ohlcv.parquet").write_bytes(b"corrupt")
    with pytest.raises(history.DataIntegrityError, match="dataset checksum"):
        history.load_validated_history(output)


def test_incomplete_data_is_saved_but_loader_refuses_it(tmp_path, monkeypatch):
    pytest.importorskip("pyarrow")
    payload, checksum = _zip([_row("2022-01-01T00:00:00Z")])
    monkeypatch.setattr(history, "_download", lambda url: checksum if url.endswith(".CHECKSUM") else payload)
    manifest = history.collect_history(start="2022-01-01T00:00:00Z", end="2022-01-01T02:00:00Z", output=tmp_path)
    assert manifest["status"] == "BLOCKED"
    assert (tmp_path / "ohlcv.parquet").exists()
    with pytest.raises(history.DataIntegrityError, match="quality gate failed"):
        history.load_validated_history(tmp_path)


def test_corrupt_pinned_cache_fails_without_refreshing_or_changing_evidence(tmp_path, monkeypatch):
    pytest.importorskip("pyarrow")
    cache = tmp_path / "cache"
    cache.mkdir()
    name = "BTCUSDT-1h-2022-01.zip"
    payload, checksum = _zip([_row("2022-01-01T00:00:00Z")])
    (cache / name).write_bytes(payload + b"bad")
    (cache / (name + ".CHECKSUM")).write_bytes(checksum)
    monkeypatch.setattr(history, "_download", lambda url: pytest.fail("unexpected refresh"))
    with pytest.raises(history.DataIntegrityError, match="checksum mismatch"):
        history.collect_history(start="2022-01-01T00:00:00Z", end="2022-01-01T01:00:00Z", output=tmp_path / "failed", cache=cache)
    report = json.loads((tmp_path / "failed" / "quality.json").read_text())
    assert report["valid"] is False
    assert (cache / name).read_bytes() == payload + b"bad"


@pytest.mark.parametrize(("code", "calls"), [(404, 1), (451, 1), (503, 3)])
def test_downloader_has_bounded_retries_and_never_retries_access_denials(monkeypatch, code, calls):
    seen = []
    def request(req, timeout):
        seen.append((req.full_url, timeout))
        raise HTTPError(req.full_url, code, "test error", {}, None)
    monkeypatch.setattr(history, "urlopen", request)
    monkeypatch.setattr(history.time, "sleep", lambda seconds: None)
    with pytest.raises(HTTPError):
        history._download("https://data.binance.vision/test.zip")
    assert len(seen) == calls
    assert all(timeout == 30 for _, timeout in seen)


def test_exact_end_boundary_does_not_download_next_month(tmp_path, monkeypatch):
    pytest.importorskip("pyarrow")
    payload, checksum = _zip([_row("2022-01-31T23:00:00Z")])
    requests = []
    def fetch(url):
        requests.append(url)
        return checksum if url.endswith(".CHECKSUM") else payload
    monkeypatch.setattr(history, "_download", fetch)
    result = history.collect_history(start="2022-01-31T23:00:00Z", end="2022-02-01T00:00:00Z", output=tmp_path)
    assert result["status"] == "PASS"
    assert len(requests) == 2
    assert all("2022-01.zip" in url for url in requests)


@pytest.mark.parametrize("target", ["raw", "quality"])
def test_loader_rejects_source_or_quality_tampering(tmp_path, monkeypatch, target):
    pytest.importorskip("pyarrow")
    payload, checksum = _zip([_row("2022-01-01T00:00:00Z")])
    monkeypatch.setattr(history, "_download", lambda url: checksum if url.endswith(".CHECKSUM") else payload)
    history.collect_history(start="2022-01-01T00:00:00Z", end="2022-01-01T01:00:00Z", output=tmp_path)
    path = tmp_path / ("raw/BTCUSDT-1h-2022-01.zip" if target == "raw" else "quality.json")
    with path.open("ab") as handle:
        handle.write(b" ")
    with pytest.raises(history.DataIntegrityError, match="checksum mismatch"):
        history.load_validated_history(tmp_path)
