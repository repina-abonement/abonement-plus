"""Client-card sale permissions via real HTTP on a disposable local database."""
from contextlib import ExitStack, redirect_stdout
from datetime import date, datetime
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from crm import db, fiscal, money, paylinks, perms, server, services as sv


class ClientSalesAccessTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='client-sale-capability-')
        self.addCleanup(folder.cleanup)
        self.path = str(Path(folder.name)/'isolated.sqlite3')
        self.con = db.connect(self.path)
        self.addCleanup(self.con.close)
        with redirect_stdout(io.StringIO()):
            db.init_db(self.con)
        self.owner = dict(self.con.execute("SELECT * FROM users WHERE role='admin' LIMIT 1").fetchone())
        self.actors = {'owner': self.owner}
        for key, name, role, senior, offline in (
            ('varya', 'Варвара Батарева', 'operator', 0, 0),
            ('senior', 'Synthetic Senior', 'operator', 1, 0),
            ('offline', 'Synthetic Offline', 'operator', 0, 1),
            ('coach', 'Synthetic Coach', 'coach', 0, 0),
            ('lawyer', 'Synthetic Lawyer', 'lawyer', 0, 0),
            ('trainee', 'Synthetic Trainee', 'trainee', 0, 0)):
            uid = self.con.execute('''INSERT INTO users(login,name,role,pw_hash,is_senior,is_offline_admin)
                VALUES(?,?,?,'disabled',?,?)''', ('sale-'+key, name, role, senior, offline)).lastrowid
            self.actors[key] = dict(self.con.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone())
        for key, actor in self.actors.items():
            self.con.execute('INSERT INTO sessions VALUES(?,?,?)',
                ('synthetic-sale-'+key, actor['id'], datetime.now().isoformat()))
        bid = self.con.execute("INSERT INTO branches(name) VALUES('Synthetic Sale Branch')").lastrowid
        self.gid = self.con.execute("INSERT INTO groups(branch_id,name,weekdays,time) VALUES(?,'Synthetic Wed Sun','2,6','18:45')", (bid,)).lastrowid
        self.closed = self.con.execute("INSERT INTO groups(branch_id,name,weekdays,time,active) VALUES(?,'Hidden Closed','2,6','18:45',0)", (bid,)).lastrowid
        self.con.execute('UPDATE discounts SET active=0')
        self.con.execute('UPDATE price_list SET fixed=1350,flex=1550,late_pct=0')
        self.con.execute("INSERT OR REPLACE INTO settings VALUES('private_general_setting','synthetic-private-sentinel')")
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch.object(sv, 'today', return_value=date(2026, 11, 20)))
        self.provider = stack.enter_context(patch.object(fiscal, 'pk_call', side_effect=AssertionError('External request forbidden')))
        stack.enter_context(patch.object(fiscal, 'ofd_get', side_effect=AssertionError('External request forbidden')))
        actual_connect = db.connect
        stack.enter_context(patch.object(db, 'connect', side_effect=lambda path=None: actual_connect(self.path if path is None else path)))
        class Handler(server.Handler):
            def log_message(self, *args):
                pass
            def finish(self):
                try:
                    super().finish()
                finally:
                    db.reset_thread_conn()
        self.http = server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.counter = 0

    def stop_server(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=5)

    def call(self, actor, method, path, body=None):
        headers = {'Content-Type': 'application/json'}
        if actor:
            headers['Cookie'] = 'crm_session=synthetic-sale-'+actor
        request = Request('http://127.0.0.1:'+str(self.http.server_port)+path, method=method,
                          headers=headers, data=json.dumps(body).encode() if body is not None else None)
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            try:
                return error.code, json.load(error)
            finally:
                error.close()

    def student(self, deposit=0):
        self.counter += 1
        sid = sv.create_student(self.con, {'name': 'Synthetic Child '+str(self.counter),
            'parent_name': 'Synthetic Parent', 'birth': '2020-01-01',
            'phone': '+7900000'+str(1000+self.counter), 'email': 'receipt@example.invalid',
            'group_id': self.gid, 'funnel': 'members'}, self.owner)
        if deposit:
            money.add_payment(self.con, sid, deposit, 'card_branch', self.owner, paid_at='2026-11-19')
        return sid

    def invoice(self, actor, sid):
        code, quote = self.call(actor, 'GET', f'/api/students/{sid}/quote?month=2026-12&group_id={self.gid}&paid_date=2026-11-20')
        self.assertEqual(code, 200, quote)
        body = {'month': '2026-12', 'group_id': self.gid, 'paid_date': '2026-11-20',
            'use_balance': False, 'request_id': 'sale-invoice-'+actor+'-'+str(sid),
            'expected_balance_token': quote['balance_preview']['token']}
        code, result = self.call(actor, 'POST', f'/api/students/{sid}/invoice', body)
        self.assertEqual(code, 200, result)
        return result['id'], quote

    def test_effective_capability_meta_groups_and_prices_without_broad_access(self):
        for actor in ('owner', 'varya', 'senior', 'offline'):
            code, result = self.call(actor, 'GET', '/api/me')
            self.assertEqual(code, 200)
            self.assertEqual(result['perms']['client_sales'], 'full')
            code, meta = self.call(actor, 'GET', '/api/meta')
            self.assertEqual(code, 200, meta)
            self.assertIn(self.gid, [g['id'] for g in meta['groups']])
            self.assertNotIn(self.closed, [g['id'] for g in meta['groups']])
            if actor == 'offline':
                self.assertNotIn('members', result['perms'])
                self.assertNotIn('sales', result['perms'])
                self.assertFalse(perms.allowed(result, 'operator'))
                self.assertNotIn('private_general_setting', meta['settings'])
                self.assertIn('price_single', meta['settings'])
        self.provider.assert_not_called()

    def test_monthly_invoice_default_off_explicit_credit_and_payment_for_each_sales_role(self):
        for actor in ('owner', 'varya', 'senior', 'offline'):
            with self.subTest(actor=actor):
                sid = self.student(2700)
                subid, quote = self.invoice(actor, sid)
                self.assertEqual(quote['balance_preview']['apply_amount'], 0)
                balance = money.balance(self.con, sid)
                self.assertEqual((balance['covered'][subid], balance['deposit']), (0, 2700))
                code, payment_quote = self.call(actor, 'GET', f'/api/subscriptions/{subid}/payment-quote?paid_date=2026-11-20&use_balance=1')
                self.assertEqual(code, 200, payment_quote)
                body = {'use_balance': True, 'paid_date': '2026-11-20',
                    'expected_balance_token': payment_quote['balance_preview']['token'],
                    'request_id': 'sale-balance-'+actor+'-'+str(subid)}
                self.assertEqual(self.call(actor, 'POST', f'/api/subscriptions/{subid}/balance-apply', body)[0], 200)
                self.assertEqual(self.call(actor, 'POST', f'/api/subscriptions/{subid}/balance-apply', body)[0], 200)
                code, paid = self.call(actor, 'POST', f'/api/subscriptions/{subid}/pay',
                    {'method': 'card_branch', 'amount': 1000, 'paid_date': '2026-11-20', 'request_id': 'sale-payment-'+actor+'-'+str(subid)})
                self.assertEqual(code, 200, paid)
                self.assertEqual(money.balance(self.con, sid)['covered'][subid], 3700)
        self.provider.assert_not_called()

    def test_single_and_multi_month_sale_routes_are_usable_by_offline_and_varya(self):
        for actor in ('varya', 'offline'):
            with self.subTest(actor=actor):
                sid = self.student()
                code, quote = self.call(actor, 'GET', f'/api/students/{sid}/single-quote?group_id={self.gid}&month=2026-12&date=2026-12-06')
                self.assertEqual(code, 200, quote)
                code, sold = self.call(actor, 'POST', f'/api/students/{sid}/single', {'group_id': self.gid, 'date': '2026-12-06'})
                self.assertEqual(code, 200, sold)
                self.assertEqual(self.con.execute('SELECT kind FROM subscriptions WHERE id=?', (sold['id'],)).fetchone()[0], 'single')
                sid = self.student(2700)
                body = {'months': ['2026-12', '2027-01'], 'group_id': self.gid,
                        'paid_date': '2026-11-20', 'use_balance': False}
                code, quote = self.call(actor, 'POST', f'/api/students/{sid}/multi-month-preview', body)
                self.assertEqual(code, 200, quote)
                self.assertEqual(quote['credit'], 0)
                code, sold = self.call(actor, 'POST', f'/api/students/{sid}/multi-month-sale',
                    dict(body, expected_token=quote['token'], request_id='sale-multi-'+actor, mode='invoice'))
                self.assertEqual(code, 200, sold)
                self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscriptions WHERE student_id=?', (sid,)).fetchone()[0], 2)
                self.assertEqual(money.balance(self.con, sid)['deposit'], 2700)
        self.provider.assert_not_called()

    def test_presale_fields_and_local_paylink_flow_are_allowed_without_provider_requests(self):
        sid = self.student()
        code, required = self.call('offline', 'GET', f'/api/students/{sid}/required?for=sale')
        self.assertEqual(code, 200, required)
        code, filled = self.call('offline', 'POST', f'/api/students/{sid}/required',
            {'child_name': 'Synthetic Updated Child', 'birth': '2020-01-01',
             'parent_name': 'Synthetic Parent', 'phone': '+79000001199', 'email': 'changed@example.invalid'})
        self.assertEqual(code, 200, filled)
        self.assertEqual(self.call('offline', 'POST', f'/api/students/{sid}/required', {'stage': 'left'})[0], 403)
        subid, quote = self.invoice('offline', sid)
        with patch.object(paylinks, 'ready', return_value=True):
            self.assertEqual(self.call('offline', 'GET', f'/api/students/{sid}/paylinks')[0], 200)
            code, link = self.call('offline', 'POST', f'/api/students/{sid}/paylinks',
                {'subscription_id': subid, 'amount': quote['total'], 'full_payment': True})
            self.assertEqual(code, 200, link)
            before = self.con.serialize()
            code, checked = self.call('offline', 'GET', f"/api/paylinks/{link['id']}/validate?student_id={sid}&subscription_id={subid}&require_full=1")
            self.assertEqual(code, 200, checked)
            self.assertTrue(checked['valid'], checked)
            self.assertEqual(self.con.serialize(), before)
            self.assertEqual(self.call('offline', 'POST', f"/api/paylinks/{link['id']}/cancel", {})[0], 200)
        with patch.object(paylinks, 'ready', return_value=False):
            code, error = self.call('offline', 'POST', f'/api/students/{sid}/paylinks',
                {'subscription_id': subid, 'amount': quote['total'], 'full_payment': True})
            self.assertEqual(code, 400, error)
        self.provider.assert_not_called()

    def test_other_permissions_remain_denied_and_denied_calls_do_not_write(self):
        sid = self.student()
        subid, _ = self.invoice('owner', sid)
        before = self.con.serialize()
        forbidden = [('GET', '/api/payments', None), ('GET', '/api/export/all', None),
            ('GET', '/api/backup', None), ('GET', '/api/sber/settings', None),
            ('PUT', '/api/settings', {'price_single': '1'}), ('GET', '/api/integrations', None),
            ('GET', '/api/erp/financial-audit?erp_id=synthetic-no-external', None),
            ('POST', f'/api/students/{sid}/refund', {'amount': 1, 'comment': 'Forbidden'}),
            ('POST', '/api/payments/1/cancel', {}), ('DELETE', f'/api/students/{sid}/error', {}),
            ('POST', f'/api/subscriptions/{subid}/cancel', {}),
            ('PATCH', f'/api/students/{sid}', {'name': 'Forbidden'}),
            ('PATCH', f'/api/students/{sid}/profile', {'name': 'Forbidden'}),
            ('POST', '/api/students', {'name': 'Forbidden'})]
        for method, path, body in forbidden:
            with self.subTest(method=method, path=path):
                self.assertEqual(self.call('offline', method, path, body)[0], 403)
        for actor, status in ((None, 401), ('coach', 403), ('lawyer', 403), ('trainee', 403)):
            for method, path, body in [('GET', f'/api/students/{sid}/quote?month=2026-12&group_id={self.gid}', None),
                ('POST', f'/api/students/{sid}/invoice', {'month': '2026-12', 'group_id': self.gid}),
                ('POST', f'/api/students/{sid}/multi-month-preview', {'months': ['2026-12', '2027-01'], 'group_id': self.gid}),
                ('GET', f'/api/students/{sid}/single-quote?group_id={self.gid}&month=2026-12', None),
                ('POST', f'/api/subscriptions/{subid}/balance-apply', {'use_balance': True})]:
                with self.subTest(actor=actor, path=path):
                    self.assertEqual(self.call(actor, method, path, body)[0], status)
        self.assertEqual(self.con.serialize(), before)


if __name__ == '__main__':
    unittest.main()
