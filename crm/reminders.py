"""Напоминание родителям перед каждым занятием (Юлия, 04.11): для тех, кто ходит по абонементу, на разовые и отработки.
У пробных своя цепочка (tasks.py), их здесь нет. Одно сообщение на семью на тренировку: если в группе двое детей
из одной семьи, родителю придёт одно. Пишем туда, где семья уже переписывалась со школой. Пока руководитель не
запустил автосообщения (comms.auto_live), ничего не уходит. Не пишем тем, кто предупредил, что не придёт, в отпуске,
не беспокоить или без оплаты (их нет в журнале).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from . import comms

DEFAULT_TEXT = ("Здравствуйте{parent}!\nНапоминаем о сегодняшнем занятии в «Футболике» ⚽\n\n"
                "📅 Дата: {date}\n⏰ Время начала: {time}\n📍 {place}\n\n"
                "Если планы изменились, пожалуйста, предупредите заранее. В случае плохого самочувствия рекомендуем остаться дома 💊\n\n"
                "До встречи! ⚽")


def due_lessons(con, now: datetime, hours: int) -> list[dict]:
    """Тренировки сегодня, до начала которых осталось не больше hours часов (и они ещё не начались)."""
    from . import closures, logic, schedule
    d = now.date()
    out = []
    for g in con.execute("SELECT g.*, b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id WHERE g.active=1").fetchall():
        gv = schedule.on(con, g, d)
        if d.weekday() not in logic.parse_weekdays(gv["weekdays"] or "") or closures.is_closed(con, g["id"], d):
            continue
        hm = logic.group_time(gv, d.weekday())
        try:
            start = datetime.combine(d, datetime.strptime(hm, "%H:%M").time())
        except ValueError:
            continue
        if start - timedelta(hours=hours) <= now < start:
            out.append({"group": g, "start": start})
    return out


def recipients(con, g, day: str) -> list[dict]:
    """Кого ждём: абонемент, разовые, отработки. Без пробных, предупредивших, отпуска и неоплативших."""
    from . import services as sv
    j = sv.journal(con, g["id"], day, paid_only=True)
    out = []
    for r in j["rows"]:
        if r.get("status") in ("trial", "vacation") or r.get("warned"):
            continue
        if g["is_makeup"] or r.get("status") == "makeup" or "makeup_id" in r:
            if (r.get("mk_status") or r.get("status")) == "cancelled":
                continue
            out.append({"id": r["id"], "kind": "makeup"})
        elif r.get("tag") in ("paid", "single"):
            out.append({"id": r["id"], "kind": r["tag"]})
    return out


def text_for(con, g, start: datetime, family_id: int) -> str:
    from . import tasks
    p = con.execute("SELECT name FROM parents WHERE family_id=? AND name!='' ORDER BY id LIMIT 1", (family_id,)).fetchone()
    first = (p[0].split()[0] if p and p[0] else "")
    place = tasks.booked_places(con).get(str(g["branch_id"]), {}).get("place") or g["branch"]
    tpl = tasks.setting(con, "msg_lesson") or DEFAULT_TEXT
    return (tpl.replace("{parent}", f", {first}" if first else "").replace("{date}", start.strftime("%d.%m.%Y"))
            .replace("{time}", start.strftime("%H:%M")).replace("{place}", place))


def tick(con, now: datetime | None = None, send: bool = True) -> int:
    from . import tasks
    now = (now or datetime.now()).replace(second=0, microsecond=0)
    if not send or not comms.auto_live(con) or tasks.setting(con, "auto_lesson") != "1":
        return 0
    try:
        hours = max(1, min(12, int(tasks.setting(con, "lesson_hours_before") or 3)))
    except ValueError:
        hours = 3
    sent = 0
    for les in due_lessons(con, now, hours):
        g, start = les["group"], les["start"]
        stamp = start.isoformat(timespec="minutes")
        for r in recipients(con, g, start.date().isoformat()):
            s = con.execute("SELECT id, family_id, dnd, archived FROM students WHERE id=?", (r["id"],)).fetchone()
            if not s or s["dnd"] or s["archived"]:
                continue
            key = f"{stamp}#fam{s['family_id']}"  # одно на семью на тренировку
            if con.execute("SELECT 1 FROM auto_messages WHERE kind='lesson' AND trial_at=?", (key,)).fetchone():
                continue
            cid = tasks.best_conversation(con, s["family_id"])
            text = text_for(con, g, start, s["family_id"])
            ok, err, channel = True, "", ""
            if not cid:
                ok, err = False, "у семьи нет переписки в мессенджере"
            else:
                channel = con.execute("SELECT channel FROM conversations WHERE id=?", (cid,)).fetchone()[0]
                try:
                    comms.send(con, cid, text, {"name": "Автосообщение"})
                except comms.CommsError as e:
                    ok, err = False, str(e)
            con.execute("INSERT OR IGNORE INTO auto_messages(student_id, kind, trial_at, ts, ok, channel, error) VALUES (?,?,?,?,?,?,?)",
                        (s["id"], "lesson", key, now.isoformat(timespec="minutes"), 1 if ok else 0, channel, err))
            con.execute("INSERT INTO history(student_id, ts, user_name, kind, text) VALUES (?,?,?,?,?)",
                        (s["id"], now.isoformat(timespec="minutes"), "Автосообщение", "message",
                         f"Напоминание о занятии {start:%d.%m %H:%M}: " + ("ушло" if ok else f"не ушло, {err}")))
            sent += ok
    return sent
