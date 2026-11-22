"""Safe CRM-side actions for individual chat messages."""
from . import comms

EMOJI = ("👍", "❤️", "😂", "😮", "😢", "🙏")


def init(con):
    con.execute("""CREATE TABLE IF NOT EXISTS message_pins(
        message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL, created_at TEXT NOT NULL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS message_reactions(
        message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL, emoji TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(message_id,user_id))""")


def public(con, message, user=None):
    mid = int(message["id"])
    message["pinned"] = bool(con.execute("SELECT 1 FROM message_pins WHERE message_id=?", (mid,)).fetchone())
    uid = int(user["id"]) if user else 0
    message["reactions"] = [dict(r) for r in con.execute(
        """SELECT emoji,COUNT(*) count,MAX(CASE WHEN user_id=? THEN 1 ELSE 0 END) mine
           FROM message_reactions WHERE message_id=? GROUP BY emoji ORDER BY MIN(created_at)""", (uid, mid))]
    editable = message.get("direction") == "out" and message.get("kind") == "text" and bool(message.get("text"))
    message["can_edit"] = bool(editable and (str(message.get("external_id") or "").startswith("demo") or message.get("channel") == "telegram"))
    message["edit_reason"] = "" if message["can_edit"] else ("Можно редактировать только отправленный текст" if not editable else "Этот мессенджер не разрешает CRM редактировать сообщение")
    return message


def _message(con, mid):
    row = con.execute("SELECT m.*,c.channel FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE m.id=?", (mid,)).fetchone()
    if not row:
        raise comms.CommsError("Сообщение не найдено")
    return dict(row)


def pin(con, mid, user, value=True):
    _message(con, mid)
    if value:
        con.execute("INSERT INTO message_pins(message_id,user_id,created_at) VALUES (?,?,?) ON CONFLICT(message_id) DO UPDATE SET user_id=excluded.user_id,created_at=excluded.created_at", (mid, user["id"], comms.ts_now()))
    else:
        con.execute("DELETE FROM message_pins WHERE message_id=?", (mid,))
    return {"ok": True, "pinned": bool(value)}


def react(con, mid, user, emoji):
    _message(con, mid)
    emoji = str(emoji or "")
    if emoji and emoji not in EMOJI:
        raise comms.CommsError("Выберите реакцию из списка")
    if emoji:
        con.execute("INSERT INTO message_reactions(message_id,user_id,emoji,created_at) VALUES (?,?,?,?) ON CONFLICT(message_id,user_id) DO UPDATE SET emoji=excluded.emoji,created_at=excluded.created_at", (mid, user["id"], emoji, comms.ts_now()))
    else:
        con.execute("DELETE FROM message_reactions WHERE message_id=? AND user_id=?", (mid, user["id"]))
    return {"ok": True}


def forward(con, mid, target_id, user, request_id=""):
    message = _message(con, mid)
    text = str(message.get("text") or "").strip()
    if not text:
        raise comms.CommsError("Пересылка вложений пока недоступна; перешлите файл отдельно")
    if not con.execute("SELECT 1 FROM conversations WHERE id=?", (int(target_id),)).fetchone():
        raise comms.CommsError("Чат для пересылки не найден")
    result = comms.send(con, int(target_id), text, user, request_id)
    from . import services
    services.audit(con, user, "message.forward", f"message={mid};conversation={int(target_id)}")
    return result


def edit(con, mid, text, user):
    message = _message(con, mid)
    text = str(text or "").strip()
    if not text:
        raise comms.CommsError("Текст сообщения не может быть пустым")
    shown = public(con, dict(message), user)
    if not shown["can_edit"]:
        raise comms.CommsError(shown["edit_reason"])
    external = str(message.get("external_id") or "")
    if not external.startswith("demo"):
        conv = con.execute("SELECT * FROM conversations WHERE id=?", (message["conversation_id"],)).fetchone()
        raw_id = external[1:] if external[:1] in ("m", "b") else external
        if not raw_id.isdigit():
            raise comms.CommsError("Не найден идентификатор сообщения Telegram")
        payload = {"chat_id": int(conv["external_id"]), "message_id": int(raw_id), "text": text}
        import json
        meta = json.loads(conv["meta"] or "{}")
        if meta.get("biz"):
            payload["business_connection_id"] = meta["biz"]
        comms.tg_call(con, "editMessageText", payload)
    con.execute("UPDATE messages SET text=? WHERE id=?", (text, mid))
    latest = con.execute("SELECT id FROM messages WHERE conversation_id=? ORDER BY ts DESC,id DESC LIMIT 1", (message["conversation_id"],)).fetchone()
    if latest and latest[0] == mid:
        con.execute("UPDATE conversations SET last_text=? WHERE id=?", ("Вы: " + text[:140], message["conversation_id"]))
    from . import services
    services.audit(con, user, "message.edit", f"message={mid}")
    return {"ok": True}
