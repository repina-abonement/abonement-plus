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

from . import logic, perms, ai, calldigest, callstats, coaches, comms, db, expenses, fakegen, fiscal, legal, merch, offers, money, planner, schedule, services as sv, sources, staff, tasks
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
    r = con.execute("""SELECT u.id, u.login, u.name, u.role, u.is_senior, u.is_promoter, s.created_at FROM sessions s
                       JOIN users u ON u.id=s.user_id WHERE s.token=? AND u.active=1""", (tok.value,)).fetchone()
    if not r:
        return None
    if (datetime.now() - datetime.fromisoformat(r["created_at"])).days > SESSION_DAYS:
        con.execute("DELETE FROM sessions WHERE token=?", (tok.value,))
        return None
    return dict(r)


def _start_session(con, h, u) -> None:
    tok = secrets.token_urlsafe(32)
    con.execute("INSERT INTO sessions(token, user_id, created_at) VALUES (?,?,?)", (tok, u["id"], db.now_iso()))
    sv.audit(con, dict(u), "login")
    cookie = f"crm_session={tok}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_DAYS * 86400}"
    if SECURE_COOKIE:
        cookie += "; Secure"
    h.extra_headers.append(("Set-Cookie", cookie))


@route("POST", "/api/login", role="public")
def login(con, h, user, body, q):
    u = con.execute("SELECT * FROM users WHERE login=? AND active=1", ((body.get("login") or "").strip(),)).fetchone()
    if not u or not db.check_password(body.get("password") or "", u["pw_hash"]):
        raise ApiError("Неверный логин или пароль", 401)
    _start_session(con, h, u)
    return {"ok": True, "user": {"name": u["name"], "role": u["role"]}}


# ---------------- быстрый вход по коду ----------------

@route("GET", "/api/pin/hello", role="public")
def pin_hello(con, h, user, body, q):
    from . import pin
    return pin.hello(con, q.get("device") or "") or {}


@route("POST", "/api/pin/login", role="public")
def pin_login(con, h, user, body, q):
    from . import pin
    u, err = pin.login(con, body.get("device") or "", str(body.get("pin") or ""))
    if err:
        return {"ok": False} | err
    _start_session(con, h, u)
    sv.audit(con, u, "login.pin")
    return {"ok": True}


@route("GET", "/api/pin", role="all")
def pin_status(con, h, user, body, q):
    from . import pin
    return {"has_pin": pin.has_pin(con, user, q.get("device") or "")}


@route("POST", "/api/pin", role="all")
def pin_set(con, h, user, body, q):
    from . import pin
    if body.get("remove"):
        pin.remove(con, user, body.get("device") or "")
        return {"ok": True}
    pin.set_pin(con, user, body.get("device") or "", str(body.get("pin") or ""))
    sv.audit(con, user, "pin.set")
    return {"ok": True}


@route("POST", "/api/logout", role="all")
def logout(con, h, user, body, q):
    c = SimpleCookie(h.headers.get("Cookie") or "")
    if c.get("crm_session"):
        con.execute("DELETE FROM sessions WHERE token=?", (c["crm_session"].value,))
    h.extra_headers.append(("Set-Cookie", "crm_session=; Path=/; Max-Age=0"))
    return {"ok": True}


@route("GET", "/api/me", role="all")
def me(con, h, user, body, q):
    return user | {"perms": perms.view_of(user), "title": perms.title(user)}


@route("POST", "/api/me/password", role="all")
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


@route("GET", "/api/users", role="admin|schedule:full")  # старшему администратору: для смен и тренеров групп
def users_list(con, h, user, body, q):
    ucols = {r[1] for r in con.execute("PRAGMA table_info(users)")}
    ph = "phone" if "phone" in ucols else "'' AS phone"
    out = [dict(r) for r in con.execute(f"""SELECT id, login, name, role, is_senior, is_promoter, active, sip_number, tg_username, {ph}, photo_at
                                            FROM users ORDER BY id""")]
    return [u | {"title": perms.title(u)} for u in out]


def set_post(con, uid: int, post: str | None, promoter=None):
    """Должность из Настроек: owner | senior | operator | coach | lawyer (+ отметка промоутера)."""
    roles = {"owner": ("admin", 0), "senior": ("operator", 1), "operator": ("operator", 0), "coach": ("coach", 0), "lawyer": ("lawyer", 0)}
    if post:
        if post not in roles:
            raise ApiError("Неизвестная должность")
        role, senior = roles[post]
        con.execute("UPDATE users SET role=?, is_senior=? WHERE id=?", (role, senior, uid))
        if role != "coach":
            con.execute("UPDATE groups SET coach_id=NULL WHERE coach_id=?", (uid,))
            con.execute("UPDATE groups SET assistant_id=NULL WHERE assistant_id=?", (uid,))
        con.execute("DELETE FROM sessions WHERE user_id=?", (uid,))  # права меняются сразу: войдёт заново
    if promoter is not None:
        con.execute("UPDATE users SET is_promoter=? WHERE id=?", (1 if promoter else 0, uid))


# ---------------- фото и анкета сотрудника ----------------

