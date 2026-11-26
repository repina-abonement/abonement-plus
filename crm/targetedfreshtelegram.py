"""Reviewed client facts from the incremental care-chat export, 23–24 Nov 2026."""
import json

from . import chatdaytransfer, money, pricing, services, tasks


KEY = "targeted_fresh_telegram_20261124_v10"
DANEYKIN_DECEMBER_TASK = "chat-day:telegram:sz-23018:daneykin"
POGOSYAN_DECEMBER_TASK = "chat-day:telegram:sz-23043:pogosyan"

TRAINER_ATTENDANCE = (
    ("Илья Морозов", "16:45"), ("Михаил Морозов", "16:45"),
    ("Максим Магин", "16:45"), ("Даниил Степаненко", "16:45"),
    ("Владимир Прохоренко", "17:45"), ("Владимир Сафронов", "17:45"),
    ("Кирилл Мун", "17:45"), ("Вася Артамонов", "17:45"),
    ("Демид Яньков", "17:45"), ("Дмитрий Королёв", "17:45"),
    ("Даниил Бородин", "18:45"), ("Антон Деменев", "18:45"),
    ("Егор Чугунов", "18:45"), ("Арсений Горбачёв", "18:45"),
    ("Максим Мастеренко", "18:45"), ("Арсений Решетняк", "18:45"),
    ("Родион Варновский", "18:45"), ("Виктор Данейкин", "19:45"),
    ("Даниил Мамедов", "19:45"), ("Алексей Калинин", "19:45"),
    ("Михаил Гущо", "19:45"), ("Илья Оруджов", "19:45"),
    ("Даниил Тарасюк", "19:45"), ("Андрей Лубяницкий", "19:45"),
    ("Артем Кожевников", "19:45"),
)

