from datetime import date
import unittest
from unittest.mock import patch

from crm import individualinvoice, money, paylinks, server, services
from tests import test_invoice_balance_flows


class IndividualInvoiceTest(unittest.TestCase):
    setUp = test_invoice_balance_flows.InvoiceBalanceFlowsTest.setUp
    invoice = test_invoice_balance_flows.InvoiceBalanceFlowsTest.invoice
    quote = test_invoice_balance_flows.InvoiceBalanceFlowsTest.quote

    def test_agreed_price_is_used_for_link_and_factual_payment(self):
        reason = 'Согласовано с руководителем, ошибка при выставлении'
        self.con.execute('UPDATE price_list SET late_pct=5')
        with patch.object(services, 'today', return_value=date(2026, 11, 26)):
            query = {'month':'2026-12', 'group_id':str(self.gid), 'paid_date':'2026-11-26',
                     'individual_price':'12150'}
            quote = server.quote(self.con, None, self.u, {}, query, self.sid)
            self.assertEqual(quote['total'], 12150)
            body = {'month':'2026-12', 'group_id':self.gid, 'paid_date':'2026-11-26',
                    'individual_price':12150, 'individual_reason':reason,
                    'request_id':'synthetic-individual-sale', 'expected_balance_token':quote['balance_preview']['token']}
            sub = server.invoice(self.con, None, self.u, body, {}, self.sid)['id']
            self.assertEqual(server.invoice(self.con, None, self.u, body, {}, self.sid)['id'], sub)
            before = self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0]
            with patch.object(paylinks, 'ready', return_value=True):
                link = paylinks.create(self.con, self.sid, 12150, self.u, subscription_id=sub, full_payment=True)
            self.assertEqual(paylinks.payment_page(self.con, link['url'].rsplit('/', 1)[-1])['amount'], 12150)
            self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0], before)
            services.pay_subscription(self.con, sub, self.u, 'tochka_qr_link', 12150, '2026-11-26', 'synthetic-individual-pay')
            invoice = self.con.execute('SELECT status,price,corrected,calculation_reason FROM subscriptions WHERE id=?', (sub,)).fetchone()
            self.assertEqual(tuple(invoice)[:3], ('paid', 12150, 1))
            self.assertIn(reason, invoice['calculation_reason'])
            self.assertEqual(money.balance(self.con, self.sid)['covered'][sub], 12150)
            with self.assertRaises(services.ApiError):
                individualinvoice.cancel(self.con, sub, 'Ошибочно выставили счёт', self.u)

    def test_unpaid_invoice_can_be_adjusted_then_cancelled(self):
        sub = self.invoice()['id']
        with patch.object(paylinks, 'ready', return_value=True):
            link = paylinks.create(self.con, self.sid, 12150, self.u, subscription_id=sub, full_payment=True)
        individualinvoice.change(self.con, sub, 11000, 'Согласовано с руководителем', self.u)
        self.assertEqual(services.payment_quote(self.con, sub)['total'], 11000)
        self.assertEqual(self.con.execute('SELECT status FROM pay_links WHERE id=?', (link['id'],)).fetchone()[0], 'expired')
        with patch.object(paylinks, 'ready', return_value=True):
            link = paylinks.create(self.con, self.sid, 11000, self.u, subscription_id=sub, full_payment=True)
        reason = 'Счёт выставлен на другую группу'
        self.con.execute("UPDATE pay_links SET checkout_state='creating' WHERE id=?", (link['id'],))
        with self.assertRaisesRegex(services.ApiError, 'PayKeeper'):
            individualinvoice.cancel(self.con, sub, reason, self.u)
        self.con.execute("UPDATE pay_links SET checkout_state='' WHERE id=?", (link['id'],))
        individualinvoice.cancel(self.con, sub, reason, self.u)
        individualinvoice.cancel(self.con, sub, reason, self.u)
        self.assertEqual(self.con.execute('SELECT status FROM subscriptions WHERE id=?', (sub,)).fetchone()[0], 'cancelled')
        self.assertEqual(self.con.execute('SELECT status FROM pay_links WHERE id=?', (link['id'],)).fetchone()[0], 'expired')
