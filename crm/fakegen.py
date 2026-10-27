"""Выдуманные клиенты для проверки CRM на «живом» объёме. Все помечены fake=1 и удаляются одной кнопкой.

Раскладка: ~65% занимаются в реальных группах (с абонементами, оплатами, посещениями и пропусками),
остальные — лиды на разных этапах воронки продаж, включая записанных на пробное. Телефоны +7 999 0xx-xx-xx.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from . import logic

BOYS = ["Артём", "Миша", "Тимур", "Матвей", "Лев", "Марк", "Иван", "Максим", "Даниил", "Кирилл", "Егор", "Арсений", "Фёдор",
        "Савва", "Мирон", "Платон", "Глеб", "Тимофей", "Никита", "Захар", "Роман", "Ярослав", "Давид", "Елисей", "Серафим"]
GIRLS = ["Алиса", "Ева", "Вера", "Мия", "Варя", "Соня", "Полина", "Аня", "Маша", "Ксюша"]
SURN = ["Иванов", "Смирнов", "Кузнецов", "Попов", "Васильев", "Петров", "Соколов", "Михайлов", "Новиков", "Фёдоров",
        "Морозов", "Волков", "Алексеев", "Лебедев", "Семёнов", "Егоров", "Павлов", "Козлов", "Степанов", "Николаев",
        "Орлов", "Андреев", "Макаров", "Никитин", "Захаров", "Зайцев", "Соловьёв", "Борисов", "Яковлев", "Григорьев"]
MOMS = ["Анна", "Мария", "Елена", "Ольга", "Наталья", "Екатерина", "Ирина", "Татьяна", "Светлана", "Юлия", "Дарья", "Ксения"]
DADS = ["Алексей", "Дмитрий", "Сергей", "Андрей", "Павел", "Игорь", "Денис", "Евгений", "Олег", "Виктор"]
SOURCES = [("Сайт", "Заявка с сайта"), ("Промоутер", "Промоутер у ТЦ"), ("Вконтакте", "Таргет ВК"),
           ("Сарафан", "Посоветовали знакомые"), ("Филиал", "Увидели тренировку"), ("Паблики", "Районный паблик")]


def init(con):
    for t in ("students", "families"):
        if "fake" not in {r[1] for r in con.execute(f"PRAGMA table_info({t})")}:
            con.execute(f"ALTER TABLE {t} ADD COLUMN fake INTEGER NOT NULL DEFAULT 0")


def _surname(base: str, female: bool) -> str:
    if not female:
        return base
    return base[:-2] + "ая" if base.endswith("ий") else base + "а"


def _age_birth(age_txt: str, rnd: random.Random, today: date) -> str:
    nums = [int(x) for x in "".join(c if c.isdigit() else " " for c in age_txt or "").split()]
    lo, hi = (nums[0], nums[-1]) if nums else (4, 7)
    if hi > 14:
        hi = lo + 3
    age_days = rnd.randint(lo * 365 + 30, hi * 365 + 330)
    return (today - timedelta(days=age_days)).isoformat()


def generate(con, count: int, user: dict, seed: int | None = None) -> dict:
    from . import money, services as sv, sources
    init(con)
    rnd = random.Random(seed)
    today = date.today()
    ym = logic.month_of(today)
    groups = [dict(g) for g in con.execute("""SELECT g.*, b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id
                                              WHERE g.active=1 AND b.active=1 AND g.is_makeup=0""")]
    if not groups:
        raise ValueError("Сначала нужны группы в расписании")
    kids_groups = [g for g in groups if "взросл" not in (g["age"] or "").lower()] or groups
    src_ids = [sources.ensure_source(con, ch, nm) for ch, nm in SOURCES]
    used = {r[0] for r in con.execute("SELECT phone_norm FROM parents")}
    made = {"members": 0, "leads": 0, "families": 0, "paid": 0, "skips": 0}
    fam_id = None
    n = 0
    while n < count:
        female_child = rnd.random() < 0.15
        child = rnd.choice(GIRLS if female_child else BOYS)
        base = rnd.choice(SURN)
        while True:
            phone = f"+7 999 0{rnd.randint(10, 99)}-{rnd.randint(10, 99)}-{rnd.randint(10, 99)}"
            norm = "7999" + "".join(c for c in phone[7:] if c.isdigit())
            if norm not in used:
                used.add(norm)
                break
        mom = rnd.random() < 0.8
        parent = f"{rnd.choice(MOMS if mom else DADS)} {_surname(base, mom)}"
        member = rnd.random() < 0.65
        if member:
            free = [x for x in kids_groups if _roster(con, x["id"]) < _cap(con, x)]
            member = bool(free)
            g = rnd.choice(free) if free else rnd.choice(kids_groups)
        else:
            g = rnd.choice(kids_groups)
        data = {"name": f"{child} {_surname(base, female_child)}", "parent_name": parent, "phone": phone,
                "family_name": _surname(base, False) + "ы", "funnel": "members" if member else "sales",
                "group_id": g["id"], "birth": _age_birth(g["age"], rnd, today), "tariff": "flex" if rnd.random() < 0.25 else "fixed",
                "source_id": rnd.choice(src_ids), "messenger": rnd.choice(["WhatsApp", "Telegram", "MAX", ""]), "force_new_family": True}
        con.execute("SAVEPOINT fk")
        try:
            sid = sv.create_student(con, data, user)
            con.execute("RELEASE fk")
        except sv.ApiError:  # группа заполнена до лимита: этот станет лидом
            con.execute("ROLLBACK TO fk")
            con.execute("RELEASE fk")
            member = False
            data["funnel"] = "sales"
            sid = sv.create_student(con, data, user)
        fam_id = con.execute("SELECT family_id FROM students WHERE id=?", (sid,)).fetchone()[0]
        con.execute("UPDATE families SET fake=1 WHERE id=?", (fam_id,))
        con.execute("UPDATE students SET fake=1 WHERE id=?", (sid,))
        made["families"] += 1
        n += 1
        kids = [sid]
        free2 = [x for x in kids_groups if _roster(con, x["id"]) < _cap(con, x)]
        if member and free2 and n < count and rnd.random() < 0.08:  # брат или сестра в той же семье
            g2 = rnd.choice(free2)
            con.execute("SAVEPOINT fk2")
            try:
                sid2 = sv.create_student(con, {"name": f"{rnd.choice(BOYS)} {base}", "family_id": fam_id, "funnel": "members",
                                               "group_id": g2["id"], "birth": _age_birth(g2["age"], rnd, today),
                                               "source_id": data["source_id"]}, user)
                con.execute("RELEASE fk2")
                con.execute("UPDATE students SET fake=1 WHERE id=?", (sid2,))
                kids.append(sid2)
                n += 1
            except sv.ApiError:
                con.execute("ROLLBACK TO fk2")
                con.execute("RELEASE fk2")
        for k in kids:
            if member:
                made["members"] += 1
                _member_life(con, k, ym, today, user, rnd, made)
            else:
                made["leads"] += 1
                _lead_life(con, k, g, today, rnd)
    return made


def _roster(con, gid: int) -> int:
    return con.execute("""SELECT COUNT(*) FROM enrollments e JOIN students s ON s.id=e.student_id
                          WHERE e.group_id=? AND e.active=1 AND s.funnel='members' AND s.stage!='left'""", (gid,)).fetchone()[0]


def _cap(con, g) -> int:
    lim = con.execute("SELECT lesson_limit FROM branches WHERE id=?", (g["branch_id"],)).fetchone()[0]
    return lim if lim else 10 ** 6  # без лимита филиала не ограничиваем (старый генератор для объёма)


def _member_life(con, sid, ym, today, user, rnd, made):
    from . import money, services as sv
    created = (today - timedelta(days=rnd.randint(20, 400))).isoformat() + "T10:00"
    con.execute("UPDATE students SET created_at=?, converted_at=? WHERE id=?", (created, created, sid))
    try:
        inv = sv.create_invoice(con, sid, ym, user)
        if rnd.random() < 0.78:
            money.pay_subscription(con, inv["id"], rnd.choice(["sbp", "card", "cash", "sbp", "online"]), user)
            made["paid"] += 1
    except Exception:  # noqa: BLE001 — для проверки не критично
        pass
    g = con.execute("SELECT g.weekdays FROM students s JOIN groups g ON g.id=s.group_id WHERE s.id=?", (sid,)).fetchone()
    days = logic.parse_weekdays(g[0]) if g else []
    d = today.replace(day=1)
    gid = con.execute("SELECT group_id FROM students WHERE id=?", (sid,)).fetchone()[0]
    absent_any = False
    while d < today:
        if d.weekday() in days:
            st = "absent" if rnd.random() < 0.12 else "present"
            try:
                sv.mark_attendance(con, sid, gid, d.isoformat(), st, user)
                absent_any |= st == "absent"
            except Exception:  # noqa: BLE001
                pass
        d += timedelta(days=1)
    made["skips"] += absent_any


def _lead_life(con, sid, g, today, rnd):
    stage = rnd.choices(["lead", "no_answer", "trial_booked", "trial_reminder", "how_was", "refused"],
                        weights=[24, 16, 22, 8, 12, 8])[0]
    created = datetime.combine(today - timedelta(days=rnd.randint(0, 25)), datetime.min.time()).replace(hour=rnd.randint(9, 21))
    trial = None
    days = logic.parse_weekdays(g["weekdays"])
    if stage in ("trial_booked", "trial_reminder") and days:
        d = today + timedelta(days=1)
        while d.weekday() not in days:
            d += timedelta(days=1)
        d += timedelta(days=7 * rnd.randint(0, 1))
        trial = f"{d.isoformat()}T{logic.group_time(g, d.weekday()) or '18:00'}"
    elif stage == "how_was" and days:
        d = today - timedelta(days=1)
        while d.weekday() not in days:
            d -= timedelta(days=1)
        trial = f"{d.isoformat()}T{logic.group_time(g, d.weekday()) or '18:00'}"
    con.execute("UPDATE students SET stage=?, trial_at=?, created_at=?, stage_changed_at=?, trial_came=? WHERE id=?",
                (stage, trial, created.isoformat(timespec="minutes"), created.isoformat(timespec="minutes"),
                 1 if stage == "how_was" else None, sid))
    if stage == "refused":
        con.execute("UPDATE students SET refusal_reason=? WHERE id=?",
                    (rnd.choice(["Далеко ездить", "Неудобное время", "Дорого", "Ребёнок не захотел"]), sid))


def remove(con) -> int:
    """Удаляет всех выдуманных клиентов вместе с их оплатами, посещениями и историей."""
    init(con)
    con.execute("DELETE FROM calls WHERE external_id LIKE 'fake-call-%'")
    try:
        con.execute("DELETE FROM ad_posts WHERE deal_id IN (SELECT id FROM ad_deals WHERE note='fake-demo')")
        con.execute("DELETE FROM ad_deals WHERE note='fake-demo'")
        con.execute("DELETE FROM ad_places WHERE note='fake-demo'")
    except Exception:  # noqa: BLE001
        pass
    ids = [r[0] for r in con.execute("SELECT id FROM students WHERE fake=1")]
    if not ids:
        return 0
    q = ",".join("?" * len(ids))
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    for t in tables:
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
        if "student_id" in cols and t != "students":
            con.execute(f"DELETE FROM {t} WHERE student_id IN ({q})", ids)
    con.execute(f"DELETE FROM students WHERE id IN ({q})", ids)
    fams = [r[0] for r in con.execute("SELECT id FROM families WHERE fake=1")]
    if fams:
        fq = ",".join("?" * len(fams))
        for t in tables:
            cols = {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
            if "family_id" in cols and t not in ("families", "students"):
                if t == "conversations":
                    con.execute(f"UPDATE {t} SET family_id=NULL WHERE family_id IN ({fq})", fams)
                else:
                    con.execute(f"DELETE FROM {t} WHERE family_id IN ({fq})", fams)
        con.execute(f"DELETE FROM families WHERE id IN ({fq})", fams)
    return len(ids)


def subscribe_all(con, user: dict, seed: int | None = None) -> dict:
    """Все выдуманные клиенты — абонементщики: лиды переводим в ученики, у всех оплачены сентябрь и октябрь."""
    from . import money, services as sv
    init(con)
    rnd = random.Random(seed)
    today = date.today()
    months = [logic.month_of(today.replace(day=1) - timedelta(days=1)), logic.month_of(today)]
    out = {"converted": 0, "invoices": 0, "paid": 0}
    for s in con.execute("SELECT * FROM students WHERE fake=1").fetchall():
        sid = s["id"]
        if s["funnel"] != "members":
            con.execute("UPDATE students SET trial_at=NULL, trial_came=NULL WHERE id=?", (sid,))
            sv.move_card(con, sid, "sales", "bought", user)
            out["converted"] += 1
        con.execute("UPDATE students SET stage='active', archived=0 WHERE id=?", (sid,))
        for ym in months:
            for e in con.execute("SELECT id FROM enrollments WHERE student_id=? AND active=1", (sid,)).fetchall():
                sub = con.execute("SELECT id, status FROM subscriptions WHERE enrollment_id=? AND month=? AND status!='cancelled'",
                                  (e["id"], ym)).fetchone()
                if not sub:
                    try:
                        sub_id = sv.create_invoice(con, sid, ym, user, e["id"])["id"]
                        out["invoices"] += 1
                    except Exception:  # noqa: BLE001
                        continue
                else:
                    sub_id = sub["id"]
                try:
                    money.pay_subscription(con, sub_id, rnd.choice(["sbp", "card", "cash", "sbp", "online"]), user)
                    out["paid"] += 1
                except ValueError:
                    pass  # уже оплачен
    return out


# ---------------- выдуманные звонки с разборами (чтобы увидеть разбор и сводку дня без ключей Яндекса) ----------------

def _A(admin, client):
    return {"who": "admin", "text": admin}, {"who": "client", "text": client}


CALL_TEMPLATES = [
    {"score": 8, "result": "записали на пробное", "outcome": "Запись на пробное, пт 17:45",
     "headline": "Чёткая запись: конкретный слот и прямой вопрос «записываю?»",
     "conclusion": "Быстро выяснила возраст и район, предложила два времени на выбор и сразу закрыла на запись. Не хватило рассказа, чем занятия полезны ребёнку",
     "points": [{"ok": True, "text": "Ребёнок записан на пробное в пятницу в 17:45"}, {"ok": True, "text": "Получены имя, возраст ребёнка и имя мамы"},
                {"ok": True, "text": "Выбран WhatsApp для подтверждения"}, {"ok": False, "text": "Не рассказала, что ребёнок получит на занятиях"}],
     "focus": "Перед записью одной фразой продай пользу: «На первом занятии тренер посмотрит координацию и подберёт нагрузку, ребёнок уйдёт довольным»",
     "tips": ["Добавь пользу перед записью"],
     "dialogue": [*_A("Добрый день, детская школа «Футболика», меня зовут Оля", "Здравствуйте, хочу записать сына на футбол"),
                  *_A("Отлично! Как зовут сына и сколько ему?", "Марк, пять лет"), *_A("Вам удобнее Небо или Чоботовская?", "Небо, мы рядом живём"),
                  *_A("Для Марка есть вторник и пятница в 17:45. Какой день ближе?", "Пятница"), *_A("Записываю Марка на пятницу в 17:45?", "Да, давайте")],
     "script": [*_A("Добрый день, «Футболика», меня зовут Оля. Как к вам обращаться?", "Анна"),
                *_A("Анна, как зовут ребёнка и сколько ему?", "Марк, пять лет"),
                *_A("Для пяти лет у нас группа 4–5: на первом занятии тренер посмотрит координацию и подберёт нагрузку. Вам ближе вторник или пятница в 17:45?", "Пятница"),
                *_A("Записываю Марка на пятницу, 31 октября, в 17:45, ТЦ «Небо», 3 этаж. Подтверждение пришлю в WhatsApp, удобно?", "Да")]},
    {"score": 5, "result": "думает", "outcome": "Расписание в WhatsApp, записи нет",
     "headline": "Разговор закончился на «пришлю расписание», без следующего шага",
     "conclusion": "Вежливо ответила на вопросы, но вместо предложения конкретного времени отправила расписание. Мама сказала «посмотрим», дата следующего звонка не назначена",
     "points": [{"ok": True, "text": "Выяснен возраст и филиал"}, {"ok": False, "text": "Не предложено конкретное время пробного"},
                {"ok": False, "text": "Нет даты следующего контакта"}],
     "focus": "Цель первого звонка не отправить информацию, а получить следующее действие. Выбери 1-2 подходящих слота и спроси: «Какой вам удобнее?»",
     "tips": ["Закрывать на конкретное время двумя вариантами", "Фиксировать дату следующего касания"],
     "dialogue": [*_A("«Футболика», здравствуйте", "Здравствуйте, сколько стоят занятия для шестилетки?"),
                  *_A("Абонемент 1350 за занятие, восемь занятий в месяц", "Понятно. А где вы находитесь?"),
                  *_A("Небо, Чоботовская и Арктика. Давайте я пришлю расписание в WhatsApp", "Хорошо, посмотрим")],
     "script": [*_A("Здравствуйте, «Футболика», меня зовут Оля. Подскажите, как зовут ребёнка?", "Дима, шесть лет"),
                *_A("Для Димы есть группа 5–6 в Небе: вторник и пятница в 18:45. Первое занятие бесплатно. Какой день удобнее?", "Наверное, вторник"),
                *_A("Записываю Диму на вторник в 18:45. Если что-то изменится, просто напишите мне в WhatsApp", "Хорошо")]},
    {"score": 7, "result": "записали на пробное", "outcome": "Запись на пробное, ср 16:45",
     "headline": "Запись есть, но несколько раз звучала тревога, что ребёнок испугается",
     "conclusion": "Аккуратно подобрала малочисленную группу и оформила запись. Но вместо уверенного «давайте обязательно попробуем» слишком часто звучали сценарии, где ребёнок может заплакать",
     "points": [{"ok": True, "text": "Записаны на среду 16:45, группа 3–4"}, {"ok": True, "text": "Выяснен источник: увидели тренировку в ТЦ"},
                {"ok": False, "text": "Не названа календарная дата, только день недели"}, {"ok": False, "text": "До записи несколько раз сформирована тревога"}],
     "focus": "С первого ответа настраивай позитивно: «Давайте обязательно попробуем». Предупреждение нужно, чтобы снять тревогу, а не напугать",
     "tips": ["Называть дату целиком: «среда, 29 октября, 16:45»"],
     "dialogue": [*_A("«Футболика», слушаю", "Здравствуйте, берёте детей в три года?"),
                  *_A("Берём, но маленькие часто держатся за маму и пугаются", "Ой, а если он заплачет?"),
                  *_A("Бывает, тогда просто попробуете в другой раз. Есть среда в 16:45", "Ну давайте попробуем")],
     "script": [*_A("Здравствуйте! Да, группа 3–4 года как раз для малышей, занятия игровые. Как зовут ребёнка?", "Лёва"),
                *_A("Лёве понравится, первое занятие бесплатное. Если в первый раз он будет стесняться, это нормально, тренер мягко включит его в игру. Удобно в среду, 29 октября, в 16:45?", "Да")]},
    {"score": 3, "result": "отказ", "outcome": "Быстрый отказ без выяснения причины",
     "headline": "Отказ принят сразу, причину не выяснили",
     "conclusion": "Мама сказала «уже ходим в другую секцию», разговор закончился за 30 секунд. Можно было спросить, что важно, и предложить пробное для сравнения",
     "points": [{"ok": False, "text": "Причина отказа не выяснена"}, {"ok": False, "text": "Не предложено пробное для сравнения"}],
     "focus": "После возражения задай один вопрос, который выясняет реальную причину: «А что для вас важно в секции?»",
     "tips": ["После возражения один уточняющий вопрос"],
     "dialogue": [*_A("Здравствуйте, это «Футболика», вы оставляли заявку", "Да, но мы уже ходим в другую секцию"),
                  *_A("Поняла, извините за беспокойство", "До свидания")],
     "script": [*_A("Здравствуйте, это «Футболика», вы оставляли заявку", "Да, но мы уже ходим в другую секцию"),
                *_A("Здорово, что ребёнок занимается спортом! А вам там всё нравится или что-то хотелось бы иначе?", "Далековато ездить"),
                *_A("У нас группа в Небе, это рядом с вами. Хотите попробовать бесплатно и сравнить?", "Можно")]},
    {"score": 9, "result": "продлил", "outcome": "Продление на ноябрь",
     "headline": "Образцовое напоминание о продлении",
     "conclusion": "Напомнила про продление до 25 числа, ответила про перерасчёт пропусков и сразу отправила ссылку на оплату",
     "points": [{"ok": True, "text": "Абонемент продлён на ноябрь"}, {"ok": True, "text": "Объяснён перерасчёт за пропуски"}],
     "focus": "Так держать: напоминание + ответ на вопрос + ссылка на оплату в одном звонке",
     "tips": [], "dialogue": [*_A("Добрый день! Напоминаю, продление на ноябрь до 25 числа", "Да, а пропуск по болезни учтёте?"),
                              *_A("Да, минус одно занятие, сумма 9450. Пришлю ссылку на оплату", "Отлично, спасибо")], "script": []},
]
CASE_NAMES = ["Анна и Марк, 5 лет", "Мария и Дима, 6 лет", "Ольга и Лёва, 3 года", "Ирина, отказ", "Елена и Тимофей, продление",
              "Дарья и Савва, 4 года", "Наталья и Егор, 7 лет", "Ксения и Глеб, 5 лет", "Юлия и Мирон, 6 лет", "Татьяна и Фёдор, 4 года"]


def fake_calls(con, user: dict, seed: int | None = None) -> dict:
    """Звонки оператора за вчера и сегодня с готовыми разборами и сводкой дня."""
    from . import calldigest
    rnd = random.Random(seed)
    op = con.execute("""SELECT id, name, sip_number FROM users WHERE role='operator' AND active=1
                        ORDER BY (sip_number!='') DESC, (login='olga') DESC, id LIMIT 1""").fetchone()
    if not op:
        raise ValueError("Нет оператора, заведите сотрудника с ролью «оператор»")
    sip = op["sip_number"] or "201"
    if not op["sip_number"]:
        con.execute("UPDATE users SET sip_number=? WHERE id=?", (sip, op["id"]))
    fams = [r[0] for r in con.execute("SELECT id FROM families WHERE fake=1 ORDER BY RANDOM() LIMIT 40")]
    import json as _json
    made = 0
    today = date.today()
    for day, n in ((today - timedelta(days=1), 10), (today, 4)):
        for i in range(n):
            t = CALL_TEMPLATES[(i + day.day) % len(CALL_TEMPLATES)]
            a = dict(t, case=CASE_NAMES[i % len(CASE_NAMES)], summary=t["conclusion"],
                     checklist={k: (True if t["score"] >= 7 else rnd.choice([True, False])) for k, _ in __import__("crm.ai", fromlist=["CHECKLIST"]).CHECKLIST},
                     objections=[], next_step=t["outcome"], good=t["headline"])
            started = datetime.combine(day, datetime.min.time()).replace(hour=10 + i, minute=rnd.randint(0, 50))
            ext = f"fake-call-{day.isoformat()}-{i}"
            fid = fams[(i + day.day) % len(fams)] if fams else None
            phone = (con.execute("SELECT phone FROM parents WHERE family_id=? LIMIT 1", (fid,)).fetchone() or ["+7 999 000-00-00"])[0]
            cur = con.execute("""INSERT OR IGNORE INTO calls(external_id, direction, phone, phone_norm, operator, family_id, status, started_at,
                                 answered_at, ended_at, duration, talk, ai_status, analysis, score, transcript, seen)
                                 VALUES (?,?,?,?,?,?,'answered',?,?,?,?,?,'done',?,?,?,1)""",
                              (ext, rnd.choice(["in", "out"]), phone, "".join(c for c in phone if c.isdigit()), sip, fid,
                               started.isoformat(timespec="seconds"), started.isoformat(timespec="seconds"),
                               (started + timedelta(minutes=3)).isoformat(timespec="seconds"), 190, 180,
                               _json.dumps(a, ensure_ascii=False), t["score"], ""))
            made += cur.rowcount
    y = (today - timedelta(days=1)).isoformat()
    d = calldigest.build(con, y, sip, use_llm=False) or {}
    d.update({"overview": "За день 10 клиентских разговоров. Тон доброжелательный и спокойный, основные потери не в тоне, а в структуре закрытия и фиксации следующего шага",
              "funnel": ["Самый стабильный путь к записи: конкретный слот и прямой вопрос «записываю?»",
                         "Главный повторяющийся недобор: разговор заканчивается на «пришлю расписание»",
                         "Отложенные лиды сохраняются, но без точной даты следующего контакта",
                         "Причины отказа часто остаются невыясненными"],
              "training": ["Закрывать на конкретное время двумя вариантами", "После возражения задавать один вопрос о реальной причине",
                           "У каждого отложенного лида фиксировать дату следующего касания", "Повторять запись целиком: ребёнок, филиал, дата, время"],
              "setting": "Цель первого контакта не отправить информацию, а получить следующее действие. Каждое сообщение, где диалог продолжается, заканчивать вопросом",
              "focus_today": ["Предлагай 2 конкретных слота вместо расписания", "После «посмотрим» назначай дату следующего звонка",
                              "Называй дату пробного целиком: «среда, 29 октября, 16:45»"]})
    calldigest.save(con, y, sip, d)
    return {"calls": made, "operator": op["name"], "digest_day": y}


# ---------------- «как настоящая школа»: полтора года истории ----------------
# Помесячно: лиды по сезону → пробные → покупки; отток по сезону (летом сильнее), часть ушедших летом возвращается в сентябре.
# Конверсии разные по источникам (сарафан и «увидели тренировку» продают лучше таргета и промоутеров).

LEADS_BY_MONTH = {1: 70, 2: 75, 3: 85, 4: 80, 5: 65, 6: 40, 7: 30, 8: 55, 9: 130, 10: 105, 11: 80, 12: 55}
CHURN_BY_MONTH = {1: .06, 2: .05, 3: .05, 4: .06, 5: .09, 6: .2, 7: .24, 8: .1, 9: .04, 10: .05, 11: .05, 12: .1}
REAL_SOURCES = [  # канал, источник, доля лидов, множитель конверсии
    ("Сайт", "Заявка с сайта", 25, 1.0), ("Вконтакте", "Таргет ВК", 22, .7), ("Сарафан", "Посоветовали знакомые", 18, 1.6),
    ("Филиал", "Увидели тренировку", 15, 1.4), ("Промоутер", "Промоутер у ТЦ", 12, .6), ("Паблики", "Районный паблик", 8, .9)]
REFUSALS = ["Далеко ездить", "Неудобное время", "Дорого", "Ребёнок не захотел", "Выбрали другую секцию", "Не дозвонились"]


def _ts(d: date, rnd, lo=9, hi=21) -> str:
    return datetime.combine(d, datetime.min.time()).replace(hour=rnd.randint(lo, hi), minute=rnd.randint(0, 59)).isoformat(timespec="minutes")


def realistic(con, user: dict, seed: int | None = 7, start_members: int = 150, months_back: int = 18) -> dict:
    """Стирает прежних выдуманных и создаёт школу с историей за полтора года, похожую на настоящую."""
    from . import services as sv, sources
    init(con)
    remove(con)
    rnd = random.Random(seed)
    today = date.today()
    now_iso = datetime.now().isoformat(timespec="minutes")
    cur = logic.month_of(today)
    st = sv.settings(con)
    prices = {"fixed": st["price_fixed"], "flex": st["price_flex"]}
    groups = [dict(g) for g in con.execute("""SELECT g.*, b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id
                                              WHERE g.active=1 AND b.active=1 AND g.is_makeup=0""")]
    groups = [g for g in groups if "взросл" not in (g["age"] or "").lower()] or groups
    if not groups:
        raise ValueError("Сначала нужны группы в расписании")
    src = [(sources.ensure_source(con, ch, nm), w, k) for ch, nm, w, k in REAL_SOURCES]
    used = {r[0] for r in con.execute("SELECT phone_norm FROM parents")}
    load = {g["id"]: 0 for g in groups}

    def pick_group():
        free = [g for g in groups if load[g["id"]] < g["capacity"]] or groups
        w = [max(1, g["capacity"] - load[g["id"]]) ** 1.3 for g in free]
        return rnd.choices(free, weights=w)[0]

    def new_kid(g, created: str, source_id, funnel: str) -> int:
        female = rnd.random() < 0.15
        base = rnd.choice(SURN)
        while True:
            phone = f"+7 999 {rnd.randint(100, 999)}-{rnd.randint(10, 99)}-{rnd.randint(10, 99)}"
            norm = "7999" + "".join(c for c in phone[7:] if c.isdigit())
            if norm not in used:
                used.add(norm)
                break
        mom = rnd.random() < 0.8
        sid = sv.create_student(con, {"name": f"{rnd.choice(GIRLS if female else BOYS)} {_surname(base, female)}",
                                      "parent_name": f"{rnd.choice(MOMS if mom else DADS)} {_surname(base, mom)}", "phone": phone,
                                      "family_name": base + "ы", "funnel": funnel, "group_id": g["id"],
                                      "birth": _age_birth(g["age"], rnd, today), "tariff": "flex" if rnd.random() < 0.22 else "fixed",
                                      "source_id": source_id, "messenger": rnd.choice(["WhatsApp", "Telegram", "MAX", "WhatsApp"]),
                                      "force_new_family": True}, user)
        con.execute("UPDATE students SET fake=1, created_at=?, stage_changed_at=? WHERE id=?", (created, created, sid))
        con.execute("UPDATE families SET fake=1 WHERE id=(SELECT family_id FROM students WHERE id=?)", (sid,))
        return sid

    def to_member(sid, g, when: str):
        con.execute("""UPDATE students SET funnel='members', stage='active', converted_at=?, stage_changed_at=? WHERE id=?""",
                    (when, when, sid))
        if not con.execute("SELECT 1 FROM enrollments WHERE student_id=?", (sid,)).fetchone():
            con.execute("INSERT INTO enrollments(student_id, group_id, weekdays, tariff, created_at) VALUES (?,?,?,?,?)",
                        (sid, g["id"], "", "fixed", when))
        load[g["id"]] += 1

    def invoice(sid, ym: str, paid_at: str | None):
        if con.execute("SELECT 1 FROM subscriptions WHERE student_id=? AND month=?", (sid, ym)).fetchone():
            return 0
        e = con.execute("""SELECT e.id, e.tariff, e.weekdays, g.weekdays AS gw FROM enrollments e JOIN groups g ON g.id=e.group_id
                           WHERE e.student_id=? ORDER BY e.id LIMIT 1""", (sid,)).fetchone()
        days = logic.parse_weekdays(e["gw"])
        q = logic.quote(weekdays=days, ym=ym, tariff=e["tariff"], prices=prices, vacations=[], prev_unresolved_absences=0)
        price = q["total"]
        if rnd.random() < 0.08:  # отпуск, перерасчёт
            price -= q["per_lesson"] * rnd.randint(1, 2)
        y, m = map(int, ym.split("-"))
        created = date(y, m, 1) - timedelta(days=rnd.randint(8, 14))
        sub = con.execute("""INSERT INTO subscriptions(student_id, enrollment_id, month, tariff, lessons, per_lesson, price, status,
                             created_at, paid_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                          (sid, e["id"], ym, e["tariff"], q["lessons_paid"], q["per_lesson"], price, "paid" if paid_at else "invoiced",
                           created.isoformat() + "T10:00", paid_at)).lastrowid
        if paid_at:
            con.execute("""INSERT INTO payments(student_id, amount, method, paid_at, comment, user_name, confirmed, confirmed_by,
                           confirmed_at, subscription_id, created_at) VALUES (?,?,?,?,?,?,1,?,?,?,?)""",
                        (sid, price, rnd.choice(["sbp", "sbp", "card", "online", "cash", "card"]), paid_at,
                         f"Абонемент на {logic.month_label(ym)}", "Ольга", "Юлия", paid_at, sub, paid_at))
        return price

    def renew_ts(ym: str) -> str:  # продление оплачивают с 15 по 25 число прошлого месяца, кто-то опаздывает
        y, m = map(int, ym.split("-"))
        first = date(y, m, 1)
        d = first - timedelta(days=rnd.randint(4, 16)) if rnd.random() < 0.85 else first + timedelta(days=rnd.randint(0, 6))
        return _ts(min(d, today), rnd)

    months = [logic.shift_month(cur, -i) for i in range(months_back, -1, -1)]
    active: list[tuple[int, dict]] = []
    left_by_month: dict[str, list] = {}
    out = {"members_now": 0, "leads": 0, "bought": 0, "left": 0, "returned": 0, "invoices": 0}

    # стартовый состав: занимаются давно
    for _ in range(start_members):
        g = pick_group()
        old = today.replace(day=1) - timedelta(days=30 * months_back + rnd.randint(30, 700))
        sid = new_kid(g, _ts(old, rnd), rnd.choices([s[0] for s in src], weights=[s[1] for s in src])[0], "sales")
        to_member(sid, g, _ts(old + timedelta(days=rnd.randint(3, 12)), rnd))
        active.append((sid, g))

    for ym in months:
        y, m = map(int, ym.split("-"))
        first = date(y, m, 1)
        last = (logic.month_bounds(ym)[1])
        is_cur = ym == cur
        # 1) отток перед месяцем
        churn = CHURN_BY_MONTH[m]
        stay, gone = [], []
        for x in active:
            (gone if rnd.random() < churn else stay).append(x)
        for sid, g in gone:
            load[g["id"]] -= 1
            con.execute("UPDATE students SET stage='left', stage_changed_at=? WHERE id=?", (_ts(first - timedelta(days=rnd.randint(1, 10)), rnd), sid))
        left_by_month[ym] = gone
        out["left"] += len(gone)
        # 2) возвращения: в сентябре часть ушедших летом, в январе часть ушедших в декабре
        back = []
        if m in (9, 1):
            pool = [x for k, v in left_by_month.items() if k < ym and k >= logic.shift_month(ym, -4) for x in v]
            for sid, g in pool:
                if rnd.random() < (0.3 if m == 9 else 0.2) and sid not in {a for a, _ in stay} and load[g["id"]] < g["capacity"]:
                    con.execute("UPDATE students SET stage='active', stage_changed_at=? WHERE id=?", (_ts(first - timedelta(days=5), rnd), sid))
                    load[g["id"]] += 1
                    back.append((sid, g))
        out["returned"] += len(back)
        # 3) оплаты продления за месяц
        for sid, g in stay + back:
            paid = rnd.random() < (0.93 if is_cur else 1)
            invoice(sid, ym, renew_ts(ym) if paid else None)
            out["invoices"] += 1
        active = stay + back
        # 4) лиды месяца и их путь
        end_day = today if is_cur else last
        n_leads = round(LEADS_BY_MONTH[m] * rnd.uniform(0.9, 1.1) * (end_day.day / last.day if is_cur else 1))
        for _ in range(n_leads):
            d = first + timedelta(days=rnd.randint(0, (end_day - first).days))
            created = _ts(d, rnd)
            if d == today and created > now_iso:
                created = now_iso
            s_id, _w, k = rnd.choices(src, weights=[s[1] for s in src])[0]
            g = pick_group()
            sid = new_kid(g, created, s_id, "sales")
            out["leads"] += 1
            booked = rnd.random() < min(0.85, 0.55 * (0.7 + 0.3 * k))
            if not booked:
                age = (today - d).days
                stage = "lead" if age <= 1 else "no_answer" if age <= 6 and rnd.random() < .6 else "refused"
                con.execute("UPDATE students SET stage=?, refusal_reason=?, archived=? WHERE id=?",
                            (stage, rnd.choice(REFUSALS) if stage == "refused" else "", 1 if age > 45 else 0, sid))
                continue
            gd = logic.parse_weekdays(g["weekdays"]) or [1, 4]
            t = d + timedelta(days=1)
            while t.weekday() not in gd:
                t += timedelta(days=1)
            if rnd.random() < 0.3:
                t += timedelta(days=7)
            trial_at = f"{t.isoformat()}T{logic.group_time(g, t.weekday()) or '18:00'}"
            con.execute("UPDATE students SET trial_booked_at=?, trial_at=?, stage='trial_booked' WHERE id=?",
                        (_ts(d, rnd) if _ts(d, rnd) >= created else created, trial_at, sid))
            if t > today or (t == today and trial_at > now_iso):
                con.execute("UPDATE students SET stage=? WHERE id=?", ("trial_reminder" if (t - today).days <= 1 else "trial_booked", sid))
                continue
            came = rnd.random() < 0.72
            con.execute("UPDATE students SET trial_came=? WHERE id=?", (1 if came else 0, sid))
            buys = came and rnd.random() < min(0.9, 0.5 * k) and load[g["id"]] < g["capacity"]
            decided = t + timedelta(days=rnd.choice([0, 0, 0, 1, 2, 3]))
            if buys and decided <= today:
                when = _ts(decided, rnd)
                when = min(when, now_iso)
                to_member(sid, g, when)
                bym = logic.month_of(decided)
                invoice(sid, bym, when)
                if bym != ym:  # пробное в конце месяца, купили уже следующий
                    pass
                active.append((sid, g))
                out["bought"] += 1
                continue
            age = (today - t).days
            if not came:
                stage = "no_answer" if age <= 7 else "refused"
            else:
                stage = "how_was" if age <= 3 else "refused"
            con.execute("UPDATE students SET stage=?, refusal_reason=?, archived=?, stage_changed_at=? WHERE id=?",
                        (stage, rnd.choice(REFUSALS[:5]) if stage == "refused" else "", 1 if age > 45 else 0, _ts(t, rnd), sid))

    # 5) следующий месяц: продление уже идёт (дедлайн 25-го)
    nxt = logic.shift_month(cur, 1)
    for sid, g in active:
        if rnd.random() < 0.94 and not con.execute("SELECT 1 FROM subscriptions WHERE student_id=? AND month=?", (sid, nxt)).fetchone():
            invoice(sid, nxt, renew_ts(nxt) if rnd.random() < (0.7 if today.day >= 25 else 0.35) else None)
            out["invoices"] += 1
    # 6) посещения за прошлый и текущий месяц
    for ym in (logic.shift_month(cur, -1), cur):
        a, b = logic.month_bounds(ym)
        for sid, g in active:
            gd = logic.parse_weekdays(g["weekdays"])
            d = a
            while d <= b and d < today:
                if d.weekday() in gd:
                    con.execute("INSERT OR IGNORE INTO attendance(student_id, group_id, date, status) VALUES (?,?,?,?)",
                                (sid, g["id"], d.isoformat(), "absent" if rnd.random() < 0.11 else "present"))
                d += timedelta(days=1)
    # 7) траты на рекламу по месяцам — чтобы считалась цена лида и ученика
    try:
        ids = {nm: sid for (sid, _w, _k), (_c, nm, _x, _y) in zip(src, REAL_SOURCES)}
        places = [("Таргет ВК (рекламный кабинет)", ids["Таргет ВК"], (18000, 32000)),
                  ("Промоутер у ТЦ «Небо»", ids["Промоутер у ТЦ"], (12000, 20000)),
                  ("Районный паблик", ids["Районный паблик"], (3000, 6000))]
        for name, sid_, (lo, hi) in places:
            pid = con.execute("""INSERT INTO ad_places(name, source_id, price, note, created_at) VALUES (?,?,?,?,?)""",
                              (name, sid_, lo, "fake-demo", now_iso)).lastrowid
            for ym in months:
                y, m = map(int, ym.split("-"))
                k = LEADS_BY_MONTH[m] / 80
                paid = date(y, m, rnd.randint(1, 5))
                if paid > today:
                    continue
                did = con.execute("""INSERT INTO ad_deals(place_id, paid_at, amount, posts_total, note, created_at) VALUES (?,?,?,?,?,?)""",
                                  (pid, paid.isoformat(), round(rnd.randint(lo, hi) * k, -2), 1, "fake-demo", now_iso)).lastrowid
                con.execute("INSERT INTO ad_posts(deal_id, planned, posted_at, status) VALUES (?,?,?,'posted')",
                            (did, (paid + timedelta(days=2)).isoformat(), (paid + timedelta(days=2)).isoformat()))
    except Exception:  # noqa: BLE001 — без раздела рекламы просто без трат
        pass
    out["members_now"] = len(active)
    return out
