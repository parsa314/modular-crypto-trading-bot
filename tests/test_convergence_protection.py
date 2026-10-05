from datetime import timedelta
from dataclasses import replace
import pytest

from test_convergence_oms import setup
from research_bot.execution.oms import OrderManagementSystem
from research_bot.execution.ledger import LiveLedger
from research_bot.execution.protection import ProtectiveStateMachine, STATES
from research_bot.execution.protective_adapters import protective_adapter
from research_bot.execution.replay_exchange import EngineeringReplayExchange


def record(ledger, sealed):
    return ledger.get_state()['protection'][sealed.intent.client_order_id]


@pytest.mark.parametrize('fraction', [1., .3])
def test_positive_fill_requires_ack_sized_to_actual_fill(tmp_path, fraction):
    *_, sealed, gateway, ledger, kwargs = setup(tmp_path)
    gateway.fill_fraction = fraction
    with ledger:
        OrderManagementSystem(ledger=ledger, **kwargs).process(sealed)
        r = record(ledger, sealed)
        assert r['state'] == 'POSITION_PROTECTED'
        assert {'PROTECTION_REQUIRED', 'PROTECTION_SUBMITTING', 'PROTECTION_CONFIRMED'} <= {h['to'] for h in r['history']}
        assert r['ack_hash']
        assert r['quantity'] == gateway.positions[sealed.intent.symbol]
        assert all(h['from'] in STATES and h['to'] in STATES for h in r['history'])


@pytest.mark.parametrize('fault', ['fail_protection_before', 'fail_protection_after'])
def test_restart_during_registration_reconciles_without_duplicate(tmp_path, fault):
    *_, sealed, gateway, ledger, kwargs = setup(tmp_path)
    setattr(gateway, fault, True)
    with ledger:
        OrderManagementSystem(ledger=ledger, **kwargs).process(sealed)
        assert record(ledger, sealed)['state'] == 'RECONCILIATION_REQUIRED'
        assert ledger.get_state()['halt_reason'] == 'POSITION_UNPROTECTED'
    setattr(gateway, fault, False)
    with LiveLedger(tmp_path/'oms.sqlite', OrderManagementSystem.identity('acct', kwargs['manifest'])) as restored:
        OrderManagementSystem(ledger=restored, **kwargs).process(sealed)
        assert gateway.submit_calls == gateway.protection_submit_calls == 1
        if fault == 'fail_protection_after':
            assert record(restored, sealed)['state'] == 'POSITION_PROTECTED'
        else:
            assert record(restored, sealed)['state'] == 'RECONCILIATION_REQUIRED'
            kwargs['clock'] = lambda: sealed.intent.valid_from+timedelta(seconds=6)
            OrderManagementSystem(ledger=restored, **kwargs).process(sealed)
            assert record(restored, sealed)['state'] == 'POSITION_CLOSED'
            assert restored.get_state()['positions'] == gateway.positions == {}
            assert restored.get_state()['halt_reason'] == 'PROTECTION_EMERGENCY_HALT'
            assert gateway.flatten_calls == 1


@pytest.mark.parametrize('leg', ['STOP', 'TP'])
def test_exit_fills_cancel_sibling_and_cash_is_idempotent(tmp_path, leg):
    *_, sealed, gateway, ledger, kwargs = setup(tmp_path)
    with ledger:
        oms = OrderManagementSystem(ledger=ledger, **kwargs)
        oms.process(sealed)
        identifier = next(iter(gateway.protections))
        gateway.fill_protection(identifier, leg)
        oms.process(sealed)
        state = ledger.get_state()
        assert state['positions'] == gateway.positions == {}
        assert state['cash'] == pytest.approx(gateway.cash)
        assert record(ledger, sealed)['state'] == 'POSITION_CLOSED'
        sibling = 'tp_status' if leg == 'STOP' else 'stop_status'
        assert gateway.protections[identifier][sibling] == 'CANCELLED'
        oms.process(sealed)
        assert ledger.get_state()['cash'] == state['cash']


def test_partial_exit_cancels_then_flattens_remaining_exposure(tmp_path):
    *_, sealed, gateway, ledger, kwargs = setup(tmp_path)
    with ledger:
        oms = OrderManagementSystem(ledger=ledger, **kwargs); oms.process(sealed)
        gateway.fill_protection(next(iter(gateway.protections)), 'TP', fraction=.4)
        oms.process(sealed)
        assert record(ledger, sealed)['state'] == 'POSITION_CLOSED'
        assert ledger.get_state()['positions'] == gateway.positions == {}
        assert ledger.get_state()['cash'] == pytest.approx(gateway.cash)
        assert gateway.flatten_calls == 1
        assert ledger.get_state()['halt_reason'] == 'PROTECTION_EMERGENCY_HALT'


