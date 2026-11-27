"""Renewals by service month, using purchase evidence available on a given day."""
import json
from datetime import date
from functools import lru_cache

from . import logic, sale_locations


def sale_day(value):
    value = str(value or "")[:10]
    if len(value) == 10 and value[2] == "." and value[5] == ".":
        value = value[6:] + "-" + value[3:5] + "-" + value[:2]
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def periods(con, today, branch_id=None, group_id=None):
    """Return a request-local calculator; CRM student IDs are the cohort identity."""
    branches = dict(con.execute("SELECT id,branch_id FROM groups"))
    lookup = sale_locations.resolver(con)
    scope_branch = branches[group_id] if group_id else branch_id

    @lru_cache(None)
    def members(month):
        first_sale = {}
        undated = set()
        missing_group = False

        def add(sid, gid, bid, day, verified_by=None):
            nonlocal missing_group
            if scope_branch and scope_branch != bid:
                return
            if group_id and gid != group_id:
                if gid is None:
                    missing_group = True
                return
            if day is None:
                undated.add(sid)
                day = verified_by
            if day is not None and (sid not in first_sale or day < first_sale[sid]):
                first_sale[sid] = day

        for sid, gid, paid_at in con.execute("""SELECT s.id,COALESCE(e.group_id,s.group_id),
                COALESCE(NULLIF(x.paid_at,''), CASE WHEN x.erp_full_managed=1 THEN x.created_at END)
                FROM subscriptions x JOIN students s ON x.student_id=s.id
                LEFT JOIN enrollments e ON e.id=x.enrollment_id
                WHERE x.month=? AND x.status='paid' AND COALESCE(x.kind,'month')='month'""", (month,)):
            add(sid, gid, branches.get(gid), sale_day(paid_at), today)
        for sid, gid, payload, recorded in con.execute("""SELECT student_id,group_id,source_payload,imported_at
                FROM erp_history_subscriptions WHERE month=?""", (month,)):
            try:
                source = json.loads(payload)
            except (ValueError, TypeError):
                source = {}
            day = (source.get("sale_date") or source.get("sale_at") or source.get("sold_at")) if isinstance(source, dict) else None
            add(sid, gid, branches.get(gid), sale_day(day), sale_day(recorded))
        if not scope_branch:
            for sid, recorded in con.execute("SELECT student_id,recorded_at FROM erp_prior_memberships WHERE month=?", (month,)):
                add(sid, None, None, None, sale_day(recorded))
        for sid, key, gid, sold in con.execute("""SELECT h.student_id,h.row_key,a.group_id,h.sale
                FROM erp_revenue_history h LEFT JOIN erp_revenue_groups a USING(row_key)
                WHERE h.month=? AND h.kind LIKE 'Абонемент%'""", (month,)):
            bid, source_group = lookup(key)
            gid = gid or source_group
            add(sid, gid, branches.get(gid, bid), sale_day(sold))
        return first_sale, undated, missing_group

    @lru_cache(None)
    def rate(month, cutoff):
        def cohort(m):
            dated, undated, missing_group = members(m)
            known = {sid for sid, day in dated.items() if day <= cutoff}
            return known, undated - known, missing_group

        prior, uncertain_prior, prior_group_gap = cohort(logic.shift_month(month, -1))
        current, uncertain_current, group_gap = cohort(month)
        reason = None
        if group_gap or prior_group_gap:
            reason = "Для этого сравнения не хватает данных о группах."
        elif uncertain_prior or (prior - current) & uncertain_current:
            reason = "В истории есть абонемент без подтверждённой даты продажи."
        elif not prior:
            reason = "Нет держателей абонемента за предыдущий месяц."
        renewed = len(prior & current)
        return dict(value=round(100 * renewed / len(prior), 1) if not reason else None,
                    previous=None if prior_group_gap or uncertain_prior else len(prior),
                    renewed=None if reason else renewed, as_of=cutoff.isoformat(), reason=reason)

    def snapshot(month, offset=0):
        target = logic.shift_month(month, offset)
        if month < today.strftime("%Y-%m"):
            cutoff = logic.month_bounds(target)[1]
        else:
            # Shift the observation date along with the service month, including advance sales.
            _, last = logic.month_bounds(logic.shift_month(today.strftime("%Y-%m"), offset))
            cutoff = last.replace(day=min(last.day, today.day))
        return rate(target, cutoff)

    return snapshot
