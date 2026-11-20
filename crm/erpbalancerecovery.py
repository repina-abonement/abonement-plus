"""Preview reviewed balances and install a narrowly reviewed local snapshot.

The public preview never writes. The guarded owner web adapter is responsible
for a backup, a fresh full-state token, atomic application and a precise undo.
Changed native evidence requires a new review; cash rows are never recreated.
"""
from datetime import date
import hashlib
import json

from . import balancecredit, historicalrefunds, money


PAYMENT_FIELDS=('id','student_id','amount','method','paid_at','created_at',
    'subscription_id','cancelled','confirmed','comment','batch')
PURCHASE_FIELDS=('id','student_id','enrollment_id','month','kind','tariff','price',
    'lessons','status','created_at','paid_at')
CASES={'117559':{
    'erp_id':'117559','subscription_id':7165,'as_of_date':'2026-11-20',
    'cutoff_date':'2026-11-02','base_amount':0,'existing_credit':2700,
    'credit_label':'На следующий турнир',
    'credit_ref':'owner:erp117559:tournament:2026-11-14:next',
    'evidence_id':'owner:erp117559:reviewed-balance:2026-11-20:v2',
    'reviewed_fingerprint':'6586f21761bd436e0b0d6d6b1baaab7c3b7eff6baccee3946d44eb17e497d5cd',
},'88202':{
    'erp_id':'88202','student_id':1719,'subscription_id':7163,'as_of_date':'2026-11-20',
    'cutoff_date':'2026-11-20','base_amount':0,'existing_credit':0,
    'credit_label':'Аванса не было','credit_ref':'owner:erp88202:no-advance:2026-11-20',
    'evidence_id':'owner:erp88202:reviewed-balance:2026-11-20:v1',
    'reviewed_fingerprint':'eb9640b7ba1087abced167eed6a38fc1e810d21fa0cc432017f592fb7c0fe6ff',
    'reviewed_terms_fingerprint':'54a3c5c64d298bf7a0095496eed6e37376bd3a1c8d9424489c5ac5d6155c7486',
    'source_checked_at':'2026-11-20T15:32:21+03:00',
    'past_source_note':'Ноябрьское покрытие 8 100 ₽ сохранено как прежнее состояние учёта. Связи исходных счетов ERP отдельно не подтверждены',
}}


def _json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _rows(con,table,sid):
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
        return []
    return [dict(row) for row in con.execute('SELECT * FROM '+table+' WHERE student_id=? ORDER BY id',(sid,))]


def financial_state(con,sid,erp_id):
    return {'erp_id':str(erp_id),
        'payments':[{k:row[k] for k in PAYMENT_FIELDS} for row in _rows(con,'payments',sid)],
        'purchases':[{k:row[k] for k in PURCHASE_FIELDS} for row in _rows(con,'subscriptions',sid)]}


def financial_fingerprint(con,sid,erp_id):
    return _hash(financial_state(con,sid,erp_id))


def _preserved(con,sid,subid):
    return {table:_rows(con,table,sid) for table in ('payments','attendance','single_visits','enrollments')} | {
        'other_purchases':[row for row in _rows(con,'subscriptions',sid) if row['id']!=subid]}


def _purchase_terms(row):
    return {key:value for key,value in dict(row).items() if key not in ('status','paid_at')}


def _other_coverage(summary,subid):
    return {key:value for key,value in summary['balance']['covered'].items() if key!=subid}


def _summary(con,sid,subid,on):
    sub=dict(con.execute('SELECT * FROM subscriptions WHERE id=? AND student_id=?',(subid,sid)).fetchone())
    value=money.balance(con,sid,date.fromisoformat(on))
    return {'balance':value,'invoice':{'id':subid,'month':sub['month'],'price':sub['price'],
        'lessons':sub['lessons'],'enrollment_id':sub['enrollment_id'],
        'status':sub['status'],'covered':value['covered'].get(subid,0),
        'payable':max(0,sub['price']-value['covered'].get(subid,0))},
        'payments_count':len(_rows(con,'payments',sid)),
        'purchases_count':len(_rows(con,'subscriptions',sid)),
        'attendance_count':len(_rows(con,'attendance',sid)),
        'funds':[dict(row) for row in con.execute('''SELECT id,amount,source_ref,label
            FROM balance_credit_funds WHERE student_id=? ORDER BY id''',(sid,))]}


