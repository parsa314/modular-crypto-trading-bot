from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

import ccxt

from .universe import MarketListing


@dataclass(frozen=True)
class DiscoveryFailure:
    exchange: str
    stage: str
    error_type: str
    message: str


@dataclass(frozen=True)
class DiscoveryBatch:
    listings: tuple[MarketListing, ...]
    failures: tuple[DiscoveryFailure, ...]
    started_at: datetime
    completed_at: datetime


def _market_type(market: dict) -> str:
    if market.get("spot"):
        return "spot"
    if market.get("swap") or market.get("contract"):
        return "perpetual"
    return str(market.get("type") or "unknown").lower()


def _quote_volume(ticker: dict | None) -> float | None:
    """Normalize public quote volume; non-finite or negative values are unknown."""
    if not ticker:
        return None
    qv = ticker.get("quoteVolume")
    if qv is not None:
        try:
            volume = float(qv)
            if math.isfinite(volume) and volume >= 0:
                return volume
        except (TypeError, ValueError, OverflowError):
            pass
    bv = ticker.get("baseVolume")
    last = ticker.get("last") or ticker.get("close")
    if bv is not None and last is not None:
        try:
            base_volume, price = float(bv), float(last)
            volume = base_volume * price
            if (
                math.isfinite(base_volume)
                and math.isfinite(price)
                and base_volume >= 0
                and price > 0
                and math.isfinite(volume)
            ):
                return volume
        except (TypeError, ValueError, OverflowError):
            pass
    return None


def _spread_bps(ticker: dict | None) -> float | None:
    if not ticker:
        return None
    try:
        bid = float(ticker.get("bid"))
        ask = float(ticker.get("ask"))
    except (TypeError, ValueError, OverflowError):
        return None
    if not (math.isfinite(bid) and math.isfinite(ask)):
        return None
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2.0
    spread = (ask - bid) / mid * 10_000.0 if mid > 0 else float("nan")
    return float(spread) if math.isfinite(spread) and spread >= 0 else None


def normalize_market_listing(
    *,
    exchange_id: str,
    market: dict,
    ticker: dict | None = None,
    observed_at: datetime | None = None,
) -> MarketListing:
    observed = observed_at or datetime.now(timezone.utc)
    symbol = str(market.get("symbol") or "")
    base = str(market.get("base") or "").upper()
    quote = str(market.get("quote") or "").upper()
    return MarketListing(
        exchange=exchange_id,
        symbol=symbol,
        base=base,
        quote=quote,
        market_type=_market_type(market),
        active=bool(market.get("active", True)),
        volume_24h_quote=_quote_volume(ticker),
        spread_bps=_spread_bps(ticker),
        # History quality is intentionally unknown at discovery time.  It is
        # filled only after the data audit, never guessed from exchange age.
        history_bars=None,
        missing_fraction=None,
        abnormal_fraction=None,
        market_cap_usd=None,
        provenance=f"ccxt:{exchange_id}:markets+ticker:{observed.isoformat()}",
    )


def _make_exchange(exchange_id: str, timeout_ms: int):
    exchange_cls = getattr(ccxt, exchange_id, None)
    if exchange_cls is None:
        raise ValueError(f"CCXT exchange not available: {exchange_id}")
    return exchange_cls({"enableRateLimit": True, "timeout": int(timeout_ms)})


def discover_exchange_markets(
    exchange_id: str,
    *,
    allowed_quotes: tuple[str, ...] = ("USDT", "USDC", "USD"),
    timeout_ms: int = 15_000,
    retries: int = 2,
) -> tuple[list[MarketListing], list[DiscoveryFailure]]:
    failures: list[DiscoveryFailure] = []
    exchange = _make_exchange(exchange_id, timeout_ms)

    markets = None
    for attempt in range(retries + 1):
        try:
            markets = exchange.load_markets()
            break
        except Exception as exc:  # provider failure isolation is intentional
            failures.append(
                DiscoveryFailure(exchange_id, "load_markets", type(exc).__name__, str(exc)[:500])
            )
            if attempt < retries:
                time.sleep(min(2 ** attempt, 4))
    if not markets:
        return [], failures

    tickers: dict = {}
    try:
        if exchange.has.get("fetchTickers"):
            tickers = exchange.fetch_tickers()
    except Exception as exc:
        failures.append(
            DiscoveryFailure(exchange_id, "fetch_tickers", type(exc).__name__, str(exc)[:500])
        )

    observed = datetime.now(timezone.utc)
    out: list[MarketListing] = []
    for symbol, market in markets.items():
        quote = str(market.get("quote") or "").upper()
        if quote not in allowed_quotes:
            continue
        mtype = _market_type(market)
        if mtype not in {"spot", "perpetual"}:
            continue
        out.append(
            normalize_market_listing(
                exchange_id=exchange_id,
                market=market,
                ticker=tickers.get(symbol),
                observed_at=observed,
            )
        )
    return out, failures


def discover_multi_exchange(
    exchange_ids: Iterable[str],
    *,
    allowed_quotes: tuple[str, ...] = ("USDT", "USDC", "USD"),
    timeout_ms: int = 15_000,
    retries: int = 1,
) -> DiscoveryBatch:
    started = datetime.now(timezone.utc)
    listings: list[MarketListing] = []
    failures: list[DiscoveryFailure] = []
    for exchange_id in exchange_ids:
        try:
            rows, errs = discover_exchange_markets(
                exchange_id,
                allowed_quotes=allowed_quotes,
                timeout_ms=timeout_ms,
                retries=retries,
            )
            listings.extend(rows)
            failures.extend(errs)
        except Exception as exc:
            failures.append(
                DiscoveryFailure(exchange_id, "initialization", type(exc).__name__, str(exc)[:500])
            )
    return DiscoveryBatch(
        listings=tuple(listings),
        failures=tuple(failures),
        started_at=started,
        completed_at=datetime.now(timezone.utc),
    )
