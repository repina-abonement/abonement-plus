"""Задачи участников команды и автосообщения родителям вокруг пробного занятия.

Задачи по пробным создаются сами (по дате пробного) и закрываются, когда участник команды нажал «Готово»
или клиент ушёл дальше по воронке. Ответственный за каждый вид задач задаётся в Настройках.
Те же виды задач используются при переносе из ERP (см. ERP_STATUSES).
"""
from __future__ import annotations

import json
import calendar
from datetime import date, datetime, timedelta

from . import comms, trialscripts

KINDS = {
    "trial_confirm": ("Напоминание о ПУ (звонок)", "📅"),
    "trial_today": ("Напоминание о сегодняшнем ПУ (звонок и смс)", "⏰"),
    "how_was": ("Как прошло пробное", "💬"),
    "no_show": ("Не пришли на ПУ", "🚫"),
    "not_marked": ("Нет отметки о ПУ", "📝"),
    "erp_how_was_sms": ("Как прошло ПУ?", "💬"),
    "erp_how_was_today": ("Как прошло ПУ?", "💬"),
    "why_not_bought": ("Почему не купили", "🤔"),
    "rebook": ("Перезаписать на ПУ", "🔁"),
    "reinvite_pu": ("Пригласить на ПУ повторно", "🔄"),
    "single_remind": ("Разовое завтра: подтвердить и отправить ссылку на оплату", "1️⃣"),
    "single_paid_remind": ("Напомнить о разовом занятии", "1️⃣"),
    "single_paid_next": ("Уточнить дату следующего разового", "1️⃣"),
    "single_next": ("Разовые: договориться о следующем занятии", "1️⃣"),
    "request_data": ("Запросить данные", "📋"),
    "manual": ("Задача", "✅"),
    "plan": ("Задача", "📌"),
    "bday": ("День рождения участника команды", "🎂"),
}
from .lifecycle import KINDS as _LIFE_KINDS  # noqa: E402
KINDS.update(_LIFE_KINDS)
# статусы задач в ERP Футболики → виды задач здесь. При переносе все они встают на ответственного за пробные
ERP_STATUSES = {
    "Напоминание о пробном занятии": "trial_confirm",
    "Как прошло пробное": "how_was",
    "Не пришли на ПУ": "no_show",
    "Почему не купили": "why_not_bought",
    "Напоминание о сегодняшнем ПУ": "trial_today",
    "Клиент не отмечен на ПУ": "not_marked",
}
OWNER_KEY = "task_owner_trials"  # один ответственный за все задачи по пробным

DEFAULT_TEMPLATES = {
    "msg_day_before": ("Здравствуйте! Напоминаем: завтра, {date}, в {time} пробное занятие по футболу, {place}.\n"
                       "Ребёнок: {child}. Подтвердите, пожалуйста, что придёте 🙌"),
    "msg_2h_before": "Ждём вас сегодня в {time} ⚽",  # Юлия, 01.11: коротко, остальное уже в сообщении после записи
    "msg_after": trialscripts.AFTER_TEXT,
    # Юлия, 01.11: пропущенный звонок, через 2 минуты клиенту вежливый вопрос (вариант 2, согласован)
    "msg_missed": ("{hello} Это «Футболика» ⚽ Вы нам звонили, а мы не успели взять трубку. "
                   "Напишите, пожалуйста, ваш вопрос, ответим в ближайшее время 🙂"),
    # Юлия, 01.11: сразу после записи на пробное уходит скрипт со всеми данными (дата, время, место, что взять)
    "msg_booked": ("Ваш Чемпион записан на пробное занятие в Футболике⚽️\n\n"
                   "🗓 {date_time}\n📍{place}\n\n"
                   "👜Не забудьте взять с собой:\n-шорты, футболку, носки\n-чистую сменную спортивную обувь\n-бутылку с водой\n\n"
                   "🎁Сразу после пробного занятия Вы можете приобрести абонемент со скидкой 20%\n\n"
                   "{chat_line}"
                   "☎️+7 991 615 09 60"),
}
# ссылка на чат с администратором (как в постах каналов); {admin} в текстах: в MAX ссылка MAX, иначе Telegram
CONTACT_SETTINGS = {"admin_tg": "@footbolika_admin",
                    "admin_max": "https://max.ru/u/f9LHodD0cOLaVBGOxjxYFwTe2vGHnJQswhXNnS5iJbwXlmYEqrQRm4LKGTI"}
AUTO_SETTINGS = {"auto_lesson": "1", "lesson_hours_before": "3", "auto_day_before": "1", "auto_2h_before": "1", "auto_after": "1", "day_before_hour": "11", "auto_booked": "1", "auto_missed": "1"}

# где проходит пробное (из скриптов Юлии); ссылки на чаты родителей вписываются в Настройках → Задачи и автосообщения
# (в код их не кладём: репозиторий публичный, а по ссылке-приглашению можно зайти в чат родителей)
BOOKED_DEFAULTS = [
    (("небо",), {"place": 'ТРЦ "Небо", Авиаторов 3А, на третьем этаже около центрального лифта', "area": "в Солнцево",
                 "tg": "", "max": ""}),
    (("чобот", "запад"), {"place": 'СК "Западный", ул. Чоботовская 4с2, тренеры встретят на входе в спорткомплекс, будут в зелёных футболках',
                          "area": "в Новопеределкино", "tg": "", "max": ""}),
]
WD_FULL = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MON_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
    assignee_id INTEGER REFERENCES users(id),
    due_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',      -- open | done | cancelled
    note TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'auto',      -- auto | erp | manual
    auto_key TEXT UNIQUE,                     -- чтобы одна и та же задача не создавалась дважды
    created_at TEXT NOT NULL,
    done_at TEXT,
    done_by TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_tasks_open ON tasks(status, assignee_id, due_at);

CREATE TABLE IF NOT EXISTS auto_messages (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    trial_at TEXT NOT NULL,
    ts TEXT NOT NULL,
    ok INTEGER NOT NULL DEFAULT 1,
    channel TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    UNIQUE(student_id, kind, trial_at)
);
"""


OLD_2H = ("Сегодня в {time} пробное занятие, ждём вас 🙂\n"
          "Возьмите удобную спортивную форму, сменную обувь и воду. Адрес: {place}")


def init(con):
    con.executescript(SCHEMA)
    if not comms.get(con,'msg_after_approved_20261116'):
        previous=comms.get(con,'msg_after')
        if previous and previous!=trialscripts.AFTER_TEXT:comms.put(con,'msg_after_previous_20261116',previous)
        comms.put(con,'msg_after',trialscripts.AFTER_TEXT)
        comms.put(con,'msg_after_approved_20261116','1')

    # 01.11: сохранённый старый текст «за 2 часа» меняем на короткий (если его не правили вручную)
    if comms.get(con, "msg_2h_before") == OLD_2H:
        comms.put(con, "msg_2h_before", "")


def setting(con, key: str) -> str:
    v = comms.get(con, key)
    if v:
        return v
    return DEFAULT_TEMPLATES.get(key) or trialscripts.DEFAULTS.get(key) or AUTO_SETTINGS.get(key) or CONTACT_SETTINGS.get(key, "")


def admin_link(con, channel: str = "") -> str:
    """Ссылка на чат с администратором под мессенджер родителя."""
    return setting(con, "admin_max") if channel.endswith("max") else setting(con, "admin_tg")


def owner(con) -> int | None:
    """Ответственный за пробные: из настроек, иначе первый оператор, иначе администратор."""
    v = comms.get(con, OWNER_KEY)
    if v and con.execute("SELECT 1 FROM users WHERE id=? AND active=1", (int(v),)).fetchone():
        return int(v)
    r = con.execute("SELECT id FROM users WHERE active=1 AND role='operator' ORDER BY id LIMIT 1").fetchone() or \
        con.execute("SELECT id FROM users WHERE active=1 AND role='admin' ORDER BY id LIMIT 1").fetchone()
    return r[0] if r else None


def _now() -> datetime:
    return datetime.now().replace(second=0, microsecond=0)


def _iso(d: datetime) -> str:
    return d.isoformat(timespec="minutes")


def renewal_covered(con, student_id, due, title):
    """ERP renewal checks refer to the month after the deadline month."""
    if not student_id or title.strip().casefold() != "проверить продление":
        return False
    from . import logic
    target = logic.shift_month(str(due)[:7], 1)
    if con.execute("""SELECT 1 FROM subscriptions WHERE student_id=? AND month=?
            AND status!='cancelled' AND COALESCE(kind,'month')='month' LIMIT 1""",
            (student_id, target)).fetchone():
        return True
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='erp_history_subscriptions'").fetchone():
        return bool(con.execute("SELECT 1 FROM erp_history_subscriptions WHERE student_id=? AND month=? LIMIT 1",
                                (student_id, target)).fetchone())
    return False


def add_task(con, kind: str, student_id: int | None, due: datetime | str, *, auto_key: str | None = None,
             title: str = "", source: str = "auto", assignee: int | None = None, note: str = "") -> int | None:
    due_s = due if isinstance(due, str) else _iso(due)
    if source in ("erp", "erp_rule", "auto") and renewal_covered(con, student_id, due_s, title):
        return None
    cur = con.execute("""INSERT OR IGNORE INTO tasks(kind, title, student_id, assignee_id, due_at, source, auto_key, note,
                         created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                      (kind, title or KINDS.get(kind, ("Задача",))[0], student_id, assignee or owner(con), due_s, source,
                       auto_key, note, _iso(_now())))
    return cur.lastrowid if cur.rowcount else None


