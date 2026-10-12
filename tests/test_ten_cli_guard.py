"""Fail-closed input boundary and truthful CLI failure artifacts."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.run_ten_strategies_research import run


@pytest.mark.parametrize("kind, reason", [
    ("missing", "HISTORICAL_INPUT_NOT_FOUND"),
    ("empty", "HISTORICAL_INPUT_EMPTY"),
])
def test_invalid_input_raises_valueerror_before_research_state(tmp_path: Path, kind: str, reason: str):
    source = tmp_path / "missing.csv"
    if kind == "empty":
        source.write_bytes(b"")
    output = tmp_path / "out" / "report.json"
    journal = tmp_path / "out" / "journal.sqlite"
    with pytest.raises(ValueError, match=reason):
        run(source, output, journal, False)
    assert not output.exists()
    assert not output.with_suffix(".protocol.json").exists()
    assert not journal.exists()
    assert not output.with_suffix(".net_returns.csv").exists()


def test_main_exits_nonzero_with_failure_json_without_success_artifacts(tmp_path: Path):
    source = tmp_path / "missing.csv"
    output = tmp_path / "out" / "report.json"
    journal = tmp_path / "out" / "journal.sqlite"
    p = subprocess.run(
        [sys.executable, "-m", "scripts.run_ten_strategies_research",
         "--csv", str(source), "--output", str(output), "--journal", str(journal)],
        capture_output=True, text=True, timeout=30,
    )
    assert p.returncode != 0
    assert not output.exists()
    assert not journal.exists()
    assert not output.with_suffix(".protocol.json").exists()
    failure = output.with_suffix(".failed.json")
    assert failure.is_file()
    row = json.loads(failure.read_text())
    assert row["status"] == "FAILED_DATA_OR_PROTOCOL"
    assert "HISTORICAL_INPUT_NOT_FOUND" in row["reason"]
    assert row["synthetic_fallback"] is False
    assert row["execution_authorized"] is False
