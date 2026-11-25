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
        con.close()


if __name__ == '__main__':
    main()
