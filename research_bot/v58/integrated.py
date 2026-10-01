"""Connected synthetic financial-ML engineering run; never market evidence."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from importlib.metadata import version
import json
import math
from pathlib import Path
import re

import numpy as np
import pandas as pd

from .contracts import assert_research_only
from .events import stable_hash
from .generators import generate_candidates
from .immutable_io import write_immutable_bundle
from .pipeline import run_synthetic_engineering_pipeline, pipeline_result_files, _source_fingerprint
from .learning import LearningConfig, run_synthetic_walk_forward
from .portfolio import PortfolioConfig, simulate_synthetic_portfolio


FEATURE_NAMES = (
    "returns_1", "ATR_percent", "realized_volatility", "trend_strength",
    "tenkan_kijun_distance_atr", "displacement_body_ratio", "relative_volume",
    "trading_range_position",
)
FEATURE_COLUMNS = tuple("feature_" + ("past_volatility" if name == "realized_volatility" else name)
                        for name in FEATURE_NAMES)


def synthetic_market_fixture(*, bars: int = 1600, seed: int = 58, price: float = 100.0) -> pd.DataFrame:
    """Seeded non-market candles, including quiet and volatile intervals.

    No outcomes are assigned or balanced. All TP/SL/TIMEOUT labels are produced
    by the same frozen barrier resolver used by the engineering pipeline.
    """
    if isinstance(bars, bool) or not isinstance(bars, int) or bars < 300:
        raise ValueError("synthetic fixture requires at least 300 bars")
    if not math.isfinite(price) or price <= 0:
        raise ValueError("synthetic starting price must be positive")
    rng = np.random.default_rng(seed)
    phase = np.arange(bars) % 160
    quiet = phase >= 110
    trend = 0.0007 * np.sin(np.arange(bars) / 80) + 0.0004
    changes = trend + rng.normal(size=bars) * np.where(quiet, 0.00008, 0.004)
    close = price * np.exp(np.cumsum(changes))
    opening = np.r_[price, close[:-1]]
    width = opening * np.where(quiet, 0.00010, 0.0015 + rng.uniform(0, 0.002, bars))
    return pd.DataFrame({
        "timestamp": pd.date_range("2025-01-01", periods=bars, freq="4h", tz="UTC"),
        "open": opening, "high": np.maximum(opening, close) + width,
        "low": np.minimum(opening, close) - width, "close": close,
        "volume": rng.uniform(800, 1200, bars),
    })


def build_synthetic_event_dataset(bars: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, dict, dict]:
    assert_research_only()
    if not bars:
        raise ValueError("at least one synthetic asset is required")
    events, records, pipeline_outputs = [], {}, {}
    excluded_tail = 0
    for symbol, frame in sorted(bars.items()):
        result = run_synthetic_engineering_pipeline(frame, symbol=symbol)
        candidates = {e.event_id: e for e in generate_candidates(frame, venue="synthetic", symbol=symbol)}
        for row in result.records:
            event = candidates[row["event_id"]]
            # Eligibility is based on available path length, never early label
            # resolution. Otherwise fast TP/SL events bias the terminal tail.
            if event.row_index + row["horizon_bars"] >= len(frame):
                excluded_tail += 1
                continue
            if row["outcome"] is None or row["resolved_at"] is None:
                raise ValueError("complete-horizon event has no resolved target")
            snapshot = dict(event.feature_values)
            record = {
                "event_id": row["event_id"], "venue": "synthetic", "symbol": symbol,
                "decision_at": row["event_timestamp"], "label_available_at": row["resolved_at"],
                "label": row["outcome"],
                **{column: snapshot[name] for name, column in zip(FEATURE_NAMES, FEATURE_COLUMNS)},
            }
            events.append(record)
            records[row["event_id"]] = row
        pipeline_outputs[symbol] = result
    if not events:
        raise ValueError("no complete-horizon synthetic learning events")
    data = pd.DataFrame(events).sort_values(["decision_at", "event_id"]).reset_index(drop=True)
    data.attrs["excluded_terminal_events"] = excluded_tail
    return data, records, pipeline_outputs


@dataclass(frozen=True)
class UtilityConfig:
    round_trip_cost_bps: float = 24.0
    timeout_loss_multiple: float = 0.25
    uncertainty_loss_multiple: float = 0.05
    safety_margin: float = 0.0
    max_entropy: float = 0.98
    max_shift_score: float = 8.0
    min_regime_confidence: float = 0.20

    def __post_init__(self) -> None:
        if any(not math.isfinite(float(v)) or isinstance(v, bool) for v in asdict(self).values()):
            raise ValueError("utility parameters must be finite real numbers")
        if min(self.round_trip_cost_bps, self.timeout_loss_multiple,
               self.uncertainty_loss_multiple, self.safety_margin) < 0:
            raise ValueError("utility costs and penalties must be nonnegative")
        if not 0 <= self.max_entropy <= 1 or not 0 <= self.min_regime_confidence <= 1 or self.max_shift_score <= 0:
            raise ValueError("invalid abstention thresholds")


def utility_decision(record: dict, prediction: dict, config: UtilityConfig | None = None) -> dict:
    """Use only pre-outcome entry geometry, probabilities and regime confidence."""
    cfg = UtilityConfig() if config is None else config
    if not isinstance(cfg, UtilityConfig):
        raise ValueError("config must be UtilityConfig")
    cfg.__post_init__()
    if record.get("classification") != "SYNTHETIC_ENGINEERING_ONLY" or record.get("venue") != "synthetic":
        raise ValueError("utility decisions require synthetic engineering records")
    if not record.get("event_id") or prediction.get("event_id") != record["event_id"]:
        raise ValueError("prediction must match the decision event identity")
    clocks = [pd.Timestamp(value) for value in
              (record["event_timestamp"], record["entry_time"], prediction["decision_at"])]
    if any(clock is pd.NaT or clock.tzinfo is None or clock.utcoffset().total_seconds() != 0
           for clock in clocks) or len(set(clocks)) != 1:
        raise ValueError("prediction and entry must share the aware UTC event clock")
    probabilities = prediction["probabilities"]
    values = [float(probabilities[name]) for name in ("TP", "SL", "TIMEOUT")]
    entropy, shift = float(prediction["entropy"]), float(prediction["shift_score"])
    confidence = float(record["regime_confidence"])
    if (any(not math.isfinite(v) or not 0 <= v <= 1 for v in values)
            or not math.isclose(sum(values), 1.0, abs_tol=1e-8)
            or not 0 <= entropy <= 1 or not math.isfinite(shift) or shift < 0
            or not math.isfinite(confidence) or not 0 <= confidence <= 1):
        raise ValueError("invalid probability or uncertainty/regime diagnostics")
    entry, stop, target = (float(record[k]) for k in ("entry_price", "stop_price", "target_price"))
    if not all(math.isfinite(v) for v in (entry, stop, target)) or not 0 < stop < entry < target:
        raise ValueError("invalid long entry geometry")
    risk, reward = (entry - stop) / entry, (target - entry) / entry
    expected = values[0] * reward - values[1] * risk - values[2] * cfg.timeout_loss_multiple * risk
    expected -= cfg.round_trip_cost_bps / 10000 + cfg.uncertainty_loss_multiple * risk * entropy
    reason = "ADMITTED"
    if confidence < cfg.min_regime_confidence:
        reason = "LOW_REGIME_CONFIDENCE"
    elif entropy > cfg.max_entropy:
        reason = "HIGH_ENTROPY"
    elif shift > cfg.max_shift_score:
        reason = "DISTRIBUTION_SHIFT"
    elif expected <= cfg.safety_margin:
        reason = "COST_UTILITY_GATE"
    return {
        "event_id": record["event_id"], "venue": "synthetic", "symbol": record["symbol"],
        "decision_at": record["entry_time"], "entry_price": entry,
        "stop_price": stop, "target_price": target, "horizon_bars": record["horizon_bars"],
        "expected_utility": expected, "admission_reason": reason,
        "entropy": entropy, "shift_score": shift, "regime_confidence": confidence,
    }


def run_synthetic_ai_demo(
    *, output: Path, bars_per_asset: int = 1600, seed: int = 58,
    symbols: tuple[str, ...] = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT"),
) -> dict:
    """Exercise the complete ML-to-shared-capital path on non-market fixtures.

    All outcomes are software checks. Real fitting stays behind the frozen
    data/split/promotion gates. Cost stress never refits or selects a model.
    """
    assert_research_only()
    if (not isinstance(symbols, tuple) or not symbols
            or any(not isinstance(symbol, str) or re.fullmatch(r"[A-Z0-9]{1,24}/[A-Z0-9]{1,24}", symbol) is None
                   for symbol in symbols)
            or len(set(symbols)) != len(symbols)):
        raise ValueError("synthetic asset symbols must be unique canonical BASE/QUOTE pairs")
    bars = {symbol: synthetic_market_fixture(bars=bars_per_asset, seed=seed + index,
                                            price=100.0 * (index + 1))
            for index, symbol in enumerate(symbols)}
    data, records, pipelines = build_synthetic_event_dataset(bars)
    learning = run_synthetic_walk_forward(data, FEATURE_COLUMNS, LearningConfig(random_seed=seed))
    first_test = min(pd.Timestamp(row["decision_at"]) for row in learning["predictions"])
    test_bars = {symbol: frame.loc[frame.timestamp >= first_test].reset_index(drop=True)
                 for symbol, frame in bars.items()}
    files: dict[str, bytes] = {}

    def json_bytes(value: object) -> bytes:
        return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")

    files["learning.json"] = json_bytes(learning)
    files["event_dataset.csv"] = data.to_csv(index=False).encode("utf-8")
    source_hashes = {result.metadata["source_code_sha256"] for result in pipelines.values()}
    if len(source_hashes) != 1:
        raise RuntimeError("source changed during dataset construction")
    for symbol, result in pipelines.items():
        for name, content in pipeline_result_files(result).items():
            files[f"events/{symbol.replace('/', '_')}/{name}"] = content
    stress = []
    for cost_bps in (0.0, 24.0, 36.0, 50.0):
        cfg = replace(UtilityConfig(), round_trip_cost_bps=cost_bps)
        decisions = [utility_decision(records[prediction["event_id"]], prediction, cfg)
                     for prediction in learning["predictions"]]
        baseline = [{**decision, "expected_utility": 0.0, "policy": "STRATEGY_ONLY",
                     "admission_reason": "ADMITTED"} for decision in decisions]
        portfolio_cfg = PortfolioConfig(round_trip_cost_bps=cost_bps)
        ml = simulate_synthetic_portfolio(test_bars, decisions, portfolio_cfg)
        strategy = simulate_synthetic_portfolio(test_bars, baseline, portfolio_cfg)
        files[f"portfolio_ml_{int(cost_bps)}bps.json"] = json_bytes(ml)
        files[f"portfolio_strategy_{int(cost_bps)}bps.json"] = json_bytes(strategy)
        stress.append({"round_trip_cost_bps": cost_bps, "ml": ml["summary"],
                       "strategy_only": strategy["summary"],
                       "no_trade": {"net_return": 0.0, "max_drawdown": 0.0, "total_costs": 0.0}})
    summary = {
        "classification": "SYNTHETIC_ENGINEERING_ONLY",
        "model_training_on_synthetic": True, "empirical_training": False,
        "paper_execution": False, "live_execution": False, "promotion_authorized": False,
        "seed": seed, "bars_per_asset": bars_per_asset, "symbols": list(symbols),
        "learning_event_count": len(data), "excluded_terminal_events": data.attrs["excluded_terminal_events"],
        "class_counts": {str(label): int(count) for label, count in data.label.value_counts().items()},
        "fold_count": learning["fold_count"], "test_event_count": len(learning["predictions"]),
        "test_start": first_test.isoformat(), "source_code_sha256": next(iter(source_hashes)),
        "dataset_sha256": sha256(files["event_dataset.csv"]).hexdigest(),
        "source_base_revision": "455014d2b146165b7edc13cf7bc9c61489447948",
        "dependencies": {name: version(name) for name in ("numpy", "pandas", "scikit-learn", "scipy")},
        "utility_config": asdict(UtilityConfig()), "portfolio_config": asdict(PortfolioConfig()),
        "cost_stress": stress,
        "limitations": ["NON_MARKET_FIXTURES", "NO_EMPIRICAL_MODEL_TRAINING",
                        "NO_PROFITABILITY_OR_PROMOTION_EVIDENCE", "NO_MICROSTRUCTURE_OR_MARKET_IMPACT_DATA",
                        "ENTROPY_AND_SHIFT_ARE_DIAGNOSTICS_NOT_CERTIFIED_UNCERTAINTY"],
    }
    files["summary.json"] = json_bytes(summary)
    if _source_fingerprint() != summary["source_code_sha256"]:
        raise RuntimeError("source changed during integrated run")
    artifact_map = {name: sha256(content).hexdigest() for name, content in sorted(files.items())}
    manifest = {"classification": "SYNTHETIC_ENGINEERING_ONLY", "files": artifact_map,
                "artifact_map_sha256": stable_hash(artifact_map), "paper_execution": False, "live_execution": False}
    files["artifact_manifest.json"] = json_bytes(manifest)
    write_immutable_bundle(output, files)
    return {**summary, "artifact_map_sha256": manifest["artifact_map_sha256"]}