def subscription_renewal_plan(con, student_id):
    from . import logic, schedule, closures
    month=con.execute("SELECT MAX(month) FROM subscriptions WHERE student_id=? AND status!='cancelled' AND COALESCE(kind,'month')='month'",(student_id,)).fetchone()[0]
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='erp_history_subscriptions'").fetchone():
        history_month=con.execute("SELECT MAX(month) FROM erp_history_subscriptions WHERE student_id=?",(student_id,)).fetchone()[0]
        if history_month and (not month or history_month > month):
            month = history_month
            names=[r[0] for r in con.execute("""SELECT DISTINCT COALESCE(g.name,'') FROM erp_history_subscriptions h
                LEFT JOIN groups g ON g.id=h.group_id WHERE h.student_id=? AND h.month=? ORDER BY g.name""",(student_id,month)) if r[0]]
            note=f"Абонемент: {logic.month_label(month)}"+(' · '+' + '.join(names) if names else '')+". Перенесён из истории ERP"
            return {'due':month+'-20T23:59','note':note}
    if not month:return None
    plans=[]
    for sub in con.execute("SELECT * FROM subscriptions WHERE student_id=? AND month=? AND status!='cancelled' AND COALESCE(kind,'month')='month'",(student_id,month)):
        enrollment=con.execute('SELECT * FROM enrollments WHERE id=?',(sub['enrollment_id'],)).fetchone()
        try:parts=json.loads(sub['lesson_schedule'] or '[]')
        except (ValueError,TypeError):parts=[]
        if not isinstance(parts,list):parts=[]
        if not parts and enrollment:parts=[{'group_id':enrollment['group_id'],'dates':None}]
        expected,actual,names=set(),set(),[]
        try:excluded=set(json.loads(sub['excluded_dates'] or '[]'))
        except (ValueError,TypeError):excluded=set()
        for part in parts:
            if not isinstance(part,dict):continue
            group=con.execute('SELECT * FROM groups WHERE id=?',(part.get('group_id'),)).fetchone()
            if not group:continue
            names.append(group['name'])
            own=enrollment['weekdays'] if enrollment and enrollment['group_id']==group['id'] else ''
            nominal={d.isoformat() for d in schedule.month_dates(con,group,own,month) if not closures.is_closed(con,group['id'],d)}
            expected.update((group['id'],day) for day in nominal)
            selected=part.get('dates')
            selected=selected if isinstance(selected,list) else nominal
            for day in selected:
                if not isinstance(day,str) or day[:7]!=month or day in excluded:continue
                try:date.fromisoformat(day)
                except ValueError:continue
                if sub['valid_from'] and day<sub['valid_from']:continue
                if sub['valid_until'] and day>sub['valid_until']:continue
                actual.add((group['id'],day))
        partial=bool(expected-actual) or bool(excluded)
        due=month+'-20'
        note=f"Абонемент: {logic.month_label(month)}"+(' · '+' + '.join(dict.fromkeys(names)) if names else '')
        if actual:
            last=max(day for _,day in actual)
            note+=f". Последнее занятие: {date.fromisoformat(last):%d.%m.%Y}"
            if partial:
                due=(date.fromisoformat(last)-timedelta(days=3)).isoformat()
                note+=f". Неполный месяц, включено занятий: {len(actual)}. Проверить продление за 3 календарных дня до последнего занятия: {date.fromisoformat(due):%d.%m.%Y}"
        plans.append((due,note))
    return {'due':min(due for due,_ in plans)+'T23:59','note':'\n'.join(note for _,note in plans)}


def refresh_subscription_renewals(con):
    # Обновляем только открытые автоматические задачи; выполненные не открываем заново.
    for task in con.execute("SELECT * FROM tasks WHERE source='subscription' AND kind='renew' AND status='open' AND auto_key LIKE 'subscription-renew:%'").fetchall():
        plan=subscription_renewal_plan(con,task['student_id'])
        if plan and (task['due_at']!=plan['due'] or task['note']!=plan['note']):
            con.execute('UPDATE tasks SET due_at=?,note=? WHERE id=?',(plan['due'],plan['note'],task['id']))


def schedule_renewal(con, student_id: int) -> int | None:
    """Одна общая задача: неполный месяц — за три дня до последнего занятия."""
    plan=subscription_renewal_plan(con,student_id)
    if not plan:return None
    due,note=plan['due'],plan['note']
    key = f"subscription-renew:{student_id}"
    existing_rows = con.execute("SELECT id FROM tasks WHERE auto_key=? ORDER BY id", (key,)).fetchall()
    existing = existing_rows[0] if existing_rows else None
    for duplicate in existing_rows[1:]:
        con.execute("UPDATE tasks SET status='cancelled',done_at=?,result='Дубликат задачи продления' WHERE id=?",(_iso(_now()),duplicate['id']))
    # Закрываем прежние задачи продажи/продления, сохраняя историю и ручные поручения.
    sale_kinds = {'call_lead', 'no_answer', 'revive', 'renew', 'why_not_bought', 'close_sale', 'first_pay'}
    erp_titles = {'проверить оплату абонемента', 'проверить продление', 'думает'}
    for task in con.execute("SELECT * FROM tasks WHERE student_id=? AND status='open'", (student_id,)).fetchall():
        if task['auto_key'] == key:
            continue
        if task['kind'] in sale_kinds or (task['source'] == 'erp' and task['title'].strip().casefold() in erp_titles):
            con.execute("""UPDATE tasks SET status='cancelled', done_at=?,
                result='Внесён абонемент: запланирована проверка продления' WHERE id=?""", (_iso(_now()), task['id']))
    if existing:
        tid = existing[0]
        con.execute("""UPDATE tasks SET kind='renew', title='Проверить продление', due_at=?,
            assignee_id=NULL, status='open', done_at=NULL, done_by='', result='',note=? WHERE id=?""", (due,note,tid))
    else:
        tid = add_task(con, 'renew', student_id, due, title='Проверить продление',
                       auto_key=key, source='subscription',note=note)
        con.execute("UPDATE tasks SET assignee_id=NULL WHERE id=?", (tid,))
    return tid


def invoice_payment_plan(con, student_id: int) -> dict | None:
    """Earliest unpaid future monthly invoice and its payment deadline."""
    from . import logic, money
    current = logic.month_of(date.today())
    covered = money.balance(con, student_id)["covered"]
    rows = []
    for sub in con.execute("""SELECT id,month,price FROM subscriptions
            WHERE student_id=? AND status!='cancelled' AND COALESCE(kind,'month')='month'
              AND month>? ORDER BY month,id""", (student_id, current)):
        remaining = max(0, sub["price"] - covered.get(sub["id"], 0))
        if remaining:
            rows.append((sub, remaining))
    if not rows:
        return None
    month = rows[0][0]["month"]
    chosen = [(sub, remaining) for sub, remaining in rows if sub["month"] == month]
    deadline_month = logic.shift_month(month, -1)
    year, mon = map(int, deadline_month.split("-"))
    configured = int((con.execute("SELECT value FROM settings WHERE key='renewal_deadline_day'").fetchone() or ["25"])[0] or 25)
    day = min(configured, calendar.monthrange(year, mon)[1])
    remaining = sum(value for _, value in chosen)
    invoice_ids = [sub["id"] for sub, _ in chosen]
    return {
        "month": month,
        "due": f"{year:04d}-{mon:02d}-{day:02d}T23:59",
        "remaining": remaining,
        "invoice_ids": invoice_ids,
        "note": f"Счёт {'№' if len(invoice_ids) == 1 else '№№'}{', '.join(map(str, invoice_ids))} на {logic.month_label(month)}. Осталось оплатить {remaining} ₽",
    }


