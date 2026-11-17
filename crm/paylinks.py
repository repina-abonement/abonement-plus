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
    columns = {r[1] for r in con.execute("PRAGMA table_info(pay_links)")}
    for name, definition in {'subscription_id':'INTEGER','receipt_token':"TEXT NOT NULL DEFAULT ''",'receipt_email':"TEXT NOT NULL DEFAULT ''",'invoice_url':"TEXT NOT NULL DEFAULT ''",'checkout_state':"TEXT NOT NULL DEFAULT ''"}.items():
        if name not in columns:
            con.execute(f"ALTER TABLE pay_links ADD COLUMN {name} {definition}")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS pay_link_receipt_token ON pay_links(receipt_token) WHERE receipt_token!=''")


def _now() -> str:
    return datetime.now().isoformat(timespec="minutes")


def ready(con) -> bool:
    return all(fiscal.cfg(con, k) for k in ("fx_pk_server", "fx_pk_user", "fx_pk_password"))


def create(con, sid: int, amount: int, user: dict, purpose: str = "", subscription_id: int | None = None, full_payment: bool = False) -> dict:
    if not ready(con):
        raise PayLinkError("PayKeeper не подключён: Чеки → Подключение, адрес, логин и пароль PayKeeper")
    amount = int(amount or 0)
    if amount <= 0:
        raise PayLinkError("Укажите сумму")
    if subscription_id:
        from . import program, pricing
        row=con.execute("SELECT student_id,status FROM subscriptions WHERE id=?",(subscription_id,)).fetchone()
        if not row or row["student_id"]!=sid or row["status"]=='cancelled':
            raise PayLinkError("Абонемент не найден или отменён")
        program.reprice(con,subscription_id)
        pricing.reprice(con,subscription_id)
        price=con.execute("SELECT price FROM subscriptions WHERE id=?",(subscription_id,)).fetchone()[0]
        left=0 if row["status"]=="paid" else max(0,price-money.balance(con,sid)["covered"].get(subscription_id,0))
        if full_payment and amount!=left:
            raise PayLinkError("Сумма к оплате изменилась. Обновите расчёт и создайте ссылку заново")
    s = con.execute("""SELECT s.name, (SELECT p.name FROM parents p WHERE p.family_id=s.family_id ORDER BY p.id LIMIT 1),
                       (SELECT p.phone FROM parents p WHERE p.family_id=s.family_id AND p.phone!='' ORDER BY p.id LIMIT 1)
                       FROM students s WHERE s.id=?""", (sid,)).fetchone()
    if not s:
        raise PayLinkError("Ученик не найден")
    purpose = (purpose or "").strip() or f"Занятия футболом: {s[0]}"
    orderid = f"crm{sid}-{secrets.token_hex(3)}"
    from . import comms
    receipt_token = secrets.token_urlsafe(32)
    base = (comms.get(con, 'public_url') or 'https://ab-plus.ru').rstrip('/')
    url = base + '/pay/' + receipt_token
    lid = con.execute("""INSERT INTO pay_links(student_id,amount,orderid,url,purpose,created_at,created_by,subscription_id,receipt_token,checkout_state)
                         VALUES (?,?,?,?,?,?,?,?,?,'new')""",
                      (sid,amount,orderid,url,purpose,_now(),user.get('name',''),subscription_id,receipt_token)).lastrowid
    con.execute("INSERT INTO history(student_id, ts, user_name, kind, text) VALUES (?,?,?,?,?)",
                (sid, _now(), user.get("name", ""), "payment", f"Ссылка на оплату {amount} ₽ отправлена родителю"))
    return {"id": lid, "url": url, "amount": amount, "purpose": purpose}


def receipt_link(con, token):
    if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{40,64}',token):
        raise PayLinkError('Ссылка не найдена')
    link = con.execute('SELECT * FROM pay_links WHERE receipt_token=?',(token,)).fetchone()
    if not link: raise PayLinkError('Ссылка не найдена')
    if link['status'] != 'open':
        raise PayLinkError('Эта ссылка уже оплачена или недоступна. Уточните у администратора')
    try: expired = datetime.fromisoformat(link['created_at']) + timedelta(days=LIVE_DAYS) <= datetime.now()
    except (TypeError,ValueError): expired = True
    if expired: raise PayLinkError('Срок действия ссылки истёк. Попросите администратора прислать новую')
    if link['subscription_id']:
        sub=con.execute('SELECT status,student_id,price FROM subscriptions WHERE id=?',(link['subscription_id'],)).fetchone()
        if not sub or sub['status'] in ('cancelled','paid'):
            raise PayLinkError('Абонемент отменён или уже оплачен. Уточните у администратора')
        left=max(0,sub['price']-money.balance(con,sub['student_id'])['covered'].get(link['subscription_id'],0))
        if link['amount']>left:raise PayLinkError('Сумма к оплате изменилась. Попросите администратора прислать новую ссылку')
    return link


