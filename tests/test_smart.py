"""Подсказки как в больших CRM: оценка лида, следующий шаг, сохранённые списки, дубли семей. Запуск: python -m tests.test_smart"""
import os
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import comms, db, services as sv, smart  # noqa: E402
from tests.test_crm import check  # noqa: E402

OP = {"id": 1, "name": "Ольга", "role": "operator"}


def main():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "s.sqlite3"))
    db.init_db(con)
    br = con.execute("INSERT INTO branches(name) VALUES ('Небо')").lastrowid
    g = con.execute("INSERT INTO groups(branch_id,name,weekdays) VALUES (?, 'Сб', '5')", (br,)).lastrowid
    now = datetime.now()
    # оценка лида
    hot = sv.create_student(con, {"name": "Петя Ли", "parent_name": "Мама", "phone": "+7 900 111-22-33", "source": "Сайт",
                                  "birth": f"{now.year - 6}-03-01"}, OP)
    con.execute("UPDATE students SET stage='trial_booked', trial_at=? WHERE id=?", ((now.replace(hour=18, minute=0)).isoformat(timespec="minutes"), hot))
    s = sv.get_student_row(con, hot)
    sc = smart.lead_score(con, s)
    check(sc["level"] == "hot" and "записан на пробное" in sc["why"], f"Свежая заявка с сайта, записан на пробное: горячий ({sc})")
    cold = sv.create_student(con, {"name": "Ваня Ко", "parent_name": "Папа", "phone": "+7 900 999-00-00", "source": "Промоутер",
                                   "birth": f"{now.year - 15}-01-01"}, OP)
    con.execute("UPDATE students SET stage='no_answer', created_at='2026-01-01T10:00' WHERE id=?", (cold,))
    sc2 = smart.lead_score(con, sv.get_student_row(con, cold))
    check(sc2["level"] == "cold", f"Старая заявка, недозвон, возраст вне групп: холодный ({sc2})")
    # следующий шаг
    st = smart.next_step(con, sv.get_student_row(con, cold))
    check(st and st["action"] == "offer", "Недозвон: следующий шаг написать в мессенджер и предложить дни")
    fam = sv.get_student_row(con, cold)["family_id"]
    cid = comms.upsert_conversation(con, "telegram", "777", title="Папа")
    comms.link(con, cid, fam)
    comms.add_message(con, cid, "in", "А можно в субботу?")
    con.execute("UPDATE conversations SET unread=1 WHERE id=?", (cid,))
    st = smart.next_step(con, sv.get_student_row(con, cold))
    check(st["action"] == "reply" and "субботу" in st["text"], "Есть непрочитанное от родителя: сначала ответить")
    d = sv.student_detail(con, hot)
    check(d["lead_score"]["level"] == "hot" and d["stage_tip"]["say"], "Карточка отдаёт оценку лида и подсказку этапа")
    # подсказки правит руководитель
    smart.save_tips(con, {"lead": {"say": "Звоним сразу", "next": "Записать"}})
    check(smart.tips(con)["lead"]["say"] == "Звоним сразу", "Текст подсказки этапа меняется в настройках")
    # сохранённые списки
    vid = smart.save_view(con, 1, "members", "Небо, не оплатили", {"branch": "Небо", "status": "unpaid_cur"})
    check(smart.views(con, 1, "members")[0]["data"]["status"] == "unpaid_cur", "Список сохранился с фильтрами")
    check(smart.views(con, 2, "members") == [], "Чужие списки не видны")
    smart.delete_view(con, 1, vid)
    check(smart.views(con, 1, "members") == [], "Список удаляется")
    # дубли: та же мама завела вторую семью с тем же телефоном
    a = sv.create_student(con, {"name": "Маша Ив", "parent_name": "Анна", "phone": "+7 900 555-55-55", "birth": "2019-05-05"}, OP)
    fa = sv.get_student_row(con, a)["family_id"]
    fb = con.execute("INSERT INTO families(name) VALUES ('Ивановы')").lastrowid
    con.execute("INSERT INTO parents(family_id, name, phone, phone_norm) VALUES (?, 'Анна Иванова', '89005555555', '79005555555')", (fb,))
    con.execute("INSERT INTO parents(family_id, name, phone, phone_norm) VALUES (?, 'Сергей', '89001112200', '79001112200')", (fb,))
    b = con.execute("INSERT INTO students(family_id, name, birth, stage, stage_changed_at, created_at) VALUES (?, 'Миша Ив', '2017-02-02', 'lead', ?, ?)",
                    (fb, now.isoformat(), now.isoformat())).lastrowid
    cid2 = comms.upsert_conversation(con, "telegram", "888", title="Анна")
    comms.link(con, cid2, fb)
    dups = smart.duplicates(con)
    pair = [x for x in dups if {x["a"]["id"], x["b"]["id"]} == {fa, fb}]
    check(pair and "телефон" in pair[0]["why"], "Две семьи с одним телефоном найдены как дубль")
    smart.merge_families(con, fa, fb, OP)
    kids = {r[0] for r in con.execute("SELECT id FROM students WHERE family_id=?", (fa,))}
    phones = [r[0] for r in con.execute("SELECT phone_norm FROM parents WHERE family_id=? ORDER BY id", (fa,))]
    check(kids == {a, b}, "После склейки оба ребёнка в одной семье")
    check(phones.count("79005555555") == 1 and "79001112200" in phones, "Телефон мамы не задвоился, папа переехал")
    check(con.execute("SELECT family_id FROM conversations WHERE id=?", (cid2,)).fetchone()[0] == fa, "Переписка переехала в оставшуюся семью")
    check(not con.execute("SELECT 1 FROM families WHERE id=?", (fb,)).fetchone(), "Дубль семьи удалён")
    check(not [x for x in smart.duplicates(con) if fb in (x["a"]["id"], x["b"]["id"])], "Дубль больше не предлагается")
    # «это не дубль»
    c1 = sv.create_student(con, {"name": "Оля", "parent_name": "Мама Оли", "phone": "+7 900 123-12-12"}, OP)
    f1 = sv.get_student_row(con, c1)["family_id"]
    f2 = con.execute("INSERT INTO families(name) VALUES ('Другие')").lastrowid
    con.execute("INSERT INTO parents(family_id, name, phone, phone_norm) VALUES (?, 'Бабушка', '79001231212', '79001231212')", (f2,))
    check([x for x in smart.duplicates(con) if {x["a"]["id"], x["b"]["id"]} == {f1, f2}], "Общий телефон у бабушки: CRM спрашивает")
    smart.not_duplicate(con, f2, f1)
    check(not [x for x in smart.duplicates(con) if {x["a"]["id"], x["b"]["id"]} == {f1, f2}], "«Это не дубль» больше не предлагается")
    print("OK")


if __name__ == "__main__":
    main()
