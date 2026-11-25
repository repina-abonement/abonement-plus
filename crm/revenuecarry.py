"""Управленческий перенос выручки при зачёте пропусков."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from . import logic


def init(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS revenue_carries (
      id INTEGER PRIMARY KEY,
      absence_id INTEGER NOT NULL UNIQUE REFERENCES attendance(id) ON DELETE CASCADE,
      student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
      group_id INTEGER NOT NULL REFERENCES groups(id),
      source_month TEXT NOT NULL,
      target_month TEXT NOT NULL,
      amount INTEGER NOT NULL CHECK(amount > 0),
      target_subscription_id INTEGER NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
      source_subscription_id INTEGER REFERENCES subscriptions(id),
      source_erp_subscription_id TEXT,
      basis TEXT NOT NULL DEFAULT '',
      source_loaded INTEGER NOT NULL DEFAULT 1,
      active INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL,
      created_by TEXT NOT NULL DEFAULT '',
      cancelled_at TEXT,
      cancelled_by TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_revenue_carries_month ON revenue_carries(source_month,target_month,active);
    """)


def _source(con, absence, source_month):
    native = con.execute("""SELECT x.id FROM subscriptions x
      LEFT JOIN enrollments e ON e.id=x.enrollment_id
      WHERE x.student_id=? AND x.month=? AND x.status='paid'
        AND COALESCE(e.group_id,(SELECT group_id FROM students WHERE id=x.student_id))=?
      ORDER BY (x.tariff='flex' AND COALESCE(x.kind,'month')='month') DESC,x.id LIMIT 1""", (absence['student_id'], source_month, absence['group_id'])).fetchone()
    if native:
        return native['id'], None, 1
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='erp_history_subscriptions'").fetchone():
        historical = con.execute("""SELECT source_id FROM erp_history_subscriptions
          WHERE student_id=? AND group_id=? AND month=? AND revenue_amount IS NOT NULL
          ORDER BY (manual_tariff IS 'flex') DESC,id LIMIT 1""", (absence['student_id'], absence['group_id'], source_month)).fetchone()
        if historical:
            return None, historical['source_id'], 1
    return None, None, 0


