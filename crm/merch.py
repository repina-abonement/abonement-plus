"""Мерч: прайс, продажи и отчёт тренера в конце смены («были ли продажи мерча»).

Оплата: наличные, карта или QR. По QR чек на кассе пробивают вручную — продажа висит красной, пока не отметят «чек пробит».
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

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
CREATE TABLE IF NOT EXISTS merch_stock_counts (
    id INTEGER PRIMARY KEY,
    counted_at TEXT NOT NULL,
    counted_by_id INTEGER NOT NULL REFERENCES users(id),
    counted_by_name TEXT NOT NULL,
    item_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS merch_stock_count_lines (
    count_id INTEGER NOT NULL REFERENCES merch_stock_counts(id) ON DELETE CASCADE,
    item_id INTEGER NOT NULL REFERENCES merch_items(id),
    item_name TEXT NOT NULL, stock INTEGER NOT NULL, norm INTEGER NOT NULL, unit TEXT NOT NULL,
    PRIMARY KEY(count_id,item_id)
);
"""

PRICE = [("Форма", 3800), ("Рюкзак большой", 3000), ("Сумка-шоппер", 1650), ("Рюкзак для обуви", 1300), ("Вымпел", 1200),
         ("Шарф", 2000), ("Гетры", 1100), ("Кружка", 880), ("Носки", 450), ("Мяч", 3850), ("Бутылка", 990), ("Тату", 330)]
METHODS = {"cash":"Наличные","paykeeper":"По ссылке PayKeeper","card_branch":"Картой на филиале","sber_qr_branch":"По QR Сбера на филиале","tochka_qr_link":"По QR/ссылке Точки от администратора","card":"Карта (канал не уточнён)","qr":"QR (банк не уточнён)"}
STOCK_MINIMUMS = [
    ("Дневник", 50, "шт."), ("Кубок", 5, "шт."), ("Наклейки круглые", 10, "листов"),
    ("Медаль", 8, "шт."), ("Спортивная бутылка", 10, "шт."), ("Кружка", 8, "шт."),
    ("Бандана", 5, "шт."), ("Сумка", 5, "шт."), ("Браслеты текущего сезона", 30, "шт."),
    ("Деревянные значки", 15, "шт."), ("Мяч", 2, "шт."), ("Носки (16–18)", 5, "пар"),
    ("Носки (18–20)", 5, "пар"), ("Гетры (16–18)", 3, "пар"), ("Гетры (18–20)", 3, "пар"),
    ("Форма (размер S)", 6, "шт."), ("Форма (размер M)", 6, "шт."),
    ("Форма (размер L)", 6, "шт."), ("Форма (размер XL)", 5, "шт."),
]
WEEKDAY_NAMES = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


class MerchError(ValueError):
    pass


def init(con):
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(merch_items)")}
    for col in ("norm INTEGER NOT NULL DEFAULT 0", "stock INTEGER", "stock_at TEXT", "stock_by TEXT NOT NULL DEFAULT ''",
                "stock_only INTEGER NOT NULL DEFAULT 0", "stock_unit TEXT NOT NULL DEFAULT 'шт.'"):
        if col.split()[0] not in cols:  # пересчёт склада: норма, сколько есть, когда и кто считал
            con.execute(f"ALTER TABLE merch_items ADD COLUMN {col}")
    if not con.execute("SELECT 1 FROM merch_items").fetchone():
        for i, (n, p) in enumerate(PRICE):
            con.execute("INSERT INTO merch_items(name, price, sort) VALUES (?,?,?)", (n, p, i))
    if not con.execute("SELECT 1 FROM settings WHERE key='merch_stock_seeded_v1'").fetchone():
        for name, norm, unit in STOCK_MINIMUMS:
            row = con.execute("SELECT id, norm FROM merch_items WHERE name=?", (name,)).fetchone()
            if row:
                con.execute("UPDATE merch_items SET norm=CASE WHEN norm=0 THEN ? ELSE norm END, stock_unit=? WHERE id=?", (norm, unit, row["id"]))
            else:
                con.execute("""INSERT INTO merch_items(name,price,active,sort,norm,stock_only,stock_unit)
                               VALUES (?,0,1,(SELECT COALESCE(MAX(sort),0)+1 FROM merch_items),?,1,?)""", (name, norm, unit))
        con.execute("INSERT INTO settings(key,value) VALUES ('merch_stock_seeded_v1','1')")


