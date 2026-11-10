"""Synthetic SQL collaborator; PostgreSQL integration is still required."""
import unittest
from datetime import datetime, timezone
from decimal import Decimal as D
from unittest.mock import patch
from research_bot.durable_risk import PostgresRiskGovernor


class Result:
    def __init__(self, value=None): self.value = value
    def fetchone(self): return self.value


class FakeConnection:
    def __init__(self, fee=D('0.1'), currency='BNB', state='CANCELLED'):
        self.queries = []
        self.fee, self.currency = fee, currency
        self.state = state
    def execute(self, sql, params):
        self.queries.append((' '.join(sql.split()), params))
        if 'FROM risk_reservations WHERE' in sql:
            return Result(('rid','BTC/USDT','BUY',D('1'),D('0'),D('0'),D('0'),'OPEN'))
        if 'FROM testnet_orders WHERE' in sql:
            return Result((self.state,D('0'),None,self.fee,self.currency))
        return Result()


class ZeroFillForeignFee(unittest.TestCase):
    def test_foreign_fee_not_deducted_as_quote_or_released(self):
        conn=FakeConnection()
        account={'cash':D('1000'),'positions':{},'reconciled':True,'unresolved_orders':False}
        governor=PostgresRiskGovernor.__new__(PostgresRiskGovernor)
        with patch.object(governor,'_account',return_value=account), patch.object(governor,'_audit'):
            governor.reconcile_accounting(account_id='a',venue_id='v',client_order_id='c',
                now=datetime(2026,1,1,tzinfo=timezone.utc),_connection=conn)
        liability=[p for q,p in conn.queries if q.startswith('INSERT INTO risk_fee_liabilities')]
        account_updates=[p for q,p in conn.queries if q.startswith('UPDATE risk_account_state SET cash=')]
        reservation_updates=[p for q,p in conn.queries if q.startswith('UPDATE risk_reservations SET matched_quantity=')]
        self.assertEqual(len(liability),1)
        self.assertEqual(liability[0][3],'UNRESOLVED')
        self.assertEqual(account_updates[0][0],D('1000'))
        self.assertTrue(account_updates[0][2])
        self.assertEqual(reservation_updates[0][3],'UNRESOLVED_FEE')

    def test_confirmed_quote_fee_is_applied_in_quote_currency(self):
        conn=FakeConnection(fee=D('0.1'),currency='USDT')
        account={'cash':D('1000'),'positions':{},'reconciled':True,'unresolved_orders':False}
        governor=PostgresRiskGovernor.__new__(PostgresRiskGovernor)
        with patch.object(governor,'_account',return_value=account), patch.object(governor,'_audit'):
            governor.reconcile_accounting(account_id='a',venue_id='v',client_order_id='c',
                now=datetime(2026,1,1,tzinfo=timezone.utc),_connection=conn)
        updates=[p for q,p in conn.queries if q.startswith('UPDATE risk_account_state SET cash=')]
        self.assertEqual(updates[0][0],D('999.9'))

    def test_missing_fee_currency_keeps_unfilled_order_unresolved(self):
        conn=FakeConnection(fee=D('0'),currency=None)
        account={'cash':D('1000'),'positions':{},'reconciled':True,'unresolved_orders':False}
        governor=PostgresRiskGovernor.__new__(PostgresRiskGovernor)
        with patch.object(governor,'_account',return_value=account), patch.object(governor,'_audit'):
            governor.reconcile_accounting(account_id='a',venue_id='v',client_order_id='c',
                now=datetime(2026,1,1,tzinfo=timezone.utc),_connection=conn)
        liabilities=[p for q,p in conn.queries if q.startswith('INSERT INTO risk_fee_liabilities')]
        reservations=[p for q,p in conn.queries if q.startswith('UPDATE risk_reservations SET matched_quantity=')]
        self.assertEqual(liabilities[0][3], 'UNRESOLVED')
        self.assertEqual(reservations[0][3], 'UNRESOLVED_FEE')

    def test_working_unfilled_order_without_fee_is_not_a_terminal_liability(self):
        conn=FakeConnection(fee=D('0'),currency=None,state='ACKNOWLEDGED')
        account={'cash':D('1000'),'positions':{},'reconciled':True,'unresolved_orders':False}
        governor=PostgresRiskGovernor.__new__(PostgresRiskGovernor)
        with patch.object(governor,'_account',return_value=account), patch.object(governor,'_audit'):
            governor.reconcile_accounting(account_id='a',venue_id='v',client_order_id='c',
                now=datetime(2026,1,1,tzinfo=timezone.utc),_connection=conn)
        self.assertFalse(any(q.startswith('INSERT INTO risk_fee_liabilities') for q, _ in conn.queries))

if __name__ == '__main__': unittest.main()
