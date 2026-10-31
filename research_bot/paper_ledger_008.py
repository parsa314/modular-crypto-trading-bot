"""Single-process-independent SQLite paper ledger; never contacts a venue.

Engineering prototype. All accounting is Decimal, with idempotent order IDs.
No result from this module establishes broker, live or prospective execution.
"""
from __future__ import annotations
from datetime import datetime
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import sqlite3

from .risk import RiskEngine, RiskLimits, RiskSnapshot


class PaperLedger:
    def __init__(self, path: Path, initial_cash: Decimal = Decimal("10000")):
        if not initial_cash.is_finite() or initial_cash <= 0:
            raise ValueError("INVALID_INITIAL_CASH")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS paper_account (id INTEGER PRIMARY KEY CHECK(id=1), cash TEXT NOT NULL, quantity TEXT NOT NULL, basis TEXT NOT NULL, realized TEXT NOT NULL, fees TEXT NOT NULL, peak TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS paper_fills (order_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL)")
            db.execute("INSERT OR IGNORE INTO paper_account VALUES (1,?,'0','0','0','0',?)", (str(initial_cash),str(initial_cash)))

    def snapshot(self) -> dict[str, str]:
        with sqlite3.connect(self.path) as db:
            r = db.execute("SELECT cash,quantity,basis,realized,fees,peak FROM paper_account WHERE id=1").fetchone()
        return dict(zip(("cash","quantity","basis","realized","fees","peak"),r))

    def fill(self, *, order_id: str, side: str, quantity: Decimal, reference_price: Decimal,
             decision_at: datetime, fill_at: datetime, fee_bps: Decimal, spread_bps: Decimal,
             slippage_bps: Decimal) -> dict:
        if not order_id or side not in {"BUY","SELL"}:
            raise ValueError("INVALID_PAPER_ORDER")
        if decision_at.tzinfo is None or fill_at.tzinfo is None or fill_at <= decision_at:
            raise ValueError("FILL_MUST_FOLLOW_DECISION")
        if not all(x.is_finite() for x in (quantity, reference_price, fee_bps, spread_bps, slippage_bps)):
            raise ValueError("NONFINITE_ORDER")
        if quantity <= 0 or reference_price <= 0 or min(fee_bps, spread_bps, slippage_bps) < 0:
            raise ValueError("INVALID_ORDER_AMOUNT_OR_COST")
        request = {"id":order_id,"side":side,"quantity":str(quantity),"reference_price":str(reference_price),
                   "decision_at":decision_at.isoformat(),"fill_at":fill_at.isoformat(),
                   "fee_bps":str(fee_bps),"spread_bps":str(spread_bps),"slippage_bps":str(slippage_bps)}
        fingerprint = hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()
        with localcontext() as ctx, sqlite3.connect(self.path) as db:
            ctx.prec = 80
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT fingerprint,result FROM paper_fills WHERE order_id=?",(order_id,)).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise ValueError("ORDER_ID_CONFLICT")
                return {**json.loads(prior[1]), "duplicate": True}
            row = db.execute("SELECT cash,quantity,basis,realized,fees,peak FROM paper_account WHERE id=1").fetchone()
            cash, held, basis, realized, fees, peak = map(Decimal,row)
            adjustment = (spread_bps / 2 + slippage_bps) / Decimal(10000)
            if adjustment >= 1:
                raise ValueError("INVALID_EXECUTION_COST")
            price = reference_price * (1 + adjustment if side == "BUY" else 1 - adjustment)
            notional = price * quantity
            fee = notional * fee_bps / Decimal(10000)
            if side == "BUY":
                if cash < notional + fee:
                    raise ValueError("INSUFFICIENT_CASH")
                cash -= notional + fee
                basis += notional + fee
                held += quantity
            else:
                if quantity > held:
                    raise ValueError("SELL_EXCEEDS_POSITION")
                released = basis * quantity / held
                basis -= released
                held -= quantity
                cash += notional - fee
                realized += notional - fee - released
            fees += fee
            equity = cash + held * reference_price
            peak = max(peak,equity)
            if equity <= 0:
                raise ValueError("INVALID_EQUITY")
            position_value = held * reference_price
            risk = RiskEngine(RiskLimits(max_drawdown=0.05,max_gross_exposure=0.70,max_asset_weight=0.35)).evaluate(
                RiskSnapshot(equity=float(equity),peak_equity=float(peak),gross_exposure=float(position_value/equity),
                             asset_weight=float(position_value/equity),turnover=float(notional/equity),
                             spread_bps=float(spread_bps),slippage_bps=float(slippage_bps)))
            if not risk.approved:
                raise ValueError("RISK_REJECTED:"+"|".join(risk.reasons))
            result = {"order_id":order_id,"side":side,"quantity":str(quantity),"fill_price":str(price),
                      "fee":str(fee),"cash":str(cash),"position":str(held),"equity":str(equity),
                      "realized":str(realized),"duplicate":False}
            db.execute("UPDATE paper_account SET cash=?,quantity=?,basis=?,realized=?,fees=?,peak=? WHERE id=1",
                       tuple(map(str,(cash,held,basis,realized,fees,peak))))
            db.execute("INSERT INTO paper_fills VALUES (?,?,?)",(order_id,fingerprint,json.dumps(result,sort_keys=True)))
        return result
