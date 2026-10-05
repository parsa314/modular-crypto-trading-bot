from __future__ import annotations

"""Leakage-aware AI confirmation gate for MT5 DEMO strategy signals.

The gate is intentionally subordinate to deterministic strategy logic. It is
trained only after a closed-bar technical signal exists and only on targets
whose next-bar outcome is already observable at the decision timestamp.

This is a DEMO/forward-validation component, not a promotion of a learned model
to real-money execution.
"""

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import brier_score_loss
from sklearn.pipeline import Pipeline

from .features import FEATURE_COLUMNS, add_features
from .regime import REGIME_COLUMNS, add_regime_features


@dataclass(frozen=True)
class MT5AIGateConfig:
    min_history: int = 360
    validation_fraction: float = 0.25
    hurdle_bps: float = 24.0
    long_threshold: float = 0.56
    short_threshold: float = 0.44
    max_validation_brier: float = 0.28
    random_state: int = 314

    def __post_init__(self) -> None:
        if self.min_history < 300:
            raise ValueError("min_history must be >= 300")
        if not 0.15 <= self.validation_fraction <= 0.40:
            raise ValueError("validation_fraction must be in [0.15, 0.40]")
        if not math.isfinite(self.hurdle_bps) or self.hurdle_bps < 0:
            raise ValueError("hurdle_bps must be finite and non-negative")
        if not 0.50 < self.long_threshold < 1.0:
            raise ValueError("long_threshold must be in (0.50, 1)")
        if not 0.0 < self.short_threshold < 0.50:
            raise ValueError("short_threshold must be in (0, 0.50)")
        if self.short_threshold >= self.long_threshold:
            raise ValueError("short_threshold must be below long_threshold")
        if not 0.0 < self.max_validation_brier <= 0.50:
            raise ValueError("max_validation_brier must be in (0, 0.50]")


@dataclass(frozen=True)
class MT5AIGateResult:
    approved: bool
    probability_up: float | None
    confidence: float
    validation_brier: float | None
    train_rows: int
    validation_rows: int
    feature_count: int
    model_name: str
    reason: str


def _model(random_state: int) -> Pipeline:
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            (
                "model",
                HistGradientBoostingClassifier(
                    learning_rate=0.05,
                    max_iter=180,
                    max_leaf_nodes=15,
                    l2_regularization=0.5,
                    random_state=random_state,
                ),
            ),
        ]
    )


def evaluate_ai_confirmation(
    frame: pd.DataFrame,
    *,
    direction: int,
    config: MT5AIGateConfig | None = None,
) -> MT5AIGateResult:
    """Train chronologically and confirm one latest closed-bar direction.

    The latest row is never part of training because its next-bar target is
    unknown. The last *training* row may use the latest closed bar as its known
    next-bar outcome, which is causal at the decision timestamp.
    """

    cfg = config or MT5AIGateConfig()
    if direction not in {-1, 1}:
        raise ValueError("direction must be -1 or +1")
    if len(frame) < cfg.min_history:
        return MT5AIGateResult(
            approved=False,
            probability_up=None,
            confidence=0.0,
            validation_brier=None,
            train_rows=0,
            validation_rows=0,
            feature_count=0,
            model_name="HGB_DEMO_CONFIRM_V1",
            reason="INSUFFICIENT_AI_HISTORY",
        )

    x = add_regime_features(add_features(frame.copy()))
    hurdle = cfg.hurdle_bps / 10_000.0
    x["future_return"] = x["close"].shift(-1) / x["close"] - 1.0
    x["target_up"] = (x["future_return"] > hurdle).astype(float)

    cols = [c for c in FEATURE_COLUMNS + REGIME_COLUMNS if c in x.columns]
    if not cols:
        return MT5AIGateResult(
            approved=False,
            probability_up=None,
            confidence=0.0,
            validation_brier=None,
            train_rows=0,
            validation_rows=0,
            feature_count=0,
            model_name="HGB_DEMO_CONFIRM_V1",
            reason="NO_AI_FEATURES",
        )

    # Latest row has no observable next-bar outcome and is prediction-only.
    latest = x.iloc[[-1]].copy()
    labelled = x.iloc[:-1].dropna(subset=["future_return"]).copy()
    if len(labelled) < cfg.min_history - 1:
        return MT5AIGateResult(
            approved=False,
            probability_up=None,
            confidence=0.0,
            validation_brier=None,
            train_rows=0,
            validation_rows=0,
            feature_count=len(cols),
            model_name="HGB_DEMO_CONFIRM_V1",
            reason="INSUFFICIENT_LABELLED_AI_HISTORY",
        )

    split = int(len(labelled) * (1.0 - cfg.validation_fraction))
    train = labelled.iloc[:split]
    validation = labelled.iloc[split:]
    if len(train) < 220 or len(validation) < 60:
        return MT5AIGateResult(
            approved=False,
            probability_up=None,
            confidence=0.0,
            validation_brier=None,
            train_rows=len(train),
            validation_rows=len(validation),
            feature_count=len(cols),
            model_name="HGB_DEMO_CONFIRM_V1",
            reason="INSUFFICIENT_CHRONOLOGICAL_SPLIT",
        )

    y_train = train["target_up"].astype(int)
    y_val = validation["target_up"].astype(int)
    if y_train.nunique() < 2 or y_val.nunique() < 2:
        return MT5AIGateResult(
            approved=False,
            probability_up=None,
            confidence=0.0,
            validation_brier=None,
            train_rows=len(train),
            validation_rows=len(validation),
            feature_count=len(cols),
            model_name="HGB_DEMO_CONFIRM_V1",
            reason="AI_TARGET_CLASS_COLLAPSE",
        )

    validation_model = _model(cfg.random_state)
    validation_model.fit(train[cols], y_train)
    val_prob = validation_model.predict_proba(validation[cols])[:, 1]
    brier = float(brier_score_loss(y_val.to_numpy(), val_prob))

    if not math.isfinite(brier) or brier > cfg.max_validation_brier:
        return MT5AIGateResult(
            approved=False,
            probability_up=None,
            confidence=0.0,
            validation_brier=brier if math.isfinite(brier) else None,
            train_rows=len(train),
            validation_rows=len(validation),
            feature_count=len(cols),
            model_name="HGB_DEMO_CONFIRM_V1",
            reason="AI_VALIDATION_QUALITY_REJECTED",
        )

    final_model = _model(cfg.random_state)
    final_model.fit(labelled[cols], labelled["target_up"].astype(int))
    probability_up = float(final_model.predict_proba(latest[cols])[:, 1][0])
    confidence = float(np.clip(2.0 * abs(probability_up - 0.5), 0.0, 1.0))

    if direction > 0:
        approved = probability_up >= cfg.long_threshold
        reason = "AI_CONFIRMS_LONG" if approved else "AI_REJECTS_LONG"
    else:
        approved = probability_up <= cfg.short_threshold
        reason = "AI_CONFIRMS_SHORT" if approved else "AI_REJECTS_SHORT"

    return MT5AIGateResult(
        approved=bool(approved),
        probability_up=probability_up,
        confidence=confidence,
        validation_brier=brier,
        train_rows=len(labelled),
        validation_rows=len(validation),
        feature_count=len(cols),
        model_name="HGB_DEMO_CONFIRM_V1",
        reason=reason,
    )
