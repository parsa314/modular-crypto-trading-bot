from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from .candle_time_v53 import CandleTimeContractV53, annotate_candle_times_v53
from .multiframework_features_v53 import build_v53_feature_frame
from .point_in_time import point_in_time_asof_join, assert_no_future_availability
from .zone_lifecycle_v53 import add_zone_lifecycle_v53


def add_ichimoku_visibility_v53(frame: pd.DataFrame) -> pd.DataFrame:
    """Separate cloud visible now from the cloud projected 26 bars forward.

    ``ichi_span_*_now`` are values computable only when bar t closes and are
    conventionally plotted 26 periods ahead. The cloud visible at decision t is
    therefore sourced from t-26. No future shift is used.
    """

    required = {"close", "ichi_span_a_now", "ichi_span_b_now"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing Ichimoku inputs: {sorted(missing)}")
    x = frame.copy()
    x["ichi_span_a_projected_t_plus_26"] = x["ichi_span_a_now"]
    x["ichi_span_b_projected_t_plus_26"] = x["ichi_span_b_now"]
    x["ichi_span_a_visible_now"] = x["ichi_span_a_now"].shift(26)
    x["ichi_span_b_visible_now"] = x["ichi_span_b_now"].shift(26)
    visible_top = x[["ichi_span_a_visible_now", "ichi_span_b_visible_now"]].max(axis=1)
    visible_bottom = x[["ichi_span_a_visible_now", "ichi_span_b_visible_now"]].min(axis=1)
    projected_top = x[["ichi_span_a_projected_t_plus_26", "ichi_span_b_projected_t_plus_26"]].max(axis=1)
    projected_bottom = x[["ichi_span_a_projected_t_plus_26", "ichi_span_b_projected_t_plus_26"]].min(axis=1)
    x["ichi_price_above_visible_cloud"] = (x["close"] > visible_top).where(visible_top.notna()).astype("float")
    x["ichi_price_below_visible_cloud"] = (x["close"] < visible_bottom).where(visible_bottom.notna()).astype("float")
    x["ichi_visible_cloud_bullish"] = (
        x["ichi_span_a_visible_now"] > x["ichi_span_b_visible_now"]
    ).where(x["ichi_span_a_visible_now"].notna() & x["ichi_span_b_visible_now"].notna()).astype("float")
    x["ichi_projected_cloud_bullish"] = (
        x["ichi_span_a_projected_t_plus_26"] > x["ichi_span_b_projected_t_plus_26"]
    ).where(x["ichi_span_a_projected_t_plus_26"].notna() & x["ichi_span_b_projected_t_plus_26"].notna()).astype("float")
    x["ichi_visible_cloud_width_pct"] = (visible_top - visible_bottom) / x["close"]
    x["ichi_projected_cloud_width_pct"] = (projected_top - projected_bottom) / x["close"]
    return x


def build_single_timeframe_v53(
    raw: pd.DataFrame,
    *,
    timeframe: str,
    publication_lag: pd.Timedelta = pd.Timedelta(0),
) -> pd.DataFrame:
    base = build_v53_feature_frame(raw)
    base = add_zone_lifecycle_v53(base)
    base = add_ichimoku_visibility_v53(base)
    timed = annotate_candle_times_v53(
        base,
        contract=CandleTimeContractV53(timeframe=timeframe, publication_lag=publication_lag),
    )
    timed["timeframe"] = timeframe
    return timed


def build_multitimeframe_feature_frame_v53(
    frames: Mapping[str, pd.DataFrame],
    *,
    decision_timeframe: str,
    publication_lags: Mapping[str, pd.Timedelta] | None = None,
) -> pd.DataFrame:
    """Build a point-in-time multi-timeframe decision frame.

    Raw ``timestamp`` remains the bar-open timestamp for provenance. Features of
    the decision bar become legal only at its ``available_at``. Therefore the
    canonical decision clock is ``decision_at`` and *all* higher-timeframe joins
    are performed against that clock. This avoids using a just-closed bar at its
    opening timestamp.
    """

    if decision_timeframe not in frames:
        raise ValueError("decision timeframe missing from frames")
    lags = dict(publication_lags or {})
    built: dict[str, pd.DataFrame] = {}
    for timeframe, raw in frames.items():
        built[timeframe] = build_single_timeframe_v53(
            raw,
            timeframe=timeframe,
            publication_lag=lags.get(timeframe, pd.Timedelta(0)),
        )

    decision = built[decision_timeframe].copy()
    if "asset" not in decision.columns:
        decision["asset"] = "UNKNOWN"
    decision_key = f"{decision_timeframe}_available_at"
    decision = decision.rename(columns={"available_at": decision_key})
    decision["decision_at"] = pd.to_datetime(decision[decision_key], utc=True, errors="raise")
    if (decision["decision_at"] < pd.to_datetime(decision["bar_close_at"], utc=True)).any():
        raise ValueError("decision_at precedes decision-bar close")

    protected = {
        "timestamp", "bar_open_at", "bar_close_at", "available_at",
        "decision_at", "asset", "timeframe",
    }

    for timeframe, high in built.items():
        if timeframe == decision_timeframe:
            continue
        h = high.copy()
        if "asset" not in h.columns:
            h["asset"] = "UNKNOWN"
        feature_cols = [
            c for c in h.columns
            if c not in protected and c not in {"open", "high", "low", "close", "volume"}
        ]
        availability_key = f"{timeframe}_available_at"
        payload = h[["asset", "available_at"] + feature_cols].copy().rename(
            columns={"available_at": availability_key}
        )
        decision = point_in_time_asof_join(
            decision,
            payload,
            decision_time_col="decision_at",
            available_time_col=availability_key,
            by="asset",
            feature_prefix=f"{timeframe}_",
        )
        assert_no_future_availability(
            decision,
            decision_time_col="decision_at",
            available_time_col=availability_key,
        )

    decision = decision.sort_values("decision_at", kind="mergesort").reset_index(drop=True)
    if decision["decision_at"].duplicated().any():
        raise ValueError("duplicate decision_at timestamps")
    return decision
