"""Client facts from marked care messages not covered by the primary batches."""
import json

from . import chatdaytransfer, tasks


KEY="targeted_verified_care_gaps_20261124_v1"


def events():
    chat="Служба заботы"
    return [
        {"source_id":"sz:20646:чугунов-турнир","source_chat":chat,"source_at":"2026-11-08T19:55:12","student_name":"Чугунов Егор","event_kind":"sale_fact","event_date":"2026-11-08","note":"Подтверждена оплата турнира 2 700 ₽; пометка «MAX» обозначает канал сообщения, способ оплаты не указан","clarification_task_title":"Сверить способ оплаты турнира 2 700 ₽","clarification_task_note":"Оплата подтверждена; повторный платёж не создавать"},
        {"source_id":"sz:20646:гмыря-турнир","source_chat":chat,"source_at":"2026-11-08T19:55:12","student_name":"Гмыря Артем","event_kind":"sale_fact","event_date":"2026-11-08","note":"Подтверждена оплата турнира 2 700 ₽; пометка «ТГ» обозначает канал сообщения, способ оплаты не указан","clarification_task_title":"Сверить способ оплаты турнира 2 700 ₽","clarification_task_note":"Оплата подтверждена; повторный платёж не создавать"},
        {"source_id":"sz:20646:шулежко-турнир","source_chat":chat,"source_at":"2026-11-08T19:55:12","student_name":"Шулежко Даниил","event_kind":"sale_fact","event_date":"2026-11-08","note":"Подтверждена оплата турнира 2 700 ₽; пометка «ТГ» обозначает канал сообщения, способ оплаты не указан","clarification_task_title":"Сверить способ оплаты турнира 2 700 ₽","clarification_task_note":"Оплата подтверждена; повторный платёж не создавать"},
        {"source_id":"sz:20651:павлинов-связь","source_chat":chat,"source_at":"2026-11-08T20:33:04","student_name":"Павлинов Петр","event_kind":"contact_fact","event_date":"2026-11-08","note":"После ПУ был недозвон и блокировка номера; чат семьи находился в WhatsApp, администратор не могла открыть его и отправить сообщение через CRM"},
        {"source_id":"sz:20651:коля-без-данных","source_chat":chat,"source_at":"2026-11-08T20:33:04","student_name":"Коля","event_kind":"lead_fact","event_date":"2026-11-08","note":"В отчёте после ПУ указан только Коля; карточку и контактные данные в СЗ найти не удалось","clarification_task_title":"Установить семью ПУ Коли от 08.11","clarification_task_note":"В СЗ нет фамилии, телефона, времени или других точных данных; карточку по догадке не создавать"},
        {"source_id":"sz:20554:тараченова-пу","source_chat":chat,"source_at":"2026-11-07T10:03:15","student_name":"Тарачанова Евангелина","event_kind":"trial_result","event_date":"2026-11-07","came":True,"outcome":"followup","note":"Пришла на ПУ 07.11 в 10:00 на Чоботовской без предварительного предупреждения; решение о покупке не указано"},
        {"source_id":"sz:20858:лид-только-телефон","source_chat":chat,"source_at":"2026-11-10T22:27:27","student_name":"Лид +79251850868","phone":"+79251850868","event_kind":"lead_fact","event_date":"2026-11-17","note":"Лид был записан на ПУ 17.11 в 17:45 в ТРЦ Небо; в СЗ нет имени ребёнка и родителя, в тот момент данные оставались только в ERP","clarification_task_title":"Установить ребёнка по телефону +79251850868","clarification_task_note":"Дата и время ПУ известны; карточку без имени по догадке не создавать"},
        {"source_id":"sz:20921:фефилатьев-пу","source_chat":chat,"source_at":"2026-11-11T15:52:25","student_name":"Фефилатьев Макар","birth":"2024-08-15","parent_name":"Анжелика","phone":"89133054075","create_if_missing":True,"event_kind":"trial_booking","event_date":"2026-11-21","trial_time":"10:00","note":"Новый лид записан на ПУ 21.11 в 10:00, предпочтительный канал MAX"},
        {"source_id":"sz:21057:рахман-продажа","source_chat":chat,"source_at":"2026-11-11T21:15:04","student_name":"Рахман Альфаз","birth":"2022-12-16","phone":"+79854710937","source":"по рекомендации Хумидана Тимура","event_kind":"sale_fact","event_date":"2026-11-11","note":"Куплены ноябрь и декабрь, группа ср/вс 18:45, сумма 15 120 ₽; способ оплаты и состав по месяцам не указаны","clarification_task_title":"Сверить состав и способ оплаты 15 120 ₽","clarification_task_note":"Покупка подтверждена; повторный платёж не создавать"},
        {"source_id":"sz:21347:бородин-ноябрь","source_chat":chat,"source_at":"2026-11-13T18:06:40","student_name":"Бородин Доброслав","event_kind":"sale_fact","event_date":"2026-11-13","note":"Ноябрь вт/пт 17:45, сумма 6 750 ₽; требовалось пробить чек, способ оплаты не указан","clarification_task_title":"Сверить способ оплаты ноября 6 750 ₽","clarification_task_note":"Покупка подтверждена; повторный платёж не создавать"},
        {"source_id":"sz:21376:смирнов-разовое","source_chat":chat,"source_at":"2026-11-13T20:03:22","student_name":"Смирнов Эрик","event_kind":"sale_fact","event_date":"2026-11-14","note":"Разовое занятие 14.11 в 11:00, сумма 2 500 ₽; способ оплаты не указан","clarification_task_title":"Сверить способ оплаты разового 2 500 ₽","clarification_task_note":"Чек требовалось пробить; повторный платёж не создавать"},
        {"source_id":"sz:21378:серегин-продажа","source_chat":chat,"source_at":"2026-11-13T20:07:06","student_name":"Серегин Глеб","event_kind":"sale_fact","event_date":"2026-11-13","note":"Абонемент вт/пт 16:45, сумма 4 320 ₽; способ и даты не указаны","clarification_task_title":"Сверить даты и способ оплаты 4 320 ₽","clarification_task_note":"Покупка подтверждена; повторный платёж не создавать"},
        {"source_id":"sz:21504:гянджунцев-разовое","source_chat":chat,"source_at":"2026-11-14T16:49:13","student_name":"Гянджунцев Артем","event_kind":"sale_fact","event_date":"2026-11-15","note":"Разовое занятие 15.11, сумма 2 500 ₽; способ и время не указаны","clarification_task_title":"Сверить время и способ оплаты разового 2 500 ₽","clarification_task_note":"Чек требовалось пробить; повторный платёж не создавать"},
        {"source_id":"sz:22523:ахмедов-контакт","source_chat":chat,"source_at":"2026-11-20T19:50:17","student_name":"Ахмедов Али","phone":"+79261300620","event_kind":"contact_fact","event_date":"2026-11-20","note":"Перед ПУ 21.11 был недозвон; семье отправлено сообщение, результат занятия в этом сообщении не подтверждён"},
        {"source_id":"sz:22523:лобоевский-болезнь","source_chat":chat,"source_at":"2026-11-20T19:50:17","student_name":"Лобоевский Матвей","event_kind":"decision_fact","event_date":"2026-11-20","note":"Не пришёл на ПУ из-за болезни и курса антибиотиков; новая дата на момент сообщения не назначена","task_title":"Связаться после выздоровления и предложить новую дату ПУ","task_due":"2026-12-04T12:00"},
        {"source_id":"sz:21585:михайленко-перенос","source_chat":chat,"source_at":"2026-11-15T19:11:41","student_name":"Михайленко Михаил","event_kind":"trial_booking","event_date":"2026-11-21","trial_time":"10:00","note":"С ПУ 15.11 ушёл с тренировки; повторное ПУ назначено на 21.11 в 10:00"},
        {"source_id":"sz:21608:орехин-условия","source_chat":chat,"source_at":"2026-11-15T20:41:10","student_name":"Орехин Александр","phone":"+79019040449","event_kind":"discount_fact","event_date":"2026-11-15","note":"Родственник тренера Ивакина: первая покупка со скидкой 20%, далее постоянная скидка 10%; ПУ на Чоботовской 08.11","clarification_task_title":"Проверить сохранение индивидуальной скидки 10% после первой покупки","clarification_task_note":"Не применять дополнительную скидку поверх согласованной"},
        {"source_id":"sz:21667:федор-лид","source_chat":chat,"source_at":"2026-11-16T11:35:32","student_name":"Федор","birth":"2024-08-24","parent_name":"Анастасия","phone":"89168316324","event_kind":"lead_fact","event_date":"2026-11-23","note":"Запись на ПУ 23.11; фамилия и время не указаны, предпочтительный канал MAX","clarification_task_title":"Уточнить фамилию и время ПУ Федора","clarification_task_note":"Дата рождения, мама, телефон и дата ПУ известны; не создавать карточку только по имени"},
        {"source_id":"sz:21714:данейкин-посещения","source_chat":chat,"source_at":"2026-11-16T14:50:32","student_name":"Данейкин Виктор","event_kind":"attendance_fact","event_date":"2026-11-16","note":"СЗ поручила добавить занятия 16, 19 и 21 ноября; время 18:45 берётся из подтверждённой группы карточки"},
        {"source_id":"sz:21718:печенкин-не-беспокоить","source_chat":chat,"source_at":"2026-11-16T15:05:53","student_name":"Печенкин Артём","parent_name":"Анастасия","phone":"89261000207","event_kind":"decision_fact","event_date":"2026-11-16","note":"Семья попросила не писать часто; готова вернуться к записи не ранее января, утреннее время неудобно из-за работы","task_due":"2027-01-10T12:00","task_title":"Связаться один раз после праздников и предложить вечернее ПУ","task_note":"Утреннее время неудобно; семья просила не беспокоить до января"},
        {"source_id":"sz:21768:дима-лид","source_chat":chat,"source_at":"2026-11-16T17:41:54","student_name":"Дима","birth":"2022-01-23","parent_name":"Ирина","phone":"+79163642574","event_kind":"lead_fact","event_date":"2026-11-20","note":"Новый лид записан на ПУ в пятницу в 17:45; фамилия не указана","clarification_task_title":"Уточнить фамилию Димы","clarification_task_note":"Дата рождения, мама, телефон, дата и время ПУ известны; не создавать карточку только по имени"},
        {"source_id":"sz:21819:королев-продажа","source_chat":chat,"source_at":"2026-11-16T19:32:23","student_name":"Королев Дима","event_kind":"sale_fact","event_date":"2026-11-16","note":"Мама вернулась и оплатила 5 400 ₽ картой; период и состав покупки не указаны","clarification_task_title":"Сверить состав покупки 5 400 ₽","clarification_task_note":"Карточная оплата подтверждена; повторный платёж не создавать"},
        {"source_id":"sz:21801:королев-вторая-группа","source_chat":chat,"source_at":"2026-11-16T18:55:06","student_name":"Королев Дима","event_kind":"group_trial","event_date":"2026-11-22","note":"После занятия 16.11 в 17:45 семья купила остаток ноября и попросила записать Диму на просмотр воскресной группы 22.11 в 12:00; постоянную группу планировали выбрать после просмотра"},
        {"source_id":"sz:21948:арон-лид","source_chat":chat,"source_at":"2026-11-17T17:38:53","student_name":"Арон","birth":"2024-08-27","parent_name":"Анна","phone":"89252051902","event_kind":"lead_fact","event_date":"2026-11-17","note":"Новый лид без фамилии; семья хочет подрасти и прийти в вечернюю группу, утро пн/ср 10:30 неудобно","clarification_task_title":"Уточнить фамилию Арона при следующем контакте","clarification_task_note":"Не писать раньше согласованного срока и не создавать вторую карточку","task_title":"Связаться через три месяца и предложить вечернее ПУ","task_due":"2027-02-17T12:00"},
        {"source_id":"sz:22055:киреева-решение","source_chat":chat,"source_at":"2026-11-17T21:03:30","student_name":"Киреева Ксения","event_kind":"decision_fact","event_date":"2026-11-17","note":"Фамилия подтверждена: Киреева. Семья планирует разовые занятия, на месте не платила; напомнить про пятницу","task_title":"Напомнить семье Киреевой про пятничное разовое занятие","task_due":"2026-11-20T12:00"},
        {"source_id":"sz:22186:юрков-пу-результат","source_chat":chat,"source_at":"2026-11-18T18:49:09","student_name":"Юрков Лев","phone":"+79523231764","event_kind":"trial_result","event_date":"2026-11-18","came":True,"outcome":"single","note":"Пришёл на ПУ; семья выбрала разовое занятие 21.11 в 12:00 и попросила ссылку, подтверждения оплаты в этом сообщении нет"},
        {"source_id":"sz:22414:мамедов-декабрь","source_chat":chat,"source_at":"2026-11-20T15:13:00","student_name":"Мамедов Даниил","event_kind":"sale_fact","event_date":"2026-11-20","note":"Оплачен декабрьский абонемент 9 450 ₽ по ссылке Точки, без занятия 28.12; точная группа в сообщении не указана","clarification_task_title":"Сверить группу декабрьского абонемента Мамедова","clarification_task_note":"Декабрь, сумма, Точка и исключение 28.12 подтверждены; повторный платёж не создавать и реферальный бонус учитывать отдельно"},
        {"source_id":"sz:22488:замковой-оплата","source_chat":chat,"source_at":"2026-11-20T18:50:06","student_name":"Замковой Илья","event_kind":"sale_fact","event_date":"2026-11-20","note":"Принята оплата 12 150 ₽ картой на филиале; чек выдан; период и состав не перечислены","clarification_task_title":"Сверить период абонемента 12 150 ₽","clarification_task_note":"Карточная оплата подтверждена; повторный платёж не создавать"},
        {"source_id":"sz:22511:наливайко-оплата","source_chat":chat,"source_at":"2026-11-20T19:03:43","student_name":"Наливайко Максим","event_kind":"sale_fact","event_date":"2026-11-20","note":"Группа вт/пт 18:45, принято 12 150 ₽ по ссылке Точки, чек пробит; месяц явно не указан","clarification_task_title":"Сверить месяц и даты оплаты 12 150 ₽","clarification_task_note":"Точка и сумма подтверждены; повторный платёж не создавать"},
        {"source_id":"sz:22514:гущо-оплата","source_chat":chat,"source_at":"2026-11-20T19:14:45","student_name":"Гущо Михаил","event_kind":"sale_fact","event_date":"2026-11-20","note":"Группа пн/чт 19:45, принято 10 800 ₽ по ссылке Точки; месяц явно не указан","clarification_task_title":"Сверить месяц и даты оплаты 10 800 ₽","clarification_task_note":"Точка и сумма подтверждены; повторный платёж не создавать"},
        {"source_id":"sz:22525:гмыря-абонемент","source_chat":chat,"source_at":"2026-11-20T19:55:34","student_name":"Гмыря Артем","event_kind":"sale_fact","event_date":"2026-11-20","note":"Принята оплата абонемента 12 150 ₽ картой на филиале; период, группа и даты в строке не указаны","clarification_task_title":"Сверить период и состав абонемента 12 150 ₽","clarification_task_note":"Карточная оплата подтверждена; повторный платёж не создавать"},
        {"source_id":"sz:22525:гасанов-пу","source_chat":chat,"source_at":"2026-11-20T19:55:34","student_name":"Гасанов Амир","event_kind":"trial_result","event_date":"2026-11-20","came":True,"outcome":"followup","note":"Опоздали на ПУ 17:45 и попали в сильную группу 18:45; рассматривают вт/пт 17:45, другие варианты не подходят; решение ожидалось вечером","followup_due":"2026-11-21T12:00","followup_title":"Уточнить решение по группе вт/пт 17:45"},
        {"source_id":"sz:22525:малышев-оплата","source_chat":chat,"source_at":"2026-11-20T19:55:34","student_name":"Малышев Артем","event_kind":"sale_fact","event_date":"2026-11-20","note":"Принята оплата абонемента 12 150 ₽ по QR терминала Сбера на филиале; в январе семья уезжает","clarification_task_title":"Сверить период и состав абонемента 12 150 ₽","clarification_task_note":"QR Сбера и сумма подтверждены; повторный платёж не создавать"},
        {"source_id":"sz:22529:беруков-оплата","source_chat":chat,"source_at":"2026-11-20T19:57:26","student_name":"Беруков Даниил","event_kind":"sale_fact","event_date":"2026-11-20","note":"Оплачен абонемент вт/пт 18:45 по ссылке PayKeeper; сумма и месяц не указаны","clarification_task_title":"Сверить сумму, месяц и даты PayKeeper","clarification_task_note":"Факт оплаты подтверждён; повторный платёж не создавать"},
        {"source_id":"sz:22542:кустов-декабрь","source_chat":chat,"source_at":"2026-11-20T20:22:34","student_name":"Кустов Павел","event_kind":"sale_fact","event_date":"2026-11-20","note":"Гибкое продление декабря, ср/вс 18:45, 9 тренировок и два переноса с ноября, 10 850 ₽ по Точке","clarification_task_title":"Сверить девять дат и два переноса декабря","clarification_task_note":"Сумма и Точка подтверждены; повторный платёж не создавать"},
        {"source_id":"sz:22548:лобченко-декабрь","source_chat":chat,"source_at":"2026-11-20T20:57:38","student_name":"Лобченко Константин","event_kind":"subscription_sale","sale_date":"2026-11-20","paid_at":"2026-11-20T20:57:38","amount":12150,"payment_method":"paykeeper_manual","tariff":"fixed","lesson_time":"17:45","weekdays":[2,6],"components":[{"month":"2026-12","price":12150,"dates":["2026-12-02","2026-12-06","2026-12-09","2026-12-13","2026-12-16","2026-12-20","2026-12-23","2026-12-27","2026-12-30"]}],"note":"Продление декабря ср/вс 17:45, 12 150 ₽ по PayKeeper"},
        {"source_id":"sz:22573:жегулин-продажа","source_chat":chat,"source_at":"2026-11-21T11:07:54","student_name":"Жегулин Севастьян","event_kind":"subscription_sale","sale_date":"2026-11-21","paid_at":"2026-11-21T11:07:54","amount":11880,"payment_method":"cash","tariff":"fixed","lesson_time":"10:00","weekdays":[5,6],"discount_percent":20,"components":[{"month":"2026-11","price":3240,"dates":["2026-11-22","2026-11-28","2026-11-29"]},{"month":"2026-12","price":8640,"dates":["2026-12-05","2026-12-06","2026-12-12","2026-12-13","2026-12-19","2026-12-20","2026-12-26","2026-12-27"]}],"note":"Покупка после ПУ: остаток ноября и декабрь со скидкой 20%, группа сб/вс 10:00, стоимость 11 880 ₽; получено 12 000 ₽ наличными, сдача 120 ₽ возвращена переводом"},
        {"source_id":"sz:22362:жегулин-пу","source_chat":chat,"source_at":"2026-11-20T11:12:41","student_name":"Жегулин Севастьян","source":"старый клиент","event_kind":"trial_booking","event_date":"2026-11-21","trial_time":"10:00","note":"Записан на ПУ 21.11 в 10:00; источник — старый клиент"},
        {"source_id":"sz:22576:кудрявцев-переход","source_chat":chat,"source_at":"2026-11-21T11:12:13","student_name":"Кудрявцев Роман","event_kind":"group_change","event_date":"2026-12-01","note":"21.11 пришёл на отработку 10:00; 22.11 вместо вечера перезаписан на 10:00. С декабря рассматривает переход из ср/вс 18:45 в утреннюю группу после оплаты"},
        {"source_id":"sz:22578:юрков-разовое","source_chat":chat,"source_at":"2026-11-21T12:16:24","student_name":"Юрков Лев","phone":"+79523231764","event_kind":"sale_fact","event_date":"2026-11-21","note":"Принята оплата разового занятия 21.11, 2 500 ₽ картой; точное время занятия не подтверждено","clarification_task_title":"Сверить время разового занятия 21.11","clarification_task_note":"Карточная оплата подтверждена; повторный платёж не создавать"},
        {"source_id":"sz:22581:викторов-декабрь","source_chat":chat,"source_at":"2026-11-21T12:37:00","student_name":"Викторов Александр","event_kind":"sale_fact","event_date":"2026-11-21","note":"Декабрь Чоботовская сб/вс 10:00, итог 6 600 ₽: 12 400 − 2 700 за пропущенный по болезни турнир − 3 100 перерасчёт двух ноябрьских занятий","clarification_task_title":"Сверить основания уменьшения декабря до 6 600 ₽","clarification_task_note":"Не создавать дополнительные возвраты или платежи без сверки уже учтённых сумм"},
        {"source_id":"sz:22591:хумидан-декабрь","source_chat":chat,"source_at":"2026-11-21T13:22:28","student_name":"Хумидан Тимур","event_kind":"sale_fact","event_date":"2026-11-21","note":"Декабрь Небо ср/вс 18:45, шесть тренировок до отпуска: 9 300 ₽ минус 3 000 ₽ за приведённого Альфаза Рахмана, итог 6 300 ₽; способ оплаты не указан","clarification_task_title":"Сверить реферальный бонус и способ оплаты 6 300 ₽","clarification_task_note":"Не создавать второй бонус или платёж до проверки карточки"},
    ]


