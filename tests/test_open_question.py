"""«Открытый вопрос» в диалоге: на столе у всех админов, ребёнок остаётся в своей воронке. Запуск: python -m tests.test_open_question"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import db, comms, services as sv
from crm import smart; smart.REQUIRE = False
from tests.test_crm import check

con = db.connect(os.path.join(tempfile.mkdtemp(), "q.sqlite3")); db.init_db(con)
U = {"id": 1, "name": "Оля", "role": "operator"}
sid = sv.create_student(con, {"name": "Петя Иванов", "parent_name": "Мама", "phone": "+7 900 000-00-01", "funnel": "sales"}, U)
stage = con.execute("SELECT funnel, stage FROM students WHERE id=?", (sid,)).fetchone()
fam = con.execute("SELECT family_id FROM students WHERE id=?", (sid,)).fetchone()[0]
cid = comms.upsert_conversation(con, "whatsapp", "79000000001", phone="79000000001")
comms.link(con, cid, fam)
comms.set_question(con, cid, "уточнить у тренера про субботу", "now", U)
u = [x for x in comms.urgent(con) if x["kind"] == "open_q"]
check(len(u) == 1 and u[0]["level"] == "red" and "субботу" in u[0]["text"], "На столе «Открытый вопрос», время пришло: красный")
check(tuple(con.execute("SELECT funnel, stage FROM students WHERE id=?", (sid,)).fetchone()) == tuple(stage), "Ребёнок остался в своей воронке и на своём этапе")
check(comms.inbox(con, "open")["conversations"][0]["id"] == cid, "В «Чатах» фильтр «Открытые вопросы»")
check(any("Открытый вопрос" in r[0] for r in con.execute("SELECT text FROM history WHERE student_id=?", (sid,))), "В истории карточки отмечено")
comms.set_question(con, cid, "", "morn", U)
check([x for x in comms.urgent(con) if x["kind"] == "open_q"][0]["level"] == "yellow", "Вернуться завтра: пока жёлтый")
comms.close_question(con, cid, U)
check(not [x for x in comms.urgent(con) if x["kind"] == "open_q"], "«Вопрос решён»: со стола ушло")
print("ok")
