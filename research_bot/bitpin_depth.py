from __future__ import annotations

import numpy as np

from .bitpin_public import RequestFn, _request_json, fetch_bitpin_orderbook
from .coinex_depth import DepthSnapshot


def fetch_bitpin_depth(
    symbol: str,
    limit: int = 20,
    *,
    request_fn: RequestFn = _request_json,
) -> DepthSnapshot:
    """Return Bitpin orderbook data in the project's execution-safe DepthSnapshot contract."""
    book = fetch_bitpin_orderbook(symbol, depth=limit, request_fn=request_fn)
    bids = book["bids"]
    asks = book["asks"]
    bid = float(bids[0][0])
    ask = float(asks[0][0])
    mid = (bid + ask) / 2.0
    bid_depth = float(sum(qty for _, qty in bids))
    ask_depth = float(sum(qty for _, qty in asks))
    bid_depth_notional = float(sum(price * qty for price, qty in bids))
    ask_depth_notional = float(sum(price * qty for price, qty in asks))
    denom = bid_depth + ask_depth
    imbalance = (bid_depth - ask_depth) / denom if denom > 0 else 0.0
    return DepthSnapshot(
        symbol=symbol,
        timestamp=book["timestamp"],
        best_bid=bid,
        best_ask=ask,
        mid=mid,
        spread_bps=float((ask - bid) / mid * 10_000.0),
        bid_depth=bid_depth,
        ask_depth=ask_depth,
        imbalance=float(np.clip(imbalance, -1.0, 1.0)),
        bid_depth_notional=bid_depth_notional,
        ask_depth_notional=ask_depth_notional,
    )
