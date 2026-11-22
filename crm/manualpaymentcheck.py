"""Review direct Tochka-link receipts: bank credit and Evotor/OFD receipt are separate evidence."""
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import fiscal, services as sv


def init(con):
    con.executescript('''
        CREATE TABLE IF NOT EXISTS manual_payment_evidence(
            payment_id INTEGER PRIMARY KEY REFERENCES payments(id),
            bank_event_id TEXT NOT NULL UNIQUE REFERENCES incoming_money_events(id),
            receipt_id INTEGER NOT NULL UNIQUE REFERENCES fx_receipts(id),
            confirmed_at TEXT NOT NULL, confirmed_by TEXT NOT NULL DEFAULT '');
    ''')


def _allowed(user):
    return user.get('role') == 'admin' or user.get('role') == 'operator' and user.get('is_senior')


def _payment(con, pid):
    row = con.execute("SELECT * FROM payments WHERE id=? AND cancelled=0", (pid,)).fetchone()
    if not row: raise sv.ApiError('Платёж не найден или отменён', 404)
    if row['method'] != 'tochka_qr_link':
        raise sv.ApiError('Эта проверка предназначена для ручной оплаты по ссылке Точки', 409)
    return row


def _window(payment):
    try: day = date.fromisoformat(str(payment['paid_at'])[:10])
    except ValueError: raise sv.ApiError('У оплаты не указана корректная дата', 409) from None
    return day - timedelta(days=1), day + timedelta(days=1)


def refresh(con, pid, user):
    if not _allowed(user): raise sv.ApiError('Проверка доступна руководителю и старшему администратору', 403)
    payment = _payment(con, pid); start, end = _window(payment)
    errors = []
    try:
        from . import incomingcontrol
        current = datetime.now(ZoneInfo('Europe/Moscow')).replace(tzinfo=None)
        transactions = fiscal.fetch_tochka(con, start, end, include_debits=True, all_currencies=True)
        incomingcontrol.ingest_bank(con, 'tochka', transactions, current)
    except Exception:
        errors.append('Не удалось обновить выписку Точки')
    try:
        fiscal.store_ofd(con, fiscal.fetch_ofd_receipts(con, start, end))
    except Exception:
        errors.append('Не удалось обновить чеки ОФД')
    result = candidates(con, pid, user)
    result['refresh_errors'] = errors
    return result


def candidates(con, pid, user):
    if not _allowed(user): raise sv.ApiError('Проверка доступна руководителю и старшему администратору', 403)
    payment = _payment(con, pid); start, end = _window(payment); minor = fiscal.kop(payment['amount'])
    bank = []
    for row in con.execute("""SELECT * FROM incoming_money_events WHERE source='tochka' AND amount_minor=?
        AND event_day BETWEEN ? AND ? ORDER BY event_day,last_seen,id""", (minor, start.isoformat(), end.isoformat())):
        data = json.loads(row['data'])
        bank.append({'id': row['id'], 'day': row['event_day'], 'amount': row['amount_minor']//100,
                     'payer': data.get('payer') or '', 'purpose': data.get('purpose') or '',
                     'operation_id': row['source_id']})
    receipts = [{'id': r['id'], 'at': r['ts'], 'amount': r['amount']//100, 'fn': r['fiscal_fn'],
                 'fd': r['fiscal_fd'], 'fp': r['fiscal_fp']}
                for r in con.execute("""SELECT * FROM fx_receipts WHERE source='ofd' AND amount=? AND op=1
                    AND substr(ts,1,10) BETWEEN ? AND ? ORDER BY ts,id""", (minor, start.isoformat(), end.isoformat()))]
    return {'payment_id': payment['id'], 'amount': payment['amount'], 'paid_at': payment['paid_at'],
            'bank_candidates': bank, 'receipt_candidates': receipts, 'state': state(con, payment['id'])}


def bind(con, pid, body, user):
    if not _allowed(user): raise sv.ApiError('Подтверждение доступно руководителю и старшему администратору', 403)
    payment = _payment(con, pid); start, end = _window(payment); minor = fiscal.kop(payment['amount'])
    bank_id = str(body.get('bank_event_id') or '')
    try: receipt_id = int(body.get('receipt_id') or 0)
    except (TypeError, ValueError): receipt_id = 0
    bank = con.execute("""SELECT * FROM incoming_money_events WHERE id=? AND source='tochka' AND amount_minor=?
        AND event_day BETWEEN ? AND ?""", (bank_id, minor, start.isoformat(), end.isoformat())).fetchone()
    receipt = con.execute("""SELECT * FROM fx_receipts WHERE id=? AND source='ofd' AND amount=? AND op=1
        AND substr(ts,1,10) BETWEEN ? AND ? AND fiscal_fn!='' AND fiscal_fd!='' AND fiscal_fp!=''""",
        (receipt_id, minor, start.isoformat(), end.isoformat())).fetchone()
    if not bank or not receipt: raise sv.ApiError('Выберите банковскую операцию и чек из проверенного списка', 409)
    stamp = datetime.now(ZoneInfo('Europe/Moscow')).replace(tzinfo=None).isoformat(timespec='seconds')
    try:
        con.execute('''INSERT INTO manual_payment_evidence VALUES(?,?,?,?,?) ON CONFLICT(payment_id) DO UPDATE SET
            bank_event_id=excluded.bank_event_id,receipt_id=excluded.receipt_id,
            confirmed_at=excluded.confirmed_at,confirmed_by=excluded.confirmed_by''',
            (payment['id'], bank['id'], receipt['id'], stamp, user.get('name','')))
    except Exception as error:
        if 'UNIQUE constraint' in str(error): raise sv.ApiError('Эта операция или чек уже привязаны к другой оплате', 409) from None
        raise
    reconcile_tasks(con, only_payment=payment['id'])
    return state(con, payment['id'])


