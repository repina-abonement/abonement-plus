"""Отчёт тренера о смене: шаги, фото, задания, сдача, напоминания. Запуск: python -m tests.test_coachreport"""
import base64
import os
import sys
import tempfile
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["CRM_DB"] = os.path.join(tempfile.mkdtemp(), "r.sqlite3")

from crm import coachreport as cr, db, services as sv  # noqa: E402
from tests.test_crm import check  # noqa: E402

JPG = base64.b64decode("/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q==")


def main():
    con = db.connect()
    db.init_db(con)
    today = date.today()
    add = lambda login, name, role: con.execute("INSERT INTO users(login,name,role,pw_hash) VALUES (?,?,?,'x')", (login, name, role)).lastrowid
    nik, art, varya = add("nik", "Никита", "coach"), add("art", "Артём", "coach"), add("varya", "Варя", "operator")
    con.execute("UPDATE users SET is_senior=1 WHERE id=?", (varya,))
    br = con.execute("INSERT INTO branches(name) VALUES ('ТЦ «Небо»')").lastrowid
    wd = str(today.weekday())
    g1 = con.execute("INSERT INTO groups(branch_id,name,weekdays,time,coach_id,assistant_id) VALUES (?,?,?,?,?,?)",
                     (br, "Небо 00:05", wd, "00:05", nik, art)).lastrowid
    g2 = con.execute("INSERT INTO groups(branch_id,name,weekdays,time,coach_id) VALUES (?,?,?,?,?)", (br, "Небо 00:10", wd, "00:10", nik)).lastrowid
    OP = {"id": varya, "name": "Варя", "role": "operator"}
    kids = [sv.create_student(con, {"name": f"Ребёнок {i}", "parent_name": "Мама", "phone": f"+7 900 100-00-0{i}", "funnel": "members",
                                    "group_id": g, "tariff": "fixed"}, OP) for i, g in enumerate((g1, g1, g2))]
    NIK = {"id": nik, "name": "Никита", "role": "coach"}
    ART = {"id": art, "name": "Артём", "role": "coach"}

    print("Смена и отчёт")
    sh = cr.my_shifts(con, nik, today)
    check(len(sh) == 1 and len(sh[0]["groups"]) == 2 and art in sh[0]["assistants"], "Две тренировки Никиты в Небе - одна смена, Артём помощник")
    v = cr.get_or_create(con, br, today, ART)
    check(v["main"] == "Никита" and not v["ready"], "Помощник открывает тот же отчёт главного")
    step = lambda v, k: next(x["done"] for x in v["steps"] if x["key"] == k)
    check(not step(v, "marks"), "Пока дети не отмечены, шаг «Дети отмечены» не готов")
    d = today.isoformat()
    sv.mark_attendance(con, kids[0], g1, d, "present", NIK)
    sv.mark_attendance(con, kids[1], g1, d, "absent", NIK)
    sv.mark_attendance(con, kids[2], g2, d, "present", NIK)
    v = cr.view(con, cr._row(con, v["id"]), sh[0])
    check(step(v, "marks") and not step(v, "absent") and v["groups"][0]["came"] == 1, "Отметили всех: шаг готов, отсутствующему нужна причина")
    rid = v["id"]
    cr.update(con, rid, {"absent": {kids[1]: "sick"}, "unlisted_none": True, "pays": [{"name": "Петров", "amount": 2500, "method": "qr", "what": "indiv"}],
                         "cash": {"receipts": True, "closed": True, "paykeeper": True}, "merch": "no", "inv": "broken", "inv_note": "порвана сетка"})
    try:
        cr.submit(con, rid, NIK)
        ok = False
    except cr.ReportError as e:
        ok = "Касса" in str(e) and "Фото" in str(e)
    check(ok, "Не сдать, пока не готовы касса и фото: CRM называет, чего не хватает")
    cr.update(con, rid, {"cash": {"terminal": True}})
    for slot, _ in cr.PHOTO_SLOTS + [("inv_broken", "")]:
        cr.add_photo(con, "report", rid, slot, JPG)

    print("Задание от старшего администратора")
    tid = cr.add_task(con, nik, d, "Убери стаканчики с ресепшена", OP)
    cr.add_photo(con, "task", tid, "", JPG)
    v = cr.view(con, cr._row(con, rid), sh[0])
    check(not v["ready"] and v["tasks"][0]["photos"], "Задание с фото появилось в отчёте, без него не сдать")
    cr.update(con, rid, {"rtasks": {tid: {"state": "done", "why": "убрал, сфоткал"}}})

    print("Задачи из «Планера» на тренера")
    from crm import tasks as tk
    t1 = tk.add_task(con, "plan", None, f"{d}T15:00", title="Повесить расписание на стенд", source="manual", assignee=nik)
    t2 = tk.add_task(con, "plan", None, f"{(today - timedelta(days=1)).isoformat()}T15:00", title="Забрать мячи со склада", source="manual", assignee=art)
    v = cr.view(con, cr._row(con, rid), sh[0])
    check({t["title"] for t in v["crm_tasks"]} == {"Повесить расписание на стенд", "Забрать мячи со склада"} and not v["ready"],
          "Задачи главного и помощника (и вчерашняя несделанная) в отчёте, без ответа не сдать")
    cr.update(con, rid, {"crm_tasks": {t1: {"state": "done"}, t2: {"state": "not"}}})
    check(not cr.view(con, cr._row(con, rid), sh[0])["ready"], "«Не сделал» без причины: не сдать")
    cr.update(con, rid, {"crm_tasks": {t2: {"state": "not", "why": "склад был закрыт"}}})

    print("Оплаты: поиск, наличные, конверт")
    found = cr.search_clients(con, "ребёнок 2")
    check(found and found[0]["id"] == kids[2] and "phone" not in found[0] and found[0]["label"] == "Небо 00:10", "Поиск по CRM: имя и группа, без телефона")
    cash = {"name": "Ребёнок 2", "student_id": kids[2], "amount": 12150, "method": "cash", "what": "month", "tariff": "fixed", "check": True}
    check(not cr.pay_ok(cash) and cr.pay_ok(cash | {"env": True}), "Наличные только с чеком и в конверте")
    check(not cr.pay_ok({**cash, "env": True, "tariff": ""}), "Абонемент без тарифа не принимаем")
    check(cr.pay_ok({"name": "Сидоров Вася", "amount": 2500, "method": "card", "what": "other", "what_text": "абонемент на декабрь"}),
          "Можно вписать своё «за что»")
    cr.update(con, rid, {"pays": [{"name": "Петров", "amount": 2500, "method": "qr", "what": "indiv"}, cash | {"env": True}]})
    check(not step(cr.view(con, cr._row(con, rid), sh[0]), "pays"), "Есть наличные: без остатка в конверте шаг не готов")
    cr.update(con, rid, {"envelope": 12150})
    check(step(cr.view(con, cr._row(con, rid), sh[0]), "pays"), "Первый конверт: остаток записан, сравнивать не с чем")

    print("Сдаём")
    v = cr.submit(con, rid, NIK)
    check(v["status"] == "done", "Все шаги готовы: отчёт сдан")
    issues = cr.desk(con, False)["issues"]
    check(len(issues) == 1 and issues[0]["note"] == "порвана сетка" and issues[0]["photos"], "Неисправность с фото на столе старшего администратора")
    txt = cr.text(con, v)
    check("ОТЧЁТ ТРЕНЕРА О СМЕНЕ" in txt and "Петров - 2 500 ₽ - QR - Индив ⚠️ не найден в CRM" in txt and "Ребёнок 1 - Болеет" in txt
          and "Остаток в конверте: 12 150 ₽" in txt, f"Текст для чата в привычном виде")
    check(con.execute("SELECT COUNT(*) FROM tasks WHERE title LIKE 'Разобрать платёж вручную%'").fetchone()[0] == 1
          and con.execute("SELECT COUNT(*) FROM tasks WHERE title='Оплата у тренера: провести в CRM' AND student_id=?", (kids[2],)).fetchone()[0] == 1,
          "Оплаты ушли админам задачами: найденная к карточке, ненайденная «разобрать вручную»")
    st = {r[0]: r[1] for r in con.execute("SELECT id, status FROM tasks WHERE id IN (?,?)", (t1, t2))}
    check(st[t1] == "review" and st[t2] == "review", "Ответы по задачам ждут администратора, у тренера задачи больше не висят")
    check("🟢Повесить расписание" in txt and "🔴Забрать мячи со склада - не сделано: склад был закрыт" in txt and "🟢Убери стаканчики с ресепшена: убрал, сфоткал" in txt,
          "Задания с комментариями в тексте отчёта")
    mt = cr.money_text(v)
    check("ОПЛАТЫ НА СМЕНЕ" in mt and "Петров - 2 500 ₽ - QR - Индив" in mt and "Наличные: 12 150 ₽" in mt and "Итого: 14 650 ₽" in mt
          and "Остаток в конверте: 12 150 ₽" in mt, f"В «ДЕНЬГИ»: оплаты смены, итог по способам и конверт ({mt})")
    v2 = dict(v, env_prev=None, data=dict(v["data"], envelope=1200, pays=[{"name": "Иванов", "amount": "1 200", "method": "cash", "what": "single", "check": 1, "env": 1},
                                                                           {"name": "Петров", "amount": 2500, "method": "card", "what": "indiv"}]))
    mt = cr.money_text(v2)
    check("Наличные: 1 200 ₽" in mt and "Итого: 3 700 ₽" in mt, "Суммы вида «1 200» считаются, наличные отдельно")
    check("Оплат на месте не было" in cr.money_text(dict(v, data=dict(v["data"], pays=[]))), "Без оплат: так и пишем")
    ans = {a["ref_id"]: a for a in cr.answers(con) if a["kind"] == "crm"}
    check(len(cr.answers(con)) == 3 and ans[t2]["comment"] == "склад был закрыт", "Три ответа на столе администраторов")
    cr.decide(con, ans[t1]["id"], OP, "accepted")
    try:
        cr.decide(con, ans[t2]["id"], OP, "returned", "")
        ok = False
    except cr.ReportError:
        ok = True
    check(ok, "Вернуть без комментария нельзя")
    tomorrow = (today + timedelta(days=1)).isoformat()
    cr.decide(con, ans[t2]["id"], OP, "returned", "склад открыт с 10, забери завтра", tomorrow)
    st = {r[0]: (r[1], r[2], r[3]) for r in con.execute("SELECT id, status, due_at, note FROM tasks WHERE id IN (?,?)", (t1, t2))}
    check(st[t1][0] == "done" and st[t2][0] == "open" and st[t2][1].startswith(tomorrow) and "забери завтра" in st[t2][2],
          "Принятая закрыта, возвращённая снова у тренера на завтра с комментарием")
    check(len(cr.answers(con)) == 1, "Разобранные ответы со стола ушли")
    r2 = cr.answers(con)[0]
    cr.decide(con, r2["id"], OP, "returned", "фото не видно", tomorrow)
    check(con.execute("SELECT 1 FROM report_tasks WHERE date=? AND text LIKE 'Убери стаканчики%'", (tomorrow,)).fetchone(),
          "Задание на отчёт вернулось тренеру новым заданием на завтра")

    print("Конверт: взяли деньги")
    check(cr.envelope_prev(con, br)["amount"] == 12150, "В конверте Неба 12 150 ₽ по отчёту")
    try:
        cr.envelope_take(con, OP, br, 5000, "other", "")
        ok = False
    except cr.ReportError:
        ok = True
    check(ok, "Без причины не записать")
    left = cr.envelope_take(con, OP, br, 5000, "bank")["left"]
    e = cr.envelope_prev(con, br)
    check(left == 7150 and e["amount"] == 7150 and e["taken"] == 5000, f"Варя взяла 5 000 ₽ в банк: осталось 7 150 ₽ ({e})")
    check([x["amount"] for x in cr.envelopes(con) if x["branch_id"] == br] == [7150], "Остаток виден в окне «Взял из конверта»")

    print("Напоминания")
    con.execute("UPDATE settings SET value=? WHERE key='coach_reports_from'", ((today + timedelta(days=1)).isoformat(),))
    check(not cr.overdue(con, sh[0]["end"] + timedelta(hours=3)), "Смены до запуска отчётов не считаются несданными")
    con.execute("UPDATE settings SET value=? WHERE key='coach_reports_from'", (today.isoformat(),))
    con.execute("UPDATE coach_reports SET status='draft' WHERE id=?", (rid,))
    end = sh[0]["end"]
    check(not cr.overdue(con, end + timedelta(minutes=10)), "Через 10 минут ещё не напоминаем")
    o = cr.overdue(con, end + timedelta(minutes=40))
    check(len(o) == 1 and o[0]["level"] == 1, "Через 30 минут: старшему администратору")
    check(cr.overdue(con, end + timedelta(minutes=125))[0]["level"] == 2, "Через 2 часа: руководителю")
    print("ok")


if __name__ == "__main__":
    main()
