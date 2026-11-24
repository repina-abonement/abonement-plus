"""Reconcile residual November trainer rows after verified care cards exist."""
import json
import re

from . import chatbulk, services as sv


KEY="targeted_trainer_residual_20261124_v1"
EXTRA_CORRECTIONS={"магин":"магнин","серегин":"серёгин",
                   "фердавс маматахунов":"фирдавс маматахунов"}
ROWS=[
('26624','2026-11-03','17:45','Замир'),('26624','2026-11-03','18:45','Даня Бирюков'),
('26693','2026-11-05','16:45','Магин'),('26693','2026-11-05','19:45','Шарикова'),('26693','2026-11-05','19:45','Ямроз'),
('26705','2026-11-06','16:45','Лев Желтов'),('26705','2026-11-06','16:45','Дамир'),('26705','2026-11-06','18:45','Даня Бирюков'),('26705','2026-11-06','19:45','Михаил Завъялов'),
('26725','2026-11-07','14:00','Варвара Косарева'),('26725','2026-11-07','15:00','Даниил Тарасюк'),
('26766','2026-11-08','13:00','Никита Каримов'),('26766','2026-11-08','13:00','Миша Завъялов'),
('26795','2026-11-09','16:15','Серегин'),('26795','2026-11-09','16:15','Шарикова'),('26795','2026-11-09','16:15','Даня'),
('26812','2026-11-10','16:45','Лев Желтов'),('26812','2026-11-10','16:45','Эмильен'),('26812','2026-11-10','18:45','Даниил Бирюков'),('26812','2026-11-10','19:45','Никита Каримов'),
('26839','2026-11-11','18:45','Альфаз Рахман'),
('26927','2026-11-13','16:45','Лев Желтов'),('26927','2026-11-13','16:45','Глеб Сергиенко'),('26927','2026-11-13','18:45','Даниил Бирюков'),('26927','2026-11-13','19:45','Михаил Завъялов'),
('26965','2026-11-14','10:00','Макс серменюк'),('26965','2026-11-14','10:00','Марат'),('26965','2026-11-14','11:00','Саша Орехин'),('26965','2026-11-14','11:00','Коля'),('26965','2026-11-14','11:00','Ваня'),('26965','2026-11-14','11:00','Артемий'),
('27018','2026-11-14','13:00','Никита Каримов'),('27047','2026-11-15','13:00','Михаил Завъялов'),('27047','2026-11-15','13:00','Никита Каримов'),
('27050','2026-11-15','11:00','Саша Орехин'),('27050','2026-11-15','11:00','Коля Сергиенков'),
('27053','2026-11-15','17:45','Марк'),('27053','2026-11-15','18:45','Михаил Михайлов'),('27053','2026-11-15','18:45','Альфаз Рахман'),
('27073','2026-11-17','16:45','Лев Желтов'),('27073','2026-11-17','19:45','Киреева Ксения'),('27073','2026-11-17','19:45','Никита Каримов'),
('27090','2026-11-18','18:45','Альфаз Рахман'),('27093','2026-11-19','19:45','Шарикова'),('27093','2026-11-19','19:45','Тарасюк'),
('27104','2026-11-20','16:45','Глеб Серёгин'),('27104','2026-11-20','16:45','Фердавс Маматахунов'),('27104','2026-11-20','19:45','Ксения Киреева'),
('27108','2026-11-21','11:00','Коля Сергиенков'),
('27119','2026-11-21','10:00','Даниил Степаненко'),('27119','2026-11-21','10:00','Севастьян Жегулин'),('27119','2026-11-21','13:00','Михаил Завъялов'),
('27132','2026-11-22','10:00','Севастьян Жегулин'),('27132','2026-11-22','13:00','Михаил Завъялов'),('27140','2026-11-22','18:45','Альфаз Рахман'),
('27148','2026-11-23','16:45','Морозов'),('27148','2026-11-23','16:45','Магин'),('27148','2026-11-23','16:45','Степаненко'),('27148','2026-11-23','19:45','Тарасюк')]


def _words(value):
    words=re.findall(r'[а-яa-z]+',str(value or '').casefold().replace('ё','е'))
    return [chatbulk.ALIASES.get(word,word) for word in words]


def _correct(words):
    label=' '.join(words)
    corrected=chatbulk.NAME_CORRECTIONS.get(label,EXTRA_CORRECTIONS.get(label,label))
    return _words(corrected)


def _matches(con,name):
    wanted=_correct(_words(name))
    if not wanted:return []
    # A lone first name is never enough; a lone surname is allowed only once.
    if len(wanted)==1 and wanted[0] in {_words(x)[0] for x in chatbulk.FIRST_NAMES}:return []
    rows=[]
    for row in con.execute("SELECT id,name FROM students WHERE archived=0"):
        actual=_correct(_words(row['name']))
        if set(wanted)==set(actual) or len(wanted)==1 and wanted[0] in actual:rows.append(dict(row))
    return rows


def apply(con):
    actor=con.execute("SELECT id,name,role FROM users WHERE active=1 AND role='admin' AND trim(name)='Юлия Репина' ORDER BY id LIMIT 1").fetchone()
    if not actor:return {'state':'not_target_database'}
    added=already=0;unresolved=[]
    for message_id,day,tm,name in ROWS:
        matches=_matches(con,name)
        if len(matches)!=1:
            unresolved.append({'message_id':message_id,'date':day,'time':tm,'name':name,
                               'reason':'карточка не найдена однозначно' if not matches else 'несколько карточек'})
            continue
        sid=matches[0]['id']
        exists=con.execute("""SELECT 1 FROM erp_history_visits WHERE student_id=? AND visit_date=? AND visit_time=? AND status='present'
            UNION ALL SELECT 1 FROM erp_history_unassigned_visits WHERE student_id=? AND visit_date=? AND visit_time=? AND status='present'
            LIMIT 1""",(sid,day,tm,sid,day,tm)).fetchone()
        if exists:already+=1;continue
        source_id=f"trainers:{message_id}:{sid}"
        payload=json.dumps({'source':'ТРЕНЕРЫ','message_id':message_id,'reported_name':name,'confirmed':True},ensure_ascii=False)
        con.execute("""INSERT OR IGNORE INTO erp_history_unassigned_visits
            (source_id,source_subscription_id,student_id,visit_date,visit_time,kind,status,source_group,source_payload,imported_at,imported_by)
            VALUES (?,NULL,?,?,?,'subscription','present','',?,?,?)""",
            (source_id,sid,day,tm,payload,day+'T23:59:00','Перенос ТРЕНЕРЫ'))
        if con.execute('SELECT changes()').fetchone()[0]:added+=1
        else:already+=1
    state='complete' if not unresolved else 'partial'
    report={'state':state,'added':added,'already':already,'unresolved':unresolved,'total':len(ROWS)}
    con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)",(KEY,json.dumps(report,ensure_ascii=False)))
    sv.audit(con,dict(actor),'targeted_trainer_residual',json.dumps({'added':added,'already':already,'unresolved':len(unresolved)},ensure_ascii=False))
    return report


def report(con):
    row=con.execute("SELECT value FROM settings WHERE key=?",(KEY,)).fetchone()
    return json.loads(row[0]) if row else {'state':'pending'}
