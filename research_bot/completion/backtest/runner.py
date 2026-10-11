from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import copy
from typing import Mapping, Sequence
import pandas as pd

from ...contracts import EvidenceStamp
from ...hypothesis import canonical_json, utc_text, HypothesisSide
from ...jamalian_data import Candle, OrderBook, BookLevel, available, currencies, decimal_value
from ..models import CompletionPolicy, ZERO, ONE, MarketRules, floor_step
from ..journal import TradeJournal, FirstSeenRegistry
from ..strategies.bundle import StrategyBundle
from ..execution.event_driven_simulator import EventDrivenExecutionSimulator, OrderRequest, TERMINAL
from ..execution.multileg import SequentialCycleSimulator
from ..portfolio.conflict_arbiter import PortfolioConflictArbiter, PortfolioView
from .metrics import performance_metrics


@dataclass(frozen=True)
class ReplayFrame:
    timestamp: datetime
    candle: Candle
    open: Decimal
    high: Decimal
    low: Decimal
    volume: Decimal
    books: tuple[OrderBook, ...] = ()
    data_class: str = "SYNTHETIC"

    def __post_init__(self) -> None:
        available(self.candle.stamp, self.timestamp)
        for name in ("open", "high", "low", "volume"):
            object.__setattr__(self, name, decimal_value(getattr(self, name), positive=name != "volume"))
        if self.high < max(self.open, self.candle.close) or self.low > min(self.open, self.candle.close) or self.high < self.low:
            raise ValueError("invalid OHLCV bounds")
        if self.data_class not in {"SYNTHETIC", "HISTORICAL", "PROSPECTIVE"}:
            raise ValueError("explicit replay source classification required")
        object.__setattr__(self, "books", tuple(self.books))

    def to_dict(self) -> dict:
        return {"timestamp": utc_text(self.timestamp), "symbol": self.candle.symbol,
            "closed_at": utc_text(self.candle.stamp.observed_at), "available_at": utc_text(self.candle.stamp.available_at),
            "lineage": self.candle.stamp.lineage, "close": str(self.candle.close),
            "open": str(self.open), "high": str(self.high), "low": str(self.low), "volume": str(self.volume),
            "data_class": self.data_class, "books": [{"symbol": b.symbol, "observed_at": utc_text(b.stamp.observed_at),
                "available_at": utc_text(b.stamp.available_at), "source": b.stamp.source, "lineage": b.stamp.lineage,
                "reliability_tier": b.stamp.reliability_tier,
                "bids": [[str(l.price), str(l.quantity)] for l in b.bids],
                "asks": [[str(l.price), str(l.quantity)] for l in b.asks]} for b in self.books]}


