"""Памятки «Правила абонемента» для родителей (Юлия, 01.11.2026: «давай сама это делать»).

CRM сама рисует картинку-памятку для каждого филиала и для сборной из текущих цен и правил.
Сервер собирает данные памятки и их отпечаток. Картинку рисует браузер любого сотрудника (canvas,
шрифт Nunito) и присылает на сервер. Поменялись цены или правила, отпечаток другой: следующий
сотрудник, открывший CRM, перерисует памятку сам. Пока свежей картинки нет, сообщения уходят без неё.
"""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import date, datetime

from . import logic

SCHEMA = """
CREATE TABLE IF NOT EXISTS memo_images (
    key TEXT PRIMARY KEY,
    hash TEXT NOT NULL,
    png BLOB NOT NULL,
    created_at TEXT NOT NULL
);
"""
# Поменяли тексты или рисунок в memos.js: увеличьте номер, и все памятки перерисуются
VERSION = 2
WD = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
WD_FULL = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def init(con):
    con.executescript(SCHEMA)


def _rub(n) -> str:
    return f"{int(n):,}".replace(",", " ") + " ₽"


def _days(weekdays: str) -> str:
    return "/".join(WD[int(x)] for x in (weekdays or "").split(",") if x != "").capitalize()


GEN = {"январь": "января", "февраль": "февраля", "март": "марта", "апрель": "апреля", "май": "мая", "июнь": "июня", "июль": "июля",
       "август": "августа", "сентябрь": "сентября", "октябрь": "октября", "ноябрь": "ноября", "декабрь": "декабря"}


def _from(ym: str) -> str:
    """«с 1 декабря» для месяца 'YYYY-MM'."""
    m = logic.month_label(ym).split()[0].lower()
    return f"с 1 {GEN.get(m, m)}"


def _makeups(con) -> list[str]:
    out = []
    for g in con.execute("""SELECT g.weekdays, g.time, g.age, b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id
                            WHERE g.active=1 AND g.is_makeup=1 ORDER BY b.name, g.weekdays, g.time"""):
        age = f" ({g['age']} лет)" if g["age"] else ""
        out.append(f"{g['branch']}: {_days(g['weekdays'])} {g['time']}{age}")
    return out


def _regular(con, b, today: date) -> dict:
    from . import pricing
    cur = logic.month_of(today)
    nxt = logic.shift_month(cur, 1)
    p, pn = pricing.prices_for(con, cur), pricing.prices_for(con, nxt)
    dl = int((con.execute("SELECT value FROM settings WHERE key='renewal_deadline_day'").fetchone() or ["25"])[0] or 25)
    late = pn["late_pct"] or p["late_pct"]
    d = {"kind": "regular", "title": b["name"], "deadline_day": dl,
         "fixed": p["fixed"], "flex": p["flex"], "fixed8": p["fixed"] * 8, "flex8": p["flex"] * 8,
         "credit": logic.MAX_FLEX_CREDIT, "late_pct": late, "makeups": _makeups(con)}
    if (pn["fixed"], pn["flex"]) != (p["fixed"], p["flex"]):
        d["next"] = {"from": _from(nxt), "fixed": pn["fixed"], "flex": pn["flex"]}
    return d


def _program(con, prog, today: date) -> dict:
    from . import program as pr
    cur = logic.month_of(today)
    nxt = logic.shift_month(cur, 1)
    p, pn = pr.prices_for(con, prog["id"], cur), pr.prices_for(con, prog["id"], nxt)
    gs = pr.groups(con, prog["id"])
    arena = [g for g in gs if g.get("lesson_kind") != "ofp"]
    ofp = [g for g in gs if g.get("lesson_kind") == "ofp"]
    title = arena[0]["branch"] if arena and len({g["branch"] for g in arena}) == 1 else prog["name"]

    def sched(lst):
        out = []
        for g in lst:
            for x in (g["weekdays"] or "").split(","):
                if x != "":
                    out.append(f"{WD_FULL[int(x)]} {logic.group_time(g, int(x)) or g['time']}")
        return ", ".join(out)
    d = {"kind": "program", "title": title, "program": prog["name"], "deadline_day": int(prog.get("deadline_day") or 25),
         "arena": p["arena"], "ofp": p["ofp"], "arena_only": p["arena_only"], "late_pct": pn["late_pct"] or p["late_pct"],
         "arena_sched": sched(arena), "ofp_sched": (ofp[0]["branch"] + ": " + sched(ofp)) if ofp else "",
         "makeups": _makeups(con)}
    if (pn["arena"], pn["ofp"], pn["arena_only"]) != (p["arena"], p["ofp"], p["arena_only"]):
        d["next"] = {"from": _from(nxt), "arena": pn["arena"], "ofp": pn["ofp"], "arena_only": pn["arena_only"]}
    return d


