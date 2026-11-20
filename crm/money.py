"""Деньги: реестр платежей, баланс ученика (долг и депозит), разовые занятия, скидки.

Платёж — единственный источник правды. Статусы абонементов («оплачен» / «ждёт оплаты»)
пересчитываются из суммы платежей ученика: деньги идут на абонементы по порядку, от старых к новым.
Лишнее остаётся депозитом и само закрывает следующий счёт. Платёж не удаляется, а отменяется.
"""
from __future__ import annotations

import math

from datetime import date, datetime

from . import logic

METHODS = {"cash": "Наличные", "paykeeper": "По ссылке PayKeeper", "paykeeper_manual": "По ссылке PayKeeper (внесено вручную)",
           "card_branch": "Картой на филиале", "sber_qr_branch": "По QR Сбера на филиале",
           "tochka_qr_link": "По QR/ссылке Точки от администратора",
           "card": "Картой (канал не уточнён)", "sbp": "СБП (банк не уточнён)",
           "online": "Онлайн-оплата (канал не уточнён)", "transfer": "Перевод",
           "unknown": "Перенесено", "balance": "Перенос с баланса", "refund": "Возврат", "refund_pending": "Зарезервировано для возврата"}
MANUAL_METHODS = ('cash','paykeeper_manual','card_branch','sber_qr_branch','tochka_qr_link','transfer')

INTERNAL = ("unknown", "balance", "refund", "refund_pending")  # их не выбирают при приёме оплаты: ставит сама CRM

