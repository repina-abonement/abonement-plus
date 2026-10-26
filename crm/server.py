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

from . import db, services as sv
from .services import ApiError

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
    return [dict(r) for r in con.execute("SELECT id, login, name, role, active FROM users ORDER BY id")]


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
    m = sv.meta(con) | {"user": user}
    if user["role"] == "coach":
        m["groups"] = [g for g in m["groups"] if g["coach_id"] == user["id"]]
        m["settings"] = {}
    return m


@route("PATCH", "/api/groups/{gid}", role="admin")
def group_patch(con, h, user, body, q, gid):
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
    return {"id": sv.create_student(con, body, user)}


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
    return sv.quote_for(con, int(sid), q.get("month"))


@route("POST", "/api/students/{sid}/invoice", role="operator")
def invoice(con, h, user, body, q, sid):
    return sv.create_invoice(con, int(sid), body.get("month"), user)


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


@route("GET", "/api/dashboard", role="admin")
def dashboard(con, h, user, body, q):
    return sv.dashboard(con, q.get("month"))


@route("PUT", "/api/settings", role="admin")
def settings_put(con, h, user, body, q):
    allowed = set(db.DEFAULT_SETTINGS)
    for k, v in body.items():
        if k in allowed:
            con.execute("UPDATE settings SET value=? WHERE key=?", (str(v), k))
    sv.audit(con, user, "settings.update", json.dumps(body, ensure_ascii=False))
    return db.settings(con)


ROLE_RANK = {"coach": 1, "operator": 2, "admin": 3}
ROLE_NEED = {"any": 1, "operator": 2, "admin": 3}


class Handler(BaseHTTPRequestHandler):
    server_version = "AbonementPlus/0.1"

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