def state(con, pid):
    row = con.execute('''SELECT e.*,b.source_id AS bank_operation_id,b.amount_minor,b.event_day,
        r.ext_id AS receipt_number,r.ts AS receipt_at,r.amount AS receipt_amount,r.op,r.fiscal_fn,r.fiscal_fd,r.fiscal_fp
        FROM manual_payment_evidence e JOIN incoming_money_events b ON b.id=e.bank_event_id
        JOIN fx_receipts r ON r.id=e.receipt_id WHERE e.payment_id=?''', (pid,)).fetchone()
    if not row: return {'status':'unverified','bank':{'status':'unverified','label':'Поступление на расчётный счёт не подтверждено'},
                        'receipt':{'status':'unverified','label':'Чек Эвотор не подтверждён'}}
    payment = con.execute('SELECT amount,cancelled FROM payments WHERE id=?', (pid,)).fetchone()
    valid = payment and not payment['cancelled'] and row['amount_minor']==fiscal.kop(payment['amount']) and row['receipt_amount']==fiscal.kop(payment['amount']) and row['op']==1
    if not valid: return {'status':'unverified','bank':{'status':'unverified','label':'Связь с банковской операцией изменилась'},
                          'receipt':{'status':'unverified','label':'Связь с чеком требует повторной проверки'}}
    return {'status':'confirmed','confirmed_at':row['confirmed_at'],'confirmed_by':row['confirmed_by'],
            'bank':{'status':'confirmed','label':'Поступление подтверждено выпиской Точки','operation_id':row['bank_operation_id'],'checked_at':row['confirmed_at']},
            'receipt':{'status':'confirmed','label':'Чек Эвотор найден в ОФД','receipt_id':row['receipt_number'],
                       'receipt_at':row['receipt_at'],'fn':row['fiscal_fn'],'fd':row['fiscal_fd'],'fp':row['fiscal_fp']}}


def reconcile_tasks(con, only_payment=None):
    """One client task per unverified manual Tochka payment; exact evidence closes it."""
    from . import tasks
    where = " AND p.id=?" if only_payment is not None else ""
    rows = con.execute("""SELECT p.*,s.name AS child FROM payments p JOIN students s ON s.id=p.student_id
        WHERE p.cancelled=0 AND p.amount>0 AND p.method='tochka_qr_link'""" + where,
        (() if only_payment is None else (only_payment,))).fetchall()
    active = set()
    today = datetime.now(ZoneInfo('Europe/Moscow')).date().isoformat() + 'T23:59'
    for payment in rows:
        key = f"manual-tochka:{payment['id']}"
        if state(con,payment['id'])['status']=='confirmed':
            con.execute("""UPDATE tasks SET status='done',done_at=?,done_by='CRM',result='Поступление Точки и чек Эвотор подтверждены'
                WHERE auto_key=? AND status='open'""", (datetime.now(ZoneInfo('Europe/Moscow')).replace(tzinfo=None).isoformat(timespec='minutes'),key))
            continue
        active.add(key)
        note = (f"Ручная оплата по ссылке Точки: {payment['amount']:,} ₽ · {str(payment['paid_at'])[:10]}.\n"
                "Нужно выбрать поступление в выписке Точки и соответствующий чек Эвотор в ОФД.").replace(',', ' ')
        existing = con.execute('SELECT id,status FROM tasks WHERE auto_key=?',(key,)).fetchone()
        if existing:
            con.execute("""UPDATE tasks SET status='open',due_at=?,title='Проверить оплату Точки и чек Эвотор',
                note=?,priority=1,done_at=NULL,done_by='',result='' WHERE id=?""",(today,note,existing['id']))
        else:
            tid=tasks.add_task(con,'manual_payment_check',payment['student_id'],today,auto_key=key,
                title='Проверить оплату Точки и чек Эвотор',note=note)
            if tid:con.execute('UPDATE tasks SET priority=1 WHERE id=?',(tid,))
    if only_payment is None:
        for row in con.execute("SELECT id,auto_key FROM tasks WHERE kind='manual_payment_check' AND status='open'"):
            if row['auto_key'] not in active:
                con.execute("UPDATE tasks SET status='cancelled',done_at=?,done_by='CRM',result='Оплата отменена или больше не требует проверки' WHERE id=?",
                    (datetime.now(ZoneInfo('Europe/Moscow')).replace(tzinfo=None).isoformat(timespec='minutes'),row['id']))


def task_board(con):
    from . import tasks
    reconcile_tasks(con)
    cards=[tasks._task_out(con,row) for row in con.execute("""SELECT * FROM tasks
        WHERE kind='manual_payment_check' AND status='open' ORDER BY priority DESC,due_at,id""")]
    return {'funnel':'manual_payment_checks','draggable':False,'columns':[{
        'key':'manual_payment_check','title':'Проверить оплату Точки и чек Эвотор','cards':cards}]}
