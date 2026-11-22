"""Isolated import of historical ERP subscriptions and marks without accounting effects."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from . import logic, schedule
from .db import normalize_phone

SCHEMA = """
CREATE TABLE IF NOT EXISTS erp_history_subscriptions (
 id INTEGER PRIMARY KEY, source_id TEXT NOT NULL UNIQUE, student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
 group_id INTEGER NOT NULL REFERENCES groups(id), month TEXT NOT NULL, valid_from TEXT NOT NULL, valid_until TEXT NOT NULL,
 lessons INTEGER NOT NULL, revenue_amount INTEGER, source_payload TEXT NOT NULL, imported_at TEXT NOT NULL, imported_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS erp_history_visits (
 id INTEGER PRIMARY KEY, source_id TEXT NOT NULL UNIQUE, source_subscription_id TEXT, student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
 group_id INTEGER NOT NULL REFERENCES groups(id), visit_date TEXT NOT NULL, visit_time TEXT NOT NULL, kind TEXT NOT NULL,
 status TEXT NOT NULL, absence_source_id TEXT, source_payload TEXT NOT NULL, imported_at TEXT NOT NULL, imported_by TEXT NOT NULL,
 UNIQUE(student_id,group_id,visit_date,visit_time,kind));
CREATE TABLE IF NOT EXISTS erp_history_unassigned_visits (
 id INTEGER PRIMARY KEY, source_id TEXT NOT NULL UNIQUE, source_subscription_id TEXT,
 student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
 visit_date TEXT NOT NULL, visit_time TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
 source_group TEXT NOT NULL, source_payload TEXT NOT NULL, imported_at TEXT NOT NULL, imported_by TEXT NOT NULL,
 UNIQUE(student_id,visit_date,visit_time,kind,source_id));
CREATE TABLE IF NOT EXISTS erp_history_planned_lessons (
 id INTEGER PRIMARY KEY, source_subscription_id TEXT NOT NULL,
 student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
 group_id INTEGER NOT NULL REFERENCES groups(id), lesson_date TEXT NOT NULL, lesson_time TEXT NOT NULL,
 source_payload TEXT NOT NULL, imported_at TEXT NOT NULL, imported_by TEXT NOT NULL,
 UNIQUE(source_subscription_id,lesson_date,lesson_time));
"""
PILOT_KEYS = {
    "5331a74804c2a561b997d253d29bbccb642f426cf4f8b8ae5c2a03b75fa3e5e1",
    "b05a6695f5f2fd90d48242b5ecc350fee14171df5e11e847d1264e44ff94eb99",
    "e096e0e682db8cb70845d2d6fe84476b0ca8ba0cefe4e8beff3340bf6a3df7be",
    "f866bad935d3d87f73f94421f1d2412a3d1417bedf450cf4c52210385ff82c25",
    "6d63ff26d801c60d5698cb7a7f032b4091d914836f038467027e2ffd169d09bd",
}
PILOT_CONTRACTS = {
    ("84894", "2026-11"): {
        "827955": ("2026-11-01", "2026-11-29", 9, 13500),
        "827957": ("2026-11-07", "2026-11-28", 4, 4800),
    },
}
SHARIPOV_NOVEMBER_MESSAGES = {"26745", "26789", "26810", "26908", "27065"}
SHULEZHKO_NOVEMBER_VISITS = {
    ("26745", "827957", "2026-11-07", "17:00", "subscription"),
    ("26789", "827955", "2026-11-08", "19:30", "subscription"),
    ("26810", "827955", "2026-11-10", "18:30", "subscription"),
    ("26908", "827957", "2026-11-12", "20:30", "makeup"),
    ("27065", "827955", "2026-11-15", "19:30", "subscription"),
}
SKIP = {"группа", "сборная", "пуп", "журнал", "тц"}


class HistoryError(ValueError): pass


def init(con):
    con.executescript(SCHEMA)
    columns = {row[1] for row in con.execute("PRAGMA table_info(erp_history_subscriptions)")}
    if "revenue_amount" not in columns:
        con.execute("ALTER TABLE erp_history_subscriptions ADD COLUMN revenue_amount INTEGER")


def _json(value): return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
def _token(value): return hashlib.sha256(_json(value).encode()).hexdigest()
def _pilot_key(erp_id, period): return hashlib.sha256(f"{erp_id}|{period}".encode()).hexdigest()
def _norm(value): return " ".join(re.sub(r"[^a-zа-я0-9]+", " ", str(value or "").lower().replace("ё", "е")).split())
def _same_name(left, right): return sorted(_norm(left).split()) == sorted(_norm(right).split()) and bool(_norm(left))


def _amount(value):
    if value in (None, ""): return None
    try: amount = Decimal(str(value).replace(" ", "").replace(",", "."))
    except InvalidOperation: return None
    return int(amount) if amount == amount.to_integral_value() and amount >= 0 else None


def _records(payload):
    rows = payload if isinstance(payload, list) else [payload]
    if not rows or any(not isinstance(row, dict) for row in rows): raise HistoryError("Нужен JSON одного ребёнка или список детей")
    return rows


def _subscriptions(row):
    """Accept the original singular JSON and the new plural form."""
    plural = row.get("subscriptions")
    if isinstance(plural, list):
        return [item for item in plural if isinstance(item, dict)]
    single = row.get("subscription")
    return [single] if isinstance(single, dict) else []


def _planned_lessons(row, sub):
    own = sub.get("schedule") or sub.get("planned_lessons") or []
    shared = [x for x in (row.get("planned_lessons") or [])
              if str(x.get("erp_subscription_id") or "") == str(sub.get("erp_id") or "")]
    return [x for x in [*own, *shared] if isinstance(x, dict)]


def _telegram_source(item):
    """Return the real Telegram message identity separately from a visit identity."""
    message_id = str(item.get("source_message_id") or "").strip()
    chat_id = str(item.get("source_chat_id") or "").strip()
    message_url = str(item.get("source_message_url") or "").strip()
    if message_id:
        stable = f"{chat_id}:{message_id}" if chat_id else message_id
        return stable, message_url or stable
    if message_url:
        return "url:" + hashlib.sha256(message_url.encode()).hexdigest(), message_url
    return "", ""


def _visit_source(item, erp_student_id=""):
    erp_id = str(item.get("erp_attendance_id") or "").strip()
    if erp_id:
        return erp_id, "erp", erp_id
    message, source_ref = _telegram_source(item)
    if message:
        # One group report can prove several children's visits and even several
        # lessons.  The message is evidence, not the visit's unique key.
        identity = [str(erp_student_id), str(item.get("erp_subscription_id") or ""),
                    str(item.get("date") or ""), str(item.get("time") or ""), _norm(item.get("type"))]
        visit_id = hashlib.sha256(_json(identity).encode()).hexdigest()
        return f"telegram:{message}:visit:{visit_id}", "telegram", source_ref
    return "", "", ""


def _legacy_visit_source(item):
    """Source id written before Telegram message and visit ids were separated."""
    message, _ = _telegram_source(item)
    if not message:
        return ""
    if message.startswith("url:"):
        return "telegram-url:" + message.removeprefix("url:")
    return "telegram:" + message


def _legacy_visit(con, table, item, student_id, group_id, kind, status):
    legacy_id = _legacy_visit_source(item)
    if not legacy_id or not student_id:
        return None
    row = con.execute(f"SELECT * FROM {table} WHERE source_id=?", (legacy_id,)).fetchone()
    if not row or row["student_id"] != student_id or row["visit_date"] != item.get("date") or row["visit_time"] != item.get("time") or row["kind"] != kind or row["status"] != status:
        return None
    if table == "erp_history_visits" and group_id and row["group_id"] != group_id:
        return None
    if table == "erp_history_unassigned_visits" and row["source_group"] != str(item.get("group") or ""):
        return None
    return row


def _pilot_contract_issue(row, erp_id, month):
    if (erp_id, month) == ("92421", "2026-11"):
        subs = _subscriptions(row)
        if {str(x.get("erp_id") or "") for x in subs} != {"825986", "825983"}:
            return "Абонементы не совпадают с разрешённым точечным предпросмотром Кононенко"
        if row.get("attendance"):
            return "Для Кононенко разрешён только план без отметок посещения"
        if not row.get("partial_attendance"):
            return "Для Кононенко требуется сохранить пустые отметки посещения"
        plans = [(sub, _planned_lessons(row, sub)) for sub in subs]
        if sum(len(items) for _, items in plans) != 10:
            return "В точечном плане Кононенко должно быть ровно 10 занятий"
        for sub, items in plans:
            if len(items) != int(sub.get("lesson_count") or 0):
                return "Число занятий Кононенко не совпадает с абонементом"
            if any(not re.fullmatch(r"2026-11-\d{2}", str(item.get("date") or "")) for item in items):
                return "Все занятия Кононенко должны относиться к ноябрю 2026"
        return ""
    if (erp_id, month) == ("85160", "2026-11"):
        subs = _subscriptions(row)
        if {str(x.get("erp_id") or "") for x in subs} != {"828113", "828114"}:
            return "Абонементы не совпадают с разрешённым точечным предпросмотром Шарипова"
        if not row.get("partial_attendance"):
            return "Для этого пилота требуется раздельный план и частично подтверждённая посещаемость"
        if sum(len(_planned_lessons(row, sub)) for sub in subs) != 13:
            return "В точечном плане Шарипова должно быть 13 занятий"
        visits = row.get("attendance") or []
        if len(visits) != 5 or {str(x.get("source_message_id") or "") for x in visits} != SHARIPOV_NOVEMBER_MESSAGES or any(
                str(x.get("source_chat_id") or "") != "-1001602433070" for x in visits):
            return "Пять посещений Шарипова должны ссылаться на подтверждённые сообщения групповых отчётов"
        return ""
    expected = PILOT_CONTRACTS.get((erp_id, month))
    if not expected:
        return ""
    actual = {}
    for sub in _subscriptions(row):
        source_id = str(sub.get("erp_id") or "")
        actual[source_id] = (str(sub.get("valid_from") or ""), str(sub.get("valid_until") or ""),
                             int(sub.get("lesson_count") or 0), _amount(sub.get("price")))
    if actual != expected:
        return "Параметры абонементов не совпадают с разрешённым точечным переносом"
    if not row.get("partial_attendance"):
        return "Для этого пилота требуется раздельный план и частично подтверждённая посещаемость"
    for sub in _subscriptions(row):
        planned = _planned_lessons(row, sub)
        if len(planned) != int(sub.get("lesson_count") or 0):
            return "Число занятий в плане не совпадает с абонементом"
        if str(sub.get("erp_id")) == "827957" and any(
                str(x.get("time") or "") != "17:00" or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(x.get("date") or ""))
                or date.fromisoformat(str(x.get("date"))).weekday() != 5 for x in planned):
            return "Субботняя ОФП должна быть запланирована в 17:00"
    if (erp_id, month) == ("84894", "2026-11"):
        visits = row.get("attendance") or []
        actual = {(str(x.get("source_message_id") or ""), str(x.get("erp_subscription_id") or ""),
                   str(x.get("date") or ""), str(x.get("time") or ""),
                   "makeup" if _norm(x.get("type")) == "отработка" else "subscription") for x in visits}
        if not SHULEZHKO_NOVEMBER_VISITS.issubset(actual) or any(
                str(x.get("source_chat_id") or "") != "-1001602433070" or
                _norm(x.get("status")) not in ("присутствовал", "был", "present") for x in visits):
            return "Пять посещений Шулежко должны точно совпадать с подтверждёнными групповыми отчётами"
    return ""


def _confirmed_group(con, item, candidates):
    """Reuse a group already confirmed for the exact same ERP lesson."""
    candidate_ids = {x["id"] for x in candidates}
    found = set()
    for row in con.execute("""SELECT group_id,source_payload FROM erp_history_visits
        WHERE visit_date=? AND visit_time=? AND kind='makeup' AND status='present'""",
        (item.get("date"), item.get("time"))):
        try: source = json.loads(row["source_payload"])
        except (TypeError, ValueError): continue
        if _norm(source.get("group")) == _norm(item.get("group")) and row["group_id"] in candidate_ids:
            found.add(row["group_id"])
    if len(found) != 1:
        return None
    group_id = found.pop()
    return next((x for x in candidates if x["id"] == group_id), None)


def _student(con, row, selected_id=None):
    erp_id = str(row.get("erp_student_id") or "").strip()
    exact = [dict(r) for r in con.execute("SELECT id,name,birth,erp_id FROM students WHERE erp_id=?", (erp_id,))]
    if len(exact) == 1: return exact[0], "erp_id"
    if len(exact) > 1: return None, "Один ERP ID указан у нескольких карточек"
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='erp_full_identities'").fetchone():
        linked = [dict(r) for r in con.execute('''SELECT s.id,s.name,s.birth,s.erp_id FROM erp_full_identities i
            JOIN students s ON s.id=i.student_id WHERE i.namespace='student' AND i.source_id=?''', (erp_id,))]
        if len(linked) == 1: return linked[0], "saved_erp_link"
        if len(linked) > 1: return None, "Сохранённая связь ERP ведёт к нескольким карточкам"
    if selected_id not in (None, ""):
        try: selected_id = int(selected_id)
        except (TypeError, ValueError): return None, "Некорректно выбрана карточка CRM"
        selected = con.execute("SELECT id,name,birth,erp_id,archived FROM students WHERE id=?", (selected_id,)).fetchone()
        source_name = _norm((row.get("child_profile") or {}).get("name") or row.get("child"))
        if not selected or selected["archived"]: return None, "Выбранная карточка CRM не найдена"
        if not source_name or not _same_name(selected["name"], source_name):
            return None, "ФИО в выбранной карточке не совпадает с ФИО в файле"
        return dict(selected), "manual_selection"
    child = row.get("child_profile") or {}
    name, birth, phone = _norm(child.get("name") or row.get("child")), str(child.get("birth") or "")[:10], normalize_phone(child.get("parent_phone") or "")
    if not (name and birth and phone): return None, "ERP ID не найден; для безопасной сверки нужны ФИО, дата рождения и телефон родителя"
    candidates = [dict(r) for r in con.execute("""SELECT DISTINCT s.id,s.name,s.birth,s.erp_id FROM students s JOIN parents p ON p.family_id=s.family_id
        WHERE s.birth=? AND p.phone_norm=?""", (birth, phone)) if _same_name(r["name"], name)]
    return (candidates[0], "profile") if len(candidates) == 1 else (None, "Несколько подходящих карточек" if candidates else "Карточка CRM не найдена")


def _group(con, source, day, tm, student_id=None, kind=""):
    before_journal = _norm(str(source).split("журнал", 1)[0])
    # Age bands are meaningful: dropping 6/8 made «ПУП 6–8» indistinguishable
    # from other programmes at the same branch and time.
    source_tokens = {x for x in before_journal.split() if x not in SKIP}
    candidates = []
    enrolled = {r[0] for r in con.execute("SELECT group_id FROM enrollments WHERE student_id=?", (student_id,))} if student_id else set()
    for raw in con.execute("SELECT g.*,b.name branch_name FROM groups g JOIN branches b ON b.id=g.branch_id WHERE g.active=1 AND b.active=1"):
        g = dict(raw); version = schedule.on(con, g, date.fromisoformat(day))
        if date.fromisoformat(day).weekday() not in logic.parse_weekdays(version.get("weekdays", "")): continue
        if logic.group_time(version, date.fromisoformat(day).weekday()) != tm: continue
        whole = set(_norm(version.get("name", "") + " " + g.get("source_name", "") + " " + g["branch_name"]).split())
        overlap = source_tokens & whole
        branch_ok = bool(set(_norm(g["branch_name"]).split()) & set(before_journal.split()))
        membership = 4 if kind == "subscription" and g["id"] in enrolled else 0
        if branch_ok and overlap: candidates.append((len(overlap) + membership, g["id"], version.get("name") or g["name"], g["branch_name"]))
    if not candidates: return None, "Группа не найдена по дате, времени и названию", []
    best = max(x[0] for x in candidates); rows = [x for x in candidates if x[0] == best]
    choices = [{"id": x[1], "name": x[2], "branch": x[3]} for x in rows]
    if len(rows) == 1: return rows[0], "", choices
    labels = ", ".join(sorted({f"{x[3]} · {x[2]} (№{x[1]})" for x in rows}))
    return None, "Несколько групп подходят одинаково: " + labels, choices


def preview(con, payload, period=None, student_ids=None, unassigned_visit_ids=None, visit_group_ids=None):
    approved_unassigned = {str(x) for x in (unassigned_visit_ids or [])}
    selected_groups = {str(k): int(v) for k, v in (visit_group_ids or {}).items() if str(v).isdigit()}
    result = {"children": [], "added": 0, "updated": 0, "existing": 0, "review": 0, "can_apply": True}
    for row in _records(payload):
        erp_id, month = str(row.get("erp_student_id") or "").strip(), str(period or row.get("month") or "")
        child = {"erp_id": erp_id, "period": month, "student": None, "subscription": None, "subscriptions": [], "planned": [], "visits": [], "issues": []}
        student, identity = _student(con, row, (student_ids or {}).get(erp_id))
        if not student: child["issues"].append(identity)
        else: child["student"] = {"id": student["id"], "name": student["name"], "match": identity}
        if row.get("month") and str(row.get("month")) != month: child["issues"].append("Период файла не совпадает с выбранным периодом")
        contract_issue = _pilot_contract_issue(row, erp_id, month)
        if contract_issue: child["issues"].append(contract_issue)
        visits = row.get("attendance") or []
        incoming_signatures = {}
        for item in visits:
            signature = (str(item.get("date") or ""), _norm(item.get("group")), _norm(item.get("status")))
            incoming_signatures[signature] = incoming_signatures.get(signature, 0) + 1
        mapped = []
        for item in visits:
            source_id, source_type, source_ref = _visit_source(item, erp_id)
            kind = "makeup" if _norm(item.get("type")) == "отработка" else "subscription" if _norm(item.get("type")) == "абонемент" else ""
            status = "present" if _norm(item.get("status")) in ("присутствовал", "был", "present") else "absent" if _norm(item.get("status")) in ("отсутствовал", "не был", "absent") else ""
            group, issue, group_candidates = _group(con, item.get("group"), str(item.get("date") or ""), str(item.get("time") or ""), student["id"] if student else None, kind) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(item.get("date") or "")) else (None, "Некорректная дата", [])
            if not group and kind == "makeup" and issue.startswith("Несколько групп подходят одинаково:"):
                confirmed = _confirmed_group(con, item, group_candidates)
                if confirmed:
                    group, issue = (0, confirmed["id"], confirmed["name"], confirmed["branch"]), ""
            selected_group_id = selected_groups.get(source_id)
            if selected_group_id:
                chosen = next((x for x in group_candidates if x["id"] == selected_group_id), None)
                if chosen: group, issue = (0, chosen["id"], chosen["name"], chosen["branch"]), ""
                else: issue = "Выбранная группа не входит в проверенные варианты этой отметки"
            existing = con.execute("SELECT * FROM erp_history_visits WHERE source_id=?", (source_id,)).fetchone() if source_id else None
            existing_unassigned = con.execute("SELECT * FROM erp_history_unassigned_visits WHERE source_id=?", (source_id,)).fetchone() if source_id else None
            # Old rows used the Telegram message itself as the unique visit id.
            # Reuse such a row only when all visit fields identify this child;
            # the same message belonging to another child is not a conflict.
            existing = existing or _legacy_visit(con, "erp_history_visits", item, student["id"] if student else None,
                                                   group[1] if group else None, kind, status)
            existing_unassigned = existing_unassigned or _legacy_visit(con, "erp_history_unassigned_visits", item,
                student["id"] if student else None, None, kind, status)
            source_conflict = bool(existing and student and group and (existing["student_id"] != student["id"] or existing["group_id"] != group[1] or existing["visit_date"] != item.get("date") or existing["visit_time"] != item.get("time") or existing["kind"] != kind or existing["status"] != status))
            unassigned_conflict = bool(existing_unassigned and student and (existing_unassigned["student_id"] != student["id"] or existing_unassigned["visit_date"] != item.get("date") or existing_unassigned["visit_time"] != item.get("time") or existing_unassigned["kind"] != kind or existing_unassigned["status"] != status or existing_unassigned["source_group"] != str(item.get("group") or "")))
            signature = (str(item.get("date") or ""), _norm(item.get("group")), _norm(item.get("status")))
            native = bool(incoming_signatures.get(signature) == 1 and student and group and con.execute("SELECT 1 FROM attendance WHERE student_id=? AND group_id=? AND date=? AND status=?", (student["id"],group[1],item.get("date"),status)).fetchone())
            outside = not str(item.get("date") or "").startswith(month + "-")
            ambiguous_group = bool(issue and issue.startswith("Несколько групп подходят одинаково:"))
            approvable_unassigned = bool(student and source_id and kind == "makeup" and status == "present" and not outside and ambiguous_group and re.fullmatch(r"\d{2}:\d{2}", str(item.get("time") or "")))
            approved = approvable_unassigned and source_id in approved_unassigned
            state = "review" if source_conflict or unassigned_conflict or outside else "already_exists" if existing or native else "assign_group" if existing_unassigned and group and selected_group_id else "already_exists" if existing_unassigned else "add_unassigned" if approved else "review" if not student or not source_id or not kind or not status or not group else "add"
            reason = issue or ("Нет ID отметки ERP" if not source_id else "Неизвестный тип или отметка" if not kind or not status else "")
            if source_conflict: reason = "Этот ID отметки ERP уже связан с другими данными"
            if unassigned_conflict: reason = "Этот ID отметки ERP уже сохранён с другими данными"
            if outside: reason = "Дата отметки находится вне выбранного периода"
            out = {"source_id": source_id, "date": item.get("date"), "time": item.get("time"), "source_group": item.get("group"),
                   "group_id": group[1] if group else None, "group": group[2] if group else None, "branch": group[3] if group else None,
                   "kind": kind, "status": status, "state": state, "reason": "" if state in ("add_unassigned", "assign_group") else reason,
                   "approvable_unassigned": approvable_unassigned, "group_unresolved": state in ("add_unassigned", "already_exists") and bool(existing_unassigned),
                   "group_candidates": group_candidates if existing_unassigned or ambiguous_group else [],
                   "source_subscription_id": str(item.get("erp_subscription_id") or ""),
                   "source_type": source_type, "source_ref": source_ref}
            if state == "add_unassigned": out["group_unresolved"] = True
            child["visits"].append(out); mapped.append((item, out)); result["existing" if state == "already_exists" else "added" if state in ("add", "add_unassigned") else "updated" if state == "assign_group" else "review"] += 1
        for sub in _subscriptions(row):
            subid = str(sub.get("erp_id") or "").strip(); revenue_amount = _amount(sub.get("price"))
            attached = [x[1] for x in mapped if x[1]["kind"] == "subscription" and x[1]["source_subscription_id"] == subid]
            planned = []
            for item in _planned_lessons(row, sub):
                day, tm = str(item.get("date") or ""), str(item.get("time") or "")
                group, issue, _ = _group(con, item.get("group") or sub.get("group"), day, tm, student["id"] if student else None, "subscription") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) else (None, "Некорректная дата", [])
                existing_plan = con.execute("SELECT * FROM erp_history_planned_lessons WHERE source_subscription_id=? AND lesson_date=? AND lesson_time=?", (subid,day,tm)).fetchone() if subid else None
                conflict = bool(existing_plan and student and group and (existing_plan["student_id"] != student["id"] or existing_plan["group_id"] != group[1]))
                outside = not day.startswith(month + "-")
                state = "review" if conflict or outside or not student or not subid or not group else "already_exists" if existing_plan else "add"
                reason = "План занятия уже связан с другими данными" if conflict else "Дата занятия находится вне выбранного периода" if outside else issue
                plan = {"source_subscription_id": subid, "date": day, "time": tm,
                        "group_id": group[1] if group else None, "group": group[2] if group else None,
                        "branch": group[3] if group else None, "state": state, "reason": reason}
                planned.append((item, plan)); child["planned"].append(plan)
                result["existing" if state == "already_exists" else "added" if state == "add" else "review"] += 1
            main_groups = {x["group_id"] for x in attached if x["group_id"]} | {x[1]["group_id"] for x in planned if x[1]["group_id"]}
            existing_sub = con.execute("SELECT * FROM erp_history_subscriptions WHERE source_id=?", (subid,)).fetchone() if subid else None
            group_id = next(iter(main_groups)) if len(main_groups) == 1 else None
            lessons = int(sub.get("lesson_count") or 0)
            existing_conflict = bool(existing_sub and student and group_id and (existing_sub["student_id"] != student["id"] or existing_sub["group_id"] != group_id or existing_sub["month"] != month or existing_sub["valid_from"] != sub.get("valid_from") or existing_sub["valid_until"] != sub.get("valid_until") or existing_sub["lessons"] != lessons or existing_sub["revenue_amount"] != revenue_amount))
            native_sub = bool(student and group_id and con.execute('''SELECT 1 FROM subscriptions x JOIN enrollments e ON e.id=x.enrollment_id
                WHERE x.student_id=? AND e.group_id=? AND x.month=? AND x.lessons=? AND COALESCE(x.valid_from,'')=? AND COALESCE(x.valid_until,'')=? AND x.status!='cancelled' ''',
                (student["id"],group_id,month,lessons,sub.get("valid_from") or '',sub.get("valid_until") or '')).fetchone())
            partial = bool(row.get("partial_attendance") or sub.get("partial_attendance"))
            plan_matches = len(planned) == lessons and all(x[1]["state"] != "review" for x in planned)
            marks_match = (partial and plan_matches and len(attached) <= lessons or lessons == len(attached)) and all(x["status"] in ("present", "absent") for x in attached)
            sub_state = "review" if existing_conflict else "already_exists" if existing_sub or native_sub else "add" if student and subid and group_id and revenue_amount is not None and marks_match else "review"
            output = {"source_id": subid, "group_id": group_id, "valid_from": sub.get("valid_from"), "valid_until": sub.get("valid_until"),
                "lessons": sub.get("lesson_count"), "revenue_amount": revenue_amount, "state": sub_state,
                "reason": "" if sub_state != "review" else "ID абонемента уже связан с другими данными" if existing_conflict else "В источнике нет точной стоимости абонемента" if revenue_amount is None else "Абонемент, группа или число отметок (посещения и явные пропуски) не сопоставлены однозначно"}
            child["subscriptions"].append(output)
            result["existing" if sub_state == "already_exists" else "added" if sub_state == "add" else "review"] += 1
        child["subscription"] = child["subscriptions"][0] if child["subscriptions"] else None
        if (erp_id, month) == ("92421", "2026-11"):
            mapped_groups = {x["source_id"]: x["group_id"] for x in child["subscriptions"]}
            if not student or student["id"] != 2:
                child["issues"].append("Точечный перенос Кононенко разрешён только в CRM 2")
            if mapped_groups != {"825986": 31, "825983": 34}:
                child["issues"].append("Абонементы Кононенко должны вести только в группы CRM 31 и 34")
        if not child["subscriptions"]: child["issues"].append("В источнике нет абонементов")
        if child["issues"] or any(x["state"] == "review" for x in child["subscriptions"]) or any(x["state"] == "review" for x in child["planned"]) or any(x["state"] == "review" for x in child["visits"]): result["can_apply"] = False
        result["children"].append(child)
    result["unassigned_visit_ids"] = sorted(approved_unassigned)
    result["visit_group_ids"] = selected_groups
    result["token"] = _token([payload, period, student_ids or {}, sorted(approved_unassigned), selected_groups, result]) if result["can_apply"] else None
    return result


def apply(con, payload, period, token, user, student_ids=None, unassigned_visit_ids=None, visit_group_ids=None):
    checked = preview(con, payload, period, student_ids, unassigned_visit_ids, visit_group_ids)
    if not checked["can_apply"] or checked["token"] != token: raise HistoryError("Предпросмотр изменился. Проверьте данные повторно")
    if any(_pilot_key(c["erp_id"], c["period"]) not in PILOT_KEYS for c in checked["children"]): raise HistoryError("Этот ребёнок и период ещё не разрешены для рабочего переноса")
    now = datetime.now().isoformat(timespec="seconds"); created_subs = created_plans = created_visits = updated_visits = 0
    source_rows = {str(r.get("erp_student_id")): r for r in _records(payload)}
    for child in checked["children"]:
        sid = child["student"]["id"]; raw = source_rows[child["erp_id"]]
        if child["student"]["match"] == "manual_selection":
            linked = con.execute("SELECT student_id FROM erp_full_identities WHERE namespace='student' AND source_id=?", (child["erp_id"],)).fetchone()
            if linked and linked["student_id"] != sid: raise HistoryError("ERP ID уже связан с другой карточкой")
            con.execute("INSERT OR IGNORE INTO erp_full_identities(namespace,source_id,student_id) VALUES ('student',?,?)", (child["erp_id"],sid))
        raw_subscriptions = {str(x.get("erp_id")): x for x in _subscriptions(raw)}
        for sub in child["subscriptions"]:
            if sub["state"] == "add":
                con.execute("INSERT INTO erp_history_subscriptions(source_id,student_id,group_id,month,valid_from,valid_until,lessons,revenue_amount,source_payload,imported_at,imported_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (sub["source_id"],sid,sub["group_id"],child["period"],sub["valid_from"],sub["valid_until"],int(sub["lessons"]),sub["revenue_amount"],_json(raw_subscriptions[sub["source_id"]]),now,user.get("name", ""))); created_subs += 1
        raw_plans = {(str(x.get("erp_subscription_id") or sub.get("erp_id") or ""), str(x.get("date") or ""), str(x.get("time") or "")): x
                     for sub in _subscriptions(raw) for x in _planned_lessons(raw, sub)}
        for plan in child["planned"]:
            if plan["state"] != "add": continue
            key = (plan["source_subscription_id"], plan["date"], plan["time"])
            con.execute("INSERT INTO erp_history_planned_lessons(source_subscription_id,student_id,group_id,lesson_date,lesson_time,source_payload,imported_at,imported_by) VALUES (?,?,?,?,?,?,?,?)",
                (plan["source_subscription_id"],sid,plan["group_id"],plan["date"],plan["time"],_json(raw_plans[key]),now,user.get("name", ""))); created_plans += 1
        by_id = {_visit_source(x, child["erp_id"])[0]: x for x in raw.get("attendance", [])}
        for visit in child["visits"]:
            if visit["state"] not in ("add", "add_unassigned", "assign_group"): continue
            item = by_id[visit["source_id"]]
            if visit["state"] == "add_unassigned":
                con.execute("INSERT INTO erp_history_unassigned_visits(source_id,source_subscription_id,student_id,visit_date,visit_time,kind,status,source_group,source_payload,imported_at,imported_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (visit["source_id"],visit["source_subscription_id"],sid,visit["date"],visit["time"],visit["kind"],visit["status"],str(item.get("group") or ""),_json(item),now,user.get("name", "")))
            else:
                con.execute("INSERT INTO erp_history_visits(source_id,source_subscription_id,student_id,group_id,visit_date,visit_time,kind,status,absence_source_id,source_payload,imported_at,imported_by) VALUES (?,?,?,?,?,?,?,?,NULL,?,?,?)",
                    (visit["source_id"],visit["source_subscription_id"],sid,visit["group_id"],visit["date"],visit["time"],visit["kind"],visit["status"],_json(item),now,user.get("name", "")))
                if visit["state"] == "assign_group": con.execute("DELETE FROM erp_history_unassigned_visits WHERE source_id=?", (visit["source_id"],))
            if visit["state"] == "assign_group": updated_visits += 1
            else: created_visits += 1
    from . import revenuecarry
    revenue_carries_activated = revenuecarry.refresh_historical_sources(con)
    return {"ok": True, "subscriptions_added": created_subs, "planned_lessons_added": created_plans, "visits_added": created_visits, "visits_updated": updated_visits, "revenue_carries_activated": revenue_carries_activated, "preview": preview(con, payload, period, student_ids, unassigned_visit_ids, visit_group_ids)}
