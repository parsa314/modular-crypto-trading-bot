"""Generic durable OMS seam, currently authorized for offline replay only.

No model or research imports. Reserved IDs are never resubmitted after an
ambiguity. Protective acknowledgements are verified using Fake-only seams.
This is an engineering boundary, not a private trading service.
"""
from copy import deepcopy
import math

from .constitution import RISK_CONSTITUTION_V1 as R
from .intent import FrozenModelManifest, IntentAuthority, digest, utc
from .replay_exchange import EngineeringReplayExchange
from .constitution import assert_risk_hash
from .promotion import PromotionGate
from .protection import ProtectiveStateMachine, entry_record, record_fill
from .production_contracts import ExecutionReceipt


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('NONFINITE_ACCOUNT_OR_ORDER_VALUE')
    return float(value)


def _cvar(returns):
    if len(returns) < 20 or any(isinstance(x, bool) or not math.isfinite(x) for x in returns):
        raise ValueError('RISK_HISTORY_NOT_READY')
    losses = sorted([max(0., -x) for x in returns], reverse=True)
    n = max(1, math.ceil(len(losses)*.05))
    return math.fsum(losses[:n])/n


class OrderManagementSystem:
    def __init__(self, *, gateway, ledger, manifest: FrozenModelManifest, authority: IntentAuthority,
                 account_id, clock, recent_returns, promotion_gate=None, constitution=R):
        # Before calling any gateway method; private adapters cannot enter via a
        # forged mode string or a research PASS. No credential constructor here.
        if type(gateway) is not EngineeringReplayExchange:
            raise ValueError('PRIVATE_EXECUTION_NOT_AUTHORIZED')
        assert_risk_hash(manifest.risk_constitution_hash, constitution)
        promotion = (promotion_gate or PromotionGate()).evaluate(manifest, risk_hash=constitution.sha256)
        if not promotion.approved or promotion.purpose != 'ENGINEERING_REPLAY':
            raise ValueError('SCIENTIFIC_PROMOTION_NOT_AUTHORIZED')
        self.constitution = constitution
        self.gateway, self.ledger, self.manifest, self.authority = gateway, ledger, manifest, authority
        self.account_id, self.clock = account_id, clock
        self.recent_returns = tuple(recent_returns)
        expected = self.identity(account_id, manifest)
        if ledger.identity != expected:
            raise ValueError('OMS_LEDGER_IDENTITY_MISMATCH')
        self.protection = ProtectiveStateMachine(ledger, gateway)

    @staticmethod
    def identity(account_id, manifest):
        return {'account_id': account_id, 'risk_hash': manifest.risk_constitution_hash, 'manifest_hash': manifest.sha256,
                'mode': 'ENGINEERING_REPLAY', 'schema': 'CONVERGENCE_OMS_V1'}

    def _halt(self, reason):
        state = self.ledger.get_state()
        # Existing safety latches cannot be cleared by a later clean cycle.
        state['halt_reason'] = state.get('halt_reason') or reason
        self.ledger.set_state(state)
        return {'status': 'HALTED', 'reason': state['halt_reason'], 'execution_authorized': False}

    def _settle(self, row, receipt):
        plan, identifier = row['intent']['order'], row['client_order_id']
        if (receipt.get('clientOrderId') != identifier or receipt.get('symbol') != plan['symbol']
                or receipt.get('side') != 'buy' or receipt.get('evidence_class') != 'ENGINEERING_REPLAY'
                or receipt.get('status') not in {'OPEN', 'PARTIAL', 'FILLED', 'CANCELLED', 'REJECTED'}):
            raise ValueError('RECEIPT_IDENTITY_OR_STATE_MISMATCH')
        amount, filled, cost, fee = (_number(receipt.get(k)) for k in ('amount', 'filled', 'cost', 'fee_quote'))
        ExecutionReceipt(identifier, receipt.get('id'), plan['symbol'], receipt['side'], filled, cost, fee,
                         receipt['status'], receipt['evidence_class'])
        if (not math.isclose(amount, plan['amount'], rel_tol=1e-10) or filled > amount + 1e-10
                or (receipt['status'] == 'FILLED' and not math.isclose(filled, amount, rel_tol=1e-10))
                or (receipt['status'] in {'OPEN', 'REJECTED'} and filled != 0)
                or (receipt['status'] == 'PARTIAL' and not 0 < filled < amount)
                or (filled == 0 and (cost != 0 or fee != 0))
                or (filled > 0 and (cost <= 0 or cost > filled * plan['price'] * (1+1e-10)))
                or cost+fee > plan['max_order_quote'] * (1+1e-10)):
            raise ValueError('FILL_BOUND_EXCEEDED')
        previous = self.ledger.get_state()
        state = deepcopy(previous)
        applied = state.setdefault('settled', {}).get(identifier, {'filled': 0., 'cost': 0., 'fee_quote': 0.})
        if any(receipt[k] < applied[k] for k in ('filled', 'cost', 'fee_quote')):
            raise ValueError('CUMULATIVE_FILL_DECREASED')
        delta = filled-applied['filled']
        spent = cost+fee-applied['cost']-applied['fee_quote']
        if delta == 0 and spent != 0:
            raise ValueError('FILL_FEE_REVISION_REQUIRES_RECONCILIATION')
        state['cash'] -= spent
        if state['cash'] < -1e-8:
            raise ValueError('SETTLEMENT_CASH_NEGATIVE')
        state['cash'] = max(0., state['cash'])
        if delta:
            state['positions'][plan['symbol']] = state['positions'].get(plan['symbol'], 0.) + delta
            state['halt_reason'] = state.get('halt_reason') or 'POSITION_UNPROTECTED'
        state['settled'][identifier] = {k: receipt[k] for k in ('filled', 'cost', 'fee_quote')}
        record_fill(state, row, receipt, utc(self.clock()))
        self.ledger.update_order(identifier, receipt['status'], receipt,
                                 account_state=state, expected_account_state=previous)
        return receipt['status']

    def _recover(self):
        for row in self.ledger.pending_orders():
            if row['intent'].get('kind') in {'PROTECTIVE_EXIT', 'EMERGENCY_FLATTEN'}:
                continue  # The protective FSM owns sell reconciliation.
            receipt = self.gateway.find_order(row['client_order_id'])
            if receipt is None:
                self.ledger.update_order(row['client_order_id'], 'UNKNOWN', {})
                return self._halt('ORDER_AMBIGUOUS')
            self._settle(row, receipt)
        return None

    def process(self, sealed):
        R = self.constitution
        with self.ledger.session_lock():
            intent = self.authority.verify(sealed)
            if (intent.account_id != self.account_id or intent.manifest_hash != self.manifest.sha256
                    or intent.risk_hash != R.sha256
                    or intent.symbol not in self.manifest.approved_symbols or intent.venue != self.gateway.venue
                    or intent.venue not in self.manifest.approved_venues or intent.timeframe not in self.manifest.approved_timeframes
                    or intent.model_id != self.manifest.model_id):
                raise ValueError('INTENT_SCOPE_MISMATCH')
            try:
                self.protection.reconcile(utc(self.clock()))
                recovery = self._recover()
                if recovery:
                    return recovery
                self.protection.reconcile(utc(self.clock()))
            except Exception:
                return self._halt('ORDER_RECONCILIATION_FAILED')
            state = self.ledger.get_state()
            if state.get('halt_reason'):
                return self._halt(state['halt_reason'])
            if self.ledger.pending_orders():
                return {'status': 'PENDING_RECONCILIATION', 'execution_authorized': False}
            existing = self.ledger.get_order(intent.client_order_id)
            if existing:
                if existing['intent']['intent_hash'] != intent.sha256:
                    return self._halt('EVENT_ID_REUSE_WITH_DIFFERENT_INTENT')
                return {'status': 'ALREADY_RESERVED', 'execution_authorized': False}
            now = utc(self.clock())
            if not intent.valid_from <= now < intent.valid_until:
                return {'status': 'REJECTED', 'reason': 'INTENT_EXPIRED_OR_EARLY', 'execution_authorized': False}
            account = self.gateway.account(now)
            if utc(account['timestamp']) != now:
                return self._halt('ACCOUNT_STALE')
            equity, cash = _number(account['equity']), _number(account['cash'])
            exposures = {s: _number(v) for s, v in account['asset_exposure'].items()}
            positions = {s: _number(v) for s, v in account['positions'].items()}
            if equity <= 0 or any(s not in self.manifest.approved_symbols for s in positions):
                return self._halt('ACCOUNT_SCOPE_UNSUPPORTED')
            if not math.isclose(cash+math.fsum(exposures.values()), equity, rel_tol=1e-10):
                return self._halt('ACCOUNT_EQUITY_MISMATCH')
            if not state:
                if positions:
                    return self._halt('UNTRACKED_OPEN_POSITION')
                state = {'cash': cash, 'positions': {}, 'peak_equity': equity, 'day': now.date().isoformat(),
                         'day_start_equity': equity, 'last_equity': equity, 'halt_reason': None, 'settled': {}}
                self.ledger.set_state(state)
            if (not math.isclose(cash, state['cash'], abs_tol=1e-8)
                    or positions != state['positions']):
                return self._halt('BALANCE_MISMATCH')
            if state['day'] != now.date().isoformat():
                state.update(day=now.date().isoformat(), day_start_equity=state['last_equity'])
            state['peak_equity'] = max(state['peak_equity'], equity)
            state['last_equity'] = equity
            state['drawdown_warning'] = equity <= state['peak_equity']*(1-R.drawdown_warning)
            self.ledger.set_state(state)
            if equity <= state['peak_equity']*(1-R.drawdown_kill):
                return self._halt('DRAWDOWN_KILL')
            if equity <= state['day_start_equity']*(1-R.max_daily_loss):
                return self._halt('DAILY_LOSS_KILL')
            try:
                if _cvar(self.recent_returns) > R.max_cvar95:
                    return self._halt('CVAR95_BREACH')
            except ValueError:
                return self._halt('RISK_HISTORY_NOT_READY')
            quote = self.gateway.quote(intent.symbol)
            bid, ask = _number(quote['bid']), _number(quote['ask'])
            age = (now-utc(quote['timestamp'])).total_seconds()
            if not 0 <= age <= 10 or bid <= 0 or ask < bid:
                return self._halt('DATA_STALE_OR_INVALID')
            spread = (ask-bid)/(ask/2+bid/2)*10_000
            if spread > 25 or spread + 2*self.gateway.fee_bps > intent.round_trip_cost_bps:
                return {'status': 'REJECTED', 'reason': 'EXECUTION_COST_CHANGED', 'execution_authorized': False}
            stop_risk = (intent.limit_price-intent.stop)/intent.limit_price + intent.round_trip_cost_bps/10_000
            # Shared account caps are recalculated now; stale research sizing
            # cannot spend the same cash independently for multiple symbols.
            ceiling = min(intent.approved_notional, equity*R.risk_per_trade/stop_risk,
                equity*R.max_asset_weight-exposures.get(intent.symbol, 0.),
                equity*R.max_gross_exposure-math.fsum(exposures.values()),
                cash-equity*R.min_cash_buffer, equity*R.max_turnover_per_step)
            if ceiling <= 0:
                return {'status': 'REJECTED', 'reason': 'PORTFOLIO_CAPACITY', 'execution_authorized': False}
            if positions.get(intent.symbol, 0.) > 0:
                return {'status': 'REJECTED', 'reason': 'ONE_ACTIVE_PROTECTIVE_GROUP_PER_SYMBOL_PHASE1', 'execution_authorized': False}
            try:
                plan = self.gateway.prepare_order(symbol=intent.symbol,
                    amount=ceiling/(intent.limit_price*(1+self.gateway.fee_bps/10_000)),
                    price=intent.limit_price, max_order_quote=ceiling)
            except ValueError as error:
                if str(error) == 'BELOW_MINIMUM_ORDER':
                    return {'status': 'REJECTED', 'reason': str(error), 'execution_authorized': False}
                return self._halt('INVALID_MARKET_PREPARATION')
            for name in ('amount', 'price', 'max_order_quote'):
                _number(plan[name])
            if (plan['symbol'] != intent.symbol or plan['side'] != 'buy'
                    or plan['price'] > intent.limit_price or plan['price'] <= intent.stop
                    or _number(plan['amount']) <= 0 or plan['amount']*plan['price'] > ceiling
                    or plan['max_order_quote'] > ceiling):
                return self._halt('EXCHANGE_PREPARATION_EXCEEDS_APPROVAL')
            journal_intent = {'intent_hash': intent.sha256, 'contract': intent.payload(),
                              'order': plan, 'risk_hash': R.sha256, 'manifest_hash': self.manifest.sha256}
            if not self.ledger.claim_order(intent.client_order_id, journal_intent):
                return {'status': 'ALREADY_RESERVED', 'execution_authorized': False}
            state = self.ledger.get_state()
            entry_record(state, intent.client_order_id, intent.payload(), now)
            self.ledger.set_state(state)
            try:
                receipt = self.gateway.submit_order(plan, intent.client_order_id)
                row = self.ledger.get_order(intent.client_order_id)
                status = self._settle(row, receipt)
                self.protection.reconcile(now)
                return {'status': status, 'client_order_id': intent.client_order_id,
                        'reason': self.ledger.get_state().get('halt_reason'),
                        'evidence_class': 'ENGINEERING_REPLAY', 'execution_authorized': False}
            except Exception:
                # Never retry submit, even when the caller reports no response.
                row = self.ledger.get_order(intent.client_order_id)
                if row['state'] in {'FILLED', 'CANCELLED', 'REJECTED'}:
                    return {'status': row['state'], 'reason': 'ACK_PERSISTED_RECHECK_REQUIRED',
                            'execution_authorized': False}
                self.ledger.update_order(intent.client_order_id, 'UNKNOWN', {})
                return {'status': 'UNKNOWN', 'reason': 'ORDER_AMBIGUOUS', 'execution_authorized': False}
