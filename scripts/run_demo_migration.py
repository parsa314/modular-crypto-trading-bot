"""Verified demo-schema migration; bounded offline PAPER, no exchange requests."""
from __future__ import annotations

import argparse
from datetime import timedelta
from decimal import Decimal as D
import json
from pathlib import Path

from research_bot.completion.backtest.runner import BacktestRunner, ReplayFrame
from research_bot.completion.demo_compat import DemoExecutionAdmission, DemoJournalProjection
from research_bot.completion.execution.cost_preflight import ExecutionCostPreflight
from research_bot.completion.execution.impact_estimator import SquareRootImpactEstimator
from research_bot.completion.execution.liquidity_gate import DepthAwareLiquidityGate
from research_bot.completion.journal import TradeJournal
from research_bot.completion.models import CompletionPolicy, MarketRules
from research_bot.completion.portfolio.conflict_arbiter import PortfolioLimits
from research_bot.completion.strategies.bundle import EmaCrossHypothesis, StrategyBundle
from research_bot.completion.strategies.grid_hypothesis import GridHypothesisGenerator, GridParameters
from research_bot.completion.strategies.triangular_arb_hypothesis import TriangularArbitrageHypothesis
from research_bot.strategies.baselines import RsiMaConfluenceHypothesis, RsiMaParams
from research_bot.jamalian_data import BookLevel, Candle, OrderBook
from research_bot.contracts import EvidenceStamp
from research_bot.hypothesis import canonical_json
from hashlib import sha256
from scripts.run_completion_paper import synthetic_frames


def demo_frames(count: int) -> list[ReplayFrame]:
    # Stress path with prices in the supplied demo's scale; not an exchange feed.
    frames=[]
    for frame in synthetic_frames(count):
        price=frame.candle.close*D(700)
        prices={"BTC_USDT":price,"USDT_IRT":D(92000),"BTC_IRT":price*D(92000)*D("1.018")}
        books=[]
        for symbol,mid in prices.items():
            lineage=sha256(canonical_json([symbol,frame.timestamp.isoformat(),str(mid),"synthetic_demo_v2"]).encode()).hexdigest()
            stamp=EvidenceStamp("SYNTHETIC_DEMO_SCENARIO",frame.timestamp,frame.timestamp,lineage=lineage,reliability_tier="SYNTHETIC")
            books.append(OrderBook(symbol,(BookLevel(mid*D("0.9995"),D("1.5")),),
                (BookLevel(mid*D("1.0005"),D("1.5")),),stamp))
        candle=Candle("BTC_USDT",price,books[0].stamp)
        frames.append(ReplayFrame(frame.timestamp,candle,price,price,price,D("1.5"),tuple(books),"SYNTHETIC"))
    return frames


def run_migration(path: str | Path, cycles: int = 80) -> dict:
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    journal=TradeJournal(path,"complete_demo_migration_v2")
    try:
        projection=DemoJournalProjection(journal)
        admission=DemoExecutionAdmission(ExecutionCostPreflight(
            SquareRootImpactEstimator(D("0.25")),DepthAwareLiquidityGate(),journal),D("0.04"),D(50000000))
        bundle=StrategyBundle(GridHypothesisGenerator(GridParameters("BTC_USDT",D(65000),D(75000),11,
            D("0.006"),fee_rate=D("0.002"),rules=MarketRules(quantity_step=D("0.000001")))),
            TriangularArbitrageHypothesis(),RsiMaConfluenceHypothesis(RsiMaParams(bar_interval=timedelta(seconds=1))),
            EmaCrossHypothesis(fast=9,slow=21),D(10000000))
        runner=BacktestRunner(journal,bundle,{"USDT":D(10000)},CompletionPolicy(mode="PAPER",fee_rate=D("0.002"),
            slippage_rate=D("0.0005")),admission=admission,
            portfolio_limits=PortfolioLimits(max_drawdown=D("0.03"),max_asset_weight=D("0.10")))
        for frame in demo_frames(cycles):
            runner.step(frame)
            projection.sync()  # Derived projection can be retried from exact source events.
        report=runner.report()
        report.update({"demo_schema":"VERIFIED_FOUR_TABLE_COMPATIBILITY_PROJECTION",
            "legacy_tables_authoritative":False,"hpt_status":"DISABLED_UNVERIFIED_CHECKPOINT",
            "admission_configuration":admission.configuration(),
            "configured_strategies":["RSI_MA","EMA","GRID","TRIANGULAR"],
            "triangular_funding":"NO_IRT_ALLOCATED; no implicit capital creation",
            "admission_decisions":[e["payload"] for e in journal.events("DEMO_ADMISSION")],
            "journal_events":len(journal.events()),
            "legacy_fills":journal.connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0]})
        return report
    finally:
        journal.close()


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal",type=Path,default=Path("data/paper_trades.sqlite"))
    parser.add_argument("--cycles",type=int,default=80)
    parser.add_argument("--live-feed",action="store_true")
    args=parser.parse_args()
    if args.live_feed:
        parser.error("real-feed research PAPER requires verified source evidence and graduation; this migration is offline")
    if args.cycles<1:
        parser.error("positive bounded cycles required")
    print(json.dumps(run_migration(args.journal,args.cycles),allow_nan=False))


if __name__ == "__main__":
    main()
