from dataclasses import replace
from datetime import timedelta
import json

import pytest

from research_bot.execution.constitution import RISK_CONSTITUTION_V1 as R
from research_bot.execution.intent import IntentAuthority, SealedIntent
from research_bot.execution.ledger import LiveLedger
from research_bot.execution.oms import OrderManagementSystem
from research_bot.execution.replay_exchange import EngineeringReplayExchange
from research_bot.v59.convergence_replay import fixture_decision, fixture_manifest, fixture_promotion_gate, main, run
from research_bot.v59.production_adapter import adapt_research_decision


def setup(tmp_path, *, event='fixture-1'):
    engine, final, c, p = fixture_decision(event)
    manifest, authority = fixture_manifest(), IntentAuthority(b'engineering-test-key-only-not-live'*2)
    sealed = adapt_research_decision(engine=engine, final=final, candidate=c, prediction=p,
        manifest=manifest, authority=authority, account_id='acct', timeframe='1h', now=c.entry_time,
        promotion_gate=fixture_promotion_gate(manifest))
    gateway = EngineeringReplayExchange(venue='fixture', quotes={c.symbol:
        {'bid': 100., 'ask': 100., 'timestamp': c.entry_time}})
    ledger = LiveLedger(tmp_path/'oms.sqlite', OrderManagementSystem.identity('acct', manifest))
    kwargs = dict(gateway=gateway, manifest=manifest, authority=authority, account_id='acct',
                  clock=lambda: c.entry_time, recent_returns=(.001,)*30,
                  promotion_gate=fixture_promotion_gate(manifest))
    return engine, final, c, p, sealed, gateway, ledger, kwargs


def test_complete_replay_and_crash_drills_are_never_economic_evidence(tmp_path):
    report = run(tmp_path/'replay')
    assert report['status'] == 'ENGINEERING_REPLAY_CONFIRMED'
    assert report['economic_evidence_countable'] is False
    normal, after, before = report['drills']
    assert normal['order_state'] == after['order_state'] == 'FILLED'
    assert normal['cash'] == pytest.approx(after['cash'])
    assert after['first']['status'] == 'UNKNOWN'
    assert before['order_state'] == 'UNKNOWN'
    assert before['cash'] == 10_000
    assert all(d['submit_calls'] == 1 for d in report['drills'])
    assert normal['halt_reason'] is after['halt_reason'] is None
    assert normal['protection_state'] == after['protection_state'] == 'POSITION_PROTECTED'
    assert before['halt_reason'] == 'ORDER_AMBIGUOUS'
    with pytest.raises(FileExistsError):
        run(tmp_path/'replay')


def test_fill_cash_and_position_are_atomic_and_not_double_charged(tmp_path):
    _, final, c, _, sealed, gateway, ledger, kwargs = setup(tmp_path)
    try:
        r = OrderManagementSystem(ledger=ledger, **kwargs).process(sealed)
        state = ledger.get_state()
        expected_notional = 25/(.01+.0024)
        expected_quantity = expected_notional/(100*1.001)
        assert final.approved_notional == pytest.approx(expected_notional)
        assert r['status'] == 'FILLED'
        assert state['cash'] == pytest.approx(10_000-expected_notional)
        assert state['positions'][c.symbol] == pytest.approx(expected_quantity)
        again = OrderManagementSystem(ledger=ledger, **kwargs).process(sealed)
        assert again['status'] == 'ALREADY_RESERVED'
        assert ledger.get_state()['cash'] == state['cash']
        assert gateway.submit_calls == 1
    finally:
        ledger.close()


@pytest.mark.parametrize('fraction', [.25, .75])
def test_cancelled_ioc_partial_fill_is_settled_and_protected(tmp_path, fraction):
    *_, sealed, gateway, ledger, kwargs = setup(tmp_path)
    gateway.fill_fraction = fraction
    try:
        r = OrderManagementSystem(ledger=ledger, **kwargs).process(sealed)
        assert r['status'] == 'CANCELLED'
        assert ledger.get_state()['halt_reason'] is None
        protection = next(iter(gateway.protections.values()))
        assert protection['quantity'] == ledger.get_order(sealed.intent.client_order_id)['result']['filled']
        assert gateway.submit_calls == 1
        assert ledger.get_order(sealed.intent.client_order_id)['result']['filled'] > 0
    finally:
        ledger.close()