SCHEMA = """
CREATE TABLE IF NOT EXISTS manual_payment_requests(request_key TEXT PRIMARY KEY,signature TEXT NOT NULL,payment_id INTEGER,created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    amount INTEGER NOT NULL,
    method TEXT NOT NULL DEFAULT 'cash',
    paid_at TEXT NOT NULL,
    comment TEXT NOT NULL DEFAULT '',
    user_name TEXT NOT NULL DEFAULT '',
    confirmed INTEGER NOT NULL DEFAULT 0,
    confirmed_by TEXT NOT NULL DEFAULT '',
    confirmed_at TEXT,
    subscription_id INTEGER,
    cancelled INTEGER NOT NULL DEFAULT 0,
    cancelled_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pay_student ON payments(student_id);
CREATE INDEX IF NOT EXISTS idx_pay_date ON payments(paid_at);

CREATE TABLE IF NOT EXISTS cancelled_service_charges (
        subscription_id INTEGER PRIMARY KEY REFERENCES subscriptions(id), student_id INTEGER NOT NULL,
        month TEXT NOT NULL, amount INTEGER NOT NULL, lessons INTEGER NOT NULL,
        created_at TEXT NOT NULL, cutoff TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS discounts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    percent INTEGER NOT NULL DEFAULT 0,
    rule TEXT NOT NULL DEFAULT 'manual',      -- manual | first_on_trial_day
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat(timespec="minutes")


def init(con):
    con.executescript(SCHEMA)
    from . import paymentallocation
    paymentallocation.init(con)
    from . import balancecredit
    balancecredit.init(con)
    pcols = {r[1] for r in con.execute("PRAGMA table_info(payments)")}
    if "batch" not in pcols:  # части одного платежа (за семью) и переносы между детьми
        con.execute("ALTER TABLE payments ADD COLUMN batch TEXT NOT NULL DEFAULT ''")
    cols = {r[1] for r in con.execute("PRAGMA table_info(subscriptions)")}
    for col in ("kind TEXT NOT NULL DEFAULT 'month'", "discount_name TEXT NOT NULL DEFAULT ''",
                "discount_amount INTEGER NOT NULL DEFAULT 0", "ledger_managed INTEGER NOT NULL DEFAULT 0"):
        if col.split()[0] not in cols:
            con.execute(f"ALTER TABLE subscriptions ADD COLUMN {col}")
    if not con.execute("SELECT 1 FROM discounts WHERE rule='first_on_trial_day'").fetchone():
        con.execute("INSERT INTO discounts(name, percent, rule, created_at) VALUES (?,?,?,?)",
                    ("Первый абонемент в день пробного", 20, "first_on_trial_day", _now()))
    con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES ('price_single', '2500')")
    # цены, которые назвала Юлия 27.10.2026: разовое 2500 ₽, многодетным 10% (ставится вручную)
    con.execute("UPDATE settings SET value='2500' WHERE key='price_single' AND value=''")
    if not con.execute("SELECT 1 FROM discounts WHERE name LIKE 'Многодет%'").fetchone():
        con.execute("INSERT INTO discounts(name, percent, rule, created_at) VALUES (?,?,?,?)", ("Многодетная семья", 10, "manual", _now()))
    if not con.execute("SELECT 1 FROM discounts WHERE rule='sibling'").fetchone():
        con.execute("INSERT INTO discounts(name, percent, rule, created_at) VALUES (?,?,?,?)",
                    ("Второй и следующий ребёнок в семье", 20, "sibling", _now()))
    # оплаченные раньше абонементы без записи о платеже — переносим в реестр, чтобы баланс сходился
    # ERP recovery imports the real payment register separately. Its historical
    # subscription status must never manufacture a second payment on restart.
    erp_guard = 'AND s.erp_full_managed=0' if 'erp_full_managed' in {r[1] for r in con.execute('PRAGMA table_info(subscriptions)')} else ''
    # A real receipt can be unbound: absence of subscription_id does not prove
    # absence of money. Bootstrap only completely income-free legacy accounts.
    # Materialize before inserting, so all eligible legacy invoices of one
    # account are seeded together rather than the first row hiding the rest.
    legacy = list(con.execute(f"""SELECT s.student_id, s.price,
                          COALESCE(s.paid_at, s.created_at) AS received_at, s.id FROM subscriptions s
                   WHERE s.status='paid' AND s.price > 0 AND s.ledger_managed=0
                     {erp_guard}
                     AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.student_id=s.student_id
                                     AND p.cancelled=0 AND p.amount>0)
                     AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.subscription_id = s.id)""")
    )
    con.executemany("""INSERT INTO payments(student_id,amount,method,paid_at,comment,user_name,
                        confirmed,subscription_id,created_at)
                       SELECT ?,?,'unknown',?,'Оплата до реестра платежей','Система',1,?,?
                       WHERE NOT EXISTS (SELECT 1 FROM payments WHERE subscription_id=?)""",
                    [(r['student_id'], r['price'], r['received_at'], r['id'], r['received_at'], r['id'])
                     for r in legacy])


# ---------------- баланс ----------------

def reconcile(con, sid: int) -> None:
    """Раскладывает все платежи ученика по его абонементам, от старых к новым."""
    _reconcile(con, sid)
    from . import lifecycle
    lifecycle.on_payment(con, sid)  # первая оплата: продажа засчитана
    if con.execute("SELECT 1 FROM subscriptions WHERE student_id=? AND status='paid' AND paid_at >= date('now', '-1 day')", (sid,)).fetchone():
        from . import nodeadline
        nodeadline.auto_exit(con, sid, "оплатил")


def obligations(con,sid):
    """Активные абонементы и сохранённая стоимость прошедшего периода отменённых."""
    return [dict(r) for r in con.execute("""SELECT id,month,price,status,paid_at,0 AS retained,id AS ordering FROM subscriptions
        WHERE student_id=? AND status!='cancelled'
        UNION ALL SELECT -subscription_id,month,amount,'paid',NULL,1,subscription_id
        FROM cancelled_service_charges WHERE student_id=? ORDER BY month,ordering""",(sid,sid))]


def _reconcile(con, sid: int) -> None:
    from . import paymentallocation
    allocated = paymentallocation.allocation(con, sid)
    if allocated['conflicts']:
        raise ValueError('Распределение оплат требует сверки: ' + '; '.join(allocated['conflicts']))
    for sub in allocated['obligations']:
        if allocated['covered'][sub['id']] >= sub["price"]:
            if not sub["retained"] and sub["status"] != "paid":
                con.execute("UPDATE subscriptions SET status='paid', paid_at=? WHERE id=?", (_now(), sub["id"]))
        else:
            if not sub["retained"] and sub["status"] == "paid":
                con.execute("UPDATE subscriptions SET status='invoiced', paid_at=NULL WHERE id=?", (sub["id"],))


def balance(con, sid: int, today: date | None = None) -> dict:
    """Долг — неоплаченное за прошлые и текущий месяц. К оплате — счета на будущие месяцы. Депозит — переплата."""
    today = today or date.today()
    cur = logic.month_of(today)
    from . import paymentallocation
    allocated = paymentallocation.allocation(con, sid)
    debt, upcoming = 0, 0
    for sub in allocated['obligations']:
        covered = allocated['covered'][sub['id']]
        left = sub["price"] - covered
        if sub["month"] <= cur:
            debt += left
        else:
            upcoming += left
    return {"paid_total": allocated['paid_total'], "debt": debt, "upcoming": upcoming,
            "deposit": allocated['free_deposit'], "covered": allocated['covered'],
            "restricted_deposit": allocated['restricted_deposit'],
            "allocation_conflicts": allocated['conflicts'],
            "cash_free_deposit": allocated.get('cash_free_deposit', allocated['free_deposit']),
            "fund_free_deposit": allocated.get('fund_free_deposit', 0),
            "fund_sources": allocated.get('fund_sources', {}),
            "balance_labels": allocated.get('fund_labels', []),
            "active_snapshot": allocated.get('active_snapshot', False),
            "accounting_basis": 'reviewed_snapshot' if allocated.get('active_snapshot') else 'ledger'}


# ---------------- платежи ----------------

def add_payment(con, sid: int, amount: int, method: str, user: dict, *, subscription_id: int | None = None,
                comment: str = "", paid_at: str | None = None, invoice_parts: list | None = None,
                allow_cancelled_targets: bool = False) -> int:
    amount = int(amount)
    if amount <= 0:
        raise ValueError("Сумма должна быть больше нуля")
    if method not in METHODS or method in INTERNAL:
        raise ValueError("Выберите способ оплаты")
    if method=='paykeeper' and user.get('id')!=0:
        raise ValueError('Оплата PayKeeper зачисляется по исходному платежу автоматически; не создавайте её повторно вручную')
    if allow_cancelled_targets is not False and not (
            allow_cancelled_targets is True and method=='paykeeper' and user.get('id')==0):
        raise ValueError('Назначение отменённого счёта сохраняется только у исходной оплаты PayKeeper')
    if invoice_parts is not None:
        if not isinstance(invoice_parts, list):
            raise ValueError('Нужен список счетов исходной оплаты')
        seen=set(); attributed=0
        for part in invoice_parts:
            if (not isinstance(part,dict) or type(part.get('subscription_id')) is not int
                    or type(part.get('amount')) is not int or part['amount']<0
                    or (part['amount']==0 and not allow_cancelled_targets)
                    or part['subscription_id'] in seen):
                raise ValueError('Проверьте точное распределение оплаты по счетам')
            sub=con.execute('SELECT student_id,status FROM subscriptions WHERE id=?',
                            (part['subscription_id'],)).fetchone()
            if not sub or sub['student_id']!=sid or (sub['status']=='cancelled' and not allow_cancelled_targets):
                raise ValueError('Счёт исходной оплаты не найден в этой карточке')
            seen.add(part['subscription_id']);attributed+=part['amount']
        if attributed>amount:
            raise ValueError('Зачёты по счетам превышают полученную оплату')
    confirmed = 1 if user.get("role") == "admin" or method in ("online", "sbp", "paykeeper", "sber_qr_branch", "tochka_qr_link") else 0
    pid = con.execute("""INSERT INTO payments(student_id, amount, method, paid_at, comment, user_name, confirmed,
                         confirmed_by, confirmed_at, subscription_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                      (sid, amount, method, paid_at or _now(), comment.strip(), user.get("name", ""), confirmed,
                       user.get("name", "") if confirmed else "", _now() if confirmed else None, subscription_id,
                       _now())).lastrowid
    if invoice_parts:
        from . import balancecredit
        for part in invoice_parts:
            balancecredit.target_payment(con,pid,part['subscription_id'],part['amount'],
                                         allow_cancelled=allow_cancelled_targets,
                                         allow_zero=allow_cancelled_targets)
    reconcile(con, sid)
    con.execute("INSERT INTO history(student_id, ts, user_name, kind, text) VALUES (?,?,?,?,?)",
                (sid, _now(), user.get("name", ""), "payment",
                 f"Принята оплата {amount} ₽, {METHODS[method].lower()}" + (f": {comment.strip()}" if comment.strip() else "")))
    return pid


