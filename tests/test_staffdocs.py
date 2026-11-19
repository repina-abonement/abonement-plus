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

# An exact terminology-only revision preserves the prior signature and timestamp.
from crm.uiwording import legacy_template
import hashlib
for slug, template in [('nda', staffdocs.NDA), ('pd_rules', staffdocs.PD_RULES)]:
    old_text = staffdocs._render(con, u, legacy_template(template), '')
    old_hash = hashlib.sha256(old_text.encode()).hexdigest()
    con.execute('UPDATE staff_acceptances SET text=?,sha256=? WHERE user_id=? AND doc=?', (old_text, old_hash, uid, slug))
    before = tuple(con.execute('SELECT text,sha256,accepted_at FROM staff_acceptances WHERE user_id=? AND doc=?', (uid,slug)).fetchone())
    shown = next(d for d in staffdocs.docs_for(con,u) if d['slug']==slug)
    assert shown['accepted_at'] and shown['accepted_sha256']==old_hash
    after = tuple(con.execute('SELECT text,sha256,accepted_at FROM staff_acceptances WHERE user_id=? AND doc=?', (uid,slug)).fetchone())
    assert before==after
original=staffdocs.DOCS
staffdocs.DOCS=[(slug,title,text+'\nAdditional substantive rule' if slug=='nda' else text) for slug,title,text in original]
assert not next(d for d in staffdocs.docs_for(con,u) if d['slug']=='nda')['accepted_at']
staffdocs.DOCS=original
print('PASS exact historical signatures preserved; substantive revision requires acceptance')