def _apply_daneykin_visits(con):
    matches=con.execute("""SELECT id,name FROM students WHERE archived=0 AND
        (trim(name)='Виктор Данейкин' OR trim(name)='Данейкин Виктор') ORDER BY id""").fetchall()
    if len(matches)!=1:
        return {'added':0,'already':0,'error':f'Карточка Данейкина определяется неоднозначно: {len(matches)}'}
    sid=matches[0]['id'];added=already=0
    for day in ('2026-11-16','2026-11-19','2026-11-21'):
        exists=con.execute("""SELECT 1 FROM erp_history_visits
            WHERE student_id=? AND visit_date=? AND visit_time='18:45' AND status='present'
            UNION ALL SELECT 1 FROM erp_history_unassigned_visits
            WHERE student_id=? AND visit_date=? AND visit_time='18:45' AND status='present' LIMIT 1""",
            (sid,day,sid,day)).fetchone()
        if exists:
            already+=1;continue
        payload=json.dumps({'source':'Служба заботы','message_id':'21714','reported_name':'Данейкин Виктор',
                            'confirmed':True},ensure_ascii=False)
        con.execute("""INSERT OR IGNORE INTO erp_history_unassigned_visits
            (source_id,source_subscription_id,student_id,visit_date,visit_time,kind,status,source_group,
             source_payload,imported_at,imported_by)
            VALUES (?,NULL,?,?,?,'subscription','present','',?,?,?)""",
            (f'sz:21714:visit:{day}:{sid}',sid,day,'18:45',payload,'2026-11-16T14:50:32','Перенос СЗ'))
        if con.execute('SELECT changes()').fetchone()[0]:added+=1
        else:already+=1
    return {'added':added,'already':already,'error':''}


