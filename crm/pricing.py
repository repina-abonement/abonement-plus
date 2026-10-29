"""Цены обычных групп (Небо, Чоботовская и др.): прайс с датой начала, оплата до 25 числа, корректировка.

Правила (Юлия, 27-28.10.2026):
- цена за занятие по прайсу оплачиваемого месяца: старая и новая живут параллельно, у каждой «с какого месяца»;
- при оплате позже 25 числа предыдущего месяца надбавка (с абонементов на декабрь 2026 +5% со всей суммы, копейки вверх);
  дата оплаты решает: счёт, выставленный до 25-го и оплаченный после, пересчитывается при оплате;
- скидки (второй ребёнок, многодетные, день пробного) только при оплате до 25-го и только от обычной цены;
- новичок в текущем месяце платит за оставшиеся занятия (с ближайшей тренировки, после пробного) и без надбавки;
- после 25-го обычную цену или скидку вручную не поставить: только «Корректировка» с обязательным комментарием,
  все корректировки видны в отчёте (кто, когда, на сколько, почему). На разовое занятие правила не действуют.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from . import logic

SCHEMA = """
CREATE TABLE IF NOT EXISTS price_list (
    id INTEGER PRIMARY KEY,
    valid_from TEXT NOT NULL UNIQUE,          -- 'YYYY-MM': для абонементов с этого месяца
    fixed INTEGER NOT NULL,                   -- негибкий тариф, за занятие
    flex INTEGER NOT NULL,                    -- гибкий тариф, за занятие
    late_pct REAL NOT NULL DEFAULT 0,         -- надбавка при оплате после срока
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS price_corrections (
    id INTEGER PRIMARY KEY,
    subscription_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    old_price INTEGER NOT NULL,
    new_price INTEGER NOT NULL,
    comment TEXT NOT NULL,
    user_name TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""
LATE_FROM = "2026-12"  # с абонементов на декабрь 2026 после 25 числа +5%


class PriceError(Exception):
    pass


def _now() -> str:
    return datetime.now().isoformat(timespec="minutes")


def init(con):
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(subscriptions)")}
    if "corrected" not in cols:
        con.execute("ALTER TABLE subscriptions ADD COLUMN corrected INTEGER NOT NULL DEFAULT 0")
    if not con.execute("SELECT 1 FROM price_list").fetchone():  # прайс из прежних настроек + правило с декабря
        st = dict(con.execute("SELECT key, value FROM settings WHERE key IN ('price_fixed','price_flex')").fetchall())
        fx, fl = int(st.get("price_fixed") or 1350), int(st.get("price_flex") or 1550)
        con.execute("INSERT INTO price_list(valid_from, fixed, flex, late_pct, created_at) VALUES ('2000-01',?,?,0,?)", (fx, fl, _now()))
        con.execute("INSERT INTO price_list(valid_from, fixed, flex, late_pct, created_at) VALUES (?,?,?,5,?)", (LATE_FROM, fx, fl, _now()))


def prices_for(con, ym: str) -> dict:
    r = con.execute("SELECT * FROM price_list WHERE valid_from<=? ORDER BY valid_from DESC LIMIT 1", (ym,)).fetchone() \
        or con.execute("SELECT * FROM price_list ORDER BY valid_from LIMIT 1").fetchone()
    return dict(r)


def listing(con) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT * FROM price_list ORDER BY valid_from")]


def save_price(con, data: dict) -> None:
    ym = str(data.get("valid_from") or "")[:7]
    try:
        logic.parse_month(ym)
        fx, fl, pct = int(data["fixed"]), int(data["flex"]), float(str(data.get("late_pct") or 0).replace(",", "."))
    except (KeyError, ValueError):
        raise PriceError("Укажите месяц и цены за занятие")
    if fx <= 0 or fl <= 0 or not 0 <= pct <= 50:
        raise PriceError("Проверьте цены и надбавку")
    con.execute("""INSERT INTO price_list(valid_from, fixed, flex, late_pct, created_at) VALUES (?,?,?,?,?)
                   ON CONFLICT(valid_from) DO UPDATE SET fixed=excluded.fixed, flex=excluded.flex, late_pct=excluded.late_pct""",
                (ym, fx, fl, pct, _now()))


def deadline(con, ym: str) -> date:
    day = int((con.execute("SELECT value FROM settings WHERE key='renewal_deadline_day'").fetchone() or ["25"])[0] or 25)
    y, m = logic.parse_month(logic.shift_month(ym, -1))
    return date(y, m, day)


def late_fee(total: int, pct: float) -> int:
    """Надбавка со всей суммы, копейки вверх: 9 × 1350 = 12 150 → +607,50 → 12 758 (пример Юлии)."""
    return math.ceil(total * pct / 100 - 1e-9)


def is_newcomer(con, sid: int, ym: str, on: date) -> bool:
    """Купил после пробного в этом месяце и ещё ни разу не платил за абонемент обычной группы.
    Дети, заведённые сразу учениками (перенос из ERP), новичками не считаются: им полный месяц."""
    if ym != logic.month_of(on):
        return False
    s = con.execute("SELECT converted_at FROM students WHERE id=?", (sid,)).fetchone()
    if not s or not s["converted_at"] or s["converted_at"][:7] != ym:
        return False
    return not con.execute("""SELECT 1 FROM subscriptions x JOIN enrollments e ON e.id=x.enrollment_id JOIN groups g ON g.id=e.group_id
                              WHERE x.student_id=? AND x.kind='month' AND x.status='paid' AND g.program_id IS NULL AND x.month<?""",
                           (sid, ym)).fetchone()


def newcomer_start(con, sid: int, on: date) -> date:
    s = con.execute("SELECT trial_at FROM students WHERE id=?", (sid,)).fetchone()
    if s and s["trial_at"] and s["trial_at"][:10] >= on.isoformat():
        return date.fromisoformat(s["trial_at"][:10]) + timedelta(days=1)  # пробное ещё впереди: абонемент со следующего занятия
    return on


def reprice(con, sub_id: int, on: date | None = None) -> bool:
    """Перед оплатой: счёт выставили до 25-го, а платят после. Надбавка и без скидки, если не было корректировки."""
    from . import money, services as sv
    on = on or date.today()
    s = con.execute("""SELECT x.*, g.program_id FROM subscriptions x JOIN enrollments e ON e.id=x.enrollment_id
                       JOIN groups g ON g.id=e.group_id WHERE x.id=? AND x.status='invoiced' AND x.kind='month'""", (sub_id,)).fetchone()
    if not s or s["program_id"] or s["corrected"] or s["late_fee"]:
        return False
    dl = deadline(con, s["month"])
    pr = prices_for(con, s["month"])
    if on <= dl or not pr["late_pct"] or s["created_at"][:10] > dl.isoformat():  # выставлен после срока: уже по правилам
        return False
    if money.balance(con, s["student_id"])["covered"].get(sub_id, 0):
        return False  # уже частично оплачен до срока: не трогаем
    base = max(0, s["lessons"] - s["credit_lessons"]) * s["per_lesson"]
    fee = late_fee(base, pr["late_pct"])
    price = base + fee
    if price == s["price"]:
        return False
    con.execute("UPDATE subscriptions SET price=?, late_fee=?, discount_name='', discount_amount=0 WHERE id=?", (price, fee, sub_id))
    sv.log(con, s["student_id"], f"Счёт на {logic.month_label(s['month'])} пересчитан по дате оплаты: {s['price']} → {price} ₽ "
           f"(оплата после {dl:%d.%m}: +{pr['late_pct']:g}% и без скидки)", None, "payment")
    return True


def correct(con, sub_id: int, price, comment: str, user: dict) -> None:
    """Корректировка: исключение из правил (обычная цена или скидка после 25-го) с обязательной причиной."""
    from . import money, services as sv
    comment = (comment or "").strip()
    if len(comment) < 5:
        raise PriceError("Напишите, почему корректируете цену")
    s = con.execute("SELECT * FROM subscriptions WHERE id=? AND status!='cancelled'", (sub_id,)).fetchone()
    if not s:
        raise PriceError("Счёт не найден или отменён")
    try:
        price = int(price)
    except (TypeError, ValueError):
        raise PriceError("Укажите новую сумму")
    if price < 0 or price == s["price"]:
        raise PriceError("Новая сумма должна отличаться от текущей")
    con.execute("UPDATE subscriptions SET price=?, corrected=1 WHERE id=?", (price, sub_id))
    con.execute("""INSERT INTO price_corrections(subscription_id, student_id, old_price, new_price, comment, user_name, created_at)
                   VALUES (?,?,?,?,?,?,?)""", (sub_id, s["student_id"], s["price"], price, comment, user.get("name", ""), _now()))
    money.reconcile(con, s["student_id"])
    sv.log(con, s["student_id"], f"Корректировка счёта на {logic.month_label(s['month'])}: {s['price']} → {price} ₽. Причина: {comment}",
           user, "payment")


def corrections(con, start: str, end: str) -> list[dict]:
    return [dict(r) for r in con.execute(
        """SELECT c.*, s.name AS student, x.month FROM price_corrections c JOIN students s ON s.id=c.student_id
           JOIN subscriptions x ON x.id=c.subscription_id WHERE substr(c.created_at,1,10) BETWEEN ? AND ?
           ORDER BY c.created_at DESC""", (start, end))]
