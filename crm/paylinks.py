"""Оплата по ссылке PayKeeper: администратор создаёт счёт из карточки, отправляет ссылку родителю,
оплата сама попадает в CRM (раз в 10 минут сверяем оплаченные счета PayKeeper по номеру заказа).
Чек 54-ФЗ пробивает касса PayKeeper, как при любой онлайн-оплате. Логин PayKeeper тот же, что в разделе «Чеки»."""
import re
import secrets
from datetime import date, datetime, timedelta

from . import fiscal, money

SCHEMA = """
CREATE TABLE IF NOT EXISTS pay_links (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id),
    amount INTEGER NOT NULL,
    orderid TEXT NOT NULL UNIQUE,
    invoice_id TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    purpose TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',      -- open | paid | expired
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT '',
    paid_at TEXT,
    payment_id INTEGER,
    pk_payment_id TEXT NOT NULL DEFAULT '',
    subscription_id INTEGER
);
"""
LIVE_DAYS = 14   # столько дней ждём оплату по ссылке
ROBOT = {"id": 0, "name": "PayKeeper", "role": "admin"}


class PayLinkError(Exception):
    pass


def init(con):
    con.executescript(SCHEMA)
    if "subscription_id" not in {r[1] for r in con.execute("PRAGMA table_info(pay_links)")}:
        con.execute("ALTER TABLE pay_links ADD COLUMN subscription_id INTEGER")


def _now() -> str:
    return datetime.now().isoformat(timespec="minutes")


def ready(con) -> bool:
    return all(fiscal.cfg(con, k) for k in ("fx_pk_server", "fx_pk_user", "fx_pk_password"))


def create(con, sid: int, amount: int, user: dict, purpose: str = "", subscription_id: int | None = None) -> dict:
    if not ready(con):
        raise PayLinkError("PayKeeper не подключён: Чеки → Подключение, адрес, логин и пароль PayKeeper")
    amount = int(amount or 0)
    if amount <= 0:
        raise PayLinkError("Укажите сумму")
    s = con.execute("""SELECT s.name, (SELECT p.name FROM parents p WHERE p.family_id=s.family_id ORDER BY p.id LIMIT 1),
                       (SELECT p.phone FROM parents p WHERE p.family_id=s.family_id AND p.phone!='' ORDER BY p.id LIMIT 1)
                       FROM students s WHERE s.id=?""", (sid,)).fetchone()
    if not s:
        raise PayLinkError("Ученик не найден")
    purpose = (purpose or "").strip() or f"Занятия футболом: {s[0]}"
    orderid = f"crm{sid}-{secrets.token_hex(3)}"
    try:
        token = (fiscal.pk_call(con, "/info/settings/token/") or {}).get("token")
        if not token:
            raise PayLinkError("PayKeeper не выдал токен для счёта")
        phone = re.sub(r"[^\d+]", "", s[2] or "")
        em = con.execute("""SELECT p.email FROM parents p JOIN students s ON s.family_id=p.family_id
                            WHERE s.id=? AND p.email != '' ORDER BY p.id LIMIT 1""", (sid,)).fetchone()
        js = fiscal.pk_call(con, "/change/invoice/preview/", form={
            "pay_amount": f"{amount:.2f}", "clientid": s[1] or s[0], "orderid": orderid,
            "service_name": purpose, "client_phone": phone,
            **({"client_email": em[0]} if em else {}), "token": token}) or {}
    except fiscal.FxError as e:
        raise PayLinkError(str(e))
    inv = str(js.get("invoice_id") or "")
    url = js.get("invoice_url") or (f"{fiscal.cfg(con, 'fx_pk_server').rstrip('/')}/bill/{inv}/" if inv else "")
    if not url:
        raise PayLinkError("PayKeeper не вернул ссылку на оплату")
    lid = con.execute("""INSERT INTO pay_links(student_id, amount, orderid, invoice_id, url, purpose, created_at, created_by, subscription_id)
                         VALUES (?,?,?,?,?,?,?,?,?)""", (sid, amount, orderid, inv, url, purpose, _now(), user.get("name", ""),
                                                         subscription_id)).lastrowid
    con.execute("INSERT INTO history(student_id, ts, user_name, kind, text) VALUES (?,?,?,?,?)",
                (sid, _now(), user.get("name", ""), "payment", f"Ссылка на оплату {amount} ₽ отправлена родителю"))
    return {"id": lid, "url": url, "amount": amount, "purpose": purpose}


def listing(con, sid: int) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT id, amount, url, purpose, status, created_at, created_by, paid_at FROM pay_links WHERE student_id=? ORDER BY id DESC LIMIT 10",
        (sid,))]


def cancel(con, lid: int) -> None:
    con.execute("UPDATE pay_links SET status='expired' WHERE id=? AND status='open'", (lid,))


def apply_payments(con, pays: list[dict]) -> int:
    """Оплаченные счета PayKeeper → оплаты в CRM. Каждый платёж PayKeeper зачисляется один раз."""
    n = 0
    for p in pays:
        if str(p.get("status")) not in ("success", "obtained", "stuck"):
            continue
        link = con.execute("SELECT * FROM pay_links WHERE orderid=? AND status!='paid'", (str(p.get("orderid") or ""),)).fetchone()
        if not link:
            continue
        amount = int(round(float(str(p.get("pay_amount") or link["amount"]).replace(",", "."))))
        paid_at = str(p.get("success_datetime") or p.get("obtain_datetime") or _now())[:16].replace(" ", "T")
        pid = money.add_payment(con, link["student_id"], amount, "online", ROBOT, subscription_id=link["subscription_id"],
                                comment=f"по ссылке PayKeeper №{p.get('id', '')}", paid_at=paid_at)
        con.execute("UPDATE pay_links SET status='paid', paid_at=?, payment_id=?, pk_payment_id=? WHERE id=?",
                    (paid_at, pid, str(p.get("id", "")), link["id"]))
        n += 1
    return n


def sync(con, today: date | None = None) -> int:
    """Раз в 10 минут: если есть неоплаченные ссылки, забрать оплаты PayKeeper за их срок и зачислить."""
    today = today or date.today()
    con.execute("UPDATE pay_links SET status='expired' WHERE status='open' AND created_at < ?",
                ((datetime.now() - timedelta(days=LIVE_DAYS)).isoformat(timespec="minutes"),))
    first = con.execute("SELECT MIN(created_at) FROM pay_links WHERE status='open'").fetchone()[0]
    if not first or not ready(con):
        return 0
    return apply_payments(con, fiscal.fetch_pk_payments(con, date.fromisoformat(first[:10]), today))
