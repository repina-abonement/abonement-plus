"""Shared branch events, separate from task completion and client birthday fields."""
from datetime import date,datetime
from zoneinfo import ZoneInfo
import json


def init(con):
    con.execute('''CREATE TABLE IF NOT EXISTS planner_events(id INTEGER PRIMARY KEY,title TEXT NOT NULL,
      day TEXT NOT NULL,start_time TEXT NOT NULL DEFAULT '',end_time TEXT NOT NULL DEFAULT '',
      branch_id INTEGER REFERENCES branches(id),participants TEXT NOT NULL DEFAULT '',note TEXT NOT NULL DEFAULT '',
      created_by INTEGER NOT NULL,updated_by INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
      cancelled INTEGER NOT NULL DEFAULT 0)''')
    con.execute('CREATE INDEX IF NOT EXISTS planner_events_day ON planner_events(day,cancelled)')
    con.execute('CREATE TABLE IF NOT EXISTS planner_event_history(id INTEGER PRIMARY KEY,event_id INTEGER NOT NULL,actor_id INTEGER NOT NULL,at TEXT NOT NULL,action TEXT NOT NULL,details TEXT NOT NULL)')


def allowed(user):return user.get('role') in ('admin','operator')


def save(con,user,data,event_id=None):
    from .planner import PlanError
    if not allowed(user):raise PlanError('Мероприятия доступны администраторам')
    old=con.execute('SELECT * FROM planner_events WHERE id=?',(event_id,)).fetchone() if event_id else None
    if event_id and not old:raise PlanError('Мероприятие не найдено')
    if data.get('cancelled') is True:
        if not old:raise PlanError('Мероприятие не найдено')
        con.execute('UPDATE planner_events SET cancelled=1 WHERE id=?',(event_id,))
        fields={'cancelled':1}
    else:
        title=str(data.get('title') or '').strip();day=str(data.get('day') or '')
        start=str(data.get('start_time') or '');end=str(data.get('end_time') or '')
        if not 1<=len(title)<=200:raise PlanError('Укажите название до 200 символов')
        try:
            date.fromisoformat(day)
            for value in (start,end):
                if value and (len(value)!=5 or datetime.strptime(value,'%H:%M').strftime('%H:%M')!=value):raise ValueError()
        except ValueError:raise PlanError('Проверьте дату и время')
        if bool(start)!=bool(end) or (start and start>=end):raise PlanError('Укажите начало и окончание мероприятия в пределах одного дня')
        branch=data.get('branch_id') or None
        if branch and not con.execute('SELECT 1 FROM branches WHERE id=?',(branch,)).fetchone():raise PlanError('Филиал не найден')
        fields=dict(title=title,day=day,start_time=start,end_time=end,branch_id=branch,participants=str(data.get('participants',old['participants'] if old else '') or '').strip(),note=str(data.get('note') or '').strip())
        if len(fields['participants'])>1000 or len(fields['note'])>2000:raise PlanError('Слишком длинное описание')
    stamp=datetime.now(ZoneInfo('Europe/Moscow')).isoformat(timespec='seconds')
    if old:
        con.execute('UPDATE planner_events SET '+','.join(k+'=?' for k in fields)+',updated_by=?,updated_at=? WHERE id=?',(*fields.values(),user['id'],stamp,event_id))
    else:
        fields.update(created_by=user['id'],updated_by=user['id'],created_at=stamp,updated_at=stamp)
        event_id=con.execute('INSERT INTO planner_events('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',tuple(fields.values())).lastrowid
    con.execute('INSERT INTO planner_event_history(event_id,actor_id,at,action,details) VALUES(?,?,?,?,?)',(event_id,user['id'],stamp,'update' if old else 'create',json.dumps(fields,ensure_ascii=False)))
    return {'id':event_id}


def listing(con,user,day):
    if not allowed(user):return []
    return [dict(r) for r in con.execute('''SELECT e.*,b.name AS branch_name,u.name AS author FROM planner_events e
      LEFT JOIN branches b ON b.id=e.branch_id LEFT JOIN users u ON u.id=e.created_by
      WHERE e.day=? AND e.cancelled=0 ORDER BY e.start_time,e.id''',(day,))]
