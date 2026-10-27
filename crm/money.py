"""Деньги: реестр платежей, баланс ученика (долг и депозит), разовые занятия, скидки.

Платёж — единственный источник правды. Статусы абонементов («оплачен» / «ждёт оплаты»)
пересчитываются из суммы платежей ученика: деньги идут на абонементы по порядку, от старых к новым.
Лишнее остаётся депозитом и само закрывает следующий счёт. Платёж не удаляется, а отменяется.
"""
from __future__ import annotations

from datetime import date, datetime

from . import logic

METHODS = {"cash": "Наличные", "card": "Картой", "sbp": "СБП", "online": "Онлайн-оплата", "transfer": "Перевод",
           "unknown": "Перенесено"}

SCHEMA = """
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
    cols = {r[1] for r in con.execute("PRAGMA table_info(subscriptions)")}
    for col in ("kind TEXT NOT NULL DEFAULT 'month'", "discount_name TEXT NOT NULL DEFAULT ''",
                "discount_amount INTEGER NOT NULL DEFAULT 0"):
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
    # оплаченные раньше абонементы без записи о платеже — переносим в реестр, чтобы баланс сходился
    con.execute("""INSERT INTO payments(student_id, amount, method, paid_at, comment, user_name, confirmed, subscription_id,
                   created_at)
                   SELECT s.student_id, s.price, 'unknown', COALESCE(s.paid_at, s.created_at), 'Оплата до реестра платежей',
                          'Система', 1, s.id, COALESCE(s.paid_at, s.created_at) FROM subscriptions s
                   WHERE s.status='paid' AND s.price > 0
                     AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.subscription_id = s.id)""")


# ---------------- баланс ----------------

def reconcile(con, sid: int) -> None:
    """Раскладывает все платежи ученика по его абонементам, от старых к новым."""
    pool = con.execute("SELECT COALESCE(SUM(amount), 0) FROM payments WHERE student_id=? AND cancelled=0", (sid,)).fetchone()[0]
    for sub in con.execute("""SELECT id, price, status, paid_at FROM subscriptions WHERE student_id=? AND status != 'cancelled'
                              ORDER BY month, id""", (sid,)).fetchall():
        if pool >= sub["price"]:
            pool -= sub["price"]
            if sub["status"] != "paid":
                con.execute("UPDATE subscriptions SET status='paid', paid_at=? WHERE id=?", (_now(), sub["id"]))
        else:
            pool = 0 if pool < 0 else pool
            if sub["status"] == "paid":
                con.execute("UPDATE subscriptions SET status='invoiced', paid_at=NULL WHERE id=?", (sub["id"],))


def balance(con, sid: int, today: date | None = None) -> dict:
    """Долг — неоплаченное за прошлые и текущий месяц. К оплате — счета на будущие месяцы. Депозит — переплата."""
    today = today or date.today()
    cur = logic.month_of(today)
    paid = con.execute("SELECT COALESCE(SUM(amount), 0) FROM payments WHERE student_id=? AND cancelled=0", (sid,)).fetchone()[0]
    pool, debt, upcoming, per_sub = paid, 0, 0, {}
    for sub in con.execute("""SELECT id, month, price FROM subscriptions WHERE student_id=? AND status != 'cancelled'
                              ORDER BY month, id""", (sid,)).fetchall():
        covered = min(pool, sub["price"])
        pool -= covered
        per_sub[sub["id"]] = covered
        left = sub["price"] - covered
        if sub["month"] <= cur:
            debt += left
        else:
            upcoming += left
    return {"paid_total": paid, "debt": debt, "upcoming": upcoming, "deposit": pool, "covered": per_sub}


# ---------------- платежи ----------------

def add_payment(con, sid: int, amount: int, method: str, user: dict, *, subscription_id: int | None = None,
                comment: str = "", paid_at: str | None = None) -> int:
    amount = int(amount)
    if amount <= 0:
        raise ValueError("Сумма должна быть больше нуля")
    if method not in METHODS or method == "unknown":
        raise ValueError("Выберите способ оплаты")
    confirmed = 1 if user.get("role") == "admin" or method in ("online", "sbp") else 0
    pid = con.execute("""INSERT INTO payments(student_id, amount, method, paid_at, comment, user_name, confirmed,
                         confirmed_by, confirmed_at, subscription_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                      (sid, amount, method, paid_at or _now(), comment.strip(), user.get("name", ""), confirmed,
                       user.get("name", "") if confirmed else "", _now() if confirmed else None, subscription_id,
                       _now())).lastrowid
    reconcile(con, sid)
    con.execute("INSERT INTO history(student_id, ts, user_name, kind, text) VALUES (?,?,?,?,?)",
                (sid, _now(), user.get("name", ""), "payment",
                 f"Принята оплата {amount} ₽, {METHODS[method].lower()}" + (f": {comment.strip()}" if comment.strip() else "")))
    return pid


