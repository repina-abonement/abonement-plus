"""Actual raised exceptions only; no database, files or remote requests."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import json
import re
import sqlite3
import unittest
from unittest.mock import patch

from crm import carderrors


class CardErrorsTest(unittest.TestCase):
    def setUp(self):
        for name,value in (('_errors',deque()),('_bytes',0)):
            change=patch.object(carderrors,name,value);change.start();self.addCleanup(change.stop)

    def raised(self,error,path='/api/students/1719'):
        # A real traceback from executed Python with a CRM-relative filename.
        # No source file is created and no traceback is constructed or faked.
        scope={'fault':error}
        exec(compile("def actual_card_failure():\n    private_local = 'DO-NOT-RETAIN-LOCAL'\n    raise fault\n",
            str(carderrors._CRM/'synthetic_card_failure.py'),'exec'),scope)
        try:scope['actual_card_failure']()
        except BaseException as actual:return carderrors.record(path,actual)

    def test_actual_traceback_has_bounded_crm_frames_without_locals_or_source(self):
        key=self.raised(KeyError('month'))
        reply=carderrors.latest(1719,key)
        self.assertTrue(reply['available']);self.assertEqual(len(reply['errors']),1)
        entry=reply['errors'][0]
        self.assertEqual(entry['type'],'KeyError');self.assertEqual(entry['message'],'Missing field: month')
        self.assertEqual(entry['frames'],[{'file':'crm/synthetic_card_failure.py','line':3,'function':'actual_card_failure'}])
        data=json.dumps(reply)
        for forbidden in ('DO-NOT-RETAIN-LOCAL','private_local','raise fault',str(carderrors._CRM),'test_card_errors.py'):
            self.assertNotIn(forbidden,data)
        self.assertRegex(entry['captured_at'],r'\+03:00$')
        self.assertEqual(reply['source'],'actual_student_get_failure')

    def test_actual_raised_chain_retains_cause_type_and_crm_frames(self):
        scope={}
        exec(compile("def chained_card_failure():\n    try:\n        raise KeyError('covered')\n    except KeyError as cause:\n        raise RuntimeError('https://private.invalid/?token=HIDE-THIS') from cause\n",
            str(carderrors._CRM/'synthetic_card_failure.py'),'exec'),scope)
        try:scope['chained_card_failure']()
        except RuntimeError as actual:key=carderrors.record('/api/students/1719',actual)
        entry=carderrors.latest(1719,key)['errors'][0]
        self.assertEqual([item['type'] for item in entry['chain']],['RuntimeError','KeyError'])
        self.assertEqual(entry['chain'][1]['message'],'Missing field: covered')
        self.assertTrue(all(item['frames'] for item in entry['chain']))
        self.assertNotIn('HIDE-THIS',json.dumps(entry));self.assertNotIn('private.invalid',json.dumps(entry))

    def test_unraised_exceptions_and_noncard_or_query_paths_are_not_captured(self):
        self.assertIsNone(carderrors.record('/api/students/1719',KeyError('month')))
        for path in ('/api/fiscal/webhook','/api/sber/oauth/callback','/api/students',
            '/api/students/1719/payments','/api/students/1719?token=SECRET','/api/students/1719/',
            '/api/students/-1','/api/students/0','https://ab-plus.ru/api/students/1719'):
            with self.subTest(path=path):self.assertIsNone(self.raised(KeyError('month'),path))
        self.assertFalse(carderrors.latest(1719)['available']);self.assertEqual(carderrors._bytes,0)

    def test_sensitive_and_arbitrary_messages_never_echo_urls_tokens_or_values(self):
        for error in (RuntimeError('https://bank.invalid/token?password=HIDDEN token=HIDDEN'),
            ValueError("invalid literal for int(): 'Customer Name +79000000000 HIDDEN'"),
            KeyError('client_secret'),KeyError('session_token'),
            sqlite3.OperationalError('no such column: api_token'),
            sqlite3.OperationalError("near 'HIDDEN': unrecognized SQL with cookie=HIDDEN")):
            with self.subTest(error=type(error).__name__):
                key=self.raised(error);data=json.dumps(carderrors.latest(1719,key))
                for forbidden in ('HIDDEN','bank.invalid','Customer Name','79000000000','client_secret','session_token','api_token'):
                    self.assertNotIn(forbidden,data)

    def test_sqlite_schema_and_constraint_identifiers_remain_useful(self):
        for message in ('no such column: subscriptions.month','no such table: invoice_balance_uses',
            'NOT NULL constraint failed: payments.method','UNIQUE constraint failed: payments.id, payments.batch',
            'table subscriptions has no column named enrollment_id','database is locked'):
            with self.subTest(message=message):
                key=self.raised(sqlite3.OperationalError(message))
                self.assertEqual(carderrors.latest(1719,key)['errors'][0]['message'],message)

    def test_builtin_index_attribute_and_type_messages_are_safely_retained(self):
        examples=((IndexError('No item with that key'),'No item with that key'),
            (AttributeError("'Row' object has no attribute 'get'"),'Row has no attribute get'),
            (TypeError("'NoneType' object is not subscriptable"),"'NoneType' object is not subscriptable"))
        for error,expected in examples:
            key=self.raised(error)
            self.assertEqual(carderrors.latest(1719,key)['errors'][0]['message'],expected)

    def test_ring_eviction_is_bounded_and_latest_is_newest_first(self):
        ids=[self.raised(KeyError('field_'+str(number))) for number in range(35)]
        result=carderrors.latest(1719)
        self.assertEqual(len(result['errors']),carderrors.MAX_ERRORS)
        self.assertEqual(result['errors'][0]['error_id'],ids[-1])
        self.assertEqual(result['errors'][-1]['error_id'],ids[-carderrors.MAX_ERRORS])
        self.assertFalse(carderrors.latest(1719,ids[0])['available'])
        self.assertLessEqual(carderrors._bytes,carderrors.MAX_BYTES)

    def test_byte_cap_and_record_cap_hold_with_large_actual_tracebacks(self):
        with patch.object(carderrors,'MAX_BYTES',2000):
            for number in range(30):self.raised(sqlite3.OperationalError('no such column: '+('a'*90)+str(number)))
            self.assertLessEqual(carderrors._bytes,2000)
            for entry,size in carderrors._errors:self.assertLessEqual(size,carderrors.MAX_RECORD_BYTES)

    def test_reader_does_not_cross_cards_or_echo_invalid_error_ids(self):
        key=self.raised(KeyError('price'))
        self.assertFalse(carderrors.latest(1720,key)['available'])
        self.assertFalse(carderrors.latest('1719 OR 1=1',key)['available'])
        invalid=carderrors.latest(1719,'https://private.invalid/?token=HIDDEN')
        self.assertFalse(invalid['available']);self.assertNotIn('HIDDEN',json.dumps(invalid))
        self.assertTrue(carderrors.latest('1719',key)['available'])

    def test_reader_returns_a_copy_and_empty_state_explicitly_explains_restart(self):
        empty=carderrors.latest(1719)
        self.assertFalse(empty['available']);self.assertTrue(empty['resets_on_restart'])
        self.assertEqual(empty['storage'],'process_memory');self.assertIn('перезапуска',empty['note'])
        key=self.raised(KeyError('price'))
        response=carderrors.latest(1719,key);response['errors'][0]['frames'].clear()
        response['errors'][0]['message']='changed by reader'
        original=carderrors.latest(1719,key)['errors'][0]
        self.assertTrue(original['frames']);self.assertEqual(original['message'],'Missing field: price')

    def test_concurrent_capture_and_reads_keep_unique_ids_and_retention_bounds(self):
        def task(number):
            key=self.raised(KeyError('field_'+str(number)))
            carderrors.latest(1719,key)
            return key
        with ThreadPoolExecutor(max_workers=12) as pool:ids=list(pool.map(task,range(120)))
        self.assertEqual(len(set(ids)),120)
        self.assertEqual(len(carderrors.latest(1719)['errors']),carderrors.MAX_ERRORS)
        self.assertLessEqual(carderrors._bytes,carderrors.MAX_BYTES)
        self.assertEqual(carderrors._bytes,sum(size for _,size in carderrors._errors))
        self.assertTrue(all(re.fullmatch('[a-f0-9]{12}',key) for key in ids))


if __name__=='__main__':unittest.main()
