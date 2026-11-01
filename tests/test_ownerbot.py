"""Красное руководителю в Telegram и напоминания «Службе заботы». Запуск: python -m tests.test_ownerbot"""
import os, sys, tempfile
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import db, comms, ownerbot
from tests.test_crm import check

con = db.connect(os.path.join(tempfile.mkdtemp(), "o.sqlite3")); db.init_db(con)
sent = []
comms.tg_call = lambda con, method, payload: sent.append(payload) or {"message_id": 1}
comms.put(con, "tg_token", "x"); comms.put(con, "tg_chat_care", "-100"); comms.put(con, "public_url", "https://crm.example")
uid = con.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()[0]

check("не подошёл" in ownerbot.link(con, 555, "/crm_me 0000"), "Чужой код не подключает")
code = ownerbot.new_code(con, uid)
check("Готово" in ownerbot.link(con, 555, f"/crm_me {code}") and ownerbot.linked(con, uid), "Код из CRM подключает личный Telegram")
check("не подошёл" in ownerbot.link(con, 777, f"/crm_me {code}"), "Код одноразовый")

t0 = datetime(2026, 11, 2, 12, 0)
con.execute("INSERT INTO calls(phone, phone_norm, direction, status, started_at) VALUES ('+79001112233','79001112233','in','missed',?)", (t0.isoformat(),))
ownerbot.tick(con, t0 + timedelta(minutes=5))
check(not sent, "5 минут: никому ничего (звук в браузере у админов)")
ownerbot.tick(con, t0 + timedelta(minutes=11))
check(len(sent) == 1 and sent[0]["chat_id"] == "-100" and "не обработан уже 11 мин" in sent[0]["text"], "10 минут: напоминание в «Службу заботы»")
ownerbot.tick(con, t0 + timedelta(minutes=12))
check(len(sent) == 1, "Повторно в ту же десятиминутку не дёргаем")
ownerbot.tick(con, t0 + timedelta(minutes=31))
own = [p for p in sent if p["chat_id"] == "555"]
check(len(own) == 1 and "больше 30 минут" in own[0]["text"] and "crm.example" in own[0]["text"], "30 минут: руководителю в Telegram со ссылкой")
ownerbot.tick(con, t0 + timedelta(minutes=40))
check(len([p for p in sent if p["chat_id"] == "555"]) == 1, "То же событие руководителю второй раз не приходит")
con.execute("INSERT INTO alerts(kind, key, text, created_at) VALUES ('x','k1','Нет отметок 2 часа: Небо',?)", (t0.isoformat(),))
ownerbot.tick(con, datetime(2026, 11, 2, 23, 0))
check(len([p for p in sent if p["chat_id"] == "555"]) == 1, "Ночью руководителю не пишем")
ownerbot.tick(con, datetime(2026, 11, 3, 8, 1))
last = [p for p in sent if p["chat_id"] == "555"][-1]["text"]
check("Нет отметок" in last, "Утром накопленное приходит")
print("ok")
