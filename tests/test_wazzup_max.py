"""MAX и WhatsApp через Wazzup: входящее приходит в «Чаты», ответ уходит в тот же канал. Запуск: python -m tests.test_wazzup_max"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import comms, db  # noqa: E402
from tests.test_crm import check  # noqa: E402


def main():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "w.sqlite3"))
    db.init_db(con)
    comms.wz_hook(con, {"messages": [
        {"chatType": "max", "chatId": "m-1", "channelId": "ch-max", "messageId": "a1", "type": "text", "text": "Здравствуйте",
         "dateTime": "2026-11-01T10:00:00Z", "contact": {"name": "Мама", "phone": "79001112233"}},
        {"chatType": "whatsapp", "chatId": "79001112244", "channelId": "ch-wa", "messageId": "a2", "type": "text", "text": "Добрый день",
         "dateTime": "2026-11-01T10:01:00Z", "contact": {"name": "Папа"}}]})
    rows = {r["channel"]: dict(r) for r in con.execute("SELECT * FROM conversations")}
    check("wazzup_max" in rows and "whatsapp" in rows, "Обе переписки пришли в «Чаты»")
    sent = {}
    comms.wz_call = lambda con, m, path, data=None, key=None: sent.update(data or {}) or {"messageId": "out1"}
    comms.send(con, rows["wazzup_max"]["id"], "Ответ", {"name": "Оля"})
    check(sent.get("chatType") == "max" and sent.get("channelId") == "ch-max" and sent.get("chatId") == "m-1", "Ответ ушёл в MAX тем же каналом")
    # чат начат из карточки по номеру, ответ пришёл с «+» или другим видом номера: та же переписка
    cid = comms.upsert_conversation(con, "whatsapp", "79005556677", phone="79005556677", meta={"chatType": "whatsapp"})
    comms.wz_hook(con, {"messages": [{"chatType": "whatsapp", "chatId": "+7 900 555-66-77", "channelId": "ch-wa", "messageId": "a3",
                                      "type": "text", "text": "Ответ", "dateTime": "2026-11-01T14:03:00Z"}]})
    n = con.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=? AND direction='in'", (cid,)).fetchone()[0]
    check(n == 1, "Ответ в WhatsApp попал в переписку, начатую из карточки")
    # один и тот же номер: WhatsApp, MAX и звонок видны одной лентой при открытии любого из чатов
    mx = comms.upsert_conversation(con, "wazzup_max", "m-9", phone="79005556677", meta={"chatType": "max"})
    comms.add_message(con, mx, "in", "Привет из MAX")
    con.execute("INSERT INTO calls(phone, phone_norm, direction, status, started_at) VALUES ('+79005556677','79005556677','in','missed','2026-11-01T12:00:00')")
    d = comms.conversation(con, cid)
    kinds = {(i["type"], i.get("channel")) for i in d["history"]}
    check(("msg", "whatsapp") in kinds and ("msg", "wazzup_max") in kinds and ("call", None) in kinds, "В чате вся история: WhatsApp, MAX и звонки")
    check({t["id"] for t in d["targets"]} == {cid, mx}, "Ответить можно в любой мессенджер этого человека")
    print("ok")


if __name__ == "__main__":
    main()
