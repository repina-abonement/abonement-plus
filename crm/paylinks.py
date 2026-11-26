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
    con.execute("""CREATE TABLE IF NOT EXISTS pay_link_subscriptions (
        link_id INTEGER NOT NULL REFERENCES pay_links(id),subscription_id INTEGER NOT NULL REFERENCES subscriptions(id),
        amount INTEGER NOT NULL,PRIMARY KEY(link_id,subscription_id))""")
    columns = {r[1] for r in con.execute("PRAGMA table_info(pay_links)")}
    for name, definition in {'subscription_id':'INTEGER','receipt_token':"TEXT NOT NULL DEFAULT ''",'receipt_email':"TEXT NOT NULL DEFAULT ''",'invoice_url':"TEXT NOT NULL DEFAULT ''",'checkout_state':"TEXT NOT NULL DEFAULT ''",
            'full_payment':'INTEGER','scope_snapshot':"TEXT NOT NULL DEFAULT ''"}.items():
        if name not in columns:
            con.execute(f"ALTER TABLE pay_links ADD COLUMN {name} {definition}")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS pay_link_receipt_token ON pay_links(receipt_token) WHERE receipt_token!=''")


def _now() -> str:
    return datetime.now().isoformat(timespec="minutes")


def ready(con) -> bool:
    return all(fiscal.cfg(con, k) for k in ("fx_pk_server", "fx_pk_user", "fx_pk_password"))


