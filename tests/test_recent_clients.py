"""Recent client cards and chats are isolated per operator and clear independently."""
import json
import os
import tempfile
import threading
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
os.environ['CRM_DB']=os.path.join(tempfile.mkdtemp(),'recents.sqlite3')
os.environ['CRM_ADMIN_PASSWORD']='admin-pass-123';os.environ['CRM_QUIET']='1'
from crm import db, server, services as sv, comms

def main():
    con=db.connect();db.init_db(con)
    admin=dict(con.execute("SELECT * FROM users WHERE login='admin'").fetchone())
    for login,role in [('recent-other','operator'),('recent-coach','coach')]:
        con.execute('INSERT INTO users(login,name,role,pw_hash) VALUES (?,?,?,?)',(login,login,role,db.hash_password('test-pass-123')))
    ids=[sv.create_student(con,{'name':'Клиент '+str(i),'parent_name':'Родитель','phone':f'+7999111000{i}'},admin) for i in range(2)]
    family=con.execute('SELECT family_id FROM students WHERE id=?',(ids[1],)).fetchone()[0]
    cid=comms.upsert_conversation(con,'telegram','demo-recent',title='Local test')
    con.execute('UPDATE conversations SET family_id=? WHERE id=?',(family,cid))
    con.close()
    srv=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);threading.Thread(target=srv.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{srv.server_port}'
    def client(login,pw):
        op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
        def call(method,path,body=None):
            req=urllib.request.Request(base+path,method=method,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
            try:
                with op.open(req) as r:return r.status,json.loads(r.read())
            except urllib.error.HTTPError as e:return e.code,json.loads(e.read())
        assert call('POST','/api/login',{'login':login,'password':pw})[0]==200
        return call
    owner=client('admin','admin-pass-123');other=client('recent-other','test-pass-123');coach=client('recent-coach','test-pass-123')
    for sid in [ids[0],ids[1],ids[0]]:assert owner('GET',f'/api/students/{sid}')[0]==200
    recent=owner('GET','/api/students/recent')[1]
    assert [r['id'] for r in recent]==[ids[0],ids[1]]
    assert other('GET','/api/students/recent')[1]==[]
    assert other('GET',f'/api/conversations/{cid}?remember=1')[0]==200
    assert [r['id'] for r in other('GET','/api/students/recent')[1]]==[ids[1]]
    assert coach('GET','/api/students/recent')[0]==403
    assert coach('DELETE','/api/students/recent')[0]==403
    assert owner('GET','/api/students/999999')[0]==404
    con=db.connect();con.execute('UPDATE students SET name=? WHERE id=?',('Новое имя',ids[0]));con.execute('UPDATE students SET archived=1 WHERE id=?',(ids[1],));con.close()
    rows=owner('GET','/api/students/recent')[1];assert len(rows)==1 and rows[0]['name']=='Новое имя'
    con=db.connect();con.execute('UPDATE students SET archived=0 WHERE id=?',(ids[1],));con.close()
    assert owner('DELETE','/api/students/recent')[0]==200 and owner('GET','/api/students/recent')[1]==[]
    assert len(other('GET','/api/students/recent')[1])==1
    srv.shutdown();print('Recent client ordering, chats, isolation, privacy and clear: OK')
if __name__=='__main__':main()
