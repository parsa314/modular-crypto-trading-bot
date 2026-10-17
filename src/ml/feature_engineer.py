"""Closed-bar features and vectorized versions of the existing v1 arms.

Calendar-day lookbacks are elapsed time, not 20 M15 observations. HMM fitting
uses a warmup prefix ending before any model-training example is admitted.
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd
from scipy.special import logsumexp

FEATURES = ["confidence", "direction", "regime_trend", "regime_range", "regime_volatile",
            "atr_ratio_20d", "volume_ratio_20d", "hour_sin", "hour_cos", "weekday_sin",
            "weekday_cos", "distance_sma50", "distance_sma200", "correlation_btc_30d",
            "momentum_5d", "momentum_10d", "momentum_20d", "rsi", "macd_atr",
            "macd_signal_atr", "bb_position", "native_daily", "strategy_code"]
STRATEGY_CODES = {name: i for i, name in enumerate(("v1_brooks", "v1_confluence", "v1_ichimoku",
                 "v1_ict", "v1_smc", "v4_donchian", "v5_rsi", "v6_volatility"))}


def validate_bars(frame: pd.DataFrame) -> pd.DataFrame:
    x = frame.copy()
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    if not required.issubset(x):
        raise ValueError(f"Missing OHLCV: {sorted(required - set(x))}")
    x["timestamp"] = pd.to_datetime(x.timestamp, utc=True)
    if x.timestamp.duplicated().any() or not x.timestamp.is_monotonic_increasing:
        raise ValueError("OHLCV must have unique increasing timestamps")
    values = x[["open", "high", "low", "close", "volume"]].to_numpy(float)
    if not np.isfinite(values).all() or (values[:, :4] <= 0).any() or (values[:, 4] < 0).any():
        raise ValueError("Invalid OHLCV numbers")
    if (x.high < x[["open", "close", "low"]].max(axis=1)).any() or (x.low > x[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError("Inconsistent OHLC")
    if "available_at" not in x:
        x["available_at"] = x.timestamp + pd.Timedelta(minutes=15)
    x["available_at"] = pd.to_datetime(x.available_at, utc=True)
    if (x.available_at < x.timestamp + pd.Timedelta(minutes=15)).any():
        raise ValueError("A signal cannot observe an unclosed M15 bar")
    return x.reset_index(drop=True)


def atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    previous = frame.close.shift(1)
    tr = pd.concat([frame.high - frame.low, (frame.high - previous).abs(),
                    (frame.low - previous).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    result = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    return result.where(loss > 0, np.where(gain > 0, 100., 50.))


@dataclass
class CausalRegime:
    model: object
    mean: float
    scale: float
    state_names: dict[int, str]
    fitted_until: pd.Timestamp

    @classmethod
    def fit(cls, bars: pd.DataFrame, warmup_end="2022-04-01T00:00:00Z"):
        from hmmlearn.hmm import GaussianHMM
        returns = np.log(bars.close).diff().fillna(0.)
        cutoff = pd.Timestamp(warmup_end)
        prefix = returns[bars.available_at < cutoff].to_numpy()
        if len(prefix) < 500:
            raise ValueError("HMM needs at least 500 prefix observations before warmup_end")
        mean, scale = float(prefix.mean()), max(float(prefix.std()), 1e-8)
        model = GaussianHMM(n_components=3, covariance_type="diag", n_iter=60,
                            random_state=42, min_covar=1e-4)
        model.fit(((prefix - mean) / scale).reshape(-1, 1))
        volatile = int(np.argmax(model.covars_.reshape(3)))
        other = [i for i in range(3) if i != volatile]
        trend = max(other, key=lambda i: abs(model.means_[i, 0]))
        names = {volatile: "volatile", trend: "trend", next(i for i in other if i != trend): "range"}
        return cls(model, mean, scale, names, cutoff)

    def filter(self, bars: pd.DataFrame) -> pd.DataFrame:
        # Forward recursion only. predict()/Viterbi/smoothed posteriors can use
        # future observations and must not be used for these feature rows.
        z = (np.log(bars.close).diff().fillna(0.).to_numpy() - self.mean) / self.scale
        means = self.model.means_.reshape(3)
        variances = self.model.covars_.reshape(3)
        transition = np.log(np.maximum(self.model.transmat_, 1e-300))
        belief = np.log(np.maximum(self.model.startprob_, 1e-300))
        probabilities = np.empty((len(z), 3))
        for i, value in enumerate(z):
            likelihood = -.5 * (np.log(2 * np.pi * variances) + (value - means) ** 2 / variances)
            if i:
                belief = logsumexp(belief[:, None] + transition, axis=0)
            belief = belief + likelihood
            belief -= logsumexp(belief)
            probabilities[i] = np.exp(belief)
        return pd.DataFrame({f"regime_{self.state_names[j]}": probabilities[:, j]
                             for j in range(3)}, index=bars.index)


def v1_scores(bars: pd.DataFrame) -> dict[str, np.ndarray]:
    """Signed 0..3 scores, matching the frozen four independent arm modules.

    Original source: c547f45d, research_bot/strategies. The v1 confluence is
    retained separately; individual-arm candidate counts are never called
    counts of original v1 executed trades.
    """
    x = bars
    c, o, h, l = x.close, x.open, x.high, x.low
    a = atr(x)
    midpoint = lambda n: (h.rolling(n).max() + l.rolling(n).min()) / 2
    tenkan, kijun = midpoint(9), midpoint(26)
    ca, cb = ((tenkan + kijun) / 2).shift(26), midpoint(52).shift(26)
    upper, lower = pd.concat([ca, cb], axis=1).max(axis=1), pd.concat([ca, cb], axis=1).min(axis=1)
    up, down, tkup, tkdown = c > upper, c < lower, tenkan > kijun, tenkan < kijun
    iu = up & tkup & ((up & (c.shift() <= upper.shift())) | (tkup & (tenkan.shift() <= kijun.shift())) | ((c.shift() <= kijun.shift()) & (c > kijun)))
    idown = down & tkdown & ((down & (c.shift() >= lower.shift())) | (tkdown & (tenkan.shift() >= kijun.shift())) | ((c.shift() >= kijun.shift()) & (c < kijun)))
    ich = np.where(iu, 2 + (c > h.shift(26)).astype(int), np.where(idown, -(2 + (c < l.shift(26)).astype(int)), 0))
    span = (h - l).replace(0, np.nan)
    body = (c - o).abs() / span
    upbreak, downbreak = (c > h.shift().rolling(12).max()) & (c > o), (c < l.shift().rolling(12).min()) & (c < o)
    obhigh = h.where(c < o).shift().ffill().where((c < o).shift().rolling(10).sum() > 0)
    oblow = l.where(c > o).shift().ffill().where((c > o).shift().rolling(10).sum() > 0)
    displacement = (span >= a) & (body >= .55)
    smc = np.where(upbreak & (c > obhigh), 2 + ((l > h.shift(2)) & displacement).astype(int),
                   np.where(downbreak & (c < oblow), -(2 + ((h < l.shift(2)) & displacement).astype(int)), 0))
    ema = c.ewm(span=20, adjust=False).mean()
    trendup, trenddown = (c > ema) & (ema > ema.shift(4)), (c < ema) & (ema < ema.shift(4))
    clv = (c - l) / span
    bu, bd = c > h.shift().rolling(5).max(), c < l.shift().rolling(5).min()
    fu, fd = (c.shift() > o.shift()) & (c > c.shift()), (c.shift() < o.shift()) & (c < c.shift())
    brooks = np.where(trendup & (c > o) & (body >= .58) & (clv >= .78) & (bu | fu), 2 + (bu & fu).astype(int),
                      np.where(trenddown & (c < o) & (body >= .58) & (clv <= .22) & (bd | fd), -(2 + (bd & fd).astype(int)), 0))
    sweepup = ((l < l.shift().rolling(20).min()) & (c > l.shift().rolling(20).min())).to_numpy()
    sweepdown = ((h > h.shift().rolling(20).max()) & (c < h.shift().rolling(20).max())).to_numpy()
    hh, ll, cc, oo, aa = [s.to_numpy() for s in (h, l, c, o, a)]
    london = x.available_at.dt.tz_convert("Europe/London").dt.hour
    ny = x.available_at.dt.tz_convert("America/New_York").dt.hour
    session = ((london >= 8) & (london < 11) | (ny >= 8) & (ny < 11)).to_numpy()
    ict = np.zeros(len(x), dtype=int)
    for i in range(89, len(x)):
        candidates = [(j, side) for j in range(i - 8, i) for side, flags in ((1, sweepup), (-1, sweepdown)) if flags[j]]
        if not candidates:
            continue
        j, side = max(candidates, key=lambda t: t[0])
        if j + 1 >= i:
            continue
        mss = cc[i] > hh[j + 1:i].max() if side == 1 else cc[i] < ll[j + 1:i].min()
        if mss:
            displacement_i = body.iloc[i] >= .55 and hh[i] - ll[i] >= .85 * aa[i] and (cc[i] > oo[i] if side == 1 else cc[i] < oo[i])
            ict[i] = side * (2 + int(displacement_i and session[i]))
    scores = {"v1_ichimoku": ich, "v1_ict": ict, "v1_smc": smc, "v1_brooks": brooks}
    matrix = np.stack(list(scores.values()), axis=1)
    positive, negative = (matrix > 0).sum(axis=1), (matrix < 0).sum(axis=1)
    total = matrix.sum(axis=1)
    scores["v1_confluence"] = np.where((positive >= 3) & (negative == 0) & (total >= 7), total,
                                       np.where((negative >= 3) & (positive == 0) & (total <= -7), total, 0))
    for values in scores.values():
        values[:89] = 0
    return scores


def candidate_signals(bars: pd.DataFrame) -> pd.DataFrame:
    """Spot BUY candidates only; v3 is absent without a cross-sectional panel."""
    frames = []
    for name, score in v1_scores(bars).items():
        ix = np.flatnonzero(score > 0)
        frames.append(pd.DataFrame({"bar_index": ix, "strategy": name,
                                    "confidence": score[ix] / (12 if name == "v1_confluence" else 3),
                                    "atr": bars.atr.iloc[ix].to_numpy(), "native_daily": 0,
                                    "horizon_m15_bars": 20}))
    daily = bars.set_index("timestamp").resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    daily = daily[daily.index + pd.Timedelta(days=1) <= bars.available_at.iloc[-1]]
    daily = daily.dropna(subset=["open", "high", "low", "close"])
    daily["atr"] = atr(daily)
    signal = {"v4_donchian": daily.close > daily.high.shift().rolling(55).max(),
              "v5_rsi": rsi(daily.close) < 30,
              "v6_volatility": daily.close > daily.high.shift().rolling(20).max() + .25 * daily.atr}
    for name, mask in signal.items():
        rows = daily[mask & daily.atr.notna()]
        positions = bars.timestamp.searchsorted(rows.index + pd.Timedelta(days=1)) - 1
        frames.append(pd.DataFrame({"bar_index": positions, "strategy": name, "confidence": 1.,
                                    "atr": rows.atr.to_numpy(), "native_daily": 1,
                                    "horizon_m15_bars": 20 * 96}))
    result = pd.concat(frames, ignore_index=True).sort_values(["bar_index", "strategy"]).reset_index(drop=True)
    result["direction"] = 1
    result["strategy_code"] = result.strategy.map(STRATEGY_CODES).astype(int)
    result["event_id"] = np.arange(len(result))
    return result


def build_features(frame: pd.DataFrame, regime: CausalRegime | None = None,
                   btc_bars: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame, CausalRegime]:
    bars = validate_bars(frame)
    if btc_bars is not None:
        btc_bars = validate_bars(btc_bars)
        if not bars.timestamp.equals(btc_bars.timestamp):
            raise ValueError("BTC timestamps must align exactly with market bars")
        if (btc_bars.available_at > bars.available_at).any():
            raise ValueError("BTC context cannot become available after the decision bar")
    bars["atr"] = atr(bars)
    regime = regime or CausalRegime.fit(bars)
    state = regime.filter(bars if btc_bars is None else validate_bars(btc_bars))
    if len(state) != len(bars):
        raise ValueError("BTC bars must align to feature bars")
    for col in state:
        bars[col] = state[col]
    t = pd.DatetimeIndex(bars.timestamp)
    cadence = bars.timestamp.diff().dropna().median()
    observations_per_day = max(1, int(round(pd.Timedelta(days=1) / cadence)))
    close = pd.Series(bars.close.to_numpy(), index=t)
    a = pd.Series(bars.atr.to_numpy(), index=t)
    volume = pd.Series(bars.volume.to_numpy(), index=t)
    bars["atr_ratio_20d"] = (a / a.rolling("20D", closed="left", min_periods=observations_per_day).mean()).to_numpy()
    bars["volume_ratio_20d"] = (volume / volume.rolling("20D", closed="left", min_periods=observations_per_day).mean().replace(0, np.nan)).to_numpy()
    bars["distance_sma50"] = bars.close / bars.close.rolling(50).mean() - 1
    bars["distance_sma200"] = bars.close / bars.close.rolling(200).mean() - 1
    for days in (5, 10, 20):
        lag = close.reindex(t - pd.Timedelta(days=days), method="ffill", tolerance=2 * cadence)
        bars[f"momentum_{days}d"] = bars.close.to_numpy() / lag.to_numpy() - 1
    if btc_bars is None:
        bars["correlation_btc_30d"] = 1.  # BTC correlated with itself, explicitly recorded.
    else:
        btc = pd.Series(btc_bars.close.to_numpy(), index=t).pct_change(fill_method=None)
        bars["correlation_btc_30d"] = close.pct_change(fill_method=None).rolling("30D", min_periods=observations_per_day).corr(btc).to_numpy()
    hour = bars.available_at.dt.hour + bars.available_at.dt.minute / 60
    weekday = bars.available_at.dt.dayofweek
    bars["hour_sin"], bars["hour_cos"] = np.sin(2 * np.pi * hour / 24), np.cos(2 * np.pi * hour / 24)
    bars["weekday_sin"], bars["weekday_cos"] = np.sin(2 * np.pi * weekday / 7), np.cos(2 * np.pi * weekday / 7)
    bars["rsi"] = rsi(bars.close) / 100
    macd = bars.close.ewm(span=12, adjust=False).mean() - bars.close.ewm(span=26, adjust=False).mean()
    bars["macd_atr"] = macd / bars.atr
    bars["macd_signal_atr"] = macd.ewm(span=9, adjust=False).mean() / bars.atr
    mid, std = bars.close.rolling(20).mean(), bars.close.rolling(20).std()
    bars["bb_position"] = (bars.close - (mid - 2 * std)) / (4 * std.replace(0, np.nan))
    signals = candidate_signals(bars)
    shared = [c for c in FEATURES if c not in signals]
    features = signals.join(bars[shared + ["timestamp", "available_at"]], on="bar_index")
    features = features[features.available_at >= regime.fitted_until]
    features = features.replace([np.inf, -np.inf], np.nan).dropna(subset=FEATURES).reset_index(drop=True)
    features["symbol"] = "BTCUSDT" if btc_bars is None else frame.get("symbol", pd.Series(["UNKNOWN"])).iloc[0]
    return features, bars, regime
