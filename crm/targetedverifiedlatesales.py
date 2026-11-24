"""Exact late-November sales and non-cash referral credit from care reports."""
import json

from . import chatdaytransfer, erphistory, tasks


KEY="targeted_verified_late_sales_20261124_v9"


def _sharikova_november_groups(con):
    source = 'СТОП Небо ПУП пн/чт/сб 19:45 возраст 6-8'
    normalize = lambda name: erphistory._norm(name).removeprefix('стоп ')
    source_name = normalize(source)
    return [row for row in con.execute(
        'SELECT id,name,source_name FROM groups WHERE is_makeup=0')
        if any(normalize(row[field]) == source_name for field in ('name', 'source_name'))]


def _sharikova_attendance(con, sid, erp_id='78877'):
    # Each of these single session lists includes both sisters.
    for message, day in [('26693', '05'), ('26795', '09'), ('26907', '12'),
                         ('27071', '16'), ('27093', '19')]:
        visit_date = f'2026-11-{day}'
        exists = con.execute("""SELECT 1 FROM erp_history_visits
            WHERE student_id=? AND visit_date=? AND visit_time='19:45'
            UNION ALL SELECT 1 FROM erp_history_unassigned_visits
            WHERE student_id=? AND visit_date=? AND visit_time='19:45'""",
            (sid, visit_date, sid, visit_date)).fetchone()
        if not exists:
            con.execute("""INSERT OR IGNORE INTO erp_history_unassigned_visits
                (source_id,student_id,visit_date,visit_time,kind,status,source_group,
                 source_payload,imported_at,imported_by)
                VALUES (?,?,?,'19:45','subscription','present','Небо 19:45',?,?,'Перенос ТРЕНЕРЫ')""",
                (f'trainers:{message}:erp:{erp_id}', sid, visit_date,
                 json.dumps({'source':'ТРЕНЕРЫ','message_id':message},ensure_ascii=False),
                 chatdaytransfer.sv.now_iso()))


