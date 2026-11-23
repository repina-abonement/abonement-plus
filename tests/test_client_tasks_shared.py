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
    sid=sv.create_student(c,{'name':'Тест Клиент','parent_name':'Анна','phone':'+79000000441','funnel':'members','source':'Тест'},u)
    uncovered=sv.create_student(c,{'name':'Без Задачи','parent_name':'Мария','phone':'+79000000442','funnel':'members','source':'Тест'},u)
    today=date.today().isoformat();tomorrow=(date.today()+timedelta(days=1)).isoformat()
    a=tasks.add_client_task(c,sid,{'title':'Обычная','due':today,'note':'Подробный контекст задачи для другого администратора'},u)
    b=tasks.add_client_task(c,sid,{'title':'Приоритет','due':today,'priority':True},u)
    later=tasks.add_client_task(c,sid,{'title':'Завтра','due':tomorrow,'priority':True},u)
    future_day=(date.today()+timedelta(days=8)).isoformat()
    future=tasks.add_client_task(c,sid,{'title':'Дальняя задача','due':future_day},u)
    # Закрытая задача с тем же названием не мешает создать новую общую задачу.
    repeated=tasks.add_client_task(c,sid,{'title':'Уточнить дни посещения','due':today,'note':'Первый вариант'},u)
    tasks.complete(c,repeated,u)
    repeated_again=tasks.add_client_task(c,sid,{'title':'Уточнить дни посещения','due':tomorrow,'note':'Сохранённое подробное описание'},u)
    assert repeated_again and repeated_again!=repeated
    assert tuple(c.execute('SELECT status,assignee_id,note FROM tasks WHERE id=?',(repeated_again,)).fetchone())==('open',None,'Сохранённое подробное описание')
    personal=tasks.add_task(c,'plan',None,today+'T12:00',title='Личное',assignee=one)
    unrelated=tasks.add_task(c,'manual',None,today+'T13:00',title='Чужое бытовое',assignee=two)
    # Задачи клиента видит другой администратор; личные поручения не становятся общими.
    cards=[t for col in tasks.erp_board(c,'today',v)['columns'] for t in col['cards']]
    assert [t['id'] for t in cards if t.get('student_id')==sid]==[b,a]
    assert personal not in [t['id'] for t in cards] and later not in [t['id'] for t in cards]
    assert future in [t['id'] for col in tasks.erp_board(c,'future',v)['columns'] for t in col['cards']]
    assert later in [t['id'] for col in tasks.erp_board(c,tomorrow,v)['columns'] for t in col['cards']]
    assert a not in [t['id'] for col in tasks.erp_board(c,tomorrow,v)['columns'] for t in col['cards']]
    assert tasks.erp_board(c,'future',v)['counts']['future']>=1
    assert tasks.ensure_client_task_coverage(c)>0
    fallback=c.execute("SELECT * FROM tasks WHERE student_id=? AND kind='client_followup'",(uncovered,)).fetchone()
    assert fallback and fallback['status']=='open' and fallback['priority']==1
    assert fallback['id'] in [t['id'] for col in tasks.erp_board(c,'today',v)['columns'] for t in col['cards']]
    normal=tasks.add_client_task(c,uncovered,{'title':'Конкретный следующий шаг','due':tomorrow},u)
    tasks.ensure_client_task_coverage(c)
    assert c.execute('SELECT status FROM tasks WHERE id=?',(fallback['id'],)).fetchone()[0]=='cancelled'
    tasks.complete(c,normal,u);tasks.ensure_client_task_coverage(c)
    assert c.execute('SELECT status FROM tasks WHERE id=?',(fallback['id'],)).fetchone()[0]=='open'
    visible={t['id'] for day in ('overdue','today','tomorrow','future') for col in tasks.erp_board(c,day,v)['columns'] for t in col['cards']}
    expected={r[0] for r in c.execute("""SELECT id FROM tasks WHERE status='open' AND kind!='manual_payment_check'
        AND (student_id IS NOT NULL OR family_id IS NOT NULL)""")}
    assert expected<=visible,(expected-visible)
    own_cards=[t for col in tasks.erp_board(c,'today',u)['columns'] for t in col['cards']]
    assert personal in [t['id'] for t in own_cards] and unrelated not in [t['id'] for t in own_cards]
    senior=dict(u,is_senior=1)
    assert unrelated not in [t['id'] for col in tasks.erp_board(c,'today',senior)['columns'] for t in col['cards']]
    mine=tasks.my_tasks(c,v)['groups'];assert {a,b}.issubset({t['id'] for t in mine['today']})
    assert personal not in {t['id'] for x in mine.values() for t in x}
    assert tasks.for_student(c,sid)['open'][0]['id']==b
    assert tuple(c.execute('SELECT title,note FROM tasks WHERE id=?',(a,)).fetchone())==('Обычная','Подробный контекст задачи для другого администратора')
    changed=tasks.edit(c,a,{'title':'Новое короткое название','note':''},u)
    assert changed['previous_text']['title']=='Обычная'
    assert changed['previous_text']['note']=='Подробный контекст задачи для другого администратора'
    restored=tasks.edit(c,a,{'title':changed['previous_text']['title'],'note':changed['previous_text']['note']},u)
    assert (restored['title'],restored['note'])==('Обычная','Подробный контекст задачи для другого администратора')
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

    # Новый клиент CRM без рекламного источника получает отдельную обязательную задачу.
    lead=sv.create_new_lead(c,{'name':'Новый без источника','parent_name':'Ирина','phone':'+79000000443'},u)
    source_task=c.execute("SELECT * FROM tasks WHERE student_id=? AND auto_key=?",(lead,f'ad-source-required:{lead}')).fetchone()
    assert source_task and source_task['status']=='open' and source_task['title']=='Уточнить рекламный источник'
    sv.update_student(c,lead,{'source':'Яндекс Карты'},u)
    assert c.execute('SELECT status FROM tasks WHERE id=?',(source_task['id'],)).fetchone()[0]=='cancelled'
    erp=sv.create_student(c,{'name':'Перенос ERP','parent_name':'Ольга','phone':'+79000000444'},u)
    c.execute("UPDATE students SET erp_id='12345' WHERE id=?",(erp,))
    tasks.ensure_ad_source_tasks(c)
    assert not c.execute("SELECT 1 FROM tasks WHERE student_id=? AND auto_key=? AND status='open'",(erp,f'ad-source-required:{erp}')).fetchone()
    old=sv.create_student(c,{'name':'Старая карточка','parent_name':'Елена','phone':'+79000000445'},u)
    c.execute("UPDATE students SET created_at='2026-11-01T12:00' WHERE id=?",(old,))
    tasks.ensure_ad_source_tasks(c)
    assert not c.execute("SELECT 1 FROM tasks WHERE student_id=? AND auto_key=? AND status='open'",(old,f'ad-source-required:{old}')).fetchone()
    tasks.complete(c,a,v)
    assert tasks.my_tasks(c,u)['done_today']>=1 and tasks.my_tasks(c,v)['done_today']>=1
    print('ok: client tasks shared with every operator, priority per date, date-only edit, no assignee choice, private plans stay private')

if __name__=='__main__':main()
