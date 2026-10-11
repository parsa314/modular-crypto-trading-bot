"""80-cycle synthetic PAPER integration; SQLite resume; no network calls."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

from research_bot.contracts import EvidenceStamp
from research_bot.hypothesis import canonical_json
from research_bot.jamalian_data import Candle, OrderBook, BookLevel
from research_bot.strategies.baselines import RsiMaConfluenceHypothesis, RsiMaParams
from research_bot.completion.models import CompletionPolicy
from research_bot.completion.journal import TradeJournal
from research_bot.completion.strategies.grid_hypothesis import GridHypothesisGenerator, GridParameters
from research_bot.completion.strategies.triangular_arb_hypothesis import TriangularArbitrageHypothesis
from research_bot.completion.strategies.bundle import StrategyBundle, EmaCrossHypothesis
from research_bot.completion.backtest.runner import BacktestRunner, ReplayFrame


def synthetic_frames(count: int = 80) -> list[ReplayFrame]:
    if type(count) is not int or count < 1:
        raise ValueError("positive synthetic frame count required")
    cycle = [100, 98, 94, 90, 86, 90, 94, 98, 102, 106, 110, 114, 110, 106, 102, 98]
    start = datetime(2026, 10, 11, tzinfo=timezone.utc)
    frames = []
    for i in range(count):
        at, price = start + timedelta(seconds=i), Decimal(cycle[i % len(cycle)])
        def observation(symbol: str, mid: Decimal) -> OrderBook:
            levels = {"bid": str(mid * Decimal("0.9999")), "ask": str(mid * Decimal("1.0001")), "size": "100"}
            digest = sha256(canonical_json([symbol, at.isoformat(), levels]).encode()).hexdigest()
            stamp = EvidenceStamp("SYNTHETIC_ENGINEERING", at, at, lineage=digest, reliability_tier="SYNTHETIC")
            return OrderBook(symbol, (BookLevel(Decimal(levels["bid"]), Decimal(100)),),
                (BookLevel(Decimal(levels["ask"]), Decimal(100)),), stamp)
        # Deliberate synthetic cross-market discrepancy exercises non-atomic legs.
        books = (observation("BTC_USDT", price), observation("USDT_IRT", Decimal(10)),
            observation("BTC_IRT", price * Decimal("10.3")))
        candle = Candle("BTC_USDT", price, books[0].stamp)
        frames.append(ReplayFrame(at, candle, price, price, price, Decimal(100), books, "SYNTHETIC"))
    return frames


def build_bundle() -> StrategyBundle:
    return StrategyBundle(GridHypothesisGenerator(GridParameters("BTC_USDT", Decimal(90), Decimal(110), 5, Decimal("0.1"))),
        TriangularArbitrageHypothesis(), RsiMaConfluenceHypothesis(RsiMaParams(bar_interval=timedelta(seconds=1))),
        EmaCrossHypothesis(), Decimal(100))


def run_example(journal_path: Path | str, cycles: int = 80, mode: str = "PAPER") -> dict:
    journal = TradeJournal(journal_path, "jamalian_completion_synthetic_v1")
    try:
        runner = BacktestRunner(journal, build_bundle(), {"USDT": Decimal(1000), "IRT": Decimal(1000), "BTC": Decimal(1)},
            CompletionPolicy(mode=mode), quote_currency="IRT")
        report = runner.run(synthetic_frames(cycles))
        report["arbitration"] = [e["payload"] for e in journal.events("ARBITRATION")]
        report["journal_events"] = len(journal.events())
        report["strategies_observed"] = sorted({e["payload"]["strategy_id"] for e in journal.events("HYPOTHESIS")})
        return report
    finally:
        journal.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--cycles", type=int, default=80)
    args = parser.parse_args()
    report = run_example(args.journal, args.cycles)
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
