"""Раздел «Клиенты и лиды» (Юлия, 04.11): один список всех карточек.
Лид: ни одной покупки (ни одного оплаченного абонемента или разового). Клиент: была хоть одна оплата.
Фильтры: Лиды / Клиенты, только исходный статус ERP, поиск по имени и телефону.
"""
from __future__ import annotations

CLIENT_STATUSES = [("active", "Занимается"), ("single", "Разовые занятия"), ("unpaid", "Не оплачен месяц"),
                   ("left", "Ушёл, бывший клиент"), ("promo", "Ждут акцию")]
ERP_MISSING = "__erp_missing__"
ERP_MISSING_LABEL = "Статус ERP не перенесён"
LIMIT = 300


def listing(con, kind: str = "all", status: str = "", erp: str = "", q: str = "", archived: bool = False) -> dict:
    from . import logic, services as sv
    st = sv.settings(con)
    cur = logic.month_of(sv.today())
    ids = lambda sql, *a: {r[0] for r in con.execute(sql, a)}
    bought = ids("SELECT DISTINCT student_id FROM subscriptions WHERE status='paid'") | ids("SELECT id FROM students WHERE converted_at IS NOT NULL")
    paid_cur = ids("SELECT DISTINCT student_id FROM subscriptions WHERE status='paid' AND month=? AND COALESCE(kind,'month')='month'", cur)
    month_any = ids("SELECT DISTINCT student_id FROM subscriptions WHERE status!='cancelled' AND month=? AND COALESCE(kind,'month')='month'", cur)
    single = ids("SELECT DISTINCT student_id FROM single_visits WHERE status='booked' AND substr(date,1,7)=?", cur) - month_any
    promo = ids("SELECT student_id FROM nodeadline WHERE removed_at IS NULL")
    labels = dict(sv.SALES_STAGES)

    where, args = "s.archived = ?", [1 if archived else 0]
    if q.strip():
        like, p = f"%{q.strip().lower()}%", sv.normalize_phone(q)
        where += """ AND (ulower(s.name) LIKE ? OR ulower(f.name) LIKE ? OR EXISTS (SELECT 1 FROM parents p WHERE p.family_id=f.id
                     AND (ulower(p.name) LIKE ? OR (? != '' AND p.phone_norm LIKE ?))))"""
        args += [like, like, like, p, f"%{p}%"]
    rows = con.execute(f"""SELECT s.id, s.name, s.funnel, s.stage, s.erp_status, s.group_id, s.family_id
                           FROM students s JOIN families f ON f.id=s.family_id WHERE {where} ORDER BY s.name""", args).fetchall()

    def status_of(r) -> tuple[str, str]:
        if r["id"] in bought or r["funnel"] == "members":
            if r["id"] in promo:
                return "promo", "Ждут акцию"
            if (r["funnel"] == "members" and r["stage"] == "left") or r["funnel"] == "sales":
                return "left", "Ушёл" if r["funnel"] == "members" else "Бывший клиент"
            if r["id"] in single:
                return "single", "Разовые занятия"
            if r["id"] in paid_cur:
                return "active", "Занимается"
            return "unpaid", "Не оплачен месяц"
        if r["id"] in single:
            return "single", "Разовые занятия"
        return "lead:" + r["stage"], labels.get(r["stage"], r["stage"])

    items, counts, erp_counts = [], {"leads": 0, "clients": 0}, {}
    for r in rows:
        internal_key, _ = status_of(r)
        is_lead = internal_key.startswith("lead:")
        counts["leads" if is_lead else "clients"] += 1
        if (kind == "leads" and not is_lead) or (kind == "clients" and is_lead):
            continue
        e = (r["erp_status"] or "").strip()
        key,label = (e,e) if e else (ERP_MISSING,ERP_MISSING_LABEL)
        counts[key] = counts.get(key, 0) + 1
        erp_counts[key] = erp_counts.get(key, 0) + 1
        if status and key != status:
            continue
        if erp and key != erp:
            continue
        items.append((r, key, label))
    out = []
    for r, key, label in items[:LIMIT]:
        s = dict(con.execute("SELECT * FROM students WHERE id=?", (r["id"],)).fetchone())
        out.append(sv.card_summary(con, s, st) | {"funnel": r["funnel"], "status": key, "status_label": label, "erp_status": r["erp_status"] or ""})
    erp_statuses = [(key,ERP_MISSING_LABEL if key==ERP_MISSING else key) for key in erp_counts]
    return {"rows": out, "total": len(items), "limit": LIMIT, "counts": counts,
            "statuses": {"clients": erp_statuses, "leads": erp_statuses},
            "erp": sorted(erp_counts.items(), key=lambda x: -x[1])}
