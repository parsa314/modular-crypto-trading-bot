import pandas as pd

from research_bot.common_anchor_v21 import (
    V21CommonAnchorConfig,
    aggregate_common_anchor_symbol,
    build_common_anchor_observation,
    build_common_anchor_snapshot,
    choose_common_anchor,
    validate_common_anchor_observation,
)
from research_bot.integrity_v19 import finalize_payload_hash
from research_bot.phase_q_v21 import (
    PHASE_Q_V21_START_AT,
    evaluate_phase_q_v21,
    protocol_metadata_v21,
    validate_phase_q_v21_snapshot,
)


ANCHOR = pd.Timestamp("2026-09-10T18:10:00Z")


def _trades(anchor=ANCHOR):
    return pd.DataFrame([
        {"timestamp": anchor - pd.Timedelta(seconds=59), "side": "buy", "price": 100, "amount": 1},
        {"timestamp": anchor - pd.Timedelta(seconds=20), "side": "sell", "price": 100, "amount": 1},
        {"timestamp": anchor - pd.Timedelta(seconds=10), "side": "buy", "price": 100, "amount": 2},
        {"timestamp": anchor - pd.Timedelta(seconds=5), "side": "sell", "price": 100, "amount": 1},
        {"timestamp": anchor - pd.Timedelta(seconds=1), "side": "buy", "price": 100, "amount": 1},
    ])


def _obs(venue, *, anchor=ANCHOR, book_age=2, symbol="BTC/USDT", timestamp_source="provider"):
    cfg = V21CommonAnchorConfig()
    return build_common_anchor_observation(
        venue=venue,
        symbol=symbol,
        anchor_at=anchor.isoformat(),
        book_observed_at=(anchor - pd.Timedelta(seconds=book_age)).isoformat(),
        book_response_at=(anchor + pd.Timedelta(seconds=1)).isoformat(),
        book_timestamp_source=timestamp_source,
        bids=[[100.0, 2.0], [99.9, 1.0]],
        asks=[[100.1, 2.0], [100.2, 1.0]],
        trades=_trades(anchor),
        trade_left_boundary_established=True,
        trade_fetch_pages=2,
        source=f"{venue}_test",
        config=cfg,
    )


def test_common_anchor_is_latest_book_timestamp():
    out = choose_common_anchor([
        "2026-09-10T18:00:01Z",
        "2026-09-10T18:00:03Z",
        "2026-09-10T18:00:02Z",
    ])
    assert pd.Timestamp(out) == pd.Timestamp("2026-09-10T18:00:03Z")


def test_two_unique_venues_share_exact_trade_window():
    out = aggregate_common_anchor_symbol([_obs("coinex"), _obs("okx")])
    assert out["feature_authorized"] is True
    assert out["accepted_venues"] == 2
    assert out["accepted_venue_names"] == ["coinex", "okx"]
    assert pd.Timestamp(out["trade_window_end"]) == ANCHOR
    assert pd.Timestamp(out["trade_window_start"]) == ANCHOR - pd.Timedelta(seconds=60)


def test_different_anchor_windows_fail_closed():
    left = _obs("coinex")
    right = _obs("okx", anchor=ANCHOR + pd.Timedelta(seconds=3))
    out = aggregate_common_anchor_symbol([left, right])
    assert out["feature_authorized"] is False
    assert "CROSS_VENUE_COMMON_WINDOW_MISMATCH" in out["quality_flags"]


def test_duplicate_venue_never_manufactures_cross_venue_coverage():
    out = aggregate_common_anchor_symbol([_obs("coinex"), _obs("coinex")])
    assert out["feature_authorized"] is False
    assert out["accepted_venues"] == 1
    assert "DUPLICATE_VENUE_OBSERVATION" in out["quality_flags"]
    assert "INSUFFICIENT_UNIQUE_VENUE_COVERAGE" in out["quality_flags"]


def test_stale_book_and_local_timestamp_fallback_are_rejected():
    stale = _obs("coinex", book_age=21)
    assert "BOOK_TOO_OLD_AT_COMMON_ANCHOR" in validate_common_anchor_observation(stale)
    fallback = _obs("okx", timestamp_source="local_fallback")
    assert "LOCAL_BOOK_TIMESTAMP_FALLBACK_NOT_ALLOWED" in validate_common_anchor_observation(fallback)


def test_left_boundary_not_established_is_rejected():
    cfg = V21CommonAnchorConfig()
    obs = build_common_anchor_observation(
        venue="coinex",
        symbol="BTC/USDT",
        anchor_at=ANCHOR.isoformat(),
        book_observed_at=(ANCHOR - pd.Timedelta(seconds=1)).isoformat(),
        book_response_at=ANCHOR.isoformat(),
        book_timestamp_source="provider",
        bids=[[100, 1]],
        asks=[[100.1, 1]],
        trades=_trades(),
        trade_left_boundary_established=False,
        trade_fetch_pages=12,
        source="test",
        config=cfg,
    )
    assert "TRADE_WINDOW_BOUNDARY_NOT_ESTABLISHED" in validate_common_anchor_observation(obs, cfg)


def _countable_snapshot(generated_at: str):
    cfg = V21CommonAnchorConfig()
    rows = [
        _obs("coinex", symbol="BTC/USDT"), _obs("okx", symbol="BTC/USDT"),
        _obs("coinex", symbol="ETH/USDT"), _obs("okx", symbol="ETH/USDT"),
    ]
    snap = build_common_anchor_snapshot(rows, cfg)
    snap.pop("pre_metadata_sha256", None)
    snap["generated_at"] = generated_at
    snap["provider_failures"] = []
    snap["raw_observation_count"] = len(rows)
    snap["collection_rule"] = "prospective_only_common_anchor_no_backfill_no_signal"
    snap["phase_q_protocol"] = protocol_metadata_v21()
    snap["ci_provenance"] = {
        "event_name": "schedule",
        "ref": "refs/heads/main",
        "sha": "abc123",
        "repository": "parsa314/modular-crypto-trading-bot",
    }
    finalize_payload_hash(snap, "snapshot_sha256")
    return snap


def test_future_snapshot_relative_to_as_of_is_not_countable():
    snap = _countable_snapshot("2026-09-10T19:00:00Z")
    reasons = validate_phase_q_v21_snapshot(snap, as_of="2026-09-10T18:30:00Z")
    assert "FUTURE_SNAPSHOT_RELATIVE_TO_AS_OF" in reasons


def test_phase_q_cannot_be_matured_by_future_or_duplicate_rows():
    first = _countable_snapshot("2026-09-10T18:17:00Z")
    duplicate = dict(first)
    future = _countable_snapshot("2026-09-17T18:17:00Z")
    out = evaluate_phase_q_v21(
        [first, duplicate, future],
        as_of="2026-09-10T18:47:00Z",
    )
    assert out["data_quality_gate_passed"] is False
    assert out["countable_unique_slots"] == 1
    assert out["excluded_snapshot_count"] == 2
    assert out["live_execution_authorized"] is False
