"""Read-only offers for children expected at a trial lesson today."""
from __future__ import annotations

from datetime import date

from . import logic, services as sv


TARIFF_LABELS = {
    "fixed": "Негибкий",
    "flex": "Гибкий",
    "pkg": "Арктика + ОФП",
    "arena_only": "Только Арктика",
}


def _variants(con, group) -> list[tuple[str, int | None, str]]:
    if group["program_id"]:
        ofp = list(con.execute("""SELECT id,name FROM groups WHERE program_id=? AND lesson_kind='ofp'
            AND active=1 ORDER BY name,id""", (group["program_id"],)))
        rows = [("arena_only", None, TARIFF_LABELS["arena_only"])]
        rows.extend(("pkg", r["id"], f"{TARIFF_LABELS['pkg']} · {r['name']}") for r in ofp)
        return rows
    from . import groupterms
    tariffs = ["fixed"] if groupterms.fixed_only(con, group["id"]) else ["fixed", "flex"]
    return [(tariff, None, TARIFF_LABELS[tariff]) for tariff in tariffs]


def _quote(con, sid: int, group_id: int, month: str, tariff: str, ofp_group_id: int | None,
           on: date, user: dict) -> dict:
    q = sv.quote_for(con, sid, month, group_id=group_id, tariff=tariff,
                     ofp_group_id=ofp_group_id, paid_date=on.isoformat(), user=user)
    return {
        "month": month,
        "total": q["total"],
        "lessons": q.get("lessons_paid", q.get("lessons", 0)),
        "discount": q.get("discount", {}).get("name") if q.get("discount") else "",
        "discount_percent": q.get("discount", {}).get("percent") if q.get("discount") else 0,
    }


def today_offers(con, user: dict, on: date | None = None) -> list[dict]:
    """Return today's booked trials and sale quotes without persisting any changes."""
    on = on or sv.today()
    day = on.isoformat()
    rows = con.execute("""SELECT s.id,s.name,s.trial_at,s.trial_came,s.converted_at,s.funnel,
            COALESCE(s.trial_group_id,s.group_id) AS trial_group_id,
            g.name AS group_name,g.time,g.age,g.program_id,b.name AS branch,
            p.name AS parent_name,p.phone
        FROM students s
        JOIN groups g ON g.id=COALESCE(s.trial_group_id,s.group_id)
        JOIN branches b ON b.id=g.branch_id
        LEFT JOIN parents p ON p.id=(SELECT p2.id FROM parents p2 WHERE p2.family_id=s.family_id
            ORDER BY p2.is_primary DESC,p2.id LIMIT 1)
        WHERE substr(s.trial_at,1,10)=? AND s.archived=0 AND s.stage!='refused'
        ORDER BY substr(s.trial_at,12,5),b.name,g.name,s.name""", (day,))
    current, nxt = day[:7], logic.shift_month(day[:7], 1)
    result = []
    for row in rows:
        group = con.execute("SELECT * FROM groups WHERE id=?", (row["trial_group_id"],)).fetchone()
        con.execute("SAVEPOINT trial_desk_quote")
        try:
            # Preview the state immediately after a successful trial. This is
            # intentionally rolled back: opening the workdesk never marks a
            # visit, converts a lead or creates an invoice.
            con.execute("""UPDATE students SET trial_came=1,converted_at=COALESCE(converted_at,?)
                WHERE id=?""", (day + "T" + (row["trial_at"] or "")[11:16], row["id"]))
            offers = []
            for tariff, ofp_group_id, label in _variants(con, group):
                prices = {}
                unavailable = {}
                for key, month in (("current", current), ("next", nxt)):
                    try:
                        quote = _quote(con, row["id"], group["id"], month, tariff, ofp_group_id, on, user)
                    except (sv.ApiError, ValueError) as error:
                        quote = None
                        unavailable[key] = {
                            "month": month,
                            "message": "После пробного занятий в этом месяце больше нет"
                            if isinstance(error, sv.ApiError) and error.message == "В абонементе должно остаться хотя бы одно занятие"
                            else "Не удалось рассчитать",
                        }
                    prices[key] = quote
                if any(prices.values()):
                    total = sum(q["total"] for q in prices.values()) if all(prices.values()) else None
                    offers.append({"tariff": tariff, "label": label, **prices,
                                   "unavailable": unavailable, "bundle_total": total})
        finally:
            con.execute("ROLLBACK TO SAVEPOINT trial_desk_quote")
            con.execute("RELEASE SAVEPOINT trial_desk_quote")
        result.append({
            "id": row["id"], "name": row["name"], "time": (row["trial_at"] or "")[11:16],
            "group": row["group_name"], "branch": row["branch"], "age": row["age"],
            "parent": row["parent_name"] or "", "phone": row["phone"] or "",
            "came": bool(row["trial_came"]), "offers": offers,
        })
    return result
