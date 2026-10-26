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

from . import coaches, comms, db, services as sv
from .services import ApiError


class Raw:
    """Ответ файлом (запись звонка, фото из мессенджера)."""
    def __init__(self, data: bytes, ctype: str):
        self.data, self.ctype = data, ctype


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
    return [dict(r) for r in con.execute("SELECT id, login, name, role, active, sip_number FROM users ORDER BY id")]


@route("PATCH", "/api/users/{uid}", role="admin")
def users_patch(con, h, user, body, q, uid):
    if "sip_number" in body:
        sip = "".join(ch for ch in str(body["sip_number"] or "") if ch.isdigit())[:10]
        con.execute("UPDATE users SET sip_number=? WHERE id=?", (sip, int(uid)))
    return {"ok": True}


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
    g = con.execute("SELECT coach_id FROM groups WHERE id=?", (group_id,)).fetchone()
    if not g or g["coach_id"] != user["id"]:
        raise ApiError("Это не ваша группа", 403)


@route("GET", "/api/meta")
def meta(con, h, user, body, q):
    m = sv.meta(con) | {"user": user, "demo": os.environ.get("CRM_DEMO", "1") == "1", "comms": comms.status(con)}
    user["sip_number"] = con.execute("SELECT sip_number FROM users WHERE id=?", (user["id"],)).fetchone()[0]
    if user["role"] == "coach":
        m["groups"] = [g for g in m["groups"] if g["coach_id"] == user["id"]]
        m["settings"] = {}
    return m


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


@route("GET", "/api/board/members", role="operator")
def board_members(con, h, user, body, q):
    return sv.board(con, "members")


@route("GET", "/api/board/skips", role="operator")
def board_skips(con, h, user, body, q):
    return sv.board(con, "skips")


@route("POST", "/api/students/{sid}/move", role="operator")
def move(con, h, user, body, q, sid):
    sv.move_card(con, int(sid), body.get("funnel"), body.get("stage"), user)
    return {"ok": True}


@route("GET", "/api/students", role="operator")
def students(con, h, user, body, q):
    return sv.search_students(con, q.get("q", ""))


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
    return sv.quote_for(con, int(sid), q.get("month"), q.get("enrollment_id"))


@route("POST", "/api/students/{sid}/invoice", role="operator")
def invoice(con, h, user, body, q, sid):
    return sv.create_invoice(con, int(sid), body.get("month"), user, body.get("enrollment_id"))


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
    sv.pay_subscription(con, int(sub), user)
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
    return sv.journal(con, int(q.get("group_id") or 0), q.get("date") or sv.today().isoformat())


@route("POST", "/api/attendance")
def attendance(con, h, user, body, q):
    coach_guard(con, user, int(body.get("group_id") or 0))
    sv.mark_attendance(con, int(body.get("student_id") or 0), int(body.get("group_id") or 0),
                       body.get("date") or "", body.get("status") or "", user)
    return {"ok": True}


@route("GET", "/api/coaches/board")
def coaches_board(con, h, user, body, q):
    me = user["id"] if user["role"] == "coach" else (int(q["as_coach"]) if q.get("as_coach") else None)
    return coaches.leaderboard(con, q.get("month"), me)


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


@route("POST", "/api/calls/seen", role="operator")
def calls_seen(con, h, user, body, q):
    comms.mark_calls_seen(con)
    return {"ok": True}


@route("GET", "/api/calls/{cid}/record", role="operator")
def call_record(con, h, user, body, q, cid):
    return Raw(*cx(comms.record_for, con, int(cid)))


@route("GET", "/api/integrations", role="admin")
def integrations_get(con, h, user, body, q):
    return comms.admin_view(con, base_url(h))


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
    result = {"ok": True}
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
            return h._send(400, {"ok": False, "error": str(e)})
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
                    if n > 1_000_000:
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
        if not comms.status(con)["telegram"] and not comms.status(con)["sipuni"]:
            comms.seed_demo(con)
    con.close()
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