def _row(con, sid: int, amount: int, method: str, user: dict, comment: str, batch: str = "") -> int:
    """Служебная строка платежа (перенос, возврат): может быть отрицательной, подтверждена сразу."""
    pid = con.execute("""INSERT INTO payments(student_id, amount, method, paid_at, comment, user_name, confirmed, confirmed_by,
                         confirmed_at, created_at, batch) VALUES (?,?,?,?,?,?,1,?,?,?,?)""",
                      (sid, amount, method, _now(), comment, user.get("name", ""), user.get("name", ""), _now(), _now(), batch)).lastrowid
    reconcile(con, sid)
    return pid


def _family(con, sid: int) -> list[int]:
    f = con.execute("SELECT family_id FROM students WHERE id=?", (sid,)).fetchone()
    return [r[0] for r in con.execute("SELECT id FROM students WHERE family_id=? AND archived=0 ORDER BY id", (f[0],))] if f else []


def pay_family(con, sid: int, amount: int, method: str, user: dict, comment: str = "") -> dict:
    """Один платёж на несколько счетов семьи (СБП по QR, наличные): закрывает неоплаченные счета всех детей семьи
    от старых к новым, остаток уходит на баланс ребёнка, из карточки которого принимали. Приход в реестре один раз:
    части одного платежа связаны общим номером batch и в сумме дают ровно внесённую сумму."""
    amount = int(amount)
    if amount <= 0:
        raise ValueError("Сумма должна быть больше нуля")
    if method not in METHODS or method in INTERNAL:
        raise ValueError("Выберите способ оплаты")
    import secrets
    batch = "fam-" + secrets.token_hex(4)
    left, parts, invoice_parts = amount, {}, {}
    for x in con.execute(f"""SELECT x.id, x.student_id, x.price, x.month FROM subscriptions x WHERE x.status='invoiced'
                             AND x.student_id IN ({','.join('?' * len(_family(con, sid)))}) ORDER BY x.month, x.id""", _family(con, sid)).fetchall():
        need = x["price"] - balance(con, x["student_id"])["covered"].get(x["id"], 0)
        take = min(left, max(0, need))
        if take:
            parts[x["student_id"]] = parts.get(x["student_id"], 0) + take
            invoice_parts.setdefault(x['student_id'],[]).append({'subscription_id':x['id'],'amount':take})
            left -= take
        if not left:
            break
    if left:
        parts[sid] = parts.get(sid, 0) + left  # остаток на баланс
    names = dict(con.execute(f"SELECT id, name FROM students WHERE id IN ({','.join('?' * len(parts))})", list(parts)).fetchall())
    note = f"Платёж за семью {amount} ₽: " + ", ".join(f"{names[k]} {v} ₽" for k, v in parts.items())
    for k, v in parts.items():
        pid = add_payment(con, k, v, method, user, comment=(comment.strip() + ". " if comment.strip() else "") + note,
                          invoice_parts=invoice_parts.get(k))
        con.execute("UPDATE payments SET batch=? WHERE id=?", (batch, pid))
    return {"batch": batch, "parts": [{"student_id": k, "name": names[k], "amount": v} for k, v in parts.items()]}


