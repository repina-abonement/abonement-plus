"""Coach access and branch-specific shift-report policy."""
from datetime import date


def reports_enabled(con, branch_id):
    row = con.execute('SELECT name FROM branches WHERE id=?', (branch_id,)).fetchone()
    return bool(row) and 'небо' not in row[0].casefold()


def nebo_only(con, uid):
    ids = {r[0] for r in con.execute('SELECT DISTINCT branch_id FROM groups WHERE active=1 AND (coach_id=? OR assistant_id=?)', (uid, uid))}
    ids.update(r[0] for r in con.execute('SELECT DISTINCT g.branch_id FROM substitutions s JOIN groups g ON g.id=s.group_id WHERE s.coach_id=? AND s.date>=? AND g.active=1', (uid, date.today().isoformat())))
    return bool(ids) and all(not reports_enabled(con, bid) for bid in ids)


def redact_contacts(value):
    if isinstance(value, dict):
        return {k: redact_contacts(v) for k, v in value.items() if k not in {'phone', 'phone_norm', 'parent_phone', 'email', 'messenger'}}
    if isinstance(value, list):
        return [redact_contacts(v) for v in value]
    return value


def redact_finances(value):
    financial = {'amount', 'price', 'per_lesson', 'salary', 'pay', 'fine', 'fine_amount', 'fine_per_minute', 'hour_rate', 'rate', 'debt', 'deposit', 'balance', 'cash_sum', 'env_prev', 'envelope', 'cash'}
    if isinstance(value, dict):
        return {k: redact_finances(v) for k, v in value.items() if k not in financial}
    if isinstance(value, list):
        return [redact_finances(v) for v in value]
    return value
