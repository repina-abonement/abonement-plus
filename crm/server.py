"""HTTP-сервер Абонемент Плюс. Только стандартная библиотека Python 3.11+.

Запуск:  python -m crm.server            (порт 8000, переменная CRM_PORT)
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import traceback
from datetime import datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import logic, ai, calldigest, callstats, coaches, comms, db, expenses, fakegen, legal, merch, offers, money, planner, schedule, services as sv, sources, staff, tasks
from .services import ApiError


class Raw:
    """Ответ файлом (запись звонка, фото из мессенджера, выгрузка)."""
    def __init__(self, data: bytes, ctype: str, filename: str | None = None):
        self.data, self.ctype, self.filename = data, ctype, filename


def csv_raw(rows: list[list], filename: str) -> "Raw":
    """Таблица для Excel: точка с запятой, UTF-8 с BOM, чтобы кириллица открылась без танцев."""
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    for r in rows:
        w.writerow(r)
    return Raw(("\ufeff" + buf.getvalue()).encode("utf-8"), "text/csv; charset=utf-8", filename)


def cx(fn, *a, **kw):
    """Ошибки внешних сервисов — понятным сообщением."""
    try:
        return fn(*a, **kw)
    except comms.CommsError as e:
        raise ApiError(str(e))


def base_url(h) -> str:
    proto = (h.headers.get("X-Forwarded-Proto") or "http").split(",")[0].strip()
    return f"{proto}://{h.headers.get('Host') or 'localhost'}"

STATIC = os.path.join(os.path.dirname(__file__), "static")
SECURE_COOKIE = os.environ.get("CRM_SECURE_COOKIE", "0") == "1"
SESSION_DAYS = 14

ROUTES: list[tuple[str, re.Pattern, callable, str]] = []


def route(method: str, pattern: str, role: str = "any"):
    rx = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[0-9]+)", pattern) + "$")

    def deco(fn):
        ROUTES.append((method, rx, fn, role))
        return fn
    return deco


# ---------------- auth ----------------

def current_user(con, handler) -> dict | None:
    raw = handler.headers.get("Cookie")
    if not raw:
        return None
    c = SimpleCookie(raw)
    tok = c.get("crm_session")
    if not tok:
        return None
    r = con.execute("""SELECT u.id, u.login, u.name, u.role, s.created_at FROM sessions s
                       JOIN users u ON u.id=s.user_id WHERE s.token=? AND u.active=1""", (tok.value,)).fetchone()
    if not r:
        return None
    if (datetime.now() - datetime.fromisoformat(r["created_at"])).days > SESSION_DAYS:
        con.execute("DELETE FROM sessions WHERE token=?", (tok.value,))
        return None
    return dict(r)


@route("POST", "/api/login", role="public")
def login(con, h, user, body, q):
    u = con.execute("SELECT * FROM users WHERE login=? AND active=1", ((body.get("login") or "").strip(),)).fetchone()
    if not u or not db.check_password(body.get("password") or "", u["pw_hash"]):
        raise ApiError("Неверный логин или пароль", 401)
    tok = secrets.token_urlsafe(32)
    con.execute("INSERT INTO sessions(token, user_id, created_at) VALUES (?,?,?)", (tok, u["id"], db.now_iso()))
    sv.audit(con, dict(u), "login")
    cookie = f"crm_session={tok}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_DAYS * 86400}"
    if SECURE_COOKIE:
        cookie += "; Secure"
    h.extra_headers.append(("Set-Cookie", cookie))
    return {"ok": True, "user": {"name": u["name"], "role": u["role"]}}


@route("POST", "/api/logout")
def logout(con, h, user, body, q):
    c = SimpleCookie(h.headers.get("Cookie") or "")
    if c.get("crm_session"):
        con.execute("DELETE FROM sessions WHERE token=?", (c["crm_session"].value,))
    h.extra_headers.append(("Set-Cookie", "crm_session=; Path=/; Max-Age=0"))
    return {"ok": True}


@route("GET", "/api/me")
def me(con, h, user, body, q):
    return user


@route("POST", "/api/me/password")
def change_password(con, h, user, body, q):
    u = con.execute("SELECT pw_hash FROM users WHERE id=?", (user["id"],)).fetchone()
    if not db.check_password(body.get("old") or "", u["pw_hash"]):
        raise ApiError("Текущий пароль неверный")
    new = body.get("new") or ""
    if len(new) < 8:
        raise ApiError("Новый пароль — минимум 8 символов")
    con.execute("UPDATE users SET pw_hash=? WHERE id=?", (db.hash_password(new), user["id"]))
    sv.audit(con, user, "password.change")
    return {"ok": True}


@route("GET", "/api/users", role="admin")
def users_list(con, h, user, body, q):
    ucols = {r[1] for r in con.execute("PRAGMA table_info(users)")}
    ph = "phone" if "phone" in ucols else "'' AS phone"
    return [dict(r) for r in con.execute(f"SELECT id, login, name, role, active, sip_number, tg_username, {ph} FROM users ORDER BY id")]


@route("PATCH", "/api/users/{uid}", role="admin")
def users_patch(con, h, user, body, q, uid):
    if "active" in body:
        if int(uid) == user["id"] and not body["active"]:
            raise ApiError("Нельзя отправить в архив самого себя")
        con.execute("UPDATE users SET active=? WHERE id=?", (1 if body["active"] else 0, int(uid)))
        if not body["active"]:
            con.execute("DELETE FROM sessions WHERE user_id=?", (int(uid),))
            con.execute("UPDATE groups SET coach_id=NULL WHERE coach_id=?", (int(uid),))
        sv.audit(con, user, "user.active", f"{uid}:{body['active']}")
    if body.get("password"):
        if len(body["password"]) < 8:
            raise ApiError("Пароль от 8 символов")
        con.execute("UPDATE users SET pw_hash=? WHERE id=?", (db.hash_password(body["password"]), int(uid)))
        con.execute("DELETE FROM sessions WHERE user_id=? AND ?!=?", (int(uid), int(uid), user["id"]))
        sv.audit(con, user, "user.password", str(uid))
    if "sip_number" in body:
        sip = "".join(ch for ch in str(body["sip_number"] or "") if ch.isdigit())[:10]
        con.execute("UPDATE users SET sip_number=? WHERE id=?", (sip, int(uid)))
    if "tg_username" in body:
        tg = "".join(ch for ch in str(body["tg_username"] or "").strip().lstrip("@") if ch.isalnum() or ch == "_")[:40]
        con.execute("UPDATE users SET tg_username=? WHERE id=?", (tg, int(uid)))
    if "phone" in body:
        if "phone" not in {r[1] for r in con.execute("PRAGMA table_info(users)")}:
            con.execute("ALTER TABLE users ADD COLUMN phone TEXT NOT NULL DEFAULT ''")
        d = "".join(ch for ch in str(body["phone"] or "") if ch.isdigit())
        if len(d) == 11 and d[0] in "78":
            d = "7" + d[1:]
        elif len(d) == 10:
            d = "7" + d
        elif d:
            raise ApiError("Телефон: 11 цифр, например +7 911 831-27-83")
        con.execute("UPDATE users SET phone=? WHERE id=?", ("+" + d if d else "", int(uid)))
    return {"ok": True}


@route("GET", "/api/backup", role="admin")
def backup_status(con, h, user, body, q):
    from . import backup
    return backup.status(con)


@route("POST", "/api/backup", role="admin")
def backup_save(con, h, user, body, q):
    from . import backup, comms
    if "yadisk_token" in body:
        comms.put(con, "backup_yadisk_token", (body.get("yadisk_token") or "").strip())
        sv.audit(con, user, "backup.token", "")
    if body.get("run"):
        con.commit()  # копия должна включать всё, что уже сохранено
        r = backup.run(con)
        return backup.status(con) | {"result": r}
    return backup.status(con)


@route("POST", "/api/users/bulk", role="admin")
def users_bulk(con, h, user, body, q):
    """Завести команду списком: «Имя Фамилия; роль; телефон» по строке. Пароли генерируются и показываются один раз."""
    import secrets
    from . import schedule
    alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    roles = {"оператор": "operator", "админ": "admin", "администратор": "admin", "тренер": "coach", "контролёр": "operator",
             "контролер": "operator", "руководитель": "admin"}
    ucols = {r[1] for r in con.execute("PRAGMA table_info(users)")}
    if "phone" not in ucols:
        con.execute("ALTER TABLE users ADD COLUMN phone TEXT NOT NULL DEFAULT ''")
    norm = lambda x: frozenset(w for w in x.lower().replace("ё", "е").split() if w)
    existing = {norm(r["name"]): dict(r) for r in con.execute("SELECT id, name, login, role FROM users WHERE active=1")}
    out = []
    for line in (body.get("text") or "").splitlines():
        parts = [p.strip() for p in line.split(";")]
        name = parts[0] if parts else ""
        if not name:
            continue
        role_word = (parts[1] if len(parts) > 1 else "").lower()
        phone = parts[2] if len(parts) > 2 else ""
        pw = "".join(secrets.choice(alphabet) for _ in range(10))
        ex = existing.get(norm(name))
        if ex:
            con.execute("UPDATE users SET pw_hash=? WHERE id=?", (db.hash_password(pw), ex["id"]))
            con.execute("DELETE FROM sessions WHERE user_id=?", (ex["id"],))
            uid, login_, status = ex["id"], ex["login"], "новый пароль"
            if phone:
                con.execute("UPDATE users SET phone=? WHERE id=?", (phone, uid))
            role = ex["role"]
        else:
            role = roles.get(role_word, "coach" if "тренер" in role_word else "operator")
            base = schedule._login(name.replace(" ", "")) or "user"
            login_, i = base, 2
            while con.execute("SELECT 1 FROM users WHERE login=?", (login_,)).fetchone():
                login_, i = f"{base}{i}", i + 1
            uid = con.execute("INSERT INTO users(login, name, role, pw_hash, phone) VALUES (?,?,?,?,?)",
                              (login_, name, role, db.hash_password(pw), phone)).lastrowid
            status = "заведён"
        if role_word.startswith("контрол"):
            for k in ("planner_controller", "checkin_reviewer"):
                con.execute("INSERT INTO settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(uid)))
        out.append({"name": name, "login": login_, "password": pw, "role": role, "status": status})
        sv.audit(con, user, "user.bulk", login_)
    return {"users": out}


@route("POST", "/api/users", role="admin")
def users_create(con, h, user, body, q):
    login_ = (body.get("login") or "").strip()
    pw = body.get("password") or ""
    if not login_ or len(pw) < 8:
        raise ApiError("Нужен логин и пароль от 8 символов")
    role = body.get("role") if body.get("role") in ("admin", "operator", "coach") else "operator"
    try:
        con.execute("INSERT INTO users(login, name, role, pw_hash) VALUES (?,?,?,?)",
                    (login_, body.get("name") or login_, role, db.hash_password(pw)))
    except Exception:
        raise ApiError("Такой логин уже есть")
    sv.audit(con, user, "user.create", login_)
    return {"ok": True}


# ---------------- данные ----------------

def coach_guard(con, user, group_id: int):
    if user["role"] != "coach":
        return
    if not schedule.coach_may(con, user["id"], group_id):
        raise ApiError("Это не ваша группа", 403)


@route("GET", "/api/meta")
def meta(con, h, user, body, q):
    m = sv.meta(con) | {"user": user, "demo": os.environ.get("CRM_DEMO", "1") == "1", "comms": comms.status(con)}
    user["sip_number"] = con.execute("SELECT sip_number FROM users WHERE id=?", (user["id"],)).fetchone()[0]
    if user["role"] == "coach":
        m["groups"] = [g for g in m["groups"] if schedule.coach_may(con, user["id"], g["id"])]
        m["settings"] = {}
    from . import staffdocs
    m["staff_docs_pending"] = staffdocs.pending(con, user)
    return m


@route("GET", "/api/staff-docs")
def staff_docs(con, h, user, body, q):
    from . import staffdocs, legal
    return {"docs": [d | {"html": legal._md(d["text"])} for d in staffdocs.docs_for(con, user, base_url(h))], "name": user["name"]}


@route("POST", "/api/staff-docs/accept")
def staff_docs_accept(con, h, user, body, q):
    from . import staffdocs
    ip = (h.headers.get("X-Forwarded-For") or h.client_address[0] or "").split(",")[0].strip()
    n = staffdocs.accept(con, user, body.get("docs") or [], body.get("signed_name") or "", ip, h.headers.get("User-Agent") or "", base_url(h))
    sv.audit(con, user, "staff_docs.accept", str(n))
    return {"ok": True, "accepted": n}


@route("GET", "/api/staff-docs/overview", role="admin")
def staff_docs_overview(con, h, user, body, q):
    from . import staffdocs
    return {"items": staffdocs.overview(con)}


@route("GET", "/api/staff-docs/log/{uid}", role="admin")
def staff_docs_log(con, h, user, body, q, uid):
    from . import staffdocs
    u = con.execute("SELECT name FROM users WHERE id=?", (int(uid),)).fetchone()
    return {"name": u[0] if u else "", "items": staffdocs.log(con, int(uid))}


@route("PATCH", "/api/groups/{gid}", role="admin")
def group_patch(con, h, user, body, q, gid):
    if "capacity" in body:
        cap = int(body["capacity"])
        if cap < 0 or cap > 200:
            raise ApiError("Неверное число мест")
        con.execute("UPDATE groups SET capacity=? WHERE id=?", (cap, int(gid)))
        if "coach_id" not in body:
            return {"ok": True}
    coach = body.get("coach_id") or None
    if coach and not con.execute("SELECT 1 FROM users WHERE id=? AND role='coach'", (coach,)).fetchone():
        raise ApiError("Тренер не найден")
    con.execute("UPDATE groups SET coach_id=? WHERE id=?", (coach, int(gid)))
    sv.audit(con, user, "group.coach", f"{gid}:{coach}")
    return {"ok": True}


@route("GET", "/api/board/sales", role="operator")
def board_sales(con, h, user, body, q):
    return sv.board(con, "sales")


@route("GET", "/api/tasks", role="operator")
def tasks_list(con, h, user, body, q):
    if q.get("as_user") and user["role"] == "admin":  # «Смотреть как»: стол глазами сотрудника
        u = con.execute("SELECT id, name, role FROM users WHERE id=?", (int(q["as_user"]),)).fetchone()
        if not u:
            raise ApiError("Сотрудник не найден", 404)
        return tasks.my_tasks(con, dict(u) | {"role": "operator"}, "mine")
    return tasks.my_tasks(con, user, q.get("scope", "mine"))


@route("POST", "/api/tasks/{tid}/done", role="coach")
def task_done(con, h, user, body, q, tid):
    t = con.execute("SELECT assignee_id FROM tasks WHERE id=?", (int(tid),)).fetchone()
    if user["role"] == "coach" and (not t or t["assignee_id"] != user["id"]):
        raise ApiError("Это не ваша задача", 403)
    cx(tasks.complete, con, int(tid), user, body.get("result") or "")
    return {"ok": True}


@route("POST", "/api/students/{sid}/tasks", role="operator")
def task_add(con, h, user, body, q, sid):
    sv.get_student_row(con, int(sid))
    due = body.get("due") or db.now_iso()
    return {"id": tasks.add_task(con, "manual", int(sid), due, title=(body.get("title") or "Задача").strip()[:200],
                                 source="manual", assignee=int(body["assignee"]) if body.get("assignee") else user["id"])}


@route("GET", "/api/tasks/settings", role="admin")
def tasks_settings(con, h, user, body, q):
    return tasks.settings_view(con)


@route("PUT", "/api/tasks/settings", role="admin")
def tasks_settings_put(con, h, user, body, q):
    tasks.save_settings(con, body)
    return tasks.settings_view(con)


def merch_alerts(con):
    from datetime import date as _d, timedelta as _td
    t = _d.today()
    reg = merch.registry(con, (t - _td(days=30)).isoformat(), t.isoformat())
    return {"qr_no_receipt": reg["qr_no_receipt"], "missing_reports": merch.missing_reports(con, t - _td(days=1)),
            "cash_not_handed": [x for x in reg["by_seller"] if x["cash_not_handed"]]}


@route("GET", "/api/workdesk", role="operator")
def workdesk(con, h, user, body, q):
    board = sv.board(con, "sales")
    trials = [dict(k, kind="trial", level=k["color"], text=k.get("badge") or "Пробное закончилось, связаться")
              for c in board["columns"] if c["key"] == "trial_done" for k in c["cards"] if k.get("color")]
    covered = {r[0] for r in con.execute("SELECT student_id FROM tasks WHERE status='open' AND kind IN ('how_was','no_show')")}
    trials = [t for t in trials if t["id"] not in covered]  # по ним уже есть задача сверху
    items = comms.urgent(con) + [{"kind": "trial", "level": t["level"], "ts": t.get("trial_at"), "student_id": t["id"],
                                  "phone": t["phone"], "title": t["name"],
                                  "text": "Пробное закончилось " + (t.get("badge") or "").replace(" мин после пробного", " мин назад") + ", связаться"}
                                 for t in trials]
    items.sort(key=lambda x: (x["level"] != "red", x["ts"] or ""), reverse=False)
    reds = [x for x in items if x["level"] == "red"]
    reds.sort(key=lambda x: x["ts"] or "", reverse=True)
    rest = [x for x in items if x["level"] != "red"]
    rest.sort(key=lambda x: x["ts"] or "", reverse=True)
    return {"urgent": reds + rest, "board": board, "birthdays": sv.birthdays(con), "needs_subs": schedule.needs_subs(con),
            "checkins": staff.day_report(con) if checkin_reviewer(con, user) else None,
            "merch_alerts": merch_alerts(con),
            "waitlist": __import__("crm.waitlist", fromlist=["funnel"]).funnel(con),
            "missing_marks": __import__("crm.pu", fromlist=["missing_marks"]).missing_marks(con),
            "alerts": __import__("crm.pu", fromlist=["open_alerts"]).open_alerts(con),
            "call_focus": calldigest.focus_for(con, (con.execute("SELECT sip_number FROM users WHERE id=?",
                                                                 (int(q["as_user"]) if q.get("as_user") and user["role"] == "admin" else user["id"],)).fetchone() or [""])[0]),
            "bills_due": expenses.bills_due(con) if user["role"] == "admin" else [],
            "ads_alerts": expenses.ads_alerts(con) if user["role"] == "admin" else [],
            "absences": schedule.absences(con),
            "coaches": [dict(r) for r in con.execute("SELECT id, name FROM users WHERE role='coach' AND active=1 ORDER BY name")],
            **comms.counts(con)}


@route("GET", "/api/board/members", role="operator")
def board_members(con, h, user, body, q):
    return sv.board(con, "members")


@route("GET", "/api/board/skips", role="operator")
def board_skips(con, h, user, body, q):
    return sv.board(con, "skips")


@route("POST", "/api/students/{sid}/move", role="operator")
def move(con, h, user, body, q, sid):
    r = sv.move_card(con, int(sid), body.get("funnel"), body.get("stage"), user, body.get("reason") or "", body.get("comment") or "")
    return {"ok": True} | (r or {})


@route("GET", "/api/students", role="operator")
def students(con, h, user, body, q):
    return sv.search_students(con, q.get("q", ""), q.get("archived") == "1")


@route("GET", "/api/check-phone", role="operator")
def check_phone(con, h, user, body, q):
    return sv.find_families_by_phone(con, q.get("phone", ""))


@route("POST", "/api/students", role="operator")
def student_create(con, h, user, body, q):
    sid = sv.create_student(con, body, user)
    if body.get("phone"):
        comms.relink_phone(con, body["phone"], sv.get_student_row(con, sid)["family_id"])
    if body.get("conversation_id"):
        comms.link(con, int(body["conversation_id"]), sv.get_student_row(con, sid)["family_id"])
    return {"id": sid}


@route("GET", "/api/students/{sid}", role="operator")
def student_get(con, h, user, body, q, sid):
    return sv.student_detail(con, int(sid))


@route("PATCH", "/api/students/{sid}", role="operator")
def student_patch(con, h, user, body, q, sid):
    sv.update_student(con, int(sid), body, user)
    return {"ok": True}


@route("POST", "/api/students/{sid}/note", role="operator")
def student_note(con, h, user, body, q, sid):
    text = (body.get("text") or "").strip()
    if not text:
        raise ApiError("Пустой комментарий")
    sv.get_student_row(con, int(sid))
    sv.log(con, int(sid), text, user, body.get("kind") or "note")
    con.execute("UPDATE students SET priority=0 WHERE id=?", (int(sid),))
    return {"ok": True}


@route("POST", "/api/students/{sid}/vacations", role="operator")
def vacation_add(con, h, user, body, q, sid):
    sv.add_vacation(con, int(sid), body.get("start"), body.get("end"), body.get("note"), user)
    return {"ok": True}


@route("DELETE", "/api/vacations/{vid}", role="operator")
def vacation_del(con, h, user, body, q, vid):
    v = con.execute("SELECT * FROM vacations WHERE id=?", (int(vid),)).fetchone()
    if not v:
        raise ApiError("Отпуск не найден", 404)
    con.execute("DELETE FROM vacations WHERE id=?", (int(vid),))
    sv.log(con, v["student_id"], f"Отпуск {sv.fmt_date(v['start'])}–{sv.fmt_date(v['end'])} удалён", user, "vacation")
    return {"ok": True}


@route("GET", "/api/students/{sid}/quote", role="operator")
def quote(con, h, user, body, q, sid):
    return sv.quote_for(con, int(sid), q.get("month"), q.get("enrollment_id"), q.get("discount_id"))


@route("POST", "/api/students/{sid}/invoice", role="operator")
def invoice(con, h, user, body, q, sid):
    return sv.create_invoice(con, int(sid), body.get("month"), user, body.get("enrollment_id"), body.get("discount_id"))


@route("POST", "/api/students/{sid}/enrollments", role="operator")
def enrollment_add(con, h, user, body, q, sid):
    return {"id": sv.add_enrollment(con, int(sid), int(body.get("group_id") or 0), body.get("weekdays"),
                                    body.get("tariff") or "fixed", user)}


@route("PATCH", "/api/enrollments/{eid}", role="operator")
def enrollment_patch(con, h, user, body, q, eid):
    sv.update_enrollment(con, int(eid), body, user)
    return {"ok": True}


@route("POST", "/api/subscriptions/{sub}/pay", role="operator")
def pay(con, h, user, body, q, sub):
    sv.pay_subscription(con, int(sub), user, body.get("method") or "cash", body.get("amount"))
    return {"ok": True}


def mx(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValueError as e:
        raise ApiError(str(e))


@route("POST", "/api/students/{sid}/payments", role="operator")
def payment_add(con, h, user, body, q, sid):
    sv.get_student_row(con, int(sid))
    return {"id": mx(money.add_payment, con, int(sid), int(body.get("amount") or 0), body.get("method") or "", user,
                     comment=body.get("comment") or "")}


@route("POST", "/api/students/{sid}/single", role="operator")
def single_sell(con, h, user, body, q, sid):
    sv.get_student_row(con, int(sid))
    return {"id": mx(money.sell_single, con, int(sid), user)}


@route("POST", "/api/payments/{pid}/cancel", role="admin")
def payment_cancel(con, h, user, body, q, pid):
    mx(money.cancel_payment, con, int(pid), user)
    return {"ok": True}


@route("POST", "/api/payments/{pid}/confirm", role="admin")
def payment_confirm(con, h, user, body, q, pid):
    money.confirm_payment(con, int(pid), user)
    return {"ok": True}


@route("GET", "/api/payments", role="admin")
def payments_list(con, h, user, body, q):
    from datetime import date as _d
    start = q.get("from") or _d.today().replace(day=1).isoformat()
    end = q.get("to") or _d.today().isoformat()
    r = money.registry(con, start, end, q.get("method", ""), q.get("unconfirmed") == "1")
    r["debtors"] = money.debtors(con)
    if q.get("csv") == "1":
        rows = [["Дата", "Ученик", "Семья", "Группа", "Сумма", "Способ", "Комментарий", "Принял", "Подтверждён", "Отменён"]]
        rows += [[x["paid_at"].replace("T", " "), x["student"], x["family"], x["group_name"] or "", x["amount"], x["method_label"],
                  x["comment"], x["user_name"], "да" if x["confirmed"] else "нет", "да" if x["cancelled"] else ""] for x in r["rows"]]
        return csv_raw(rows, f"oplaty-{start}-{end}.csv")
    return r


def sx(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except schedule.SchedError as e:
        raise ApiError(str(e))


@route("GET", "/api/structure", role="operator")
def structure(con, h, user, body, q):
    return schedule.structure(con)


@route("POST", "/api/branches", role="admin")
def branch_add(con, h, user, body, q):
    return {"id": sx(schedule.save_branch, con, body)}


@route("PUT", "/api/branches/{bid}", role="admin")
def branch_edit(con, h, user, body, q, bid):
    return {"id": sx(schedule.save_branch, con, body, int(bid))}


@route("POST", "/api/branches/{bid}/archive", role="admin")
def branch_archive(con, h, user, body, q, bid):
    sx(schedule.set_branch_active, con, int(bid), bool(body.get("active")))
    return {"ok": True}


@route("POST", "/api/groups", role="admin")
def group_add(con, h, user, body, q):
    gid = sx(schedule.save_group, con, body)
    sv.audit(con, user, "group.create", str(gid))
    return {"id": gid}


@route("PUT", "/api/groups/{gid}", role="admin")
def group_edit(con, h, user, body, q, gid):
    sx(schedule.save_group, con, body, int(gid))
    sv.audit(con, user, "group.edit", str(gid))
    return {"ok": True}


@route("POST", "/api/groups/{gid}/archive", role="admin")
def group_archive(con, h, user, body, q, gid):
    sx(schedule.set_group_active, con, int(gid), bool(body.get("active")))
    return {"ok": True}


@route("GET", "/api/groups/{gid}/export", role="operator")
def group_export(con, h, user, body, q, gid):
    name, rows = sx(schedule.group_export, con, int(gid))
    return csv_raw(rows, name)


@route("POST", "/api/shifts", role="admin")
def shift_add(con, h, user, body, q):
    return {"id": sx(schedule.save_shift, con, body)}


@route("PUT", "/api/shifts/{shid}", role="admin")
def shift_edit(con, h, user, body, q, shid):
    return {"id": sx(schedule.save_shift, con, body, int(shid))}


@route("DELETE", "/api/shifts/{shid}", role="admin")
def shift_del(con, h, user, body, q, shid):
    schedule.remove_shift(con, int(shid))
    return {"ok": True}


@route("GET", "/api/schedule/day")
def schedule_day(con, h, user, body, q):
    from datetime import date as _d
    return schedule.day(con, _d.fromisoformat(q["date"]) if q.get("date") else _d.today())


@route("POST", "/api/substitutions", role="operator")
def sub_save(con, h, user, body, q):
    return {"id": sx(schedule.save_sub, con, body, user)}


@route("DELETE", "/api/substitutions/{sid}", role="operator")
def sub_delete(con, h, user, body, q, sid):
    schedule.remove_sub(con, int(sid))
    return {"ok": True}


@route("POST", "/api/schedule/import", role="admin")
def schedule_import(con, h, user, body, q):
    r = sx(schedule.import_schedule, con, body)
    sv.audit(con, user, "schedule.import", f"{r['groups_new']} новых, {r['groups_updated']} обновлено")
    return r


@route("GET", "/api/absences", role="operator")
def absences_list(con, h, user, body, q):
    return {"items": schedule.absences(con), "needs": schedule.needs_subs(con)}


@route("POST", "/api/absences", role="operator")
def absence_add(con, h, user, body, q):
    return {"id": sx(schedule.save_absence, con, body, user)}


@route("DELETE", "/api/absences/{aid}", role="operator")
def absence_delete(con, h, user, body, q, aid):
    schedule.remove_absence(con, int(aid))
    return {"ok": True}


def stx(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except staff.StaffError as e:
        raise ApiError(str(e))


@route("POST", "/api/checkin", role="coach")
def checkin(con, h, user, body, q):
    f = lambda k: float(body[k]) if body.get(k) not in (None, "") else None
    return stx(staff.checkin, con, user, int(body.get("branch_id") or 0), f("lat"), f("lng"), f("accuracy"))


def checkin_reviewer(con, user):
    if user["role"] == "admin":
        return True
    return str(user["id"]) == staff.setting(con, "checkin_reviewer")


@route("GET", "/api/checkins", role="operator")
def checkins_day(con, h, user, body, q):
    from datetime import date as _d
    if not checkin_reviewer(con, user):
        raise ApiError("Приходы тренеров проверяет назначенный сотрудник", 403)
    return staff.day_report(con, _d.fromisoformat(q["date"]) if q.get("date") else None)


@route("POST", "/api/checkins/{cid}/review", role="operator")
def checkin_review(con, h, user, body, q, cid):
    if not checkin_reviewer(con, user):
        raise ApiError("Приходы тренеров проверяет назначенный сотрудник", 403)
    stx(staff.review, con, int(cid), body.get("action") or "", user, body.get("note") or "")
    return {"ok": True}


@route("GET", "/api/staff/settings", role="admin")
def staff_settings(con, h, user, body, q):
    return {k: staff.setting(con, k) for k in staff.DEFAULTS}


@route("PUT", "/api/staff/settings", role="admin")
def staff_settings_put(con, h, user, body, q):
    for k in staff.DEFAULTS:
        if k in body:
            comms.put(con, k, str(body[k]).strip())
    return {k: staff.setting(con, k) for k in staff.DEFAULTS}


@route("GET", "/api/lessons/staff")
def lesson_staff_get(con, h, user, body, q):
    gid = int(q.get("group_id") or 0)
    coach_guard(con, user, gid)
    coaches_ = [dict(r) for r in con.execute("SELECT id, name FROM users WHERE role='coach' AND active=1 ORDER BY name")]
    return staff.lesson_staff(con, gid, q.get("date") or "") | {"coaches": coaches_}


@route("POST", "/api/lessons/staff")
def lesson_staff_set(con, h, user, body, q):
    gid = int(body.get("group_id") or 0)
    coach_guard(con, user, gid)
    stx(staff.set_lesson_staff, con, gid, body.get("date") or "", int(body.get("main_id") or 0),
        int(body["assistant_id"]) if body.get("assistant_id") else None, user)
    return {"ok": True}


def mex(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except merch.MerchError as e:
        raise ApiError(str(e))


@route("GET", "/api/merch/items")
def merch_items(con, h, user, body, q):
    return {"items": merch.items(con, all_=user["role"] == "admin" and q.get("all") == "1"), "methods": merch.METHODS}


@route("POST", "/api/merch/items", role="admin")
def merch_item_add(con, h, user, body, q):
    return {"id": mex(merch.save_item, con, body)}


@route("PUT", "/api/merch/items/{iid}", role="admin")
def merch_item_put(con, h, user, body, q, iid):
    return {"id": mex(merch.save_item, con, body, int(iid))}


@route("POST", "/api/merch/sales")
def merch_sell(con, h, user, body, q):
    return {"ids": mex(merch.sell, con, user, body)}


@route("GET", "/api/merch/sales", role="operator")
def merch_sales(con, h, user, body, q):
    from datetime import date as _d
    t = _d.today()
    return merch.registry(con, q.get("from") or t.replace(day=1).isoformat(), q.get("to") or t.isoformat())


@route("POST", "/api/merch/sales/{sid}/flag", role="operator")
def merch_flag(con, h, user, body, q, sid):
    mex(merch.set_flag, con, int(sid), body.get("flag") or "", bool(body.get("value")))
    return {"ok": True}


@route("POST", "/api/merch/sales/{sid}/cancel", role="admin")
def merch_cancel(con, h, user, body, q, sid):
    merch.cancel(con, int(sid))
    return {"ok": True}


@route("POST", "/api/shift/report")
def shift_report(con, h, user, body, q):
    merch.report_shift(con, user, int(body.get("branch_id") or 0), bool(body.get("sold")))
    return {"ok": True}


def px(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except planner.PlanError as e:
        raise ApiError(str(e))


@route("GET", "/api/planner")
def planner_view(con, h, user, body, q):
    return planner.view(con, user)


@route("POST", "/api/planner/tasks")
def planner_add(con, h, user, body, q):
    return {"id": px(planner.add, con, user, body)}


@route("GET", "/api/planner/week")
def planner_week(con, h, user, body, q):
    from datetime import date as _d
    return px(planner.week, con, user, q.get("start") or _d.today().isoformat(), int(q["user"]) if q.get("user") else None)


@route("POST", "/api/planner/tasks/{tid}/move")
def planner_move(con, h, user, body, q, tid):
    px(planner.move, con, int(tid), user, body.get("date") or "")
    return {"ok": True}


@route("POST", "/api/planner/tasks/{tid}/reopen")
def planner_reopen(con, h, user, body, q, tid):
    px(planner.reopen, con, int(tid), user)
    return {"ok": True}


@route("POST", "/api/planner/tasks/{tid}/cancel")
def planner_cancel(con, h, user, body, q, tid):
    px(planner.cancel, con, int(tid), user)
    return {"ok": True}


@route("POST", "/api/planner/tasks/{tid}/return")
def planner_return(con, h, user, body, q, tid):
    px(planner.return_task, con, int(tid), user, body.get("note") or "")
    return {"ok": True}


@route("POST", "/api/planner/recurring/{rid}/stop")
def planner_stop(con, h, user, body, q, rid):
    px(planner.stop_recurring, con, int(rid), user)
    return {"ok": True}


@route("PUT", "/api/planner/settings", role="admin")
def planner_settings(con, h, user, body, q):
    px(planner.save_settings, con, body)
    return {"ok": True}


@route("PUT", "/api/users/{uid}/birth")
def user_birth(con, h, user, body, q, uid):
    if not planner.can_manage(con, user) and int(uid) != user["id"]:
        raise ApiError("Нет доступа", 403)
    px(planner.set_birth, con, int(uid), body.get("birth") or "")
    return {"ok": True}


def ex(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except expenses.ExpError as e:
        raise ApiError(str(e))


@route("GET", "/api/bills", role="admin")
def bills_get(con, h, user, body, q):
    return expenses.bills_view(con)


@route("POST", "/api/bills", role="admin")
def bills_add(con, h, user, body, q):
    return {"id": ex(expenses.save_bill, con, body)}


@route("PUT", "/api/bills/{bid}", role="admin")
def bills_put(con, h, user, body, q, bid):
    return {"id": ex(expenses.save_bill, con, body, int(bid))}


@route("POST", "/api/bills/{bid}/pay", role="admin")
def bills_pay(con, h, user, body, q, bid):
    return {"next_due": ex(expenses.pay_bill, con, int(bid), user, body.get("amount"))}


@route("POST", "/api/bills/{bid}/archive", role="admin")
def bills_archive(con, h, user, body, q, bid):
    expenses.archive_bill(con, int(bid))
    return {"ok": True}


@route("GET", "/api/ads", role="admin")
def ads_get(con, h, user, body, q):
    return expenses.ads_view(con)


@route("POST", "/api/ads/places", role="admin")
def ads_place_add(con, h, user, body, q):
    return {"id": ex(expenses.save_place, con, body)}


@route("PUT", "/api/ads/places/{pid}", role="admin")
def ads_place_put(con, h, user, body, q, pid):
    return {"id": ex(expenses.save_place, con, body, int(pid))}


@route("POST", "/api/ads/deals", role="admin")
def ads_deal_add(con, h, user, body, q):
    return {"id": ex(expenses.add_deal, con, body)}


@route("POST", "/api/ads/posts/{pid}", role="admin")
def ads_post_mark(con, h, user, body, q, pid):
    ex(expenses.mark_post, con, int(pid), body.get("link") or "", body.get("status") or "posted", body.get("planned") or "")
    return {"ok": True}


@route("GET", "/api/offers", role="operator")
def offers_list(con, h, user, body, q):
    return {"programs": offers.listing(con, base_url(h))}


@route("POST", "/api/offers", role="admin")
def offers_upload(con, h, user, body, q):
    try:
        oid = offers.upload(con, body, user)
    except offers.OfferError as e:
        raise ApiError(str(e))
    sv.audit(con, user, "offer.upload", str(oid))
    return {"id": oid}


@route("POST", "/api/offers/{oid}/archive", role="admin")
def offers_archive(con, h, user, body, q, oid):
    offers.archive(con, int(oid), bool(body.get("archived", True)))
    return {"ok": True}


@route("POST", "/api/fake-clients", role="admin")
def fake_add(con, h, user, body, q):
    n = max(1, min(500, int(body.get("count") or 150)))
    try:
        r = fakegen.generate(con, n, user)
    except ValueError as e:
        raise ApiError(str(e))
    sv.audit(con, user, "fake.add", str(n))
    return r


@route("POST", "/api/fake-clients/subscribe", role="admin")
def fake_subscribe(con, h, user, body, q):
    return fakegen.subscribe_all(con, user)


@route("POST", "/api/fake-clients/calls", role="admin")
def fake_calls(con, h, user, body, q):
    try:
        return fakegen.fake_calls(con, user)
    except ValueError as e:
        raise ApiError(str(e))


@route("POST", "/api/fake-clients/realistic", role="admin")
def fake_realistic(con, h, user, body, q):
    try:
        r = fakegen.realistic(con, user, start_members=int(body.get("start") or 150))
        r["calls"] = fakegen.fake_calls(con, user)["calls"] if con.execute("SELECT 1 FROM users WHERE role='operator' AND active=1").fetchone() else 0
        return r
    except ValueError as e:
        raise ApiError(str(e))


@route("POST", "/api/fake-clients/remove", role="admin")
def fake_remove(con, h, user, body, q):
    n = fakegen.remove(con)
    sv.audit(con, user, "fake.remove", str(n))
    return {"removed": n}


@route("GET", "/api/schedule/week")
def schedule_week(con, h, user, body, q):
    return schedule.week(con)


@route("POST", "/api/students/{sid}/archive", role="operator")
def student_archive(con, h, user, body, q, sid):
    sv.get_student_row(con, int(sid))
    schedule.set_student_archived(con, int(sid), bool(body.get("archived")), user)
    return {"ok": True}


@route("GET", "/api/archive", role="admin")
def archive(con, h, user, body, q):
    return schedule.archive_list(con)


@route("GET", "/api/sources", role="operator")
def sources_list(con, h, user, body, q):
    return sources.listing(con, active_only=q.get("all") != "1")


@route("POST", "/api/sources", role="admin")
def sources_add(con, h, user, body, q):
    if not (body.get("name") or "").strip():
        raise ApiError("Укажите название источника")
    return {"id": sources.ensure_source(con, body.get("channel") or "Другое", body["name"])}


@route("POST", "/api/sources/import", role="admin")
def sources_import(con, h, user, body, q):
    return {"added": sources.import_text(con, body.get("text") or "")}


@route("POST", "/api/sources/{sid}/archive", role="admin")
def sources_archive(con, h, user, body, q, sid):
    sources.set_active(con, int(sid), bool(body.get("active")))
    return {"ok": True}


@route("GET", "/api/reports/sources", role="admin")
def sources_report(con, h, user, body, q):
    from datetime import date as _d
    start = q.get("from") or _d.today().replace(day=1).isoformat()
    return sources.report(con, start, q.get("to") or _d.today().isoformat()) | {"from": start}


@route("GET", "/api/discounts", role="operator")
def discounts_list(con, h, user, body, q):
    return money.discounts(con, active_only=q.get("all") != "1")


@route("POST", "/api/discounts", role="admin")
def discounts_save(con, h, user, body, q):
    mx(money.save_discount, con, body)
    return {"ok": True}


@route("POST", "/api/discounts/{did}/archive", role="admin")
def discounts_archive(con, h, user, body, q, did):
    money.archive_discount(con, int(did), bool(body.get("active")))
    return {"ok": True}


@route("POST", "/api/subscriptions/{sub}/cancel", role="operator")
def cancel(con, h, user, body, q, sub):
    sv.cancel_subscription(con, int(sub), user)
    return {"ok": True}


@route("POST", "/api/students/{sid}/makeups", role="operator")
def makeup_book(con, h, user, body, q, sid):
    return sv.book_makeup(con, int(sid), int(body.get("group_id") or 0), body.get("date") or "",
                          body.get("absence_id"), user)


@route("POST", "/api/makeups/{mid}/status")
def makeup_status(con, h, user, body, q, mid):
    m = con.execute("SELECT group_id FROM makeups WHERE id=?", (int(mid),)).fetchone()
    if m:
        coach_guard(con, user, m["group_id"])
    sv.set_makeup_status(con, int(mid), body.get("status"), user)
    return {"ok": True}


@route("GET", "/api/journal")
def journal(con, h, user, body, q):
    coach_guard(con, user, int(q.get("group_id") or 0))
    j = sv.journal(con, int(q.get("group_id") or 0), q.get("date") or sv.today().isoformat())
    from . import pu
    from datetime import datetime as _dt
    if j["has_lesson"]:
        g = con.execute("SELECT * FROM groups WHERE id=?", (j["group"]["id"],)).fetchone()
        end = pu._lesson_end(con, g, _dt.fromisoformat(j["date"]).date())
        n = pu._unmarked(j["rows"])
        if end and n and _dt.now() >= end + __import__("datetime").timedelta(minutes=30):
            j["overdue_marks"] = {"missing": n, "minutes": int((_dt.now() - end).total_seconds() // 60)}
    return j


@route("POST", "/api/attendance")
def attendance(con, h, user, body, q):
    coach_guard(con, user, int(body.get("group_id") or 0))
    sv.mark_attendance(con, int(body.get("student_id") or 0), int(body.get("group_id") or 0),
                       body.get("date") or "", body.get("status") or "", user)
    return {"ok": True}


@route("GET", "/api/coach/today")
def coach_today(con, h, user, body, q):
    cid = user["id"] if user["role"] == "coach" else int(q.get("coach_id") or 0)
    out = coaches.coach_today(con, cid)
    out["checkins"] = staff.my_day(con, cid)
    for les in out["today"]:
        les["staff"] = staff.lesson_staff(con, les["id"], out["date"])
    out["coaches"] = [dict(r) for r in con.execute("SELECT id, name FROM users WHERE role='coach' AND active=1 ORDER BY name")]
    out["merch"] = merch.my_shift(con, cid, out["date"])
    out["tasks"] = [tasks._task_out(con, r) for r in con.execute(
        "SELECT * FROM tasks WHERE status='open' AND assignee_id=? AND due_at<=? ORDER BY due_at", (cid, out["date"] + "T23:59"))]
    from . import pu
    out["missing_marks"] = [m for m in pu.missing_marks(con) if m["coach_id"] == cid]
    return out


@route("GET", "/api/coaches/board")
def coaches_board(con, h, user, body, q):
    me = user["id"] if user["role"] == "coach" else (int(q["as_coach"]) if q.get("as_coach") else None)
    return coaches.leaderboard(con, q.get("month"), me)


@route("GET", "/api/analytics", role="admin")
def analytics_report(con, h, user, body, q):
    from . import analytics
    return analytics.report(con, q.get("month"), int(q["branch_id"]) if q.get("branch_id") else None, q.get("part") or "groups")


@route("GET", "/api/salary")
def salary_report(con, h, user, body, q):
    """Зарплата тренеров за месяц. Тренер видит только свою."""
    from . import salary
    if user["role"] == "coach":
        return salary.payroll(con, q.get("month"), None, user["id"])
    if user["role"] != "admin":
        raise ApiError("Нет доступа", 403)
    return salary.payroll(con, q.get("month"), int(q["branch_id"]) if q.get("branch_id") else None)


@route("GET", "/api/dashboard", role="admin")
def dashboard(con, h, user, body, q):
    return sv.dashboard(con, q.get("month"), int(q["branch_id"]) if q.get("branch_id") else None)


@route("POST", "/api/students/{sid}/trial")
def trial_mark(con, h, user, body, q, sid):
    s = sv.get_student_row(con, int(sid))
    if s["group_id"]:
        coach_guard(con, user, s["group_id"])
    sv.mark_trial(con, int(sid), bool(body.get("came")), user)
    return {"ok": True}


@route("POST", "/api/demo/reset", role="admin")
def demo_reset(con, h, user, body, q):
    if os.environ.get("CRM_DEMO", "1") != "1":
        raise ApiError("Тестовый режим выключен — стирать данные нельзя", 403)
    db.reset_demo(con)
    con.execute("COMMIT")
    db.seed_demo(con)
    con.execute("BEGIN")
    comms.seed_demo(con)
    coaches.seed_demo_coaches(con)
    money.init(con)
    ai.seed_demo(con)
    tasks.seed_demo(con)
    return {"ok": True}


@route("PUT", "/api/settings", role="admin")
def settings_put(con, h, user, body, q):
    allowed = set(db.DEFAULT_SETTINGS)
    for k, v in body.items():
        if k in allowed:
            con.execute("UPDATE settings SET value=? WHERE key=?", (str(v), k))
    sv.audit(con, user, "settings.update", json.dumps(body, ensure_ascii=False))
    return db.settings(con)


# ---------------- связь: переписки, звонки, интеграции ----------------

@route("GET", "/api/inbox", role="operator")
def inbox(con, h, user, body, q):
    return comms.inbox(con, q.get("filter", "all"), q.get("q", ""))


@route("GET", "/api/inbox/counts", role="operator")
def inbox_counts(con, h, user, body, q):
    return comms.counts(con)


@route("GET", "/api/conversations/{cid}", role="operator")
def conv_get(con, h, user, body, q, cid):
    return cx(comms.conversation, con, int(cid))


@route("POST", "/api/conversations/{cid}/send", role="operator")
def conv_send(con, h, user, body, q, cid):
    return cx(comms.send, con, int(cid), body.get("text") or "", user)


@route("POST", "/api/conversations/{cid}/link", role="operator")
def conv_link(con, h, user, body, q, cid):
    cx(comms.link, con, int(cid), body.get("family_id"))
    return {"ok": True}


@route("POST", "/api/students/{sid}/program", role="operator")
def student_program(con, h, user, body, q, sid):
    from . import program
    return program.set_plan(con, int(sid), int(body.get("program_id") or 0), body.get("tariff") or "",
                            int(body["ofp_group_id"]) if body.get("ofp_group_id") else None, user)


@route("GET", "/api/programs", role="operator")
def programs_list(con, h, user, body, q):
    from . import program
    return {"programs": program.info(con)}


@route("POST", "/api/programs/{pid}/prices", role="admin")
def program_price(con, h, user, body, q, pid):
    from . import program
    program.save_price(con, int(pid), body)
    return {"ok": True}


@route("PATCH", "/api/programs/{pid}", role="admin")
def program_patch(con, h, user, body, q, pid):
    from . import program
    program.save_program(con, int(pid), body)
    return {"ok": True}


@route("POST", "/api/students/{sid}/waitlist", role="operator")
def waitlist_add(con, h, user, body, q, sid):
    from . import waitlist
    return {"id": waitlist.add(con, int(sid), int(body.get("group_id") or 0), body.get("month"), body, user)}


@route("POST", "/api/waitlist/{wid}/status", role="operator")
def waitlist_status(con, h, user, body, q, wid):
    from . import waitlist
    waitlist.set_status(con, int(wid), body.get("status") or "", user)
    return {"ok": True}


@route("GET", "/api/waitlist/funnel", role="operator")
def waitlist_funnel(con, h, user, body, q):
    from . import waitlist
    return waitlist.funnel(con, force=bool(q.get("force"))) or {}


@route("GET", "/api/trial-offer", role="operator")
def trial_offer(con, h, user, body, q):
    from . import trialoffer
    age = int(q["age"]) if str(q.get("age") or "").isdigit() else None
    child = q.get("child") or ""
    bid = int(q["branch_id"]) if q.get("branch_id") else None
    if q.get("student_id"):
        s = con.execute("SELECT name, birth, branch_id, group_id FROM students WHERE id=?", (int(q["student_id"]),)).fetchone()
        if s:
            if bid is None and s["branch_id"] and not s["group_id"]:
                bid = s["branch_id"]
            child = child or s["name"]
            if age is None:
                age = trialoffer.years(s["birth"])
    return trialoffer.offer(con, age, child, bid)


@route("POST", "/api/students/{sid}/cancel-trial", role="operator")
def cancel_trial(con, h, user, body, q, sid):
    from . import pu
    pu.cancel_trial(con, int(sid), body.get("reason") or "", user)
    return {"ok": True}


@route("POST", "/api/tasks/{tid}/rebook", role="operator")
def task_rebook(con, h, user, body, q, tid):
    from . import pu
    return pu.complete(con, int(tid), body.get("result") or "", body, user)


@route("POST", "/api/tasks/{tid}/assign", role="operator")
def task_assign(con, h, user, body, q, tid):
    from . import pu
    pu.reassign(con, int(tid), int(body.get("user_id") or 0), user)
    return {"ok": True}


@route("GET", "/api/pu/task/{tid}", role="operator")
def pu_task(con, h, user, body, q, tid):
    from . import pu
    t = con.execute("SELECT * FROM tasks WHERE id=?", (int(tid),)).fetchone()
    if not t:
        raise ApiError("Задача не найдена", 404)
    kids = pu._family_kids(con, t["family_id"]) if t["family_id"] else []
    if not kids and t["student_id"]:
        kids = [dict(con.execute("SELECT id, name, group_id FROM students WHERE id=?", (t["student_id"],)).fetchone())]
    for k in kids:
        k.update({"misses": pu.card_info(con, k["id"])["misses"], "birth": con.execute("SELECT birth FROM students WHERE id=?", (k["id"],)).fetchone()[0]})
    fam = con.execute("SELECT name FROM families WHERE id=?", (t["family_id"],)).fetchone() if t["family_id"] else None
    staff_ = [dict(r) for r in con.execute("SELECT id, name FROM users WHERE active=1 AND role IN ('admin','operator') ORDER BY name")]
    return {"task": dict(t), "kids": kids, "family": fam[0] if fam else "", "refusals": pu.REFUSALS, "staff": staff_,
            "max_later": (__import__("datetime").date.today() + __import__("datetime").timedelta(days=14)).isoformat()}


@route("GET", "/api/pu/report", role="operator")
def pu_report(con, h, user, body, q):
    from . import pu
    from datetime import date as _d
    ym = q.get("month") or _d.today().isoformat()[:7]
    a, b = logic.month_bounds(ym)
    return pu.report(con, a.isoformat(), b.isoformat(), int(q["branch_id"]) if q.get("branch_id") else None) | {
        "marks": pu.marks_report(con, a, min(b, _d.today()))}


@route("GET", "/api/alerts", role="operator")
def alerts_list(con, h, user, body, q):
    from . import pu
    return {"items": pu.open_alerts(con), "missing_marks": pu.missing_marks(con)}


@route("POST", "/api/alerts/{aid}/close", role="operator")
def alert_close(con, h, user, body, q, aid):
    from . import pu
    pu.close_alert(con, int(aid), user)
    return {"ok": True}


@route("GET", "/api/responsibles", role="operator")
def responsibles(con, h, user, body, q):
    from . import pu
    return {"items": pu.responsibles(con), "staff": [dict(r) for r in con.execute(
        "SELECT id, name, role FROM users WHERE active=1 AND role IN ('admin','operator') ORDER BY role, name")]}


@route("POST", "/api/responsibles", role="admin")
def responsibles_set(con, h, user, body, q):
    from . import pu
    uid = int(body["user_id"]) if body.get("user_id") else None
    if uid and not con.execute("SELECT 1 FROM users WHERE id=? AND role IN ('admin','operator')", (uid,)).fetchone():
        raise ApiError("Ответственным может быть только админ или оператор")
    pu.set_responsible(con, int(body.get("branch_id") or 0), uid)
    return {"ok": True}


@route("GET", "/api/closures", role="operator")
def closures_list(con, h, user, body, q):
    from . import closures
    return {"items": closures.listing(con)}


@route("POST", "/api/closures", role="admin")
def closures_add(con, h, user, body, q):
    from . import closures
    try:
        return {"id": closures.add(con, body, user)}
    except closures.ClosureError as e:
        raise ApiError(str(e))


@route("DELETE", "/api/closures/{cid}", role="admin")
def closures_del(con, h, user, body, q, cid):
    from . import closures
    closures.remove(con, int(cid))
    return {"ok": True}


@route("GET", "/api/families/{fid}/lessons", role="operator")
def family_lessons(con, h, user, body, q, fid):
    from . import capacity
    return {"kids": capacity.family_lessons(con, int(fid))}


@route("POST", "/api/students/{sid}/warn", role="operator")
def student_warn(con, h, user, body, q, sid):
    from . import capacity
    gid = int(body["group_id"]) if body.get("group_id") else None
    if body.get("undo"):
        capacity.unwarn(con, int(sid), body.get("date") or "", gid or 0, user)
        return {"ok": True}
    return capacity.warn(con, int(sid), body.get("date") or "", gid, body.get("note") or "", user)


@route("GET", "/api/free-slots", role="operator")
def free_slots(con, h, user, body, q):
    from . import capacity
    return {"slots": capacity.free_slots(con, int(q.get("days") or 14), int(q["branch_id"]) if q.get("branch_id") else None)}


@route("GET", "/api/occupancy", role="operator")
def occupancy(con, h, user, body, q):
    from . import capacity
    return capacity.occupancy(con, int(q.get("group_id") or 0), q.get("date") or "")


@route("GET", "/api/families/{fid}/comms", role="operator")
def family_comms(con, h, user, body, q, fid):
    return comms.family_comms(con, int(fid))


@route("POST", "/api/families/{fid}/whatsapp", role="operator")
def family_whatsapp(con, h, user, body, q, fid):
    p = con.execute("SELECT phone FROM parents WHERE family_id=? AND id=?", (int(fid), int(body.get("parent_id") or 0))).fetchone()
    if not p:
        raise ApiError("Родитель не найден")
    cid = cx(comms.start_whatsapp, con, p[0])
    comms.link(con, cid, int(fid))
    return cx(comms.send, con, cid, body.get("text") or "", user)


@route("GET", "/api/messages/{mid}/media", role="operator")
def message_media(con, h, user, body, q, mid):
    return Raw(*cx(comms.media_for, con, int(mid)))


@route("POST", "/api/call", role="operator")
def make_call(con, h, user, body, q):
    sip = con.execute("SELECT sip_number FROM users WHERE id=?", (user["id"],)).fetchone()[0]
    return cx(comms.sipuni_call, con, body.get("phone") or "", sip)


@route("GET", "/api/calls", role="operator")
def calls(con, h, user, body, q):
    return comms.calls_list(con, q.get("filter", "all"))


@route("GET", "/api/calls/analytics", role="operator")
def calls_analytics(con, h, user, body, q):
    return callstats.analytics(con, q.get("period", "week"))


@route("POST", "/api/calls/seen", role="operator")
def calls_seen(con, h, user, body, q):
    comms.mark_calls_seen(con)
    return {"ok": True}


@route("POST", "/api/calls/{cid}/seen", role="operator")
def call_seen(con, h, user, body, q, cid):
    r = con.execute("SELECT phone_norm FROM calls WHERE id=?", (int(cid),)).fetchone()
    if r:
        con.execute("UPDATE calls SET seen=1 WHERE phone_norm=? AND seen=0", (r[0],))
    return {"ok": True}


@route("GET", "/api/calls/{cid}/ai", role="operator")
def call_ai(con, h, user, body, q, cid):
    return cx(ai.call_ai, con, int(cid))


@route("GET", "/api/calls/digests", role="operator")
def call_digests(con, h, user, body, q):
    return {"items": calldigest.listing(con)}


@route("GET", "/api/calls/digest", role="operator")
def call_digest(con, h, user, body, q):
    d = calldigest.get(con, q.get("day") or "", q.get("operator") or "")
    if not d:
        raise ApiError("Сводки за этот день нет", 404)
    names = {r[0]: r[1] for r in con.execute("SELECT sip_number, name FROM users WHERE sip_number != ''")}
    d["name"] = names.get(d["operator"], f"Номер {d['operator']}")
    return d


@route("POST", "/api/calls/digest", role="admin")
def call_digest_build(con, h, user, body, q):
    d = calldigest.build(con, body.get("day") or "", body.get("operator") or "")
    if not d:
        raise ApiError("За этот день у оператора нет разобранных звонков")
    return d


@route("POST", "/api/calls/{cid}/ai/retry", role="operator")
def call_ai_retry(con, h, user, body, q, cid):
    ai.retry(con, int(cid))
    return {"ok": True}


@route("POST", "/api/integrations/yandex/test", role="admin")
def int_yandex(con, h, user, body, q):
    return cx(ai.test_connection, con)


@route("GET", "/api/calls/{cid}/record", role="operator")
def call_record(con, h, user, body, q, cid):
    return Raw(*cx(comms.record_for, con, int(cid)))


@route("GET", "/api/integrations", role="admin")
def integrations_get(con, h, user, body, q):
    comms.put(con, "public_url", base_url(h))
    return comms.admin_view(con, base_url(h))


@route("GET", "/api/legal", role="admin")
def legal_get(con, h, user, body, q):
    comms.put(con, "public_url", base_url(h))
    return legal.admin_view(con, base_url(h))


@route("PUT", "/api/legal", role="admin")
def legal_put(con, h, user, body, q):
    legal.save(con, body)
    sv.audit(con, user, "legal.update", ",".join(sorted(body)))
    return legal.admin_view(con, base_url(h))


@route("POST", "/api/families/{fid}/consent", role="operator")
def family_consent(con, h, user, body, q, fid):
    mx(legal.set_consent, con, int(fid), body.get("kind") or "", bool(body.get("value")), body.get("how") or "", user)
    return {"ok": True}


@route("PUT", "/api/integrations", role="admin")
def integrations_put(con, h, user, body, q):
    comms.save_settings(con, body)
    sv.audit(con, user, "integrations.update", ",".join(sorted(k for k in body if k in comms.EDITABLE_KEYS)))
    return comms.admin_view(con, base_url(h))


@route("POST", "/api/integrations/telegram/connect", role="admin")
def int_tg(con, h, user, body, q):
    return cx(comms.tg_connect, con, base_url(h))


@route("POST", "/api/integrations/max/connect", role="admin")
def int_max(con, h, user, body, q):
    return cx(comms.max_connect, con, base_url(h))


@route("POST", "/api/integrations/wazzup/connect", role="admin")
def int_wz(con, h, user, body, q):
    return cx(comms.wz_connect, con, base_url(h))


@route("GET", "/api/integrations/log", role="admin")
def int_log(con, h, user, body, q):
    return [dict(r) for r in con.execute("SELECT * FROM hook_log ORDER BY id DESC LIMIT 30")]


HOOKS = {"sipuni", "telegram", "max", "wazzup", "lead"}


def handle_hook(h, method: str, source: str, token: str, url) -> None:
    """Входящие события от сервисов. Отвечаем быстро и почти всегда 200, чтобы сервис не слал повторно."""
    params = {k: v[0] for k, v in parse_qs(url.query).items()}
    raw = b""
    if method == "POST":
        n = int(h.headers.get("Content-Length") or 0)
        if n > 2_000_000:
            return h._send(413, {"error": "too large"})
        raw = h.rfile.read(n) if n else b""
    ctype = h.headers.get("Content-Type") or ""
    body = {}
    if raw:
        try:
            if "json" in ctype or raw[:1] in (b"{", b"["):
                body = json.loads(raw)
            else:
                body = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
        except (ValueError, UnicodeDecodeError):
            return h._send(400, {"error": "bad body"})
    if isinstance(body, dict) and body.get("test") is True and len(body) == 1:
        return h._send(200, {"ok": True})  # проверка адреса от Wazzup
    con = db.get_conn()
    if source not in HOOKS or not secrets.compare_digest(token, comms.get(con, "hook_token") or "-"):
        return h._send(403, {"error": "forbidden"})
    payload = (params | body) if isinstance(body, dict) else {"items": body, **params}
    result = {"ok": True, "success": True}  # Sipuni требует поле success
    con.execute("BEGIN IMMEDIATE")
    try:
        if source == "sipuni":
            comms.sipuni_hook(con, payload)
        elif source == "telegram":
            comms.tg_hook(con, payload, h.headers)
        elif source == "max":
            comms.max_hook(con, payload, h.headers)
        elif source == "wazzup":
            comms.wz_hook(con, payload)
        elif source == "lead":
            result = comms.lead_hook(con, payload, sv.create_student)
        if source in ("sipuni", "lead", "max", "wazzup"):
            comms.log_hook(con, source, payload)
        con.execute("COMMIT")
    except PermissionError as e:
        con.execute("ROLLBACK")
        return h._send(403, {"error": str(e)})
    except Exception as e:  # noqa: BLE001 — событие не должно ронять сервис
        con.execute("ROLLBACK")
        if not isinstance(e, comms.CommsError):
            traceback.print_exc()
        try:
            comms.log_hook(con, source, payload, f"{type(e).__name__}: {e}")
        except Exception:  # noqa: BLE001
            pass
        if source == "lead":
            return h._send(400, {"ok": False, "success": False, "error": str(e)})
        # остальным сервисам отвечаем «принято»: ошибку видно в журнале событий, а Sipuni не отключит отправку
    return h._send(200, result)


ROLE_RANK = {"coach": 1, "operator": 2, "admin": 3}
ROLE_NEED = {"any": 1, "operator": 2, "admin": 3}


class Handler(BaseHTTPRequestHandler):
    server_version = "AbonementPlus/0.2"

    def log_message(self, fmt, *args):
        if os.environ.get("CRM_QUIET") != "1":
            super().log_message(fmt, *args)

    def _send(self, status: int, payload, ctype="application/json; charset=utf-8"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        if ctype.startswith("application/json"):
            self.send_header("Cache-Control", "no-store")
        for k, v in getattr(self, "extra_headers", []):
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _send_raw(self, r: "Raw"):
        data, status = r.data, 200
        rng = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
        extra = [("Accept-Ranges", "bytes"), ("Cache-Control", "private, max-age=3600")]
        if getattr(r, "filename", None):
            from urllib.parse import quote as _q
            extra.append(("Content-Disposition", f"attachment; filename*=UTF-8''{_q(r.filename)}"))
        if rng and (rng.group(1) or rng.group(2)):
            total = len(data)
            if rng.group(1):
                start = int(rng.group(1))
                end = min(int(rng.group(2)), total - 1) if rng.group(2) else total - 1
            else:
                start, end = max(0, total - int(rng.group(2))), total - 1
            if start >= total:
                start, end = 0, total - 1
            data, status = data[start:end + 1], 206
            extra.append(("Content-Range", f"bytes {start}-{end}/{total}"))
        self.extra_headers = getattr(self, "extra_headers", []) + extra
        return self._send(status, data, r.ctype)

    def _static(self, path: str):
        if path in ("", "/"):
            path = "/index.html"
        full = os.path.normpath(os.path.join(STATIC, path.lstrip("/")))
        if not full.startswith(os.path.normpath(STATIC)) or not os.path.isfile(full):
            full = os.path.join(STATIC, "index.html")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        st = os.stat(full)
        etag = f'"{int(st.st_mtime)}-{st.st_size}"'
        fonts = full.endswith((".woff", ".woff2"))
        # html/js/css: всегда сверяемся с сервером, иначе Safari держит старую версию после обновления CRM
        self.extra_headers.append(("Cache-Control", "public, max-age=604800" if fonts else "no-cache"))
        self.extra_headers.append(("ETag", etag))
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            for k, v in self.extra_headers:
                self.send_header(k, v)
            self.end_headers()
            return
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype)

    def _handle(self, method: str):
        self.extra_headers = []
        url = urlparse(self.path)
        hm = re.match(r"^/hooks/([a-z]+)/([A-Za-z0-9_\-]+)/?$", url.path)
        if hm and method in ("GET", "POST"):
            try:
                return handle_hook(self, method, hm.group(1), hm.group(2), url)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                return self._send(500, {"error": "Ошибка сервера"})
        om = re.match(r"^/offers/(v\d+|[a-z0-9-]+)\.pdf$", url.path)
        if om and method in ("GET", "HEAD"):  # оферты публичные: по ссылке без входа
            con = db.get_conn()
            key = om.group(1)
            oid = int(key[1:]) if re.fullmatch(r"v\d+", key) else offers.current_id(con, key)
            f = offers.file(con, oid) if oid else None
            if not f:
                return self._send(404, b"Not found", "text/plain; charset=utf-8")
            from urllib.parse import quote as _q
            self.extra_headers.append(("Content-Disposition", f"inline; filename*=UTF-8''{_q(f[0])}"))
            self.extra_headers.append(("Cache-Control", "no-cache"))
            return self._send(200, f[1], "application/pdf")
        lm = re.match(r"^/legal(?:/([a-z]+))?/?$", url.path)
        if lm and method in ("GET", "HEAD"):
            con = db.get_conn()
            return self._send(200, legal.page(con, lm.group(1) or "", base_url(self)).encode(), "text/html; charset=utf-8")
        if not url.path.startswith("/api/"):
            if method in ("GET", "HEAD"):
                return self._static(url.path)
            return self._send(405, {"error": "Метод не поддерживается"})
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        con = db.get_conn()
        try:
            for m, rx, fn, role in ROUTES:
                if m != method:
                    continue
                mt = rx.match(url.path)
                if not mt:
                    continue
                body = {}
                if method in ("POST", "PUT", "PATCH", "DELETE"):
                    if method != "DELETE" and "application/json" not in (self.headers.get("Content-Type") or ""):
                        raise ApiError("Ожидается JSON", 415)
                    n = int(self.headers.get("Content-Length") or 0)
                    if n > (21_000_000 if url.path.startswith("/api/offers") else 1_000_000):
                        raise ApiError("Слишком большой запрос", 413)
                    if n:
                        body = json.loads(self.rfile.read(n) or b"{}")
                user = None
                if role != "public":
                    user = current_user(con, self)
                    if not user:
                        raise ApiError("Нужно войти", 401)
                    if ROLE_RANK.get(user["role"], 0) < ROLE_NEED.get(role, 1):
                        raise ApiError("Недостаточно прав", 403)
                con.execute("BEGIN" if method in ("GET", "HEAD") else "BEGIN IMMEDIATE")
                try:
                    result = fn(con, self, user, body, q, **mt.groupdict())
                    con.execute("COMMIT")
                except BaseException:
                    con.execute("ROLLBACK")
                    raise
                if isinstance(result, Raw):
                    return self._send_raw(result)
                return self._send(200, result)
            raise ApiError("Не найдено", 404)
        except ApiError as e:
            return self._send(e.status, {"error": e.message})
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            traceback.print_exc()
            return self._send(400, {"error": "Неверные данные запроса"})
        except Exception:
            traceback.print_exc()
            return self._send(500, {"error": "Ошибка сервера"})

    def do_GET(self):
        self._handle("GET")

    def do_HEAD(self):
        self._handle("HEAD")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_PATCH(self):
        self._handle("PATCH")

    def do_DELETE(self):
        self._handle("DELETE")


def main():
    con = db.connect()
    db.init_db(con)
    if os.environ.get("CRM_DEMO", "1") == "1":
        db.seed_demo(con)
        coaches.seed_demo_coaches(con)
        money.init(con)
        if not comms.status(con)["telegram"] and not comms.status(con)["sipuni"]:
            comms.seed_demo(con)
        ai.seed_demo(con)
        tasks.seed_demo(con)
    con.close()
    ai.start_worker(db.connect)
    tasks.start_worker(db.connect)
    host = os.environ.get("CRM_HOST", "127.0.0.1")
    port = int(os.environ.get("CRM_PORT", "8000"))
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    print(f"Абонемент Плюс запущен: http://{host}:{port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