@route("GET", "/api/team", role="team")
def team_view(con, h, user, body, q):
    from . import team
    v = team.view(con)
    if not perms.can(user, "pay", "full"):  # модель оплаты: каждый видит только свою
        rs = perms.roles_of(user)
        v["pay"] = {k: t for k, t in v["pay"].items() if (k == "admins" and rs & {"senior", "operator"}) or (k == "promoters" and "promoter" in rs)}
        if "coach" not in rs:
            v.pop("coach_pay", None)
    return v


@route("PUT", "/api/team", role="admin")
def team_save(con, h, user, body, q):
    from . import team
    team.save(con, body)
    return {"ok": True}


@route("GET", "/api/users/{uid}/photo", role="all")
def user_photo(con, h, user, body, q, uid):
    from . import profiles
    p = profiles.read_photo(int(uid))
    if not p:
        raise ApiError("Фото нет", 404)
    return Raw(p[0], p[1])


@route("POST", "/api/users/{uid}/photo", role="admin")
def user_photo_set(con, h, user, body, q, uid):
    from . import profiles
    import base64
    if body.get("remove"):
        profiles.remove_photo(con, int(uid))
    elif body.get("url"):
        profiles.photo_from_url(con, int(uid), str(body["url"]))
    elif body.get("data"):
        raw = str(body["data"]).split(",", 1)[-1]
        try:
            profiles.save_photo(con, int(uid), base64.b64decode(raw), bool(body.get("square")))
        except ValueError:
            raise ApiError("Не удалось прочитать фото")
    sv.audit(con, user, "user.photo", str(uid))
    return {"ok": True}


@route("GET", "/api/users/{uid}/profile", role="all")
def user_profile(con, h, user, body, q, uid):
    from . import profiles
    p = profiles.profile(con, int(uid))
    if not perms.allowed(user, "operator") and user["id"] != int(uid):
        p["phone"] = ""
    return p


@route("PUT", "/api/users/{uid}/profile", role="all")
def user_profile_save(con, h, user, body, q, uid):
    from . import profiles
    if user["role"] != "admin" and user["id"] != int(uid):
        raise ApiError("Нет доступа", 403)
    profiles.save_bio(con, int(uid), body.get("bio") or {})
    return {"ok": True}


@route("PATCH", "/api/users/{uid}", role="admin")
def users_patch(con, h, user, body, q, uid):
    if body.get("post") or "is_promoter" in body:
        if int(uid) == user["id"] and body.get("post") not in (None, "owner"):
            raise ApiError("Нельзя снять с себя должность руководителя")
        set_post(con, int(uid), body.get("post"), body.get("is_promoter"))
        sv.audit(con, user, "user.post", f"{uid}:{body.get('post')}:{body.get('is_promoter')}")
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
        from . import pin
        pin.remove_all(con, int(uid))
        sv.audit(con, user, "user.password", str(uid))
    if "sip_number" in body:
        sip = "".join(ch for ch in str(body["sip_number"] or "") if ch.isdigit())[:10]
        con.execute("UPDATE users SET sip_number=? WHERE id=?", (sip, int(uid)))
    if body.get("name"):
        nm = " ".join(str(body["name"]).split())[:80]
        if len(nm) < 2:
            raise ApiError("Укажите имя")
        con.execute("UPDATE users SET name=? WHERE id=?", (nm, int(uid)))
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


