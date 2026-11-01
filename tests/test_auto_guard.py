"""Напоминания о пробном: не тем, кто отказался, и не без группы (01.11). Запуск: python -m tests.test_auto_guard"""
import os, sys, tempfile
from datetime import date, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import db, comms, schedule, tasks, services as sv
from crm import smart; smart.REQUIRE = False
from tests.test_crm import check

con = db.connect(os.path.join(tempfile.mkdtemp(), "g.sqlite3")); db.init_db(con)
comms.put(con, "auto_live", "1")
U = {"id": 1, "name": "Оля", "role": "operator"}
b = schedule.save_branch(con, {"name": "ТЦ «Небо»"})
d = date.today() + timedelta(days=3)
g = schedule.save_group(con, {"name": "Небо 17:45", "branch_id": b, "weekdays": [d.weekday()], "time": "17:45", "age": "4–5"})
sent = []
comms.send = lambda con, c, text, user: sent.append(text) or {"id": 1}
def kid(name, phone, group=True):
    sid = sv.create_student(con, {"name": name, "parent_name": "Мама", "phone": phone, "funnel": "sales"}, U)
    fam = con.execute("SELECT family_id FROM students WHERE id=?", (sid,)).fetchone()[0]
    cid = comms.upsert_conversation(con, "max", "c" + phone, phone=phone); comms.link(con, cid, fam)
    con.execute("UPDATE students SET trial_at=?, group_id=? WHERE id=?", (f"{d.isoformat()}T17:45", g if group else None, sid))
    return dict(con.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone())
ok = kid("Петя Иванов", "79000000001")
r = tasks.send_auto(con, ok, "day_before", "msg_day_before")
check(r.get("ok") and "Авиаторов 3А" in sent[-1] and "в школе" not in sent[-1], "Адрес из «Места филиала», не «в школе»")
ref = kid("Вася Петров", "79000000002")
con.execute("UPDATE students SET stage='refused' WHERE id=?", (ref["id"],))
check(tasks.send_auto(con, ref, "2h_before", "msg_2h_before").get("skipped") and len(sent) == 1, "Отказавшемуся напоминание не уходит")
nog = kid("Коля Сидоров", "79000000003", group=False)
check(tasks.send_auto(con, nog, "2h_before", "msg_2h_before").get("error") == "не выбрана группа" and len(sent) == 1, "Без группы не уходит, в истории «выберите группу»")
# запись на пробное снимает «Отказ» и «не беспокоить»
con.execute("UPDATE students SET stage='refused', dnd=1, refusal_reason='Дорого', trial_at=NULL WHERE id=?", (ref["id"],))
sv.update_student(con, ref["id"], {"group_id": g, "trial_at": f"{d.isoformat()}T17:45"}, U)
st = con.execute("SELECT stage, dnd, refusal_reason FROM students WHERE id=?", (ref["id"],)).fetchone()
check(tuple(st) == ("trial_booked", 0, ""), "Записали на пробное: этап «Записан на пробное», отказ и «не беспокоить» сняты")
# выдуманным клиентам не пишем никогда
fk = kid("Тест Тестов", "79000000009")
con.execute("UPDATE families SET fake=1 WHERE id=(SELECT family_id FROM students WHERE id=?)", (fk["id"],))
n = len(sent)
tasks.send_auto(con, fk, "day_before", "msg_day_before")
check(len(sent) == n, "Выдуманному (тестовому) клиенту автосообщение не уходит")
# без группы на пробное не записать
nb = sv.create_student(con, {"name": "Без Группы", "parent_name": "Мама", "phone": "79000000010", "funnel": "sales"}, U)
try:
    sv.update_student(con, nb, {"trial_at": f"{d.isoformat()}T17:45"}, U); okg = False
except sv.ApiError as e:
    okg = "группу" in e.message
check(okg, "Без группы на пробное не записать")
print("ok")
