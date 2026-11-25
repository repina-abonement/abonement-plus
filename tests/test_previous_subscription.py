"""Ручная история абонемента для перерасчёта, без финансовой операции."""
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from crm import db, schedule, services as sv, smart


def main():
    with TemporaryDirectory() as directory, patch.object(smart, 'REQUIRE', False), patch.object(sv, 'today', return_value=date(2026, 11, 25)):
        con = db.connect(str(Path(directory) / 'crm.sqlite'))
        db.init_db(con)
        actor = {'id': 1, 'name': 'Администратор', 'role': 'operator'}
        branch = schedule.save_branch(con, {'name': 'Тест'})
        group = schedule.save_group(con, {'name': 'Вт и Пт', 'branch_id': branch, 'weekdays': [1, 4]})
        sid = sv.create_student(con, {'name': 'Ребёнок Тестов', 'parent_name': 'Анна',
                                'phone': '+79000001125', 'funnel': 'members', 'group_id': group}, actor)
        data = {'month': '2026-11', 'group_id': group, 'tariff': 'flex', 'lessons': 8, 'price': 12400}
        for attempt in range(2):
            sv.add_previous_subscription(con, sid, data, actor)
        history = con.execute('SELECT manual_tariff, revenue_amount FROM erp_history_subscriptions').fetchall()
        assert [tuple(row) for row in history] == [('flex', 12400)]
        assert not con.execute('SELECT 1 FROM subscriptions WHERE student_id=?', (sid,)).fetchone()
        assert not con.execute('SELECT 1 FROM payments WHERE student_id=?', (sid,)).fetchone()
        day = '2026-11-10'
        journal = sv.journal(con, group, day, paid_only=True, include_unpaid_marks=False)
        assert any(row['id'] == sid and row['tag'] == 'historical' for row in journal['rows'])
        sv.mark_attendance(con, sid, group, day, 'absent', actor)
        sv.mark_attendance(con, sid, group, '2026-11-13', 'present', actor)
        assert not con.execute("SELECT 1 FROM tasks WHERE auto_key LIKE 'life:unpaid_visit:%'").fetchone()
        options = {'tariff': 'flex', 'credit_requested': True, 'group_id': group}
        quote = sv.quote_for(con, sid, '2026-12', **options)
        assert quote['credit_absence_dates'] == [day] and quote['credit'] > 0, quote
        sv.create_invoice(con, sid, '2026-12', actor, **options)
        assert con.execute('SELECT resolved FROM attendance WHERE student_id=? AND date=?', (sid, day)).fetchone()[0] == 'credit'
        assert not con.execute('SELECT 1 FROM payments WHERE student_id=?', (sid,)).fetchone()
        assert sv.quote_for(con, sid, '2026-12', **options)['credit_available'] == 0
        # Особые условия группы могут ограничивать число переносов, но не меняют тариф.
        con.execute("INSERT INTO group_terms VALUES (?,'2026-11',1600,2)", (group,))
        for index, manual in enumerate((False, True)):
            fixed_child = sv.create_student(con, {'name': 'Другой Ребёнок', 'parent_name': 'Анна',
                'phone': f'+7900000113{index}', 'funnel': 'members', 'group_id': group}, actor)
            if manual:
                sv.add_previous_subscription(con, fixed_child, dict(data, tariff='fixed'), actor)
            else:
                enrollment = sv.enrollments_of(con, fixed_child)[0]['id']
                con.execute("""INSERT INTO subscriptions
                    (student_id,enrollment_id,month,tariff,lessons,per_lesson,price,status,created_at)
                    VALUES (?,?,'2026-11','fixed',8,1600,12800,'paid','2026-11-01')""",
                    (fixed_child, enrollment))
            sv.mark_attendance(con, fixed_child, group, day, 'absent', actor)
            for next_tariff in ('flex', 'fixed'):
                fixed_quote = sv.quote_for(con, fixed_child, '2026-12', tariff=next_tariff,
                                           credit_requested=True, group_id=group)
                assert fixed_quote['credit_available'] == fixed_quote['credit'] == 0
            invoice = sv.create_invoice(con, fixed_child, '2026-12', actor, **options)
            assert con.execute('SELECT credit_lessons FROM subscriptions WHERE id=?',
                               (invoice['id'],)).fetchone()[0] == 0
            assert con.execute('SELECT resolved FROM attendance WHERE student_id=? AND date=?',
                               (fixed_child, day)).fetchone()[0] is None
        con.close()


if __name__ == '__main__':
    main()
