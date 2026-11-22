"""Точечный контракт октябрьского переноса Веткина (Данзанова)."""
from crm import erphistory


def payload():
    group = "Арктика Сборная вт 18:30/четверг 20:30/вс 19:30"
    main_dates = [
        ("2026-10-06", "18:30"), ("2026-10-11", "19:30"),
        ("2026-10-13", "18:30"), ("2026-10-18", "19:30"),
        ("2026-10-20", "18:30"), ("2026-10-25", "19:30"),
        ("2026-10-27", "18:30"),
    ]
    ofp_dates = [
        ("2026-10-08", "20:30"), ("2026-10-15", "20:30"),
        ("2026-10-22", "20:30"), ("2026-10-29", "20:30"),
    ]
    subscriptions = [
        {"erp_id": "821284", "valid_from": "2026-10-06", "valid_until": "2026-10-27",
         "lesson_count": 7, "price": 10500, "group": group,
         "schedule": [{"date": d, "time": t, "group": group} for d, t in main_dates]},
        {"erp_id": "821285", "valid_from": "2026-10-08", "valid_until": "2026-10-29",
         "lesson_count": 4, "price": 4800, "group": group,
         "schedule": [{"date": d, "time": t, "group": group} for d, t in ofp_dates]},
    ]
    marks = {
        ("821284", "2026-10-06", "18:30"): "был",
        ("821284", "2026-10-11", "19:30"): "был",
        ("821284", "2026-10-13", "18:30"): "был",
        ("821284", "2026-10-18", "19:30"): "был",
        ("821284", "2026-10-20", "18:30"): "был",
        ("821284", "2026-10-25", "19:30"): "был",
        ("821284", "2026-10-27", "18:30"): "был",
        ("821285", "2026-10-08", "20:30"): "был",
        ("821285", "2026-10-15", "20:30"): "не был",
        ("821285", "2026-10-22", "20:30"): "не был",
        ("821285", "2026-10-29", "20:30"): "был",
    }
    attendance = [
        {"date": d, "time": t, "group": group, "type": "Абонемент",
         "erp_subscription_id": sub_id, "status": status}
        for (sub_id, d, t), status in marks.items()
    ]
    return {
        "erp_student_id": "125824", "month": "2026-10", "partial_attendance": True,
        "subscriptions": subscriptions, "attendance": attendance,
    }


def test_vetkin_october_contract_accepts_exact_two_subscriptions_and_nine_visits_two_absences():
    assert erphistory._pilot_contract_issue(payload(), "125824", "2026-10") == ""


def test_vetkin_october_contract_rejects_wrong_subscription_set():
    row = payload()
    row["subscriptions"] = row["subscriptions"][:1]
    assert "ровно абонементы" in erphistory._pilot_contract_issue(row, "125824", "2026-10")


def test_vetkin_october_contract_rejects_duplicate_journal_row():
    row = payload()
    row["attendance"].append(dict(row["attendance"][0]))
    assert "ровно 11 строк" in erphistory._pilot_contract_issue(row, "125824", "2026-10")


def test_vetkin_october_contract_rejects_wrong_mark():
    row = payload()
    row["attendance"] = [
        {**x, "status": "не был"} if x["date"] == "2026-10-06" else x
        for x in row["attendance"]
    ]
    assert "точно совпадать с журналом ERP" in erphistory._pilot_contract_issue(row, "125824", "2026-10")


def test_vetkin_october_contract_rejects_wrong_plan_date():
    row = payload()
    row["subscriptions"][0]["schedule"][0]["date"] = "2026-10-07"
    assert "проверенным расписанием октября" in erphistory._pilot_contract_issue(row, "125824", "2026-10")