ERP_PROFILES = {
    "Артем Кожевников": dict(erp_id="87888", birth="2019-11-28", parent_name="Елена", phone="+79852611161", email="kozhevnikovmm@mail.ru", erp_status="Постоянный посетитель", group="СТОП Небо ПУП пн/чт/сб 19:45 возраст 6-8", history=[dict(at="2026-11-02T00:00",text="абонемент, группа СТОП Небо ПУП пн/чт/сб 19:45, период 02.11–30.11")]),
    "Даниил Бородин": dict(erp_id="96240", birth="2020-12-08", parent_name="Анна", phone="+79259367897", erp_status="Постоянный посетитель", group="СТОП Небо ПУП пн/чт/сб 18:45 возраст 5-6", history=[dict(at="2026-11-02T00:00",text="абонемент, группа СТОП Небо ПУП пн/чт/сб 18:45, период 02.11–30.11")]),
    "Антон Деменев": dict(erp_id="103975", birth="2020-05-28", parent_name="Майя", phone="+79854352626", erp_status="Постоянный посетитель", group="СТОП Небо ПУП пн/чт/сб 18:45 возраст 5-6", history=[dict(at="2026-11-01T00:00",text="абонемент, группа СТОП Небо ПУП пн/чт/сб 18:45, период 02.11–30.11")]),
    "Иван Черных": dict(erp_id="133160", birth="2016-04-15", parent_name="Елена", phone="+79601333926", erp_status="Постоянный посетитель", group="Чоботовская сб/вс 6-8", history=[dict(at="2026-11-07T00:00",text="абонемент, группа Чоботовская сб/вс 6-8, период 07.11–29.11")]),
    "Илья Морозов": dict(erp_id="130752", birth="2021-07-08", parent_name="Наталья", phone="+79032090955", erp_status="Постоянный посетитель", group="Небо ПУП пн/чт/сб 16:45 возраст 3-4", history=[dict(at="2026-11-02T00:00",text="абонемент, группа Небо ПУП пн/чт/сб 16:45, период 02.11–30.11")]),
    "Михаил Морозов": dict(name="Миша Морозов", erp_id="130753", birth="2022-11-25", parent_name="Наталья", phone="+79032090955", erp_status="Постоянный посетитель", group="Небо ПУП пн/чт/сб 16:45 возраст 3-4", history=[dict(at="2026-11-02T00:00",text="абонемент, группа Небо ПУП пн/чт/сб 16:45, период 02.11–30.11")]),
    "Дмитрий Королёв": dict(erp_id="80953", birth="2021-04-22", parent_name="Наталья", phone="+79261834378", email="nsavkina81@mail.ru", erp_status="Был на пробном, не купил абонемент", group="СТОП Небо ПУП пн/чт/сб 17:45 возраст 4-5"),
    "Владимир Прохоренко": dict(name="Вова Прохоренко", erp_id="128476", birth="2022-03-07", parent_name="Наталья", phone="+79264191683", erp_status="Постоянный посетитель", group="СТОП Небо ПУП пн/чт/сб 17:45 возраст 4-5", history=[dict(at="2026-11-02T00:00",text="абонемент, группа СТОП Небо ПУП пн/чт/сб 17:45, период 02.11–30.11")]),
    "Кирилл Мун": dict(erp_id="101579", birth="2022-03-04", parent_name="Дарья", phone="+79161108711", erp_status="Постоянный посетитель", group="СТОП Небо ПУП пн/чт/сб 17:45 возраст 4-5", history=[dict(at="2026-11-02T00:00",text="абонемент, группа СТОП Небо ПУП пн/чт/сб 17:45, период 02.11–30.11")]),
    "Вася Артамонов": dict(erp_id="112091", birth="2021-09-09", parent_name="Анастасия", phone="+79100876088", erp_status="Постоянный посетитель", group="СТОП Небо ПУП пн/чт/сб 17:45 возраст 4-5", history=[dict(at="2026-11-02T00:00",text="абонемент, группа СТОП Небо ПУП пн/чт/сб 17:45, период 02.11–30.11")]),
    "Владимир Сафронов": dict(erp_id="111692", birth="2022-03-26", parent_name="Виктория", phone="+79015138399", erp_status="Постоянный посетитель", group="СТОП Небо ПУП пн/чт/сб 17:45 возраст 4-5", history=[dict(at="2026-11-02T00:00",text="абонемент, группа СТОП Небо ПУП пн/чт/сб 17:45, период 02.11–30.11")]),
    "Арсений Горбачёв": dict(name="Арсений Горбачев", erp_id="116282", birth="2022-08-23", parent_name="Ксения", phone="+79998611901", erp_status="Постоянный посетитель", group="СТОП Небо ПУП пн/чт/сб 17:45 возраст 4-5", history=[dict(at="2026-11-02T00:00",text="абонемент, группа СТОП Небо ПУП пн/чт/сб 17:45, период 02.11–30.11")]),
    "Максим Мастеренко": dict(erp_id="83867", birth="2020-03-27", parent_name="Анна", phone="+79030083078", erp_status="Постоянный посетитель", group="СТОП Небо ПУП пн/чт/сб 18:45 возраст 5-6"),
    "Родион Варновский": dict(erp_id="86488", birth="2021-08-27", parent_name="Артем", phone="+79267081022", email="varnovsky@yandex.ru", erp_status="", group="СТОП Небо ПУП пн/чт/сб 18:45 возраст 5-6"),
    "Егор Чугунов": dict(erp_id="129379", birth="2020-12-24", parent_name="Гузель", phone="+79629171342", erp_status="Постоянный посетитель", group="СТОП Небо ПУП пн/чт/сб 18:45 возраст 5-6", history=[dict(at="2026-11-02T00:00",text="абонемент, группа СТОП Небо ПУП пн/чт/сб 18:45, период 02.11–30.11")]),
    "Илья Оруджов": dict(erp_id="103859", birth="2020-08-28", parent_name="Анастасия", phone="+79037350736", erp_status="Постоянный посетитель", group="СТОП Небо ПУП пн/чт/сб 19:45 возраст 6-8", history=[dict(at="2026-11-02T00:00",text="абонементы: СТОП Небо ПУП пн/чт/сб 19:45, период 02.11–30.11; Небо НУП ср/вс 19:45, период 04.11–25.11; отработка 14.11")]),
    "Андрей Лубяницкий": dict(erp_id="68549", birth="2019-11-15", parent_name="Ирина", phone="+79272738446", email="lub-irina@yandex.ru", erp_status="Нет абонемента", group="СТОП Небо ПУП пн/чт/сб 19:45 возраст 6-8"),
    "Алексей Калинин": dict(erp_id="110372", birth="2018-11-27", parent_name="Калинина Кристина Сергеевна", phone="+79264777614", erp_status="Постоянный посетитель", group="СТОП Небо ПУП пн/чт/сб 19:45 возраст 6-8", history=[dict(at="2026-11-01T00:00",text="абонемент, группа СТОП Небо ПУП пн/чт/сб 19:45, период 02.11–30.11")]),
    "Виктор Данейкин": dict(erp_id="88688", birth="2020-04-06", parent_name="Татьяна", phone="+79265241538", email="tiana2003@bk.ru", erp_status="Постоянный посетитель", group="СТОП Небо ПУП пн/чт/сб 18:45 возраст 5-6", history=[dict(at="2026-11-02T00:00",text="абонемент, группа СТОП Небо ПУП пн/чт/сб 18:45, период 02.11–30.11")]),
    "Михаил Гущо": dict(erp_id="117687", birth="2020-02-28", parent_name="Наталия", phone="+79645669815", erp_status="Постоянный посетитель", group="СТОП Небо ПУП пн/чт/сб 19:45 возраст 6-8", history=[dict(at="2026-11-02T00:00",text="абонемент, группа СТОП Небо ПУП пн/чт/сб 19:45, период 02.11–30.11")]),
    "Даниил Мамедов": dict(erp_id="88202", birth="2020-01-28", parent_name="Анастасия", phone="+79778500815", email="Stasia_93@inbox.ru", erp_status="Постоянный посетитель", group="СТОП Небо ПУП пн/чт/сб 19:45 возраст 6-8", history=[dict(at="2026-11-12T00:00",text="абонемент, группа СТОП Небо ПУП пн/чт/сб 19:45, период 12.11–30.11")]),
    "Леон Погосян": dict(erp_id="105641", birth="2020-11-19", parent_name="Лена", phone="+79613348693", erp_status="Постоянный посетитель", group="Небо НУП сб/вс 12:00 возраст 5-6", history=[dict(at="2026-11-01T00:00",text="абонемент, группа Небо НУП сб/вс 12:00, период 01.11–29.11; отработка 04.11")]),
}


