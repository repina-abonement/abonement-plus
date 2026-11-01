"""Проверки задач и автосообщений по пробным. Запуск: python -m tests.test_tasks"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import comms, db, services as sv, tasks  # noqa: E402
from tests.test_crm import check  # noqa: E402

SENT = []


def fake(method, url, *, data=None, form=None, headers=None, timeout=12, raw=False):
    SENT.append(data)
    return 200, {"ok": True, "result": {"message_id": len(SENT)}}


def main():
    comms.http = fake
    con = db.connect(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
    db.init_db(con)
    __import__("crm.comms", fromlist=["put"]).put(con, "auto_live", "1")  # тест проверяет отправку: автосообщения включены
    br = con.execute("INSERT INTO branches(name) VALUES ('ТЦ «Небо»')").lastrowid
    g = con.execute("INSERT INTO groups(branch_id,name,weekdays,time) VALUES (?, 'Небо Пн/Чт', '0,3', '17:00')", (br,)).lastrowid
    olga = con.execute("INSERT INTO users(login,name,role,pw_hash) VALUES ('olga','Ольга','operator','x$y')").lastrowid
    comms.put(con, "tg_token", "1:a"); comms.put(con, "tg_ok", "1")
    sid = sv.create_student(con, {"name": "Даша Морозова", "parent_name": "Ольга М", "phone": "+7 900 777-77-77",
                                  "group_id": g}, {"name": "Тест"})
    fam = sv.get_student_row(con, sid)["family_id"]
    cid = comms.upsert_conversation(con, "telegram", "4242", title="Ольга М", phone="+79007777777")
    trial = datetime(2026, 11, 5, 17, 0)
    con.execute("UPDATE students SET trial_at=?, stage='trial_booked' WHERE id=?", (trial.isoformat(timespec="minutes"), sid))

    print("Задачи и автосообщения по пробному")
    r = tasks.run(con, datetime(2026, 11, 3, 15, 0))
    t = con.execute("SELECT * FROM tasks WHERE kind='trial_confirm'").fetchone()
    check(t and t["assignee_id"] == olga and t["due_at"] == "2026-11-04T11:00", "Задача «напомнить о пробном» на Олю, на 11:00 накануне")
    check(r["sent"] == 0, "За два дня сообщения ещё не уходят")
    r = tasks.run(con, datetime(2026, 11, 4, 11, 0))
    check(r["sent"] == 1 and "завтра, 05.11, в 17:00 пробное занятие по футболу, ТРЦ \"Небо\", Авиаторов 3А" in SENT[-1]["text"] and "Ребёнок: Даша" in SENT[-1]["text"] and SENT[-1]["chat_id"] == 4242,
          "Накануне в 11:00 ушло напоминание в Telegram")
    r = tasks.run(con, datetime(2026, 11, 4, 12, 0))
    check(r["sent"] == 0, "Второй раз то же напоминание не уходит")
    r = tasks.run(con, datetime(2026, 11, 5, 15, 0))
    check(r["sent"] == 1 and "Ждём вас сегодня в 17:00" in SENT[-1]["text"], "За 2 часа ушло короткое напоминание")
    con.execute("UPDATE students SET trial_came=1 WHERE id=?", (sid,))
    r = tasks.run(con, datetime(2026, 11, 5, 18, 5))
    check(r["sent"] == 0, "Через 5 минут после пробного ещё рано")
    r = tasks.run(con, datetime(2026, 11, 5, 18, 10))
    check(r["sent"] == 1 and "Как прошло пробное" in SENT[-1]["text"], "Через 10 минут после пробного ушло «Как прошло?»")
    check(con.execute("SELECT 1 FROM tasks WHERE kind='how_was' AND status='open'").fetchone(), "Появилась задача «Как прошло пробное»")
    check(not con.execute("SELECT 1 FROM tasks WHERE kind='not_marked'").fetchone(), "Тренер отметил, задачи «не отмечен» нет")

    print("Не пришёл и перенос")
    sid2 = sv.create_student(con, {"name": "Глеб", "parent_name": "Света", "phone": "+7 900 888-88-88", "group_id": g}, {"name": "Т"})
    con.execute("UPDATE students SET trial_at='2026-11-05T17:00', stage='trial_booked' WHERE id=?", (sid2,))
    sv.mark_trial(con, sid2, False, {"name": "Тренер"})
    r = tasks.run(con, datetime(2026, 11, 5, 18, 20))
    check(con.execute("SELECT 1 FROM tasks WHERE kind='rebook' AND student_id=?", (sid2,)).fetchone(), "Не пришёл: этап «Не дошёл до ПУ» и задача «Перезаписать на ПУ»")
    check(not any("Как прошло" in (x or {}).get("text", "") and x.get("chat_id") != 4242 for x in SENT),
          "Не пришедшему «как понравилось» не пишем")
    a = con.execute("SELECT error FROM auto_messages WHERE student_id=?", (sid2,)).fetchall()
    check(all("нет переписки" in x[0] for x in a), "Без мессенджера сообщение не уходит, причина видна")
    sid3 = sv.create_student(con, {"name": "Ваня", "parent_name": "Дима", "phone": "+7 900 666-66-66", "group_id": g}, {"name": "Т"})
    con.execute("UPDATE students SET trial_at='2026-11-09T17:00', stage='trial_booked' WHERE id=?", (sid3,))
    tasks.run(con, datetime(2026, 11, 6, 10, 0))
    con.execute("UPDATE students SET trial_at='2026-11-12T17:00' WHERE id=?", (sid3,))
    tasks.run(con, datetime(2026, 11, 6, 10, 1))
    st = [r[0] for r in con.execute("SELECT status FROM tasks WHERE student_id=? ORDER BY id", (sid3,))]
    check(st == ["cancelled", "open"], "Пробное перенесли: старая задача снята, новая на новую дату")

    print("Перенос из ERP")
    tid = tasks.import_erp_task(con, "Почему не купили", sid, "2026-11-06T10:00")
    t = con.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    check(t["kind"] == "why_not_bought" and t["assignee_id"] == olga and t["source"] == "erp",
          "Статус ERP «Почему не купили» встал на Олю")
    check(set(tasks.ERP_STATUSES) == {"Напоминание о пробном занятии", "Как прошло пробное", "Не пришли на ПУ",
                                      "Почему не купили", "Напоминание о сегодняшнем ПУ", "Клиент не отмечен на ПУ"},
          "Все шесть статусов ERP учтены")
    tasks.complete(con, tid, {"name": "Ольга"}, "Дорого, перезвонить в декабре")
    check("Дорого" in con.execute("SELECT text FROM history WHERE student_id=? ORDER BY id DESC", (sid,)).fetchone()[0],
          "Итог задачи записан в историю клиента")

    print("Смена исполнителя и правка задачи")
    artem = con.execute("INSERT INTO users(login,name,role,pw_hash) VALUES ('artem','Артём Орёл','coach','x$y')").lastrowid
    varya = con.execute("INSERT INTO users(login,name,role,pw_hash,is_senior) VALUES ('varya','Варвара Батарева','operator','x$y',1)").lastrowid
    gone = con.execute("INSERT INTO users(login,name,role,pw_hash,active) VALUES ('gone','Уволен','operator','x$y',0)").lastrowid
    tid = tasks.import_erp_task(con, "Как прошло пробное", sid, "2026-11-07T10:00")
    con.execute("UPDATE tasks SET assignee_id=? WHERE id=?", (artem, tid))
    r = tasks.edit(con, tid, {"assignee_id": varya}, {"name": "Юлия"})
    check(r["assignee_id"] == varya and r["assignee"] == "Варвара Батарева", "Задачу с Артёма (тренер) передали Варваре")
    check(any("передана: Варвара Батарева" in h[0] for h in con.execute("SELECT text FROM history WHERE student_id=?", (sid,))),
          "В истории карточки: «Задача … передана: Варвара Батарева»")
    tasks.edit(con, tid, {"assignee_id": artem}, {"name": "Юлия"})
    check(con.execute("SELECT assignee_id FROM tasks WHERE id=?", (tid,)).fetchone()[0] == artem, "Тренеру передать можно")
    for bad, why in (({"assignee_id": gone}, "уволенному"), ({"assignee_id": 999}, "несуществующему"), ({"title": "  "}, "без текста")):
        try:
            tasks.edit(con, tid, bad, {"name": "Юлия"}); ok = False
        except comms.CommsError:
            ok = True
        check(ok, f"Нельзя: {why}")
    tasks.edit(con, tid, {"title": "Перезвонить маме", "due_at": "2026-11-08T12:30", "note": "после работы"}, {"name": "Юлия"})
    t = con.execute("SELECT title, due_at, note FROM tasks WHERE id=?", (tid,)).fetchone()
    check(tuple(t) == ("Перезвонить маме", "2026-11-08T12:30", "после работы"), "Правка текста, срока и заметки")
    ft = tasks.for_student(con, sid)
    check(any(x["id"] == tid for x in ft["open"]) and ft["open"][0]["due_at"] <= ft["open"][-1]["due_at"], "В карточке открытые задачи, ближайшая первой")
    print("Воронка «Ручные задачи»")
    from datetime import date as _date, timedelta as _td
    today = _date.today()
    adm = {"id": con.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()[0], "name": "Юлия", "role": "admin"}
    mk = lambda title, d, uid: tasks.add_task(con, "manual", sid, f"{d.isoformat()}T15:00", title=title, source="manual", assignee=uid)
    t_old, t_now, t_next = mk("Вчерашняя", today - _td(days=1), varya), mk("Сегодняшняя", today, varya), mk("Завтрашняя", today + _td(days=1), varya)
    b = tasks.manual_board(con, adm, "all")
    cols = {c["key"]: [x["title"] for x in c["cards"]] for c in b["columns"]}
    check(cols["overdue"] == ["Вчерашняя"] and cols["today"] == ["Сегодняшняя"] and "Завтрашняя" not in sum(cols.values(), []),
          "Задача появляется в свой день: вчерашняя в «Просрочено», сегодняшняя в «Сегодня», завтрашней ещё нет")
    check(b["later"] >= 1, "Счётчик запланированных на следующие дни")
    check(all("Напомнить" not in x and "Как прошло" not in x for x in sum(cols.values(), [])), "Автоматические задачи сюда не попадают")
    tasks.complete(con, t_now, {"name": "Варвара"}, "сделано")
    b = tasks.manual_board(con, adm, "all")
    check([x["title"] for x in b["columns"][2]["cards"]] == ["Сегодняшняя"], "Выполненная в «Выполнено сегодня»")
    mine = tasks.manual_board(con, {"id": olga, "name": "Ольга", "role": "operator"}, "all")
    check(not sum((c["cards"] for c in mine["columns"]), []), "Обычный сотрудник видит только свои ручные задачи")
    check(tasks._task_out(con, con.execute("SELECT * FROM tasks WHERE id=?", (t_old,)).fetchone())["manual"] is True, "Метка «ручная»")

    # на сервере правка задач доступна только админам и операторам, тренерам нет
    from crm import server
    route = next(r for r in server.ROUTES if r[0] == "PATCH" and r[1].pattern.startswith("^/api/tasks/"))
    check(route[3] == "operator", "PATCH /api/tasks/{id}: тренеру нельзя (роль operator и выше)")


if __name__ == "__main__":
    main()
    print("\nВсе проверки задач пройдены")
