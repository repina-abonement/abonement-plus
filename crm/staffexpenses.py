"""Shared operational expenses and owner-recorded reimbursements. No money is sent."""
import base64,binascii,json,re,hashlib
from decimal import Decimal,InvalidOperation
from datetime import date,datetime
from zoneinfo import ZoneInfo
from . import offlineplanner


class ExpenseError(ValueError):pass
FUNDING={'personal':'Личные деньги','owner':'Оплатила Юлия','cash':'Конверт','advance':'Выданы Юлией заранее','company_card':'Корпоративная карта'}


def init(con):
    con.executescript('''CREATE TABLE IF NOT EXISTS staff_expenses(id INTEGER PRIMARY KEY,title TEXT NOT NULL,
      amount INTEGER NOT NULL,paid_date TEXT NOT NULL,branch_id INTEGER REFERENCES branches(id),paid_by_id INTEGER NOT NULL REFERENCES users(id),
      funding TEXT NOT NULL,note TEXT NOT NULL DEFAULT '',created_by INTEGER NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
      updated_by INTEGER NOT NULL,updated_at TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,cancelled INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS staff_expense_batches(actor_id INTEGER NOT NULL,request_id TEXT NOT NULL,signature TEXT NOT NULL,result TEXT NOT NULL,PRIMARY KEY(actor_id,request_id));
    CREATE TABLE IF NOT EXISTS staff_reimbursements(id INTEGER PRIMARY KEY,expense_id INTEGER NOT NULL REFERENCES staff_expenses(id),amount INTEGER NOT NULL,
      transferred_at TEXT NOT NULL,method TEXT NOT NULL,note TEXT NOT NULL DEFAULT '',recorded_by INTEGER NOT NULL,recorded_at TEXT NOT NULL,
      received_at TEXT,received_by INTEGER,voided INTEGER NOT NULL DEFAULT 0,request_id TEXT NOT NULL,UNIQUE(recorded_by,request_id));
    CREATE TABLE IF NOT EXISTS staff_expense_history(id INTEGER PRIMARY KEY,expense_id INTEGER NOT NULL,actor_id INTEGER NOT NULL,at TEXT NOT NULL,action TEXT NOT NULL,details TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS staff_expense_receipts(id INTEGER PRIMARY KEY,expense_id INTEGER NOT NULL REFERENCES staff_expenses(id),mime TEXT NOT NULL,data BLOB NOT NULL,uploaded_by INTEGER NOT NULL,at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS staff_expenses_day ON staff_expenses(paid_date,id);''')
    if 'category' not in {r[1] for r in con.execute('PRAGMA table_info(staff_expenses)')}:
        con.execute("ALTER TABLE staff_expenses ADD COLUMN category TEXT NOT NULL DEFAULT ''")


CATEGORIES={'printing':'Печать','decor':'Украшения','supplies':'Хозтовары и инвентарь','laundry':'Стирка','maintenance':'Ремонт и работы','other':'Другое'}

def category_for(title):
    value=str(title).casefold()
    for key,pattern in [('laundry',r'стир|прачеч'),('printing',r'печат|распечат|полиграф|прайс'),('decor',r'ёлк|елк|украш|декор|прищеп|пергамент|гирлянд'),('maintenance',r'ремонт|техник|электрик|сантехник|монтаж'),('supplies',r'крюч|хоз|инвентар|бумаг|мыл|салфет|вод[ауы]|моющ')]:
        if re.search(pattern,value):return key
    return 'other'


def stamp():return datetime.now(ZoneInfo('Europe/Moscow')).isoformat(timespec='seconds')

def allowed(con,user):return user.get('role')=='admin' or offlineplanner.member(con,user) or offlineplanner.can_assign(con,user)

def require(con,user):
    if not allowed(con,user):raise ExpenseError('Нет доступа к общему журналу расходов')

def minor(value):
    try:
        amount=Decimal(str(value).replace(' ','').replace('\u00a0','').replace(',','.'))*100
        if not amount.is_finite() or amount<=0 or amount!=amount.to_integral_value() or amount>1000000000:raise ValueError()
        return int(amount)
    except (InvalidOperation,ValueError):raise ExpenseError('Укажите положительную сумму с точностью до копейки')

def valid_day(value):
    try:
        value=str(value)
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):raise ValueError()
        parsed=date.fromisoformat(value)
        if parsed>datetime.now(ZoneInfo('Europe/Moscow')).date():raise ValueError()
        return value
    except ValueError:raise ExpenseError('Укажите фактическую дату не позже сегодняшней')

def history(con,eid,user,action,details):
    con.execute('INSERT INTO staff_expense_history(expense_id,actor_id,at,action,details) VALUES(?,?,?,?,?)',(eid,user['id'],stamp(),action,json.dumps(details,ensure_ascii=False)))