def sync_subscription_payment_tasks(con, student_id: int) -> int | None:
    """Move renewal -> invoice payment control -> next renewal, idempotently."""
    plan = invoice_payment_plan(con, student_id)
    key = f"subscription-payment:{student_id}"
    payment_rows = con.execute("""SELECT * FROM tasks WHERE student_id=? AND
        (auto_key=? OR (status='open' AND trim(title)='Проверить оплату абонемента'))
        ORDER BY CASE WHEN auto_key=? THEN 0 ELSE 1 END,id""", (student_id, key, key)).fetchall()
    if not plan:
        keyed = [task for task in payment_rows if task["auto_key"] == key]
        if not keyed:
            return None
        for task in keyed:
            if task["status"] == "open":
                con.execute("""UPDATE tasks SET status='done',done_at=?,done_by='CRM',
                    result='Счёт полностью оплачен' WHERE id=?""", (_iso(_now()), task["id"]))
        return schedule_renewal(con, student_id)

    primary = payment_rows[0] if payment_rows else None
    for duplicate in payment_rows[1:]:
        if duplicate["status"] == "open":
            con.execute("UPDATE tasks SET status='cancelled',done_at=?,result='Дубликат контроля оплаты' WHERE id=?",
                        (_iso(_now()), duplicate["id"]))
    # The invoice replaces the preceding renewal check, but not unrelated manual work.
    for task in con.execute("SELECT * FROM tasks WHERE student_id=? AND status='open'", (student_id,)).fetchall():
        if primary and task["id"] == primary["id"]:
            continue
        if task["kind"] == "renew" or task["title"].strip().casefold() == "проверить продление":
            con.execute("""UPDATE tasks SET status='cancelled',done_at=?,
                result='Счёт выставлен: ожидаем оплату' WHERE id=?""", (_iso(_now()), task["id"]))
    if primary:
        tid = primary["id"]
        con.execute("""UPDATE tasks SET kind='payment_check',title='Проверить оплату абонемента',
            due_at=?,status='open',done_at=NULL,done_by='',result='',note=?,auto_key=? WHERE id=?""",
            (plan["due"], plan["note"], key, tid))
    else:
        tid = add_task(con, "payment_check", student_id, plan["due"], title="Проверить оплату абонемента",
                       auto_key=key, source="subscription", note=plan["note"])
    con.execute("UPDATE tasks SET assignee_id=NULL WHERE id=?", (tid,))
    return tid


def import_erp_task(con, erp_status: str, student_id: int, due: str, note: str = "") -> int | None:
    """Перенести задачу ERP без переименования; kind нужен только для колонки CRM."""
    title = erp_status.strip()
    kind = ERP_STATUSES.get(title, "manual")
    return add_task(con, kind, student_id, due, source="erp", note=note,
                    title=title, auto_key=f"erp:{title}:{student_id}:{due}")


# ---------------- автоматика: задачи и сообщения ----------------

def _place(con, s: dict) -> str:
    if not s.get("group_id"):
        return "в школе"
    g = con.execute("SELECT g.name, b.name FROM groups g JOIN branches b ON b.id=g.branch_id WHERE g.id=?",
                    (s["group_id"],)).fetchone()
    return f"{g[1]}" if g else "в школе"


def render_template(con, key: str, s: dict, channel: str = "") -> str:
    branch_text=trialscripts.render(con,key,s,channel)
    if branch_text is not None:return branch_text
    t = datetime.fromisoformat(s["trial_at"])
    child = s["name"] if "заявка" not in s["name"].lower() else "ребёнка"
    return (setting(con, key).replace("{child}", child.split()[0]).replace("{time}", t.strftime("%H:%M"))
            .replace("{date}", f"{t.day:02d}.{t.month:02d}").replace("{place}", _place_full(con, s))
            .replace("{admin}", admin_link(con, channel)))


def best_conversation(con, family_id: int) -> int | None:
    """Куда писать семье: сохраняем Telegram; для нового контакта MAX → Telegram → WhatsApp.
    Выдуманным (тестовым) семьям не пишем никогда: их случайные номера могут оказаться чужими."""
    fam = con.execute("SELECT * FROM families WHERE id=?", (family_id or 0,)).fetchone()
    if not fam or ("fake" in fam.keys() and fam["fake"]):
        return None
    primary=con.execute('SELECT * FROM parents WHERE family_id=? AND is_primary=1 LIMIT 1',(family_id,)).fetchone()
    if primary:
        username=(primary['messenger'] or '').strip().lstrip('@').casefold()
        r=con.execute("""SELECT id FROM conversations WHERE family_id=? AND external_id NOT LIKE 'demo-%'
            AND ((phone_norm!='' AND phone_norm=?) OR (username!='' AND lower(ltrim(username,'@'))=?))
            ORDER BY CASE channel WHEN 'wazzup_telegram' THEN 0 WHEN 'telegram' THEN 0
                                  WHEN 'wazzup_max' THEN 1 WHEN 'max' THEN 1
                                  WHEN 'whatsapp' THEN 2 ELSE 99 END,
                     last_at IS NULL,last_at DESC LIMIT 1""",(family_id,primary['phone_norm'],username)).fetchone()
    else:
        r = con.execute("""SELECT id FROM conversations WHERE family_id=? AND external_id NOT LIKE 'demo-%'
                       ORDER BY CASE channel WHEN 'wazzup_telegram' THEN 0 WHEN 'telegram' THEN 0
                                             WHEN 'wazzup_max' THEN 1 WHEN 'max' THEN 1
                                             WHEN 'whatsapp' THEN 2 ELSE 99 END,
                                last_at IS NULL, last_at DESC LIMIT 1""", (family_id,)).fetchone()
    if r:
        current=dict(con.execute('SELECT * FROM conversations WHERE id=?',(r[0],)).fetchone())
        conversations=[dict(c) for c in con.execute('SELECT * FROM conversations WHERE family_id=?',(family_id,))]
        preferred=comms.preferred_reply(con,current,conversations)
        if current['channel']!='telegram' or preferred!=current['id']:return preferred
        if not comms.status(con)['wazzup_telegram']:
            return current['id']
        if primary and primary['phone_norm'] and current.get('phone_norm')==primary['phone_norm'] and comms.status(con)['wazzup_telegram']:
            if any(c['channel']=='wazzup_telegram' and c.get('phone_norm')==primary['phone_norm'] for c in conversations):return None
            cid=comms.start_wazzup_telegram(con,primary['phone'])
            existing=con.execute('SELECT family_id FROM conversations WHERE id=?',(cid,)).fetchone()
            if existing and existing[0] not in (None,family_id):return None
            comms.link(con,cid,family_id);return cid
        return None
    available=comms.status(con)
    starters=(("wazzup_max",comms.start_wazzup_max),("wazzup_telegram",comms.start_wazzup_telegram),("whatsapp",comms.start_whatsapp))
    for channel,start in starters:
        if available[channel]:
            p = con.execute("SELECT phone FROM parents WHERE family_id=? AND phone != '' ORDER BY is_primary DESC,id LIMIT 1", (family_id,)).fetchone()
            if p and (not primary or primary['phone']==p[0]):
                cid = start(con, p[0])
                comms.link(con, cid, family_id)
                return cid
    return None


def _place_full(con, s: dict) -> str:
    """Где проходит пробное: адрес и как найти из Настроек → Скрипты, иначе название филиала."""
    g = con.execute("SELECT branch_id FROM groups WHERE id=?", (s.get("group_id") or 0,)).fetchone()
    pl = booked_places(con).get(str(g[0]) if g else "", {})
    return pl.get("place") or _place(con, s)


