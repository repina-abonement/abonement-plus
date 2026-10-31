"""Автоцепочки сообщений родителям (Юлия выбрала 31.10.2026, по образцу Salesforce).

Серия сообщений по расписанию. Любой ответ родителя останавливает цепочку и ставит задачу ответить.
Цепочка тоже останавливается, когда цель достигнута (купил абонемент, оплатил месяц) или клиент ушёл.
По умолчанию все цепочки ВЫКЛЮЧЕНЫ: включает руководитель в Настройках, когда согласует тексты.
Сообщения уходят только с 10:00 до 20:00.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from . import comms, logic

CHAINS = {
    "after_trial": {
        "title": "После пробного, если не купили",
        "when": "Ребёнок был на пробном, но абонемент ещё не куплен. Отсчёт от дня пробного",
        "goal": "Купил абонемент или отказ",
        "steps": [
            {"days": 1, "text": "Здравствуйте! Как {child} после тренировки? Если хотите продолжить, подскажу дни и стоимость 🙂"},
            {"days": 3, "text": "В группе {group} ещё есть места. Записать {child} на постоянные тренировки?"},
            {"days": 7, "text": "Если пока не получилось, ничего страшного. Напишите, когда будет удобно, подберём группу и время 🙂"},
        ],
    },
    "renewal": {
        "title": "Продление абонемента",
        "when": "С 20 числа, если следующий месяц ещё не оплачен. Отсчёт от 20 числа",
        "goal": "Следующий месяц оплачен или ученик ушёл",
        "steps": [
            {"days": 0, "text": "Здравствуйте! Пора продлить абонемент на {month}, ждём {child} на тренировках ⚽ Оплатить без надбавки можно до 25 числа. Прислать ссылку на оплату?"},
            {"days": 4, "text": "Напоминаем: завтра, 25 числа, последний день, чтобы оплатить {month} без надбавки. Прислать ссылку на оплату?"},
        ],
    },
}
HOURS = (10, 20)

SCHEMA = """
CREATE TABLE IF NOT EXISTS chain_runs (
    id INTEGER PRIMARY KEY,
    chain TEXT NOT NULL,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    period TEXT NOT NULL,                  -- для какого пробного / месяца, чтобы не начать дважды
    started_at TEXT NOT NULL,
    step INTEGER NOT NULL DEFAULT 0,       -- сколько сообщений уже ушло
    next_at TEXT,
    status TEXT NOT NULL DEFAULT 'active', -- active | done | stopped
    stop_reason TEXT NOT NULL DEFAULT '',
    UNIQUE(chain, student_id, period)
);
"""


def init(con):
    con.executescript(SCHEMA)


def _iso(d: datetime) -> str:
    return d.isoformat(timespec="minutes")


def config(con) -> dict:
    out = {}
    for k, c in CHAINS.items():
        v = {}
        raw = comms.get(con, f"chain_{k}")
        if raw:
            try:
                v = json.loads(raw)
            except ValueError:
                v = {}
        steps = v.get("steps") or c["steps"]
        out[k] = {"title": c["title"], "when": c["when"], "goal": c["goal"], "enabled": bool(v.get("enabled")),
                  "steps": [{"days": int(s.get("days", 0)), "text": str(s.get("text", ""))} for s in steps]}
    return out


def save_config(con, data: dict):
    for k in CHAINS:
        if k not in data:
            continue
        d = data[k]
        steps = [{"days": max(0, min(60, int(s.get("days") or 0))), "text": str(s.get("text") or "").strip()[:1000]}
                 for s in (d.get("steps") or []) if str(s.get("text") or "").strip()][:6]
        comms.put(con, f"chain_{k}", json.dumps({"enabled": bool(d.get("enabled")), "steps": steps}, ensure_ascii=False))


def _render(con, text: str, s: dict, period: str) -> str:
    child = s["name"].split()[0] if "заявка" not in s["name"].lower() else "ребёнка"
    g = con.execute("""SELECT g.name FROM enrollments e JOIN groups g ON g.id=e.group_id WHERE e.student_id=? AND e.active=1
                       ORDER BY e.id LIMIT 1""", (s["id"],)).fetchone() or \
        (con.execute("SELECT name FROM groups WHERE id=?", (s["group_id"],)).fetchone() if s.get("group_id") else None)
    month = logic.month_label(period) if len(period) == 7 else ""
    return (text.replace("{child}", child).replace("{group}", g[0] if g else "")
            .replace("{month}", month.split()[0].lower() if month else "следующий месяц"))


def _start_base(chain: str, period: str) -> datetime:
    if chain == "after_trial":
        return datetime.fromisoformat(period).replace(hour=0, minute=0)
    y, m = map(int, period.split("-"))
    prev = logic.shift_month(period, -1)
    py, pm = map(int, prev.split("-"))
    return datetime(py, pm, 20)


def _slot(base: datetime, days: int, now: datetime) -> datetime:
    t = (base + timedelta(days=days)).replace(hour=HOURS[0], minute=0)
    return max(t, now) if t.date() <= now.date() else t


def _candidates(con, chain: str, now: datetime):
    if chain == "after_trial":
        for s in con.execute("""SELECT * FROM students WHERE funnel='sales' AND archived=0 AND trial_came=1
                                AND stage NOT IN ('bought','refused') AND trial_at >= ?""",
                             (_iso(now - timedelta(days=14)),)):
            yield dict(s), s["trial_at"]
    elif chain == "renewal" and now.day >= 20:
        nxt = logic.shift_month(logic.month_of(now.date()), 1)
        for s in con.execute("SELECT * FROM students WHERE funnel='members' AND archived=0 AND stage != 'left'"):
            if not con.execute("SELECT 1 FROM enrollments WHERE student_id=? AND active=1", (s["id"],)).fetchone():
                continue
            if _paid(con, s["id"], nxt):
                continue
            yield dict(s), nxt


def _paid(con, sid: int, month: str) -> bool:
    subs = con.execute("SELECT status FROM subscriptions WHERE student_id=? AND month=? AND status != 'cancelled'", (sid, month)).fetchall()
    return bool(subs) and all(r[0] == "paid" for r in subs)


def _goal_reached(con, run, s) -> str:
    if not s or s["archived"]:
        return "клиент в архиве"
    if run["chain"] == "after_trial":
        if s["funnel"] == "members" or s["stage"] == "bought":
            return "купил абонемент"
        if s["stage"] == "refused":
            return "отказ"
    if run["chain"] == "renewal":
        if s["stage"] == "left":
            return "ушёл"
        if _paid(con, s["id"], run["period"]):
            return f"оплатил {logic.month_label(run['period'])}"
    return ""


def _replied(con, family_id: int, since: str) -> bool:
    return bool(con.execute("""SELECT 1 FROM messages m JOIN conversations c ON c.id=m.conversation_id
                               WHERE c.family_id=? AND m.direction='in' AND m.ts > ? LIMIT 1""", (family_id, since)).fetchone())


def _stop(con, run_id: int, reason: str, status: str = "stopped"):
    con.execute("UPDATE chain_runs SET status=?, stop_reason=?, next_at=NULL WHERE id=?", (status, reason, run_id))


def tick(con, now: datetime | None = None, send: bool = True) -> dict:
    from . import tasks
    from .services import log
    now = (now or datetime.now()).replace(second=0, microsecond=0)
    cfg = config(con)
    started = sent = 0
    for k, c in cfg.items():
        if not c["enabled"] or not c["steps"]:
            continue
        for s, period in _candidates(con, k, now):
            base = _start_base(k, period)
            cur = con.execute("INSERT OR IGNORE INTO chain_runs(chain, student_id, period, started_at, next_at) VALUES (?,?,?,?,?)",
                              (k, s["id"], period, _iso(now), _iso(_slot(base, c["steps"][0]["days"], now))))
            if cur.rowcount:
                started += 1
                log(con, s["id"], f"Началась автоцепочка «{c['title']}»", None, "system")
    for run in [dict(r) for r in con.execute("SELECT * FROM chain_runs WHERE status='active'")]:
        c = cfg.get(run["chain"])
        s = con.execute("SELECT * FROM students WHERE id=?", (run["student_id"],)).fetchone()
        s = dict(s) if s else None
        why = _goal_reached(con, run, s)
        if why:
            _stop(con, run["id"], why, "done")
            continue
        if not c or not c["enabled"]:
            continue  # выключили цепочку: ждём, вдруг включат обратно
        if _replied(con, s["family_id"], run["started_at"]):
            _stop(con, run["id"], "родитель ответил")
            tasks.add_task(con, "manual", s["id"], now, title="Родитель ответил на автосообщение: ответить", source="chain",
                           auto_key=f"chain_reply:{run['id']}")
            log(con, s["id"], f"Автоцепочка «{c['title']}» остановлена: родитель ответил", None, "system")
            continue
        if run["step"] >= len(c["steps"]):
            _stop(con, run["id"], "все сообщения отправлены", "done")
            continue
        if not run["next_at"] or now < datetime.fromisoformat(run["next_at"]) or not (HOURS[0] <= now.hour < HOURS[1]) or not send:
            continue
        text = _render(con, c["steps"][run["step"]]["text"], s, run["period"])
        cid = tasks.best_conversation(con, s["family_id"])
        if not cid:
            _stop(con, run["id"], "у семьи нет переписки в мессенджере")
            tasks.add_task(con, "manual", s["id"], now, title="Автоцепочка не смогла написать: напишите родителю сами", source="chain",
                           auto_key=f"chain_nochat:{run['id']}", note=text)
            continue
        try:
            comms.send(con, cid, text, {"name": "Автоцепочка"})
        except comms.CommsError as e:
            _stop(con, run["id"], f"не отправилось: {e}")
            tasks.add_task(con, "manual", s["id"], now, title="Автоцепочка не смогла написать: напишите родителю сами", source="chain",
                           auto_key=f"chain_err:{run['id']}", note=text)
            continue
        sent += 1
        step = run["step"] + 1
        log(con, s["id"], f"Автоцепочка «{c['title']}», сообщение {step} из {len(c['steps'])}: «{text[:80]}»", None, "message")
        if step >= len(c["steps"]):
            con.execute("UPDATE chain_runs SET step=?, status='done', stop_reason='все сообщения отправлены', next_at=NULL WHERE id=?",
                        (step, run["id"]))
        else:
            nxt = _slot(_start_base(run["chain"], run["period"]), c["steps"][step]["days"], now)
            con.execute("UPDATE chain_runs SET step=?, next_at=? WHERE id=?", (step, _iso(max(nxt, now + timedelta(hours=1))), run["id"]))
    return {"started": started, "sent": sent}


def for_student(con, sid: int) -> list[dict]:
    cfg = config(con)
    return [{"id": r["id"], "title": cfg.get(r["chain"], {}).get("title", r["chain"]), "step": r["step"],
             "total": len(cfg.get(r["chain"], {}).get("steps", [])), "next_at": r["next_at"], "enabled": cfg.get(r["chain"], {}).get("enabled")}
            for r in con.execute("SELECT * FROM chain_runs WHERE student_id=? AND status='active'", (sid,))]


def stop_by_user(con, run_id: int, user: dict):
    r = con.execute("SELECT student_id FROM chain_runs WHERE id=? AND status='active'", (run_id,)).fetchone()
    if not r:
        return
    _stop(con, run_id, f"остановил {user.get('name', '')}")
    from .services import log
    log(con, r[0], "Автоцепочка остановлена вручную", user, "system")


def stats(con) -> dict:
    out = {}
    for k in CHAINS:
        row = con.execute("""SELECT SUM(status='active'), SUM(status='done' AND stop_reason NOT IN ('все сообщения отправлены')),
                             SUM(stop_reason='родитель ответил'), COUNT(*) FROM chain_runs WHERE chain=?""", (k,)).fetchone()
        out[k] = {"active": row[0] or 0, "goal": row[1] or 0, "replied": row[2] or 0, "total": row[3] or 0}
    return out
