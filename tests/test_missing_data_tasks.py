"""Независимые задачи запроса данных, без изменения клиентской цепочки."""
import tempfile
from pathlib import Path
from datetime import date
from crm import db, tasks, schedule, services as sv, smart
smart.REQUIRE = False


def main():
    con = db.connect(str(Path(tempfile.mkdtemp()) / 'test.sqlite3')); db.init_db(con)
    user = {'id': 1, 'name': 'Админ', 'role': 'operator'}
    branch = schedule.save_branch(con, {'name': 'Тест'})
    group = schedule.save_group(con, {'name': 'Группа', 'branch_id': branch, 'weekdays': [1], 'time': '16:00'})
    def kid(name, month, status='paid', start='', end=''):
        sid = sv.create_student(con, {'name': name, 'parent_name': 'Мама', 'phone': '+790011122' + str(con.execute('SELECT COUNT(*) FROM students').fetchone()[0]), 'funnel': 'members', 'group_id': group}, user)
        con.execute("INSERT INTO subscriptions(student_id,month,tariff,lessons,per_lesson,price,status,created_at,valid_from,valid_until) VALUES (?,?,'fixed',8,1000,8000,?,'2026-11-01',?,?)", (sid, month, status, start, end))
        return sid
    sid = kid('Ребёнок', '2026-11')
    expired = kid('Истёкший', '2026-10')
    future = kid('Будущий', '2026-12')
    cancelled = kid('Отменённый', '2026-11', 'cancelled')
    crossing = kid('Переходящий', '2026-10', start='2026-10-20', end='2026-11-20')
    other = tasks.add_task(con, 'manual', sid, '2026-11-13', title='Другая задача')
    con.execute('UPDATE students SET priority=1 WHERE id=?', (sid,))
    assert tasks.request_missing_data(con, date(2026,11,13)) == 2
    rows = con.execute("SELECT * FROM tasks WHERE kind='request_data'").fetchall()
    assert {r['student_id'] for r in rows} == {sid, crossing}
    task = next(r for r in rows if r['student_id']==sid)
    assert 'Дата рождения' in task['note'] and 'Почта' in task['note']
    assert tasks._erp_col(dict(task)) == ('request_data', 'Запрос данных')
    assert tasks.request_missing_data(con, date(2026,11,13)) == 0
    before = dict(con.execute('SELECT * FROM tasks WHERE id=?', (other,)).fetchone())
    con.execute("UPDATE students SET name='Матвей Тестовый' WHERE id=?", (sid,))
    tasks.request_missing_data(con, date(2026,11,13))
    assert 'Имя и фамилия ребёнка' not in con.execute('SELECT note FROM tasks WHERE id=?',(task['id'],)).fetchone()[0]
    try:
        tasks.complete(con, task['id'], user)
        raise AssertionError('Частично заполненная карточка позволила закрыть задачу')
    except tasks.comms.CommsError:
        pass
    assert con.execute('SELECT status FROM tasks WHERE id=?',(task['id'],)).fetchone()[0] == 'open'
    con.execute("UPDATE students SET birth='2021-01-01' WHERE id=?", (sid,))
    con.execute("UPDATE parents SET name='Анна',email='test@example.org' WHERE family_id=(SELECT family_id FROM students WHERE id=?)",(sid,))
    tasks.request_missing_data(con, date(2026,11,13))
    assert con.execute('SELECT status FROM tasks WHERE id=?',(task['id'],)).fetchone()[0] == 'open'
    tasks.complete(con, task['id'], user)
    assert tasks.request_missing_data(con, date(2026,11,14)) == 0
    assert dict(con.execute('SELECT * FROM tasks WHERE id=?', (other,)).fetchone()) == before
    assert con.execute('SELECT priority FROM students WHERE id=?',(sid,)).fetchone()[0] == 1
    assert con.execute('SELECT COUNT(*) FROM tasks WHERE student_id=?',(sid,)).fetchone()[0] == 2
    print('PASS active/expired/future/cancelled/cross-month, deduplication, isolated completion, no recreation or chain')

if __name__ == '__main__': main()
