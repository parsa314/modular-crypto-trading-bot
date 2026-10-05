import json

import pytest

from research_bot.v59 import native_study as study


def test_trial_registration_is_create_only_and_never_promotes(tmp_path):
    record = study.freeze(tmp_path)
    assert record["config"]["execution_authorized"] is False
    assert record["config"]["final_evaluation"] is False
    with pytest.raises(FileExistsError):
        study.freeze(tmp_path)


def test_changed_code_rejects_run_before_input_read(tmp_path, monkeypatch):
    study.freeze(tmp_path)
    monkeypatch.setattr(study, "source_hashes", lambda: {"changed": "source"})
    with pytest.raises(ValueError, match="changed"):
        study.run([], tmp_path)


@pytest.mark.parametrize("status,symbol,start", [("BLOCKED", "BTCUSDT", study.START),
                                                ("PASS", "UNKNOWN", study.START),
                                                ("PASS", "BTCUSDT", "2026-09-25T00:00:00Z")])
def test_unverified_or_wrong_window_input_is_rejected(tmp_path, status, symbol, start):
    (tmp_path / "manifest.json").write_text(json.dumps({"status": status, "symbol": symbol,
                    "market": "spot", "timeframe": "1h", "start_inclusive": start,
                    "end_exclusive": study.END, "source": "Binance Vision official Spot monthly klines"}))
    with pytest.raises(ValueError):
        study.load_verified_binance_bundle(tmp_path)
