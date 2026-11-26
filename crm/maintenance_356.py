"""Correct the owner-confirmed duplicate December sale without moving money."""
import json

from . import comms, money, services

STUDENT = 134
MARKER = "maintenance_356"


def apply(con):
    if comms.get(con, MARKER):
        return "complete"
    con.execute("SAVEPOINT correct_duplicate_sale")
    try:
        result = _correct(con)
        if result != "complete":
            con.execute("ROLLBACK TO correct_duplicate_sale")
        return result
    except (ValueError, services.ApiError):
        con.execute("ROLLBACK TO correct_duplicate_sale")
        return "guard-rejected"
    except BaseException:
        con.execute("ROLLBACK TO correct_duplicate_sale")
        raise
    finally:
        con.execute("RELEASE correct_duplicate_sale")


def _correct(con):
    payments = {row["id"]: dict(row) for row in con.execute(
        "SELECT * FROM payments WHERE student_id=? AND cancelled=0", (STUDENT,)
    )}
    original = payments.get(82)
    duplicate = payments.get(84)
    if not original or not duplicate:
        return "payment-mismatch"
    if (original["subscription_id"], original["amount"], original["method"],
        original["confirmed"], original["paid_at"][:10]) != (86, 10800, "tochka_qr_link", 1, "2026-11-21"):
        return "payment-mismatch"
    if (duplicate["subscription_id"], duplicate["amount"], duplicate["method"],
        duplicate["confirmed"], duplicate["paid_at"][:10]) != (88, 8100, "cash", 0, "2026-11-21"):
        return "payment-mismatch"
    sales = con.execute("""SELECT s.id,s.status,s.price,s.lessons,e.group_id FROM subscriptions s
        LEFT JOIN enrollments e ON e.id=s.enrollment_id
        WHERE s.student_id=? AND s.month='2026-12' ORDER BY s.id""", (STUDENT,)).fetchall()
    if [tuple(row) for row in sales] != [(86, "cancelled", 10800, 7, 11), (88, "paid", 8100, 6, 8)]:
        return "subscription-mismatch"
    before = money.balance(con, STUDENT)
    if (before["deposit"], before["debt"], before["covered"].get(88)) != (10800, 0, 8100):
        return "balance-mismatch"
    if con.execute("""SELECT 1 FROM pay_links WHERE student_id=?
        UNION ALL SELECT 1 FROM attendance WHERE student_id=? AND date LIKE '2026-12-%'
        UNION ALL SELECT 1 FROM refund_requests WHERE subscription_id IN (86,88)
        UNION ALL SELECT 1 FROM fiscal_payment_checks WHERE payment_id=84 AND status='confirmed'
        UNION ALL SELECT 1 FROM revenue_carries WHERE target_subscription_id=88 OR source_subscription_id=88""",
        (STUDENT, STUDENT)).fetchone():
        return "dependencies-found"

    cash_note = f"Принята оплата 8100 ₽, {money.METHODS['cash'].lower()}"
    if duplicate["comment"]:
        cash_note += f": {duplicate['comment']}"
    hidden = [row[0] for row in con.execute("""SELECT id FROM history WHERE student_id=?
        AND kind='payment' AND user_name=? AND substr(ts,1,16)=substr(?,1,16)
        AND (text=? OR (text LIKE 'Выставлен счёт на %' AND text LIKE '%8100 ₽ (6 занятий%'))""",
        (STUDENT, duplicate["user_name"], duplicate["created_at"], cash_note))]
    previous_history = con.execute("SELECT COALESCE(MAX(id),0) FROM history").fetchone()[0]
    user = {"name": "CRM"}
    services.cancel_subscription(con, 88, user, comment="Ошибочное повторное оформление")
    money.cancel_payment(con, 84, user)

    after = money.balance(con, STUDENT)
    saved = con.execute("SELECT * FROM payments WHERE id=82").fetchone()
    if (after["deposit"], after["debt"]) != (10800, 0) or dict(saved) != original:
        return "verification-failed"
    hidden.extend(row[0] for row in con.execute(
        "SELECT id FROM history WHERE student_id=? AND id>?", (STUDENT, previous_history)
    ))
    comms.put(con, MARKER, json.dumps(hidden))
    return "complete"


RESET_MARKER = "maintenance_356_reset"


def reset_payment(con):
    """Reverse only the original manual entry so it can be recorded once again."""
    if comms.get(con, RESET_MARKER):
        return "complete"
    if not comms.get(con, MARKER):
        return "correction-required"
    payment = con.execute("SELECT * FROM payments WHERE id=82").fetchone()
    if not payment or (
        payment["student_id"], payment["subscription_id"], payment["amount"],
        payment["method"], payment["confirmed"], payment["cancelled"], payment["paid_at"][:10],
    ) != (STUDENT, 86, 10800, "tochka_qr_link", 1, 0, "2026-11-21"):
        return "payment-mismatch"
    if con.execute("""SELECT 1 FROM subscriptions WHERE student_id=? AND month='2026-12' AND status!='cancelled'
        UNION ALL SELECT 1 FROM pay_links WHERE payment_id=82
        UNION ALL SELECT 1 FROM fiscal_payment_checks WHERE payment_id=82 AND status='confirmed'""",
        (STUDENT,)).fetchone():
        return "dependencies-found"
    before = money.balance(con, STUDENT)
    if (before["deposit"], before["debt"]) != (10800, 0):
        return "balance-mismatch"

    con.execute("SAVEPOINT reverse_original_entry")
    try:
        previous_history = con.execute("SELECT COALESCE(MAX(id),0) FROM history").fetchone()[0]
        money.cancel_payment(con, 82, {"name": "CRM"})
        after = money.balance(con, STUDENT)
        if (after["deposit"], after["debt"]) != (0, 0) or after["covered"] != before["covered"]:
            con.execute("ROLLBACK TO reverse_original_entry")
            return "verification-failed"
        hidden = [5313]
        hidden.extend(row[0] for row in con.execute(
            "SELECT id FROM history WHERE student_id=? AND id>?", (STUDENT, previous_history)
        ))
        comms.put(con, RESET_MARKER, json.dumps(hidden))
        return "complete"
    except ValueError:
        con.execute("ROLLBACK TO reverse_original_entry")
        return "guard-rejected"
    except BaseException:
        con.execute("ROLLBACK TO reverse_original_entry")
        raise
    finally:
        con.execute("RELEASE reverse_original_entry")
