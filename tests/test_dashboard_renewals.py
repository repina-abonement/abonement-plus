import unittest
from datetime import date

from crm import dashboard_renewals, db


class RenewalSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(':memory:')
        self.addCleanup(self.con.close)
        db.init_db(self.con)
        family = self.con.execute("INSERT INTO families(name) VALUES ('Тест')").lastrowid
        self.ids = [self.con.execute("""INSERT INTO students(name,family_id,created_at,stage_changed_at)
            VALUES('Ученик',?,'2022-01-01','2022-01-01')""", (family,)).lastrowid for _ in range(3)]

    def sale(self, kid, month, sold, kind='Абонемент'):
        self.con.execute("""INSERT INTO erp_revenue_history VALUES
            (hex(randomblob(16)),?,'1',?,?,?,'','',100)""", (self.ids[kid], month, kind, sold))

    def test_prepayments_late_sales_and_comparable_dates(self):
        for kid in range(3):
            self.sale(kid, '2024-01', '02.01.2024')
            self.sale(kid, '2024-02', '30.01.2024')
        self.sale(0, '2024-03', '25.02.2024')
        self.sale(0, '2024-03', '12.03.2024')
        self.sale(1, '2024-03', '31.03.2024')
        self.sale(2, '2024-03', '02.03.2024', 'Разовое')
        snapshot = dashboard_renewals.periods(self.con, date(2024, 3, 30))
        rate = snapshot('2024-03')
        self.assertEqual((rate['renewed'], rate['previous'], rate['value']), (1, 3, 33.3))
        self.assertEqual(snapshot('2024-03', -1)['as_of'], '2024-02-29')
        self.assertEqual(snapshot('2024-03', -12)['as_of'], '2023-03-30')
        self.assertEqual(snapshot('2024-02', -1)['as_of'], '2024-01-31')
        self.assertIsNone(snapshot('2024-01')['value'])

    def test_no_renewals_after_a_paid_month_is_zero(self):
        self.sale(0, '2024-01', '01.01.2024')
        self.sale(1, '2024-01', '15.03.2024')
        result = dashboard_renewals.periods(self.con, date(2024, 3, 30))('2024-02')
        self.assertEqual((result['renewed'], result['previous'], result['value']), (0, 1, 0))
