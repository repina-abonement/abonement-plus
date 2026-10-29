"""Права по должностям: меню и сервер. Запуск: python -m tests.test_perms"""
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["CRM_DB"] = os.path.join(tempfile.mkdtemp(), "p.sqlite3")
os.environ["CRM_ADMIN_PASSWORD"] = "admin-pass-1"
os.environ["CRM_QUIET"] = "1"

from crm import db, perms, server  # noqa: E402
from tests.test_crm import check  # noqa: E402


def main():
    con = db.connect()
    db.init_db(con)
    db.seed_demo(con)
    gid = con.execute("SELECT id FROM groups WHERE is_makeup=0 AND active=1 ORDER BY id LIMIT 1").fetchone()[0]
    con.close()
    srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"

    def session(login, pw):
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

        def call(method, path, body=None):
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
            try:
                with op.open(req) as r:
                    return r.status, r.read()
            except urllib.error.HTTPError as e:
                return e.code, e.read()
        code, _ = call("POST", "/api/login", {"login": login, "password": pw})
        assert code == 200, login
        return call

    owner = session("admin", "admin-pass-1")
    print("Должности заводит руководитель")
    for login, post, prom in [("varya", "senior", False), ("olga", "operator", False), ("ivan", "coach", True),
                              ("lesha", "coach", False), ("babanov", "lawyer", False)]:
        code, _ = owner("POST", "/api/users", {"login": login, "name": login.title(), "password": "pass-1234", "post": post,
                                               "is_promoter": prom})
        assert code == 200, login
    users = {u["login"]: u for u in json.loads(owner("GET", "/api/users")[1])}
    check(users["varya"]["title"] == "Старший администратор" and users["ivan"]["title"] == "Тренер, промоутер"
          and users["babanov"]["title"] == "Юрист", "Должности и отметка промоутера сохраняются")
    S = {k: session(k, "pass-1234") for k in ("varya", "olga", "ivan", "lesha", "babanov")}
    S["owner"] = owner
    check(S["olga"]("PATCH", f"/api/users/{users['olga']['id']}", {"post": "owner"})[0] == 403,
          "Администратор не может сам себя повысить")

    def menu(who):
        return set(json.loads(S[who]("GET", "/api/meta")[1])["user"]["perms"])

    print("Меню")
    check({"dashboard", "spend", "payments", "export"} <= menu("owner"), "Руководитель видит всё")
    check("payments" in menu("varya") and "fiscal" in menu("varya") and "dashboard" not in menu("varya"),
          "Старший администратор: оплаты и чеки есть, дашборда нет")
    check(not {"payments", "fiscal", "spend", "dashboard", "export"} & menu("olga") and "sales" in menu("olga"),
          "Администратор: рабочий стол есть, денег и выгрузки нет")
    check("today" in menu("ivan") and "sales" not in menu("ivan") and "promoter" in perms.roles_of(users["ivan"]),
          "Тренер-промоутер: свой стол, рабочего стола администраторов нет")
    check(menu("babanov") == {"docs", "staffdocs", "team", "pay", "planner", "settings"}, "Юрист: документы, команда, своё")

    print("Сервер не пускает по прямой ссылке")
    rules = [  # (кто, запрос, ожидаемый ответ)
        ("olga", "GET /api/payments", 403), ("olga", "GET /api/fiscal", 403), ("olga", "GET /api/board/sales", 200),
        ("olga", f"GET /api/groups/{gid}/export", 403), ("olga", "GET /api/legal", 403), ("olga", "GET /api/schedule/week", 200),
        ("olga", "GET /api/journal?group_id=%d" % gid, 200),
        ("varya", "GET /api/payments", 200), ("varya", "GET /api/payments?csv=1", 403), ("varya", "GET /api/fiscal", 200),
        ("varya", "GET /api/dashboard", 403), ("varya", "GET /api/coaches/board", 200),
        ("lesha", "GET /api/board/sales", 403), ("lesha", "GET /api/payments", 403), ("lesha", "GET /api/coach/today", 200),
        ("lesha", "GET /api/staff-docs/overview", 403), ("lesha", "GET /api/students", 403), ("lesha", "GET /api/coach/kids", 200),
        ("varya", "GET /api/users", 200), ("olga", "GET /api/users", 403), ("babanov", "GET /api/coach/kids", 403),
        ("babanov", "GET /api/legal", 200), ("babanov", "GET /api/offers", 200), ("babanov", "GET /api/staff-docs/overview", 200),
        ("babanov", "GET /api/board/sales", 403), ("babanov", "GET /api/schedule/week", 403), ("babanov", "GET /api/students", 403),
        ("babanov", "GET /api/team", 200), ("babanov", "GET /api/planner", 200),
        ("owner", "GET /api/payments?csv=1", 200), ("owner", f"GET /api/groups/{gid}/export", 200),
    ]
    bad = [f"{w} {req}: {S[w](*req.split(' ', 1))[0]} вместо {exp}" for w, req, exp in rules if S[w](*req.split(" ", 1))[0] != exp]
    check(not bad, "Каждая должность открывает только своё" + ("".join("\n    " + b for b in bad)))

    sid = json.loads(owner("GET", "/api/students")[1])
    sid = (sid[0] if isinstance(sid, list) else sid["items"][0])["id"]
    mark = {"student_id": sid, "group_id": gid, "date": "2026-01-01", "status": "present"}
    check(S["olga"]("POST", "/api/attendance", mark)[0] == 403, "Администратор журнал только смотрит")
    check(S["varya"]("POST", "/api/attendance", mark)[0] != 403, "Старший администратор отмечает в журнале")

    print("Тренер видит своих детей")
    import sqlite3
    c2 = sqlite3.connect(os.environ["CRM_DB"])
    c2.execute("UPDATE groups SET coach_id=? WHERE id=?", (users["lesha"]["id"], gid)); c2.commit()
    kids = json.loads(S["lesha"]("GET", "/api/coach/kids")[1])["groups"]
    check(len(kids) == 1 and kids[0]["id"] == gid and all("parents" in k for k in kids[0]["kids"]),
          "Только его группа, у детей телефоны родителей")
    check("members" in menu("lesha"), "В меню тренера «Мои ученики»")

    print("Модели оплаты: только своя")
    t = lambda who: json.loads(S[who]("GET", "/api/team")[1])
    check(set(t("owner")["pay"]) >= {"admins", "promoters"} and "coach_pay" in t("owner"), "Руководитель видит все модели")
    check(set(t("olga")["pay"]) == {"admins"} and "coach_pay" not in t("olga"), "Администратор видит только свою")
    check(set(t("ivan")["pay"]) == {"promoters"} and "coach_pay" in t("ivan"), "Тренер-промоутер: тренерская и промоутерская")
    check(not t("babanov")["pay"] and "coach_pay" not in t("babanov"), "Юристу чужие модели не видны")
    tm = t("owner")["members"]
    check(users["babanov"]["id"] in tm["lawyers"] and users["babanov"]["id"] not in tm["admins"]
          and users["varya"]["id"] in tm["control"], "В «Команде» юрист в своём отделе, старший администратор отдельно")
    print("ok")


if __name__ == "__main__":
    main()
