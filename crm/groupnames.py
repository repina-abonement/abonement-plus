"""Group titles generated from branch, weekdays, per-day time and explicit age."""
from . import logic
WD=['Пн','Вт','Ср','Чт','Пт','Сб','Вс']


def label(con, group):
    g=dict(group)
    branch=con.execute('SELECT name FROM branches WHERE id=?',(g['branch_id'],)).fetchone()
    days=logic.parse_weekdays(g.get('weekdays') or '')
    times={logic.group_time(g,d) for d in days}
    when=('/'.join(WD[d] for d in days)+' '+(next(iter(times)) or 'Время не указано')) if len(times)==1 else '/'.join(WD[d]+' '+(logic.group_time(g,d) or 'Время не указано') for d in days)
    source=g.get('source_name') or g.get('name') or ''
    kind='Отработка' if g.get('is_makeup') else 'ОФП' if g.get('lesson_kind')=='ofp' or 'офп' in source.lower() else 'Сборная' if g.get('program_id') or 'сборная' in source.lower() else 'Папы' if 'пап' in source.lower() else ''
    return ' · '.join(x for x in [(branch[0] if branch else 'Филиал не указан'),when or 'Дни не указаны',g.get('age') or 'Возраст не указан',kind] if x)


def sync(con,branch_id=None):
    q='SELECT * FROM groups'+(' WHERE branch_id=?' if branch_id else '')
    for row in con.execute(q,(branch_id,) if branch_id else ()).fetchall():
        name=label(con,row)
        if row['name']!=name:con.execute('UPDATE groups SET name=? WHERE id=?',(name,row['id']))


def init(con):
    cols={r[1] for r in con.execute('PRAGMA table_info(groups)')}
    if 'source_name' not in cols:
        con.execute("ALTER TABLE groups ADD COLUMN source_name TEXT NOT NULL DEFAULT ''")
        con.execute('UPDATE groups SET source_name=name')
    sync(con)
