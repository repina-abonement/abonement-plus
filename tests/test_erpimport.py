"""Перенос из ERP: чтение Excel и CSV, угадывание колонок и групп, пробный прогон, перенос, сверка, отмена. Запуск: python -m tests.test_erpimport"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_crm import check  # noqa: E402
from crm import db, erpimport as ei, money, services as sv, xlsx  # noqa: E402

OWNER = {"id": 1, "name": "Юлия", "role": "admin"}


def main():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "e.sqlite3"))
    db.init_db(con)
    money.init(con)
    br = con.execute("INSERT INTO branches(name) VALUES ('ТЦ «Небо»')").lastrowid
    g1 = con.execute("INSERT INTO groups(branch_id,name,weekdays,time) VALUES (?, 'Небо Пн/Чт 17:00', '0,3', '17:00')", (br,)).lastrowid
    g2 = con.execute("INSERT INTO groups(branch_id,name,weekdays,time) VALUES (?, 'Небо Сб 10:00', '5', '10:00')", (br,)).lastrowid
    fake = sv.create_student(con, {"name": "Выдуманный Тест", "parent_name": "Тест", "phone": "+7 999 000-00-01"}, OWNER)
    clients = xlsx.build([("Клиенты", [
        ["ID", "ФИО ребенка", "Дата рождения", "ФИО родителя", "Телефон", "E-mail", "Филиал", "Группа", "Статус", "Тариф", "Дата пробного", "Источник", "Комментарий"],
        ["101", "Иванов Петя", "01.05.2019", "Иванова Анна", "8 (900) 111-22-33", "anna@mail.ru", "Небо", "Пн/Чт 17:00", "Занимается", "Гибкий", "", "Сайт", "Аллергия на орехи"],
        ["102", "Иванова Маша", "02.02.2017", "Иванова Анна", "+7 900 111 22 33", "", "Небо", "Сб 10:00", "Занимается", "", "", "", ""],
        ["103", "Смирнов Гоша", "45000", "Смирнова Елена", "79005556677", "", "Небо", "Сб 10:00", "Записан на пробное", "", "15.10.2026 10:00", "Промоутер", ""],
        ["104", "Козлов Ваня", "", "Козлов Дима", "", "", "", "", "Ушёл", "", "", "", ""],
        ["101", "Иванов Петя", "01.05.2019", "Иванова Анна", "89001112233", "", "", "", "Занимается", "", "", "", ""],
    ])])
    up = ei.upload(con, "Клиенты.xlsx", clients)
    check(up["kind"] == "clients" and up["rows"] == 5, f"Excel с клиентами прочитан, тип угадан ({up})")
    m = ei.uploads(con)[0]["mapping"]
    check({"erp_id", "child_name", "birth", "parent_name", "phone", "email", "group", "status", "tariff", "trial_at", "source", "comment"} <= set(m),
          f"Колонки угаданы по названиям: {sorted(m)}")
    subs_csv = ("Код клиента;Ученик;Месяц;Группа;Стоимость;Занятий;Статус\n"
                "101;Иванов Петя;Сентябрь 2026;Пн/Чт 17:00;12 400;8;Оплачен\n"
                "101;Иванов Петя;10.2026;Пн/Чт 17:00;12400,00;8;оплачен\n"
                "102;Иванова Маша;Октябрь 2026;Сб 10:00;5400;4;не оплачен\n").encode("cp1251")
    check(ei.upload(con, "абонементы.csv", subs_csv)["kind"] == "subs", "CSV в кодировке Windows с «;» прочитан как абонементы")
    pays_csv = ("ID клиента,Плательщик,Дата оплаты,Сумма,Способ оплаты,Комментарий\n"
                "101,Иванов Петя,01.09.2026,12400,Наличные,\n"
                "101,Иванов Петя,2026-10-02,12400,СБП,\n"
                "999,Неизвестный,05.10.2026,1000,Карта,\n").encode()
    check(ei.upload(con, "оплаты.csv", pays_csv)["kind"] == "payments", "Оплаты угаданы")
    vis_csv = ("ID клиента;ФИО;Дата занятия;Группа;Отметка\n101;Иванов Петя;03.09.2026;Пн/Чт 17:00;был\n101;Иванов Петя;07.09.2026;Пн/Чт 17:00;н\n").encode()
    check(ei.upload(con, "посещения.csv", vis_csv)["kind"] == "visits", "Посещения угаданы")
    tasks_csv = ("ID клиента;ФИО;Задача;Срок;Комментарий\n103;Смирнов Гоша;Не пришли на ПУ;20.10.2026;перезвонить\n").encode()
    check(ei.upload(con, "задачи.csv", tasks_csv)["kind"] == "tasks", "Задачи угаданы")
    # группы
    gr = {g["erp"]: g["group_id"] for g in ei.erp_groups(con)}
    check(gr.get("Небо · Пн/Чт 17:00") == g1 and gr.get("Небо · Сб 10:00") == g2, f"Группы ERP сами нашли пары в CRM: {gr}")
    ei.save_groups(con, {k: v for k, v in gr.items() if v} | {"Пн/Чт 17:00": g1, "Сб 10:00": g2})
    # пробный прогон ничего не пишет
    p = ei.plan(con)
    check(p["stats"]["children"] == 4 and p["stats"]["families"] == 3, f"Пробный прогон: 4 ребёнка, 3 семьи ({p['stats']})")
    check(any("повторяется" in x for x in p["problems"]) and any("Неизвестный" in x for x in p["problems"]),
          "Нестыковки найдены: повтор строки и оплата без ребёнка")
    check(con.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 1, "Пробный прогон ничего не записал")
    try:
        ei.run(con, OWNER, False)
        ok = False
    except ei.ImportError_:
        ok = True
    check(ok, "Без отметки про Роскомнадзор перенос не начинается")
    r = ei.run(con, OWNER, True)
    st = r["stats"]
    check(st["children"] == 4 and st["removed_before"] == 1, f"Перенесено 4 ребёнка, выдуманный удалён ({st})")
    check(fake and not con.execute("SELECT 1 FROM students WHERE name='Выдуманный Тест'").fetchone(), "Выдуманного клиента больше нет")
    petya = con.execute("SELECT * FROM students WHERE erp_id='101'").fetchone()
    check(petya["birth"] == "2019-05-01" and petya["tariff"] == "flex" and petya["funnel"] == "members", "Дата рождения, тариф и статус Пети перенесены")
    masha = con.execute("SELECT * FROM students WHERE erp_id='102'").fetchone()
    check(masha["family_id"] == petya["family_id"], "Брат и сестра с одним телефоном попали в одну семью")
    check(con.execute("SELECT email FROM parents WHERE family_id=?", (petya["family_id"],)).fetchone()[0] == "anna@mail.ru", "Почта родителя перенесена")
    gosha = con.execute("SELECT * FROM students WHERE erp_id='103'").fetchone()
    check(gosha["stage"] == "missed_trial" and gosha["birth"] == "2023-03-15", f"Зависший на пробном: «Не дошёл до ПУ»; дата рождения из числа Excel ({gosha['stage']}, {gosha['birth']})")
    check(con.execute("SELECT stage FROM students WHERE erp_id='104'").fetchone()[0] == "left", "Ушедший перенесён как ушедший")
    subs = con.execute("SELECT month, price, status FROM subscriptions WHERE student_id=? ORDER BY month", (petya["id"],)).fetchall()
    check([tuple(x) for x in subs] == [("2026-09", 12400, "paid"), ("2026-10", 12400, "paid")], f"История абонементов Пети: {[tuple(x) for x in subs]}")
    check(con.execute("SELECT status FROM subscriptions WHERE student_id=?", (masha["id"],)).fetchone()[0] == "invoiced", "Неоплаченный абонемент так и остался неоплаченным")
    pays = con.execute("SELECT amount, method FROM payments WHERE student_id=? ORDER BY paid_at", (petya["id"],)).fetchall()
    check([tuple(x) for x in pays] == [(12400, "cash"), (12400, "sbp")], "Оплаты с датами и способами перенесены")
    bal = money.balance(con, petya["id"])
    check(bal["debt"] == 0 and bal["deposit"] == 0, f"Баланс Пети сходится: долга и переплаты нет ({bal})")
    att = con.execute("SELECT status FROM attendance WHERE student_id=? ORDER BY date", (petya["id"],)).fetchall()
    check([x[0] for x in att] == ["present", "absent"], "Посещения и пропуски перенесены")
    check(con.execute("SELECT kind FROM tasks WHERE student_id=?", (gosha["id"],)).fetchone()[0] == "no_show", "Задача ERP «Не пришли на ПУ» стала нашей задачей")
    check(con.execute("SELECT 1 FROM history WHERE student_id=? AND text='Аллергия на орехи'", (petya["id"],)).fetchone(), "Комментарий из ERP в истории")
    v = ei.verify(con)
    rows = {x["label"]: x for x in v["rows"]}
    check(rows["Дети и лиды"]["ok"] and rows["Абонементы"]["ok"], f"Сверка: дети и абонементы сходятся ({v['rows']})")
    check(not rows["Оплаты, штук"]["ok"], "Сверка показывает, что оплата без найденного ребёнка не перенеслась")
    try:
        ei.run(con, OWNER, True)
        ok = False
    except ei.ImportError_:
        ok = True
    check(ok, "Второй раз поверх не переносит")
    u = ei.undo(con, OWNER)
    check(con.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 0 and con.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 0,
          f"Отмена убрала всё перенесённое ({u})")
    print("OK")


if __name__ == "__main__":
    main()
