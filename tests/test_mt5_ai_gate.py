from __future__ import annotations

import numpy as np
import pandas as pd

from research_bot.mt5_ai_gate import (
    MT5AIGateConfig,
    evaluate_ai_confirmation,
)


def synthetic(n=700, seed=314):
    rng = np.random.default_rng(seed)
    ret = rng.normal(0.0002, 0.009, n)
    close = 100.0 * np.exp(np.cumsum(ret))
    open_ = np.r_[close[0], close[:-1]]
    intrabar = np.maximum(0.002 * close, np.abs(rng.normal(0, 0.004, n)) * close)
    high = np.maximum(open_, close) + intrabar
    low = np.minimum(open_, close) - intrabar
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.lognormal(8.0, 0.5, n),
        }
    )


def test_ai_gate_fails_closed_with_insufficient_history():
    result = evaluate_ai_confirmation(
        synthetic(250),
        direction=1,
        config=MT5AIGateConfig(min_history=300),
    )
    assert result.approved is False
    assert result.reason == "INSUFFICIENT_AI_HISTORY"
    assert result.probability_up is None


def test_ai_gate_produces_chronological_validation_evidence():
    result = evaluate_ai_confirmation(
        synthetic(700),
        direction=1,
        config=MT5AIGateConfig(
            min_history=360,
            max_validation_brier=0.50,
        ),
    )

    assert result.train_rows >= 359
    assert result.validation_rows >= 60
    assert result.feature_count > 20
    assert result.model_name == "HGB_DEMO_CONFIRM_V1"
    assert result.reason in {"AI_CONFIRMS_LONG", "AI_REJECTS_LONG"}
    assert result.probability_up is not None
    assert 0.0 <= result.probability_up <= 1.0
    assert result.validation_brier is not None
    assert 0.0 <= result.validation_brier <= 0.50


def test_ai_gate_short_uses_lower_probability_threshold():
    result = evaluate_ai_confirmation(
        synthetic(700, seed=99),
        direction=-1,
        config=MT5AIGateConfig(
            min_history=360,
            max_validation_brier=0.50,
        ),
    )

    assert result.reason in {"AI_CONFIRMS_SHORT", "AI_REJECTS_SHORT"}
    assert result.probability_up is not None
    if result.approved:
        assert result.probability_up <= 0.44
    else:
        assert result.probability_up > 0.44