def _now():
    return datetime.now()


def items(con, all_=False, inventory=False) -> list[dict]:
    clauses = []
    if not all_:
        clauses.append("active=1")
    if inventory:
        clauses.append("(stock_only=1 OR norm>0 OR stock IS NOT NULL)")
    elif not all_:
        clauses.append("stock_only=0 AND price>0")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    return [dict(r) for r in con.execute(f"SELECT * FROM merch_items{where} ORDER BY sort, name")]


def stock_status(con, today: date | None = None) -> dict:
    today = today or _now().date()
    monday = today - timedelta(days=today.weekday())
    last = con.execute("SELECT * FROM merch_stock_counts ORDER BY counted_at DESC,id DESC LIMIT 1").fetchone()
    inventory_ids = {r[0] for r in con.execute("""SELECT id FROM merch_items WHERE active=1
        AND (stock_only=1 OR norm>0 OR stock IS NOT NULL)""")}
    counted_ids = {r[0] for r in con.execute("""SELECT DISTINCT l.item_id FROM merch_stock_count_lines l
        JOIN merch_stock_counts c ON c.id=l.count_id WHERE substr(c.counted_at,1,10) BETWEEN ? AND ?""",
        (monday.isoformat(), (monday + timedelta(days=6)).isoformat()))}
    shortages = [{"id": r["id"], "name": r["name"], "stock": r["stock"], "norm": r["norm"],
                  "missing": r["norm"] - r["stock"], "unit": r["stock_unit"]}
                 for r in con.execute("""SELECT * FROM merch_items WHERE active=1 AND norm>0 AND stock IS NOT NULL
                                          AND stock<norm ORDER BY sort,name""")]
    return {"week_start": monday.isoformat(), "week_end": (monday + timedelta(days=6)).isoformat(),
            "weekly_due": not inventory_ids.issubset(counted_ids), "counted_items": len(inventory_ids & counted_ids),
            "total_items": len(inventory_ids), "last_count": dict(last) if last else None, "shortages": shortages}


def stock_count_values(con, counted_on: str | None = None) -> dict:
    """Последнее сохранённое значение каждой позиции за выбранную дату; пропуски остаются пустыми."""
    try: day = date.fromisoformat(counted_on) if counted_on else _now().date()
    except (TypeError, ValueError): raise MerchError("Выберите корректную дату пересчёта")
    values = {}
    for row in con.execute("""SELECT l.*,c.counted_at,c.counted_by_name FROM merch_stock_count_lines l
        JOIN merch_stock_counts c ON c.id=l.count_id WHERE substr(c.counted_at,1,10)=? ORDER BY c.id""", (day.isoformat(),)):
        values[str(row["item_id"])] = dict(row)
    return {"counted_on": day.isoformat(), "values": values}


def planner_task_section(con, task: dict, repeat: dict | None, user: dict) -> dict | None:
    """Данные пересчёта только для существующей повторяемой задачи по мерчу."""
    haystack = f"{task.get('title', '')} {task.get('note', '')}".lower().replace("ё", "е")
    if not task.get("recur_id") or "мерч" not in haystack:
        return None
    weekdays = [int(x) for x in (repeat or {}).get("weekdays", [])]
    labels = [WEEKDAY_NAMES[x] for x in weekdays if 0 <= x < len(WEEKDAY_NAMES)]
    snapshot = stock_count_values(con)
    return {"stock_items": items(con, inventory=True), "stock_status": stock_status(con),
            "stock_values": snapshot["values"], "selected_stock_date": snapshot["counted_on"],
            "can_norm": user.get("role") == "admin" or bool(user.get("is_senior")),
            "schedule": ", ".join(labels) or task.get("due_at", "")[:10]}


