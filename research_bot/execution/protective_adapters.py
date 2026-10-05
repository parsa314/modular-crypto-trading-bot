"""Venue-specific protective seams tested ONLY against a local Fake exchange.

Native Binance/OKX/CoinEx API capabilities, parameters and acknowledgements are
not certified here. No universal CCXT OCO/STOP_MARKET assumption is made.
"""
import math
from .replay_exchange import EngineeringReplayExchange


class FakeProtectiveAdapter:
    venue = 'fixture'
    adapter_id = 'FIXTURE_MATCHED_PROTECTIVE_PAIR_V1'
    live_adapter_verified = False

    def __init__(self, gateway):
        if type(gateway) is not EngineeringReplayExchange or gateway.venue != self.venue:
            raise ValueError('REAL_PROTECTIVE_TRANSPORT_FORBIDDEN_PHASE1')
        self.gateway = gateway

    def submit(self, intent, now):
        return self.gateway.submit_protection({**intent.__dict__, 'protection_id': intent.protection_id,
            'adapter_id': self.adapter_id}, now=now)

    def lookup(self, identifier, now):
        return self.gateway.find_protection(identifier, now=now)

    def cancel(self, identifier, now):
        return self.gateway.cancel_protection(identifier, now=now)

    def validate_ack(self, intent, ack, now):
        if (ack.get('evidence_class') != 'ENGINEERING_REPLAY' or ack.get('acknowledged') is not True
                or ack.get('protection_id') != intent.protection_id or ack.get('symbol') != intent.symbol
                or ack.get('venue') != self.venue or ack.get('adapter_id') != self.adapter_id
                or not isinstance(ack.get('group_id'), str) or not ack.get('group_id')
                or ack.get('stop_status') != 'OPEN' or ack.get('tp_status') != 'OPEN'
                or not math.isclose(ack.get('quantity', -1), intent.quantity, rel_tol=1e-10)
                or ack.get('stop_loss') != intent.stop_loss or ack.get('take_profit') != intent.take_profit
                or ack.get('observed_at') != now or ack.get('exit_receipts')):
            raise ValueError('PROTECTIVE_ACK_NOT_CONFIRMED')
        return True


class BinanceSpotProtectiveAdapter(FakeProtectiveAdapter):
    venue, adapter_id = 'binance', 'BINANCE_SPOT_FAKE_PROTECTIVE_PAIR_V1'


class OKXSpotProtectiveAdapter(FakeProtectiveAdapter):
    venue, adapter_id = 'okx', 'OKX_SPOT_FAKE_PROTECTIVE_PAIR_V1'


class CoinExSpotProtectiveAdapter(FakeProtectiveAdapter):
    venue, adapter_id = 'coinex', 'COINEX_SPOT_FAKE_PROTECTIVE_PAIR_V1'


def protective_adapter(gateway):
    classes = {x.venue: x for x in (FakeProtectiveAdapter, BinanceSpotProtectiveAdapter,
                                   OKXSpotProtectiveAdapter, CoinExSpotProtectiveAdapter)}
    if gateway.venue not in classes:
        raise ValueError('VENUE_PROTECTION_CAPABILITY_UNAVAILABLE')
    return classes[gateway.venue](gateway)
