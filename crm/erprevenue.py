"""Owner-approved historical revenue snapshot, separated from live billing."""
import csv
import hashlib
import io
import json
from collections import defaultdict

DIGEST = "2dee63dec450785aa1b282866b1bcfd43b2f80343df3c37e4839c0ffccb4db0c"


# Owner-confirmed full refund of this historical service period. Billing is separate.
REFUNDED_SALE = ("120486", "Абонемент (стандарт /8)", "07.11.2026", "29.11.2026")
REFUNDED_MONTH = "2026-11"
REFUNDED_AMOUNT = 9450


def recognized_amount(eid, kind, start, end, amount):
    if (eid, kind, start, end) == REFUNDED_SALE and amount == REFUNDED_AMOUNT:
        return 0
    return amount


def correct_refunded_snapshot(con):
    """Correct the existing snapshot and its two cached totals exactly once."""
    con.execute("SAVEPOINT refunded_snapshot")
    try:
        rows = con.execute("""SELECT row_key, amount FROM erp_revenue_history
            WHERE erp_id=? AND kind=? AND start=? AND end=? AND month=?""",
            (*REFUNDED_SALE, REFUNDED_MONTH)).fetchall()
        if len(rows) == 1 and rows[0][1] == REFUNDED_AMOUNT:
            con.execute("UPDATE erp_revenue_history SET amount=0 WHERE row_key=?", (rows[0][0],))
            con.execute("UPDATE erp_revenue_months SET amount=amount-? WHERE month=?",
                        (REFUNDED_AMOUNT, REFUNDED_MONTH))
            con.execute("""UPDATE erp_subscription_months
                SET subscription_revenue=subscription_revenue-? WHERE month=?""",
                (REFUNDED_AMOUNT, REFUNDED_MONTH))
    except Exception:
        con.execute("ROLLBACK TO refunded_snapshot")
        raise
    finally:
        con.execute("RELEASE refunded_snapshot")


def transfer(con, content, token=None):
    digest = hashlib.sha256(content.encode()).hexdigest() if isinstance(content, str) else None
    if digest == "4927fc8048ae020e82fbd66f7cb37971f038ef9e3cd8bf97db70d7d22c9629b5":
        return assign_november_groups(con, content, token)
    if digest != DIGEST:
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
        value = recognized_amount(eid, kind, start, end, int(value))
        totals[month] += value
        key = file + "/" + row
        candidates = links[eid]
        if kind.startswith("Абонемент"):
            identity = ("crm", next(iter(candidates))) if len(candidates) == 1 else ("erp", eid)
            holders[month].add(identity)
            subscriptions[month] += 1
            subscription_revenue[month] += value
        if len(candidates) != 1 or (key in existing and existing[key] not in candidates):
            skipped.add(eid)
            missing += 1
            continue
        if key in existing:
            already += 1
            continue
        sid, = candidates
        additions.append((key, sid, eid, month, kind, sale, start, end, value))
        amount += value
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


def assign_november_groups(con, content, token):
    from . import erpimport, erppeople

    if token is not None and not con.in_transaction:
        con.execute("BEGIN IMMEDIATE")
    norm = lambda name: erpimport._norm(name).removeprefix("стоп ").strip()
    additions, issues = [], []
    already = amount = 0
    for row in csv.DictReader(io.StringIO(content)):
        key, eid, branch, label = (row[k] for k in ("Строка", "ERP ID", "Филиал", "Группа"))
        sale = con.execute("""SELECT r.*, a.group_id FROM erp_revenue_history r
            LEFT JOIN erp_revenue_groups a USING(row_key) WHERE r.row_key=? AND r.erp_id=?
            AND r.month='2026-11' AND r.kind=?""",
            (key, eid, row["Вид"])).fetchone()
        if not sale or sale["amount"] != recognized_amount(
                eid, sale["kind"], sale["start"], sale["end"], int(row["Выручка"])):
            issues.append(f"{eid} · история продажи не найдена")
            continue
        branches = erppeople._matching_branches(con, branch)
        candidates = set()
        if len(branches) == 1:
            saved = erpimport._group_id(con, branch, label)
            for g in con.execute("SELECT id,name,source_name FROM groups WHERE branch_id=? AND is_makeup=0",
                                 (branches[0]["id"],)):
                if (g["id"] == saved if saved else norm(label) in (norm(g["name"]), norm(g["source_name"] or ""))):
                    candidates.add(g["id"])
        if len(candidates) != 1 or (sale["group_id"] and sale["group_id"] not in candidates):
            issues.append(f"{eid} · {branch} · {label}")
            continue
        if sale["group_id"]:
            already += 1
            continue
        additions.append((key, candidates.pop(), sale["student_id"]))
        amount += sale["amount"]
    signature = hashlib.sha256(json.dumps(["groups", additions]).encode()).hexdigest()
    if token is not None:
        if token != signature:
            raise ValueError("Данные изменились. Повторите проверку групп")
        con.executemany("INSERT INTO erp_revenue_groups(row_key,group_id) VALUES (?,?)",
                        [(key, gid) for key, gid, _ in additions])
    return dict(count=len(additions), amount=amount, already=already, missing=len(issues),
                ids=issues, token=signature, groups=True)
