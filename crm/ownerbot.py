"""Красное с рабочего стола руководителя: лично ему в Telegram (Юлия, 01.11.2026).

Подключение: в «Моём профиле» кнопка даёт код, руководитель отправляет боту школы «/crm_me КОД».
Каждое событие приходит один раз; несколько событий за проход складываются в одно сообщение.
Ночью (22:00-8:00) бот молчит: накопленное приходит утренней сводкой.
Пропущенный звонок и заявка с сайта приходят руководителю, только если админы не обработали их за 30 минут:
до этого их дёргает звук в браузере и сообщения в чат «Служба заботы» (каждые 10 минут).
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from . import comms

SCHEMA = """
CREATE TABLE IF NOT EXISTS owner_sent (
    user_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
"""
OWNER_AFTER_MIN = 30   # пропущенный звонок / заявка: руководителю, если не обработали за 30 минут
CARE_EVERY_MIN = 10    # до этого: напоминание в чат «Служба заботы» каждые 10 минут
QUIET = (22, 8)


def init(con):
    con.executescript(SCHEMA)


def _iso(d: datetime) -> str:
    return d.isoformat(timespec="seconds")


# ---------- подключение ----------

def new_code(con, uid: int) -> str:
    code = f"{secrets.randbelow(9000) + 1000}"
    comms.put(con, f"owner_code_{uid}", f"{code}|{_iso(datetime.now())}")
    return code


def link(con, chat_id, text: str) -> str:
    """Сообщение боту «/crm_me 1234»: привязываем личный Telegram руководителя. Код живёт 30 минут."""
    code = (text.split() + [""])[1].strip()
    for (uid,) in con.execute("SELECT id FROM users WHERE role='admin' AND active=1").fetchall():
        v = comms.get(con, f"owner_code_{uid}")
        if not v or "|" not in v:
            continue
        c, at = v.split("|", 1)
        if code and secrets.compare_digest(c, code) and datetime.fromisoformat(at) > datetime.now() - timedelta(minutes=30):
            comms.put(con, f"owner_tg_{uid}", str(chat_id))
            comms.put(con, f"owner_code_{uid}", "")
            return "✅ Готово: сюда будет приходить всё красное с вашего рабочего стола в CRM"
    return "Код не подошёл или устарел. Возьмите новый в CRM: Мой профиль → «Красное в Telegram»"


def linked(con, uid: int) -> bool:
    return bool(comms.get(con, f"owner_tg_{uid}"))


def unlink(con, uid: int):
    comms.put(con, f"owner_tg_{uid}", "")


# ---------- что красное ----------

def _hm(iso: str) -> str:
    return (iso or "")[11:16]


def collect(con, now: datetime) -> list[tuple[str, str]]:
    from . import capacity  # noqa: F401 — модули с таблицами уже инициализированы
    out = []
    late = _iso(now - timedelta(minutes=OWNER_AFTER_MIN))
    for k in con.execute("""SELECT c.*, f.name AS family FROM calls c LEFT JOIN families f ON f.id=c.family_id
                            WHERE c.direction='in' AND c.status IN ('missed','busy') AND c.seen=0 AND c.started_at <= ?
                              AND c.started_at >= ?""", (late, _iso(now - timedelta(days=2)))):
        who = f"семья {k['family']}" if k["family"] else k["phone"]
        out.append((f"call:{k['id']}", f"📞 Пропущенный звонок {_hm(k['started_at'])} ({who}) не обработан больше 30 минут"))
    for s in con.execute("""SELECT id, name, stage_changed_at FROM students WHERE priority=1 AND archived=0
                            AND stage_changed_at <= ?""", (late,)):
        out.append((f"lead:{s['id']}", f"🔥 Заявка с сайта ({s['name']}) с {_hm(s['stage_changed_at'])}: больше 30 минут без ответа"))
    for c in con.execute("SELECT * FROM conversations WHERE open_q=1 AND open_due <= ?", (now.isoformat(timespec="minutes"),)):
        out.append((f"oq:{c['id']}:{c['open_due']}", f"❗ Открытый вопрос в переписке «{c['title'] or c['phone']}»"
                                                   f"{': ' + c['open_note'] if c['open_note'] else ''} (отметил(а) {c['open_by'] or '?'})"))
    try:
        from . import reviews
        for r in reviews.open_list(con):
            out.append((f"rv:{r['id']}", f"⭐ Новый отзыв на {r['label']}: {r['subject']}"))
    except Exception:  # noqa: BLE001
        pass
    for a in con.execute("SELECT id, text FROM alerts WHERE closed_at IS NULL ORDER BY id"):
        out.append((f"alert:{a['id']}", f"⚠️ {a['text']}"))
    try:
        from . import schedule
        soon = (now.date() + timedelta(days=2)).isoformat()
        for x in schedule.needs_subs(con, now.date()):
            if x["date"] <= soon:
                out.append((f"sub:{x['group_id']}:{x['date']}:{x['role']}",
                            f"🔁 Нет замены: {x['absent']} отсутствует, {x['group']} ({x['branch']}) {x['date'][8:]}.{x['date'][5:7]} {x['time'] or ''}"))
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import staff
        for r in (staff.day_report(con, now.date(), now) or {}).get("rows", []):
            if r["state"] in ("late", "absent"):
                what = "опоздал(а)" if r["state"] == "late" else "не отметил(а) приход"
                out.append((f"chk:{r.get('user_id')}:{now.date()}:{r['state']}", f"🧑‍🏫 {r['name']} {what} на смену"))
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import coachreport
        for o in coachreport.overdue(con):
            if o.get("level") == 2:
                out.append((f"rep:{o['branch_id']}:{o['date']}", f"📝 Не сдан отчёт о смене: {o['coach']}, {o['branch']}"))
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import expenses
        for b in expenses.bills_upcoming(con, now.date()):
            if b["level"] == "red":
                out.append((f"bill:{b['id']}:{b['next_due']}", f"💳 Срок платежа: {b['name']} {b['amount']} ₽ (до {b['next_due'][8:]}.{b['next_due'][5:7]})"))
    except Exception:  # noqa: BLE001
        pass
    return out


# ---------- отправка ----------

def owner_chats(con) -> list[str]:
    """Личные Telegram руководителей, подключённые к CRM («Красное в Telegram»)."""
    return [c for (uid,) in con.execute("SELECT id FROM users WHERE role='admin' AND active=1").fetchall()
            if (c := comms.get(con, f"owner_tg_{uid}"))]


def heartbeat(con) -> bool:
    """Сигнал «CRM жива» сторожу на мосту в Нидерландах (deploy/relay/watch.py) раз в минуту: если сигнал пропал,
    сторож сам напишет руководителю в Telegram, даже когда сервер CRM лежит целиком."""
    relay, tok = comms.get(con, "tg_relay"), comms.get(con, "tg_token")
    chats = owner_chats(con)
    if not relay or not tok or not chats:
        return False
    try:
        comms.http("POST", relay.rstrip("/") + "/watch/beat", data={"token": tok, "chats": chats}, timeout=8)
        return True
    except comms.CommsError:
        return False


def alert(con, text: str) -> int:
    """Срочное руководителю сразу, в любое время (например, подбор пароля)."""
    n = 0
    for chat in owner_chats(con):
        try:
            comms.tg_call(con, "sendMessage", {"chat_id": chat, "text": text})
            n += 1
        except comms.CommsError:
            pass
    return n


def tick(con, now: datetime | None = None) -> int:
    now = now or datetime.now()
    if not comms.get(con, "tg_token"):
        return 0
    care_remind(con, now)
    quiet = now.hour >= QUIET[0] or now.hour < QUIET[1]
    sent = 0
    items = None
    for (uid,) in con.execute("SELECT id FROM users WHERE role='admin' AND active=1").fetchall():
        chat = comms.get(con, f"owner_tg_{uid}")
        if not chat or quiet:
            continue
        items = items if items is not None else collect(con, now)
        new = [(k, t) for k, t in items if not con.execute("SELECT 1 FROM owner_sent WHERE user_id=? AND key=?", (uid, k)).fetchone()]
        if not new:
            continue
        morning = now.hour == QUIET[1] and now.minute < 30 and len(new) > 1
        head = "🌅 Утренняя сводка: требует вашего внимания" if morning else "🔴 Требует вашего внимания"
        url = comms.get(con, "public_url")
        text = head + "\n\n" + "\n".join("• " + t for _, t in new[:30]) + (f"\n\nОткрыть CRM: {url}" if url else "")
        try:
            comms.tg_call(con, "sendMessage", {"chat_id": chat, "text": text, "disable_web_page_preview": True})
        except comms.CommsError:
            continue
        for k, _ in new:
            con.execute("INSERT OR IGNORE INTO owner_sent(user_id, key, sent_at) VALUES (?,?,?)", (uid, k, _iso(now)))
        sent += 1
    return sent


def care_remind(con, now: datetime):
    """Пропущенный звонок или заявка не обработаны: каждые 10 минут напоминание в чат «Служба заботы» (до 30 минут)."""
    from .pu import notify_chat
    lines = []
    for k in con.execute("""SELECT c.id, c.started_at, c.phone, f.name AS family FROM calls c LEFT JOIN families f ON f.id=c.family_id
                            WHERE c.direction='in' AND c.status IN ('missed','busy') AND c.seen=0 AND c.started_at >= ?""",
                         (_iso(now - timedelta(minutes=OWNER_AFTER_MIN)),)):
        mins = int((now - datetime.fromisoformat(k["started_at"][:19])).total_seconds() // 60)
        slot = mins // CARE_EVERY_MIN
        if slot < 1:
            continue
        key = f"care:call:{k['id']}:{slot}"
        if con.execute("SELECT 1 FROM owner_sent WHERE user_id=0 AND key=?", (key,)).fetchone():
            continue
        con.execute("INSERT INTO owner_sent(user_id, key, sent_at) VALUES (0,?,?)", (key, _iso(now)))
        who = f"семья {k['family']}" if k["family"] else k["phone"]
        lines.append(f"📞 Пропущенный звонок {_hm(k['started_at'])} ({who}) не обработан уже {mins} мин. Перезвоните и нажмите «Обработан»")
    if lines:
        notify_chat(con, "care", "⏰ " + "\n".join(lines) + f"\nЧерез {OWNER_AFTER_MIN} минут CRM сообщит руководителю")
