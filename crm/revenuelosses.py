"""Revenue and reductions by the month in which the subscription is valid."""
import json
from collections import defaultdict
from datetime import date

from . import referrals


def report(con, month, branch_id=None, group_id=None):
    date.fromisoformat(month + '-01')
    scope = " AND g.id=?" if group_id else " AND g.branch_id=?" if branch_id else ""
    args = (month, group_id or branch_id) if scope else (month,)
    if group_id:
        group = con.execute("SELECT branch_id FROM groups WHERE id=?", (group_id,)).fetchone()
        if not group or (branch_id and group[0] != branch_id):
            raise ValueError("Выберите группу из указанного филиала")
    bonuses = referrals.used(con, month)
    discounts = defaultdict(int)
    totals = dict(actual=0, bonuses=0, incomplete=0, recalculations=0, surcharge=0,
                  subscriptions=0, singles=0, incomplete_unknown=0)
    holders = set()
    rows = con.execute("""SELECT x.* FROM subscriptions x JOIN students s ON s.id=x.student_id
        LEFT JOIN enrollments e ON e.id=x.enrollment_id
        LEFT JOIN groups g ON g.id=COALESCE(e.group_id,s.group_id)
        WHERE x.month=? AND x.status='paid'""" + scope, args)
    for row in rows:
        holders.add(row['student_id'])
        bonus = bonuses.get(row['id'], 0)
        totals['actual'] += row['price'] - bonus
        totals['bonuses'] += bonus
        totals['surcharge'] += row['late_fee'] or 0
        totals['recalculations'] += (row['credit_lessons'] or 0) * row['per_lesson']
        if row['discount_amount']:
            discounts[row['discount_name'] or 'Скидка без названия'] += row['discount_amount']
        if row['kind'] == 'single':
            totals['singles'] += 1
            continue
        totals['subscriptions'] += 1
        if row['incomplete_value'] is not None:
            totals['incomplete'] += row['incomplete_value']
        else:
            dates = json.loads(row['excluded_dates'] or '[]')
            if not row['program_selection']:
                totals['incomplete'] += len(dates) * row['per_lesson']
            totals['incomplete_unknown'] += 1

    historical = con.execute("""SELECT h.student_id,h.revenue_amount
        FROM erp_history_subscriptions h JOIN groups g ON g.id=h.group_id
        WHERE h.month=? AND h.revenue_amount IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM subscriptions x JOIN students s ON s.id=x.student_id
            LEFT JOIN enrollments e ON e.id=x.enrollment_id
            WHERE x.student_id=h.student_id AND x.month=h.month AND x.status!='cancelled'
              AND COALESCE(e.group_id,s.group_id)=h.group_id)""" + scope, args)
    for row in historical:
        holders.add(row['student_id'])
        totals['actual'] += row['revenue_amount']
        totals['subscriptions'] += 1
        totals['incomplete_unknown'] += 1

    totals['discounts'] = sum(discounts.values())
    totals['losses'] = sum(totals[k] for k in ('discounts', 'bonuses', 'recalculations', 'incomplete'))
    totals['potential'] = totals['actual'] + totals['losses'] - totals['surcharge']
    totals['customers'] = len(holders)
    purchases = totals['subscriptions'] + totals['singles']
    totals['average'] = round(totals['actual'] / purchases, 2) if purchases else 0
    return {'month': month, 'totals': totals, 'discounts': sorted(discounts.items())}
