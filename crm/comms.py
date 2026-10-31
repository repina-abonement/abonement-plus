"""Связь с клиентами: Telegram, MAX, WhatsApp (через Wazzup), телефония Sipuni, заявки с сайта.

Все внешние сервисы шлют события на /hooks/<сервис>/<секрет>. Секрет хранится в таблице
integrations и показывается администратору в Настройках. Сырые события пишутся в hook_log
(последние 300), чтобы разбирать, если сервис прислал что-то неожиданное.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import uuid
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .db import normalize_phone

SCHEMA = """
CREATE TABLE IF NOT EXISTS integrations (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY,
    channel TEXT NOT NULL,                 -- telegram | max | whatsapp
    external_id TEXT NOT NULL,             -- id чата в мессенджере
    title TEXT NOT NULL DEFAULT '',
    username TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    phone_norm TEXT NOT NULL DEFAULT '',
    family_id INTEGER REFERENCES families(id) ON DELETE SET NULL,
    meta TEXT NOT NULL DEFAULT '{}',
    unread INTEGER NOT NULL DEFAULT 0,
    last_at TEXT,
    last_text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(channel, external_id)
);
CREATE INDEX IF NOT EXISTS idx_conv_family ON conversations(family_id);
CREATE INDEX IF NOT EXISTS idx_conv_phone ON conversations(phone_norm);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    direction TEXT NOT NULL,               -- in | out
    text TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'text',     -- text | image | file | audio | video | sticker | contact | other
    media TEXT NOT NULL DEFAULT '',        -- ссылка или tg:<file_id>
    external_id TEXT,
    user_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',       -- '' | error | demo
    error TEXT NOT NULL DEFAULT '',
    ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_msg_ext ON messages(conversation_id, external_id) WHERE external_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY,
    external_id TEXT UNIQUE,
    direction TEXT NOT NULL DEFAULT 'in',  -- in | out
    phone TEXT NOT NULL DEFAULT '',
    phone_norm TEXT NOT NULL DEFAULT '',
    operator TEXT NOT NULL DEFAULT '',     -- внутренний номер сотрудника
    family_id INTEGER REFERENCES families(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'ringing',-- ringing | talking | answered | missed | busy | failed
    started_at TEXT NOT NULL,
    answered_at TEXT,
    ended_at TEXT,
    duration INTEGER,
    talk INTEGER,
    record_url TEXT NOT NULL DEFAULT '',
    transcript TEXT NOT NULL DEFAULT '',
    seen INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_calls_phone ON calls(phone_norm);
CREATE INDEX IF NOT EXISTS idx_calls_family ON calls(family_id);

CREATE TABLE IF NOT EXISTS lead_imports (
    ext_id TEXT PRIMARY KEY,
    student_id INTEGER,
    ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hook_log (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    source TEXT NOT NULL,
    payload TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT ''
);
"""

SECRET_KEYS = {"sipuni_secret", "tg_token", "max_token", "wazzup_key", "yc_api_key"}
EDITABLE_KEYS = {"sipuni_user", "sipuni_secret", "tg_token", "tg_relay", "max_token", "max_api", "wazzup_key", "bot_greeting",
                 "yc_api_key", "yc_folder", "ai_enabled", "ai_mode", "ai_model", "ai_min_talk", "lead_notify"}
DEFAULT_GREETING = ("Здравствуйте! Вы написали в нашу школу. Задайте вопрос здесь, администратор скоро ответит.\n"
                    "Чтобы мы сразу нашли вашу карточку, нажмите кнопку «Поделиться номером» ниже")
CHANNEL_LABELS = {"telegram": "Telegram", "max": "MAX", "whatsapp": "WhatsApp"}
KIND_LABELS = {"image": "📷 Фото", "file": "📎 Файл", "audio": "🎤 Голосовое", "video": "🎬 Видео",
               "sticker": "Стикер", "contact": "👤 Контакт", "other": "Вложение"}


class CommsError(Exception):
    pass


def ts_now() -> str:
    return datetime.now().replace(microsecond=0).isoformat(timespec="seconds")


def _ts_from_unix(v) -> str | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x <= 0:
        return None
    if x > 1e12:  # миллисекунды
        x /= 1000
    return datetime.fromtimestamp(x).replace(microsecond=0).isoformat(timespec="seconds")


# ---------------- настройки интеграций ----------------

def init(con):
    con.executescript(SCHEMA)
    ucols = {r[1] for r in con.execute("PRAGMA table_info(users)")}
    if "sip_number" not in ucols:
        con.execute("ALTER TABLE users ADD COLUMN sip_number TEXT NOT NULL DEFAULT ''")
    scols = {r[1] for r in con.execute("PRAGMA table_info(students)")}
    if "priority" not in scols:
        con.execute("ALTER TABLE students ADD COLUMN priority INTEGER NOT NULL DEFAULT 0")
    if not get(con, "hook_token"):
        put(con, "hook_token", secrets.token_urlsafe(18))
    if con.execute("SELECT name FROM sqlite_master WHERE name='conversations'").fetchone():
        merge_wazzup_duplicates(con)
    for k in ("tg_secret", "max_secret"):
        if not get(con, k):
            put(con, k, secrets.token_hex(16))


def get(con, key: str, default: str = "") -> str:
    r = con.execute("SELECT value FROM integrations WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def put(con, key: str, value: str):
    con.execute("INSERT INTO integrations(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value if value is not None else ""))


def status(con) -> dict:
    """Что подключено — без секретов. Видят все сотрудники."""
    return {
        "sipuni": bool(get(con, "sipuni_user") and get(con, "sipuni_secret")),
        "telegram": bool(get(con, "tg_token")) and get(con, "tg_ok") == "1",
        "telegram_business": get(con, "tg_business_enabled") == "1",
        "max": bool(get(con, "max_token")) and get(con, "max_ok") == "1",
        "whatsapp": bool(get(con, "wazzup_key") and get(con, "wazzup_channel")),
        "wazzup_telegram": bool(get(con, "wazzup_key") and get(con, "wazzup_tg_channel")),
        "ai": get(con, "ai_enabled") == "1" and bool(get(con, "yc_api_key")),
    }


def admin_view(con, base_url: str) -> dict:
    tok = get(con, "hook_token")
    vals = {}
    for k in EDITABLE_KEYS:
        v = get(con, k)
        vals[k] = ("••••" + v[-4:] if len(v) > 4 else "••••") if (k in SECRET_KEYS and v) else v
    if not vals.get("bot_greeting"):
        vals["bot_greeting"] = DEFAULT_GREETING
    if not vals.get("max_api"):
        vals["max_api"] = MAX_API_DEFAULT
    vals["ai_mode"] = vals.get("ai_mode") or "deferred"
    vals["ai_model"] = vals.get("ai_model") or "yandexgpt"
    vals["ai_min_talk"] = vals.get("ai_min_talk") or "25"
    vals["ai_enabled"] = vals.get("ai_enabled") or "0"
    return {
        "values": vals,
        "status": status(con),
        "hooks": {
            "sipuni": f"{base_url}/hooks/sipuni/{tok}",
            "lead": f"{base_url}/hooks/lead/{tok}",
        },
        "info": {
            "tg_bot": get(con, "tg_bot"), "tg_error": get(con, "tg_error"),
            "tg_business_enabled": get(con, "tg_business_enabled") == "1",
            "max_bot": get(con, "max_bot"), "max_error": get(con, "max_error"),
            "wazzup_channels": json.loads(get(con, "wazzup_channels") or "[]"),
            "wazzup_channel": get(con, "wazzup_channel"), "wazzup_error": get(con, "wazzup_error"),
            "sipuni_last": get(con, "sipuni_last"), "lead_last": get(con, "lead_last"),
            "ai_last_error": (con.execute("SELECT ai_error FROM calls WHERE ai_error != '' ORDER BY id DESC LIMIT 1")
                              .fetchone() or [""])[0],
        },
    }


def save_settings(con, data: dict):
    for k, v in data.items():
        if k not in EDITABLE_KEYS:
            continue
        v = (v or "").strip() if isinstance(v, str) else str(v)
        if k in SECRET_KEYS and v.startswith("••••"):
            continue  # не меняли
        put(con, k, v)
        if k in ("tg_token", "tg_relay"):
            put(con, "tg_ok", "0")
        if k == "tg_relay" and v and not v.startswith("https://"):
            raise CommsError("Адрес моста должен начинаться с https://")
        if k == "max_token":
            put(con, "max_ok", "0")


# ---------------- HTTP наружу ----------------

def _http(method: str, url: str, *, data=None, form=None, headers=None, timeout: int = 12, raw: bool = False, context=None):
    h = {"User-Agent": "AbonementPlus/0.2"} | (headers or {})
    body = None
    if data is not None:
        body = json.dumps(data, ensure_ascii=False).encode()
        h["Content-Type"] = "application/json"
    elif form is not None:
        body = urlencode(form).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded"
    req = Request(url, data=body, method=method, headers=h)
    try:
        with urlopen(req, timeout=timeout, context=context) as r:
            content = r.read()
            ctype = r.headers.get("Content-Type", "")
            status_code = r.status
    except HTTPError as e:
        content = e.read() or b""
        ctype = e.headers.get("Content-Type", "") if e.headers else ""
        status_code = e.code
    except (URLError, TimeoutError, OSError) as e:
        raise CommsError(f"нет связи с сервисом ({getattr(e, 'reason', e)})")
    if raw:
        return status_code, content, ctype
    try:
        js = json.loads(content.decode() or "null")
    except (ValueError, UnicodeDecodeError):
        js = {"_text": content[:500].decode(errors="replace")}
    return status_code, js


http = _http  # тесты подменяют


# ---------------- семьи и диалоги ----------------

def family_by_phone(con, phone_norm: str) -> int | None:
    if len(phone_norm or "") < 10:
        return None
    r = con.execute("SELECT family_id FROM parents WHERE phone_norm=? ORDER BY id LIMIT 1", (phone_norm,)).fetchone()
    return r[0] if r else None


def relink_phone(con, phone: str, family_id: int):
    """Новый телефон в семье: подтянуть к ней звонки и переписки с этого номера."""
    p = normalize_phone(phone or "")
    if len(p) < 10:
        return
    con.execute("UPDATE conversations SET family_id=? WHERE phone_norm=? AND family_id IS NULL", (family_id, p))
    con.execute("UPDATE calls SET family_id=? WHERE phone_norm=? AND family_id IS NULL", (family_id, p))


def upsert_conversation(con, channel: str, ext: str, *, title: str = "", username: str = "", phone: str = "",
                        meta: dict | None = None) -> int:
    ext = str(ext)
    pn = normalize_phone(phone) if phone else ""
    r = con.execute("SELECT * FROM conversations WHERE channel=? AND external_id=?", (channel, ext)).fetchone()
    if not r:
        cid = con.execute(
            """INSERT INTO conversations(channel, external_id, title, username, phone, phone_norm, family_id, meta,
               created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
            (channel, ext, title or "", username or "", phone or "", pn, family_by_phone(con, pn),
             json.dumps(meta or {}, ensure_ascii=False), ts_now())).lastrowid
        return cid
    sets, args = [], []
    if title and title != r["title"]:
        sets.append("title=?"); args.append(title)
    if username and username != r["username"]:
        sets.append("username=?"); args.append(username)
    if pn and pn != r["phone_norm"]:
        sets += ["phone=?", "phone_norm=?"]; args += [phone, pn]
        if not r["family_id"]:
            fam = family_by_phone(con, pn)
            if fam:
                sets.append("family_id=?"); args.append(fam)
    if meta:
        m = json.loads(r["meta"] or "{}")
        if any(m.get(k) != v for k, v in meta.items()):
            sets.append("meta=?"); args.append(json.dumps(m | meta, ensure_ascii=False))
    if sets:
        con.execute(f"UPDATE conversations SET {', '.join(sets)} WHERE id=?", (*args, r["id"]))
    return r["id"]


def add_message(con, cid: int, direction: str, text: str = "", *, kind: str = "text", media: str = "",
                external_id: str | None = None, user_name: str = "", status: str = "", error: str = "",
                ts: str | None = None) -> int | None:
    ts = ts or ts_now()
    cur = con.execute(
        """INSERT OR IGNORE INTO messages(conversation_id, direction, text, kind, media, external_id, user_name,
           status, error, ts) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (cid, direction, text or "", kind, media or "", str(external_id) if external_id is not None else None,
         user_name, status, error, ts))
    if not cur.rowcount:
        return None
    preview = (text or "").strip().replace("\n", " ")[:120] or KIND_LABELS.get(kind, "")
    if direction == "out":
        preview = "Вы: " + preview
        con.execute("UPDATE conversations SET last_at=?, last_text=?, unread=0 WHERE id=?", (ts, preview, cid))
    else:
        con.execute("UPDATE conversations SET last_at=?, last_text=?, unread=unread+1 WHERE id=?", (ts, preview, cid))
    return cur.lastrowid


def log_hook(con, source: str, payload, error: str = ""):
    try:
        text = json.dumps(payload, ensure_ascii=False, default=str)[:8000]
    except (TypeError, ValueError):
        text = str(payload)[:8000]
    con.execute("INSERT INTO hook_log(ts, source, payload, error) VALUES (?,?,?,?)", (ts_now(), source, text, error))
    con.execute("DELETE FROM hook_log WHERE id <= (SELECT MAX(id) - 300 FROM hook_log)")


# ---------------- Telegram ----------------

TG_API = "https://api.telegram.org"


def tg_api(con) -> str:
    """Адрес Telegram API. Если задан мост (tg_relay) - запросы идут через него: из России Telegram API недоступен."""
    relay = (get(con, "tg_relay") or "").rstrip("/")
    return f"{relay}/tg" if relay else TG_API


def tg_call(con, method: str, payload: dict | None = None, token: str | None = None) -> dict:
    tok = token or get(con, "tg_token")
    if not tok:
        raise CommsError("Telegram не подключён")
    code, js = http("POST", f"{tg_api(con)}/bot{tok}/{method}", data=payload or {})
    if not isinstance(js, dict) or not js.get("ok"):
        desc = (js or {}).get("description") if isinstance(js, dict) else None
        raise CommsError(f"Telegram: {desc or 'ошибка ' + str(code)}")
    return js.get("result")


def tg_connect(con, base_url: str) -> dict:
    try:
        me = tg_call(con, "getMe")
        tg_call(con, "setWebhook", {
            "url": (f"{get(con, 'tg_relay').rstrip('/')}/hook/telegram/{get(con, 'hook_token')}" if get(con, "tg_relay")
                    else f"{base_url}/hooks/telegram/{get(con, 'hook_token')}"),
            "secret_token": get(con, "tg_secret"),
            "allowed_updates": ["message", "edited_message", "business_connection", "business_message"],
            "drop_pending_updates": False,
        })
    except CommsError as e:
        put(con, "tg_ok", "0")
        put(con, "tg_error", str(e))
        raise
    put(con, "tg_bot", "@" + (me.get("username") or ""))
    put(con, "tg_ok", "1")
    put(con, "tg_error", "")
    return {"bot": get(con, "tg_bot")}


def _tg_kind_media(m: dict) -> tuple[str, str]:
    if m.get("photo"):
        return "image", "tg:" + m["photo"][-1]["file_id"]
    for key, kind in (("voice", "audio"), ("audio", "audio"), ("video", "video"), ("video_note", "video"),
                      ("document", "file"), ("sticker", "sticker"), ("animation", "video")):
        if m.get(key):
            return kind, "tg:" + m[key]["file_id"]
    if m.get("contact"):
        return "contact", ""
    if m.get("text") is not None:
        return "text", ""
    return "other", ""


def tg_hook(con, upd: dict, headers) -> None:
    if not hmac.compare_digest(headers.get("X-Telegram-Bot-Api-Secret-Token") or "", get(con, "tg_secret")):
        raise PermissionError("неверный секрет Telegram")
    if "business_connection" in upd:
        bc = upd["business_connection"]
        put(con, "tg_business_conn", bc.get("id", ""))
        put(con, "tg_business_owner", str((bc.get("user") or {}).get("id", "")))
        put(con, "tg_business_enabled", "1" if bc.get("is_enabled", True) else "0")
        return
    m = upd.get("message") or upd.get("business_message")
    if not m:
        return
    chat = m.get("chat") or {}
    if chat.get("type") in ("group", "supergroup"):
        # рабочие чаты сотрудников: команда в чате подключает его к уведомлениям CRM
        cmd = (m.get("text") or "").split("@")[0].strip().lower()
        names = {"/crm_coaches": ("tg_chat_coaches", "ТРЕНЕРЫ"), "/crm_care": ("tg_chat_care", "Служба заботы")}
        if cmd in names:
            put(con, names[cmd][0], str(chat.get("id")))
            try:
                tg_call(con, "sendMessage", {"chat_id": chat.get("id"), "text": f"✅ Чат подключён к CRM как «{names[cmd][1]}»"})
            except CommsError:
                pass
        return
    if chat.get("type") != "private":
        return
    biz = m.get("business_connection_id")
    sender = m.get("from") or {}
    if biz:
        put(con, "tg_business_conn", biz)
    outgoing = bool(biz) and str(sender.get("id")) == get(con, "tg_business_owner")
    title = " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x)
    kind, media = _tg_kind_media(m)
    text = m.get("text") or m.get("caption") or ""
    phone = ""
    if m.get("contact"):
        c = m["contact"]
        text = "Контакт: " + (c.get("phone_number") or "")
        if not c.get("user_id") or c.get("user_id") == chat.get("id"):
            phone = c.get("phone_number") or ""
    cid = upsert_conversation(con, "telegram", chat["id"], title=title, username=chat.get("username") or "",
                              phone=phone, meta={"biz": biz} if biz else None)
    if biz is None and not outgoing:
        # обычный бот: если клиент перестал писать через бизнес-аккаунт, отвечаем от бота
        con.execute("UPDATE conversations SET meta=json_set(meta, '$.biz', NULL) WHERE id=?", (cid,))
    new = add_message(con, cid, "out" if outgoing else "in", text, kind=kind, media=media,
                      external_id=f"{'b' if biz else 'm'}{m.get('message_id')}",
                      user_name="С телефона" if outgoing else "", ts=_ts_from_unix(m.get("date")))
    if not new or biz:
        return  # в бизнес-аккаунте бот сам ничего не отвечает
    try:
        if text.startswith("/start"):
            _tg_send_raw(con, cid, chat["id"], greeting(con), keyboard="contact")
        elif kind == "contact" and phone:
            consent_from_bot(con, cid)
            _tg_send_raw(con, cid, chat["id"], "Спасибо, номер получили", keyboard="remove")
    except CommsError as e:
        log_hook(con, "telegram", {"reply_failed": str(e)}, str(e))


def greeting(con) -> str:
    g = get(con, "bot_greeting") or DEFAULT_GREETING
    url = get(con, "public_url")
    if url and "/legal/" not in g:
        g += f"\n\nНажимая «Поделиться номером», вы соглашаетесь на обработку персональных данных: {url}/legal/consent"
    return g


def consent_from_bot(con, cid: int):
    """Родитель сам поделился номером после приветствия со ссылкой на согласие — фиксируем согласие в семье."""
    r = con.execute("SELECT family_id FROM conversations WHERE id=?", (cid,)).fetchone()
    if r and r[0] and get(con, "public_url"):
        con.execute("UPDATE families SET consent_pd_at=COALESCE(consent_pd_at, ?), consent_pd_how=CASE WHEN consent_pd_at IS NULL THEN 'bot' ELSE consent_pd_how END WHERE id=?",
                    (ts_now()[:16], r[0]))


def _tg_send_raw(con, cid, chat_id, text, *, biz=None, keyboard=None) -> str:
    payload = {"chat_id": chat_id, "text": text}
    if biz:
        payload["business_connection_id"] = biz
    if keyboard == "contact":
        payload["reply_markup"] = {"keyboard": [[{"text": "📱 Поделиться номером", "request_contact": True}]],
                                   "resize_keyboard": True, "one_time_keyboard": True}
    elif keyboard == "remove":
        payload["reply_markup"] = {"remove_keyboard": True}
    res = tg_call(con, "sendMessage", payload)
    ext = f"{'b' if biz else 'm'}{res.get('message_id')}"
    add_message(con, cid, "out", text, external_id=ext, user_name="Бот")
    return ext


# ---------------- MAX ----------------

MAX_API_DEFAULT = "https://platform-api2.max.ru"


def max_call(con, method: str, path: str, *, query: dict | None = None, data=None, token: str | None = None):
    tok = token or get(con, "max_token")
    if not tok:
        raise CommsError("MAX не подключён")
    base = (get(con, "max_api") or MAX_API_DEFAULT).rstrip("/")
    url = f"{base}/{path}" + (("?" + urlencode(query)) if query else "")
    code, js = http(method, url, data=data, headers={"Authorization": tok})
    if code >= 300:
        msg = js.get("message") if isinstance(js, dict) else None
        raise CommsError(f"MAX: {msg or 'ошибка ' + str(code)}")
    return js


def max_connect(con, base_url: str) -> dict:
    url = f"{base_url}/hooks/max/{get(con, 'hook_token')}"
    try:
        me = max_call(con, "GET", "me")
        subs = (max_call(con, "GET", "subscriptions") or {}).get("subscriptions") or []
        for s in subs:  # старые адреса этой CRM (например, до смены домена)
            if "/hooks/max/" in (s.get("url") or "") and s.get("url") != url:
                max_call(con, "DELETE", "subscriptions", query={"url": s["url"]})
        max_call(con, "POST", "subscriptions", data={
            "url": url, "update_types": ["message_created", "bot_started"], "secret": get(con, "max_secret")})
    except CommsError as e:
        put(con, "max_ok", "0")
        put(con, "max_error", str(e))
        raise
    put(con, "max_bot", (me or {}).get("name") or (me or {}).get("first_name") or "бот")
    put(con, "max_ok", "1")
    put(con, "max_error", "")
    return {"bot": get(con, "max_bot")}


def _vcf_phone(vcf: str) -> str:
    for line in (vcf or "").splitlines():
        if line.upper().startswith("TEL"):
            return line.split(":", 1)[-1].strip()
    return ""


def _max_user_name(u: dict) -> str:
    return " ".join(x for x in (u.get("first_name"), u.get("last_name")) if x) or u.get("name") or ""


def max_hook(con, upd: dict, headers) -> None:
    if not hmac.compare_digest(headers.get("X-Max-Bot-Api-Secret") or "", get(con, "max_secret")):
        raise PermissionError("неверный секрет MAX")
    t = upd.get("update_type")
    if t == "bot_started":
        u = upd.get("user") or {}
        cid = upsert_conversation(con, "max", upd.get("chat_id"), title=_max_user_name(u),
                                  username=u.get("username") or "", meta={"user_id": u.get("user_id")})
        try:
            _max_send_raw(con, cid, upd.get("chat_id"), greeting(con), contact_button=True)
        except CommsError as e:
            log_hook(con, "max", {"reply_failed": str(e)}, str(e))
        return
    if t != "message_created":
        return
    msg = upd.get("message") or {}
    rec = msg.get("recipient") or {}
    if rec.get("chat_type") not in (None, "dialog"):
        return
    sender = msg.get("sender") or {}
    body = msg.get("body") or {}
    if sender.get("is_bot"):
        return  # своё сообщение уже записано при отправке
    text, kind, media, phone = body.get("text") or "", "text", "", ""
    for a in body.get("attachments") or []:
        at = a.get("type")
        p = a.get("payload") or {}
        if at == "contact":
            phone = _vcf_phone(p.get("vcf_info") or "")
            kind, text = "contact", "Контакт: " + phone
        elif at in ("image", "video", "audio", "file", "sticker"):
            kind = {"image": "image", "video": "video", "audio": "audio", "file": "file", "sticker": "sticker"}[at]
            media = p.get("url") or ""
        elif at == "inline_keyboard":
            continue
        else:
            kind = kind if kind != "text" else "other"
    chat_id = rec.get("chat_id") or sender.get("user_id")
    cid = upsert_conversation(con, "max", chat_id, title=_max_user_name(sender), username=sender.get("username") or "",
                              phone=phone, meta={"user_id": sender.get("user_id")})
    new = add_message(con, cid, "in", text, kind=kind, media=media, external_id=body.get("mid"),
                      ts=_ts_from_unix(msg.get("timestamp")))
    if new and kind == "contact" and phone:
        consent_from_bot(con, cid)
        try:
            _max_send_raw(con, cid, chat_id, "Спасибо, номер получили")
        except CommsError:
            pass


def _max_send_raw(con, cid, chat_id, text, *, contact_button=False) -> str:
    data = {"text": text}
    if contact_button:
        data["attachments"] = [{"type": "inline_keyboard", "payload": {
            "buttons": [[{"type": "request_contact", "text": "📱 Поделиться номером"}]]}}]
    js = max_call(con, "POST", "messages", query={"chat_id": chat_id}, data=data)
    mid = (((js or {}).get("message") or {}).get("body") or {}).get("mid") or ("x" + uuid.uuid4().hex)
    add_message(con, cid, "out", text, external_id=mid, user_name="Бот")
    return mid


# ---------------- WhatsApp через Wazzup ----------------

WAZZUP_API = "https://api.wazzup24.com/v3"


def wz_call(con, method: str, path: str, data=None, key: str | None = None):
    k = key or get(con, "wazzup_key")
    if not k:
        raise CommsError("Wazzup не подключён")
    code, js = http(method, f"{WAZZUP_API}/{path}", data=data, headers={"Authorization": f"Bearer {k}"})
    if code >= 300:
        err = (js.get("error") or js.get("description") or js.get("_text")) if isinstance(js, dict) else None
        raise CommsError(f"Wazzup: {err or 'ошибка ' + str(code)}")
    return js


def wz_connect(con, base_url: str) -> dict:
    try:
        chans = wz_call(con, "GET", "channels") or []
        wz_call(con, "PATCH", "webhooks", {
            "webhooksUri": f"{base_url}/hooks/wazzup/{get(con, 'hook_token')}",
            "subscriptions": {"messagesAndStatuses": True, "contactsAndDealsCreation": False}})
    except CommsError as e:
        put(con, "wazzup_error", str(e))
        raise
    lst = [{"id": c.get("channelId"), "transport": c.get("transport"), "state": c.get("state"),
            "name": c.get("plainId") or c.get("name") or ""} for c in chans if isinstance(c, dict)]
    put(con, "wazzup_channels", json.dumps(lst, ensure_ascii=False))
    wa = [c for c in lst if c["transport"] in ("whatsapp", "wapi")]
    if wa and not any(c["id"] == get(con, "wazzup_channel") for c in wa):
        put(con, "wazzup_channel", wa[0]["id"])
    tg = [c for c in lst if c["transport"] in ("telegram", "tgapi")]  # рабочий аккаунт Telegram через Wazzup
    put(con, "wazzup_tg_channel", tg[0]["id"] if tg else "")
    put(con, "wazzup_error", "" if wa or tg else "В Wazzup нет каналов WhatsApp или Telegram")
    return {"channels": lst}


WZ_KINDS = {"text": "text", "image": "image", "audio": "audio", "video": "video", "document": "file",
            "vcard": "contact", "sticker": "sticker"}


def _utc_to_local(s: str) -> str | None:
    """Время Wazzup приходит в UTC (…Z): переводим в местное, как всё остальное в CRM."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
    except ValueError:
        return s[:19]


def merge_conversations(con, src: int, dst: int) -> None:
    """Две переписки с одним человеком в одном канале → одна. Сообщения переносим, повторы (эхо Wazzup) отбрасываем."""
    if src == dst:
        return
    for m in con.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY id", (src,)).fetchall():
        dup = m["external_id"] and con.execute("SELECT 1 FROM messages WHERE conversation_id=? AND external_id=?", (dst, m["external_id"])).fetchone()
        if not dup:
            con.execute("UPDATE messages SET conversation_id=? WHERE id=?", (dst, m["id"]))
    con.execute("DELETE FROM messages WHERE conversation_id=?", (src,))
    s, d = con.execute("SELECT * FROM conversations WHERE id=?", (src,)).fetchone(), con.execute("SELECT * FROM conversations WHERE id=?", (dst,)).fetchone()
    con.execute("""UPDATE conversations SET family_id=COALESCE(family_id, ?), phone=CASE WHEN phone='' THEN ? ELSE phone END,
                   phone_norm=CASE WHEN phone_norm='' THEN ? ELSE phone_norm END WHERE id=?""", (s["family_id"], s["phone"], s["phone_norm"], dst))
    last = con.execute("SELECT ts, text, direction FROM messages WHERE conversation_id=? ORDER BY ts DESC, id DESC LIMIT 1", (dst,)).fetchone()
    if last:
        con.execute("UPDATE conversations SET last_at=?, last_text=? WHERE id=?", (last[0], ("Вы: " if last[2] == "out" else "") + (last[1] or "")[:120], dst))
    con.execute("DELETE FROM conversations WHERE id=?", (src,))
    _ = d


def merge_wazzup_duplicates(con) -> int:
    """Переписка «по номеру» и настоящий чат Telegram одной семьи склеиваются в одну."""
    n = 0
    for p in con.execute("""SELECT id, family_id, phone_norm FROM conversations WHERE channel='wazzup_telegram'
                            AND external_id LIKE 'phone:%'""").fetchall():
        other = con.execute("""SELECT id FROM conversations WHERE channel='wazzup_telegram' AND external_id NOT LIKE 'phone:%' AND id!=?
                               AND ((family_id IS NOT NULL AND family_id=?) OR (phone_norm!='' AND phone_norm=?)) ORDER BY id LIMIT 1""",
                            (p["id"], p["family_id"], p["phone_norm"])).fetchone()
        if other:
            merge_conversations(con, p["id"], other[0])
            n += 1
    # эхо Wazzup собственных сообщений CRM («Из Wazzup» с тем же текстом в ту же минуту) — повтор
    con.execute("""DELETE FROM messages WHERE user_name='Из Wazzup' AND direction='out' AND EXISTS (
                     SELECT 1 FROM messages o WHERE o.conversation_id=messages.conversation_id AND o.id!=messages.id AND o.direction='out'
                     AND o.user_name!='Из Wazzup' AND o.text=messages.text AND substr(o.ts,1,16)=substr(messages.ts,1,16))""")
    return n


def _wz_tg_rekey(con, chat_id: str, phone: str, echo_id: str = "") -> None:
    """Первое сообщение в Telegram мы отправили «по номеру» (phone:…), а ответ приходит с настоящим номером чата.
    Связываем: по телефону, если Telegram его показал; иначе единственный недавний чат «по номеру» без ответа."""
    pn = normalize_phone(phone) if phone else ""
    row = None
    if echo_id:  # эхо нашего же сообщения: по его номеру точно знаем, какая переписка «по номеру» это была
        row = con.execute("""SELECT c.id FROM conversations c JOIN messages m ON m.conversation_id=c.id
                             WHERE c.channel='wazzup_telegram' AND c.external_id LIKE 'phone:%' AND m.external_id=?""", (echo_id,)).fetchone()
    if not row and pn:
        row = con.execute("SELECT id FROM conversations WHERE channel='wazzup_telegram' AND external_id=?", ("phone:" + pn,)).fetchone()
    existing = con.execute("SELECT id FROM conversations WHERE channel='wazzup_telegram' AND external_id=?", (chat_id,)).fetchone()
    if existing:
        if row:
            merge_conversations(con, row[0], existing[0])
        return
    if not row:
        since = (datetime.now() - timedelta(hours=48)).isoformat(timespec="seconds")
        cands = con.execute("""SELECT c.id FROM conversations c WHERE c.channel='wazzup_telegram' AND c.external_id LIKE 'phone:%'
                               AND EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id=c.id AND m.direction='out' AND m.ts>=?)
                               AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id=c.id AND m.direction='in')""", (since,)).fetchall()
        row = cands[0] if len(cands) == 1 else None
    if row:
        con.execute("UPDATE conversations SET external_id=? WHERE id=?", (chat_id, row[0]))


def wz_hook(con, payload: dict) -> None:
    for m in payload.get("messages") or []:
        ct = m.get("chatType") or ""
        if ct not in ("whatsapp", "telegram", "max"):
            continue
        channel = "whatsapp" if ct == "whatsapp" else f"wazzup_{ct}"
        contact = m.get("contact") or {}
        phone = m.get("chatId") if ct == "whatsapp" else (contact.get("phone") or "")
        if ct == "telegram":
            _wz_tg_rekey(con, str(m.get("chatId") or ""), phone, str(m.get("messageId") or "") if m.get("isEcho") else "")
        cid = upsert_conversation(con, channel, m.get("chatId"), title=contact.get("name") or "",
                                  username=contact.get("username") or "", phone=phone or "",
                                  meta={"channelId": m.get("channelId"), "chatType": ct})
        kind = WZ_KINDS.get(m.get("type") or "text", "other")
        echo = bool(m.get("isEcho"))
        if echo and m.get("messageId") and con.execute("SELECT 1 FROM messages WHERE external_id=?", (str(m["messageId"]),)).fetchone():
            continue  # эхо сообщения, которое CRM сама отправила: оно уже в переписке
        dt = _utc_to_local(m.get("dateTime") or "")
        add_message(con, cid, "out" if echo else "in", m.get("text") or "", kind=kind, media=m.get("contentUri") or "",
                    external_id=m.get("messageId"), user_name="Из Wazzup" if echo else "", ts=dt)


def _wz_send_raw(con, cid, conv: dict, text: str) -> str:
    meta = json.loads(conv["meta"] or "{}")
    crm_id = uuid.uuid4().hex
    body = {"channelId": meta.get("channelId") or get(con, "wazzup_channel"),
            "chatType": meta.get("chatType") or "whatsapp", "text": text, "crmMessageId": crm_id}
    if conv["external_id"].startswith("phone:"):  # первое сообщение в Telegram по номеру: чата ещё нет
        body["phone"] = conv["external_id"][6:]
    else:
        body["chatId"] = conv["external_id"]
    js = wz_call(con, "POST", "message", body)
    return (js or {}).get("messageId") or crm_id


# ---------------- отправка из CRM ----------------

def send(con, cid: int, text: str, user: dict) -> dict:
    text = (text or "").strip()
    if not text:
        raise CommsError("Пустое сообщение")
    conv = con.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
    if not conv:
        raise CommsError("Переписка не найдена")
    conv = dict(conv)
    name = user.get("name") or ""
    if conv["external_id"].startswith("demo-"):
        mid = add_message(con, cid, "out", text, user_name=name, status="demo", external_id="demo" + uuid.uuid4().hex)
        return {"id": mid, "demo": True}
    try:
        if conv["channel"] == "telegram":
            meta = json.loads(conv["meta"] or "{}")
            biz = meta.get("biz")
            payload = {"chat_id": int(conv["external_id"]), "text": text}
            if biz:
                payload["business_connection_id"] = biz
            res = tg_call(con, "sendMessage", payload)
            ext = f"{'b' if biz else 'm'}{res.get('message_id')}"
        elif conv["channel"] == "max":
            js = max_call(con, "POST", "messages", query={"chat_id": conv["external_id"]}, data={"text": text})
            ext = (((js or {}).get("message") or {}).get("body") or {}).get("mid") or ("x" + uuid.uuid4().hex)
        elif conv["channel"] == "whatsapp" or conv["channel"].startswith("wazzup_"):
            ext = _wz_send_raw(con, cid, conv, text)
        else:
            raise CommsError("Этот канал не поддерживается")
    except CommsError as e:
        add_message(con, cid, "out", text, user_name=name, status="error", error=str(e))
        raise
    mid = add_message(con, cid, "out", text, user_name=name, external_id=ext)
    return {"id": mid}


def start_whatsapp(con, phone: str) -> int:
    """Новая переписка в WhatsApp по номеру родителя (первым написать можно только через Wazzup)."""
    if not status(con)["whatsapp"]:
        raise CommsError("WhatsApp не подключён: нужен Wazzup")
    p = normalize_phone(phone)
    if len(p) < 11:
        raise CommsError("Неверный номер телефона")
    return upsert_conversation(con, "whatsapp", p, phone=p,
                               meta={"channelId": get(con, "wazzup_channel"), "chatType": "whatsapp"})


def start_wazzup_telegram(con, phone: str) -> int:
    """Новая переписка в Telegram от рабочего аккаунта (через Wazzup) по номеру родителя: можно написать первыми."""
    if not status(con)["wazzup_telegram"]:
        raise CommsError("Telegram через Wazzup не подключён")
    p = normalize_phone(phone)
    if len(p) < 11:
        raise CommsError("Неверный номер телефона")
    return upsert_conversation(con, "wazzup_telegram", "phone:" + p, phone=p,
                               meta={"channelId": get(con, "wazzup_tg_channel"), "chatType": "telegram"})


def media_for(con, mid: int) -> tuple[bytes, str]:
    r = con.execute("SELECT media FROM messages WHERE id=?", (mid,)).fetchone()
    if not r or not r[0]:
        raise CommsError("Вложения нет")
    media = r[0]
    if media.startswith("tg:"):
        f = tg_call(con, "getFile", {"file_id": media[3:]})
        url = f"{tg_api(con)}/file/bot{get(con, 'tg_token')}/{f['file_path']}"
    elif media.startswith("https://"):
        url = media
    else:
        raise CommsError("Вложение недоступно")
    code, content, ctype = http("GET", url, raw=True, timeout=30)
    if code >= 300:
        raise CommsError("Не удалось скачать вложение")
    return content, ctype or "application/octet-stream"


# ---------------- Sipuni ----------------

SIPUNI_API = "https://sipuni.com/api"


def sipuni_call(con, phone: str, sip_number: str) -> dict:
    user, secret = get(con, "sipuni_user"), get(con, "sipuni_secret")
    if not (user and secret):
        raise CommsError("Sipuni не подключён")
    if not sip_number:
        raise CommsError("Не указан ваш внутренний номер Sipuni. Его задаёт администратор в Настройках → Сотрудники")
    p = normalize_phone(phone)
    if len(p) < 11:
        raise CommsError("Неверный номер телефона")
    params = {"antiaon": "0", "phone": p, "reverse": "0", "sipnumber": str(sip_number), "user": user}
    params["hash"] = hashlib.md5("+".join([*params.values(), secret]).encode()).hexdigest()
    code, js = http("POST", f"{SIPUNI_API}/callback/call_number", form=params)
    ok = code < 300 and not (isinstance(js, dict) and js.get("success") is False)
    if not ok:
        msg = (js.get("message") or js.get("_text")) if isinstance(js, dict) else None
        raise CommsError(f"Sipuni: {msg or 'ошибка ' + str(code)}")
    # перезвонили — пропущенные с этого номера больше не висят
    con.execute("UPDATE calls SET seen=1 WHERE phone_norm=? AND seen=0", (p,))
    return {"ok": True}


SIPUNI_STATUS = {"ANSWER": "answered", "ANSWERED": "answered", "NOANSWER": "missed", "NO ANSWER": "missed",
                 "CANCEL": "missed", "BUSY": "busy", "CONGESTION": "failed", "CHANUNAVAIL": "failed"}


def sipuni_hook(con, p: dict) -> None:
    put(con, "sipuni_last", ts_now())
    ev = str(p.get("event") or "")
    call_id = str(p.get("call_id") or p.get("callId") or p.get("id") or "")
    if not call_id or ev not in ("1", "2", "3"):
        return
    # внутренние номера приходят логином «кабинет+короткий» (077804201), короткий номер — в short_*
    src = str(p.get("short_src_num") or p.get("src_num") or "").strip()
    dst = str(p.get("short_dst_num") or p.get("dst_num") or "").strip()
    st, dt = str(p.get("src_type") or ""), str(p.get("dst_type") or "")
    if st == "2" and dt == "2":
        return  # звонок между сотрудниками
    outgoing = st == "2"
    client = dst if outgoing else src
    operator = src if outgoing else (dst if dt == "2" else "")
    pn = normalize_phone(client)
    ts = _ts_from_unix(p.get("timestamp")) or ts_now()
    row = con.execute("SELECT * FROM calls WHERE external_id=?", (call_id,)).fetchone()
    if not row:
        started = _ts_from_unix(p.get("call_start_timestamp")) or ts
        con.execute("""INSERT INTO calls(external_id, direction, phone, phone_norm, operator, family_id, status,
                       started_at) VALUES (?,?,?,?,?,?,?,?)""",
                    (call_id, "out" if outgoing else "in", client, pn, operator, family_by_phone(con, pn),
                     "ringing", started))
        row = con.execute("SELECT * FROM calls WHERE external_id=?", (call_id,)).fetchone()
    if ev == "3":
        con.execute("UPDATE calls SET status='talking', answered_at=COALESCE(answered_at, ?), operator=? WHERE id=?",
                    (ts, operator or row["operator"], row["id"]))
    elif ev == "2":
        stat = SIPUNI_STATUS.get(str(p.get("status") or "").upper(), "failed")
        answered = row["answered_at"] or _ts_from_unix(p.get("call_answer_timestamp"))
        if answered and not p.get("status"):
            stat = "answered"
        start = datetime.fromisoformat(row["started_at"])
        end = datetime.fromisoformat(ts)
        talk = int((end - datetime.fromisoformat(answered)).total_seconds()) if answered else None
        con.execute("""UPDATE calls SET status=?, ended_at=?, answered_at=?, duration=?, talk=?, record_url=?,
                       operator=CASE WHEN operator='' THEN ? ELSE operator END WHERE id=?""",
                    (stat, ts, answered, max(0, int((end - start).total_seconds())), talk,
                     str(p.get("call_record_link") or p.get("record") or row["record_url"] or ""), operator, row["id"]))
        if stat == "answered" and pn:
            con.execute("UPDATE calls SET seen=1 WHERE phone_norm=? AND seen=0", (pn,))


def record_for(con, call_id: int) -> tuple[bytes, str]:
    r = con.execute("SELECT record_url FROM calls WHERE id=?", (call_id,)).fetchone()
    if not r or not r[0]:
        raise CommsError("Записи нет")
    code, content, ctype = http("GET", r[0], raw=True, timeout=40)
    if code >= 300 or not content:
        raise CommsError("Sipuni не отдал запись")
    if "json" in (ctype or "") or "html" in (ctype or ""):
        raise CommsError("Запись пока недоступна, попробуйте через минуту")
    return content, ctype if (ctype or "").startswith("audio") else "audio/mpeg"


# ---------------- заявки с сайта ----------------

LEAD_KEYS = {  # формы сайтов называют поля по-разному: «Name», «Телефон», «Возраст ребёнка»...
    "name": ("name", "имя", "fio", "фио", "parent", "родитель", "имя родителя"),
    "phone": ("phone", "телефон", "tel", "номер", "номер телефона"),
    "child": ("child", "ребенок", "ребёнок", "имя ребенка", "имя ребёнка"),
    "age": ("age", "возраст", "возраст ребенка", "возраст ребёнка"),
    "branch": ("branch", "филиал", "адрес", "площадка"),
    "city": ("city", "город"),
    "comment": ("comment", "комментарий", "сообщение", "message"),
    "id": ("id", "ext_id", "tranid", "leadid", "lead_id", "request_id"),
}


def lead_fields(data: dict) -> dict:
    low = {str(k).strip().lower(): v for k, v in data.items()}
    out = {}
    for key, names in LEAD_KEYS.items():
        v = next((low[n] for n in names if low.get(n) not in (None, "")), None)
        if v is not None:
            out[key] = str(v)
    return out


def lead_hook(con, data: dict, create_student) -> dict:
    """Заявка с сайта → карточка в «Новый лид», красная (высокий приоритет)."""
    if {str(k).lower(): v for k, v in data.items()} == {"test": "test"}:
        return {"ok": True, "test": True}  # проверка адреса при подключении формы (Tilda и др.)
    data = data | lead_fields(data)
    put(con, "lead_last", ts_now())
    ext = str(data.get("id") or data.get("ext_id") or "").strip()
    if ext:
        done = con.execute("SELECT student_id FROM lead_imports WHERE ext_id=?", (ext,)).fetchone()
        if done:
            return {"ok": True, "duplicate": True, "student_id": done[0]}
    name = (data.get("name") or "").strip()
    phone = (data.get("phone") or "").strip()
    if name in ("—", "-"):
        name = ""
    if len(normalize_phone(phone)) < 11:
        raise CommsError("В заявке нет телефона")
    details = [f"{k}: {v}" for k, v in (("Город", data.get("city")), ("Филиал", data.get("branch")),
                                          ("Ребёнок", data.get("child")), ("Возраст", data.get("age")),
                                          ("Комментарий", data.get("comment"))) if v and v not in ("—", "-")]
    pn = normalize_phone(phone)
    fam = family_by_phone(con, pn)
    sid = None
    if fam:
        open_lead = con.execute("""SELECT id, stage FROM students WHERE family_id=? AND funnel='sales'
                                   AND stage NOT IN ('bought','refused') ORDER BY id DESC LIMIT 1""", (fam,)).fetchone()
        if open_lead and open_lead["stage"] in ("missed_trial", "later"):
            from . import pu
            sid = open_lead[0]
            con.execute("UPDATE students SET stage='missed_trial', stage_changed_at=? WHERE id=?", (ts_now()[:16], sid))
            pu.repeat_request(con, sid)
            _lead_note(con, sid, "Повторная заявка с сайта", details)
        elif open_lead:
            sid = open_lead[0]
            con.execute("UPDATE students SET priority=1, stage='lead', stage_changed_at=? WHERE id=?",
                        (ts_now()[:16], sid))
            _lead_note(con, sid, "Повторная заявка с сайта", details)
    if not sid:
        child = (data.get("child") or "").strip() or "Ребёнок (заявка с сайта)"
        site = con.execute("""SELECT s.id FROM ad_sources s JOIN ad_channels c ON c.id=s.channel_id
                              WHERE c.name='Сайт' AND s.active=1 ORDER BY s.id LIMIT 1""").fetchone()
        body = {"name": child, "source": "Сайт", "funnel": "sales", "source_id": site[0] if site else None}
        if fam:
            body["family_id"] = fam
        else:
            body |= {"parent_name": name or "Родитель", "phone": phone, "messenger": ""}
        sid = create_student(con, body, {"name": "Заявка с сайта"})
        con.execute("UPDATE students SET priority=1 WHERE id=?", (sid,))
        from . import capacity
        bid = capacity.match_branch(con, " ".join(str(data.get(k) or "") for k in ("branch", "city", "comment")))
        if bid:
            con.execute("UPDATE students SET branch_id=? WHERE id=?", (bid, sid))
        _lead_note(con, sid, "Заявка с сайта", details)
        relink_phone(con, phone, con.execute("SELECT family_id FROM students WHERE id=?", (sid,)).fetchone()[0])
    if ext:
        con.execute("INSERT OR IGNORE INTO lead_imports(ext_id, student_id, ts) VALUES (?,?,?)", (ext, sid, ts_now()))
    if get(con, "lead_notify") == "1":  # сообщение в «Службу заботы» шлёт сама CRM (вместо Google-скрипта)
        from . import pu
        pu.notify_chat(con, "care", "\n".join(["⚠️ Срочно! Упала заявка с сайта!", "", f"Имя: {name or '—'}", f"Телефон: {phone}",
                                                f"Город: {data.get('city') or '—'}", f"Филиал: {data.get('branch') or '—'}", "",
                                                "Она уже в CRM: «Новый лид», красная карточка. Обработать"]))
    return {"ok": True, "student_id": sid}


def _lead_note(con, sid, title, details):
    con.execute("INSERT INTO history(student_id, ts, user_name, kind, text) VALUES (?,?,?,?,?)",
                (sid, ts_now()[:16], "Заявка с сайта", "lead", title + (". " + "; ".join(details) if details else "")))


# ---------------- ленты для интерфейса ----------------

def _conv_out(con, r) -> dict:
    d = dict(r)
    d["meta"] = json.loads(d["meta"] or "{}")
    d["channel_label"] = CHANNEL_LABELS.get(d["channel"], d["channel"].replace("wazzup_", "").capitalize())
    d["family_name"] = ""
    d["students"] = []
    if d["family_id"]:
        f = con.execute("SELECT name FROM families WHERE id=?", (d["family_id"],)).fetchone()
        d["family_name"] = f[0] if f else ""
        d["students"] = [dict(x) for x in con.execute(
            "SELECT id, name, funnel, stage, birth, group_id, trial_at FROM students WHERE family_id=? AND archived=0 ORDER BY id", (d["family_id"],))]
    d["demo"] = d["external_id"].startswith("demo-")
    return d


def inbox(con, filt: str = "all", q: str = "") -> dict:
    where, args = [], []
    if filt == "unread":
        where.append("c.unread > 0")
    elif filt == "nofamily":
        where.append("c.family_id IS NULL")
    if q.strip():
        like = f"%{q.strip().lower()}%"
        pq = normalize_phone(q)
        where.append("""(ulower(c.title) LIKE ? OR ulower(c.username) LIKE ? OR (? != '' AND c.phone_norm LIKE ?)
                        OR c.family_id IN (SELECT id FROM families WHERE ulower(name) LIKE ?))""")
        args += [like, like, pq, f"%{pq}%", like]
    sql = "SELECT c.* FROM conversations c" + (" WHERE " + " AND ".join(where) if where else "")
    sql += " ORDER BY c.last_at IS NULL, c.last_at DESC LIMIT 200"
    return {"conversations": [_conv_out(con, r) for r in con.execute(sql, args)], **counts(con)}


def counts(con) -> dict:
    return {
        "unread": con.execute("SELECT COUNT(*) FROM conversations WHERE unread > 0").fetchone()[0],
        "missed": con.execute("SELECT COUNT(*) FROM calls WHERE direction='in' AND status IN ('missed','busy') AND seen=0")
                     .fetchone()[0],
        # для звука: номера последних входящих (сообщение, пропущенный звонок, заявка с сайта)
        "last_in": con.execute("SELECT COALESCE(MAX(id), 0) FROM messages WHERE direction='in'").fetchone()[0],
        "last_missed": con.execute("SELECT COALESCE(MAX(id), 0) FROM calls WHERE direction='in' AND status IN ('missed','busy')").fetchone()[0],
        "last_lead": con.execute("SELECT COALESCE(MAX(id), 0) FROM students WHERE priority=1").fetchone()[0],
    }


def conversation(con, cid: int, mark_read: bool = True) -> dict:
    r = con.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
    if not r:
        raise CommsError("Переписка не найдена")
    msgs = [dict(x) for x in con.execute(
        "SELECT * FROM (SELECT * FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT 200) ORDER BY id", (cid,))]
    if mark_read and r["unread"]:
        con.execute("UPDATE conversations SET unread=0 WHERE id=?", (cid,))
    return {"conversation": _conv_out(con, r), "messages": msgs}


def link(con, cid: int, family_id) -> None:
    if family_id and not con.execute("SELECT 1 FROM families WHERE id=?", (family_id,)).fetchone():
        raise CommsError("Семья не найдена")
    con.execute("UPDATE conversations SET family_id=? WHERE id=?", (family_id or None, cid))


def calls_list(con, filt: str = "all") -> list[dict]:
    where = ""
    if filt == "missed":
        where = "WHERE c.direction='in' AND c.status IN ('missed','busy') AND c.seen=0"
    rows = []
    for r in con.execute(f"""SELECT c.*, f.name AS family_name FROM calls c LEFT JOIN families f ON f.id=c.family_id
                             {where} ORDER BY c.started_at DESC, c.id DESC LIMIT 200"""):
        d = dict(r)
        d["students"] = [dict(x) for x in con.execute(
            "SELECT id, name FROM students WHERE family_id=? ORDER BY id", (d["family_id"],))] if d["family_id"] else []
        d["has_record"] = bool(d.pop("record_url"))
        rows.append(d)
    return rows


def mark_calls_seen(con):
    con.execute("UPDATE calls SET seen=1 WHERE seen=0")


def family_comms(con, family_id: int) -> dict:
    phones = [r[0] for r in con.execute("SELECT phone_norm FROM parents WHERE family_id=? AND phone_norm != ''",
                                        (family_id,))]
    ph_sql = ",".join("?" * len(phones)) or "''"
    convs = [_conv_out(con, r) for r in con.execute(
        f"SELECT * FROM conversations WHERE family_id=? OR phone_norm IN ({ph_sql}) ORDER BY last_at DESC",
        (family_id, *phones))]
    items = []
    for c in convs:
        for m in con.execute("SELECT * FROM (SELECT * FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT 60)",
                             (c["id"],)):
            items.append({"type": "msg", "ts": m["ts"], "channel": c["channel"], "channel_label": c["channel_label"],
                          "conversation_id": c["id"], **{k: m[k] for k in ("id", "direction", "text", "kind", "media",
                                                                            "user_name", "status", "error")}})
    for k in con.execute(f"SELECT * FROM calls WHERE family_id=? OR phone_norm IN ({ph_sql}) ORDER BY id DESC LIMIT 60",
                         (family_id, *phones)):
        items.append({"type": "call", "ts": k["started_at"], "id": k["id"], "direction": k["direction"],
                      "status": k["status"], "talk": k["talk"], "duration": k["duration"], "phone": k["phone"],
                      "operator": k["operator"], "has_record": bool(k["record_url"]), "transcript": k["transcript"],
                      "score": k["score"], "ai_status": k["ai_status"]})
    items.sort(key=lambda x: (x["ts"] or "", x["id"]))
    for c in convs:
        if c["unread"]:
            con.execute("UPDATE conversations SET unread=0 WHERE id=?", (c["id"],))
    return {"conversations": convs, "items": items[-120:], "status": status(con)}


# ---------------- тестовые данные ----------------

def seed_demo(con):
    """Пара выдуманных переписок и звонков, чтобы было видно, как это выглядит. Только в тестовом режиме."""
    if con.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] or \
            con.execute("SELECT COUNT(*) FROM calls").fetchone()[0]:
        return
    now = datetime.now().replace(microsecond=0)
    t = lambda mins: (now - timedelta(minutes=mins)).isoformat(timespec="seconds")
    fam = con.execute("SELECT family_id FROM parents WHERE phone_norm='79001111111'").fetchone()
    fam2 = con.execute("SELECT family_id FROM parents WHERE phone_norm='79005555555'").fetchone()
    c1 = con.execute("""INSERT INTO conversations(channel, external_id, title, phone, phone_norm, family_id, created_at)
                        VALUES ('telegram','demo-1','Анна Иванова','+7 900 111-11-11','79001111111',?,?)""",
                     (fam[0] if fam else None, t(300))).lastrowid
    add_message(con, c1, "in", "Добрый день! Миша в пятницу не придёт, заболел", ts=t(180))
    add_message(con, c1, "out", "Анна, добрый день! Поняла, отметим. Скорейшего выздоровления Мише", user_name="Оператор",
                ts=t(170), status="demo", external_id="demo-a")
    add_message(con, c1, "in", "Спасибо! А отработать можно в субботу?", ts=t(25))
    c2 = con.execute("""INSERT INTO conversations(channel, external_id, title, created_at)
                        VALUES ('max','demo-2','Марина',?)""", (t(60),)).lastrowid
    add_message(con, c2, "in", "Здравствуйте, сколько стоит пробное для ребёнка 4 лет?", ts=t(12))
    if fam2:
        con.execute("""INSERT INTO calls(external_id, direction, phone, phone_norm, family_id, status, started_at,
                       ended_at, duration) VALUES ('demo-call-1','in','+7 900 555-55-55','79005555555',?,'missed',?,?,20)""",
                    (fam2[0], t(40), t(40)))
    if fam:
        con.execute("""INSERT INTO calls(external_id, direction, phone, phone_norm, operator, family_id, status, started_at,
                       answered_at, ended_at, duration, talk, seen) VALUES
                       ('demo-call-2','out','+7 900 111-11-11','79001111111','201',?,'answered',?,?,?,95,80,1)""",
                    (fam[0], t(160), t(160), t(158)))
    con.execute("""INSERT INTO calls(external_id, direction, phone, phone_norm, status, started_at, ended_at, duration)
                   VALUES ('demo-call-3','in','+7 999 123-45-67','79991234567','missed',?,?,15)""", (t(8), t(8)))


DEMO_TABLES = ["messages", "conversations", "calls", "lead_imports", "hook_log"]


# ---------------- рабочий стол ----------------

def missed_by_family(con) -> dict:
    """Семьи с пропущенными и ещё не перезвоненными звонками: family_id → время последнего."""
    out = {}
    for fid, ts in con.execute("""SELECT family_id, MAX(started_at) FROM calls WHERE direction='in'
                                  AND status IN ('missed','busy') AND seen=0 AND family_id IS NOT NULL GROUP BY family_id"""):
        out[fid] = ts
    return out


def urgent(con) -> list[dict]:
    """Что горит прямо сейчас. Сортировка: красное сверху, внутри по времени."""
    items = []
    for k in con.execute("""SELECT c.*, f.name AS family_name FROM calls c LEFT JOIN families f ON f.id=c.family_id
                            WHERE c.direction='in' AND c.status IN ('missed','busy') AND c.seen=0
                            ORDER BY c.started_at DESC LIMIT 50"""):
        studs = [dict(x) for x in con.execute("SELECT id, name FROM students WHERE family_id=? ORDER BY id",
                                              (k["family_id"],))] if k["family_id"] else []
        items.append({"kind": "missed_call", "level": "red", "ts": k["started_at"], "id": k["id"], "phone": k["phone"],
                      "title": (studs[0]["name"] if studs else k["phone"]),
                      "text": "Пропущенный звонок" + (f", семья {k['family_name']}" if k["family_name"] else ", номера нет в базе"),
                      "student_id": studs[0]["id"] if studs else None})
    for s in con.execute("""SELECT s.id, s.name, s.stage_changed_at, p.phone FROM students s
                            LEFT JOIN parents p ON p.id = (SELECT MIN(id) FROM parents WHERE family_id=s.family_id)
                            WHERE s.priority=1 ORDER BY s.stage_changed_at DESC"""):
        items.append({"kind": "site_lead", "level": "red", "ts": s["stage_changed_at"], "student_id": s["id"],
                      "phone": s["phone"] or "", "title": s["name"], "text": "Заявка с сайта, позвонить сейчас"})
    for c in con.execute("""SELECT c.*, f.name AS family_name FROM conversations c LEFT JOIN families f ON f.id=c.family_id
                            WHERE c.unread > 0 ORDER BY c.last_at DESC LIMIT 50"""):
        items.append({"kind": "message", "level": "yellow", "ts": c["last_at"], "conversation_id": c["id"],
                      "title": c["title"] or c["username"] or c["phone"] or "Без имени",
                      "text": f"{CHANNEL_LABELS.get(c['channel'], c['channel'])}: {c['last_text']}",
                      "count": c["unread"]})
    return items
