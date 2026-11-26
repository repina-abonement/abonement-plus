"""CRM navigation history and private access bound to one account."""
from . import perms
from .services import ApiError, now_iso

VIEWS = frozenset(
    'today sales inbox planner members nodeadline journal schedule coaches team posts payments fiscal '
    'merch spend staffexpenses dashboard revenue-losses search docs settings'.split()
)
TABS = frozenset(('feed', 'abon', 'visits', 'info'))
KINDS = frozenset(('view', 'card', 'copy', 'copy-selection'))
COPY_TYPES = frozenset(('text', 'payment-link', 'client-list', 'message', 'login', 'backup', 'phone'))


def init(con):
    con.execute("""CREATE TABLE IF NOT EXISTS activity_owner (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        user_id INTEGER NOT NULL REFERENCES users(id))""")
    con.execute("""CREATE TABLE IF NOT EXISTS crm_activity (
        id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), ts TEXT NOT NULL,
        kind TEXT NOT NULL, section TEXT NOT NULL, tab TEXT NOT NULL, student_id INTEGER,
        copy_type TEXT NOT NULL)""")
    con.execute("CREATE INDEX IF NOT EXISTS crm_activity_user_order ON crm_activity(user_id,id)")
    if not con.execute("SELECT 1 FROM activity_owner").fetchone():
        candidates = con.execute("""SELECT id,name FROM users WHERE role='admin' AND active=1""").fetchall()
        matches = [row['id'] for row in candidates if ' '.join(row['name'].casefold().split()) == 'юлия репина']
        if len(matches) == 1:
            con.execute("INSERT INTO activity_owner VALUES (1,?)", (matches[0],))


def may_read(con, user):
    if user['role'] != 'admin':
        return False
    owner = con.execute("SELECT user_id FROM activity_owner WHERE singleton=1").fetchone()
    return bool(owner and owner[0] == user['id'])


def record(con, user, data):
    kind = data.get('kind')
    section = data.get('section')
    if not isinstance(kind, str) or kind not in KINDS or not isinstance(section, str) or section not in VIEWS:
        raise ApiError('Неизвестное действие')
    permission = {'nodeadline': 'sales', 'revenue-losses': 'dashboard'}.get(section, section)
    if not perms.can(user, permission):
        raise ApiError('Недостаточно прав', 403)
    tab = data.get('tab')
    tab = tab if isinstance(tab, str) and tab in TABS else ''
    student_id = data.get('student_id')
    if type(student_id) is not int or not 0 < student_id < 2**63 or not perms.can(user, 'search', 'view') or not con.execute(
            "SELECT 1 FROM students WHERE id=?", (student_id,)).fetchone():
        student_id = None
    if kind == 'card' and not student_id:
        raise ApiError('Карточка не найдена', 404)
    copy_type = data.get('copy_type')
    copy_type = copy_type if isinstance(copy_type, str) and copy_type in COPY_TYPES else ''
    con.execute("""INSERT INTO crm_activity(user_id,ts,kind,section,tab,student_id,copy_type)
        VALUES (?,?,?,?,?,?,?)""", (user['id'], now_iso(), kind, section, tab, student_id, copy_type))
    return {'ok': True}


def history(con, user, q):
    if not may_read(con, user):
        raise ApiError('Недостаточно прав', 403)
    try:
        before = int(q.get('before') or 0)
        user_id = int(q.get('user_id') or 0)
    except ValueError:
        raise ApiError('Некорректный фильтр') from None
    rows = con.execute("""SELECT a.*,u.name FROM crm_activity a JOIN users u ON u.id=a.user_id
        WHERE (?=0 OR a.id<?) AND (?=0 OR a.user_id=?) ORDER BY a.id DESC LIMIT 101""",
        (before, before, user_id, user_id)).fetchall()
    people = con.execute("""SELECT id,name FROM users
        WHERE active=1 OR id IN (SELECT user_id FROM crm_activity) ORDER BY name,id""")
    return {'rows': [dict(row) for row in rows[:100]], 'users': [dict(row) for row in people],
            'next': rows[99]['id'] if len(rows) > 100 else None}