def test_zero_fill_reservation_survives_restart_and_changed_event_size(tmp_path):
    *_, sealed, gateway, ledger, kwargs = setup(tmp_path)
    gateway.fill_fraction = 0.
    try:
        assert OrderManagementSystem(ledger=ledger, **kwargs).process(sealed)['status'] == 'CANCELLED'
    finally:
        ledger.close()
    with LiveLedger(tmp_path/'oms.sqlite', OrderManagementSystem.identity('acct', kwargs['manifest'])) as restored:
        oms = OrderManagementSystem(ledger=restored, **kwargs)
        assert oms.process(sealed)['status'] == 'ALREADY_RESERVED'
        changed = replace(sealed.intent, approved_notional=sealed.intent.approved_notional/2)
        assert changed.client_order_id == sealed.intent.client_order_id
        assert oms.process(kwargs['authority'].seal(changed))['reason'] == 'EVENT_ID_REUSE_WITH_DIFFERENT_INTENT'
        assert gateway.submit_calls == 1


def test_tampered_intent_is_rejected_before_gateway(tmp_path):
    *_, sealed, gateway, ledger, kwargs = setup(tmp_path)
    try:
        tampered = SealedIntent(replace(sealed.intent, approved_notional=9000), sealed.mac)
        with pytest.raises(ValueError, match='AUTHENTICATION'):
            OrderManagementSystem(ledger=ledger, **kwargs).process(tampered)
        assert gateway.submit_calls == 0
        assert ledger.get_state() == {}
    finally:
        ledger.close()


@pytest.mark.parametrize('seconds', [-1, 31])
def test_early_or_expired_intent_never_reserves_or_submits(tmp_path, seconds):
    *_, sealed, gateway, ledger, kwargs = setup(tmp_path)
    kwargs['clock'] = lambda: sealed.intent.valid_from+timedelta(seconds=seconds)
    try:
        assert OrderManagementSystem(ledger=ledger, **kwargs).process(sealed)['reason'] == 'INTENT_EXPIRED_OR_EARLY'
        assert gateway.submit_calls == 0
        assert ledger.get_order(sealed.intent.client_order_id) is None
    finally:
        ledger.close()


@pytest.mark.parametrize('age', [-1, 11])
def test_stale_or_future_quote_latches_no_new_orders(tmp_path, age):
    *_, sealed, gateway, ledger, kwargs = setup(tmp_path)
    gateway.quotes[sealed.intent.symbol]['timestamp'] -= timedelta(seconds=age)
    try:
        assert OrderManagementSystem(ledger=ledger, **kwargs).process(sealed)['reason'] == 'DATA_STALE_OR_INVALID'
        assert gateway.submit_calls == 0
    finally:
        ledger.close()


@pytest.mark.parametrize('equity,reason', [(9400., 'DRAWDOWN_KILL'), (9700., 'DAILY_LOSS_KILL')])
def test_runtime_rechecks_same_constitution_instead_of_reusing_old_finance_pass(tmp_path, equity, reason):
    *_, sealed, gateway, ledger, kwargs = setup(tmp_path)
    gateway.cash = equity
    ledger.set_state({'cash': equity, 'positions': {}, 'peak_equity': 10_000.,
        'day': sealed.intent.valid_from.date().isoformat(), 'day_start_equity': 10_000.,
        'last_equity': equity, 'halt_reason': None, 'settled': {}})
    try:
        assert OrderManagementSystem(ledger=ledger, **kwargs).process(sealed)['reason'] == reason
        gateway.cash = 10_000.
        assert OrderManagementSystem(ledger=ledger, **kwargs).process(sealed)['reason'] == reason
        assert gateway.submit_calls == 0
    finally:
        ledger.close()


def test_source_decision_cannot_be_substituted(tmp_path):
    engine, final, c, p, sealed, gateway, ledger, kwargs = setup(tmp_path)
    try:
        with pytest.raises(ValueError, match='AUDIT_BINDING'):
            adapt_research_decision(engine=engine, final=replace(final, approved_notional=9000), candidate=c,
                prediction=p, manifest=kwargs['manifest'], authority=kwargs['authority'], account_id='acct',
                timeframe='1h', now=c.entry_time, promotion_gate=fixture_promotion_gate(kwargs['manifest']))
    finally:
        ledger.close()


@pytest.mark.parametrize('mode', ['live', 'testnet'])
def test_private_cli_gate_precedes_any_file_or_credential_access(tmp_path, mode):
    absent = tmp_path/'must-not-be-created'
    assert main(['--mode', mode, '--output', str(absent)]) == 2
    assert not absent.exists()


def test_a_gateway_mode_string_cannot_bypass_authorization(tmp_path):
    class ForbiddenGateway:
        mode = 'ENGINEERING_REPLAY'
        def account(self, now):
            raise AssertionError('PRIVATE_API_MUST_NOT_BE_CALLED')
    *_, sealed, gateway, ledger, kwargs = setup(tmp_path)
    kwargs['gateway'] = ForbiddenGateway()
    try:
        with pytest.raises(ValueError, match='PRIVATE_EXECUTION'):
            OrderManagementSystem(ledger=ledger, **kwargs)
    finally:
        ledger.close()


