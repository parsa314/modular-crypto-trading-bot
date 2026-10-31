"""PostgreSQL admission authority for the existing TESTNET OMS.

No trading I/O. Account trust is injected explicitly and defaults to empty.
Fixture sources prove engineering behavior only; no actual broker anchor is
certified by this module. All account mutations serialize on one account row.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import hashlib
import json
import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from psycopg import Connection
    from .testnet_execution import PostgresTestnetOrderStore, TestnetOrderIntent
from uuid import uuid4

from .financial_decimal import decimal_value as D
from .operational_risk import BrokerRiskContext, OperationalRiskEngine, POLICY_ID
from .order_reconciliation_v52 import OrderLifecycle, ReconciledOrder, TERMINAL_STATES
from .risk import RiskSnapshot

LOG = logging.getLogger(__name__)


class RiskAdmissionError(RuntimeError):
    pass


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RiskAdmissionError("AWARE_UTC_TIME_REQUIRED")
    return value.astimezone(timezone.utc)


def encoded(value: Any) -> str:
    def convert(item: Any) -> str:
        if isinstance(item, Decimal):
            return str(item)
        if isinstance(item, datetime):
            return utc(item).isoformat()
        raise TypeError(type(item).__name__)
    return json.dumps(value, default=convert, sort_keys=True, separators=(",", ":"))


def fingerprint(value: Any) -> str:
    return hashlib.sha256(encoded(value).encode()).hexdigest()


@dataclass(frozen=True)
class AccountObservation:
    account_id: str
    venue_id: str
    snapshot_id: str
    source_id: str
    observed_at: datetime
    equity: Decimal
    cash: Decimal
    gross_notional: Decimal
    asset_notionals: dict[str, Decimal]
    positions: dict[str, Decimal]
    cumulative_net_deposits: Decimal = Decimal("0")
    reconciled: bool = False
    unresolved_orders: bool = True
    cash_flow_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RiskApproval:
    approval_id: str
    account_id: str
    venue_id: str
    symbol: str
    side: str
    client_order_id: str
    approved_quantity: Decimal
    approved_notional: Decimal
    reference_price: Decimal
    stop_price: Decimal | None
    cost_assumptions: Decimal
    risk_profile_id: str
    risk_snapshot_id: str
    risk_snapshot_hash: str
    issued_at: datetime
    expires_at: datetime
    reservation_id: str
    approval_state: str = "ISSUED"

    @property
    def approved(self) -> bool:
        return self.approval_state == "ISSUED"

    @property
    def kill_switch(self) -> bool:
        return not self.approved


class PostgresRiskGovernor:
    """Durable independent risk authority; same database/schema as the OMS.

    accepted_fixture_sources is explicitly test-only. Non-fixture account
    sources remain ANCHOR_UNVERIFIED until a separate broker provenance
    integration is reviewed. Nothing reads DATABASE_URL automatically.
    """

    def __init__(self, database_url: str, *, accepted_fixture_sources: frozenset[str] = frozenset()) -> None:
        if not database_url:
            raise ValueError("explicit database URL required")
        if any(not source.startswith("fixture:") for source in accepted_fixture_sources):
            raise ValueError("only explicit fixture provenance is supported at this engineering gate")
        self.database_url = database_url
        self.accepted_fixture_sources = accepted_fixture_sources

    def _connect(self) -> Connection:
        import psycopg
        return psycopg.connect(self.database_url)

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS risk_account_state (
              account_id TEXT NOT NULL, venue_id TEXT NOT NULL,
              snapshot_id TEXT NOT NULL, snapshot_hash TEXT NOT NULL,
              source_id TEXT NOT NULL, anchor_status TEXT NOT NULL,
              observed_at TIMESTAMPTZ NOT NULL, equity NUMERIC NOT NULL,
              cash NUMERIC NOT NULL, day_equity NUMERIC NOT NULL,
              day_utc DATE NOT NULL, peak_equity NUMERIC NOT NULL,
              net_deposits NUMERIC NOT NULL, gross_notional NUMERIC NOT NULL,
              asset_notionals JSONB NOT NULL, positions JSONB NOT NULL,
              reconciled BOOLEAN NOT NULL, unresolved_orders BOOLEAN NOT NULL,
              state_version BIGINT NOT NULL DEFAULT 0,
              PRIMARY KEY(account_id,venue_id));
            CREATE TABLE IF NOT EXISTS risk_account_observations (
              account_id TEXT NOT NULL, venue_id TEXT NOT NULL, snapshot_id TEXT NOT NULL,
              snapshot_hash TEXT NOT NULL, payload JSONB NOT NULL,
              PRIMARY KEY(account_id,venue_id,snapshot_id));
            CREATE TABLE IF NOT EXISTS risk_halts (
              account_id TEXT NOT NULL, venue_id TEXT NOT NULL, halt_type TEXT NOT NULL,
              state TEXT NOT NULL, policy_version TEXT NOT NULL,
              trigger_equity NUMERIC NOT NULL, trigger_threshold NUMERIC NOT NULL,
              triggered_at TIMESTAMPTZ NOT NULL, source_snapshot_id TEXT NOT NULL,
              state_version BIGINT NOT NULL DEFAULT 0, day_utc DATE NOT NULL,
              release_authorization TEXT, released_at TIMESTAMPTZ,
              PRIMARY KEY(account_id,venue_id,halt_type));
            CREATE TABLE IF NOT EXISTS risk_reservations (
              reservation_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, venue_id TEXT NOT NULL,
              client_order_id TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
              quantity NUMERIC NOT NULL, notional NUMERIC NOT NULL, loss_budget NUMERIC NOT NULL,
              matched_quantity NUMERIC NOT NULL DEFAULT 0, cumulative_fee NUMERIC NOT NULL DEFAULT 0,
              cumulative_quote NUMERIC NOT NULL DEFAULT 0, state TEXT NOT NULL,
              created_at TIMESTAMPTZ NOT NULL, UNIQUE(account_id,venue_id,client_order_id));
            CREATE TABLE IF NOT EXISTS risk_approvals (
              approval_id TEXT PRIMARY KEY, reservation_id TEXT NOT NULL UNIQUE
                REFERENCES risk_reservations(reservation_id),
              payload JSONB NOT NULL, state TEXT NOT NULL, consumed_at TIMESTAMPTZ);
            CREATE TABLE IF NOT EXISTS risk_audit (
              id BIGSERIAL PRIMARY KEY, account_id TEXT NOT NULL, venue_id TEXT NOT NULL,
              event TEXT NOT NULL, observed_at TIMESTAMPTZ NOT NULL, payload JSONB NOT NULL);
            CREATE TABLE IF NOT EXISTS risk_cash_flow_receipts (
              account_id TEXT NOT NULL, venue_id TEXT NOT NULL, receipt_id TEXT NOT NULL,
              snapshot_id TEXT NOT NULL, PRIMARY KEY(account_id,venue_id,receipt_id));
            """)

    @staticmethod
    def _account(conn: Connection, account_id: str, venue_id: str) -> dict[str, Any]:
        from psycopg.rows import dict_row
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM risk_account_state WHERE account_id=%s AND venue_id=%s FOR UPDATE",
                        (account_id, venue_id))
            row = cur.fetchone()
        if row is None:
            raise RiskAdmissionError("ANCHOR_UNVERIFIED")
        return row

    @staticmethod
    def _audit(conn: Connection, account: str, venue: str, event: str, now: datetime, payload: dict[str, Any]) -> None:
        conn.execute("INSERT INTO risk_audit(account_id,venue_id,event,observed_at,payload) VALUES (%s,%s,%s,%s,%s::jsonb)",
                     (account, venue, event, now, encoded(payload)))

    def observe(self, observation: AccountObservation, *, received_at: datetime) -> str:
        """Persist a source observation without accepting caller day/peak anchors."""
        o = observation
        now, at = utc(received_at), utc(o.observed_at)
        if not timedelta(0) <= now-at <= timedelta(seconds=120):
            raise RiskAdmissionError("STALE_OR_FUTURE_ACCOUNT_OBSERVATION")
        values = [D(x) for x in (o.equity, o.cash, o.gross_notional)]
        if values[0] <= 0 or min(values[1:]) < 0 or not all((o.account_id, o.venue_id, o.snapshot_id, o.source_id)):
            raise RiskAdmissionError("INVALID_ACCOUNT_OBSERVATION")
        for mapping in (o.asset_notionals, o.positions):
            if any(D(x) < 0 for x in mapping.values()):
                raise RiskAdmissionError("NEGATIVE_SPOT_ACCOUNT_STATE")
        if sum((D(x) for x in o.asset_notionals.values()), Decimal(0)) != D(o.gross_notional):
            raise RiskAdmissionError("GROSS_ASSET_RECONCILIATION_MISMATCH")
        if D(o.cash) + D(o.gross_notional) != D(o.equity):
            raise RiskAdmissionError("EQUITY_RECONCILIATION_MISMATCH")
        payload = asdict(o)
        digest = fingerprint(payload)
        status = "RECONCILED_TEST_FIXTURE" if o.source_id in self.accepted_fixture_sources else "ANCHOR_UNVERIFIED"
        with localcontext() as ctx, self._connect() as conn:
            ctx.prec = 120
            # Serialize first insertion as well as later updates across processes.
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (encoded([o.account_id,o.venue_id]),))
            existing = conn.execute("SELECT snapshot_hash FROM risk_account_observations WHERE account_id=%s AND venue_id=%s AND snapshot_id=%s",
                                    (o.account_id,o.venue_id,o.snapshot_id)).fetchone()
            if existing:
                if existing[0] != digest:
                    raise RiskAdmissionError("ACCOUNT_SNAPSHOT_HASH_CONFLICT")
                return digest
            try:
                old = self._account(conn,o.account_id,o.venue_id)
            except RiskAdmissionError:
                old = None
            flow = D(o.cumulative_net_deposits) - (old['net_deposits'] if old else D(o.cumulative_net_deposits))
            if old and at <= old['observed_at']:
                raise RiskAdmissionError("STALE_ACCOUNT_VERSION")
            if flow and not o.cash_flow_ids:
                raise RiskAdmissionError("UNEXPLAINED_CASH_FLOW")
            if flow:
                for receipt in o.cash_flow_ids:
                    if not receipt or conn.execute("SELECT 1 FROM risk_cash_flow_receipts WHERE account_id=%s AND venue_id=%s AND receipt_id=%s",(o.account_id,o.venue_id,receipt)).fetchone():
                        raise RiskAdmissionError("REUSED_OR_INVALID_CASH_FLOW_RECEIPT")
                    conn.execute("INSERT INTO risk_cash_flow_receipts VALUES (%s,%s,%s,%s)",(o.account_id,o.venue_id,receipt,o.snapshot_id))
            day = at.date()
            # Day rollover requires an observation within the first two minutes
            # of UTC day; a missed opening cannot be silently manufactured.
            valid_open = (at.hour == 0 and at.minute < 2)
            if old is None:
                day_equity, peak = D(o.equity), D(o.equity)
                if not valid_open:
                    status = "ANCHOR_UNVERIFIED"
            else:
                if old['anchor_status'] != 'RECONCILED_TEST_FIXTURE':
                    status = 'ANCHOR_UNVERIFIED'
                peak = max(D(o.equity), old['peak_equity']+flow)
                day_equity = old['day_equity']+flow
                if old['day_utc'] != day:
                    day_equity = D(o.equity)
                    if not valid_open or not o.reconciled or o.unresolved_orders or o.source_id not in self.accepted_fixture_sources:
                        status = "ANCHOR_UNVERIFIED"
                    else:
                        status = "RECONCILED_TEST_FIXTURE" if o.source_id in self.accepted_fixture_sources else "ANCHOR_UNVERIFIED"
                        conn.execute("UPDATE risk_halts SET state='RELEASED',released_at=%s,release_authorization='UTC_RECONCILED_ROLLOVER',state_version=state_version+1 WHERE account_id=%s AND venue_id=%s AND halt_type='DAILY' AND state='ACTIVE'",
                                     (at,o.account_id,o.venue_id))
            if day_equity <= 0 or peak <= 0:
                status = "ANCHOR_UNVERIFIED"
            unresolved = o.unresolved_orders
            if conn.execute("SELECT to_regclass('testnet_orders')").fetchone()[0] is not None:
                # Broker balances may already include a fill. Apply its delta
                # before replacing the local account projection, or a subsequent
                # restart could count the same fill twice.
                if conn.execute("""SELECT 1 FROM risk_reservations r
                    JOIN testnet_orders t ON t.client_order_id=r.client_order_id AND t.venue=r.venue_id
                    WHERE r.account_id=%s AND r.venue_id=%s
                    AND (t.matched_quantity != r.matched_quantity OR t.fee_reported != r.cumulative_fee) LIMIT 1""",
                    (o.account_id,o.venue_id)).fetchone():
                    raise RiskAdmissionError("PENDING_FILL_ACCOUNTING_REQUIRED")
                unresolved = unresolved or conn.execute("""SELECT 1 FROM risk_reservations r
                    JOIN testnet_orders t ON t.client_order_id=r.client_order_id AND t.venue=r.venue_id
                    WHERE r.account_id=%s AND r.venue_id=%s
                    AND (t.state IN ('CREATED','SUBMITTING','UNKNOWN_PENDING_RECONCILIATION')
                         OR (t.matched_quantity > 0 AND t.fee_currency IS DISTINCT FROM 'USDT')) LIMIT 1""",
                    (o.account_id,o.venue_id)).fetchone() is not None
            conn.execute("INSERT INTO risk_account_observations VALUES (%s,%s,%s,%s,%s::jsonb)",
                         (o.account_id,o.venue_id,o.snapshot_id,digest,encoded(payload)))
            conn.execute("""INSERT INTO risk_account_state(account_id,venue_id,snapshot_id,snapshot_hash,source_id,anchor_status,observed_at,equity,cash,day_equity,day_utc,peak_equity,net_deposits,gross_notional,asset_notionals,positions,reconciled,unresolved_orders)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s)
              ON CONFLICT(account_id,venue_id) DO UPDATE SET snapshot_id=EXCLUDED.snapshot_id,snapshot_hash=EXCLUDED.snapshot_hash,source_id=EXCLUDED.source_id,anchor_status=EXCLUDED.anchor_status,observed_at=EXCLUDED.observed_at,equity=EXCLUDED.equity,cash=EXCLUDED.cash,day_equity=EXCLUDED.day_equity,day_utc=EXCLUDED.day_utc,peak_equity=EXCLUDED.peak_equity,net_deposits=EXCLUDED.net_deposits,gross_notional=EXCLUDED.gross_notional,asset_notionals=EXCLUDED.asset_notionals,positions=EXCLUDED.positions,reconciled=EXCLUDED.reconciled,unresolved_orders=EXCLUDED.unresolved_orders,state_version=risk_account_state.state_version+1""",
              (o.account_id,o.venue_id,o.snapshot_id,digest,o.source_id,status,at,D(o.equity),D(o.cash),day_equity,day,peak,D(o.cumulative_net_deposits),D(o.gross_notional),encoded(o.asset_notionals),encoded(o.positions),o.reconciled,unresolved))
            self._latch(conn,self._account(conn,o.account_id,o.venue_id),at)
            self._audit(conn,o.account_id,o.venue_id,"ACCOUNT_OBSERVED",now,{"snapshot_hash":digest,"anchor_status":status,"cash_flow_ids":o.cash_flow_ids})
        return digest

    @staticmethod
    def _latch(conn: Connection, account: dict[str, Any], now: datetime) -> None:
        for kind, anchor, threshold in [("HARD",account['peak_equity'],D('.05')),("DAILY",account['day_equity'],D('.02'))]:
            if anchor <= 0 or (anchor-account['equity'])/anchor < threshold:
                continue
            conn.execute("""INSERT INTO risk_halts(account_id,venue_id,halt_type,state,policy_version,trigger_equity,trigger_threshold,triggered_at,source_snapshot_id,day_utc)
              VALUES (%s,%s,%s,'ACTIVE',%s,%s,%s,%s,%s,%s)
              ON CONFLICT(account_id,venue_id,halt_type) DO UPDATE SET state='ACTIVE',policy_version=EXCLUDED.policy_version,trigger_equity=EXCLUDED.trigger_equity,trigger_threshold=EXCLUDED.trigger_threshold,triggered_at=EXCLUDED.triggered_at,source_snapshot_id=EXCLUDED.source_snapshot_id,day_utc=EXCLUDED.day_utc,release_authorization=NULL,released_at=NULL,state_version=risk_halts.state_version+1
              WHERE risk_halts.state != 'ACTIVE'""",
              (account['account_id'],account['venue_id'],kind,POLICY_ID,account['equity'],threshold,now,account['snapshot_id'],account['day_utc']))

    @staticmethod
    def _fresh(account: dict[str, Any], now: datetime) -> None:
        if account['anchor_status'] != 'RECONCILED_TEST_FIXTURE':
            raise RiskAdmissionError("ANCHOR_UNVERIFIED")
        if not account['reconciled'] or account['unresolved_orders']:
            raise RiskAdmissionError("ACCOUNT_NOT_RECONCILED")
        if not timedelta(0) <= now-account['observed_at'] <= timedelta(seconds=120):
            raise RiskAdmissionError("STALE_ACCOUNT_ANCHOR")
        if account['day_utc'] != now.date():
            raise RiskAdmissionError("MISSING_UTC_DAY_ANCHOR")

    def approve(self, *, account_id: str, venue_id: str, client_order_id: str,
                symbol: str, side: str, quantity: Decimal, reference_price: Decimal,
                stop_price: Decimal | None, cost_bps: Decimal, now: datetime,
                quote_at: datetime, ttl_seconds: int = 30) -> RiskApproval:
        from psycopg import errors
        try:
            return self._approve(account_id=account_id,venue_id=venue_id,client_order_id=client_order_id,
                symbol=symbol,side=side,quantity=quantity,reference_price=reference_price,
                stop_price=stop_price,cost_bps=cost_bps,now=now,quote_at=quote_at,ttl_seconds=ttl_seconds)
        except (errors.DeadlockDetected,errors.SerializationFailure,errors.LockNotAvailable) as exc:
            LOG.error("RISK_TRANSACTION_CONFLICT_RETRY_REEVALUATION_REQUIRED", exc_info=True)
            raise RiskAdmissionError("RISK_TRANSACTION_CONFLICT_REEVALUATE") from exc
        except RiskAdmissionError:
            LOG.warning("RISK_ADMISSION_REJECTED", exc_info=True)
            raise

    def _approve(self, *, account_id: str, venue_id: str, client_order_id: str,
                 symbol: str, side: str, quantity: Decimal, reference_price: Decimal,
                 stop_price: Decimal | None, cost_bps: Decimal, now: datetime,
                 quote_at: datetime, ttl_seconds: int) -> RiskApproval:
        now, quote_at = utc(now), utc(quote_at)
        qty, ref, cost = D(quantity), D(reference_price), D(cost_bps)
        if not client_order_id or not 0 < ttl_seconds <= 30 or not timedelta(0) <= now-quote_at <= timedelta(seconds=30):
            raise RiskAdmissionError("INVALID_INTENT_OR_QUOTE_OR_TTL")
        with localcontext() as ctx, self._connect() as conn:
            ctx.prec = 120
            conn.execute("SET LOCAL lock_timeout='5s'")
            a = self._account(conn,account_id,venue_id)
            self._fresh(a,now)
            active = conn.execute("SELECT halt_type FROM risk_halts WHERE account_id=%s AND venue_id=%s AND state='ACTIVE'",(account_id,venue_id)).fetchall()
            if side == 'BUY' and active:
                raise RiskAdmissionError("DURABLE_NEW_ENTRY_HALT")
            reservations = conn.execute("SELECT symbol,side,quantity,notional,loss_budget,matched_quantity FROM risk_reservations WHERE account_id=%s AND venue_id=%s AND state != 'CLOSED'",(account_id,venue_id)).fetchall()
            # Conservatively keep even filled notional until terminal reconciliation.
            pending_gross = sum((r[3] for r in reservations if r[1]=='BUY'),D(0))
            pending_asset = sum((r[3] for r in reservations if r[1]=='BUY' and r[0]==symbol),D(0))
            pending_loss = sum((r[4] for r in reservations if r[1]=='BUY'),D(0))
            reserved_sell = sum((r[2]-r[5] for r in reservations if r[1]=='SELL' and r[0]==symbol),D(0))
            held = D(a['positions'].get(symbol,'0'))
            last_entry = conn.execute("""SELECT max(p.consumed_at) FROM risk_approvals p
                JOIN risk_reservations r ON r.reservation_id=p.reservation_id
                WHERE r.account_id=%s AND r.venue_id=%s AND r.side='BUY'""",(account_id,venue_id)).fetchone()[0]
            snapshot = RiskSnapshot(a['equity'],a['peak_equity'],(a['gross_notional']+pending_gross)/a['equity'],
                (D(a['asset_notionals'].get(symbol,'0'))+pending_asset)/a['equity'],0,0,0)
            context = BrokerRiskContext(account_id,symbol, a['observed_at'],quote_at,held,
                reserved_sell_quantity=reserved_sell,reconciled=True,unresolved_orders=False,
                day_start_equity=a['day_equity'],day_start_date_utc=a['day_utc'].isoformat(),
                open_positions=len([v for v in a['positions'].values() if D(v)>0])+len(reservations),
                same_direction_positions=len([v for v in a['positions'].values() if D(v)>0])+len(reservations),last_entry_at=last_entry)
            decision = OperationalRiskEngine().evaluate_order(snapshot,symbol=symbol,side=side,quantity=qty,price=ref,
                now=now,context=context,stop_price=stop_price,roundtrip_cost_bps=cost)
            if not decision.approved:
                raise RiskAdmissionError('|'.join(decision.reasons))
            notional = qty*ref
            loss = qty*(ref-D(stop_price))+notional*cost/D(10000) if side=='BUY' else D(0)
            if side=='BUY' and (pending_loss+loss > a['equity']*D('.0025') or pending_gross+notional > a['cash']):
                raise RiskAdmissionError("SHARED_RISK_OR_CASH_BUDGET")
            approval = RiskApproval(str(uuid4()),account_id,venue_id,symbol,side,client_order_id,qty,notional,ref,
                None if stop_price is None else D(stop_price),cost,POLICY_ID,a['snapshot_id'],a['snapshot_hash'],now,
                now+timedelta(seconds=ttl_seconds),str(uuid4()))
            conn.execute("INSERT INTO risk_reservations(reservation_id,account_id,venue_id,client_order_id,symbol,side,quantity,notional,loss_budget,state,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'RESERVED',%s)",
                (approval.reservation_id,account_id,venue_id,client_order_id,symbol,side,qty,notional,loss,now))
            conn.execute("INSERT INTO risk_approvals(approval_id,reservation_id,payload,state) VALUES (%s,%s,%s::jsonb,'ISSUED')",
                (approval.approval_id,approval.reservation_id,encoded(asdict(approval))))
            self._audit(conn,account_id,venue_id,"APPROVAL_RESERVED",now,asdict(approval))
            return approval

    def consume_with_intent(self, approval: RiskApproval, *, store: PostgresTestnetOrderStore,
                            intent: TestnetOrderIntent, order: ReconciledOrder,
                            venue_id: str, account_id: str, now: datetime) -> None:
        """Approval state and durable intent commit together before send."""
        from .testnet_execution import PostgresTestnetOrderStore
        if not isinstance(approval,RiskApproval) or not isinstance(store,PostgresTestnetOrderStore) or store.database_url != self.database_url:
            raise RiskAdmissionError("DURABLE_BOUND_APPROVAL_REQUIRED")
        if (approval.account_id,approval.venue_id,approval.client_order_id,approval.symbol,approval.side,
            approval.approved_quantity,approval.reference_price,approval.risk_profile_id) != (
            account_id,venue_id,intent.client_order_id,intent.symbol,intent.side.value,intent.quantity,intent.reference_price,POLICY_ID):
            raise RiskAdmissionError("APPROVAL_INTENT_BINDING_MISMATCH")
        now = utc(now)
        if not approval.issued_at <= now <= approval.expires_at:
            raise RiskAdmissionError("APPROVAL_EXPIRED_OR_FUTURE")
        with self._connect() as conn:
            a = self._account(conn,account_id,venue_id)
            self._fresh(a,now)
            if a['snapshot_hash'] != approval.risk_snapshot_hash:
                raise RiskAdmissionError("ACCOUNT_CHANGED_REAPPROVAL_REQUIRED")
            if approval.side == 'BUY' and conn.execute("SELECT 1 FROM risk_halts WHERE account_id=%s AND venue_id=%s AND state='ACTIVE'",(account_id,venue_id)).fetchone():
                raise RiskAdmissionError("DURABLE_NEW_ENTRY_HALT")
            row = conn.execute("SELECT payload,state FROM risk_approvals WHERE approval_id=%s FOR UPDATE",(approval.approval_id,)).fetchone()
            if row is None or row[0] != json.loads(encoded(asdict(approval))) or row[1] != 'ISSUED':
                raise RiskAdmissionError("INVALID_OR_CONSUMED_APPROVAL")
            store.create_in_transaction(conn,venue=venue_id,intent=intent,order=order)
            conn.execute("UPDATE risk_approvals SET state='CONSUMED',consumed_at=%s WHERE approval_id=%s",(now,approval.approval_id))
            conn.execute("UPDATE risk_reservations SET state='SUBMITTED' WHERE reservation_id=%s",(approval.reservation_id,))
            # Crash after the network send must never leave admission open.
            # Only a fresh account/order reconciliation can clear this state.
            conn.execute("UPDATE risk_account_state SET reconciled=FALSE,unresolved_orders=TRUE,state_version=state_version+1 WHERE account_id=%s AND venue_id=%s",(account_id,venue_id))
            self._audit(conn,account_id,venue_id,"INTENT_APPROVAL_CONSUMED",now,{"client_order_id":intent.client_order_id})

    def release_hard_halt(self, *, account_id: str, venue_id: str, authorization: str,
                          expected_version: int, now: datetime) -> None:
        """Explicit audit input; callers must authenticate the operator externally."""
        if not authorization.strip():
            raise RiskAdmissionError("RELEASE_AUTHORIZATION_REQUIRED")
        now = utc(now)
        with self._connect() as conn:
            a = self._account(conn,account_id,venue_id)
            self._fresh(a,now)
            if (a['peak_equity']-a['equity'])/a['peak_equity'] >= D('.05'):
                raise RiskAdmissionError("DRAWDOWN_STILL_BREACHED")
            count = conn.execute("UPDATE risk_halts SET state='RELEASED',released_at=%s,release_authorization=%s,state_version=state_version+1 WHERE account_id=%s AND venue_id=%s AND halt_type='HARD' AND state='ACTIVE' AND state_version=%s",
                (now,authorization,account_id,venue_id,expected_version)).rowcount
            if count != 1:
                raise RiskAdmissionError("STALE_HALT_VERSION")
            self._audit(conn,account_id,venue_id,"HARD_HALT_RELEASED",now,{"authorization":authorization})

    def reconcile_accounting(self, *, account_id: str, venue_id: str,
                             client_order_id: str, now: datetime) -> None:
        """Apply persisted cumulative fills once; unknown economics retain risk.

        This runs even for an already terminal OMS row after restart. A crash
        between OMS persistence and this transaction is recovered by invoking
        reconcile again. Entries remain closed until account reconciliation.
        """
        with localcontext() as ctx, self._connect() as conn:
            ctx.prec = 120
            a = self._account(conn,account_id,venue_id)
            row = conn.execute("SELECT reservation_id,symbol,side,quantity,matched_quantity,cumulative_fee,cumulative_quote,state FROM risk_reservations WHERE account_id=%s AND venue_id=%s AND client_order_id=%s FOR UPDATE",
                (account_id,venue_id,client_order_id)).fetchone()
            if row is None:
                raise RiskAdmissionError("RESERVATION_NOT_FOUND")
            order = conn.execute("SELECT state,matched_quantity,average_price,fee_reported,fee_currency FROM testnet_orders WHERE client_order_id=%s AND venue=%s FOR UPDATE",
                (client_order_id,venue_id)).fetchone()
            if order is None:
                raise RiskAdmissionError("PERSISTED_ORDER_NOT_FOUND")
            state, matched, avg, fee, currency = order
            rid,symbol,side,requested,previous,previous_fee,previous_quote,reservation_state = row
            if matched < previous or matched > requested:
                raise RiskAdmissionError("CUMULATIVE_ACCOUNTING_REGRESSION")
            if matched and avg is None:
                conn.execute("UPDATE risk_account_state SET reconciled=FALSE WHERE account_id=%s AND venue_id=%s",(account_id,venue_id))
                self._audit(conn,account_id,venue_id,"FILL_ECONOMICS_UNRESOLVED",utc(now),{"client_order_id":client_order_id})
                return
            # A fill with an unknown or non-quote fee still changes the actual
            # position and gross cash. Apply those effects provisionally once;
            # do not invent a fee or release the reservation.
            fee_unresolved = bool(matched and currency != 'USDT')
            if fee_unresolved:
                fee = previous_fee
            elif fee < previous_fee:
                raise RiskAdmissionError("CUMULATIVE_ACCOUNTING_REGRESSION")
            quote = matched*avg if matched else D(0)
            delta, delta_fee, delta_quote = matched-previous, fee-previous_fee, quote-previous_quote
            if delta > 0 and delta_quote <= 0:
                raise RiskAdmissionError("INVALID_INCREMENTAL_FILL_NOTIONAL")
            if delta == 0 and delta_quote != 0:
                raise RiskAdmissionError("UNCHANGED_FILL_NOTIONAL_CONFLICT")
            positions = {k:D(v) for k,v in a['positions'].items()}
            held = positions.get(symbol,D(0))
            cash = a['cash']
            if side == 'BUY':
                cash -= delta_quote+delta_fee
                positions[symbol] = held+delta
            else:
                if delta > held:
                    raise RiskAdmissionError("EXIT_WOULD_REVERSE_POSITION")
                positions[symbol] = held-delta
                cash += delta_quote-delta_fee
            if cash < 0:
                raise RiskAdmissionError("NEGATIVE_CASH_AFTER_SETTLEMENT")
            terminal = OrderLifecycle(state) in TERMINAL_STATES
            if delta or delta_fee or fee_unresolved:
                conn.execute("UPDATE risk_account_state SET cash=%s,positions=%s::jsonb,reconciled=FALSE,unresolved_orders=unresolved_orders OR %s,state_version=state_version+1 WHERE account_id=%s AND venue_id=%s",
                    (cash,encoded(positions),fee_unresolved,account_id,venue_id))
            conn.execute("UPDATE risk_reservations SET matched_quantity=%s,cumulative_fee=%s,cumulative_quote=%s,state=%s WHERE reservation_id=%s",
                (matched,fee,quote,'CLOSED' if terminal and not fee_unresolved else 'PARTIAL' if matched else reservation_state,rid))
            if delta or delta_fee or (terminal and reservation_state != 'CLOSED'):
                self._audit(conn,account_id,venue_id,"CUMULATIVE_FILL_APPLIED",utc(now),
                    {"client_order_id":client_order_id,"quantity_delta":delta,"fee_delta":delta_fee,"quote_delta":delta_quote,"terminal":terminal,"fee_unresolved":fee_unresolved})
