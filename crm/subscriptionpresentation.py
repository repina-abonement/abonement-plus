"""Read-only purchase presentation, independent of accounting allocation/month.

A single purchase belongs visually to its unambiguous effective booking date.
Unknown dates stay unknown; no schedule/payment timestamp supplies a substitute.
"""
from collections import defaultdict
from datetime import date
import json

from . import logic, money, schedule


def rows_for_card(con, sid, covered=None):
    if covered is None:
        covered=money.balance(con,sid)['covered']
    rows=[dict(r) for r in con.execute('''SELECT x.*,e.group_id,g.name AS group_name
        FROM subscriptions x LEFT JOIN enrollments e ON e.id=x.enrollment_id AND e.student_id=x.student_id
        LEFT JOIN groups g ON g.id=e.group_id WHERE x.student_id=? ORDER BY x.month DESC,x.id''',(sid,))]
    carry_in=defaultdict(int);carry_out=defaultdict(int);carry_history_out=defaultdict(int);carry_pending=defaultdict(int)
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='revenue_carries'").fetchone():
        for c in con.execute("SELECT * FROM revenue_carries WHERE student_id=? AND active=1",(sid,)):
            carry_in[c['target_subscription_id']]+=c['amount']
            if c['source_subscription_id']:
                carry_out[c['source_subscription_id']]+=c['amount']
            if c['source_erp_subscription_id'] and c['source_loaded']:
                carry_history_out[c['source_erp_subscription_id']]+=c['amount']
            if not c['source_loaded']:
                carry_pending[c['target_subscription_id']]+=c['amount']
    visits=defaultdict(list)
    for row in con.execute('''SELECT v.* FROM single_visits v JOIN subscriptions x ON x.id=v.subscription_id
            WHERE x.student_id=? ORDER BY v.id''',(sid,)):
        visits[row['subscription_id']].append(dict(row))
    sources={}
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='erp_single_sales'").fetchone():
        sources={r['subscription_id']:dict(r) for r in con.execute('SELECT * FROM erp_single_sales WHERE student_id=?',(sid,))}
    groups={r['id']:dict(r) for r in con.execute('''SELECT g.*,b.name AS branch_name FROM groups g
        JOIN branches b ON b.id=g.branch_id''')}
    for row in rows:
        row.update(accounting_month=row['month'],display_month=row['month'],
            history_only=row['status']=='cancelled',include_in_totals=row['status']!='cancelled',
            paid_amount=min(row['price'],max(0,covered.get(row['id'],covered.get(str(row['id']),0)))),
            single_date=None,single_time='',single_date_status=None,date_needs_review=False)
        row.update(revenue_carry_in=carry_in[row['id']],revenue_carry_out=carry_out[row['id']],
                   revenue_carry_source_pending=carry_pending[row['id']])
        if row['kind']!='single':
            continue
        source=sources.get(row['id'],{})
        row['source_sale_id']=source.get('source_id')
        row['source_lesson_date']=source.get('lesson_date')
        row['source_invoice_id']=None
        if source:
            try:payload=json.loads(source.get('source_payload') or '{}')
            except (TypeError,ValueError):payload={}
            if isinstance(payload,dict):row['source_invoice_id']=payload.get('invoice_id') or payload.get('source_invoice_id')
        active=[v for v in visits[row['id']] if v['status']=='booked']
        booking=active[0] if len(active)==1 and active[0]['student_id']==sid else None
        if booking:
            try:
                booked=date.fromisoformat(booking['date'])
                if booked.isoformat()!=booking['date'] or booking['group_id'] not in groups:raise ValueError()
            except (TypeError,ValueError):booking=None
        if booking:
            group=groups[booking['group_id']]
            version=schedule.on(con,group,booked)
            row.update(single_date=booking['date'],display_month=booking['date'][:7],
                single_time=logic.group_time(version,booked.weekday()),single_visit_id=booking['id'],
                single_date_status='confirmed',group_id=booking['group_id'],group_name=version['name'],
                group_weekdays=version.get('weekdays',''),group_time=version.get('time',''),
                branch_name=group['branch_name'])
        else:
            row['single_date_status']='ambiguous' if active else 'not_booked'
            row['date_needs_review']=row['status']!='cancelled'
            # Source/enrollment may identify a group, never a missing visit day.
            group=groups.get(source.get('group_id') or row.get('group_id'))
            if group:
                row.update(group_id=group['id'],group_name=group['name'],branch_name=group['branch_name'])
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='erp_history_subscriptions'").fetchone():
        for h in con.execute('''SELECT h.*,g.name group_name,b.name branch_name FROM erp_history_subscriptions h
                JOIN groups g ON g.id=h.group_id JOIN branches b ON b.id=g.branch_id WHERE h.student_id=? ORDER BY h.month DESC,h.id''',(sid,)):
            x=dict(h);rows.append({'id':'erp-history-'+str(x['id']),'student_id':sid,'enrollment_id':None,'month':x['month'],
                'tariff':'fixed','lessons':x['lessons'],'price':x.get('revenue_amount'),'status':'historical','kind':'month','valid_from':x['valid_from'],
                'valid_until':x['valid_until'],'group_id':x['group_id'],'group_name':x['group_name'],'branch_name':x['branch_name'],
                'history_import':True,'source_subscription_id':x['source_id'],'display_month':x['month'],'accounting_month':None,
                'history_only':True,'include_in_totals':False,'paid_amount':None,'analytics_only':True,'issued_by_name':'Перенос истории ERP',
                'revenue_carry_in':0,'revenue_carry_out':carry_history_out[x['source_id']],'revenue_carry_source_pending':0})
    return rows


def unpaid_group_names(con, sid, ym, enrollments):
    """Warn about actual current bills first, then a missing recurring month.

    Coverage follows the existing FIFO ledger. A paid visit in another invoice
    of the same group can remove a false missing-month warning, but never hide
    an independently outstanding bill, including an inactive enrollment's bill.
    """
    from . import subscriptiondates
    rows=rows_for_card(con,sid)
    owed={};covered=set()
    for row in rows:
        if row.get('history_import'):
            continue
        if row['history_only'] or row['display_month']!=ym or not row['group_id']:
            continue
        gid=row['group_id']
        if row['price']>row['paid_amount']:
            owed[gid]=row.get('group_name') or 'Группа №'+str(gid)
        else:
            covered.add(gid)
    entitlement=subscriptiondates.calendar_entitlements(con,sid,ym)
    names=[];seen=set()
    for enrollment in enrollments:
        gid=enrollment['group_id']
        if gid in seen:
            continue
        seen.add(gid)
        if gid in owed or gid not in covered and subscriptiondates.recurring_calendar_allowed(gid,entitlement):
            names.append(enrollment['group_name'])
    for gid,name in owed.items():
        if gid not in seen:
            names.append(name);seen.add(gid)
    return names
