"""Мои платежи (аренда, подписки, сервисы) и реклама в районных пабликах.

Платежи: у каждого дата следующей оплаты. За N дней до неё платёж появляется на столе жёлтым, в день оплаты
и после — красным. «Оплачено» пишет историю и переносит следующую дату (месяц, год, неделя).

Паблики: договорились → оплатили N постов до даты → на столе напоминания выложить пост и что оплата сгорает.
Каждый паблик — рекламный источник «Паблики → название», так видно, сколько заявок пришло и почём заявка.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS bills (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'other',   -- rent | service | ads | tax | other
    amount INTEGER NOT NULL DEFAULT 0,
    period TEXT NOT NULL DEFAULT 'monthly',   -- monthly | yearly | weekly | once
    next_due TEXT NOT NULL,
    remind_days INTEGER NOT NULL DEFAULT 3,
    pay_where TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bill_payments (
    id INTEGER PRIMARY KEY,
    bill_id INTEGER NOT NULL REFERENCES bills(id),
    due TEXT NOT NULL,
    amount INTEGER NOT NULL,
    paid_at TEXT NOT NULL,
    paid_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS ad_places (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    link TEXT NOT NULL DEFAULT '',
    contact TEXT NOT NULL DEFAULT '',
    branch_id INTEGER REFERENCES branches(id),
    price INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    source_id INTEGER REFERENCES ad_sources(id),
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ad_deals (
    id INTEGER PRIMARY KEY,
    place_id INTEGER NOT NULL REFERENCES ad_places(id),
    paid_at TEXT NOT NULL,
    amount INTEGER NOT NULL DEFAULT 0,
    posts_total INTEGER NOT NULL DEFAULT 1,
    valid_until TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ad_posts (
    id INTEGER PRIMARY KEY,
    deal_id INTEGER NOT NULL REFERENCES ad_deals(id),
    planned TEXT NOT NULL,
    posted_at TEXT,
    link TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'planned'      -- planned | posted | cancelled
);
"""
CATEGORIES = {"rent": "Аренда", "crm": "Для работы CRM", "service": "Сервисы и подписки", "ads": "Реклама", "tax": "Налоги и банк", "other": "Другое"}
PERIODS = {"monthly": "каждый месяц", "yearly": "каждый год", "weekly": "каждую неделю", "once": "один раз"}


class ExpError(ValueError):
    pass


def init(con):
    con.executescript(SCHEMA)
    if "anchor_day" not in {r[1] for r in con.execute("PRAGMA table_info(bills)")}:
        con.execute("ALTER TABLE bills ADD COLUMN anchor_day INTEGER NOT NULL DEFAULT 0")


def _now() -> str:
    return datetime.now().isoformat(timespec="minutes")


def _d(s: str, what: str) -> date:
    try:
        return date.fromisoformat(s or "")
    except ValueError:
        raise ExpError(f"Укажите {what}")


