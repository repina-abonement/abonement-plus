from unittest import TestCase
from unittest.mock import patch

from crm import analytics, balancecredit, money, referrals, revenuelosses, salary, server, services, smart
from tests import test_sale_paid_date


class ReferralBonusTest(TestCase):
    setUp = test_sale_paid_date.SaleDateTest.setUp

    def test_award_and_spend_separate_from_money_and_revenue(self):
        con, friend, user = self.con, self.sid, self.u
        recipient = services.create_student(con, {
            'name': 'Synthetic Referrer', 'parent_name': 'Parent Two', 'phone': '+79000000102',
            'group_id': self.gid, 'funnel': 'members',
        }, user)
        with patch.object(smart, 'REQUIRE', False):
            self.assertFalse(con.execute("SELECT 1 FROM subscriptions WHERE student_id=?", (friend,)).fetchone())
            response = server.referral_bonus(con, None, user, {'friend_id': friend}, {}, recipient)
            self.assertEqual(response['balance']['referral_balance'], 3000)
            self.assertEqual(response['balance']['cash_free_deposit'], 0)
            self.assertTrue(referrals.award(con, recipient, friend, user)['already_awarded'])
            self.assertEqual(con.execute('SELECT COUNT(*) FROM payments').fetchone()[0], 0)
            self.assertEqual(revenuelosses.report(con, '2026-11')['totals']['bonuses'], 0)
            bill = services.create_invoice(con, recipient, '2026-11', user, group_id=self.gid,
                                           paid_date='2026-11-16', excluded_dates=['2026-11-30'])

        preview = balancecredit.preview(con, recipient, bill['quote']['total'], True, bill['id'])
        self.assertEqual(preview['apply_amount'], 3000)
        self.assertEqual(money.balance(con, recipient)['referral_balance'], 3000)
        balancecredit.apply_to_invoice(con, bill['id'], user, True, preview['token'], 'referral-use-123')
        self.assertEqual(money.balance(con, recipient)['referral_balance'], 0)
        self.assertEqual(con.execute('SELECT COUNT(*) FROM payments').fetchone()[0], 0)
        services.pay_subscription(con, bill['id'], user, 'card_branch',
                                  preview['payable'], '2026-11-16', 'referral-recipient-pay-123')
        self.assertEqual([row['amount'] for row in money.subscription_payment_details(con, recipient)[bill['id']]],
                         [preview['payable']])
        totals = revenuelosses.report(con, '2026-11', group_id=self.gid)['totals']
        self.assertEqual(totals['bonuses'], 3000)
        self.assertGreater(totals['incomplete'], 0)
        self.assertEqual(totals['actual'], preview['payable'])
        self.assertEqual(totals['potential'], totals['actual'] + totals['losses'] - totals['surcharge'])
        self.assertEqual(analytics._holders(con, '2026-11')[recipient]['revenue'], preview['payable'])
        self.assertEqual(salary._revenue(con, '2026-11')[self.gid], totals['actual'])
        self.assertEqual(services.dashboard(con, '2026-11', group_id=self.gid)['kpi']['revenue']['value'],
                         totals['actual'])
