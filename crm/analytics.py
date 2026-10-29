"""Аналитика бизнеса поверх дашборда: филиалы и группы, тренеры, отток и продления, источники с ценой ученика.

Всё считается из абонементов (оплаченных), посещений, карточек лидов и рекламных трат. Держатель месяца —
ребёнок с хотя бы одним оплаченным абонементом на этот месяц.
"""
from __future__ import annotations

from datetime import date

from . import logic, sources


def _pct(a, b):
    return round(a * 100 / b) if b else None


def _holders(con, ym: str, branch_id: int | None = None) -> dict[int, dict]:
    """student_id -> {group_id, branch_id, revenue}"""
    out = {}
    for r in con.execute("""SELECT x.student_id, x.price, COALESCE(e.group_id, s.group_id) AS gid, g.branch_id
                            FROM subscriptions x JOIN students s ON s.id=x.student_id
                            LEFT JOIN enrollments e ON e.id=x.enrollment_id LEFT JOIN groups g ON g.id=COALESCE(e.group_id, s.group_id)
                            WHERE x.month=? AND x.status='paid'""", (ym,)):
        if branch_id and r["branch_id"] != branch_id:
            continue
        h = out.setdefault(r["student_id"], {"group_id": r["gid"], "branch_id": r["branch_id"], "revenue": 0})
        h["revenue"] += r["price"]
    return out


def _attendance(con, ym: str) -> dict[int, list[int]]:
    a, b = logic.month_bounds(ym)
    out = {}
    for gid, st, n in con.execute("""SELECT group_id, status, COUNT(*) FROM attendance WHERE date BETWEEN ? AND ?
                                     GROUP BY group_id, status""", (a.isoformat(), b.isoformat())):
        x = out.setdefault(gid, [0, 0])
        x[0 if st == "present" else 1] += n
    return out


def groups_report(con, ym: str, branch_id: int | None = None) -> dict:
    prev = logic.shift_month(ym, -1)
    h, hp = _holders(con, ym, branch_id), _holders(con, prev, branch_id)
    att = _attendance(con, ym)
    coaches = {r[0]: r[1] for r in con.execute("SELECT id, name FROM users")}
    branches = {r[0]: r[1] for r in con.execute("SELECT id, name FROM branches")}
    rows = []
    for g in con.execute("""SELECT * FROM groups WHERE active=1 AND is_makeup=0""" + (" AND branch_id=?" if branch_id else "")
                         + " ORDER BY branch_id, weekdays, time", (branch_id,) if branch_id else ()):
        kids = [sid for sid, x in h.items() if x["group_id"] == g["id"]]
        was = [sid for sid, x in hp.items() if x["group_id"] == g["id"]]
        stayed = len(set(was) & set(h))
        p, a = att.get(g["id"], [0, 0])
        rows.append({"id": g["id"], "name": g["name"], "branch": branches.get(g["branch_id"], ""), "branch_id": g["branch_id"],
                     "coach": coaches.get(g["coach_id"], ""), "coach_id": g["coach_id"], "capacity": g["capacity"],
                     "kids": len(kids), "fill": _pct(len(kids), g["capacity"]),
                     "revenue": sum(h[s]["revenue"] for s in kids), "prev": len(was),
                     "left": len(was) - stayed, "retention": _pct(stayed, len(was)),
                     "attendance": _pct(p, p + a)})
    br = {}
    for r in rows:
        b = br.setdefault(r["branch_id"], {"branch": r["branch"], "groups": 0, "capacity": 0, "kids": 0, "revenue": 0,
                                           "prev": 0, "left": 0, "present": 0, "total": 0})
        b["groups"] += 1
        for k in ("capacity", "kids", "revenue", "prev", "left"):
            b[k] += r[k]
        p, a = att.get(r["id"], [0, 0])
        b["present"] += p
        b["total"] += p + a
    for b in br.values():
        b["fill"] = _pct(b["kids"], b["capacity"])
        b["retention"] = _pct(b["prev"] - b["left"], b["prev"])
        b["attendance"] = _pct(b["present"], b["total"])
    return {"groups": rows, "branches": sorted(br.values(), key=lambda x: -x["revenue"])}


