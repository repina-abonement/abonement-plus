"""One-time recovery of Alexander Khvostov's verified 23.11 trial result."""
import json

from . import chatdaytransfer, comms


KEY = "targeted_khvostov_chat_transfer_20261123_v2"
NAME = "Хвостов Александр"
PHONE = "+79253530404"
SOURCE_ID = "sz:22926:хвостов-результат"


def _save(con, report):
    con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)",
                (KEY, json.dumps(report, ensure_ascii=False)))
    return report


def apply(con):
    saved = con.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    if saved:
        try:
            report = json.loads(saved[0])
            if report.get("state") == "complete":
                return report
        except (TypeError, ValueError):
            pass
    actor = con.execute("""SELECT id,name,role FROM users
        WHERE active=1 AND role='admin' AND trim(name)='Юлия Репина'
        ORDER BY id LIMIT 1""").fetchone()
    if not actor:
        return {"state": "not_target_database"}

    phone = comms.normalize_phone(PHONE)
    by_name = con.execute("""SELECT id,name FROM students WHERE archived=0
        AND trim(name) IN ('Хвостов Александр','Александр Хвостов')""").fetchall()
    by_phone = con.execute("""SELECT DISTINCT s.id,s.name FROM students s
        JOIN parents p ON p.family_id=s.family_id
        WHERE s.archived=0 AND (phone_match(p.phone_norm,?) OR phone_match(p.phone,?))""",
        (phone, phone)).fetchall()
    ids = {row["id"] for row in [*by_name, *by_phone]}
    if len(ids) > 1 or (by_name and by_phone and by_name[0]["id"] != by_phone[0]["id"]):
        return _save(con, {"state": "identity_conflict", "candidate_ids": sorted(ids)})
    if by_name and not by_phone:
        return _save(con, {"state": "phone_mismatch", "candidate_id": by_name[0]["id"]})
    if by_phone and " ".join(by_phone[0]["name"].casefold().split()) not in {
            "хвостов александр", "александр хвостов"}:
        return _save(con, {"state": "name_mismatch", "candidate_id": by_phone[0]["id"]})

    event = {
        "source_id": SOURCE_ID,
        "source_chat": "Служба заботы и ТРЕНЕРЫ",
        "source_at": "2026-11-23T11:31:14",
        "student_name": NAME,
        "birth": "2024-08-13",
        "parent_name": "Евгения",
        "phone": PHONE,
        "source": "ТРЦ",
        "create_if_missing": True,
        "event_kind": "trial_result",
        "event_date": "2026-11-23",
        "trial_time": "10:30",
        "came": False,
        "outcome": "reinvite",
        "note": "ПУ 23.11 в 10:30 не состоялось: ребёнок не смог отойти от родителей и войти в тренировку. После нескольких попыток посмотрел часть занятия; предложен мягкий адаптационный визит вместе с папой без давления",
        "followup_due": "2026-11-24T11:00",
        "followup_title": "Пригласить на адаптационное ПУ 25.11 в 10:30 вместе с родителем",
    }
    result = chatdaytransfer.apply(con, {"events": [event]}, dict(actor))
    row = con.execute("""SELECT s.id,s.birth,s.trial_at,s.trial_came,s.stage,s.source
        FROM students s JOIN parents p ON p.family_id=s.family_id
        WHERE s.archived=0 AND (phone_match(p.phone_norm,?) OR phone_match(p.phone,?))""",
        (phone, phone)).fetchone()
    open_rebook = con.execute("""SELECT id FROM tasks WHERE student_id=?
        AND status='open' AND kind='rebook' ORDER BY id""", (row["id"],)).fetchall() if row else []
    if len(open_rebook) == 1:
        con.execute("""UPDATE tasks SET title=?,due_at=?,note=? WHERE id=?""",
                    (event["followup_title"], event["followup_due"],
                     "Связаться 24.11 и пригласить на занятие этой же группы в среду 25.11 в 10:30. Предложить прийти вместе с родителем, мягко и без давления",
                     open_rebook[0]["id"]))
    task = con.execute("""SELECT id,title,due_at FROM tasks WHERE student_id=?
        AND status='open' AND kind='rebook' ORDER BY id LIMIT 1""", (row["id"],)).fetchone() if row else None
    complete = bool(row and row["birth"] == "2024-08-13"
                    and row["trial_at"] == "2026-11-23T10:30"
                    and row["trial_came"] == 0 and row["stage"] == "reinvite_pu"
                    and row["source"] == "ТРЦ" and task
                    and task["title"] == event["followup_title"]
                    and str(task["due_at"])[:10] == "2026-11-24"
                    and not result["errors"] and not result["unmatched"] and not result["ambiguous"])
    return _save(con, {"state": "complete" if complete else "verification_failed",
                       "student_id": row["id"] if row else None,
                       "task_id": task["id"] if task else None,
                       "task_title": task["title"] if task else None,
                       "task_due": task["due_at"] if task else None,
                       "transfer": {key: len(value) for key, value in result.items()}})
