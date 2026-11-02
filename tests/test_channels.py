"""Посты в каналы филиалов (Telegram и MAX). Запуск: python -m tests.test_channels"""
import os, sys, tempfile, base64
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("CRM_DB", os.path.join(tempfile.mkdtemp(), "c.sqlite3"))
from crm import db, comms, channels, perms
from tests.test_crm import check

con = db.connect(os.path.join(tempfile.mkdtemp(), "c.sqlite3")); db.init_db(con)
tg, mx = [], []
comms.tg_call = lambda con, method, payload=None, token=None: tg.append((method, payload)) or {"message_id": 1}
comms.max_call = lambda con, method, path, query=None, data=None, token=None: (
    {"chats": [{"chat_id": -77, "type": "channel", "title": "Футболика Небо"}, {"chat_id": 5, "type": "dialog"}]}
    if path == "chats" else (mx.append((path, query, data)) or {}))
comms.put(con, "tg_token", "x"); comms.put(con, "tg_ok", "1"); comms.put(con, "tg_upd_v", "2"); comms.put(con, "tg_secret", "s")
comms.put(con, "max_token", "y"); comms.put(con, "max_ok", "1")
br = con.execute("INSERT INTO branches(name) VALUES ('ТЦ «Небо»')").lastrowid
OWNER = {"id": 1, "name": "Юлия", "role": "admin"}

check(perms.can({"role": "admin"}, "posts", "full") and perms.can({"role": "operator", "is_senior": 1}, "posts", "full")
      and not perms.can({"role": "operator"}, "posts") and not perms.can({"role": "coach"}, "posts"), "Посты: руководитель и Варя, админы и тренеры нет")

comms.tg_hook(con, {"my_chat_member": {"chat": {"id": -1001, "type": "channel", "title": "Футболика Небо TG"}}},
              {"X-Telegram-Bot-Api-Secret-Token": "s"})
comms.tg_hook(con, {"channel_post": {"chat": {"id": -1001, "type": "channel", "title": "Футболика Небо"}, "text": "привет"}},
              {"X-Telegram-Bot-Api-Secret-Token": "s"})
check(channels.refresh_max(con) == 1, "MAX: канал найден в списке чатов бота, личный диалог нет")
known = {(k["kind"], k["chat_id"]) for k in channels.overview(con)["known"]}
check(known == {("tg", "-1001"), ("max", "-77")}, f"Каналы найдены сами ({known})")
try:
    channels.link(con, br, "tg", "-999"); ok = False
except channels.PostError:
    ok = True
check(ok, "Неизвестный канал не привязать")
channels.link(con, br, "tg", "-1001"); channels.link(con, br, "max", "-77")
t = {x["key"]: x for x in channels.targets(con)}
check(set(t) == {"tg:-1001", "max:-77"} and t["tg:-1001"]["label"] == "ТЦ «Небо»: Telegram", "Каналы привязаны к филиалу")

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
photo = channels.save_photo(PNG)
check(os.path.exists(channels.photo_path(photo)) and channels.photo_path("../x") is None, "Фото сохранено, чужие пути не открываются")
p = channels.create(con, OWNER, {"text": "🎉 С днём рождения!", "photo": photo, "targets": ["tg:-1001", "max:-77"]}, "https://crm.example")
check(p["status"] == "sent" and tg[-1][0] == "sendPhoto" and tg[-1][1]["chat_id"] == "-1001" and tg[-1][1]["caption"] == "🎉 С днём рождения!"
      and tg[-1][1]["photo"] == f"https://crm.example/api/post-photo?t={photo}", "Telegram: фото с подписью")
check(mx[-1][1] == {"chat_id": "-77"} and mx[-1][2]["attachments"][0]["payload"]["url"].endswith(photo), "MAX: текст и картинка")
long = "а" * 1500
channels.create(con, OWNER, {"text": long, "photo": photo, "targets": ["tg:-1001"]}, "https://crm.example")
check([m for m, _ in tg[-2:]] == ["sendPhoto", "sendMessage"] and "caption" not in tg[-2][1], "Длинный текст: картинка, потом текст отдельно")
try:
    channels.create(con, OWNER, {"text": "x", "targets": []}, "https://crm.example"); ok = False
except channels.PostError:
    ok = True
check(ok, "Без каналов не публикуем")
when = (datetime.now() + timedelta(hours=1)).isoformat(timespec="minutes")
s = channels.create(con, OWNER, {"text": "Завтра турнир!", "targets": ["max:-77"], "publish_at": when}, "https://crm.example")
n = len(mx)
check(s["status"] == "scheduled" and channels.tick(con) == 0 and len(mx) == n, "Запланированный пост ждёт своего времени")
con.execute("UPDATE channel_posts SET publish_at=? WHERE id=?", ((datetime.now() - timedelta(minutes=1)).isoformat(timespec="minutes"), s["id"]))
check(channels.tick(con) == 1 and channels.post(con, s["id"])["status"] == "sent" and mx[-1][2]["text"] == "Завтра турнир!", "Время пришло: опубликован")
def boom(*a, **k):
    raise comms.CommsError("Telegram: not enough rights")
comms.tg_call = boom
r = channels.create(con, OWNER, {"text": "x", "targets": ["tg:-1001", "max:-77"]}, "https://crm.example")
check(r["status"] == "partial" and "not enough rights" in r["results"][0]["error"], "Нет прав в Telegram: видно, где не вышло")
print("ok")
