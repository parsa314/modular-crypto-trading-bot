"""L0 configuration, extracted from the existing operator CLI without model imports.

Configuration describes limits; it never grants permission to place orders.
The foundation command supports BACKTEST initialization only.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json
import math
from pathlib import Path


def timeframe_milliseconds(timeframe: str) -> int:
    mapping = {"1m": 60000, "5m": 300000, "15m": 900000, "30m": 1800000,
               "1h": 3600000, "4h": 14400000, "1d": 86400000, "1w": 604800000}
    if not isinstance(timeframe, str) or timeframe not in mapping:
        raise ValueError("Unsupported timeframe for historical pagination")
    return mapping[timeframe]


@dataclass(frozen=True)
class LiveConfig:
    exchange_id: str
    symbol: str
    timeframe: str
    capital_limit_quote: float
    max_order_quote: float
    max_daily_loss: float
    max_drawdown: float
    max_position_weight: float = .35
    max_fee_bps: float = 20.0
    max_slippage_bps: float = 10.0
    max_spread_bps: float = 25.0
    quote_max_age_seconds: float = 10.0
    entry_grace_seconds: float = 60.0
    poll_seconds: float = 10.0
    kill_file: str = "LIVE_STOP"

    def __post_init__(self):
        if self.exchange_id not in {"coinex", "binance", "okx"}:
            raise ValueError("supported spot venues: coinex, binance, okx")
        if not isinstance(self.symbol, str) or self.symbol.count("/") != 1 or ":" in self.symbol:
            raise ValueError("one unleveraged BASE/QUOTE spot symbol is required")
        if any(not part.strip() or part != part.strip() for part in self.symbol.split("/")):
            raise ValueError("invalid spot symbol")
        bar_seconds = timeframe_milliseconds(self.timeframe) / 1000
        for name in ("capital_limit_quote", "max_order_quote", "max_daily_loss", "max_drawdown",
                     "max_position_weight", "max_fee_bps", "max_slippage_bps", "max_spread_bps",
                     "quote_max_age_seconds", "entry_grace_seconds", "poll_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        if self.max_order_quote > self.capital_limit_quote:
            raise ValueError("order ceiling cannot exceed capital ceiling")
        if not 0 < self.max_daily_loss < 1 or not 0 < self.max_drawdown < 1 or not 0 < self.max_position_weight <= 1:
            raise ValueError("loss/drawdown/position limits must be fractions")
        if max(self.max_fee_bps, self.max_slippage_bps, self.max_spread_bps) >= 1000:
            raise ValueError("fee/slippage/spread limits must be below 1000 bps")
        if self.entry_grace_seconds >= bar_seconds or self.poll_seconds >= bar_seconds:
            raise ValueError("entry grace and polling must be shorter than one bar")
        if not isinstance(self.kill_file, str) or not self.kill_file.strip():
            raise ValueError("kill_file must be a nonempty path")
        object.__setattr__(self, "kill_file", str(Path(self.kill_file).expanduser().resolve()))


class DeploymentMode(str, Enum):
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    LIVE_MICRO = "LIVE_MICRO"
    LIVE_FULL = "LIVE_FULL"


class ConfigError(ValueError):
    """A public reason code with no user-supplied values or secrets."""


@dataclass(frozen=True)
class GovernanceConfig:
    """Compose the existing v0 live limits with the MASTER v3 governance ceiling.

    Frozen values prevent ordinary reassignment. They are not a security sandbox
    for arbitrary Python code. Only trusted operator code may construct L0.
    """

    mode: DeploymentMode = DeploymentMode.BACKTEST
    live: LiveConfig | None = None
    full_allocation_quote: float | None = None
    max_daily_loss_fraction: float = .02
    max_drawdown_fraction: float = .10

    def __post_init__(self):
        try:
            object.__setattr__(self, "mode", DeploymentMode(self.mode))
        except (ValueError, TypeError):
            raise ConfigError("INVALID_DEPLOYMENT_MODE") from None
        if self.live is not None and not isinstance(self.live, LiveConfig):
            raise ConfigError("INVALID_LIVE_CONFIG")
        for name, ceiling in (("max_daily_loss_fraction", .02), ("max_drawdown_fraction", .10)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= ceiling:
                raise ConfigError("GOVERNANCE_LIMIT_OUT_OF_RANGE")
        if self.full_allocation_quote is not None:
            value = self.full_allocation_quote
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ConfigError("INVALID_FULL_ALLOCATION")
        if self.live is not None:
            if self.live.max_daily_loss > self.max_daily_loss_fraction or self.live.max_drawdown > self.max_drawdown_fraction:
                raise ConfigError("LIVE_LIMIT_EXCEEDS_GOVERNANCE")
            if self.full_allocation_quote is not None and self.live.capital_limit_quote > self.full_allocation_quote:
                raise ConfigError("CAPITAL_EXCEEDS_FULL_ALLOCATION")
        if self.mode in {DeploymentMode.LIVE_MICRO, DeploymentMode.LIVE_FULL}:
            if self.live is None or self.full_allocation_quote is None:
                raise ConfigError("LIVE_CONFIGURATION_INCOMPLETE")
            if self.mode == DeploymentMode.LIVE_MICRO and self.live.capital_limit_quote > self.full_allocation_quote * .05:
                raise ConfigError("MICRO_CAPITAL_EXCEEDS_FIVE_PERCENT")

    def public_dict(self) -> dict:
        result = asdict(self)
        result["mode"] = self.mode.value
        return result

    @property
    def sha256(self) -> str:
        payload = json.dumps(self.public_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(payload.encode()).hexdigest()

    @classmethod
    def from_json(cls, path: str | Path) -> GovernanceConfig:
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ConfigError("DUPLICATE_CONFIG_KEY")
                result[key] = value
            return result

        def reject_constant(value):
            raise ConfigError("NONFINITE_CONFIG_VALUE")

        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique_object,
                              parse_constant=reject_constant)
            if not isinstance(data, dict):
                raise ConfigError("CONFIG_OBJECT_REQUIRED")
            if data.get("live") is not None:
                if not isinstance(data["live"], dict):
                    raise ConfigError("INVALID_LIVE_CONFIG")
                data["live"] = LiveConfig(**data["live"])
            return cls(**data)
        except ConfigError:
            raise
        except (ValueError, TypeError, OverflowError):
            raise ConfigError("INVALID_CONFIG_SCHEMA") from None
