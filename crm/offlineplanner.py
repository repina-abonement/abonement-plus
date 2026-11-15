"""Изолированный общий Планер офлайн-администраторов, без доступа по старшинству."""
from . import comms
VIEWER_KEY='offline_planner_viewer'
POOL_LOGIN='__offline_planner__'


def init(con):
    if 'is_offline_admin' not in {r[1] for r in con.execute('PRAGMA table_info(users)')}:
        con.execute('ALTER TABLE users ADD COLUMN is_offline_admin INTEGER NOT NULL DEFAULT 0')
    if not pool_id(con):
        con.execute("INSERT INTO users(login,name,role,pw_hash,active) VALUES (?, 'Офлайн-администраторы','offline_pool','disabled',0)",(POOL_LOGIN,))


def pool_id(con):
    r=con.execute("SELECT id FROM users WHERE login=? AND role='offline_pool'",(POOL_LOGIN,)).fetchone()
    return r[0] if r else None


def member(con,user):
    r=con.execute('SELECT role,is_offline_admin,active FROM users WHERE id=?',(user['id'],)).fetchone()
    return bool(r and r['active'] and r['role']=='operator' and r['is_offline_admin'])


def can_view(con,user):
    return user['role']=='admin' or member(con,user) or comms.get(con,VIEWER_KEY)==str(user['id'])


def can_edit(con,user):
    return user['role']=='admin' or member(con,user)


def validate_viewer(con,value):
    from .planner import PlanError
    if value in (None,'',0,'0'):return ''
    try:uid=int(value)
    except (ValueError,TypeError):raise PlanError('Выберите сотрудника для просмотра общего Планера')
    if not con.execute("SELECT 1 FROM users WHERE id=? AND active=1 AND role='operator'",(uid,)).fetchone():raise PlanError('Для просмотра выберите действующего администратора')
    return str(uid)


def provision_requested(con):
    """Однократное поручение Юлии; неоднозначные имена не угадываем."""
    import re
    from . import services as sv
    key='offline_planner_named_setup_v1'
    if comms.get(con,key)=='done':return
    people=[dict(r) for r in con.execute("SELECT * FROM users WHERE active=1 AND role='operator'")]
    def matches(words):
        return [r for r in people if words.intersection(re.findall(r'[а-яё]+',r['name'].casefold()))]
    targets=[matches({'данейкина'}),matches({'сафронова'}),matches({'варвара','варя'})]
    if any(len(rows)!=1 for rows in targets) or len({rows[0]['id'] for rows in targets})!=3:return
    con.execute('SAVEPOINT offline_staff_setup')
    try:
        for rows in targets[:2]:
            uid=rows[0]['id']
            con.execute('UPDATE users SET is_offline_admin=1,is_senior=0 WHERE id=?',(uid,))
            con.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
            sv.audit(con,None,'staff.offline_admin',str(uid))
        comms.put(con,VIEWER_KEY,str(targets[2][0]['id']))
        comms.put(con,key,'done')
        sv.audit(con,None,'planner.offline_viewer',str(targets[2][0]['id']))
        con.execute('RELEASE offline_staff_setup')
    except BaseException:
        con.execute('ROLLBACK TO offline_staff_setup');con.execute('RELEASE offline_staff_setup');raise
