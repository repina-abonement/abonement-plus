"""Cash received by actual payment date, independent of service-month revenue."""
from collections import defaultdict
from datetime import date
from . import money


def report(con, month):
    start = date.fromisoformat(month + '-01')
    end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    def empty():
        return dict(received=0, returned=0, net=0, pending_received=0, pending_returned=0)
    totals = empty()
    days, methods = defaultdict(empty), defaultdict(empty)
    # EXISTS avoids multiplying payments with multiple provider links.
    rows = con.execute('''SELECT p.*, CASE WHEN EXISTS (
        SELECT 1 FROM pay_links l WHERE l.payment_id=p.id AND l.pk_payment_id!=''
        ) THEN 'paykeeper' ELSE p.method END AS channel
        FROM payments p WHERE p.paid_at>=? AND p.paid_at<? AND p.cancelled=0
        AND p.method NOT IN ('unknown','opening_balance','balance','refund_pending')
        ORDER BY p.paid_at,p.id''', (start.isoformat(), end.isoformat()))
    for row in rows:
        amount = row['amount']
        if not amount:
            continue
        key = ('received' if amount > 0 else 'returned') if row['confirmed'] else (
            'pending_received' if amount > 0 else 'pending_returned')
        for bucket in (totals, days[row['paid_at'][:10]], methods[row['channel']]):
            bucket[key] += abs(amount)
            bucket['net'] = bucket['received'] - bucket['returned']
    return dict(month=month, totals=totals,
                days=[dict(date=k, **v) for k,v in sorted(days.items())],
                methods=[dict(method=k, label=money.METHODS.get(k,k), **v) for k,v in sorted(methods.items())])
