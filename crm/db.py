"""База данных SQLite: схема, подключение, тестовые данные."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
from datetime import date, datetime, timedelta

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    login TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'admin',      -- admin | operator | coach
    pw_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS branches (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY,
    branch_id INTEGER NOT NULL REFERENCES branches(id),
    name TEXT NOT NULL,
    weekdays TEXT NOT NULL,          -- "0,3" = Пн, Чт
    time TEXT NOT NULL DEFAULT '',
    age TEXT NOT NULL DEFAULT '',
    is_makeup INTEGER NOT NULL DEFAULT 0,
    coach_id INTEGER REFERENCES users(id),
    capacity INTEGER NOT NULL DEFAULT 12,     -- мест в группе
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS families (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS parents (
    id INTEGER PRIMARY KEY,
    family_id INTEGER NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    phone TEXT NOT NULL DEFAULT '',
    phone_norm TEXT NOT NULL DEFAULT '',
    messenger TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_parents_phone ON parents(phone_norm);

CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY,
    family_id INTEGER NOT NULL REFERENCES families(id),
    name TEXT NOT NULL,
    birth TEXT NOT NULL DEFAULT '',
    group_id INTEGER REFERENCES groups(id),
    tariff TEXT NOT NULL DEFAULT 'fixed',     -- fixed | flex
    funnel TEXT NOT NULL DEFAULT 'sales',     -- sales | members
    stage TEXT NOT NULL DEFAULT 'lead',
    stage_changed_at TEXT NOT NULL,
    trial_at TEXT,                            -- 'YYYY-MM-DDTHH:MM'
    contacted_after_trial INTEGER NOT NULL DEFAULT 0,
    skip_stage TEXT NOT NULL DEFAULT 'none',  -- none | offered | refused
    source TEXT NOT NULL DEFAULT '',
    refusal_reason TEXT NOT NULL DEFAULT '',
    converted_at TEXT,                        -- когда купил первый абонемент
    trial_booked_at TEXT,                     -- когда записали на пробное
    trial_came INTEGER,                       -- пришёл на пробное: 1 / 0 / NULL
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    group_id INTEGER NOT NULL REFERENCES groups(id),
    date TEXT NOT NULL,
    status TEXT NOT NULL,                     -- present | absent
    resolved TEXT,                            -- NULL | makeup | credit
    resolved_month TEXT,                      -- для credit: месяц абонемента
    UNIQUE(student_id, date, group_id)
);

CREATE TABLE IF NOT EXISTS vacations (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS makeups (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    absence_id INTEGER NOT NULL REFERENCES attendance(id) ON DELETE CASCADE,
    group_id INTEGER NOT NULL REFERENCES groups(id),
    date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'booked'     -- booked | done | missed | cancelled
);

-- Ученик может ходить в несколько групп: на каждую группу свой абонемент.
-- weekdays — дни, в которые ученик ходит в эту группу ('' = все дни группы).
CREATE TABLE IF NOT EXISTS enrollments (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    group_id INTEGER NOT NULL REFERENCES groups(id),
    weekdays TEXT NOT NULL DEFAULT '',
    tariff TEXT NOT NULL DEFAULT 'fixed',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    enrollment_id INTEGER REFERENCES enrollments(id) ON DELETE CASCADE,
    month TEXT NOT NULL,
    tariff TEXT NOT NULL,
    lessons INTEGER NOT NULL,
    per_lesson INTEGER NOT NULL,
    credit_lessons INTEGER NOT NULL DEFAULT 0,
    late_fee INTEGER NOT NULL DEFAULT 0,
    price INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'invoiced',  -- invoiced | paid | cancelled
    created_at TEXT NOT NULL,
    paid_at TEXT
);

CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    user_name TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'note',
    text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    user_id INTEGER,
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
"""

DEFAULT_SETTINGS = {
    "price_fixed": "1350",
    "price_flex": "1550",
    "late_fee_enabled": "0",
    "late_fee_percent": "10",
    "renewal_deadline_day": "25",
    "trial_minutes": "60",
    "price_single": "2500",
}

_local = threading.local()
DB_PATH = os.environ.get("CRM_DB", os.path.join(os.path.dirname(__file__), "..", "data", "crm.sqlite3"))


