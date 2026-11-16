"""Explicit upload/send, scoped drafts, expiring fetch tickets and channel routing."""
import base64,tempfile,unittest,itertools,json
from pathlib import Path
from unittest.mock import patch
from datetime import datetime,timedelta,timezone
from crm import db,comms,chatattachments as files,server


class AttachmentTest(unittest.TestCase):
    def setUp(self):
        self.con=db.connect(str(Path(tempfile.mkdtemp())/'attachments.sqlite3'));db.init_db(self.con)
        self.user=dict(self.con.execute("SELECT * FROM users WHERE role='admin'").fetchone())
        comms.put(self.con,'public_url','https://crm.example.invalid')
        self.cid=self.chat('telegram','12345')
        self.encoded=base64.b64encode(b'%PDF-1.4\nSynthetic').decode()
    def chat(self,channel,external,meta=None):
        return self.con.execute("INSERT INTO conversations(channel,external_id,created_at,meta) VALUES (?,?,'2026-11-16',?)",(channel,external,json.dumps(meta or {}))).lastrowid
    def upload(self,cid=None):return files.upload(self.con,cid or self.cid,self.user,'../folder/test.pdf',self.encoded)
    def test_upload_does_not_send_and_send_is_idempotent(self):
        with patch.object(comms,'tg_call',return_value={'message_id':700}) as send:
            item=self.upload();send.assert_not_called()
            self.assertEqual(self.con.execute('SELECT count(*) FROM messages').fetchone()[0],0)
            self.con.commit()
            result=files.send(self.con,self.cid,item['id'],self.user,'Подпись')
            again=files.send(self.con,self.cid,item['id'],self.user,'Подпись')
        self.assertTrue(result['ok']);self.assertTrue(again['already_sent']);self.assertEqual(send.call_count,1)
        self.assertEqual(send.call_args.args[1],'sendDocument');payload=send.call_args.args[2]
        self.assertEqual(payload['caption'],'Подпись');self.assertTrue(payload['document'].endswith('/test.pdf'))
        row=self.con.execute('SELECT * FROM chat_attachments WHERE id=?',(item['id'],)).fetchone()
        self.assertEqual(files.public_file(self.con,row['ticket'])[0],b'%PDF-1.4\nSynthetic')
        self.con.execute('UPDATE chat_attachments SET expires_at=? WHERE id=?',((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),item['id']))
        with self.assertRaises(comms.CommsError):files.public_file(self.con,row['ticket'])
        self.assertEqual(comms.media_for(self.con,result['id'])[0],b'%PDF-1.4\nSynthetic')
    def test_wrong_chat_or_actor_cannot_send_draft(self):
        item=self.upload();other=self.chat('telegram','54321')
        with patch.object(comms,'tg_call') as send:
            with self.assertRaises(comms.CommsError):files.send(self.con,other,item['id'],self.user)
            with self.assertRaises(comms.CommsError):files.send(self.con,self.cid,item['id'],{'id':999,'name':'Other'})
        send.assert_not_called()
    def test_failure_is_saved_and_not_automatically_retried(self):
        item=self.upload()
        with patch.object(comms,'tg_call',side_effect=comms.CommsError('Сетевой отказ')) as send:
            result=files.send(self.con,self.cid,item['id'],self.user)
            self.assertFalse(result['ok']);self.assertTrue(result['uncertain'])
            with self.assertRaises(comms.CommsError):files.send(self.con,self.cid,item['id'],self.user)
        self.assertEqual(send.call_count,1)
        self.assertEqual(self.con.execute('SELECT status FROM messages WHERE id=?',(result['id'],)).fetchone()[0],'error')
    def test_wazzup_transport_uses_same_selected_channel(self):
        cid=self.chat('wazzup_telegram','phone:79000000111',{'channelId':'test-tg','chatType':'telegram'})
        item=self.upload(cid)
        with patch.object(comms,'wz_call',return_value={'messageId':'test-file'}) as request:
            result=files.send(self.con,cid,item['id'],self.user)
        self.assertTrue(result['ok']);payload=request.call_args.args[3]
        self.assertEqual(payload['channelId'],'test-tg');self.assertEqual(payload['chatType'],'telegram');self.assertEqual(payload['phone'],'79000000111');self.assertIn('contentUri',payload)
    def test_validation_and_photo_business_connection(self):
        for name,data in [('test.html',b'<html>'),('test.png',b'not image'),('test.pdf',b'not pdf')]:
            with self.assertRaises(comms.CommsError):files.upload(self.con,self.cid,self.user,name,base64.b64encode(data).decode())
        with self.assertRaises(comms.CommsError):files.upload(self.con,self.cid,self.user,'test.pdf',base64.b64encode(b'x'*(files.MAX_BYTES+1)).decode())
        cid=self.chat('telegram','99999',{'biz':'business-test'})
        item=files.upload(self.con,cid,self.user,'photo.png',base64.b64encode(b'\x89PNG\r\n\x1a\nTEST').decode())
        with patch.object(comms,'tg_call',return_value={'message_id':800}) as request:files.send(self.con,cid,item['id'],self.user)
        self.assertEqual(request.call_args.args[1],'sendPhoto');self.assertEqual(request.call_args.args[2]['business_connection_id'],'business-test')
    def test_opaque_attachment_routes_match_real_ids_and_tickets(self):
        item=self.upload();r=self.con.execute('SELECT * FROM chat_attachments WHERE id=?',(item['id'],)).fetchone()
        paths=[('POST','/api/alerts/123/close','operator'),('DELETE','/api/absences/123','schedule:full'),('POST',f"/api/conversations/{self.cid}/attachments/{item['id']}/send",'operator'),('GET',f"/api/chat-files/{r['ticket']}/test.pdf",'public'),('HEAD',f"/api/chat-files/{r['ticket']}/test.pdf",'public')]
        for method,path,role in paths:
            matches=[(rx,fn,permission) for m,rx,fn,permission in server.ROUTES if m==method and rx.fullmatch(path)]
            self.assertEqual(len(matches),1,path);self.assertEqual(matches[0][2],role)
    def test_pin_route_uses_real_current_user(self):
        comms.pin_conversation(self.con,self.cid,self.user,True)
        result=server.inbox(self.con,None,self.user,{}, {})
        self.assertTrue(result['conversations'][0]['pinned'])
    def test_wazzup_diagnostics_read_only_and_no_account_phone(self):
        comms.put(self.con,'wazzup_key','TEST_API_KEY');comms.put(self.con,'wazzup_tg_channel','tg-selected')
        before=self.con.total_changes
        with patch.object(comms,'wz_call',return_value=[{'channelId':'tg-selected','transport':'telegram','state':'active','plainId':'79000000111'}]) as request:
            report=comms.wazzup_diagnostics(self.con)
        self.assertEqual(self.con.total_changes,before);self.assertEqual(request.call_args.args[1:3],('GET','channels'))
        self.assertTrue(report['channels'][0]['selected']);self.assertNotIn('79000000111',json.dumps(report));self.assertNotIn('TEST_API_KEY',json.dumps(report))

if __name__=='__main__':unittest.main()
