from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

from .hashing import stable_hash


@dataclass(frozen=True)
class MarketSourceSpec:
    source_id: str
    venue: str
    transport: str
    market_types: tuple[str, ...]
    evidence_class: str
    wire_raw_available: bool
    execution_capability: bool
    priority: int

    def __post_init__(self) -> None:
        for name in ("source_id", "venue", "transport", "evidence_class"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        if not self.market_types or any(x not in {"spot", "swap", "future"} for x in self.market_types):
            raise ValueError("invalid market_types")
        if self.execution_capability:
            raise ValueError("V59 Stage-2 market sources must be read-only")
        if not isinstance(self.priority, int) or self.priority < 1:
            raise ValueError("priority must be a positive integer")


DEFAULT_SOURCES = (
    MarketSourceSpec(
        "CCXT_BINANCE_PUBLIC",
        "binance",
        "CCXT_PUBLIC",
        ("spot",),
        "PUBLIC_MARKET_DATA",
        False,
        False,
        1,
    ),
    MarketSourceSpec(
        "CCXT_COINEX_PUBLIC",
        "coinex",
        "CCXT_PUBLIC",
        ("spot",),
        "PUBLIC_MARKET_DATA",
        False,
        False,
        2,
    ),
    MarketSourceSpec(
        "CCXT_BYBIT_PUBLIC",
        "bybit",
        "CCXT_PUBLIC",
        ("spot",),
        "PUBLIC_MARKET_DATA",
        False,
        False,
        3,
    ),
)


class SourceRegistry:
    def __init__(self, sources: Iterable[MarketSourceSpec] = DEFAULT_SOURCES) -> None:
        values = tuple(sources)
        ids = [x.source_id for x in values]
        if len(ids) != len(set(ids)):
            raise ValueError("source ids must be unique")
        self._sources = {x.source_id: x for x in values}

    def get(self, source_id: str) -> MarketSourceSpec:
        return self._sources[source_id]

    def ordered(self, *, market_type: str) -> tuple[MarketSourceSpec, ...]:
        return tuple(
            sorted(
                (x for x in self._sources.values() if market_type in x.market_types),
                key=lambda x: (x.priority, x.source_id),
            )
        )

    def snapshot(self) -> dict:
        rows = [asdict(x) for x in sorted(self._sources.values(), key=lambda y: y.source_id)]
        return {"sources": rows, "registry_hash": stable_hash(rows)}
