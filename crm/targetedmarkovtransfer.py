"""One-time recovery of Timofey Markov's verified 22.11 care-chat transfer."""
import json

from . import chatdaytransfer, comms


KEY = "targeted_markov_chat_transfer_20261123"
NAME = "Марков Тимофей"
PHONE = "+79261775474"
SOURCE_ID = "sz:22891:марков-тимофей"


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

    # This migration is deliberately limited to the real owner's database.
    # Fresh/test databases must never receive a real client fixture.
    actor = con.execute("""SELECT id,name,role FROM users
        WHERE active=1 AND role='admin' AND trim(name)='Юлия Репина'
        ORDER BY id LIMIT 1""").fetchone()
    if not actor:
        return {"state": "not_target_database"}

    phone = comms.normalize_phone(PHONE)
    by_name = con.execute("""SELECT id,name FROM students WHERE archived=0
        AND trim(name) IN ('Марков Тимофей','Тимофей Марков')""").fetchall()
    by_phone = con.execute("""SELECT DISTINCT s.id,s.name FROM students s
        JOIN parents p ON p.family_id=s.family_id
        WHERE s.archived=0 AND (phone_match(p.phone_norm,?) OR phone_match(p.phone,?))""",
        (phone, phone)).fetchall()
    ids = {row["id"] for row in [*by_name, *by_phone]}
    if len(ids) > 1:
        return _save(con, {"state": "identity_conflict", "candidate_ids": sorted(ids)})
    if by_name and by_phone and by_name[0]["id"] != by_phone[0]["id"]:
        return _save(con, {"state": "identity_conflict", "candidate_ids": sorted(ids)})
    # A name match with another parent's phone is not safe enough to create a
    # second card or silently replace the contact.
    if by_name and not by_phone:
        return _save(con, {"state": "phone_mismatch", "candidate_id": by_name[0]["id"]})
    if by_phone and " ".join(by_phone[0]["name"].casefold().split()) not in {
            "марков тимофей", "тимофей марков"}:
        return _save(con, {"state": "name_mismatch", "candidate_id": by_phone[0]["id"]})

    event = {
        "source_id": SOURCE_ID,
        "source_chat": "Служба заботы",
        "source_at": "2026-11-22T20:32:01",
        "student_name": NAME,
        "birth": "2024-04-23",
        "parent_name": "Вера",
        "phone": PHONE,
        "create_if_missing": True,
        "event_kind": "trial_result",
        "event_date": "2026-11-22",
        "trial_time": "16:45",
        "came": True,
        "outcome": "later",
        "note": "Пробное 22.11 в 16:45: решили подождать до весны; на тренировке пока не всё понимал, группа 1,5–3 семье неудобна",
    }
    result = chatdaytransfer.apply(con, {"events": [event]}, dict(actor))
    row = con.execute("""SELECT s.id,s.name,s.birth,s.trial_at,s.trial_came,s.stage
        FROM students s JOIN parents p ON p.family_id=s.family_id
        WHERE s.archived=0 AND (phone_match(p.phone_norm,?) OR phone_match(p.phone,?))""",
        (phone, phone)).fetchone()
    task = con.execute("""SELECT id,title,due_at FROM tasks WHERE student_id=?
        AND status='open' AND kind='rebook' ORDER BY id LIMIT 1""", (row["id"],)).fetchone() if row else None
    complete = bool(row and row["birth"] == "2024-04-23"
                    and row["trial_at"] == "2026-11-22T16:45"
                    and row["trial_came"] == 1 and row["stage"] == "later"
                    and task and str(task["due_at"])[:10] == "2027-03-23"
                    and not result["errors"] and not result["unmatched"] and not result["ambiguous"])
    return _save(con, {"state": "complete" if complete else "verification_failed",
                       "student_id": row["id"] if row else None,
                       "task_id": task["id"] if task else None,
                       "task_title": task["title"] if task else None,
                       "task_due": task["due_at"] if task else None,
                       "transfer": {key: len(value) for key, value in result.items()}})


def report(con):
    row = con.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    return json.loads(row[0]) if row else {"state": "pending"}
