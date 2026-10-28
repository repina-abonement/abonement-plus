"""Быстрый вход по коду. Запуск: python -m tests.test_pin"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import db, pin
from crm.services import ApiError

con = db.connect(os.path.join(tempfile.mkdtemp(), "p.sqlite3")); db.init_db(con)
uid = con.execute("INSERT INTO users(login, name, role, pw_hash) VALUES ('j','Юлия Репина','admin','x')").lastrowid
u = {"id": uid}
dev, other = "a" * 64, "b" * 64
for bad in ("123", "abcd", "1111", "1234"):
    try:
        pin.set_pin(con, u, dev, bad); raise AssertionError(bad)
    except ApiError:
        pass
pin.set_pin(con, u, dev, "2580")
assert pin.hello(con, dev) == {"name": "Юлия"} and pin.hello(con, other) is None
assert pin.login(con, dev, "2580")[0]["id"] == uid
assert pin.login(con, other, "2580")[1]["need_password"], "с другого устройства код не подходит"
for i in range(4):
    user, err = pin.login(con, dev, "0000")
    assert user is None and not err["need_password"]
user, err = pin.login(con, dev, "0000")
assert err["need_password"] and pin.hello(con, dev) is None, "после 5 ошибок код сброшен"
pin.set_pin(con, u, dev, "2580")
assert pin.login(con, dev, "2580")[0]
pin.remove_all(con, uid)
assert pin.hello(con, dev) is None
print("✓ код только с этого устройства, 5 ошибок - сброс, простые коды нельзя")
print("ok")