def move_balance(con, from_sid: int, to_sid: int, amount: int, user: dict) -> None:
    """Перенос с баланса (переплаты) одного ребёнка на другого в той же семье. Новых денег не появляется."""
    amount = int(amount)
    if from_sid == to_sid or to_sid not in _family(con, from_sid):
        raise ValueError("Переносить можно только между детьми одной семьи")
    if amount <= 0 or amount > balance(con, from_sid)["deposit"]:
        raise ValueError("Сумма должна быть не больше переплаты на балансе")
    if amount>balance(con,from_sid).get('cash_free_deposit',0):
        raise ValueError('Для этих средств доступен зачёт в счёт. Перенос требует проверки исходной оплаты')
    import secrets
    batch = "mv-" + secrets.token_hex(4)
    names = dict(con.execute("SELECT id, name FROM students WHERE id IN (?,?)", (from_sid, to_sid)).fetchall())
    _row(con, from_sid, -amount, "balance", user, f"Перенос на баланс: {names[to_sid]}", batch)
    _row(con, to_sid, amount, "balance", user, f"Перенос с баланса: {names[from_sid]}", batch)
    for s, t in ((from_sid, f"Перенесено {amount} ₽ с баланса на {names[to_sid]}"), (to_sid, f"Пришло {amount} ₽ с баланса {names[from_sid]}")):
        con.execute("INSERT INTO history(student_id, ts, user_name, kind, text) VALUES (?,?,?,?,?)", (s, _now(), user.get("name", ""), "payment", t))


def refund(con, sid: int, amount: int, user: dict, comment: str) -> None:
    """Возврат родителю из переплаты. Отдельная операция: в реестре минусом, нужен чек возврата."""
    amount = int(amount)
    if con.execute("SELECT 1 FROM payments p LEFT JOIN pay_links l ON l.payment_id=p.id WHERE p.student_id=? AND p.cancelled=0 AND (p.method IN ('online','paykeeper') OR l.pk_payment_id!='')", (sid,)).fetchone():
        raise ValueError("Онлайн-оплаты возвращаются только по исходному платежу через PayKeeper: используйте отмену абонемента с проверкой возврата и чека")
    if not (comment or "").strip():
        raise ValueError("Напишите, почему возврат и как вернули деньги")
    if amount <= 0 or amount > balance(con, sid)["deposit"]:
        raise ValueError("Вернуть можно не больше переплаты на балансе")
    if amount>balance(con,sid).get('cash_free_deposit',0):
        raise ValueError('Возврат этих средств требует проверки исходной оплаты. Запись возврата не создана')
    _row(con, sid, -amount, "refund", user, comment.strip())
    con.execute("INSERT INTO history(student_id, ts, user_name, kind, text) VALUES (?,?,?,?,?)",
                (sid, _now(), user.get("name", ""), "payment", f"Возврат {amount} ₽: {comment.strip()}. Пробейте чек возврата"))


