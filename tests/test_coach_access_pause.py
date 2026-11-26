from datetime import datetime
import unittest

from crm import db, pin
from tests import test_historical_refund_http


class CoachAccessPauseTest(unittest.TestCase):
    def test_password_pin_and_existing_session_are_blocked(self):
        fixture = test_historical_refund_http.HistoricalRefundHttpTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        con = fixture.con
        password = 'synthetic-coach-password'
        uid = con.execute("""INSERT INTO users(login,name,role,pw_hash,is_promoter)
            VALUES('synthetic-paused-coach','Synthetic coach','coach',?,1)""",
            (db.hash_password(password),)).lastrowid
        con.execute('INSERT INTO sessions(token,user_id,created_at) VALUES(?,?,?)',
                    ('synthetic-historical-coach', uid, datetime.now().isoformat()))
        device = 'f' * 64
        pin.set_pin(con, {'id': uid}, device, '2580')
        con.commit()

        status, body = fixture.call('POST', '/api/login',
            {'login': 'synthetic-paused-coach', 'password': password}, actor=None)
        self.assertEqual(status, 403)
        self.assertIn('временно закрыт', body['error'])
        self.assertEqual(fixture.call('GET', '/api/pin/hello?device=' + device, actor=None), (200, {}))
        _, reply = fixture.call('POST', '/api/pin/login', {'device': device, 'pin': '2580'}, actor=None)
        self.assertFalse(reply['ok'])
        self.assertIn('временно закрыт', reply['error'])
        for path in ('/api/meta', '/api/me', '/api/students', '/api/planner', '/api/coaches/board'):
            self.assertEqual(fixture.call('GET', path, actor='coach')[0], 401, path)
        self.assertEqual(fixture.call('GET', '/api/me', actor='operator')[0], 200)
        self.assertEqual(con.execute('SELECT COUNT(*) FROM sessions WHERE user_id=?', (uid,)).fetchone()[0], 1)
