"""Фото и анкета сотрудника. Запуск: python -m tests.test_profiles"""
import os, sys, tempfile
d = tempfile.mkdtemp(); os.environ["CRM_DB"] = os.path.join(d, "p.sqlite3")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import db, profiles
from crm.services import ApiError

profiles.PHOTO_DIR = os.path.join(d, "photos")
con = db.connect(os.environ["CRM_DB"]); db.init_db(con)
uid = con.execute("INSERT INTO users(login, name, role, pw_hash) VALUES ('n','Никита Евсюнин','coach','x')").lastrowid
png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
profiles.save_photo(con, uid, png)
assert profiles.read_photo(uid) == (png, "image/png")
assert con.execute("SELECT photo_at FROM users WHERE id=?", (uid,)).fetchone()[0]
for bad in (b"", b"<html>not image"):
    try:
        profiles.save_photo(con, uid, bad); raise AssertionError("приняли не картинку")
    except ApiError:
        pass
try:
    profiles.photo_from_url(con, uid, "https://evil.example/x.jpg"); raise AssertionError("чужая ссылка")
except ApiError:
    pass
profiles.save_bio(con, uid, {"education": "Высшее", "experience": "1 год", "hacker": "x"})
p = profiles.profile(con, uid)
assert p["bio"] == {"education": "Высшее", "experience": "1 год"}
profiles.remove_photo(con, uid)
assert profiles.read_photo(uid) is None
print("✓ фото только картинкой и только с сайта Футболики, анкета сохраняется")
print("ok")