def _event(message, key, at, name, note, task_title="", task_due=""):
    event = {"source_id": f"telegram:sz-{message}:{key}", "source_chat": "Служба заботы",
             "source_at": at, "student_name": name, "event_kind": "note", "note": note}
    if task_title:
        event.update(task_title=task_title, task_due=task_due,
                     task_note="Проверенный факт из СЗ: " + note)
    profile = ERP_PROFILES.get(name)
    if profile:
        event.update(erp_create_if_missing=True, erp_id=profile["erp_id"],
                     erp_group_name=profile.get("group", ""), erp_profile=profile)
    return event


def events():
    return [
        _event(22959,"borodin","2026-11-23T13:16:43","Даниил Бородин",
               "СЗ: подтверждено продление на декабрь Пн/Чт/Сб за 13 800 ₽ через Точка Банк; электронный чек позднее подтверждён сообщением 22977",
               "Оформить подтверждённый декабрьский абонемент", "2026-11-24T23:59"),
        _event(22961,"demenev","2026-11-23T13:22:06","Антон Деменев",
               "СЗ: подтверждено продление на декабрь Пн/Чт/Сб, 12 занятий, 13 800 ₽ через Точка Банк; электронный чек подтверждён",
               "Оформить подтверждённый декабрьский абонемент", "2026-11-24T23:59"),
        _event(22966,"golushko","2026-11-23T15:14:35","Егор Голушко",
               "СЗ: Егор Голушко, дата рождения 08.04.2023, записан на пробное 29.11 в 16:45; рекламный источник — ТРЦ Небо, семья зашла и увидела филиал. Имя родителя и телефон в доступных источниках не найдены"),
        _event(22969,"chernykh","2026-11-23T15:22:04","Иван Черных",
               "СЗ: подтверждено продление на декабрь в Чоботовской, гибкий абонемент 6 тренировок; 26–27 декабря уезжают; оплачено 7 950 ₽ через Точка Банк с зачётом отменённого занятия 1 350 ₽; чек подтверждён",
               "Оформить подтверждённый декабрьский абонемент", "2026-11-24T23:59"),
        _event(22981,"morozov_ilya","2026-11-23T17:04:30","Илья Морозов",
               "СЗ: продление на декабрь в группе 16:45, карточная оплата 10 800 ₽. В сообщении дети и суммы перечислены в одном порядке: Илья — 10 800 ₽, Миша — 8 640 ₽ со скидкой 20% за второго ребёнка",
               "Сверить даты декабрьского абонемента Ильи", "2026-11-24T23:59"),
        _event(22981,"morozov_mikhail","2026-11-23T17:04:30","Михаил Морозов",
               "СЗ: продление на декабрь в группе 16:45, карточная оплата 8 640 ₽ после скидки 20% за второго ребёнка. В сообщении дети и суммы перечислены в одном порядке: Илья — 10 800 ₽, Миша — 8 640 ₽",
               "Сверить даты декабрьского абонемента Миши", "2026-11-24T23:59"),
        _event(22981,"stepanenko","2026-11-23T17:04:30","Даниил Степаненко",
               "СЗ: при покупке сразу оформлялись абонементы на ноябрь и декабрь; сумма в сообщении не указана",
               "Проверить оформление декабрьского абонемента", "2026-11-24T23:59"),
        _event(22981,"magin","2026-11-23T17:04:30","Максим Магин",
               "СЗ: семья обещала оплатить 25.11 по ссылке",
               "Проверить оплату по ссылке", "2026-11-25T23:59"),
        _event(22997,"korolev","2026-11-23T18:16:12","Дмитрий Королёв",
               "СЗ: семья решает продление после ответа по месту в группе Пн/Чт 18:45; тренер считает этот вариант неподходящим; решение принимает мама",
               "Согласовать подходящую группу и продление", "2026-11-24T23:59"),
        _event(22997,"prokhorenko","2026-11-23T18:16:12","Владимир Прохоренко",
               "СЗ: ссылка на оплату есть, семья обещала оплатить 25.11",
               "Проверить оплату по ссылке", "2026-11-25T23:59"),
        _event(22997,"mun","2026-11-23T18:16:12","Кирилл Мун",
               "СЗ: ребёнка привела няня; нужно написать маме об оплате",
               "Написать маме об оплате", "2026-11-24T23:59"),
        _event(22997,"artamonov","2026-11-23T18:16:12","Вася Артамонов",
               "СЗ: семья сомневается в продлении; причину выяснить не удалось, мама отвечала односложно",
               "Выяснить причину сомнений и отработать продление", "2026-11-24T23:59"),
        _event(22997,"safronov","2026-11-23T18:16:12","Владимир Сафронов",
               "СЗ №22919 и №22997: декабрь Пн/Чт/Сб 17:45, 12 650 ₽, оплата подтверждена; точный состав дат и исключений не указан",
               "Сверить только даты декабря 12 650 ₽", "2026-11-24T23:59"),
        _event(23001,"gorbachev","2026-11-23T19:43:03","Арсений Горбачёв",
               "СЗ: папа сообщил, что абонемент продлили; самостоятельного подтверждения оплаты в сообщении нет",
               "Проверить продление и оплату", "2026-11-24T23:59"),
        _event(23001,"masterenko","2026-11-23T19:43:03","Максим Мастеренко",
               "СЗ: семья просит ссылку на оплату",
               "Отправить ссылку на оплату", "2026-11-24T23:59"),
        _event(23001,"varnovsky","2026-11-23T19:43:03","Родион Варновский",
               "СЗ: семье индивидуально согласована оплата 26.11 без повышения 5%; нужно связаться и спокойно подтвердить условия и оферту",
               "Связаться и подтвердить исключение по оплате", "2026-11-24T23:59"),
        _event(23001,"chugunov","2026-11-23T19:43:03","Егор Чугунов",
               "СЗ: ребёнок сомневается, хочет ли заниматься; мама хочет продолжать. Договорились написать семье 25.11",
               "Связаться с семьёй по продлению", "2026-11-25T23:59"),
        _event(23018,"orudzhov","2026-11-23T20:45:42","Илья Оруджов",
               "СЗ: карточная оплата 9 450 ₽, негибкий декабрьский абонемент, исключено 28.12",
               "Сверить только даты декабря 9 450 ₽", "2026-11-24T23:59"),
        _event(23018,"tarasyuk","2026-11-23T20:45:42","Даниил Тарасюк",
               "СЗ: папа обещал оплатить по ссылке 25.11; предположение об оплате во время тренировки не подтвердилось",
               "Проверить оплату по ссылке", "2026-11-25T23:59"),
        _event(23018,"lubyanitsky","2026-11-23T20:45:42","Андрей Лубяницкий",
               "СЗ: мама просит ссылку с вводом данных карты, не СБП; сотрудник не нашёл карточку через поиск CRM",
               "Найти карточку и отправить ссылку для оплаты картой", "2026-11-24T23:59"),
        _event(23018,"kalinin","2026-11-23T20:45:42","Алексей Калинин",
               "СЗ: семья планирует оплатить по ссылке",
               "Проверить отправку ссылки и оплату", "2026-11-25T23:59"),
        _event(23018,"daneykin","2026-11-23T20:45:42","Виктор Данейкин",
               "СЗ: оплачено картой 11 500 ₽, негибкий декабрьский абонемент, исключено 28.12",
               "Оформить подтверждённый декабрьский абонемент", "2026-11-24T23:59"),
        _event(23018,"gushcho","2026-11-23T20:45:42","Михаил Гущо",
               "СЗ: папы не было на тренировке, ребёнка забрали и ушли; оплату не обсудили",
               "Связаться по продлению и оплате", "2026-11-24T23:59"),
        _event(23018,"mamedov","2026-11-23T20:45:42","Даниил Мамедов",
               "СЗ: папы не было на тренировке, ребёнка забрали и ушли; оплату не обсудили",
               "Связаться по продлению и оплате", "2026-11-24T23:59"),
        _event(23043,"pogosyan","2026-11-24T10:47:22","Леон Погосян",
               "СЗ: подтверждено продление на декабрь, группа Сб/Вс 12:00, 8 занятий, 9 720 ₽ через Точка Банк; чек поручено пробить",
               "Оформить подтверждённый декабрьский абонемент", "2026-11-24T23:59"),
    ]


