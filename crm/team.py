"""Раздел «Команда»: оргструктура, зоны ответственности ролей, модели оплаты.

Тексты зон и оплаты хранятся в настройке team_config (JSON): руководитель правит их прямо на странице.
Состав отделов всегда по должности сотрудника (Настройки → Сотрудники, см. perms.py): руководитель - admin,
старший администратор - operator с отметкой is_senior, администраторы - остальные operator, тренеры - coach,
юрист - lawyer, промоутеры - с отметкой is_promoter.
"""
from __future__ import annotations

import json

DEFAULT_ZONES = [
    {"key": "owner", "title": "Руководитель", "color": "#1d1d22", "items": [
        "стратегия и развитие школы", "финансы, цены и правила абонементов", "найм и обучение команды",
        "партнёры и управляющая компания Футболики", "вопрос, не решённый старшим администратором"]},
    {"key": "control", "title": "Старший администратор", "color": "#7a5bc7", "items": [
        "задачи в Планере и отметки в журнале", "приход тренеров и штрафы за опоздание", "замены, отпуск, больничный тренеров",
        "мерч, касса и чеки", "жалоба или конфликт, не решённые на месте"]},
    {"key": "admins", "title": "Администраторы", "color": "#d99a0b", "items": [
        "новая заявка и пробное занятие", "оплата, продление, отработка", "переписка и звонки родителям",
        "лист ожидания и перевод в другую группу", "«Не дошёл до ПУ» и «Пригласим позже»"]},
    {"key": "coaches", "title": "Тренеры", "color": "#23a26d", "items": [
        "тренировка по методике Футболики", "отметки в журнале сразу после занятия", "кто вёл тренировку и отчёт смены",
        "напомнить родителям об оплате до 25 числа", "мерч на смене"]},
    {"key": "promoters", "title": "Промоутеры", "color": "#2c6fd1", "items": [
        "сбор контактов в районе", "контакт в таблицу без дублей, каждый ребёнок отдельно", "отметить район и удобный мессенджер"]},
    {"key": "lawyers", "title": "Юрист", "color": "#2a9d9b", "items": [
        "оферты, договоры и юридические документы", "документы сотрудников", "претензии и споры с клиентами"]},
]
DEFAULT_PAY = {
    "admins": "Смена 1000 ₽\nЗапись на пробное 200 ₽\nПокупка абонемента после пробного 200 ₽",
    "promoters": "300 ₽ за контакт (за каждого ребёнка или номер телефона, по договорённости)",
}


def _get(con, key, default=""):
    r = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def config(con) -> dict:
    try:
        c = json.loads(_get(con, "team_config") or "{}")
    except ValueError:
        c = {}
    users = [dict(r) for r in con.execute("SELECT id, name, role, is_senior, is_promoter FROM users WHERE active=1 ORDER BY name")]
    owner = next((u["id"] for u in users if u["role"] == "admin" and u["name"] != "Администратор"), None) or \
        next((u["id"] for u in users if u["role"] == "admin"), None)
    real = [u for u in users if "тест" not in u["name"].lower()]
    auto = {
        "owner": [owner] if owner else [],
        "control": [u["id"] for u in real if u["role"] == "operator" and u["is_senior"]],
        "admins": [u["id"] for u in real if u["role"] == "operator" and not u["is_senior"]],
        "coaches": [u["id"] for u in real if u["role"] == "coach"],
        "lawyers": [u["id"] for u in real if u["role"] == "lawyer"],
        "promoters": [u["id"] for u in real if u["is_promoter"]],
    }
    zones = c.get("zones") or DEFAULT_ZONES
    have = {z["key"] for z in zones}
    old = lambda x: x.replace("Контролёр", "Старший администратор").replace("контролёром", "старшим администратором")  # прежнее название
    zones = [z | {"title": old(z["title"]), "items": [old(x) for x in z["items"]]} for z in zones]
    zones += [z for z in DEFAULT_ZONES if z["key"] not in have]
    return {"zones": zones, "members": auto, "pay": {**DEFAULT_PAY, **(c.get("pay") or {})}}


def save(con, data: dict):
    cur = config(con)
    out = {"zones": cur["zones"], "pay": cur["pay"]}
    if isinstance(data.get("zones"), list):
        out["zones"] = [{"key": str(z.get("key") or i), "title": str(z.get("title") or "")[:60], "color": str(z.get("color") or "#23a26d")[:9],
                         "items": [str(x).strip()[:200] for x in (z.get("items") or []) if str(x).strip()][:12]}
                        for i, z in enumerate(data["zones"][:12])]
    if isinstance(data.get("pay"), dict):
        out["pay"] = {k: str(v)[:1000] for k, v in data["pay"].items()}
    con.execute("INSERT INTO settings(key, value) VALUES ('team_config', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(out, ensure_ascii=False),))


def view(con) -> dict:
    from . import staff
    c = config(con)
    users = {r["id"]: dict(r) for r in con.execute("SELECT id, name, role, bio FROM users WHERE active=1")}
    for u in users.values():
        try:
            b = json.loads(u.pop("bio") or "{}")
        except ValueError:
            b = {}
        u["nickname"] = b.get("nickname", "")
        u["groups"] = con.execute("SELECT COUNT(*) FROM groups WHERE active=1 AND (coach_id=? OR assistant_id=?)", (u["id"], u["id"])).fetchone()[0]
        u["branches"] = [r[0] for r in con.execute(
            "SELECT DISTINCT branch_id FROM groups WHERE active=1 AND (coach_id=? OR assistant_id=?) ORDER BY branch_id", (u["id"], u["id"]))]
    pct = {k: staff.pay_share(con, *v) for k, v in {"main": (True, False), "assistant": (False, False), "solo": (True, True)}.items()}
    prog = con.execute("SELECT name, coach_pct FROM programs ORDER BY id LIMIT 1").fetchone() if con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='programs'").fetchone() else None
    return c | {"users": users, "branches": [dict(r) for r in con.execute("SELECT id, name FROM branches WHERE active=1 ORDER BY id")],
                "coach_pay": {"pct": pct, "makeup": int(staff.setting(con, "pay_makeup_fixed") or 0),
                              "late_fine": int(staff.setting(con, "late_fine") or 0),
                              "program": {"name": prog[0], "pct": prog[1]} if prog else None}}
