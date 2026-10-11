"""Reproducible offline engineering exercise, never market/forward evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd

from research_bot.course.configuration import load_evaluation_config
from research_bot.course.contracts import BookSnapshot, MarketMetadata, digest
from research_bot.course.evaluation import evaluate_research
from research_bot.course.labels import replay_course_events
from research_bot.course.simulation import OfflineOrderSimulator, SimOrderRequest
from research_bot.course.stateful import (
    ConversionLeg,
    GridConfig,
    GridLevel,
    GridStrategyStateMachine,
    TriangularArbitrageEngine,
)
from research_bot.course.strategies import CourseStrategy, strategy_ids
from research_bot.orchestrator import ResearchTradingOrchestrator
from research_bot.risk import RiskSnapshot

BASE_SHA = "fa31e6f5ceb09f4f14ae13fef50706f2e10e1835"


def book(base, quote, bid, ask, now, *, depth="10000"):
    market = MarketMetadata(
        "SYNTHETIC_FIXTURE",
        base + "/" + quote,
        base,
        quote,
        Decimal(".01"),
        Decimal(".001"),
        Decimal(".01"),
        "TOMAN" if quote == "IRT" else quote,
    )
    return BookSnapshot(
        market,
        now,
        now,
        now,
        now,
        "FIXTURE_SEQUENCE_1",
        tuple((Decimal(bid) - Decimal(".01") * i, Decimal(depth)) for i in range(5)),
        tuple((Decimal(ask) + Decimal(".01") * i, Decimal(depth)) for i in range(5)),
        digest((base, quote, bid, ask, now)),
    )


def run(output: Path, seed: int) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    rng = np.random.default_rng(seed)
    count = 360
    times = pd.date_range("2026-01-01", periods=count, freq="4h", tz="UTC")
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.006, count)))
    opening = close * np.exp(rng.normal(0, 0.004, count))
    frame = pd.DataFrame(
        {
            "close_time": times,
            "available_at": times,
            "open_time": times - pd.Timedelta(hours=4) + pd.Timedelta(milliseconds=1),
            "open": opening,
            "high": np.maximum(opening, close) * 1.01,
            "low": np.minimum(opening, close) * 0.99,
            "close": close,
            "volume": np.ones(count) * 10,
        }
    )
    frame.to_csv(output / "synthetic_candles.csv", index=False)
    dataset_hash = hashlib.sha256(
        (output / "synthetic_candles.csv").read_bytes()
    ).hexdigest()
    now = times[-1].to_pydatetime()
    books = (
        book("USDT", "IRT", "9", "10", now),
        book("BTC", "USDT", "99", "100", now),
        book("BTC", "IRT", "1100", "1101", now),
    )
    candle_styles = [s for s in strategy_ids() if s != "order_book_imbalance"]
    candidates = []
    for sid in candle_styles:
        candidates.extend(
            CourseStrategy(sid, enabled=True).generate(
                frame, symbol="BTC/USDT", venue="SYNTHETIC_FIXTURE", decision_time=now
            )
        )
    candidates.extend(
        CourseStrategy("order_book_imbalance", enabled=True).generate(
            books[1], symbol="BTC/USDT", venue="SYNTHETIC_FIXTURE", decision_time=now
        )
    )
    simulator = OfflineOrderSimulator({"USDT": Decimal(1000), "BTC": Decimal(".1")})
    grid = GridStrategyStateMachine(
        simulator, GridConfig(range_preregistration_ref="ENGINEERING_FIXTURE_ONLY")
    )
    grid.prepare(
        books[1],
        (
            GridLevel("BUY", Decimal(".01"), Decimal(98)),
            GridLevel("SELL", Decimal(".01"), Decimal(102)),
        ),
        now,
        range_confirmed=True,
        range_evidence_ref="SYNTHETIC_RANGE_WITNESS",
        range_evidence_available_at=now,
    )
    candidates.append(grid.to_candidate())
    sides: tuple[Literal["BUY", "SELL"], ...] = ("BUY", "BUY", "SELL")
    arb = TriangularArbitrageEngine().evaluate(
        tuple(
            ConversionLeg(b, s, Decimal(".004"))
            for b, s in zip(books, sides)
        ),
        Decimal(10000),
        now,
    )
    candidates.append(arb.to_candidate())
    risk = RiskSnapshot(10000, 10000, 0, 0, 0, 2, 2, (0.001,) * 30)
    core = ResearchTradingOrchestrator()
    decisions = core.process_research_candidates(
        candidates=candidates,
        decision_time=now,
        risk_snapshot=risk,
        run_id="SYNTHETIC_ENGINEERING_FIXTURE_SEED_" + str(seed),
        git_commit=BASE_SHA,
        dataset_hash=dataset_hash,
        artifact_dir=output / "decisions",
    )
    # Independent existing RiskEngine approves this finite fixture wallet only.
    approved = core.risk_engine.evaluate(risk).approved
    simulator.create(
        SimOrderRequest(
            "FIXTURE_ORDER",
            books[1].market,
            "BUY",
            Decimal(".01"),
            fee_rate=Decimal(".001"),
        ),
        now,
    )
    simulator.approve("FIXTURE_ORDER", books[1], now, risk_approved=approved)
    simulator.submit("FIXTURE_ORDER", now)
    simulator.fill("FIXTURE_ORDER", books[1], now)
    state = simulator.export_state()
    assert OfflineOrderSimulator.from_state(state).export_state() == state
    records = []
    positions: dict[str, list[int]] = {sid: [] for sid in candle_styles}
    for i in range(151, count - 1):
        t = times[i].to_pydatetime()
        row = {
            "event_id": "FIXTURE_EVENT_" + str(i),
            "decision_time": t,
            "available_at": t,
            "entry_time": frame.open_time.iloc[i + 1].to_pydatetime(),
            "exit_time": times[i + 1].to_pydatetime(),
            "label_available_at": times[i + 1].to_pydatetime(),
            "gross_return": float(frame.close.iloc[i + 1] / frame.open.iloc[i + 1] - 1),
            "ret_1": float(frame.close.iloc[i] / frame.close.iloc[i - 1] - 1),
        }
        records.append(row)
        for sid in candle_styles:
            c = CourseStrategy(sid, enabled=True).generate(
                frame.iloc[: i + 1],
                symbol="BTC/USDT",
                venue="SYNTHETIC_FIXTURE",
                decision_time=t,
            )[0]
            positions[sid].append(c.direction)
    events = pd.DataFrame(records)
    events.to_csv(output / "synthetic_events.csv", index=False)
    evaluation = evaluate_research(
        events,
        feature_columns=["ret_1"],
        strategy_positions=positions,
        git_commit=BASE_SHA,
        config=load_evaluation_config(),
        provenance={
            "evidence_class": "SYNTHETIC_ENGINEERING_FIXTURE",
            "input_hypothesis_generation": "CLOSED_PREFIX_COURSE_RULES",
            "source_code_sha256": hashlib.sha256(
                Path(__file__).read_bytes()
            ).hexdigest(),
            "working_tree_modified": True,
            "complete_trial_universe": False,
        },
    )
    completed_baselines = {
        sid: [
            asdict(event)
            for event in replay_course_events(
                frame,
                CourseStrategy(sid, enabled=True),
                symbol="BTC/USDT",
                venue="SYNTHETIC_FIXTURE",
            )
        ]
        for sid in candle_styles[:6]
    }
    artifacts = {
        "evaluation.json": evaluation,
        "course_exit_events.json": completed_baselines,
        "simulator_state.json": state,
        "grid_plan.json": {
            "candidate": asdict(grid.to_candidate()),
            "state": grid.state,
        },
        "arbitrage_hypothesis.json": asdict(arb),
    }
    for name, payload in artifacts.items():
        (output / name).write_text(
            json.dumps(payload, default=str, allow_nan=False, sort_keys=True, indent=2)
        )
    source_root = Path(__file__).resolve().parents[1]
    source_files = sorted(
        [
            *source_root.glob("research_bot/course/**/*.py"),
            *source_root.glob("research_bot/course/configs/*.json"),
            Path(__file__),
            *[
                source_root / "research_bot" / n
                for n in (
                    "execution.py",
                    "orchestrator.py",
                    "risk.py",
                    "decision.py",
                    "service.py",
                )
            ],
        ]
    )
    source_hashes = {
        str(p.relative_to(source_root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in source_files
    }
    report = {
        "schema": "COURSE_RESEARCH_REPORT_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "execution_flags": {
            "research_only": True,
            "paper_execution": False,
            "live_execution": False,
        },
        "status": "INTEGRATION_BLOCKED",
        "scientific_status": "SCIENTIFIC_DECISION_DEFERRED",
        "data_class": "SYNTHETIC_ENGINEERING_FIXTURE",
        "dataset_hash": dataset_hash,
        "source_git_commit": BASE_SHA,
        "working_tree_modified": True,
        "source_file_sha256": source_hashes,
        "working_source_hash": digest(source_hashes),
        "panels": {
            "data_freshness": {"status": "SYNTHETIC_ENGINEERING_FIXTURE"},
            "active_hypotheses": {
                "status": "NO_TRADE",
                "metrics": {"hypotheses": len(candidates)},
            },
            "risk_gate_decisions": {
                "status": "DENIED_RESEARCH_ONLY",
                "metrics": {"no_trade": len(decisions)},
            },
            "simulated_orders": {
                "status": "ENGINEERING_REPLAY",
                "metrics": {"simulated_fills": 1},
            },
            "grid_state": {"status": grid.state},
            "arbitrage_opportunities": {
                "status": "RESEARCH_CANDIDATE",
                "metrics": {"opportunity_count": 1},
            },
            "model_calibration": {"status": "BLOCKED"},
            "evidence_ledger": {"status": "ENGINEERING_TESTED"},
        },
        "metrics": {
            "hypotheses": len(candidates),
            "no_trade": len(decisions),
            "simulated_fills": 1,
        },
        "strategies": [
            {"strategy_id": sid, "status": "RESEARCH_CANDIDATE"}
            for sid in strategy_ids()
        ]
        + [
            {"strategy_id": "grid", "status": "RESEARCH_CANDIDATE"},
            {
                "strategy_id": "triangular_arbitrage",
                "status": "RESEARCH_CANDIDATE",
            },
        ],
        "counts": {
            "hypotheses": len(candidates),
            "no_trade": len(decisions),
            "simulated_fills": 1,
        },
        "artifacts": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in output.glob("*.json")
        },
        "remaining_gates": [
            "REAL_DATA_AND_PREREGISTRATION",
            "MODEL_CPCV",
            "PROSPECTIVE_SEQUENCE_EVIDENCE",
            "REAL_DATA_EXIT_POLICY_REPLAY_AND_ABLATIONS",
            "INDEPENDENT_SCIENTIFIC_REVIEW",
        ],
    }
    (output / "report.json").write_text(
        json.dumps(report, allow_nan=False, sort_keys=True, indent=2)
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New artifact directory; existing evidence is never overwritten",
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    report = run(args.output, args.seed)
    print(
        json.dumps(
            {
                "status": report["status"],
                "data_class": report["data_class"],
                "counts": report["counts"],
                "report": str(args.output / "report.json"),
                "execution_flags": report["execution_flags"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
