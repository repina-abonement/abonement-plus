"""Мерч: прайс, продажи и отчёт тренера в конце смены («были ли продажи мерча»).

Оплата: наличные, карта или QR. По QR чек на кассе пробивают вручную — продажа висит красной, пока не отметят «чек пробит».
"""
from __future__ import annotations

from datetime import date, datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS merch_items (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    price INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS merch_sales (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    branch_id INTEGER REFERENCES branches(id),
    user_id INTEGER NOT NULL REFERENCES users(id),      -- кто продал
    student_id INTEGER REFERENCES students(id),         -- кому, если знаем
    item_id INTEGER NOT NULL REFERENCES merch_items(id),
    item_name TEXT NOT NULL,
    qty INTEGER NOT NULL DEFAULT 1,
    price INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    method TEXT NOT NULL,                                -- cash | card | qr
    receipt_done INTEGER NOT NULL DEFAULT 0,             -- для QR: чек пробит на кассе
    cash_handed INTEGER NOT NULL DEFAULT 0,              -- для наличных: сдал деньги администратору
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ok',                   -- ok | cancelled
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shift_reports (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    branch_id INTEGER NOT NULL REFERENCES branches(id),
    date TEXT NOT NULL,
    merch_sold INTEGER NOT NULL,                         -- 0 продаж не было, 1 были
    at TEXT NOT NULL,
    UNIQUE(user_id, branch_id, date)
);
"""

PRICE = [("Форма", 3800), ("Рюкзак большой", 3000), ("Сумка-шоппер", 1650), ("Рюкзак для обуви", 1300), ("Вымпел", 1200),
         ("Шарф", 2000), ("Гетры", 1100), ("Кружка", 880), ("Носки", 450), ("Мяч", 3850), ("Бутылка", 990), ("Тату", 330)]
METHODS = {"cash": "Наличные", "card": "Карта", "qr": "QR"}


class MerchError(ValueError):
    pass


def init(con):
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(merch_items)")}
    for col in ("norm INTEGER NOT NULL DEFAULT 0", "stock INTEGER", "stock_at TEXT", "stock_by TEXT NOT NULL DEFAULT ''"):
        if col.split()[0] not in cols:  # пересчёт склада: норма, сколько есть, когда и кто считал
            con.execute(f"ALTER TABLE merch_items ADD COLUMN {col}")
    if not con.execute("SELECT 1 FROM merch_items").fetchone():
        for i, (n, p) in enumerate(PRICE):
            con.execute("INSERT INTO merch_items(name, price, sort) VALUES (?,?,?)", (n, p, i))


def _now():
    return datetime.now()


def items(con, all_=False) -> list[dict]:
    return [dict(r) for r in con.execute(f"SELECT * FROM merch_items {'' if all_ else 'WHERE active=1'} ORDER BY sort, name")]


def stock_count(con, rows: list, user: dict, can_norm: bool) -> dict:
    """Пересчёт мерча (общий склад): вписали, сколько есть, CRM считает, что заказать, и ставит задачу руководителю.
    Например, кружки 5 при норме 15: заказать 10. Норму меняют руководитель и старший администратор."""
    from . import planner
    now = _now().isoformat(timespec="minutes")
    for r in rows:
        iid = int(r.get("id") or 0)
        if not con.execute("SELECT 1 FROM merch_items WHERE id=?", (iid,)).fetchone():
            continue
        if can_norm and r.get("norm") not in (None, ""):
            con.execute("UPDATE merch_items SET norm=? WHERE id=?", (max(0, int(r["norm"])), iid))
        if r.get("stock") not in (None, ""):
            con.execute("UPDATE merch_items SET stock=?, stock_at=?, stock_by=? WHERE id=?", (max(0, int(r["stock"])), now, user.get("name", ""), iid))
    order = [{"name": i["name"], "qty": i["norm"] - i["stock"], "stock": i["stock"], "norm": i["norm"]}
             for i in con.execute("SELECT * FROM merch_items WHERE active=1 AND stock IS NOT NULL AND norm > stock ORDER BY sort, name")]
    task = None
    if order:
        owner = con.execute("SELECT id FROM users WHERE role='admin' AND active=1 ORDER BY id LIMIT 1").fetchone()
        if owner:
            title = "Заказать мерч: " + ", ".join(f"{o['name']} {o['qty']}" for o in order)
            note = f"Пересчёт {now[8:10]}.{now[5:7]} ({user.get('name', '')}). " + "; ".join(f"{o['name']}: есть {o['stock']} из {o['norm']}" for o in order)
            # задачу руководителю ставит сама CRM, поэтому и администратор, который считал, может её создать
            task = planner.add(con, user | {"role": "admin"}, {"title": title[:300], "assignee_id": owner[0], "note": note})
    return {"order": order, "task": task}


def save_item(con, data: dict, iid: int | None = None) -> int:
    name = (data.get("name") or "").strip()
    try:
        price = int(data.get("price") or 0)
    except ValueError:
        raise MerchError("Цена числом")
    if not name or price <= 0:
        raise MerchError("Укажите название и цену")
    if iid:
        con.execute("UPDATE merch_items SET name=?, price=?, active=? WHERE id=?",
                    (name, price, 0 if data.get("active") in (False, 0, "0") else 1, iid))
        return iid
    try:
        return con.execute("INSERT INTO merch_items(name, price, sort) VALUES (?,?,(SELECT COALESCE(MAX(sort),0)+1 FROM merch_items))",
                           (name, price)).lastrowid
    except Exception:
        raise MerchError("Такой товар уже есть")


def sell(con, user: dict, data: dict) -> list[int]:
    """Продажа: несколько позиций одним чеком. data = {branch_id, method, lines:[{item_id, qty}], student_id?, note?}"""
    method = data.get("method")
    if method not in METHODS:
        raise MerchError("Выберите способ оплаты: наличные, карта или QR")
    lines = [x for x in data.get("lines") or [] if int(x.get("qty") or 0) > 0]
    if not lines:
        raise MerchError("Добавьте хотя бы один товар")
    bid = int(data.get("branch_id") or 0) or None
    sid = int(data.get("student_id") or 0) or None
    d = data.get("date") or _now().date().isoformat()
    ids = []
    for ln in lines:
        it = con.execute("SELECT * FROM merch_items WHERE id=?", (int(ln["item_id"]),)).fetchone()
        if not it:
            raise MerchError("Товар не найден")
        qty = int(ln["qty"])
        if qty > 50:
            raise MerchError("Слишком большое количество")
        ids.append(con.execute(
            """INSERT INTO merch_sales(date, branch_id, user_id, student_id, item_id, item_name, qty, price, amount, method,
               receipt_done, note, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (d, bid, user["id"], sid, it["id"], it["name"], qty, it["price"], qty * it["price"], method,
             1 if method == "qr" and data.get("receipt_done") else 0, (data.get("note") or "").strip()[:200],
             _now().isoformat(timespec="minutes"))).lastrowid)
    if bid:
        report_shift(con, user, bid, True, d)
    return ids


def set_flag(con, sale_id: int, flag: str, value: bool):
    if flag not in ("receipt_done", "cash_handed"):
        raise MerchError("Неизвестная отметка")
    con.execute(f"UPDATE merch_sales SET {flag}=? WHERE id=?", (1 if value else 0, sale_id))


def cancel(con, sale_id: int):
    con.execute("UPDATE merch_sales SET status='cancelled' WHERE id=?", (sale_id,))


def report_shift(con, user: dict, branch_id: int, sold: bool, d: str | None = None):
    d = d or _now().date().isoformat()
    con.execute("""INSERT INTO shift_reports(user_id, branch_id, date, merch_sold, at) VALUES (?,?,?,?,?)
                   ON CONFLICT(user_id, branch_id, date) DO UPDATE SET merch_sold=MAX(merch_sold, excluded.merch_sold), at=excluded.at""",
                (user["id"], branch_id, d, 1 if sold else 0, _now().isoformat(timespec="minutes")))


def my_shift(con, user_id: int, d: str) -> dict:
    """Для стола тренера: ответил ли он про мерч и что продал сегодня."""
    reps = {r["branch_id"]: r["merch_sold"] for r in con.execute("SELECT * FROM shift_reports WHERE user_id=? AND date=?", (user_id, d))}
    sales = [dict(r) for r in con.execute("""SELECT * FROM merch_sales WHERE user_id=? AND date=? AND status='ok' ORDER BY id""",
                                          (user_id, d))]
    return {"reports": reps, "sales": sales, "items": items(con), "methods": METHODS}


def registry(con, d_from: str, d_to: str) -> dict:
    rows = [dict(r) for r in con.execute(
        """SELECT m.*, u.name AS seller, b.name AS branch, s.name AS student FROM merch_sales m
           JOIN users u ON u.id=m.user_id LEFT JOIN branches b ON b.id=m.branch_id LEFT JOIN students s ON s.id=m.student_id
           WHERE m.date BETWEEN ? AND ? ORDER BY m.date DESC, m.id DESC""", (d_from, d_to))]
    ok = [r for r in rows if r["status"] == "ok"]
    by_method = {k: sum(r["amount"] for r in ok if r["method"] == k) for k in METHODS}
    by_item = {}
    for r in ok:
        x = by_item.setdefault(r["item_name"], {"qty": 0, "amount": 0})
        x["qty"] += r["qty"]
        x["amount"] += r["amount"]
    by_seller = {}
    for r in ok:
        x = by_seller.setdefault(r["seller"], {"amount": 0, "cash_not_handed": 0})
        x["amount"] += r["amount"]
        if r["method"] == "cash" and not r["cash_handed"]:
            x["cash_not_handed"] += r["amount"]
    return {"rows": rows, "total": sum(r["amount"] for r in ok), "by_method": by_method,
            "by_item": sorted(({"name": k, **v} for k, v in by_item.items()), key=lambda x: -x["amount"]),
            "by_seller": [{"name": k, **v} for k, v in by_seller.items()],
            "qr_no_receipt": [r for r in ok if r["method"] == "qr" and not r["receipt_done"]],
            "methods": METHODS}


def missing_reports(con, d: date) -> list[dict]:
    """Тренеры, которые вели смену, но не ответили про мерч. Показываем на следующий день и позже."""
    from . import staff
    out = []
    for s in staff.expected(con, d):
        if not con.execute("SELECT 1 FROM shift_reports WHERE user_id=? AND branch_id=? AND date=?",
                           (s["user_id"], s["branch_id"], d.isoformat())).fetchone():
            name = (con.execute("SELECT name FROM users WHERE id=?", (s["user_id"],)).fetchone() or [""])[0]
            out.append({"name": name, "branch": s["branch"], "date": d.isoformat()})
    return out
