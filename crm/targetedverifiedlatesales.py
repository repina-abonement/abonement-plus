"""Exact late-November sales and non-cash referral credit from care reports."""
import json

from . import chatdaytransfer


KEY="targeted_verified_late_sales_20261124_v1"


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
    ]


def apply(con):
    old=con.execute("SELECT value FROM settings WHERE key=?",(KEY,)).fetchone()
    if old:
        try:
            saved=json.loads(old[0])
            if saved.get('state')=='complete':return saved
        except (TypeError,ValueError):pass
    actor=con.execute("""SELECT id,name,role FROM users WHERE active=1 AND role='admin'
        AND trim(name)='Юлия Репина' ORDER BY id LIMIT 1""").fetchone()
    if not actor:return {'state':'not_target_database'}
    result=chatdaytransfer.apply(con,{'events':events()},dict(actor))
    report={'state':'complete' if not result['errors'] and not result['ambiguous'] else 'partial',
            'counts':{key:len(value) for key,value in result.items()},'unmatched':result['unmatched'],
            'ambiguous':result['ambiguous'],'errors':result['errors']}
    con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)",(KEY,json.dumps(report,ensure_ascii=False)))
    return report


def report(con):
    row=con.execute("SELECT value FROM settings WHERE key=?",(KEY,)).fetchone()
    return json.loads(row[0]) if row else {'state':'pending'}
