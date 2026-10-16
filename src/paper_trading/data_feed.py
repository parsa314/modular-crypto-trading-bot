from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd

from .paper_broker import OrderBook


@dataclass(frozen=True)
class Snapshot:
    timestamp: datetime
    prices: dict[str, float]
    volume24: dict[str, float]
    price_times: dict[str, datetime]
    history: pd.DataFrame  # closed UTC daily bars only
    source: str


class BinancePublicFeed:
    REST = "https://data-api.binance.vision"
    WS = "wss://data-stream.binance.vision/ws/!miniTicker@arr"
    ALLOWED = {"/api/v3/klines", "/api/v3/ticker/24hr", "/api/v3/depth"}

    def __init__(self, symbols: list[str], mark_seconds: float = 5):
        self.symbols = symbols
        self.mark_seconds = mark_seconds
        self.prices: dict[str, float] = {}
        self.volumes: dict[str, float] = {}
        self.times: dict[str, datetime] = {}
        self.history = pd.DataFrame()
        self.history_day = ""

    def _get(self, route: str, **params):
        if route not in self.ALLOWED:
            raise ValueError("only public market-data GET routes are available")
        with urlopen(self.REST + route + "?" + urlencode(params), timeout=15) as response:
            result = json.load(response)
        if isinstance(result, dict) and "code" in result:
            raise RuntimeError(f"Binance market data error: {result['code']}")
        return result

    def _daily(self, now: datetime) -> pd.DataFrame:
        frames = []
        now_ms = int(now.timestamp() * 1000)
        for s in self.symbols:
            rows = self._get("/api/v3/klines", symbol=s, interval="1d", limit=120, endTime=now_ms - 1)
            closed = [r for r in rows if int(r[6]) < now_ms]
            frames.extend({"symbol": s, "date": pd.to_datetime(r[0], unit="ms", utc=True),
                           "open": float(r[1]), "close": float(r[4]), "quote_volume": float(r[7])} for r in closed)
        if not frames:
            raise RuntimeError("no closed daily market data")
        return pd.DataFrame(frames)

    async def _bootstrap(self):
        now = datetime.now(timezone.utc)
        rows = await asyncio.to_thread(self._get, "/api/v3/ticker/24hr", symbols=json.dumps(self.symbols, separators=(",", ":")))
        if {r["symbol"] for r in rows} != set(self.symbols):
            raise RuntimeError("incomplete public ticker snapshot")
        for row in rows:
            s = row["symbol"]
            self.prices[s] = float(row["lastPrice"])
            self.volumes[s] = float(row["quoteVolume"])
            self.times[s] = datetime.fromtimestamp(int(row["closeTime"]) / 1000, timezone.utc)
        self.history = await asyncio.to_thread(self._daily, now)
        self.history_day = now.strftime("%Y-%m-%d")

    def _snapshot(self) -> Snapshot:
        return Snapshot(datetime.now(timezone.utc), dict(self.prices), dict(self.volumes), dict(self.times), self.history, "binance_public_websocket")

    async def snapshots(self):
        from websockets.asyncio.client import connect

        await self._bootstrap()
        yield self._snapshot()
        while True:
            # Planned rotation precedes Binance's 24-hour connection expiry.
            async with connect(self.WS, open_timeout=20, ping_interval=20, ping_timeout=20, max_size=4_000_000) as socket:
                connected = time.monotonic()
                last_emit = 0.0
                while time.monotonic() - connected < 23 * 3600:
                    message = await asyncio.wait_for(socket.recv(), timeout=30)
                    rows = json.loads(message)
                    if not isinstance(rows, list):
                        raise RuntimeError("unexpected public websocket message")
                    for row in rows:
                        s = row.get("s")
                        if s in self.symbols:
                            self.prices[s] = float(row["c"])
                            self.volumes[s] = float(row["q"])
                            self.times[s] = datetime.fromtimestamp(int(row["E"]) / 1000, timezone.utc)
                    now = datetime.now(timezone.utc)
                    if now.strftime("%Y-%m-%d") != self.history_day:
                        self.history = await asyncio.to_thread(self._daily, now)
                        self.history_day = now.strftime("%Y-%m-%d")
                    if time.monotonic() - last_emit >= self.mark_seconds:
                        last_emit = time.monotonic()
                        yield self._snapshot()
            # Only a planned rotation reconnects. Any exception propagates and halts.
            await self._bootstrap()

    async def order_book(self, symbol: str, snapshot: Snapshot) -> OrderBook:
        row = await asyncio.to_thread(self._get, "/api/v3/depth", symbol=symbol, limit=100)
        return OrderBook(tuple((float(p), float(q)) for p, q in row["bids"]),
                         tuple((float(p), float(q)) for p, q in row["asks"]), datetime.now(timezone.utc))