def booked_places(con) -> dict:
    """Место и чат родителей по каждому филиалу: сохранённое в Настройках, иначе из скриптов Юлии."""
    try:
        saved = json.loads(comms.get(con, "booked_places") or "{}")
    except ValueError:
        saved = {}
    out = {}
    for b in con.execute("SELECT id, name, address FROM branches WHERE active=1 ORDER BY name"):
        base = next((v for keys, v in BOOKED_DEFAULTS if any(k in b["name"].lower() for k in keys)),
                    {"place": b["name"] + (", " + b["address"] if b["address"] else ""), "area": "", "tg": "", "max": ""})
        out[str(b["id"])] = {"name": b["name"]} | base | {k: v for k, v in (saved.get(str(b["id"])) or {}).items() if k in base}
    return out


def booked_text(con, s: dict, channel: str = "") -> str:
    branch_text=trialscripts.render(con,'msg_booked',s,channel)
    if branch_text is not None:return branch_text
    t = datetime.fromisoformat(s["trial_at"])
    g = con.execute("SELECT branch_id FROM groups WHERE id=?", (s.get("group_id") or 0,)).fetchone()
    pl = booked_places(con).get(str(g[0]) if g else "", {})
    # в MAX ссылка на чат родителей в MAX, иначе на Telegram (Юлия, 01.11); нет ссылки: строки нет
    link = pl.get("max") if channel.endswith("max") else pl.get("tg")
    chat_line = f"ℹ️Группа для родителей футболистов филиала {pl.get('area', '')} - {link}\n\n".replace("  ", " ") if link else ""
    dt = f"{WD_FULL[t.weekday()].capitalize()}, {t.day} {MON_GEN[t.month - 1]}, в {t.strftime('%H:%M')}"
    return (setting(con, "msg_booked").replace("{date_time}", dt).replace("{place}", pl.get("place") or _place(con, s))
            .replace("{chat_line}", chat_line).replace("{admin}", admin_link(con, channel)).replace("{child}", (s["name"] or "").split()[0] if s.get("name") else ""))


def send_booked(con, sid: int) -> dict:
    """Сразу после записи на пробное: скрипт со всеми данными в мессенджер семьи (один раз на каждое время пробного)."""
    s = dict(con.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone())
    if not comms.auto_live(con) or setting(con, "auto_booked") != "1" or not s.get("trial_at") or s["funnel"] != "sales":
        return {"skipped": True}
    if con.execute("SELECT 1 FROM auto_messages WHERE student_id=? AND kind='booked' AND trial_at=?", (sid, s["trial_at"])).fetchone():
        return {"skipped": True}
    cid = best_conversation(con, s["family_id"])
    ok, err, channel, text = True, "", "", ""
    if not cid:
        ok, err = False, "у семьи нет переписки в мессенджере"
    else:
        channel = con.execute("SELECT channel FROM conversations WHERE id=?", (cid,)).fetchone()[0]
        try:
            text = booked_text(con, s, channel)
            comms.send(con, cid, text, {"name": "Автосообщение"})
        except comms.CommsError as e:
            ok, err = False, str(e)
    con.execute("INSERT OR IGNORE INTO auto_messages(student_id, kind, trial_at, ts, ok, channel, error) VALUES (?,?,?,?,?,?,?)",
                (sid, "booked", s["trial_at"], _iso(_now()), 1 if ok else 0, channel, err))
    con.execute("INSERT INTO history(student_id, ts, user_name, kind, text) VALUES (?,?,?,?,?)",
                (sid, _iso(_now()), "Автосообщение", "message",
                 "Ушло сообщение о записи на пробное: дата, время, место, что взять" if ok else
                 f"Не ушло сообщение о записи на пробное: {err}. Напишите родителю вручную"))
    return {"ok": ok, "error": err}


MISSED_WAIT_MIN = 2      # ждём, вдруг админ сразу перезвонит
MISSED_HOURS = (9, 21)   # ночью не пишем: звонок после 21:00 получит сообщение в 9:00


def missed_call_messages(con, now: datetime) -> int:
    """Пропущенный звонок не обработан 2 минуты: клиенту вежливый вопрос в мессенджер (один раз в день на номер)."""
    if not comms.auto_live(con) or setting(con, "auto_missed") != "1":
        return 0
    if not (MISSED_HOURS[0] <= now.hour < MISSED_HOURS[1]):
        return 0
    staff = {comms.normalize_phone(r[0] or "") for r in con.execute("SELECT phone FROM users")} if \
        "phone" in {r[1] for r in con.execute("PRAGMA table_info(users)")} else set()
    sent = 0
    for k in con.execute("""SELECT * FROM calls WHERE direction='in' AND status IN ('missed','busy') AND seen=0
                            AND started_at <= ? AND started_at >= ? ORDER BY started_at""",
                         (_iso(now - timedelta(minutes=MISSED_WAIT_MIN)), _iso(now - timedelta(hours=14)))).fetchall():
        p = k["phone_norm"] or comms.normalize_phone(k["phone"] or "")
        if len(p) < 11 or p in staff or (k["external_id"] or "").startswith(("fake-", "demo")):
            continue
        if con.execute("""SELECT 1 FROM parents pa JOIN families f ON f.id=pa.family_id WHERE pa.phone_norm=? AND f.fake=1""", (p,)).fetchone():
            continue  # номер выдуманного клиента
        if con.execute("""SELECT 1 FROM calls WHERE phone_norm=? AND started_at > ? AND (status='answered' OR direction='out')""",
                       (p, k["started_at"])).fetchone():
            continue  # уже поговорили: клиент дозвонился или ему перезвонили
        day = now.date().isoformat()
        key = f"missed:{p}:{day}"  # раз в день на номер (таблица отметок из ownerbot)
        if con.execute("SELECT 1 FROM owner_sent WHERE user_id=-1 AND key=?", (key,)).fetchone():
            continue
        par = con.execute("SELECT name, family_id FROM parents WHERE phone_norm=? ORDER BY id LIMIT 1", (p,)).fetchone()
        first = (par["name"] or "").split()[0] if par and par["name"] else ""
        hello = f"{first}, здравствуйте!" if first and not any(x in first.lower() for x in ("заявка", "клиент", "мама", "папа")) else "Здравствуйте!"
        text = setting(con, "msg_missed").replace("{hello}", hello)
        cid, ok, err, channel = None, True, "", ""
        try:
            cid = best_conversation(con, par["family_id"]) if par else None
            if not cid and comms.status(con)["whatsapp"]:
                cid = comms.start_whatsapp(con, p)
            if not cid:
                ok, err = False, "нет мессенджера для этого номера"
            else:
                channel = con.execute("SELECT channel FROM conversations WHERE id=?", (cid,)).fetchone()[0]
                comms.send(con, cid, text, {"name": "Автосообщение"})
        except comms.CommsError as e:
            ok, err = False, str(e)
        sid = con.execute("SELECT id FROM students WHERE family_id=? ORDER BY id LIMIT 1", (par["family_id"],)).fetchone() if par else None
        con.execute("INSERT OR IGNORE INTO owner_sent(user_id, key, sent_at) VALUES (-1,?,?)", (key, _iso(now)))
        if sid:
            con.execute("INSERT INTO history(student_id, ts, user_name, kind, text) VALUES (?,?,?,?,?)",
                        (sid[0], _iso(now), "Автосообщение", "message",
                         "После пропущенного звонка ушло сообщение: «Вы нам звонили…»" if ok else f"После пропущенного звонка сообщение не ушло: {err}"))
        sent += ok
    return sent


