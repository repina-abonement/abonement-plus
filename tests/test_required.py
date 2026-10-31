"""Обязательные данные: перед пробным имя и фамилия ребёнка, дата рождения, имя и телефон родителя; перед продажей ещё почта. Запуск: python -m tests.test_required"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_crm import check  # noqa: E402
from crm import db, services as sv, smart  # noqa: E402

OP = {"id": 1, "name": "Ольга", "role": "operator"}


def main():
    smart.REQUIRE = True
    con = db.connect(os.path.join(tempfile.mkdtemp(), "r.sqlite3"))
    db.init_db(con)
    br = con.execute("INSERT INTO branches(name) VALUES ('Небо')").lastrowid
    g = con.execute("INSERT INTO groups(branch_id,name,weekdays) VALUES (?, 'Небо Сб', '5')", (br,)).lastrowid
    sid = sv.create_student(con, {"name": "Заявка с сайта", "parent_name": "Заявка", "phone": "+7 900 111-22-33", "source": "Сайт"}, OP)
    check(sid, "Заявка с сайта создаётся и без данных")
    try:
        sv.update_student(con, sid, {"group_id": g, "trial_at": "2026-11-07T10:00"}, OP)
        ok = False
    except sv.ApiError as e:
        ok = e.status == 422 and set(e.extra["need"]["missing"]) == {"child_name", "birth", "parent_name"}
    check(ok, "Без имени, фамилии, даты рождения и имени родителя на пробное не записать")
    check(not sv.get_student_row(con, sid)["trial_at"], "Пробное не записалось")
    try:
        sv.move_card(con, sid, "sales", "trial_booked", OP)
        ok = False
    except sv.ApiError as e:
        ok = e.status == 422
    check(ok, "Перетащить в «Записан на пробное» тоже нельзя")
    for bad, msg in (({"child_name": "Петя"}, "Без фамилии не принимается"), ({"birth": "2030-01-01"}, "Дата рождения в будущем не принимается")):
        try:
            smart.fill_required(con, sid, bad, OP)
            ok = False
        except sv.ApiError:
            ok = True
        check(ok, msg)
    smart.fill_required(con, sid, {"child_name": "Петя Иванов", "birth": "2019-05-01", "parent_name": "Анна", "phone": "+7 900 111-22-33"}, OP)
    sv.update_student(con, sid, {"group_id": g, "trial_at": "2026-11-07T10:00"}, OP)
    check(sv.get_student_row(con, sid)["stage"] == "trial_booked", "Данные заполнены: записали на пробное")
    # продажа: нужна почта
    m = sv.create_student(con, {"name": "Аня Петрова", "birth": "2018-02-02", "parent_name": "Мама Ани", "phone": "+7 900 222-33-44",
                                "funnel": "members", "group_id": g}, OP)
    try:
        sv.create_invoice(con, m, "2026-12", OP)
        ok = False
    except sv.ApiError as e:
        ok = e.status == 422 and e.extra["need"]["missing"] == ["email"]
    check(ok, "Без почты родителя абонемент не продать")
    try:
        smart.fill_required(con, m, {"email": "anna@mail"}, OP)
        ok = False
    except sv.ApiError:
        ok = True
    check(ok, "Почта без домена не принимается")
    smart.fill_required(con, m, {"email": "anna@mail.ru"}, OP)
    r = sv.create_invoice(con, m, "2026-12", OP)
    check(r, "С почтой абонемент продаётся")
    check(con.execute("SELECT email FROM parents WHERE family_id=(SELECT family_id FROM students WHERE id=?)", (m,)).fetchone()[0] == "anna@mail.ru",
          "Почта сохранилась у родителя")
    print("OK")


if __name__ == "__main__":
    main()
