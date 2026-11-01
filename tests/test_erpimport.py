"""Перенос из ERP: чтение Excel и CSV, угадывание колонок и групп, пробный прогон, перенос, сверка, отмена. Запуск: python -m tests.test_erpimport"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_crm import check  # noqa: E402
from datetime import date, datetime  # noqa: E402

from crm import db, erpimport as ei, money, services as sv, xlsx  # noqa: E402

OWNER = {"id": 1, "name": "Юлия", "role": "admin"}
NOW = datetime(2026, 11, 1, 9, 0)  # «текущий месяц» в тесте - ноябрь 2026


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
    tasks_csv = ("ID клиента;ФИО;Задача;Срок;Комментарий;Исполнитель в ERP\n103;Смирнов Гоша;Не пришли на ПУ;20.10.2026;перезвонить;Оператор Ольга\n").encode()
    check(ei.upload(con, "задачи.csv", tasks_csv)["kind"] == "tasks", "Задачи угаданы")
    # группы
    gr = {g["erp"]: g["group_id"] for g in ei.erp_groups(con)}
    check(gr.get("Небо · Пн/Чт 17:00") == g1 and gr.get("Небо · Сб 10:00") == g2, f"Группы ERP сами нашли пары в CRM: {gr}")
    ei.save_groups(con, {k: v for k, v in gr.items() if v} | {"Пн/Чт 17:00": g1, "Сб 10:00": g2})
    # пробный прогон ничего не пишет
    p = ei.plan(con, NOW.date())
    check(p["stats"]["children"] == 4 and p["stats"]["families"] == 3, f"Пробный прогон: 4 ребёнка, 3 семьи ({p['stats']})")
    check(any("повторяется" in x for x in p["problems"]) and any("Неизвестный" in x for x in p["problems"]),
          "Нестыковки найдены: повтор строки и оплата без ребёнка")
    check(con.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 1, "Пробный прогон ничего не записал")
    try:
        ei.run(con, OWNER, False, now=NOW)
        ok = False
    except ei.ImportError_:
        ok = True
    check(ok, "Без отметки про Роскомнадзор перенос не начинается")
    r = ei.run(con, OWNER, True, now=NOW)
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
    check(gosha["stage_changed_at"][:10] == "2026-10-15", f"Дата этапа: настоящая (дата пробного), а не день переноса ({gosha['stage_changed_at']})")
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
    check("исполнитель в ERP: Оператор Ольга" in (con.execute("SELECT note FROM tasks WHERE student_id=?", (gosha["id"],)).fetchone()[0] or ""),
          "Исполнитель из ERP виден в задаче")
    check(con.execute("SELECT 1 FROM history WHERE student_id=? AND text='Аллергия на орехи'", (petya["id"],)).fetchone(), "Комментарий из ERP в истории")
    v = ei.verify(con)
    rows = {x["label"]: x for x in v["rows"]}
    check(rows["Дети и лиды"]["ok"] and rows["Абонементы"]["ok"], f"Сверка: дети и абонементы сходятся ({v['rows']})")
    check(not rows["Оплаты, штук"]["ok"], "Сверка показывает, что оплата без найденного ребёнка не перенеслась")
    try:
        ei.run(con, OWNER, True, now=NOW)
        ok = False
    except ei.ImportError_:
        ok = True
    check(ok, "Второй раз поверх не переносит")
    daily(con, petya, masha, gosha, g1, g2)
    u = ei.undo(con, OWNER)
    check(con.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 0 and con.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 0,
          f"Отмена убрала всё перенесённое ({u})")
    rules()
    print("OK")



def daily(con, petya, masha, gosha, g1, g2):
    """Ежедневное «Обновить из ERP»: ничего не удаляет, тренерские отметки остаются."""
    con.execute("INSERT INTO attendance(student_id, group_id, date, status) VALUES (?,?,?, 'present')", (petya["id"], g1, "2026-11-02"))
    con.execute("UPDATE students SET stage='trial_booked', funnel='sales' WHERE id=?", (gosha["id"],))  # админ в CRM переписал
    con.execute("DELETE FROM erp_uploads")
    try:
        ei.update(con, OWNER, now=NOW)
        ok = False
    except ei.ImportError_:
        ok = True
    check(ok, "Без свежих файлов обновлять нечего")
    clients = xlsx.build([("Клиенты", [
        ["ID", "ФИО ребенка", "Дата рождения", "ФИО родителя", "Телефон", "Филиал", "Группа", "Статус", "Дата пробного"],
        ["101", "Иванов Петя", "01.05.2019", "Иванова Анна", "89001112233", "Небо", "Пн/Чт 17:00", "Занимается", ""],
        ["102", "Иванова Маша", "02.02.2017", "Иванова Анна", "89001112233", "Небо", "Сб 10:00", "Занимается", ""],
        ["103", "Смирнов Гоша", "", "Смирнова Елена", "79005556677", "Небо", "Сб 10:00", "Записан на пробное", "15.10.2026 10:00"],
        ["104", "Козлов Ваня", "", "Козлов Дима", "", "", "", "Ушёл", ""],
        ["105", "Новиков Лёва", "03.03.2018", "Новикова Ира", "89007778899", "Небо", "Сб 10:00", "Записан на пробное", "07.11.2026 10:00"],
    ])])
    ei.upload(con, "Клиенты.xlsx", clients)
    ei.upload(con, "абонементы.csv", ("Код клиента;Ученик;Месяц;Группа;Стоимость;Занятий;Статус\n"
                                     "102;Иванова Маша;Октябрь 2026;Сб 10:00;5400;4;Оплачен\n"
                                     "101;Иванов Петя;Ноябрь 2026;Пн/Чт 17:00;12400;8;Оплачен\n").encode())
    ei.upload(con, "оплаты.csv", ("ID клиента,Плательщик,Дата оплаты,Сумма,Способ оплаты\n"
                                 "101,Иванов Петя,2026-10-02,12400,СБП\n101,Иванов Петя,31.10.2026,12400,СБП\n").encode())
    n0 = con.execute("SELECT COUNT(*) FROM students").fetchone()[0]
    r = ei.update(con, OWNER, now=NOW)["stats"]
    check(r["new"] == 1 and con.execute("SELECT COUNT(*) FROM students").fetchone()[0] == n0 + 1, f"Новый клиент из ERP добавлен ({r})")
    leva = con.execute("SELECT * FROM students WHERE erp_id='105'").fetchone()
    check(leva["stage"] == "trial_booked" and leva["trial_at"].startswith("2026-11-07"), "Новый лид записан на пробное с датой")
    check(con.execute("SELECT stage FROM students WHERE id=?", (gosha["id"],)).fetchone()[0] == "trial_booked",
          "Статус в ERP не менялся: этап, который поставили в CRM, не перетёрт")
    check(con.execute("SELECT status FROM subscriptions WHERE student_id=? AND month='2026-10'", (masha["id"],)).fetchone()[0] == "paid",
          "Абонемент Маши стал оплаченным")
    check(con.execute("SELECT COUNT(*) FROM subscriptions WHERE student_id=? AND month='2026-11'", (petya["id"],)).fetchone()[0] == 1,
          "Новый абонемент Пети на ноябрь добавлен")
    check(con.execute("SELECT COUNT(*) FROM payments WHERE student_id=?", (petya["id"],)).fetchone()[0] == 3,
          "Старая оплата не задвоилась, новая добавлена")
    check(con.execute("SELECT 1 FROM attendance WHERE student_id=? AND date='2026-11-02'", (petya["id"],)).fetchone(),
          "Отметка тренера в CRM осталась")
    check(not ei.uploads(con) and con.execute("SELECT value FROM settings WHERE key='erp_last_update'").fetchone(),
          "Файлы дня использованы, время обновления запомнено")
    # статус в ERP сменился: этап обновляется
    ei.upload(con, "Клиенты.xlsx", xlsx.build([("Клиенты", [
        ["ID", "ФИО ребенка", "Телефон", "Филиал", "Группа", "Статус"],
        ["104", "Козлов Ваня", "", "Небо", "Сб 10:00", "Отказ"]])]))
    r = ei.update(con, OWNER, now=NOW)["stats"]
    check(r["new"] == 0 and r["stage"] == 1, f"Повторное обновление не плодит дублей, сменившийся статус в ERP обновил этап ({r})")
    # итоги дня для ERP: что отметил тренер
    d = ei.day_for_erp(con, "2026-11-02")
    rows = {x["name"]: x["mark"] for g in d["groups"] for x in g["rows"]}
    check(rows.get("Иванов Петя") == "был", f"Итоги дня: отметка тренера попала в список для ERP ({d['groups']})")


def rules():
    """Решения Юлии 01.11.2026: статус и группа по абонементам, лиды без имени сохраняются, дата продажи вместо пустой даты оплаты."""
    con = db.connect(os.path.join(tempfile.mkdtemp(), "r.sqlite3"))
    db.init_db(con)
    money.init(con)
    br = con.execute("INSERT INTO branches(name) VALUES ('ТЦ «Небо»')").lastrowid
    neb = con.execute("INSERT INTO groups(branch_id,name,weekdays,time) VALUES (?, 'Небо Вт/Пт 16:45 3–4', '1,4', '16:45')", (br,)).lastrowid
    chob = con.execute("INSERT INTO groups(branch_id,name,weekdays,time) VALUES (?, 'Чоботовская Сб/Вс 3–5', '5,6', '10:00')", (br,)).lastrowid
    cl = ("ID,ФИО посетителя,Телефон,Имя родителя,Статус,Филиал,Группа\n"
          "1,Два Группы,79000000001,Мама,Постоянный посетитель,ТЦ Небо;Чоботовская 4с2,Небо НУП вт/пт;Чоботовская сб/вс\n"
          "2,Не Оплатил,79000000002,Мама,Постоянный посетитель,ТЦ Небо,Небо НУП вт/пт\n"
          "3,Давно Ушёл,79000000003,Мама,Постоянный посетитель,ТЦ Небо,Небо НУП вт/пт\n"
          "4,Вернулся Сам,79000000004,Мама,Ушедший,ТЦ Небо,Небо НУП вт/пт\n"
          "5,Только Пробное,79000000005,Мама,Был на пробном не купил абонемент,,\n").encode()
    ei.upload(con, "клиенты.csv", cl, "clients")
    leads = ("ID,Имя родителя,Имя ребенка,Телефон,Комментарий\n77,Папа Без Имени,,79000000077,перезвонить\n78,,,,\n").encode()
    u = ei.upload(con, "лиды.csv", leads, "clients")
    ei.set_upload(con, u["id"], None, {"parent_name": 1, "child_name": 2, "phone": 3, "comment": 4})
    subs = ("ID посетителя,ФИО посетителя,Группа,Тип,Дата начала,Стоимость со скидкой,Оплачено(руб.)\n"
            "1,Два Группы,Небо НУП вт/пт,Абонемент (стандарт /8),03.10.2026,8000,8000\n"
            "1,Два Группы,Чоботовская сб/вс,Абонемент (стандарт /8),07.11.2026,8000,8000\n"
            "1,Два Группы,Небо НУП вт/пт,Отработка,10.11.2026,0,0\n"
            "2,Не Оплатил,Небо НУП вт/пт,Абонемент (стандарт /8),03.10.2026,8000,4000\n"
            "2,Не Оплатил,Старая группа без пары,Разовое,03.10.2026,1500,1500\n"
            "3,Давно Ушёл,Небо НУП вт/пт,Абонемент (стандарт /8),03.03.2025,8000,8000\n"
            "4,Вернулся Сам,Небо НУП вт/пт,Разовое,01.12.2026,1500,1500\n"
            "5,Только Пробное,Небо НУП вт/пт,Вводное,12.10.2026,0,0\n").encode()
    u = ei.upload(con, "абонементы.csv", subs, "subs")
    ei.set_upload(con, u["id"], None, {"erp_id": 0, "child_name": 1, "group": 2, "tariff": 3, "month": 4, "price": 5, "status": 6})
    cl2 = ("ID,ФИО посетителя,Телефон,Статус,Филиал,Группа\n"
           "11,Тёзка Один,79000000011,Ушедший,ТЦ Небо,Чоботовская сб/вс\n"
           "12,Тёзка Один,79000000012,Ушедший,ТЦ Небо,Небо НУП вт/пт\n").encode()
    ei.upload(con, "тёзки.csv", cl2, "clients")
    vis = ("Номер клиента в ERP,Ребёнок,Дата занятия,Группа,Отметка\n,Тёзка Один,04.10.2025,Небо НУП вт/пт,был\n,Тёзка Один,05.10.2025,Чоботовская сб/вс,пропуск\n").encode()
    ei.upload(con, "журнал.csv", vis, "visits")
    pays = ("ID посетителя,Посетитель,Дата продажи,Дата оплаты,Сумма,Вид оплаты\n"
            "1,Два Группы,01.10.2026,02.10.2026,8000,Наличные\n"
            "2,Не Оплатил,05.10.2026,,4000,PayKeeper\n").encode()
    u = ei.upload(con, "оплаты.csv", pays, "payments")
    ei.set_upload(con, u["id"], None, {"erp_id": 0, "child_name": 1, "date": 3, "amount": 4, "method": 5})  # как в старых загрузках: без date_alt
    ei.save_groups(con, {"Небо НУП вт/пт": neb, "Чоботовская сб/вс": chob, "ТЦ Небо;Чоботовская 4с2 · Небо НУП вт/пт;Чоботовская сб/вс": "",
                         "ТЦ Небо · Небо НУП вт/пт": neb})
    p = ei.plan(con, NOW.date())
    s = p["stats"]
    check(s["children"] == 8 and s["no_name"] == 1, f"Лид без имени ребёнка не потерян, пустая строка пропущена ({s})")
    check(s["members_active"] == 3 and s["members_unpaid"] == 1 and s["members_left"] == 3 and s["leads"] == 2,
          f"Статусы по абонементам: 3 занимаются (1 не оплатил ноябрь), 1 ушёл, 2 лида ({s})")
    check(s["subs"] == 6 and s["subs_trials"] == 1 and s["subs_makeups"] == 1, "Вводные и отработки не считаются абонементами")
    check(s["pay_date_from_sale"] == 1 and not any("не понял дату" in x for x in p["problems"]), "Пустая дата оплаты заменена датой продажи")
    check(p["problem_types"].get("Лиды и клиенты без имени ребёнка (сохраняются с пометкой)") == 1, f"Итог нестыковок по видам ({p['problem_types']})")
    ei.run(con, OWNER, True, now=NOW)
    g = lambda erp: con.execute("SELECT * FROM students WHERE erp_id=?", (erp,)).fetchone()
    two = g("1")
    act = [r[0] for r in con.execute("SELECT group_id FROM enrollments WHERE student_id=? AND active=1", (two["id"],))]
    check(act == [chob] and two["group_id"] == chob, f"Действующая группа - та, куда продан абонемент на ноябрь ({act})")
    check(g("2")["stage"] == "active" and g("3")["stage"] == "left" and g("4")["stage"] == "active", "Не оплатил ноябрь - занимается, давно ушёл - ушёл, купил на декабрь - занимается")
    two_subs = con.execute("SELECT price, status FROM subscriptions WHERE student_id=?", (g("2")["id"],)).fetchall()
    check([tuple(x) for x in two_subs] == [(9500, "invoiced")], f"Продажи одного месяца сложены, частично оплаченный - не оплачен ({[tuple(x) for x in two_subs]})")
    v = {x["label"]: x for x in ei.verify(con)["rows"]}
    check(v["Абонементы"]["ok"], f"Сверка абонементов учитывает сложенные продажи ({v['Абонементы']})")
    t1, t2 = g("11")["id"], g("12")["id"]
    vv = {r[0]: r[1] for r in con.execute("SELECT student_id, status FROM attendance WHERE student_id IN (?,?)", (t1, t2))}
    check(vv == {t2: "present", t1: "absent"}, f"Посещения тёзок без номера разведены по группе ({vv})")
    check(g("5")["funnel"] == "sales" and g("5")["trial_at"].startswith("2026-10-12"), "Только вводное: остаётся лидом, дата пробного из вводного")
    nn = con.execute("SELECT s.*, f.name AS fam FROM students s JOIN families f ON f.id=s.family_id WHERE s.name=?", (ei.NO_NAME,)).fetchone()
    check(nn and nn["fam"] == "Папа Без Имени" and nn["funnel"] == "sales", "Лид без имени перенесён в воронку продаж с семьёй родителя")
    check(con.execute("SELECT paid_at FROM payments WHERE student_id=?", (g("2")["id"],)).fetchone()[0].startswith("2026-10-05"), "Оплата получила дату продажи")


if __name__ == "__main__":
    main()
