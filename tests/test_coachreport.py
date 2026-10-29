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
    cr.update(con, rid, {"absent": {kids[1]: "sick"}, "unlisted_none": True, "pays": [{"name": "Петров", "amount": 2500, "method": "qr", "what": "индив"}],
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
    cr.task_done(con, tid, NIK)

    print("Сдаём")
    v = cr.submit(con, rid, NIK)
    check(v["status"] == "done", "Все шаги готовы: отчёт сдан")
    issues = cr.desk(con, False)["issues"]
    check(len(issues) == 1 and issues[0]["note"] == "порвана сетка" and issues[0]["photos"], "Неисправность с фото на столе старшего администратора")
    txt = cr.text(con, v)
    check("ОТЧЁТ ТРЕНЕРА О СМЕНЕ" in txt and "Петров - 2500 ₽ - QR - индив" in txt and "Ребёнок 1 - Болеет" in txt, "Текст для чата в привычном виде")

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
