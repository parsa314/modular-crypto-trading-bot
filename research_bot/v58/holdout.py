from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


PROSPECTIVE_HOLDOUT_START = datetime(2026, 9, 25, 16, 0, tzinfo=timezone.utc)
PROSPECTIVE_HOLDOUT_BARS = 540
PROSPECTIVE_HOLDOUT_LAST_OPEN = datetime(2026, 12, 24, 12, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class HoldoutSeal:
    venue: str = "CoinEx"
    timeframe: str = "4h"
    start_bar_open: datetime = PROSPECTIVE_HOLDOUT_START
    bars: int = PROSPECTIVE_HOLDOUT_BARS
    last_bar_open: datetime = PROSPECTIVE_HOLDOUT_LAST_OPEN
    symbols: tuple[str, ...] = (
        "BTC/USDT",
        "ETH/USDT",
        "SOL/USDT",
        "XRP/USDT",
        "DOGE/USDT",
    )
    status: str = "SEALED_FUTURE_NOT_AVAILABLE"

    def __post_init__(self) -> None:
        if self.bars != 540:
            raise ValueError("prospective holdout length is frozen to 540 bars")
        expected_last = self.start_bar_open + (self.bars - 1) * __import__("datetime").timedelta(hours=4)
        if expected_last != self.last_bar_open:
            raise ValueError("prospective holdout last bar does not match frozen 4h grid")


PROSPECTIVE_HOLDOUT = HoldoutSeal()


def assert_tuning_timestamp_allowed(timestamp: datetime) -> None:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    ts = timestamp.astimezone(timezone.utc)
    if ts >= PROSPECTIVE_HOLDOUT_START:
        raise RuntimeError("sealed prospective holdout may not be used for fitting or tuning")
