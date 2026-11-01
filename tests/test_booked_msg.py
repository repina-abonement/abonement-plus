"""Сразу после записи на пробное родителю уходит скрипт: дата, время, место, что взять. Запуск: python -m tests.test_booked_msg"""
import os, sys, tempfile
from datetime import date, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import db, schedule, comms, tasks, services as sv
from crm import smart; smart.REQUIRE = False
from tests.test_crm import check

con = db.connect(os.path.join(tempfile.mkdtemp(), "b.sqlite3")); db.init_db(con)
U = {"id": 1, "name": "Оля", "role": "operator"}
b = schedule.save_branch(con, {"name": "ТЦ «Небо»"})
d = date.today() + timedelta(days=3)
g = schedule.save_group(con, {"name": "Небо 17:45 4–5", "branch_id": b, "weekdays": [d.weekday()], "time": "17:45", "age": "4–5"})
sid = sv.create_student(con, {"name": "Дарья Петрова", "parent_name": "Юлия", "phone": "+7 900 000-11-11", "funnel": "sales"}, U)
fam = con.execute("SELECT family_id FROM students WHERE id=?", (sid,)).fetchone()[0]
cid = comms.upsert_conversation(con, "wazzup_max", "m-1", phone="79000001111", meta={"chatType": "max"})
comms.link(con, cid, fam)
sent = []
comms.send = lambda con, c, text, user: sent.append((c, text)) or {"id": 1}
sv.update_student(con, sid, {"group_id": g, "trial_at": f"{d.isoformat()}T17:45"}, U)
check(len(sent) == 1 and sent[0][0] == cid, "Сразу после записи ушло одно сообщение в переписку семьи")
t = sent[0][1]
check("Авиаторов 3А" in t and "17:45" in t and "бутылку с водой" in t, "В сообщении место, время и что взять")
check("Группа для родителей" not in t, "Нет ссылки на чат родителей: строки с чатом нет")
check("—" not in t and "{" not in t, "Без длинного тире и неподставленных полей")
sv.update_student(con, sid, {"name": "Дарья Петрова"}, U)
check(len(sent) == 1, "Повторно не уходит, если время не меняли")
tasks.save_settings(con, {"booked_places": {str(b): {"place": "ТРЦ Небо", "area": "в Солнцево", "tg": "t.me/x", "max": "max.ru/chat"}}})
sv.update_student(con, sid, {"trial_at": f"{(d + timedelta(days=7)).isoformat()}T17:45"}, U)
check(len(sent) == 2 and "max.ru/chat" in sent[1][1] and "t.me/x" not in sent[1][1], "Перенесли время: новое сообщение, в MAX ссылка на MAX-чат")
print("ok")