class ParquetFeed:
    def __init__(self, path: Path, symbol: str, start: str, end: str,
                 bar_minutes: int = 15, delay_seconds: float = 0):
        self.path, self.symbol = path, symbol
        self.start, self.end = pd.Timestamp(start), pd.Timestamp(end)
        if self.start.tzinfo is None or self.end.tzinfo is None or self.start >= self.end:
            raise ValueError("replay start/end require UTC timezone, end exclusive")
        self.minutes, self.delay = bar_minutes, delay_seconds

    async def snapshots(self):
        # Predicate pushdown limits reading to the explicitly selected period.
        frame = pd.read_parquet(self.path, filters=[("timestamp", ">=", self.start - pd.Timedelta(days=120)), ("timestamp", "<", self.end)])
        required = {"timestamp", "open", "close", "volume"}
        if not required.issubset(frame):
            raise ValueError("parquet requires timestamp/open/close/volume")
        frame = frame.sort_values("timestamp").reset_index(drop=True)
        frame["timestamp"] = pd.to_datetime(frame.timestamp, utc=True)
        if frame.empty or frame.timestamp.duplicated().any():
            raise ValueError("empty or duplicate replay bars")
        if (frame.timestamp.diff().dropna() != pd.Timedelta(minutes=self.minutes)).any():
            raise ValueError("replay data gap; prices will not be interpolated")
        for c in ("open", "close", "volume"):
            frame[c] = pd.to_numeric(frame[c], errors="raise")
        if "quote_volume" not in frame:
            frame["quote_volume"] = frame.volume * frame.close
            source = "parquet_quote_volume_proxy_model_slippage"
        else:
            source = "parquet_model_slippage"
        frame["date"] = frame.timestamp.dt.normalize()
        per_day = 1440 // self.minutes
        daily = frame.groupby("date").agg(open=("open", "first"), close=("close", "last"), quote_volume=("quote_volume", "sum"), count=("close", "size")).reset_index()
        daily = daily[daily["count"] == per_day].drop(columns="count")
        daily["symbol"] = self.symbol
        volumes = frame.quote_volume.rolling(per_day, min_periods=per_day).sum()
        emitted = False
        for i, row in enumerate(frame.itertuples()):
            available = row.timestamp + pd.Timedelta(minutes=self.minutes)
            if available < self.start or available >= self.end:
                continue
            now = available.to_pydatetime()
            history = daily[daily.date < available.normalize()].copy()
            q = float(volumes.iloc[i])
            if pd.isna(q):
                continue
            emitted = True
            yield Snapshot(now, {self.symbol: float(row.close)}, {self.symbol: q}, {self.symbol: now}, history, source)
            if self.delay:
                await asyncio.sleep(self.delay)
        if not emitted:
            raise ValueError("no replay observations in selected dates")

    async def order_book(self, symbol: str, snapshot: Snapshot) -> OrderBook:
        price = snapshot.prices[symbol]
        return OrderBook(((price, 1e20),), ((price, 1e20),), snapshot.timestamp, "parquet_model_only")
