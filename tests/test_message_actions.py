import sqlite3
import unittest
from unittest.mock import patch

from crm import comms, db, messageactions


class MessageActionsTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        db.init_db(self.con)
        self.user = dict(self.con.execute("SELECT * FROM users WHERE role='admin' LIMIT 1").fetchone())
        self.cid = self.con.execute("INSERT INTO conversations(channel,external_id,title,created_at) VALUES ('telegram','123','Тест',?)", (comms.ts_now(),)).lastrowid
        self.mid = comms.add_message(self.con, self.cid, "out", "Исходный текст", external_id="m44", status="sent")

    def test_pin_and_reaction_are_returned_in_chat(self):
        messageactions.pin(self.con, self.mid, self.user, True)
        messageactions.react(self.con, self.mid, self.user, "👍")
        item = comms.conversation(self.con, self.cid, False, self.user)["messages"][0]
        self.assertTrue(item["pinned"])
        self.assertEqual(item["reactions"], [{"emoji": "👍", "count": 1, "mine": 1}])

    def test_forward_uses_normal_send_path(self):
        target = self.con.execute("INSERT INTO conversations(channel,external_id,title,created_at) VALUES ('max','demo-target','Другой чат',?)", (comms.ts_now(),)).lastrowid
        result = messageactions.forward(self.con, self.mid, target, self.user, "fwd_1234567890123456")
        self.assertTrue(result["id"])
        self.assertEqual(self.con.execute("SELECT text FROM messages WHERE id=?", (result["id"],)).fetchone()[0], "Исходный текст")

    def test_telegram_edit_is_confirmed_before_local_update(self):
        with patch.object(comms, "tg_call", return_value={}) as call:
            messageactions.edit(self.con, self.mid, "Новый текст", self.user)
        self.assertEqual(call.call_args.args[0:2], (self.con, "editMessageText"))
        self.assertEqual(self.con.execute("SELECT text FROM messages WHERE id=?", (self.mid,)).fetchone()[0], "Новый текст")

    def test_whatsapp_edit_uses_wazzup_before_local_update(self):
        self.con.execute("UPDATE conversations SET channel='whatsapp' WHERE id=?", (self.cid,))
        self.con.execute("UPDATE messages SET external_id='wz-message-id' WHERE id=?", (self.mid,))
        with patch.object(comms, "wz_call", return_value={}) as call:
            messageactions.edit(self.con, self.mid, "Исправленный текст", self.user)
        self.assertEqual(call.call_args.args[0:3], (self.con, "PATCH", "message/wz-message-id"))
        self.assertEqual(call.call_args.args[3], {"text": "Исправленный текст"})
        self.assertEqual(self.con.execute("SELECT text FROM messages WHERE id=?", (self.mid,)).fetchone()[0], "Исправленный текст")


if __name__ == "__main__":
    unittest.main()
