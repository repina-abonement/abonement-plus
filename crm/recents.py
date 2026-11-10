"""Per-user recently viewed client IDs; labels are read from current records."""
import time


def init(con):
    con.execute('''CREATE TABLE IF NOT EXISTS recent_clients (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
        viewed_at INTEGER NOT NULL,
        PRIMARY KEY(user_id, student_id)
    )''')


def remember(con, uid, sid):
    con.execute('''INSERT INTO recent_clients(user_id, student_id, viewed_at)
        SELECT ?,id,? FROM students WHERE id=?
        ON CONFLICT(user_id,student_id) DO UPDATE SET viewed_at=excluded.viewed_at''', (uid,time.time_ns(),sid))
    con.execute('''DELETE FROM recent_clients WHERE user_id=? AND student_id NOT IN
        (SELECT student_id FROM recent_clients WHERE user_id=? ORDER BY viewed_at DESC LIMIT 20)''',(uid,uid))


def listing(con, uid):
    return [dict(r) for r in con.execute('''SELECT s.id,s.name,s.funnel,
        (SELECT p.name FROM parents p WHERE p.family_id=s.family_id ORDER BY p.id LIMIT 1) AS parent,
        (SELECT p.phone FROM parents p WHERE p.family_id=s.family_id ORDER BY p.id LIMIT 1) AS phone
        FROM recent_clients r JOIN students s ON s.id=r.student_id
        WHERE r.user_id=? AND s.archived=0 ORDER BY r.viewed_at DESC LIMIT 8''',(uid,))]


def clear(con, uid):
    con.execute('DELETE FROM recent_clients WHERE user_id=?',(uid,))
