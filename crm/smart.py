"""Подсказки по образцу больших CRM (Salesforce), выбраны Юлией 31.10.2026.

- оценка лида: горячий / тёплый / холодный и почему;
- «следующий шаг» в карточке: одно главное действие прямо сейчас;
- подсказки на этапах воронки: что сказать и что сделать, чтобы перейти дальше (тексты правит руководитель);
- сохранённые списки (фильтры) у каждого сотрудника;
- поиск дублей семей и склейка одной кнопкой.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from . import logic

# ---------------- подсказки на этапах воронки ----------------

STAGE_TIPS = {
    "lead": ("Позвоните в первые 15 минут: чем быстрее звонок, тем выше шанс записи. Представьтесь, спросите имя и возраст ребёнка, "
             "расскажите про бесплатное пробное и предложите два ближайших дня на выбор.",
             "Записать на пробное или, если не дозвонились, перевести в «Недозвон» и написать в мессенджер."),
    "no_answer": ("Напишите в мессенджер: «Здравствуйте! Вы оставляли заявку на пробное по футболу. Подобрать удобный день?» "
                  "и кнопкой «Предложить дни для пробного» отправьте ближайшие свободные даты.",
                  "Когда родитель ответил и выбрал день: «Записать на пробное»."),
    "trial_booked": ("Накануне подтвердите пробное: дата, время, адрес, что взять (форма, сменная обувь, вода).",
                     "CRM сама переведёт карточку, когда начнётся пробное."),
    "trial_reminder": ("Напомните о пробном и уточните, точно ли придут. Если планы поменялись, сразу перенесите на другой день.",
                       "CRM сама переведёт карточку, когда начнётся пробное."),
    "how_was": ("Спросите, как ребёнку тренировка, что понравилось. Предложите постоянную группу и скажите про скидку 20% "
                "на первый абонемент в день пробного.",
                "«Продать абонемент» или отказ с причиной. «Думают» = задача перезвонить."),
    "missed_trial": ("Уточните, что помешало прийти, и предложите другой день. Без давления: «Давайте подберём день, когда удобно».",
                     "Записать на новое пробное."),
}
TIP_STAGES = list(STAGE_TIPS)


def tips(con) -> dict:
    out = {}
    for k, (say, nxt) in STAGE_TIPS.items():
        r = con.execute("SELECT value FROM settings WHERE key=?", (f"tip_{k}",)).fetchone()
        if r and r[0]:
            try:
                v = json.loads(r[0])
                say, nxt = v.get("say", say), v.get("next", nxt)
            except ValueError:
                pass
        out[k] = {"say": say, "next": nxt}
    return out


def save_tips(con, data: dict):
    for k in STAGE_TIPS:
        if k in data and isinstance(data[k], dict):
            v = {"say": str(data[k].get("say", "")).strip()[:1500], "next": str(data[k].get("next", "")).strip()[:500]}
            con.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (f"tip_{k}", json.dumps(v, ensure_ascii=False)))


# ---------------- оценка лида ----------------

def _age(birth: str, ref: date) -> float | None:
    try:
        b = date.fromisoformat(birth[:10])
    except ValueError:
        return None
    return (ref - b).days / 365.25


def lead_score(con, s: dict, ref: datetime | None = None) -> dict | None:
    """Горячий / тёплый / холодный лид и причины. Для учеников None."""
    if s["funnel"] != "sales" or s["stage"] in ("bought", "refused"):
        return None
    ref = ref or datetime.now()
    pts, why = 0, []
    src = (s.get("source") or "").lower()
    if "сайт" in src or s.get("priority"):
        pts += 2; why.append("сам оставил заявку")
    elif src:
        why.append(f"источник: {s['source']}")
    try:
        days = (ref - datetime.fromisoformat(s["created_at"])).days
    except (TypeError, ValueError):
        days = 0
    if days <= 3:
        pts += 2; why.append("заявка свежая")
    elif days <= 14:
        pts += 1
    elif days > 30:
        pts -= 2; why.append("заявке больше месяца")
    replied = con.execute("""SELECT 1 FROM messages m JOIN conversations c ON c.id=m.conversation_id
                             WHERE c.family_id=? AND m.direction='in' LIMIT 1""", (s["family_id"],)).fetchone()
    if replied:
        pts += 2; why.append("отвечал в мессенджере")
    if s["stage"] in ("trial_booked", "trial_reminder"):
        pts += 3; why.append("записан на пробное")
    if s.get("trial_came") == 1:
        pts += 3; why.append("был на пробном")
    if s["stage"] == "no_answer":
        pts -= 1; why.append("не дозвонились")
    if s["stage"] == "missed_trial" or s.get("trial_came") == 0:
        pts -= 1; why.append("не пришёл на пробное")
    a = _age(s.get("birth") or "", ref.date())
    if a is not None and 3 <= a < 13:
        pts += 1
    elif a is not None:
        pts -= 1; why.append("возраст вне групп")
    level = "hot" if pts >= 5 else "warm" if pts >= 2 else "cold"
    return {"level": level, "label": {"hot": "🔥 Горячий", "warm": "🌤 Тёплый", "cold": "❄ Холодный"}[level],
            "why": why, "points": pts}


# ---------------- следующий шаг ----------------

def next_step(con, s: dict, detail: dict | None = None, ref: datetime | None = None) -> dict | None:
    """Одно главное действие по клиенту прямо сейчас: {text, action}. action: reply | call | trial | offer | sell | link | makeup | task."""
    ref = ref or datetime.now()
    fid = s["family_id"]
    unread = con.execute("""SELECT m.text FROM conversations c JOIN messages m ON m.conversation_id=c.id
                            WHERE c.family_id=? AND c.unread > 0 AND m.direction='in' ORDER BY m.id DESC LIMIT 1""", (fid,)).fetchone()
    if unread:
        t = (unread[0] or "").strip().replace("\n", " ")
        return {"text": f"Ответить родителю: «{t[:70]}{'…' if len(t) > 70 else ''}»" if t else "Ответить родителю в переписке", "action": "reply"}
    missed = con.execute("""SELECT started_at FROM calls WHERE family_id=? AND status='missed' AND seen=0
                            ORDER BY id DESC LIMIT 1""", (fid,)).fetchone()
    if missed:
        return {"text": f"Перезвонить: пропущенный звонок в {missed[0][11:16]}", "action": "call"}
    task = con.execute("""SELECT title, kind, due_at FROM tasks WHERE student_id=? AND status='open' AND due_at <= ?
                          ORDER BY due_at LIMIT 1""", (s["id"], ref.isoformat(timespec="minutes"))).fetchone()
    if task:
        from .tasks import KINDS
        title = task["title"] or KINDS.get(task["kind"], ("Задача",))[0]
        return {"text": f"Задача на сейчас: {title}", "action": "task"}
    if s["funnel"] == "sales":
        st = s["stage"]
        if st == "lead":
            return {"text": "Позвонить и записать на пробное", "action": "call"}
        if st == "no_answer":
            return {"text": "Написать в мессенджер и предложить дни для пробного", "action": "offer"}
        if st in ("trial_booked", "trial_reminder") and s.get("trial_at"):
            t = datetime.fromisoformat(s["trial_at"])
            if t.date() == ref.date() + timedelta(days=1) or (t.date() == ref.date() and t > ref):
                return {"text": f"Подтвердить пробное {t.strftime('%d.%m в %H:%M')}", "action": "call"}
            return None
        if st in ("how_was", "trial_done") or s.get("trial_came") == 1:
            return {"text": "Узнать, как прошло пробное, и предложить абонемент", "action": "sell"}
        if st == "missed_trial":
            return {"text": "Перезаписать на пробное", "action": "trial"}
        return None
    if s["stage"] == "left":
        return None
    d = detail or {}
    bal = d.get("balance") or {}
    cur = logic.month_of(ref.date())
    nxt = logic.shift_month(cur, 1)
    if bal.get("debt"):
        return {"text": f"Отправить ссылку на оплату: долг {bal['debt']:,} ₽".replace(",", " "), "action": "link"}
    if ref.day >= 20:
        has_next = con.execute("SELECT 1 FROM subscriptions WHERE student_id=? AND month=? AND status != 'cancelled'",
                               (s["id"], nxt)).fetchone()
        if not has_next:
            return {"text": f"Продлить на {logic.month_label(nxt)}: выставить счёт и отправить ссылку", "action": "sell"}
        if bal.get("upcoming"):
            return {"text": f"Напомнить об оплате {logic.month_label(nxt)}", "action": "link"}
    c = d.get("counters") or {}
    if c.get("available") and any(e.get("tariff") == "flex" for e in d.get("enrollments", [])):
        return {"text": f"Предложить отработку: можно отработать {c['available']}", "action": "makeup"}
    return None


# ---------------- сохранённые списки ----------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS saved_views (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    section TEXT NOT NULL,
    name TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS not_duplicates (
    a INTEGER NOT NULL,
    b INTEGER NOT NULL,
    PRIMARY KEY (a, b)
);
"""


