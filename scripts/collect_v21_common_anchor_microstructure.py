from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

import ccxt
import pandas as pd

from research_bot.coinex_depth import fetch_coinex_depth
from research_bot.coinex_public import fetch_coinex_market_deals
from research_bot.common_anchor_v21 import (
    CommonAnchorVenueObservation,
    V21CommonAnchorConfig,
    build_common_anchor_snapshot,
    choose_common_anchor,
)
from research_bot.forward_microstructure_v19 import TRADE_SIDE_SEMANTICS, summarize_trade_window
from research_bot.integrity_v19 import finalize_payload_hash
from research_bot.phase_q_v21 import protocol_metadata_v21
from research_bot.venue_adapter_v19 import normalized_orderbook_limit


def _utc(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _ci_provenance() -> dict:
    return {
        "event_name": os.getenv("GITHUB_EVENT_NAME", "local"),
        "run_id": os.getenv("GITHUB_RUN_ID"),
        "run_attempt": os.getenv("GITHUB_RUN_ATTEMPT"),
        "workflow": os.getenv("GITHUB_WORKFLOW"),
        "ref": os.getenv("GITHUB_REF"),
        "sha": os.getenv("GITHUB_SHA"),
        "repository": os.getenv("GITHUB_REPOSITORY"),
        "actor": os.getenv("GITHUB_ACTOR"),
    }


def _book_from_coinex(symbol: str, cfg: V21CommonAnchorConfig) -> dict:
    depth = fetch_coinex_depth(symbol, limit=cfg.depth_levels)
    response_at = datetime.now(timezone.utc).isoformat()
    return {
        "venue": "coinex",
        "symbol": symbol,
        "book_observed_at": depth.timestamp.isoformat(),
        "book_response_at": response_at,
        "book_timestamp_source": depth.timestamp_source,
        "best_bid": float(depth.best_bid),
        "best_ask": float(depth.best_ask),
        "bid_depth_notional": float(depth.bid_depth_notional),
        "ask_depth_notional": float(depth.ask_depth_notional),
        "source": "coinex_public_depth",
    }


def _book_from_ccxt(venue: str, symbol: str, cfg: V21CommonAnchorConfig) -> dict:
    exchange = getattr(ccxt, venue)({"enableRateLimit": True})
    try:
        exchange.load_markets()
        if symbol not in exchange.markets:
            raise RuntimeError("SYMBOL_NOT_LISTED")
        request_limit = normalized_orderbook_limit(venue, cfg.depth_levels)
        book = exchange.fetch_order_book(symbol, limit=request_limit)
        response_at = datetime.now(timezone.utc)
        bids = list(book.get("bids") or [])[: cfg.depth_levels]
        asks = list(book.get("asks") or [])[: cfg.depth_levels]
        if not bids or not asks:
            raise RuntimeError("EMPTY_ORDER_BOOK")
        ts_ms = book.get("timestamp")
        if ts_ms is None:
            observed_at = response_at
            timestamp_source = "local_fallback"
        else:
            observed_at = datetime.fromtimestamp(float(ts_ms) / 1000.0, tz=timezone.utc)
            timestamp_source = "provider"
        return {
            "venue": venue,
            "symbol": symbol,
            "book_observed_at": observed_at.isoformat(),
            "book_response_at": response_at.isoformat(),
            "book_timestamp_source": timestamp_source,
            "best_bid": float(bids[0][0]),
            "best_ask": float(asks[0][0]),
            "bid_depth_notional": float(sum(float(p) * float(q) for p, q, *_ in bids)),
            "ask_depth_notional": float(sum(float(p) * float(q) for p, q, *_ in asks)),
            "source": f"{venue}_public_ccxt_orderbook",
        }
    finally:
        close = getattr(exchange, "close", None)
        if callable(close):
            close()


def _fetch_book(venue: str, symbol: str, cfg: V21CommonAnchorConfig) -> dict:
    return _book_from_coinex(symbol, cfg) if venue == "coinex" else _book_from_ccxt(venue, symbol, cfg)


def _dedupe_trade_rows(rows: list[dict]) -> list[dict]:
    seen: set[tuple[Any, ...]] = set()
    out: list[dict] = []
    for row in rows:
        key = (
            row.get("id"),
            row.get("timestamp"),
            row.get("side"),
            row.get("price"),
            row.get("amount"),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _trades_from_ccxt(
    venue: str,
    symbol: str,
    anchor_at: str,
    cfg: V21CommonAnchorConfig,
) -> tuple[pd.DataFrame, bool, int, dict]:
    exchange = getattr(ccxt, venue)({"enableRateLimit": True})
    start = _utc(anchor_at) - pd.Timedelta(seconds=cfg.trade_window_seconds)
    start_ms = int(start.timestamp() * 1000)
    anchor_ms = int(_utc(anchor_at).timestamp() * 1000)
    cursor = start_ms
    collected: list[dict] = []
    pages = 0
    first_page_len = 0
    stop_reason = "MAX_PAGES"
    try:
        exchange.load_markets()
        if symbol not in exchange.markets:
            raise RuntimeError("SYMBOL_NOT_LISTED")
        for page in range(cfg.max_trade_pages):
            raw = exchange.fetch_trades(symbol, since=cursor, limit=cfg.trades_limit)
            pages += 1
            if page == 0:
                first_page_len = len(raw)
            if not raw:
                stop_reason = "EMPTY_PAGE"
                break
            normalized = []
            timestamps = []
            for item in raw:
                ts = item.get("timestamp")
                if ts is None:
                    continue
                ts = int(ts)
                timestamps.append(ts)
                price = float(item.get("price") or 0.0)
                amount = float(item.get("amount") or 0.0)
                normalized.append({
                    "id": item.get("id"),
                    "timestamp": ts,
                    "side": item.get("side"),
                    "price": price,
                    "amount": amount,
                    "notional": float(item.get("cost")) if item.get("cost") is not None else price * amount,
                })
            collected.extend(normalized)
            if not timestamps:
                stop_reason = "NO_TIMESTAMPED_TRADES"
                break
            last_ts = max(timestamps)
            if last_ts >= anchor_ms:
                stop_reason = "REACHED_ANCHOR"
                break
            next_cursor = last_ts + 1
            if next_cursor <= cursor:
                stop_reason = "NON_ADVANCING_CURSOR"
                break
            cursor = next_cursor
            if len(raw) < cfg.trades_limit:
                stop_reason = "SHORT_PAGE_BEFORE_ANCHOR"
                break
    finally:
        close = getattr(exchange, "close", None)
        if callable(close):
            close()

    collected = _dedupe_trade_rows(collected)
    frame = pd.DataFrame(collected)
    if frame.empty:
        frame = pd.DataFrame(columns=["timestamp", "side", "price", "amount", "notional"])
        return frame, False, pages, {"stop_reason": stop_reason, "first_page_len": first_page_len}
    ts = pd.to_datetime(frame["timestamp"], unit="ms", utc=True, errors="coerce")
    valid_ts = ts.dropna()
    earliest = valid_ts.min() if len(valid_ts) else None
    # If a provider returns a short first page after an explicit `since`, the
    # absence of earlier rows is treated as an exhausted/sparse interval. If it
    # returns a full page, require actual coverage close to the left boundary.
    left_established = bool(
        earliest is not None
        and (
            earliest <= start + pd.Timedelta(seconds=5)
            or first_page_len < cfg.trades_limit
        )
    )
    meta = {
        "stop_reason": stop_reason,
        "first_page_len": int(first_page_len),
        "earliest_raw_trade_at": earliest.isoformat() if earliest is not None else None,
        "raw_unique_trade_rows": int(len(frame)),
    }
    return frame, left_established, pages, meta


def _trades_from_coinex(
    symbol: str,
    anchor_at: str,
    cfg: V21CommonAnchorConfig,
) -> tuple[pd.DataFrame, bool, int, dict]:
    frame = fetch_coinex_market_deals(
        symbol,
        market_type="spot",
        pages=cfg.max_trade_pages,
        limit=1000,
    )
    start = _utc(anchor_at) - pd.Timedelta(seconds=cfg.trade_window_seconds)
    if frame.empty:
        return frame, False, cfg.max_trade_pages, {"stop_reason": "EMPTY_RECENT_DEALS"}
    earliest = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce").dropna().min()
    left_established = bool(
        earliest is not None
        and (
            earliest <= start + pd.Timedelta(seconds=5)
            or len(frame) < 1000
        )
    )
    return frame, left_established, cfg.max_trade_pages, {
        "stop_reason": "BACKWARD_ID_PAGINATION",
        "earliest_raw_trade_at": earliest.isoformat() if earliest is not None else None,
        "raw_unique_trade_rows": int(len(frame)),
    }


def _fetch_trades(
    venue: str,
    symbol: str,
    anchor_at: str,
    cfg: V21CommonAnchorConfig,
) -> tuple[pd.DataFrame, bool, int, dict]:
    if venue == "coinex":
        return _trades_from_coinex(symbol, anchor_at, cfg)
    return _trades_from_ccxt(venue, symbol, anchor_at, cfg)


def _observation_from_parts(
    book: dict,
    trades: pd.DataFrame,
    *,
    anchor_at: str,
    left_established: bool,
    pages: int,
    cfg: V21CommonAnchorConfig,
) -> CommonAnchorVenueObservation:
    tw = summarize_trade_window(trades, observed_at=anchor_at, window_seconds=cfg.trade_window_seconds)
    return CommonAnchorVenueObservation(
        venue=book["venue"],
        symbol=book["symbol"],
        anchor_at=_utc(anchor_at).isoformat(),
        book_observed_at=_utc(book["book_observed_at"]).isoformat(),
        book_response_at=_utc(book["book_response_at"]).isoformat(),
        book_timestamp_source=book["book_timestamp_source"],
        best_bid=float(book["best_bid"]),
        best_ask=float(book["best_ask"]),
        bid_depth_notional=float(book["bid_depth_notional"]),
        ask_depth_notional=float(book["ask_depth_notional"]),
        trade_buy_notional=float(tw["trade_buy_notional"]),
        trade_sell_notional=float(tw["trade_sell_notional"]),
        trade_unknown_notional=float(tw["trade_unknown_notional"]),
        trade_count=int(tw["trade_count"]),
        raw_trade_count=int(tw["raw_trade_count"]),
        future_trade_count_excluded=int(tw["future_trade_count_excluded"]),
        trade_window_start=tw["trade_window_start"],
        trade_window_end=tw["trade_window_end"],
        trade_first_at=tw["trade_first_at"],
        trade_last_at=tw["trade_last_at"],
        trade_staleness_seconds=tw["trade_staleness_seconds"],
        trade_left_boundary_established=bool(left_established),
        trade_fetch_pages=int(pages),
        trade_side_semantics=TRADE_SIDE_SEMANTICS,
        source=f"{book['source']}+public_trades_common_anchor",
    )


def collect(cfg: V21CommonAnchorConfig) -> tuple[list[CommonAnchorVenueObservation], list[dict], list[dict]]:
    books: list[dict] = []
    failures: list[dict] = []
    trade_fetch_diagnostics: list[dict] = []

    with ThreadPoolExecutor(max_workers=max(1, len(cfg.symbols) * len(cfg.venues))) as pool:
        futures = {
            pool.submit(_fetch_book, venue, symbol, cfg): (venue, symbol)
            for symbol in cfg.symbols
            for venue in cfg.venues
        }
        for future in as_completed(futures):
            venue, symbol = futures[future]
            try:
                books.append(future.result())
            except Exception as exc:
                failures.append({"stage": "book", "venue": venue, "symbol": symbol, "reason": f"{type(exc).__name__}:{exc}"})

    observations: list[CommonAnchorVenueObservation] = []
    for symbol in cfg.symbols:
        symbol_books = [b for b in books if b["symbol"] == symbol]
        provider_books = [b for b in symbol_books if b.get("book_timestamp_source") == "provider"]
        if not provider_books:
            failures.append({"stage": "anchor", "venue": None, "symbol": symbol, "reason": "NO_PROVIDER_TIMESTAMPED_BOOKS"})
            continue
        anchor_at = choose_common_anchor(b["book_observed_at"] for b in provider_books)
        if _utc(anchor_at) > pd.Timestamp.now(tz="UTC") + pd.Timedelta(seconds=5):
            failures.append({"stage": "anchor", "venue": None, "symbol": symbol, "reason": "COMMON_ANCHOR_TOO_FAR_IN_FUTURE"})
            continue

        with ThreadPoolExecutor(max_workers=max(1, len(symbol_books))) as pool:
            tf = {
                pool.submit(_fetch_trades, b["venue"], symbol, anchor_at, cfg): b
                for b in symbol_books
            }
            for future in as_completed(tf):
                book = tf[future]
                try:
                    trades, left_established, pages, meta = future.result()
                    trade_fetch_diagnostics.append({
                        "venue": book["venue"],
                        "symbol": symbol,
                        "anchor_at": anchor_at,
                        "left_boundary_established": bool(left_established),
                        "pages": int(pages),
                        **meta,
                    })
                    observations.append(_observation_from_parts(
                        book,
                        trades,
                        anchor_at=anchor_at,
                        left_established=left_established,
                        pages=pages,
                        cfg=cfg,
                    ))
                except Exception as exc:
                    failures.append({"stage": "trades", "venue": book["venue"], "symbol": symbol, "reason": f"{type(exc).__name__}:{exc}"})
    return observations, failures, trade_fetch_diagnostics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/v21/forward/common-anchor.json")
    parser.add_argument("--symbols", default="BTC/USDT,ETH/USDT")
    parser.add_argument("--venues", default="coinex,okx,kucoin")
    parser.add_argument("--trade-window-seconds", type=int, default=60)
    args = parser.parse_args()

    cfg = V21CommonAnchorConfig(
        symbols=tuple(x.strip().upper() for x in args.symbols.split(",") if x.strip()),
        venues=tuple(x.strip().lower() for x in args.venues.split(",") if x.strip()),
        trade_window_seconds=int(args.trade_window_seconds),
    )
    observations, failures, trade_diag = collect(cfg)
    snapshot = build_common_anchor_snapshot(observations, cfg)
    snapshot["generated_at"] = datetime.now(timezone.utc).isoformat()
    snapshot["provider_failures"] = failures
    snapshot["trade_fetch_diagnostics"] = trade_diag
    snapshot["raw_book_count"] = int(len({(x.venue, x.symbol) for x in observations}) + sum(1 for x in failures if x.get("stage") == "trades"))
    snapshot["raw_observation_count"] = int(len(observations))
    snapshot["collection_rule"] = "prospective_only_common_anchor_no_backfill_no_signal"
    snapshot["phase_q_protocol"] = protocol_metadata_v21()
    snapshot["ci_provenance"] = _ci_provenance()
    snapshot.pop("pre_metadata_sha256", None)
    finalize_payload_hash(snapshot, "snapshot_sha256")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, indent=2, default=str), encoding="utf-8")
    print(json.dumps({
        "output": str(out),
        "authorized_symbol_count": snapshot["authorized_symbol_count"],
        "raw_observation_count": snapshot["raw_observation_count"],
        "provider_failures": len(failures),
        "trade_fetch_diagnostics": len(trade_diag),
        "phase_q_protocol_version": snapshot["phase_q_protocol"]["protocol_version"],
        "snapshot_sha256": snapshot["snapshot_sha256"],
    }, indent=2))


if __name__ == "__main__":
    main()