def stock_count(con, rows: list, user: dict, can_norm: bool, counted_on: str | None = None) -> dict:
    """Пересчёт мерча (общий склад): вписали, сколько есть, CRM считает, что заказать, и ставит задачу руководителю.
    Например, кружки 5 при норме 15: заказать 10. Норму меняют руководитель и старший администратор."""
    from . import planner
    today = _now().date()
    try: count_day = date.fromisoformat(counted_on) if counted_on else today
    except (TypeError, ValueError): raise MerchError("Выберите корректную дату пересчёта")
    if count_day > today: raise MerchError("Нельзя внести пересчёт будущей датой")
    now = count_day.isoformat() + "T" + _now().strftime("%H:%M")
    inventory = items(con, inventory=True)
    filled = [r for r in rows if r.get("stock") not in (None, "")]
    if not filled: raise MerchError("Заполните хотя бы одну позицию")
    for r in filled:
        iid = int(r.get("id") or 0)
        if not con.execute("SELECT 1 FROM merch_items WHERE id=?", (iid,)).fetchone():
            continue
        try: stock = max(0, int(r["stock"]))
        except (TypeError, ValueError): raise MerchError("Остаток укажите целым числом")
        if can_norm and count_day == today and r.get("norm") not in (None, ""):
            con.execute("UPDATE merch_items SET norm=? WHERE id=?", (max(0, int(r["norm"])), iid))
        current = con.execute("SELECT stock_at FROM merch_items WHERE id=?", (iid,)).fetchone()
        if not current[0] or current[0][:10] <= count_day.isoformat():
            con.execute("UPDATE merch_items SET stock=?, stock_at=?, stock_by=? WHERE id=?", (stock, now, user.get("name", ""), iid))
    count_id = con.execute("INSERT INTO merch_stock_counts(counted_at,counted_by_id,counted_by_name,item_count) VALUES (?,?,?,?)",
                (now, user["id"], user.get("name", ""), len(filled))).lastrowid
    current_items = {i["id"]: i for i in items(con, inventory=True)}
    order = []
    for r in filled:
        item = current_items.get(int(r.get("id") or 0))
        if not item: continue
        stock = max(0, int(r["stock"])); norm = max(0, int(r.get("norm") or item["norm"])) if can_norm else item["norm"]
        con.execute("INSERT INTO merch_stock_count_lines(count_id,item_id,item_name,stock,norm,unit) VALUES (?,?,?,?,?,?)",
                    (count_id,item["id"],item["name"],stock,norm,item["stock_unit"]))
        if norm > stock: order.append({"name":item["name"],"qty":norm-stock,"stock":stock,"norm":norm})
    task = None
    if order and count_day == today:
        owner = con.execute("SELECT id FROM users WHERE role='admin' AND active=1 ORDER BY id LIMIT 1").fetchone()
        if owner:
            title = "Заказать мерч: " + ", ".join(f"{o['name']} {o['qty']}" for o in order)
            note = f"Пересчёт {now[8:10]}.{now[5:7]} ({user.get('name', '')}). " + "; ".join(f"{o['name']}: есть {o['stock']} из {o['norm']}" for o in order)
            # задачу руководителю ставит сама CRM, поэтому и администратор, который считал, может её создать
            task = planner.add(con, user | {"role": "admin"}, {"title": title[:300], "assignee_id": owner[0], "note": note})
    return {"order": order, "task": task, "counted_on": count_day.isoformat(), "historical": count_day < today,
            "stock_status": stock_status(con)}


def add_stock_item(con, data: dict) -> int:
    name = _clean_name(data.get("name"))
    if not name:
        raise MerchError("Укажите название позиции")
    try:
        norm = max(0, int(data.get("norm") or 0))
    except (TypeError, ValueError):
        raise MerchError("Минимум укажите числом")
    unit = _clean_name(data.get("unit")) or "шт."
    existing = next((dict(row) for row in con.execute("SELECT * FROM merch_items")
                     if _clean_name(row["name"]).casefold().replace("ё", "е") == name.casefold().replace("ё", "е")), None)
    if existing:
        if existing.get("stock_only") or existing.get("norm") or existing.get("stock") is not None:
            raise MerchError("Такая позиция уже есть в складском списке")
        # A sale item may already exist in the merch catalogue. Attach that
        # exact row to inventory, preserving its price and sales history.
        con.execute("UPDATE merch_items SET active=1,stock_only=1,norm=?,stock_unit=? WHERE id=?",
                    (norm, unit, existing["id"]))
        return existing["id"]
    try:
        return con.execute("""INSERT INTO merch_items(name,price,active,sort,norm,stock_only,stock_unit)
                              VALUES (?,0,1,(SELECT COALESCE(MAX(sort),0)+1 FROM merch_items),?,1,?)""",
                           (name, norm, unit)).lastrowid
    except Exception:
        raise MerchError("Такая позиция уже есть")


def _clean_name(value) -> str:
    return " ".join(str(value or "").split())[:120]


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
             1 if method in ("qr","sber_qr_branch","tochka_qr_link") and data.get("receipt_done") else 0, (data.get("note") or "").strip()[:200],
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
