"""Датированные условия конкретной группы; не переписывают проданные абонементы."""
import json
from datetime import date
from . import logic,schedule,comms


def init(con):
    if 'started_on' not in {r[1] for r in con.execute('PRAGMA table_info(groups)')}:
        con.execute("ALTER TABLE groups ADD COLUMN started_on TEXT NOT NULL DEFAULT ''")
    con.execute('''CREATE TABLE IF NOT EXISTS group_terms(group_id INTEGER NOT NULL REFERENCES groups(id),valid_from TEXT NOT NULL,per_lesson INTEGER NOT NULL,credit_limit INTEGER NOT NULL,PRIMARY KEY(group_id,valid_from))''')


def terms(con,gid,ym):
    r=con.execute('SELECT * FROM group_terms WHERE group_id=? AND valid_from<=? ORDER BY valid_from DESC LIMIT 1',(gid,ym)).fetchone()
    return dict(r) if r else None


def provision_nebo_morning(con):
    from . import services as sv
    key='nebo_mon_wed_morning_terms_202611_v1'
    if comms.get(con,key):return
    candidates=[]
    for row in con.execute("SELECT g.* FROM groups g JOIN branches b ON b.id=g.branch_id WHERE g.active=1 AND b.active=1 AND g.is_makeup=0 AND g.program_id IS NULL"):
        branch=con.execute('SELECT name FROM branches WHERE id=?',(row['branch_id'],)).fetchone()[0]
        if 'небо' in branch.casefold() and set(logic.parse_weekdays(row['weekdays']))=={0,2} and row['time']=='10:00':candidates.append(dict(row))
    if len(candidates)!=1:return
    g=candidates[0];gid=g['id']
    if g.get('started_on') not in ('','2026-11-16'):return
    # Не принимаем старую группу с историческими абонементами за новую.
    if con.execute("SELECT 1 FROM subscriptions x JOIN enrollments e ON e.id=x.enrollment_id WHERE e.group_id=? AND x.month<'2026-11' LIMIT 1",(gid,)).fetchone():return
    existing=con.execute("SELECT * FROM group_versions WHERE group_id=? AND valid_from='2026-11-18'",(gid,)).fetchone()
    if existing and existing['time']!='10:30':return
    if con.execute("SELECT 1 FROM group_terms WHERE group_id=? AND valid_from='2026-11'",(gid,)).fetchone():return
    con.execute('SAVEPOINT morning_terms')
    try:
        con.execute("UPDATE groups SET started_on='2026-11-16' WHERE id=?",(gid,))
        stamp=sv.now_iso()
        con.execute('INSERT OR IGNORE INTO group_versions(group_id,valid_from,weekdays,time,day_times,created_at) VALUES (?,?,?,?,?,?)',(gid,schedule.BASE,g['weekdays'],g['time'],g['day_times'],stamp))
        con.execute("INSERT OR IGNORE INTO group_versions(group_id,valid_from,weekdays,time,day_times,created_by,created_at) VALUES (?,'2026-11-18',?,'10:30','','По поручению руководителя',?)",(gid,g['weekdays'],stamp))
        con.execute("INSERT INTO group_terms(group_id,valid_from,per_lesson,credit_limit) VALUES (?,'2026-11',1600,2)",(gid,))
        comms.put(con,key,str(gid))
        sv.audit(con,None,'group.morning_terms',json.dumps({'group_id':gid,'started_on':'2026-11-16','time_change':'2026-11-18','time':'10:30','rate':1600,'credit_limit':2}))
        con.execute('RELEASE morning_terms')
    except BaseException:
        con.execute('ROLLBACK TO morning_terms');con.execute('RELEASE morning_terms');raise


def provision_nebo_weekend_december(con):
    """Shift the four verified weekend groups by 30 minutes from 1 December."""
    from . import services as sv
    key = 'nebo_weekend_times_20261201'
    if comms.get(con, key):
        return
    effective = '2026-12-01'
    slots = {
        19: ('3–4', '10:00', '10:30'),
        20: ('4–5', '11:00', '11:30'),
        21: ('5–6', '12:00', '12:30'),
        22: ('6–8', '13:00', '13:30'),
    }
    con.execute('SAVEPOINT nebo_weekend_times')
    try:
        groups = con.execute("""
            SELECT g.* FROM groups g JOIN branches b ON b.id=g.branch_id
            WHERE g.id IN (19,20,21,22) AND b.id=1 AND b.name='ТЦ «Небо»'
              AND b.active=1 AND g.active=1 AND g.is_makeup=0 AND g.program_id IS NULL
              AND g.lesson_kind='' AND g.weekdays='5,6' AND g.day_times=''
        """).fetchall()
        if len(groups) != len(slots):
            return
        rows = []
        for g in groups:
            gid = g['id']
            age, old, new = slots[gid]
            before = schedule.on(con, g, '2026-11-30')
            after = schedule.on(con, g, effective)
            if (g['age'] != age or before['weekdays'] != '5,6' or before['time'] != old
                    or before['day_times'] or after['weekdays'] != '5,6' or after['day_times']
                    or after['time'] not in (old, new)):
                return
            if after['time'] == new:
                continue
            if con.execute('SELECT 1 FROM group_versions WHERE group_id=? AND valid_from=?',
                           (gid, effective)).fetchone():
                return
            rows.extend([(gid, schedule.BASE, g['time']), (gid, effective, new)])
        stamp = sv.now_iso()
        con.executemany("""
            INSERT INTO group_versions(group_id,valid_from,time,weekdays,day_times,created_at,created_by)
            VALUES (?,?,?,'5,6','',?,'По поручению руководителя')
            ON CONFLICT(group_id,valid_from) DO NOTHING
        """, [(gid, start, time, stamp) for gid, start, time in rows])
        comms.put(con, key, effective)
        sv.audit(con, None, 'group.weekend_times', key)
    except BaseException:
        con.execute('ROLLBACK TO nebo_weekend_times')
        raise
    finally:
        con.execute('RELEASE nebo_weekend_times')


def fixed_only(con,gid):
    """The specifically requested Nebo morning group has one tariff for new sales."""
    marked=comms.get(con,'nebo_mon_wed_morning_terms_202611_v1')
    if marked and str(marked).isdigit():return int(marked)==int(gid)
    row=con.execute('SELECT g.*,b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id WHERE g.id=?',(gid,)).fetchone()
    return bool(row and 'небо' in row['branch'].casefold() and set(logic.parse_weekdays(row['weekdays']))=={0,2} and row['time'] in ('10:00','10:30') and not row['program_id'] and not row['is_makeup'])
