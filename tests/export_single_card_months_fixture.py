"""Build private, synthetic API payloads for the card browser regression.

Usage: python -m tests.export_single_card_months_fixture /tmp/card-fixtures.json
Only a temporary SQLite fixture is opened; no bank, production CRM or messages.
"""
import json
from pathlib import Path
import sys

from crm import clientattendance, services
from tests.test_erp_single_recovery import SingleAggregateRecoveryTest


def export(path):
    case = SingleAggregateRecoveryTest()
    case.setUp()
    try:
        meta = services.meta(case.con)
        payloads = {}

        def capture(name):
            payloads[name] = {'detail': services.student_detail(case.con, case.sid),
                'calendar': {month: clientattendance.calendar(case.con, case.sid, month)
                             for month in ('2026-11', '2026-12')}}

        capture('monthly')
        case.apply_recovery()
        capture('recovered')
        enrollment = case.con.execute('SELECT id FROM enrollments WHERE student_id=? AND group_id=?',
                                      (case.sid, case.gid)).fetchone()[0]
        monthly = case.con.execute('''INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,kind,
            lessons,per_lesson,price,status,created_at) VALUES (?,?,'2026-11','fixed','month',4,2250,9000,
            'invoiced','2026-11-20T12:00')''', (case.sid, enrollment)).lastrowid
        capture('monthly_debt')
        case.con.execute('''INSERT INTO payments(student_id,amount,method,paid_at,comment,user_name,confirmed,
            subscription_id,created_at) VALUES (?,2000,'card','2026-11-20T12:00',
            'Синтетическая частичная оплата','Синтетический администратор',1,?,'2026-11-20T12:00')''',
            (case.sid, monthly))
        capture('partial')
        case.con.execute("UPDATE subscriptions SET status='paid' WHERE id=?", (monthly,))
        capture('stale_paid')
        result = {'meta': meta, 'cases': payloads, 'student_id': case.sid,
                  'aggregate_id': case.aggregate_id, 'monthly_id': monthly}
        Path(path).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    finally:
        case.doCleanups()


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Pass the output JSON path')
    export(sys.argv[1])
