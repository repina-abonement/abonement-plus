"""Offline administrators inherit chat access without integration administration."""
import json,tempfile,threading,unittest,urllib.request,urllib.error
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from crm import db,server,comms,perms

class OfflineChatAccess(unittest.TestCase):
    def test_actual_http_sessions_can_read_and_send_but_cannot_configure_integrations(self):
        old_path=db.DB_PATH;self.addCleanup(setattr,db,'DB_PATH',old_path);db.DB_PATH=str(Path(tempfile.mkdtemp())/'offline-chat.sqlite3')
        con=db.connect();db.init_db(con)
        users=[]
        for number in (1,2):
            uid=con.execute("INSERT INTO users(login,name,role,pw_hash) VALUES (?,?,'operator','disabled')",(f'offline-{number}',f'Synthetic Offline {number}')).lastrowid
            server.set_post(con,uid,'offline_admin')
            token=f'synthetic-offline-{number}'
            con.execute('INSERT INTO sessions(token,user_id,created_at) VALUES (?,?,?)',(token,uid,datetime.now().isoformat()))
            users.append((token,dict(con.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())))
        uid=con.execute("INSERT INTO users(login,name,role,pw_hash) VALUES ('synthetic-coach','Synthetic Coach','coach','disabled')").lastrowid
        con.execute('INSERT INTO sessions(token,user_id,created_at) VALUES (?,?,?)',('synthetic-coach',uid,datetime.now().isoformat()))
        comms.put(con,'wazzup_key','FAKE_KEY');comms.put(con,'wazzup_tg_channel','synthetic-tg')
        fid=con.execute("INSERT INTO families(name) VALUES ('Synthetic Family')").lastrowid
        cid=comms.upsert_conversation(con,'wazzup_telegram','phone:79000000111',phone='79000000111',meta={'channelId':'synthetic-tg','chatType':'telegram'})
        comms.link(con,cid,fid);con.close()
        srv=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start()
        def call(token,method,path,data=None):
            req=urllib.request.Request(f'http://127.0.0.1:{srv.server_port}'+path,method=method,data=json.dumps(data).encode() if data is not None else None,headers={'Cookie':'crm_session='+token,'Content-Type':'application/json'})
            try:
                with urllib.request.urlopen(req,timeout=5) as r:return r.status,json.load(r)
            except urllib.error.HTTPError as e:return e.code,json.load(e)
        try:
            with patch.object(comms,'wz_call',side_effect=[{'messageId':'synthetic-1'},{'messageId':'synthetic-2'}]) as provider:
                for token,user in users:
                    self.assertTrue(user['is_offline_admin']);self.assertEqual(perms.title(user),'Офлайн-администратор')
                    code,meta=call(token,'GET','/api/meta');self.assertEqual(code,200);self.assertEqual(meta['user']['perms']['inbox'],'full')
                    for endpoint in ['/api/inbox',f'/api/conversations/{cid}',f'/api/families/{fid}/comms']:
                        self.assertEqual(call(token,'GET',endpoint)[0],200)
                    self.assertEqual(call(token,'POST',f'/api/conversations/{cid}/send',{'text':'Synthetic access test'})[0],200)
                    self.assertEqual(call(token,'POST',f'/api/conversations/{cid}/unread',{})[0],200)
                    self.assertEqual(call(token,'GET',f'/api/conversations/{cid}?read=0')[1]['conversation']['unread'],1)
                    self.assertEqual(call(token,'GET',f'/api/families/{fid}/comms?read=0')[0],200)
                    self.assertEqual(call(token,'GET','/api/inbox/counts')[1]['unread'],1)
                    self.assertEqual(call(token,'POST',f'/api/conversations/{cid}/read',{})[0],200)
                    self.assertEqual(call(token,'GET','/api/inbox/counts')[1]['unread'],0)
                    self.assertEqual(call(token,'POST',f'/api/conversations/{cid}/preferences',{'muted':True})[0],200)
                    self.assertTrue(call(token,'GET',f'/api/conversations/{cid}?read=0')[1]['conversation']['muted'])
                    self.assertEqual(call(token,'GET','/api/integrations')[0],403)
                    self.assertEqual(call(token,'PUT','/api/integrations',{'wazzup_key':'NO_CHANGE'})[0],403)
                self.assertEqual(call('synthetic-coach','GET','/api/inbox')[0],403)
                self.assertEqual(call('synthetic-coach','POST',f'/api/conversations/{cid}/send',{'text':'Must not send'})[0],403)
                for action in ['unread','read','preferences']:
                    self.assertEqual(call('synthetic-coach','POST',f'/api/conversations/{cid}/{action}',{'muted':True})[0],403)
                self.assertEqual(provider.call_count,2)
            with patch.object(comms,'wz_call',side_effect=comms.CommsError('Wazzup: POST_MESSAGE_ERROR · requestId=abc123456789')) as provider:
                for token,user in users:
                    code,response=call(token,'POST',f'/api/conversations/{cid}/send',{'text':'Synthetic failed delivery'})
                    self.assertEqual(code,400)
                    self.assertIn('requestId=abc123456789',response['error'])
                self.assertEqual(provider.call_count,2)
            reader=db.connect()
            failures=reader.execute("SELECT status,error FROM messages WHERE text='Synthetic failed delivery'").fetchall()
            self.assertEqual(len(failures),2)
            self.assertTrue(all(row['status']=='error' and 'abc123456789' in row['error'] for row in failures))
            with patch.object(comms,'wz_call',return_value=[]):
                diagnostic=comms.wazzup_diagnostics(reader)
            self.assertEqual(len(diagnostic['failures']),2)
            self.assertEqual(diagnostic['failures'][0]['codes'],['POST_MESSAGE_ERROR'])
            reader.close()
        finally:
            srv.shutdown();srv.server_close();thread.join(timeout=5);db.DB_PATH=old_path

if __name__=='__main__':unittest.main()