def send_auto(con, s: dict, kind: str, tpl: str) -> dict:
    """Отправить автосообщение один раз за пробное. Возвращает, что получилось."""
    if not comms.auto_live(con):  # автосообщения клиентам ещё не запущены руководителем
        return {"skipped": True}
    if con.execute("SELECT 1 FROM auto_messages WHERE student_id=? AND kind=? AND trial_at=?",
                   (s["id"], kind, s["trial_at"])).fetchone():
        return {"skipped": True}
    # 01.11: напоминание ушло лиду, который уже отказался, и с «Адрес: в школе». Теперь не пишем, если
    # отказался/не дошёл/отложил, и если не выбрана группа (неизвестно, куда приходить)
    cur = con.execute("SELECT stage, group_id, dnd, archived FROM students WHERE id=?", (s["id"],)).fetchone()
    if not cur or cur["stage"] in ("refused", "missed_trial", "later", "bought") or cur["dnd"] or cur["archived"]:
        return {"skipped": True}
    if not cur["group_id"]:
        con.execute("INSERT OR IGNORE INTO auto_messages(student_id, kind, trial_at, ts, ok, channel, error) VALUES (?,?,?,?,0,'',?)",
                    (s["id"], kind, s["trial_at"], _iso(_now()), "не выбрана группа"))
        con.execute("INSERT INTO history(student_id, ts, user_name, kind, text) VALUES (?,?,?,?,?)",
                    (s["id"], _iso(_now()), "Автосообщение", "message", "Напоминание о пробном не ушло: не выбрана группа. Выберите группу или напишите вручную"))
        return {"ok": False, "error": "не выбрана группа"}
    cid = best_conversation(con, s["family_id"])
    ok, err, channel = True, "", ""
    text = ""
    if not cid:
        ok, err = False, "у семьи нет переписки в мессенджере"
    else:
        channel = con.execute("SELECT channel FROM conversations WHERE id=?", (cid,)).fetchone()[0]
        try:
            text = render_template(con, tpl, s, channel)
            comms.send(con, cid, text, {"name": "Автосообщение"})
        except comms.CommsError as e:
            ok, err = False, str(e)
    con.execute("INSERT OR IGNORE INTO auto_messages(student_id, kind, trial_at, ts, ok, channel, error) VALUES (?,?,?,?,?,?,?)",
                (s["id"], kind, s["trial_at"], _iso(_now()), 1 if ok else 0, channel, err))
    labels = {"day_before": "за день", "2h_before": "за 2 часа", "after": "после пробного"}
    con.execute("INSERT INTO history(student_id, ts, user_name, kind, text) VALUES (?,?,?,?,?)",
                (s["id"], _iso(_now()), "Автосообщение", "message",
                 (f"Ушло сообщение {labels.get(kind, '')}: «{text[:80]}…»" if ok else
                  f"Не ушло сообщение {labels.get(kind, '')}: {err}. Напишите вручную")))
    return {"ok": ok, "error": err}


_PAYLINK_SYNC = datetime.min


def request_missing_data(con, day: date) -> int:
    """Независимая задача по обязательным данным; email родитель вводит при оплате."""
    from . import logic, smart
    made = 0

    # Почта для чека вводится родителем по ссылке и после успешного создания счёта
    # сохраняется в основной контакт. Старые автозадачи только из-за почты больше
    # не должны оставаться в планере.
    for task in con.execute(
        """SELECT id,student_id FROM tasks
           WHERE kind='request_data' AND status='open'
             AND source='data_quality' AND auto_key LIKE 'request_data:%'"""
    ).fetchall():
        info = smart.required_info(con, task['student_id'], 'sale')
        required = [key for key in info['missing'] if key != 'email']
        if not required:
            con.execute(
                """UPDATE tasks SET status='cancelled',done_at=?,done_by='CRM',
                   result='Почту родитель укажет при следующей оплате по ссылке'
                   WHERE id=?""",
                (_iso(_now()), task['id']),
            )

    for student in con.execute("SELECT id FROM students WHERE archived=0 AND funnel='members' AND stage!='left'").fetchall():
        sid = student['id']
        active = False
        for sub in con.execute("SELECT month,valid_from,valid_until FROM subscriptions WHERE student_id=? AND status!='cancelled' AND COALESCE(kind,'month')='month'", (sid,)):
            start, end = logic.month_bounds(sub['month'])
            if (sub['valid_from'] or start.isoformat()) <= day.isoformat() <= (sub['valid_until'] or end.isoformat()):
                active = True
                break
        if not active:
            continue
        info = smart.required_info(con, sid, 'sale')
        missing = [key for key in info['missing'] if key != 'email']
        if not missing:
            continue
        note = 'Необходимо запросить и заполнить: ' + ', '.join(info['labels'][k] for k in missing) + '. После выполнения закройте задачу вручную.'
        existing = con.execute(
            """SELECT id FROM tasks WHERE student_id=? AND kind='request_data'
               AND status='open' AND source='data_quality'
               AND auto_key LIKE 'request_data:%' ORDER BY id LIMIT 1""",
            (sid,),
        ).fetchone()
        if existing:
            con.execute("UPDATE tasks SET note=? WHERE id=?", (note, existing['id']))
            continue
        # Постоянный ключ намеренно сохраняется и после ручного закрытия.
        made += bool(add_task(con, 'request_data', sid, day.isoformat(), auto_key=f'request_data:{sid}', source='data_quality', note=note))
    return made


def run(con, now: datetime | None = None, send: bool = True) -> dict:
    """Один проход: создать задачи по пробным, отправить автосообщения, закрыть неактуальные задачи."""
    now = now or _now()
    from . import erpstatus
    erpstatus.sync_all(con, now.date())
    from . import planner, program, merch
    request_missing_data(con, now.date())
    merch.keep_order_task_today(con, now.date())
    planner.generate(con, now.date())
    program.apply_pending(con, now.date())
    refresh_subscription_renewals(con)
    from . import pu, waitlist, ownerbot, lifecycle
    pu.tick(con, now)
    lifecycle.tick(con, now)  # задачи на всех этапах, где клиент может застрять
    try:
        if send:
            ownerbot.heartbeat(con)  # сторож на мосту: если сигнал пропал, руководителю в Telegram
            ownerbot.tick(con, now)
            missed_call_messages(con, now)  # клиенту после пропущенного звонка (только после запуска автосообщений)  # красное руководителю в Telegram, напоминания в «Службу заботы»
    except Exception:  # noqa: BLE001 — уведомления не должны ломать проход
        pass
    from . import coachreport, schedule
    coachreport.tick(con, now)
    schedule.apply_versions(con, now.date())  # с даты изменения в карточке группы новое расписание  # несданные отчёты о смене: 30 минут и 2 часа
    waitlist._sync_done(con)
    program.price_notice(con, now.date())
    global _PAYLINK_SYNC
    if (now - _PAYLINK_SYNC).total_seconds() >= 600:  # оплаты по ссылкам PayKeeper: раз в 10 минут
        _PAYLINK_SYNC = now
        from . import paylinks
        try:
            paylinks.sync(con, now.date())
        except Exception:  # noqa: BLE001 — PayKeeper недоступен: попробуем в следующий раз
            pass
    from . import chains, channels
    if send:
        try:
            channels.tick(con)  # запланированные посты в каналы филиалов
        except Exception:  # noqa: BLE001
            pass
    chains.tick(con, now, send)  # автоцепочки сообщений (включаются в Настройках)
    from . import reminders
    try:
        reminders.tick(con, now, send)  # напоминание перед каждым занятием: абонемент, разовые, отработки (04.11)
    except Exception:  # noqa: BLE001 — напоминания не должны ломать проход
        pass
    st = {k: setting(con, k) for k in AUTO_SETTINGS}
    trial_min = int((con.execute("SELECT value FROM settings WHERE key='trial_minutes'").fetchone() or ["60"])[0])
    made = sent = 0
    rows = [dict(r) for r in con.execute(
        """SELECT * FROM students WHERE funnel='sales' AND trial_at IS NOT NULL AND archived=0 AND historical_trial=0
           AND COALESCE(erp_id,'')=''
           AND stage NOT IN ('bought','refused') AND trial_at >= ?""", (_iso(now - timedelta(days=3)),))]
    for s in rows:
        t = datetime.fromisoformat(s["trial_at"])
        end = t + timedelta(minutes=trial_min)
        key = f"{s['id']}:{s['trial_at']}"
        day_before = datetime.combine(t.date() - timedelta(days=1), datetime.min.time()) + timedelta(hours=int(st["day_before_hour"]))
        # задачи
        if t > now:
            # ERP: задача звонка возникает при записи на ПУ, срок — за 23:59 до занятия.
            made += bool(add_task(con, "trial_confirm", s["id"], max(now, t - timedelta(hours=23, minutes=59)),
                                  auto_key=f"trial_confirm:{key}"))
        # «не пришёл» и «нет отметки 2 часа» ведёт этап «Не дошёл до ПУ» (crm/pu.py)
        if s["trial_came"] == 1:  # ERP: событие «Пришли на ПУ» создаёт «Почему не купили» без задержки.
            marked = s.get("trial_marked_at") or _iso(now)
            made += bool(add_task(con, "why_not_bought", s["id"], marked[:16], title="Почему не купили",
                                  auto_key=f"why_not_bought:{key}"))
        # сообщения
        if send and st["auto_day_before"] == "1" and now >= day_before and now < t - timedelta(hours=3) and t.date() > now.date():
            sent += bool(send_auto(con, s, "day_before", "msg_day_before").get("ok"))
        if send and st["auto_2h_before"] == "1" and t - timedelta(hours=2) <= now < t:
            sent += bool(send_auto(con, s, "2h_before", "msg_2h_before").get("ok"))
        if send and st["auto_after"] == "1" and s["trial_came"] != 0 and end + timedelta(minutes=10) <= now < end + timedelta(hours=3):
            sent += bool(send_auto(con, s, "after", "msg_after").get("ok"))
    # пробное перенесли или клиент купил/отказался — старые автозадачи по нему больше не нужны
    for tid, akey, sid in con.execute("""SELECT t.id, t.auto_key, t.student_id FROM tasks t WHERE t.status='open'
                                         AND t.source='auto' AND t.auto_key IS NOT NULL""").fetchall():
        s = con.execute("SELECT trial_at, stage, funnel, trial_came FROM students WHERE id=?", (sid,)).fetchone()
        kind, rest = akey.split(":", 1)
        stale = (not s or s["trial_at"] is None or not rest.endswith(":" + s["trial_at"])
                 or s["funnel"] != "sales" or s["stage"] in ("bought", "refused")
                 or (kind == "not_marked" and s["trial_came"] is not None)
                 or (kind in ("how_was", "why_not_bought") and s["trial_came"] == 0))
        if stale:
            con.execute("UPDATE tasks SET status='cancelled', done_at=? WHERE id=?", (_iso(now), tid))
    from . import singles
    singles.sync_paid_tasks(con, now)
    return {"tasks": made, "sent": sent}


