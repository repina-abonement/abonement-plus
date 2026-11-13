"""Продажа планирует общую проверку продления по последнему месяцу."""
import tempfile
from pathlib import Path
from datetime import datetime
from crm import db, services as sv, tasks, lifecycle


def main():
    with tempfile.TemporaryDirectory() as tmp:
        con = db.connect(str(Path(tmp) / 'test.sqlite3'))
        db.init_db(con)
        who = {'id': 1, 'name': 'Тест', 'role': 'admin'}
        branch = con.execute("INSERT INTO branches(name) VALUES ('Тест')").lastrowid
        group = con.execute("INSERT INTO groups(branch_id,name,weekdays) VALUES (?, 'Тест', '1,4')", (branch,)).lastrowid
        sid = sv.create_student(con, {'name': 'Иван Примеров', 'birth': '2020-01-01', 'parent_name': 'Анна', 'phone': '+79000000001',
            'email': 'test@example.invalid', 'group_id': group, 'funnel': 'members'}, who)
        con.execute("UPDATE parents SET email='test@example.invalid'")
        old = tasks.add_task(con, 'why_not_bought', sid, '2026-10-01T23:59')
        erp_payment = tasks.add_task(con, 'manual', sid, '2026-10-01T23:59', title='ПРОВЕРИТЬ ОПЛАТУ АБОНЕМЕНТА', source='erp')
        manual = tasks.add_task(con, 'manual', sid, '2026-10-01T23:59', title='Позвонить по другому вопросу')
        for month, expected in [('2026-10','2026-10'), ('2026-12','2026-12'), ('2026-11','2026-12')]:
            sv.create_invoice(con, sid, month, who)
            rows = con.execute("SELECT * FROM tasks WHERE student_id=? AND kind='renew' AND status='open'", (sid,)).fetchall()
            assert len(rows) == 1
            t = rows[0]
            assert t['title'] == 'Проверить продление' and t['due_at'] == expected + '-20T23:59'
            assert t['assignee_id'] is None
        assert con.execute('SELECT status FROM tasks WHERE id=?', (old,)).fetchone()[0] == 'cancelled'
        assert con.execute('SELECT status FROM tasks WHERE id=?', (erp_payment,)).fetchone()[0] == 'cancelled'
        assert con.execute('SELECT status FROM tasks WHERE id=?', (manual,)).fetchone()[0] == 'open'
        con.execute("INSERT INTO subscriptions(student_id,month,tariff,lessons,per_lesson,price,status,created_at) VALUES (?, '2027-02','fixed',8,1,8,'cancelled','2026-11-13')", (sid,))
        con.execute("INSERT INTO subscriptions(student_id,month,tariff,kind,lessons,per_lesson,price,status,created_at) VALUES (?, '2027-03','single','single',1,1,1,'paid','2026-11-13')", (sid,))
        tasks.schedule_renewal(con, sid)
        assert con.execute('SELECT due_at FROM tasks WHERE id=?', (t['id'],)).fetchone()[0] == '2026-12-20T23:59'
        lifecycle.tick(con, datetime(2026,11,21,12))
        assert con.execute("SELECT COUNT(*) FROM tasks WHERE student_id=? AND kind='renew' AND status='open'", (sid,)).fetchone()[0] == 1
        print('ok: chronological and out-of-order sales, cancellation, singles, shared task, no lifecycle duplicates')


if __name__ == '__main__':
    main()
