"""Owner renames a login without changing passwords or user-linked records."""
import json
import os
import tempfile
import threading
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
os.environ['CRM_DB']=os.path.join(tempfile.mkdtemp(),'login-edit.sqlite3')
os.environ['CRM_ADMIN_PASSWORD']='admin-pass-123'
os.environ['CRM_QUIET']='1'
from crm import db, server

def main():
    con=db.connect(); db.init_db(con); con.close()
    srv=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
    threading.Thread(target=srv.serve_forever,daemon=True).start()
    base=f'http://127.0.0.1:{srv.server_port}'
    def client():
        op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
        def call(method,path,body=None):
            req=urllib.request.Request(base+path,method=method,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
            try:
                with op.open(req) as r:return r.status,json.loads(r.read())
            except urllib.error.HTTPError as e:return e.code,json.loads(e.read())
        return call
    owner=client(); assert owner('POST','/api/login',{'login':'admin','password':'admin-pass-123'})[0]==200
    for login in ['login-old','login-taken']:
        assert owner('POST','/api/users',{'login':login,'password':'employee-pass-123','post':'operator','name':login})[0]==200
    users={u['login']:u for u in owner('GET','/api/users')[1]}; uid=users['login-old']['id']
    worker=client(); assert worker('POST','/api/login',{'login':'login-old','password':'employee-pass-123'})[0]==200
    assert worker('PATCH',f'/api/users/{uid}',{'login':'unauthorized'})[0]==403
    assert owner('PATCH',f'/api/users/{uid}',{'login':' New.Login_1 '})[0]==200
    assert worker('GET','/api/me')[1]['login']=='new.login_1'
    probe=client(); assert probe('POST','/api/login',{'login':'NEW.LOGIN_1','password':'employee-pass-123'})[0]==200
    assert probe('POST','/api/login',{'login':'login-old','password':'employee-pass-123'})[0]==401
    for login in ['', 'bad login', 'кириллица', 'a'*81, 'LOGIN-TAKEN']:
        assert owner('PATCH',f'/api/users/{uid}',{'login':login})[0]==400
        assert worker('GET','/api/me')[1]['login']=='new.login_1'
    assert owner('PATCH','/api/users/999999',{'login':'missing'})[0]==404
    assert owner('PATCH',f'/api/users/{uid}',{'login':'new.login_1'})[0]==200
    first = owner('POST','/api/users',{'name':'Тестовый Стажёр','post':'trainee','phone':'+7 999 111 22 33'})
    second = owner('POST','/api/users',{'name':'Тестовый Стажёр','post':'trainee'})
    assert first[0]==200 and second[0]==200
    a,b=first[1],second[1]
    assert a['login']!=b['login'] and a['password']!=b['password']
    assert len(a['password'])>=16 and a['login'].isascii()
    auto=client(); assert auto('POST','/api/login',{'login':a['login'],'password':a['password']})[0]==200
    assert auto('GET','/api/meta')[1]['user']['perms']=={'planner':'own'}
    listed=owner('GET','/api/users')[1]
    saved=next(u for u in listed if u['id']==a['id'])
    assert saved['phone']=='+79991112233' and 'password' not in saved
    assert auto('POST','/api/users',{'name':'Denied','post':'owner'})[0]==403
    assert owner('POST','/api/users',{})[0]==400
    me=owner('GET','/api/me')[1]
    assert owner('PATCH',f'/api/users/{me["id"]}',{'login':'admin-renamed'})[0]==200
    assert owner('GET','/api/me')[1]['login']=='admin-renamed'
    srv.shutdown(); print('Login rename, owner authorization, uniqueness and password preservation: OK')
if __name__=='__main__': main()