# ---------------- для интерфейса ----------------

def _task_out(con, r) -> dict:
    d = dict(r)
    d["icon"] = KINDS.get(d["kind"], ("", "✅"))[1]
    d["manual"] = d["kind"] in MANUAL_KINDS
    d["client_task"] = bool(d.get("student_id") or d.get("family_id"))
    if d["student_id"]:
        s = con.execute("""SELECT s.id, s.name, s.trial_at, s.trial_came, s.stage, s.funnel, s.converted_at, s.created_at, s.erp_id, s.family_id, f.name AS family,
                           (SELECT p.phone FROM parents p WHERE p.family_id=s.family_id ORDER BY p.is_primary DESC,p.id LIMIT 1) AS phone,
                           (SELECT p.name FROM parents p WHERE p.family_id=s.family_id ORDER BY p.is_primary DESC,p.id LIMIT 1) AS parent
                           FROM students s JOIN families f ON f.id=s.family_id WHERE s.id=?""", (d["student_id"],)).fetchone()
        d["student"] = dict(s) if s else None
        if s and s["trial_at"]:
            d["auto"] = [dict(x) for x in con.execute(
                "SELECT kind, ts, ok, channel, error FROM auto_messages WHERE student_id=? AND trial_at=? ORDER BY ts",
                (s["id"], s["trial_at"]))]
    a = con.execute("SELECT name FROM users WHERE id=?", (d["assignee_id"],)).fetchone() if d["assignee_id"] else None
    d["assignee"] = a[0] if a else ""
    from . import erpworkflow
    d["erp_outcomes"] = erpworkflow.options(d)
    d["erp_due_dates"] = {outcome: erpworkflow.due_at(con, outcome, d.get("student_id")).date().isoformat()
                          for outcome in d["erp_outcomes"] if outcome != "Закрыть задачу"}
    return d


def _planner_privacy(con, user: dict, alias: str = "") -> tuple[str, list[int]]:
    from . import planner
    if user["role"] == "admin":
        return "", []
    ids = [staff["id"] for staff in planner.visible_staff(con, user)]
    placeholders = ",".join("?" for _ in ids)
    shared = f" OR {alias}student_id IS NOT NULL OR {alias}family_id IS NOT NULL" if user["role"] == "operator" else ""
    return f" AND ({alias}kind!='plan'{shared} OR {alias}assignee_id IN ({placeholders}))", ids


def my_tasks(con, user: dict, scope: str = "mine") -> dict:
    run(con, send=False)  # задачи свежие сразу; сообщения отправляет фоновый процесс
    now = _now()
    end_today = datetime.combine(date.today(), datetime.max.time()).isoformat(timespec="minutes")
    end_tomorrow = (datetime.combine(date.today() + timedelta(days=1), datetime.max.time())).isoformat(timespec="minutes")
    from . import planner
    boss = planner.can_manage(con, user)
    where, args = "status='open' AND due_at <= ?", [end_tomorrow]
    if scope == "mine" or not boss:
        if user.get("role") in ("admin", "operator"):  # «Перезаписать на ПУ» видят все админы и операторы
            where += " AND (assignee_id=? OR student_id IS NOT NULL OR family_id IS NOT NULL)"
        else:
            where += " AND assignee_id=?"
        args.append(user["id"])
    privacy, visible_ids = _planner_privacy(con, user)
    rows = [_task_out(con, r) for r in con.execute(
        f"SELECT * FROM tasks WHERE {where}{privacy} ORDER BY substr(due_at,1,10),priority DESC,due_at,id", (*args, *visible_ids))]
    groups = {"overdue": [], "today": [], "tomorrow": []}
    for t in rows:
        if t["due_at"] < _iso(now - timedelta(minutes=30)) and t["due_at"][:10] < date.today().isoformat():
            groups["overdue"].append(t)
        elif t["due_at"] <= end_today:
            groups["today"].append(t)
        else:
            groups["tomorrow"].append(t)
    done_where, done_args = "status='done' AND done_at>=?", [date.today().isoformat()]
    if scope == "mine" or not boss:
        done_where += " AND (assignee_id=? OR student_id IS NOT NULL OR family_id IS NOT NULL)" if user["role"] in ("admin","operator") else " AND assignee_id=?"
        done_args.append(user["id"])
    done_today = con.execute(f"SELECT COUNT(*) FROM tasks WHERE {done_where}{privacy}", (*done_args,*visible_ids)).fetchone()[0]
    team = []
    if boss:
        for u in con.execute("SELECT id, name FROM users WHERE active=1 ORDER BY role, id"):
            c = con.execute("""SELECT SUM(due_at <= ?), SUM(due_at < ?) FROM tasks WHERE status='open' AND assignee_id=?""",
                            (end_today, date.today().isoformat(), u["id"])).fetchone()
            if c[0]:
                team.append({"id": u["id"], "name": u["name"], "open": c[0] or 0, "overdue": c[1] or 0})
    return {"groups": groups, "done_today": done_today, "team": team, "scope": scope, "can_manage": boss}


def edit(con, tid: int, data: dict, user: dict) -> dict:
    """Правка открытой задачи из карточки или раздела «Задачи»: что сделать, срок, заметка, исполнитель.
    Передать можно любому действующему участнику команды (в т.ч. тренеру); править могут админы и операторы (проверка в маршруте)."""
    t = con.execute("SELECT * FROM tasks WHERE id=? AND status='open'", (tid,)).fetchone()
    if not t:
        raise comms.CommsError("Задача не найдена или уже закрыта")
    client = bool(t["student_id"] or t["family_id"])
    if t["kind"] == "plan" and not client:
        from . import planner
        if not planner.can_change_plan(con, user, t["assignee_id"]):
            raise comms.CommsError("Нет доступа к задаче")
        if data.get("assignee_id") and not planner.can_change_plan(con, user, int(data["assignee_id"])):
            raise comms.CommsError("Нет доступа к планеру участника команды")
    sets, args, notes = [], [], []
    if client and t["assignee_id"] is not None:
        sets.append("assignee_id=NULL")
    if "assignee_id" in data and not client:
        uid = int(data.get("assignee_id") or 0)
        u = con.execute("SELECT id, name FROM users WHERE id=? AND active=1", (uid,)).fetchone()
        if not u:
            raise comms.CommsError("Выберите участника команды из списка")
        if uid != t["assignee_id"]:
            sets.append("assignee_id=?"); args.append(uid)
            notes.append(f"Задача «{t['title']}» передана: {u['name']}")
    if "title" in data:
        title = (data.get("title") or "").strip()[:200]
        if not title:
            raise comms.CommsError("Напишите, что нужно сделать")
        if title != t["title"]:
            sets.append("title=?"); args.append(title)
            notes.append(f"Задача «{t['title']}» переименована: «{title}»")
    if "due_at" in data:
        try:
            due = client_due(data.get("due_at")) if client else datetime.fromisoformat(str(data.get("due_at"))[:16]).isoformat(timespec="minutes")
        except ValueError:
            raise comms.CommsError("Неверный срок") from None
        if due != t["due_at"][:16]:
            sets.append("due_at=?"); args.append(due)
            notes.append(f"Задача «{t['title']}»: срок {due[8:10]}.{due[5:7]}" + ("" if client else " "+due[11:16]))
    if "priority" in data:
        if data["priority"] not in (True,False,0,1):
            raise comms.CommsError("Неверный приоритет")
        priority=int(bool(data["priority"]))
        sets.append("priority=?");args.append(priority)
        if priority != t["priority"]:
            notes.append(f"Задача «{t['title']}»: " + ("важная" if priority else "обычный приоритет"))
    if "note" in data:
        note = (data.get("note") or "").strip()[:1000]
        if note != (t["note"] or ""):
            sets.append("note=?"); args.append(note)
    if sets:
        con.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", (*args, tid))
        if t["student_id"]:
            from . import services as sv
            for n in notes:
                sv.log(con, t["student_id"], n, user, "note")
    return _task_out(con, con.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone())


