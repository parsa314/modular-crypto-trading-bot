from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path

import yaml
import numpy as np
import pandas as pd

from .data_feed import BinancePublicFeed, ParquetFeed, Snapshot
from .logger import DailyLogger, atomic_json, single_instance
from .paper_broker import PaperBroker, liquidity_tiers
from .portfolio import Portfolio
from .strategy_runner import StrategyRunner


ROOT = Path(__file__).resolve().parents[2]


def load_config(path: Path) -> dict:
    config = yaml.safe_load(path.read_text())
    if not isinstance(config, dict) or config.get("mode") not in {"websocket", "replay"}:
        raise ValueError("mode must be websocket or replay; account/testnet transports are not implemented")
    universe = config.get("universe", [])
    if not universe or len(universe) != len(set(universe)) or any(not isinstance(s, str) or not s.endswith("USDT") or not s.isalnum() or s != s.upper() for s in universe):
        raise ValueError("universe must contain unique uppercase spot USDT symbols")
    risk = config["risk"]
    for key, maximum in (("daily_drawdown", .05), ("total_drawdown", .15), ("max_position", .10)):
        if not 0 < float(risk[key]) <= maximum:
            raise ValueError(f"{key} must be positive and at most {maximum}")
    if not math.isfinite(risk["min_volume24_usdt"]) or risk["min_volume24_usdt"] < 5_000_000:
        raise ValueError("minimum liquidity cannot be below 5M USDT")
    if not 0 < config["max_stale_seconds"] <= 120 or not 0 < config["mark_seconds"] <= 30:
        raise ValueError("stale limit <=120s and mark interval <=30s required")
    cost = config["cost"]
    if not math.isfinite(cost["fee_bps"]) or not 10 <= cost["fee_bps"] < 10000 or any(not math.isfinite(cost["tier_penalty_bps"][i]) or not p <= cost["tier_penalty_bps"][i] < 10000 for i, p in ((1, 5), (2, 15), (3, 40))):
        raise ValueError("cost assumptions cannot be below the configured tier model")
    return config


