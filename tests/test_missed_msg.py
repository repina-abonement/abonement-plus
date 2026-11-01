"""Пропущенный звонок: через 2 минуты клиенту «Вы нам звонили…». Запуск: python -m tests.test_missed_msg"""
import os, sys, tempfile
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import db, comms, tasks, services as sv
from crm import smart; smart.REQUIRE = False
from tests.test_crm import check

con = db.connect(os.path.join(tempfile.mkdtemp(), "m.sqlite3")); db.init_db(con)
U = {"id": 1, "name": "Оля", "role": "operator"}
sid = sv.create_student(con, {"name": "Петя Иванов", "parent_name": "Анна Иванова", "phone": "+7 900 111-22-33", "funnel": "sales"}, U)
fam = con.execute("SELECT family_id FROM students WHERE id=?", (sid,)).fetchone()[0]
cid = comms.upsert_conversation(con, "wazzup_max", "m-1", phone="79001112233", meta={"chatType": "max"}); comms.link(con, cid, fam)
comms.put(con, "wazzup_key", "k"); comms.put(con, "wazzup_channel", "c")
sent = []
comms.send = lambda con, c, text, user: sent.append((c, text)) or {"id": 1}
t0 = datetime(2026, 11, 2, 12, 0)
def call(phone, at, status="missed", direction="in"):
    con.execute("INSERT INTO calls(phone, phone_norm, direction, status, started_at) VALUES (?,?,?,?,?)",
                (phone, comms.normalize_phone(phone), direction, status, at.isoformat()))
call("+79001112233", t0)
tasks.missed_call_messages(con, t0 + timedelta(minutes=3))
check(not sent, "Пока автосообщения не запущены руководителем, ничего не уходит")
comms.put(con, "auto_live", "1")
tasks.missed_call_messages(con, t0 + timedelta(minutes=1))
check(not sent, "Первые 2 минуты ждём")
tasks.missed_call_messages(con, t0 + timedelta(minutes=3))
check(len(sent) == 1 and sent[0][0] == cid and sent[0][1].startswith("Анна, здравствуйте! Это «Футболика»"), "Через 2 минуты: в MAX семьи, по имени")
tasks.missed_call_messages(con, t0 + timedelta(minutes=10))
call("+79001112233", t0 + timedelta(minutes=20))
tasks.missed_call_messages(con, t0 + timedelta(minutes=25))
check(len(sent) == 1, "Одному номеру раз в день")
call("+79005556677", t0 + timedelta(minutes=30))
call("+79005556677", t0 + timedelta(minutes=31), status="answered", direction="out")
tasks.missed_call_messages(con, t0 + timedelta(minutes=35))
check(len(sent) == 1, "Уже перезвонили: не пишем")
call("+79009998877", datetime(2026, 11, 2, 22, 30))
tasks.missed_call_messages(con, datetime(2026, 11, 2, 22, 40))
check(len(sent) == 1, "Ночью не пишем")
tasks.missed_call_messages(con, datetime(2026, 11, 3, 9, 1))
check(len(sent) == 2 and sent[-1][1].startswith("Здравствуйте! Это «Футболика»"), "Утром в 9: новому номеру в WhatsApp")
check("—" not in sent[0][1], "Без длинного тире")
print("ok")
