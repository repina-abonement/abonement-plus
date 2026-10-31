"""«Скачать базу в Excel»: файл открывается, листы и данные на месте. Запуск: python -m tests.test_fullexport"""
import io
import os
import sys
import tempfile
import zipfile
from xml.dom import minidom

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import db, fullexport, money, services as sv  # noqa: E402
from tests.test_crm import check  # noqa: E402

OP = {"id": 1, "name": "Ольга", "role": "operator"}


def main():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "e.sqlite3"))
    db.init_db(con)
    br = con.execute("INSERT INTO branches(name) VALUES ('Небо')").lastrowid
    g = con.execute("INSERT INTO groups(branch_id,name,weekdays) VALUES (?, 'Сб <10:00> & Вс', '56')", (br,)).lastrowid
    a = sv.create_student(con, {"name": "Аня", "parent_name": "Мама Ани", "phone": "+7 900 000-11-22", "funnel": "members", "group_id": g}, OP)
    money.add_payment(con, a, 5400, "sbp", OP, comment="за ноябрь")
    data, name = fullexport.build(con)
    z = zipfile.ZipFile(io.BytesIO(data))
    for n in z.namelist():
        if n.endswith(".xml") or n.endswith(".rels"):
            minidom.parseString(z.read(n))
    check(name.endswith(".xlsx"), "Файл Excel, все части читаются")
    wb = z.read("xl/workbook.xml").decode()
    check(all(s in wb for s in ("Дети и родители", "Абонементы", "Оплаты", "Посещения", "Группы", "Сотрудники")), "Шесть листов")
    s1, s3 = z.read("xl/worksheets/sheet1.xml").decode(), z.read("xl/worksheets/sheet3.xml").decode()
    check("Аня" in s1 and "+7 900 000-11-22" in s1 and "Небо · Сб &lt;10:00&gt; &amp; Вс" in s1, "Ребёнок с телефоном родителя и группой")
    check("5400" in s3 and "СБП" in s3 and "за ноябрь" in s3, "Оплата с суммой, способом и комментарием")
    data, name = fullexport.build(con, "contacts")
    z = zipfile.ZipFile(io.BytesIO(data))
    s = z.read("xl/worksheets/sheet1.xml").decode()
    check(name.startswith("kontakty-") and "xl/worksheets/sheet2.xml" not in z.namelist(), "Контакты: один лист")
    check("Аня" in s and "+7 900 000-11-22" in s and "Мама Ани" in s and "5400" not in s, "Ребёнок, родитель, телефон, без денег")
    print("ok")


if __name__ == "__main__":
    main()
