"""«Клиенты и лиды» (Юлия, 04.11). Запуск: python -m tests.test_people"""
import os, sys, tempfile
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import db, nodeadline, people, schedule, services as sv
from crm import smart; smart.REQUIRE = False
from tests.test_crm import check


def main():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "p.sqlite3")); db.init_db(con)
    U = {"id": 1, "name": "Оля", "role": "operator"}
    sv.today = lambda: date(2026, 11, 4)
    b = schedule.save_branch(con, {"name": "ТЦ «Небо»"})
    g = schedule.save_group(con, {"name": "Небо Пн/Чт 17:00", "branch_id": b, "weekdays": [0, 3], "time": "17:00", "age": "4–5"})
    new = lambda name, phone, funnel: sv.create_student(con, {"name": name, "parent_name": "Мама", "phone": phone, "funnel": funnel,
                                                              "tariff": "fixed", "group_id": g}, U)
    lead = new("Лид Новый", "+7 900 500-00-01", "sales")
    act = new("Клиент Оплатил", "+7 900 500-00-02", "members")
    unp = new("Клиент Неоплатил", "+7 900 500-00-03", "members")
    gone = new("Клиент Ушёл", "+7 900 500-00-04", "members")
    for k, ym in ((act, "2026-11"), (unp, "2026-10"), (gone, "2026-09")):
        con.execute("INSERT INTO subscriptions(student_id,month,tariff,lessons,per_lesson,price,status,created_at,kind) VALUES (?,?,'fixed',9,1000,9000,'paid',?,'month')", (k, ym, ym + "-01"))
    sv.move_card(con, gone, "members", "left", U, "Дорого")
    con.execute("UPDATE students SET erp_status='Постоянный посетитель' WHERE id=?", (act,))
    d = people.listing(con)
    check(d["counts"]["leads"] == 1 and d["counts"]["clients"] == 3, "Лид: ни одной покупки; клиенты: была оплата")
    st = {r["id"]: r["status_label"] for r in d["rows"]}
    check(st[act] == "Постоянный посетитель" and all(st[sid] == people.ERP_MISSING_LABEL for sid in (unp,gone,lead)),
          "Только статусы ERP; нет подстановки внутренних этапов и оплаты")
    check([r["id"] for r in people.listing(con, "clients", "Постоянный посетитель")["rows"]] == [act], "Фильтр клиентов по статусу ERP")
    check({r["id"] for r in people.listing(con, "clients", people.ERP_MISSING)["rows"]} == {unp,gone}, "Фильтр отсутствующего статуса ERP")
    check(people.listing(con, "clients", "unpaid")["total"] == 0, "Внутренний статус больше не является фильтром")
    con.execute("UPDATE students SET erp_status='  Пробное занятие  ' WHERE id=?",(lead,))
    check(people.listing(con, "leads", "Пробное занятие")["rows"][0]['status_label']=='Пробное занятие', "Статус ERP лида без лишних пробелов")
    check([r["id"] for r in people.listing(con, "leads")["rows"]] == [lead], "Только лиды")
    check([r["id"] for r in people.listing(con, "clients", "", "Постоянный посетитель")["rows"]] == [act], "Фильтр по статусу ERP")
    check([r["id"] for r in people.listing(con, "all", "", "", "500-00-03")["rows"]] == [unp], "Поиск по телефону")
    con.execute("UPDATE students SET archived=1 WHERE id=?",(gone,))
    check(not any(r['id']==gone for r in people.listing(con)['rows']), "Архив скрыт по умолчанию")
    check(people.listing(con,erp=people.ERP_MISSING,archived=True)['rows'][0]['id']==gone, "Архив и фильтр ERP совместимы")
    print("ok")


if __name__ == "__main__":
    main()
