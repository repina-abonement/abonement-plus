"""Preserve verified November Telegram facts for the priority active clients.

The source messages are group reports and care-chat summaries.  One Telegram
message may describe several children, therefore every stored source key also
contains the child identity.  This module never creates a client. It attaches
evidence to one uniquely matching existing card and may repair a precisely
confirmed sale when the client, groups, composition, price and payment are all
unambiguous. A manual task is used only when one of those facts is missing or
conflicts.
"""
import json

from . import chatdaytransfer, money, pricing, services, tasks


KEY = "targeted_priority_clients_telegram_20261123_v1"
SHARIPOV_ERP_ID = "85160"
SHARIPOV_DECEMBER_TASK = "priority-clients:sharipov-december-sale-2026-11-23"
KONONENKO_ERP_ID = "92421"
KONONENKO_DECEMBER_TASK = "priority-clients:kononenko-december-sale-2026-11-22"


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


def _sharipov_groups(con, student_id):
    """Return the unique November arena and OFP groups proved by ERP history."""
    rows = con.execute("""SELECT h.source_id,h.group_id,g.name,g.program_id,g.lesson_kind
        FROM erp_history_subscriptions h JOIN groups g ON g.id=h.group_id
        WHERE h.student_id=? AND h.month='2026-11' AND h.source_id IN ('828113','828114')
        ORDER BY h.source_id""", (student_id,)).fetchall()
    if len(rows) != 2:
        return None, "Не найдены обе подтверждённые строки ERP 828113 и 828114"
    arena = [r for r in rows if r["lesson_kind"] == "arena"]
    ofp = [r for r in rows if r["lesson_kind"] == "ofp"]
    if len(arena) != 1 or len(ofp) != 1 or not arena[0]["program_id"] or arena[0]["program_id"] != ofp[0]["program_id"]:
        return None, "Группы ERP не дают однозначную пару Арктика + ОФП одной сборной"
    return {"arena": dict(arena[0]), "ofp": dict(ofp[0])}, None


def _finish_sharipov_task(con, actor, result):
    row = con.execute("SELECT id,status FROM tasks WHERE auto_key=? ORDER BY id DESC LIMIT 1",
                      (SHARIPOV_DECEMBER_TASK,)).fetchone()
    if row and row["status"] == "open":
        tasks.complete(con, row["id"], actor, result=result)


def _sharipov_manual_task(con, student_id, reason):
    task_id = tasks.add_task(
        con, "payment_check", student_id, "2026-11-24T23:59",
        auto_key=SHARIPOV_DECEMBER_TASK,
        title="Оформить подтверждённый декабрьский абонемент Шарипова",
        note=("СЗ 23.11: Арктика + ОФП, 13 занятий, 20 400 ₽ через PayKeeper. "
              "Автоматическое оформление остановлено: " + reason), source="manual")
    row = con.execute("SELECT id FROM tasks WHERE auto_key=?", (SHARIPOV_DECEMBER_TASK,)).fetchone()
    return task_id or (row[0] if row else None)