def receipt_email(value):
    if not isinstance(value,str): raise PayLinkError('Укажите корректный email для чека')
    email=value.strip()
    if len(email)>254 or email.count('@')!=1 or any(c.isspace() for c in email):
        raise PayLinkError('Укажите корректный email для чека')
    local,domain=email.split('@')
    if not local or len(local)>64 or local.startswith('.') or local.endswith('.') or '..' in local or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+",local):
        raise PayLinkError('Укажите корректный email для чека')
    try: labels=domain.encode('idna').decode('ascii').split('.')
    except UnicodeError: raise PayLinkError('Укажите корректный email для чека') from None
    if len('.'.join(labels))>253 or len(labels)<2 or not (labels[-1].isalpha() or labels[-1].startswith('xn--')) or any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?',x) for x in labels):
        raise PayLinkError('Укажите корректный email для чека')
    return email


def checkout(con, token, email):
    from urllib.parse import urlparse
    email=receipt_email(email)
    link=receipt_link(con,token)
    if link['invoice_id'] and link['invoice_url']:
        return {'url':link['invoice_url']}
    if link['checkout_state'] in ('creating','uncertain'):
        raise PayLinkError('Нет подтверждения создания счёта. Обратитесь к администратору: повторный счёт автоматически не создаётся')
    if not ready(con): raise PayLinkError('Оплата временно недоступна. Сообщите администратору')
    try:
        gateway_token=(fiscal.pk_call(con,'/info/settings/token/') or {}).get('token')
    except fiscal.FxError:
        raise PayLinkError('Не удалось подключиться к оплате. Попробуйте позже') from None
    if not gateway_token: raise PayLinkError('Не удалось подключиться к оплате. Попробуйте позже')
    # No family email is read here. The customer chooses the receipt address.
    customer=con.execute("""SELECT s.name,(SELECT p.name FROM parents p WHERE p.family_id=s.family_id ORDER BY p.id LIMIT 1),
                            (SELECT p.phone FROM parents p WHERE p.family_id=s.family_id AND p.phone!='' ORDER BY p.id LIMIT 1)
                            FROM students s WHERE s.id=?""",(link['student_id'],)).fetchone()
    if not customer: raise PayLinkError('Оплата временно недоступна. Сообщите администратору')
    con.execute("UPDATE pay_links SET checkout_state='creating',receipt_email=? WHERE id=?",(email,link['id']))
    try:
        result=fiscal.pk_call(con,'/change/invoice/preview/',form={'pay_amount':f"{link['amount']:.2f}",
            'clientid':customer[1] or customer[0],'orderid':link['orderid'],'service_name':link['purpose'],
            'client_phone':re.sub(r'[^\d+]','',customer[2] or ''),'client_email':email,'token':gateway_token}) or {}
        invoice_id=str(result.get('invoice_id') or '')
        target=result.get('invoice_url') or (f"{fiscal.cfg(con,'fx_pk_server').rstrip('/')}/bill/{invoice_id}/" if invoice_id else '')
        parsed=urlparse(target)
        if not invoice_id or parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Invalid gateway acknowledgement')
    except Exception:
        con.execute("UPDATE pay_links SET checkout_state='uncertain' WHERE id=?",(link['id'],))
        raise PayLinkError('Нет подтверждения создания счёта. Сообщите администратору; повторный счёт автоматически не создаётся') from None
    con.execute("UPDATE pay_links SET invoice_id=?,invoice_url=?,checkout_state='ready' WHERE id=?",(invoice_id,target,link['id']))
    return {'url':target}


def listing(con, sid: int) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT id, amount, url, purpose, status, created_at, created_by, paid_at,subscription_id FROM pay_links WHERE student_id=? ORDER BY id DESC LIMIT 10",
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
        if link["subscription_id"]:
            from . import program, pricing
            try:paid_day=date.fromisoformat(paid_at[:10])
            except ValueError:paid_day=date.today()
            program.reprice(con,link["subscription_id"],on=paid_day)
            pricing.reprice(con,link["subscription_id"],on=paid_day)
        pid = money.add_payment(con, link["student_id"], amount, "paykeeper", ROBOT, subscription_id=link["subscription_id"],
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