def ledger(con, sid: int) -> list[dict]:
    """Лента баланса: поступления, переносы, возвраты и абонементы по времени, с остатком после каждой строки."""
    months = dict(con.execute("SELECT id, month FROM subscriptions WHERE student_id=?", (sid,)).fetchall())
    ev = [{"at": p["paid_at"], "kind": "pay", "ord": (months.get(p["subscription_id"], "9999"), 1, p["id"]), "text": f"{METHODS.get(payment_method(con,p), p['method'])}" + (f": {p['comment']}" if p["comment"] else ""),
           "amount": p["amount"], "cancelled": bool(p["cancelled"])}
          for p in con.execute("SELECT * FROM payments WHERE student_id=? ORDER BY paid_at, id", (sid,))]
    for x in con.execute("""SELECT x.*, g.name AS gname FROM subscriptions x LEFT JOIN enrollments e ON e.id=x.enrollment_id
                            LEFT JOIN groups g ON g.id=e.group_id WHERE x.student_id=? ORDER BY x.created_at, x.id""", (sid,)):
        label = "Разовое занятие" if x["kind"] == "single" else f"Абонемент {logic.month_label(x['month']).lower()}" + (f", {x['gname']}" if x["gname"] else "")
        ev.append({"at": x["created_at"], "kind": "sub", "ord": (x["month"], 0, x["id"]), "text": label + (" (отменён)" if x["status"] == "cancelled" else ""),
                   "amount": -x["price"], "cancelled": x["status"] == "cancelled"})
    for charge in con.execute('SELECT * FROM cancelled_service_charges WHERE student_id=?',(sid,)):
        ev.append({'at':charge['created_at'],'kind':'retained','ord':(charge['month'],0,charge['subscription_id']),
                   'text':f"Стоимость прошедшего периода до {charge['cutoff']} по отменённому абонементу #{charge['subscription_id']}",
                   'amount':-charge['amount'],'cancelled':False})
    ev.sort(key=lambda e: ((e["at"] or "")[:16], e.pop("ord")))  # в одну минуту: сначала счёт, потом оплата по нему
    rest = 0
    for e in ev:
        if not e["cancelled"]:
            rest += e["amount"]
        e["rest"] = rest
    return ev[::-1]


def pay_subscription(con, sub_id: int, method: str, user: dict, amount: int | None = None, paid_at: str | None = None) -> int:
    from . import pricing, program
    on=date.fromisoformat(paid_at[:10]) if paid_at else None
    program.reprice(con, sub_id,on=on)  # фактическая дата оплаты
    pricing.reprice(con, sub_id,on=on)  # обычные группы: тоже (+5% и без скидки после 25 числа)
    sub = con.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
    if not sub or sub["status"] == "cancelled":
        raise ValueError("Счёт не найден или отменён")
    left = sub["price"] - balance(con, sub["student_id"])["covered"].get(sub_id, 0)
    if left <= 0:
        raise ValueError("Этот счёт уже оплачен")
    payment_id = add_payment(con, sub["student_id"], amount or left, method, user, subscription_id=sub_id,paid_at=paid_at,
                       comment=f"Абонемент на {logic.month_label(sub['month'])}" if sub["kind"] == "month" else "Разовое занятие")

    if paid_at and con.execute("SELECT status FROM subscriptions WHERE id=?",(sub_id,)).fetchone()[0]=='paid':
        latest=con.execute("SELECT MAX(paid_at) FROM payments WHERE subscription_id=? AND cancelled=0 AND confirmed=1",(sub_id,)).fetchone()[0]
        con.execute("UPDATE subscriptions SET paid_at=? WHERE id=?",(latest or paid_at,sub_id))
    return payment_id


def cancel_payment(con, pid: int, user: dict) -> None:
    p = con.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
    if not p or p["cancelled"]:
        raise ValueError("Платёж не найден или уже отменён")
    if con.execute("SELECT 1 FROM pay_links WHERE payment_id=? AND status='paid'",(pid,)).fetchone():
        raise ValueError("Оплата PayKeeper не отменяется как запись: оформите возврат через исходный платёж с фискальным чеком")
    if con.execute("SELECT 1 FROM subscription_refunds WHERE reserve_id=? OR payment_id=?",(pid,pid)).fetchone():
        raise ValueError("Эта строка связана с возвратом абонемента. Нельзя отменять резерв или подтверждённый возврат как обычный платёж")
    source_tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('erp_payment_allocations','erp_refund_allocations')")}
    if (('erp_payment_allocations' in source_tables and con.execute('SELECT 1 FROM erp_payment_allocations WHERE payment_id=?', (pid,)).fetchone())
            or ('erp_refund_allocations' in source_tables and con.execute('SELECT 1 FROM erp_refund_allocations WHERE refund_payment_id=? OR original_payment_id=?', (pid, pid)).fetchone())):
        raise ValueError('Эта операция связана с подтверждёнными зачётами или историческим возвратом. Для исправления нужна отдельная сверка источника')
    con.execute("UPDATE payments SET cancelled=1, cancelled_by=? WHERE id=?", (user.get("name", ""), pid))
    reconcile(con, p["student_id"])
    con.execute("INSERT INTO history(student_id, ts, user_name, kind, text) VALUES (?,?,?,?,?)",
                (p["student_id"], _now(), user.get("name", ""), "payment", f"Отменён платёж {p['amount']} ₽ от {p['paid_at'][:10]}"))