def init(con):
    con.executescript(SCHEMA)
    if "email" not in [c[1] for c in con.execute("PRAGMA table_info(parents)")]:
        con.execute("ALTER TABLE parents ADD COLUMN email TEXT NOT NULL DEFAULT ''")


def views(con, uid: int, section: str) -> list[dict]:
    return [{"id": r["id"], "name": r["name"], "data": json.loads(r["data"] or "{}")} for r in con.execute(
        "SELECT id, name, data FROM saved_views WHERE user_id=? AND section=? ORDER BY id", (uid, section))]


def save_view(con, uid: int, section: str, name: str, data: dict) -> int:
    name = " ".join((name or "").split())[:60]
    if not name:
        raise ValueError("Назовите список")
    return con.execute("INSERT INTO saved_views(user_id, section, name, data, created_at) VALUES (?,?,?,?,?)",
                       (uid, section, name, json.dumps(data or {}, ensure_ascii=False), datetime.now().isoformat(timespec="seconds"))).lastrowid


def delete_view(con, uid: int, vid: int):
    con.execute("DELETE FROM saved_views WHERE id=? AND user_id=?", (vid, uid))


# ---------------- дубли семей ----------------

def _norm_name(n: str) -> str:
    return " ".join(sorted((n or "").lower().replace("ё", "е").split()))