def _add_month(d: date, n: int = 1, anchor: int = 0) -> date:
    """+n месяцев; anchor — «каждое 31-е»: в коротких месяцах последний день, потом снова 31-е."""
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    last = (date(y, m, 28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return date(y, m, min(anchor or d.day, last.day))


# ---------------- платежи ----------------

def save_bill(con, data: dict, bid: int | None = None) -> int:
    title = (data.get("title") or "").strip()
    if not title:
        raise ExpError("Напишите, за что платёж")
    cat = data.get("category") if data.get("category") in CATEGORIES else "other"
    per = data.get("period") if data.get("period") in PERIODS else "monthly"
    due = _d(data.get("next_due"), "дату оплаты").isoformat()
    try:
        amount = int(float(str(data.get("amount") or 0).replace(" ", "").replace(",", ".")))
        remind = int(data.get("remind_days") if data.get("remind_days") not in (None, "") else 3)
    except ValueError:
        raise ExpError("Сумма и дни числом")
    vals = (title, cat, amount, per, due, remind, (data.get("pay_where") or "").strip()[:300], (data.get("note") or "").strip()[:500])
    if bid:
        con.execute("""UPDATE bills SET title=?, category=?, amount=?, period=?, next_due=?, remind_days=?, pay_where=?, note=?,
                       anchor_day=? WHERE id=?""", (*vals, int(due[8:10]), bid))
        return bid
    return con.execute("""INSERT INTO bills(title, category, amount, period, next_due, remind_days, pay_where, note, created_at,
                          anchor_day) VALUES (?,?,?,?,?,?,?,?,?,?)""", (*vals, _now(), int(due[8:10]))).lastrowid


def pay_bill(con, bid: int, user: dict, amount=None) -> str | None:
    b = con.execute("SELECT * FROM bills WHERE id=?", (bid,)).fetchone()
    if not b:
        raise ExpError("Платёж не найден")
    amt = int(amount) if amount not in (None, "") else b["amount"]
    con.execute("INSERT INTO bill_payments(bill_id, due, amount, paid_at, paid_by) VALUES (?,?,?,?,?)",
                (bid, b["next_due"], amt, _now(), user.get("name", "")))
    due = date.fromisoformat(b["next_due"])
    nxt = {"monthly": lambda: _add_month(due, 1, b["anchor_day"]), "yearly": lambda: _add_month(due, 12, b["anchor_day"]),
           "weekly": lambda: due + timedelta(days=7)}.get(b["period"])
    if nxt:
        con.execute("UPDATE bills SET next_due=? WHERE id=?", (nxt().isoformat(), bid))
        return nxt().isoformat()
    con.execute("UPDATE bills SET active=0 WHERE id=?", (bid,))
    return None


def archive_bill(con, bid: int):
    con.execute("UPDATE bills SET active=0 WHERE id=?", (bid,))


def bills_view(con, today: date | None = None) -> dict:
    today = today or date.today()
    rows = []
    for b in con.execute("SELECT * FROM bills WHERE active=1 ORDER BY next_due"):
        left = (date.fromisoformat(b["next_due"]) - today).days
        level = "red" if left <= 0 else "yellow" if left <= b["remind_days"] else ""
        last = con.execute("SELECT paid_at, amount FROM bill_payments WHERE bill_id=? ORDER BY id DESC LIMIT 1", (b["id"],)).fetchone()
        rows.append(dict(b) | {"left": left, "level": level, "category_label": CATEGORIES[b["category"]],
                               "period_label": PERIODS[b["period"]], "last_paid": dict(last) if last else None})
    month = {}
    for b in rows:
        k = 12 if b["period"] == "yearly" else 1
        per_month = b["amount"] / k if b["period"] != "weekly" else b["amount"] * 52 / 12
        if b["period"] != "once":
            month[b["category_label"]] = month.get(b["category_label"], 0) + round(per_month)
    m0 = today.replace(day=1).isoformat()
    paid_month = con.execute("SELECT COALESCE(SUM(amount),0) FROM bill_payments WHERE paid_at>=?", (m0,)).fetchone()[0]
    history = [dict(r) for r in con.execute(
        """SELECT p.*, b.title FROM bill_payments p JOIN bills b ON b.id=p.bill_id ORDER BY p.id DESC LIMIT 30""")]
    return {"bills": rows, "per_month": month, "per_month_total": sum(month.values()), "paid_this_month": paid_month,
            "history": history, "categories": CATEGORIES, "periods": PERIODS}


def bills_upcoming(con, today: date | None = None, days: int = 30) -> list[dict]:
    """Для рабочего стола: всё, что платить в ближайшие 30 дней (и просроченное), чтобы ничего не упустить."""
    return [b for b in bills_view(con, today)["bills"] if b["left"] <= days]


def bills_due(con, today: date | None = None) -> list[dict]:
    """Для рабочего стола: что пора платить и что просрочено."""
    return [b for b in bills_view(con, today)["bills"] if b["level"]]


# ---------------- паблики ----------------

def save_place(con, data: dict, pid: int | None = None) -> int:
    from . import sources
    name = (data.get("name") or "").strip()
    if not name:
        raise ExpError("Название паблика")
    vals = (name, (data.get("link") or "").strip(), (data.get("contact") or "").strip(), int(data.get("branch_id") or 0) or None,
            int(data.get("price") or 0), (data.get("note") or "").strip()[:500])
    sid = sources.ensure_source(con, "Паблики", name)
    if pid:
        con.execute("UPDATE ad_places SET name=?, link=?, contact=?, branch_id=?, price=?, note=?, source_id=? WHERE id=?",
                    (*vals, sid, pid))
        return pid
    return con.execute("""INSERT INTO ad_places(name, link, contact, branch_id, price, note, source_id, created_at)
                          VALUES (?,?,?,?,?,?,?,?)""", (*vals, sid, _now())).lastrowid


def add_deal(con, data: dict) -> int:
    """Оплатили размещение: сколько постов, до какого числа, и когда планируем их выложить."""
    pid = int(data.get("place_id") or 0)
    if not con.execute("SELECT 1 FROM ad_places WHERE id=?", (pid,)).fetchone():
        raise ExpError("Выберите паблик")
    n = max(1, int(data.get("posts_total") or 1))
    paid = _d(data.get("paid_at") or date.today().isoformat(), "дату оплаты")
    until = data.get("valid_until") or ""
    if until:
        _d(until, "срок действия")
    did = con.execute("""INSERT INTO ad_deals(place_id, paid_at, amount, posts_total, valid_until, note, created_at)
                         VALUES (?,?,?,?,?,?,?)""",
                      (pid, paid.isoformat(), int(data.get("amount") or 0), n, until, (data.get("note") or "").strip()[:500],
                       _now())).lastrowid
    plan = [x for x in (data.get("planned") or []) if x]
    for i in range(n):
        d = plan[i] if i < len(plan) else ""
        if not d:  # не указали — раскладываем равномерно до конца срока (или раз в неделю)
            end = date.fromisoformat(until) if until else paid + timedelta(days=7 * n)
            step = max(1, (end - paid).days // n)
            d = (paid + timedelta(days=step * i + 1)).isoformat()
        con.execute("INSERT INTO ad_posts(deal_id, planned) VALUES (?,?)", (did, _d(d, "дату поста").isoformat()))
    return did


def mark_post(con, post_id: int, link: str = "", status: str = "posted", planned: str = ""):
    if status not in ("posted", "planned", "cancelled"):
        raise ExpError("Неизвестный статус")
    if planned:
        con.execute("UPDATE ad_posts SET planned=? WHERE id=?", (_d(planned, "дату").isoformat(), post_id))
        return
    con.execute("UPDATE ad_posts SET status=?, posted_at=?, link=? WHERE id=?",
                (status, _now() if status == "posted" else None, (link or "").strip(), post_id))


def ads_view(con, today: date | None = None) -> dict:
    today = today or date.today()
    places = []
    for p in con.execute("""SELECT a.*, b.name AS branch FROM ad_places a LEFT JOIN branches b ON b.id=a.branch_id
                            WHERE a.active=1 ORDER BY a.name"""):
        deals = []
        for d in con.execute("SELECT * FROM ad_deals WHERE place_id=? ORDER BY paid_at DESC", (p["id"],)):
            posts = [dict(x) for x in con.execute("SELECT * FROM ad_posts WHERE deal_id=? ORDER BY planned", (d["id"],))]
            done = sum(1 for x in posts if x["status"] == "posted")
            deals.append(dict(d) | {"posts": posts, "posted": done, "left": sum(1 for x in posts if x["status"] == "planned")})
        spent = sum(d["amount"] for d in deals)
        leads = con.execute("SELECT COUNT(*) FROM students WHERE source_id=?", (p["source_id"],)).fetchone()[0] if p["source_id"] else 0
        bought = con.execute("SELECT COUNT(*) FROM students WHERE source_id=? AND converted_at IS NOT NULL",
                             (p["source_id"],)).fetchone()[0] if p["source_id"] else 0
        places.append(dict(p) | {"deals": deals, "spent": spent, "leads": leads, "bought": bought,
                                 "per_lead": round(spent / leads) if leads else None})
    return {"places": places, "alerts": ads_alerts(con, today), "spent_total": sum(p["spent"] for p in places)}


def ads_alerts(con, today: date | None = None) -> list[dict]:
    """Для стола: пора выложить пост, пост просрочен, оплата сгорает, а посты не вышли."""
    today = today or date.today()
    out = []
    for r in con.execute("""SELECT x.*, d.valid_until, p.name AS place, p.link AS place_link, p.contact FROM ad_posts x
                            JOIN ad_deals d ON d.id=x.deal_id JOIN ad_places p ON p.id=d.place_id
                            WHERE x.status='planned' AND x.planned<=? ORDER BY x.planned""", (today.isoformat(),)):
        late = r["planned"] < today.isoformat()
        out.append({"kind": "post", "level": "red" if late else "yellow", "post_id": r["id"], "place": r["place"],
                    "link": r["place_link"], "contact": r["contact"], "date": r["planned"],
                    "text": f"Пост должен был выйти {r['planned'][8:10]}.{r['planned'][5:7]}, проверьте" if late else "Сегодня выходит пост: проверьте или напомните админу паблика"})
    for d in con.execute("""SELECT d.*, p.name AS place, p.contact FROM ad_deals d JOIN ad_places p ON p.id=d.place_id
                            WHERE d.valid_until!='' AND d.valid_until>=? AND d.valid_until<=?""",
                         (today.isoformat(), (today + timedelta(days=5)).isoformat())):
        left = con.execute("SELECT COUNT(*) FROM ad_posts WHERE deal_id=? AND status='planned'", (d["id"],)).fetchone()[0]
        if left:
            out.append({"kind": "expire", "level": "red", "deal_id": d["id"], "place": d["place"], "contact": d["contact"],
                        "date": d["valid_until"], "text": f"Оплата сгорает {d['valid_until'][8:10]}.{d['valid_until'][5:7]}, не вышло постов: {left}"})
    return out
