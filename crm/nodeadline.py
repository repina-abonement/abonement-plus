"""«Воронки без дедлайнов» (Юлия, 03.11 и 04.11): клиенты, которым звонить не по сроку, а по поводу.
Первая воронка «Ждут акцию»: ушли или отказались из-за цены. Задач по сроку нет: когда появляется акция,
руководитель одной кнопкой ставит задачу «Предложить акцию» всем в воронке.

Как попадают: сами при «Ушёл» с причиной «Дорого» и при отказе после пробного «Дорого»; вручную из карточки.
Как выходят: сами, когда клиент оплатил абонемент или снова записался на пробное; вручную кнопкой «Убрать».
"""
from __future__ import annotations

from datetime import datetime

FUNNELS = [("promo", "Ждут акцию")]
LABELS = dict(FUNNELS)

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodeadline (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    funnel TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL,
    added_by TEXT NOT NULL DEFAULT '',
    removed_at TEXT,
    removed_why TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS nodeadline_open ON nodeadline(student_id) WHERE removed_at IS NULL;
"""


def init(con) -> None:
    con.executescript(SCHEMA)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def add(con, sid: int, funnel: str, note: str, user: dict | None) -> None:
    from . import services as sv
    if funnel not in LABELS:
        raise sv.ApiError("Нет такой воронки")
    if con.execute("SELECT 1 FROM nodeadline WHERE student_id=? AND funnel=? AND removed_at IS NULL", (sid, funnel)).fetchone():
        return
    con.execute("INSERT INTO nodeadline(student_id, funnel, note, added_at, added_by) VALUES (?,?,?,?,?)",
                (sid, funnel, (note or "")[:300], _now(), (user or {}).get("name", "CRM")))
    sv.log(con, sid, f"В воронку без дедлайнов «{LABELS[funnel]}»" + (f": {note}" if note else ""), user, "stage")


def remove(con, sid: int, why: str, user: dict | None) -> None:
    from . import services as sv
    rows = con.execute("SELECT id, funnel FROM nodeadline WHERE student_id=? AND removed_at IS NULL", (sid,)).fetchall()
    for r in rows:
        con.execute("UPDATE nodeadline SET removed_at=?, removed_why=? WHERE id=?", (_now(), why, r["id"]))
        sv.log(con, sid, f"Убран из воронки «{LABELS.get(r['funnel'], r['funnel'])}»: {why}", user, "stage")


def of_student(con, sid: int) -> list[dict]:
    return [{"funnel": r["funnel"], "title": LABELS.get(r["funnel"], r["funnel"]), "note": r["note"], "added_at": r["added_at"]}
            for r in con.execute("SELECT * FROM nodeadline WHERE student_id=? AND removed_at IS NULL", (sid,))]


def auto_exit(con, sid: int, why: str) -> None:
    """Оплатил или снова записался на пробное: из воронки без дедлайнов выходит сам."""
    if con.execute("SELECT 1 FROM nodeadline WHERE student_id=? AND removed_at IS NULL", (sid,)).fetchone():
        remove(con, sid, why, None)


def board(con) -> dict:
    out = []
    for key, title in FUNNELS:
        cards = []
        for r in con.execute("""SELECT n.note, n.added_at, s.id, s.name, s.funnel, s.stage, f.id AS family_id,
                                       (SELECT p.name FROM parents p WHERE p.family_id=s.family_id ORDER BY p.id LIMIT 1) AS parent,
                                       (SELECT p.phone FROM parents p WHERE p.family_id=s.family_id ORDER BY p.id LIMIT 1) AS phone,
                                       (SELECT 1 FROM tasks t WHERE t.student_id=s.id AND t.status='open' AND t.auto_key LIKE 'nd:%') AS has_task
                                FROM nodeadline n JOIN students s ON s.id=n.student_id JOIN families f ON f.id=s.family_id
                                WHERE n.funnel=? AND n.removed_at IS NULL AND s.archived=0 ORDER BY n.added_at DESC""", (key,)):
            cards.append({"id": r["id"], "name": r["name"], "parent": r["parent"] or "", "phone": r["phone"] or "", "note": r["note"],
                          "since": r["added_at"][:10], "was": "ушёл" if r["funnel"] == "members" else "после пробного",
                          "has_task": bool(r["has_task"])})
        out.append({"key": key, "title": title, "cards": cards})
    return {"funnels": out}


def make_tasks(con, funnel: str, title: str, due: str, note: str, user: dict) -> int:
    """Появилась акция: задача всем в воронке (одна на ребёнка; у кого уже есть такая открытая, не дублируем)."""
    from . import services as sv, tasks
    if funnel not in LABELS:
        raise sv.ApiError("Нет такой воронки")
    title = (title or "").strip() or "Предложить акцию"
    try:
        datetime.fromisoformat(due)
    except (TypeError, ValueError):
        raise sv.ApiError("Выберите дату задачи")
    made = 0
    for (sid,) in con.execute("""SELECT n.student_id FROM nodeadline n JOIN students s ON s.id=n.student_id
                                 WHERE n.funnel=? AND n.removed_at IS NULL AND s.archived=0""", (funnel,)).fetchall():
        made += bool(tasks.add_task(con, "manual", sid, due, auto_key=f"nd:{funnel}:{sid}:{due[:10]}", title=title,
                                    source="manual", note=note or ""))
    return made