MANUAL_KINDS = ("manual", "plan")  # поставил участник команды (карточка клиента или «Планер»); остальные CRM ставит сама по цепочке событий


def manual_board(con, user: dict, scope: str = "mine") -> dict:
    """Воронка «Ручные задачи»: задача появляется в свой день. Просрочено / Сегодня. Выполненные не показываем (Юлия, 01.11)."""
    from . import planner
    boss = planner.can_manage(con, user)
    today = date.today().isoformat()
    who = "" if scope == "all" and boss else " AND (assignee_id=? OR student_id IS NOT NULL OR family_id IS NOT NULL)" if user["role"] in ("admin","operator") else " AND assignee_id=?"
    args = [] if who == "" else [user["id"]]
    privacy, visible_ids = _planner_privacy(con, user)
    who += privacy
    args += visible_ids
    kinds = ",".join("?" * len(MANUAL_KINDS))
    rows = lambda where, extra, order: [_task_out(con, r) for r in con.execute(
        f"SELECT * FROM tasks WHERE kind IN ({kinds}) AND {where}{who} ORDER BY substr(due_at,1,10),priority DESC,{order},id", (*MANUAL_KINDS, *extra, *args))]
    return {"columns": [
        {"key": "overdue", "title": "Просрочено", "cards": rows("status='open' AND substr(due_at,1,10) < ?", [today], "due_at")},
        {"key": "today", "title": "Сегодня", "cards": rows("status='open' AND substr(due_at,1,10) = ?", [today], "due_at")},
    ], "scope": scope if boss else "mine", "can_manage": boss,
        "later": con.execute(f"SELECT COUNT(*) FROM tasks WHERE kind IN ({kinds}) AND status='open' AND substr(due_at,1,10) > ?{who}",
                             (*MANUAL_KINDS, today, *args)).fetchone()[0]}


# «Задачи как в ERP» (Юлия, 03.11): общий список для всех администраторов, колонки по статусам ERP,
# переключатели Просрочено / Сегодня / Завтра. Наши новые виды задач, которых в ERP не было, в колонке «Другое»,
# ручные в «Ручных»: ни одна задача не теряется
ERP_COLUMNS = [
    ("unpaid_visit", "Был на занятии без оплаты"),  # красная, первая (Юлия, 04.11)
    ("new_application", "Новая заявка"),
    ("old_unprocessed_lead", "Необработанные ранее"),
    ("trial_confirm", "Напоминание о ПУ (звонок)"),
    ("trial_today", "Напоминание о сегодняшнем ПУ (звонок и смс)"),
    ("not_marked", "Нет отметки о ПУ"),
    ("how_was", "Как прошло ПУ?"),
    ("erp_how_was_sms", "Как прошло ПУ?"),
    ("erp_how_was_today", "Как прошло ПУ?"),
    ("no_show", "Не пришли на ПУ"),
    ("why_not_bought", "Почему не купили"),
    ("think", "Думает"),
    ("renew", "Проверить продление"),
    ("request_data", "Запрос данных"),
    ("reinvite_pu", "Приглашение на ПУ повторно"),
    ("single", "Разовые занятия"),
]
THINK_KINDS = ("close_sale",)


NEW_LEAD_KINDS = ("call_lead", "no_answer", "revive", "reinvite")


def _erp_col(t: dict) -> tuple[str, str]:
    kind, title = t["kind"], (t.get("title") or "").strip()
    st = t.get("student") or {}
    # «Новая заявка» — очередь ещё не обработанных заявок, а не только сегодняшних.
    # Вчерашняя заявка остаётся здесь, пока карточку не сдвинули с первого этапа.
    if kind in NEW_LEAD_KINDS and st.get("funnel") == "sales" and not st.get("trial_at"):
        if st.get("stage") == "lead" and kind == "call_lead" and not st.get("erp_id"):
            return "new_application", "Новая заявка"
        return "old_unprocessed_lead", "Необработанные ранее"
    for k, name in ERP_COLUMNS:
        if kind == k:
            return k, name
    if kind == "rebook":  # этап «Не дошёл до ПУ»: нет отметки тренера или не пришли
        return ("not_marked", "Клиент не отмечен на ПУ") if t.get("miss") == "no_mark" else ("no_show", "Не пришли на ПУ")
    if kind in ("single_remind", "single_next", "single_paid_remind", "single_paid_next"):
        return "single", "Разовые занятия"
    if kind in THINK_KINDS or title.lower() == "думает":
        return "think", "Думает"
    if kind in MANUAL_KINDS:
        if t.get("source") == "erp" and title:  # статус ERP, которого нет в списке: своя колонка с тем же названием
            for k, name in ERP_COLUMNS:
                if title == name:
                    return k, name
            return "erp:" + title, title
        return "manual", "Ручные"
    return "other", "Другое"


def erp_board(con, day: str = "today", user: dict | None = None) -> dict:
    today = date.today()
    tomorrow = (today + timedelta(days=1)).isoformat()
    t0 = today.isoformat()
    base = """SELECT t.* FROM tasks t LEFT JOIN users u ON u.id=t.assignee_id
              WHERE t.status='open' AND t.kind != 'bday'
                AND NOT (t.kind='plan' AND t.student_id IS NULL AND t.family_id IS NULL AND u.role='offline_pool')
                AND (t.student_id IS NOT NULL OR t.family_id IS NOT NULL OR u.id IS NULL OR u.role != 'coach')"""
    privacy, visible_ids = _planner_privacy(con, user, "t.") if user is not None else ("", [])
    base += privacy
    scope_args = []
    # Рабочий стол участника команды: общие клиентские задачи + лично назначенные.
    # Право старшего смотреть планеры команды не превращает его собственный стол
    # в свалку всех бытовых поручений офлайн-администраторов.
    if user is not None and user.get("role") != "admin":
        base += " AND (t.student_id IS NOT NULL OR t.family_id IS NOT NULL OR t.assignee_id=?)"
        scope_args.append(int(user["id"]))
    cond = {"overdue": ("substr(t.due_at,1,10) < ?", t0), "today": ("substr(t.due_at,1,10) = ?", t0),
            "tomorrow": ("substr(t.due_at,1,10) = ?", tomorrow)}
    counts = {k: con.execute(f"SELECT COUNT(*) FROM ({base} AND {c})", (*visible_ids, *scope_args, v)).fetchone()[0] for k, (c, v) in cond.items()}
    c, v = cond.get(day, cond["today"])
    order = [k for k, _ in ERP_COLUMNS]
    cols: dict[str, dict] = {}
    for r in con.execute(f"{base} AND {c} ORDER BY substr(t.due_at,1,10),t.priority DESC,t.due_at,t.id", (*visible_ids, *scope_args, v)):
        t = _task_out(con, r)
        if t["kind"] == "rebook":
            m = con.execute("""SELECT kind FROM trial_misses WHERE (student_id=? OR student_id IN (SELECT id FROM students WHERE family_id=?))
                               ORDER BY id DESC LIMIT 1""", (t.get("student_id") or 0, t.get("family_id") or 0)).fetchone()
            t["miss"] = m[0] if m else ""
        key, name = _erp_col(t)
        cols.setdefault(key, {"key": key, "title": name, "cards": []})["cards"].append(t)
    rank = lambda k: order.index(k) if k in order else (len(order) + (0 if k.startswith("erp:") else 1 if k == "manual" else 2))
    return {"day": day if day in cond else "today", "counts": counts, "columns": sorted(cols.values(), key=lambda x: rank(x["key"]))}


