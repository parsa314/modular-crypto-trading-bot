from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np


@dataclass(frozen=True)
class V54PromotionPolicy:
    min_completed_symbols: int = 3
    min_positive_increment_symbols: int = 3
    min_positive_paired_mean_symbols: int = 3
    base_cost_key: str = "cost_24"

    def __post_init__(self) -> None:
        if self.min_completed_symbols < 1:
            raise ValueError("min_completed_symbols must be positive")
        if self.min_positive_increment_symbols < 1:
            raise ValueError("min_positive_increment_symbols must be positive")
        if self.min_positive_paired_mean_symbols < 1:
            raise ValueError("min_positive_paired_mean_symbols must be positive")


def summarize_family_evidence_v54(
    per_symbol: dict[str, dict],
    policy: V54PromotionPolicy | None = None,
) -> dict[str, dict]:
    """Aggregate symbol-level feature-family evidence without authorizing trading.

    `promotion_candidate` preserves the preregistered structural rule based on
    ALL-minus-DROP Sharpe. `paired_return_support` is a stronger diagnostic that
    additionally asks for positive paired OOS mean-return differences across
    symbols. Neither state enables paper/live execution.
    """

    pol = policy or V54PromotionPolicy()
    family_rows: dict[str, list[dict]] = {}
    for symbol, report in sorted(per_symbol.items()):
        for family, values in report.get("family_value", {}).items():
            inference = values.get("paired_net_return_inference") or {}
            mean_diff = inference.get("mean")
            family_rows.setdefault(family, []).append({
                "symbol": symbol,
                "sharpe_increment": float(values.get("all_minus_drop_sharpe", 0.0)),
                "positive_increment": bool(values.get("positive_increment", False)),
                "paired_mean": None if mean_diff is None else float(mean_diff),
                "paired_ci_low": inference.get("ci_low"),
                "paired_ci_high": inference.get("ci_high"),
                "paired_p_nonpositive": inference.get("p_nonpositive"),
            })

    summary: dict[str, dict] = {}
    for family, rows in sorted(family_rows.items()):
        increments = np.asarray([r["sharpe_increment"] for r in rows], dtype=float)
        paired_means = [r["paired_mean"] for r in rows if r["paired_mean"] is not None and np.isfinite(r["paired_mean"])]
        positive_increment_symbols = sum(r["positive_increment"] for r in rows)
        positive_paired_symbols = sum((r["paired_mean"] is not None and r["paired_mean"] > 0) for r in rows)
        completed = len(rows)
        median_increment = float(np.median(increments)) if completed else 0.0
        promotion_candidate = bool(
            completed >= pol.min_completed_symbols
            and positive_increment_symbols >= pol.min_positive_increment_symbols
            and median_increment > 0
        )
        paired_support = bool(
            promotion_candidate
            and positive_paired_symbols >= pol.min_positive_paired_mean_symbols
            and paired_means
            and float(np.median(np.asarray(paired_means, dtype=float))) > 0
        )
        summary[family] = {
            "symbols": completed,
            "rows": rows,
            "positive_increment_symbols": int(positive_increment_symbols),
            "positive_paired_mean_symbols": int(positive_paired_symbols),
            "median_all_minus_drop_sharpe": median_increment,
            "median_paired_net_return_difference": (
                float(np.median(np.asarray(paired_means, dtype=float))) if paired_means else None
            ),
            "promotion_candidate": promotion_candidate,
            "paired_return_support": paired_support,
            "execution_authorized": False,
        }
    return {
        "policy": asdict(pol),
        "families": summary,
        "paper_execution": False,
        "live_execution": False,
    }
