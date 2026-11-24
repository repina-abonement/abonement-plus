"""Bulk, auditable import of care and trainer Telegram exports since 01.11.2026.

Only facts that are structurally unambiguous are written automatically.  Every
source message remains represented in the preview so an unsupported phrase can
never silently disappear from the migration scope.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date

from . import erpfull, erpimport, services as sv, tasks


SINCE = "2026-11-01"
ALIASES = {"миша": "михаил", "женя": "евгений", "даня": "даниил", "дима": "дмитрий",
           "саша": "александр", "вова": "владимир", "ваня": "иван", "леша": "алексей",
           "лёша": "алексей"}
SIGNIFICANT = re.compile(
    r"\b(?:пу|пробн|оплат|продл|абонем|отработ|пропуск|перезапис|записан|не\s+куп|"
    r"ссылк|чек|возврат|баланс|уезжа|боле|групп|турнир)\w*", re.I)
TIME = re.compile(r"^\s*(\d{1,2}:\d{2})\s*$")
NAME = re.compile(r"^[А-ЯЁа-яё][А-ЯЁа-яё-]*(?:\s+[А-ЯЁа-яё][А-ЯЁа-яё-]*){0,3}(?:\s+ПУ)?\s*$", re.I)
SKIP = {"офп", "индивы", "оплаты", "пу", "дети", "список", "не было", "нет"}
NON_NAMES = {"его", "ее", "её", "они", "он", "она", "мы", "вы", "не", "был", "была", "было",
             "пришел", "пришла", "пришли", "ушел", "ушла", "оплатил", "оплатила", "готов", "готово",
             "ребенок", "ребёнок", "детей", "плану", "факту", "всего", "итого", "новый", "новая"}
OPERATIONAL = {"поле", "инвентарь", "диван", "ресепшен", "пейкипер", "смена", "ворота", "белые",
               "касса", "терминал", "отчет", "отчёт", "чек", "чеки"}


class BulkError(ValueError):
    pass


def _text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(x if isinstance(x, str) else str((x or {}).get("text") or "") for x in value)
    return ""


def _words(value):
    words = re.findall(r"[а-яё]+", str(value or "").casefold())
    return [ALIASES.get(word, word.replace("ё", "е")) for word in words]


def _sources(exports):
    if not isinstance(exports, list) or not exports:
        raise BulkError("Выберите выгрузки СЗ и ТРЕНЕРЫ")
    chats = {}
    for raw in exports:
        if not isinstance(raw, dict) or not isinstance(raw.get("messages"), list):
            raise BulkError("Один из файлов не является экспортом Telegram result.json")
        name = str(raw.get("name") or "")
        kind = "care" if re.search(r"служба\s+заботы", name, re.I) else "trainers" if "тренер" in name.casefold() else ""
        if not kind:
            continue
        chat_id = str(raw.get("id") or name)
        bucket = chats.setdefault((kind, chat_id), {"kind": kind, "name": name, "id": chat_id, "messages": {}})
        for message in raw["messages"]:
            if not isinstance(message, dict) or message.get("id") is None:
                continue
            at = str(message.get("date") or "")[:19]
            if len(at) < 10 or at[:10] < SINCE or at[:10] > date.today().isoformat():
                continue
            key = str(message["id"])
            candidate = {"id": key, "at": at, "text": _text(message.get("text")).strip()}
            old = bucket["messages"].get(key)
            if not old or str(message.get("edited_unixtime") or "") >= str(old.get("edited_unixtime") or ""):
                bucket["messages"][key] = candidate
    kinds = {item["kind"] for item in chats.values()}
    if kinds != {"care", "trainers"}:
        raise BulkError("Нужны выгрузки обоих чатов: СЗ и ТРЕНЕРЫ")
    return [{**item, "messages": sorted(item["messages"].values(), key=lambda x: (x["at"], int(x["id"]) if x["id"].isdigit() else x["id"]))}
            for item in chats.values()]


def _trainer_rows(chat):
    rows = []
    for message in chat["messages"]:
        current = ""
        for raw_line in message["text"].replace("\r", "").split("\n"):
            raw_line = raw_line.strip()
            time = TIME.match(raw_line)
            if time:
                current = time.group(1).zfill(5)
                continue
            line = re.sub(r"^[\s•*#\-–—\d.)]+", "", raw_line).strip()
            if not current or not line or line.casefold() in SKIP:
                continue
            if len(_words(line)) == 1 and line == line.upper():
                continue
            clean = re.sub(r"\s*\([^)]*\)\s*$", "", line).strip()
            clean = re.sub(r"\s+ПУ\s*$", "", clean, flags=re.I).strip()
            raw_words = re.findall(r"[а-яё]+", clean.casefold())
            if NAME.match(clean) and 1 <= len(_words(clean)) <= 3 and not set(raw_words) & (NON_NAMES | OPERATIONAL):
                rows.append({"message_id": message["id"], "source_at": message["at"],
                             "date": message["at"][:10], "time": current, "name": clean,
                             "source_id": f"trainers:{message['id']}"})
    unique = {}
    for row in rows:
        unique[(row["message_id"], row["time"], tuple(_words(row["name"])))] = row
    return list(unique.values())


def _match_student(con, name, lesson_time):
    wanted = _words(name)
    candidates = []
    for row in con.execute("SELECT id,name,group_id FROM students WHERE archived=0"):
        actual = _words(row["name"])
        if (len(wanted) >= 2 and set(wanted) == set(actual)) or (len(wanted) == 1 and wanted[0] in actual):
            candidates.append(dict(row))
    if len(candidates) > 1:
        timed = []
        for item in candidates:
            hit = con.execute("""SELECT 1 FROM groups g LEFT JOIN enrollments e ON e.group_id=g.id
                WHERE g.time=? AND (g.id=? OR (e.student_id=? AND e.active=1)) LIMIT 1""",
                (lesson_time, item.get("group_id"), item["id"])).fetchone()
            if hit:
                timed.append(item)
        candidates = timed or candidates
    return candidates


def _erp_profiles(con):
    """Read staged visitor registers only; never read payment registers."""
    profiles = {}
    for upload in con.execute("SELECT * FROM erp_uploads WHERE kind='clients' ORDER BY id"):
        file = dict(upload)
        for key in ("headers", "rows", "mapping"):
            file[key] = json.loads(file[key])
        for row in file["rows"]:
            rec = erpfull._record(file, row)
            if rec.get("id_namespace") != "student":
                continue
            erp_id = str(rec.get("erp_id") or "").strip()
            name = " ".join(str(rec.get("child_name") or "").split())
            if not erp_id or not name:
                continue
            profiles[erp_id] = {"erp_id": erp_id, "name": name,
                "birth": (erpfull._date(rec.get("birth") or "") or "")[:10],
                "parent_name": " ".join(str(rec.get("parent_name") or "").split()),
                "phone": " ".join(str(rec.get("phone") or "").split()),
                "email": " ".join(str(rec.get("email") or "").split()),
                "status": " ".join(str(rec.get("status") or "").split()),
                "group": " ".join(str(rec.get("group") or "").split()),
                "branch": " ".join(str(rec.get("branch") or "").split())}
    return list(profiles.values())


def _erp_match(profiles, name, lesson_time):
    wanted = _words(name)
    matches = []
    for profile in profiles:
        actual = _words(profile["name"])
        exact = len(wanted) >= 2 and set(wanted) == set(actual)
        surname = len(wanted) == 1 and wanted[0] in actual
        if not exact and not surname:
            continue
        if surname and lesson_time not in profile["group"]:
            continue
        matches.append(profile)
    return matches


def _erp_group(con, profile, lesson_time):
    exact = con.execute("SELECT id FROM groups WHERE active=1 AND trim(name)=? ORDER BY id", (profile["group"],)).fetchall()
    if len(exact) == 1:
        return exact[0]["id"]
    rows = con.execute("""SELECT g.id FROM groups g JOIN branches b ON b.id=g.branch_id
        WHERE g.active=1 AND g.is_makeup=0 AND g.time=? AND (?='' OR lower(b.name) LIKE lower(?)) ORDER BY g.id""",
        (lesson_time, profile["branch"], "%"+profile["branch"]+"%" if profile["branch"] else "")).fetchall()
    return rows[0]["id"] if len(rows) == 1 else None


def _erp_ready(profile):
    return bool(profile.get("erp_id") and profile.get("name") and profile.get("birth")
                and profile.get("parent_name") and profile.get("phone"))


def _already_care(con, message_id):
    patterns = (f"sz:{message_id}%", f"telegram:sz-{message_id}:%", f"telegram:sz-{message_id}")
    return bool(con.execute("""SELECT 1 FROM chat_day_transfer_events
        WHERE source_id LIKE ? OR source_id LIKE ? OR source_id=? LIMIT 1""", patterns).fetchone())


def _attendance_exists(con, sid, day, lesson_time, source_id):
    if con.execute("SELECT 1 FROM erp_history_unassigned_visits WHERE source_id=?", (source_id,)).fetchone():
        return True
    if con.execute("""SELECT 1 FROM erp_history_visits WHERE student_id=? AND visit_date=?
        AND COALESCE(visit_time,'')=? AND status='present' LIMIT 1""", (sid, day, lesson_time)).fetchone():
        return True
    if con.execute("""SELECT 1 FROM erp_history_unassigned_visits WHERE student_id=? AND visit_date=?
        AND COALESCE(visit_time,'')=? AND status='present' LIMIT 1""", (sid, day, lesson_time)).fetchone():
        return True
    return bool(con.execute("""SELECT 1 FROM attendance a LEFT JOIN groups g ON g.id=a.group_id
        WHERE a.student_id=? AND a.date=? AND a.status='present' AND (g.time=? OR g.time IS NULL) LIMIT 1""",
        (sid, day, lesson_time)).fetchone())


def _plan(con, exports):
    chats = _sources(exports)
    care = next(x for x in chats if x["kind"] == "care")
    trainer_chats = [x for x in chats if x["kind"] == "trainers"]
    attendance, unresolved = [], []
    erp_profiles = _erp_profiles(con)
    for chat in trainer_chats:
        for row in _trainer_rows(chat):
            matches = _match_student(con, row["name"], row["time"])
            erp_profile = None
            if not matches:
                erp_matches = _erp_match(erp_profiles, row["name"], row["time"])
                if len(erp_matches) == 1:
                    linked = con.execute("SELECT id,name,group_id FROM students WHERE archived=0 AND erp_id=?",
                                         (erp_matches[0]["erp_id"],)).fetchall()
                    if len(linked) == 1:
                        matches = [dict(linked[0])]
                    elif _erp_ready(erp_matches[0]):
                        erp_profile = erp_matches[0]
            if len(matches) != 1:
                if erp_profile:
                    group_id = _erp_group(con, erp_profile, row["time"])
                    attendance.append({**row, "student_id": None, "student_name": erp_profile["name"],
                        "source_id": f"{row['source_id']}:erp:{erp_profile['erp_id']}", "already": False,
                        "create_from_erp": {**erp_profile, "group_id": group_id}})
                    continue
                erp_matches = _erp_match(erp_profiles, row["name"], row["time"])
                reason = "карточка и однозначная полная запись ERP не найдены"
                if len(erp_matches) == 1 and not _erp_ready(erp_matches[0]):
                    reason = "в ERP не хватает даты рождения, родителя или телефона"
                elif len(erp_matches) > 1:
                    reason = "в ERP найдено несколько подходящих детей"
                unresolved.append({**row, "reason": reason, "matches": [x["name"] for x in matches],
                                   "erp_matches": [x["name"] for x in erp_matches]})
                continue
            sid = matches[0]["id"]
            source_id = f"{row['source_id']}:{sid}"
            attendance.append({**row, "student_id": sid, "student_name": matches[0]["name"],
                               "source_id": source_id,
                               "already": _attendance_exists(con, sid, row["date"], row["time"], source_id)})
    care_text = [m for m in care["messages"] if m["text"]]
    care_significant = [m for m in care_text if SIGNIFICANT.search(m["text"])]
    care_with_events = [m for m in care_significant if _already_care(con, m["id"])]
    care_without_events = [m for m in care_significant if not _already_care(con, m["id"])]
    payload = [{k: row.get(k) for k in ("source_id", "student_id", "date", "time", "create_from_erp")}
               for row in attendance if not row["already"]]
    token = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {"period": {"from": SINCE, "to": date.today().isoformat()},
            "files": len(exports), "chats": len(chats),
            "messages": {"care": len(care["messages"]), "trainers": sum(len(x["messages"]) for x in trainer_chats)},
            "trainer": {"rows": len(attendance) + len(unresolved), "ready": sum(not x["already"] for x in attendance),
                        "already": sum(x["already"] for x in attendance), "unresolved": unresolved,
                        "create_from_erp": sum(bool(x.get("create_from_erp")) for x in attendance)},
            "erp": {"profiles": len(erp_profiles), "files": con.execute(
                "SELECT count(*) FROM erp_uploads WHERE kind='clients'").fetchone()[0]},
            "care": {"significant": len(care_significant), "with_events": len(care_with_events),
                     "without_events": len(care_without_events), "pending_messages": [
                         {"id": x["id"], "at": x["at"], "text": x["text"][:500]} for x in care_without_events]},
            "attendance": attendance, "token": token}


def plan(con, exports):
    return _plan(con, exports)


def apply(con, exports, token, user):
    report = _plan(con, exports)
    if not token or token != report["token"]:
        raise BulkError("Данные изменились. Сначала снова выполните проверку")
    added = 0
    created_by_erp = {}
    for row in report["attendance"]:
        if row["already"]:
            continue
        sid = row["student_id"]
        profile = row.get("create_from_erp")
        if profile:
            sid = created_by_erp.get(profile["erp_id"])
            if not sid:
                old = con.execute("SELECT id FROM students WHERE archived=0 AND erp_id=?", (profile["erp_id"],)).fetchone()
                if old:
                    sid = old["id"]
                else:
                    sid = sv.create_student(con, {"name": profile["name"], "birth": profile["birth"],
                        "parent_name": profile["parent_name"], "phone": profile["phone"], "email": profile["email"],
                        "group_id": profile.get("group_id"), "funnel": "members" if profile.get("group_id") else "sales",
                        "source": "ERP"}, user)
                    con.execute("UPDATE students SET erp_id=?,erp_status=? WHERE id=?",
                                (profile["erp_id"], profile["status"], sid))
                    sv.log(con, sid, "Карточка создана из ERP перед переносом подтверждённых сведений ТРЕНЕРЫ",
                           user, "erp")
                created_by_erp[profile["erp_id"]] = sid
            row["source_id"] = f"trainers:{row['message_id']}:{sid}"
        payload = json.dumps({"source": "ТРЕНЕРЫ", "message_id": row["message_id"], "confirmed": True}, ensure_ascii=False)
        con.execute("""INSERT OR IGNORE INTO erp_history_unassigned_visits
            (source_id,source_subscription_id,student_id,visit_date,visit_time,kind,status,source_group,source_payload,imported_at,imported_by)
            VALUES (?,NULL,?,?,?,'subscription','present',?,?,?,?)""",
            (row["source_id"], sid, row["date"], row["time"], f"Отчёт тренера {row['time']}",
             payload, sv.now_iso(), user.get("name", "")))
        added += con.execute("SELECT changes()").fetchone()[0]
    unresolved = report["trainer"]["unresolved"]
    if unresolved:
        detail = "; ".join(f"№{x['message_id']} {x['date']} {x['time']} — {x['name']}" for x in unresolved[:100])
        if len(unresolved) > 100:
            detail += f"; ещё {len(unresolved)-100} строк — в массовом отчёте"
        tasks.add_task(con, "manual", None, date.today().isoformat()+"T23:59",
            auto_key="chat-bulk:trainers-unresolved:2026-11", title="Сверить нераспознанные отметки ТРЕНЕРЫ за ноябрь",
            note="CRM не стала угадывать карточки: "+detail, source="manual", shared=True)
    after = _plan(con, exports)
    return {"added": added, "created_from_erp": len(created_by_erp), "trainer": after["trainer"],
            "care": after["care"], "period": after["period"], "review_task": bool(unresolved)}
