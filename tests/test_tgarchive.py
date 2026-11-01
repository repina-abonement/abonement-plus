"""Старая переписка из экспорта Telegram в карточки. Запуск: python -m tests.test_tgarchive"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import db, comms, tgarchive, services as sv
from crm import smart; smart.REQUIRE = False
from tests.test_crm import check

con = db.connect(os.path.join(tempfile.mkdtemp(), "t.sqlite3")); db.init_db(con)
sid = sv.create_student(con, {"name": "Петя Иванов", "parent_name": "Анна", "phone": "+7 900 111-22-33", "funnel": "members"}, {"name": "Оля"})
fam = con.execute("SELECT family_id FROM students WHERE id=?", (sid,)).fetchone()[0]
data = {"contacts": [{"name": "Анна мама Пети", "phone": "+7 (900) 111-22-33"}, {"name": "Чужой", "phone": "+79990000000"}],
        "chats": [{"id": 101, "name": "Анна мама Пети", "msgs": [[1, "2026-09-01T10:00:00", False, "Здравствуйте, сколько стоит?", ""],
                                                                [2, "2026-09-01T10:05:00", True, "Добрый день! 1350 за занятие", ""],
                                                                [3, "2026-09-01T10:06:00", False, "", "фото"]]},
                  {"id": 102, "name": "Чужой", "msgs": [[1, "2026-09-02T10:00:00", False, "привет", ""]]},
                  {"id": 103, "name": "Без контакта", "msgs": [[1, "2026-09-02T10:00:00", False, "привет", ""]]}]}
r = tgarchive.import_(con, data, {"name": "Юлия"})
check(r["clients"] == 1 and r["messages"] == 3 and r["not_clients"] == 1 and r["no_phone"] == 1, "Переносим только клиентов CRM, по номеру из контактов")
r2 = tgarchive.import_(con, data, {"name": "Юлия"})
check(r2["messages"] == 0, "Повторная загрузка ничего не задваивает")
items = comms.family_comms(con, fam)["items"]
check([i["direction"] for i in items] == ["in", "out", "in"] and items[0]["channel_label"] == "Telegram (до CRM)", "Переписка в карточке, по порядку, с пометкой «до CRM»")
c = con.execute("SELECT id, unread FROM conversations WHERE channel='tg_archive'").fetchone()
check(c["unread"] == 0, "Архив не делает непрочитанных")
try:
    comms.send(con, c["id"], "тест", {"name": "Оля"}); ok = False
except comms.CommsError:
    ok = True
check(ok, "В архив ответить нельзя")
# номера нет: находим по фамилии и имени ребёнка в названии чата
sv.create_student(con, {"name": "Урюпина Аглая", "parent_name": "Марина", "phone": "+7 900 333-44-55", "funnel": "members"}, {"name": "Оля"})
sv.create_student(con, {"name": "Иван Петров", "parent_name": "А", "phone": "+7 900 333-44-56", "funnel": "members"}, {"name": "Оля"})
sv.create_student(con, {"name": "Петров Иван", "parent_name": "Б", "phone": "+7 900 333-44-57", "funnel": "members"}, {"name": "Оля"})
r3 = tgarchive.import_(con, {"contacts": [], "chats": [
    {"id": 201, "name": "Урюпина Аглая, мама Марина", "msgs": [[1, "2026-09-03T10:00:00", False, "Записываем Аглаю?", ""]]},
    {"id": 202, "name": "Петров Иван папа", "msgs": [[1, "2026-09-03T10:00:00", False, "привет", ""]]},
    {"id": 203, "name": "Аглая", "msgs": [[1, "2026-09-03T10:00:00", False, "привет", ""]]}]}, {"name": "Юлия"})
check(r3["by_name"] == 1 and r3["ambiguous"] == 1 and r3["no_phone"] == 1, "По имени ребёнка: один нашли, два Ивана Петрова не гадаем, по одному имени не угадываем")
print("ok")