@route("GET", "/api/export/all", role="export")
def export_all(con, h, user, body, q):
    """Вся база понятными таблицами Excel: дети и родители, абонементы, оплаты, посещения, группы, сотрудники."""
    from . import fullexport
    data, name = fullexport.build(con)
    sv.audit(con, user, "export.all", name)
    return Raw(data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", name)


@route("POST", "/api/backup", role="admin")
def backup_save(con, h, user, body, q):
    from . import backup, comms
    if "yadisk_token" in body:
        comms.put(con, "backup_yadisk_token", (body.get("yadisk_token") or "").strip())
        sv.audit(con, user, "backup.token", "")
    if body.get("run"):
        con.execute("COMMIT")  # копия должна включать всё, что уже сохранено
        try:
            r = backup.run(con)
            r["excel"] = backup.excel_snapshot(con)
        finally:
            con.execute("BEGIN")  # обработчик запроса сам закроет транзакцию
        return backup.status(con) | {"result": r}
    return backup.status(con)


@route("POST", "/api/users/bulk", role="admin")
def users_bulk(con, h, user, body, q):
    """Завести команду списком: «Имя Фамилия; роль; телефон» по строке. Пароли генерируются и показываются один раз."""
    import secrets
    from . import schedule
    alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    roles = {"оператор": "operator", "админ": "operator", "администратор": "operator", "тренер": "coach", "контролёр": "operator",
             "контролер": "operator", "руководитель": "admin", "юрист": "lawyer", "старший администратор": "operator",
             "старший админ": "operator", "промоутер": "coach"}
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
        if not ex and (role_word.startswith("контрол") or role_word.startswith("старш")):
            con.execute("UPDATE users SET is_senior=1 WHERE id=?", (uid,))
        if not ex and "промоутер" in role_word:
            con.execute("UPDATE users SET is_promoter=1 WHERE id=?", (uid,))
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
    try:
        uid = con.execute("INSERT INTO users(login, name, role, pw_hash) VALUES (?,?,'operator',?)",
                          (login_, body.get("name") or login_, db.hash_password(pw))).lastrowid
    except Exception:
        raise ApiError("Такой логин уже есть")
    set_post(con, uid, body.get("post") or {"admin": "owner"}.get(body.get("role"), body.get("role")) or "operator",
             body.get("is_promoter") in (True, "1", "on"))
    sv.audit(con, user, "user.create", login_)
    return {"ok": True}


# ---------------- данные ----------------

def journal_write(user):
    """Отмечать в журнале: руководитель, старший администратор, тренер (свои группы). Администратор только смотрит."""
    if perms.level(user, "journal") == perms.LEVEL["view"]:
        raise ApiError("Журнал у вас только для просмотра", 403)


def coach_guard(con, user, group_id: int):
    if user["role"] != "coach":
        return
    if not schedule.coach_may(con, user["id"], group_id):
        raise ApiError("Это не ваша группа", 403)


@route("GET", "/api/meta", role="all")
def meta(con, h, user, body, q):
    user["sip_number"] = con.execute("SELECT sip_number FROM users WHERE id=?", (user["id"],)).fetchone()[0]
    user |= {"perms": perms.view_of(user), "title": perms.title(user)}
    m = sv.meta(con) | {"user": user, "demo": os.environ.get("CRM_DEMO", "1") == "1" and not perms.real_staff(con), "comms": comms.status(con)}
    if user["role"] == "coach":
        m["groups"] = [g for g in m["groups"] if schedule.coach_may(con, user["id"], g["id"])]
        m["settings"] = {}
    elif not perms.allowed(user, "any"):  # юрист: группы и цены ему не нужны
        m["groups"], m["settings"] = [], {}
    from . import staffdocs
    m["staff_docs_pending"] = staffdocs.pending(con, user)
    import json as _json
    m["staff"] = []
    for r in con.execute("SELECT id, name, role, photo_at, photo_sq, bio FROM users WHERE active=1 ORDER BY name"):
        b = _json.loads(r["bio"] or "{}") if r["bio"] else {}
        m["staff"].append({"id": r["id"], "name": r["name"], "role": r["role"], "photo_at": r["photo_at"], "photo_sq": r["photo_sq"],
                           "sticker": b.get("sticker", ""), "nickname": b.get("nickname", "")})
    return m


@route("GET", "/api/staff-docs", role="all")
def staff_docs(con, h, user, body, q):
    from . import staffdocs, legal
    return {"docs": [d | {"html": legal._md(d["text"])} for d in staffdocs.docs_for(con, user, base_url(h))], "name": user["name"]}


@route("POST", "/api/staff-docs/accept", role="all")
def staff_docs_accept(con, h, user, body, q):
    from . import staffdocs
    ip = (h.headers.get("X-Forwarded-For") or h.client_address[0] or "").split(",")[0].strip()
    n = staffdocs.accept(con, user, body.get("docs") or [], body.get("signed_name") or "", ip, h.headers.get("User-Agent") or "", base_url(h))
    sv.audit(con, user, "staff_docs.accept", str(n))
    return {"ok": True, "accepted": n}


@route("GET", "/api/staff-docs/overview", role="staffdocs:view")
def staff_docs_overview(con, h, user, body, q):
    from . import staffdocs
    return {"items": staffdocs.overview(con)}


@route("GET", "/api/staff-docs/log/{uid}", role="staffdocs:view")
def staff_docs_log(con, h, user, body, q, uid):
    from . import staffdocs
    u = con.execute("SELECT name FROM users WHERE id=?", (int(uid),)).fetchone()
    return {"name": u[0] if u else "", "items": staffdocs.log(con, int(uid))}


@route("PATCH", "/api/groups/{gid}", role="schedule:full")
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
            "bills_due": expenses.bills_upcoming(con) if user["role"] == "admin" else [],
            "ads_alerts": expenses.ads_alerts(con) if user["role"] == "admin" else [],
            "absences": schedule.absences(con),
            "shift_reports": __import__("crm.coachreport", fromlist=["desk"]).desk(con, user["role"] == "admin")
            if perms.can(user, "schedule", "full") else None,
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


@route("GET", "/api/enrollments/{eid}/transfer", role="operator")
def enrollment_transfer_plan(con, h, user, body, q, eid):
    from . import transfer
    return transfer.plan(con, int(eid), int(q.get("group_id") or 0), q.get("date") or "", [int(x) for x in (q.get("weekdays") or "").split(",") if x])


@route("POST", "/api/enrollments/{eid}/transfer", role="operator")
def enrollment_transfer(con, h, user, body, q, eid):
    from . import transfer
    return transfer.apply(con, int(eid), int(body.get("group_id") or 0), body.get("date") or "", user, body.get("weekdays"))


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


@route("GET", "/api/payments", role="payments:view")
def payments_list(con, h, user, body, q):
    from datetime import date as _d
    start = q.get("from") or _d.today().replace(day=1).isoformat()
    end = q.get("to") or _d.today().isoformat()
    r = money.registry(con, start, end, q.get("method", ""), q.get("unconfirmed") == "1")
    r["debtors"] = money.debtors(con)
    from . import pricing
    r["corrections"] = pricing.corrections(con, start, end)
    if q.get("csv") == "1":
        if not perms.can(user, "export"):
            raise ApiError("Выгружать данные может только руководитель", 403)
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


@route("POST", "/api/branches", role="schedule:full")
def branch_add(con, h, user, body, q):
    return {"id": sx(schedule.save_branch, con, body)}


@route("PUT", "/api/branches/{bid}", role="schedule:full")
def branch_edit(con, h, user, body, q, bid):
    return {"id": sx(schedule.save_branch, con, body, int(bid))}


@route("POST", "/api/branches/{bid}/archive", role="schedule:full")
def branch_archive(con, h, user, body, q, bid):
    sx(schedule.set_branch_active, con, int(bid), bool(body.get("active")))
    return {"ok": True}


@route("POST", "/api/groups", role="schedule:full")
def group_add(con, h, user, body, q):
    gid = sx(schedule.save_group, con, body)
    sv.audit(con, user, "group.create", str(gid))
    return {"id": gid}


@route("POST", "/api/groups/{gid}/version", role="schedule:full")
def group_version(con, h, user, body, q, gid):
    """Новые дни и время группы с даты (до неё действует прежнее расписание)."""
    if body.get("cancel"):
        return sx(schedule.cancel_change, con, int(gid), str(body["cancel"]), user) or {"ok": True}
    return sx(schedule.plan_change, con, int(gid), str(body.get("valid_from") or ""), body, user)


@route("PUT", "/api/groups/{gid}", role="schedule:full")
def group_edit(con, h, user, body, q, gid):
    sx(schedule.save_group, con, body, int(gid))
    sv.audit(con, user, "group.edit", str(gid))
    return {"ok": True}


@route("POST", "/api/groups/{gid}/archive", role="schedule:full")
def group_archive(con, h, user, body, q, gid):
    sx(schedule.set_group_active, con, int(gid), bool(body.get("active")))
    return {"ok": True}


@route("GET", "/api/groups/{gid}/export", role="export")
def group_export(con, h, user, body, q, gid):
    name, rows = sx(schedule.group_export, con, int(gid))
    return csv_raw(rows, name)


@route("POST", "/api/shifts", role="schedule:full")
def shift_add(con, h, user, body, q):
    return {"id": sx(schedule.save_shift, con, body)}


@route("PUT", "/api/shifts/{shid}", role="schedule:full")
def shift_edit(con, h, user, body, q, shid):
    return {"id": sx(schedule.save_shift, con, body, int(shid))}


@route("DELETE", "/api/shifts/{shid}", role="schedule:full")
def shift_del(con, h, user, body, q, shid):
    schedule.remove_shift(con, int(shid))
    return {"ok": True}


@route("GET", "/api/schedule/day", role="schedule")
def schedule_day(con, h, user, body, q):
    from datetime import date as _d
    return schedule.day(con, _d.fromisoformat(q["date"]) if q.get("date") else _d.today())


@route("POST", "/api/substitutions", role="schedule:full")
def sub_save(con, h, user, body, q):
    return {"id": sx(schedule.save_sub, con, body, user)}


@route("DELETE", "/api/substitutions/{sid}", role="schedule:full")
def sub_delete(con, h, user, body, q, sid):
    schedule.remove_sub(con, int(sid))
    return {"ok": True}


@route("POST", "/api/schedule/import", role="schedule:full")
def schedule_import(con, h, user, body, q):
    r = sx(schedule.import_schedule, con, body)
    sv.audit(con, user, "schedule.import", f"{r['groups_new']} новых, {r['groups_updated']} обновлено")
    return r


@route("GET", "/api/absences", role="operator")
def absences_list(con, h, user, body, q):
    return {"items": schedule.absences(con), "needs": schedule.needs_subs(con)}


@route("POST", "/api/absences", role="schedule:full")
def absence_add(con, h, user, body, q):
    return {"id": sx(schedule.save_absence, con, body, user)}


@route("DELETE", "/api/absences/{aid}", role="schedule:full")
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


@route("GET", "/api/lessons/staff", role="journal")
def lesson_staff_get(con, h, user, body, q):
    gid = int(q.get("group_id") or 0)
    coach_guard(con, user, gid)
    coaches_ = [dict(r) for r in con.execute("SELECT id, name FROM users WHERE role='coach' AND active=1 ORDER BY name")]
    return staff.lesson_staff(con, gid, q.get("date") or "") | {"coaches": coaches_}


@route("POST", "/api/lessons/staff", role="journal")
def lesson_staff_set(con, h, user, body, q):
    journal_write(user)
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
    return {"items": merch.items(con, all_=user["role"] == "admin" and q.get("all") == "1"), "methods": merch.METHODS,
            "can_norm": perms.can(user, "schedule", "full")}


@route("POST", "/api/merch/stock", role="merch:full")
def merch_stock(con, h, user, body, q):
    """Пересчёт склада мерча: остатки, норма, список к заказу и задача руководителю в Планер."""
    try:
        return merch.stock_count(con, body.get("items") or [], user, perms.can(user, "schedule", "full"))
    except planner.PlanError as e:
        raise ApiError(str(e))


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


@route("GET", "/api/planner", role="planner")
def planner_view(con, h, user, body, q):
    return planner.view(con, user)


@route("POST", "/api/planner/tasks", role="planner")
def planner_add(con, h, user, body, q):
    return {"id": px(planner.add, con, user, body)}


@route("GET", "/api/planner/week", role="planner")
def planner_week(con, h, user, body, q):
    from datetime import date as _d
    return px(planner.week, con, user, q.get("start") or _d.today().isoformat(), int(q["user"]) if q.get("user") else None)


@route("POST", "/api/planner/tasks/{tid}/move", role="planner")
def planner_move(con, h, user, body, q, tid):
    px(planner.move, con, int(tid), user, body.get("date") or "")
    return {"ok": True}


@route("POST", "/api/planner/tasks/{tid}/reopen", role="planner")
def planner_reopen(con, h, user, body, q, tid):
    px(planner.reopen, con, int(tid), user)
    return {"ok": True}


@route("POST", "/api/planner/tasks/{tid}/cancel", role="planner")
def planner_cancel(con, h, user, body, q, tid):
    px(planner.cancel, con, int(tid), user)
    return {"ok": True}


@route("POST", "/api/planner/tasks/{tid}/return", role="planner")
def planner_return(con, h, user, body, q, tid):
    px(planner.return_task, con, int(tid), user, body.get("note") or "")
    return {"ok": True}


@route("POST", "/api/planner/recurring/{rid}/stop", role="planner")
def planner_stop(con, h, user, body, q, rid):
    px(planner.stop_recurring, con, int(rid), user)
    return {"ok": True}


@route("PUT", "/api/planner/settings", role="planner:full")
def planner_settings(con, h, user, body, q):
    px(planner.save_settings, con, body)
    return {"ok": True}


@route("PUT", "/api/users/{uid}/birth", role="all")
def user_birth(con, h, user, body, q, uid):
    if not planner.can_manage(con, user) and int(uid) != user["id"]:
        raise ApiError("Нет доступа", 403)
    px(planner.set_birth, con, int(uid), body.get("birth") or "")
    return {"ok": True}


# ---------------- чеки 54-ФЗ ----------------

def fx(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except fiscal.FxError as e:
        raise ApiError(str(e))


@route("GET", "/api/fiscal", role="fiscal")
def fiscal_get(con, h, user, body, q):
    v = fiscal.view(con, int(q.get("days") or 30))
    if user["role"] != "admin":
        v.pop("settings", None)
    return v


@route("PUT", "/api/fiscal/settings", role="admin")
def fiscal_settings(con, h, user, body, q):
    fiscal.save_settings(con, body)
    sv.audit(con, user, "fiscal.settings", ",".join(sorted(k for k in body if k in fiscal.PLAIN_KEYS + fiscal.SECRET_KEYS)))
    return fiscal.settings_view(con)


@route("POST", "/api/fiscal/test", role="admin")
def fiscal_test(con, h, user, body, q):
    return fiscal.test_sources(con)


@route("POST", "/api/fiscal/run", role="fiscal")
def fiscal_run(con, h, user, body, q):
    return fiscal.run(con)


@route("POST", "/api/fiscal/{pid}/resolve", role="fiscal")
def fiscal_resolve(con, h, user, body, q, pid):
    fx(fiscal.resolve, con, int(pid), body.get("action") or "", user, (body.get("note") or "").strip())
    sv.audit(con, user, "fiscal.resolve", f"{pid}:{body.get('action')}")
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


@route("GET", "/api/offers", role="operator|docs")
def offers_list(con, h, user, body, q):
    return {"programs": offers.listing(con, base_url(h))}


@route("POST", "/api/offers", role="docs:full")
def offers_upload(con, h, user, body, q):
    try:
        oid = offers.upload(con, body, user)
    except offers.OfferError as e:
        raise ApiError(str(e))
    sv.audit(con, user, "offer.upload", str(oid))
    return {"id": oid}


@route("POST", "/api/offers/{oid}/archive", role="docs:full")
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


@route("GET", "/api/schedule/week", role="schedule")
def schedule_week(con, h, user, body, q):
    if q.get("start"):
        from datetime import date as _d
        try:
            st = _d.fromisoformat(q["start"])
        except ValueError:
            raise ApiError("Неверная дата")
        return schedule.week_days(con, st)
    return schedule.week(con)


@route("POST", "/api/students/{sid}/archive", role="operator")
def student_archive(con, h, user, body, q, sid):
    sv.get_student_row(con, int(sid))
    schedule.set_student_archived(con, int(sid), bool(body.get("archived")), user)
    return {"ok": True}


@route("GET", "/api/archive/students", role="members:full")
def archive_students(con, h, user, body, q):
    return schedule.archived_students(con)


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


def prx(fn, *a, **kw):
    from . import pricing
    try:
        return fn(*a, **kw)
    except pricing.PriceError as e:
        raise ApiError(str(e))


@route("GET", "/api/prices", role="admin")
def prices_get(con, h, user, body, q):
    from . import pricing
    return {"items": pricing.listing(con)}


@route("POST", "/api/prices", role="admin")
def prices_save(con, h, user, body, q):
    from . import pricing
    prx(pricing.save_price, con, body)
    sv.audit(con, user, "prices.save", json.dumps(body, ensure_ascii=False))
    return {"items": pricing.listing(con)}


@route("POST", "/api/subscriptions/{sub}/correct", role="operator")
def subscription_correct(con, h, user, body, q, sub):
    from . import pricing
    prx(pricing.correct, con, int(sub), body.get("price"), body.get("comment") or "", user)
    return {"ok": True}


@route("POST", "/api/attendance/{aid}/cert", role="operator")
def attendance_cert(con, h, user, body, q, aid):
    """Отметка «есть медсправка» на пропуске: только такие пропуски идут в перерасчёт на «Гибком»."""
    a = con.execute("SELECT student_id, date FROM attendance WHERE id=? AND status='absent'", (int(aid),)).fetchone()
    if not a:
        raise ApiError("Пропуск не найден", 404)
    con.execute("UPDATE attendance SET cert=? WHERE id=?", (1 if body.get("cert") else 0, int(aid)))
    sv.log(con, a["student_id"], f"Пропуск {a['date'][8:]}.{a['date'][5:7]}: {'есть медсправка' if body.get('cert') else 'справка снята'}", user, "system")
    return {"ok": True}


@route("POST", "/api/subscriptions/{sub}/cancel", role="operator")
def cancel(con, h, user, body, q, sub):
    refund = bool(body.get("refund"))
    if refund and not perms.can(user, "payments", "view"):
        raise ApiError("Возврат делают руководитель или старший администратор", 403)
    sv.cancel_subscription(con, int(sub), user, refund, body.get("comment") or "")
    return {"ok": True}


@route("POST", "/api/students/{sid}/pay-family", role="operator")
def pay_family(con, h, user, body, q, sid):
    sv.get_student_row(con, int(sid))
    return mx(money.pay_family, con, int(sid), int(body.get("amount") or 0), body.get("method") or "", user, body.get("comment") or "")


@route("POST", "/api/students/{sid}/move-balance", role="operator")
def move_balance(con, h, user, body, q, sid):
    mx(money.move_balance, con, int(sid), int(body.get("to") or 0), int(body.get("amount") or 0), user)
    return {"ok": True}


@route("POST", "/api/students/{sid}/refund", role="payments:view")
def refund(con, h, user, body, q, sid):
    mx(money.refund, con, int(sid), int(body.get("amount") or 0), user, body.get("comment") or "")
    return {"ok": True}


@route("POST", "/api/students/{sid}/makeups", role="operator")
def makeup_book(con, h, user, body, q, sid):
    return sv.book_makeup(con, int(sid), int(body.get("group_id") or 0), body.get("date") or "",
                          body.get("absence_id"), user)


@route("POST", "/api/makeups/{mid}/status", role="journal")
def makeup_status(con, h, user, body, q, mid):
    journal_write(user)
    m = con.execute("SELECT group_id FROM makeups WHERE id=?", (int(mid),)).fetchone()
    if m:
        coach_guard(con, user, m["group_id"])
    sv.set_makeup_status(con, int(mid), body.get("status"), user)
    return {"ok": True}


@route("GET", "/api/journal", role="journal")
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


@route("POST", "/api/attendance", role="journal")
def attendance(con, h, user, body, q):
    journal_write(user)
    coach_guard(con, user, int(body.get("group_id") or 0))
    sv.mark_attendance(con, int(body.get("student_id") or 0), int(body.get("group_id") or 0),
                       body.get("date") or "", body.get("status") or "", user)
    return {"ok": True}


@route("GET", "/api/coach/today", role="today")
def coach_today(con, h, user, body, q):
    cid = int(q.get("coach_id") or 0) if perms.can(user, "today", "full") else user["id"]
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


@route("GET", "/api/coach/kids", role="members")
def coach_kids(con, h, user, body, q):
    """«Мои ученики» тренера: его группы (главным или помощником), дети и телефоны родителей. Только просмотр."""
    from datetime import date as _d
    today = _d.today().isoformat()
    cid = int(q.get("coach_id") or 0) if perms.can(user, "members", "full") else user["id"]
    out = []
    for g in con.execute("""SELECT g.id, g.name, g.age, g.coach_id, b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id
                            WHERE g.active=1 AND g.is_makeup=0 AND (g.coach_id=? OR g.assistant_id=?) ORDER BY b.id, g.name""", (cid, cid)):
        kids = []
        for s in con.execute("""SELECT DISTINCT s.id, s.name, s.birth, s.family_id FROM enrollments e JOIN students s ON s.id=e.student_id
                                WHERE e.group_id=? AND (e.active=1 OR e.end_date > ?) AND (e.start_date='' OR e.start_date <= ?)
                                  AND s.funnel='members' AND s.stage != 'left' AND s.archived=0 ORDER BY s.name""", (g["id"], today, today)):
            parents = [dict(p) for p in con.execute("SELECT name, phone FROM parents WHERE family_id=? ORDER BY id", (s["family_id"],))]
            kids.append({"id": s["id"], "name": s["name"], "birth": s["birth"] or "", "parents": parents})
        out.append({"id": g["id"], "name": g["name"], "age": g["age"], "branch": g["branch"], "main": g["coach_id"] == cid, "kids": kids})
    return {"groups": out}


# ---------------- отчёт тренера о смене (coachreport.py) ----------------

def crx(fn, *a, **kw):
    from . import coachreport
    try:
        return fn(*a, **kw)
    except coachreport.ReportError as e:
        raise ApiError(str(e))


def _report_access(con, user, rid):
    from . import coachreport
    return crx(coachreport.check_access, con, int(rid), user, perms.can(user, "schedule", "full"))


@route("GET", "/api/coach-report", role="today|schedule:full")
def coach_report_get(con, h, user, body, q):
    from . import coachreport
    from datetime import date as _d
    d = _d.fromisoformat(q.get("date") or _d.today().isoformat())
    manager = perms.can(user, "schedule", "full")
    return crx(coachreport.get_or_create, con, int(q.get("branch_id") or 0), d, user, manager,
               int(q["main_id"]) if q.get("main_id") and manager else None)


@route("GET", "/api/coach-shifts", role="today")
def coach_shifts(con, h, user, body, q):
    """Смены тренера сегодня и вчера (вчерашний несданный отчёт тоже можно сдать) со статусом отчёта."""
    from . import coachreport
    from datetime import date as _d, timedelta as _td
    out = []
    for d in (_d.today() - _td(days=1), _d.today()):
        for s in coachreport.my_shifts(con, user["id"], d):
            r = con.execute("SELECT id, status FROM coach_reports WHERE branch_id=? AND date=? AND main_id=?",
                            (s["branch_id"], d.isoformat(), s["main_id"])).fetchone()
            if d < _d.today() and r and r["status"] == "done":
                continue
            out.append({"branch_id": s["branch_id"], "branch": s["branch"], "date": d.isoformat(), "main": s["main_id"] == user["id"],
                        "end": s["end"].isoformat(timespec="minutes") if s["end"] else None, "groups": len(s["groups"]),
                        "status": r["status"] if r else "none"})
    return {"shifts": out}


@route("POST", "/api/coach-report/{rid}", role="today|schedule:full")
def coach_report_patch(con, h, user, body, q, rid):
    from . import coachreport
    _report_access(con, user, rid)
    crx(coachreport.update, con, int(rid), body)
    r, s = _report_access(con, user, rid)
    return coachreport.view(con, r, s)


@route("POST", "/api/coach-report/{rid}/photo", role="today|schedule:full")
def coach_report_photo(con, h, user, body, q, rid):
    from . import coachreport
    import base64
    r, s = _report_access(con, user, rid)
    if r["status"] == "done":
        raise ApiError("Отчёт уже сдан")
    if body.get("remove"):
        if con.execute("SELECT 1 FROM report_photos WHERE id=? AND kind='report' AND ref_id=?", (int(body["remove"]), r["id"])).fetchone():
            coachreport.remove_photo(con, int(body["remove"]))
    else:
        try:
            raw = base64.b64decode(str(body.get("data") or "").split(",", 1)[-1])
        except ValueError:
            raise ApiError("Не удалось прочитать фото")
        crx(coachreport.add_photo, con, "report", r["id"], str(body.get("slot") or ""), raw)
    return coachreport.view(con, coachreport._row(con, r["id"]), s)


@route("POST", "/api/coach-report/{rid}/submit", role="today")
def coach_report_submit(con, h, user, body, q, rid):
    from . import coachreport
    return crx(coachreport.submit, con, int(rid), user)


@route("GET", "/api/report-photo/{pid}", role="today|schedule:full|operator")
def report_photo(con, h, user, body, q, pid):
    from . import coachreport
    try:
        with open(coachreport.photo_path(int(pid)), "rb") as f:
            return Raw(f.read(), "image/jpeg")
    except FileNotFoundError:
        raise ApiError("Фото нет", 404)


@route("POST", "/api/report-tasks", role="schedule:full")
def report_task_add(con, h, user, body, q):
    from . import coachreport
    import base64
    tid = crx(coachreport.add_task, con, int(body.get("coach_id") or 0), str(body.get("date") or ""), body.get("text") or "", user)
    if body.get("photo"):
        try:
            raw = base64.b64decode(str(body["photo"]).split(",", 1)[-1])
        except ValueError:
            raise ApiError("Не удалось прочитать фото")
        crx(coachreport.add_photo, con, "task", tid, "", raw)
    return {"id": tid}


@route("POST", "/api/report-tasks/{tid}/done", role="today|schedule:full")
def report_task_done(con, h, user, body, q, tid):
    from . import coachreport
    t = con.execute("SELECT coach_id, date FROM report_tasks WHERE id=?", (int(tid),)).fetchone()
    if not t:
        raise ApiError("Задание не найдено", 404)
    if not perms.can(user, "schedule", "full"):  # тренер отмечает задания своей смены (свои и помощника/главного)
        from datetime import date as _d
        mine = coachreport.my_shifts(con, user["id"], _d.fromisoformat(t["date"]))
        if not any(t["coach_id"] in (s["main_id"], *s["assistants"]) for s in mine):
            raise ApiError("Это не твоё задание", 403)
    crx(coachreport.task_done, con, int(tid), user)
    return {"ok": True}


@route("DELETE", "/api/report-tasks/{tid}", role="schedule:full")
def report_task_del(con, h, user, body, q, tid):
    con.execute("DELETE FROM report_tasks WHERE id=? AND done_at IS NULL", (int(tid),))
    return {"ok": True}


@route("POST", "/api/inventory/{iid}/close", role="schedule:full")
def inventory_close(con, h, user, body, q, iid):
    con.execute("UPDATE inventory_issues SET closed_at=?, closed_by=? WHERE id=?",
                (__import__("datetime").datetime.now().isoformat(timespec="minutes"), user.get("name", ""), int(iid)))
    return {"ok": True}


@route("GET", "/api/coaches/board", role="coaches")
def coaches_board(con, h, user, body, q):
    me = user["id"] if user["role"] == "coach" else (int(q["as_coach"]) if q.get("as_coach") else None)
    return coaches.leaderboard(con, q.get("month"), me)


@route("GET", "/api/analytics", role="admin")
def analytics_report(con, h, user, body, q):
    from . import analytics
    return analytics.report(con, q.get("month"), int(q["branch_id"]) if q.get("branch_id") else None, q.get("part") or "groups")


@route("GET", "/api/salary", role="pay")
def salary_report(con, h, user, body, q):
    """Зарплата тренеров за месяц. Тренер видит только свою."""
    from . import salary
    if not perms.can(user, "pay", "full"):
        return salary.payroll(con, q.get("month"), None, user["id"])
    return salary.payroll(con, q.get("month"), int(q["branch_id"]) if q.get("branch_id") else None)


@route("GET", "/api/my-pay", role="pay")
def my_pay(con, h, user, body, q):
    """«Моя зарплата»: на сегодня, прогноз на месяц, цель. Руководитель может открыть любого (user_id)."""
    from . import mypay
    who = user
    if q.get("user_id") and perms.can(user, "pay", "full"):
        r = con.execute("SELECT id, name, role, is_senior, is_promoter FROM users WHERE id=?", (int(q["user_id"]),)).fetchone()
        if not r:
            raise ApiError("Сотрудник не найден", 404)
        who = dict(r)
    return mypay.view(con, who, q.get("month")) | {"name": who.get("name", "")}


@route("POST", "/api/my-pay", role="pay")
def my_pay_set(con, h, user, body, q):
    from . import mypay
    ym = body.get("month") or logic.month_of(__import__("datetime").date.today())
    if "goal" in body:
        mypay.set_goal(con, user["id"], ym, body["goal"])
    if "contacts" in body:
        if "promoter" not in perms.roles_of(user):
            raise ApiError("Контакты вписывает промоутер", 403)
        mypay.set_contacts(con, user["id"], ym, body["contacts"])
    return mypay.view(con, user, ym)


@route("GET", "/api/dashboard", role="admin")
def dashboard(con, h, user, body, q):
    return sv.dashboard(con, q.get("month"), int(q["branch_id"]) if q.get("branch_id") else None)


@route("POST", "/api/students/{sid}/trial")
def trial_mark(con, h, user, body, q, sid):
    s = sv.get_student_row(con, int(sid))
    if s["group_id"]:
        coach_guard(con, user, s["group_id"])
    if body.get("came") is None:  # «Встречаю детей»: сняли отметку «пришёл»
        sv.clear_trial_mark(con, int(sid), user)
    else:
        sv.mark_trial(con, int(sid), bool(body.get("came")), user)
    return {"ok": True}


@route("POST", "/api/demo/reset", role="admin")
def demo_reset(con, h, user, body, q):
    if os.environ.get("CRM_DEMO", "1") != "1" or perms.real_staff(con):
        raise ApiError("В CRM работает настоящая команда: пересоздавать тестовые данные нельзя", 403)
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


@route("POST", "/api/closures", role="schedule:full")
def closures_add(con, h, user, body, q):
    from . import closures
    try:
        return {"id": closures.add(con, body, user)}
    except closures.ClosureError as e:
        raise ApiError(str(e))


@route("DELETE", "/api/closures/{cid}", role="schedule:full")
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


@route("GET", "/api/legal", role="docs:full")
def legal_get(con, h, user, body, q):
    comms.put(con, "public_url", base_url(h))
    return legal.admin_view(con, base_url(h))


@route("PUT", "/api/legal", role="docs:full")
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
                    if not any(perms.allowed(user, x) for x in role.split("|")):
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
    if os.environ.get("CRM_DEMO", "1") == "1" and not perms.real_staff(con):  # при настоящей команде выдуманных не заводим
        db.seed_demo(con)
        coaches.seed_demo_coaches(con)
        money.init(con)
        if not comms.status(con)["telegram"] and not comms.status(con)["sipuni"]:
            comms.seed_demo(con)
        ai.seed_demo(con)
        tasks.seed_demo(con)
    n = perms.archive_test_staff(con)
    if n:
        print(f"Тестовые сотрудники отправлены в архив: {n}", flush=True)
    con.commit()
    con.close()
    ai.start_worker(db.connect)
    tasks.start_worker(db.connect)
    fiscal.start_worker(db.connect)
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
