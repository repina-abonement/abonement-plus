"""Аналитика звонков операторов: нагрузка, пропущенные, скорость перезвона, продажи после звонка."""
from __future__ import annotations

from datetime import date, datetime, timedelta

PERIODS = {"today": "Сегодня", "week": "7 дней", "month": "30 дней"}


def _bounds(period: str) -> tuple[str, str, str, str]:
    """Начало и конец периода и такого же прошлого периода (для сравнения)."""
    now = datetime.now().replace(microsecond=0)
    if period == "today":
        start = datetime.combine(date.today(), datetime.min.time())
        prev_start = start - timedelta(days=1)
    else:
        days = 7 if period == "week" else 30
        start = now - timedelta(days=days)
        prev_start = start - timedelta(days=days)
    f = lambda d: d.isoformat(timespec="seconds")
    return f(start), f(now), f(prev_start), f(start)


def _mins(a: str, b: str) -> int:
    return int((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() // 60)


def _fmt_mins(m):
    if m is None:
        return None
    return f"{m} мин" if m < 60 else f"{m // 60} ч {m % 60:02d} мин"


def _summary(con, start: str, end: str) -> dict:
    rows = [dict(r) for r in con.execute("SELECT * FROM calls WHERE started_at BETWEEN ? AND ? ORDER BY started_at",
                                         (start, end))]
    inc = [r for r in rows if r["direction"] == "in"]
    out = [r for r in rows if r["direction"] == "out"]
    missed = [r for r in inc if r["status"] in ("missed", "busy", "failed")]
    # скорость перезвона: первый следующий исходящий или принятый входящий с этим номером
    delays, never = [], 0
    for m in missed:
        nxt = con.execute("""SELECT started_at FROM calls WHERE phone_norm=? AND started_at > ?
                             AND (direction='out' OR status='answered') ORDER BY started_at LIMIT 1""",
                          (m["phone_norm"], m["started_at"])).fetchone()
        if nxt:
            delays.append(_mins(m["started_at"], nxt[0]))
        else:
            never += 1
    delays.sort()
    return {
        "rows": rows, "incoming": len(inc), "outgoing": len(out), "missed": len(missed),
        "missed_pct": round(100 * len(missed) / len(inc)) if inc else None,
        "called_back": len(delays), "never_called_back": never,
        "median_callback": delays[len(delays) // 2] if delays else None,
        "fast_callback": sum(1 for d in delays if d <= 15),
        "talk_total": sum(r["talk"] or 0 for r in rows),
        "out_answered": sum(1 for r in out if r["status"] == "answered"),
    }


def analytics(con, period: str = "week") -> dict:
    period = period if period in PERIODS else "week"
    start, end, pstart, pend = _bounds(period)
    cur, prev = _summary(con, start, end), _summary(con, pstart, pend)
    names = {r[0]: r[1] for r in con.execute("SELECT sip_number, name FROM users WHERE sip_number != ''")}

    # по операторам
    ops: dict[str, dict] = {}
    for r in cur["rows"]:
        op = r["operator"] or ""
        if not op:
            continue
        o = ops.setdefault(op, {"number": op, "name": names.get(op, f"Номер {op}"), "out": 0, "out_answered": 0,
                                "in_answered": 0, "talk": 0, "talks": [], "families": set()})
        if r["direction"] == "out":
            o["out"] += 1
            o["out_answered"] += r["status"] == "answered"
        elif r["status"] == "answered":
            o["in_answered"] += 1
        if r["talk"]:
            o["talk"] += r["talk"]
            o["talks"].append(r["talk"])
        if r["family_id"] and r["status"] == "answered":
            o["families"].add((r["family_id"], r["started_at"]))
    # продажи: лиды, с которыми поговорил оператор, и сколько из них потом записались на пробное / купили
    for o in ops.values():
        fams = {}
        for fid, ts in o["families"]:
            fams[fid] = min(ts, fams.get(fid, ts))
        leads = trial = bought = 0
        for fid, ts in fams.items():
            st = con.execute("""SELECT MAX(trial_booked_at >= ?), MAX(converted_at >= ?), COUNT(*) FROM students
                                WHERE family_id=? AND (funnel='sales' OR converted_at >= ?)""",
                             (ts[:16], ts[:16], fid, ts[:16])).fetchone()
            if not st[2]:
                continue
            leads += 1
            trial += bool(st[0])
            bought += bool(st[1])
        o.update(leads=leads, trial=trial, bought=bought,
                 avg_talk=round(sum(o["talks"]) / len(o["talks"])) if o["talks"] else None,
                 reach=round(100 * o["out_answered"] / o["out"]) if o["out"] else None)
        del o["families"], o["talks"]
    op_list = sorted(ops.values(), key=lambda o: (-(o["out"] + o["in_answered"]), o["name"]))

    # когда чаще всего пропускаем: по часам
    hours = [0] * 24
    for r in cur["rows"]:
        if r["direction"] == "in" and r["status"] in ("missed", "busy", "failed"):
            hours[int(r["started_at"][11:13])] += 1

    waiting = [dict(r) for r in con.execute(
        """SELECT c.id, c.phone, c.started_at, f.name AS family_name,
                  (SELECT s.id FROM students s WHERE s.family_id=c.family_id ORDER BY s.id LIMIT 1) AS student_id
           FROM calls c LEFT JOIN families f ON f.id=c.family_id
           WHERE c.direction='in' AND c.status IN ('missed','busy','failed') AND c.seen=0
           ORDER BY c.started_at LIMIT 30""")]
    for w in waiting:
        w["waiting"] = _fmt_mins(_mins(w["started_at"], datetime.now().replace(microsecond=0).isoformat()))

    def delta(key):
        a, b = cur[key], prev[key]
        return None if a is None or b is None else a - b

    for d in (cur, prev):
        d.pop("rows")
    return {
        "period": period, "period_label": PERIODS[period],
        "summary": cur | {"median_callback_label": _fmt_mins(cur["median_callback"]),
                          "talk_total_label": _fmt_mins(cur["talk_total"] // 60) if cur["talk_total"] else "0 мин"},
        "prev": prev,
        "delta": {k: delta(k) for k in ("incoming", "outgoing", "missed", "missed_pct", "median_callback")},
        "operators": op_list, "missed_by_hour": hours, "waiting": waiting,
        "unassigned": sum(1 for o in op_list if o["name"].startswith("Номер ")),
    }
