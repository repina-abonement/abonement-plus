"""Immutable sale attribution and recorded date exclusions; never infer legacy sellers."""
import json


def init(con):
    cols={r[1] for r in con.execute('PRAGMA table_info(subscriptions)')}
    for name,kind in [('issued_by','INTEGER'),('issued_by_name',"TEXT NOT NULL DEFAULT ''"),('exclusion_reasons',"TEXT NOT NULL DEFAULT '{}'"), ('incomplete_value','INTEGER')]:
        if name not in cols:
            con.execute(f'ALTER TABLE subscriptions ADD COLUMN {name} {kind}')


def record(con,subid,user,quote=None,reason=''):
    quote=quote or {}
    cutoff=quote.get('trial_cutoff') or ''
    reasons={day:('Пробное занятие' if day==cutoff else 'До пробного занятия') if cutoff and day<=cutoff else (reason.strip() or 'Причина не указана') for day in quote.get('excluded_dates',[])}
    con.execute('UPDATE subscriptions SET issued_by=?,issued_by_name=?,exclusion_reasons=? WHERE id=?',((user or {}).get('id'),(user or {}).get('name',''),json.dumps(reasons,ensure_ascii=False),subid))
    if "incomplete_value" in quote:
        con.execute('UPDATE subscriptions SET incomplete_value=? WHERE id=?', (quote["incomplete_value"], subid))
