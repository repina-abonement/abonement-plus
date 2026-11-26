from crm import db, erpimport, erppeople, people, services


def main():
    con = db.connect(":memory:")
    db.init_db(con)
    admin = {"id": 1, "name": "Admin"}
    visitor = services.create_student(con, {
        "name": "Петров Иван", "parent_name": "Мария", "phone": "79000000000",
    }, admin)
    con.execute("UPDATE students SET erp_id='42', archived=1 WHERE id=?", (visitor,))
    visitors = erpimport.upload(con, "visitors.csv", (
        "ID;ФИО посетителя;Статус\n42;Петров Иван;Постоянный посетитель\n"
        "99;Клиент без карточки;Бывший посетитель"
    ).encode(), "clients")["id"]
    leads = erpimport.upload(con, "leads.csv", (
        "ID;Имя ребенка;Имя родителя;Телефон;Дата создания лида\n"
        "42;Петров Иван;Мария;79000000000;01.11.2026\n"
        "43;Петрова Ольга;Мария;79000000000;02.11.2026\n"
        "44;;Мария;79000000000;02.11.2026"
    ).encode(), "clients")["id"]

    plan = erppeople.transfer(con, [visitors], [leads], admin)
    assert plan["counts"]["updated"] == 1
    assert plan["counts"]["new"] == 3
    assert len(plan["issues"]) == 1
    assert con.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 1
    con.execute("UPDATE students SET erp_status='Другое значение' WHERE id=?", (visitor,))
    try:
        erppeople.transfer(con, [visitors], [leads], admin, plan["token"])
    except erpimport.ImportError_:
        pass
    else:
        raise AssertionError("Accepted a stale preview")
    plan = erppeople.transfer(con, [visitors], [leads], admin)
    erppeople.transfer(con, [visitors], [leads], admin, plan["token"])
    assert people.listing(con, erp="Новый лид")["total"] == 3
    assert people.listing(con, archived=True)["rows"][0]["status_label"] == "Постоянный посетитель"
    assert con.execute("SELECT COUNT(DISTINCT family_id) FROM students").fetchone()[0] == 1
    again = erppeople.transfer(con, [visitors], [leads], admin)
    assert again["counts"]["same"] == 4
    erppeople.transfer(con, [visitors], [leads], admin, again["token"])
    assert con.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 4
    print("ok")


def test_lead_only_import_preserves_metadata_and_routes_task():
    from crm import tasks

    con = db.connect(":memory:")
    db.init_db(con)
    admin = {"id": 1, "name": "Admin"}
    file_id = erpimport.upload(con, "leads.csv", (
        "ID;Имя ребенка;Имя родителя;Телефон;Канал;Источник;Территория;Филиал;Комментарий;Дата создания лида;Задача\n"
        "9001;Тестовый Ребёнок;Мария;79000000001;Тестовый канал;Тестовый источник;"
        "Тестовая территория;Тестовый филиал;Комментарий;25.11.2026;26.11.2026"
    ).encode(), "clients")["id"]
    for _ in range(2):
        preview = erppeople.transfer(con, [], [file_id], admin)
        erppeople.transfer(con, [], [file_id], admin, preview["token"])
    student = dict(con.execute("SELECT * FROM students").fetchone())
    assert con.execute("SELECT count(*) FROM students").fetchone()[0] == 1
    assert student["created_at"] == "2026-11-25" and student["branch_id"]
    assert con.execute("SELECT territory FROM erp_lead_details WHERE source_id='9001'").fetchone()[0] == "Тестовая территория"
    assert con.execute("SELECT student_id FROM erp_full_identities WHERE namespace='lead' AND source_id='9001'").fetchone()[0] == student["id"]
    task = dict(con.execute("SELECT * FROM tasks WHERE auto_key='erp-lead:9001'").fetchone())
    assert task["title"] == "Новый лид" and task["due_at"] == "2026-11-26T23:59"
    assert tasks._erp_col(dict(task, student=student))[0] == "new_application"
    assert not services.new_cards_today(con)["cards"]

    undated = erpimport.upload(con, "undated.csv", (
        "ID;Имя ребенка;Имя родителя;Телефон;Филиал;Дата создания лида;Задача\n"
        "9002;Другой Ребёнок;Ирина;79000000002;Тестовый филиал;25.11.2026;"
    ).encode(), "clients")["id"]
    preview = erppeople.transfer(con, [], [undated], admin)
    assert len(preview["lead_branches"][0]["matches"]) == 1
    erppeople.transfer(con, [], [undated], admin, preview["token"])
    task = con.execute("SELECT title,due_at FROM tasks WHERE auto_key='erp-lead:9002'").fetchone()
    assert tuple(task) == ("Новый лид. Даты задачи нет, надо создать", "")
    sid = con.execute("SELECT student_id FROM tasks WHERE auto_key='erp-lead:9002'").fetchone()[0]
    tasks.add_task(con, "request_data", sid, "2026-11-25", auto_key=f"ad-source-required:{sid}", source="data_quality")
    for _ in range(2):
        tasks.ensure_ad_source_tasks(con)
    assert con.execute("SELECT status FROM tasks WHERE auto_key=?", (f"ad-source-required:{sid}",)).fetchone()[0] == "cancelled"
    assert con.execute("SELECT status FROM tasks WHERE auto_key='erp-lead:9002'").fetchone()[0] == "open"


if __name__ == "__main__":
    main()


def test_mass_visitors_create_and_fill_without_overwriting():
    con = db.connect(":memory:")
    db.init_db(con)
    admin = {"id": 1, "name": "Admin"}
    con.execute("INSERT INTO branches(name) VALUES ('ТРЦ Небо')")
    sid = services.create_student(con, {
        "name": "Котов Илья", "parent_name": "Ирина", "phone": "79000000001",
    }, admin)
    con.execute("UPDATE students SET erp_status='Думают' WHERE id=?", (sid,))
    upload = erpimport.upload(con, "visitors.csv", (
        "ID;ФИО посетителя;Телефон;Имя родителя;Почта;Дата рождения;Статус;Филиал\n"
        "301;Котов Илья;79000000001;Мария;test@example.com;15.11.2018;Постоянный посетитель;ТЦ Небо\n"
        "302;Котова Анна;79000000001;Ирина;;19.11.2019;Нет абонемента;ТЦ Небо\n"
        "303;Котов Илья;79000000002;Анна;;;;ТЦ Небо\n"
    ).encode(), "clients")["id"]
    for expected in (1, 0):
        plan = erppeople.transfer(con, [upload], [], admin, visitor_cards=True)
        assert plan["counts"].get("new", 0) == expected
        assert len(plan["issues"]) == 1
        erppeople.transfer(con, [upload], [], admin, plan["token"], visitor_cards=True)
    rows = con.execute("SELECT erp_id,erp_status,birth,branch_id FROM students ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [
        ("301", "Думают", "2018-11-15", 1), ("302", "Нет абонемента", "2019-11-19", 1)]
    assert tuple(con.execute("SELECT name,email FROM parents").fetchone()) == ("Ирина", "test@example.com")
    assert con.execute("SELECT COUNT(*) FROM families").fetchone()[0] == 1
