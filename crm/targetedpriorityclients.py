"""Preserve verified November Telegram facts for the priority active clients.

The source messages are group reports and care-chat summaries.  One Telegram
message may describe several children, therefore every stored source key also
contains the child identity.  This module never creates a client, payment or
subscription; it only attaches evidence to one uniquely matching existing
card and creates narrowly scoped follow-up tasks where the chat itself shows
an unresolved operational action.
"""
import json

from . import chatdaytransfer, tasks


KEY = "targeted_priority_clients_telegram_20261123_v1"


CLIENTS = {
    "shulezhko": "Даниил Шулежко",
    "sharipov": "Даниил Шарипов",
    "miller": "Матвей Миллер",
    "kononenko": "Иван Кононенко",
    "yamroz_artemiy": "Артемий Ямроз",
    "yamroz_lukeriya": "Лукерия Ямроз",
    "pluzhnik": "Максим Плужник",
    "minibaev": "Дамир Минибаев",
    "bachurin": "Владислав Бачурин",
}

def _event(client, message_id, at, chat, note):
    return {
        "source_id": f"telegram:{message_id}:{client}",
        "source_chat": chat,
        "source_at": at,
        "student_name": CLIENTS[client],
        "event_kind": "note",
        "note": note,
    }


def _events():
    events = []
    # Finalised care-chat summary of November subscriptions.  Message 20600
    # was immediately repeated as 20608, so only the later copy is retained.
    summary = {
        "shulezhko": "СЗ: ноябрь — Арктика + ОФП, общая сумма 18 300 ₽, две оплаты",
        "sharipov": "СЗ: ноябрь — Арктика + ОФП, общая сумма 18 300 ₽, две оплаты",
        "miller": "СЗ: ноябрь — Арктика + ОФП по четвергам, общая сумма 18 300 ₽, две оплаты",
        "kononenko": "СЗ: ноябрь — 6 занятий Арктики и 3 занятия в Небе, без 01.11, 07.11 и 08.11; оплачено 12 600 ₽",
        "yamroz_artemiy": "СЗ: ноябрь — Арктика + ОФП, общая сумма 18 300 ₽, две оплаты",
        "yamroz_lukeriya": "СЗ: ноябрь — Арктика + ОФП, общая сумма 14 640 ₽ со скидкой на второго ребёнка, две оплаты",
        "pluzhnik": "СЗ: ноябрь — Арктика + ОФП, общая сумма 18 300 ₽, две оплаты",
        "minibaev": "СЗ: ноябрь — Арктика + ОФП по четвергам, общая сумма 18 300 ₽, две оплаты",
        "bachurin": "СЗ: на 07.11 ноябрь ещё не оплачен; ранее семья не оплачивала из-за болезни, сотрудник написал семье",
    }
    for client, note in summary.items():
        events.append(_event(client, "sz-20608", "2026-11-07T17:06:58", "Служба заботы", note))

    attendance = [
        ("trainers-26693", "2026-11-05T22:04:14", ["bachurin"], "Тренеры: присутствовал 05.11 в группе Небо 19:45"),
        ("trainers-26693-ofp", "2026-11-05T22:04:14", ["minibaev", "yamroz_artemiy", "yamroz_lukeriya", "miller", "pluzhnik"], "Тренеры: присутствовал 05.11 на ОФП"),
        ("trainers-26745", "2026-11-07T22:24:13", ["shulezhko", "sharipov"], "Тренеры: присутствовал 07.11 на ОФП в Небе, группа 17:00"),
        ("trainers-26789", "2026-11-08T21:07:19", ["shulezhko", "sharipov", "miller", "kononenko", "bachurin"], "Тренеры: присутствовал 08.11 на тренировке Арктики"),
        ("trainers-26810", "2026-11-10T19:59:25", ["shulezhko", "sharipov", "miller", "kononenko", "yamroz_artemiy", "yamroz_lukeriya", "pluzhnik", "minibaev"], "Тренеры: присутствовал 10.11 на тренировке Арктики"),
        ("trainers-26907", "2026-11-12T22:10:54", ["bachurin"], "Тренеры: присутствовал 12.11 в группе Небо 19:45"),
        ("trainers-26908", "2026-11-12T22:12:17", ["shulezhko", "sharipov", "miller", "yamroz_artemiy", "yamroz_lukeriya", "pluzhnik", "minibaev"], "Тренеры: присутствовал 12.11 на ОФП"),
        ("trainers-27065", "2026-11-15T22:07:32", ["shulezhko", "sharipov", "miller", "kononenko", "yamroz_artemiy", "yamroz_lukeriya", "pluzhnik", "minibaev"], "Тренеры: присутствовал 15.11 на тренировке Арктики"),
        ("trainers-27093", "2026-11-19T20:41:23", ["kononenko"], "Тренеры: присутствовал 19.11 в группе Небо 19:45; СЗ оформляла это как отработку"),
    ]
    for message_id, at, clients, note in attendance:
        for client in clients:
            events.append(_event(client, message_id, at, "ТРЕНЕРЫ", note))

    events.extend([
        _event("shulezhko", "sz-20410", "2026-11-05T10:00:44", "Служба заботы", "СЗ: получена оплата по ссылке 6 000 ₽"),
        _event("shulezhko", "sz-20646", "2026-11-08T19:55:12", "Служба заботы", "СЗ: получена оплата за турнир 2 700 ₽ через Telegram"),
        _event("shulezhko", "sz-20893", "2026-11-11T10:20:21", "Служба заботы", "СЗ: оплата Шулежко обнаружена в PayKeeper"),
        _event("pluzhnik", "sz-20680", "2026-11-09T21:07:57", "Служба заботы", "СЗ: в PayKeeper видны две оплаты Плужника — 13 500 ₽ и 4 800 ₽"),
        _event("pluzhnik", "sz-20924", "2026-11-11T17:00:40", "Служба заботы", "СЗ: обе оплаты Плужника проверены, чеки присутствуют"),
        _event("kononenko", "sz-21928", "2026-11-17T15:40:55", "Служба заботы", "СЗ: назначена отработка 19.11 в 19:45"),
        _event("kononenko", "sz-22845", "2026-11-22T13:16:30", "Служба заботы", "СЗ: оплачен декабрьский абонемент Арктика + ОФП по субботам — 16 400 ₽ через PayKeeper; отпуск с 29.12"),
        _event("sharipov", "sz-22955", "2026-11-23T12:44:19", "Служба заботы", "СЗ: оплачено продление на декабрь — Арктика + ОФП, три раза в неделю, 20 400 ₽ через PayKeeper. Сотрудник сообщил, что абонемент не появился в CRM"),
        _event("minibaev", "sz-22662", "2026-11-21T18:34:17", "Служба заботы", "СЗ: семья в MAX попросила заменить субботнее занятие на четверг; предупреждены, далее ходили по четвергам"),
        _event("miller", "sz-22317", "2026-11-19T21:36:20", "Служба заботы", "СЗ: Матвею выдан кубок за наклейки; дополнительно нужен вымпел"),
        _event("yamroz_artemiy", "sz-20270", "2026-11-03T19:05:20", "Служба заботы", "СЗ: Артемий включён в список оплат за ноябрьский турнир"),
        _event("yamroz_lukeriya", "sz-20270", "2026-11-03T19:05:20", "Служба заботы", "СЗ: Лукерия включена в список оплат за ноябрьский турнир"),
    ])
    return events


