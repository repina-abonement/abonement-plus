"""One-time recovery of Evgeny Sergievsky by verified ERP identity."""
import json

from . import chatdaytransfer, tasks

KEY = "targeted_sergievsky_chat_transfer_20261123_v3"
ERP_ID = "182330"
NAME = "Евгений Сергиевский"
SOURCE_ID = "sz:22884"


def _save(con, report):
    con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)", (KEY, json.dumps(report, ensure_ascii=False)))
    return report


def apply(con):
    saved=con.execute("SELECT value FROM settings WHERE key=?",(KEY,)).fetchone()
    if saved:
        try:
            report=json.loads(saved[0])
            if report.get('state')=='complete':return report
        except (TypeError,ValueError):pass
    actor=con.execute("SELECT id,name,role FROM users WHERE active=1 AND role='admin' AND trim(name)='Юлия Репина' ORDER BY id LIMIT 1").fetchone()
    if not actor:return {'state':'not_target_database'}
    by_erp=con.execute("SELECT id,name,erp_id FROM students WHERE archived=0 AND erp_id=?",(ERP_ID,)).fetchall()
    by_name=con.execute("SELECT id,name,erp_id FROM students WHERE archived=0 AND trim(name) IN ('Евгений Сергиевский','Сергиевский Евгений')").fetchall()
    ids={row['id'] for row in [*by_erp,*by_name]}
    if len(by_erp)>1 or len(ids)>1:
        return _save(con,{'state':'identity_conflict','candidate_ids':sorted(ids)})
    if by_name and by_name[0]['erp_id'] and str(by_name[0]['erp_id'])!=ERP_ID:
        return _save(con,{'state':'erp_id_mismatch','candidate_id':by_name[0]['id'],'actual_erp_id':by_name[0]['erp_id']})
    if by_name and not by_name[0]['erp_id']:
        con.execute("UPDATE students SET erp_id=? WHERE id=?",(ERP_ID,by_name[0]['id']))
    event={'source_id':SOURCE_ID,'source_chat':'Служба заботы','source_at':'2026-11-22T17:16:40',
           'student_name':NAME,'erp_id':ERP_ID,'erp_create_if_missing':True,
           'erp_group_name':'Небо НУП ср/вс 16:45 возраст 3-4','erp_group_time':'16:45','erp_branch_hint':'Небо',
           'event_kind':'makeup_booking','event_date':'2026-11-24',
           'note':'Отработка назначена на 24.11 в 17:45. Родитель просил обязательно прислать напоминание',
           'task_title':'Напомнить об отработке 24.11 в 17:45','task_due':'2026-11-24T09:00',
           'clarification_task_title':'Заполнить данные семьи Сергиевского из ERP',
           'clarification_task_note':'Добавить дату рождения, имя приоритетного родителя и телефон из карточки ERP или рабочего чата, не создавая второго клиента'}
    result=chatdaytransfer.apply(con,{'events':[event]},dict(actor))
    row=con.execute("SELECT id,name,erp_id,group_id FROM students WHERE archived=0 AND erp_id=?",(ERP_ID,)).fetchone()
    history_events=[
        {'source_id':'sz:20167:сергиевский-абон','source_chat':'Служба заботы','source_at':'2026-11-02T17:59:09',
         'student_name':NAME,'erp_id':ERP_ID,'event_kind':'note',
         'note':'СЗ: оформить абонемент в группе Ср/Вс 16:45 начиная с 11.11. Сумма и способ оплаты в сообщении не указаны'},
        {'source_id':'trainers:26839:сергиевский','source_chat':'ТРЕНЕРЫ','source_at':'2026-11-11T20:08:39',
         'student_name':NAME,'erp_id':ERP_ID,'event_kind':'note','note':'Указан тренером в составе группы 16:45'},
        {'source_id':'trainers:27053:сергиевский','source_chat':'ТРЕНЕРЫ','source_at':'2026-11-15T19:53:41',
         'student_name':NAME,'erp_id':ERP_ID,'event_kind':'note','note':'Указан тренером в составе группы 16:45'},
        {'source_id':'sz:21894:сергиевский','source_chat':'Служба заботы','source_at':'2026-11-17T13:52:05',
         'student_name':NAME,'erp_id':ERP_ID,'event_kind':'note','note':'Указан в составе группы 16:45'},
        {'source_id':'trainers:27140:сергиевский','source_chat':'ТРЕНЕРЫ','source_at':'2026-11-22T20:07:17',
         'student_name':NAME,'erp_id':ERP_ID,'event_kind':'note','note':'Указан тренером в составе группы 16:45'},
        {'source_id':'sz:22880:сергиевский-декабрь','source_chat':'Служба заботы','source_at':'2026-11-22T17:08:26',
         'student_name':NAME,'erp_id':ERP_ID,'event_kind':'note',
         'note':'Семья уезжает с 10.12. До отъезда в их группе только три занятия; нужно согласовать декабрьский абонемент'},
    ]
    history_result=chatdaytransfer.apply(con,{'events':history_events},dict(actor)) if row else {'errors':['card missing'],'unmatched':[],'ambiguous':[]}
    day_before_id=tasks.add_task(con,'manual',row['id'],'2026-11-23T23:59',
        auto_key='sergievsky-makeup-day-before:2026-11-24',
        title='Напомнить родителю об отработке 24.11 в 17:45',
        note='Родитель сам попросил напомнить заранее. Сегодня отправить отдельное напоминание об отработке завтра, 24.11 в 17:45',
        source='manual') if row else None
    renew_key='sergievsky-december-renewal:2026-12'
    existing_renew=con.execute("""SELECT id FROM tasks WHERE student_id=? AND status='open'
        AND (auto_key=? OR (kind='renew' AND title='Проверить продление')) ORDER BY id""",
        (row['id'],renew_key)).fetchall() if row else []
    if len(existing_renew)==1:
        con.execute("""UPDATE tasks SET kind='renew',title=?,note=?,due_at='2026-11-23T23:59',
            auto_key=?,source='manual' WHERE id=?""",
            ('Проверить продление на декабрь с учётом отъезда 10.12',
             'СЗ: семья уезжает с 10 декабря, до отъезда в группе Ср/Вс 16:45 только три занятия. Связаться и согласовать состав и стоимость декабрьского абонемента',
             renew_key,existing_renew[0]['id']))
        renew_id=existing_renew[0]['id']
    elif not existing_renew:
        renew_id=tasks.add_task(con,'renew',row['id'],'2026-11-23T23:59',auto_key=renew_key,
            title='Проверить продление на декабрь с учётом отъезда 10.12',
            note='СЗ: семья уезжает с 10 декабря, до отъезда в группе Ср/Вс 16:45 только три занятия. Связаться и согласовать состав и стоимость декабрьского абонемента',
            source='manual') if row else None
    else:
        renew_id=None
    reminder=con.execute("SELECT id,title,due_at FROM tasks WHERE student_id=? AND status='open' AND title=?",
                         (row['id'],event['task_title'])).fetchone() if row else None
    clarify=con.execute("SELECT id,title,due_at FROM tasks WHERE student_id=? AND status='open' AND title=?",
                        (row['id'],event['clarification_task_title'])).fetchone() if row else None
    day_before=con.execute("SELECT id,title,due_at FROM tasks WHERE student_id=? AND status='open' AND auto_key=?",
                           (row['id'],'sergievsky-makeup-day-before:2026-11-24')).fetchone() if row else None
    renew=con.execute("SELECT id,title,due_at FROM tasks WHERE student_id=? AND status='open' AND auto_key=?",
                      (row['id'],renew_key)).fetchone() if row else None
    complete=bool(row and row['name'] in ('Евгений Сергиевский','Сергиевский Евгений') and str(row['erp_id'])==ERP_ID
                  and reminder and str(reminder['due_at'])[:10]=='2026-11-24' and clarify
                  and day_before and str(day_before['due_at'])[:10]=='2026-11-23'
                  and renew and str(renew['due_at'])[:10]=='2026-11-23'
                  and not result['errors'] and not result['unmatched'] and not result['ambiguous']
                  and not history_result['errors'] and not history_result['unmatched'] and not history_result['ambiguous'])
    return _save(con,{'state':'complete' if complete else 'verification_failed','student_id':row['id'] if row else None,
                      'group_id':row['group_id'] if row else None,'reminder_task_id':reminder['id'] if reminder else None,
                      'day_before_task_id':day_before['id'] if day_before else day_before_id,
                      'renewal_task_id':renew['id'] if renew else renew_id,
                      'clarification_task_id':clarify['id'] if clarify else None,
                      'transfer':{key:len(value) for key,value in result.items()},
                      'history_transfer':{key:len(value) for key,value in history_result.items()}})
