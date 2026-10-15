"""Frozen v3 decision and execution rules (no holdout data access).

This module accepts a caller-supplied, audited historical catalog and daily
spot panel.  It deliberately has no downloader and does not infer the catalog
from the symbols present in a price panel: doing so would admit survivorship
bias.  Only Train/Validation decisions are enabled by the public entry point.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math

import numpy as np
import pandas as pd


DEVELOPMENT_END = pd.Timestamp("2024-01-01", tz="UTC")
STABLE_BASES = frozenset(
    "USDC BUSD FDUSD TUSD USDP PAX DAI GUSD USDD UST USTC EUR GBP AUD BRL TRY RUB UAH BIDR IDRT BVND".split()
)
LEVERAGED_SUFFIXES = ("DOWN", "BULL", "BEAR", "UP", "3L", "3S")


class V3DataInvalid(ValueError):
    """A causal or historical-data prerequisite has not been established."""


@dataclass(frozen=True)
class Decision:
    sunday: pd.Timestamp
    execution: pd.Timestamp
    universe: tuple[str, ...]
    targets: dict[str, float]
    cash_weight: float
    cost_bps: dict[str, int]
    positive_count: int
    catalog_sha256: str


@dataclass(frozen=True)
class DevelopmentResult:
    equity: pd.DataFrame
    orders: pd.DataFrame
    decisions: tuple[Decision, ...]
    catalog_sha256: str


def _utc_day(value: object) -> pd.Timestamp:
    day = pd.Timestamp(value)
    if day.tzinfo is None:
        raise V3DataInvalid("timestamps must explicitly be UTC")
    day = day.tz_convert("UTC")
    if day != day.normalize():
        raise V3DataInvalid("daily timestamps must be midnight UTC")
    return day


def prepare_catalog(catalog: pd.DataFrame, *, complete: bool) -> tuple[pd.DataFrame, str]:
    """Validate an externally frozen *all-ever-listed* spot USDT catalog.

    `complete` must be an audited assertion by the data acquisition layer,
    never a deduction from an archive or today's exchangeInfo response.
    """
    if not complete:
        raise V3DataInvalid("SURVIVORSHIP_UNRESOLVED: all-ever-listed catalog not attested")
    required = {"symbol", "listed_at", "delisted_at", "source"}
    if not required.issubset(catalog):
        raise V3DataInvalid(f"catalog missing {sorted(required - set(catalog))}")
    out = catalog[list(sorted(required))].copy()
    out["symbol"] = out["symbol"].astype(str).str.upper()
    if out.symbol.duplicated().any() or out.empty:
        raise V3DataInvalid("catalog must have one row per historical symbol")
    if (out.source.isna() | out.source.astype(str).str.strip().eq("")).any():
        raise V3DataInvalid("catalog events need an auditable source")
    for col in ("listed_at", "delisted_at"):
        parsed = pd.to_datetime(out[col], utc=True, errors="coerce")
        if col == "listed_at" and parsed.isna().any():
            raise V3DataInvalid("each listing needs a documented date")
        if (parsed.dropna() != parsed.dropna().dt.normalize()).any():
            raise V3DataInvalid("catalog listing boundaries must be UTC days")
        out[col] = parsed
    if ((out.delisted_at.notna()) & (out.delisted_at <= out.listed_at)).any():
        raise V3DataInvalid("delisted_at is first untradeable day, after listing")
    if (~out.symbol.str.endswith("USDT")).any():
        raise V3DataInvalid("catalog must contain spot USDT symbols only")
    out = out.sort_values("symbol").reset_index(drop=True)
    canonical = out.assign(
        listed_at=out.listed_at.dt.strftime("%Y-%m-%d"),
        delisted_at=out.delisted_at.dt.strftime("%Y-%m-%d").fillna(""),
    ).to_dict(orient="records")
    digest = sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return out, digest


def prepare_panel(panel: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "symbol", "open", "close", "quote_volume"}
    if not required.issubset(panel):
        raise V3DataInvalid(f"daily panel missing {sorted(required - set(panel))}")
    out = panel[list(sorted(required))].copy()
    out["date"] = pd.to_datetime(out.date, utc=True, errors="coerce")
    if out.date.isna().any() or (out.date != out.date.dt.normalize()).any():
        raise V3DataInvalid("panel needs valid UTC daily open timestamps")
    out["symbol"] = out.symbol.astype(str).str.upper()
    if out.duplicated(["date", "symbol"]).any():
        raise V3DataInvalid("duplicate asset-day bar")
    for col in ("open", "close", "quote_volume"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    if (~np.isfinite(out[["open", "close", "quote_volume"]].to_numpy())).any():
        raise V3DataInvalid("nonfinite bar values")
    if ((out.open <= 0) | (out.close <= 0) | (out.quote_volume < 0)).any():
        raise V3DataInvalid("invalid price or quote volume")
    return out.set_index(["symbol", "date"]).sort_index()


def _is_excluded(symbol: str) -> bool:
    base = symbol[:-4]
    return base in STABLE_BASES or base.endswith(LEVERAGED_SUFFIXES)


def _history(panel: pd.DataFrame, symbol: str, sunday: pd.Timestamp) -> pd.DataFrame | None:
    days = pd.date_range(sunday - pd.Timedelta(days=89), sunday, freq="D", tz="UTC")
    try:
        return panel.loc[symbol].reindex(days)
    except KeyError:
        return None


def _percentile(values: pd.Series) -> pd.Series:
    # Average rank for tied values, with symbol order used only for final ties.
    return values.rank(method="average", pct=True)


def _eligible_snapshot(panel: pd.DataFrame, cat: pd.DataFrame, day: pd.Timestamp) -> pd.DataFrame:
    active = cat[(cat.listed_at <= day) & (cat.delisted_at.isna() | (cat.delisted_at > day))]
    rows: list[dict[str, float | str]] = []
    for symbol in active.symbol:
        if _is_excluded(symbol):
            continue
        h = _history(panel, symbol, day)
        if h is None:
            continue
        liquid = (h.quote_volume > 1_000_000) & h.close.notna() & h.open.notna()
        if int(liquid.sum()) < 81:
            continue
        recent = h.iloc[-30:]
        if recent[["quote_volume", "open", "close"]].isna().any().any():
            continue
        if recent.quote_volume.le(0).any():
            continue
        closes = h.close.iloc[-29:]
        if closes.isna().any():
            continue
        ret = {f"R{lag}": float(closes.iloc[-1] / closes.iloc[-1 - lag] - 1) for lag in (7, 14, 28)}
        rows.append({
            "symbol": symbol,
            "median30": float(recent.quote_volume.median()),
            "mean30": float(recent.quote_volume.mean()),
            **ret,
        })
    if not rows:
        return pd.DataFrame(columns=["median30", "mean30", "R7", "R14", "R28"]).rename_axis("symbol")
    return pd.DataFrame(rows).set_index("symbol")


def _tier_costs(eligible: pd.DataFrame) -> dict[str, int]:
    by_mean = eligible.sort_index(kind="stable").sort_values("mean30", ascending=False, kind="stable")
    ranks = {symbol: i + 1 for i, symbol in enumerate(by_mean.index)}
    return {
        s: (15 if s in {"BTCUSDT", "ETHUSDT"} else 25 if ranks[s] <= 20 else 50)
        for s in eligible.index
    }


def decide(
    panel: pd.DataFrame,
    catalog: pd.DataFrame,
    sunday: object,
    *,
    catalog_complete: bool,
    variant: str = "main",
) -> Decision:
    """Apply the pre-registered weekly rule using bars closed by Sunday.

    The caller must supply the complete historical catalog independently of
    returns.  This development entry point rejects every Test-3 decision.
    """
    day = _utc_day(sunday)
    if day.weekday() != 6:
        raise V3DataInvalid("decision must use a completed UTC Sunday")
    if day >= DEVELOPMENT_END:
        raise V3DataInvalid("Test-3 is sealed in the development entry point")
    if variant not in {"main", "R7", "R14", "R28"}:
        raise V3DataInvalid("only four pre-registered variants are allowed")
    cat, digest = prepare_catalog(catalog, complete=catalog_complete)
    bars = prepare_panel(panel)
    if set(bars.index.get_level_values("symbol")) - set(cat.symbol):
        raise V3DataInvalid("price panel contains symbol absent from historical catalog")
    eligible = _eligible_snapshot(bars, cat, day)
    if eligible.empty:
        return Decision(day, day + pd.Timedelta(days=2), (), {}, 1.0, {}, 0, digest)
    # Cost rank covers all eligible names; universe rank uses median volume.
    selected = eligible.sort_index(kind="stable").sort_values("median30", ascending=False, kind="stable").head(50)
    names = tuple(selected.index)
    all_costs = _tier_costs(eligible)
    costs = {s: all_costs[s] for s in names}
    if len(selected) < 25:
        return Decision(day, day + pd.Timedelta(days=2), names, {}, 1.0, costs, 0, digest)
    scores = _percentile(selected.R7) + _percentile(selected.R14) + _percentile(selected.R28)
    if variant != "main":
        scores = _percentile(selected[variant])
    winners = scores.sort_index(kind="stable").sort_values(ascending=False, kind="stable").head(math.ceil(len(selected) / 5)).index
    momentum = selected[["R7", "R14", "R28"]].mean(axis=1)
    positive = [s for s in winners if momentum[s] > 0]
    n = len(positive)
    invested = 1.0 if n >= 10 else 0.1 * n if n >= 5 else min(0.1 * n, 0.3)
    targets = {s: invested / n for s in positive} if n else {}
    return Decision(day, day + pd.Timedelta(days=2), names, targets, 1.0 - invested, costs, n, digest)


def rebalance_at_open(
    cash: float,
    shares: dict[str, float],
    open_prices: dict[str, float],
    targets: dict[str, float],
    cost_bps: dict[str, int],
) -> tuple[float, dict[str, float], float, dict[str, float]]:
    """Solve post-cost target weights and book actual notional and fees.

    Returns cash, shares, total cost in quote currency, and absolute order
    notional.  Capacity checks belong to the caller using trailing volume.
    """
    if cash < 0 or any(q < 0 for q in shares.values()):
        raise V3DataInvalid("spot long-only book cannot have debt or shorts")
    weight_sum = sum(targets.values())
    if any(w < 0 or not math.isfinite(w) for w in targets.values()) or weight_sum > 1 + 1e-12:
        raise V3DataInvalid("invalid target weights")
    symbols = set(shares) | set(targets)
    if symbols - (set(open_prices) & set(cost_bps)):
        raise V3DataInvalid("missing execution price or causal cost tier")
    if any(open_prices[s] <= 0 or cost_bps[s] not in {15, 25, 50} for s in symbols):
        raise V3DataInvalid("invalid execution input")
    gross = cash + sum(shares.get(s, 0.0) * open_prices[s] for s in symbols)

    def costs(nav: float) -> float:
        return sum(abs(targets.get(s, 0.0) * nav - shares.get(s, 0.0) * open_prices[s]) * cost_bps[s] / 10_000 for s in symbols)

    lo, hi = 0.0, gross
    for _ in range(70):
        mid = (lo + hi) / 2
        if mid + costs(mid) > gross:
            hi = mid
        else:
            lo = mid
    post = (lo + hi) / 2
    orders = {s: abs(targets.get(s, 0.0) * post - shares.get(s, 0.0) * open_prices[s]) for s in symbols}
    new_shares = {s: targets[s] * post / open_prices[s] for s in targets if targets[s] > 0}
    total_cost = sum(orders[s] * cost_bps[s] / 10_000 for s in symbols)
    new_cash = post * max(0.0, 1.0 - weight_sum)
    if not math.isclose(gross, new_cash + sum(new_shares[s] * open_prices[s] for s in new_shares) + total_cost, rel_tol=1e-10, abs_tol=1e-8):
        raise AssertionError("cost and cash ledger do not balance")
    return new_cash, new_shares, total_cost, orders


def _exit_costs(
    bars: pd.DataFrame, cat: pd.DataFrame, cutoff: pd.Timestamp, symbols: set[str]
) -> dict[str, int]:
    """Cost rank as of a closed day; noneligible held names pay Tier 3."""
    if not symbols:
        return {}
    eligible = _eligible_snapshot(bars, cat, cutoff)
    costs = _tier_costs(eligible)
    for symbol in symbols:
        h = _history(bars, symbol, cutoff)
        if h is None or h.iloc[-30:][["quote_volume", "open", "close"]].isna().any().any():
            raise V3DataInvalid(f"DATA_INVALID: 30-day exit cost history missing for {symbol}")
        costs.setdefault(symbol, 15 if symbol in {"BTCUSDT", "ETHUSDT"} else 50)
    return {s: costs[s] for s in symbols}


def _volume_capacity(bars: pd.DataFrame, symbol: str, cutoff: pd.Timestamp) -> float:
    h = _history(bars, symbol, cutoff)
    if h is None or h.iloc[-30:].quote_volume.isna().any():
        raise V3DataInvalid(f"DATA_INVALID: 30-day capacity history missing for {symbol}")
    return 0.01 * float(h.iloc[-30:].quote_volume.median())


def simulate_development(
    panel: pd.DataFrame,
    catalog: pd.DataFrame,
    *,
    start: object,
    end: object,
    catalog_complete: bool,
    variant: str = "main",
    initial_nav: float = 50_000.0,
) -> DevelopmentResult:
    """Three matched, long-only books on Train/Validation only.

    `end` is exclusive.  The first bar after a 90-day warmup is used only
    when it is a Tuesday; the three books begin at that identical open.
    Delisted positions with no independently verified tradable exit are
    conservatively written down to zero on the first untradeable day.
    """
    begin, stop = _utc_day(start), _utc_day(end)
    if begin >= stop or stop > DEVELOPMENT_END:
        raise V3DataInvalid("development dates must end by 2024-01-01, exclusive")
    if initial_nav <= 0 or not math.isfinite(initial_nav):
        raise V3DataInvalid("initial NAV must be positive")
    cat, digest = prepare_catalog(catalog, complete=catalog_complete)
    bars = prepare_panel(panel)
    if set(bars.index.get_level_values("symbol")) - set(cat.symbol):
        raise V3DataInvalid("price panel contains symbol absent from historical catalog")
    if bars.index.get_level_values("date").max() >= DEVELOPMENT_END:
        raise V3DataInvalid("development panel must not contain Test-3 bars")
    all_days = bars.index.get_level_values("date").unique().sort_values()
    first_possible = max(begin, all_days.min() + pd.Timedelta(days=91))
    tuesdays = [d for d in all_days if first_possible <= d < stop and d.weekday() == 1]
    if not tuesdays:
        raise V3DataInvalid("no development Tuesday after 90-day warmup")
    first = tuesdays[0]
    days = pd.date_range(first, stop - pd.Timedelta(days=1), freq="D", tz="UTC")
    if not set(days).issubset(set(all_days)):
        raise V3DataInvalid("missing whole daily bar in evaluation window")

    holdings: dict[str, dict[str, float]] = {name: {} for name in ("strategy", "equal", "btc")}
    cash = {name: float(initial_nav) for name in holdings}
    out: list[dict[str, float | pd.Timestamp]] = []
    ledger: list[dict[str, float | str | pd.Timestamp | int]] = []
    decisions: list[Decision] = []
    delist = cat.set_index("symbol").delisted_at.to_dict()

    def price(symbol: str, day: pd.Timestamp, column: str) -> float:
        try:
            return float(bars.at[(symbol, day), column])
        except KeyError as exc:
            raise V3DataInvalid(f"DATA_INVALID: {symbol} {day.date()} {column} missing") from exc

    def trade(name: str, day: pd.Timestamp, cutoff: pd.Timestamp, target: dict[str, float], costs: dict[str, int], column: str, cause: str) -> None:
        symbols = set(holdings[name]) | set(target)
        use_costs = costs | _exit_costs(bars, cat, cutoff, symbols - set(costs))
        px = {s: price(s, day, column) for s in symbols}
        new_cash, new_shares, _cost, orders = rebalance_at_open(cash[name], holdings[name], px, target, use_costs)
        for symbol, notional in orders.items():
            if notional < 1e-8:
                continue
            capacity = _volume_capacity(bars, symbol, cutoff)
            if notional > capacity + 1e-8:
                raise V3DataInvalid(f"CAPACITY_FAIL: {name} {symbol} {day.date()} order {notional:.2f} > {capacity:.2f}")
            before = holdings[name].get(symbol, 0.0)
            after = new_shares.get(symbol, 0.0)
            ledger.append({
                "date": day, "book": name, "symbol": symbol,
                "side": "buy" if after > before else "sell", "reason": cause,
                "notional": notional, "cost_bps": use_costs[symbol],
                "cost": notional * use_costs[symbol] / 10_000,
            })
        cash[name], holdings[name] = new_cash, new_shares

    for day in days:
        for name, shares in holdings.items():
            for symbol in list(shares):
                event = delist[symbol]
                if pd.notna(event) and event <= day:
                    if (symbol, day) in bars.index:
                        raise V3DataInvalid(f"catalog says {symbol} untradeable but daily bar exists")
                    ledger.append({"date": day, "book": name, "symbol": symbol, "side": "writeoff", "reason": "delisting_without_verified_exit", "notional": 0.0, "cost_bps": 0, "cost": 0.0})
                    del shares[symbol]
        if day.weekday() == 1:
            sunday = day - pd.Timedelta(days=2)
            decision = decide(panel, catalog, sunday, catalog_complete=catalog_complete, variant=variant)
            decisions.append(decision)
            trade("strategy", day, sunday, decision.targets, decision.cost_bps, "open", "weekly")
            equal = {s: 1.0 / len(decision.universe) for s in decision.universe} if len(decision.universe) >= 25 else {}
            trade("equal", day, sunday, equal, decision.cost_bps, "open", "weekly")
            if day == first:
                trade("btc", day, sunday, {"BTCUSDT": 1.0}, {"BTCUSDT": 15}, "open", "buy_and_hold_entry")
        nav = {name: cash[name] + sum(q * price(s, day, "close") for s, q in holdings[name].items()) for name in cash}
        out.append({"date": day, **nav})

    final_day = days[-1]
    cutoff = final_day - pd.Timedelta(days=1)
    for name in cash:
        trade(name, final_day, cutoff, {}, {}, "close", "split_liquidation")
        out[-1][name] = cash[name]
    equity = pd.DataFrame(out).set_index("date")
    return DevelopmentResult(equity, pd.DataFrame(ledger), tuple(decisions), digest)