def coaches_report(con, ym: str, branch_id: int | None = None) -> list[dict]:
    g = groups_report(con, ym, branch_id)["groups"]
    att = _attendance(con, ym)
    out = {}
    for r in g:
        if not r["coach_id"]:
            continue
        c = out.setdefault(r["coach_id"], {"coach": r["coach"], "groups": 0, "kids": 0, "capacity": 0, "revenue": 0,
                                           "prev": 0, "left": 0, "present": 0, "total": 0})
        c["groups"] += 1
        for k in ("kids", "capacity", "revenue", "prev", "left"):
            c[k] += r[k]
        p, a = att.get(r["id"], [0, 0])
        c["present"] += p
        c["total"] += p + a
    for c in out.values():
        c["fill"] = _pct(c["kids"], c["capacity"])
        c["retention"] = _pct(c["prev"] - c["left"], c["prev"])
        c["attendance"] = _pct(c["present"], c["total"])
    return sorted(out.values(), key=lambda x: -x["kids"])


def churn_report(con, ym: str, branch_id: int | None = None) -> dict:
    prev, nxt = logic.shift_month(ym, -1), logic.shift_month(ym, 1)
    h, hp = _holders(con, ym, branch_id), _holders(con, prev, branch_id)
    first = {r[0]: r[1] for r in con.execute("SELECT student_id, MIN(month) FROM subscriptions WHERE status='paid' GROUP BY student_id")}
    months_paid = {r[0]: (r[1], r[2]) for r in con.execute(
        "SELECT student_id, COUNT(DISTINCT month), SUM(price) FROM subscriptions WHERE status='paid' GROUP BY student_id")}
    names = {r["id"]: dict(r) for r in con.execute(
        """SELECT s.id, s.name, s.stage, s.refusal_reason, s.family_id, g.name AS grp FROM students s LEFT JOIN groups g ON g.id=s.group_id
           WHERE s.funnel='members'""")}
    unpaid = {r[0]: r[1] for r in con.execute(
        "SELECT student_id, SUM(price) FROM subscriptions WHERE month=? AND status='invoiced' GROUP BY student_id", (ym,))}

    def person(sid, extra=None):
        n = names.get(sid, {})
        mp = months_paid.get(sid, (0, 0))
        return {"id": sid, "name": n.get("name", "?"), "group": n.get("grp") or "", "family_id": n.get("family_id"),
                "months": mp[0], "ltv": mp[1] or 0, "since": first.get(sid), **(extra or {})}

    gone = [sid for sid in hp if sid not in h]
    left = [person(s, {"debt": unpaid.get(s, 0), "status": "debt" if s in unpaid else "left" if names.get(s, {}).get("stage") == "left" else "silent"})
            for s in gone]
    left.sort(key=lambda x: (x["status"] != "debt", -x["months"]))
    # продление на следующий месяц
    nx = _holders(con, nxt, branch_id)
    inv_next = {r[0]: r[1] for r in con.execute(
        "SELECT student_id, SUM(price) FROM subscriptions WHERE month=? AND status='invoiced' GROUP BY student_id", (nxt,))}
    ren = {"paid": 0, "invoiced": 0, "none": 0, "paid_rub": 0, "invoiced_rub": 0}
    waiting = []
    for sid in h:
        if sid in nx:
            ren["paid"] += 1
            ren["paid_rub"] += nx[sid]["revenue"]
        elif sid in inv_next:
            ren["invoiced"] += 1
            ren["invoiced_rub"] += inv_next[sid]
            waiting.append(person(sid, {"debt": inv_next[sid], "status": "invoiced"}))
        else:
            ren["none"] += 1
            waiting.append(person(sid, {"debt": 0, "status": "none"}))
    waiting.sort(key=lambda x: (x["status"] != "invoiced", -x["months"]))
    # отток по месяцам за год
    series, prev_series, labels = [], [], []

    def churn_of(m):
        a, b = _holders(con, logic.shift_month(m, -1), branch_id), _holders(con, m, branch_id)
        return _pct(len(set(a) - set(b)), len(a)) if b else None

    for i in range(11, -1, -1):
        m = logic.shift_month(ym, -i)
        series.append(churn_of(m))
        prev_series.append(churn_of(logic.shift_month(m, -12)))
        labels.append(logic.month_label(m).split()[0].capitalize())
    lt = [v[0] for v in months_paid.values() if v[0]]
    ltv = [v[1] for v in months_paid.values() if v[0]]
    reasons = {}
    for s in gone:
        r = names.get(s, {}).get("refusal_reason") or ""
        if r:
            reasons[r] = reasons.get(r, 0) + 1
    return {"prev_holders": len(hp), "left_count": len(gone), "churn": _pct(len(gone), len(hp)),
            "left": left[:200], "renewal": ren, "renewal_pct": _pct(ren["paid"], len(h)), "next_label": logic.month_label(nxt),
            "waiting": waiting[:200], "series": series, "prev_series": prev_series, "labels": labels,
            "avg_months": round(sum(lt) / len(lt), 1) if lt else None, "avg_ltv": round(sum(ltv) / len(ltv)) if ltv else None,
            "reasons": sorted(reasons.items(), key=lambda x: -x[1])}