def confirm_payment(con, pid: int, user: dict) -> None:
    con.execute("UPDATE payments SET confirmed=1, confirmed_by=?, confirmed_at=? WHERE id=? AND cancelled=0",
                (user.get("name", ""), _now(), pid))


def payment_method(con, payment):
    if con.execute("SELECT 1 FROM pay_links WHERE payment_id=? AND pk_payment_id!=''",(payment['id'],)).fetchone():
        return 'paykeeper'
    return payment['method']


def payments_for_student(con,sid):
    result=[dict(r) for r in con.execute('SELECT * FROM payments WHERE student_id=? ORDER BY paid_at DESC,id DESC LIMIT 30',(sid,))]
    for p in result:
        p['display_method']=payment_method(con,p)
        p['method_label']=METHODS.get(p['display_method'],p['method'])
    return result


def registry(con, start: str, end: str, method: str = "", only_unconfirmed: bool = False) -> dict:
    where = ["p.paid_at BETWEEN ? AND ?", "p.method NOT IN ('balance','refund_pending')"]  # перенос между детьми семьи не приход
    args = [start, end + "T23:59"]
    if method:
        if method=='paykeeper':
            where.append("(p.method IN ('paykeeper','paykeeper_manual') OR EXISTS (SELECT 1 FROM pay_links l WHERE l.payment_id=p.id AND l.pk_payment_id!=''))")
        else:
            where.append("p.method=? AND NOT EXISTS (SELECT 1 FROM pay_links l WHERE l.payment_id=p.id AND l.pk_payment_id!='')")
            args.append(method)
    if only_unconfirmed:
        where.append("p.confirmed=0 AND p.cancelled=0")
    rows = [dict(r) for r in con.execute(
        f"""SELECT p.*, s.name AS student, f.name AS family, g.name AS group_name
            FROM payments p JOIN students s ON s.id=p.student_id JOIN families f ON f.id=s.family_id
            LEFT JOIN groups g ON g.id=s.group_id WHERE {' AND '.join(where)} ORDER BY p.paid_at DESC, p.id DESC""", args)]
    from . import fiscal,paymentcontrol,paymentworker,payoutcheck
    control_from = fiscal.control_from(con)
    totals = {}
    for r in rows:
        r["display_method"] = payment_method(con,r)
        r["method_label"] = METHODS.get(r["display_method"], r["method"])
        r["control"] = (paymentcontrol.state(con,r) if r["paid_at"] >= control_from else
                        {"status":"excluded","label":"Сверка за этот период отложена","issues":[]})
        if not r["cancelled"]:
            totals[r["method_label"]] = totals.get(r["method_label"], 0) + r["amount"]
    unconfirmed = con.execute("SELECT COUNT(*), COALESCE(SUM(amount),0) FROM payments WHERE confirmed=0 AND cancelled=0").fetchone()
    return {"rows": rows, "totals": totals, "total": sum(totals.values()),"control_summary":paymentcontrol.summarize(rows),
            "control_jobs":paymentworker.states(con),
            "payout_batches":payoutcheck.view(con,start,end),
            "unconfirmed": {"count": unconfirmed[0], "sum": unconfirmed[1]}, "methods": METHODS}


def debtors(con) -> list[dict]:
    out = []
    for (sid,) in con.execute("SELECT DISTINCT student_id FROM subscriptions WHERE status='invoiced'").fetchall():
        b = balance(con, sid)
        if b["debt"] > 0:
            s = con.execute("SELECT id, name FROM students WHERE id=?", (sid,)).fetchone()
            out.append({"id": s["id"], "name": s["name"], "debt": b["debt"]})
    return sorted(out, key=lambda x: -x["debt"])


# ---------------- скидки ----------------

def discounts(con, active_only: bool = True) -> list[dict]:
    q = "SELECT * FROM discounts" + (" WHERE active=1" if active_only else "") + " ORDER BY active DESC, id"
    return [dict(r) for r in con.execute(q)]


def _sibling_applies(con, sid: int) -> bool:
    """Скидка на второго и следующих детей: в семье есть другой ребёнок, который занимается и пришёл раньше."""
    s = con.execute("SELECT family_id FROM students WHERE id=?", (sid,)).fetchone()
    if not s:
        return False
    kids = con.execute("""SELECT id FROM students WHERE family_id=? AND archived=0 AND funnel='members' AND stage!='left'
                          ORDER BY COALESCE(converted_at, created_at), id""", (s["family_id"],)).fetchall()
    ids = [k[0] for k in kids]
    others = [i for i in ids if i != sid]
    return bool(others) and (sid not in ids or ids.index(sid) > 0)


