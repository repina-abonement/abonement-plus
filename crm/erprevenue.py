"""Owner-approved historical revenue snapshot, separated from live billing."""
import csv
import hashlib
import io
import json
from collections import defaultdict

DIGEST = "2dee63dec450785aa1b282866b1bcfd43b2f80343df3c37e4839c0ffccb4db0c"


def transfer(con, content, token=None):
    if not isinstance(content, str) or hashlib.sha256(content.encode()).hexdigest() != DIGEST:
        raise ValueError("Выберите подготовленный файл «Абонементы ERP 2022–2026.csv»")
    if token is not None and not con.in_transaction:
        con.execute("BEGIN IMMEDIATE")
    links = defaultdict(set)
    for sid, eid in con.execute("""SELECT id,erp_id FROM students WHERE erp_id!=''
            UNION SELECT s.id,i.source_id FROM erp_full_identities i JOIN students s
            ON s.id=i.student_id WHERE i.namespace='student'"""):
        links[str(eid)].add(sid)
    existing = dict(con.execute("SELECT row_key,student_id FROM erp_revenue_history"))
    totals = defaultdict(int)
    holders = defaultdict(set)
    subscriptions = defaultdict(int)
    subscription_revenue = defaultdict(int)
    additions = []
    skipped = set()
    missing = already = amount = 0
    reader = csv.reader(io.StringIO(content))
    next(reader)
    for file, row, eid, kind, sale, start, end, value in reader:
        month = start[6:] + "-" + start[3:5]
        totals[month] += int(value)
        key = file + "/" + row
        candidates = links[eid]
        if kind.startswith("Абонемент"):
            identity = ("crm", next(iter(candidates))) if len(candidates) == 1 else ("erp", eid)
            holders[month].add(identity)
            subscriptions[month] += 1
            subscription_revenue[month] += int(value)
        if len(candidates) != 1 or (key in existing and existing[key] not in candidates):
            skipped.add(eid)
            missing += 1
            continue
        if key in existing:
            already += 1
            continue
        sid, = candidates
        additions.append((key, sid, eid, month, kind, sale, start, end, int(value)))
        amount += int(value)
    signature = hashlib.sha256(json.dumps(additions).encode()).hexdigest()
    update_totals = dict(con.execute("SELECT month,amount FROM erp_revenue_months")) != totals
    metrics = [(m, len(holders[m]), subscriptions[m], subscription_revenue[m]) for m in sorted(totals)]
    saved = [tuple(row) for row in con.execute("SELECT * FROM erp_subscription_months ORDER BY month")]
    update_totals = update_totals or metrics != saved
    if token is not None:
        if signature != token:
            raise ValueError("Сопоставление изменилось. Повторите проверку")
        if update_totals:
            con.executemany("INSERT OR REPLACE INTO erp_subscription_months VALUES (?,?,?,?)", metrics)
            con.executemany("INSERT OR REPLACE INTO erp_revenue_months(month,amount) VALUES (?,?)", totals.items())
        con.executemany("""INSERT INTO erp_revenue_history
            (row_key,student_id,erp_id,month,kind,sale,start,end,amount)
            VALUES (?,?,?,?,?,?,?,?,?)""", additions)
    return dict(update_totals=update_totals, token=signature, count=len(additions), amount=amount, already=already,
                missing=missing, ids=sorted(skipped, key=int))