def create(con, sid: int, amount: int, user: dict, purpose: str = "", subscription_id: int | None = None,
           full_payment: bool | None = False, replace_open: bool = False) -> dict:
    if not ready(con):
        raise PayLinkError("PayKeeper не подключён: Чеки → Подключение, адрес, логин и пароль PayKeeper")
    amount = int(amount or 0)
    if amount <= 0:
        raise PayLinkError("Укажите сумму")
    if full_payment is not None and type(full_payment) is not bool:
        raise PayLinkError('Подтвердите, на всю ли сумму счёта создаётся ссылка')
    if type(replace_open) is not bool:
        raise PayLinkError('Не удалось проверить замену прежней ссылки')
    if full_payment is True and not subscription_id:
        raise PayLinkError('Для полной оплаты выберите исходный счёт')
    if subscription_id:
        from . import program, pricing, services as sv
        row=con.execute("SELECT student_id,status,kind FROM subscriptions WHERE id=?",(subscription_id,)).fetchone()
        if not row or row["student_id"]!=sid or row["status"]=='cancelled':
            raise PayLinkError("Абонемент не найден или отменён")
        program.reprice(con,subscription_id,on=sv.today())
        pricing.reprice(con,subscription_id,on=sv.today())
        price=con.execute("SELECT price FROM subscriptions WHERE id=?",(subscription_id,)).fetchone()[0]
        left=0 if row["status"]=="paid" else max(0,price-money.balance(con,sid)["covered"].get(subscription_id,0))
        if left<=0:
            raise PayLinkError('Этот счёт уже оплачен. Ссылка на оплату не требуется')
        if amount>left or full_payment is not False and amount!=left:
            raise PayLinkError("Сумма к оплате изменилась. Обновите расчёт и создайте ссылку заново")
        if replace_open:
            previous = con.execute("""SELECT id,invoice_id,checkout_state FROM pay_links
                WHERE student_id=? AND subscription_id=? AND status='open' ORDER BY id""",
                (sid, subscription_id)).fetchall()
            if any(row['invoice_id'] or row['checkout_state'] in ('creating','uncertain','ready') for row in previous):
                raise PayLinkError('По прежней ссылке PayKeeper уже начал создание счёта. Не создавайте второй счёт — сначала проверьте прежнюю ссылку')
    s = con.execute("""SELECT s.name, (SELECT p.name FROM parents p WHERE p.family_id=s.family_id ORDER BY p.id LIMIT 1),
                       (SELECT p.phone FROM parents p WHERE p.family_id=s.family_id AND p.phone!='' ORDER BY p.id LIMIT 1)
                       FROM students s WHERE s.id=?""", (sid,)).fetchone()
    if not s:
        raise PayLinkError("Ученик не найден")
    if subscription_id:
        purpose=customer_payment_purpose(s[0],row['kind']=='single')
    else:
        purpose = (purpose or "").strip() or customer_payment_purpose(s[0])
    orderid = f"crm{sid}-{secrets.token_hex(3)}"
    from . import comms
    receipt_token = secrets.token_urlsafe(32)
    base = (comms.get(con, 'public_url') or 'https://ab-plus.ru').rstrip('/')
    url = base + '/pay/' + receipt_token
    from . import paylinkguard
    scope=paylinkguard.snapshot(con,sid,subscription_id,full_payment=full_payment)
    lid = con.execute("""INSERT INTO pay_links(student_id,amount,orderid,url,purpose,created_at,created_by,subscription_id,receipt_token,checkout_state,full_payment,scope_snapshot)
                         VALUES (?,?,?,?,?,?,?,?,?,'new',?,?)""",
                      (sid,amount,orderid,url,purpose,_now(),user.get('name',''),subscription_id,receipt_token,
                       None if full_payment is None else int(full_payment),scope)).lastrowid
    if replace_open and subscription_id:
        con.execute("""UPDATE pay_links SET status='expired' WHERE student_id=? AND subscription_id=?
            AND status='open' AND id!=? AND invoice_id='' AND checkout_state IN ('','new')""", (sid, subscription_id, lid))
    con.execute("INSERT INTO history(student_id, ts, user_name, kind, text) VALUES (?,?,?,?,?)",
                (sid, _now(), user.get("name", ""), "payment", f"Создана ссылка на оплату {amount} ₽; email для чека введёт клиент"))
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
    from . import paylinkguard
    checked=paylinkguard.inspect(con,link)
    if not checked['valid']:
        due = checked['current_due']
        scopes = checked['invoice_scope']
        if checked.get('amount_changed') and due > link['amount']:
            old_amount = f"{link['amount']:,}".replace(',', ' ')
            new_amount = f"{due:,}".replace(',', ' ')
            changed = [scope for scope in scopes if scope['current_due'] != scope['amount']]
            if changed and all(scope['standard_late_fee'] for scope in changed):
                raise PayLinkError(
                    'Поможем обновить ссылку на оплату 😊\n\n'
                    'Продление абонемента на следующий месяц - до 25 числа включительно. '
                    'При оплате с 26 числа - надбавка 5%, а также открываем возможность '
                    'занять свободные места желающим из очереди ожидания\n\n'
                    f'Эта ссылка была выставлена на {old_amount} ₽. Сейчас к оплате {new_amount} ₽. '
                    'Ссылка со старой суммой больше не принимает оплату\n\n'
                    'Напишите нам в чат школы, и мы уточним наличие места и пришлём ссылку с актуальной суммой'
                )
            raise PayLinkError(
                'Поможем обновить ссылку на оплату 😊\n\n'
                f'Эта ссылка была выставлена на {old_amount} ₽. Сумма счёта изменилась, и сейчас к оплате '
                f'{new_amount} ₽. Ссылка со старой суммой больше не принимает оплату\n\n'
                'Напишите нам в чат школы, и мы пришлём ссылку с актуальной суммой'
            )
        raise PayLinkError(checked['reason'])
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
    # A payment link identifies a family, not a particular secondary contact.
    parents = con.execute(
        """SELECT p.id, p.is_primary FROM parents p
           JOIN students s ON s.family_id=p.family_id WHERE s.id=?
           ORDER BY p.is_primary DESC, p.id LIMIT 2""", (link['student_id'],)
    ).fetchall()
    if len(parents) == 1 or (parents and parents[0]['is_primary'] and not parents[1]['is_primary']):
        con.execute("UPDATE parents SET email=? WHERE id=? AND TRIM(COALESCE(email,''))=''",
                    (email, parents[0]['id']))
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
        parts=con.execute('SELECT subscription_id,amount FROM pay_link_subscriptions WHERE link_id=? ORDER BY subscription_id',(link['id'],)).fetchall()
        if not parts and link['subscription_id']:
            parts=[{'subscription_id':link['subscription_id'],'amount':link['amount']}]
        if parts:
            from . import program, pricing
            try:paid_day=date.fromisoformat(paid_at[:10])
            except ValueError:paid_day=date.today()
            for part in parts:
                sub=con.execute('SELECT status FROM subscriptions WHERE id=? AND student_id=?',
                    (part['subscription_id'],link['student_id'])).fetchone()
                if sub and sub['status']!='cancelled':
                    program.reprice(con,part['subscription_id'],on=paid_day)
                    pricing.reprice(con,part['subscription_id'],on=paid_day)
        remaining=amount
        invoice_parts=[]
        covered_before=money.balance(con,link['student_id'])['covered']
        for part in parts:
            if remaining<=0:
                break
            # Each slice belongs to its original link component. A slice which
            # is now paid/cancelled stays free; it cannot spill into the next
            # component or replace another historical payment's allocation.
            portion=min(max(0,part['amount']),remaining)
            if portion:
                sub=con.execute('SELECT price,status FROM subscriptions WHERE id=? AND student_id=?',
                    (part['subscription_id'],link['student_id'])).fetchone()
                due=max(0,sub['price']-covered_before.get(part['subscription_id'],0)) if sub and sub['status']!='cancelled' else 0
                # Zero is trusted purpose metadata, never a zero-money income.
                invoice_parts.append({'subscription_id':part['subscription_id'],'amount':min(portion,due)})
                remaining-=portion
        pid = money.add_payment(con, link["student_id"], amount, "paykeeper", ROBOT, subscription_id=link["subscription_id"],
                                comment=f"по ссылке PayKeeper №{p.get('id', '')}", paid_at=paid_at,
                                invoice_parts=invoice_parts,allow_cancelled_targets=True)
        con.execute("UPDATE pay_links SET status='paid', paid_at=?, payment_id=?, pk_payment_id=? WHERE id=?",
                    (paid_at, pid, str(p.get("id", "")), link["id"]))
        n += 1
    return n


