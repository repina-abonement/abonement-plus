"""Бизнес-логика Абонемент Плюс: расчёт цены абонемента, пропуски, отработки.

Правила (из техзадания):
- Абонемент покупается на календарный месяц. Цена = число занятий группы
  в месяце (без дней отпуска) × цена одного занятия по тарифу.
- Тарифы: негибкий 1350 ₽/занятие, гибкий 1550 ₽/занятие (цены в настройках).
- Гибкий: неотработанные пропуски прошлого месяца уменьшают цену
  следующего месяца, максимум 3 занятия. Использованные пропуски обнуляются.
- Отработать можно пропуски текущего и прошлого месяца (оба тарифа).
  Более старые сгорают.
- Каждый пропуск используется один раз: отработкой ИЛИ перерасчётом.
"""
from __future__ import annotations

import math

import calendar
from datetime import date, datetime, timedelta

MAX_FLEX_CREDIT = 3

WEEKDAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTH_NAMES = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль",
               "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]


def parse_month(ym: str) -> tuple[int, int]:
    y, m = ym.split("-")
    return int(y), int(m)


def month_str(y: int, m: int) -> str:
    return f"{y:04d}-{m:02d}"


def shift_month(ym: str, delta: int) -> str:
    y, m = parse_month(ym)
    idx = y * 12 + (m - 1) + delta
    return month_str(idx // 12, idx % 12 + 1)


def month_of(d: date) -> str:
    return month_str(d.year, d.month)


def month_label(ym: str) -> str:
    y, m = parse_month(ym)
    return f"{MONTH_NAMES[m - 1]} {y}"


def month_bounds(ym: str) -> tuple[date, date]:
    y, m = parse_month(ym)
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def parse_weekdays(s: str) -> list[int]:
    return [int(x) for x in s.split(",") if x.strip() != ""]


def parse_day_times(s: str) -> dict[int, str]:
    """«5=11:30,6=10:00» → {5: "11:30", 6: "10:00"}: у группы в разные дни разное время."""
    out = {}
    for part in (s or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip().isdigit() and v.strip():
                out[int(k)] = v.strip()
    return out


def group_time(g, weekday: int) -> str:
    keys = g.keys() if hasattr(g, "keys") else []
    dt = parse_day_times(g["day_times"]) if "day_times" in keys else {}
    return dt.get(weekday) or g["time"] or ""


def lesson_dates(weekdays: list[int], ym: str) -> list[date]:
    start, end = month_bounds(ym)
    out, d = [], start
    while d <= end:
        if d.weekday() in weekdays:
            out.append(d)
        d += timedelta(days=1)
    return out


def in_vacation(d: date, vacations: list[tuple[date, date]]) -> bool:
    return any(s <= d <= e for s, e in vacations)


def age_warning(birth: str, age_txt: str, branch: str = "", on: date | None = None) -> str:
    """Подходит ли ребёнок группе по возрасту (Юлия, 28.10): «3-5» - от 3 до 5 полных лет включительно.
    Исключение: в Чоботовской в группу 6-8 можно до 10 лет. Пустая строка - подходит или не знаем."""
    import re
    n = [int(x) for x in re.findall(r"\d+", age_txt or "")]
    if not birth or not n:
        return ""
    try:
        b = date.fromisoformat(birth[:10])
    except ValueError:
        return ""
    on = on or date.today()
    months = (on.year - b.year) * 12 + on.month - b.month - (on.day < b.day)
    y, lo, hi = months // 12, n[0], n[-1]
    if "чобот" in (branch or "").lower() and (lo, hi) == (6, 8):
        hi = 9
    if lo <= y <= hi:
        return ""
    return f"не по возрасту: {y} {'год' if y % 10 == 1 and y % 100 != 11 else 'года' if y % 10 in (2, 3, 4) and y % 100 not in (12, 13, 14) else 'лет'} {months % 12} мес., группа {age_txt}"


def quote(*, weekdays: list[int], ym: str, tariff: str, prices: dict,
          vacations: list[tuple[date, date]], prev_unresolved_absences: int, closed: set | None = None) -> dict:
    """Цена абонемента на месяц ym.

    prev_unresolved_absences — неотработанные и не пересчитанные пропуски
    прошлого месяца (ym - 1). Учитываются только на гибком тарифе.
    """
    per = int(prices["flex" if tariff == "flex" else "fixed"])
    all_dates = [d for d in lesson_dates(weekdays, ym) if not closed or d.isoformat() not in closed]
    vac_dates = [d for d in all_dates if in_vacation(d, vacations)]
    paid_dates = [d for d in all_dates if not in_vacation(d, vacations)]
    credit_lessons = min(MAX_FLEX_CREDIT, prev_unresolved_absences) if tariff == "flex" else 0
    base = len(paid_dates) * per
    credit = credit_lessons * per
    total = max(0, base - credit)
    return {
        "month": ym,
        "month_label": month_label(ym),
        "per_lesson": per,
        "lessons_total": len(all_dates),
        "vacation_lessons": len(vac_dates),
        "lessons_paid": len(paid_dates),
        "base": base,
        "credit_lessons": credit_lessons,
        "credit": credit,
        "total": total,
        "dates": [d.isoformat() for d in paid_dates],
        "vacation_dates": [d.isoformat() for d in vac_dates],
    }


def apply_late_fee(total: int, *, enabled: bool, percent: float, today: date,
                   target_month: str, deadline_day: int = 25) -> tuple[int, int]:
    """Наценка за оплату после 25 числа месяца, предшествующего target_month."""
    if not enabled or percent <= 0:
        return total, 0
    prev = shift_month(target_month, -1)
    y, m = parse_month(prev)
    deadline = date(y, m, deadline_day)
    if today > deadline:
        fee = math.ceil(total * percent / 100 - 1e-9)  # копейки округляем вверх
        return total + fee, fee
    return total, 0


def makeup_window(today: date) -> tuple[date, date]:
    """Пропуски, которые ещё можно отработать: текущий и прошлый месяц."""
    cur = month_of(today)
    start, _ = month_bounds(shift_month(cur, -1))
    _, end = month_bounds(cur)
    return start, end


def trial_state(trial_at: datetime | None, contacted: bool, now: datetime,
                duration_min: int = 60) -> dict:
    """Состояние пробного: идёт / закончилось (+ цвет просрочки)."""
    if not trial_at:
        return {"state": None, "color": None, "minutes_since_end": None}
    end = trial_at + timedelta(minutes=duration_min)
    if trial_at <= now < end:
        return {"state": "on_trial", "color": None, "minutes_since_end": None}
    if now >= end:
        mins = int((now - end).total_seconds() // 60)
        color = None
        if not contacted:
            color = "red" if mins >= 60 else ("yellow" if mins >= 30 else None)
        return {"state": "trial_done", "color": color, "minutes_since_end": mins}
    return {"state": "upcoming", "color": None, "minutes_since_end": None}
