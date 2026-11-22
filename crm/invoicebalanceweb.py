"""Explicit invoice balance confirmation and idempotent invoice creation."""
from datetime import datetime
import hashlib
import json


def init(con):
    con.execute('''CREATE TABLE IF NOT EXISTS invoice_creation_requests (
        request_key TEXT PRIMARY KEY,student_id INTEGER NOT NULL,
        signature TEXT NOT NULL,result TEXT NOT NULL,created_at TEXT NOT NULL)''')


def _key(value, required=False):
    from .services import ApiError
    if value in (None, '') and not required:
        return None
    if not isinstance(value,str) or not 8 <= len(value) <= 100:
        raise ApiError('Обновите форму счёта перед подтверждением')
    return value


def _signature(data):
    return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False,
        separators=(',',':')).encode()).hexdigest()


def creation_replay(con,sid,key,data,required=False):
    from .services import ApiError
    key=_key(key,required)
    if key is None:
        return None
    previous=con.execute('SELECT * FROM invoice_creation_requests WHERE request_key=?',(key,)).fetchone()
    if previous:
        if previous['student_id'] != sid or previous['signature'] != _signature(data):
            raise ApiError('Этот запрос счёта уже подтверждён с другими данными',409)
        return json.loads(previous['result'])
    return None


def remember_creation(con,sid,key,data,result):
    key=_key(key)
    if key is None:
        return
    con.execute('INSERT INTO invoice_creation_requests VALUES (?,?,?,?,?)',
        (key,sid,_signature(data),json.dumps(result,ensure_ascii=False),
         datetime.now().isoformat(timespec='seconds')))


def query_choice(q):
    from .services import ApiError
    value=q.get('use_balance')
    if value in (None,'','0'):
        return False
    if value == '1':
        return True
    raise ApiError('Выберите, нужно ли зачесть деньги с баланса')


def register(route):
    from . import singlechanges
    singlechanges.register(route)
    @route('POST','/api/subscriptions/{sub}/balance-apply',role='operator|client_sales:full')
    def apply_balance(con,h,user,body,q,sub):
        from . import balancecredit, money, program, pricing
        from .services import ApiError, sale_payment_day
        if body.get('use_balance') is not True:
            raise ApiError('Зачёт выполняется только после явного подтверждения')
        request_id=_key(body.get('request_id'),True)
        expected=body.get('expected_balance_token',body.get('balance_token'))
        if not isinstance(expected,str) or not expected:
            raise ApiError('Обновите расчёт счёта перед подтверждением',409)
        day=sale_payment_day(body.get('paid_date'))
        # A repeated successful confirmation must return its original result
        # before current pricing rules can alter the already confirmed invoice.
        previous=con.execute('SELECT 1 FROM invoice_balance_uses WHERE subscription_id=? OR request_id=?',
            (int(sub),request_id)).fetchone()
        if previous:
            try:
                result=balancecredit.apply_to_invoice(con,int(sub),user,True,expected,request_id)
            except ValueError as error:
                raise ApiError(str(error),409) from error
            return {'id':int(sub),'balance_preview':result,'ok':True}
        con.execute('SAVEPOINT confirm_invoice_balance')
        try:
            # payment-quote computes this same price in a rolled-back preview.
            # Only the actual confirmation may persist that calculation, and
            # stale tokens roll back its price/history together with the debit.
            program.reprice(con,int(sub),on=day)
            pricing.reprice(con,int(sub),on=day)
            result=balancecredit.apply_to_invoice(con,int(sub),user,True,expected,request_id)
            money.reconcile(con,con.execute('SELECT student_id FROM subscriptions WHERE id=?',(int(sub),)).fetchone()[0])
            con.execute('RELEASE confirm_invoice_balance')
        except BaseException as error:
            con.execute('ROLLBACK TO confirm_invoice_balance')
            con.execute('RELEASE confirm_invoice_balance')
            if isinstance(error,ValueError):
                raise ApiError(str(error),409) from error
            raise
        return {'id':int(sub),'balance_preview':result,'ok':True}
