"""Idempotent early-November facts proved by the reviewed care export.

This batch records exact operational facts automatically.  A financial record
is created only when client, schedule, amount, period and payment method are all
proved; otherwise the exact fact and one clarification task are retained.
"""
import json

from . import chatdaytransfer


KEY = "targeted_verified_care_history_20261124_v1"


def events():
    chat = "Служба заботы"
    return [
        {"source_id":"sz:21367:власов-отработки","source_chat":chat,"source_at":"2026-11-13T19:06:17","student_name":"Власов Захар","event_kind":"makeup_booking","event_date":"2026-11-18","note":"Назначены отработки 18, 25 и 29 ноября в 17:45"},
        {"source_id":"sz:21473:марков-пу","source_chat":chat,"source_at":"2026-11-14T11:55:50","student_name":"Марков Тимофей","birth":"2024-04-23","parent_name":"Вера","phone":"89261775474","create_if_missing":True,"event_kind":"trial_booking","event_date":"2026-11-18","trial_time":"16:45","note":"Записан на ПУ 18.11 в 16:45","task_title":"Проверить результат ПУ 18.11","task_due":"2026-11-18T18:00"},
        {"source_id":"sz:21476:тупота-отсутствие","source_chat":chat,"source_at":"2026-11-14T12:07:26","student_name":"Тупота Сергей","event_kind":"absence","event_date":"2026-12-22","note":"Не будет на тренировке 22.12; абонемент с переносами"},
        {"source_id":"sz:21482:кудинов-пу","source_chat":chat,"source_at":"2026-11-14T12:22:17","student_name":"Кудинов Дима","birth":"2023-09-07","parent_name":"Кристина","phone":"+79167241136","source":"Яндекс","create_if_missing":True,"event_kind":"trial_booking","event_date":"2026-11-22","trial_time":"10:00","note":"ПУ на Чоботовской перенесено с 15.11 на 22.11 в 10:00","task_title":"Проверить результат ПУ 22.11","task_due":"2026-11-22T12:00"},
        {"source_id":"sz:21483:маракаткин-отработка","source_chat":chat,"source_at":"2026-11-14T12:54:42","student_name":"Маракаткин Дима","event_kind":"makeup_booking","event_date":"2026-11-18","note":"Пропускает 14.11; записан на отработку 18.11 в 19:45"},
        {"source_id":"sz:21513:ахмедов-пу","source_chat":chat,"source_at":"2026-11-14T19:11:14","student_name":"Ахмедов Али","birth":"2018-12-21","parent_name":"Анна","phone":"+79261300620","source":"Иван Ивакин 26","create_if_missing":True,"event_kind":"trial_booking","event_date":"2026-11-21","trial_time":"10:00","note":"Записан на ПУ 21.11 на Чоботовской","task_title":"Проверить результат ПУ 21.11","task_due":"2026-11-21T12:00"},
        {"source_id":"sz:21525:меньшиков-отработки","source_chat":chat,"source_at":"2026-11-15T10:23:40","student_name":"Меньшиков Павел","event_kind":"makeup_booking","event_date":"2026-11-21","note":"За пропуски 04.11 и 08.11 назначены отработки 21.11 и 28.11 в 10:00"},
        {"source_id":"sz:21543:абуталипов-ноябрь","source_chat":chat,"source_at":"2026-11-15T11:02:26","student_name":"Абуталипов Лев","event_kind":"subscription_sale","sale_date":"2026-11-01","paid_at":"2026-11-01T12:00:00","amount":12150,"payment_method":"paykeeper_manual","tariff":"fixed","lesson_time":"10:00","weekdays":[5,6],"components":[{"month":"2026-11","price":12150,"dates":["2026-11-01","2026-11-07","2026-11-08","2026-11-14","2026-11-15","2026-11-21","2026-11-22","2026-11-28","2026-11-29"]}],"note":"Ноябрьский абонемент Чоботовская, сб/вс 10:00, 12 150 ₽, оплачен 01.11 по PayKeeper"},
        {"source_id":"sz:21568:сафронов-отсутствие","source_chat":chat,"source_at":"2026-11-15T13:25:24","student_name":"Сафронов Владимир","event_kind":"absence","event_date":"2026-11-16","note":"Пропустит тренировку 16.11"},
        {"source_id":"sz:21577:михайленко-пу","source_chat":chat,"source_at":"2026-11-15T15:36:36","student_name":"Михайленко Михаил","event_kind":"trial_booking","event_date":"2026-11-15","trial_time":"18:45","note":"Записан на ПУ 15.11 в 18:45; источник — задачи 2025"},
        {"source_id":"sz:21578:печенкин-пу","source_chat":chat,"source_at":"2026-11-15T16:20:59","student_name":"Печенкин Артём","parent_name":"Анастасия","phone":"89261000207","event_kind":"trial_booking","event_date":"2026-12-07","trial_time":"10:00","note":"Была запись на ПУ 07.12 в 10:00; семья могла перенести на январь","task_title":"Уточнить актуальность ПУ и не отправлять лишние сообщения","task_due":"2026-12-01T12:00"},
        {"source_id":"sz:21579:шакиров-продажа","source_chat":chat,"source_at":"2026-11-15T17:17:49","student_name":"Шакиров Илья","event_kind":"sale_fact","event_date":"2026-11-15","note":"Подтверждена покупка гибкого абонемента на 4 занятия, Чоботовская сб/вс 10:00, скидка 20%, сумма 4 960 ₽; точные даты и способ оплаты в сообщении не указаны","clarification_task_title":"Уточнить даты и способ оплаты абонемента 4 960 ₽","clarification_task_note":"Не создавать вторую оплату; сначала проверить существующий абонемент и платёж"},
        {"source_id":"sz:21580:меджвел-пу","source_chat":chat,"source_at":"2026-11-15T18:47:20","student_name":"Меджвел Марк","event_kind":"trial_result","event_date":"2026-11-15","came":True,"outcome":"followup","note":"После ПУ в 17:45 нужно связаться с мамой по покупке; рекомендованы вт/пт 17:45 или сб/вс 12:00","followup_due":"2026-11-16T12:00","followup_title":"Связаться с мамой Марка по покупке абонемента"},
        {"source_id":"sz:21778:кушнарев-декабрь","source_chat":chat,"source_at":"2026-11-16T17:52:34","student_name":"Кушнарев Семен","event_kind":"sale_fact","event_date":"2026-11-16","note":"Подтверждена оплата декабрьского абонемента 13 800 ₽ наличными; деньги положены в конверт. Группа и даты в сообщении не указаны","clarification_task_title":"Определить группу и даты декабрьского абонемента 13 800 ₽","clarification_task_note":"Оплата уже получена наличными; не создавать повторный платёж без сверки карточки"},
        {"source_id":"sz:21815:патрушев-отработка","source_chat":chat,"source_at":"2026-11-16T19:19:15","student_name":"Патрушев Григорий","event_kind":"makeup_booking","event_date":"2026-11-22","note":"Отработка назначена на 22.11 в 13:00 вместо 19.11"},
        {"source_id":"sz:21863:киреева-разовое","source_chat":chat,"source_at":"2026-11-17T11:23:14","student_name":"Киреева Ксюша","event_kind":"sale_fact","event_date":"2026-11-17","note":"Подтверждено разовое занятие 17.11 в 19:45 стоимостью 2 500 ₽; способ оплаты в сообщении не указан","clarification_task_title":"Уточнить способ оплаты разового занятия 2 500 ₽","clarification_task_note":"Не создавать вторую оплату; сначала проверить карточку и существующий платёж"},
        {"source_id":"sz:22002:туманишвили-пу","source_chat":chat,"source_at":"2026-11-17T20:09:57","student_name":"Туманишвили Эрик","birth":"2024-07-07","phone":"+79108023909","event_kind":"trial_booking","event_date":"2026-12-07","trial_time":"10:30","note":"Записан на ПУ в ТРЦ Небо 07.12 в 10:30"},
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
    con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)",
                (KEY, json.dumps(report, ensure_ascii=False)))
    return report


def report(con):
    row = con.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    return json.loads(row[0]) if row else {"state": "pending"}