def _family_card(con, fid: int) -> dict:
    f = con.execute("SELECT id, name FROM families WHERE id=?", (fid,)).fetchone()
    kids = [dict(r) for r in con.execute("SELECT id, name, birth, funnel, stage, archived FROM students WHERE family_id=? ORDER BY id", (fid,))]
    parents = [dict(r) for r in con.execute("SELECT name, phone FROM parents WHERE family_id=? ORDER BY id", (fid,))]
    pays = con.execute("""SELECT COUNT(*) FROM payments p JOIN students s ON s.id=p.student_id WHERE s.family_id=? AND p.cancelled=0""",
                       (fid,)).fetchone()[0]
    return {"id": fid, "name": f["name"] if f else "", "kids": kids, "parents": parents, "payments": pays}


def duplicates(con, limit: int = 50) -> list[dict]:
    """Пары семей, похожих на одну и ту же: общий телефон или ребёнок с тем же именем и датой рождения."""
    pairs: dict[tuple[int, int], str] = {}
    for a, b, ph in con.execute("""SELECT p1.family_id, p2.family_id, p1.phone FROM parents p1 JOIN parents p2
                                   ON p1.phone_norm = p2.phone_norm AND p1.family_id < p2.family_id
                                   WHERE length(p1.phone_norm) >= 10"""):
        pairs.setdefault((a, b), f"один телефон {ph}")
    kids = {}
    for r in con.execute("SELECT family_id, name, birth FROM students WHERE birth != ''"):
        kids.setdefault((_norm_name(r["name"]), r["birth"][:10]), set()).add(r["family_id"])
    for (nm, birth), fams in kids.items():
        fl = sorted(fams)
        for i in range(len(fl)):
            for j in range(i + 1, len(fl)):
                pairs.setdefault((fl[i], fl[j]), f"ребёнок с тем же именем и датой рождения ({birth[8:10]}.{birth[5:7]}.{birth[:4]})")
    skip = {(r[0], r[1]) for r in con.execute("SELECT a, b FROM not_duplicates")}
    out = []
    for (a, b), why in sorted(pairs.items()):
        if (a, b) in skip:
            continue
        out.append({"a": _family_card(con, a), "b": _family_card(con, b), "why": why})
        if len(out) >= limit:
            break
    return out


