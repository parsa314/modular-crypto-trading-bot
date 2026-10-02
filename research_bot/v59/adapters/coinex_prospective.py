from __future__ import annotations

from datetime import datetime, timezone
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..hashing import stable_hash
from ..microstructure import PITMetric
from ..microstructure_plane import MetricProviderResult


BASE_URL = "https://api.coinex.com/v2"


def _market(symbol: str) -> str:
    value = symbol.replace("/", "").replace("-", "").upper()
    if not value:
        raise ValueError("symbol is required")
    return value


class CoinExProspectiveDerivativesProvider:
    """Read-only current CoinEx futures-market snapshot.

    Values are timestamped as available only when this process actually
    observes them. They are therefore safe for prospective collection, but are
    not retroactively usable for historical decisions.
    """

    provider_id = "COINEX_PROSPECTIVE_DERIVATIVES_V59"

    def __init__(self, *, timeout: int = 20) -> None:
        self.timeout = int(timeout)
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")

    def fetch_metrics(self, *, symbol: str) -> MetricProviderResult:
        market = _market(symbol)
        query = urlencode({"market": market})
        url = f"{BASE_URL}/futures/market?{query}"
        request = Request(url, headers={"User-Agent": "modular-crypto-research-bot/v59"})
        with urlopen(request, timeout=self.timeout) as response:
            raw = response.read()
        payload = json.loads(raw.decode("utf-8"))
        if payload.get("code") != 0:
            raise RuntimeError(f"CoinEx public market error: {payload}")
        rows = payload.get("data") or []
        if not rows:
            raise RuntimeError(f"CoinEx returned no futures market row for {market}")
        row = rows[0]
        observed = datetime.now(timezone.utc)
        source_hash = stable_hash(
            {
                "provider_id": self.provider_id,
                "market": market,
                "observed_at": observed,
                "payload": row,
            }
        )
        values = {
            "open_interest_volume": row.get("open_interest_volume"),
            "maker_fee_rate": row.get("maker_fee_rate"),
            "taker_fee_rate": row.get("taker_fee_rate"),
        }
        metrics = []
        for name, value in values.items():
            if value is None:
                continue
            metrics.append(
                PITMetric(
                    entity=f"coinex:{market}",
                    metric=name,
                    effective_at=observed,
                    available_at=observed,
                    observed_at=observed,
                    value=float(value),
                    source="coinex_v2_futures_market_prospective",
                    source_hash=source_hash,
                    confidence=1.0,
                )
            )
        if not metrics:
            raise RuntimeError("CoinEx futures market row contained no supported metrics")
        return MetricProviderResult(
            provider_id=self.provider_id,
            metrics=tuple(metrics),
            raw_payload=raw,
            provider_metadata={
                "availability_policy": "OBSERVED_NOW_ONLY",
                "historical_pit_authorized": False,
                "endpoint": "/v2/futures/market",
                "market": market,
            },
        )