def _save(con, report):
    con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)", (KEY, json.dumps(report, ensure_ascii=False)))
    return report


def ensure_attendance_control(con):
    ids = {}
    for key, name in CLIENTS.items():
        rows = con.execute("SELECT id FROM students WHERE archived=0 AND trim(name) IN (?,?)",
                           (name, " ".join(reversed(name.split())))).fetchall()
        if len(rows) == 1:
            ids[key] = rows[0]["id"]
    inserted = 0
    # A report with an explicit date and a uniquely determined programme
    # session is safe to mark. Several group reports omit the time in text,
    # but the named programme has one session at that date in the sold plan.
    report_times = {
        "trainers-26693-ofp": "20:30", "trainers-26789": "19:30",
        "trainers-26810": "18:30", "trainers-26908": "20:30",
        "trainers-27065": "19:30",
    }
    import re
    for event in _events():
        if event.get("source_chat") != "ТРЕНЕРЫ":
            continue
        date_match = re.search(r"\b(\d{2})\.(\d{2})\b", event.get("note", ""))
        time_match = re.search(r"\b(\d{2}:\d{2})\b", event.get("note", ""))
        message_id = event["source_id"].split(":", 2)[1]
        tm = time_match.group(1) if time_match else report_times.get(message_id)
        if not date_match or not tm:
            continue
        client = event["source_id"].rsplit(":", 1)[-1]
        sid = ids.get(client)
        if not sid:
            continue
        day = f"2026-{date_match.group(2)}-{date_match.group(1)}"
        kind = "makeup" if "отработ" in event.get("note", "").casefold() else "subscription"
        group = event.get("note", "").removeprefix("Тренеры: присутствовал ")
        exists = con.execute(
            "SELECT 1 FROM erp_history_visits WHERE student_id=? AND visit_date=? AND visit_time=?", (sid, day, tm)).fetchone() or con.execute(
            "SELECT 1 FROM erp_history_unassigned_visits WHERE student_id=? AND visit_date=? AND visit_time=?", (sid, day, tm)).fetchone() or con.execute(
            "SELECT 1 FROM attendance WHERE student_id=? AND date=? AND status='present'", (sid, day)).fetchone()
        if exists:
            continue
        source_id = f"priority-trainers:{message_id}:{client}:{day}:{tm}"
        payload = json.dumps({"source": "ТРЕНЕРЫ", "message_id": message_id, "confirmed": True}, ensure_ascii=False)
        con.execute("""INSERT OR IGNORE INTO erp_history_unassigned_visits
            (source_id,source_subscription_id,student_id,visit_date,visit_time,kind,status,source_group,source_payload,imported_at,imported_by)
            VALUES (?,NULL,?,?,?,?,?,?,?,?,?)""",
            (source_id, sid, day, tm, kind, "present", group, payload, "2026-11-23T20:00:00", "Перенос ТРЕНЕРЫ"))
        inserted += con.execute("SELECT changes()").fetchone()[0]
    audit_tasks = {}
    for key, sid in ids.items():
        audit_tasks[key] = tasks.add_task(
            con, "manual", sid, "2026-11-24T23:59",
            auto_key=f"priority-clients:attendance-audit:{key}:2026-11-01-22",
            title="Сверить и проставить все посещения 01–22.11",
            note="Сопоставить план каждого ноябрьского абонемента с отчётами ТРЕНЕРЫ. Подтверждённые отметки уже внесены. По каждой оставшейся дате проставить «был»/«не был»; если отчёта нет — уточнить у тренера, не угадывать",
            source="manual")
    return {"student_ids": ids, "inserted_marks": inserted, "task_ids": audit_tasks}


