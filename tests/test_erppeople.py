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


if __name__ == "__main__":
    main()
