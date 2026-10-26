"""Задачи сотрудников и автосообщения родителям вокруг пробного занятия.

Задачи по пробным создаются сами (по дате пробного) и закрываются, когда сотрудник нажал «Готово»
или клиент ушёл дальше по воронке. Ответственный за каждый вид задач задаётся в Настройках.
Те же виды задач используются при переносе из ERP (см. ERP_STATUSES).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from . import comms

KINDS = {
    "trial_confirm": ("Напомнить о пробном и подтвердить", "📅"),
    "trial_today": ("Напоминание о сегодняшнем пробном", "⏰"),
    "how_was": ("Как прошло пробное", "💬"),
    "no_show": ("Не пришли на пробное", "🚫"),
    "not_marked": ("Тренер не отметил клиента на пробном", "📝"),
    "why_not_bought": ("Почему не купили", "🤔"),
    "manual": ("Задача", "✅"),
}
# статусы задач в ERP Футболики → виды задач здесь. При переносе все они встают на ответственного за пробные
ERP_STATUSES = {
    "Напоминание о пробном занятии": "trial_confirm",
    "Как прошло пробное": "how_was",
    "Не пришли на ПУ": "no_show",
    "Почему не купили": "why_not_bought",
    "Напоминание о сегодняшнем ПУ": "trial_today",
    "Клиент не отмечен на ПУ": "not_marked",
}
OWNER_KEY = "task_owner_trials"  # один ответственный за все задачи по пробным

DEFAULT_TEMPLATES = {
    "msg_day_before": ("Здравствуйте! Напоминаем: завтра, {date}, в {time} пробное занятие по футболу, {place}.\n"
                       "Ребёнок: {child}. Подтвердите, пожалуйста, что придёте 🙌"),
    "msg_2h_before": ("Сегодня в {time} пробное занятие, ждём вас 🙂\n"
                      "Возьмите удобную спортивную форму, сменную обувь и воду. Адрес: {place}"),
    "msg_after": "Здравствуйте! Как прошло пробное занятие? Поделитесь, пожалуйста, впечатлениями, нам важно ваше мнение 🙂",
}
AUTO_SETTINGS = {"auto_day_before": "1", "auto_2h_before": "1", "auto_after": "1", "day_before_hour": "11"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
    assignee_id INTEGER REFERENCES users(id),
    due_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',      -- open | done | cancelled
    note TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'auto',      -- auto | erp | manual
    auto_key TEXT UNIQUE,                     -- чтобы одна и та же задача не создавалась дважды
    created_at TEXT NOT NULL,
    done_at TEXT,
    done_by TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_tasks_open ON tasks(status, assignee_id, due_at);

CREATE TABLE IF NOT EXISTS auto_messages (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    trial_at TEXT NOT NULL,
    ts TEXT NOT NULL,
    ok INTEGER NOT NULL DEFAULT 1,
    channel TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    UNIQUE(student_id, kind, trial_at)
);
"""


def init(con):
    con.executescript(SCHEMA)


def setting(con, key: str) -> str:
    v = comms.get(con, key)
    if v:
        return v
    return DEFAULT_TEMPLATES.get(key) or AUTO_SETTINGS.get(key, "")


def owner(con) -> int | None:
    """Ответственный за пробные: из настроек, иначе первый оператор, иначе администратор."""
    v = comms.get(con, OWNER_KEY)
    if v and con.execute("SELECT 1 FROM users WHERE id=? AND active=1", (int(v),)).fetchone():
        return int(v)
    r = con.execute("SELECT id FROM users WHERE active=1 AND role='operator' ORDER BY id LIMIT 1").fetchone() or \
        con.execute("SELECT id FROM users WHERE active=1 AND role='admin' ORDER BY id LIMIT 1").fetchone()
    return r[0] if r else None


def _now() -> datetime:
    return datetime.now().replace(second=0, microsecond=0)


def _iso(d: datetime) -> str:
    return d.isoformat(timespec="minutes")