def record_invoice(con, subscription_id: int, absence_ids: list[int], amount_each: int, user: dict):
    """Idempotently attach each credited absence to one target subscription."""
    target = con.execute("""SELECT x.*,COALESCE(e.group_id,s.group_id) group_id
      FROM subscriptions x JOIN students s ON s.id=x.student_id
      LEFT JOIN enrollments e ON e.id=x.enrollment_id WHERE x.id=?""", (subscription_id,)).fetchone()
    if not target:
        raise ValueError('Абонемент зачёта не найден')
    source_month = logic.shift_month(target['month'], -1)
    for absence_id in absence_ids:
        absence = con.execute("SELECT * FROM attendance WHERE id=?", (absence_id,)).fetchone()
        if not absence or absence['student_id'] != target['student_id'] or absence['status'] != 'absent' or absence['date'][:7] != source_month:
            raise ValueError('Пропуск не относится к этому абонементу')
        existing = con.execute("SELECT * FROM revenue_carries WHERE absence_id=?", (absence_id,)).fetchone()
        if existing and existing['active']:
            if existing['target_subscription_id'] != subscription_id or existing['amount'] != amount_each:
                raise ValueError('Пропуск уже связан с другим переносом выручки')
            continue
        source_id, source_erp_id, loaded = _source(con, absence, source_month)
        if existing:
            con.execute("""UPDATE revenue_carries SET target_subscription_id=?,amount=?,target_month=?,group_id=?,
                          source_subscription_id=?,source_erp_subscription_id=?,source_loaded=?,
                          active=1,cancelled_at=NULL,cancelled_by='' WHERE id=?""",
                        (subscription_id, amount_each, target['month'], absence['group_id'],
                         source_id, source_erp_id, loaded, existing['id']))
            continue
        con.execute("""INSERT INTO revenue_carries(absence_id,student_id,group_id,source_month,target_month,amount,
          target_subscription_id,source_subscription_id,source_erp_subscription_id,basis,source_loaded,created_at,created_by)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (absence_id,target['student_id'],absence['group_id'],source_month,target['month'],amount_each,
           subscription_id,source_id,source_erp_id,'Зачёт пропуска в оплату следующего месяца',loaded,
           datetime.now().isoformat(timespec='seconds'),user.get('name','')))


def cancel_invoice(con, subscription_id: int, user: dict):
    con.execute("""UPDATE revenue_carries SET active=0,cancelled_at=?,cancelled_by=?
      WHERE target_subscription_id=? AND active=1""",
      (datetime.now().isoformat(timespec='seconds'),user.get('name',''),subscription_id))


def adjustments(con, month: str, group_id: int | None = None, branch_id: int | None = None):
    """Paired recognized adjustments, grouped by student and group."""
    scope = " AND r.group_id=?" if group_id else " AND g.branch_id=?" if branch_id else ""
    args = (group_id,) if group_id else (branch_id,) if branch_id else ()
    rows = con.execute("""WITH monthly AS (
        SELECT r.student_id,
          CASE WHEN r.target_month=? THEN COALESCE(e.group_id,r.group_id) ELSE r.group_id END group_id,
          CASE WHEN r.target_month=? THEN r.amount ELSE -r.amount END amount
        FROM revenue_carries r JOIN subscriptions x ON x.id=r.target_subscription_id
        LEFT JOIN enrollments e ON e.id=x.enrollment_id
        WHERE r.active=1 AND r.source_loaded=1 AND x.status='paid'
          AND (r.source_month=? OR r.target_month=?)
      )
      SELECT r.student_id,r.group_id,SUM(r.amount) amount
      FROM monthly r JOIN groups g ON g.id=r.group_id WHERE 1=1""" + scope + " GROUP BY r.student_id,r.group_id",
      (month,month,month,month,*args)).fetchall()
    return [dict(row) for row in rows]


def pending_source(con, student_id: int):
    return [dict(row) for row in con.execute("""SELECT r.*,x.month target_subscription_month
      FROM revenue_carries r JOIN subscriptions x ON x.id=r.target_subscription_id
      WHERE r.student_id=? AND r.active=1 AND r.source_loaded=0 ORDER BY r.id""", (student_id,))]


def refresh_historical_sources(con):
    """Activate pending debits once the exact historical source sale is loaded."""
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='erp_history_subscriptions'").fetchone():
        return 0
    changed = 0
    for row in con.execute("SELECT * FROM revenue_carries WHERE active=1 AND source_loaded=0"):
        source = con.execute("""SELECT source_id FROM erp_history_subscriptions
          WHERE student_id=? AND group_id=? AND month=? AND revenue_amount IS NOT NULL
            AND (? IS NULL OR source_id=?) ORDER BY id LIMIT 1""",
          (row['student_id'],row['group_id'],row['source_month'],row['source_erp_subscription_id'],row['source_erp_subscription_id'])).fetchone()
        if source:
            con.execute("UPDATE revenue_carries SET source_erp_subscription_id=?,source_loaded=1 WHERE id=?",
                        (source['source_id'],row['id']))
            changed += 1
    return changed


def historical_preview(con, student_id: int, target_subscription_id: int, absence_ids: list[int],
                       source_erp_subscription_id: str, basis: str):
    """Review a documented legacy carry without inventing an ERP payment."""
    basis=' '.join(str(basis or '').split())
    source_erp_subscription_id=str(source_erp_subscription_id or '').strip()
    target=con.execute("SELECT * FROM subscriptions WHERE id=? AND student_id=? AND status!='cancelled'",
                       (target_subscription_id,student_id)).fetchone()
    issues=[]
    if not target:issues.append('Целевой абонемент не найден')
    if not basis:issues.append('Сохраните основание переноса')
    if not source_erp_subscription_id:issues.append('Укажите ID исходного абонемента ERP')
    absences=[dict(x) for x in con.execute("SELECT * FROM attendance WHERE id IN (%s)" % ','.join('?'*len(absence_ids)),absence_ids)] if absence_ids else []
    if len(absences)!=len(set(absence_ids)) or any(x['student_id']!=student_id or x['status']!='absent' for x in absences):
        issues.append('Выбранные пропуски не найдены однозначно')
    source=con.execute("SELECT * FROM erp_history_subscriptions WHERE source_id=? AND student_id=?",
        (source_erp_subscription_id,student_id)).fetchone() if source_erp_subscription_id and con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='erp_history_subscriptions'").fetchone() else None
    per_lesson=int(target['per_lesson']) if target else 0
    state={'student_id':student_id,'target_subscription_id':target_subscription_id,'absence_ids':sorted(set(absence_ids)),
           'source_erp_subscription_id':source_erp_subscription_id,'basis':basis,'amount':len(set(absence_ids))*per_lesson,
           'source_loaded':bool(source),'source_month':logic.shift_month(target['month'],-1) if target else None,
           'target_month':target['month'] if target else None,'issues':issues}
    state['token']=hashlib.sha256(json.dumps(state,ensure_ascii=False,sort_keys=True).encode()).hexdigest() if not issues else None
    return state


def historical_context(con, erp_id: str):
    matches=con.execute("SELECT id,name FROM students WHERE erp_id=?",(str(erp_id or '').strip(),)).fetchall()
    if len(matches)!=1:raise ValueError('Карточка по ERP ID не найдена однозначно')
    student=matches[0]
    subscriptions=[dict(x) for x in con.execute("""SELECT x.id,x.month,x.price,x.per_lesson,x.credit_lessons,g.name group_name
      FROM subscriptions x LEFT JOIN enrollments e ON e.id=x.enrollment_id LEFT JOIN groups g ON g.id=e.group_id
      WHERE x.student_id=? AND x.status!='cancelled' ORDER BY x.month DESC,x.id DESC""",(student['id'],))]
    absences=[dict(x) for x in con.execute("""SELECT a.id,a.date,a.group_id,a.resolved,a.resolved_month,g.name group_name
      FROM attendance a JOIN groups g ON g.id=a.group_id WHERE a.student_id=? AND a.status='absent'
      ORDER BY a.date DESC,a.id DESC""",(student['id'],))]
    return {'student':dict(student),'subscriptions':subscriptions,'absences':absences,
            'pending':pending_source(con,student['id'])}


def historical_apply(con, student_id: int, target_subscription_id: int, absence_ids: list[int],
                     source_erp_subscription_id: str, basis: str, token: str, user: dict):
    checked=historical_preview(con,student_id,target_subscription_id,absence_ids,source_erp_subscription_id,basis)
    if not checked['token'] or checked['token']!=token:raise ValueError('Сверка изменилась — проверьте перенос повторно')
    target=con.execute('SELECT per_lesson FROM subscriptions WHERE id=?',(target_subscription_id,)).fetchone()
    record_invoice(con,target_subscription_id,absence_ids,target['per_lesson'],user)
    con.execute("""UPDATE revenue_carries SET source_subscription_id=NULL,source_erp_subscription_id=?,basis=?,source_loaded=?
      WHERE target_subscription_id=? AND absence_id IN (%s)""" % ','.join('?'*len(absence_ids)),
      (source_erp_subscription_id,basis,1 if checked['source_loaded'] else 0,target_subscription_id,*absence_ids))
    return historical_preview(con,student_id,target_subscription_id,absence_ids,source_erp_subscription_id,basis)
