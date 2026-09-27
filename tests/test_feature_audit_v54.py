import numpy as np
import pandas as pd
import pytest

from research_bot.feature_audit_v54 import (
    V54AuditConfig,
    _prepare_panel,
    audit_feature_families_v54,
    expanding_folds_v54,
    feature_families_v54,
)
from research_bot.multitimeframe_v53 import build_multitimeframe_feature_frame_v53


def _raw(n: int, freq: str = "1h", seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    r = rng.normal(0.00015, 0.003, n)
    close = 100 * np.exp(np.cumsum(r))
    open_ = np.r_[close[0], close[:-1]]
    w = close * rng.uniform(0.001, 0.004, n)
    return pd.DataFrame({
        "timestamp": pd.date_range("2025-01-01", periods=n, freq=freq, tz="UTC"),
        "open": open_,
        "high": np.maximum(open_, close) + w,
        "low": np.minimum(open_, close) - w,
        "close": close,
        "volume": rng.lognormal(8.0, 0.3, n),
    })


def _audit_frame(n: int = 1200) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    ts = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    latent = rng.normal(0, 1, n)
    ret = 0.0005 * np.roll(latent, 1) + rng.normal(0, 0.002, n)
    close = 100 * np.exp(np.cumsum(ret))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * 1.002
    low = np.minimum(open_, close) * 0.998
    decision_at = ts + pd.Timedelta(hours=1)
    frame = pd.DataFrame({
        "timestamp": ts,
        "bar_open_at": ts,
        "bar_close_at": decision_at,
        "1h_available_at": decision_at,
        "decision_at": decision_at,
        "open": open_, "high": high, "low": low, "close": close,
        "volume": 1000 + rng.normal(0, 10, n),
        "smc_structure_state": np.sign(latent),
        "smc_bos_bull": (latent > 0.8).astype(float),
        "ict_sweep_bull": (latent > 1.2).astype(float),
        "brooks_always_in": np.sign(latent + rng.normal(0, .2, n)),
        "brooks_market_trend": np.sign(latent),
        "brooks_bull_signal_quality": np.clip((latent + 2) / 4, 0, 1),
        "ichi_tk_bullish": (latent > 0).astype(float),
        "ichi_price_above_visible_cloud": (latent > .2).astype(float),
        "ichi_projected_cloud_bullish": (latent > -.2).astype(float),
        "4h_smc_structure_state": np.sign(pd.Series(latent).rolling(4, min_periods=1).mean()).to_numpy(),
        "4h_brooks_always_in": np.sign(pd.Series(latent).rolling(4, min_periods=1).mean()).to_numpy(),
        "4h_ichi_projected_cloud_bullish": (pd.Series(latent).rolling(4, min_periods=1).mean() > 0).astype(float).to_numpy(),
        "4h_available_at": decision_at - pd.Timedelta(minutes=1),
    })
    return frame


def test_mtf_decision_clock_is_bar_availability_not_bar_open():
    one = _raw(260, "1h", 1)
    four = _raw(100, "4h", 2)
    out = build_multitimeframe_feature_frame_v53({"1h": one, "4h": four}, decision_timeframe="1h")
    assert "decision_at" in out
    assert (out["decision_at"] == out["1h_available_at"]).all()
    assert (out["decision_at"] >= out["bar_close_at"]).all()
    assert (out["4h_available_at"].dropna() <= out.loc[out["4h_available_at"].notna(), "decision_at"]).all()


def test_prepare_panel_rejects_bar_open_as_decision_time():
    frame = _audit_frame(800)
    frame["decision_at"] = frame["timestamp"]
    with pytest.raises(ValueError, match="decision precedes"):
        _prepare_panel(frame, V54AuditConfig(min_train_rows=300, test_rows=100, step_rows=100))


def test_prepare_panel_rejects_future_htf_availability():
    frame = _audit_frame(800)
    frame.loc[500, "4h_available_at"] = frame.loc[500, "decision_at"] + pd.Timedelta(seconds=1)
    with pytest.raises(ValueError, match="future availability"):
        _prepare_panel(frame, V54AuditConfig(min_train_rows=300, test_rows=100, step_rows=100))


def test_walk_forward_folds_have_required_purge():
    cfg = V54AuditConfig(min_train_rows=300, test_rows=100, step_rows=100, purge_bars=2, horizon_bars=1)
    folds = expanding_folds_v54(900, cfg)
    assert len(folds) >= 4
    for tr, te in folds:
        assert tr.stop <= te.start - cfg.purge_bars


def test_feature_family_registry_separates_major_families():
    fam = feature_families_v54(_audit_frame())
    assert {"SMC_ICT", "BROOKS", "ICHIMOKU", "HTF"} <= set(fam)
    assert all(len(fam[name]) >= 2 for name in ("SMC_ICT", "BROOKS", "ICHIMOKU", "HTF"))


def test_v54_audit_runs_all_drop_and_only_ablations_and_cost_stress():
    cfg = V54AuditConfig(min_train_rows=500, test_rows=150, step_rows=150, purge_bars=1)
    out = audit_feature_families_v54(_audit_frame(1300), cfg)
    assert out["paper_execution"] is False
    assert out["live_execution"] is False
    assert out["status"] == "RESEARCH_ONLY_NO_EXECUTION_AUTHORIZATION"
    assert "ALL" in out["variants"]
    for name in ("SMC_ICT", "BROOKS", "ICHIMOKU", "HTF"):
        assert f"ONLY_{name}" in out["variants"]
        assert f"DROP_{name}" in out["variants"]
        assert name in out["family_value"]
    agg = out["variants"]["ALL"]["aggregate"]
    assert {"cost_0", "cost_24", "cost_36"} <= set(agg)
    assert agg["cost_36"]["net_total_return"] <= agg["cost_0"]["net_total_return"]