def not_duplicate(con, a: int, b: int):
    a, b = sorted((int(a), int(b)))
    con.execute("INSERT OR IGNORE INTO not_duplicates(a, b) VALUES (?, ?)", (a, b))


def merge_families(con, keep: int, drop: int, user: dict) -> dict:
    """Всё из семьи drop переезжает в keep: дети, родители (без повторов телефона), переписки, звонки, задачи. drop удаляется."""
    keep, drop = int(keep), int(drop)
    if keep == drop:
        raise ValueError("Это одна и та же семья")
    for fid in (keep, drop):
        if not con.execute("SELECT 1 FROM families WHERE id=?", (fid,)).fetchone():
            raise ValueError("Семья не найдена")
    have = {r[0] for r in con.execute("SELECT phone_norm FROM parents WHERE family_id=? AND phone_norm != ''", (keep,))}
    moved_parents = 0
    for p in con.execute("SELECT id, phone_norm FROM parents WHERE family_id=?", (drop,)).fetchall():
        if p["phone_norm"] and p["phone_norm"] in have:
            con.execute("DELETE FROM parents WHERE id=?", (p["id"],))
        else:
            con.execute("UPDATE parents SET family_id=? WHERE id=?", (keep, p["id"]))
            moved_parents += 1
    # все остальные таблицы, где есть family_id (дети, переписки, звонки, задачи и т.д.)
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        if t in ("families", "parents"):
            continue
        cols = [c[1] for c in con.execute(f'PRAGMA table_info("{t}")')]
        if "family_id" in cols:
            con.execute(f'UPDATE "{t}" SET family_id=? WHERE family_id=?', (keep, drop))
    note = con.execute("SELECT note FROM families WHERE id=?", (drop,)).fetchone()[0]
    if note:
        con.execute("UPDATE families SET note = TRIM(note || ' ' || ?) WHERE id=?", (note, keep))
    con.execute("DELETE FROM families WHERE id=?", (drop,))
    con.execute("DELETE FROM not_duplicates WHERE a=? OR b=?", (drop, drop))
    from .services import log
    for (sid,) in con.execute("SELECT id FROM students WHERE family_id=?", (keep,)).fetchall():
        log(con, sid, "Семья склеена с дублем", user, "system")
    return {"ok": True, "parents_moved": moved_parents}


# ---------------- обязательные данные (Юлия, 31.10) ----------------
# Перед записью на пробное: имя и фамилия ребёнка, дата рождения, имя и телефон родителя.
# Перед продажей абонемента и ссылкой на оплату: ещё почта родителя (туда PayKeeper шлёт чек). Без исключений.

import re as _re

EMAIL_RE = _re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
FIELD_LABELS = {"child_name": "Имя и фамилия ребёнка", "birth": "Дата рождения ребёнка", "parent_name": "Как зовут родителя",
                "phone": "Телефон родителя", "email": "Почта родителя (для чека)"}
_PLACEHOLDER = ("заявка", "лид", "без имени", "ребёнок", "ребенок", "клиент")


def _first_parent(con, family_id: int):
    return con.execute("SELECT id, name, phone, phone_norm, email FROM parents WHERE family_id=? ORDER BY id LIMIT 1", (family_id,)).fetchone()


