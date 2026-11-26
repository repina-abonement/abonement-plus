import unittest
from unittest.mock import patch

from crm import maintenance_354, money, services
from tests import test_invoice_balance_flows


class TestSaleCleanup(unittest.TestCase):
    setUp = test_invoice_balance_flows.InvoiceBalanceFlowsTest.setUp
    quote = test_invoice_balance_flows.InvoiceBalanceFlowsTest.quote
    invoice = test_invoice_balance_flows.InvoiceBalanceFlowsTest.invoice

    def test_purge_is_precise_and_protects_fiscal_references(self):
        import hashlib
        con = self.con
        con.execute('DELETE FROM payments WHERE id=?', (self.deposit_id,))
        subid = self.invoice()['id']
        services.pay_subscription(con, subid, self.u, 'cash', 12500, '2026-11-20', 'synthetic-test-sale-cash')
        con.execute("UPDATE payments SET created_at='2026-11-26' WHERE student_id=?", (self.sid,))
        con.execute("UPDATE subscriptions SET created_at='2026-11-26' WHERE id=?", (subid,))
        payment = con.execute("SELECT id FROM payments WHERE student_id=?", (self.sid,)).fetchone()[0]
        note = hashlib.sha256('balance child synthetic'.encode()).hexdigest()
        other = services.create_student(con, {'name':'Synthetic Other', 'parent_name':'Other parent',
            'phone':'+79000100000', 'group_id':self.gid, 'funnel':'members'}, self.u)
        other_payment = money.add_payment(con, other, 1550, 'cash', self.u)
        with patch.multiple(maintenance_354, STUDENT_ID=self.sid, IDENTITY=note):
            con.execute("INSERT INTO fiscal_payment_checks VALUES (?,'2026-11-26','confirmed','{}')", (payment,))
            self.assertEqual(maintenance_354.apply(con), 'protected-records')
            self.assertIsNotNone(con.execute('SELECT 1 FROM payments WHERE id=?', (payment,)).fetchone())
            con.execute('DELETE FROM fiscal_payment_checks WHERE payment_id=?', (payment,))
            self.assertEqual(maintenance_354.apply(con), 'complete')
            self.assertEqual(maintenance_354.apply(con), 'complete')
            for table in ('students', 'subscriptions', 'payments', 'history', 'invoice_creation_requests'):
                where = 'id' if table == 'students' else 'student_id'
                self.assertIsNone(con.execute(f'SELECT 1 FROM {table} WHERE {where}=?', (self.sid,)).fetchone())
            self.assertIsNotNone(con.execute('SELECT 1 FROM payments WHERE id=?', (other_payment,)).fetchone())
            self.assertEqual(list(con.execute('PRAGMA foreign_key_check')), [])
