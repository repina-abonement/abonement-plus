"""MAX selection, webhooks and messages never fall back to WhatsApp."""
import json
import os
import tempfile
import threading
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from unittest.mock import patch
os.environ['CRM_DB']=os.path.join(tempfile.mkdtemp(),'max.sqlite3')
os.environ['CRM_ADMIN_PASSWORD']='admin-pass-123';os.environ['CRM_QUIET']='1'
from crm import db,comms,server,services as sv

def main():
    con=db.connect();db.init_db(con)
    admin=dict(con.execute("SELECT * FROM users WHERE login='admin'").fetchone())
    for key,value in {'wazzup_key':'fake-test-key','wazzup_channel':'wa-test','wazzup_tg_channel':'tg-test','wazzup_max_channel':'mx-test'}.items():comms.put(con,key,value)
    comms.put(con,'wazzup_channels',json.dumps([{'id':'wa-test','transport':'whatsapp'},{'id':'mx-test','transport':'maxapi'}]))
    sid=sv.create_student(con,{'name':'Ребёнок для теста','parent_name':'Родитель','phone':'+79060727385'},admin)
    family=con.execute('SELECT family_id FROM students WHERE id=?',(sid,)).fetchone()[0]
    parent=con.execute('SELECT id FROM parents WHERE family_id=?',(family,)).fetchone()[0]
    cid=comms.start_wazzup_max(con,'+7 (906) 072-73-85')
    assert con.execute('SELECT external_id FROM conversations WHERE id=?',(cid,)).fetchone()[0]=='phone:79060727385'
    with patch.object(comms,'wz_call',return_value={'messageId':'test-max-1'}) as send:
        comms.send(con,cid,'MAX only',admin)
        body=send.call_args.args[3]
        assert body['channelId']=='mx-test' and body['chatType']=='max' and body['phone']=='79060727385' and 'chatId' not in body
    comms.wz_hook(con,{'messages':[{'chatType':'maxapi','channelId':'mx-test','chatId':'max-real-id','messageId':'test-max-1','isEcho':True,'text':'MAX only','contact':{'phone':'79060727385'}}]})
    assert con.execute("SELECT count(*) FROM conversations WHERE channel='wazzup_max'").fetchone()[0]==1
    assert con.execute('SELECT external_id FROM conversations WHERE id=?',(cid,)).fetchone()[0]=='max-real-id'
    comms.wz_hook(con,{'messages':[{'chatType':'max','channelId':'mx-test','chatId':'max-real-id','messageId':'in-max-1','text':'Reply','contact':{'phone':'79060727385'}}]})
    assert con.execute('SELECT family_id FROM conversations WHERE id=?',(cid,)).fetchone()[0]==family
    with patch.object(comms,'wz_call',return_value={'messageId':'test-max-2'}) as send:
        comms.send_sticker(con,cid,'thanks',admin)
        body=send.call_args.args[3];assert body['chatType']=='max' and body['channelId']=='mx-test' and body['chatId']=='max-real-id'
    # Missing metadata selects this conversation's service, never the WhatsApp default.
    con.execute("UPDATE conversations SET meta='{}' WHERE id=?",(cid,))
    with patch.object(comms,'wz_call',return_value={'messageId':'test-max-3'}) as send:
        comms.send(con,cid,'Still MAX',admin)
        assert send.call_args.args[3]['channelId']=='mx-test'
    comms.put(con,'wazzup_max_channel','')
    with patch.object(comms,'wz_call') as send:
        try:comms.send(con,cid,'Must not send',admin);raise AssertionError('Fallback to WA')
        except comms.CommsError:assert not send.called
    comms.put(con,'wazzup_max_channel','mx-test')
    con.execute('UPDATE conversations SET meta=? WHERE id=?',(json.dumps({'channelId':'wa-test','chatType':'max'}),cid))
    with patch.object(comms,'wz_call') as send:
        try:comms.send(con,cid,'Wrong transport',admin);raise AssertionError('Wrong channel accepted')
        except comms.CommsError:assert not send.called
    con.execute('UPDATE conversations SET meta=? WHERE id=?',(json.dumps({'channelId':'mx-test','chatType':'max'}),cid))
    con.close()
    srv=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);threading.Thread(target=srv.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{srv.server_port}'
    op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    def call(path,body):
        req=urllib.request.Request(base+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
        try:
            with op.open(req) as r:return r.status,json.loads(r.read())
        except urllib.error.HTTPError as e:return e.code,json.loads(e.read())
    assert call('/api/login',{'login':'admin','password':'admin-pass-123'})[0]==200
    with patch.object(comms,'wz_call',return_value={'messageId':'family-max-1'}) as send:
        code,sent=call(f'/api/families/{family}/whatsapp',{'parent_id':parent,'text':'Hello MAX','channel':'max'})
        assert code==200 and sent['conversation_id']==cid
        assert send.call_args.args[3]['chatType']=='max'
        send.reset_mock()
        assert call(f'/api/families/{family}/whatsapp',{'parent_id':parent,'sticker':'thanks','channel':'max'})[0]==200
        assert send.call_args.args[3]['chatType']=='max' and 'contentUri' in send.call_args.args[3]
        send.reset_mock()
        assert call(f'/api/families/{family}/whatsapp',{'parent_id':parent,'text':'Wrong channel','channel':'unknown'})[0]==400
        assert not send.called
    srv.shutdown();print('MAX text, stickers, phone-start, rekey, family routing and no WA fallback: OK')
if __name__=='__main__':main()