def ensure_sharipov_december_sale(con, actor):
    """Create the confirmed December package and payment once, or return an exact blocker."""
    matches = con.execute("""SELECT DISTINCT id FROM students
        WHERE archived=0 AND (erp_id=? OR trim(name) IN ('Даниил Шарипов','Шарипов Даниил'))
        ORDER BY id""", (SHARIPOV_ERP_ID,)).fetchall()
    if len(matches) != 1:
        return {"state": "blocked", "reason": f"Карточка Шарипова определяется неоднозначно: {len(matches)} совпадений"}
    sid = matches[0]["id"]
    current = con.execute("""SELECT id,price,status,lessons FROM subscriptions
        WHERE student_id=? AND month='2026-12' AND status!='cancelled' AND COALESCE(kind,'month')='month'
        ORDER BY id""", (sid,)).fetchall()
    if len(current) > 1:
        reason = "Уже существует несколько декабрьских абонементов; нужна сверка дублей"
        _sharipov_manual_task(con, sid, reason)
        return {"state": "blocked", "student_id": sid, "reason": reason}
    if current:
        sub = current[0]
        paid = money.balance(con, sid)["covered"].get(sub["id"], 0)
        if sub["price"] == 20400 and sub["lessons"] == 13 and paid >= 20400 and sub["status"] == "paid":
            _finish_sharipov_task(con, actor, "Декабрьский пакет Арктика + ОФП и оплата PayKeeper оформлены")
            return {"state": "already_complete", "student_id": sid, "subscription_id": sub["id"]}
        reason = f"Найден конфликтующий декабрьский абонемент #{sub['id']}: {sub['lessons']} занятий, {sub['price']} ₽, статус {sub['status']}"
        _sharipov_manual_task(con, sid, reason)
        return {"state": "blocked", "student_id": sid, "reason": reason}
    groups, reason = _sharipov_groups(con, sid)
    if reason:
        _sharipov_manual_task(con, sid, reason)
        return {"state": "blocked", "student_id": sid, "reason": reason}
    exact_payment = con.execute("""SELECT id,subscription_id FROM payments
        WHERE student_id=? AND amount=20400 AND cancelled=0
          AND method IN ('paykeeper','paykeeper_manual') AND substr(paid_at,1,10)='2026-11-23'""", (sid,)).fetchall()
    enrollment = con.execute("SELECT id FROM enrollments WHERE student_id=? AND group_id=? AND active=1 ORDER BY id LIMIT 1",
                             (sid, groups["arena"]["group_id"])).fetchone()
    con.execute("SAVEPOINT sharipov_december_sale")
    try:
        eid = enrollment["id"] if enrollment else services.add_enrollment(
            con, sid, groups["arena"]["group_id"], None, "fixed", actor)
        invoice = services.create_invoice(con, sid, "2026-12", actor, enrollment_id=eid,
            tariff="pkg", ofp_group_id=groups["ofp"]["group_id"], paid_date="2026-11-23")
        sub_id = invoice["id"]
        sub = con.execute("SELECT price,lessons FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
        if sub["lessons"] != 13:
            raise ValueError(f"Расчёт дал {sub['lessons']} занятий вместо подтверждённых 13")
        if sub["price"] != 20400:
            pricing.correct(con, sub_id, 20400,
                "СЗ 23.11 подтверждает пакет Арктика + ОФП за 20 400 ₽ через PayKeeper", actor)
        if exact_payment and exact_payment[0]["subscription_id"] is None:
            con.execute("UPDATE payments SET subscription_id=? WHERE id=?", (sub_id, exact_payment[0]["id"]))
        elif exact_payment and exact_payment[0]["subscription_id"] != sub_id:
            raise ValueError("Существующий платёж 20 400 ₽ уже привязан к другому абонементу")
        money.reconcile(con, sid)
        covered = money.balance(con, sid)["covered"].get(sub_id, 0)
        if covered >= 20400:
            payment_id = exact_payment[0]["id"] if exact_payment else None
        elif exact_payment:
            raise ValueError("Существующий платёж 20 400 ₽ после сверки не зачёлся в декабрьский абонемент")
        else:
            payment_id = money.pay_subscription(con, sub_id, "paykeeper_manual", actor, amount=20400,
                                                paid_at="2026-11-23T12:44:19")
        verified = con.execute("SELECT price,lessons,status FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
        payment = con.execute("SELECT amount,method,cancelled FROM payments WHERE id=?", (payment_id,)).fetchone() if payment_id else None
        payment_ok = payment and tuple(payment) in ((20400, "paykeeper_manual", 0), (20400, "paykeeper", 0))
        if tuple(verified) != (20400, 13, "paid") or not payment_ok:
            raise ValueError("Итоговая проверка абонемента или платежа не пройдена")
        _finish_sharipov_task(con, actor, "Декабрьский пакет Арктика + ОФП и оплата PayKeeper оформлены")
        tasks.schedule_renewal(con, sid)
        con.execute("RELEASE sharipov_december_sale")
        return {"state": "created", "student_id": sid, "subscription_id": sub_id, "payment_id": payment_id}
    except Exception as error:
        con.execute("ROLLBACK TO sharipov_december_sale")
        con.execute("RELEASE sharipov_december_sale")
        reason = str(error)
        _sharipov_manual_task(con, sid, reason)
        return {"state": "blocked", "student_id": sid, "reason": reason}


def _kononenko_manual_task(con, student_id, reason):
    task_id = tasks.add_task(
        con, "payment_check", student_id, "2026-11-24T23:59",
        auto_key=KONONENKO_DECEMBER_TASK,
        title="Проверить декабрьский абонемент Кононенко",
        note=("СЗ 22.11: Арктика + ОФП по субботам, отпуск с 29.12, 16 400 ₽ через PayKeeper. "
              "Автоматическое оформление остановлено: " + reason), source="manual")
    row = con.execute("SELECT id FROM tasks WHERE auto_key=?", (KONONENKO_DECEMBER_TASK,)).fetchone()
    return task_id or (row[0] if row else None)


def _finish_kononenko_task(con, actor):
    row = con.execute("SELECT id,status FROM tasks WHERE auto_key=? ORDER BY id DESC LIMIT 1",
                      (KONONENKO_DECEMBER_TASK,)).fetchone()
    if row and row["status"] == "open":
        tasks.complete(con, row["id"], actor, result="Декабрьский пакет и оплата PayKeeper оформлены")


def ensure_kononenko_december_sale(con, actor):
    """Apply the exact Telegram-confirmed December package without duplicate money."""
    matches = con.execute("""SELECT id FROM students WHERE archived=0
        AND (erp_id=? OR trim(name) IN ('Иван Кононенко','Кононенко Иван')) ORDER BY id""",
        (KONONENKO_ERP_ID,)).fetchall()
    if len(matches) != 1:
        return {"state": "blocked", "reason": f"Карточка Кононенко определяется неоднозначно: {len(matches)} совпадений"}
    sid = matches[0]["id"]
    history = con.execute("""SELECT h.source_id,h.group_id,g.program_id,g.lesson_kind
        FROM erp_history_subscriptions h JOIN groups g ON g.id=h.group_id
        WHERE h.student_id=? AND h.month='2026-11' AND h.source_id IN ('825986','825983')""", (sid,)).fetchall()
    arena = [row for row in history if row["lesson_kind"] == "arena"]
    ofp = [row for row in history if row["lesson_kind"] == "ofp"]
    if len(arena) != 1 or len(ofp) != 1 or not arena[0]["program_id"] or arena[0]["program_id"] != ofp[0]["program_id"]:
        reason = "Не найдена однозначная пара групп ERP 825986/825983 для Арктики и ОФП"
        _kononenko_manual_task(con, sid, reason)
        return {"state": "blocked", "student_id": sid, "reason": reason}
    invoices = con.execute("""SELECT id,price,lessons,status FROM subscriptions WHERE student_id=?
        AND month='2026-12' AND status!='cancelled' AND COALESCE(kind,'month')='month' ORDER BY id""", (sid,)).fetchall()
    if len(invoices) > 1 or invoices and (invoices[0]["price"] != 16400 or invoices[0]["lessons"] != 12):
        reason = "Декабрьский счёт конфликтует с подтверждением СЗ: ожидаются 12 занятий и 16 400 ₽"
        _kononenko_manual_task(con, sid, reason)
        return {"state": "blocked", "student_id": sid, "reason": reason}
    exact = con.execute("""SELECT id,subscription_id FROM payments WHERE student_id=? AND amount=16400
        AND cancelled=0 AND method IN ('paykeeper','paykeeper_manual') AND substr(paid_at,1,10)='2026-11-22' ORDER BY id""", (sid,)).fetchall()
    if len(exact) > 1:
        reason = "Найдено несколько платежей PayKeeper по 16 400 ₽ от 22.11"
        _kononenko_manual_task(con, sid, reason)
        return {"state": "blocked", "student_id": sid, "reason": reason}
    if invoices:
        sub_id = invoices[0]["id"]
        if exact and exact[0]["subscription_id"] is None:
            con.execute("UPDATE payments SET subscription_id=? WHERE id=?", (sub_id, exact[0]["id"]))
        money.reconcile(con, sid)
        covered = money.balance(con, sid)["covered"].get(sub_id, 0)
        if covered < 16400:
            if exact:
                reason = "Существующий платёж PayKeeper не зачёлся в декабрьский счёт"
                _kononenko_manual_task(con, sid, reason)
                return {"state": "blocked", "student_id": sid, "reason": reason}
            payment_id = money.pay_subscription(con, sub_id, "paykeeper_manual", actor, amount=16400,
                                                paid_at="2026-11-22T13:16:30")
        else:
            payment_id = exact[0]["id"] if exact else None
        _finish_kononenko_task(con, actor)
        tasks.sync_subscription_payment_tasks(con, sid)
        tasks.schedule_renewal(con, sid)
        return {"state": "already_complete" if covered >= 16400 else "completed_existing",
                "student_id": sid, "subscription_id": sub_id, "payment_id": payment_id}

    enrollment = con.execute("SELECT id FROM enrollments WHERE student_id=? AND group_id=? AND active=1 ORDER BY id LIMIT 1",
                             (sid, arena[0]["group_id"])).fetchone()
    con.execute("SAVEPOINT kononenko_december_sale")
    try:
        eid = enrollment["id"] if enrollment else services.add_enrollment(con, sid, arena[0]["group_id"], None, "fixed", actor)
        invoice = services.create_invoice(con, sid, "2026-12", actor, enrollment_id=eid, tariff="pkg",
            ofp_group_id=ofp[0]["group_id"], excluded_dates=["2026-12-29"], paid_date="2026-11-22")
        sub_id = invoice["id"]
        sub = con.execute("SELECT price,lessons FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
        if sub["lessons"] != 12:
            raise ValueError(f"Расчёт дал {sub['lessons']} занятий вместо подтверждённых 12")
        if sub["price"] != 16400:
            pricing.correct(con, sub_id, 16400,
                "СЗ 22.11 подтверждает Арктику + ОФП по субботам, отпуск с 29.12, 16 400 ₽ PayKeeper", actor)
        if exact and exact[0]["subscription_id"] is None:
            con.execute("UPDATE payments SET subscription_id=? WHERE id=?", (sub_id, exact[0]["id"]))
            money.reconcile(con, sid)
            payment_id = exact[0]["id"]
        elif exact:
            raise ValueError("Существующий платёж уже привязан к другому абонементу")
        else:
            payment_id = money.pay_subscription(con, sub_id, "paykeeper_manual", actor, amount=16400,
                                                paid_at="2026-11-22T13:16:30")
        verified = con.execute("SELECT price,lessons,status FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
        if tuple(verified) != (16400, 12, "paid"):
            raise ValueError("Итоговая проверка декабрьского абонемента не пройдена")
        _finish_kononenko_task(con, actor)
        tasks.schedule_renewal(con, sid)
        con.execute("RELEASE kononenko_december_sale")
        return {"state": "created", "student_id": sid, "subscription_id": sub_id, "payment_id": payment_id}
    except Exception as error:
        con.execute("ROLLBACK TO kononenko_december_sale")
        con.execute("RELEASE kononenko_december_sale")
        reason = str(error)
        _kononenko_manual_task(con, sid, reason)
        return {"state": "blocked", "student_id": sid, "reason": reason}
def apply(con):
    actor = con.execute("SELECT id,name,role FROM users WHERE active=1 AND role='admin' AND trim(name)='Юлия Репина' ORDER BY id LIMIT 1").fetchone()
    if not actor:
        return {"state": "not_target_database"}
    actor = dict(actor)
    previous = con.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    if previous:
        try:
            saved = json.loads(previous[0])
            if saved.get("state") == "complete":
                ensure_attendance_control(con)
                saved["sharipov_december_sale"] = ensure_sharipov_december_sale(con, actor)
                saved["kononenko_december_sale"] = ensure_kononenko_december_sale(con, actor)
                _save(con, saved)
                return saved
        except (TypeError, ValueError):
            pass
    result = chatdaytransfer.apply(con, {"events": _events()}, actor)
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
    sharipov_sale = ensure_sharipov_december_sale(con, actor)
    kononenko_sale = ensure_kononenko_december_sale(con, actor)
    if sharipov_sale.get("state") == "blocked" and "sharipov" in ids:
        task_ids["sharipov"] = _sharipov_manual_task(con, ids["sharipov"], sharipov_sale["reason"])
    complete = not result["errors"] and not result["unmatched"] and not result["ambiguous"] and not unresolved
    attendance_control = ensure_attendance_control(con)
    return _save(con, {
        "state": "complete" if complete else "verification_failed",
        "events": {key: len(value) for key, value in result.items()},
        "student_ids": ids,
        "unresolved": unresolved,
        "task_ids": task_ids,
        "attendance_control": attendance_control,
        "sharipov_december_sale": sharipov_sale,
        "kononenko_december_sale": kononenko_sale,
    })


def report(con):
    row = con.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    return json.loads(row[0]) if row else {"state": "pending"}


def sharipov_sale_report(con):
    """Read the actual working rows; never trust only the saved migration state."""
    matches = con.execute("""SELECT id FROM students WHERE archived=0
        AND (erp_id=? OR trim(name) IN ('Даниил Шарипов','Шарипов Даниил')) ORDER BY id""",
        (SHARIPOV_ERP_ID,)).fetchall()
    result = {"card_matches": len(matches), "complete": False, "subscriptions": 0,
              "subscription_id": None, "lessons": None, "price": None, "status": None,
              "covered": 0, "payments": [], "manual_task": None}
    if len(matches) != 1:
        return result
    sid = matches[0]["id"]
    rows = con.execute("""SELECT id,lessons,price,status FROM subscriptions
        WHERE student_id=? AND month='2026-12' AND status!='cancelled' AND COALESCE(kind,'month')='month'
        ORDER BY id""", (sid,)).fetchall()
    result["subscriptions"] = len(rows)
    task = con.execute("SELECT id,status,due_at,result FROM tasks WHERE auto_key=? ORDER BY id DESC LIMIT 1",
                       (SHARIPOV_DECEMBER_TASK,)).fetchone()
    result["manual_task"] = dict(task) if task else None
    if len(rows) != 1:
        return result
    sub = rows[0]
    covered = money.balance(con, sid)["covered"].get(sub["id"], 0)
    payments = [dict(row) for row in con.execute("""SELECT id,amount,method,paid_at,cancelled
        FROM payments WHERE student_id=? AND cancelled=0 AND amount=20400
          AND method IN ('paykeeper','paykeeper_manual') AND substr(paid_at,1,10)='2026-11-23'
        ORDER BY id""", (sid,))]
    result.update(subscription_id=sub["id"], lessons=sub["lessons"], price=sub["price"],
                  status=sub["status"], covered=covered, payments=payments)
    result["complete"] = (sub["lessons"] == 13 and sub["price"] == 20400 and
                          sub["status"] == "paid" and covered >= 20400 and len(payments) == 1 and
                          (not task or task["status"] != "open"))
    return result


def miller_report(con):
    """Read Matvey Miller's transferred facts and all current open actions."""
    matches = con.execute("""SELECT id,name,birth,erp_id,erp_status FROM students WHERE archived=0
        AND (erp_id='79204' OR trim(name) IN ('Матвей Миллер','Миллер Матвей')) ORDER BY id""").fetchall()
    result = {"card_matches": len(matches), "student": None, "parents": [], "history_subscriptions": 0,
              "history_visits": 0, "telegram_events": 0, "december_invoices": [], "open_tasks": [],
              "pennant_task": None}
    if len(matches) != 1:
        return result
    student = matches[0]
    sid = student["id"]
    result["student"] = dict(student)
    result["parents"] = [dict(row) for row in con.execute("""SELECT name,phone,email
        FROM parents WHERE family_id=(SELECT family_id FROM students WHERE id=?) ORDER BY id""", (sid,))]
    result["history_subscriptions"] = con.execute(
        "SELECT count(*) FROM erp_history_subscriptions WHERE student_id=? AND month='2026-11'", (sid,)).fetchone()[0]
    result["history_visits"] = con.execute("""SELECT count(*) FROM (
        SELECT visit_date,visit_time,status FROM erp_history_visits WHERE student_id=? AND visit_date BETWEEN '2026-11-01' AND '2026-11-30'
        UNION SELECT visit_date,visit_time,status FROM erp_history_unassigned_visits WHERE student_id=? AND visit_date BETWEEN '2026-11-01' AND '2026-11-30')""",
        (sid, sid)).fetchone()[0]
    result["telegram_events"] = con.execute("""SELECT count(*) FROM chat_day_transfer_events
        WHERE student_id=? AND source_id LIKE 'telegram:%' AND substr(source_at,1,10)>='2026-11-01'""", (sid,)).fetchone()[0]
    covered = money.balance(con, sid)["covered"]
    for row in con.execute("""SELECT id,lessons,price,status FROM subscriptions
        WHERE student_id=? AND month='2026-12' AND status!='cancelled' ORDER BY id""", (sid,)):
        item = dict(row)
        item["covered"] = covered.get(row["id"], 0)
        item["remaining"] = max(0, row["price"] - item["covered"])
        result["december_invoices"].append(item)
    result["open_tasks"] = [dict(row) for row in con.execute("""SELECT id,kind,title,due_at,source,auto_key
        FROM tasks WHERE student_id=? AND status='open' ORDER BY due_at,id""", (sid,))]
    result["pennant_task"] = next((task for task in result["open_tasks"]
        if task["auto_key"] == "priority-clients:miller-pennant-2026-11"), None)
    return result


def kononenko_sale_report(con):
    matches = con.execute("""SELECT id FROM students WHERE archived=0
        AND (erp_id=? OR trim(name) IN ('Иван Кононенко','Кононенко Иван')) ORDER BY id""",
        (KONONENKO_ERP_ID,)).fetchall()
    result = {"card_matches": len(matches), "complete": False, "subscriptions": 0,
              "subscription_id": None, "lessons": None, "price": None, "status": None,
              "covered": 0, "payments": [], "open_tasks": []}
    if len(matches) != 1:
        return result
    sid = matches[0]["id"]
    rows = con.execute("""SELECT id,lessons,price,status,excluded_dates FROM subscriptions WHERE student_id=?
        AND month='2026-12' AND status!='cancelled' AND COALESCE(kind,'month')='month' ORDER BY id""", (sid,)).fetchall()
    result["subscriptions"] = len(rows)
    result["open_tasks"] = [dict(row) for row in con.execute(
        "SELECT id,kind,title,due_at,auto_key FROM tasks WHERE student_id=? AND status='open' ORDER BY due_at,id", (sid,))]
    if len(rows) != 1:
        return result
    sub = rows[0]
    covered = money.balance(con, sid)["covered"].get(sub["id"], 0)
    payments = [dict(row) for row in con.execute("""SELECT id,amount,method,paid_at FROM payments
        WHERE student_id=? AND cancelled=0 AND amount=16400 AND method IN ('paykeeper','paykeeper_manual')
          AND substr(paid_at,1,10)='2026-11-22' ORDER BY id""", (sid,))]
    try:
        excluded = json.loads(sub["excluded_dates"] or "[]")
    except (TypeError, ValueError):
        excluded = []
    result.update(subscription_id=sub["id"], lessons=sub["lessons"], price=sub["price"],
                  status=sub["status"], covered=covered, payments=payments, excluded_dates=excluded)
    result["complete"] = (sub["lessons"] == 12 and sub["price"] == 16400 and sub["status"] == "paid"
                          and covered >= 16400 and len(payments) == 1 and "2026-12-29" in excluded)
    return result


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
        "sharipov_december_sale": sharipov_sale_report(con),
        "miller": miller_report(con),
        "kononenko_december_sale": kononenko_sale_report(con),
        "summary": {
            "cards": sum(row["matches"] == 1 for row in rows),
            "marks": sum(row["marks"] for row in rows),
            "open_tasks": sum(bool(row["task"] and row["task"]["status"] == "open") for row in rows),
            "missing_tasks": sum(not row["task"] for row in rows),
        },
    }
