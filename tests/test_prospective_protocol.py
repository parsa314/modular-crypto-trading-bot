from datetime import datetime, timedelta, timezone

import pytest

from research_bot.prospective_protocol import ProspectiveWindow, TERMINAL, continuity_status, register_window


START = datetime(2026, 10, 6, tzinfo=timezone.utc)


def window():
    return ProspectiveWindow("V25B_DATA_QUALITY_20261006", START, START + timedelta(days=7),
                             "876e229a8d3e73f2e9d646d4126c8ce08e4c11b4")


def test_registration_precedes_boundary_and_is_write_once(tmp_path):
    path = tmp_path / "registration.json"
    r = register_window(window(), path, registered_at=START - timedelta(days=1))
    assert r["collection_active"] is False
    assert r["economic_metrics"] is None
    with pytest.raises(FileExistsError):
        register_window(window(), path, registered_at=START - timedelta(days=1))
    with pytest.raises(ValueError, match="precede"):
        register_window(window(), tmp_path / "late.json", registered_at=START)


def test_bootstrap_gap_cannot_hide_missing_first_days():
    r = continuity_status(window(), current_at=START + timedelta(days=2), previous_at=None)
    assert r["status"] == TERMINAL
    assert r["collection_allowed"] is False


def test_terminal_continuity_failure_cannot_be_rehabilitated():
    r = continuity_status(window(), current_at=START + timedelta(hours=8),
                          previous_at=START + timedelta(hours=7), previous_status=TERMINAL)
    assert r["status"] == TERMINAL
    assert r["backfill_authorized"] is False


def test_exact_frozen_gap_boundary_is_allowed():
    r = continuity_status(window(), current_at=START + timedelta(hours=5.5), previous_at=START)
    assert r["collection_allowed"] is True
    assert r["execution_authorized"] is False


def test_gap_above_threshold_blocks():
    r = continuity_status(window(), current_at=START + timedelta(hours=5.5, microseconds=1), previous_at=START)
    assert r["status"] == TERMINAL


def test_previous_link_from_other_window_cannot_be_imported():
    with pytest.raises(ValueError):
        continuity_status(window(), current_at=START + timedelta(hours=1), previous_at=START - timedelta(days=1))


def test_placeholder_source_sha_is_rejected():
    with pytest.raises(ValueError):
        ProspectiveWindow("V25B_OTHER", START, START + timedelta(days=7), "0" * 40)
