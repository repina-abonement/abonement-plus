import tempfile
from pathlib import Path

from crm import chatdaytransfer, db, services


def test_chat_day_transfer_is_idempotent_and_never_guesses_duplicate_names():
    con=db.connect(str(Path(tempfile.mkdtemp())/'chat-day.sqlite3'));db.init_db(con)
    user={'id':1,'role':'admin','name':'Руководитель'}
    sid=services.create_student(con,{'name':'Ворожцов Михаил','parent_name':'Анна','phone':'+7 900 111-22-33'},user)
    services.create_student(con,{'name':'Иванов Иван','parent_name':'Ольга','phone':'+7 900 000-00-01'},user)
    services.create_student(con,{'name':'Иванов Иван','parent_name':'Мария','phone':'+7 900 000-00-02'},user)
    payload={'events':[{'source_id':'sz:1','source_chat':'СЗ','source_at':'2026-11-22T10:32:29','student_name':'Ворожцов Миша','event_kind':'note','note':'Записан на разовое занятие 28.11 в 10:00','task_title':'Напомнить о разовом занятии','task_due':'2026-11-27T12:00'}]}
    first=chatdaytransfer.apply(con,payload,user)
    assert first['applied'][0]['student_id']==sid
    assert len(con.execute("SELECT * FROM history WHERE student_id=? AND kind='chat_transfer'",(sid,)).fetchall())==1
    assert con.execute("SELECT title FROM tasks WHERE auto_key='chat-day:sz:1'").fetchone()[0]=='Напомнить о разовом занятии'
    second=chatdaytransfer.apply(con,payload,user)
    assert len(second['already'])==1 and not second['applied']
    ambiguous=chatdaytransfer.apply(con,{'events':[{'source_id':'sz:2','source_at':'2026-11-22T12:00:00','student_name':'Иванов Иван','note':'Факт'}]},user)
    assert ambiguous['ambiguous'] and not ambiguous['applied']

