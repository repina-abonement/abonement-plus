"""Telegram автоматически в карточке по подтверждённым идентификаторам, без совпадений по имени."""
import tempfile
from pathlib import Path
from crm import db, comms, services as sv


def main():
    con=db.connect(str(Path(tempfile.mkdtemp())/'family.sqlite3'))
    db.init_db(con)
    user={'id':1,'name':'Пример','role':'admin'}
    sid=sv.create_student(con,{'name':'Иван Примеров','parent_name':'Анна','phone':'+79000000201'},user)
    fid=con.execute('SELECT family_id FROM students WHERE id=?',(sid,)).fetchone()[0]
    con.execute("UPDATE parents SET messenger='https://t.me/Anna_Example' WHERE family_id=?",(fid,))
    cid=comms.upsert_conversation(con,'wazzup_telegram','12345',username='anna_example',title='Анна')
    comms.add_message(con,cid,'in','Вымышленное сообщение')
    assert con.execute('SELECT family_id FROM conversations WHERE id=?',(cid,)).fetchone()[0]==fid
    con.execute('UPDATE conversations SET family_id=NULL WHERE id=?',(cid,))
    data=comms.family_comms(con,fid)
    assert any(c['id']==cid for c in data['conversations']) and data['items'][0]['text']=='Вымышленное сообщение'
    # Ручная привязка к другой семье не переопределяется даже при том же username.
    sid2=sv.create_student(con,{'name':'Пётр Примеров','parent_name':'Анна','phone':'+79000000202'},user)
    fid2=con.execute('SELECT family_id FROM students WHERE id=?',(sid2,)).fetchone()[0]
    comms.link(con,cid,fid2)
    assert not comms.family_comms(con,fid)['conversations']
    con.execute("UPDATE parents SET messenger='@Anna_Example' WHERE family_id=?",(fid2,))
    ambiguous=comms.upsert_conversation(con,'telegram','98765',username='anna_example',title='Анна')
    assert con.execute('SELECT family_id FROM conversations WHERE id=?',(ambiguous,)).fetchone()[0] is None
    assert not any(c['id']==ambiguous for c in comms.family_comms(con,fid)['conversations'])
    con.execute("UPDATE parents SET messenger='' WHERE family_id=?",(fid2,))
    byphone=comms.upsert_conversation(con,'wazzup_telegram','54321',phone='8 (900) 000-02-01')
    assert con.execute('SELECT family_id FROM conversations WHERE id=?',(byphone,)).fetchone()[0]==fid
    archive=comms.upsert_conversation(con,'tg_archive','tga:45678')
    comms.link(con,archive,fid)
    active=comms.upsert_conversation(con,'wazzup_telegram','45678')
    assert any(c['id']==active for c in comms.family_comms(con,fid)['conversations'])
    conflicting=comms.upsert_conversation(con,'wazzup_telegram','76543',username='anna_example',phone='+79000000202')
    assert con.execute('SELECT family_id FROM conversations WHERE id=?',(conflicting,)).fetchone()[0] is None
    assert not any(c['id']==conflicting for c in comms.family_comms(con,fid)['conversations'])
    # Один чат «по номеру» не даёт основания связать с ним постороннего отправителя.
    placeholder=comms.upsert_conversation(con,'wazzup_telegram','phone:79000000201',phone='+79000000201')
    comms.add_message(con,placeholder,'out','Пример')
    comms._wz_tg_rekey(con,'999999','')
    assert con.execute('SELECT external_id FROM conversations WHERE id=?',(placeholder,)).fetchone()[0]=='phone:79000000201'
    # Чужой явно привязанный чат не подмешивается через общий телефон.
    con.execute('UPDATE conversations SET family_id=? WHERE id=?',(fid2,byphone))
    assert not any(c['id']==byphone for c in comms.family_comms(con,fid)['conversations'])
    print('ok: exact username/phone, existing conversations, no name guessing, ambiguous identity, manual link retained, no unrelated chat merge')


if __name__=='__main__':main()