def required_info(con, sid: int, kind: str) -> dict:
    s = con.execute("SELECT id, name, birth, family_id FROM students WHERE id=?", (sid,)).fetchone()
    if not s:
        from .services import ApiError
        raise ApiError("Ученик не найден", 404)
    p = _first_parent(con, s["family_id"])
    name = " ".join((s["name"] or "").split())
    vals = {"child_name": name, "birth": (s["birth"] or "")[:10], "parent_name": (p["name"] if p else "") or "",
            "phone": (p["phone"] if p else "") or "", "email": (p["email"] if p else "") or ""}
    miss = []
    if len(name.split()) < 2 or any(w in name.lower() for w in _PLACEHOLDER):
        miss.append("child_name")
    if not vals["birth"]:
        miss.append("birth")
    pn = vals["parent_name"].strip().lower()
    if not pn or any(pn.startswith(w) for w in _PLACEHOLDER) or pn in ("родитель", "мама", "папа"):
        miss.append("parent_name")
    if not p or len(p["phone_norm"] or "") < 11:
        miss.append("phone")
    if kind == "sale" and not EMAIL_RE.match(vals["email"].strip()):
        miss.append("email")
    return {"kind": kind, "student_id": sid, "missing": miss, "values": vals,
            "fields": ["child_name", "birth", "parent_name", "phone"] + (["email"] if kind == "sale" else []),
            "labels": FIELD_LABELS}


REQUIRE = True  # старые автотесты выключают проверку (tests/test_crm.py), её проверяет tests/test_required.py


def require(con, sid: int, kind: str):
    if not REQUIRE:
        return
    info = required_info(con, sid, kind)
    if info["missing"]:
        from .services import ApiError
        what = ", ".join(FIELD_LABELS[k].lower() for k in info["missing"])
        msg = ("Чтобы записать на пробное" if kind == "trial" else "Чтобы продать абонемент") + f", заполните: {what}"
        raise ApiError(msg, 422, {"need": info})


def fill_required(con, sid: int, data: dict, user: dict):
    from .services import ApiError, log
    from .db import normalize_phone
    s = con.execute("SELECT id, name, birth, family_id FROM students WHERE id=?", (sid,)).fetchone()
    if not s:
        raise ApiError("Ученик не найден", 404)
    name = " ".join(str(data.get("child_name", s["name"]) or "").split())
    if "child_name" in data and len(name.split()) < 2:
        raise ApiError("Впишите имя и фамилию ребёнка")
    birth = str(data.get("birth", s["birth"]) or "")[:10]
    if "birth" in data:
        try:
            b = datetime.fromisoformat(birth).date()
        except ValueError:
            raise ApiError("Неверная дата рождения")
        if not (date(2000, 1, 1) < b < date.today()):
            raise ApiError("Проверьте дату рождения")
    email = str(data.get("email", "") or "").strip()
    if "email" in data and not EMAIL_RE.match(email):
        raise ApiError("Проверьте почту: например, ivanova@mail.ru")
    phone = str(data.get("phone", "") or "").strip()
    if "phone" in data and len(normalize_phone(phone)) < 11:
        raise ApiError("Проверьте телефон: 11 цифр, например +7 900 123-45-67")
    pname = " ".join(str(data.get("parent_name", "") or "").split())
    if "parent_name" in data and len(pname) < 2:
        raise ApiError("Впишите, как зовут родителя")
    con.execute("UPDATE students SET name=?, birth=? WHERE id=?", (name, birth, sid))
    p = _first_parent(con, s["family_id"])
    if not p:
        con.execute("INSERT INTO parents(family_id, name, phone, phone_norm, email) VALUES (?,?,?,?,?)",
                    (s["family_id"], pname or "Родитель", phone, normalize_phone(phone) if phone else "", email))
    else:
        sets, args = [], []
        if pname:
            sets.append("name=?"); args.append(pname)
        if phone:
            sets += ["phone=?", "phone_norm=?"]; args += [phone, normalize_phone(phone)]
        if email:
            sets.append("email=?"); args.append(email)
        if sets:
            con.execute(f"UPDATE parents SET {', '.join(sets)} WHERE id=?", (*args, p["id"]))
    log(con, sid, "Заполнены обязательные данные: " + ", ".join(FIELD_LABELS[k].lower() for k in data if k in FIELD_LABELS), user, "system")
