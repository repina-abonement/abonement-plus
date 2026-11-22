import os
import tempfile
import unittest

from crm import comms, db


class MessageReplyContextTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(os.path.join(tempfile.mkdtemp(), "reply.sqlite3"))
        db.init_db(self.con)

    def test_wazzup_reply_resolves_original_in_same_conversation(self):
        base = {"chatType": "telegram", "chatId": "safe-chat", "channelId": "safe-channel",
                "contact": {"name": "Родитель"}}
        comms.wz_hook(self.con, {"messages": [base | {"messageId": "original-real-id", "isEcho": True,
            "text": "Оплата: https://ab-plus.ru/pay/synthetic"}]})
        comms.wz_hook(self.con, {"messages": [base | {"messageId": "reply-real-id", "text": "Оплачено",
            "quotedMessage": {"messageId": "original-real-id", "type": "text",
                              "text": "Оплата: https://ab-plus.ru/pay/synthetic"}}]})
        cid = self.con.execute("SELECT id FROM conversations WHERE external_id='safe-chat'").fetchone()[0]
        reply = [m for m in comms.conversation(self.con, cid, False)["messages"] if m["external_id"] == "reply-real-id"][0]
        self.assertEqual(reply["reply_context"]["external_id"], "original-real-id")
        self.assertIn("https://ab-plus.ru/pay/synthetic", reply["reply_context"]["text"])
        self.assertTrue(reply["reply_context"]["message_id"])

    def test_reference_never_crosses_conversations_and_uses_provider_snapshot(self):
        first = comms.upsert_conversation(self.con, "wazzup_telegram", "first")
        second = comms.upsert_conversation(self.con, "wazzup_telegram", "second")
        comms.add_message(self.con, first, "out", "Чужое сообщение", external_id="same-provider-id")
        comms.add_message(self.con, second, "in", "Ответ", external_id="answer",
                          reply_external_id="same-provider-id", reply_snapshot={"text": "Реальная цитата", "kind": "text"})
        reply = comms.conversation(self.con, second, False)["messages"][0]["reply_context"]
        self.assertNotIn("message_id", reply)
        self.assertEqual(reply["text"], "Реальная цитата")

    def test_direct_telegram_reply_to_message_is_preserved(self):
        comms.put(self.con, "tg_secret", "secret")
        original = {"message_id": 70, "date": 1795340000, "chat": {"id": 101, "type": "private"},
                    "from": {"id": 101}, "text": "Исходная ссылка https://example.test/pay"}
        comms.tg_hook(self.con, {"message": original}, {"X-Telegram-Bot-Api-Secret-Token": "secret"})
        reply = {"message_id": 71, "date": 1795340010, "chat": {"id": 101, "type": "private"},
                 "from": {"id": 101}, "text": "Готово", "reply_to_message": original}
        comms.tg_hook(self.con, {"message": reply}, {"X-Telegram-Bot-Api-Secret-Token": "secret"})
        cid = self.con.execute("SELECT id FROM conversations WHERE channel='telegram'").fetchone()[0]
        result = comms.conversation(self.con, cid, False)["messages"][-1]["reply_context"]
        self.assertEqual(result["external_id"], "m70")
        self.assertIn("Исходная ссылка", result["text"])


if __name__ == "__main__":
    unittest.main()
