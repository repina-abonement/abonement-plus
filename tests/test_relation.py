"""Кем приходится ребёнку представитель. Запуск: python -m tests.test_relation"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import db, smart, services as sv
smart.REQUIRE = False
from tests.test_crm import check

con = db.connect(os.path.join(tempfile.mkdtemp(), "r.sqlite3")); db.init_db(con)
check(smart.guess_relation("Ольга (мама)") == "мама" and smart.guess_relation("Братеева") == "", "Угадываем роль по целому слову")
sid = sv.create_student(con, {"name": "Петя Иванов", "parent_name": "Ольга", "phone": "+7 900 000-00-01", "funnel": "sales"}, {"name": "Оля"})
pid = con.execute("SELECT id FROM parents LIMIT 1").fetchone()[0]
smart.set_relation(con, pid, "бабушка")
check(sv.student_detail(con, sid)["parents"][0]["relation"] == "бабушка", "Роль видна в карточке")
print("ok")