def _sync_identity_tasks(con,result):
    specs={
        'sz:20651:коля-без-данных':('Установить семью ПУ Коли от 08.11','В СЗ нет фамилии, телефона и времени; карточку по догадке не создавать.'),
        'sz:20858:лид-только-телефон':('Установить ребёнка по телефону +79251850868','ПУ было назначено на 17.11 в 17:45 в ТРЦ Небо; имя и родитель в СЗ отсутствуют, сначала проверить ERP.'),
        'sz:21667:федор-лид':('Уточнить фамилию и время ПУ Федора','Дата рождения 24.08.2024, мама Анастасия, 89168316324, ПУ 23.11; фамилии и времени в СЗ нет.'),
        'sz:21768:дима-лид':('Уточнить фамилию Димы','Дата рождения 23.01.2022, мама Ирина, +79163642574, ПУ 20.11 в 17:45; фамилии в СЗ нет.'),
        'sz:21948:арон-лид':('Уточнить фамилию Арона при следующем контакте','Дата рождения 27.08.2024, мама Анна, 89252051902; связаться не раньше 17.02.2027.'),
    }
    missing={row.get('source_id') for key in ('unmatched','ambiguous','errors')
             for row in result.get(key,[]) if isinstance(row,dict)}
    created=closed=0
    for source,(title,note) in specs.items():
        key='chat-unmatched:'+source
        if source in missing:
            due='2027-02-17T12:00' if 'арон' in source else '2026-11-24T23:59'
            if tasks.add_task(con,'plan',None,due,auto_key=key,title=title,note=note,source='manual'):
                created+=1
        else:
            con.execute("""UPDATE tasks SET status='cancelled',done_at=?,done_by='CRM',
                result='Карточка найдена, подтверждённый факт применён автоматически'
                WHERE auto_key=? AND status='open'""",('2026-11-24T23:59:00',key))
            closed+=con.execute('SELECT changes()').fetchone()[0]
    return {'created':created,'closed':closed,'still_missing':sorted(missing & set(specs))}