def large_family_discount(discount):
    return discount.get('rule')=='large_family' or 'многодет' in str(discount.get('name') or '').casefold().replace('ё','е')


def choose_discount(con,sid,on=None,selected_id=None,user=None,candidates=None):
    """One discount: an explicit permitted choice wins; otherwise existing maximum-percent priority."""
    from .services import ApiError
    if selected_id=='none':return None
    if selected_id not in (None,''):
        if isinstance(selected_id,bool) or not str(selected_id).isdigit():raise ApiError('Выберите одну действующую скидку')
        selected=con.execute('SELECT * FROM discounts WHERE id=? AND active=1',(int(selected_id),)).fetchone()
        if not selected:raise ApiError('Выберите действующую скидку')
        selected=dict(selected)
        if large_family_discount(selected) and (user or {}).get('role') not in ('admin','operator'):
            raise ApiError('Скидку «Многодетная семья» может выбрать только администратор',403)
        return selected
    automatic=auto_discounts(con,sid,on) if candidates is None else candidates
    return best_discount([d for d in automatic if not large_family_discount(d)])


def auto_discounts(con, sid: int, today: date | None = None) -> list[dict]:
    """Скидки, которые ставятся сами: первый абонемент в день пробного; второй и следующие дети в семье."""
    today = today or date.today()
    out = []
    d = next((r for r in con.execute("SELECT * FROM discounts WHERE rule='first_on_trial_day' AND active=1 ORDER BY id") if not large_family_discount(dict(r))),None)
    if d:
        s = con.execute("SELECT trial_at FROM students WHERE id=?", (sid,)).fetchone()
        had = con.execute("SELECT 1 FROM subscriptions WHERE student_id=? AND status != 'cancelled'", (sid,)).fetchone()
        if s and s["trial_at"] and s["trial_at"][:10] == today.isoformat() and not had:
            out.append(dict(d))
    d = next((r for r in con.execute("SELECT * FROM discounts WHERE rule='sibling' AND active=1 ORDER BY id") if not large_family_discount(dict(r))),None)
    if d and _sibling_applies(con, sid):
        out.append(dict(d))
    return out


def best_discount(cands: list) -> dict | None:
    """Скидки не суммируются: из подходящих берём самую большую."""
    cands = [c for c in cands if c]
    return max(cands, key=lambda c: int(c.get("percent") or 0)) if cands else None


def auto_discount(con, sid: int, today: date | None = None) -> dict | None:
    return best_discount(auto_discounts(con, sid, today))


def apply_discount(total: int, d: dict | None) -> tuple[int, int]:
    if not d or not d.get("percent"):
        return total, 0
    off = math.floor(total * int(d["percent"]) / 100 + 1e-9)  # цена после скидки — вверх до рубля
    return total - off, off


def save_discount(con, data: dict) -> None:
    name = (data.get("name") or "").strip()
    pct = int(data.get("percent") or 0)
    if not name or not 0 < pct <= 100:
        raise ValueError("Нужно название и скидка от 1 до 100%")
    if data.get("id"):
        con.execute("UPDATE discounts SET name=?, percent=? WHERE id=?", (name, pct, int(data["id"])))
    else:
        con.execute("INSERT INTO discounts(name, percent, created_at) VALUES (?,?,?)", (name, pct, _now()))


def archive_discount(con, did: int, active: bool) -> None:
    con.execute("UPDATE discounts SET active=? WHERE id=?", (1 if active else 0, did))


# ---------------- разовое занятие ----------------

def sell_single(con, sid: int, user: dict, group_id: int | None = None) -> int:
    from . import program
    if program.only_program(con, sid):
        raise ValueError("В сборной разовых занятий нет: абонемент только на месяц")
    price = (con.execute("SELECT value FROM settings WHERE key='price_single'").fetchone() or [""])[0]
    if not str(price).strip().isdigit():
        raise ValueError("Укажите цену разового занятия в Настройках → Цены и правила")
    s = con.execute("SELECT group_id FROM students WHERE id=?", (sid,)).fetchone()
    sub = con.execute("""INSERT INTO subscriptions(student_id, enrollment_id, month, tariff, lessons, per_lesson, price, status,
                         created_at, kind) VALUES (?,?,?,?,?,?,?,'invoiced',?, 'single')""",
                      (sid, None, logic.month_of(date.today()), "single", 1, int(price), int(price), _now())).lastrowid
    from . import saleinfo
    saleinfo.record(con,sub,user)
    reconcile(con, sid)
    con.execute("INSERT INTO history(student_id, ts, user_name, kind, text) VALUES (?,?,?,?,?)",
                (sid, _now(), user.get("name", ""), "payment", f"Выставлено разовое занятие: {price} ₽"))
    return sub