def _trainer_attendance(con):
    """Apply only the 24 full-name rows explicitly listed in trainer report 27148."""
    added = already = 0
    unresolved = []
    for name, tm in TRAINER_ATTENDANCE:
        profile = ERP_PROFILES.get(name, {})
        lookup_name = str(profile.get("name") or name)
        if profile.get("erp_id"):
            rows = con.execute("SELECT id,name FROM students WHERE archived=0 AND erp_id=?",
                               (str(profile["erp_id"]),)).fetchall()
        else:
            rows = []
        if not rows:
            rows = con.execute("""SELECT id,name FROM students WHERE archived=0
                AND (lower(trim(name))=lower(?) OR lower(trim(name))=lower(?))""",
                (lookup_name, " ".join(reversed(lookup_name.split())))).fetchall()
        if len(rows) != 1:
            unresolved.append({"name": name, "reason": "карточка не найдена однозначно"})
            continue
        sid = rows[0]["id"]
        exists = con.execute("""SELECT 1 FROM attendance
            WHERE student_id=? AND date='2026-11-23' AND status='present'""", (sid,)).fetchone()
        exists = exists or con.execute("""SELECT 1 FROM erp_history_visits
            WHERE student_id=? AND visit_date='2026-11-23' AND visit_time=? AND status='present'""",
            (sid, tm)).fetchone()
        exists = exists or con.execute("""SELECT 1 FROM erp_history_unassigned_visits
            WHERE student_id=? AND visit_date='2026-11-23' AND visit_time=? AND status='present'""",
            (sid, tm)).fetchone()
        if exists:
            already += 1
            continue
        source_id = "trainers:27148:" + str(profile.get("erp_id") or sid)
        payload = json.dumps({"source": "ТРЕНЕРЫ", "message_id": "27148", "confirmed": True}, ensure_ascii=False)
        con.execute("""INSERT OR IGNORE INTO erp_history_unassigned_visits
            (source_id,source_subscription_id,student_id,visit_date,visit_time,kind,status,source_group,source_payload,imported_at,imported_by)
            VALUES (?,NULL,?,'2026-11-23',?,'subscription','present',?,?,?,?)""",
            (source_id, sid, tm, f"ПУП, группа {tm}", payload, "2026-11-23T22:12:23", "Перенос ТРЕНЕРЫ"))
        if con.execute("SELECT changes()").fetchone()[0]:
            added += 1
        else:
            already += 1
    # The report also says one more child in the 18:45 total, but does not name
    # that child.  Never turn a headcount discrepancy into a guessed visit.
    unresolved.append({"name": "Не названный ребёнок 18:45", "reason": "в отчёте указано 8, перечислено 7"})
    varya = con.execute("""SELECT id FROM users WHERE active=1 AND role='operator'
        AND (name LIKE '%Варвар%' OR trim(name)='Варя') ORDER BY is_senior DESC,id LIMIT 1""").fetchone()
    varya_id = varya["id"] if varya else None
    task_key = "trainers:27148:identify-missing-clients"
    title = "Уточнить восьмого ребёнка группы 18:45"
    note = ("В отчёте тренера №27148 за 23.11 в сводке указано 8 детей в группе 18:45, "
            "но перечислены только 7: Бородин, Деменев, Чугунов, Горбачёв, Мастеренко, Решетняк и Варновский. "
            "Уточнить восьмого и привязать отметку посещения 23.11 18:45 к правильной карточке.")
    tasks.add_task(con, "manual", None, "2026-11-24T23:59", auto_key=task_key,
        title=title, note=note, source="manual", assignee=varya_id, shared=not bool(varya_id))
    task = con.execute("SELECT id FROM tasks WHERE auto_key=?", (task_key,)).fetchone()
    if task:
        con.execute("""UPDATE tasks SET title=?,note=?,due_at='2026-11-24T23:59',assignee_id=?
            WHERE id=? AND status='open'""", (title, note, varya_id, task["id"]))
    return {"added": added, "already": already, "unresolved": unresolved,
            "task_id": task["id"] if task else None, "assignee_id": varya_id}


