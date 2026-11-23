"""One-time recovery of Mikhail Rubaylo's verified 30.11 trial booking."""
import json

from . import chatdaytransfer, comms

KEY = "targeted_rubaylo_chat_transfer_20261123"
NAME = "Рубайло Михаил"
PHONE = "+79164496354"
SOURCE_ID = "sz:22822"


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
    phone=comms.normalize_phone(PHONE)
    by_name=con.execute("SELECT id,name FROM students WHERE archived=0 AND trim(name) IN ('Рубайло Михаил','Михаил Рубайло')").fetchall()
    by_phone=con.execute("""SELECT DISTINCT s.id,s.name FROM students s JOIN parents p ON p.family_id=s.family_id
        WHERE s.archived=0 AND (phone_match(p.phone_norm,?) OR phone_match(p.phone,?))""",(phone,phone)).fetchall()
    ids={row['id'] for row in [*by_name,*by_phone]}
    if len(ids)>1 or (by_name and by_phone and by_name[0]['id']!=by_phone[0]['id']):
        return _save(con,{'state':'identity_conflict','candidate_ids':sorted(ids)})
    if by_name and not by_phone:return _save(con,{'state':'phone_mismatch','candidate_id':by_name[0]['id']})
    if by_phone and ' '.join(by_phone[0]['name'].casefold().split()) not in {'рубайло михаил','михаил рубайло'}:
        return _save(con,{'state':'name_mismatch','candidate_id':by_phone[0]['id']})
    event={'source_id':SOURCE_ID,'source_chat':'Служба заботы','source_at':'2026-11-22T11:41:54',
           'student_name':NAME,'birth':'2025-02-01','parent_name':'Анастасия','phone':PHONE,
           'source':'канал Футболики','create_if_missing':True,'event_kind':'trial_booking',
           'event_date':'2026-11-30','trial_time':'10:30','note':'Пробное перенесено на 30.11 в 10:30'}
    result=chatdaytransfer.apply(con,{'events':[event]},dict(actor))
    row=con.execute("""SELECT s.id,s.birth,s.trial_at,s.historical_trial,s.stage,s.source FROM students s
        JOIN parents p ON p.family_id=s.family_id WHERE s.archived=0
        AND (phone_match(p.phone_norm,?) OR phone_match(p.phone,?))""",(phone,phone)).fetchone()
    task=con.execute("""SELECT id,title,due_at FROM tasks WHERE student_id=? AND status='open'
        AND kind='trial_confirm' ORDER BY id LIMIT 1""",(row['id'],)).fetchone() if row else None
    complete=bool(row and row['birth']=='2025-02-01' and row['trial_at']=='2026-11-30T10:30'
                  and row['historical_trial']==0 and row['stage']=='trial_booked' and row['source']=='канал Футболики'
                  and task and task['title']=='Напоминание о ПУ (звонок)' and str(task['due_at'])[:10]=='2026-11-29'
                  and not result['errors'] and not result['unmatched'] and not result['ambiguous'])
    return _save(con,{'state':'complete' if complete else 'verification_failed','student_id':row['id'] if row else None,
                      'task_id':task['id'] if task else None,'task_title':task['title'] if task else None,
                      'task_due':task['due_at'] if task else None,'transfer':{k:len(v) for k,v in result.items()}})