def sync(con, today: date | None = None) -> int:
    """Раз в 10 минут: если есть неоплаченные ссылки, забрать оплаты PayKeeper за их срок и зачислить."""
    if con.in_transaction:
        raise RuntimeError("PayKeeper polling requires an idle connection")
    today = today or date.today()
    con.execute("UPDATE pay_links SET status='expired' WHERE status='open' AND created_at < ?",
                ((datetime.now() - timedelta(days=LIVE_DAYS)).isoformat(timespec="minutes"),))
    first = con.execute("SELECT MIN(created_at) FROM pay_links WHERE status='open'").fetchone()[0]
    if not first or not ready(con):
        return 0
    payments = fiscal.fetch_pk_payments(con, date.fromisoformat(first[:10]), today)
    if not payments:
        return 0
    con.execute("BEGIN IMMEDIATE")
    try:
        applied = apply_payments(con, payments)
        con.commit()
        return applied
    except BaseException:
        con.rollback()
        raise


def payment_page(con,token):
    """Only details of this opaque payment link, never family contacts/history."""
    import json
    from . import services as sv
    link=receipt_link(con,token)
    student=con.execute('SELECT id,name,family_id FROM students WHERE id=?',(link['student_id'],)).fetchone()
    if not student:raise PayLinkError('Ссылка недоступна')
    email=''
    # Once an invoice exists its fiscal address cannot silently change.
    candidates=[link['receipt_email']] if link['invoice_id'] else []
    if not link['invoice_id']:
        candidates.extend(r[0] for r in con.execute("""SELECT p.receipt_email FROM pay_links p JOIN students s ON s.id=p.student_id
            WHERE s.family_id=? AND p.receipt_email!='' AND p.checkout_state='ready'
            ORDER BY p.id DESC""",(student['family_id'],)))
        candidates.extend(r[0] for r in con.execute("SELECT email FROM parents WHERE family_id=? AND email!='' ORDER BY is_primary DESC,id",(student['family_id'],)))
    for candidate in candidates:
        try:email=receipt_email(candidate);break
        except PayLinkError:continue
    out={'amount':link['amount'],'invoice_ready':bool(link['invoice_id']),'email':email,'child':student['name'],'purpose':link['purpose'] or 'Оплата занятий','privacy_url':'/legal/privacy'}
    parts=con.execute('SELECT subscription_id,amount FROM pay_link_subscriptions WHERE link_id=? ORDER BY subscription_id',(link['id'],)).fetchall()
    if parts:
        out['subscriptions']=[]
        for part in parts:
            sub=con.execute('SELECT * FROM subscriptions WHERE id=?',(part['subscription_id'],)).fetchone()
            current=sv.payment_quote(con,sub['id'],sv.today().isoformat())
            group=con.execute('SELECT g.name,b.name AS branch FROM enrollments e JOIN groups g ON g.id=e.group_id JOIN branches b ON b.id=g.branch_id WHERE e.id=?',(sub['enrollment_id'],)).fetchone()
            out['subscriptions'].append({'month':sub['month'],'price':current['price'],'amount':part['amount'],'lessons':sub['lessons'],
                'paid':current['paid'],'remaining':current['total'],'late_fee':current['late_fee'],
                'group':dict(group) if group else {},'discount_name':current['discount_name'],'discount_amount':current['discount_amount']})
        return out
    if not link['subscription_id']:return out
    sub=con.execute('SELECT * FROM subscriptions WHERE id=? AND student_id=?',(link['subscription_id'],student['id'])).fetchone()
    if not sub:raise PayLinkError('Ссылка недоступна')
    current=sv.payment_quote(con,sub['id'],sv.today().isoformat())
    sub=dict(sub,price=current['price'],late_fee=current['late_fee'],
        discount_name=current['discount_name'],discount_amount=current['discount_amount'])
    covered=current['paid']
    schedule=json.loads(sub['lesson_schedule'] or '[]') or json.loads(sub['program_selection'] or '{}').get('contract',{}).get('components',[])
    groups=[]
    for part in schedule:
        group=con.execute('SELECT g.name,b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id WHERE g.id=?',(part.get('group_id'),)).fetchone()
        groups.append({'name':part.get('group') or (group['name'] if group else ''),'branch':group['branch'] if group else '', 'dates':sorted(set(part.get('dates') or []))})
    if not groups:
        group=con.execute('SELECT g.name,b.name AS branch FROM enrollments e JOIN groups g ON g.id=e.group_id JOIN branches b ON b.id=g.branch_id WHERE e.id=?',(sub['enrollment_id'],)).fetchone()
        if group:groups=[{'name':group['name'],'branch':group['branch'],'dates':[]}]
    single=con.execute('SELECT date,g.name,b.name AS branch FROM single_visits v JOIN groups g ON g.id=v.group_id JOIN branches b ON b.id=g.branch_id WHERE v.subscription_id=?',(sub['id'],)).fetchone() if sub['kind']=='single' else None
    if single:groups=[{'name':single['name'],'branch':single['branch'],'dates':[single['date']]}]
    out['subscription']={'month':sub['month'],'kind':sub['kind'],'tariff':sub['tariff'],'lessons':sub['lessons'],'valid_from':sub['valid_from'],'valid_until':sub['valid_until'],'price':sub['price'],'paid':covered,'remaining':current['total'],'discount_name':sub['discount_name'],'discount_amount':sub['discount_amount'],'late_fee':sub['late_fee'],'groups':groups,'excluded_dates':json.loads(sub['excluded_dates'] or '[]')}
    return out