def _record_reviewed(con,sid,case,who,before):
    subid=case['subscription_id']
    reviewed=[row['id'] for row in _rows(con,'payments',sid)]
    historical={row['id']:before['balance']['covered'].get(row['id'],0)
        for row in _rows(con,'subscriptions',sid) if row['id']!=subid and row['status']!='cancelled'}
    balancecredit.protect_invoice(con,subid,who)
    arguments=dict(amount=case['base_amount'],as_of_date=case['as_of_date'],cutoff_date=case['cutoff_date'],
        reviewed_payment_ids=reviewed,historical_coverage=historical,evidence_id=case['evidence_id'],user=who)
    snapshot=balancecredit.record_snapshot(con,sid,**arguments)
    fund_arguments=dict(source_ref=case['credit_ref'],label=case['credit_label'],
        evidence_id=case['credit_ref'],snapshot_id=snapshot,user=who)
    fund=balancecredit.add_existing_fund(con,sid,case['existing_credit'],**fund_arguments) if case['existing_credit'] else None
    money._reconcile(con,sid)
    return snapshot,fund,arguments,fund_arguments


def apply_reviewed(con,erp_id,user):
    """Install only CRM-175, inside the web adapter's checked transaction.

    No transaction is committed here. The caller must rollback on any failure.
    Other reviewed clients retain their existing read-only preview workflow.
    """
    erp_id=str(erp_id)
    if erp_id!='88202':
        raise ValueError('Для этой карточки рабочее применение не подготовлено')
    case=CASES[erp_id]
    checked=preview(con,erp_id)
    if not checked['plan_ready']:
        raise ValueError('; '.join(checked['issues']) or 'Сначала нужна успешная проверка на копии')
    sid=con.execute('SELECT id FROM students WHERE erp_id=?',(erp_id,)).fetchone()['id']
    subid=case['subscription_id']
    before=checked['before']
    original=_preserved(con,sid,subid)
    target=con.execute('SELECT * FROM subscriptions WHERE id=?',(subid,)).fetchone()
    snapshot,fund,_,_=_record_reviewed(con,sid,case,user,before)
    after=_summary(con,sid,subid,case['as_of_date'])
    expected={key:value for key,value in checked['without_balance'].items() if key not in ('use_balance','credit_label')}
    if after!=expected or original!=_preserved(con,sid,subid):
        raise ValueError('Результат отличается от плана или изменены исходные записи. Требуется откат транзакции')
    if _purchase_terms(con.execute('SELECT * FROM subscriptions WHERE id=?',(subid,)).fetchone())!=_purchase_terms(target):
        raise ValueError('Изменены условия декабрьской покупки. Требуется откат транзакции')
    if _other_coverage(after,subid)!=_other_coverage(before,subid):
        raise ValueError('Изменилось покрытие прошлых покупок. Требуется откат транзакции')
    return {'erp_id':erp_id,'student_id':sid,'subscription_id':subid,'snapshot_id':snapshot,'fund_id':fund,
        'before':before,'after':after,'read_only':False,'external_actions':False,
        'payments_preserved':True,'other_purchases_preserved':True,'attendance_preserved':True,
        'past_coverage_preserved':True,'purchase_terms_preserved':True,'past_source_note':case.get('past_source_note','')}


def preview(con,erp_id,*,case=None):
    snapshot=historicalrefunds._clone(con)
    try:
        return _preview_snapshot(snapshot,erp_id,case=case)
    finally:
        snapshot.close()


