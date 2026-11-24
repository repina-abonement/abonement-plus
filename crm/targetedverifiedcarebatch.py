"""One-time idempotent application of fully specified care-chat sales.

Only structured facts proved by the reviewed 01–24 November exports live here;
raw chat exports are intentionally not committed.  One failed client does not
roll back or block another client.
"""
import json

from . import chatdaytransfer


KEY = "targeted_verified_care_sales_20261124_v1"


def _sale(source_id, at, name, amount, method, components, **extra):
    return {"source_id": source_id, "source_chat": "Служба заботы", "source_at": at,
            "student_name": name, "event_kind": "subscription_sale",
            "sale_date": at[:10], "paid_at": at, "amount": amount,
            "payment_method": method, "components": components, **extra}


def events():
    return [
        _sale("sz:21661:решетняк-ноябрь", "2026-11-16T11:07:53", "Решетняк Матвей", 5120,
            "card_branch", [{"month": "2026-11", "price": 5120,
                "dates": ["2026-11-18", "2026-11-23", "2026-11-25", "2026-11-30"]}],
            birth="2025-03-10", parent_name="Кристина", phone="+79636161967",
            create_if_missing=True, weekdays=[0, 2], tariff="fixed", discount_percent=20,
            discount_rule="sibling", note="Четыре занятия ноября, скидка 20% за второго ребёнка"),
        _sale("sz:21662:никитина-продажа", "2026-11-16T11:11:14", "Никитина Софья", 10240,
            "online", [
                {"month": "2026-11", "price": 5120,
                 "dates": ["2026-11-18", "2026-11-23", "2026-11-25", "2026-11-30"]},
                {"month": "2026-12", "price": 5120,
                 "dates": ["2026-12-02", "2026-12-07", "2026-12-09", "2026-12-14"]}],
            birth="2025-03-05", parent_name="Екатерина", phone="+79260327334",
            create_if_missing=True, weekdays=[0, 2], tariff="fixed", discount_percent=20,
            payment_method_needs_confirmation=True,
            clarification_task_title="Уточнить способ оплаты по QR: ссылка Точки или терминал Сбера",
            clarification_task_note="Уточнить только канал уже существующей оплаты; новую оплату не создавать",
            note="Восемь занятий ноября и декабря до 14.12, скидка 20%; с 15.12 семья уезжает"),
        _sale("sz:21788:киселев-ноябрь", "2026-11-16T18:16:23", "Киселёв Илья", 5120,
            "card", [{"month": "2026-11", "price": 5120,
                "dates": ["2026-11-18", "2026-11-23", "2026-11-25", "2026-11-30"]}],
            birth="2025-04-01", parent_name="Мария", phone="+79191076540",
            create_if_missing=True, weekdays=[0, 2], tariff="fixed", discount_percent=20,
            note="Четыре занятия ноября; чек подтверждён, точный карточный канал не указан"),
        _sale("sz:22889:subscription-sale", "2026-11-22T20:15:29", "Микаладзе Леон", 9920,
            "card_branch", [
                {"month": "2026-11", "price": 2480, "dates": ["2026-11-25", "2026-11-29"]},
                {"month": "2026-12", "price": 7440,
                 "dates": ["2026-12-02", "2026-12-06", "2026-12-09", "2026-12-13", "2026-12-16", "2026-12-20"]}],
            lesson_time="16:45", weekdays=[2, 6], tariff="flex", discount_percent=20,
            note="Ноябрь и декабрь, Ср/Вс 16:45, скидка 20%, картой на филиале"),
        _sale("sz:22890:subscription-sale", "2026-11-22T20:17:12", "Мастердеев Денис", 11880,
            "card_branch", [
                {"month": "2026-11", "price": 2160, "dates": ["2026-11-25", "2026-11-29"]},
                {"month": "2026-12", "price": 9720,
                 "dates": ["2026-12-02", "2026-12-06", "2026-12-09", "2026-12-13", "2026-12-16",
                           "2026-12-20", "2026-12-23", "2026-12-27", "2026-12-30"]}],
            birth="2022-03-04", parent_name="Наталия", phone="+79859523765",
            create_if_missing=True, lesson_time="17:45", weekdays=[2, 6], tariff="fixed",
            discount_percent=20, note="Ноябрь и декабрь, Ср/Вс 17:45, скидка 20%, картой на филиале"),
        _sale("sz:22925:решетняк-декабрь", "2026-11-23T11:15:40", "Решетняк Матвей", 11520,
            "tochka_qr_link", [{"month": "2026-12", "price": 11520,
                "dates": ["2026-12-02", "2026-12-07", "2026-12-09", "2026-12-14", "2026-12-16",
                          "2026-12-21", "2026-12-23", "2026-12-28", "2026-12-30"]}],
            birth="2025-03-10", parent_name="Кристина", phone="+79636161967",
            create_if_missing=True, lesson_time="10:30", weekdays=[0, 2], tariff="fixed",
            discount_percent=20, discount_rule="sibling",
            note="Девять занятий декабря, скидка 20% за второго ребёнка, оплачено по ссылке Точки"),
        _sale("sz:22926:дюссек-продажа", "2026-11-23T11:31:14", "Дюссек Михаил", 10240,
            "cash", [{"month": "2026-12", "price": 10240,
                "dates": ["2026-12-02", "2026-12-07", "2026-12-09", "2026-12-14", "2026-12-16",
                          "2026-12-21", "2026-12-23", "2026-12-28"]}],
            birth="2024-01-26", parent_name="Мария", phone="+79105416473",
            create_if_missing=True, lesson_time="10:30", weekdays=[0, 2], tariff="fixed",
            discount_percent=20,
            note="Декабрь без 30.12; получено 10 500 ₽ наличными, сдача 260 ₽ возвращена"),
    ]


def apply(con):
    old = con.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    if old:
        try:
            saved = json.loads(old[0])
            if saved.get("state") == "complete":
                return saved
        except (TypeError, ValueError):
            pass
    actor = con.execute("""SELECT id,name,role FROM users WHERE active=1 AND role='admin'
        AND trim(name)='Юлия Репина' ORDER BY id LIMIT 1""").fetchone()
    if not actor:
        return {"state": "not_target_database"}
    result = chatdaytransfer.apply(con, {"events": events()}, dict(actor))
    report = {"state": "complete" if not result["errors"] and not result["ambiguous"] else "partial",
              "counts": {key: len(value) for key, value in result.items()},
              "unmatched": result["unmatched"], "ambiguous": result["ambiguous"], "errors": result["errors"]}
    con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)", (KEY, json.dumps(report, ensure_ascii=False)))
    return report


def report(con):
    row = con.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    return json.loads(row[0]) if row else {"state": "pending"}
