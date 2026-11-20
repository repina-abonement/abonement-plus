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
"""
PILOT_KEYS = {"5331a74804c2a561b997d253d29bbccb642f426cf4f8b8ae5c2a03b75fa3e5e1"}
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


def _amount(value):
    if value in (None, ""): return None
    try: amount = Decimal(str(value).replace(" ", "").replace(",", "."))
    except InvalidOperation: return None
    return int(amount) if amount == amount.to_integral_value() and amount >= 0 else None


def _records(payload):
    rows = payload if isinstance(payload, list) else [payload]
    if not rows or any(not isinstance(row, dict) for row in rows): raise HistoryError("Нужен JSON одного ребёнка или список детей")
    return rows


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
        if not source_name or _norm(selected["name"]) != source_name:
            return None, "ФИО в выбранной карточке не совпадает с ФИО в файле"
        return dict(selected), "manual_selection"
    child = row.get("child_profile") or {}
    name, birth, phone = _norm(child.get("name") or row.get("child")), str(child.get("birth") or "")[:10], normalize_phone(child.get("parent_phone") or "")
    if not (name and birth and phone): return None, "ERP ID не найден; для безопасной сверки нужны ФИО, дата рождения и телефон родителя"
    candidates = [dict(r) for r in con.execute("""SELECT DISTINCT s.id,s.name,s.birth,s.erp_id FROM students s JOIN parents p ON p.family_id=s.family_id
        WHERE s.birth=? AND p.phone_norm=?""", (birth, phone)) if _norm(r["name"]) == name]
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
    if not candidates: return None, "Группа не найдена по дате, времени и названию"
    best = max(x[0] for x in candidates); rows = [x for x in candidates if x[0] == best]
    if len(rows) == 1: return rows[0], ""
    labels = ", ".join(sorted({f"{x[3]} · {x[2]} (№{x[1]})" for x in rows}))
    return None, "Несколько групп подходят одинаково: " + labels


def preview(con, payload, period=None, student_ids=None):
    result = {"children": [], "added": 0, "existing": 0, "review": 0, "can_apply": True}
    for row in _records(payload):
        erp_id, month = str(row.get("erp_student_id") or "").strip(), str(period or row.get("month") or "")
        child = {"erp_id": erp_id, "period": month, "student": None, "subscription": None, "visits": [], "issues": []}
        student, identity = _student(con, row, (student_ids or {}).get(erp_id))
        if not student: child["issues"].append(identity)
        else: child["student"] = {"id": student["id"], "name": student["name"], "match": identity}
        if row.get("month") and str(row.get("month")) != month: child["issues"].append("Период файла не совпадает с выбранным периодом")
        visits = row.get("attendance") or []
        mapped = []
        for item in visits:
            source_id = str(item.get("erp_attendance_id") or "").strip()
            kind = "makeup" if _norm(item.get("type")) == "отработка" else "subscription" if _norm(item.get("type")) == "абонемент" else ""
            status = "present" if _norm(item.get("status")) in ("присутствовал", "был", "present") else "absent" if _norm(item.get("status")) in ("отсутствовал", "не был", "absent") else ""
            group, issue = _group(con, item.get("group"), str(item.get("date") or ""), str(item.get("time") or ""), student["id"] if student else None, kind) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(item.get("date") or "")) else (None, "Некорректная дата")
            existing = con.execute("SELECT * FROM erp_history_visits WHERE source_id=?", (source_id,)).fetchone() if source_id else None
            source_conflict = bool(existing and student and group and (existing["student_id"] != student["id"] or existing["group_id"] != group[1] or existing["visit_date"] != item.get("date") or existing["visit_time"] != item.get("time") or existing["kind"] != kind or existing["status"] != status))
            native = bool(student and group and con.execute("SELECT 1 FROM attendance WHERE student_id=? AND group_id=? AND date=? AND status=?", (student["id"],group[1],item.get("date"),status)).fetchone())
            outside = not str(item.get("date") or "").startswith(month + "-")
            state = "review" if source_conflict or outside else "already_exists" if existing or native else "review" if not student or not source_id or not kind or not status or not group else "add"
            reason = issue or ("Нет ID отметки ERP" if not source_id else "Неизвестный тип или отметка" if not kind or not status else "")
            if source_conflict: reason = "Этот ID отметки ERP уже связан с другими данными"
            if outside: reason = "Дата отметки находится вне выбранного периода"
            out = {"source_id": source_id, "date": item.get("date"), "time": item.get("time"), "source_group": item.get("group"),
                   "group_id": group[1] if group else None, "group": group[2] if group else None, "branch": group[3] if group else None,
                   "kind": kind, "status": status, "state": state, "reason": reason, "source_subscription_id": str(item.get("erp_subscription_id") or "")}
            child["visits"].append(out); mapped.append((item, out)); result["existing" if state == "already_exists" else "added" if state == "add" else "review"] += 1
        sub = row.get("subscription") or {}; subid = str(sub.get("erp_id") or "").strip(); revenue_amount = _amount(sub.get("price"))
        main_groups = {x[1]["group_id"] for x in mapped if x[1]["kind"] == "subscription" and x[1]["group_id"] and x[1]["source_subscription_id"] == subid}
        existing_sub = con.execute("SELECT * FROM erp_history_subscriptions WHERE source_id=?", (subid,)).fetchone() if subid else None
        group_id = next(iter(main_groups)) if len(main_groups) == 1 else None
        existing_conflict = bool(existing_sub and student and group_id and (existing_sub["student_id"] != student["id"] or existing_sub["group_id"] != group_id or existing_sub["month"] != month or existing_sub["valid_from"] != sub.get("valid_from") or existing_sub["valid_until"] != sub.get("valid_until") or existing_sub["lessons"] != int(sub.get("lesson_count") or 0) or existing_sub["revenue_amount"] != revenue_amount))
        native_sub = bool(student and group_id and con.execute('''SELECT 1 FROM subscriptions x JOIN enrollments e ON e.id=x.enrollment_id
            WHERE x.student_id=? AND e.group_id=? AND x.month=? AND x.lessons=? AND COALESCE(x.valid_from,'')=? AND COALESCE(x.valid_until,'')=? AND x.status!='cancelled' ''',
            (student["id"],group_id,month,int(sub.get("lesson_count") or 0),sub.get("valid_from") or '',sub.get("valid_until") or '')).fetchone())
        sub_state = "review" if existing_conflict else "already_exists" if existing_sub or native_sub else "add" if student and subid and group_id and revenue_amount is not None and int(sub.get("lesson_count") or 0) == sum(x[1]["kind"] == "subscription" and x[1]["status"] == "present" for x in mapped) else "review"
        child["subscription"] = {"source_id": subid, "group_id": next(iter(main_groups)) if len(main_groups) == 1 else None,
            "valid_from": sub.get("valid_from"), "valid_until": sub.get("valid_until"), "lessons": sub.get("lesson_count"), "revenue_amount": revenue_amount, "state": sub_state,
            "reason": "" if sub_state != "review" else "ID абонемента уже связан с другими данными" if existing_conflict else "В источнике нет точной стоимости абонемента" if revenue_amount is None else "Абонемент, группа или число подтверждённых посещений не сопоставлены однозначно"}
        result["existing" if sub_state == "already_exists" else "added" if sub_state == "add" else "review"] += 1
        if child["issues"] or sub_state == "review" or any(x["state"] == "review" for x in child["visits"]): result["can_apply"] = False
        result["children"].append(child)
    result["token"] = _token([payload, period, student_ids or {}, result]) if result["can_apply"] else None
    return result


def apply(con, payload, period, token, user, student_ids=None):
    checked = preview(con, payload, period, student_ids)
    if not checked["can_apply"] or checked["token"] != token: raise HistoryError("Предпросмотр изменился. Проверьте данные повторно")
    if any(_pilot_key(c["erp_id"], c["period"]) not in PILOT_KEYS for c in checked["children"]): raise HistoryError("Этот ребёнок и период ещё не разрешены для рабочего переноса")
    now = datetime.now().isoformat(timespec="seconds"); created_subs = created_visits = 0
    source_rows = {str(r.get("erp_student_id")): r for r in _records(payload)}
    for child in checked["children"]:
        sid = child["student"]["id"]; raw = source_rows[child["erp_id"]]; sub = child["subscription"]
        if child["student"]["match"] == "manual_selection":
            linked = con.execute("SELECT student_id FROM erp_full_identities WHERE namespace='student' AND source_id=?", (child["erp_id"],)).fetchone()
            if linked and linked["student_id"] != sid: raise HistoryError("ERP ID уже связан с другой карточкой")
            con.execute("INSERT OR IGNORE INTO erp_full_identities(namespace,source_id,student_id) VALUES ('student',?,?)", (child["erp_id"],sid))
        if sub["state"] == "add":
            con.execute("INSERT INTO erp_history_subscriptions(source_id,student_id,group_id,month,valid_from,valid_until,lessons,revenue_amount,source_payload,imported_at,imported_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (sub["source_id"],sid,sub["group_id"],child["period"],sub["valid_from"],sub["valid_until"],int(sub["lessons"]),sub["revenue_amount"],_json(raw["subscription"]),now,user.get("name", ""))); created_subs += 1
        by_id = {str(x.get("erp_attendance_id")): x for x in raw.get("attendance", [])}
        for visit in child["visits"]:
            if visit["state"] != "add": continue
            item = by_id[visit["source_id"]]
            con.execute("INSERT INTO erp_history_visits(source_id,source_subscription_id,student_id,group_id,visit_date,visit_time,kind,status,absence_source_id,source_payload,imported_at,imported_by) VALUES (?,?,?,?,?,?,?,?,NULL,?,?,?)",
                (visit["source_id"],visit["source_subscription_id"],sid,visit["group_id"],visit["date"],visit["time"],visit["kind"],visit["status"],_json(item),now,user.get("name", ""))); created_visits += 1
    return {"ok": True, "subscriptions_added": created_subs, "visits_added": created_visits, "preview": preview(con, payload, period, student_ids)}
