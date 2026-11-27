import sqlite3
import unittest
from crm import cashreceipts


class CashReceiptsTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(':memory:')
        self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        self.con.executescript('''CREATE TABLE payments(id INTEGER PRIMARY KEY,amount INTEGER,
            method TEXT,paid_at TEXT,confirmed INTEGER,cancelled INTEGER);
            CREATE TABLE pay_links(payment_id INTEGER,pk_payment_id TEXT);''')

    def add(self, amount, method='cash', day='2026-11-20', confirmed=1, cancelled=0):
        return self.con.execute('INSERT INTO payments(amount,method,paid_at,confirmed,cancelled) VALUES (?,?,?,?,?)',
                                (amount,method,day,confirmed,cancelled)).lastrowid

    def test_actual_dates_internal_moves_pending_and_refunds(self):
        self.add(10800,'tochka_qr_link')  # entire receipt including unused deposit
        self.add(1350,day='2026-12-01')  # later top-up belongs to December
        self.add(-500,'refund',day='2026-12-02')
        self.add(3000,confirmed=0)
        self.add(9000,cancelled=1)
        for method in ('unknown','opening_balance','balance','refund_pending'):
            self.add(7000,method)
            self.add(-7000,method)
        nov=cashreceipts.report(self.con,'2026-11')
        self.assertEqual(nov['totals'],dict(received=10800,returned=0,net=10800,pending_received=3000,pending_returned=0))
        dec=cashreceipts.report(self.con,'2026-12')
        self.assertEqual(dec['totals']['net'],850)
        for key in dec['totals']:
            self.assertEqual(sum(x[key] for x in dec['days']),dec['totals'][key])
            self.assertEqual(sum(x[key] for x in dec['methods']),dec['totals'][key])

    def test_provider_links_do_not_duplicate_receipts_and_boundary(self):
        pid=self.add(100,'paykeeper_manual','2026-12-31T23:59:59')
        self.con.executemany('INSERT INTO pay_links VALUES (?,?)',[(pid,'a'),(pid,'b')])
        self.add(200,day='2027-01-01T00:00:00')
        rep=cashreceipts.report(self.con,'2026-12')
        self.assertEqual(rep['totals']['received'],100)
        self.assertEqual(rep['methods'][0]['method'],'paykeeper')
        self.con.execute('UPDATE payments SET cancelled=1 WHERE id=?',(pid,))
        self.assertEqual(cashreceipts.report(self.con,'2026-12')['totals']['received'],0)
        with self.assertRaises(ValueError):cashreceipts.report(self.con,'2026-13')
