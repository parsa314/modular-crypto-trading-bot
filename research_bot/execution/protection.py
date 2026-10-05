"""Durable protective lifecycle for Phase-1 Fake transports only.

Freeze: never repeat an ambiguous submit. After the fixed deadline, confirmed
cancellation/absence permits one durable emergency flatten. Unknown cancellation
forbids replacement.
Deadline starts at entry intent creation (conservative Fake-time bound), not at
restart or registration. Emergency paths latch manual-review halt after closure.
"""
from copy import deepcopy
from datetime import datetime, timedelta
import math

from .intent import digest, utc
from .production_contracts import ProtectiveOrderIntent, ExecutionReceipt
from .protective_adapters import protective_adapter


STATES = frozenset({'ENTRY_INTENT_CREATED', 'ENTRY_SUBMITTING', 'ENTRY_OPEN', 'ENTRY_PARTIAL',
    'ENTRY_FILLED', 'PROTECTION_REQUIRED', 'PROTECTION_SUBMITTING', 'PROTECTION_CONFIRMED',
    'POSITION_PROTECTED', 'EXIT_PARTIAL', 'POSITION_CLOSED', 'PROTECTION_CANCELLED',
    'RECONCILIATION_REQUIRED', 'EMERGENCY_HALT'})
PROTECTION_DEADLINE_SECONDS = 5
TRANSITIONS = {
    'ENTRY_INTENT_CREATED': {'ENTRY_SUBMITTING'},
    'ENTRY_SUBMITTING': {'ENTRY_OPEN', 'ENTRY_PARTIAL', 'ENTRY_FILLED', 'POSITION_CLOSED'},
    'ENTRY_OPEN': {'ENTRY_PARTIAL', 'ENTRY_FILLED', 'POSITION_CLOSED'},
    'ENTRY_PARTIAL': {'PROTECTION_REQUIRED'}, 'ENTRY_FILLED': {'PROTECTION_REQUIRED'},
    'PROTECTION_REQUIRED': {'PROTECTION_SUBMITTING'},
    'PROTECTION_SUBMITTING': {'PROTECTION_CONFIRMED', 'RECONCILIATION_REQUIRED'},
    'PROTECTION_CONFIRMED': {'POSITION_PROTECTED'},
    'POSITION_PROTECTED': {'EXIT_PARTIAL', 'PROTECTION_CANCELLED', 'POSITION_CLOSED', 'RECONCILIATION_REQUIRED'},
    'EXIT_PARTIAL': {'PROTECTION_CANCELLED', 'RECONCILIATION_REQUIRED'},
    'RECONCILIATION_REQUIRED': {'PROTECTION_CONFIRMED', 'PROTECTION_CANCELLED', 'EMERGENCY_HALT'},
    'PROTECTION_CANCELLED': {'POSITION_CLOSED', 'EMERGENCY_HALT'},
    'EMERGENCY_HALT': {'POSITION_CLOSED'}, 'POSITION_CLOSED': set(),
}


def transition(record, target, now):
    previous = record['state']
    if target == previous:
        return
    if target not in TRANSITIONS[previous]:
        raise ValueError('INVALID_PROTECTIVE_TRANSITION:'+previous+'->'+target)
    history = record.setdefault('history', [])
    entry = {'from': previous, 'to': target, 'at': utc(now).isoformat(),
             'previous_hash': history[-1]['hash'] if history else digest({'entry': record['entry_id']})}
    entry['hash'] = digest(entry)
    history.append(entry)
    record['state'] = target


def entry_record(state, identifier, contract, now):
    records = state.setdefault('protection', {})
    if identifier not in records:
        record = {'entry_id': identifier, 'contract': contract, 'state': 'ENTRY_INTENT_CREATED',
                  'quantity': 0., 'history': [], 'exit_ids': []}
        transition(record, 'ENTRY_SUBMITTING', now)
        records[identifier] = record
    return records[identifier]


