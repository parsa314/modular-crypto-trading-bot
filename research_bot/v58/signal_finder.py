"""Unified V58 research signal finder.

Combines the ten pre-registered Ichimoku/ICT/SMC/Brooks hypotheses with the
supplied multi-timeframe FVG/ICT/TSI family. This layer discovers candidates
only; AI/economic/financial gates remain downstream and execution stays off.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import json
import math

import pandas as pd

from .confluence10 import evaluate_confluence10, registry as confluence10_registry
from .events import stable_hash
from .features import add_v58_continuous_features
from .fvg_ict_tsi import (
    FVGICTTSIConfig,
    _rule_delta,
    atr,
    normalize_ohlcv,
    resample_ohlcv,
    scan_signals as scan_fvg_ict_tsi,
)


@dataclass(frozen=True)
class CombinedSignalConfig:
    htf: str = "1h"
    ltf: str = "5min"
    confirmation: str = "either"
    stop_mode: str = "zone_edge"
    reward_risk: float = 2.0
    include_confluence10: bool = True
    include_fvg_ict_tsi: bool = True

    def to_fvg(self) -> FVGICTTSIConfig:
        return FVGICTTSIConfig(
            htf=self.htf,
            ltf=self.ltf,
            confirmation=self.confirmation,  # type: ignore[arg-type]
            stop_mode=self.stop_mode,  # type: ignore[arg-type]
            reward_risk=self.reward_risk,
        )


def _v58_timeframe(rule: str) -> str:
    delta = _rule_delta(rule)
    minutes = int(delta / pd.Timedelta(minutes=1))
    if minutes % (24 * 60) == 0:
        return f"{minutes // (24 * 60)}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def _confluence_signals(
    raw: pd.DataFrame,
    *,
    symbol: str,
    venue: str,
    ltf_rule: str,
) -> pd.DataFrame:
    base = normalize_ohlcv(raw)
    ltf = resample_ohlcv(base, ltf_rule)
    if len(ltf) < 210:
        return pd.DataFrame()
    timeframe = _v58_timeframe(ltf_rule)
    feature_input = ltf.reset_index()
    features = add_v58_continuous_features(feature_input, timeframe=timeframe)
    decision_atr = atr(ltf, 14)
    duration = _rule_delta(ltf_rule)
    rows: list[dict[str, Any]] = []

    for i in range(200, len(features) - 1):
        signal_open = pd.Timestamp(features["timestamp"].iloc[i])
        decision_time = signal_open + duration
        next_open_time = pd.Timestamp(features["timestamp"].iloc[i + 1])
        if decision_time != next_open_time:
            continue
        hits = evaluate_confluence10(features.iloc[i].to_dict())
        if not hits:
            continue
        a = float(decision_atr.iloc[i]) if i < len(decision_atr) else math.nan
        entry = float(features["open"].iloc[i + 1])
        if not math.isfinite(a) or a <= 0 or not math.isfinite(entry) or entry <= 0:
            continue
        for hit in hits:
            stop = entry - a
            target = entry + 1.5 * a
            signal_id = stable_hash(
                {
                    "family": "V58_CONFLUENCE10",
                    "strategy_id": hit.strategy_id,
                    "symbol": symbol,
                    "venue": venue,
                    "ltf": timeframe,
                    "decision_time": decision_time.isoformat(),
                }
            )
            rows.append(
                {
                    "signal_id": signal_id,
                    "strategy_id": hit.strategy_id,
                    "family": "V58_CONFLUENCE10",
                    "symbol": symbol,
                    "venue": venue,
                    "direction": "long",
                    "htf": None,
                    "ltf": timeframe,
                    "signal_time": decision_time.isoformat(),
                    "entry_time": decision_time.isoformat(),
                    "entry_reference_price": entry,
                    "stop_price": float(stop),
                    "target_price": float(target),
                    "reward_risk": 1.5,
                    "confirmation_mode": "FOUR_FRAMEWORK_RULE",
                    "stop_mode": "COMMON_V58_BARRIER",
                    "confirmations": list(hit.states),
                    "ai_gate_required": True,
                    "economic_gate_required": True,
                    "financial_gate_required": True,
                    "portfolio_execution_eligible": True,
                    "execution_note": "ELIGIBLE_FOR_V58_SPOT_FINANCIAL_GATE",
                }
            )
    return pd.DataFrame(rows)


def scan_combined_signals(
    raw: pd.DataFrame,
    config: CombinedSignalConfig | None = None,
    *,
    symbol: str,
    venue: str = "research_csv",
) -> dict[str, Any]:
    cfg = CombinedSignalConfig() if config is None else config
    if not isinstance(cfg, CombinedSignalConfig):
        raise ValueError("config must be CombinedSignalConfig")
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be a nonempty string")
    base = normalize_ohlcv(raw)

    fvg_result: dict[str, Any] | None = None
    fvg_frame = pd.DataFrame()
    if cfg.include_fvg_ict_tsi:
        fvg_result = scan_fvg_ict_tsi(base, cfg.to_fvg(), symbol=symbol, venue=venue)
        fvg_frame = fvg_result["signals"].copy()
        if not fvg_frame.empty:
            fvg_frame["family"] = "FVG_ICT_TSI_MTF"
            fvg_frame["confirmations"] = fvg_frame.apply(
                lambda row: [
                    name
                    for name, passed in (
                        ("TSI_CROSS", bool(row["tsi_cross"])),
                        ("STRUCTURE_BREAK", bool(row["structure_break"])),
                        ("MIDPOINT_REJECTION", bool(row["midpoint_rejection"])),
                    )
                    if passed
                ],
                axis=1,
            )

    confluence_frame = pd.DataFrame()
    if cfg.include_confluence10:
        confluence_frame = _confluence_signals(
            base, symbol=symbol, venue=venue, ltf_rule=cfg.ltf
        )

    frames = [frame for frame in (confluence_frame, fvg_frame) if not frame.empty]
    combined = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    if not combined.empty:
        if combined["signal_id"].duplicated().any():
            raise RuntimeError("duplicate combined signal identity")
        combined = combined.sort_values(
            ["signal_time", "strategy_id", "signal_id"], kind="mergesort"
        ).reset_index(drop=True)

    registry = {
        "confluence10": confluence10_registry(),
        "fvg_ict_tsi": {
            "strategy_id": "FVG_ICT_TSI_MTF",
            "source": "USER_SUPPLIED_FVG_ICT_TSI_STRATEGY",
            "htf": cfg.htf,
            "ltf": cfg.ltf,
            "confirmation": cfg.confirmation,
            "stop_mode": cfg.stop_mode,
            "reward_risk": cfg.reward_risk,
            "financial_gate_required": True,
            "paper_execution": False,
            "live_execution": False,
        },
    }
    summary = {
        "classification": "V58_RESEARCH_SIGNAL_DISCOVERY",
        "symbol": symbol,
        "venue": venue,
        "config": asdict(cfg),
        "confluence10_count": int(len(confluence_frame)),
        "fvg_ict_tsi_count": int(len(fvg_frame)),
        "combined_count": int(len(combined)),
        "long_count": int((combined.get("direction", pd.Series(dtype=str)) == "long").sum()) if not combined.empty else 0,
        "short_count": int((combined.get("direction", pd.Series(dtype=str)) == "short").sum()) if not combined.empty else 0,
        "financial_gate_required": True,
        "ai_gate_required": True,
        "paper_execution": False,
        "live_execution": False,
        "registry_sha256": stable_hash(registry),
    }
    return {
        "summary": summary,
        "registry": registry,
        "combined": combined,
        "confluence10": confluence_frame,
        "fvg_ict_tsi": fvg_frame,
        "fvgs": pd.DataFrame() if fvg_result is None else fvg_result["fvgs"],
    }


def write_signal_scan(result: dict[str, Any], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    result["combined"].to_csv(output / "combined_signals.csv", index=False)
    result["confluence10"].to_csv(output / "confluence10_signals.csv", index=False)
    result["fvg_ict_tsi"].to_csv(output / "fvg_ict_tsi_signals.csv", index=False)
    result["fvgs"].to_csv(output / "detected_fvgs.csv", index=False)
    (output / "signal_registry.json").write_text(
        json.dumps(result["registry"], sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (output / "signal_scan_summary.json").write_text(
        json.dumps(result["summary"], sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
