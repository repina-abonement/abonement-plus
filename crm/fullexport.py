"""«Скачать базу в Excel»: все данные CRM понятными таблицами, по листу на тему. Только для руководителя."""
import re
from datetime import datetime

from . import xlsx
from .money import METHODS

ATT = {"present": "был", "absent": "пропуск"}
SUB = {"paid": "оплачен", "invoiced": "выставлен", "cancelled": "отменён", "partial": "частично"}
POST = {"admin": "Руководитель", "operator": "Администратор", "coach": "Тренер", "lawyer": "Юрист"}


def _ru(v):
    """2026-10-31T15:57 → 31.10.2026 15:57, 2026-10 → 10.2026: даты в привычном виде."""
    if not isinstance(v, str):
        return v
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?)?", v)
    if m:
        return f"{m[3]}.{m[2]}.{m[1]}" + (f" {m[4]}" if m[4] else "")
    m = re.fullmatch(r"(\d{4})-(\d{2})", v)
    return f"{m[2]}.{m[1]}" if m else v


def _rows(con, sql, args=()):
    return [list(r) for r in con.execute(sql, args)]


def build(con) -> tuple[bytes, str]:
    real = "AND s.fake=0" if con.execute("SELECT COUNT(*) FROM students WHERE fake=0").fetchone()[0] else ""
    kids = [["Ребёнок", "Дата рождения", "Семья", "Родитель", "Телефон", "Мессенджер", "Ещё родители", "Группы", "Тариф",
             "Статус", "Этап", "Источник", "Пришёл в CRM", "Стал учеником", "Код"]]
    for r in con.execute(f"""
        SELECT s.id, s.name, s.birth, f.name, s.tariff, s.funnel, s.stage, s.archived, s.source, s.created_at, s.converted_at,
          (SELECT GROUP_CONCAT(b.name || ' · ' || g.name, ', ') FROM enrollments e JOIN groups g ON g.id=e.group_id
             JOIN branches b ON b.id=g.branch_id WHERE e.student_id=s.id AND e.active=1),
          s.family_id
        FROM students s JOIN families f ON f.id=s.family_id WHERE 1=1 {real} ORDER BY s.archived, s.funnel DESC, s.name"""):
        ps = con.execute("SELECT name, phone, messenger FROM parents WHERE family_id=? ORDER BY id", (r[12],)).fetchall()
        p0 = ps[0] if ps else ("", "", "")
        status = "в архиве" if r[7] else ("ученик" if r[5] == "members" else "лид")
        kids.append([r[1], r[2], r[3], p0[0], p0[1], p0[2], "; ".join(f"{p[0]} {p[1]}".strip() for p in ps[1:]),
                     r[11] or "", r[4], status, r[6], r[8], (r[9] or "")[:10], (r[10] or "")[:10], r[0]])
    subs = [["Ребёнок", "Месяц", "Группа", "Тариф", "Занятий", "Цена занятия", "Скидка", "Сумма скидки", "Надбавка",
             "К оплате", "Статус", "Оплачен", "Вид"]]
    subs += _rows(con, f"""SELECT s.name, x.month, COALESCE(b.name || ' · ' || g.name, ''), x.tariff, x.lessons, x.per_lesson,
        x.discount_name, x.discount_amount, x.late_fee, x.price, x.status, substr(COALESCE(x.paid_at,''),1,10), x.kind
        FROM subscriptions x JOIN students s ON s.id=x.student_id
        LEFT JOIN enrollments e ON e.id=x.enrollment_id LEFT JOIN groups g ON g.id=e.group_id LEFT JOIN branches b ON b.id=g.branch_id
        WHERE 1=1 {real} ORDER BY x.month DESC, s.name""")
    for r in subs[1:]:
        r[10] = SUB.get(r[10], r[10])
        r[12] = "месяц" if r[12] == "month" else ("разовое" if r[12] == "single" else r[12])
    pays = [["Дата", "Ребёнок", "Сумма", "Способ", "Комментарий", "Внёс", "Подтверждено", "Отменён"]]
    pays += _rows(con, f"""SELECT substr(p.paid_at,1,16), s.name, p.amount, p.method, p.comment, p.user_name,
        CASE WHEN p.confirmed THEN 'да' ELSE '' END, CASE WHEN p.cancelled THEN 'да' ELSE '' END
        FROM payments p JOIN students s ON s.id=p.student_id WHERE 1=1 {real} ORDER BY p.paid_at DESC""")
    for r in pays[1:]:
        r[3] = METHODS.get(r[3], r[3])
    att = [["Дата", "Ребёнок", "Группа", "Отметка", "Медсправка", "Пропуск закрыт", "Комментарий"]]
    att += _rows(con, f"""SELECT a.date, s.name, COALESCE(b.name || ' · ' || g.name, ''), a.status,
        CASE WHEN a.cert THEN 'да' ELSE '' END, CASE WHEN a.resolved THEN 'да' ELSE '' END, a.note
        FROM attendance a JOIN students s ON s.id=a.student_id LEFT JOIN groups g ON g.id=a.group_id
        LEFT JOIN branches b ON b.id=g.branch_id WHERE 1=1 {real} ORDER BY a.date DESC, s.name""")
    for r in att[1:]:
        r[3] = ATT.get(r[3], r[3])
    grp = [["Филиал", "Группа", "Дни", "Время", "Возраст", "Тренер", "Помощник", "Мест", "Учеников", "Активна"]]
    grp += _rows(con, """SELECT b.name, g.name, g.weekdays, g.time, g.age, COALESCE(c.name,''), COALESCE(a.name,''), g.capacity,
        (SELECT COUNT(*) FROM enrollments e JOIN students s ON s.id=e.student_id WHERE e.group_id=g.id AND e.active=1 AND s.archived=0),
        CASE WHEN g.active AND b.active THEN 'да' ELSE 'архив' END
        FROM groups g JOIN branches b ON b.id=g.branch_id LEFT JOIN users c ON c.id=g.coach_id LEFT JOIN users a ON a.id=g.assistant_id
        ORDER BY b.name, g.name""")
    days = "Пн Вт Ср Чт Пт Сб Вс".split()
    for r in grp[1:]:
        r[2] = " ".join(days[int(d)] for d in str(r[2] or "") if d.isdigit() and int(d) < 7)
    staff = [["Имя", "Должность", "Промоутер", "Телефон", "Telegram", "Логин", "Работает"]]
    for r in con.execute("SELECT name, role, is_senior, is_promoter, phone, tg_username, login, active FROM users ORDER BY active DESC, name"):
        staff.append([r[0], "Старший администратор" if r[2] else POST.get(r[1], r[1]), "да" if r[3] else "", r[4], r[5], r[6],
                      "да" if r[7] else "архив"])
    now = datetime.now()
    sheets = [("Дети и родители", kids), ("Абонементы", subs), ("Оплаты", pays), ("Посещения", att),
              ("Группы", grp), ("Сотрудники", staff)]
    sheets = [(n, [rows[0]] + [[_ru(v) for v in r] for r in rows[1:]]) for n, rows in sheets]
    return xlsx.build(sheets), f"abonement-plus-{now:%Y-%m-%d_%H%M}.xlsx"
