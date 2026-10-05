import pytest

from research_bot.execution.postgres_ledger import PostgresLedger


@pytest.mark.parametrize('mode', ['LIVE','PAPER','TESTNET',None])
def test_private_modes_rejected_before_database_connection(monkeypatch, mode):
    def forbidden(*args, **kwargs):
        raise AssertionError('DATABASE_OR_EXCHANGE_ACCESS_FORBIDDEN')
    monkeypatch.setattr('psycopg.connect', forbidden)
    with pytest.raises(ValueError, match='ENGINEERING_REPLAY'):
        PostgresLedger('unused', 'journal', {'mode':mode,'account_id':'account'})
