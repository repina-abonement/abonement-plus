"""Remove the two ERP visitor cards explicitly identified by the owner."""
import sqlite3

from . import erpimport, services, studentdelete

TARGETS = {
    3643: (["влад", "мельников"], "102338"),
    3646: (["владислав", "мельников"], "103093"),
}
ACTOR = {"role": "admin", "name": "CRM"}


def apply(con):
    cards = con.execute("SELECT * FROM students WHERE id IN (3643,3646)").fetchall()
    if not cards:
        return "complete"
    for card in cards:
        sid = card["id"]
        name, erp_id = TARGETS[sid]
        if not card["erp_id"] and sorted(erpimport._norm(card["name"]).split()) != name:
            return "name-mismatch"
        if card["erp_id"] not in ("", None, erp_id):
            return "erp-id-mismatch"
        if not con.execute("SELECT 1 FROM parents WHERE family_id=? AND phone_norm=?",
                           (card["family_id"], "79859881756")).fetchone():
            return "parent-mismatch"
        report = studentdelete.preview(con, sid, ACTOR)
        for relation in report["state"]["relations"]:
            table, column, rows = relation["table"], relation["column"], relation["rows"]
            if column != "student_id":
                return "blocked-reference-" + table.replace("_", "-")
            if table in {"history", "crm_activity", "recent_clients"}:
                continue
            if table == "erp_full_identities" and all(
                row["namespace"] == "student" and row["source_id"] == erp_id for row in rows
            ):
                continue
            if table == "tasks":
                continue
            if table == "erp_full_rows" and all(not row["target_table"] and row["target_id"] is None for row in rows):
                continue
            return "blocked-" + table.replace("_", "-")

    con.execute("SAVEPOINT remove_erp_visitor_cards")
    try:
        for table in ("erp_full_identities", "tasks", "history"):
            con.execute(f"DELETE FROM {table} WHERE student_id IN (3643,3646)")
        con.execute("""UPDATE erp_full_rows SET student_id=NULL
            WHERE student_id IN (3643,3646) AND target_table='' AND target_id IS NULL""")
        if con.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='crm_activity'").fetchone():
            con.execute("UPDATE crm_activity SET student_id=NULL WHERE student_id IN (3643,3646)")
        con.execute("DELETE FROM students WHERE id IN (3643,3646)")
        services.audit(con, ACTOR, "maintenance.remove_erroneous_erp_cards", str(len(cards)))
    except BaseException as error:
        con.execute("ROLLBACK TO remove_erp_visitor_cards")
        if isinstance(error, sqlite3.IntegrityError):
            return "dependencies-found"
        raise
    finally:
        con.execute("RELEASE remove_erp_visitor_cards")
    return "complete"


def release_empty_family(con):
    """Release the owner's phone only if the deleted cards left an unused family."""
    if con.execute("SELECT 1 FROM students WHERE id IN (3643,3646)").fetchone():
        return "cards-present"
    matches = con.execute("""SELECT family_id,name FROM parents
        WHERE phone_norm='79859881756'""").fetchall()
    if not matches:
        return "phone-available"
    if len(matches) != 1 or erpimport._norm(matches[0]["name"]) != "маргарита":
        return "contact-mismatch"
    family = matches[0]["family_id"]
    cards = con.execute("SELECT archived FROM students WHERE family_id=?", (family,)).fetchall()
    if cards:
        return "archived-card-present" if all(card["archived"] for card in cards) else "active-card-present"
    if con.execute("SELECT COUNT(*) FROM parents WHERE family_id=?", (family,)).fetchone()[0] != 1:
        return "other-contacts-present"
    for (table,) in con.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        if table == "parents":
            continue
        quoted = '"' + table.replace('"', '""') + '"'
        refs = {r["from"] for r in con.execute(f"PRAGMA foreign_key_list({quoted})") if r["table"] == "families"}
        refs.update(r["name"] for r in con.execute(f"PRAGMA table_info({quoted})") if r["name"] == "family_id")
        for ref in refs:
            column = '"' + ref.replace('"', '""') + '"'
            if con.execute(f"SELECT 1 FROM {quoted} WHERE {column}=? LIMIT 1", (family,)).fetchone():
                return "family-linked-" + table.replace("_", "-")
    con.execute("DELETE FROM families WHERE id=?", (family,))
    services.audit(con, ACTOR, "maintenance.remove_empty_family", "1")
    return "phone-available"
