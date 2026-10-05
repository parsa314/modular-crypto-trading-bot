"""Local deterministic exchange boundary for convergence failure drills.

This has no network or credentials. Its fills and account snapshots are
engineering fixtures and never count as paper/testnet/live evidence.
"""
from copy import deepcopy
import math
from decimal import Decimal, ROUND_FLOOR


class EngineeringReplayExchange:
    mode = 'ENGINEERING_REPLAY'

    def __init__(self, *, venue, quotes, cash=10_000., fee_bps=10., amount_step=1e-8, price_tick=.01, min_notional=1.):
        if not 0 <= fee_bps < 100 or not math.isfinite(cash) or cash <= 0:
            raise ValueError('INVALID_REPLAY_ACCOUNT')
        self.venue, self.quotes, self.fee_bps = venue, deepcopy(quotes), fee_bps
        self.cash, self.positions, self.orders = float(cash), {}, {}
        self.submit_calls = 0
        self.fail_before_submit = False
        self.fail_after_submit = False
        self.fill_fraction = 1.
        self.amount_step, self.price_tick, self.min_notional = amount_step, price_tick, min_notional
        if any(isinstance(x, bool) or not math.isfinite(x) or x <= 0 for x in (amount_step, price_tick, min_notional)):
            raise ValueError('INVALID_REPLAY_MARKET_LIMITS')
        self.protections = {}
        self.protection_submit_calls = self.flatten_calls = 0
        self.fail_protection_before = self.fail_protection_after = False
        self.protection_read_unknown = False
        self.fail_cancel_protection = False
        self.fail_flatten_after = False
        self.protection_ack_quantity_factor = 1.

    def quote(self, symbol):
        return deepcopy(self.quotes[symbol])

    def account(self, now):
        exposure = {s: q * self.quotes[s]['bid'] for s, q in self.positions.items()}
        return {'timestamp': now, 'cash': self.cash, 'positions': dict(self.positions),
                'asset_exposure': exposure, 'equity': self.cash + math.fsum(exposure.values())}

    def prepare_order(self, *, symbol, amount, price, max_order_quote):
        def floor(value, step):
            return float((Decimal(str(value))/Decimal(str(step))).to_integral_value(rounding=ROUND_FLOOR)*Decimal(str(step)))
        amount, price = floor(amount, self.amount_step), floor(price, self.price_tick)
        if amount <= 0 or amount*price < self.min_notional:
            raise ValueError('BELOW_MINIMUM_ORDER')
        return {'symbol': symbol, 'side': 'buy', 'amount': amount, 'price': price,
                'max_order_quote': max_order_quote}

    def submit_order(self, intent, client_order_id):
        self.submit_calls += 1
        if self.fail_before_submit:
            raise TimeoutError('REPLAY_INJECTED_BEFORE_SUBMIT')
        if client_order_id in self.orders:
            raise RuntimeError('DUPLICATE_SUBMIT')
        if not 0 <= self.fill_fraction <= 1:
            raise ValueError('INVALID_FILL_FRACTION')
        quote = self.quote(intent['symbol'])
        filled = intent['amount'] * self.fill_fraction if quote['ask'] <= intent['price'] else 0.
        cost = filled * quote['ask']
        fee = cost * self.fee_bps / 10_000
        if cost + fee > self.cash:
            raise RuntimeError('REPLAY_INSUFFICIENT_FUNDS')
        self.cash -= cost + fee
        if filled:
            self.positions[intent['symbol']] = self.positions.get(intent['symbol'], 0.) + filled
        result = {'id': 'REPLAY-'+client_order_id, 'clientOrderId': client_order_id,
                  'symbol': intent['symbol'], 'side': 'buy', 'amount': intent['amount'],
                  'filled': filled, 'cost': cost, 'fee_quote': fee,
                  'status': 'FILLED' if filled == intent['amount'] else 'CANCELLED',
                  'evidence_class': 'ENGINEERING_REPLAY'}
        self.orders[client_order_id] = result
        if self.fail_after_submit:
            raise TimeoutError('REPLAY_INJECTED_AFTER_SUBMIT')
        return deepcopy(result)

    def find_order(self, client_order_id):
        return deepcopy(self.orders.get(client_order_id))

    def submit_protection(self, request, *, now):
        self.protection_submit_calls += 1
        if self.fail_protection_before:
            raise TimeoutError('FAKE_PROTECTION_BEFORE_ACK')
        identifier = request['protection_id']
        if identifier in self.protections:
            raise RuntimeError('DUPLICATE_PROTECTION_SUBMIT')
        group = {**deepcopy(request), 'quantity': request['quantity']*self.protection_ack_quantity_factor,
                 'group_id': 'FAKE-PROTECT-'+identifier, 'stop_status': 'OPEN', 'tp_status': 'OPEN',
                 'acknowledged': True, 'evidence_class': 'ENGINEERING_REPLAY', 'observed_at': now,
                 'exit_receipts': []}
        self.protections[identifier] = group
        if self.fail_protection_after:
            raise TimeoutError('FAKE_PROTECTION_AFTER_ACK')
        return deepcopy(group)

    def find_protection(self, identifier, *, now):
        if self.protection_read_unknown:
            return {'state': 'UNKNOWN', 'evidence_class': 'ENGINEERING_REPLAY'}
        if identifier not in self.protections:
            return {'state': 'ABSENT_CONFIRMED', 'evidence_class': 'ENGINEERING_REPLAY'}
        group = deepcopy(self.protections[identifier]);group['observed_at'] = now
        return group

    def cancel_protection(self, identifier, *, now):
        if self.fail_cancel_protection:
            raise TimeoutError('FAKE_CANCELLATION_UNKNOWN')
        if identifier not in self.protections:
            return {'state': 'ABSENT_CONFIRMED', 'evidence_class': 'ENGINEERING_REPLAY'}
        group = self.protections[identifier]
        for key in ('stop_status', 'tp_status'):
            if group[key] not in {'FILLED', 'CANCELLED'}:
                group[key] = 'CANCELLED'
        group['observed_at'] = now
        return deepcopy(group)

    def fill_protection(self, identifier, leg, *, fraction=1.):
        group = self.protections[identifier]
        key = {'STOP': 'stop_status', 'TP': 'tp_status'}[leg]
        if group[key] != 'OPEN' or not 0 < fraction <= 1:
            raise ValueError('FAKE_EXIT_NOT_EXECUTABLE')
        symbol = group['symbol']
        quantity = min(group['quantity']*fraction, self.positions.get(symbol, 0.))
        if quantity <= 0:
            raise ValueError('FAKE_NO_POSITION_TO_EXIT')
        price = group['stop_loss'] if leg == 'STOP' else group['take_profit']
        receipt = self._sell(symbol, quantity, price, 'X'+identifier[1:]+leg)
        group['exit_receipts'].append(receipt)
        group[key] = 'FILLED' if fraction == 1 else 'PARTIAL'
        return deepcopy(receipt)

    def _sell(self, symbol, quantity, price, identifier):
        if identifier in self.orders:
            raise RuntimeError('DUPLICATE_EXIT')
        cost, fee = quantity*price, quantity*price*self.fee_bps/10_000
        self.cash += cost-fee
        remaining = self.positions[symbol]-quantity
        if remaining > 1e-8:
            self.positions[symbol] = remaining
        else:
            self.positions.pop(symbol, None)
        receipt = {'id': 'FAKE-EXIT-'+identifier, 'clientOrderId': identifier, 'symbol': symbol,
                   'side': 'sell', 'amount': quantity, 'filled': quantity, 'cost': cost, 'fee_quote': fee,
                   'status': 'FILLED', 'evidence_class': 'ENGINEERING_REPLAY'}
        self.orders[identifier] = receipt
        return deepcopy(receipt)

    def emergency_flatten(self, symbol, quantity, identifier):
        self.flatten_calls += 1
        if quantity > self.positions.get(symbol, 0.)+1e-8:
            raise ValueError('FAKE_FLATTEN_QUANTITY_MISMATCH')
        receipt = self._sell(symbol, quantity, self.quotes[symbol]['bid'], identifier)
        if self.fail_flatten_after:
            raise TimeoutError('FAKE_FLATTEN_AFTER_FILL')
        return receipt