def events():
    chat="Служба заботы"
    return [
        {"source_id":"sz:22891:павлов-декабрь","source_chat":chat,"source_at":"2026-11-22T20:32:01","student_name":"Павлов Матвей","phone":"+79530113980","event_kind":"subscription_sale","sale_date":"2026-11-22","paid_at":"2026-11-22T20:32:01","amount":8100,"payment_method":"card_branch","tariff":"fixed","lesson_time":"10:00","weekdays":[5,6],"components":[{"month":"2026-12","price":8100,"dates":["2026-12-05","2026-12-06","2026-12-12","2026-12-13","2026-12-19","2026-12-20"]}],"note":"Декабрь сб/вс 10:00 без 26 и 27.12, 8 100 ₽; карточный способ подтверждён отчётом смены"},
        {"source_id":"sz:22891:ухаботин-декабрь","source_chat":chat,"source_at":"2026-11-22T20:32:01","student_name":"Ухаботин Егор","event_kind":"subscription_sale","sale_date":"2026-11-22","paid_at":"2026-11-22T20:32:01","amount":10800,"payment_method":"card_branch","tariff":"fixed","lesson_time":"11:00","weekdays":[5,6],"components":[{"month":"2026-12","price":10800,"dates":["2026-12-05","2026-12-06","2026-12-12","2026-12-13","2026-12-19","2026-12-20","2026-12-26","2026-12-27"]}],"note":"Декабрь сб/вс 11:00, 10 800 ₽ картой"},
        {"source_id":"sz:22891:полторахов-декабрь","source_chat":chat,"source_at":"2026-11-22T20:32:01","student_name":"Полторахов Глеб","event_kind":"subscription_sale","sale_date":"2026-11-22","paid_at":"2026-11-22T20:32:01","amount":10800,"payment_method":"card_branch","tariff":"fixed","lesson_time":"11:00","weekdays":[5,6],"components":[{"month":"2026-12","price":10800,"dates":["2026-12-05","2026-12-06","2026-12-12","2026-12-13","2026-12-19","2026-12-20","2026-12-26","2026-12-27"]}],"note":"Декабрь сб/вс 11:00, 10 800 ₽ картой"},
        {"source_id":"sz:22891:регер-декабрь","source_chat":chat,"source_at":"2026-11-22T20:32:01","student_name":"Регер Лев","event_kind":"subscription_sale","sale_date":"2026-11-22","paid_at":"2026-11-22T20:32:01","amount":9450,"payment_method":"cash","tariff":"fixed","lesson_time":"18:45","weekdays":[2,6],"components":[{"month":"2026-12","price":9450,"dates":["2026-12-02","2026-12-06","2026-12-09","2026-12-13","2026-12-16","2026-12-20","2026-12-23"]}],"note":"Декабрь ср/вс 18:45 без 27 и 30.12, стоимость 9 450 ₽; получено 10 000 ₽ наличными, сдача 550 ₽ возвращена переводом"},
        {"source_id":"sz:22891:меньшиков-декабрь","source_chat":chat,"source_at":"2026-11-22T20:32:01","student_name":"Меньшиков Павел","event_kind":"sale_fact","event_date":"2026-11-22","note":"Подтверждён декабрьский абонемент 10 800 ₽ картой, расписание ср 18:45/вс 10:00; точный состав дат не перечислен","clarification_task_title":"Сверить даты декабрьского абонемента 10 800 ₽","clarification_task_note":"Оплата картой подтверждена; повторный платёж не создавать"},
        {"source_id":"sz:22891:трубников-декабрь","source_chat":chat,"source_at":"2026-11-22T20:32:01","student_name":"Трубников Герман","event_kind":"sale_fact","event_date":"2026-11-22","note":"Декабрь сб/вс 11:00 без одного занятия, стоимость 9 450 ₽; получено 10 000 ₽ наличными, сдача 550 ₽ возвращена переводом; исключённая дата не указана","clarification_task_title":"Уточнить исключённую дату декабря","clarification_task_note":"Стоимость и наличная оплата подтверждены; повторный платёж не создавать"},
        {"source_id":"sz:22891:краскова-декабрь","source_chat":chat,"source_at":"2026-11-22T20:32:01","student_name":"Краскова Кира","event_kind":"sale_fact","event_date":"2026-11-22","note":"Декабрь сб/вс 11:00 без одного занятия, 9 450 ₽ картой; исключённая дата не указана","clarification_task_title":"Уточнить исключённую дату декабря","clarification_task_note":"Карточная оплата подтверждена; повторный платёж не создавать"},
        {"source_id":"sz:22891:мамедов-бонус","source_chat":chat,"source_at":"2026-11-22T20:32:01","student_name":"Мамедов Даня","event_kind":"balance_credit","event_date":"2026-11-22","amount":3000,"credit_label":"Бонус 3 000 ₽ за рекомендацию Микаладзе Леона","note":"Начислить на свободный баланс 3 000 ₽ за рекомендацию Микаладзе Леона; автоматически на счета не списывать"},
        {"source_id":"sz:22821:рябов-декабрь","source_chat":chat,"source_at":"2026-11-22T11:30:16","student_name":"Рябов Михаил","event_kind":"sale_fact","event_date":"2026-11-21","note":"Подтверждена оплата декабрьского абонемента 12 150 ₽ через PayKeeper от 21.11; группа и состав дат не указаны","clarification_task_title":"Сверить группу и даты декабря 12 150 ₽","clarification_task_note":"PayKeeper подтверждён; повторный платёж не создавать"},
        {"source_id":"sz:22845:кононенко-декабрь","source_chat":chat,"source_at":"2026-11-22T13:16:30","student_name":"Кононенко Иван","event_kind":"sale_fact","event_date":"2026-11-22","note":"Декабрь Арктика + ОФП по субботам, 16 400 ₽ через PayKeeper; отпуск с 29.12","clarification_task_title":"Сверить состав Арктика + ОФП на декабрь","clarification_task_note":"Оплата 16 400 ₽ PayKeeper подтверждена; сохранить оба вида занятий и не создавать повторный платёж"},
        {"source_id":"sz:22847:шарикова-декабрь","source_chat":chat,"source_at":"2026-11-22T13:18:29","student_name":"Шарикова Евгения","erp_id":"78877","event_kind":"subscription_sale","sale_date":"2026-11-22","paid_at":"2026-11-22T13:18:29","amount":9720,"payment_method":"paykeeper_manual","tariff":"fixed","branch_hint":"Небо","lesson_time":"19:45","weekdays":[0,3],"discount_percent":10,"discount_rule":"large_family","components":[{"month":"2026-12","price":9720,"dates":["2026-12-03","2026-12-07","2026-12-10","2026-12-14","2026-12-17","2026-12-21","2026-12-24","2026-12-28","2026-12-31"]}],"note":"СЗ №22847: декабрь Евгении Шариковой, группа Небо пн/чт 19:45, оплачены девять указанных занятий за 9 720 ₽ по PayKeeper, скидка 10% многодетной семье. Сестра-близняшка Мария — отдельная карточка ERP 78875"},
        {"source_id":"sz:22850:хасаханов-продажа","source_chat":chat,"source_at":"2026-11-22T13:22:32","student_name":"Хасаханов Эмин","phone":"+79995992010","event_kind":"sale_fact","event_date":"2026-11-22","note":"После ПУ куплен абонемент Чоботовская сб/вс 10:00 со скидкой 20%, сумма 12 400 ₽; тренерский отчёт №27120 подтвердил наличные, десять занятий и тариф с переносом","resolved_clarification_note":"ТРЕНЕРЫ №27120 полностью подтвердил способ и состав; ручная сверка заменена точной продажей"},
        {"source_id":"trainers:27120:хасаханов-продажа","source_chat":"ТРЕНЕРЫ","source_at":"2026-11-22T12:19:45","student_name":"Хасаханов Эмин","phone":"+79995992010","event_kind":"subscription_sale","sale_date":"2026-11-22","paid_at":"2026-11-22T12:19:45","amount":12400,"payment_method":"cash","tariff":"flex","branch_hint":"Чобот","lesson_time":"10:00","weekdays":[5,6],"discount_percent":20,"components":[{"month":"2026-11","price":2480,"dates":["2026-11-28","2026-11-29"]},{"month":"2026-12","price":9920,"dates":["2026-12-05","2026-12-06","2026-12-12","2026-12-13","2026-12-19","2026-12-20","2026-12-26","2026-12-27"]}],"note":"СЗ №22850 и ТРЕНЕРЫ №27120: после ПУ куплены два занятия ноября и восемь декабря, гибкий тариф с переносом, скидка 20%, всего 12 400 ₽ наличными; повторный платёж не создавать"},
        {"source_id":"sz:22852:тупота-декабрь","source_chat":chat,"source_at":"2026-11-22T13:24:58","student_name":"Тупота Сергей","event_kind":"sale_fact","event_date":"2026-11-22","note":"Декабрь сб/вс 12:00, гибкий тариф с двумя переносами, 10 850 ₽ через PayKeeper","clarification_task_title":"Сверить даты и два переноса гибкого декабря","clarification_task_note":"PayKeeper 10 850 ₽ подтверждён; повторный платёж не создавать"},
        {"source_id":"sz:22858:михайлов-декабрь","source_chat":chat,"source_at":"2026-11-22T13:35:39","student_name":"Михайлов Иван","event_kind":"subscription_sale","sale_date":"2026-11-22","paid_at":"2026-11-22T13:35:39","amount":10800,"payment_method":"paykeeper_manual","tariff":"fixed","branch_hint":"Чобот","lesson_time":"11:00","weekdays":[5,6],"components":[{"month":"2026-12","price":10800,"dates":["2026-12-05","2026-12-06","2026-12-12","2026-12-13","2026-12-19","2026-12-20","2026-12-26","2026-12-27"]}],"note":"Декабрь сб/вс 11:00, 10 800 ₽ по PayKeeper"},
        {"source_id":"sz:22919:сафронов-декабрь","source_chat":chat,"source_at":"2026-11-23T10:57:45","student_name":"Сафронов Владимир","event_kind":"sale_fact","event_date":"2026-11-23","note":"Продление на декабрь пн/чт/сб, сумма 12 650 ₽; точные даты и способ оплаты не указаны","clarification_task_title":"Сверить даты и способ оплаты декабря 12 650 ₽","clarification_task_note":"Продление подтверждено; платёж без способа не создавать"},
        {"source_id":"sz:22920:санчес-пу","source_chat":chat,"source_at":"2026-11-23T10:59:46","student_name":"Муньос Санчес Энрике","birth":"2021-03-20","parent_name":"Ирина","phone":"+79685426638","event_kind":"trial_fact","event_date":"2026-11-28","note":"Новый лид записан на ПУ 28.11 на Чоботовской; время и рекламный источник не указаны","clarification_task_title":"Уточнить время ПУ и рекламный источник","clarification_task_note":"ФИО, дата рождения, родитель, телефон, дата и филиал известны"},
        {"source_id":"sz:22925:решетняк-арсений-декабрь","source_chat":chat,"source_at":"2026-11-23T11:15:40","student_name":"Решетняк Арсений","event_kind":"sale_fact","event_date":"2026-11-23","note":"Декабрь пн/чт, 8 тренировок: 10 800 ₽ минус 3 000 ₽ за приведённую Никитину Софью, к оплате 7 800 ₽ по Точке","clarification_task_title":"Сверить применение бонуса 3 000 ₽ к декабрю","clarification_task_note":"Оплата 7 800 ₽ по Точке подтверждена; не создавать повторный платёж или второй бонус"},
        {"source_id":"sz:22927:кожевников-декабрь","source_chat":chat,"source_at":"2026-11-23T11:35:07","student_name":"Кожевников Артем","event_kind":"sale_fact","event_date":"2026-11-23","note":"Декабрь пн/чт/сб, 12 тренировок, 12 420 ₽ по Точке после скидки 10% многодетной семье","clarification_task_title":"Сверить время группы и 12 дат декабря","clarification_task_note":"Сумма, скидка и Точка подтверждены; повторный платёж не создавать"},
        {"source_id":"sz:22955:шарипов-декабрь","source_chat":chat,"source_at":"2026-11-23T12:44:19","student_name":"Шарипов Даниил","event_kind":"sale_fact","event_date":"2026-11-23","note":"Декабрь Арктика + ОФП три раза в неделю, 20 400 ₽ через PayKeeper; администратор не видел абонемент в CRM","clarification_task_title":"Оформить точный состав Арктика + ОФП после сверки","clarification_task_note":"PayKeeper 20 400 ₽ подтверждён; не создавать обычный одиночный абонемент или повторный платёж"},
        {"source_id":"sz:22959:бородин-декабрь","source_chat":chat,"source_at":"2026-11-23T13:16:43","student_name":"Бородин Даниил","event_kind":"sale_fact","event_date":"2026-11-23","note":"Продление декабря пн/чт/сб, 13 800 ₽ по Точке; время и точный состав дат не указаны","clarification_task_title":"Сверить время и даты декабря 13 800 ₽","clarification_task_note":"Оплата по Точке подтверждена; повторный платёж не создавать"},
        {"source_id":"sz:22961:деменев-декабрь","source_chat":chat,"source_at":"2026-11-23T13:22:06","student_name":"Деменев Антон","event_kind":"sale_fact","event_date":"2026-11-23","note":"Продление декабря пн/чт/сб, 12 занятий, 13 800 ₽ по Точке; время группы не указано","clarification_task_title":"Сверить время группы декабря 13 800 ₽","clarification_task_note":"12 занятий и оплата по Точке подтверждены; повторный платёж не создавать"},
    ]


