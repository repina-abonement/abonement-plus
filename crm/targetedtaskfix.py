"""One narrowly-scoped production repair requested on 2026-11-22."""
import json
from . import tasks

KEY='targeted_task_fix_20261122_sharipov_shulezhko'
TARGETS=((6,4644,{'даниил шарипов','шарипов даниил'}),(7,4645,{'даниил шулежко','шулежко даниил'}))


def apply(con):
    previous=con.execute("SELECT value FROM settings WHERE key=?",(KEY,)).fetchone()
    if previous:
        try:
            saved=json.loads(previous[0])
            if saved.get('state')=='complete':return saved
            con.execute('DELETE FROM settings WHERE key=?',(KEY,))
        except (TypeError,ValueError):return {'state':'invalid_previous_report'}
    rows=[]
    for student_id,old_task_id,names in TARGETS:
        student=con.execute('SELECT id,name FROM students WHERE id=?',(student_id,)).fetchone()
        old=con.execute('SELECT id,student_id,title,status FROM tasks WHERE id=?',(old_task_id,)).fetchone()
        if not student or ' '.join(student['name'].casefold().split()) not in names:
            rows.append({'student_id':student_id,'old_task_id':old_task_id,'state':'student_mismatch'});continue
        if not old or old['student_id']!=student_id or old['status']!='open' or old['title'].strip().casefold()!='новый лид':
            rows.append({'student_id':student_id,'old_task_id':old_task_id,'state':'old_task_not_open'});continue
        months=[r[0] for r in con.execute("SELECT DISTINCT month FROM subscriptions WHERE student_id=? AND status!='cancelled' AND COALESCE(kind,'month')='month' ORDER BY month",(student_id,))]
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='erp_history_subscriptions'").fetchone():
            months+= [r[0] for r in con.execute("SELECT DISTINCT month FROM erp_history_subscriptions WHERE student_id=? ORDER BY month",(student_id,))]
        new_id=tasks.schedule_renewal(con,student_id)
        new=con.execute("SELECT id,kind,title,due_at,status FROM tasks WHERE id=?",(new_id,)).fetchone() if new_id else None
        old_after=con.execute('SELECT status FROM tasks WHERE id=?',(old_task_id,)).fetchone()
        open_renew=con.execute("SELECT count(*) FROM tasks WHERE student_id=? AND status='open' AND kind='renew' AND title='Проверить продление'",(student_id,)).fetchone()[0]
        state='applied' if new and old_after and old_after[0]=='cancelled' and open_renew==1 else 'verification_failed'
        rows.append({'student_id':student_id,'old_task_id':old_task_id,'old_status':old_after[0] if old_after else 'missing',
                     'new_task_id':new['id'] if new else None,'new_due':new['due_at'] if new else None,
                     'new_status':new['status'] if new else None,'open_renew_count':open_renew,
                     'has_december':'2026-12' in months,'state':state})
    report={'state':'complete' if len(rows)==2 and all(r['state']=='applied' for r in rows) else 'not_applied','rows':rows}
    con.execute("INSERT INTO settings(key,value) VALUES (?,?)",(KEY,json.dumps(report,ensure_ascii=False)))
    return report


def report(con):
    row=con.execute('SELECT value FROM settings WHERE key=?',(KEY,)).fetchone()
    return json.loads(row[0]) if row else {'state':'pending','rows':[]}
