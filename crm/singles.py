"""Разовые занятия (Юлия, 03.11): клиент ходит не по абонементу, а на отдельные даты.

Статус клиента «Разовые занятия», в должники не попадает. Админ записывает на дату и группу.
Накануне в 10:00 задача: уточнить, придёт ли, отправить ссылку на оплату; CRM предлагает текст сообщения.
Когда задачу закрыли, админ выбирает следующую дату: календарь, «пока не знаем» (спросить через 7 дней)
или ближайший набор (август или январь). Тренер видит ребёнка в журнале и «Встречаю детей» с пометкой «разовое».
Отметка «пришёл / не пришёл» обычная, в журнале посещений.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS single_visits (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    group_id INTEGER NOT NULL REFERENCES groups(id),
    date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'booked',   -- booked | cancelled
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS single_visits_day ON single_visits(group_id, date);
CREATE INDEX IF NOT EXISTS single_visits_kid ON single_visits(student_id, date);
"""
ASK_AGAIN_DAYS = 7
REMIND_HOUR = 10
DEFAULT_TEXT = ("Здравствуйте! Напоминаем: завтра, {date}, в {time} занятие по футболу, {place}. "
                "Подтвердите, пожалуйста, что {child} придёт 🙌\n"
                "Оплатить разовое занятие можно по ссылке: {link}")


def init(con) -> None:
    con.executescript(SCHEMA)


def _now() -> datetime:
    return datetime.now().replace(second=0, microsecond=0)


def lesson_time(con, group_id: int, day: str) -> str:
    from . import logic, schedule
    g = con.execute("SELECT * FROM groups WHERE id=?", (group_id,)).fetchone()
    d = date.fromisoformat(day)
    return logic.group_time(schedule.on(con, g, d), d.weekday()) if g else ""


def is_single(con, sid: int, ym: str | None = None) -> bool:
    """Клиент на разовых в этом месяце: есть разовые записи и нет абонемента на месяц."""
    from . import services as sv
    ym = ym or sv.today().isoformat()[:7]
    if not con.execute("SELECT 1 FROM single_visits WHERE student_id=? AND status='booked' AND substr(date,1,7)=?", (sid, ym)).fetchone():
        return False
    return not con.execute("""SELECT 1 FROM subscriptions WHERE student_id=? AND month=? AND COALESCE(kind,'month')='month'
                              AND status!='cancelled'""", (sid, ym)).fetchone()


def message(con, v) -> str:
    """Текст для родителя накануне: CRM предлагает сама (Юлия, 03.11)."""
    from . import services as sv
    s = con.execute("SELECT name FROM students WHERE id=?", (v["student_id"],)).fetchone()
    g = con.execute("SELECT g.name, b.name AS branch, b.address FROM groups g JOIN branches b ON b.id=g.branch_id WHERE g.id=?",
                    (v["group_id"],)).fetchone()
    tpl = sv.settings(con).get("msg_single") or DEFAULT_TEXT
    place = (g["branch"] + (", " + g["address"] if "address" in g.keys() and g["address"] else "")) if g else ""
    child = (s["name"] if s else "").split(" ")[0]
    return tpl.format(date=f"{v['date'][8:10]}.{v['date'][5:7]}", time=lesson_time(con, v["group_id"], v["date"]),
                      place=place, child=child, link="[ссылка: кнопка «💳 Ссылка на оплату» в карточке]")


def _remind_due(day: str) -> str:
    """Накануне в 10:00; если записали позже, задача сразу."""
    due = datetime.fromisoformat(day) - timedelta(days=1) + timedelta(hours=REMIND_HOUR)
    return max(due, _now()).isoformat(timespec="minutes")


