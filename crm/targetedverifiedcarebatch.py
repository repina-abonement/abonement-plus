"""One-time idempotent application of fully specified care-chat sales.

Only structured facts proved by the reviewed 01–24 November exports live here;
raw chat exports are intentionally not committed.  One failed client does not
roll back or block another client.
"""
import json

from . import chatdaytransfer


KEY = "targeted_verified_care_sales_20261124_v2"


def _sale(source_id, at, name, amount, method, components, **extra):
    return {"source_id": source_id, "source_chat": "Служба заботы", "source_at": at,
            "student_name": name, "event_kind": "subscription_sale",
            "sale_date": at[:10], "paid_at": at, "amount": amount,
            "payment_method": method, "components": components, **extra}


def sale_events():
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


def fact_events():
    """Exact non-subscription facts from the same reviewed care exports."""
    base = {"source_chat": "Служба заботы"}
    rows = [
        {"source_id":"sz:21661:решетняк-пу","source_at":"2026-11-16T11:07:53","birth":"2025-03-10","parent_name":"Кристина","phone":"+79636161967","create_if_missing":True,"trial_time":"10:00","student_name":"Решетняк Матвей","event_kind":"trial_result","event_date":"2026-11-16","came":True,"outcome":"purchased","subscription_months":["2026-11"],"note":"Пришёл на ПУ 16.11 в 10:00 и купил ноябрьский абонемент"},
        {"source_id":"sz:21662:никитина-пу","source_at":"2026-11-16T11:11:14","birth":"2025-03-05","parent_name":"Екатерина","phone":"+79260327334","source":"по рекомендации друзей, Кристина Решетняк","create_if_missing":True,"trial_time":"10:00","student_name":"Никитина Софья","event_kind":"trial_result","event_date":"2026-11-16","came":True,"outcome":"purchased","subscription_months":["2026-11","2026-12"],"note":"Пришла на ПУ 16.11 в 10:00 и купила занятия на ноябрь и первую половину декабря"},
        {"source_id":"sz:21788:киселев-пу","source_at":"2026-11-16T18:16:23","birth":"2025-04-01","parent_name":"Мария","phone":"+79191076540","source":"по рекомендации друзей, Кристина Решетняк","create_if_missing":True,"trial_time":"10:00","student_name":"Киселёв Илья","event_kind":"trial_result","event_date":"2026-11-16","came":True,"outcome":"purchased","subscription_months":["2026-11"],"note":"Пришёл на ПУ 16.11 в 10:00; в тот же день купили ноябрьский абонемент"},
        {"source_id":"sz:21993","source_at":"2026-11-17T19:20:21","birth":"2022-06-07","parent_name":"Айдана","phone":"89309822520","create_if_missing":True,"trial_time":"17:45","student_name":"Тамиров Арслан","event_kind":"trial_booking","event_date":"2026-11-20","note":"Записан на пробное 20.11 в 17:45","task_title":"Проверить результат пробного 20.11","task_due":"2026-11-20T12:00"},
        {"source_id":"sz:22809:trial-v2","source_at":"2026-11-22T10:32:29","birth":"2024-07-22","parent_name":"Мария","phone":"+79917016938","create_if_missing":True,"student_name":"Ворожцов Миша","event_kind":"trial_result","event_date":"2026-11-22","came":True,"note":"Пришёл и занимался; семья выбрала разовые занятия"},
        {"source_id":"sz:22809:single","source_at":"2026-11-22T10:32:29","birth":"2024-07-22","parent_name":"Мария","phone":"+79917016938","create_if_missing":True,"trial_time":"10:00","student_name":"Ворожцов Миша","event_kind":"single_sale","event_date":"2026-11-28","lesson_time":"10:00","amount":2500,"payment_method":"card_branch","paid_at":"2026-11-22T10:32:29","note":"Оплачено разовое занятие 28.11 в 10:00, 2500 ₽ картой на филиале"},
        {"source_id":"sz:22822","source_at":"2026-11-22T11:41:54","birth":"2025-02-01","parent_name":"Анастасия","phone":"+79164496354","source":"канал Футболики","create_if_missing":True,"trial_time":"10:30","student_name":"Рубайло Михаил","event_kind":"trial_booking","event_date":"2026-11-30","note":"Пробное перенесено на 30.11 в 10:30","task_title":"Подтвердить пробное 30.11 в 10:30","task_due":"2026-11-30T09:00"},
        {"source_id":"sz:22873","source_at":"2026-11-22T15:28:57","student_name":"Гунин Егор","phone":"+79263212580","event_kind":"trial_booking","event_date":"2026-11-28","note":"Записан на пробное 28.11 в 11:00","task_title":"Подтвердить пробное 28.11 в 11:00","task_due":"2026-11-28T09:00"},
        {"source_id":"sz:22884","source_at":"2026-11-22T17:16:40","student_name":"Евгений Сергиевский","event_kind":"makeup_booking","event_date":"2026-11-24","note":"Отработка назначена на 24.11 в 17:45; нужно напоминание","task_title":"Напомнить об отработке 24.11 в 17:45","task_due":"2026-11-24T09:00","erp_id":"182330","erp_create_if_missing":True,"erp_group_name":"Небо НУП ср/вс 16:45 возраст 3-4","erp_group_time":"16:45","erp_branch_hint":"Небо","clarification_task_title":"Заполнить данные семьи Сергиевского из ERP","clarification_task_note":"Карточка восстановлена по ERP ID 182330; дополнить семейные данные без создания дубля"},
        {"source_id":"sz:22886","source_at":"2026-11-22T19:58:03","birth":"2024-08-13","parent_name":"Евгения","phone":"89253530404","create_if_missing":True,"trial_time":"10:30","student_name":"Хвостов Саша","event_kind":"trial_booking","event_date":"2026-11-23","note":"Записан на пробное 23.11 в 10:30","task_title":"Проверить результат пробного 23.11","task_due":"2026-11-23T12:00"},
        {"source_id":"sz:22891:егоров-артур","source_at":"2026-11-22T20:32:01","birth":"2023-10-17","parent_name":"Октябрина","phone":"+79142001995","create_if_missing":True,"trial_time":"16:45","student_name":"Егоров Артур","event_kind":"trial_result","event_date":"2026-11-22","came":False,"outcome":"later","note":"На тренировку не вошёл; семья пока не готова повторять попытку","followup_due":"2027-02-21T11:00","followup_title":"Пригласить снова на ПУ: ребёнок будет готов","refusal_reason":"Ребёнок пока не готов войти в тренировку"},
        {"source_id":"sz:22891:микаладзе-леон","source_at":"2026-11-22T20:32:01","student_name":"Микаладзе Леон","event_kind":"trial_result","event_date":"2026-11-22","came":True,"outcome":"purchased","subscription_months":["2026-11","2026-12"],"note":"ПУ 22.11 в 16:45; куплены ноябрь и декабрь"},
        {"source_id":"sz:22891:марков-тимофей","source_at":"2026-11-22T20:32:01","birth":"2024-04-23","parent_name":"Вера","phone":"+79261775474","create_if_missing":True,"trial_time":"16:45","student_name":"Марков Тимофей","event_kind":"trial_result","event_date":"2026-11-22","came":True,"outcome":"later","note":"Решили подождать до весны; связаться в апреле","followup_title":"Связаться с семьёй весной и предложить подходящую группу"},
        {"source_id":"sz:22891:мастердеев-денис","source_at":"2026-11-22T20:32:01","birth":"2022-03-04","parent_name":"Наталия","phone":"+79859523765","create_if_missing":True,"trial_time":"17:45","student_name":"Мастердеев Денис","event_kind":"trial_result","event_date":"2026-11-22","came":True,"outcome":"purchased","subscription_months":["2026-11","2026-12"],"note":"ПУ 22.11 в 17:45; куплены ноябрь и декабрь"},
        {"source_id":"sz:22891:привалов-даня","source_at":"2026-11-22T20:32:01","student_name":"Привалов Даня","event_kind":"trial_result","event_date":"2026-11-22","came":True,"outcome":"later","note":"Семья решила вернуться к вопросу позже","followup_due":"2027-02-21T11:00","followup_title":"Пригласить снова на ПУ: ребёнок подрос","refusal_reason":"Семья пока не планирует покупку"},
        {"source_id":"sz:22926:хвостов-результат","source_at":"2026-11-23T11:31:14","birth":"2024-08-13","parent_name":"Евгения","phone":"+79253530404","create_if_missing":True,"student_name":"Хвостов Александр","event_kind":"trial_result","event_date":"2026-11-23","came":False,"outcome":"reinvite","note":"Пробное не состоялось: ребёнок не смог отойти от родителей","followup_due":"2026-11-30T11:00","followup_title":"Предложить адаптационное повторное ПУ с родителем"},
        {"source_id":"sz:22926:дюссек-результат","source_at":"2026-11-23T11:31:14","birth":"2024-01-26","parent_name":"Мария","phone":"+79105416473","create_if_missing":True,"trial_time":"10:30","student_name":"Дюссек Михаил","event_kind":"trial_result","event_date":"2026-11-23","came":True,"outcome":"purchased","subscription_months":["2026-12"],"note":"Пришёл на ПУ 23.11 в 10:30 и купил декабрьский абонемент"},
    ]
    return [dict(base, **row) for row in rows]


def events():
    return sale_events() + fact_events()


def apply(con):
    old = con.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    if old:
        try:
            saved = json.loads(old[0])
            if chatdaytransfer.saved_batch_can_skip(saved):
                return saved
        except (TypeError, ValueError):
            pass
    actor = con.execute("""SELECT id,name,role FROM users WHERE active=1 AND role='admin'
        AND trim(name)='Юлия Репина' ORDER BY id LIMIT 1""").fetchone()
    if not actor:
        return {"state": "not_target_database"}
    result = chatdaytransfer.apply(con, {"events": events()}, dict(actor))
    report = {"state": "complete" if chatdaytransfer.batch_is_complete(result) else "partial",
              "counts": {key: len(value) for key, value in result.items()},
              "unmatched": result["unmatched"], "ambiguous": result["ambiguous"], "errors": result["errors"]}
    con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)", (KEY, json.dumps(report, ensure_ascii=False)))
    return report


def report(con):
    row = con.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    return json.loads(row[0]) if row else {"state": "pending"}
