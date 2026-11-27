"""Service-month revenue recorded on CRM cards, attributed to the sale's group."""
from collections import Counter

from . import referrals, revenuecarry


def rows(con, month):
    """Yield (student, group, is_membership, recognized_amount)."""
    covered = Counter()
    bonuses = referrals.used(con, month)
    for sid, gid, kind, price, sub_id in con.execute("""SELECT s.id,COALESCE(e.group_id,s.group_id),
            x.kind,x.price,x.id FROM subscriptions x JOIN students s ON s.id=x.student_id
            LEFT JOIN enrollments e ON e.id=x.enrollment_id WHERE x.month=? AND x.status='paid'""", (month,)):
        member = kind != "single"
        covered[sid, gid, member, price] += 1
        yield sid, gid, member, price - bonuses.get(sub_id, 0)
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='erp_history_subscriptions'").fetchone():
        for sid, gid, price in con.execute("""SELECT h.student_id,h.group_id,h.revenue_amount
                FROM erp_history_subscriptions h WHERE h.month=? AND h.revenue_amount IS NOT NULL
                AND NOT EXISTS (SELECT 1 FROM subscriptions x JOIN students s ON s.id=x.student_id
                LEFT JOIN enrollments e ON e.id=x.enrollment_id WHERE x.student_id=h.student_id
                AND x.month=h.month AND x.status!='cancelled' AND COALESCE(e.group_id,s.group_id)=h.group_id)""", (month,)):
            covered[sid, gid, True, price] += 1
            yield sid, gid, True, price
    for sid, gid, kind, price in con.execute("""SELECT h.student_id,a.group_id,h.kind,h.amount
            FROM erp_revenue_history h LEFT JOIN erp_revenue_groups a USING(row_key)
            WHERE h.month=? ORDER BY a.group_id IS NULL,h.row_key""", (month,)):
        member = kind.startswith("Абонемент")
        key = sid, gid, member, price
        if gid is None and not covered[key]:
            matches = [k for k, count in covered.items() if count and (k[0], k[2], k[3]) == (sid, member, price)]
            if len(matches) == 1:
                key = matches[0]
        if covered[key]:
            covered[key] -= 1
        else:
            yield sid, gid, member, price
    for row in revenuecarry.adjustments(con, month):
        yield row["student_id"], row["group_id"], False, row["amount"]


def months(con):
    return [r[0] for r in con.execute("""SELECT month FROM subscriptions UNION SELECT month FROM erp_revenue_history
        UNION SELECT month FROM erp_history_subscriptions ORDER BY month""")]
