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
    if len(cards) != len(TARGETS) or cards[0]["family_id"] != cards[1]["family_id"]:
        return "identity-mismatch"
    for card in cards:
        sid = card["id"]
        name, erp_id = TARGETS[sid]
        if (sorted(erpimport._norm(card["name"]).split()) != name or card["erp_id"] != erp_id
                or not con.execute("SELECT 1 FROM parents WHERE family_id=? AND phone_norm=?",
                                   (card["family_id"], "79859881756")).fetchone()):
            return "identity-mismatch"
        report = studentdelete.preview(con, sid, ACTOR)
        for relation in report["state"]["relations"]:
            table, column, rows = relation["table"], relation["column"], relation["rows"]
            if column != "student_id":
                return "dependencies-found"
            if table == "history":
                continue
            if table == "erp_full_identities" and all(
                row["namespace"] == "student" and row["source_id"] == erp_id for row in rows
            ):
                continue
            if table == "tasks" and all(
                row["auto_key"] in {f"erp-visitor-pending:{sid}", f"client-coverage:{sid}",
                                    f"ad-source-required:{sid}"} and row["source"] != "manual"
                for row in rows
            ):
                continue
            return "dependencies-found"

    con.execute("SAVEPOINT remove_erp_visitor_cards")
    try:
        for table in ("erp_full_identities", "tasks", "history"):
            con.execute(f"DELETE FROM {table} WHERE student_id IN (3643,3646)")
        con.execute("DELETE FROM students WHERE id IN (3643,3646)")
        services.audit(con, ACTOR, "maintenance.remove_erroneous_erp_cards", "2")
    except BaseException as error:
        con.execute("ROLLBACK TO remove_erp_visitor_cards")
        if isinstance(error, sqlite3.IntegrityError):
            return "dependencies-found"
        raise
    finally:
        con.execute("RELEASE remove_erp_visitor_cards")
    return "complete"
