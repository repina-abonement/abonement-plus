"""One-time cleanup of the owner-confirmed synthetic sale."""
import hashlib


STUDENT_ID = 1550
IDENTITY = '3fc3b314d17a344c56608b752b1237c571c84c5ba25fe0ccb781cf3c3bafb010'
MARKER = 'maintenance_354'
PAYMENT_METHODS = ('cash', 'card_branch', 'sber_qr_branch', 'tochka_qr_link', 'transfer')
OWNED = frozenset((
    'enrollments', 'subscriptions', 'history', 'payments', 'tasks', 'recent_clients',
    'crm_activity', 'invoice_creation_requests', 'invoice_funding_policies',
    'invoice_payment_targets', 'manual_payment_requests', 'pay_links', 'pay_link_subscriptions',
    'price_corrections',
))


def _related(con, ids):
    """Find both declared and legacy typed references before deleting."""
    related = {}
    for table, in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        safe = '"' + table.replace('"', '""') + '"'
        columns = {row['name'] for row in con.execute(f'PRAGMA table_info({safe})')}
        refs = {row['from']: row['table'] for row in con.execute(f'PRAGMA foreign_key_list({safe})')
                if row['table'] in ids}
        for column, target in (('student_id', 'students'), ('subscription_id', 'subscriptions'),
                               ('payment_id', 'payments'), ('link_id', 'pay_links')):
            if column in columns:
                refs.setdefault(column, target)
        for column, target in refs.items():
            keys = ids[target]
            if not keys:
                continue
            ident = '"' + column.replace('"', '""') + '"'
            if con.execute(f'SELECT 1 FROM {safe} WHERE {ident} IN ({",".join("?" for _ in keys)})',
                           keys).fetchone():
                related.setdefault(table, []).append((ident, keys))
    return related


def apply(con):
    done = con.execute("SELECT 1 FROM integrations WHERE key=?", (MARKER,)).fetchone()
    if done:
        return 'complete'
    student = con.execute("SELECT * FROM students WHERE id=?", (STUDENT_ID,)).fetchone()
    if not student:
        return 'not-found'
    identity = ' '.join(sorted(student['name'].casefold().split()))
    if hashlib.sha256(identity.encode()).hexdigest() != IDENTITY:
        return 'identity-mismatch'
    if student['erp_id'] or student['trial_at']:
        return 'protected-records'
    subs = con.execute("SELECT * FROM subscriptions WHERE student_id=?", (STUDENT_ID,)).fetchall()
    if len(subs) != 1 or subs[0]['kind'] != 'month' or subs[0]['month'] != '2026-12':
        return 'sale-mismatch'
    if not subs[0]['created_at'].startswith('2026-11-26'):
        return 'sale-mismatch'
    subid = subs[0]['id']
    payments = con.execute("SELECT * FROM payments WHERE student_id=? OR subscription_id=?",
                           (STUDENT_ID, subid)).fetchall()
    if len(payments) > 1 or any(p['student_id'] != STUDENT_ID or p['subscription_id'] != subid
        or p['method'] not in PAYMENT_METHODS or p['amount'] <= 0 or p['batch']
        or not p['created_at'].startswith('2026-11-26') for p in payments):
        return 'payment-mismatch'
    links = con.execute("""SELECT * FROM pay_links
        WHERE student_id=? OR subscription_id=? OR id IN (
            SELECT link_id FROM pay_link_subscriptions WHERE subscription_id=?)""",
        (STUDENT_ID, subid, subid)).fetchall()
    if any(link['student_id'] != STUDENT_ID or not link['receipt_token'] or link['payment_id']
           or link['invoice_id'] or link['invoice_url'] or link['pk_payment_id']
           or link['status'] == 'paid' or link['checkout_state'] not in ('', 'new', 'pending') for link in links):
        return 'protected-payment-link'
    ids = {'students': (STUDENT_ID,), 'subscriptions': (subid,),
           'payments': tuple(p['id'] for p in payments), 'pay_links': tuple(link['id'] for link in links)}
    related = _related(con, ids)
    if set(related) - OWNED:
        return 'protected-records'
    for column, keys in related.get('invoice_payment_targets', []):
        rows = con.execute(f'SELECT payment_id,subscription_id FROM invoice_payment_targets WHERE {column}=?', keys)
        if any(row['payment_id'] not in ids['payments'] or row['subscription_id'] != subid for row in rows):
            return 'shared-payment'
    if ids['pay_links'] and con.execute(
            f'SELECT 1 FROM pay_link_subscriptions WHERE link_id IN ({",".join("?" for _ in ids["pay_links"])}) AND subscription_id!=?',
            (*ids['pay_links'], subid)).fetchone():
        return 'shared-link'
    con.execute('SAVEPOINT cleanup_test_sale')
    try:
        for table in ('crm_activity', 'recent_clients', 'manual_payment_requests', 'invoice_creation_requests',
                      'invoice_payment_targets', 'invoice_funding_policies', 'pay_link_subscriptions',
                      'pay_links', 'price_corrections'):
            for column, keys in related.get(table, []):
                con.execute(f'DELETE FROM "{table}" WHERE {column} IN ({",".join("?" for _ in keys)})', keys)
        con.execute('DELETE FROM students WHERE id=?', (STUDENT_ID,))
        if _related(con, ids) or con.execute("SELECT 1 FROM students WHERE id=?", (STUDENT_ID,)).fetchone():
            raise ValueError('Remaining CRM references')
        con.execute("INSERT INTO integrations(key,value) VALUES (?,?)", (MARKER, 'complete'))
    except Exception:
        con.execute('ROLLBACK TO cleanup_test_sale')
        con.execute('RELEASE cleanup_test_sale')
        return 'blocked'
    con.execute('RELEASE cleanup_test_sale')
    return 'complete'
