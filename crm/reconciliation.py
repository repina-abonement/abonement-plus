"""Read-only chain projection. Acquirer acceptance, bank settlement and OFD are distinct."""
from . import fiscal


def payment_state(con, payment):
    bank = {'status': 'unverified', 'label': 'Зачисление на расчётный счёт не подтверждено',
            'note': 'Нужна связь реестра выплат эквайринга с операцией банковской выписки'}
    if payment['method'] == 'cash':
        bank = {'status': 'not_applicable', 'label': 'Наличные — зачисление на счёт отдельно',
                'note': 'Получение наличных и последующая инкассация — разные операции'}
    ofd = {'status': 'unknown', 'label': 'Чек в ОФД не подтверждён'}
    link = con.execute("SELECT pk_payment_id FROM pay_links WHERE payment_id=? AND pk_payment_id!='' ORDER BY id DESC LIMIT 1",
                       (payment['id'],)).fetchone()
    if not link:
        ofd['note'] = 'Нет подтверждённой связи оплаты с фискальными реквизитами чека'
        return {'bank': bank, 'ofd': ofd, 'complete': False}
    receipt = con.execute("""SELECT o.* FROM fx_receipts p JOIN fx_receipts o
          ON o.source='ofd' AND o.fiscal_fn=p.fiscal_fn AND o.fiscal_fd=p.fiscal_fd
             AND o.fiscal_fp=p.fiscal_fp AND o.amount=p.amount AND o.op=p.op
          WHERE p.source='paykeeper' AND p.pk_payment=? AND p.pk_status IN ('success','fiscal_details') AND p.op=1
             AND p.fiscal_fn!='' AND p.fiscal_fd!='' AND p.fiscal_fp!='' AND p.amount=?
          ORDER BY o.id DESC LIMIT 1""", (link['pk_payment_id'], fiscal.kop(payment['amount']))).fetchone()
    if receipt:
        ofd.update(status='confirmed', label='Чек найден в ОФД', fn=receipt['fiscal_fn'],
                   fd=receipt['fiscal_fd'], fp=receipt['fiscal_fp'], receipt_at=receipt['ts'])
    else:
        ofd['note'] = 'Подтверждение требует совпадения ФН, ФД, ФП, суммы и типа операции'
    last = con.execute("SELECT ts,ok FROM fx_runs WHERE source='ofd' ORDER BY id DESC LIMIT 1").fetchone()
    ofd['source_available'] = bool(last['ok']) if last else None
    ofd['source_checked_at'] = last['ts'] if last else None
    if not receipt and (not fiscal.cfg(con,'fx_ofd_token') or (last and not last['ok'])):
        ofd.update(status='unavailable', label='ОФД: источник недоступен')
    return {'bank': bank, 'ofd': ofd, 'complete': False}


def run_ofd_backlog(con, now=None):
    """One historical receipt day per cycle; never prints, refunds or notifies clients."""
    from datetime import datetime, timedelta, date
    from zoneinfo import ZoneInfo
    if not fiscal.cfg(con, 'fx_ofd_token'):
        return {'skipped': True}
    current = now or datetime.now(ZoneInfo('Europe/Moscow'))
    if current.tzinfo is not None:
        current = current.astimezone(ZoneInfo('Europe/Moscow')).replace(tzinfo=None)
    cutoff = (current - timedelta(minutes=10)).strftime('%Y-%m-%d %H:%M:%S')
    row = con.execute("""SELECT DISTINCT substr(p.ts,1,10) AS day, d.checked_at
        FROM fx_receipts p LEFT JOIN fiscal_ofd_days d ON d.day=substr(p.ts,1,10)
        WHERE p.source='paykeeper' AND p.pk_status IN ('success','fiscal_details') AND p.op=1
          AND p.fiscal_fn!='' AND p.fiscal_fd!='' AND p.fiscal_fp!=''
          AND NOT EXISTS(SELECT 1 FROM fx_receipts o WHERE o.source='ofd'
            AND o.fiscal_fn=p.fiscal_fn AND o.fiscal_fd=p.fiscal_fd
            AND o.fiscal_fp=p.fiscal_fp AND o.amount=p.amount AND o.op=p.op)
          AND (d.checked_at IS NULL OR d.checked_at<=?)
        ORDER BY d.checked_at IS NOT NULL,d.checked_at,day LIMIT 1""", (cutoff,)).fetchone()
    if not row:
        return {'checked': 0}
    day = row['day']
    try:
        target = date.fromisoformat(day)
    except ValueError:
        return {'checked': 0, 'invalid_date': True}
    ok = False
    try:
        receipts = fiscal.fetch_ofd_receipts(con, target, target)
        fiscal.store_ofd(con, receipts)
        ok = True
    except Exception:
        pass
    stamp = current.strftime('%Y-%m-%d %H:%M:%S')
    con.execute('INSERT INTO fiscal_ofd_days VALUES(?,?,?) ON CONFLICT(day) DO UPDATE SET checked_at=excluded.checked_at,ok=excluded.ok',
                (day, stamp, int(ok)))
    fiscal._log_run(con, 'ofd', ok, '' if ok else 'Не удалось прочитать чеки ОФД за день', current)
    return {'checked': 1, 'day': day, 'ok': ok}
