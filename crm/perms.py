"""Права по разделам CRM.

Должность хранится в users.role: admin (Руководитель), operator (Администратор), coach (Тренер), lawyer (Юрист).
Старший администратор: operator с отметкой is_senior, поэтому он везде, где нужны администраторы (смены, задачи).
Промоутер: отметка is_promoter, ставится вместе с любой должностью, права складываются.

Каждый раздел: должность → уровень. full - всё, view - только смотреть, own - только своё.
По этой же таблице строится меню и проверяется каждый запрос к серверу.
"""
from __future__ import annotations

LEVEL = {"own": 1, "view": 2, "full": 3}

TITLES = {"owner": "Руководитель", "senior": "Старший администратор", "operator": "Администратор",
          "coach": "Тренер", "promoter": "Промоутер", "lawyer": "Юрист"}

DESK = {"owner": "full", "senior": "full", "operator": "full"}
SECTIONS: dict[str, dict[str, str]] = {
    "sales": DESK, "inbox": DESK, "skips": DESK, "merch": DESK, "members": DESK, "search": DESK,
    "today": {"owner": "full", "coach": "own"},
    "planner": {"owner": "full", "senior": "full", "operator": "own", "coach": "own", "promoter": "own", "lawyer": "own"},
    "journal": {"owner": "full", "senior": "full", "operator": "view", "coach": "own"},
    "schedule": {"owner": "full", "senior": "full", "operator": "view", "coach": "view", "promoter": "view"},
    "coaches": {"owner": "full", "senior": "view", "coach": "own"},
    "team": {"owner": "full", "senior": "view", "operator": "view", "coach": "view", "promoter": "view", "lawyer": "view"},
    "pay": {"owner": "full", "senior": "own", "operator": "own", "coach": "own", "promoter": "own", "lawyer": "own"},
    "payments": {"owner": "full", "senior": "view"},
    "fiscal": {"owner": "full", "senior": "full"},
    "spend": {"owner": "full"},
    "dashboard": {"owner": "full"},
    "docs": {"owner": "full", "lawyer": "full"},
    "staffdocs": {"owner": "full", "lawyer": "view", "coach": "own", "promoter": "own"},
    "settings": {"owner": "full", "senior": "own", "operator": "own", "coach": "own", "promoter": "own", "lawyer": "own"},
    "export": {"owner": "full"},  # выгрузка в Excel, копии базы: только руководитель
}

# старые метки у маршрутов: кого пускать
LEGACY = {
    "admin": {"owner"},
    "operator": {"owner", "senior", "operator"},
    "any": {"owner", "senior", "operator", "coach", "promoter"},  # все, кроме юриста: у него свои разделы
    "coach": {"owner", "senior", "operator", "coach", "promoter"},
}


def roles_of(user: dict) -> set[str]:
    base = user.get("role")
    out = set()
    if base == "admin":
        out.add("owner")
    elif base == "operator":
        out.add("senior" if user.get("is_senior") else "operator")
    elif base in ("coach", "lawyer"):
        out.add(base)
    if user.get("is_promoter"):
        out.add("promoter")
    return out


def level(user: dict, section: str) -> int:
    rules = SECTIONS.get(section, {})
    return max((LEVEL[rules[r]] for r in roles_of(user) if r in rules), default=0)


def can(user: dict, section: str, need: str = "own") -> bool:
    return level(user, section) >= LEVEL[need]


def allowed(user: dict, spec: str) -> bool:
    """spec у маршрута: all (любой вошедший), старая метка admin/operator/any/coach или «раздел[:уровень]»."""
    if spec == "all":
        return True
    if spec in LEGACY:
        return bool(roles_of(user) & LEGACY[spec])
    section, _, need = spec.partition(":")
    return can(user, section, need or "own")


def view_of(user: dict) -> dict:
    """Для меню: уровень по каждому разделу, где он есть."""
    lv = {v: k for k, v in LEVEL.items()}
    return {s: lv[level(user, s)] for s in SECTIONS if level(user, s)}


def title(user: dict) -> str:
    rs = roles_of(user)
    main = next((TITLES[r] for r in ("owner", "senior", "operator", "coach", "lawyer") if r in rs), "")
    if "promoter" in rs:
        return f"{main}, промоутер" if main else TITLES["promoter"]
    return main


def init(con) -> None:
    """Отметки старшего администратора и промоутера. При первом запуске переносим тех, кто уже работает."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(users)")}
    if "is_senior" in cols:
        return
    con.execute("ALTER TABLE users ADD COLUMN is_senior INTEGER NOT NULL DEFAULT 0")
    con.execute("ALTER TABLE users ADD COLUMN is_promoter INTEGER NOT NULL DEFAULT 0")
    for key in ("checkin_reviewer", "planner_controller"):  # раньше старший администратор был просто настройкой
        r = con.execute("SELECT value FROM integrations WHERE key=?", (key,)).fetchone()
        if r and str(r[0]).isdigit():
            con.execute("UPDATE users SET is_senior=1 WHERE id=? AND role='operator'", (int(r[0]),))
    con.execute("UPDATE users SET is_promoter=1 WHERE role='coach' AND (name LIKE '%Евсюнин%' OR name LIKE '%Ивакин%')")
    con.execute("UPDATE users SET role='lawyer' WHERE name LIKE '%Бабанов%' AND role!='admin'")