def targets(con, today: date | None = None) -> list[dict]:
    """Какие памятки нужны: по одной на филиал с обычными группами и по одной на каждую сборную."""
    today = today or date.today()
    out = []
    for b in con.execute("""SELECT DISTINCT b.id, b.name FROM branches b JOIN groups g ON g.branch_id=b.id
                            WHERE b.active=1 AND g.active=1 AND g.is_makeup=0 AND g.program_id IS NULL ORDER BY b.name"""):
        out.append({"key": f"b{b['id']}", "data": _regular(con, b, today)})
    try:
        for prog in con.execute("SELECT * FROM programs ORDER BY id").fetchall():
            try:
                out.append({"key": f"p{prog['id']}", "data": _program(con, dict(prog), today)})
            except Exception:  # noqa: BLE001 — у направления нет цен: памятки нет
                continue
    except Exception:  # noqa: BLE001 — нет таблицы направлений
        pass
    for t in out:
        t["hash"] = hashlib.sha256(json.dumps([VERSION, t["data"]], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    return out


def status(con) -> list[dict]:
    saved = {r["key"]: r for r in con.execute("SELECT key, hash, created_at FROM memo_images")}
    out = []
    for t in targets(con):
        s = saved.get(t["key"])
        out.append(t | {"fresh": bool(s and s["hash"] == t["hash"]), "drawn_at": s["created_at"] if s else None})
    return out


def save(con, key: str, h: str, png_b64: str) -> bool:
    t = next((x for x in targets(con) if x["key"] == key), None)
    if not t or t["hash"] != h:
        return False  # данные успели поменяться: эту картинку не берём
    png = base64.b64decode(png_b64.split(",")[-1])
    if not png.startswith(b"\x89PNG") or len(png) > 5_000_000:
        return False
    con.execute("""INSERT INTO memo_images(key, hash, png, created_at) VALUES (?,?,?,?)
                   ON CONFLICT(key) DO UPDATE SET hash=excluded.hash, png=excluded.png, created_at=excluded.created_at""",
                (key, h, png, datetime.now().isoformat(timespec="minutes")))
    return True


def image(con, key: str, fresh_only: bool = True) -> bytes | None:
    """Картинка памятки; если цены поменялись, а новая ещё не нарисована, None (не шлём устаревшую)."""
    r = con.execute("SELECT hash, png FROM memo_images WHERE key=?", (key,)).fetchone()
    if not r:
        return None
    if fresh_only:
        t = next((x for x in targets(con) if x["key"] == key), None)
        if not t or t["hash"] != r["hash"]:
            return None
    return r["png"]


def key_for_student(con, sid: int) -> str | None:
    """Какая памятка нужна ученику: сборная по направлению, иначе филиал его группы."""
    r = con.execute("""SELECT g.branch_id, g.program_id FROM enrollments e JOIN groups g ON g.id=e.group_id
                       WHERE e.student_id=? AND e.active=1 ORDER BY g.program_id IS NULL, e.id LIMIT 1""", (sid,)).fetchone()
    if not r:
        r = con.execute("SELECT g.branch_id, g.program_id FROM students s JOIN groups g ON g.id=s.group_id WHERE s.id=?", (sid,)).fetchone()
    if not r:
        return None
    return f"p{r['program_id']}" if r["program_id"] else f"b{r['branch_id']}"