def create_bundle(con,sid,sub_ids,user):
    from . import services as sv, logic
    if len(set(sub_ids))!=len(sub_ids) or len(sub_ids)<2:raise PayLinkError('Выберите несколько разных абонементов')
    parts=[]
    for sub_id in sub_ids:
        sub=con.execute("SELECT * FROM subscriptions WHERE id=? AND student_id=? AND status!='cancelled'",(sub_id,sid)).fetchone()
        if not sub:raise PayLinkError('Абонемент не найден')
        due=sv.payment_quote(con,sub_id,sv.today().isoformat())['total']
        if due:parts.append((sub_id,due,sub['month']))
    if not parts:raise PayLinkError('Все выбранные абонементы уже оплачены')
    child=sv.get_student_row(con,sid)['name']
    purpose=customer_payment_purpose(child)
    link=create(con,sid,sum(amount for _,amount,_ in parts),user,purpose)
    con.executemany('INSERT INTO pay_link_subscriptions(link_id,subscription_id,amount) VALUES (?,?,?)',[(link['id'],sub,amount) for sub,amount,_ in parts])
    from . import paylinkguard
    con.execute('UPDATE pay_links SET full_payment=1,scope_snapshot=? WHERE id=?',
        (paylinkguard.snapshot(con,sid,parts=[{'subscription_id':sub} for sub,_,_ in parts],full_payment=True),link['id']))
    return link


def customer_payment_purpose(child,single=False):
    name=' '.join(str(child or '').split())
    return f"{'Разовое занятие' if single else 'Абонемент'} {name}. Футболика"
