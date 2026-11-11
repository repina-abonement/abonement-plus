"""Общие клиентские задачи: только дата, приоритет внутри дня, личные планеры закрыты."""
import tempfile
from pathlib import Path
from datetime import date,timedelta
from crm import db,tasks,services as sv,smart

def main():
    c=db.connect(str(Path(tempfile.mkdtemp())/'tasks.sqlite3'));db.init_db(c);smart.REQUIRE=False
    def staff(login,role):return c.execute("INSERT INTO users(login,pw_hash,name,role) VALUES (?,'test',?,?)",(login,login,role)).lastrowid
    one=staff('one','operator');two=staff('two','operator');coach=staff('coach','coach')
    u={'id':one,'name':'one','role':'operator'};v={'id':two,'name':'two','role':'operator'}
    sid=sv.create_student(c,{'name':'Тест Клиент','parent_name':'Анна','phone':'+79000000441','funnel':'members'},u)
    today=date.today().isoformat();tomorrow=(date.today()+timedelta(days=1)).isoformat()
    a=tasks.add_client_task(c,sid,{'title':'Обычная','due':today},u)
    b=tasks.add_client_task(c,sid,{'title':'Приоритет','due':today,'priority':True},u)
    later=tasks.add_client_task(c,sid,{'title':'Завтра','due':tomorrow,'priority':True},u)
    personal=tasks.add_task(c,'plan',None,today+'T12:00',title='Личное',assignee=one)
    # Задачи клиента видит другой администратор; личные поручения не становятся общими.
    cards=[t for col in tasks.erp_board(c,'today',v)['columns'] for t in col['cards']]
    assert [t['id'] for t in cards if t.get('student_id')==sid]==[b,a]
    assert personal not in [t['id'] for t in cards] and later not in [t['id'] for t in cards]
    mine=tasks.my_tasks(c,v)['groups'];assert {a,b}.issubset({t['id'] for t in mine['today']})
    assert personal not in {t['id'] for x in mine.values() for t in x}
    assert tasks.for_student(c,sid)['open'][0]['id']==b
    # Старый исполнитель-тренер не скрывает клиентскую задачу от администраторов.
    c.execute('UPDATE tasks SET assignee_id=? WHERE id=?',(coach,a))
    assert a in [t['id'] for col in tasks.erp_board(c,'today',v)['columns'] for t in col['cards']]
    updated=tasks.edit(c,a,{'due_at':today,'priority':True,'assignee_id':coach},v)
    assert updated['due_at']==today+'T23:59' and updated['priority']==1 and updated['assignee_id'] is None
    assert updated['client_task']
    tasks.edit(c,b,{'priority':False,'due_at':tomorrow},u)
    assert b not in [t['id'] for col in tasks.erp_board(c,'today',v)['columns'] for t in col['cards']]
    assert tasks.for_student(c,sid)['open'][0]['id']==a
    assert c.execute('SELECT due_at,assignee_id FROM tasks WHERE id=?',(personal,)).fetchone()[0]==today+'T12:00'
    tasks.complete(c,a,v)
    assert tasks.my_tasks(c,u)['done_today']>=1 and tasks.my_tasks(c,v)['done_today']>=1
    print('ok: client tasks shared with every operator, priority per date, date-only edit, no assignee choice, private plans stay private')

if __name__=='__main__':main()
