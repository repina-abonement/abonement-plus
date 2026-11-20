"""Actual HTTP failures on a temporary CRM, with no working data/providers."""
from collections import deque
import json
import unittest
from unittest.mock import patch

from crm import carderrors, fiscal, server
from tests import test_historical_refund_http as fixture


class CardErrorHttpTest(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture.HistoricalRefundHttpTest();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.con=self.fixture.con;self.sid=self.fixture.fixture.sid
        self.path='/api/students/'+str(self.sid)
        self.diag='/api/diagnostics/student-card?student_id='+str(self.sid)
        for name,value in (('_errors',deque()),('_bytes',0)):
            self.enterContext(patch.object(carderrors,name,value))
        self.enterContext(patch.object(server.traceback,'print_exc'))
        for name in ('pk_call','ofd_get','run'):
            self.enterContext(patch.object(fiscal,name,side_effect=AssertionError('Provider requests forbidden')))

    def finance(self):
        tables=('payments','subscriptions','erp_payment_allocations','erp_refund_allocations',
            'invoice_funding_policies','invoice_payment_targets','invoice_balance_uses',
            'balance_account_snapshots','balance_credit_funds','pay_links','pay_link_subscriptions','attendance')
        return {name:[tuple(row) for row in self.con.execute('SELECT * FROM '+name+' ORDER BY 1')]
            for name in tables}

    def test_actual_failed_get_is_private_and_its_transaction_is_rolled_back(self):
        before=self.con.serialize()
        def broken(con,sid):
            # Simulate a defective read path. Nothing can persist after 500.
            con.execute('UPDATE subscriptions SET price=price+1 WHERE student_id=?',(sid,))
            raise KeyError('covered')
        with patch.object(server.sv,'student_detail',side_effect=broken):
            status,body=self.fixture.call('GET',self.path+'?token=DO-NOT-CAPTURE')
        self.assertEqual(status,500)
        self.assertEqual(body['error'],'Ошибка сервера')
        self.assertRegex(body['error_id'],r'^[a-f0-9]{12}$')
        self.assertNotIn('covered',json.dumps(body))
        self.assertEqual(self.con.serialize(),before)
        status,reply=self.fixture.call('GET',self.diag+'&error_id='+body['error_id'])
        self.assertEqual(status,200,reply);self.assertTrue(reply['available'])
        self.assertTrue(reply['read_only']);self.assertFalse(reply['live_provider_requests'])
        event=reply['errors'][0]
        self.assertEqual(event['type'],'KeyError')
        self.assertEqual(event['message'],'Missing field: covered')
        self.assertTrue(any(row['file']=='crm/server.py' and row['function']=='student_get' for row in event['frames']))
        self.assertNotIn('DO-NOT-CAPTURE',json.dumps(reply))
        self.assertEqual(self.con.serialize(),before)

    def test_diagnostic_requires_owner_and_does_not_cross_student_ids(self):
        with patch.object(server.sv,'student_detail',side_effect=IndexError('No item with that key')):
            self.assertEqual(self.fixture.call('GET',self.path)[0],500)
        before=self.con.serialize()
        for actor,status in ((None,401),('operator',403)):
            self.assertEqual(self.fixture.call('GET',self.diag,actor=actor)[0],status)
        code,reply=self.fixture.call('GET','/api/diagnostics/student-card?student_id='+str(self.sid+100))
        self.assertEqual(code,200);self.assertFalse(reply['available'])
        self.assertEqual(self.con.serialize(),before)

    def test_successful_card_get_and_diagnostic_do_not_change_financial_rows(self):
        before=self.finance()
        code,card=self.fixture.call('GET',self.path)
        self.assertEqual(code,200,card)
        self.assertEqual(self.finance(),before)
        code,reply=self.fixture.call('GET',self.diag)
        self.assertEqual(code,200);self.assertFalse(reply['available'])
        self.assertTrue(reply['resets_on_restart']);self.assertEqual(self.finance(),before)

    def test_capture_failure_does_not_hide_original_http_error_or_write_data(self):
        before=self.con.serialize()
        with patch.object(server.sv,'student_detail',side_effect=KeyError('month')), \
                patch.object(carderrors,'record',side_effect=RuntimeError('Capture failure')):
            code,body=self.fixture.call('GET',self.path)
        self.assertEqual(code,500);self.assertEqual(body,{'error':'Ошибка сервера'})
        self.assertFalse(carderrors.latest(self.sid)['available'])
        self.assertEqual(self.con.serialize(),before)

    def test_nonget_failures_and_bad_request_errors_do_not_enter_card_ring(self):
        before=self.con.serialize()
        with patch.object(server.sv,'update_student',side_effect=KeyError('month')):
            self.assertEqual(self.fixture.call('PATCH',self.path,{'name':'Synthetic unchanged'})[0],500)
        with patch.object(server.sv,'student_detail',side_effect=ValueError('Private value hidden')):
            self.assertEqual(self.fixture.call('GET',self.path)[0],400)
        self.assertFalse(carderrors.latest(self.sid)['available'])
        self.assertEqual(self.con.serialize(),before)

    def test_invalid_diagnostic_queries_are_rejected_without_writes(self):
        before=self.con.serialize()
        for query in ('','student_id=0','student_id=-1','student_id=no',
                'student_id=1&error_id=bad'):
            self.assertEqual(self.fixture.call('GET','/api/diagnostics/student-card?'+query)[0],400)
        self.assertEqual(self.con.serialize(),before)


if __name__=='__main__':unittest.main()