def _payment_receipt_info(con,payment):
    """Confirmed fiscal evidence only, matched by provider payment ID, never by amount/date."""
    link=con.execute("SELECT pk_payment_id FROM pay_links WHERE payment_id=? AND pk_payment_id!='' ORDER BY id DESC LIMIT 1",(payment['id'],)).fetchone()
    if not link:
        return {'status':'unknown','label':'Чек не подтверждён'}
    provider_id=link['pk_payment_id']
    rows=list(con.execute("SELECT * FROM fx_receipts WHERE source='paykeeper' AND pk_payment=? AND op=1 ORDER BY id DESC",(provider_id,)))
    from . import fiscal
    receipt=next((r for r in rows if r['pk_status']=='success'
                  and all(r[k] for k in ('fiscal_fn','fiscal_fd','fiscal_fp'))
                  and r['amount']==fiscal.kop(payment['amount'])),None)
    if receipt:return {'status':'confirmed','label':'Чек пробит','receipt_id':receipt['ext_id'],'receipt_at':receipt['ts']}
    primary=next((r for r in rows if r['pk_status']=='fiscal_details'
                  and all(r[k] for k in ('fiscal_fn','fiscal_fd','fiscal_fp'))
                  and r['amount']==fiscal.kop(payment['amount'])),None)
    if primary:
        from . import reconciliation
        if reconciliation.payment_state(con,payment)['ofd']['status']=='confirmed':
            return {'status':'confirmed','label':'Чек найден в ОФД','receipt_id':primary['fiscal_fd'],'receipt_at':primary['ts']}
        return {'status':'fiscal_details','label':'Реквизиты чека получены','receipt_id':primary['fiscal_fd'],'receipt_at':primary['ts']}
    if any(r['pk_status'] in ('failed','timeout') for r in rows):return {'status':'failed','label':'Ошибка фискализации'}
    if any(r['pk_status'] in ('created','sending','request_sent') for r in rows):return {'status':'pending','label':'Чек формируется'}
    missing=con.execute("SELECT 1 FROM fx_payments WHERE source='paykeeper' AND ext_id=? AND status='missing'",(provider_id,)).fetchone()
    return {'status':'missing' if missing else 'unknown','label':'Чек не найден' if missing else 'Чек не подтверждён'}


def payment_receipt_info(con,payment):
    import json
    result=_payment_receipt_info(con,payment)
    check=con.execute('SELECT result FROM fiscal_payment_checks WHERE payment_id=?',(payment['id'],)).fetchone()
    if check:
        latest=json.loads(check['result'])
        if result['status']=='confirmed':result.update({k:latest[k] for k in ('checked_at','source_available','note') if k in latest})
        elif latest.get('status')!='confirmed':result.update(latest)
        else:result.update({k:latest[k] for k in ('checked_at','source_available') if k in latest})
    return result


def subscription_payment_details(con,sid):
    """Explain the shared allocation, including proved historical sources."""
    from . import reconciliation, paymentallocation
    allocated = paymentallocation.allocation(con, sid)
    payments={p['id']:dict(p) for p in con.execute('SELECT * FROM payments WHERE student_id=? AND cancelled=0',(sid,))}
    details={}
    for sub in allocated['obligations']:
        rows=[]
        for source in allocated['sources'][sub['id']]:
            p=payments[source['payment_id']]
            rows.append({'payment_id':p['id'],'amount':source['amount'],'payment_total':p['amount'],
                    'method':payment_method(con,p),'method_label':METHODS.get(payment_method(con,p),p['method']),
                    'paid_at':p['paid_at'],'created_at':p['created_at'],'receipt':payment_receipt_info(con,p),
                    'reconciliation':reconciliation.payment_state(con,p)})
        for source in allocated.get('fund_sources',{}).get(sub['id'],[]):
            rows.append({'record_kind':'balance_credit','payment_id':None,'fund_id':source['fund_id'],
                'amount':source['amount'],'payment_total':source['amount'],'method':'balance',
                'method_label':'Зачтено с баланса','fund_label':source['label'],
                'paid_at':None,'created_at':None,
                'receipt':{'status':'unverified','label':'Чек исходной оплаты требует сверки'},
                'reconciliation':None})
        for source in allocated.get('historical_sources',{}).get(sub['id'],[]):
            if source['amount']:
                rows.append({'record_kind':'historical_settlement','payment_id':None,
                    'amount':source['amount'],'payment_total':source['amount'],'method':'balance',
                    'method_label':'Оплата сохранена по сверке ERP',
                    'fund_label':'Исходные поступления сохранены в истории денег',
                    'paid_at':None,'created_at':None,
                    'receipt':{'status':'unverified','label':'Чек исходной оплаты требует сверки'},
                    'reconciliation':None})
        if not sub['retained']:details[sub['id']]=rows
    return details
