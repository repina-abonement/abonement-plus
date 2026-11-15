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


def share_victoria_existing(con):
    """Открытые личные задачи Виктории: перенос, не копирование; история закрытых сохраняется."""
    import re,json
    from . import services as sv
    key='offline_planner_victoria_open_v1'
    if comms.get(con,key):return
    people=[dict(r) for r in con.execute("SELECT * FROM users WHERE active=1 AND role='operator' AND is_offline_admin=1")]
    def find(required):
        return [r for r in people if required.issubset(set(re.findall(r'[а-яё]+',r['name'].casefold())))]
    source=find({'виктория','сафронова'});peer=find({'татьяна','данейкина'})
    if len(source)!=1 or len(peer)!=1 or source[0]['id']==peer[0]['id']:return
    uid=source[0]['id'];peer_id=peer[0]['id'];pool=pool_id(con)
    con.execute('SAVEPOINT offline_victoria_tasks')
    try:
        series=[r[0] for r in con.execute("SELECT id FROM recurring WHERE created_by_id=? AND assignee_id IN (?,?) AND active=1",(uid,uid,peer_id))]
        rows=[dict(r) for r in con.execute("""SELECT id,assignee_id,recur_id FROM tasks WHERE kind='plan' AND status='open'
                   AND student_id IS NULL AND family_id IS NULL AND created_by_id=? AND assignee_id IN (?,?)""",(uid,uid,peer_id))]
        # Exceptions from these same series must move as well, even if edited by another authorized person.
        if series:
            placeholders=','.join('?' for _ in series)
            extra=[dict(r) for r in con.execute(f"SELECT id,assignee_id,recur_id FROM tasks WHERE kind='plan' AND status='open' AND student_id IS NULL AND family_id IS NULL AND recur_id IN ({placeholders}) AND assignee_id IN (?,?)",(*series,uid,peer_id))]
            rows=list({r['id']:r for r in rows+extra}.values())
        for row in rows:con.execute('UPDATE tasks SET assignee_id=? WHERE id=?',(pool,row['id']))
        for rid in series:con.execute('UPDATE recurring SET assignee_id=? WHERE id=?',(pool,rid))
        evidence={'source_user':uid,'peer_user':peer_id,'pool':pool,'tasks':rows,'recurring':series}
        sv.audit(con,None,'planner.offline_share_victoria',json.dumps(evidence,ensure_ascii=False))
        comms.put(con,key,json.dumps({'tasks':len(rows),'recurring':len(series),'source':uid}))
        con.execute('RELEASE offline_victoria_tasks')
    except BaseException:
        con.execute('ROLLBACK TO offline_victoria_tasks');con.execute('RELEASE offline_victoria_tasks');raise