def _preview_snapshot(con,erp_id,*,case=None):
    case=dict(case if case is not None else CASES.get(str(erp_id),{}))
    result={'read_only':True,'preview_on_copy':True,'external_actions':False,
        'erp_id':str(erp_id),'can_apply':False,'production_apply_available':False,
        'plan_ready':False,'before':None,'without_balance':None,'with_balance':None,'issues':[]}
    if not case or case.get('erp_id')!=str(erp_id):
        result['issues'].append('Для этой карточки нет подтверждённого плана восстановления баланса')
        return result
    students=list(con.execute('SELECT id FROM students WHERE erp_id=?',(str(erp_id),)))
    if len(students)!=1:
        result['issues'].append('Клиент ERP не сопоставлен с единственной карточкой CRM')
        return result
    sid,subid=students[0]['id'],case['subscription_id']
    if case.get('student_id') is not None and sid!=case['student_id']:
        result['issues'].append('Карточка CRM не совпадает с подтверждённым клиентом ERP')
        return result
    target=con.execute('SELECT * FROM subscriptions WHERE id=? AND student_id=?',(subid,sid)).fetchone()
    if not target or target['status']=='cancelled':
        result['issues'].append('Проверяемый декабрьский счёт не найден или отменён')
        return result
    if case.get('reviewed_terms_fingerprint') and _hash(_purchase_terms(target))!=case['reviewed_terms_fingerprint']:
        result['issues'].append('Условия декабрьской покупки изменились после снимка. Нужна повторная сверка')
        return result
    current=financial_fingerprint(con,sid,erp_id)
    result['reviewed_fingerprint']=case['reviewed_fingerprint']
    result['current_fingerprint']=current
    result['before']=_summary(con,sid,subid,case['as_of_date'])
    if current!=case['reviewed_fingerprint']:
        result['issues'].append('Финансовые записи изменились после предоставленного снимка. Нужна повторная сверка; план не применялся')
        return result
    original=_preserved(con,sid,subid)
    copy=con
    who={'id':0,'name':'Предварительная проверка баланса','role':'admin'}
    try:
        snapshot,fund,arguments,fund_arguments=_record_reviewed(copy,sid,case,who,result['before'])
        if original!=_preserved(copy,sid,subid):
            raise ValueError('Проверка затронула исходные оплаты, другие покупки или посещения')
        off=_summary(copy,sid,subid,case['as_of_date'])
        if _other_coverage(off,subid)!=_other_coverage(result['before'],subid):
            raise ValueError('Изменилось покрытие прошлых покупок. Нужна отдельная сверка сохранённых начислений')
        off['use_balance']=False;off['credit_label']=case['credit_label']
        copied_target=copy.execute('SELECT * FROM subscriptions WHERE id=?',(subid,)).fetchone()
        if _purchase_terms(copied_target)!=_purchase_terms(target):
            raise ValueError('Проверка изменила стоимость, занятия или условия абонемента')
        repeat_snapshot=balancecredit.record_snapshot(copy,sid,**arguments)
        repeat_fund=balancecredit.add_existing_fund(copy,sid,case['existing_credit'],**fund_arguments) if case['existing_credit'] else None
        if (snapshot,fund)!=(repeat_snapshot,repeat_fund) or _summary(copy,sid,subid,case['as_of_date'])!= {k:v for k,v in off.items() if k not in ('use_balance','credit_label')}:
            raise ValueError('Повторное восстановление изменило сумму или создало дубль зачёта')
        result['without_balance']=off
        selected=historicalrefunds._clone(copy)
        try:
            quoted=balancecredit.preview(selected,sid,target['price'],True,subid)
            apply_args=dict(use_balance=True,expected_token=quoted['token'],
                request_id='preview-only:'+case['evidence_id'])
            applied=balancecredit.apply_to_invoice(selected,subid,who,**apply_args)
            money._reconcile(selected,sid)
            on=_summary(selected,sid,subid,case['as_of_date'])
            if _other_coverage(on,subid)!=_other_coverage(result['before'],subid):
                raise ValueError('Зачёт изменил покрытие других покупок')
            repeated=balancecredit.apply_to_invoice(selected,subid,who,**apply_args)
            reopened=balancecredit.preview(selected,sid,target['price'],False,subid)
            if on!=_summary(selected,sid,subid,case['as_of_date']):
                raise ValueError('Повторное подтверждение ещё раз изменило баланс')
            if original!=_preserved(selected,sid,subid):
                raise ValueError('Зачёт изменил исходные оплаты, другие покупки или посещения')
            if _purchase_terms(selected.execute('SELECT * FROM subscriptions WHERE id=?',(subid,)).fetchone())!=_purchase_terms(target):
                raise ValueError('Зачёт изменил стоимость, занятия или условия абонемента')
            on.update(use_balance=True,applied_amount=quoted['apply_amount'],
                repeat_unchanged=True,reopened=reopened)
            result['with_balance']=on
        finally:
            selected.close()
        result.update(plan_ready=True,payments_preserved=True,other_purchases_preserved=True,past_coverage_preserved=True,purchase_terms_preserved=True,
            attendance_preserved=True,repeat_carry_unchanged=True,
            selected_for_current_invoice='without_balance',
            authority={'base_amount':case['base_amount'],'available_balance':off['balance']['deposit'],
                'source_ref':case['credit_ref'],'label':case['credit_label'],
                'provenance':'owner_confirmation','cash_rows_created':0},
            source_coverage_complete=False,
            past_source_note=case.get('past_source_note',''),
            source_note='Старый реестр неполон. Подтверждение владельца задаёт баланс; операции после '+case['cutoff_date']+' сверяются отдельно')
    except (ValueError,KeyError) as error:
        result.update(plan_ready=False,without_balance=None,with_balance=None)
        result['issues'].append(str(error))
    return result