def record_fill(state, row, receipt, now):
    record = entry_record(state, row['client_order_id'], row['intent']['contract'], now)
    if not receipt['filled']:
        if receipt['status'] in {'CANCELLED', 'REJECTED'}:
            transition(record, 'POSITION_CLOSED', now)
        elif record['state'] == 'ENTRY_SUBMITTING':
            transition(record, 'ENTRY_OPEN', now)
        return
    if record['quantity'] not in (0., receipt['filled']):
        raise ValueError('ENTRY_FILL_CHANGED_REQUIRES_PROTECTIVE_RECONCILIATION')
    record['quantity'] = receipt['filled']
    record.setdefault('deadline', (utc(datetime.fromisoformat(record['contract']['created_at']))
                                   +timedelta(seconds=PROTECTION_DEADLINE_SECONDS)).isoformat())
    if record['state'] in {'ENTRY_SUBMITTING', 'ENTRY_OPEN'}:
        transition(record, 'ENTRY_FILLED' if receipt['status'] == 'FILLED' else 'ENTRY_PARTIAL', now)
        transition(record, 'PROTECTION_REQUIRED', now)
    state['halt_reason'] = state.get('halt_reason') or 'POSITION_UNPROTECTED'


class ProtectiveStateMachine:
    deadline_seconds = PROTECTION_DEADLINE_SECONDS

    def __init__(self, ledger, gateway):
        self.ledger, self.gateway = ledger, gateway
        self.adapter = protective_adapter(gateway)

    def _save(self, state, record, target, now):
        transition(record, target, now)
        self.ledger.set_state(state)

    def _critical(self, state, record, now):
        if record['state'] not in {'RECONCILIATION_REQUIRED', 'EMERGENCY_HALT'}:
            self._save(state, record, 'RECONCILIATION_REQUIRED', now)
        state['halt_reason'] = state.get('halt_reason') or 'POSITION_UNPROTECTED'
        self.ledger.set_state(state)

    def _intent(self, record):
        c = record['contract']
        return ProtectiveOrderIntent(record['entry_id'], c['symbol'], c['venue'], record['quantity'],
                                     c['stop'], c['target'], c['risk_hash'])

    def _settle_exit(self, identifier, receipt, symbol, maximum):
        if (receipt.get('clientOrderId') != identifier or receipt.get('symbol') != symbol
                or receipt.get('side') != 'sell' or receipt.get('status') != 'FILLED'
                or receipt.get('evidence_class') != 'ENGINEERING_REPLAY'):
            raise ValueError('EXIT_RECEIPT_IDENTITY_MISMATCH')
        values = [receipt.get(k) for k in ('amount', 'filled', 'cost', 'fee_quote')]
        if any(isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) or x < 0 for x in values):
            raise ValueError('EXIT_RECEIPT_NONFINITE')
        amount, filled, cost, fee = values
        ExecutionReceipt(identifier, receipt.get('id'), symbol, 'sell', filled, cost, fee,
                         receipt['status'], receipt['evidence_class'])
        if not 0 < filled <= maximum+1e-8 or amount != filled or cost <= 0 or fee > cost:
            raise ValueError('EXIT_RECEIPT_QUANTITY_MISMATCH')
        existing = self.ledger.get_order(identifier)
        if existing and existing['state'] == 'FILLED':
            if existing['result'] != receipt:
                raise ValueError('EXIT_RECEIPT_MUTATION')
            return
        previous = self.ledger.get_state()
        state = deepcopy(previous)
        if filled > state['positions'].get(symbol, 0.)+1e-8:
            raise ValueError('EXIT_EXCEEDS_TRACKED_POSITION')
        remaining = state['positions'][symbol]-filled
        if remaining > 1e-8:
            state['positions'][symbol] = remaining
        else:
            state['positions'].pop(symbol, None)
        state['cash'] += cost-fee
        if existing is None:
            self.ledger.claim_order(identifier, {'kind': 'PROTECTIVE_EXIT', 'symbol': symbol, 'maximum': maximum})
        self.ledger.update_order(identifier, 'FILLED', receipt, account_state=state, expected_account_state=previous)

    def _cancel_confirmed(self, record, now):
        ack = self.adapter.cancel(self._intent(record).protection_id, now)
        if ack.get('evidence_class') != 'ENGINEERING_REPLAY':
            raise ValueError('CANCELLATION_EVIDENCE_INVALID')
        if ack.get('state') == 'ABSENT_CONFIRMED':
            return
        if (ack.get('observed_at') != now or ack.get('protection_id') != self._intent(record).protection_id
                or any(ack.get(k) not in {'FILLED', 'CANCELLED'} for k in ('stop_status', 'tp_status'))):
            raise ValueError('CANCELLATION_UNKNOWN')

    def _flatten(self, entry_id, now):
        state = self.ledger.get_state(); record = state['protection'][entry_id]
        self._cancel_confirmed(record, now)  # Never sell while a sibling might still execute.
        if record['state'] != 'EMERGENCY_HALT':
            self._save(state, record, 'PROTECTION_CANCELLED', now)
            self._save(state, record, 'EMERGENCY_HALT', now)
        state['halt_reason'] = 'PROTECTION_EMERGENCY_HALT'
        self.ledger.set_state(state)
        symbol = record['contract']['symbol']
        quantity = state['positions'].get(symbol, 0.)
        identifier = 'F'+digest({'entry': entry_id, 'role': 'EMERGENCY_FLATTEN_V1'})[:30]
        if quantity:
            existing = self.ledger.get_order(identifier)
            if existing:
                receipt = self.gateway.find_order(identifier)
                if receipt is None:
                    raise ValueError('EMERGENCY_FLATTEN_AMBIGUOUS_NO_RETRY')
                quantity = existing['intent']['maximum']
            else:
                self.ledger.claim_order(identifier, {'kind': 'EMERGENCY_FLATTEN', 'symbol': symbol, 'maximum': quantity})
                receipt = self.gateway.emergency_flatten(symbol, quantity, identifier)
            self._settle_exit(identifier, receipt, symbol, quantity)
        state = self.ledger.get_state(); record = state['protection'][entry_id]
        if state['positions'].get(symbol, 0.) > 1e-8:
            raise ValueError('EMERGENCY_FLATTEN_NOT_CONFIRMED')
        self._save(state, record, 'POSITION_CLOSED', now)

    def reconcile(self, now):
        now = utc(now)
        for entry_id in tuple(self.ledger.get_state().get('protection', {})):
            state = self.ledger.get_state(); record = state['protection'][entry_id]
            if record['state'] in {'ENTRY_SUBMITTING', 'ENTRY_OPEN', 'POSITION_CLOSED'}:
                continue
            try:
                if record['state'] == 'EMERGENCY_HALT':
                    self._flatten(entry_id, now)
                    continue
                intent = self._intent(record)
                if record['state'] == 'PROTECTION_REQUIRED':
                    self._save(state, record, 'PROTECTION_SUBMITTING', now)
                    if now < utc(datetime.fromisoformat(record['deadline'])):
                        ack = self.adapter.submit(intent, now)
                    else:
                        ack = self.adapter.lookup(intent.protection_id, now)
                else:
                    ack = self.adapter.lookup(intent.protection_id, now)
                if ack.get('state') == 'UNKNOWN' or (ack.get('observed_at', now) != now):
                    raise ValueError('PROTECTIVE_STATE_UNKNOWN_OR_STALE')
                if ack.get('exit_receipts'):
                    if (ack.get('protection_id') != intent.protection_id or ack.get('symbol') != intent.symbol
                            or ack.get('evidence_class') != 'ENGINEERING_REPLAY'):
                        raise ValueError('PROTECTIVE_EXIT_EVIDENCE_MISMATCH')
                    for receipt in ack['exit_receipts']:
                        self._settle_exit(receipt['clientOrderId'], receipt, intent.symbol, intent.quantity)
                    state = self.ledger.get_state(); record = state['protection'][entry_id]
                    if state['positions'].get(intent.symbol, 0.) > 1e-8:
                        self._save(state, record, 'EXIT_PARTIAL', now)
                        self._flatten(entry_id, now)
                    else:
                        self._cancel_confirmed(record, now)
                        self._save(state, record, 'POSITION_CLOSED', now)
                    continue
                if ack.get('state') == 'ABSENT_CONFIRMED':
                    self._critical(state, record, now)
                    if now >= utc(datetime.fromisoformat(record['deadline'])):
                        self._flatten(entry_id, now)
                    continue
                try:
                    self.adapter.validate_ack(intent, ack, now)
                except ValueError:
                    self._critical(state, record, now)
                    if now >= utc(datetime.fromisoformat(record['deadline'])):
                        self._flatten(entry_id, now)
                    continue
                if record['state'] != 'POSITION_PROTECTED':
                    record['ack_hash'] = digest(ack)
                    self._save(state, record, 'PROTECTION_CONFIRMED', now)
                    self._save(state, record, 'POSITION_PROTECTED', now)
            except Exception as error:
                state = self.ledger.get_state(); record = state['protection'][entry_id]
                record['last_error'] = type(error).__name__+':'+str(error)
                self._critical(state, record, now)
        state = self.ledger.get_state()
        records = state.get('protection', {}).values()
        if (state.get('halt_reason') == 'POSITION_UNPROTECTED'
                and all(r['state'] in {'POSITION_PROTECTED', 'POSITION_CLOSED'} for r in records)):
            state['halt_reason'] = None
            self.ledger.set_state(state)
