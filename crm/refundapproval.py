"""Owner-reviewed refund requests; approval never claims money has reached the customer."""
import hashlib,json
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from . import services as sv,subcancel,money,tasks


def init(con):
    con.execute('''CREATE TABLE IF NOT EXISTS refund_requests (
        id INTEGER PRIMARY KEY,subscription_id INTEGER NOT NULL,student_id INTEGER NOT NULL,
        state TEXT NOT NULL DEFAULT 'pending',amount INTEGER NOT NULL,effective_date TEXT NOT NULL,
        reason TEXT NOT NULL,plan_token TEXT NOT NULL,created_at TEXT NOT NULL,created_by INTEGER,created_name TEXT NOT NULL,
        reviewed_at TEXT,reviewed_by INTEGER,review_comment TEXT NOT NULL DEFAULT '',last_error TEXT NOT NULL DEFAULT '',
        control_task_id INTEGER,received_at TEXT,received_by INTEGER,received_proof TEXT NOT NULL DEFAULT '')''')
    if 'sources_json' not in {r[1] for r in con.execute('PRAGMA table_info(refund_requests)')}:
        con.execute("ALTER TABLE refund_requests ADD COLUMN sources_json TEXT NOT NULL DEFAULT '[]'")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS refund_request_active ON refund_requests(subscription_id) WHERE state IN ('pending','approved','prepared')")