def apply(con):
    old=con.execute("SELECT value FROM settings WHERE key=?",(KEY,)).fetchone()
    if old:
        try:
            saved=json.loads(old[0])
            if chatdaytransfer.saved_batch_can_skip(saved):return saved
        except (TypeError,ValueError):pass
    actor=con.execute("SELECT id,name,role FROM users WHERE active=1 AND role='admin' AND trim(name)='Юлия Репина' ORDER BY id LIMIT 1").fetchone()
    if not actor:return {'state':'not_target_database'}
    result=chatdaytransfer.apply(con,{'events':events()},dict(actor))
    daneykin=_apply_daneykin_visits(con)
    identity_tasks=_sync_identity_tasks(con,result)
    report={'state':'complete' if chatdaytransfer.batch_is_complete(result,daneykin['error']) else 'partial',
            'counts':{key:len(value) for key,value in result.items()},'unmatched':result['unmatched'],
            'ambiguous':result['ambiguous'],'errors':result['errors'],'daneykin_visits':daneykin,
            'identity_tasks':identity_tasks}
    con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)",(KEY,json.dumps(report,ensure_ascii=False)))
    return report


def report(con):
    row=con.execute("SELECT value FROM settings WHERE key=?",(KEY,)).fetchone()
    return json.loads(row[0]) if row else {'state':'pending'}