def sources_report(con, ym: str) -> dict:
    a, b = logic.month_bounds(ym)
    rep = sources.report(con, a.isoformat(), b.isoformat())
    spend = {}
    try:
        for sid, amount in con.execute("""SELECT p.source_id, SUM(d.amount) FROM ad_deals d JOIN ad_places p ON p.id=d.place_id
                                          WHERE d.paid_at BETWEEN ? AND ? GROUP BY p.source_id""", (a.isoformat(), b.isoformat() + "T23:59")):
            spend[sid] = amount
        names = {r[0]: r[1] for r in con.execute("SELECT id, name FROM ad_sources")}
    except Exception:  # noqa: BLE001 — нет таблиц рекламы
        names = {}
    by_name = {names.get(k): v for k, v in spend.items()}
    revenue = {}
    for r in con.execute("""SELECT COALESCE(src.name, NULLIF(s.source,''), 'Без источника') AS nm, SUM(x.price)
                            FROM students s JOIN subscriptions x ON x.student_id=s.id AND x.status='paid'
                            LEFT JOIN ad_sources src ON src.id=s.source_id
                            WHERE s.converted_at BETWEEN ? AND ? GROUP BY nm""", (a.isoformat(), b.isoformat() + "T23:59")):
        revenue[r[0]] = r[1]
    for x in rep["sources"]:
        x["conv"] = _pct(x["bought"], x["leads"])
        x["spend"] = by_name.get(x["source"], 0)
        x["per_lead"] = round(x["spend"] / x["leads"]) if x["spend"] and x["leads"] else None
        x["per_buy"] = round(x["spend"] / x["bought"]) if x["spend"] and x["bought"] else None
        x["revenue"] = revenue.get(x["source"], 0)
    for c in rep["channels"]:
        c["conv"] = _pct(c["bought"], c["leads"])
    tot = {k: sum(x[k] for x in rep["sources"]) for k in ("leads", "trial", "came", "bought", "spend", "revenue")}
    tot["conv"] = _pct(tot["bought"], tot["leads"])
    rep["total"] = tot
    return rep


def report(con, ym: str | None = None, branch_id: int | None = None, part: str = "groups") -> dict:
    ym = ym or logic.month_of(date.today())
    out = {"month": ym, "month_label": logic.month_label(ym)}
    if part == "groups":
        out.update(groups_report(con, ym, branch_id))
    elif part == "coaches":
        out["coaches"] = coaches_report(con, ym, branch_id)
    elif part == "churn":
        out.update(churn_report(con, ym, branch_id))
    elif part == "sources":
        out.update(sources_report(con, ym))
    elif part == "program":
        out.update(program_report(con, ym))
    elif part == "trials":
        out.update(trials_report(con, ym, branch_id))
    return out