class PaperEngine:
    def __init__(self, config: dict, feed, state_path: Path, log_dir: Path, root: Path = ROOT):
        self.config, self.feed, self.state_path = config, feed, state_path
        self.logger = DailyLogger(log_dir)
        identity = {k: v for k, v in config.items() if k not in {"state_file", "log_dir", "mark_seconds"}}
        self.fingerprint = sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        self.portfolio = Portfolio.create(config["initial_cash"])
        if state_path.exists():
            record = json.loads(state_path.read_text())
            if record["config_sha256"] != self.fingerprint:
                raise ValueError("state/config mismatch; choose a separate paper state file")
            self.portfolio = Portfolio.from_dict(record["portfolio"])
            if record.get("pending_event"):
                event = record["pending_event"]
                self.logger.write(datetime.fromisoformat(event["timestamp"]), event)
                record["pending_event"] = None
                atomic_json(state_path, record)
        self.runner = StrategyRunner(config, root)
        self.broker = PaperBroker(config["cost"]["fee_bps"], config["cost"]["tier_penalty_bps"], config["risk"]["max_position"], config["risk"]["min_volume24_usdt"])
        self.now = datetime.now(timezone.utc)

    def checkpoint(self, kind: str, **details):
        self.portfolio.sequence += 1
        event = {"timestamp": self.now.isoformat(), "sequence": self.portfolio.sequence,
                 "event": kind, "strategy": self.runner.name, "mode": self.config["mode"],
                 "portfolio": self.portfolio.snapshot(), **details}
        record = {"config_sha256": self.fingerprint, "portfolio": self.portfolio.to_dict(), "pending_event": event}
        atomic_json(self.state_path, record)
        self.logger.write(self.now, event)
        record["pending_event"] = None
        atomic_json(self.state_path, record)

    def _validate(self, snapshot: Snapshot):
        active = {s for s in self.config["universe"] if snapshot.volume24.get(s, 0) > self.config["risk"]["min_volume24_usdt"]} | set(self.portfolio.positions)
        for s in active:
            if s not in snapshot.prices or s not in snapshot.price_times:
                raise ValueError(f"missing current market data: {s}")
            age = (snapshot.timestamp - snapshot.price_times[s]).total_seconds()
            if age > self.config["max_stale_seconds"] or age < -5:
                raise ValueError(f"STALE_MARKET_DATA: {s}")
        if any(not math.isfinite(q) or q < 0 for q in snapshot.volume24.values()):
            raise ValueError("invalid rolling 24h quote volume")
        if not snapshot.history.empty and (snapshot.history.date >= snapshot.timestamp.replace(hour=0, minute=0, second=0, microsecond=0)).any():
            raise ValueError("strategy history contains an unclosed daily bar")
        if not snapshot.history.empty:
            h = snapshot.history
            if h.duplicated(["symbol", "date"]).any() or not np.isfinite(h[["open", "close", "quote_volume"]].to_numpy(dtype=float)).all():
                raise ValueError("duplicate or nonfinite daily history")
            if (h[["open", "close"]] <= 0).any().any() or (h.quote_volume < 0).any():
                raise ValueError("invalid daily price or volume")

    async def process(self, snapshot: Snapshot):
        self.now = snapshot.timestamp
        self._validate(snapshot)
        if self.portfolio.last_timestamp and snapshot.timestamp <= datetime.fromisoformat(self.portfolio.last_timestamp):
            return False
        risk = self.config["risk"]
        metrics = self.portfolio.mark(snapshot.prices, self.now, risk["daily_drawdown"], risk["total_drawdown"])
        if self.portfolio.halt_reason:
            self.portfolio.last_timestamp = self.now.isoformat()
            self.checkpoint("halt", risk=metrics, source=snapshot.source)
            return False
        signal = None
        hour = self.now.strftime("%Y-%m-%dT%H")
        if hour != self.portfolio.last_signal_slot:
            signal = self.runner.targets(snapshot, self.portfolio)
            self.portfolio.strategy_state["targets"] = signal
            self.portfolio.last_signal_slot = hour
        slot = self.runner.rebalance_slot(snapshot)
        scheduled = slot is not None and slot != self.portfolio.last_rebalance_slot
        weights = self.portfolio.snapshot()["weights"]
        trim = any(w > risk["max_position"] + 1e-9 for w in weights.values())
        trades = []
        reason = None
        if scheduled or trim:
            targets = self.portfolio.strategy_state.get("targets", {}) if scheduled else {s: min(w, risk["max_position"]) for s, w in weights.items()}
            targets = {s: min(w, risk["max_position"]) for s, w in targets.items() if w > 0}
            # New purchases require >5M rolling volume; exits remain permitted.
            targets = {s: w for s, w in targets.items() if snapshot.volume24.get(s, 0) > risk["min_volume24_usdt"] or s in self.portfolio.positions}
            symbols = sorted(set(targets) | set(self.portfolio.positions))
            eligible_tier = [s for s in self.config["universe"] if len(snapshot.history[snapshot.history.symbol == s]) >= 30]
            last_closed_day = pd.Timestamp(self.now).normalize() - pd.Timedelta(days=1)
            if any(snapshot.history.loc[snapshot.history.symbol == s, "date"].max() != last_closed_day for s in eligible_tier):
                raise ValueError("stale daily volume history for cost tier")
            tiers = liquidity_tiers(snapshot.history, eligible_tier)
            books = dict(zip(symbols, await asyncio.gather(*(self.feed.order_book(s, snapshot) for s in symbols))))
            trade_time = datetime.now(timezone.utc) if self.config["mode"] == "websocket" else self.now
            self.portfolio, trades = self.broker.rebalance(self.portfolio, targets, books, tiers, snapshot.volume24, trade_time)
            reason = "scheduled" if scheduled else "position_limit_trim"
            if scheduled:
                self.portfolio.last_rebalance_slot = slot
            # Execution costs can themselves trip the limits.
            metrics = self.portfolio.mark({}, self.now, risk["daily_drawdown"], risk["total_drawdown"])
        self.portfolio.last_timestamp = self.now.isoformat()
        self.checkpoint("halt" if self.portfolio.halt_reason else "snapshot", source=snapshot.source,
                        risk=metrics, signal=signal, trades=trades, rebalance_reason=reason)
        return True

    async def run(self, max_events: int | None = None) -> int:
        if self.portfolio.halt_reason:
            print(json.dumps({"status": "HALTED", "reason": self.portfolio.halt_reason}))
            return 2
        count = 0
        try:
            async for snapshot in self.feed.snapshots():
                processed = await self.process(snapshot)
                if self.portfolio.halt_reason:
                    return 2
                if processed:
                    count += 1
                if max_events and count >= max_events:
                    break
        except asyncio.CancelledError:
            self.checkpoint("stopped", reason="operator_interrupt")
            raise
        except Exception as exc:
            self.portfolio.halt_reason = f"DATA_OR_EXECUTION_ERROR:{type(exc).__name__}:{exc}"
            self.checkpoint("halt", error=str(exc))
            print(json.dumps({"status": "HALTED", "reason": self.portfolio.halt_reason}))
            return 1
        self.checkpoint("stopped", reason="event_limit_or_replay_complete")
        print(json.dumps({"status": "STOPPED", "events": count, "equity": self.portfolio.equity(), "state": str(self.state_path)}))
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Local paper book with public data only")
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.yaml"))
    parser.add_argument("--mode", choices=["websocket", "replay"])
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--log-dir", type=Path)
    parser.add_argument("--max-events", type=int)
    args = parser.parse_args()
    config = load_config(args.config)
    if args.mode:
        config["mode"] = args.mode
    if args.max_events is not None and args.max_events <= 0:
        parser.error("--max-events must be positive")
    if config["mode"] == "websocket":
        feed = BinancePublicFeed(config["universe"], config["mark_seconds"])
    else:
        replay = config["replay"]
        if config["universe"] != [replay["symbol"]]:
            parser.error("single-file replay requires universe=[replay.symbol]")
        feed = ParquetFeed(ROOT / replay["path"], replay["symbol"], replay["start"], replay["end"], replay["bar_minutes"], replay["delay_seconds"])
    state_path = args.state_file or ROOT / config["state_file"]
    log_dir = args.log_dir or ROOT / config["log_dir"]
    with single_instance(state_path):
        engine = PaperEngine(config, feed, state_path, log_dir)
        return asyncio.run(engine.run(args.max_events))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
