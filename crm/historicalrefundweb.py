"""Owner-only local accounting of refunds executed before the CRM migration.

These routes never issue bank, payment-provider, fiscal or messaging requests.
The plan runs on a separate copy; applying requires verified source identities.
"""
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime
import re
from zoneinfo import ZoneInfo

from . import erpimport
from .erpsinglerecoveryweb import _backup_current, provenance
from .services import ApiError


def _body(body):
    if not isinstance(body, dict):
        raise ApiError('Передайте сведения об исходных покупках, оплатах и уже выполненном возврате')
    return {key:value for key,value in body.items() if key!='token'}


def _metadata():
    return {'provenance':provenance(),'checked_at':datetime.now(ZoneInfo('Europe/Moscow')).isoformat(timespec='seconds'),
        'live_provider_requests':False}


@contextmanager
def _snapshot_copy(con):
    from .historicalrefunds import _clone
    snapshot=_clone(con)
    try:
        yield snapshot
    finally:
        snapshot.close()


def register(route):
    @route('GET', '/api/erp/financial-audit', role='admin', transactional=False)
    def financial_audit(con, h, user, body, q):
        from . import erpfinancialaudit
        erp_id=str(q.get('erp_id') or '').strip()
        month=str(q.get('month') or '').strip() or None
        if not erp_id or len(erp_id)>64:
            raise ApiError('Укажите ID клиента ERP')
        if month and not re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])',month):
            raise ApiError('Укажите месяц в формате ГГГГ-ММ')
        try:
            with _snapshot_copy(con) as snapshot:
                result=erpfinancialaudit.audit(snapshot,erp_id,target_month=month)
            return result|_metadata()|{'read_only':True,'snapshot_on_copy':True}
        except (erpimport.ImportError_,ValueError) as exc:
            raise ApiError(str(exc)) from exc

    @route('GET', '/api/erp/historical-refunds/context', role='admin', transactional=False)
    def refund_context(con, h, user, body, q):
        from . import historicalrefunds as engine
        erp_id=str(q.get('erp_id') or '').strip()
        if not erp_id or len(erp_id)>64:
            raise ApiError('Укажите ID клиента ERP')
        try:
            with _snapshot_copy(con) as snapshot:
                result=engine.context(snapshot,erp_id)
            return result|_metadata()|{'read_only':True,'snapshot_on_copy':True}
        except (erpimport.ImportError_,ValueError) as exc:
            raise ApiError(str(exc)) from exc

    @route('POST', '/api/erp/historical-refunds/plan', role='admin', transactional=False)
    def refund_plan(con, h, user, body, q):
        from . import historicalrefunds as engine
        try:
            with _snapshot_copy(con) as snapshot:
                result=engine.plan(snapshot,_body(body))
            return result|_metadata()|{'preview_on_copy':True}
        except (erpimport.ImportError_,ValueError) as exc:
            raise ApiError(str(exc)) from exc

    @route('POST', '/api/erp/historical-refunds/apply', role='admin', transactional=False)
    def refund_apply(con, h, user, body, q):
        from . import historicalrefunds as engine
        try:
            data=_body(body)
            token=body.get('token')
            checked=engine.plan(con,data)
            if checked.get('already_applied'):
                return checked|_metadata()
            if not token or token!=checked.get('token'):
                raise ApiError('План изменился или не подтверждён. Повторите проверку на копии')
            if not checked.get('can_apply'):
                raise ApiError('Исходные связи или реквизиты возврата требуют сверки. Рабочие записи не изменены')
            path=_backup_current(con,prefix='erp-historical-refund')
            result=engine.apply(con,data,token,user,path)
            return result|_metadata()|{'backup_created':True,'backup_filename':Path(path).name}
        except (erpimport.ImportError_,ValueError) as exc:
            raise ApiError(str(exc)) from exc
