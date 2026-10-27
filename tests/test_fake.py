"""Проверки выдуманных клиентов. Запуск: python -m tests.test_fake"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import db, fakegen, schedule, services as sv  # noqa: E402
from tests.test_crm import check  # noqa: E402

ADM = {"id": 1, "name": "Юлия", "role": "admin"}


def main():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "f.sqlite3"))
    db.init_db(con)
    print("Выдуманные клиенты")
    b = schedule.save_branch(con, {"name": "Небо"})
    for wd, t, age in (([0, 3], "16:45", "3–4"), ([1, 4], "17:45", "4–5"), ([5, 6], "10:00", "5–6")):
        schedule.save_group(con, {"name": f"Небо {t} {age}", "branch_id": b, "weekdays": wd, "time": t, "age": age})
    real = sv.create_student(con, {"name": "Настоящий", "parent_name": "Мама", "phone": "+7 900 111-11-11", "funnel": "sales"}, ADM)
    r = fakegen.generate(con, 150, ADM, seed=3)
    total = con.execute("SELECT COUNT(*) FROM students WHERE fake=1").fetchone()[0]
    check(total == 150 and r["members"] > 70 and r["leads"] > 25, "150 выдуманных: и занимающиеся, и лиды")
    check(con.execute("SELECT COUNT(*) FROM payments").fetchone()[0] > 50, "У выдуманных есть оплаты")
    n = fakegen.remove(con)
    check(n == 150 and con.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 1, "Удалились все выдуманные, настоящий остался")
    check(con.execute("SELECT id FROM students").fetchone()[0] == real, "…это именно настоящий клиент")


if __name__ == "__main__":
    main()
    print("\nВсе проверки выдуманных клиентов пройдены")
