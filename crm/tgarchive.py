"""Старая переписка из Telegram школы (экспорт Telegram Desktop, JSON) в карточки клиентов (Юлия, 01.11.2026).

Браузер сам ужимает файл экспорта (оставляет только личные чаты, контакты и текст сообщений) и присылает сюда.
Чат попадает в CRM, только если его номер есть у клиента в CRM: чужие переписки не храним.
Повторная загрузка того же файла ничего не задваивает. Отправить в такую переписку нельзя: это архив.
"""
from __future__ import annotations

import re

from . import comms

CHANNEL = "tg_archive"


def _name(c: dict) -> str:
    return " ".join(x for x in ((c.get("first_name") or "").strip(), (c.get("last_name") or "").strip()) if x)


def _words(text: str) -> set[str]:
    return set(re.findall(r"[а-яa-z]+", (text or "").lower().replace("ё", "е")))


def _kid_index(con) -> list[tuple[set[str], int]]:
    fake = " AND COALESCE(f.fake, 0)=0" if "fake" in {r[1] for r in con.execute("PRAGMA table_info(families)")} else ""
    out = []
    for r in con.execute(f"SELECT s.name, s.family_id FROM students s JOIN families f ON f.id=s.family_id WHERE s.archived=0{fake}"):
        w = _words(r["name"])
        if len(w) >= 2:  # нужны и имя, и фамилия: по одному имени не угадываем
            out.append((w, r["family_id"]))
    return out


def _by_child_name(kids, chat_name: str) -> set[int]:
    cw = _words(chat_name)
    return {fam for w, fam in kids if w <= cw} if cw else set()


def import_(con, data: dict, user: dict) -> dict:
    by_name = {}
    for c in data.get("contacts") or []:
        p = comms.normalize_phone(str(c.get("phone") or ""))
        n = (c.get("name") or "").strip().lower()
        if n and len(p) >= 11:
            by_name.setdefault(n, p)
    st = {"chats": 0, "clients": 0, "messages": 0, "not_clients": 0, "no_phone": 0, "by_name": 0, "ambiguous": 0}
    kids = _kid_index(con)
    for ch in data.get("chats") or []:
        st["chats"] += 1
        name = (ch.get("name") or "").strip()
        phone = comms.normalize_phone(str(ch.get("phone") or "")) or by_name.get(name.lower(), "")
        fam = comms.family_by_phone(con, phone) if len(phone) >= 11 else None
        if not fam:
            # номера нет или он не клиентский: ищем по фамилии и имени ребёнка в названии чата («Урюпина Аглая, мама Марина»)
            fams = _by_child_name(kids, name)
            if len(fams) == 1:
                fam = fams.pop()
                st["by_name"] += 1
            elif len(fams) > 1:
                st["ambiguous"] += 1  # подходят разные семьи: не гадаем
                continue
        if not fam:
            st["not_clients" if len(phone) >= 11 else "no_phone"] += 1  # не клиент CRM: переписку не храним
            continue
        st["clients"] += 1
        cid = comms.upsert_conversation(con, CHANNEL, f"tga:{ch.get('id')}", title=name, phone=phone)
        con.execute("UPDATE conversations SET family_id=COALESCE(family_id, ?) WHERE id=?", (fam, cid))
        last = None
        for m in ch.get("msgs") or []:
            try:
                mid, ts, out, text, media = m[0], str(m[1])[:19], bool(m[2]), str(m[3] or ""), str(m[4] or "")
            except (IndexError, TypeError):
                continue
            if not text and not media:
                continue
            kind = "text" if text else "other"
            cur = con.execute("""INSERT OR IGNORE INTO messages(conversation_id, direction, text, kind, media, external_id, user_name, status, error, ts)
                                 VALUES (?,?,?,?,'',?,?,'','',?)""",
                              (cid, "out" if out else "in", text or f"({media}, не перенесено)", kind, f"tga:{ch.get('id')}:{mid}",
                               "Архив Telegram" if out else "", ts))
            st["messages"] += cur.rowcount
            if not last or ts > last[0]:
                last = (ts, ("Вы: " if out else "") + (text or media).replace("\n", " ")[:120])
        if last:
            con.execute("""UPDATE conversations SET last_at=CASE WHEN last_at IS NULL OR last_at < ? THEN ? ELSE last_at END,
                           last_text=CASE WHEN last_at IS NULL OR last_at < ? THEN ? ELSE last_text END, unread=0 WHERE id=?""",
                        (last[0], last[0], last[0], last[1], cid))
    return st