def apply(con):
    previous = con.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    if previous:
        try:
            saved = json.loads(previous[0])
            if saved.get("state") == "complete":
                ensure_attendance_control(con)
                return saved
        except (TypeError, ValueError):
            pass
    actor = con.execute("SELECT id,name,role FROM users WHERE active=1 AND role='admin' AND trim(name)='Юлия Репина' ORDER BY id LIMIT 1").fetchone()
    if not actor:
        return {"state": "not_target_database"}
    result = chatdaytransfer.apply(con, {"events": _events()}, dict(actor))
    unresolved = []
    ids = {}
    for key, name in CLIENTS.items():
        rows = con.execute("SELECT id FROM students WHERE archived=0 AND trim(name) IN (?,?)",
                           (name, " ".join(reversed(name.split())))).fetchall()
        if len(rows) != 1:
            unresolved.append({"client": key, "name": name, "matches": len(rows)})
        else:
            ids[key] = rows[0]["id"]
    task_ids = {}
    if "bachurin" in ids:
        task_ids["bachurin"] = tasks.add_task(
            con, "renew", ids["bachurin"], "2026-11-23T23:59",
            auto_key="priority-clients:bachurin-payment-2026-11",
            title="Уточнить оплату и дальнейшие посещения после болезни",
            note="СЗ 07.11: ноябрь не был оплачен, семья ранее не оплачивала из-за болезни; при этом тренеры подтвердили посещения 08.11 и 12.11",
            source="manual")
    if "miller" in ids:
        task_ids["miller"] = tasks.add_task(
            con, "manual", ids["miller"], "2026-11-23T23:59",
            auto_key="priority-clients:miller-pennant-2026-11",
            title="Передать Матвею вымпел за наклейки",
            note="19.11 кубок уже выдан; в сообщении СЗ указано, что дополнительно нужен вымпел",
            source="manual")
    if "sharipov" in ids:
        task_ids["sharipov"] = tasks.add_task(
            con, "payment_check", ids["sharipov"], "2026-11-23T23:59",
            auto_key="priority-clients:sharipov-december-sale-2026-11-23",
            title="Оформить подтверждённый декабрьский абонемент Шарипова",
            note="СЗ 23.11: Арктика + ОФП, три раза в неделю, 20 400 ₽ через PayKeeper; сотрудник прямо указал, что абонемент не появился в CRM. Перед оформлением проверить существующие декабрьские строки и не создавать дубль оплаты",
            source="manual")
    complete = not result["errors"] and not result["unmatched"] and not result["ambiguous"] and not unresolved
    attendance_control = ensure_attendance_control(con)
    return _save(con, {
        "state": "complete" if complete else "verification_failed",
        "events": {key: len(value) for key, value in result.items()},
        "student_ids": ids,
        "unresolved": unresolved,
        "task_ids": task_ids,
        "attendance_control": attendance_control,
    })