def test_unknown_state_never_replaces_or_blindly_flattens(tmp_path):
    *_, sealed, gateway, ledger, kwargs = setup(tmp_path)
    with ledger:
        oms = OrderManagementSystem(ledger=ledger, **kwargs); oms.process(sealed)
        gateway.protection_read_unknown = True
        kwargs['clock'] = lambda: sealed.intent.valid_from+timedelta(seconds=9)
        OrderManagementSystem(ledger=ledger, **kwargs).process(sealed)
        assert record(ledger, sealed)['state'] == 'RECONCILIATION_REQUIRED'
        assert ledger.get_state()['halt_reason'] == 'POSITION_UNPROTECTED'
        assert gateway.protection_submit_calls == 1 and gateway.flatten_calls == 0


def test_bad_ack_cannot_report_protected_and_deadline_closes_exposure(tmp_path):
    *_, sealed, gateway, ledger, kwargs = setup(tmp_path)
    gateway.protection_ack_quantity_factor = .5
    with ledger:
        OrderManagementSystem(ledger=ledger, **kwargs).process(sealed)
        assert record(ledger, sealed)['state'] == 'RECONCILIATION_REQUIRED'
        kwargs['clock'] = lambda: sealed.intent.valid_from+timedelta(seconds=6)
        OrderManagementSystem(ledger=ledger, **kwargs).process(sealed)
        assert record(ledger, sealed)['state'] == 'POSITION_CLOSED'
        assert ledger.get_state()['positions'] == {}


def test_crash_after_emergency_flatten_recovers_once(tmp_path):
    *_, sealed, gateway, ledger, kwargs = setup(tmp_path)
    gateway.fail_protection_before = True
    with ledger:
        OrderManagementSystem(ledger=ledger, **kwargs).process(sealed)
        gateway.fail_flatten_after = True
        kwargs['clock'] = lambda: sealed.intent.valid_from+timedelta(seconds=6)
        ProtectiveStateMachine(ledger, gateway).reconcile(kwargs['clock']())
        assert record(ledger, sealed)['state'] == 'EMERGENCY_HALT'
    gateway.fail_flatten_after = False
    with LiveLedger(tmp_path/'oms.sqlite', OrderManagementSystem.identity('acct', kwargs['manifest'])) as restored:
        OrderManagementSystem(ledger=restored, **kwargs).process(sealed)
        assert record(restored, sealed)['state'] == 'POSITION_CLOSED'
        assert restored.get_state()['positions'] == gateway.positions == {}
        assert restored.get_state()['cash'] == pytest.approx(gateway.cash)
        assert gateway.flatten_calls == 1


@pytest.mark.parametrize('venue', ['fixture', 'binance', 'okx', 'coinex'])
def test_venue_seams_accept_only_fake_transport(venue):
    adapter = protective_adapter(EngineeringReplayExchange(venue=venue, quotes={}))
    assert adapter.venue == venue and adapter.live_adapter_verified is False
    with pytest.raises(ValueError, match='UNAVAILABLE'):
        protective_adapter(EngineeringReplayExchange(venue='unknown', quotes={}))


def test_unconfirmed_cancel_never_flattens(tmp_path):
    *_, sealed, gateway, ledger, kwargs = setup(tmp_path)
    gateway.protection_ack_quantity_factor = .5
    with ledger:
        OrderManagementSystem(ledger=ledger, **kwargs).process(sealed)
        gateway.fail_cancel_protection = True
        kwargs['clock'] = lambda: sealed.intent.valid_from+timedelta(seconds=6)
        OrderManagementSystem(ledger=ledger, **kwargs).process(sealed)
        assert gateway.flatten_calls == 0
        assert record(ledger, sealed)['state'] != 'POSITION_PROTECTED'
        assert ledger.get_state()['halt_reason'] == 'POSITION_UNPROTECTED'


def test_restart_cannot_reset_protection_deadline_after_ambiguous_filled_entry(tmp_path):
    *_, sealed, gateway, ledger, kwargs = setup(tmp_path)
    gateway.fail_after_submit = True
    with ledger:
        assert OrderManagementSystem(ledger=ledger, **kwargs).process(sealed)['status'] == 'UNKNOWN'
    gateway.fail_after_submit = False
    kwargs['clock'] = lambda: sealed.intent.valid_from+timedelta(seconds=10)
    with LiveLedger(tmp_path/'oms.sqlite', OrderManagementSystem.identity('acct', kwargs['manifest'])) as restored:
        OrderManagementSystem(ledger=restored, **kwargs).process(sealed)
        assert record(restored, sealed)['state'] == 'POSITION_CLOSED'
        assert restored.get_state()['positions'] == gateway.positions == {}
        assert gateway.submit_calls == gateway.flatten_calls == 1
        assert gateway.protection_submit_calls == 0
