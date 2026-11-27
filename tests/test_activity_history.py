from datetime import datetime
import unittest

from crm import activity
from tests import test_historical_refund_http


class ActivityHistoryTest(unittest.TestCase):
    def test_history_is_private_and_copies_only_record_metadata(self):
        fixture = test_historical_refund_http.HistoricalRefundHttpTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        con = fixture.con
        owner = con.execute("SELECT user_id FROM sessions WHERE token='synthetic-historical-owner'").fetchone()[0]
        actor = con.execute("SELECT user_id FROM sessions WHERE token='synthetic-historical-operator'").fetchone()[0]
        con.execute("UPDATE users SET name='Юлия Репина' WHERE id=?", (owner,))
        activity.init(con)
        outsider = con.execute("""INSERT INTO users(login,name,role,pw_hash)
            VALUES('other-owner','Other owner','admin','unused')""").lastrowid
        con.execute("INSERT INTO sessions VALUES(?,?,?)",
                    ('synthetic-historical-other', outsider, datetime.now().isoformat()))
        con.commit()

        endpoint = '/api/activity'
        for user, status in ((None, 401), ('operator', 403), ('other', 403), ('owner', 200)):
            self.assertEqual(fixture.call('GET', endpoint, actor=user)[0], status)

        for user, status in ((None, 401), ('operator', 403), ('other', 403), ('owner', 200)):
            self.assertEqual(fixture.call('GET', '/api/cash-receipts?month=2026-11', actor=user)[0], status)

        student = fixture.fixture.sid
        result = fixture.call('POST', endpoint, {'kind': 'copy', 'section': 'sales', 'tab': 'abon',
            'student_id': student, 'copy_type': 'payment-link', 'user_id': owner, 'text': 'CLIPBOARD PRIVATE'},
            actor='operator')
        self.assertEqual(result, (200, {'ok': True}))
        _, response = fixture.call('GET', endpoint)
        self.assertEqual(len(response['rows']), 1)
        row = response['rows'][0]
        self.assertEqual((row['user_id'], row['student_id'], row['copy_type']), (actor, student, 'payment-link'))
        self.assertNotIn('CLIPBOARD PRIVATE', str(response))
        self.assertEqual(fixture.call('GET', endpoint + '?user_id=' + str(owner))[1]['rows'], [])