def _finish_daneykin_task(con, actor):
    row = con.execute("SELECT id,status FROM tasks WHERE auto_key=? ORDER BY id DESC LIMIT 1",
                      (DANEYKIN_DECEMBER_TASK,)).fetchone()
    if row and row["status"] == "open":
        tasks.complete(con, row["id"], actor,
                       result="Декабрьский абонемент и карточная оплата 11 500 ₽ оформлены")


def _daneykin_december_sale(con, actor):
    """Apply the exact sale from care message 23018 without duplicate money."""
    matches = con.execute("""SELECT id FROM students WHERE archived=0
        AND (erp_id='88688' OR trim(name) IN ('Виктор Данейкин','Данейкин Виктор')) ORDER BY id""").fetchall()
    if len(matches) != 1:
        return {"state": "blocked", "reason": f"Карточка Данейкина определяется неоднозначно: {len(matches)} совпадений"}
    sid = matches[0]["id"]
    current = con.execute("""SELECT id,price,status,lessons,excluded_dates FROM subscriptions
        WHERE student_id=? AND month='2026-12' AND status!='cancelled' AND COALESCE(kind,'month')='month'
        ORDER BY id""", (sid,)).fetchall()
    if len(current) > 1:
        return {"state": "blocked", "student_id": sid,
                "reason": "Уже существует несколько декабрьских абонементов"}
    if current:
        sub = current[0]
        covered = money.balance(con, sid)["covered"].get(sub["id"], 0)
        if sub["price"] == 11500 and covered >= 11500 and sub["status"] == "paid":
            _finish_daneykin_task(con, actor)
            return {"state": "already_complete", "student_id": sid, "subscription_id": sub["id"]}
        return {"state": "blocked", "student_id": sid,
                "reason": f"Найден другой декабрьский абонемент #{sub['id']}"}
    groups = con.execute("""SELECT id,name FROM groups WHERE active=1 AND time='19:45'
        AND name LIKE '%ПУП%' ORDER BY id""").fetchall()
    if len(groups) != 1:
        return {"state": "blocked", "student_id": sid,
                "reason": f"Группа ПУП 19:45 определяется неоднозначно: {len(groups)} совпадений"}
    con.execute("SAVEPOINT daneykin_december_sale")
    try:
        invoice = services.create_invoice(con, sid, "2026-12", actor, tariff="fixed",
            excluded_dates=["2026-12-28"], group_id=groups[0]["id"], group_action="add",
            paid_date="2026-11-23", exclusion_reason="СЗ №23018: исключено 28.12")
        sub_id = invoice["id"]
        sub = con.execute("SELECT price FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
        if sub["price"] != 11500:
            pricing.correct(con, sub_id, 11500,
                "СЗ №23018 подтверждает негибкий декабрьский абонемент за 11 500 ₽", actor)
        payment_id = money.pay_subscription(con, sub_id, "card_branch", actor, amount=11500,
                                            paid_at="2026-11-23T20:45:42")
        verified = con.execute("SELECT price,status,excluded_dates FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
        payment = con.execute("SELECT amount,method,cancelled FROM payments WHERE id=?", (payment_id,)).fetchone()
        exclusions = json.loads(verified["excluded_dates"] or "[]")
        if verified["price"] != 11500 or verified["status"] != "paid" or "2026-12-28" not in exclusions:
            raise ValueError("Итоговая проверка абонемента Данейкина не пройдена")
        if tuple(payment) != (11500, "card_branch", 0):
            raise ValueError("Итоговая проверка оплаты Данейкина не пройдена")
        _finish_daneykin_task(con, actor)
        con.execute("RELEASE daneykin_december_sale")
        return {"state": "created", "student_id": sid, "subscription_id": sub_id, "payment_id": payment_id}
    except Exception as error:
        con.execute("ROLLBACK TO daneykin_december_sale")
        con.execute("RELEASE daneykin_december_sale")
        return {"state": "blocked", "student_id": sid, "reason": str(error)}


def _pogosyan_december_sale(con, actor):
    """Apply the exact eight-lesson sale from care message 23043."""
    matches = con.execute("""SELECT id FROM students WHERE archived=0
        AND (erp_id='105641' OR trim(name) IN ('Леон Погосян','Погосян Леон')) ORDER BY id""").fetchall()
    if len(matches) != 1:
        return {"state": "blocked", "reason": f"Карточка Погосяна определяется неоднозначно: {len(matches)} совпадений"}
    sid = matches[0]["id"]
    current = con.execute("""SELECT id,price,status,lessons FROM subscriptions
        WHERE student_id=? AND month='2026-12' AND status!='cancelled' AND COALESCE(kind,'month')='month'
        ORDER BY id""", (sid,)).fetchall()
    if len(current) > 1:
        return {"state": "blocked", "student_id": sid, "reason": "Уже существует несколько декабрьских абонементов"}
    if current:
        sub = current[0]
        covered = money.balance(con, sid)["covered"].get(sub["id"], 0)
        if (sub["price"], sub["lessons"], sub["status"]) == (9720, 8, "paid") and covered >= 9720:
            task = con.execute("SELECT id,status FROM tasks WHERE auto_key=? ORDER BY id DESC LIMIT 1",
                               (POGOSYAN_DECEMBER_TASK,)).fetchone()
            if task and task["status"] == "open":
                tasks.complete(con, task["id"], actor, result="Декабрьский абонемент и оплата 9 720 ₽ оформлены")
            return {"state": "already_complete", "student_id": sid, "subscription_id": sub["id"]}
        return {"state": "blocked", "student_id": sid, "reason": f"Найден другой декабрьский абонемент #{sub['id']}"}
    groups = con.execute("""SELECT id,name FROM groups WHERE active=1 AND is_makeup=0 AND time='12:00'
        AND weekdays LIKE '%5%' AND weekdays LIKE '%6%' ORDER BY id""").fetchall()
    exact = [row for row in groups if "Небо" in row["name"] and ("5-6" in row["name"] or "сб/вс" in row["name"].lower())]
    if len(exact) != 1:
        return {"state": "blocked", "student_id": sid,
                "reason": f"Группа Небо сб/вс 12:00 определяется неоднозначно: {len(exact)} совпадений"}
    con.execute("SAVEPOINT pogosyan_december_sale")
    try:
        invoice = services.create_invoice(con, sid, "2026-12", actor, tariff="fixed",
            excluded_dates=[], group_id=exact[0]["id"], group_action="add", paid_date="2026-11-24",
            exclusion_reason="СЗ №23043: подтверждены все 8 декабрьских занятий")
        sub_id = invoice["id"]
        sub = con.execute("SELECT price,lessons FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
        if sub["lessons"] != 8:
            raise ValueError("Календарь не дал восемь занятий Погосяна")
        if sub["price"] != 9720:
            pricing.correct(con, sub_id, 9720, "СЗ №23043 подтверждает согласованную стоимость 9 720 ₽", actor)
        payment_id = money.pay_subscription(con, sub_id, "tochka_qr_link", actor, amount=9720,
                                            paid_at="2026-11-24T10:47:22")
        verified = con.execute("SELECT price,lessons,status FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
        payment = con.execute("SELECT amount,method,cancelled FROM payments WHERE id=?", (payment_id,)).fetchone()
        if tuple(verified) != (9720, 8, "paid") or tuple(payment) != (9720, "tochka_qr_link", 0):
            raise ValueError("Итоговая проверка продажи Погосяна не пройдена")
        task = con.execute("SELECT id,status FROM tasks WHERE auto_key=? ORDER BY id DESC LIMIT 1",
                           (POGOSYAN_DECEMBER_TASK,)).fetchone()
        if task and task["status"] == "open":
            tasks.complete(con, task["id"], actor, result="Декабрьский абонемент и оплата 9 720 ₽ оформлены")
        con.execute("RELEASE pogosyan_december_sale")
        return {"state": "created", "student_id": sid, "subscription_id": sub_id, "payment_id": payment_id}
    except Exception as error:
        con.execute("ROLLBACK TO pogosyan_december_sale")
        con.execute("RELEASE pogosyan_december_sale")
        return {"state": "blocked", "student_id": sid, "reason": str(error)}


def _chernykh_december_sale(con, actor):
    """Apply the exact six-lesson sale from care message 22969."""
    matches = con.execute("""SELECT id FROM students WHERE archived=0
        AND (erp_id='133160' OR trim(name) IN ('Иван Черных','Черных Иван')) ORDER BY id""").fetchall()
    if len(matches) != 1:
        return {"state": "blocked", "reason": f"Карточка Черных определяется неоднозначно: {len(matches)} совпадений"}
    sid = matches[0]["id"]
    current = con.execute("""SELECT id,price,status,lessons FROM subscriptions
        WHERE student_id=? AND month='2026-12' AND status!='cancelled' AND COALESCE(kind,'month')='month'
        ORDER BY id""", (sid,)).fetchall()
    if len(current) > 1:
        return {"state": "blocked", "student_id": sid, "reason": "Уже существует несколько декабрьских абонементов"}
    if current:
        sub = current[0]
        covered = money.balance(con, sid)["covered"].get(sub["id"], 0)
        if (sub["price"], sub["lessons"], sub["status"]) == (7950, 6, "paid") and covered >= 7950:
            task = con.execute("SELECT id,status FROM tasks WHERE auto_key=? ORDER BY id DESC LIMIT 1",
                               ("chat-day:telegram:sz-22969:chernykh",)).fetchone()
            if task and task["status"] == "open":
                tasks.complete(con, task["id"], actor, result="Декабрьский абонемент и оплата 7 950 ₽ оформлены")
            return {"state": "already_complete", "student_id": sid, "subscription_id": sub["id"]}
        return {"state": "blocked", "student_id": sid, "reason": f"Найден другой декабрьский абонемент #{sub['id']}"}
    groups = con.execute("""SELECT id,name FROM groups WHERE active=1 AND is_makeup=0
        AND weekdays LIKE '%5%' AND weekdays LIKE '%6%' ORDER BY id""").fetchall()
    exact = [row for row in groups if "Чобот" in row["name"] and "6-8" in row["name"]]
    if len(exact) != 1:
        return {"state": "blocked", "student_id": sid,
                "reason": f"Группа Чоботовская сб/вс 6-8 определяется неоднозначно: {len(exact)} совпадений"}
    con.execute("SAVEPOINT chernykh_december_sale")
    try:
        invoice = services.create_invoice(con, sid, "2026-12", actor, tariff="flex",
            excluded_dates=["2026-12-26", "2026-12-27"], group_id=exact[0]["id"], group_action="add",
            paid_date="2026-11-23", exclusion_reason="СЗ №22969: семья уезжает 26–27.12")
        sub_id = invoice["id"]
        sub = con.execute("SELECT price,lessons FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
        if sub["lessons"] != 6:
            raise ValueError("Календарь не дал шесть занятий Черных")
        if sub["price"] != 7950:
            pricing.correct(con, sub_id, 7950,
                "СЗ №22969: 9 300 ₽ минус подтверждённый зачёт 1 350 ₽ за отменённое занятие", actor)
        payment_id = money.pay_subscription(con, sub_id, "tochka_qr_link", actor, amount=7950,
                                            paid_at="2026-11-23T15:22:04")
        verified = con.execute("SELECT price,lessons,status FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
        payment = con.execute("SELECT amount,method,cancelled FROM payments WHERE id=?", (payment_id,)).fetchone()
        if tuple(verified) != (7950, 6, "paid") or tuple(payment) != (7950, "tochka_qr_link", 0):
            raise ValueError("Итоговая проверка продажи Черных не пройдена")
        task = con.execute("SELECT id,status FROM tasks WHERE auto_key=? ORDER BY id DESC LIMIT 1",
                           ("chat-day:telegram:sz-22969:chernykh",)).fetchone()
        if task and task["status"] == "open":
            tasks.complete(con, task["id"], actor, result="Декабрьский абонемент и оплата 7 950 ₽ оформлены")
        con.execute("RELEASE chernykh_december_sale")
        return {"state": "created", "student_id": sid, "subscription_id": sub_id, "payment_id": payment_id}
    except Exception as error:
        con.execute("ROLLBACK TO chernykh_december_sale")
        con.execute("RELEASE chernykh_december_sale")
        return {"state": "blocked", "student_id": sid, "reason": str(error)}


def _reconcile_missing_identity_tasks(con, result):
    """Keep one manual task only when the exports truly lack card identity."""
    key = "telegram:sz-22966:golushko-identity"
    missing = any(row.get("source_id") == "telegram:sz-22966:golushko" for row in result.get("unmatched", []))
    existing = con.execute("SELECT id,status FROM tasks WHERE auto_key=? ORDER BY id DESC LIMIT 1", (key,)).fetchone()
    if missing:
        varya = con.execute("""SELECT id FROM users WHERE active=1 AND role='operator'
            AND (name LIKE '%Варвар%' OR trim(name)='Варя') ORDER BY is_senior DESC,id LIMIT 1""").fetchone()
        assignee = varya["id"] if varya else None
        task_id = tasks.add_task(con, "manual", None, "2026-11-24T23:59", auto_key=key,
            title="Установить контакт семьи Егора Голушко",
            note="СЗ №22966: Егор Голушко, 08.04.2023, ПУ 29.11 в 16:45, источник ТРЦ Небо. В СЗ и доступном ERP нет имени родителя и телефона; карточку по догадке не создавать. Найти контакт по записи филиала и после этого внести ПУ.",
            source="manual", assignee=assignee, shared=not bool(assignee))
        return {"state": "open", "task_id": task_id, "assignee_id": assignee}
    if existing and existing["status"] == "open":
        con.execute("""UPDATE tasks SET status='cancelled',done_at=?,done_by='CRM',
            result='Карточка Голушко найдена, факт СЗ применён автоматически' WHERE id=?""",
            (con.execute("SELECT datetime('now')").fetchone()[0], existing["id"]))
    return {"state": "not_needed", "task_id": existing["id"] if existing else None}


def apply(con):
    if con.in_transaction:
        return _apply(con)
    # Reserve the WAL writer before reading the snapshot used by the batch.
    con.execute("BEGIN IMMEDIATE")
    try:
        report = _apply(con)
        con.commit()
        return report
    except BaseException:
        con.rollback()
        raise


def _apply(con):
    previous = con.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    if previous:
        try:
            saved = json.loads(previous[0])
            if saved.get("state") == "complete":
                return saved
        except (TypeError, ValueError):
            pass
    actor = con.execute("SELECT id,name,role FROM users WHERE active=1 AND role='admin' AND trim(name)='Юлия Репина' ORDER BY id LIMIT 1").fetchone()
    if not actor:
        return {"state": "not_target_database"}
    result = chatdaytransfer.apply(con, {"events": events()}, dict(actor))
    identity_tasks = _reconcile_missing_identity_tasks(con, result)
    attendance = _trainer_attendance(con)
    daneykin_sale = _daneykin_december_sale(con, dict(actor))
    pogosyan_sale = _pogosyan_december_sale(con, dict(actor))
    chernykh_sale = _chernykh_december_sale(con, dict(actor))
    sales_ok = all(row.get("state") in ("created", "already_complete")
                   for row in (daneykin_sale, pogosyan_sale, chernykh_sale))
    report = {"state": "complete" if sales_ok and not result["errors"] and not result["unmatched"] and not result["ambiguous"] else "verification_failed",
              "counts": {**{key: len(value) for key, value in result.items()},
                         "created_from_erp": sum(bool(item.get("created")) for item in result["applied"])},
              "unmatched": result["unmatched"], "ambiguous": result["ambiguous"], "errors": result["errors"],
              "trainer_attendance": attendance, "daneykin_december_sale": daneykin_sale,
              "pogosyan_december_sale": pogosyan_sale, "chernykh_december_sale": chernykh_sale,
              "identity_tasks": identity_tasks}
    con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)", (KEY, json.dumps(report, ensure_ascii=False)))
    return report


def report(con):
    row = con.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    return json.loads(row[0]) if row else {"state": "pending"}
