"""Документы сотрудников: после подписи CRM пускает дальше. Запуск: python -m tests.test_staffdocs"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import db, staffdocs

con = db.connect(os.path.join(tempfile.mkdtemp(), "d.sqlite3")); db.init_db(con)
uid = con.execute("INSERT INTO users(login, name, role, pw_hash) VALUES ('n','Никита Евсюнин','coach','x')").lastrowid
u = {"id": uid, "name": "Никита Евсюнин", "role": "coach"}
assert staffdocs.pending(con, u) == 5
docs = staffdocs.docs_for(con, u, "https://185-207-65-245.sslip.io")  # подпись идёт из браузера, с адресом сайта
staffdocs.accept(con, u, [d["slug"] for d in docs], "Никита Евсюнин", "1.2.3.4", "ua", "https://185-207-65-245.sslip.io")
assert staffdocs.pending(con, u) == 0, "после подписи документы не должны снова требоваться"
print("✓ подписал - CRM открывается (и без своего домена)")
print("ok")