def add_task(con, kind: str, student_id: int | None, due: datetime | str, *, auto_key: str | None = None,
             title: str = "", source: str = "auto", assignee: int | None = None, note: str = "") -> int | None:
    due_s = due if isinstance(due, str) else _iso(due)
    cur = con.execute("""INSERT OR IGNORE INTO tasks(kind, title, student_id, assignee_id, due_at, source, auto_key, note,
                         created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                      (kind, title or KINDS.get(kind, ("Задача",))[0], student_id, assignee or owner(con), due_s, source,
                       auto_key, note, _iso(_now())))
    return cur.lastrowid if cur.rowcount else None


def import_erp_task(con, erp_status: str, student_id: int, due: str, note: str = "") -> int | None:
    """Для переноса из ERP: задача со статусом ERP встаёт на ответственного за пробные."""
    kind = ERP_STATUSES.get(erp_status.strip(), "manual")
    return add_task(con, kind, student_id, due, source="erp", note=note,
                    title=KINDS[kind][0] if kind != "manual" else erp_status,
                    auto_key=f"erp:{erp_status}:{student_id}:{due}")


# ---------------- автоматика: задачи и сообщения ----------------

def _place(con, s: dict) -> str:
    if not s.get("group_id"):
        return "в школе"
    g = con.execute("SELECT g.name, b.name FROM groups g JOIN branches b ON b.id=g.branch_id WHERE g.id=?",
                    (s["group_id"],)).fetchone()
    return f"{g[1]}" if g else "в школе"


def render_template(con, key: str, s: dict) -> str:
    t = datetime.fromisoformat(s["trial_at"])
    child = s["name"] if "заявка" not in s["name"].lower() else "ребёнка"
    return (setting(con, key).replace("{child}", child.split()[0]).replace("{time}", t.strftime("%H:%M"))
            .replace("{date}", f"{t.day:02d}.{t.month:02d}").replace("{place}", _place(con, s)))


def best_conversation(con, family_id: int) -> int | None:
    """Куда писать семье: последняя живая переписка, иначе новый WhatsApp через Wazzup."""
    r = con.execute("""SELECT id FROM conversations WHERE family_id=? AND external_id NOT LIKE 'demo-%'
                       ORDER BY last_at IS NULL, last_at DESC LIMIT 1""", (family_id,)).fetchone()
    if r:
        return r[0]
    if comms.status(con)["whatsapp"]:
        p = con.execute("SELECT phone FROM parents WHERE family_id=? AND phone != '' ORDER BY id LIMIT 1", (family_id,)).fetchone()
        if p:
            cid = comms.start_whatsapp(con, p[0])
            comms.link(con, cid, family_id)
            return cid
    return None


def send_auto(con, s: dict, kind: str, tpl: str) -> dict:
    """Отправить автосообщение один раз за пробное. Возвращает, что получилось."""
    if con.execute("SELECT 1 FROM auto_messages WHERE student_id=? AND kind=? AND trial_at=?",
                   (s["id"], kind, s["trial_at"])).fetchone():
        return {"skipped": True}
    text = render_template(con, tpl, s)
    cid = best_conversation(con, s["family_id"])
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
                (s["id"], kind, s["trial_at"], _iso(_now()), 1 if ok else 0, channel, err))
    labels = {"day_before": "за день", "2h_before": "за 2 часа", "after": "после пробного"}
    con.execute("INSERT INTO history(student_id, ts, user_name, kind, text) VALUES (?,?,?,?,?)",
                (s["id"], _iso(_now()), "Автосообщение", "message",
                 (f"Ушло сообщение {labels.get(kind, '')}: «{text[:80]}…»" if ok else
                  f"Не ушло сообщение {labels.get(kind, '')}: {err}. Напишите вручную")))
    return {"ok": ok, "error": err}


def run(con, now: datetime | None = None, send: bool = True) -> dict:
    """Один проход: создать задачи по пробным, отправить автосообщения, закрыть неактуальные задачи."""
    now = now or _now()
    st = {k: setting(con, k) for k in AUTO_SETTINGS}
    trial_min = int((con.execute("SELECT value FROM settings WHERE key='trial_minutes'").fetchone() or ["60"])[0])
    made = sent = 0
    rows = [dict(r) for r in con.execute(
        """SELECT * FROM students WHERE funnel='sales' AND trial_at IS NOT NULL AND archived=0
           AND stage NOT IN ('bought','refused') AND trial_at >= ?""", (_iso(now - timedelta(days=3)),))]
    for s in rows:
        t = datetime.fromisoformat(s["trial_at"])
        end = t + timedelta(minutes=trial_min)
        key = f"{s['id']}:{s['trial_at']}"
        day_before = datetime.combine(t.date() - timedelta(days=1), datetime.min.time()) + timedelta(hours=int(st["day_before_hour"]))
        # задачи
        if t.date() > now.date():
            made += bool(add_task(con, "trial_confirm", s["id"], min(day_before, t - timedelta(hours=3)),
                                  auto_key=f"trial_confirm:{key}"))
        elif t.date() == now.date() and now < t and s["created_at"][:10] == now.date().isoformat():
            made += bool(add_task(con, "trial_today", s["id"], now, auto_key=f"trial_today:{key}"))
        if now >= end + timedelta(minutes=10):
            if s["trial_came"] == 0:
                made += bool(add_task(con, "no_show", s["id"], end, auto_key=f"no_show:{key}"))
            else:
                made += bool(add_task(con, "how_was", s["id"], end + timedelta(minutes=10), auto_key=f"how_was:{key}"))
        if s["trial_came"] is None and now >= end + timedelta(minutes=60):
            made += bool(add_task(con, "not_marked", s["id"], end + timedelta(minutes=60), auto_key=f"not_marked:{key}"))
        # сообщения
        if send and st["auto_day_before"] == "1" and now >= day_before and now < t - timedelta(hours=3) and t.date() > now.date():
            sent += bool(send_auto(con, s, "day_before", "msg_day_before").get("ok"))
        if send and st["auto_2h_before"] == "1" and t - timedelta(hours=2) <= now < t:
            sent += bool(send_auto(con, s, "2h_before", "msg_2h_before").get("ok"))
        if send and st["auto_after"] == "1" and s["trial_came"] != 0 and end + timedelta(minutes=10) <= now < end + timedelta(hours=3):
            sent += bool(send_auto(con, s, "after", "msg_after").get("ok"))
    # пробное перенесли или клиент купил/отказался — старые автозадачи по нему больше не нужны
    for tid, akey, sid in con.execute("""SELECT t.id, t.auto_key, t.student_id FROM tasks t WHERE t.status='open'
                                         AND t.source='auto' AND t.auto_key IS NOT NULL""").fetchall():
        s = con.execute("SELECT trial_at, stage, funnel, trial_came FROM students WHERE id=?", (sid,)).fetchone()
        kind, rest = akey.split(":", 1)
        stale = (not s or s["trial_at"] is None or not rest.endswith(":" + s["trial_at"])
                 or s["funnel"] != "sales" or s["stage"] in ("bought", "refused")
                 or (kind == "not_marked" and s["trial_came"] is not None)
                 or (kind == "how_was" and s["trial_came"] == 0))
        if stale:
            con.execute("UPDATE tasks SET status='cancelled', done_at=? WHERE id=?", (_iso(now), tid))
    return {"tasks": made, "sent": sent}


# ---------------- для интерфейса ----------------

def _task_out(con, r) -> dict:
    d = dict(r)
    d["icon"] = KINDS.get(d["kind"], ("", "✅"))[1]
    if d["student_id"]:
        s = con.execute("""SELECT s.id, s.name, s.trial_at, s.trial_came, s.stage, s.family_id, f.name AS family,
                           (SELECT p.phone FROM parents p WHERE p.family_id=s.family_id ORDER BY p.id LIMIT 1) AS phone,
                           (SELECT p.name FROM parents p WHERE p.family_id=s.family_id ORDER BY p.id LIMIT 1) AS parent
                           FROM students s JOIN families f ON f.id=s.family_id WHERE s.id=?""", (d["student_id"],)).fetchone()
        d["student"] = dict(s) if s else None
        if s and s["trial_at"]:
            d["auto"] = [dict(x) for x in con.execute(
                "SELECT kind, ts, ok, channel, error FROM auto_messages WHERE student_id=? AND trial_at=? ORDER BY ts",
                (s["id"], s["trial_at"]))]
    a = con.execute("SELECT name FROM users WHERE id=?", (d["assignee_id"],)).fetchone() if d["assignee_id"] else None
    d["assignee"] = a[0] if a else ""
    return d


def my_tasks(con, user: dict, scope: str = "mine") -> dict:
    run(con, send=False)  # задачи свежие сразу; сообщения отправляет фоновый процесс
    now = _now()
    end_today = datetime.combine(date.today(), datetime.max.time()).isoformat(timespec="minutes")
    end_tomorrow = (datetime.combine(date.today() + timedelta(days=1), datetime.max.time())).isoformat(timespec="minutes")
    where, args = "status='open' AND due_at <= ?", [end_tomorrow]
    if scope == "mine" or user["role"] != "admin":
        where += " AND assignee_id=?"
        args.append(user["id"])
    rows = [_task_out(con, r) for r in con.execute(f"SELECT * FROM tasks WHERE {where} ORDER BY due_at", args)]
    groups = {"overdue": [], "today": [], "tomorrow": []}
    for t in rows:
        if t["due_at"] < _iso(now - timedelta(minutes=30)) and t["due_at"][:10] < date.today().isoformat():
            groups["overdue"].append(t)
        elif t["due_at"] <= end_today:
            groups["today"].append(t)
        else:
            groups["tomorrow"].append(t)
    done_today = con.execute(f"""SELECT COUNT(*) FROM tasks WHERE status='done' AND done_at >= ?
                                 {"AND assignee_id=?" if scope == "mine" or user["role"] != "admin" else ""}""",
                             (date.today().isoformat(), *([user["id"]] if scope == "mine" or user["role"] != "admin" else []))).fetchone()[0]
    team = []
    if user["role"] == "admin":
        for u in con.execute("SELECT id, name FROM users WHERE active=1 AND role IN ('admin','operator') ORDER BY id"):
            c = con.execute("""SELECT SUM(due_at <= ?), SUM(due_at < ?) FROM tasks WHERE status='open' AND assignee_id=?""",
                            (end_today, date.today().isoformat(), u["id"])).fetchone()
            team.append({"id": u["id"], "name": u["name"], "open": c[0] or 0, "overdue": c[1] or 0})
    return {"groups": groups, "done_today": done_today, "team": team, "scope": scope}


def complete(con, tid: int, user: dict, result: str = "") -> None:
    t = con.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    if not t:
        raise comms.CommsError("Задача не найдена")
    con.execute("UPDATE tasks SET status='done', done_at=?, done_by=?, result=? WHERE id=?",
                (_iso(_now()), user.get("name", ""), result.strip(), tid))
    if t["student_id"]:
        con.execute("INSERT INTO history(student_id, ts, user_name, kind, text) VALUES (?,?,?,?,?)",
                    (t["student_id"], _iso(_now()), user.get("name", ""), "task",
                     f"Задача «{t['title']}» выполнена" + (f": {result.strip()}" if result.strip() else "")))
        con.execute("UPDATE students SET priority=0 WHERE id=?", (t["student_id"],))
        if t["kind"] in ("how_was", "no_show"):  # связались после пробного — карточка больше не горит
            con.execute("UPDATE students SET contacted_after_trial=1 WHERE id=?", (t["student_id"],))


def settings_view(con) -> dict:
    vals = {k: setting(con, k) for k in list(DEFAULT_TEMPLATES) + list(AUTO_SETTINGS)}
    vals[OWNER_KEY] = str(owner(con) or "")
    return {"values": vals, "erp_statuses": list(ERP_STATUSES)}


def save_settings(con, data: dict):
    for k, v in data.items():
        if k in DEFAULT_TEMPLATES or k in AUTO_SETTINGS or k == OWNER_KEY:
            comms.put(con, k, str(v or "").strip())


def preview(con, s_id: int) -> dict:
    s = dict(con.execute("SELECT * FROM students WHERE id=?", (s_id,)).fetchone())
    return {k: render_template(con, k, s) for k in DEFAULT_TEMPLATES}


def start_worker(connect):
    import threading
    import time
    import traceback

    def loop():
        con = connect()
        while True:
            try:
                con.execute("BEGIN IMMEDIATE")
                try:
                    run(con)
                    con.execute("COMMIT")
                except BaseException:
                    con.execute("ROLLBACK")
                    raise
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(60)
    threading.Thread(target=loop, name="tasks-worker", daemon=True).start()


def seed_demo(con):
    """Тестовый режим: оператор Ольга (тест), на неё встают задачи по пробным."""
    from .db import hash_password
    r = con.execute("SELECT id FROM users WHERE login='olga'").fetchone()
    uid = r[0] if r else con.execute("INSERT INTO users(login, name, role, pw_hash) VALUES (?,?,?,?)",
                                     ("olga", "Ольга (тест)", "operator", hash_password("olga-test-1"))).lastrowid
    if not comms.get(con, OWNER_KEY):
        comms.put(con, OWNER_KEY, str(uid))
