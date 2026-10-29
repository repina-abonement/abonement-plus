"""Отчёт тренера о смене: пошагово с телефона, крупными кнопками, пока все шаги не сделаны, не сдать.

Смена = тренировки одного главного тренера в одном филиале за день. Отчёт один на смену: сдаёт главный тренер,
помощник видит тот же отчёт и может заполнять шаги (Юлия, 29.10.2026).
Шаги: 1) дети отмечены во всех группах (CRM проверяет журнал сама); 2) кто не пришёл и почему; 3) пробные: пришли,
итог каждого; 4) пришли, но не записаны; 5) индивы и оплаты на месте; 6) касса (чеки, смена, PayKeeper, терминал);
7) мерч; 8) инвентарь: всё исправно с фото или неисправности с фото (красная плашка старшему администратору и
руководителю); 9) восемь фото филиала; 10) задания старшего администратора на сегодня (могут быть с фото).
Не сдан через 30 минут после последней тренировки: красная плашка у старшего администратора и сообщение в чат тренеров.
Через 2 часа: красная плашка у руководителя и упоминание руководителя в чате. Сданный отчёт уходит текстом в чат тренеров.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta

from . import logic

SCHEMA = """
CREATE TABLE IF NOT EXISTS coach_reports (
    id INTEGER PRIMARY KEY,
    branch_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    main_id INTEGER NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'draft',      -- draft | done
    submitted_at TEXT, submitted_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(branch_id, date, main_id)
);
CREATE TABLE IF NOT EXISTS report_photos (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,                        -- report | task
    ref_id INTEGER NOT NULL,
    slot TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS report_tasks (
    id INTEGER PRIMARY KEY,
    coach_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    text TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    done_at TEXT
);
CREATE TABLE IF NOT EXISTS inventory_issues (
    id INTEGER PRIMARY KEY,
    report_id INTEGER NOT NULL,
    branch_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    closed_at TEXT, closed_by TEXT NOT NULL DEFAULT ''
);
"""
PHOTO_SLOTS = [("field", "Поле и инвентарь"), ("stickers", "Уголок с наклейками на поле"), ("sofa", "Диван"),
               ("coach_corner", "Уголок тренеров в раздевалке"), ("reception", "Ресепшен"), ("paykeeper", "PayKeeper"),
               ("shift_closed", "Закрытая смена"), ("goals", "Белые ворота")]
CASH = [("receipts", "Чеки пробил", "Проверь чат за сегодня и вчерашний вечер, пробей непробитые"),
        ("closed", "Смену закрыл", "Кассовую смену, не свою 😉"),
        ("paykeeper", "PayKeeper открыт", "Надпись «нефискализированные платежи отсутствуют»"),
        ("terminal", "Терминал на зарядке", "На ночь всегда, как свой телефон")]
ABSENT = {"sick": "Болеет", "no_answer": "Не дозвонился", "other": "Другое"}
TRIAL = {"bought": "Купили на месте", "think": "Думают", "no": "Не подошло"}
PAY_METHODS = {"card": "Карта", "qr": "QR", "cash": "Наличные"}
LATE_SENIOR, LATE_OWNER = 30, 120  # минут после последней тренировки


class ReportError(Exception):
    pass


def _now() -> datetime:
    return datetime.now().replace(second=0, microsecond=0)


def _iso(d: datetime) -> str:
    return d.isoformat(timespec="minutes")


def init(con):
    con.executescript(SCHEMA)
    # с какого дня ждём отчёты: смены до запуска не считаем несданными
    con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES ('coach_reports_from', ?)", (date.today().isoformat(),))


def photo_path(pid: int) -> str:
    from .profiles import PHOTO_DIR
    return os.path.join(PHOTO_DIR, f"r{int(pid)}.jpg")


# ---------------- смены ----------------

def shifts(con, d: date) -> list[dict]:
    """Смены дня: тренировки по филиалу и главному тренеру (с учётом замен и отметки «кто вёл»)."""
    from . import pu, staff
    out = {}
    for p in staff.planned(con, d):
        ls = staff.lesson_staff(con, p["group_id"], d.isoformat())
        main = ls["main_id"] or p["main"]
        if not main:
            continue
        g = con.execute("SELECT g.*, b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id WHERE g.id=?",
                        (p["group_id"],)).fetchone()
        end = pu._lesson_end(con, g, d)
        s = out.setdefault((g["branch_id"], main), {"branch_id": g["branch_id"], "branch": g["branch"], "main_id": main,
                                                     "assistants": set(), "groups": [], "end": None})
        if ls["assistant_id"] or p["assistant"]:
            s["assistants"].add(ls["assistant_id"] or p["assistant"])
        s["groups"].append({"id": g["id"], "name": g["name"], "time": logic.group_time(g, d.weekday()), "makeup": bool(g["is_makeup"])})
        if end and (not s["end"] or end > s["end"]):
            s["end"] = end
    res = []
    for s in out.values():
        s["groups"].sort(key=lambda x: x["time"] or "")
        s["assistants"] = sorted(s["assistants"])
        s["date"] = d.isoformat()
        res.append(s)
    return sorted(res, key=lambda x: (x["branch_id"], x["end"] or datetime.min))


def my_shifts(con, uid: int, d: date) -> list[dict]:
    return [s for s in shifts(con, d) if s["main_id"] == uid or uid in s["assistants"]]


def _shift(con, branch_id: int, d: date, main_id: int | None = None, uid: int | None = None) -> dict:
    for s in shifts(con, d):
        if s["branch_id"] == branch_id and (main_id is None or s["main_id"] == main_id) and \
                (uid is None or s["main_id"] == uid or uid in s["assistants"]):
            return s
    raise ReportError("Смена не найдена: в этот день у тебя нет тренировок в этом филиале")


def get_or_create(con, branch_id: int, d: date, user: dict, any_coach: bool = False, main_id: int | None = None) -> dict:
    s = _shift(con, branch_id, d, main_id, None if any_coach else user["id"])
    r = con.execute("SELECT * FROM coach_reports WHERE branch_id=? AND date=? AND main_id=?", (branch_id, d.isoformat(), s["main_id"])).fetchone()
    if not r:
        con.execute("INSERT INTO coach_reports(branch_id, date, main_id, created_at) VALUES (?,?,?,?)",
                    (branch_id, d.isoformat(), s["main_id"], _iso(_now())))
        r = con.execute("SELECT * FROM coach_reports WHERE branch_id=? AND date=? AND main_id=?", (branch_id, d.isoformat(), s["main_id"])).fetchone()
    return view(con, dict(r), s)


def _row(con, rid: int) -> dict:
    r = con.execute("SELECT * FROM coach_reports WHERE id=?", (rid,)).fetchone()
    if not r:
        raise ReportError("Отчёт не найден")
    return dict(r)


def check_access(con, rid: int, user: dict, manager: bool) -> tuple[dict, dict]:
    r = _row(con, rid)
    s = _shift(con, r["branch_id"], date.fromisoformat(r["date"]), r["main_id"])
    if not manager and user["id"] != r["main_id"] and user["id"] not in s["assistants"]:
        raise ReportError("Это не твоя смена")
    return r, s


# ---------------- шаги ----------------

def _photos(con, kind: str, ref: int) -> dict[str, list[int]]:
    out = {}
    for p in con.execute("SELECT id, slot FROM report_photos WHERE kind=? AND ref_id=? ORDER BY id", (kind, ref)):
        out.setdefault(p["slot"], []).append(p["id"])
    return out


def tasks_for(con, coach_ids: list[int], d: str) -> list[dict]:
    if not coach_ids:
        return []
    q = f"SELECT * FROM report_tasks WHERE date=? AND coach_id IN ({','.join('?' * len(coach_ids))}) ORDER BY id"
    return [dict(t) | {"photos": _photos(con, "task", t["id"]).get("", [])} for t in con.execute(q, (d, *coach_ids))]


def view(con, r: dict, s: dict) -> dict:
    from . import pu, services as sv
    data = json.loads(r["data"] or "{}")
    groups, absent, trials = [], [], []
    for g in s["groups"]:
        j = sv.journal(con, g["id"], r["date"])
        rows = j["rows"]
        regular = [x for x in rows if x.get("status") not in ("trial",) and "makeup_id" not in x]
        came = sum(1 for x in rows if x.get("status") == "present" or x.get("trial_came") == 1
                   or ("makeup_id" in x and (x.get("mk_status") or x.get("status")) == "done"))
        plan = sum(1 for x in rows if x.get("status") != "vacation" and not x.get("warned"))
        groups.append({**g, "came": came, "plan": plan, "unmarked": pu._unmarked(rows)})
        absent += [{"id": x["id"], "name": x["name"], "group": g["name"]} for x in regular if x.get("status") == "absent"]
        trials += [{"id": x["id"], "name": x["name"], "group": g["name"], "came": x.get("trial_came")} for x in rows if x.get("status") == "trial"]
    photos = _photos(con, "report", r["id"])
    people = [s["main_id"], *s["assistants"]]
    tasks = tasks_for(con, people, r["date"])
    names = {u["id"]: dict(u) for u in con.execute(f"SELECT id, name FROM users WHERE id IN ({','.join('?' * len(people))})", people)}
    arrivals = {c["user_id"]: c["at"] for c in con.execute(
        f"SELECT user_id, at FROM checkins WHERE branch_id=? AND date=? AND user_id IN ({','.join('?' * len(people))})",
        (r["branch_id"], r["date"], *people))}
    cash = data.get("cash") or {}
    steps = [
        {"key": "marks", "title": "Дети отмечены", "done": all(g["unmarked"] == 0 for g in groups)},
        {"key": "absent", "title": "Кто не пришёл", "done": all(str(a["id"]) in (data.get("absent") or {}) for a in absent)},
        {"key": "trials", "title": "Пробные", "done": all(t["came"] is not None for t in trials)
         and all(str(t["id"]) in (data.get("trials") or {}) for t in trials if t["came"] == 1)},
        {"key": "unlisted", "title": "Пришли, но не записаны", "done": bool(data.get("unlisted_none") or data.get("unlisted"))},
        {"key": "pays", "title": "Индивы и оплаты на месте", "done": bool(data.get("pays_none") or data.get("pays"))},
        {"key": "cash", "title": "Касса", "done": all(cash.get(k) for k, _, _ in CASH)},
        {"key": "merch", "title": "Мерч", "done": data.get("merch") in ("yes", "no")},
        {"key": "inventory", "title": "Инвентарь", "done": (data.get("inv") == "ok" and bool(photos.get("inv_ok")))
         or (data.get("inv") == "broken" and bool(photos.get("inv_broken")) and len((data.get("inv_note") or "").strip()) >= 3)},
        {"key": "photos", "title": "Фото филиала", "done": all(photos.get(k) for k, _ in PHOTO_SLOTS)},
    ]
    if tasks:
        steps.append({"key": "tasks", "title": "Задания на сегодня", "done": all(t["done_at"] for t in tasks)})
    end = s["end"]
    return {"id": r["id"], "branch_id": r["branch_id"], "branch": s["branch"], "date": r["date"], "status": r["status"],
            "submitted_at": r["submitted_at"], "submitted_by": r["submitted_by"], "end": _iso(end) if end else None,
            "main": names.get(s["main_id"], {}).get("name", ""), "main_id": s["main_id"],
            "assistants": [names.get(a, {}).get("name", "") for a in s["assistants"]],
            "arrivals": {names.get(k, {}).get("name", ""): v[11:16] for k, v in arrivals.items()},
            "groups": groups, "absent": absent, "trials": trials, "data": data, "photos": photos, "tasks": tasks,
            "steps": steps, "ready": all(x["done"] for x in steps),
            "slots": PHOTO_SLOTS, "cash_items": CASH, "absent_reasons": ABSENT, "trial_outcomes": TRIAL, "pay_methods": PAY_METHODS}


ALLOWED = {"absent", "trials", "unlisted", "unlisted_none", "pays", "pays_none", "cash", "merch", "inv", "inv_note"}


def update(con, rid: int, patch: dict) -> None:
    r = _row(con, rid)
    if r["status"] == "done":
        raise ReportError("Отчёт уже сдан")
    data = json.loads(r["data"] or "{}")
    for k, v in patch.items():
        if k not in ALLOWED:
            continue
        if k in ("absent", "trials", "cash"):  # словари дополняем: шаги заполняются по одному
            data[k] = {**(data.get(k) or {}), **{str(a): b for a, b in (v or {}).items()}}
        elif k in ("unlisted", "pays"):
            data[k] = [x for x in (v or []) if isinstance(x, dict)][:30]
        else:
            data[k] = v
    con.execute("UPDATE coach_reports SET data=? WHERE id=?", (json.dumps(data, ensure_ascii=False), rid))


def add_photo(con, kind: str, ref: int, slot: str, raw: bytes) -> int:
    from .profiles import PHOTO_DIR, _kind
    if not raw or len(raw) > 4 * 1024 * 1024 or not _kind(raw):
        raise ReportError("Нужна фотография до 4 МБ")
    pid = con.execute("INSERT INTO report_photos(kind, ref_id, slot, created_at) VALUES (?,?,?,?)",
                      (kind, ref, slot[:20], _iso(_now()))).lastrowid
    os.makedirs(PHOTO_DIR, exist_ok=True)
    with open(photo_path(pid), "wb") as f:
        f.write(raw)
    return pid


def remove_photo(con, pid: int) -> None:
    con.execute("DELETE FROM report_photos WHERE id=?", (pid,))
    try:
        os.remove(photo_path(pid))
    except FileNotFoundError:
        pass


def task_done(con, tid: int, user: dict) -> None:
    t = con.execute("SELECT * FROM report_tasks WHERE id=?", (tid,)).fetchone()
    if not t:
        raise ReportError("Задание не найдено")
    con.execute("UPDATE report_tasks SET done_at=? WHERE id=?", (None if t["done_at"] else _iso(_now()), tid))


def add_task(con, coach_id: int, d: str, text: str, user: dict) -> int:
    text = (text or "").strip()
    if len(text) < 3:
        raise ReportError("Напишите задание")
    date.fromisoformat(d)
    if not con.execute("SELECT 1 FROM users WHERE id=? AND role='coach' AND active=1", (coach_id,)).fetchone():
        raise ReportError("Выберите тренера")
    return con.execute("INSERT INTO report_tasks(coach_id, date, text, created_by, created_at) VALUES (?,?,?,?,?)",
                       (coach_id, d, text[:500], user.get("name", ""), _iso(_now()))).lastrowid


# ---------------- сдать ----------------

def submit(con, rid: int, user: dict) -> dict:
    from . import merch, pu
    r, s = check_access(con, rid, user, False)
    v = view(con, r, s)
    if r["status"] == "done":
        raise ReportError("Отчёт уже сдан")
    left = [x["title"] for x in v["steps"] if not x["done"]]
    if left:
        raise ReportError("Ещё не готово: " + ", ".join(left))
    now = _iso(_now())
    con.execute("UPDATE coach_reports SET status='done', submitted_at=?, submitted_by=? WHERE id=?", (now, user.get("name", ""), rid))
    if v["data"].get("inv") == "broken":
        con.execute("INSERT INTO inventory_issues(report_id, branch_id, date, note, created_at) VALUES (?,?,?,?,?)",
                    (rid, r["branch_id"], r["date"], v["data"].get("inv_note", ""), now))
        pu.notify_chat(con, "care", f"🔴 {_managers(con)} Неисправный инвентарь, {s['branch']}: {v['data'].get('inv_note', '')}. Фото в CRM")
    trial_tasks(con, v)
    merch.report_shift(con, {"id": s["main_id"], "name": v["main"]}, r["branch_id"], v["data"].get("merch") == "yes", r["date"])
    for a in s["assistants"]:  # помощник тоже ответил про мерч: отчёт общий
        merch.report_shift(con, {"id": a, "name": ""}, r["branch_id"], v["data"].get("merch") == "yes", r["date"])
    con.execute("UPDATE alerts SET closed_at=? WHERE key LIKE ? AND closed_at IS NULL", (now, f"rep%:{rid}:%"))
    pu.notify_chat(con, "coaches", text(con, view(con, _row(con, rid), s)))
    return view(con, _row(con, rid), s)


TRIAL_TASK = {"bought": "Купили на месте у тренера: оформить абонемент", "think": "Думают после пробного: перезвонить",
              "no": "Не подошло после пробного: узнать причину"}


def trial_tasks(con, v: dict) -> int:
    """Итог пробного от тренера сразу становится задачей ответственному за филиал, со словами тренера."""
    from . import pu, tasks
    n = 0
    gid = {g["name"]: g["id"] for g in v["groups"]}
    for t in v["trials"]:
        out = (v["data"].get("trials") or {}).get(str(t["id"]))
        if t["came"] != 1 or out not in TRIAL_TASK:
            continue
        due = _iso(_now() + timedelta(hours=2 if out == "bought" else 20))
        note = f"Тренер {v['main']} в отчёте о смене {v['date'][8:]}.{v['date'][5:7]}: «{TRIAL[out]}», группа {t['group']}"
        n += bool(tasks.add_task(con, "manual", t["id"], due, title=TRIAL_TASK[out], assignee=pu.responsible(con, gid.get(t["group"])),
                                 source="manual", note=note, auto_key=f"trial_out:{t['id']}:{v['date']}"))
    return n


def _managers(con) -> str:
    from . import pu
    return " ".join(x for x in (pu._mention(con, pu._controller(con)), pu._mention(con, pu._owner_admin(con))) if x)


def text(con, v: dict) -> str:
    """Текст отчёта в привычном виде для чата тренеров."""
    from . import pu
    d = date.fromisoformat(v["date"])
    data = v["data"]
    ok = lambda b: "🟢" if b else "🔴"
    lines = ["ОТЧЁТ ТРЕНЕРА О СМЕНЕ", "___", f"Дата {d:%d.%m.%Y}", f"День недели - {logic.WEEKDAY_NAMES[d.weekday()].lower()}",
             f"Место проведения - {v['branch']}", "Тренеры:"]
    lines.append(f"Гл {v['main']}" + (f", время прихода {v['arrivals'][v['main']]}" if v["arrivals"].get(v["main"]) else ""))
    lines += [f"Пм {a}" + (f", время прихода {v['arrivals'][a]}" if v["arrivals"].get(a) else "") for a in v["assistants"]]
    lines += ["", "🟢дети в CRM отмечены"] + [f"{ok(data.get('cash', {}).get(k))}{t.lower()}" for k, t, _ in CASH]
    tr = v["trials"]
    bought = [t for t in tr if (data.get("trials") or {}).get(str(t["id"])) == "bought"]
    lines += ["", "🟢ПУ:", f"По плану записано - {len(tr)}", f"Пришли - {sum(1 for t in tr if t['came'] == 1)}", f"Купили - {len(bought)}"]
    lines += [f"{t['name']} - {TRIAL.get((data.get('trials') or {}).get(str(t['id'])), 'не пришёл')}" for t in tr]
    pays = data.get("pays") or []
    lines += ["", "🟢Индивы и оплаты:"] + ([f"{p.get('name', '')} - {p.get('amount', '')} ₽ - {PAY_METHODS.get(p.get('method'), '')} - {p.get('what', '')}"
                                          for p in pays] or ["Не было"])
    un = data.get("unlisted") or []
    lines += ["", "🟢Пришли, но не записаны:"] + ([f"{u.get('name', '')} - {u.get('group', '')}" for u in un] or ["Не было"])
    if un:
        lines.append(" ".join(x for x in (pu._mention(con, pu._controller(con)),) if x))
    lines += [""] + [f"{i}) {g['time'] or ''} {g['name']}: {g['came']} из {g['plan']}" for i, g in enumerate(v["groups"], 1)]
    if v["absent"]:
        lines += [""] + [f"{a['name']} - {ABSENT.get((data.get('absent') or {}).get(str(a['id'])), '')}" for a in v["absent"]]
    lines += ["", f"{'🟢инвентарь исправен' if data.get('inv') == 'ok' else '🔴неисправность: ' + data.get('inv_note', '')}",
              f"🟢мерч: {'продавали' if data.get('merch') == 'yes' else 'не продавали'}", "🟢фото в CRM", "", "#отчет_тренера_о_смене"]
    return "\n".join(lines)


# ---------------- контроль ----------------

def overdue(con, now: datetime | None = None) -> list[dict]:
    """Несданные отчёты: level 1 - старшему администратору (30 мин), level 2 - руководителю (2 часа)."""
    now = now or _now()
    since = (con.execute("SELECT value FROM settings WHERE key='coach_reports_from'").fetchone() or [""])[0]
    out = []
    for d in (now.date() - timedelta(days=1), now.date()):
        if d.isoformat() < since:
            continue
        for s in shifts(con, d):
            if not s["end"] or now < s["end"] + timedelta(minutes=LATE_SENIOR):
                continue
            r = con.execute("SELECT id, status FROM coach_reports WHERE branch_id=? AND date=? AND main_id=?",
                            (s["branch_id"], d.isoformat(), s["main_id"])).fetchone()
            if r and r["status"] == "done":
                continue
            mins = int((now - s["end"]).total_seconds() // 60)
            name = (con.execute("SELECT name FROM users WHERE id=?", (s["main_id"],)).fetchone() or [""])[0]
            out.append({"branch_id": s["branch_id"], "branch": s["branch"], "date": d.isoformat(), "coach_id": s["main_id"],
                        "coach": name, "end": _iso(s["end"])[11:], "minutes": mins, "level": 2 if mins >= LATE_OWNER else 1,
                        "report_id": r["id"] if r else None})
    return out


def tick(con, now: datetime | None = None) -> int:
    from . import pu
    n = 0
    for o in overdue(con, now):
        key = f"{o['branch_id']}:{o['date']}:{o['coach_id']}"
        if pu.add_alert(con, "rep30", f"rep30:{key}", f"Отчёт о смене не сдан: {o['coach']}, {o['branch']}"):
            con.execute("UPDATE alerts SET closed_at=? WHERE key=?", (_iso(_now()), f"rep30:{key}"))  # на столе видно и так
            pu.notify_chat(con, "coaches", f"⚠️ {pu._mention(con, o['coach_id'])} {pu._mention(con, pu._controller(con))} "
                                           f"Отчёт о смене не сдан: {o['branch']}, тренировки закончились в {o['end']}. Сдай в CRM на экране «Сегодня»")
            n += 1
        if o["level"] == 2 and pu.add_alert(con, "rep120", f"rep120:{key}", f"Отчёт о смене не сдан 2 часа: {o['coach']}, {o['branch']}"):
            con.execute("UPDATE alerts SET closed_at=? WHERE key=?", (_iso(_now()), f"rep120:{key}"))
            pu.notify_chat(con, "coaches", f"🔴 {pu._mention(con, pu._owner_admin(con))} Прошло 2 часа, {o['coach']} не сдал отчёт о смене: "
                                           f"{o['branch']}, {o['date'][8:]}.{o['date'][5:7]}")
            n += 1
    return n


def desk(con, owner: bool) -> dict:
    """Для стола: несданные отчёты, открытые неисправности, задания на сегодня, сданные отчёты за сегодня."""
    late = [o for o in overdue(con) if owner and o["level"] == 2 or not owner]
    issues = [dict(i) | {"branch": (con.execute("SELECT name FROM branches WHERE id=?", (i["branch_id"],)).fetchone() or [""])[0],
                         "photos": _photos(con, "report", i["report_id"]).get("inv_broken", [])}
              for i in con.execute("SELECT * FROM inventory_issues WHERE closed_at IS NULL ORDER BY id DESC")]
    today = date.today().isoformat()
    done = [dict(r) for r in con.execute("""SELECT r.id, r.date, r.branch_id, r.main_id, r.submitted_at, r.submitted_by, b.name AS branch, u.name AS coach
                                            FROM coach_reports r JOIN branches b ON b.id=r.branch_id JOIN users u ON u.id=r.main_id
                                            WHERE r.status='done' AND r.date>=? ORDER BY r.submitted_at DESC""",
                                         ((date.today() - timedelta(days=1)).isoformat(),))]
    tasks = [dict(t) | {"coach": (con.execute("SELECT name FROM users WHERE id=?", (t["coach_id"],)).fetchone() or [""])[0]}
             for t in con.execute("SELECT * FROM report_tasks WHERE date>=? ORDER BY date, id", (today,))]
    return {"late": late, "issues": issues, "done": done, "tasks": tasks}
