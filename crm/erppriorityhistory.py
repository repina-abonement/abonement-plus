"""Targeted, non-financial ERP subscription chronology for explicit ERP IDs."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

from . import erpfull, erpimport as ei


class PriorityHistoryError(ValueError):
    pass


def _norm(value):
    return re.sub(r"\s+", " ", str(value or "").strip().lower().replace("ё", "е").replace("№", "n"))


def _date(value):
    parsed = erpfull._date(value)
    return (parsed or "")[:10]


def _raw(file, row, *names):
    values = {_norm(name): str(row[index] if index < len(row) else "").strip()
              for index, name in enumerate(file["headers"])}
    for name in names:
        if values.get(_norm(name)):
            return values[_norm(name)]
    return ""


def _entry(file, row):
    rec = erpfull._record(file, row)
    erp_id = str(rec.get("erp_id") or _raw(file, row, "ID посетителя", "ID клиента")).strip()
    sale_id = str(rec.get("sale_id") or _raw(file, row, "ID продажи", "ID покупки")).strip()
    sale_at = _date(rec.get("sale_at") or _raw(file, row, "Дата продажи", "Дата события"))
    valid_from = _date(_raw(file, row, "Дата начала", "Начало действия", "Действует с") or rec.get("month"))
    valid_until = _date(_raw(file, row, "Дата окончания", "Окончание действия", "Действует до"))
    lessons = str(rec.get("lessons") or _raw(file, row, "Количество занятий", "Кол-во занятий", "Занятий")).strip()
    group = str(rec.get("group") or _raw(file, row, "Группа")).strip()
    tariff = str(rec.get("tariff") or _raw(file, row, "Тип", "Тариф", "Вид абонемента")).strip()
    identity = sale_id or hashlib.sha256(json.dumps(
        [erp_id, sale_at, valid_from, valid_until, group, lessons, tariff], ensure_ascii=False).encode()).hexdigest()
    return {"erp_id": erp_id, "sale_id": sale_id, "sale_at": sale_at, "valid_from": valid_from,
            "valid_until": valid_until, "lessons": lessons, "group": group, "tariff": tariff,
            "source_id": "erp-subscription:" + identity}


def plan(con, file_id, erp_ids, since="2026-11-01"):
    requested = {str(value).strip() for value in (erp_ids or []) if str(value).strip()}
    if not requested or any(not value.isdigit() for value in requested):
        raise PriorityHistoryError("Укажите ERP ID детей только цифрами")
    files = erpfull._files(con, [file_id])
    file = files[0]
    if file["kind"] != "subs":
        raise PriorityHistoryError("Выберите файл «Проданные абонементы»")
    students = {str(row["erp_id"]): dict(row) for row in con.execute(
        "SELECT id,name,erp_id FROM students WHERE archived=0 AND erp_id!=''")}
    entries, found, issues = [], set(), []
    for number, row in enumerate(file["rows"], 2):
        item = _entry(file, row)
        if item["erp_id"] not in requested:
            continue
        found.add(item["erp_id"])
        if item["erp_id"] not in students:
            issues.append([number, item["erp_id"], "Карточка с этим ERP ID не найдена в CRM"])
            continue
        relevant = max(item["sale_at"], item["valid_from"])
        if relevant and relevant < since:
            continue
        if not (item["sale_at"] or item["valid_from"]):
            issues.append([number, item["erp_id"], "Не хватает даты продажи или начала абонемента"])
            continue
        item["student_id"] = students[item["erp_id"]]["id"]
        item["student"] = students[item["erp_id"]]["name"]
        item["already"] = bool(con.execute(
            "SELECT 1 FROM history WHERE student_id=? AND kind=?", (item["student_id"], item["source_id"])).fetchone())
        entries.append(item)
    signature = hashlib.sha256(json.dumps(
        [file["digest"], sorted(requested), since, [(e["source_id"], e["student_id"]) for e in entries]],
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {"token": signature, "file_id": file_id, "since": since, "requested": sorted(requested),
            "found": sorted(found), "missing": sorted(requested - found), "issues": issues,
            "entries": entries, "new": sum(not e["already"] for e in entries),
            "already": sum(e["already"] for e in entries)}


def apply(con, file_id, erp_ids, token, user, since="2026-11-01"):
    report = plan(con, file_id, erp_ids, since)
    if token != report["token"]:
        raise PriorityHistoryError("Файл или список ID изменился. Сначала повторите проверку")
    if report["issues"]:
        raise PriorityHistoryError("Сначала устраните ошибки проверки")
    added = 0
    for item in report["entries"]:
        if item["already"]:
            continue
        parts = ["ERP: сведения об абонементе"]
        if item["sale_at"]:
            parts.append("дата продажи: " + item["sale_at"])
        if item["tariff"]:
            parts.append("тип: " + item["tariff"])
        if item["group"]:
            parts.append("группа: " + item["group"])
        if item["valid_from"] or item["valid_until"]:
            parts.append("период: " + (item["valid_from"] or "?") + " — " + (item["valid_until"] or "?"))
        if item["lessons"]:
            parts.append("занятий: " + item["lessons"])
        if item["sale_id"]:
            parts.append("ID продажи ERP: " + item["sale_id"])
        # The requested chronology starts on `since`.  Preserve an earlier
        # sale date in the text, while placing the November membership fact at
        # the beginning of its covered period rather than fabricating a sale.
        ts = max(item["sale_at"] or since, item["valid_from"] or since, since) + "T12:00:00"
        con.execute("INSERT INTO history(student_id,ts,user_name,kind,text) VALUES (?,?,?,?,?)",
                    (item["student_id"], ts, (user or {}).get("name", "ERP"), item["source_id"], "; ".join(parts)))
        added += 1
    report["applied"] = added
    return report
