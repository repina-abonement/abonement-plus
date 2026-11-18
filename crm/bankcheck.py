"""Scheduled read-only bank evidence; never initiates payments or prints receipts."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from . import fiscal


def init(con):
    con.execute('''CREATE TABLE IF NOT EXISTS bank_payment_checks (
        payment_id INTEGER PRIMARY KEY, provider_payment_id TEXT NOT NULL,
        checked_at TEXT NOT NULL, result TEXT NOT NULL)''')


def run_automatic(con, now=None, limit=3):
    if not all(fiscal.cfg(con,k) for k in ('fx_tochka_token','fx_pk_server','fx_pk_user','fx_pk_password')):
        return {'checked':0,'configured':False}
    current=now or datetime.now(ZoneInfo('Europe/Moscow'))
    if current.tzinfo is not None:
        current=current.astimezone(ZoneInfo('Europe/Moscow')).replace(tzinfo=None)
    stamp=current.isoformat(timespec='seconds')
    cutoff=(current-timedelta(minutes=10)).isoformat(timespec='seconds')
    rows=con.execute('''SELECT p.id,min(l.pk_payment_id) AS pk_payment_id FROM payments p
        JOIN pay_links l ON l.payment_id=p.id
        LEFT JOIN bank_payment_checks c ON c.payment_id=p.id
        WHERE p.cancelled=0 AND p.amount>0 AND l.pk_payment_id!=''
          AND (c.payment_id IS NULL OR c.checked_at<=?)
        GROUP BY p.id HAVING count(DISTINCT l.pk_payment_id)=1
        ORDER BY c.checked_at IS NOT NULL,c.checked_at,p.id LIMIT ?''',
        (cutoff,max(0,min(int(limit),10)))).fetchall()
    from . import paykeepercheck
    for row in rows:
        try:
            inspection=paykeepercheck.inspect(con,row['pk_payment_id'],include_bank=True,include_ofd=False)
            result=inspection['bank_settlement']
        except Exception:
            result={'status':'unavailable','note':'Не удалось проверить зачисление. Деньги не отмечены как зачисленные'}
        # Current registry/statement probe returns candidates, not final settlement proof.
        result=dict(result,status='unverified')
        con.execute('''INSERT INTO bank_payment_checks VALUES(?,?,?,?)
            ON CONFLICT(payment_id) DO UPDATE SET provider_payment_id=excluded.provider_payment_id,
            checked_at=excluded.checked_at,result=excluded.result''',
            (row['id'],row['pk_payment_id'],stamp,json.dumps(result,ensure_ascii=False)))
    return {'checked':len(rows),'configured':True}


def state(con,payment_id):
    row=con.execute('SELECT * FROM bank_payment_checks WHERE payment_id=?',(payment_id,)).fetchone()
    if not row:return None
    # Ignore stale associations if an operator corrects a link later.
    if not con.execute('SELECT 1 FROM pay_links WHERE payment_id=? AND pk_payment_id=?',
                       (payment_id,row['provider_payment_id'])).fetchone():return None
    result=json.loads(row['result'])
    note=result.get('note') or 'Зачисление на расчётный счёт пока не подтверждено'
    if result.get('registry_status')=='permission_denied':
        note='Точка: ключу CRM не хватает права чтения эквайринга ReadAcquiringData'
    elif result.get('registry_matches') or result.get('statement_candidates'):
        note='Найдены связанные операции. Сумма выплаты, комиссия и зачисление ещё требуют подтверждения'
    elif result.get('statement_status')=='read':
        note='Выписка прочитана. Подтверждённая связь с зачислением пока не найдена'
    return {'status':'unverified','label':'Зачисление на расчётный счёт не подтверждено',
            'checked_at':row['checked_at'],'note':note}