def trials_report(con, ym: str, branch_id: int | None = None) -> dict:
    """Конверсия пробных за месяц (по дате пробного): записано → пришли → купили в день пробного → купили позже.
    В разрезе филиалов, групп и тренеров, с причинами отказа и итогами, которые тренер отметил в отчёте о смене."""
    import json as _json
    rows = [dict(r) for r in con.execute(
        """SELECT s.id, s.trial_at, s.trial_came, s.converted_at, s.stage, s.funnel, s.refusal_reason,
                  g.id AS gid, g.name AS gname, b.id AS bid, b.name AS branch, u.name AS coach
           FROM students s JOIN groups g ON g.id=s.group_id JOIN branches b ON b.id=g.branch_id LEFT JOIN users u ON u.id=g.coach_id
           WHERE s.trial_at IS NOT NULL AND substr(s.trial_at,1,7)=? AND s.archived=0""" + (" AND b.id=?" if branch_id else ""),
        (ym, branch_id) if branch_id else (ym,))]
    coach_says = {}
    for r in con.execute("SELECT data FROM coach_reports WHERE substr(date,1,7)=? AND status='done'", (ym,)):
        coach_says.update((_json.loads(r[0] or "{}").get("trials") or {}))

    def agg(items):
        booked = len(items)
        came = sum(1 for x in items if x["trial_came"] == 1)
        same = sum(1 for x in items if x["converted_at"] and x["converted_at"][:10] <= x["trial_at"][:10])
        later = sum(1 for x in items if x["converted_at"] and x["converted_at"][:10] > x["trial_at"][:10])
        return {"booked": booked, "came": came, "bought_same_day": same, "bought_later": later,
                "came_pct": round(100 * came / booked) if booked else None,
                "bought_pct": round(100 * (same + later) / came) if came else None}
    by = lambda key, label: sorted(({"name": k, **agg([x for x in rows if x[key] == k])} for k in {x[key] for x in rows if x[key]}),
                                   key=lambda r: -r["booked"])
    reasons = {}
    for x in rows:
        if x["refusal_reason"]:
            reasons[x["refusal_reason"]] = reasons.get(x["refusal_reason"], 0) + 1
    from .coachreport import TRIAL
    says = {}
    for x in rows:
        v = coach_says.get(str(x["id"]))
        if v in TRIAL:
            says[TRIAL[v]] = says.get(TRIAL[v], 0) + 1
    return {"total": agg(rows), "branches": by("branch", "Филиал"), "groups": by("gname", "Группа"), "coaches": by("coach", "Тренер"),
            "reasons": sorted(({"reason": k, "n": v} for k, v in reasons.items()), key=lambda r: -r["n"]),
            "coach_says": [{"label": k, "n": v} for k, v in says.items()]}


