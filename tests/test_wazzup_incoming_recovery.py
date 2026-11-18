import io,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit
from crm import db,comms,server,wazzupqueue


class IncomingRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.con=db.connect(str(Path(self.tmp.name)/'incoming.sqlite3'))
        self.addCleanup(self.con.close);db.init_db(self.con)
        comms.put(self.con,'hook_token','synthetic-hook-token')
        self.payload={'messages':[{'chatType':'max','chatId':'synthetic-max-chat','channelId':'synthetic-max-channel',
            'messageId':'synthetic-incoming-id','dateTime':'2026-11-18T09:55:00Z','type':'text',
            'text':'Synthetic incoming message','contact':{'name':'Synthetic parent','phone':'79000007942'}}]}

    def hook(self,drain=True):
        raw=json.dumps(self.payload).encode();responses=[]
        h=SimpleNamespace(headers={'Content-Type':'application/json','Content-Length':str(len(raw))},
            rfile=io.BytesIO(raw),_send=lambda status,body:responses.append((status,body)))
        with patch.object(db,'get_conn',return_value=self.con):
            server.handle_hook(h,'POST','wazzup','synthetic-hook-token',urlsplit('/hooks/wazzup/synthetic-hook-token'))
        if drain and responses[-1][0]==200:wazzupqueue.process_pending(self.con,force=True)
        return responses[-1]

    def test_failed_processing_is_durably_accepted_and_retry_is_idempotent(self):
        original=comms.wz_hook
        def fail_after_write(con,payload):
            original(con,payload)
            raise comms.CommsError('Synthetic ingest failure')
        with patch.object(comms,'wz_hook',side_effect=fail_after_write):
            status,body=self.hook()
        self.assertEqual(status,200);self.assertTrue(body['queued'])
        self.assertEqual(self.con.execute('SELECT count(*) FROM messages').fetchone()[0],0)
        self.assertEqual(wazzupqueue.stats(self.con)['pending'],1)
        self.assertEqual(self.hook()[0],200);self.assertEqual(self.hook()[0],200)
        self.assertEqual(self.con.execute('SELECT count(*) FROM messages').fetchone()[0],1)
        self.assertEqual(self.con.execute('SELECT unread FROM conversations').fetchone()[0],1)
        self.assertEqual(len(comms.inbox(self.con,q='7942')['conversations']),1)
        self.assertEqual(len(comms.inbox(self.con,q='Synthetic parent')['conversations']),1)

    def test_replay_saved_event_no_outgoing_api_and_no_duplicate(self):
        comms.log_hook(self.con,'wazzup',self.payload,'Synthetic previous ingest failure')
        hid=self.con.execute('SELECT max(id) FROM hook_log').fetchone()[0]
        with patch.object(comms,'wz_call',side_effect=AssertionError('Must not send to provider')):
            self.assertEqual(comms.replay_wazzup_hook(self.con,hid)['added'],1)
            self.assertEqual(comms.replay_wazzup_hook(self.con,hid)['added'],0)
        self.assertEqual(self.con.execute('SELECT unread FROM conversations').fetchone()[0],1)
        comms.log_hook(self.con,'wazzup',{'messages':['x'*10000]})
        with self.assertRaises(comms.CommsError):
            comms.replay_wazzup_hook(self.con,self.con.execute('SELECT max(id) FROM hook_log').fetchone()[0])

    def test_diagnostics_show_incoming_error_without_text_or_contact(self):
        comms.log_hook(self.con,'wazzup',self.payload,'ValueError: Synthetic incoming message')
        report=comms.wazzup_diagnostics(self.con)
        event=report['incoming_events'][0]
        self.assertTrue(event['failed']);self.assertEqual(event['error_type'],'ValueError')
        self.assertEqual(event['chat_types'],['max'])
        self.assertNotIn('Synthetic incoming message',str(report))
        self.assertNotIn('79000007942',str(report))

    def test_exact_max_chat_id_search_does_not_turn_it_into_phone(self):
        self.payload['messages'][0]['chatId']='19000007942'
        self.payload['messages'][0]['contact']={'name':'Synthetic parent'}
        self.hook()
        row=self.con.execute('SELECT * FROM conversations').fetchone()
        self.assertEqual(row['phone_norm'],'')
        self.assertEqual(len(comms.inbox(self.con,q='19000007942')['conversations']),1)
        self.assertEqual(len(comms.inbox(self.con,q='7942')['conversations']),0)