def _plan(con,subid,effective):
    sub=subcancel._sub(con,subid)
    if sub['status']=='cancelled':raise sv.ApiError('Абонемент уже отменён — нужно сверить существующий возврат')
    period=subcancel.period_quote(con,sub,effective)
    if period['releasable']<=0:raise sv.ApiError('Нет оплаченной суммы к возврату')
    parts=subcancel._sources(con,sub,period['releasable'],period['retained_amount'])
    token=hashlib.sha256(json.dumps([dict(sub),period,parts],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return sub,period,parts,token


def request(con,subid,user,data):
    if user.get('role') not in ('admin','operator'):raise sv.ApiError('Нет доступа',403)
    reason=str(data.get('reason') or '').strip()
    if len(reason)<3:raise sv.ApiError('Укажите причину возврата')
    sub,period,parts,token=_plan(con,subid,data.get('effective_date'))
    if data.get('expected_releasable') is not None and data['expected_releasable']!=period['releasable']:raise sv.ApiError('Сумма изменилась — проверьте расчёт',409)
    old=con.execute("SELECT * FROM refund_requests WHERE subscription_id=? AND state IN ('pending','approved','prepared')",(subid,)).fetchone()
    if old:
        if old['plan_token']!=token:raise sv.ApiError('Уже есть заявка с другим расчётом — сначала сверьте её',409)
        return {'ok':True,'request_id':old['id'],'state':old['state'],'already_exists':True}
    rid=con.execute('''INSERT INTO refund_requests(subscription_id,student_id,amount,effective_date,reason,plan_token,created_at,created_by,created_name)
        VALUES (?,?,?,?,?,?,?,?,?)''',(subid,sub['student_id'],period['releasable'],period['effective_date'],reason[:1000],token,sv.now_iso(),user['id'],user.get('name',''))).lastrowid
    con.execute('UPDATE refund_requests SET sources_json=? WHERE id=?',(json.dumps(parts,ensure_ascii=False),rid))
    note=f"Возврат {period['releasable']} ₽ по абонементу #{subid}. Ожидается личное решение Юлии. После одобрения проверить возврат исходным способом, чек возврата и получение денег клиентом. Другие задачи не закрывать."
    due=(datetime.now(ZoneInfo('Europe/Moscow')).date()+timedelta(days=1)).isoformat()+'T23:59'
    tid=tasks.add_task(con,'manual',sub['student_id'],due,auto_key=f'refund-control:{rid}',title='Контроль возврата: деньги получены клиентом',source='refund',note=note)
    con.execute('UPDATE tasks SET assignee_id=NULL WHERE id=?',(tid,))
    con.execute('UPDATE refund_requests SET control_task_id=? WHERE id=?',(tid,rid))
    sv.log(con,sub['student_id'],f"Заявка на возврат #{rid}: {period['releasable']} ₽. Ожидается личное решение Юлии. Причина: {reason}",user,'payment')
    sv.audit(con,user,'refund.request',str(rid))
    return {'ok':True,'request_id':rid,'state':'pending'}


def listing(con,sid=None):
    return [dict(r) for r in con.execute('''SELECT r.*,s.name AS child,g.name AS group_name,x.month AS month FROM refund_requests r
        JOIN students s ON s.id=r.student_id JOIN subscriptions x ON x.id=r.subscription_id
        LEFT JOIN enrollments e ON e.id=x.enrollment_id LEFT JOIN groups g ON g.id=e.group_id
        WHERE '''+('r.student_id=?' if sid else "r.state IN ('pending','approved','prepared')")+' ORDER BY r.id DESC',(sid,) if sid else ())]


def review(con,rid,user,data):
    if user.get('role')!='admin':raise sv.ApiError('Личное одобрение возврата доступно только Юлии — руководителю',403)
    row=con.execute('SELECT * FROM refund_requests WHERE id=?',(rid,)).fetchone()
    if not row:raise sv.ApiError('Заявка не найдена',404)
    decision=data.get('decision')
    if decision not in ('approve','reject'):raise sv.ApiError('Выберите решение')
    target='approved' if decision=='approve' else 'rejected'
    if row['state']==target:return {'ok':True,'state':target}
    if row['state']!='pending':raise sv.ApiError('Заявка уже рассмотрена')
    comment=str(data.get('comment') or '').strip()
    if decision=='reject' and not comment:raise sv.ApiError('Укажите причину отклонения')
    if decision=='approve':
        _,_,_,token=_plan(con,row['subscription_id'],row['effective_date'])
        if token!=row['plan_token']:raise sv.ApiError('Расчёт изменился — нужна новая заявка на актуальную сумму',409)
    con.execute('UPDATE refund_requests SET state=?,reviewed_at=?,reviewed_by=?,review_comment=? WHERE id=?',(target,sv.now_iso(),user['id'],comment[:1000],rid))
    if target=='rejected':
        con.execute("UPDATE tasks SET status='cancelled',done_at=?,result=? WHERE id=? AND status='open'",(sv.now_iso(),'Юлия отклонила возврат: '+comment,row['control_task_id']))
    else:
        con.execute("UPDATE tasks SET note=note||? WHERE id=?",('\nЮлия одобрила '+sv.now_iso()+'. Возврат ещё не выполнен, деньги клиенту не подтверждены.',row['control_task_id']))
    sv.audit(con,user,'refund.'+decision,str(rid))
    return {'ok':True,'state':target}


def prepare(con,rid,user):
    if user.get('role') not in ('admin','operator'):raise sv.ApiError('Нет доступа',403)
    row=con.execute('SELECT * FROM refund_requests WHERE id=?',(rid,)).fetchone()
    if not row or row['state'] not in ('approved','prepared'):raise sv.ApiError('Сначала нужно личное одобрение Юлии',403)
    if row['state']=='prepared':return {'ok':True,'state':'prepared'}
    _,_,_,token=_plan(con,row['subscription_id'],row['effective_date'])
    if token!=row['plan_token']:raise sv.ApiError('Расчёт после одобрения изменился — требуется повторное согласование',409)
    con.execute('SAVEPOINT prepare_approved_refund')
    try:
        result=subcancel.cancel(con,row['subscription_id'],user,{'mode':'refund','reason':row['reason'],
            'effective_date':row['effective_date'],'expected_releasable':row['amount'],
            'approved_by_yulia':True,'approval':row['reviewed_at'][:10],'_preserve_other_tasks':True})
        con.execute("UPDATE refund_requests SET state='prepared',last_error='' WHERE id=?",(rid,))
        con.execute('RELEASE prepare_approved_refund')
        return {'ok':True,'state':'prepared','cancellation':result}
    except Exception as error:
        con.execute('ROLLBACK TO prepare_approved_refund');con.execute('RELEASE prepare_approved_refund')
        message=str(error) if isinstance(error,sv.ApiError) else 'Не удалось проверить PayKeeper. Финансовые изменения не сохранены'
        con.execute('UPDATE refund_requests SET last_error=? WHERE id=?',(message[:1000],rid))
        return {'ok':False,'state':'approved','error':message}


def received(con,rid,user,data):
    if user.get('role') not in ('admin','operator'):raise sv.ApiError('Нет доступа',403)
    row=con.execute('SELECT * FROM refund_requests WHERE id=?',(rid,)).fetchone()
    if not row or row['state'] not in ('prepared','received'):raise sv.ApiError('Возврат ещё не подготовлен')
    if row['state']=='received':return {'ok':True,'state':'received'}
    refunds=list(con.execute('''SELECT r.status FROM subscription_refunds r JOIN subscription_cancellations c ON c.id=r.cancellation_id
        WHERE c.subscription_id=?''',(row['subscription_id'],)))
    if not refunds or any(r['status'] not in ('receipt_pending','done') for r in refunds):raise sv.ApiError('Сначала подтвердите фактический возврат исходным способом')
    proof=str(data.get('proof') or '').strip()
    if data.get('customer_received') is not True or len(proof)<5:raise sv.ApiError('Подтвердите получение денег клиентом и укажите подтверждение')
    con.execute("UPDATE refund_requests SET state='received',received_at=?,received_by=?,received_proof=? WHERE id=?",(sv.now_iso(),user['id'],proof[:1000],rid))
    con.execute("UPDATE tasks SET status='done',done_at=?,done_by=?,result=? WHERE id=?",(sv.now_iso(),user.get('name',''),proof[:1000],row['control_task_id']))
    sv.audit(con,user,'refund.customer_received',str(rid))
    return {'ok':True,'state':'received'}