def program_report(con, ym: str, slug: str = "sbornaya") -> dict:
    """Отчёт по направлению (сборная) за месяц: потенциал, факт, потери по причинам, отток, прибыль, безубыточность."""
    from . import program, services as sv
    p = con.execute("SELECT * FROM programs WHERE slug=?", (slug,)).fetchone()
    if not p:
        return {"program": None}
    pid = p["id"]
    arena = program.groups(con, pid, "arena")
    ofp = program.groups(con, pid, "ofp")
    gids = [g["id"] for g in program.groups(con, pid)] + [r[0] for r in con.execute(
        "SELECT id FROM groups WHERE program_id=? AND active=0", (pid,))]
    if not arena:
        return {"program": dict(p), "empty": True}
    pr = program.prices_for(con, pid, ym)
    n_arena = len(program._dates(con, arena[0]["id"], ym))
    thu = next((g for g in ofp if g["weekdays"] == "3"), ofp[0] if ofp else None)
    n_ofp_std = len(program._dates(con, thu["id"], ym)) if thu else 0
    full_pkg = n_arena * pr["arena"] + n_ofp_std * pr["ofp"]
    q = ",".join("?" * len(gids))
    subs = [dict(r) for r in con.execute(
        f"""SELECT x.*, s.name FROM subscriptions x JOIN enrollments e ON e.id=x.enrollment_id JOIN students s ON s.id=x.student_id
            WHERE e.group_id IN ({q}) AND x.month=? AND x.status!='cancelled' AND x.kind='month'""", (*gids, ym))]
    kids = {r[0]: r[1] for r in con.execute(
        f"""SELECT DISTINCT s.id, s.name FROM enrollments e JOIN students s ON s.id=e.student_id
            WHERE e.group_id=? AND e.active=1 AND s.stage!='left' AND s.archived=0""", (arena[0]["id"],))}
    for x in subs:
        kids.setdefault(x["student_id"], x["name"])
    paid = [x for x in subs if x["status"] == "paid"]
    actual = sum(x["price"] for x in paid)
    debts = sum(x["price"] for x in subs if x["status"] == "invoiced")
    with_sub = {x["student_id"] for x in subs}
    potential = full_pkg * len(kids)
    loss = {"partial": 0, "no_ofp": 0, "discounts": 0, "debts": debts, "not_bought": 0}
    late_gain = 0
    for x in subs:
        plan = program.plan_of(con, x["student_id"], pid)
        tariff = x["tariff"] if x["tariff"] in program.TARIFFS else (plan["tariff"] if plan else "pkg")
        full_t = n_arena * pr["arena_only"] if tariff == "arena_only" else full_pkg
        if tariff == "arena_only":
            loss["no_ofp"] += full_pkg - full_t
        loss["discounts"] += x["discount_amount"] or 0
        late_gain += x["late_fee"] or 0
        base = x["price"] + (x["discount_amount"] or 0) - (x["late_fee"] or 0)
        loss["partial"] += max(0, full_t - base)
    for sid in kids:
        if sid not in with_sub:
            loss["not_bought"] += full_pkg
    prev = logic.shift_month(ym, -1)
    prev_paid = {r[0]: r[1] for r in con.execute(
        f"""SELECT DISTINCT x.student_id, s.name FROM subscriptions x JOIN enrollments e ON e.id=x.enrollment_id JOIN students s ON s.id=x.student_id
            WHERE e.group_id IN ({q}) AND x.month=? AND x.status='paid'""", (*gids, prev))}
    paid_ids = {x["student_id"] for x in paid}
    left = [{"id": k, "name": v} for k, v in prev_paid.items() if k not in paid_ids]
    coach = round(actual * p["coach_pct"] / 100)
    rent = n_arena * p["arena_rent"]
    royalty = round(actual * p["royalty_pct"] / 100)
    acq = round(actual * p["acquiring_pct"] / 100)
    profit = actual - coach - rent - royalty - acq
    per_kid = actual / len(paid_ids) if paid_ids else full_pkg
    margin = per_kid * (1 - (p["coach_pct"] + p["royalty_pct"] + p["acquiring_pct"]) / 100)
    breakeven = -(-rent // margin) if margin > 0 else None
    coach_name = (con.execute("SELECT u.name FROM groups g JOIN users u ON u.id=g.coach_id WHERE g.id=?", (arena[0]["id"],)).fetchone() or [""])[0]
    by_tariff = {}
    for x in paid:
        plan = program.plan_of(con, x["student_id"], pid)
        t = x["tariff"] if x["tariff"] in program.TARIFFS else (plan["tariff"] if plan else "pkg")
        lab = program.TARIFFS[t]
        if t == "pkg" and plan and plan["ofp_group_id"]:
            lab += ", " + ("Чт" if any(g["id"] == plan["ofp_group_id"] and g["weekdays"] == "3" for g in ofp) else "Сб")
        by_tariff[lab] = by_tariff.get(lab, 0) + 1
    return {
        "program": {"id": pid, "name": p["name"], "coach_pct": p["coach_pct"], "royalty_pct": p["royalty_pct"],
                    "acquiring_pct": p["acquiring_pct"], "arena_rent": p["arena_rent"]},
        "month": ym, "month_label": logic.month_label(ym), "prices": pr, "lessons": {"arena": n_arena, "ofp": n_ofp_std},
        "full_pkg": full_pkg, "kids": len(kids), "paid_kids": len(paid_ids), "potential": potential, "actual": actual,
        "collect_pct": round(actual * 100 / potential) if potential else None, "loss": loss, "late_gain": late_gain,
        "left": left, "prev_kids": len(prev_paid), "churn": round(len(left) * 100 / len(prev_paid)) if prev_paid else None,
        "costs": {"coach": coach, "rent": rent, "royalty": royalty, "acquiring": acq}, "coach_name": coach_name,
        "profit": profit, "breakeven": int(breakeven) if breakeven else None, "per_kid": round(per_kid), "by_tariff": by_tariff,
        "unpaid": [{"id": x["student_id"], "name": x["name"], "amount": x["price"]} for x in subs if x["status"] == "invoiced"],
    }