def parse(text):
    if not isinstance(text,str) or len(text)>30000:raise ExpenseError('Слишком большой список')
    rows=[];errors=[];warnings=[]
    money=r'\d[\d \u00a0]*(?:[.,]\d{1,2})?'
    for number,line in enumerate(text.splitlines(),1):
        line=line.strip()
        if not line:continue
        if re.match(r'^(итого|всего|сумма)\b',line,re.I):warnings.append(f'Строка {number}: итог не считается отдельным расходом');continue
        start=re.fullmatch(r'('+money+r')\s*[-–—:]\s*(.+)',line)
        end=re.fullmatch(r'(.+?)\s+('+money+r')\s*(?:₽|руб\.?|р\.?)?',line,re.I)
        if start:amount,title=start.group(1),start.group(2)
        elif end:title,amount=end.group(1),end.group(2)
        else:errors.append(f'Строка {number}: не удалось определить сумму');continue
        try:rows.append({'title':title.strip(' -–—:'),'amount':minor(amount)/100,'category':category_for(title)})
        except ExpenseError as e:errors.append(f'Строка {number}: {e}')
    if len(rows)>100:raise ExpenseError('За один раз можно внести до 100 расходов')
    return {'rows':rows,'errors':errors,'warnings':warnings,'total_minor':sum(minor(r['amount']) for r in rows)}

def owner_of(con):
    """Resolve the owner only from unambiguous active admin accounts."""
    people=[dict(r) for r in con.execute("SELECT id,name FROM users WHERE active=1 AND role='admin' ORDER BY id")]
    named=[p for p in people if re.search(r'\bюлия\b',p['name'].casefold())]
    person=named[0] if len(named)==1 else people[0] if len(people)==1 else None
    return {'id':person['id'],'name':person['name']} if person else None


def fields(con,data):
    title=str(data.get('title') or '').strip();note=str(data.get('note') or '').strip()
    if not title or len(title)>300 or len(note)>2000:raise ExpenseError('Проверьте описание расхода')
    try:payer=int(data.get('paid_by_id') or 0)
    except (TypeError,ValueError):raise ExpenseError('Выберите участника команды, который оплатил покупку')
    person=con.execute('SELECT * FROM users WHERE id=? AND active=1',(payer,)).fetchone()
    if not person or not allowed(con,dict(person)):raise ExpenseError('Выберите участника команды общего журнала, который оплатил покупку')
    branch=data.get('branch_id') or None
    if branch and not con.execute('SELECT 1 FROM branches WHERE id=?',(branch,)).fetchone():raise ExpenseError('Филиал не найден')
    source=data.get('funding')
    if source not in FUNDING:raise ExpenseError('Выберите источник денег')
    if source=='owner':
        owner=owner_of(con)
        if person['role']!='admin' or owner and payer!=owner['id']:
            raise ExpenseError('Для покупки, оплаченной Юлией, выберите её учётную запись плательщика')
    category=data.get('category') or category_for(title)
    if category not in CATEGORIES:raise ExpenseError('Выберите категорию')
    return dict(category=category,title=title,amount=minor(data.get('amount')),paid_date=valid_day(data.get('paid_date')),branch_id=branch,paid_by_id=int(payer),funding=source,note=note)


