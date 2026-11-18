import tempfile,unittest
from pathlib import Path
from crm import db,comms

class WazzupDeliveryStatus(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.con=db.connect(str(Path(self.tmp.name)/'delivery.sqlite3'));self.addCleanup(self.con.close);db.init_db(self.con)
        self.cid=comms.upsert_conversation(self.con,'whatsapp','79000000101',meta={'channelId':'synthetic-channel','chatType':'whatsapp'})

    def status(self,value,message='synthetic-message',channel='synthetic-channel'):
        comms.wz_hook(self.con,{'statuses':[{'messageId':message,'channelId':channel,'status':value}]})

    def current(self):return self.con.execute("SELECT status FROM messages WHERE external_id='synthetic-message'").fetchone()[0]

    def test_delivery_read_and_no_regression_no_new_message(self):
        comms.add_message(self.con,self.cid,'out','Synthetic',status='sent',external_id='synthetic-message')
        self.status('delivered');self.assertEqual(self.current(),'delivered')
        self.status('read');self.assertEqual(self.current(),'read')
        self.status('sent');self.status('error');self.status('delivered');self.assertEqual(self.current(),'read')
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM messages').fetchone()[0],1)
        self.assertEqual(self.con.execute('SELECT unread FROM conversations WHERE id=?',(self.cid,)).fetchone()[0],0)

    def test_status_before_message_is_applied_and_wrong_channel_ignored(self):
        self.status('delivered')
        comms.add_message(self.con,self.cid,'out','Synthetic',status='sent',external_id='synthetic-message')
        self.assertEqual(self.current(),'delivered')
        self.status('read',channel='other-channel');self.assertEqual(self.current(),'delivered')

    def test_unknown_and_incoming_not_marked_and_errors_not_masked(self):
        comms.add_message(self.con,self.cid,'out','Synthetic',status='sent',external_id='synthetic-message')
        self.status('unexpected');self.assertEqual(self.current(),'sent')
        self.status('error');self.status('sent');self.assertEqual(self.current(),'error')
        self.status('delivered');self.assertEqual(self.current(),'delivered')
        comms.add_message(self.con,self.cid,'in','Synthetic incoming',external_id='incoming-message')
        self.status('read',message='incoming-message')
        self.assertEqual(self.con.execute("SELECT status FROM messages WHERE external_id='incoming-message'").fetchone()[0],'')