def for_student(con, sid: int) -> dict:
    """Задачи для карточки: открытые (ближайшая первой) и несколько последних выполненных."""
    fam = (con.execute("SELECT family_id FROM students WHERE id=?", (sid,)).fetchone() or [None])[0]
    q = "SELECT * FROM tasks WHERE (student_id=? OR (kind='rebook' AND family_id=?)) AND status=? ORDER BY "
    return {"open": [_task_out(con, r) for r in con.execute(q + "substr(due_at,1,10),priority DESC,due_at,id", (sid, fam, "open"))], "done": []}  # выполненные не показываем


def complete(con, tid: int, user: dict, result: str = "", comment: str = "", next_due: str | None = None) -> None:
    t = con.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    if not t:
        raise comms.CommsError("Задача не найдена")
    if t["status"] != "open":
        raise comms.CommsError("Задача уже закрыта")
    if t['source']=='refund' and str(t['auto_key'] or '').startswith('refund-control:'):
        request=con.execute('SELECT state FROM refund_requests WHERE control_task_id=?',(tid,)).fetchone()
        if request and request['state']!='received':raise comms.CommsError('Сначала подтвердите получение денег клиентом в заявке на возврат')
    client = bool(t["student_id"] or t["family_id"])
    if t["kind"] == "plan" and not client:
        from . import planner
        if not planner.can_change_plan(con, user, t["assignee_id"]):
            raise comms.CommsError("Нет доступа к задаче")
    if t["kind"] == "request_data":
        from . import smart
        info = smart.required_info(con, t["student_id"], 'sale')
        missing = [key for key in info['missing'] if key != 'email']
        if missing:
            raise comms.CommsError('Задача остаётся открытой. Заполните: ' + ', '.join(info['labels'][k] for k in missing))
    if t["kind"] == "rebook":
        raise comms.CommsError("Эту задачу закрывают результатом: перезаписан, не дозвонились, перезвонить позже или отказ")
    from . import erpworkflow
    choices=erpworkflow.options(t)
    outcome=result.strip()
    transition_comment=comment.strip()
    transition_due = None
    if choices:
        if outcome not in choices:raise comms.CommsError('Выберите результат задачи из списка ERP')
        if not transition_comment:raise comms.CommsError('Напишите комментарий к изменению статуса')
        if len(transition_comment)>1000:raise comms.CommsError('Комментарий не должен быть длиннее 1000 символов')
        if outcome!='Закрыть задачу':
            if next_due is None:
                transition_due = erpworkflow.due_at(con,outcome,t['student_id'])
            else:
                try:
                    transition_due = datetime.combine(date.fromisoformat(str(next_due)[:10]), datetime.max.time()).replace(second=0,microsecond=0)
                except (TypeError,ValueError):
                    raise comms.CommsError('Выберите дату следующей задачи') from None
            kind=erpworkflow.next_kind(outcome,t)
            if renewal_covered(con, t["student_id"], transition_due, outcome):
                raise comms.CommsError("На месяц продления уже оформлен абонемент. Выберите другой результат")
            add_task(con,kind,t['student_id'],transition_due,
                     title=outcome,source='erp_rule',assignee=t['assignee_id'],
                     auto_key=f"erp-rule:{tid}:{outcome}",note=transition_comment)
    con.execute("UPDATE tasks SET status='done', done_at=?, done_by=?, result=? WHERE id=?",
                (_iso(_now()), user.get("name", ""), (outcome + (": " + transition_comment if choices else "")), tid))
    if t["student_id"]:
        con.execute("INSERT INTO history(student_id, ts, user_name, kind, text) VALUES (?,?,?,?,?)",
                    (t["student_id"], _iso(_now()), user.get("name", ""), "task",
                     (f"Задача «{t['title']}» выполнена. Следующий шаг ERP: «{outcome}». Комментарий: {transition_comment}" +
                      (f". Срок следующей задачи: {transition_due:%d.%m.%Y}" if transition_due else "") if choices else
                      f"Задача «{t['title']}» выполнена" + (f": {outcome}" if outcome else ""))))
        if t["kind"] != "request_data":
            con.execute("UPDATE students SET priority=0 WHERE id=?", (t["student_id"],))
        if t["kind"] in ("how_was", "no_show"):  # связались после пробного — карточка больше не горит
            con.execute("UPDATE students SET contacted_after_trial=1 WHERE id=?", (t["student_id"],))


def settings_view(con) -> dict:
    vals = {k: setting(con, k) for k in list(DEFAULT_TEMPLATES) + list(trialscripts.DEFAULTS) + list(AUTO_SETTINGS) + list(CONTACT_SETTINGS)}
    vals[OWNER_KEY] = str(owner(con) or "")
    from . import trialoffer
    vals["msg_trial_offer"] = setting(con, "msg_trial_offer") or trialoffer.DEFAULT
    from . import reminders
    vals["msg_lesson"] = setting(con, "msg_lesson") or reminders.DEFAULT_TEXT
    from . import chatscripts
    return {"values": vals, "erp_statuses": list(ERP_STATUSES), "branch_templates":trialscripts.LABELS, "booked_places": booked_places(con), "school_rules":chatscripts.profiles(con), "auto_live": comms.auto_live(con)}


def save_settings(con, data: dict):
    if "school_rules" in data:
        from . import chatscripts
        chatscripts.save_profiles(con,data["school_rules"])
    for k, v in data.items():
        if k in DEFAULT_TEMPLATES or k in trialscripts.DEFAULTS or k in AUTO_SETTINGS or k in CONTACT_SETTINGS or k in (OWNER_KEY, "msg_trial_offer", "msg_lesson"):
            comms.put(con, k, str(v or "").strip())
        elif k == "booked_places" and isinstance(v, dict):
            comms.put(con, k, json.dumps({str(b): {f: str(x.get(f) or "").strip() for f in ("place", "area", "tg", "max")}
                                          for b, x in v.items() if isinstance(x, dict)}, ensure_ascii=False))


def preview(con, s_id: int) -> dict:
    s = dict(con.execute("SELECT * FROM students WHERE id=?", (s_id,)).fetchone())
    return {k: render_template(con, k, s) for k in DEFAULT_TEMPLATES}


def start_worker(connect):
    import threading
    import time
    import traceback

    def loop():
        con = connect()
        while True:
            try:
                con.execute("BEGIN IMMEDIATE")
                try:
                    run(con)
                    con.execute("COMMIT")
                except BaseException:
                    con.execute("ROLLBACK")
                    raise
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(60)
    threading.Thread(target=loop, name="tasks-worker", daemon=True).start()


def seed_demo(con):
    """Тестовый режим: оператор Ольга (тест), на неё встают задачи по пробным."""
    from .db import hash_password
    r = con.execute("SELECT id FROM users WHERE login='olga'").fetchone()
    uid = r[0] if r else con.execute("INSERT INTO users(login, name, role, pw_hash) VALUES (?,?,?,?)",
                                     ("olga", "Ольга (тест)", "operator", hash_password("olga-test-1"))).lastrowid
    if not comms.get(con, OWNER_KEY):
        comms.put(con, OWNER_KEY, str(uid))



def client_due(value):
    """Клиентская задача назначается на день, без времени."""
    return date.fromisoformat(str(value or date.today())[:10]).isoformat()+'T23:59'


def add_client_task(con,sid,data,user):
    title=str(data.get('title') or '').strip()[:200]
    if not title:raise comms.CommsError('Напишите, что нужно сделать')
    try:due=client_due(data.get('due'))
    except ValueError:raise comms.CommsError('Неверная дата') from None
    if data.get('priority',False) not in (True,False,0,1):raise comms.CommsError('Неверный приоритет')
    tid=add_task(con,'manual',sid,due,title=title,source='manual',note=str(data.get('note') or '')[:1000])
    con.execute('UPDATE tasks SET assignee_id=NULL,priority=? WHERE id=?',(int(bool(data.get('priority'))),tid))
    return tid
