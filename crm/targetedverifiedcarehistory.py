"""Idempotent early-November facts proved by the reviewed care export.

This batch records exact operational facts automatically.  A financial record
is created only when client, schedule, amount, period and payment method are all
proved; otherwise the exact fact and one clarification task are retained.
"""
import json

from . import chatdaytransfer


KEY = "targeted_verified_care_history_20261124_v5"


def early_events():
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


def later_events():
    chat = "Служба заботы"
    return [
        {"source_id":"sz:22117:юрков-пу","source_chat":chat,"source_at":"2026-11-18T12:13:56","student_name":"Юрков Лев","birth":"2021-05-01","parent_name":"Марк","phone":"+79523231764","source":"Telegram @yurkovmark","event_kind":"lead_fact","event_date":"2026-11-18","note":"Хотели прийти на ПУ 18.11 в 17:45 или 19:45; точное время в сообщении не подтверждено","clarification_task_title":"Уточнить, состоялось ли ПУ Льва Юркова 18.11","clarification_task_note":"Не путать с действующим клиентом Иваном Юрковым; телефон Льва +79523231764"},
        {"source_id":"sz:22224:хабибрахманов-пу","source_chat":chat,"source_at":"2026-11-18T20:46:17","student_name":"Хабибрахманов Малика","birth":"2022-06-02","phone":"+79177167165","source":"промо Никита","event_kind":"trial_booking","event_date":"2026-11-22","trial_time":"10:00","note":"Записан на ПУ 22.11 в 10:00 на Чоботовской"},
        {"source_id":"sz:22225:дюссек-пу","source_chat":chat,"source_at":"2026-11-18T20:54:27","student_name":"Дюссек Михаил","birth":"2024-01-26","parent_name":"Мария","phone":"89105416473","create_if_missing":True,"event_kind":"trial_booking","event_date":"2026-11-23","trial_time":"10:30","note":"Записан на ПУ 23.11 в 10:30","task_title":"Проверить результат ПУ 23.11","task_due":"2026-11-23T12:00"},
        {"source_id":"sz:22277:маматахунов-пу","source_chat":chat,"source_at":"2026-11-19T19:31:11","student_name":"Маматахунов Фирдавс","birth":"2023-11-10","parent_name":"Бобир","phone":"+79145882005","create_if_missing":True,"event_kind":"trial_booking","event_date":"2026-11-20","trial_time":"16:45","note":"Записан на ПУ 20.11 в 16:45; полное имя подтверждено последующим сообщением о покупке"},
        {"source_id":"sz:22281:привалов-пу","source_chat":chat,"source_at":"2026-11-19T19:35:13","student_name":"Привалов Даня","event_kind":"trial_booking","event_date":"2026-11-22","trial_time":"17:45","note":"Существующий клиент записан на ПУ 22.11 в 17:45"},
        {"source_id":"sz:22325:хасаханов-пу","source_chat":chat,"source_at":"2026-11-19T21:56:34","student_name":"Хасаханов Эмин","birth":"2022-09-24","phone":"+79995992010","source":"ранее ходили в Радужный","event_kind":"trial_booking","event_date":"2026-11-22","trial_time":"10:00","note":"Записан на ПУ 22.11 в 10:00"},
        {"source_id":"sz:22401:степаненко-отработка","source_chat":chat,"source_at":"2026-11-20T14:16:03","student_name":"Степаненко Даниил","event_kind":"makeup_booking","event_date":"2026-11-21","note":"За пропуск 19.11 назначена отработка 21.11 в 10:00"},
        {"source_id":"sz:22467:маматахунов-источник","source_chat":chat,"source_at":"2026-11-20T17:50:31","student_name":"Маматахунов Фирдавс","phone":"+79145882005","source":"ТРЦ Небо — шли в Детский мир и зашли в клуб","event_kind":"source_fact","event_date":"2026-11-20","note":"Рекламный источник подтверждён СЗ: семья шла в Детский мир и зашла в филиал ТРЦ Небо"},
        {"source_id":"sz:22516:сафронов-отработка","source_chat":chat,"source_at":"2026-11-20T19:15:45","student_name":"Сафронов Владимир","event_kind":"makeup_booking","event_date":"2026-11-22","note":"Отработка назначена на 22.11 в 17:45"},
        {"source_id":"sz:22537:рыбин-пу","source_chat":chat,"source_at":"2026-11-20T20:18:23","student_name":"Рыбин Арсен","event_kind":"trial_booking","event_date":"2026-11-30","trial_time":"10:30","note":"После пропущенного по болезни ПУ перезаписан на 30.11 в 10:30","task_title":"Подтвердить повторное ПУ 30.11","task_due":"2026-11-30T09:00"},
        {"source_id":"sz:22539:маматахунов-продажа","source_chat":chat,"source_at":"2026-11-20T20:20:06","student_name":"Маматахунов Фирдавс","birth":"2023-11-10","parent_name":"Бобир","phone":"+79145882005","create_if_missing":True,"event_kind":"subscription_sale","sale_date":"2026-11-20","paid_at":"2026-11-20T20:20:06","amount":11880,"payment_method":"tochka_qr_link","tariff":"fixed","branch_hint":"Небо","lesson_time":"16:45","weekdays":[1,4],"discount_percent":20,"components":[{"month":"2026-11","price":2160,"dates":["2026-11-24","2026-11-27"]},{"month":"2026-12","price":9720,"dates":["2026-12-01","2026-12-04","2026-12-08","2026-12-11","2026-12-15","2026-12-18","2026-12-22","2026-12-25","2026-12-29"]}],"note":"После ПУ куплены 11 занятий до конца декабря, вт/пт 16:45, 11 880 ₽ по ссылке Точки"},
        {"source_id":"sz:22544:недугов-пу","source_chat":chat,"source_at":"2026-11-20T20:25:24","student_name":"Недугов Макар","birth":"2024-04-24","phone":"+79266515801","source":"промо Никита 26","event_kind":"trial_booking","event_date":"2026-12-02","trial_time":"10:30","note":"Записан на ПУ 02.12 в 10:30 в ТРЦ Небо"},
        {"source_id":"sz:22567:ворожцов-перенос-пу","source_chat":chat,"source_at":"2026-11-21T10:22:40","student_name":"Ворожцов Миша","event_kind":"trial_booking","event_date":"2026-11-22","trial_time":"10:00","note":"21.11 ушёл с поля и не занимался; повторное ПУ назначено на 22.11 в 10:00"},
        {"source_id":"sz:22570:урюпина-перенос-пу","source_chat":chat,"source_at":"2026-11-21T10:45:08","student_name":"Урюпина Аглая","event_kind":"trial_booking","event_date":"2026-11-28","trial_time":"10:00","note":"Не пришла на ПУ 21.11 в 10:00; перезаписана на 28.11"},
        {"source_id":"sz:22577:лапин-переход","source_chat":chat,"source_at":"2026-11-21T11:24:34","student_name":"Лапин Иван","event_kind":"group_change","event_date":"2026-12-01","note":"С тренерами и мамой согласован переход с декабря из группы сб/вс 10:00 в группу 11:00; ноябрь заканчивает в прежней группе"},
        {"source_id":"sz:22582:яньков-декабрь","source_chat":chat,"source_at":"2026-11-21T12:42:19","student_name":"Яньков Демид","event_kind":"sale_fact","event_date":"2026-11-21","note":"Подтверждена оплата декабря 10 800 ₽ через Точку, Небо пн/чт 17:45; семья уезжает 23.12, остаток нужно перенести на баланс или январь","clarification_task_title":"Уточнить состав декабря и перенос остатка Янькова","clarification_task_note":"Оплата уже подтверждена; не создавать повторный платёж до проверки существующей записи"},
        {"source_id":"sz:22588:егоров-пу","source_chat":chat,"source_at":"2026-11-21T13:45:33","student_name":"Егоров Артур","birth":"2023-10-17","parent_name":"Октябрина","phone":"+79142001995","source":"Яндекс Карты, входящий звонок","create_if_missing":True,"event_kind":"trial_booking","event_date":"2026-11-22","trial_time":"16:45","note":"Записан на ПУ 22.11 в 16:45"},
        {"source_id":"sz:22592:литвинов-декабрь","source_chat":chat,"source_at":"2026-11-21T13:45:33","student_name":"Литвинов Ярослав","event_kind":"subscription_sale","sale_date":"2026-11-21","paid_at":"2026-11-21T13:45:33","amount":12150,"payment_method":"tochka_qr_link","tariff":"fixed","branch_hint":"Небо","lesson_time":"18:45","weekdays":[1,4],"components":[{"month":"2026-12","price":12150,"dates":["2026-12-01","2026-12-04","2026-12-08","2026-12-11","2026-12-15","2026-12-18","2026-12-22","2026-12-25","2026-12-29"]}],"note":"Продление на декабрь, Небо вт/пт 18:45, 12 150 ₽ по ссылке Точки"},
        {"source_id":"sz:22594:мастердеев-пу","source_chat":chat,"source_at":"2026-11-21T15:44:01","student_name":"Мастердеев Денис","birth":"2022-03-04","parent_name":"Наталия","phone":"+79859523765","create_if_missing":True,"event_kind":"trial_booking","event_date":"2026-11-22","trial_time":"17:45","note":"Записан на ПУ 22.11 в 17:45 в ТРЦ Небо"},
        {"source_id":"sz:22595:ременюк-разовое","source_chat":chat,"source_at":"2026-11-21T16:07:26","student_name":"Ременюк Максим","event_kind":"single_booking","event_date":"2026-11-28","note":"Записан на разовое занятие 28.11 на Чоботовской; занятие уже оплачено ранее"},
        {"source_id":"sz:22789:патрушев-отработка-перенос","source_chat":chat,"source_at":"2026-11-21T20:39:58","student_name":"Патрушев Григорий","event_kind":"makeup_booking","event_date":"2026-11-29","note":"Заболел и не сможет 22.11; отработка перенесена на 29.11 в 13:00"},
    ]


