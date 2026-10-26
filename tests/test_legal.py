"""Проверки юридических страниц и согласий. Запуск: python -m tests.test_legal"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import comms, db, legal, services as sv  # noqa: E402
from tests.test_crm import check  # noqa: E402

OP = {"id": 1, "name": "Оля", "role": "operator"}


def main():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "l.sqlite3"))
    db.init_db(con)
    print("Документы")
    check("ИНН" in " ".join(legal.missing(con)), "Пока реквизиты пустые, CRM напоминает их заполнить")
    h = legal.page(con, "privacy", "https://crm.example.ru")
    check("<mark>" in h and "<h1>" in h, "Политика открывается, пустые реквизиты подсвечены")
    legal.save(con, {"legal_ip_name": "ИП Тестова Ю. А.", "legal_inn": "770000000000", "text_consent": "# Моё согласие\n{legal_ip_name} <b>"})
    h = legal.page(con, "consent", "https://crm.example.ru")
    check("ИП Тестова Ю. А." in h and "&lt;b&gt;" in h, "Свой текст согласия: реквизиты подставлены, HTML экранирован")
    legal.save(con, {"text_consent": legal.DOCS["consent"][1]})
    check(not comms.get(con, "legal_text_consent"), "Вернули стандартный текст — своя версия не хранится")
    check("Документы" in legal.page(con, "nope", ""), "Неизвестный адрес ведёт на список документов")

    print("Согласия")
    sid = sv.create_student(con, {"name": "Миша", "parent_name": "Анна", "phone": "+7 900 123-45-67", "funnel": "leads"}, OP)
    fid = con.execute("SELECT family_id FROM students WHERE id=?", (sid,)).fetchone()[0]
    try:
        legal.set_consent(con, fid, "pd", True, "", OP)
        check(False, "Без способа получения согласие не ставится")
    except ValueError:
        check(True, "Без способа получения согласие не ставится")
    legal.set_consent(con, fid, "pd", True, "call", OP)
    legal.set_consent(con, fid, "ads", True, "", OP)
    legal.set_consent(con, fid, "ads", False, "", OP)
    f = con.execute("SELECT consent_pd_at, consent_pd_how, consent_ads_revoked_at FROM families WHERE id=?", (fid,)).fetchone()
    check(f[0] and f[1] == "call" and f[2], "Согласие на данные есть, от рассылок отказались")
    n = con.execute("SELECT COUNT(*) FROM history WHERE student_id=? AND kind='consent'", (sid,)).fetchone()[0]
    check(n == 3, "Каждое изменение записано в историю ученика")

    print("Чат-бот")
    check("/legal/" not in comms.greeting(con), "Без адреса CRM ссылку не добавляем")
    comms.put(con, "public_url", "https://crm.example.ru")
    check("https://crm.example.ru/legal/consent" in comms.greeting(con), "Приветствие бота содержит ссылку на согласие")
    sid2 = sv.create_student(con, {"name": "Катя", "parent_name": "Ира", "phone": "+7 900 222-33-44", "funnel": "leads"}, OP)
    fid2 = con.execute("SELECT family_id FROM students WHERE id=?", (sid2,)).fetchone()[0]
    cid = con.execute("INSERT INTO conversations(channel, external_id, title, family_id, created_at) VALUES ('telegram','1','Ира',?,'2026-10-27')", (fid2,)).lastrowid
    comms.consent_from_bot(con, cid)
    check(con.execute("SELECT consent_pd_how FROM families WHERE id=?", (fid2,)).fetchone()[0] == "bot",
          "Поделились номером в боте — согласие отмечено «через бота»")


if __name__ == "__main__":
    main()
    print("\nВсе юридические проверки пройдены")