class BacktestRunner:
    """Same generators, simulator and SQLite schema for replay and PAPER.

SYNTHETIC_ENGINEERING PAPER accepts only synthetic frames. Scientific PAPER
needs a journaled graduation record. OHLCV-only depth is explicitly modeled
and cannot pass the default graduation gate as historical order-book evidence.
"""
    def __init__(self, journal: TradeJournal, strategies: StrategyBundle, balances: Mapping[str, Decimal],
                 policy: CompletionPolicy | None = None, quote_currency: str = "USDT",
                 periods_per_year: int = 365 * 86400) -> None:
        self.journal, self.strategies = journal, strategies
        self.policy = policy or CompletionPolicy(mode="BACKTEST")
        self.simulator = EventDrivenExecutionSimulator(balances, journal, self.policy)
        if (self.policy.mode == "PAPER" and self.policy.purpose == "GRADUATED_RESEARCH"
                and self.simulator.graduation.get("configuration_sha256") != self.configuration_sha256()):
            raise ValueError("graduated strategy configuration does not match PAPER bundle")
        self.cycles = SequentialCycleSimulator(self.simulator)
        self.arbiter, self.first_seen = PortfolioConflictArbiter(journal), FirstSeenRegistry(journal)
        self.quote_currency, self.periods_per_year = quote_currency, periods_per_year
        self.state = journal.restore("runner") or {"frames": {}, "curve": [], "grid_links": {}, "peak": "0", "lots": {}, "pnls": {}}
        if "strategies" in self.state:
            if self.state["quote_currency"] != quote_currency or self.state["periods_per_year"] != periods_per_year:
                raise ValueError("run valuation or sampling configuration changed")
            strategies.restore(self.state["strategies"])

    def _books(self, frame: ReplayFrame) -> tuple[OrderBook, ...]:
        if frame.books:
            return frame.books
        price = frame.candle.close
        depth = max(Decimal("0.00000001"), floor_step(frame.volume / 10, Decimal("0.00000001")))
        return (OrderBook(frame.candle.symbol,
            (BookLevel(price * Decimal("0.9999"), depth),), (BookLevel(price * Decimal("1.0001"), depth),),
            EvidenceStamp("OHLCV_SYNTHETIC_BOOK_MODEL", frame.candle.stamp.observed_at,
                frame.candle.stamp.available_at, lineage=frame.candle.stamp.lineage)),)

    def _marks(self, books: Sequence[OrderBook]) -> dict[str, Decimal]:
        marks = {self.quote_currency: ONE}
        # Causal currency valuation graph; no external prices or future data.
        for _ in range(len(books) + 1):
            for book in books:
                base, quote = currencies(book.symbol)
                mid = (book.bids[0].price + book.asks[0].price) / 2
                if quote in marks and base not in marks:
                    marks[base] = mid * marks[quote]
                elif base in marks and quote not in marks:
                    marks[quote] = marks[base] / mid
        if not set(self.simulator.balances()).issubset(marks):
            raise ValueError("unvalued portfolio currency")
        return marks

    def _account_fills(self, fills: Sequence[dict], marks: Mapping[str, Decimal], at: datetime) -> None:
        for fill in fills:
            base, quote = currencies(fill["symbol"])
            if base in {"IRT", "USDT"}:
                continue  # Cash conversion effects are included in marked equity.
            quantity, price = Decimal(fill["quantity"]), Decimal(fill["price"])
            lots = self.state["lots"].setdefault(base, [])
            if fill["side"] == "BUY":
                lots.append({"quantity": fill["received"], "unit_cost": str(quantity * price * marks[quote] / Decimal(fill["received"]))})
                continue
            remaining, basis = quantity, ZERO
            while remaining > ZERO and lots:
                lot = lots[0]
                consumed = min(remaining, Decimal(lot["quantity"]))
                basis += consumed * Decimal(lot["unit_cost"])
                remaining -= consumed
                lot["quantity"] = str(Decimal(lot["quantity"]) - consumed)
                if Decimal(lot["quantity"]) == ZERO:
                    lots.pop(0)
            if remaining > ZERO:
                raise ValueError("inventory cost basis missing")
            pnl = Decimal(fill["received"]) * marks[quote] - basis
            identity = fill["client_order_id"]
            self.state["pnls"][identity] = str(Decimal(self.state["pnls"].get(identity, "0")) + pnl)
            self.journal.append("trade_close:" + sha256(canonical_json(fill).encode()).hexdigest(), "TRADE_CLOSE", at,
                {"client_order_id": identity, "pnl_quote": str(pnl), "quote_currency": self.quote_currency,
                    "quantity": str(quantity), "cost_basis": "FIFO_MARKED_INITIAL_INVENTORY"})

    def step(self, frame: ReplayFrame) -> dict:
        if self.policy.mode == "PAPER" and self.policy.purpose == "SYNTHETIC_ENGINEERING" and frame.data_class != "SYNTHETIC":
            raise ValueError("engineering PAPER demonstration must use synthetic data")
        if self.policy.mode == "PAPER" and self.policy.purpose == "GRADUATED_RESEARCH" and frame.data_class != "PROSPECTIVE":
            raise ValueError("research PAPER requires fresh prospective observations")
        key = utc_text(frame.timestamp)
        digest = sha256(canonical_json(frame.to_dict()).encode()).hexdigest()
        if key in self.state["frames"]:
            if self.state["frames"][key]["digest"] != digest:
                raise ValueError("replay frame revised")
            return copy.deepcopy(self.state["frames"][key])
        if self.state["curve"] and frame.timestamp <= datetime.fromisoformat(self.state["curve"][-1]["timestamp"]):
            raise ValueError("replay frames must be chronological")
        before_state = copy.deepcopy(self.state)
        before_bundle = self.strategies.checkpoint()
        before_arbiter = list(self.arbiter.recent), set(self.arbiter.decided)
        before_cycles = copy.deepcopy(self.cycles.cycles)
        try:
            with self.simulator.atomic():
                record = self._step(frame, digest)
                self.state["strategies"] = self.strategies.checkpoint()
                self.state["quote_currency"], self.state["periods_per_year"] = self.quote_currency, self.periods_per_year
                self.journal.checkpoint("runner", self.state)
                return record
        except BaseException:
            self.state = before_state
            self.strategies.restore(before_bundle)
            self.arbiter.recent, self.arbiter.decided = before_arbiter
            self.cycles.cycles = before_cycles
            raise

    def _step(self, frame: ReplayFrame, digest: str) -> dict:
        at, books = frame.timestamp, self._books(frame)
        marks = self._marks(books)
        registrations = {}
        for book in books:
            available(book.stamp, at, self.policy.max_book_age)
            snapshot_key = "book:" + book.stamp.source + ":" + book.symbol + ":" + utc_text(book.stamp.observed_at)
            if self.policy.mode == "BACKTEST" and frame.data_class == "PROSPECTIVE":
                original = self.journal.connection.execute("SELECT 1 FROM completion_first_seen WHERE experiment_id=? AND snapshot_key=?",
                    (self.journal.experiment_id, snapshot_key)).fetchone()
                if original is None:
                    raise ValueError("BACKTEST cannot create prospective evidence; original registration required")
            payload = {"bids": [[str(l.price), str(l.quantity)] for l in book.bids],
                "asks": [[str(l.price), str(l.quantity)] for l in book.asks]}
            registrations[book.symbol] = self.first_seen.register(snapshot_key,
                book.stamp, payload, at, frame.data_class)
            if (self.policy.mode == "PAPER" and self.policy.purpose == "GRADUATED_RESEARCH"
                    and not registrations[book.symbol]["prospective_clean"]):
                raise ValueError("research PAPER requires verified clean first-seen sources")
        if not self.state["curve"]:
            self.state["lots"] = {asset: [{"quantity": str(amount), "unit_cost": str(marks[asset])}]
                for asset, amount in self.simulator.balances().items() if asset not in {"IRT", "USDT"}}
        fills = []
        for book in sorted(books, key=lambda b: b.symbol):
            fills += self.simulator.on_book(book, at)
        self._account_fills(fills, marks, at)
        self.cycles.advance(at)
        for identity, cycle in self.cycles.cycles.items():
            if cycle["status"].startswith("FAILED") and not cycle.get("unwind_orders"):
                self.cycles.emergency_unwind(identity, books, at)
        grid = self.strategies.grid
        if grid:
            for identity, level_id in list(self.state["grid_links"].items()):
                order = self.simulator.order(identity)
                if order.state in TERMINAL:
                    grid.release(level_id, order.state.value)
                    del self.state["grid_links"][identity]
        inventory = self.simulator.inventory(at)
        hypotheses = self.strategies.generate(frame.candle, books, inventory, at, registrations)
        for h in hypotheses:
            self.journal.append("hypothesis:" + h.hypothesis_id, "HYPOTHESIS", at, h.to_dict())
        if self.strategies.triangular:
            self.journal.append("decay:" + digest, "OPPORTUNITY_DECAY", at, self.strategies.triangular.diagnostics)
        if grid and grid.pending_cancel_ids:
            for level_id in grid.pending_cancel_ids:
                linked = next((identity for identity, level in self.state["grid_links"].items() if level == level_id
                    and self.simulator.order(identity).state not in TERMINAL), None)
                if linked:
                    self.simulator.cancel(linked, at)
                else:
                    grid.release(level_id, "CANCELLED")
        active_orders = [o for o in self.simulator.orders() if o.state not in TERMINAL]
        active_cycles = [c for c in self.cycles.cycles.values() if c["status"] == "RUNNING"]
        active_assets = tuple({o.request.symbol.split("_")[0] for o in active_orders} |
            {currency for c in active_cycles for plan in c["plan"] for currency in plan["symbol"].split("_") if currency not in {"IRT", "USDT"}})
        equity = sum((v * marks[k] for k, v in inventory.balances.items()), ZERO)
        self.state["peak"] = str(max(Decimal(self.state["peak"]), equity))
        view = PortfolioView(inventory, marks, EvidenceStamp("replay_marks", at, at, lineage=digest),
            self.quote_currency, Decimal(self.state["peak"]), active_assets=active_assets,
            active_ids=tuple(o.request.client_order_id for o in active_orders),
            recent_returns=tuple(Decimal(r["return"]) for r in self.state["curve"][-100:]))
        result = self.arbiter.resolve(hypotheses, view, at)
        for h in result.approved:
            if h.side is HypothesisSide.MULTI_LEG:
                self.cycles.start(h, at, True)
                continue
            plan = h.metadata["plan"][0]
            request = OrderRequest(h.hypothesis_id, plan["symbol"], plan["side"], Decimal(plan["quantity"]),
                Decimal(plan["limit_price"]), at, at + timedelta(seconds=30),
                MarketRules(plan["quantity_step"], plan["price_tick"], plan.get("min_notional", "0")), h.earliest_executable, h.strategy_id)
            order = self.simulator.submit(request, True)
            if grid and "level_id" in h.metadata:
                self.state["grid_links"][h.hypothesis_id] = h.metadata["level_id"]
                if order.state in TERMINAL:
                    grid.release(h.metadata["level_id"], order.state.value)
                else:
                    grid.mark_submitted(h.metadata["level_id"])
        if grid:
            for rejection in result.rejected:
                if "level_id" in rejection.candidate.metadata:
                    grid.release(rejection.candidate.metadata["level_id"], "REJECTED")
        previous = Decimal(self.state["curve"][-1]["equity"]) if self.state["curve"] else equity
        point = {"timestamp": utc_text(at), "equity": str(equity), "return": str(equity / previous - ONE)}
        self.state["curve"].append(point)
        record = {"digest": digest, **point, "hypotheses": len(hypotheses),
            "source_event_times": sorted({utc_text(b.stamp.observed_at) for b in books}),
            "approved": [h.hypothesis_id for h in result.approved],
            "rejected": [{"id": r.candidate.hypothesis_id, "reason": r.reason} for r in result.rejected],
            "fills": len(fills), "data_class": frame.data_class,
            "fill_model": "BOOK_REPLAY" if frame.books else "OHLCV_SYNTHETIC_DEPTH",
            "paper_execution": self.policy.paper_execution, "live_execution": False}
        self.state["frames"][utc_text(at)] = record
        self.journal.append("frame:" + digest, "REPLAY_FRAME", at, record)
        return copy.deepcopy(record)

    def run(self, frames: Sequence[ReplayFrame]) -> dict:
        for frame in frames:
            self.step(frame)
        return self.report()

    def configuration_sha256(self) -> str:
        return sha256(canonical_json({"strategies": self.strategies.configuration(),
            "execution_model_sha256": self.policy.execution_model_sha256()}).encode()).hexdigest()

    def report(self) -> dict:
        if not self.state["curve"]:
            return {"status": "EMPTY_REPLAY", "economic_validation": False}
        starts, intervals = {}, []
        end_of_run = datetime.fromisoformat(self.state["curve"][-1]["timestamp"])
        for event in self.journal.events("ORDER_TRANSITION"):
            identity, state = event["payload"]["client_order_id"], event["payload"]["state"]
            time = datetime.fromisoformat(event["timestamp"])
            if state == "ACKNOWLEDGED":
                order = self.simulator.order(identity)
                starts[identity] = max(time, order.request.not_before or time)
            elif state in {s.value for s in TERMINAL} and identity in starts:
                start = starts.pop(identity)
                if time > start:
                    intervals.append((start, time))
        intervals.extend((start, end_of_run) for start in starts.values() if end_of_run > start)
        metrics = performance_metrics([Decimal(r["equity"]) for r in self.state["curve"]],
            [Decimal(v) for v in self.state["pnls"].values()], intervals, self.periods_per_year)
        diagnostics = self.state.get("diagnostics")
        if diagnostics:
            metrics["dsr"] = diagnostics["dsr_per_period"]
            metrics["pbo"] = diagnostics["cscv_pbo"]["pbo"]
            metrics["statistical_status"] = diagnostics["status"]
        return {"mode": self.policy.mode, "purpose": self.policy.purpose, "paper_execution": True, "live_execution": False,
            "configuration_sha256": self.configuration_sha256(),
            "execution_model_sha256": self.policy.execution_model_sha256(),
            "experiment_id": self.journal.experiment_id, "frames": len(self.state["curve"]), "curve": copy.deepcopy(self.state["curve"]),
            "dataset_sha256": sha256(canonical_json([self.state["frames"][key]["digest"] for key in sorted(self.state["frames"])]).encode()).hexdigest(),
            "metrics": metrics, "balances": {k: str(v) for k, v in self.simulator.balances().items()},
            "first_seen": self.first_seen.report(), "failure_statistics": self.cycles.failure_statistics(),
            "source_event_times": sorted({t for row in self.state["frames"].values() for t in row["source_event_times"]}),
            "fills": sum(e["payload"]["fill"] is not None for e in self.journal.events("ORDER_TRANSITION")),
            "economic_validation": False, "limitations": ["Close-quoted OHLCV depth is synthetic; passive queue/impact is not modeled.",
                "FIFO inventory reductions are marked in the portfolio quote; cash FX effects appear in equity returns.",
                "IRT and USDT are explicitly modeled as cash; FX/depeg stress needs a separate risk protocol.",
                "Sampled opportunity duration is not proof of continuous executability."]}

    def diagnose_trials(self, trial_returns: "pd.DataFrame", label_ends: "pd.DatetimeIndex", selected_trial: str) -> dict:
        """Attach DSR/PBO diagnostics for the complete externally replayed trials.

The selected path must match this simulator's exact timestamps/net returns.
No missing/constant paths are fabricated; insufficient support raises ValueError.
"""
        import numpy as np
        import pandas as pd
        from ...jamalian_validation import ValidationHarness
        report = self.report()
        expected = np.asarray([float(Decimal(x)) for x in report["metrics"]["net_returns"]])
        index = pd.DatetimeIndex([r["timestamp"] for r in report["curve"][1:]])
        if selected_trial not in trial_returns or not index.equals(trial_returns.index) or not np.array_equal(expected, trial_returns[selected_trial].to_numpy(float)):
            raise ValueError("trial path must match the journaled simulator replay")
        synthetic = all(r["data_class"] == "SYNTHETIC" for r in self.state["frames"].values())
        diagnostic = ValidationHarness().diagnose(trial_returns, label_ends,
            dataset_sha256=report["dataset_sha256"], trial_count=len(trial_returns.columns),
            data_class="SYNTHETIC_SMOKE" if synthetic else "DEVELOPMENT_REPLAY")
        self.journal.append("diagnostic:" + sha256(canonical_json(diagnostic).encode()).hexdigest(), "VALIDATION_DIAGNOSTIC",
            datetime.fromisoformat(self.state["curve"][-1]["timestamp"]), diagnostic)
        self.state["diagnostics"] = diagnostic
        self.journal.checkpoint("runner", self.state)
        return self.report()