def report(con):
    row = con.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    return json.loads(row[0]) if row else {"state": "pending"}


def attendance_report(con):
    """Read-only working-state check for the nine priority client cards."""
    rows = []
    for key, name in CLIENTS.items():
        matches = con.execute(
            "SELECT id FROM students WHERE archived=0 AND trim(name) IN (?,?) ORDER BY id",
            (name, " ".join(reversed(name.split()))),
        ).fetchall()
        item = {"key": key, "name": name, "matches": len(matches), "marks": 0,
                "mark_dates": [], "task": None}
        if len(matches) != 1:
            rows.append(item)
            continue
        sid = matches[0]["id"]
        historical = set()
        for table in ("erp_history_visits", "erp_history_unassigned_visits"):
            for row in con.execute(
                    f"SELECT visit_date,visit_time,status FROM {table} WHERE student_id=? AND visit_date BETWEEN '2026-11-01' AND '2026-11-22'",
                    (sid,)):
                historical.add((row["visit_date"], row["visit_time"] or "", row["status"]))
        marks = set(historical)
        historical_dates = {(date, status) for date, _, status in historical}
        for row in con.execute(
                "SELECT date,status,group_id FROM attendance WHERE student_id=? AND date BETWEEN '2026-11-01' AND '2026-11-22'",
                (sid,)):
            if (row["date"], row["status"]) not in historical_dates:
                marks.add((row["date"], "", f"native:{row['group_id']}:{row['status']}"))
        task = con.execute(
            "SELECT id,title,due_at,status FROM tasks WHERE auto_key=? ORDER BY id DESC LIMIT 1",
            (f"priority-clients:attendance-audit:{key}:2026-11-01-22",),
        ).fetchone()
        item.update(student_id=sid, marks=len(marks),
                    mark_dates=sorted({mark[0] for mark in marks}),
                    task=dict(task) if task else None)
        rows.append(item)
    return {
        "period": {"from": "2026-11-01", "to": "2026-11-22"},
        "clients": rows,
        "summary": {
            "cards": sum(row["matches"] == 1 for row in rows),
            "marks": sum(row["marks"] for row in rows),
            "open_tasks": sum(bool(row["task"] and row["task"]["status"] == "open") for row in rows),
            "missing_tasks": sum(not row["task"] for row in rows),
        },
    }