def book(con, sid: int, group_id: int, day: str, user: dict | None, note: str = "") -> int:
    from . import services as sv, tasks
    try:
        d = date.fromisoformat(day)
    except (TypeError, ValueError):
        raise sv.ApiError("Выберите дату")
    if d < sv.today():
        raise sv.ApiError("Дата уже прошла")
    j = sv.journal(con, group_id, day)
    if not j["has_lesson"]:
        raise sv.ApiError("В этот день у группы нет занятия")
    if con.execute("SELECT 1 FROM single_visits WHERE student_id=? AND date=? AND status='booked'", (sid, day)).fetchone():
        raise sv.ApiError("На эту дату уже записан")
    vid = con.execute("INSERT INTO single_visits(student_id, group_id, date, note, created_at, created_by) VALUES (?,?,?,?,?,?)",
                      (sid, group_id, day, (note or "")[:200], _now().isoformat(timespec="minutes"), (user or {}).get("name", ""))).lastrowid
    v = con.execute("SELECT * FROM single_visits WHERE id=?", (vid,)).fetchone()
    tasks.add_task(con, "single_remind", sid, _remind_due(day), auto_key=f"single:{vid}:{day}",
                   title=f"Разовое {day[8:10]}.{day[5:7]}: подтвердить и отправить ссылку на оплату", source="single",
                   note=message(con, v))
    # «пока не знаем» / «в ближайший набор» больше не нужны: дату выбрали
    con.execute("UPDATE tasks SET status='cancelled', done_at=?, result='Записан на разовое' WHERE student_id=? AND kind='single_next' AND status='open'",
                (_now().isoformat(timespec="minutes"), sid))
    g = (con.execute("SELECT name FROM groups WHERE id=?", (group_id,)).fetchone() or [""])[0]
    sv.log(con, sid, f"Записан на разовое занятие {d:%d.%m.%Y}, {g}", user, "stage")
    return vid


def cancel(con, vid: int, user: dict | None) -> None:
    from . import services as sv
    v = con.execute("SELECT * FROM single_visits WHERE id=?", (vid,)).fetchone()
    if not v:
        raise sv.ApiError("Запись не найдена", 404)
    con.execute("UPDATE single_visits SET status='cancelled' WHERE id=?", (vid,))
    con.execute("UPDATE tasks SET status='cancelled', done_at=?, result='Разовое отменено' WHERE auto_key=? AND status='open'",
                (_now().isoformat(timespec="minutes"), f"single:{vid}:{v['date']}"))
    sv.log(con, v["student_id"], f"Отменено разовое {v['date'][8:10]}.{v['date'][5:7]}", user, "stage")


def next_step(con, sid: int, how: str, user: dict | None, group_id: int | None = None, day: str = "") -> dict:
    """После задачи накануне: следующая дата (date), «пока не знаем» (unknown, спросить через 7 дней) или ближайший набор (intake)."""
    from . import lifecycle, services as sv, tasks
    if how == "date":
        return {"visit": book(con, sid, int(group_id or 0), day, user)}
    if how == "unknown":
        when = sv.today() + timedelta(days=ASK_AGAIN_DAYS)
        title = "Разовые: договориться о следующем занятии"
    elif how == "intake":
        when = lifecycle.next_intake(sv.today())
        title = "Разовые: пригласить в новый набор"
    else:
        raise sv.ApiError("Выберите, что дальше")
    tasks.add_task(con, "single_next", sid, f"{when.isoformat()}T{REMIND_HOUR:02d}:00", auto_key=f"single_next:{sid}:{when.isoformat()}",
                   title=title, source="single")
    sv.log(con, sid, f"{title}: {when:%d.%m.%Y}", user, "stage")
    return {"due": when.isoformat()}


def of_student(con, sid: int) -> list[dict]:
    from . import services as sv
    t0 = (sv.today() - timedelta(days=60)).isoformat()
    return [dict(r) | {"time": lesson_time(con, r["group_id"], r["date"])} for r in con.execute(
        """SELECT v.id, v.group_id, v.date, v.status, v.note, g.name AS group_name, b.name AS branch,
                  (SELECT a.status FROM attendance a WHERE a.student_id=v.student_id AND a.group_id=v.group_id AND a.date=v.date) AS came
           FROM single_visits v JOIN groups g ON g.id=v.group_id JOIN branches b ON b.id=g.branch_id
           WHERE v.student_id=? AND v.status='booked' AND v.date >= ? ORDER BY v.date DESC""", (sid, t0))]


def rows_for_day(con, group_id: int, day: str) -> list:
    return con.execute("""SELECT v.id AS single_id, s.id, s.name, s.birth FROM single_visits v JOIN students s ON s.id=v.student_id
                          WHERE v.group_id=? AND v.date=? AND v.status='booked' AND s.archived=0 ORDER BY s.name""",
                       (group_id, day)).fetchall()