def source_events():
    """Ad sources confirmed anywhere in the reviewed care thread."""
    chat = "Служба заботы"
    rows = [
        ("sz:20167:крюкова-источник","2026-11-02T17:59:09","Крюкова Алена","+79152013018","промо Никита"),
        ("sz:20167:кыймаштаев-источник","2026-11-02T17:59:09","Кыймаштаев Дамир","+79858443323","промо Никита"),
        ("sz:20662:серегин-источник","2026-11-09T11:52:43","Серегин Глеб","","ТРЦ Небо"),
        ("sz:20167:муравицкий-источник","2026-11-02T17:59:09","Муравицкий Макар","+79060235909","промо Никита"),
        ("sz:20167:иванов-источник","2026-11-02T17:59:09","Иванов Дмитрий","+79153655645","Яндекс"),
        ("sz:21321:киселев-источник","2026-11-13T15:48:01","Киселёв Илья","+79191076540","ТРЦ Небо"),
        ("sz:21323:никитина-источник","2026-11-13T16:03:21","Никитина Софья","+79260327334","по рекомендации Кристины Решетняк"),
        ("sz:21577:михайленко-источник","2026-11-15T15:36:36","Михайленко Михаил","","задачи 2025"),
        ("sz:21989:рубайло-источник","2026-11-17T19:04:26","Рубайло Михаил","+79164496354","канал Футболики"),
        ("sz:22224:хабибрахманов-источник","2026-11-18T20:46:17","Хабибрахманов Малика","+79177167165","промо Никита"),
        ("sz:22544:недугов-источник","2026-11-20T20:25:24","Недугов Макар","+79266515801","промо Никита 26"),
        ("sz:22588:егоров-источник","2026-11-21T13:10:46","Егоров Артур","+79142001995","Яндекс Карты — входящий звонок"),
        ("sz:22647:бердов-источник","2026-11-21T18:17:52","Бердов Абос","89850208788","ТРЦ"),
        ("sz:22781:мастердеев-источник","2026-11-21T20:15:30","Мастердеев Денис","+79859523765","ТРЦ Небо"),
        ("sz:22873:гунин-источник","2026-11-22T15:28:57","Гунин Егор","+79263212580","Яндекс Карты"),
        ("sz:22886:хвостов-источник","2026-11-22T19:58:03","Хвостов Александр","+79253530404","ТРЦ"),
        ("sz:22891:микаладзе-источник","2026-11-22T20:32:01","Микаладзе Леон","","по рекомендации друга Мамедова Дани"),
    ]
    return [{"source_id":source_id,"source_chat":chat,"source_at":at,"student_name":name,
             "phone":phone,"source":source,"event_kind":"source_fact","event_date":at[:10],
             "note":"Рекламный источник подтверждён СЗ: "+source}
            for source_id,at,name,phone,source in rows]


def events():
    return early_events() + later_events() + source_events()


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