def test_atomic_settlement_rolls_back_on_account_compare_and_swap_conflict(tmp_path):
    *_, sealed, gateway, ledger, kwargs = setup(tmp_path)
    try:
        ledger.set_state({'cash': 100.})
        ledger.claim_order('atomic-test', {'amount': 1})
        with pytest.raises(RuntimeError, match='state changed'):
            ledger.update_order('atomic-test', 'FILLED', {'filled': 1.}, account_state={'cash': 50.},
                                 expected_account_state={'cash': 101.})
        assert ledger.get_order('atomic-test')['state'] == 'SUBMITTING'
        assert ledger.get_state() == {'cash': 100.}
    finally:
        ledger.close()


def test_second_executor_cannot_submit_while_session_lock_is_held(tmp_path):
    *_, sealed, gateway, ledger, kwargs = setup(tmp_path)
    try:
        with LiveLedger(tmp_path/'oms.sqlite', ledger.identity) as other:
            with ledger.session_lock():
                with pytest.raises(RuntimeError, match='another runner'):
                    OrderManagementSystem(ledger=other, **kwargs).process(sealed)
        assert gateway.submit_calls == 0
    finally:
        ledger.close()


def test_crash_after_reservation_before_submit_never_resubmits(tmp_path, monkeypatch):
    *_, sealed, gateway, ledger, kwargs = setup(tmp_path)
    class ProcessDied(BaseException):
        pass
    original = gateway.submit_order
    def crash(*args):
        raise ProcessDied()
    monkeypatch.setattr(gateway, 'submit_order', crash)
    with ledger:
        with pytest.raises(ProcessDied):
            OrderManagementSystem(ledger=ledger, **kwargs).process(sealed)
        assert ledger.get_order(sealed.intent.client_order_id)['state'] == 'SUBMITTING'
    monkeypatch.setattr(gateway, 'submit_order', original)
    with LiveLedger(tmp_path/'oms.sqlite', OrderManagementSystem.identity('acct', kwargs['manifest'])) as restored:
        assert OrderManagementSystem(ledger=restored, **kwargs).process(sealed)['reason'] == 'ORDER_AMBIGUOUS'
        assert gateway.submit_calls == 0


def test_minimum_and_precision_are_checked_before_reservation(tmp_path):
    *_, sealed, gateway, ledger, kwargs = setup(tmp_path)
    gateway.min_notional = 100_000.
    with ledger:
        assert OrderManagementSystem(ledger=ledger, **kwargs).process(sealed)['reason'] == 'BELOW_MINIMUM_ORDER'
        assert gateway.submit_calls == 0 and ledger.get_order(sealed.intent.client_order_id) is None
    plan = EngineeringReplayExchange(venue='fixture', quotes={},
        amount_step=.001, price_tick=.05).prepare_order(symbol='BTC/USDT', amount=1.23456, price=100.079, max_order_quote=200.)
    assert plan['amount'] == 1.234 and plan['price'] == 100.05


@pytest.mark.parametrize('name,value', [('limit_price', float('nan')), ('stop', 0.), ('target', float('inf'))])
def test_invalid_intent_price_fails_before_any_gateway(tmp_path, name, value):
    *_, sealed, gateway, ledger, kwargs = setup(tmp_path)
    with ledger:
        with pytest.raises(ValueError):
            replace(sealed.intent, **{name: value})
        assert gateway.submit_calls == 0


def test_missing_venue_receipt_id_cannot_establish_position_or_protection(tmp_path, monkeypatch):
    *_, sealed, gateway, ledger, kwargs = setup(tmp_path)
    original = gateway.submit_order
    def corrupt(*args):
        result = original(*args);result.pop('id');return result
    monkeypatch.setattr(gateway, 'submit_order', corrupt)
    with ledger:
        result = OrderManagementSystem(ledger=ledger, **kwargs).process(sealed)
        assert result['status'] == 'UNKNOWN'
        assert ledger.get_state()['positions'] == {}  # No fabricated settled/protected state.
        assert gateway.protection_submit_calls == 0
        monkeypatch.setattr(gateway, 'submit_order', original)
        # Authoritative recovery has the intact persisted Fake venue receipt.
        OrderManagementSystem(ledger=ledger, **kwargs).process(sealed)
        assert ledger.get_state()['protection'][sealed.intent.client_order_id]['state'] == 'POSITION_PROTECTED'
        assert gateway.submit_calls == 1
