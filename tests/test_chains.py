"""Автоцепочки: выключены по умолчанию, шлют по расписанию, ответ родителя останавливает, цель останавливает. Запуск: python -m tests.test_chains"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import chains, comms, db, services as sv  # noqa: E402
from tests.test_crm import check  # noqa: E402

OP = {"id": 1, "name": "Ольга", "role": "operator"}
SENT = []


def main():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "c.sqlite3"))
    db.init_db(con)
    comms.send = lambda con, cid, text, user: SENT.append((cid, text)) or {"ok": True}
    br = con.execute("INSERT INTO branches(name) VALUES ('Небо')").lastrowid
    g = con.execute("INSERT INTO groups(branch_id,name,weekdays) VALUES (?, 'Небо Сб 10:00', '5')", (br,)).lastrowid
    t0 = datetime(2026, 11, 3, 18, 0)
    a = sv.create_student(con, {"name": "Петя Ли", "parent_name": "Мама", "phone": "+7 900 111-22-33", "group_id": g}, OP)
    con.execute("UPDATE students SET stage='how_was', trial_at=?, trial_came=1 WHERE id=?", (t0.isoformat(timespec="minutes"), a))
    fam = sv.get_student_row(con, a)["family_id"]
    cid = comms.upsert_conversation(con, "telegram", "1", title="Мама")
    comms.link(con, cid, fam)
    r = chains.tick(con, t0 + timedelta(days=1, hours=-6))
    check(r == {"started": 0, "sent": 0} and not SENT, "Цепочки выключены по умолчанию: ничего не уходит")
    cfg = chains.config(con)
    cfg["after_trial"]["enabled"] = True
    chains.save_config(con, cfg)
    chains.tick(con, datetime(2026, 11, 3, 22, 0))
    check(not SENT, "Цепочка началась, но первое сообщение ждёт своего дня")
    chains.tick(con, datetime(2026, 11, 4, 8, 0))
    check(not SENT, "Ночью и рано утром не пишем (только с 10 до 20)")
    chains.tick(con, datetime(2026, 11, 4, 10, 5))
    check(len(SENT) == 1 and "Петя" in SENT[0][1], f"На следующий день в 10 ушло первое сообщение с именем ребёнка: {SENT}")
    chains.tick(con, datetime(2026, 11, 4, 12, 0))
    check(len(SENT) == 1, "Второе сообщение не уходит раньше срока")
    chains.tick(con, datetime(2026, 11, 6, 10, 0))
    check(len(SENT) == 2 and "Небо Сб 10:00" in SENT[1][1], "Через 3 дня ушло второе, с названием группы")
    comms.add_message(con, cid, "in", "Да, хотим в субботу")
    con.execute("UPDATE messages SET ts='2026-11-06T15:00' WHERE conversation_id=?", (cid,))
    chains.tick(con, datetime(2026, 11, 7, 11, 0))
    run = con.execute("SELECT status, stop_reason FROM chain_runs WHERE student_id=?", (a,)).fetchone()
    task = con.execute("SELECT title FROM tasks WHERE student_id=? AND source='chain'", (a,)).fetchone()
    check(run["status"] == "stopped" and run["stop_reason"] == "родитель ответил" and task, "Родитель ответил: цепочка остановилась, задача ответить")
    chains.tick(con, datetime(2026, 11, 10, 11, 0))
    check(len(SENT) == 2, "После ответа больше ничего не шлём")
    # цель: купил абонемент
    b = sv.create_student(con, {"name": "Гоша", "parent_name": "Папа", "phone": "+7 900 222-33-44", "group_id": g}, OP)
    con.execute("UPDATE students SET stage='how_was', trial_at=?, trial_came=1 WHERE id=?", (t0.isoformat(timespec="minutes"), b))
    cid2 = comms.upsert_conversation(con, "telegram", "2", title="Папа")
    comms.link(con, cid2, sv.get_student_row(con, b)["family_id"])
    chains.tick(con, datetime(2026, 11, 4, 10, 0))
    n = len(SENT)
    con.execute("UPDATE students SET funnel='members', stage='active' WHERE id=?", (b,))
    chains.tick(con, datetime(2026, 11, 6, 10, 0))
    st = con.execute("SELECT status, stop_reason FROM chain_runs WHERE student_id=?", (b,)).fetchone()
    check(st["status"] == "done" and "купил" in st["stop_reason"] and len(SENT) == n, "Купил абонемент: цепочка закончилась сама")
    # без мессенджера: задача написать вручную
    c = sv.create_student(con, {"name": "Лиза", "parent_name": "Мама Лизы", "phone": "+7 900 333-44-55", "group_id": g}, OP)
    con.execute("UPDATE students SET stage='how_was', trial_at=?, trial_came=1 WHERE id=?", (t0.isoformat(timespec="minutes"), c))
    chains.tick(con, datetime(2026, 11, 4, 10, 0))
    chains.tick(con, datetime(2026, 11, 4, 10, 1))
    check(con.execute("SELECT 1 FROM tasks WHERE student_id=? AND title LIKE 'Автоцепочка не смогла%'", (c,)).fetchone(),
          "Нет мессенджера: задача написать родителю самим")
    # продление: с 20 числа, пока следующий месяц не оплачен
    m = sv.create_student(con, {"name": "Аня", "parent_name": "Мама Ани", "phone": "+7 900 444-55-66", "funnel": "members", "group_id": g}, OP)
    cid3 = comms.upsert_conversation(con, "telegram", "3", title="Мама Ани")
    comms.link(con, cid3, sv.get_student_row(con, m)["family_id"])
    cfg = chains.config(con); cfg["renewal"]["enabled"] = True; chains.save_config(con, cfg)
    n = len(SENT)
    chains.tick(con, datetime(2026, 11, 19, 12, 0))
    check(len(SENT) == n, "До 20 числа о продлении не пишем")
    chains.tick(con, datetime(2026, 11, 20, 10, 0)); chains.tick(con, datetime(2026, 11, 20, 10, 1))
    mine = [t for c_, t in SENT[n:] if "Аня" in t]
    check(len(mine) == 1 and "декабрь" in mine[0], f"20 числа ушло напоминание о продлении на декабрь: {mine}")
    print("OK")


if __name__ == "__main__":
    main()
