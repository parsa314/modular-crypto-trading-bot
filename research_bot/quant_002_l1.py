"""Isolated deterministic OHLCV research simulation; never a broker or OMS.

L1 cannot establish queue position, actual bid/ask, partial fills or stop prices.
All fills are scenario assumptions and no result is operational evidence.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_DOWN, localcontext
import hashlib
import json
from typing import Any


def _d(value: Any) -> Decimal:
    if isinstance(value, (float, bool)):
        raise ValueError("EXACT_DECIMAL_REQUIRED")
    try:
        n = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("INVALID_DECIMAL") from exc
    if not n.is_finite():
        raise ValueError("INVALID_DECIMAL")
    return n


def _utc(value: datetime | str) -> datetime:
    dt = datetime.fromisoformat(value) if isinstance(value, str) else value
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("TIMEZONE_REQUIRED")
    return dt.astimezone(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


@dataclass(frozen=True)
class L1Bar:
    timestamp: datetime
    available_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    source_sha256: str

    def payload(self) -> dict[str, str]:
        t, a = _utc(self.timestamp), _utc(self.available_at)
        prices = [_d(v) for v in (self.open, self.high, self.low, self.close)]
        op, high, low, close = prices
        if t.minute or t.second or t.microsecond or a < t + timedelta(hours=1):
            raise ValueError("INCOMPLETE_OR_MISALIGNED_BAR")
        if min(prices) <= 0 or high < max(op, close) or low > min(op, close) or low > high:
            raise ValueError("INVALID_OHLC")
        if len(self.source_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.source_sha256):
            raise ValueError("SOURCE_SHA256_REQUIRED")
        return {"timestamp":t.isoformat(), "available_at":a.isoformat(), "open":str(op),
                "high":str(high), "low":str(low), "close":str(close), "source_sha256":self.source_sha256}


@dataclass(frozen=True)
class CostScenario:
    name: str
    fees_rt_bps: Decimal
    spread_rt_bps: Decimal
    slippage_rt_bps: Decimal

    def __post_init__(self):
        if not self.name or min(_d(self.fees_rt_bps), _d(self.spread_rt_bps), _d(self.slippage_rt_bps)) < 0:
            raise ValueError("INVALID_COST_SCENARIO")

    @property
    def total_bps(self) -> Decimal:
        return _d(self.fees_rt_bps) + _d(self.spread_rt_bps) + _d(self.slippage_rt_bps)


class L1Simulator:
    """One-asset long-only finite-state replay with deterministic event journal.

    `stop_first` vetoes unproven target-limit fills; `target_touch_assumed` is
    an explicitly optimistic sensitivity scenario, not an observed execution.
    """
    def __init__(self, scenario: CostScenario, *, cash: Decimal = Decimal("10000"),
                 mode: str = "stop_first", source_id: str, source_sha256: str):
        if (mode not in {"stop_first", "target_touch_assumed"} or not source_id or len(source_sha256) != 64
            or any(c not in "0123456789abcdef" for c in source_sha256)):
            raise ValueError("INVALID_SIMULATOR_CONFIG")
        if scenario.total_bps >= Decimal("20000"):
            raise ValueError("INVALID_COST_SCENARIO")
        initial = _d(cash)
        if initial <= 0:
            raise ValueError("INVALID_STARTING_CASH")
        self.config = {"scenario":scenario.name,"fees_rt_bps":str(_d(scenario.fees_rt_bps)),
            "spread_rt_bps":str(_d(scenario.spread_rt_bps)),"slippage_rt_bps":str(_d(scenario.slippage_rt_bps)),
            "mode":mode,"source_id":source_id,"source_sha256":source_sha256,
            "starting_cash":str(initial),"contract":"ICH_KIJUN_PULLBACK_V1"}
        self.state = {"cash":str(initial),"peak":str(initial),"day":None,"day_open":None,
            "daily_halt":False,"hard_halt":False,"last_bar":None,"pending":None,"position":None,
            "events":[],"trades":[],"signals":{},"bars":{},"nav_history":[],"total_fees":"0"}

    def _event(self, kind: str, at: datetime, available: datetime, *, key: str,
               price: Decimal | None = None, quantity: Decimal | None = None, **detail: Any) -> None:
        body = {"kind":kind,"event_at":_utc(at).isoformat(),"available_at":_utc(available).isoformat(),
                "key":key,"price":None if price is None else str(price),
                "quantity":None if quantity is None else str(quantity),"source_id":self.config["source_id"],
                "source_sha256":self.config["source_sha256"],
                "contract":self.config["contract"],"scenario":self.config["scenario"],"mode":self.config["mode"],
                "details":detail}
        body["event_id"] = _hash(body)
        if any(e["event_id"] == body["event_id"] for e in self.state["events"]):
            return
        self.state["events"].append(body)

    def signal(self, setup: dict[str, Any]) -> bool:
        required={"setup_id","version","direction","evidence_at","market_regime","timeframe_context","trigger",
                  "confirmation","entry_rule","invalidation","stop_rule","target_rule","timeout",
                  "cancellation_rule","source_provenance","execution_eligible"}
        if not required.issubset(setup) or setup["version"] != self.config["contract"] or setup["direction"] != "LONG":
            raise ValueError("INVALID_SETUP_CONTRACT")
        key = str(setup["setup_id"])
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("SETUP_ID_REQUIRED")
        fingerprint = _hash(setup)
        prior = self.state["signals"].get(key)
        if prior is not None:
            if prior != fingerprint:
                raise ValueError("CONFLICTING_SIGNAL")
            return False
        immutable={k:v for k,v in setup.items() if k not in {"setup_id","execution_eligible","evidence_state"}}
        if _hash(immutable) != key:
            raise ValueError("SETUP_IDENTITY_MISMATCH")
        at = _utc(setup["evidence_at"])
        last = self.state["last_bar"]
        if last is None or at < _utc(last["available_at"]) or at - _utc(last["available_at"]) > timedelta(minutes=15):
            raise ValueError("STALE_OR_FUTURE_SIGNAL")
        stop = _d(setup["invalidation"]["level"])
        h1 = setup["source_provenance"].get("H1",{})
        h4 = setup["source_provenance"].get("H4",{})
        context=setup["timeframe_context"]
        if (stop <= 0 or setup["execution_eligible"] is not False
            or setup["market_regime"].get("type") != "H4_ABOVE_VISIBLE_CLOUD_TENKAN_ABOVE_KIJUN"
            or setup["trigger"].get("type") != "H1_KIJUN_TOUCH_RECLAIM_BULLISH"
            or setup["confirmation"].get("type") != "H4_CLOUD_TK"
            or setup["entry_rule"].get("kind") != "FIRST_H1_OPEN_STRICTLY_AFTER_DECISION"
            or setup["entry_rule"].get("max_delay_hours") != 2
            or setup["invalidation"].get("kind") != "TRIGGER_BAR_LOW"
            or setup["stop_rule"].get("kind") != "TRIGGER_BAR_LOW"
            or _d(setup["stop_rule"].get("level")) != stop
            or setup["target_rule"].get("kind") != "ENTRY_PLUS_R_MULTIPLE"
            or setup["target_rule"].get("multiple") != "2"
            or setup["timeout"].get("count") != 8
            or setup["timeout"].get("unit") != "COMPLETED_H1_BARS_FROM_ENTRY"
            or not {"NO_ELIGIBLE_ENTRY_WITHIN_2H","ENTRY_AT_OR_BELOW_INVALIDATION","STALE_OR_INVALID_DATA","RISK_VETO","UNVERIFIED_EXECUTION_POLICY"}.issubset(set(setup["cancellation_rule"]))
            or h1.get("raw_sha256") != self.config["source_sha256"] or h4.get("raw_sha256") != self.config["source_sha256"]
            or h1.get("source_id") != self.config["source_id"] or h4.get("source_id") != self.config["source_id"]
            or h1.get("timeframe") != "H1" or h4.get("timeframe") != "H4"
            or (h1.get("venue"),h1.get("instrument")) != (h4.get("venue"),h4.get("instrument"))
            or not h1.get("venue") or not h1.get("instrument")
            or _utc(context["h1_available_at"]) != _utc(last["available_at"])
            or not timedelta(0) <= at - _utc(context["h4_available_at"]) <= timedelta(hours=4,minutes=15)
            or _utc(setup["trigger"]["bar_open"]) != _utc(last["timestamp"])
            or _d(setup["trigger"]["bar_low"]) != stop or _d(last["low"]) != stop):
            raise ValueError("INVALID_FROZEN_SETUP")
        self.state["signals"][key] = fingerprint
        self._event("SIGNAL",at,at,key=key)
        if self.state["position"] or self.state["pending"] or self.state["daily_halt"] or self.state["hard_halt"]:
            self._event("CANCELLED",at,at,key=key,reason="POSITION_PENDING_OR_RISK_HALT")
            return False
        self.state["pending"] = {"id":key,"decision_at":at.isoformat(),"stop":str(stop)}
        self._event("ORDER_INTENT",at,at,key=key,order_type="MARKET_AT_NEXT_ELIGIBLE_OPEN")
        self._event("SUBMITTED_SIMULATED",at,at,key=key,partial_fill="UNMODELED")
        return True

    def _latch(self, equity: Decimal) -> None:
        peak = max(_d(self.state["peak"]), equity)
        self.state["peak"] = str(peak)
        if equity <= peak * Decimal("0.95"):
            self.state["hard_halt"] = True
        if self.state["day_open"] is not None and equity <= _d(self.state["day_open"]) * Decimal("0.98"):
            self.state["daily_halt"] = True

    def _rates(self) -> tuple[Decimal, Decimal, Decimal]:
        return tuple(_d(self.config[k]) / Decimal("20000") for k in ("fees_rt_bps","spread_rt_bps","slippage_rt_bps"))

    def expire(self, *, as_of: datetime) -> bool:
        """Expire a pending intent by wall-clock evidence without inventing bars."""
        at = _utc(as_of)
        if self.state["last_bar"] and at < _utc(self.state["last_bar"]["available_at"]):
            raise ValueError("EXPIRY_TIME_REGRESSION")
        order = self.state["pending"]
        if order is None:
            return False
        deadline = _utc(order["decision_at"]) + timedelta(hours=2)
        if at <= deadline:
            return False
        # Wall-clock time is not an event-time watermark: an earlier eligible
        # candle may still arrive. Never turn missing data into a no-fill.
        if not self.state["last_bar"] or _utc(self.state["last_bar"]["timestamp"]) < deadline:
            raise ValueError("EXPIRY_UNRESOLVED_MISSING_MARKET_DATA")
        self._event("EXPIRED",deadline,at,key=order["id"],reason="NO_ELIGIBLE_OPEN_WITHIN_2H")
        self.state["pending"] = None
        return True

    def bar(self, bar: L1Bar) -> bool:
        before=deepcopy(self.state)
        try:
            with localcontext() as ctx:
                values=(_d(bar.open),_d(bar.high),_d(bar.low),_d(bar.close),_d(self.state["cash"]))
                ctx.prec=max(320,5*sum(len(v.as_tuple().digits) for v in values)+100)
                return self._bar(bar)
        except Exception:
            self.state=before
            raise

    def _bar(self, bar: L1Bar) -> bool:
        b=bar.payload(); stamp=_utc(b["timestamp"]); available=_utc(b["available_at"])
        if b["source_sha256"] != self.config["source_sha256"]:
            raise ValueError("MARKET_SOURCE_MISMATCH")
        previous = self.state["bars"].get(b["timestamp"])
        if previous is not None:
            if previous != _hash(b):
                raise ValueError("CONFLICTING_MARKET_EVENT")
            return False
        last=self.state["last_bar"]
        if last and (stamp != _utc(last["timestamp"]) + timedelta(hours=1) or available < _utc(last["available_at"])):
            raise ValueError("MISSING_OR_OUT_OF_ORDER_BAR")
        if last and stamp < _utc(last["available_at"]):
            raise ValueError("LATE_BAR_OVERLAP")
        op,high,low,close=map(_d,(b["open"],b["high"],b["low"],b["close"]))
        position=self.state["position"]
        if self.state["day"] != stamp.date().isoformat():
            if stamp.hour != 0:
                self.state["daily_halt"] = True
                self.state["day_open"] = None
            else:
                opening=_d(self.state["cash"]) + (_d(position["quantity"])*op if position else Decimal(0))
                self.state["day_open"],self.state["daily_halt"] = str(opening),False
            self.state["day"] = stamp.date().isoformat()
        fee_rate,spread_rate,slip_rate=self._rates()
        price_rate=spread_rate+slip_rate
        order=self.state["pending"]
        if order and stamp > _utc(order["decision_at"]):
            key=order["id"]; stop=_d(order["stop"])
            if stamp > _utc(order["decision_at"])+timedelta(hours=2):
                self._event("EXPIRED",stamp,available,key=key,reason="NO_ELIGIBLE_OPEN_WITHIN_2H")
            elif self.state["daily_halt"] or self.state["hard_halt"] or op <= stop:
                self._event("CANCELLED",stamp,available,key=key,reason="HALT_OR_OPEN_BELOW_STOP")
            else:
                entry=op*(Decimal(1)+price_rate)
                cash=_d(self.state["cash"])
                # Compute a conservative sizing bound using enough precision
                # to multiply the original exact Decimal inputs without loss.
                precision=max(320,3*sum(len(v.as_tuple().digits) for v in (entry,stop,fee_rate,price_rate,cash))+100)
                with localcontext() as sizing:
                    sizing.prec=precision
                    per_unit=entry*(Decimal(1)+fee_rate)-stop*(Decimal(1)-price_rate)*(Decimal(1)-fee_rate)
                    # Enforce the asset cap against NAV immediately after entry
                    # (marked at the observed open), not the larger pre-fee cash.
                    exposure_unit=entry+Decimal("0.35")*(entry*(Decimal(1)+fee_rate)-op)
                if per_unit <= 0 or cash <= 0:
                    self._event("CANCELLED",stamp,available,key=key,reason="INVALID_RISK_OR_CAPITAL")
                else:
                    with localcontext() as sizing:
                        sizing.prec=80
                        sizing.rounding=ROUND_DOWN
                        quantity=min(cash*Decimal("0.0025")/per_unit,cash*Decimal("0.35")/exposure_unit,cash/(entry*(Decimal(1)+fee_rate)))
                    with localcontext() as exact:
                        exact.prec=precision
                        while quantity > 0 and (quantity*per_unit > cash*Decimal("0.0025")
                                or quantity*exposure_unit > cash*Decimal("0.35")
                                or quantity*entry*(Decimal(1)+fee_rate) > cash):
                            with localcontext() as sizing:
                                sizing.prec=80
                                sizing.rounding=ROUND_DOWN
                                quantity=quantity.next_minus()
                    if quantity <= 0:
                        self._event("CANCELLED",stamp,available,key=key,reason="INSUFFICIENT_CAPITAL")
                    else:
                        fee=entry*quantity*fee_rate
                        self.state["cash"]=str(cash-entry*quantity-fee)
                        self.state["total_fees"]=str(_d(self.state["total_fees"])+fee)
                        position={"id":key,"entry_at":stamp.isoformat(),"entry":str(entry),"raw_entry":str(op),
                            "quantity":str(quantity),"stop":str(stop),"target":str(entry+Decimal(2)*(entry-stop)),
                            "entry_fee":str(fee),"entry_spread":str(op*quantity*spread_rate),
                            "entry_slippage":str(op*quantity*slip_rate),"bars":0,"timeout_pending":False}
                        self.state["position"]=position
                        self._event("FILLED",stamp,available,key=key,price=entry,quantity=quantity,fee=str(fee),
                                    spread_bps=str(spread_rate*Decimal(10000)),slippage_bps=str(slip_rate*Decimal(10000)),
                                    price_assumption="H1_OPEN_SCENARIO",partial_fill="UNMODELED")
            self.state["pending"]=None
        position=self.state["position"]
        if position:
            key=position["id"]; qty=_d(position["quantity"]); stop=_d(position["stop"]); target=_d(position["target"])
            reason=None; raw_exit=None
            if position.get("risk_exit_pending",False):
                if op <= stop:
                    self._event("CONFLICTING_EXIT_INTENTS",stamp,available,key=key,
                                reason="EXPOSURE_EXIT_AND_GAP_STOP_BOTH_OPEN_SINGLE_FILL_ASSUMED")
                reason,raw_exit="EXPOSURE_RISK_NEXT_OPEN",op
            elif position["timeout_pending"]:
                if op <= stop:
                    self._event("CONFLICTING_EXIT_INTENTS",stamp,available,key=key,
                                reason="TIMEOUT_AND_GAP_STOP_BOTH_OPEN_SINGLE_FILL_ASSUMED")
                reason,raw_exit="TIMEOUT_NEXT_OPEN",op
            elif op <= stop:
                reason,raw_exit="GAP_THROUGH_STOP",op
            else:
                # Only evaluate extremes when exposure survives the bar open.
                # High-before-low remains a conservative, unobserved path.
                self._latch(_d(self.state["cash"])+qty*high)
                self._latch(_d(self.state["cash"])+qty*low)
                stop_hit, target_hit = low <= stop, high >= target
                # A sell limit cannot fill below its limit. Under the optimistic
                # bound, require enough raw move to leave the limit after the
                # model's adverse price adjustment. This is still not proof of fill.
                target_assumed = self.config["mode"] == "target_touch_assumed" and high*(Decimal(1)-price_rate) >= target
                with localcontext() as reporting:
                    reporting.prec=80
                    reporting.rounding=ROUND_CEILING
                    target_raw_reference=target/(Decimal(1)-price_rate)
                # A rounded reference outside the observed range cannot support
                # even the optimistic touch assumption.
                target_assumed = target_assumed and target_raw_reference <= high
                if stop_hit and target_hit:
                    self._event("AMBIGUOUS",stamp+timedelta(hours=1),available,key=key,reason="STOP_AND_TARGET_SAME_BAR")
                    reason = "AMBIGUOUS_TARGET_BOUND" if target_assumed else "AMBIGUOUS_STOP_BOUND"
                    raw_exit = target_raw_reference if target_assumed else stop
                elif stop_hit:
                    reason,raw_exit="STOP_SCENARIO",stop
                elif target_hit:
                    self._event("TARGET_TOUCH_UNMODELED",stamp+timedelta(hours=1),available,key=key,price=target)
                    if target_assumed:
                        reason,raw_exit="TARGET_TOUCH_ASSUMED",target_raw_reference
            if raw_exit is not None:
                exit_price=target if reason in {"TARGET_TOUCH_ASSUMED","AMBIGUOUS_TARGET_BOUND"} else raw_exit*(Decimal(1)-price_rate)
                fee=exit_price*qty*fee_rate
                cash=_d(self.state["cash"])+exit_price*qty-fee
                pnl=(exit_price-_d(position["entry"]))*qty-_d(position["entry_fee"])-fee
                self.state["cash"]=str(cash);self.state["total_fees"]=str(_d(self.state["total_fees"])+fee)
                exit_at=stamp if reason in {"TIMEOUT_NEXT_OPEN","GAP_THROUGH_STOP","EXPOSURE_RISK_NEXT_OPEN"} else stamp+timedelta(hours=1)
                self._event("EXIT",exit_at,
                            available,key=key,price=exit_price,quantity=qty,reason=reason,fee=str(fee),raw_price=str(raw_exit))
                trade={"id":key,"entry_at":position["entry_at"],"exit_at":exit_at.isoformat(),
                       "exit_time_semantics":"BAR_OPEN" if reason in {"TIMEOUT_NEXT_OPEN","GAP_THROUGH_STOP","EXPOSURE_RISK_NEXT_OPEN"} else "BAR_CLOSE_DETECTION_ACTUAL_UNKNOWN",
                       "entry":position["entry"],
                       "exit":str(exit_price),"quantity":str(qty),"pnl":str(pnl),"exit_reason":reason,
                       "entry_fee":position["entry_fee"],"exit_fee":str(fee),
                       "spread_cost":str(_d(position["entry_spread"])+raw_exit*qty*spread_rate),
                       "slippage_cost":str(_d(position["entry_slippage"])+raw_exit*qty*slip_rate)}
                self.state["trades"].append(trade)
                self._event("SETTLED_SIMULATED",stamp+timedelta(hours=1),available,key=key,price=exit_price,quantity=qty,pnl=str(pnl))
                self.state["position"]=None
                self._latch(cash)
            else:
                position["bars"] += 1
                if position["bars"] >= 8:
                    position["timeout_pending"] = True
        current=self.state["position"]
        nav=_d(self.state["cash"])+(_d(current["quantity"])*close if current else Decimal(0))
        self._latch(nav)
        if current and (nav <= 0 or _d(current["quantity"])*close > nav*Decimal("0.35")):
            current["risk_exit_pending"] = True
            self._event("EXPOSURE_LIMIT_BREACH",stamp+timedelta(hours=1),available,key=current["id"],
                        reason="REDUCE_AT_NEXT_ELIGIBLE_OPEN",mark=str(close))
        # This rounded ratio is reporting-only; it is never fed back into risk.
        with localcontext() as reporting:
            reporting.prec=34
            exposure=(_d(current["quantity"])*close/nav) if current and nav > 0 else Decimal(0)
        self.state["nav_history"].append({"bar_open":stamp.isoformat(),"available_at":available.isoformat(),
            "nav":str(nav),"cash":self.state["cash"],
            "gross_exposure":str(exposure),
            "daily_halt":self.state["daily_halt"],"hard_halt":self.state["hard_halt"]})
        self.state["bars"][b["timestamp"]]=_hash(b)
        self.state["last_bar"]=b
        return True

    def snapshot(self) -> str:
        payload={"config":self.config,"state":self.state}
        return _json({"payload":payload,"sha256":_hash(payload)})

    @classmethod
    def restore(cls, snapshot: str) -> "L1Simulator":
        envelope=json.loads(snapshot); obj=envelope["payload"]
        if envelope.get("sha256") != _hash(obj):
            raise ValueError("SIMULATION_SNAPSHOT_MISMATCH")
        c=obj["config"]
        sim=cls(CostScenario(c["scenario"],_d(c["fees_rt_bps"]),_d(c["spread_rt_bps"]),_d(c["slippage_rt_bps"])),
                cash=_d(c["starting_cash"]),mode=c["mode"],source_id=c["source_id"],source_sha256=c["source_sha256"])
        sim.state=obj["state"]
        return sim