def create_batch(con,user,data):
    require(con,user)
    rows=data.get('rows');request=str(data.get('request_id') or '')
    if not isinstance(rows,list) or not 1<=len(rows)<=100 or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',request):raise ExpenseError('Проверьте список расходов')
    defaults=data.get('defaults') or {}
    if not isinstance(defaults,dict) or any(not isinstance(row,dict) for row in rows):raise ExpenseError('Проверьте строки расходов')
    prepared=[fields(con,defaults|row) for row in rows]
    signature=hashlib.sha256(json.dumps(prepared,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    old=con.execute('SELECT * FROM staff_expense_batches WHERE actor_id=? AND request_id=?',(user['id'],request)).fetchone()
    if old:
        if old['signature']!=signature:raise ExpenseError('Этот список уже сохранён с другими данными')
        return json.loads(old['result'])
    ids=[]
    for row in prepared:
        row.update(created_by=user['id'],created_at=stamp(),updated_by=user['id'],updated_at=stamp())
        eid=con.execute('INSERT INTO staff_expenses('+','.join(row)+') VALUES('+','.join('?' for _ in row)+')',tuple(row.values())).lastrowid
        history(con,eid,user,'created',row);ids.append(eid)
    result={'ids':ids};con.execute('INSERT INTO staff_expense_batches VALUES(?,?,?,?)',(user['id'],request,signature,json.dumps(result)))
    return result


def get_row(con,eid):
    row=con.execute('SELECT * FROM staff_expenses WHERE id=?',(eid,)).fetchone()
    if not row:raise ExpenseError('Расход не найден')
    return row


def edit(con,user,eid,data):
    require(con,user);old=get_row(con,eid)
    try:version=int(data.get('version') or 0)
    except (TypeError,ValueError):raise ExpenseError('Обновите журнал перед изменением')
    if version!=old['version']:raise ExpenseError('Запись уже изменили. Обновите журнал')
    if old['cancelled']:raise ExpenseError('Расход отменён')
    transfers=con.execute('SELECT count(*) FROM staff_reimbursements WHERE expense_id=? AND voided=0',(eid,)).fetchone()[0]
    if data.get('cancelled') is True:
        if transfers:raise ExpenseError('Нельзя отменить расход с отметками возмещения')
        values={'cancelled':1}
    else:
        values=fields(con,data)
        if transfers and any(values[k]!=old[k] for k in ['amount','paid_by_id','funding']):raise ExpenseError('После возмещения нельзя менять сумму, плательщика и источник денег')
    values.update(updated_by=user['id'],updated_at=stamp(),version=old['version']+1)
    con.execute('UPDATE staff_expenses SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),eid))
    history(con,eid,user,'edited',{'before':dict(old),'after':values});return {'id':eid}


def reimburse(con,user,eid,data):
    require(con,user)
    if user.get('role')!='admin':raise ExpenseError('Отметить передачу денег может только Юлия')
    row=get_row(con,eid)
    if row['cancelled'] or row['funding']!='personal':raise ExpenseError('Этот расход не требует возмещения личных денег')
    amount=minor(data.get('amount'));day=valid_day(data.get('transferred_at'));request=str(data.get('request_id') or '')
    method=data.get('method')
    if method not in ('transfer','cash') or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',request):raise ExpenseError('Проверьте способ передачи денег')
    old=con.execute('SELECT * FROM staff_reimbursements WHERE recorded_by=? AND request_id=?',(user['id'],request)).fetchone()
    if old:
        if old['expense_id']!=eid or old['amount']!=amount or old['transferred_at']!=day or old['method']!=method:raise ExpenseError('Эта отметка уже сохранена с другими данными')
        return {'id':old['id']}
    total=con.execute('SELECT coalesce(sum(amount),0) FROM staff_reimbursements WHERE expense_id=? AND voided=0',(eid,)).fetchone()[0]
    if amount!=row['amount']-total:raise ExpenseError('Отметьте получение всей оставшейся суммы')
    rid=con.execute('INSERT INTO staff_reimbursements(expense_id,amount,transferred_at,method,note,recorded_by,recorded_at,request_id) VALUES(?,?,?,?,?,?,?,?)',(eid,amount,day,method,str(data.get('note') or '')[:1000],user['id'],stamp(),request)).lastrowid
    history(con,eid,user,'money_transferred',{'id':rid,'amount':amount,'method':method,'date':day});return {'id':rid}


def mark_received(con,user,eid,data):
    require(con,user);row=get_row(con,eid)
    if row['cancelled'] or row['funding']!='personal' or row['paid_by_id']!=user['id']:
        raise ExpenseError('Возмещение отмечает участник команды, оплативший покупку личными деньгами')
    request=str(data.get('request_id') or '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',request):raise ExpenseError('Обновите страницу и попробуйте ещё раз')
    request='received:'+request
    previous=con.execute('SELECT * FROM staff_reimbursements WHERE recorded_by=? AND request_id=?',(user['id'],request)).fetchone()
    if previous:
        if previous['expense_id']!=eid or (data.get('amount') is not None and previous['amount']!=minor(data['amount'])):raise ExpenseError('Эта отметка уже сохранена с другими данными')
        return {'id':previous['id']}
    sent=con.execute('SELECT coalesce(sum(amount),0) FROM staff_reimbursements WHERE expense_id=? AND voided=0',(eid,)).fetchone()[0]
    left=row['amount']-sent
    if left<=0:return {'id':None,'already_reimbursed':True}
    amount=minor(data['amount']) if data.get('amount') is not None else left
    if amount!=left:raise ExpenseError('Частичное возмещение больше не используется. Отметьте всю оставшуюся сумму')
    now=stamp()
    rid=con.execute("""INSERT INTO staff_reimbursements(expense_id,amount,transferred_at,method,note,recorded_by,recorded_at,received_at,received_by,request_id)
        VALUES(?,?,?,'not_specified','Получение отмечено участником команды',?,?,?,?,?)""",(eid,amount,now[:10],user['id'],now,now,user['id'],request)).lastrowid
    history(con,eid,user,'reimbursement_received',{'id':rid,'amount':amount,'source':'employee_confirmation'})
    return {'id':rid}


def receipt_money(con,user,rid,data):
    require(con,user)
    transfer=con.execute('SELECT * FROM staff_reimbursements WHERE id=?',(rid,)).fetchone()
    if not transfer:raise ExpenseError('Отметка не найдена')
    row=get_row(con,transfer['expense_id'])
    if row['funding']=='owner':raise ExpenseError('Покупка оплачена Юлией и не требует возмещения')
    if data.get('voided') is True:
        own_receipt=transfer['method']=='not_specified' and transfer['recorded_by']==transfer['received_by'] and (user['id']==row['paid_by_id'] or user['role']=='admin')
        if not own_receipt and (user['role']!='admin' or transfer['received_at']):raise ExpenseError('Можно исправить свою отметку получения или неподтверждённую передачу денег от лица Юлии')
        if transfer['voided']:return {'id':rid}
        con.execute('UPDATE staff_reimbursements SET voided=1 WHERE id=?',(rid,));history(con,row['id'],user,'transfer_voided',{'id':rid})
    else:
        if data.get('received') is not True:raise ExpenseError('Подтвердите фактическое получение денег')
        if user['id']!=row['paid_by_id'] or transfer['voided']:raise ExpenseError('Получение подтверждает участник команды, оплативший покупку')
        if not transfer['received_at']:
            con.execute('UPDATE staff_reimbursements SET received_at=?,received_by=? WHERE id=?',(stamp(),user['id'],rid));history(con,row['id'],user,'money_received',{'id':rid,'amount':transfer['amount']})
    return {'id':rid}


def add_receipt(con,user,eid,data):
    require(con,user);get_row(con,eid)
    encoded=data.get('data')
    if not isinstance(encoded,str) or len(encoded)>5600000:raise ExpenseError('Фото должно быть не больше 4 МБ')
    try:raw=base64.b64decode(encoded,validate=True)
    except (ValueError,binascii.Error):raise ExpenseError('Не удалось прочитать фото')
    mime='image/jpeg' if raw.startswith(b'\xff\xd8\xff') else 'image/png' if raw.startswith(b'\x89PNG\r\n\x1a\n') else 'image/webp' if raw[:4]==b'RIFF' and raw[8:12]==b'WEBP' else ''
    if not mime or not 1<=len(raw)<=4*1024*1024:raise ExpenseError('Нужно фото JPG, PNG или WEBP до 4 МБ')
    if con.execute('SELECT count(*) FROM staff_expense_receipts WHERE expense_id=?',(eid,)).fetchone()[0]>=5:raise ExpenseError('Можно прикрепить до пяти фото')
    rid=con.execute('INSERT INTO staff_expense_receipts(expense_id,mime,data,uploaded_by,at) VALUES(?,?,?,?,?)',(eid,mime,raw,user['id'],stamp())).lastrowid
    history(con,eid,user,'receipt_added',{'id':rid});return {'id':rid}


def listing(con,user):
    require(con,user)
    rows=[dict(r) for r in con.execute('''SELECT e.*,p.name AS payer,c.name AS author,b.name AS branch_name FROM staff_expenses e
      JOIN users p ON p.id=e.paid_by_id JOIN users c ON c.id=e.created_by LEFT JOIN branches b ON b.id=e.branch_id ORDER BY paid_date DESC,e.id DESC''')]
    for row in rows:
        transfers=[dict(r) for r in con.execute('SELECT * FROM staff_reimbursements WHERE expense_id=? ORDER BY id',(row['id'],))]
        active=[t for t in transfers if not t['voided']];sent=sum(t['amount'] for t in active);received=sum(t['amount'] for t in active if t['received_at'])
        personal=row['funding']=='personal' and not row['cancelled']
        row['category']=row.get('category') or category_for(row['title'])
        row.update(transfers=transfers,to_transfer=max(0,row['amount']-sent) if personal else 0,awaiting_receipt=0,
          received=received,status='Отменён' if row['cancelled'] else 'Не требует возмещения' if not personal else 'Возмещено' if sent==row['amount'] else 'Возмещено частично' if sent else 'К возмещению')
        row['receipts']=[dict(r) for r in con.execute('SELECT id,at FROM staff_expense_receipts WHERE expense_id=?',(row['id'],))]
        row['history']=[dict(r) for r in con.execute('SELECT h.*,u.name AS actor FROM staff_expense_history h LEFT JOIN users u ON u.id=h.actor_id WHERE expense_id=? ORDER BY h.id',(row['id'],))]
    return {'rows':rows,'funding':FUNDING,'categories':CATEGORIES,'workflow':'simple','owner':owner_of(con),'can_reimburse':user['role']=='admin','staff':[{'id':r['id'],'name':r['name'],'role':r['role']} for r in con.execute("SELECT * FROM users WHERE active=1 AND role IN ('admin','operator') ORDER BY name") if allowed(con,dict(r))],
      'summary':{'spent':sum(r['amount'] for r in rows if not r['cancelled']),'to_transfer':sum(r['to_transfer'] for r in rows),'awaiting_receipt':sum(r['awaiting_receipt'] for r in rows)}}