def pay_subscription(con, sub_id: int, method: str, user: dict, amount: int | None = None) -> int:
    sub = con.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
    if not sub or sub["status"] == "cancelled":
        raise ValueError("Счёт не найден или отменён")
    left = sub["price"] - balance(con, sub["student_id"])["covered"].get(sub_id, 0)
    if left <= 0:
        raise ValueError("Этот счёт уже оплачен")
    return add_payment(con, sub["student_id"], amount or left, method, user, subscription_id=sub_id,
                       comment=f"Абонемент на {logic.month_label(sub['month'])}" if sub["kind"] == "month" else "Разовое занятие")


def cancel_payment(con, pid: int, user: dict) -> None:
    p = con.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
    if not p or p["cancelled"]:
        raise ValueError("Платёж не найден или уже отменён")
    con.execute("UPDATE payments SET cancelled=1, cancelled_by=? WHERE id=?", (user.get("name", ""), pid))
    reconcile(con, p["student_id"])
    con.execute("INSERT INTO history(student_id, ts, user_name, kind, text) VALUES (?,?,?,?,?)",
                (p["student_id"], _now(), user.get("name", ""), "payment", f"Отменён платёж {p['amount']} ₽ от {p['paid_at'][:10]}"))


def confirm_payment(con, pid: int, user: dict) -> None:
    con.execute("UPDATE payments SET confirmed=1, confirmed_by=?, confirmed_at=? WHERE id=? AND cancelled=0",
                (user.get("name", ""), _now(), pid))


def registry(con, start: str, end: str, method: str = "", only_unconfirmed: bool = False) -> dict:
    where = ["p.paid_at BETWEEN ? AND ?"]
    args = [start, end + "T23:59"]
    if method:
        where.append("p.method=?")
        args.append(method)
    if only_unconfirmed:
        where.append("p.confirmed=0 AND p.cancelled=0")
    rows = [dict(r) for r in con.execute(
        f"""SELECT p.*, s.name AS student, f.name AS family, g.name AS group_name
            FROM payments p JOIN students s ON s.id=p.student_id JOIN families f ON f.id=s.family_id
            LEFT JOIN groups g ON g.id=s.group_id WHERE {' AND '.join(where)} ORDER BY p.paid_at DESC, p.id DESC""", args)]
    totals = {}
    for r in rows:
        r["method_label"] = METHODS.get(r["method"], r["method"])
        if not r["cancelled"]:
            totals[r["method_label"]] = totals.get(r["method_label"], 0) + r["amount"]
    unconfirmed = con.execute("SELECT COUNT(*), COALESCE(SUM(amount),0) FROM payments WHERE confirmed=0 AND cancelled=0").fetchone()
    return {"rows": rows, "totals": totals, "total": sum(totals.values()),
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


def auto_discount(con, sid: int, today: date | None = None) -> dict | None:
    """Скидка на первый абонемент в день пробного: ни одного абонемента ещё не было, пробное сегодня."""
    today = today or date.today()
    d = con.execute("SELECT * FROM discounts WHERE rule='first_on_trial_day' AND active=1").fetchone()
    if not d:
        return None
    s = con.execute("SELECT trial_at FROM students WHERE id=?", (sid,)).fetchone()
    had = con.execute("SELECT 1 FROM subscriptions WHERE student_id=? AND status != 'cancelled'", (sid,)).fetchone()
    if s and s["trial_at"] and s["trial_at"][:10] == today.isoformat() and not had:
        return dict(d)
    return None


def apply_discount(total: int, d: dict | None) -> tuple[int, int]:
    if not d or not d.get("percent"):
        return total, 0
    off = round(total * int(d["percent"]) / 100)
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
    price = (con.execute("SELECT value FROM settings WHERE key='price_single'").fetchone() or [""])[0]
    if not str(price).strip().isdigit():
        raise ValueError("Укажите цену разового занятия в Настройках → Цены и правила")
    s = con.execute("SELECT group_id FROM students WHERE id=?", (sid,)).fetchone()
    sub = con.execute("""INSERT INTO subscriptions(student_id, enrollment_id, month, tariff, lessons, per_lesson, price, status,
                         created_at, kind) VALUES (?,?,?,?,?,?,?,'invoiced',?, 'single')""",
                      (sid, None, logic.month_of(date.today()), "single", 1, int(price), int(price), _now())).lastrowid
    reconcile(con, sid)
    con.execute("INSERT INTO history(student_id, ts, user_name, kind, text) VALUES (?,?,?,?,?)",
                (sid, _now(), user.get("name", ""), "payment", f"Выставлено разовое занятие: {price} ₽"))
    return sub