def apply(con):
    old=con.execute("SELECT value FROM settings WHERE key=?",(KEY,)).fetchone()
    if old:
        try:
            saved=json.loads(old[0])
            if chatdaytransfer.saved_batch_can_skip(saved):return saved
        except (TypeError,ValueError):pass
    actor=con.execute("""SELECT id,name,role FROM users WHERE active=1 AND role='admin'
        AND trim(name)='Юлия Репина' ORDER BY id LIMIT 1""").fetchone()
    if not actor:return {'state':'not_target_database'}
    profile_event={
        'source_id':'erp:78877:726405:november', 'source_chat':'ERP',
        'source_at':'2026-11-05T00:00:00', 'student_name':'Евгения Шарикова',
        'erp_id':'78877', 'erp_create_if_missing':True, 'event_kind':'note',
        'erp_profile':{'name':'Евгения Шарикова','birth':'2020-04-28',
                       'parent_name':'Ольга','phone':'+79309613965','email':'olgavin90@mail.ru',
                       'erp_status':'Вернувшийся'},
        'note':'ERP №726405: ноябрьский стандартный абонемент на 8 занятий, 05–30.11, историческая стоимость 8 640 ₽.',
    }
    identity=chatdaytransfer.apply(con,{'events':[profile_event]},dict(actor))
    found=identity['applied'] or identity['already']
    if found:
        sid=found[0]['student_id']
        _sharikova_attendance(con, sid)
        groups=_sharikova_november_groups(con)
        if len(groups)==1:
            con.execute("""INSERT OR IGNORE INTO erp_history_subscriptions
                (source_id,student_id,group_id,month,valid_from,valid_until,lessons,
                 revenue_amount,source_payload,imported_at,imported_by)
                VALUES ('726405',?,?,'2026-11','2026-11-05','2026-11-30',8,8640,?,?,'Перенос ERP')""",
                (sid,groups[0]['id'],json.dumps({'erp_id':'78877'}),chatdaytransfer.sv.now_iso()))
        elif not con.execute("SELECT 1 FROM erp_history_subscriptions WHERE source_id='726405' AND student_id=?",(sid,)).fetchone():
            identity['errors'].append({'source_id':profile_event['source_id'],'name':profile_event['student_name'],
                                       'error':'Для ноября сохранён факт ERP; исходная группа не определена однозначно'})
    profile_event={
        'source_id':'erp:78875:726406:november', 'source_chat':'ERP',
        'source_at':'2026-11-05T00:00:00', 'student_name':'Мария Шарикова',
        'erp_id':'78875', 'erp_create_if_missing':True, 'event_kind':'note',
        'erp_profile':{'name':'Мария Шарикова','birth':'2020-04-28',
                       'parent_name':'Ольга','phone':'+79309613965','email':'olgavin90@mail.ru',
                       'erp_status':'Вернувшийся'},
        'note':'ERP №726406: ноябрьский стандартный абонемент на 8 занятий, 05–30.11, историческая стоимость 8 640 ₽.',
    }
    maria=chatdaytransfer.apply(con,{'events':[profile_event]},dict(actor))
    found=maria['applied'] or maria['already']
    if found:
        sid=found[0]['student_id']
        _sharikova_attendance(con, sid, '78875')
        groups=_sharikova_november_groups(con)
        if len(groups)==1:
            con.execute("""INSERT OR IGNORE INTO erp_history_subscriptions
                (source_id,student_id,group_id,month,valid_from,valid_until,lessons,
                 revenue_amount,source_payload,imported_at,imported_by)
                VALUES ('726406',?,?,'2026-11','2026-11-05','2026-11-30',8,8640,?,?,'Перенос ERP')""",
                (sid,groups[0]['id'],json.dumps({'erp_id':'78875'}),chatdaytransfer.sv.now_iso()))
        elif not con.execute("SELECT 1 FROM erp_history_subscriptions WHERE source_id='726406' AND student_id=?",(sid,)).fetchone():
            maria['errors'].append({'source_id':profile_event['source_id'],'name':profile_event['student_name'],
                                       'error':'Для ноября сохранён факт ERP; исходная группа не определена однозначно'})
        task_id = tasks.add_task(con, 'manual', sid, '2026-11-25T10:00',
            auto_key='manual:78875:december-payment-check', source='manual', shared=True,
            title='Сверить декабрьскую оплату сестёр Шариковых',
            note='В СЗ №22847 оплата 9 720 ₽ через PayKeeper от 22.11 подтверждена только для Евгении. Проверить PayKeeper и CRM по обеим сёстрам и выяснить статус декабрьской оплаты Марии.')
        if task_id:
            con.execute('UPDATE tasks SET priority=1 WHERE id=?', (task_id,))
    for key in identity:
        identity[key].extend(maria[key])
    result=chatdaytransfer.apply(con,{'events':events()},dict(actor))
    for key in result:
        result[key].extend(identity[key])
    report={'state':'complete' if chatdaytransfer.batch_is_complete(result) else 'partial',
            'counts':{key:len(value) for key,value in result.items()},'unmatched':result['unmatched'],
            'ambiguous':result['ambiguous'],'errors':result['errors']}
    con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)",(KEY,json.dumps(report,ensure_ascii=False)))
    return report


def report(con):
    row=con.execute("SELECT value FROM settings WHERE key=?",(KEY,)).fetchone()
    return json.loads(row[0]) if row else {'state':'pending'}
