from __future__ import annotations

"""Fail-closed terminal evidence validation for the frozen v0.51 experiment.

This module does not fetch market data, score candidates, perform PAPER/LIVE
execution, or relax the preregistered A0/A1 gate.  It only validates whether
prospective evidence is admissible for terminal analysis and routes the final
scientific decision through the already-frozen v0.51 decision function.
"""

from dataclasses import dataclass
import re
from typing import Iterable

import numpy as np
import pandas as pd

from research_bot.overlap_arbitration_v51 import (
    ALLOWED_ASSETS_V51,
    ALLOWED_VENUES_V51,
    BLOCK_DAYS_V51,
    N_BLOCKS_V51,
    PROSPECTIVE_START_V51,
    route_decision_v51,
    validate_venue_block_support_v51,
)

TIMEFRAME_V51 = "4h"
PROSPECTIVE_END_V51 = PROSPECTIVE_START_V51 + pd.Timedelta(days=BLOCK_DAYS_V51 * N_BLOCKS_V51)
MAX_CAPTURE_LAG_MINUTES_V51 = 60.0
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class EvidenceAuditV51:
    rows_total: int
    eligible_rows: int
    ineligible_rows: int
    invalid_rows: int
    eligible_series: int
    eligible_blocks: int
    source_commits: tuple[str, ...]


def _asset(symbol: object) -> str:
    return str(symbol).upper().replace("/", "").replace("-", "").replace("USDT", "")


def _required_columns() -> set[str]:
    return {
        "venue", "symbol", "timeframe", "bar_open_at", "bar_close_at", "first_seen_at",
        "open", "high", "low", "close", "volume", "payload_sha256",
        "prospective_eligible", "source_commit", "collector_version",
    }