def connect(path: str | None = None) -> sqlite3.Connection:
    p = path or DB_PATH
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    con = sqlite3.connect(p, timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    # встроенный lower() в SQLite не понимает кириллицу
    con.create_function("ulower", 1, lambda v: v.lower() if isinstance(v, str) else v, deterministic=True)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    return con


def get_conn() -> sqlite3.Connection:
    con = getattr(_local, "con", None)
    if con is None:
        con = connect()
        _local.con = con
    return con


def reset_thread_conn():
    con = getattr(_local, "con", None)
    if con is not None:
        con.close()
    _local.con = None


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat(timespec="minutes")


def normalize_phone(phone: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) == 11 and digits[0] in "78":
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    return digits


def hash_password(pw: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex()
    return f"{salt}${h}"


def check_password(pw: str, stored: str) -> bool:
    salt, _ = stored.split("$", 1)
    return hmac.compare_digest(hash_password(pw, salt), stored)


def init_db(con: sqlite3.Connection):
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(groups)")}
    if "coach_id" not in cols:
        con.execute("ALTER TABLE groups ADD COLUMN coach_id INTEGER REFERENCES users(id)")
    scols = {r[1] for r in con.execute("PRAGMA table_info(students)")}
    for col in ("converted_at TEXT", "trial_booked_at TEXT", "trial_came INTEGER"):
        if col.split()[0] not in scols:
            con.execute(f"ALTER TABLE students ADD COLUMN {col}")
    if "capacity" not in cols:
        con.execute("ALTER TABLE groups ADD COLUMN capacity INTEGER NOT NULL DEFAULT 12")
    subcols = {r[1] for r in con.execute("PRAGMA table_info(subscriptions)")}
    if "enrollment_id" not in subcols:
        # старая схема: один абонемент на ученика в месяц → абонемент на каждую группу
        con.executescript("""
            CREATE TABLE subscriptions_new (
                id INTEGER PRIMARY KEY,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                enrollment_id INTEGER REFERENCES enrollments(id) ON DELETE CASCADE,
                month TEXT NOT NULL, tariff TEXT NOT NULL, lessons INTEGER NOT NULL, per_lesson INTEGER NOT NULL,
                credit_lessons INTEGER NOT NULL DEFAULT 0, late_fee INTEGER NOT NULL DEFAULT 0, price INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'invoiced', created_at TEXT NOT NULL, paid_at TEXT);
            INSERT INTO subscriptions_new(id, student_id, month, tariff, lessons, per_lesson, credit_lessons, late_fee,
                price, status, created_at, paid_at)
              SELECT id, student_id, month, tariff, lessons, per_lesson, credit_lessons, late_fee, price, status,
                created_at, paid_at FROM subscriptions;
            DROP TABLE subscriptions;
            ALTER TABLE subscriptions_new RENAME TO subscriptions;
        """)
    sync_enrollments(con)
    from . import ai, calldigest, capacity, closures, comms, program, pu, waitlist, expenses, fakegen, legal, merch, money, offers, planner, schedule, sources, staff, tasks
    legal.init(con)
    schedule.init(con)
    capacity.init(con)
    closures.init(con)
    program.init(con)
    waitlist.init(con)
    staff.init(con)
    merch.init(con)
    sources.init(con)
    money.init(con)
    comms.init(con)
    ai.init(con)
    calldigest.init(con)
    tasks.init(con)
    planner.init(con)
    expenses.init(con)
    offers.init(con)
    fakegen.init(con)
    pu.init(con)
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sub_enr_month ON subscriptions(enrollment_id, month)")
    for k, v in DEFAULT_SETTINGS.items():
        con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
    login = os.environ.get("CRM_ADMIN_LOGIN", "admin")
    pw = os.environ.get("CRM_ADMIN_PASSWORD")
    exists = con.execute("SELECT id FROM users WHERE login = ?", (login,)).fetchone()
    if not exists:
        if not pw:
            pw = secrets.token_urlsafe(9)
            print(f"\n*** Создан администратор: логин '{login}', пароль '{pw}' — смените его! ***\n", flush=True)
        con.execute("INSERT INTO users(login, name, role, pw_hash) VALUES (?, ?, 'admin', ?)",
                    (login, "Администратор", hash_password(pw)))


def sync_enrollments(con):
    """Каждому ученику с группой — запись о группе; абонементам без группы — основная группа ученика."""
    con.execute("""INSERT INTO enrollments(student_id, group_id, weekdays, tariff, created_at)
                   SELECT s.id, s.group_id, '', s.tariff, s.created_at FROM students s
                   WHERE s.group_id IS NOT NULL AND s.funnel = 'members'
                     AND NOT EXISTS (SELECT 1 FROM enrollments e WHERE e.student_id = s.id)""")
    con.execute("""UPDATE subscriptions SET enrollment_id =
                     (SELECT MIN(e.id) FROM enrollments e WHERE e.student_id = subscriptions.student_id)
                   WHERE enrollment_id IS NULL""")


def settings(con) -> dict:
    return {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM settings")}


DATA_TABLES = ["shifts", "payments", "tasks", "auto_messages", "messages", "conversations", "calls", "lead_imports", "hook_log", "history", "makeups", "subscriptions", "vacations", "attendance", "students", "parents",
               "families", "groups", "branches", "audit"]


def reset_demo(con: sqlite3.Connection):
    """Стирает клиентов и создаёт тестовых заново. Только при CRM_DEMO=1."""
    for t in DATA_TABLES:
        con.execute(f"DELETE FROM {t}")
    con.execute("UPDATE groups SET coach_id = NULL")
    con.execute("DELETE FROM users WHERE login IN ('trener', 'trener2', 'trener3', 'olga')")
    con.execute("DELETE FROM integrations WHERE key = 'task_owner_trials'")


def seed_demo(con: sqlite3.Connection, today: date | None = None):
    """Выдуманные клиенты для теста. Не использовать с реальными данными."""
    if con.execute("SELECT COUNT(*) FROM branches").fetchone()[0]:
        return
    today = today or date.today()
    con.execute("BEGIN")
    b = {}
    for name in ["ТЦ «Небо»", "Чоботовская", "Арктика"]:
        b[name] = con.execute("INSERT INTO branches(name) VALUES (?)", (name,)).lastrowid
    g = {}
    groups = [
        ("ТЦ «Небо»", "Небо Пн/Чт 17:00", "0,3", "17:00", "3–5", 0),
        ("ТЦ «Небо»", "Небо Вт/Пт 18:00", "1,4", "18:00", "6–8", 0),
        ("Чоботовская", "Чоботовская Пн/Ср 17:30", "0,2", "17:30", "3–5", 0),
        ("Арктика", "Арктика Вт/Чт 18:00", "1,3", "18:00", "6–8", 0),
        ("ТЦ «Небо»", "Отработка Сб 14:00", "5", "14:00", "3–5", 1),
        ("ТЦ «Небо»", "Отработка Сб 15:00", "5", "15:00", "6–8", 1),
    ]
    for br, name, wd, t, age, mk in groups:
        g[name] = con.execute(
            "INSERT INTO groups(branch_id, name, weekdays, time, age, is_makeup) VALUES (?,?,?,?,?,?)",
            (b[br], name, wd, t, age, mk)).lastrowid

    now = now_iso()

    def family(fname, parents):
        fid = con.execute("INSERT INTO families(name) VALUES (?)", (fname,)).lastrowid
        for pname, phone in parents:
            con.execute("INSERT INTO parents(family_id, name, phone, phone_norm, messenger) VALUES (?,?,?,?,?)",
                        (fid, pname, phone, normalize_phone(phone), "WhatsApp"))
        return fid

    def student(fid, name, birth, group, tariff, funnel, stage, trial_at=None, source="Сайт"):
        sid = con.execute(
            """INSERT INTO students(family_id, name, birth, group_id, tariff, funnel, stage,
               stage_changed_at, trial_at, source, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (fid, name, birth, g.get(group), tariff, funnel, stage, now, trial_at, source, now)).lastrowid
        con.execute("INSERT INTO history(student_id, ts, user_name, kind, text) VALUES (?,?,?,?,?)",
                    (sid, now, "Система", "system", "Тестовый клиент создан"))
        return sid

    # Ученики с абонементом
    f1 = family("Ивановы", [("Анна Иванова", "+7 900 111-11-11")])
    s1 = student(f1, "Миша Иванов", "2021-03-14", "Небо Пн/Чт 17:00", "flex", "members", "active")
    s2 = student(f1, "Катя Иванова", "2019-07-02", "Небо Вт/Пт 18:00", "fixed", "members", "active")
    f2 = family("Петровы", [("Олег Петров", "+7 900 222-22-22")])
    s3 = student(f2, "Тимур Петров", "2020-11-20", "Небо Пн/Чт 17:00", "fixed", "members", "active")
    f3 = family("Сидоровы", [("Мария Сидорова", "+7 900 333-33-33")])
    s4 = student(f3, "Лиза Сидорова", "2021-01-09", "Чоботовская Пн/Ср 17:30", "flex", "members", "active")
    f4 = family("Кузнецовы", [("Ирина Кузнецова", "+7 900 444-44-44")])
    s5 = student(f4, "Артём Кузнецов", "2018-05-30", "Арктика Вт/Чт 18:00", "flex", "members", "active")

    # Лиды в воронке продаж
    f5 = family("Смирновы", [("Елена Смирнова", "+7 900 555-55-55")])
    student(f5, "Саша Смирнов", "2021-09-01", None, "fixed", "sales", "lead", source="Промоутер")
    f6 = family("Волковы", [("Дмитрий Волков", "+7 900 666-66-66")])
    student(f6, "Ваня Волков", "2020-02-17", None, "fixed", "sales", "no_answer")
    f7 = family("Морозовы", [("Ольга Морозова", "+7 900 777-77-77")])
    trial_soon = (datetime.now() + timedelta(days=1)).replace(hour=17, minute=0, second=0, microsecond=0)
    student(f7, "Даша Морозова", "2021-06-11", "Небо Пн/Чт 17:00", "fixed", "sales", "trial_booked",
            trial_at=trial_soon.isoformat(timespec="minutes"))
    f8 = family("Новиковы", [("Светлана Новикова", "+7 900 888-88-88")])
    trial_done = (datetime.now() - timedelta(minutes=110)).replace(second=0, microsecond=0)
    student(f8, "Глеб Новиков", "2019-12-25", "Небо Вт/Пт 18:00", "fixed", "sales", "trial_booked",
            trial_at=trial_done.isoformat(timespec="minutes"))

    # Посещения за прошлый и текущий месяц: несколько пропусков
    from .logic import lesson_dates, month_of, parse_weekdays, shift_month
    cur = month_of(today)
    absences = {s1: 2, s3: 1, s4: 4, s5: 1}
    members = {s1: "Небо Пн/Чт 17:00", s2: "Небо Вт/Пт 18:00", s3: "Небо Пн/Чт 17:00",
               s4: "Чоботовская Пн/Ср 17:30", s5: "Арктика Вт/Чт 18:00"}
    for sid, gname in members.items():
        wd = parse_weekdays(con.execute("SELECT weekdays FROM groups WHERE id=?", (g[gname],)).fetchone()[0])
        n_abs = absences.get(sid, 0)
        for ym in (shift_month(cur, -1), cur):
            dates = [d for d in lesson_dates(wd, ym) if d < today]
            for i, d in enumerate(dates):
                status = "absent" if (ym == cur and i < n_abs) else "present"
                con.execute("INSERT OR IGNORE INTO attendance(student_id, group_id, date, status) VALUES (?,?,?,?)",
                            (sid, g[gname], d.isoformat(), status))
            # абонемент текущего месяца оплачен
        con.execute("""INSERT OR IGNORE INTO subscriptions(student_id, month, tariff, lessons, per_lesson, price,
                       status, created_at, paid_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (sid, cur, "fixed", 8, 1350, 10800, "paid", now, now))
        con.execute("""UPDATE subscriptions SET tariff=(SELECT tariff FROM students WHERE id=?),
                       price=CASE (SELECT tariff FROM students WHERE id=?) WHEN 'flex' THEN 12400 ELSE 10800 END
                       WHERE student_id=?""", (sid, sid, sid))
        con.execute("UPDATE subscriptions SET month=? WHERE student_id=? AND month=?",
                    (cur, sid, cur))
        prev_m = shift_month(cur, -1)
        con.execute("""INSERT OR IGNORE INTO subscriptions(student_id, month, tariff, lessons, per_lesson, price,
                       status, created_at, paid_at) SELECT student_id, ?, tariff, lessons, per_lesson, price,
                       'paid', created_at, paid_at FROM subscriptions WHERE student_id=? AND month=?""",
                    (prev_m, sid, cur))
    # история оплат за 14 месяцев, чтобы на дашборде были графики
    import random
    rnd = random.Random(7)
    for sid in members:
        for back in range(2, 15):
            if rnd.random() < 0.2:
                continue
            m = shift_month(cur, -back)
            con.execute("""INSERT OR IGNORE INTO subscriptions(student_id, month, tariff, lessons, per_lesson, price,
                           status, created_at, paid_at) SELECT id, ?, tariff, 8, 0, ?, 'paid', ?, ? FROM students WHERE id=?""",
                        (m, rnd.choice([10800, 12400, 9450, 13950]), now, now, sid))
    con.execute("UPDATE students SET trial_booked_at=created_at WHERE trial_at IS NOT NULL")
    con.execute("UPDATE students SET trial_came=1 WHERE trial_at < ?", (now,))
    con.execute("UPDATE groups SET capacity=12 WHERE is_makeup=0")

    sync_enrollments(con)
    # Миша ходит в две группы: Пн/Чт — только по понедельникам, Вт/Пт — только по пятницам
    e1 = con.execute("SELECT id FROM enrollments WHERE student_id=?", (s1,)).fetchone()[0]
    con.execute("UPDATE enrollments SET weekdays='0' WHERE id=?", (e1,))
    con.execute("INSERT INTO enrollments(student_id, group_id, weekdays, tariff, created_at) VALUES (?,?,?,?,?)",
                (s1, g["Небо Вт/Пт 18:00"], "4", "flex", now))

    # тестовый тренер для групп Небо
    coach = con.execute("INSERT OR IGNORE INTO users(login, name, role, pw_hash) VALUES (?,?,?,?)",
                        ("trener", "Тренер Алексей (тест)", "coach", hash_password("trener-test-1"))).lastrowid
    if coach:
        con.execute("UPDATE groups SET coach_id=? WHERE branch_id=?", (coach, b["ТЦ «Небо»"]))
    con.execute("COMMIT")
