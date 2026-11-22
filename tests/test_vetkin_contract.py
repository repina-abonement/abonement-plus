"""Точечный контракт октябрьского переноса Веткина (Данзанова)."""
from crm import erphistory


def payload():
    main_group = "Арктика Сборная вт 18:30/четверг 20:30/вс 19:30"
    ofp_group = "Арктика Сборная ОФП Чт 20:30"
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
         "lesson_count": 7, "price": 10500, "group": main_group,
         "schedule": [{"date": d, "time": t, "group": main_group} for d, t in main_dates]},
        {"erp_id": "821285", "valid_from": "2026-10-08", "valid_until": "2026-10-29",
         "lesson_count": 4, "price": 4800, "group": ofp_group,
         "schedule": [{"date": d, "time": t, "group": ofp_group} for d, t in ofp_dates]},
    ]
    attendance = []
    idx = 1
    for sub_id, group, dates in (
        ("821284", main_group, main_dates), ("821285", ofp_group, ofp_dates)
    ):
        for d, t in dates:
            attendance.append({
                "date": d, "time": t, "group": group, "type": "Абонемент",
                "erp_subscription_id": sub_id, "erp_attendance_id": f"VETKIN-{idx}",
                "status": "присутствовал" if d == "2026-10-06" else "отсутствовал",
            })
            idx += 1
    return {
        "erp_student_id": "125824", "month": "2026-10", "partial_attendance": True,
        "subscriptions": subscriptions, "attendance": attendance,
    }


def test_vetkin_october_contract_accepts_two_separate_subscriptions_and_11_marks():
    assert erphistory._pilot_contract_issue(payload(), "125824", "2026-10") == ""


def test_vetkin_october_contract_rejects_wrong_subscription_set():
    row = payload()
    row["subscriptions"] = row["subscriptions"][:1]
    assert "ровно абонементы" in erphistory._pilot_contract_issue(row, "125824", "2026-10")


def test_vetkin_october_contract_rejects_duplicate_attendance_ids():
    row = payload()
    row["attendance"][1]["erp_attendance_id"] = row["attendance"][0]["erp_attendance_id"]
    assert "уникальные ERP ID" in erphistory._pilot_contract_issue(row, "125824", "2026-10")


def test_vetkin_october_contract_rejects_wrong_plan_date():
    row = payload()
    row["subscriptions"][0]["schedule"][0]["date"] = "2026-10-07"
    assert "проверенным расписанием" in erphistory._pilot_contract_issue(row, "125824", "2026-10")