def audit_prospective_evidence_v51(frame: pd.DataFrame) -> EvidenceAuditV51:
    """Validate raw persisted evidence without manufacturing missing support.

    Eligible rows must satisfy the frozen clock, venue/asset universe, 4h
    timeframe, non-negative <=60 minute capture lag, finite/valid OHLCV,
    immutable-looking SHA-256 provenance, and a real 40-char Git source commit.
    Ineligible rows are retained but cannot rescue missing prospective support.
    """

    missing = sorted(_required_columns().difference(frame.columns))
    if missing:
        raise ValueError(f"v0.51 evidence missing columns: {missing}")

    x = frame.copy()
    for col in ("bar_open_at", "bar_close_at", "first_seen_at"):
        x[col] = pd.to_datetime(x[col], utc=True, errors="coerce")
    if x[["bar_open_at", "bar_close_at", "first_seen_at"]].isna().any().any():
        raise ValueError("v0.51 evidence contains invalid timestamps")

    x["venue"] = x["venue"].astype(str).str.lower()
    x["symbol"] = x["symbol"].astype(str).str.upper()
    x["timeframe"] = x["timeframe"].astype(str)
    assets = x["symbol"].map(_asset)
    if not set(x["venue"]).issubset(ALLOWED_VENUES_V51):
        raise ValueError("v0.51 evidence contains forbidden venue")
    if not set(assets).issubset(ALLOWED_ASSETS_V51):
        raise ValueError("v0.51 evidence contains forbidden asset")
    if not x["timeframe"].eq(TIMEFRAME_V51).all():
        raise ValueError("v0.51 evidence contains forbidden timeframe")

    if x.duplicated(["venue", "symbol", "timeframe", "bar_open_at"], keep=False).any():
        raise ValueError("v0.51 evidence contains duplicate bar identity")

    numeric_cols = ["open", "high", "low", "close", "volume"]
    for col in numeric_cols:
        x[col] = pd.to_numeric(x[col], errors="coerce")
    vals = x[numeric_cols].to_numpy(dtype=float)
    if not np.isfinite(vals).all():
        raise ValueError("v0.51 evidence contains non-finite OHLCV")
    if (x[["open", "high", "low", "close"]] <= 0).any().any() or (x["volume"] < 0).any():
        raise ValueError("v0.51 evidence contains invalid OHLCV sign")
    if (x["high"] < x[["open", "low", "close"]].max(axis=1)).any():
        raise ValueError("v0.51 evidence contains invalid high")
    if (x["low"] > x[["open", "high", "close"]].min(axis=1)).any():
        raise ValueError("v0.51 evidence contains invalid low")

    if not x["payload_sha256"].astype(str).str.fullmatch(SHA256_RE).all():
        raise ValueError("v0.51 evidence contains invalid payload SHA-256")
    if not x["source_commit"].astype(str).str.fullmatch(SHA40_RE).all():
        raise ValueError("v0.51 evidence contains invalid source commit provenance")
    if x["collector_version"].isna().any() or x["collector_version"].astype(str).str.len().eq(0).any():
        raise ValueError("v0.51 evidence contains missing collector version")

    if x["prospective_eligible"].isna().any() or not x["prospective_eligible"].map(type).eq(bool).all():
        raise ValueError("v0.51 prospective_eligible must be boolean")

    lag = (x["first_seen_at"] - x["bar_close_at"]).dt.total_seconds() / 60.0
    eligible = x["prospective_eligible"]
    eligibility_truth = (
        (x["bar_close_at"] >= PROSPECTIVE_START_V51)
        & (x["bar_close_at"] < PROSPECTIVE_END_V51)
        & (lag >= 0.0)
        & (lag <= MAX_CAPTURE_LAG_MINUTES_V51)
    )
    if (eligible != eligibility_truth).any():
        raise ValueError("v0.51 persisted eligibility contradicts frozen first-seen rule")

    valid = pd.Series(True, index=x.index)
    invalid_rows = int((~valid).sum())
    eligible_x = x.loc[eligible].copy()
    if not eligible_x.empty:
        elapsed = (eligible_x["bar_close_at"] - PROSPECTIVE_START_V51).dt.total_seconds() / 86_400.0
        eligible_x["block"] = (elapsed // BLOCK_DAYS_V51).astype(int) + 1
        eligible_x["series"] = eligible_x["venue"] + ":" + eligible_x["symbol"]
        blocks = int(eligible_x["block"].nunique())
        series = int(eligible_x["series"].nunique())
    else:
        blocks = 0
        series = 0

    commits = tuple(sorted(set(x["source_commit"].astype(str))))
    return EvidenceAuditV51(
        rows_total=int(len(x)),
        eligible_rows=int(eligible.sum()),
        ineligible_rows=int((~eligible).sum()),
        invalid_rows=invalid_rows,
        eligible_series=series,
        eligible_blocks=blocks,
        source_commits=commits,
    )


def terminal_route_v51(
    *,
    evidence: pd.DataFrame,
    venue_block_support: pd.DataFrame,
    conflict_counts: list[int],
    expectancy_deltas: list[float],
    aggregate_expectancy_a0: float,
    aggregate_expectancy_a1: float,
    profit_factor_a0: float,
    profit_factor_a1: float,
    stress_profit_factor_a0: float,
    stress_profit_factor_a1: float,
    worst_drawdown_a1: float,
    causal_checks_passed: bool,
) -> tuple[str, EvidenceAuditV51]:
    """Return a terminal scientific route while remaining fail closed.

    Missing venue/block evidence or malformed provenance never falls through to
    economic interpretation. Passing can only mean the preregistered
    arbitration advancement criterion was supported; it never authorizes
    PAPER/LIVE trading.
    """

    audit = audit_prospective_evidence_v51(evidence)
    if audit.eligible_rows == 0 or audit.eligible_series < 15 or audit.eligible_blocks < N_BLOCKS_V51:
        return "V51_INCOMPLETE_EVIDENCE", audit

    try:
        validate_venue_block_support_v51(venue_block_support)
    except ValueError:
        return "V51_INCOMPLETE_EVIDENCE", audit

    # The support matrix and raw ledger must agree. A complete-looking support
    # matrix must not allow economic routing when raw first-seen evidence is
    # absent for any frozen venue × 30-day block cell.
    eligible = evidence.loc[evidence["prospective_eligible"]].copy()
    elapsed_days = (
        pd.to_datetime(eligible["bar_close_at"], utc=True) - PROSPECTIVE_START_V51
    ).dt.total_seconds() / 86_400.0
    eligible["prospective_block_v51"] = (elapsed_days // BLOCK_DAYS_V51).astype(int) + 1
    observed_cells = set(zip(
        eligible["venue"].astype(str).str.lower(),
        eligible["prospective_block_v51"].astype(int),
    ))
    expected_cells = {
        (venue, block)
        for venue in ALLOWED_VENUES_V51
        for block in range(1, N_BLOCKS_V51 + 1)
    }
    if observed_cells != expected_cells:
        return "V51_INCOMPLETE_EVIDENCE", audit

    decision = route_decision_v51(
        conflict_counts=conflict_counts,
        expectancy_deltas=expectancy_deltas,
        aggregate_expectancy_a0=aggregate_expectancy_a0,
        aggregate_expectancy_a1=aggregate_expectancy_a1,
        profit_factor_a0=profit_factor_a0,
        profit_factor_a1=profit_factor_a1,
        stress_profit_factor_a0=stress_profit_factor_a0,
        stress_profit_factor_a1=stress_profit_factor_a1,
        worst_drawdown_a1=worst_drawdown_a1,
        causal_checks_passed=causal_checks_passed,
    )
    return decision, audit
