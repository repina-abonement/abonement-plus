"""Перенос клиентов из старой ERP (Юлия, 31.10.2026: «абсолютно все данные, чтобы работа продолжилась без накладок»).

Как устроено (данные клиентов не проходят через чат и помощника):
1. Руководитель загружает выгрузки ERP (Excel .xlsx или CSV) в Настройки → «Перенос из ERP». Файлы разбираются на сервере.
2. CRM угадывает, какая колонка что значит (по названиям), неугаданное руководитель выбирает сам.
3. Группы ERP сопоставляются с нашими группами.
4. Пробный прогон: ничего не записывает, показывает, что будет перенесено, и все нестыковки.
5. Перенос (только с отметкой «изменения в РКН поданы»): резервная копия, удаление выдуманных клиентов, запись.
   Всё записанное помнится, перенос можно отменить целиком.
6. Сверка: итоги CRM против итогов файлов.
"""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from datetime import date, datetime, timedelta
from xml.etree import ElementTree as ET

from . import logic
from .db import normalize_phone

SCHEMA = """
CREATE TABLE IF NOT EXISTS erp_uploads (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,                  -- clients | subs | payments | visits | tasks
    filename TEXT NOT NULL,
    headers TEXT NOT NULL,
    rows TEXT NOT NULL,
    mapping TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS erp_group_map (
    erp_name TEXT PRIMARY KEY,
    group_id INTEGER                     -- NULL = не переносить в группу
);
CREATE TABLE IF NOT EXISTS erp_batches (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    user_name TEXT NOT NULL DEFAULT '',
    stats TEXT NOT NULL DEFAULT '{}',
    backup TEXT NOT NULL DEFAULT '',
    undone_at TEXT
);
CREATE TABLE IF NOT EXISTS erp_rows (
    batch_id INTEGER NOT NULL,
    tbl TEXT NOT NULL,
    row_id INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_erp_rows ON erp_rows(batch_id, tbl);
"""


def init(con):
    con.executescript(SCHEMA)
    if "erp_id" not in [c[1] for c in con.execute("PRAGMA table_info(students)")]:
        con.execute("ALTER TABLE students ADD COLUMN erp_id TEXT NOT NULL DEFAULT ''")


class ImportError_(Exception):
    pass


# ---------------- чтение файлов ----------------

def _xlsx(data: bytes) -> list[list[str]]:
    z = zipfile.ZipFile(io.BytesIO(data))
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
            shared.append("".join(t.text or "" for t in si.iter("{%s}t" % ns["m"])))
    sheets = sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
    if not sheets:
        raise ImportError_("В файле Excel нет листов")
    root = ET.fromstring(z.read(sheets[0]))
    rows = []
    for r in root.iter("{%s}row" % ns["m"]):
        cells = {}
        for c in r.findall("m:c", ns):
            ref = c.get("r", "")
            col = 0
            for ch in re.sub(r"\d", "", ref):
                col = col * 26 + (ord(ch) - 64)
            t = c.get("t")
            v = c.find("m:v", ns)
            if t == "s" and v is not None:
                val = shared[int(v.text)]
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter("{%s}t" % ns["m"]))
            else:
                val = v.text if v is not None and v.text is not None else ""
            cells[col - 1] = val
        if cells:
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
    return rows


def _csv(data: bytes) -> list[list[str]]:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ImportError_("Не получилось прочитать файл: неизвестная кодировка")
    first = text.split("\n", 1)[0]
    delim = max((";", ",", "\t"), key=first.count)
    return [r for r in csv.reader(io.StringIO(text), delimiter=delim)]


def parse_table(data: bytes, filename: str) -> tuple[list[str], list[list[str]]]:
    name = filename.lower()
    if name.endswith(".xlsx"):
        rows = _xlsx(data)
    elif name.endswith(".csv") or name.endswith(".txt"):
        rows = _csv(data)
    elif name.endswith(".xls"):
        raise ImportError_("Старый формат .xls: откройте файл в Excel и сохраните как .xlsx или CSV")
    else:
        raise ImportError_("Нужен файл Excel (.xlsx) или CSV")
    rows = [[str(c).strip() for c in r] for r in rows if any(str(c).strip() for c in r)]
    if len(rows) < 2:
        raise ImportError_("В файле нет строк с данными")
    # заголовок: первая строка, где заполнено хотя бы 2 ячейки
    hi = next((i for i, r in enumerate(rows[:10]) if sum(1 for c in r if c) >= 2), 0)
    headers = [h or f"Колонка {i + 1}" for i, h in enumerate(rows[hi])]
    body = [r + [""] * (len(headers) - len(r)) for r in rows[hi + 1:]]
    return headers, [r[:len(headers)] for r in body]


# ---------------- что какой колонкой называется ----------------

FIELDS = {
    "clients": [
        ("erp_id", "Номер клиента в ERP", ["id", "код", "номер клиента", "№ клиента", "id клиента"]),
        ("child_name", "Ребёнок (ФИО или имя)", ["фио ребенка", "ребенок", "ученик", "фио ученика", "имя ребенка", "клиент", "фио", "имя"]),
        ("child_last", "Фамилия ребёнка (если отдельно)", ["фамилия ребенка", "фамилия"]),
        ("birth", "Дата рождения", ["дата рождения", "д.р", "др", "рождения"]),
        ("parent_name", "Родитель", ["фио родителя", "родитель", "контактное лицо", "представитель", "мама", "папа"]),
        ("phone", "Телефон", ["телефон родителя", "телефон", "моб", "тел"]),
        ("phone2", "Второй телефон", ["телефон 2", "доп. телефон", "второй телефон", "доп телефон"]),
        ("email", "Почта", ["email", "e-mail", "почта", "эл. почта"]),
        ("branch", "Филиал", ["филиал", "площадка", "локация", "адрес"]),
        ("group", "Группа", ["группа", "расписание"]),
        ("status", "Статус / этап", ["статус", "этап", "состояние"]),
        ("tariff", "Тариф (гибкий / базовый)", ["тариф", "тип абонемента"]),
        ("trial_at", "Дата пробного", ["пробное", "дата пробного", "пу"]),
        ("source", "Источник", ["источник", "откуда", "канал"]),
        ("created", "Дата обращения", ["дата создания", "дата обращения", "создан", "дата заявки", "дата добавления"]),
        ("comment", "Комментарий", ["комментарий", "примечание", "заметка"]),
    ],
    "subs": [
        ("erp_id", "Номер клиента в ERP", ["id клиента", "код клиента", "номер клиента", "id"]),
        ("child_name", "Ребёнок", ["фио ребенка", "ребенок", "ученик", "клиент", "фио"]),
        ("phone", "Телефон", ["телефон"]),
        ("month", "Месяц абонемента", ["месяц", "период", "за месяц"]),
        ("group", "Группа", ["группа"]),
        ("price", "Стоимость", ["стоимость", "сумма", "цена", "к оплате"]),
        ("lessons", "Занятий", ["занятий", "кол-во занятий", "количество занятий", "занятия"]),
        ("status", "Статус (оплачен / нет)", ["статус", "оплачен", "оплата"]),
        ("tariff", "Тариф", ["тариф", "тип"]),
    ],
    "payments": [
        ("erp_id", "Номер клиента в ERP", ["id клиента", "код клиента", "номер клиента", "id"]),
        ("child_name", "Ребёнок", ["фио ребенка", "ребенок", "ученик", "клиент", "фио", "плательщик"]),
        ("phone", "Телефон", ["телефон"]),
        ("date", "Дата оплаты", ["дата оплаты", "дата платежа", "дата"]),
        ("amount", "Сумма", ["сумма", "оплачено", "размер"]),
        ("method", "Способ оплаты", ["способ", "тип оплаты", "форма оплаты", "касса"]),
        ("month", "За какой месяц", ["месяц", "период", "за месяц"]),
        ("comment", "Комментарий", ["комментарий", "назначение", "примечание"]),
    ],
    "visits": [
        ("erp_id", "Номер клиента в ERP", ["id клиента", "код клиента", "номер клиента", "id"]),
        ("child_name", "Ребёнок", ["фио ребенка", "ребенок", "ученик", "клиент", "фио"]),
        ("phone", "Телефон", ["телефон"]),
        ("date", "Дата занятия", ["дата занятия", "дата"]),
        ("group", "Группа", ["группа"]),
        ("status", "Отметка (был / пропуск)", ["отметка", "посещение", "статус", "присутствие", "был"]),
    ],
    "tasks": [
        ("erp_id", "Номер клиента в ERP", ["id клиента", "код клиента", "номер клиента", "id"]),
        ("child_name", "Ребёнок", ["фио ребенка", "ребенок", "ученик", "клиент", "фио"]),
        ("phone", "Телефон", ["телефон"]),
        ("task", "Задача (вид или статус)", ["задача", "тип задачи", "статус задачи", "вид", "статус"]),
        ("due", "Срок", ["срок", "дата", "когда"]),
        ("comment", "Комментарий", ["комментарий", "описание", "текст"]),
    ],
}
KIND_LABELS = {"clients": "Клиенты (дети и родители)", "subs": "Абонементы", "payments": "Оплаты", "visits": "Посещения", "tasks": "Задачи"}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower().replace("ё", "е")).strip(" :.")


def guess_mapping(kind: str, headers: list[str]) -> dict:
    out, used = {}, set()
    hs = [_norm(h) for h in headers]
    for key, _label, syn in FIELDS[kind]:
        best = None
        for s in syn:  # сначала точное совпадение, потом «содержит»
            for i, h in enumerate(hs):
                if i not in used and h == s:
                    best = i; break
            if best is not None:
                break
        if best is None:
            for s in syn:
                for i, h in enumerate(hs):
                    if i not in used and len(s) > 2 and s in h:
                        best = i; break
                if best is not None:
                    break
        if best is not None:
            out[key] = best
            used.add(best)
    return out


def guess_kind(headers: list[str]) -> str:
    hs = " | ".join(_norm(h) for h in headers)
    if "задач" in hs:
        return "tasks"
    if "отметк" in hs or "посещ" in hs or "присутств" in hs:
        return "visits"
    if ("сумма" in hs or "оплачено" in hs) and "дата" in hs and "месяц" not in hs:
        return "payments"
    if "месяц" in hs or "период" in hs or "абонемент" in hs:
        return "subs"
    return "clients"


# ---------------- значения ----------------

MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}


def parse_date(v: str) -> str | None:
    v = (v or "").strip()
    if not v:
        return None
    if re.fullmatch(r"\d{5}(\.\d+)?", v):  # дата Excel числом
        d = datetime(1899, 12, 30) + timedelta(days=float(v))
        return d.date().isoformat() if d.hour == 0 and d.minute == 0 else d.isoformat(timespec="minutes")
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{1,2}):(\d{2}))?", v)
    if m:
        y, mo, d, hh, mm = m.groups()
    else:
        m = re.match(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})(?:[ ,]+(\d{1,2}):(\d{2}))?", v)
        if not m:
            return None
        d, mo, y, hh, mm = m.groups()
        if len(y) == 2:
            y = "20" + y if int(y) < 50 else "19" + y
    try:
        dt = datetime(int(y), int(mo), int(d), int(hh or 0), int(mm or 0))
    except ValueError:
        return None
    return dt.isoformat(timespec="minutes") if hh else dt.date().isoformat()


def parse_month(v: str) -> str | None:
    v = _norm(v)
    if not v:
        return None
    m = re.match(r"(\d{4})-(\d{1,2})", v)
    if m:
        return f"{int(m[1]):04d}-{int(m[2]):02d}"
    m = re.match(r"(\d{1,2})[./](\d{4})$", v)
    if m:
        return f"{int(m[2]):04d}-{int(m[1]):02d}"
    y = re.search(r"(20\d{2})", v)
    for k, n in MONTHS.items():
        if v.startswith(k) and (k != "ма" or v.startswith("ма") and not v.startswith("мар")):
            return f"{int(y[1]) if y else date.today().year:04d}-{n:02d}"
    d = parse_date(v)
    return d[:7] if d else None


def parse_money(v: str) -> int | None:
    v = re.sub(r"[^\d,.\-]", "", (v or "").replace(" ", "").replace(" ", ""))
    if not v:
        return None
    v = v.replace(",", ".")
    if v.count(".") > 1:
        v = v.replace(".", "", v.count(".") - 1)
    try:
        return int(round(float(v)))
    except ValueError:
        return None


def pay_method(v: str) -> str:
    v = _norm(v)
    if "нал" in v and "без" not in v:
        return "cash"
    if "сбп" in v or "qr" in v:
        return "sbp"
    if "онлайн" in v or "paykeeper" in v or "сайт" in v or "эквайр" in v:
        return "online"
    if "карт" in v or "терминал" in v:
        return "card"
    if "перевод" in v or "безнал" in v or "счет" in v or "р/с" in v:
        return "transfer"
    return "unknown"


def client_stage(status: str) -> tuple[str, str]:
    """(funnel, stage) по статусу ERP."""
    s = _norm(status)
    if any(w in s for w in ("ушел", "ушла", "отток", "выбыл", "архив", "бывш")):
        return "members", "left"
    if any(w in s for w in ("занима", "актив", "ученик", "постоян", "клиент", "абонемент", "купил")):
        return "members", "active"
    if "отказ" in s:
        return "sales", "refused"
    if "недозвон" in s or "не дозвон" in s:
        return "sales", "no_answer"
    if "не дош" in s or "не приш" in s:
        return "sales", "missed_trial"
    if "как прошл" in s or "был на пробн" in s:
        return "sales", "how_was"
    if "пробн" in s or "пу" == s or "запис" in s:
        return "sales", "trial_booked"
    if "позже" in s or "думают" in s:
        return "sales", "later"
    return "sales", "lead"


def visit_status(v: str) -> str | None:
    v = _norm(v)
    if not v:
        return None
    if v in ("1", "да", "+", "был", "была", "присутствовал", "присутствовала", "пришел", "пришла", "посетил", "посетила") or "был" in v and "не" not in v:
        return "present"
    if v in ("0", "нет", "-", "н", "нб") or "пропуск" in v or "не был" in v or "отсутств" in v or "болел" in v:
        return "absent"
    return None


# ---------------- загрузки ----------------

def upload(con, filename: str, data: bytes, kind: str | None = None) -> dict:
    headers, rows = parse_table(data, filename)
    kind = kind if kind in FIELDS else guess_kind(headers)
    mapping = guess_mapping(kind, headers)
    uid = con.execute("INSERT INTO erp_uploads(kind, filename, headers, rows, mapping, created_at) VALUES (?,?,?,?,?,?)",
                      (kind, filename[:200], json.dumps(headers, ensure_ascii=False), json.dumps(rows, ensure_ascii=False),
                       json.dumps(mapping), datetime.now().isoformat(timespec="seconds"))).lastrowid
    return {"id": uid, "kind": kind, "rows": len(rows)}


def uploads(con, preview: int = 3) -> list[dict]:
    out = []
    for r in con.execute("SELECT * FROM erp_uploads ORDER BY id"):
        rows = json.loads(r["rows"])
        out.append({"id": r["id"], "kind": r["kind"], "kind_label": KIND_LABELS[r["kind"]], "filename": r["filename"],
                    "headers": json.loads(r["headers"]), "mapping": json.loads(r["mapping"]), "count": len(rows),
                    "preview": rows[:preview], "fields": [{"key": k, "label": l} for k, l, _ in FIELDS[r["kind"]]]})
    return out


def set_upload(con, uid: int, kind: str | None, mapping: dict | None):
    r = con.execute("SELECT * FROM erp_uploads WHERE id=?", (uid,)).fetchone()
    if not r:
        raise ImportError_("Файл не найден")
    if kind and kind in FIELDS and kind != r["kind"]:
        con.execute("UPDATE erp_uploads SET kind=?, mapping=? WHERE id=?", (kind, json.dumps(guess_mapping(kind, json.loads(r["headers"]))), uid))
    if mapping is not None:
        clean = {k: int(v) for k, v in mapping.items() if v not in (None, "", -1, "-1")}
        con.execute("UPDATE erp_uploads SET mapping=? WHERE id=?", (json.dumps(clean), uid))


def delete_upload(con, uid: int):
    con.execute("DELETE FROM erp_uploads WHERE id=?", (uid,))


def _records(con, kind: str) -> list[dict]:
    out = []
    for r in con.execute("SELECT * FROM erp_uploads WHERE kind=? ORDER BY id", (kind,)):
        mp = json.loads(r["mapping"])
        for i, row in enumerate(json.loads(r["rows"])):
            rec = {k: (row[c] if c < len(row) else "") for k, c in mp.items()}
            rec["_where"] = f"{r['filename']}, строка {i + 2}"
            out.append(rec)
    return out


# ---------------- группы ----------------

def _group_key(branch: str, group: str) -> str:
    return " · ".join(x for x in ((branch or "").strip(), (group or "").strip()) if x)


def erp_groups(con) -> list[dict]:
    names: dict[str, int] = {}
    for kind in ("clients", "subs", "visits"):
        for rec in _records(con, kind):
            k = _group_key(rec.get("branch", "") if kind == "clients" else "", rec.get("group", ""))
            if rec.get("group"):
                names[k] = names.get(k, 0) + 1
    ours = [dict(r) for r in con.execute("""SELECT g.id, g.name, b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id
                                            WHERE g.active=1 ORDER BY b.name, g.name""")]
    saved = {r[0]: r[1] for r in con.execute("SELECT erp_name, group_id FROM erp_group_map")}
    out = []
    for n, cnt in sorted(names.items()):
        if n in saved:
            gid = saved[n]
        else:
            gid = _guess_group(n, ours)
        out.append({"erp": n, "count": cnt, "group_id": gid, "saved": n in saved})
    return out


def _guess_group(name: str, ours: list[dict]) -> int | None:
    nn = _norm(name)
    for g in ours:
        if _norm(g["name"]) == nn or _norm(f"{g['branch']} · {g['name']}") == nn:
            return g["id"]
    tm = re.search(r"\d{1,2}[:.]\d{2}", nn)
    days = set(re.findall(r"пн|вт|ср|чт|пт|сб|вс", nn))
    cands = []
    for g in ours:
        gn = _norm(f"{g['branch']} {g['name']}")
        score = 0
        if tm and tm.group(0).replace(".", ":") in gn:
            score += 2
        if days and days <= set(re.findall(r"пн|вт|ср|чт|пт|сб|вс", gn)):
            score += 2
        if any(w in gn for w in re.findall(r"[а-я]{4,}", nn)):
            score += 1
        if score >= 4:
            cands.append((score, g["id"]))
    cands.sort(reverse=True)
    return cands[0][1] if cands and (len(cands) == 1 or cands[0][0] > cands[1][0]) else None


def save_groups(con, mapping: dict):
    for k, v in mapping.items():
        con.execute("INSERT INTO erp_group_map(erp_name, group_id) VALUES (?, ?) ON CONFLICT(erp_name) DO UPDATE SET group_id=excluded.group_id",
                    (k, int(v) if v not in (None, "", "0", 0) else None))


def _group_id(con, branch: str, group: str) -> int | None:
    k = _group_key(branch, group)
    r = con.execute("SELECT group_id FROM erp_group_map WHERE erp_name=?", (k,)).fetchone()
    if r:
        return r[0]
    if group and branch:  # в абонементах и посещениях филиала обычно нет
        r = con.execute("SELECT group_id FROM erp_group_map WHERE erp_name LIKE ?", (f"% · {group.strip()}",)).fetchone()
        if r:
            return r[0]
    r = con.execute("SELECT group_id FROM erp_group_map WHERE erp_name=? OR erp_name LIKE ?", (group.strip(), f"% · {group.strip()}")).fetchone()
    return r[0] if r else None


# ---------------- план переноса ----------------

def _split_name(rec: dict) -> str:
    n = " ".join((rec.get("child_name") or "").split())
    last = " ".join((rec.get("child_last") or "").split())
    if last and last.lower() not in n.lower():
        n = f"{n} {last}".strip()
    return n


def _key(rec: dict) -> tuple:
    if rec.get("erp_id"):
        return ("id", rec["erp_id"].strip())
    return ("np", _norm(_split_name(rec)), normalize_phone(rec.get("phone", ""))[-10:])


def plan(con) -> dict:
    """Пробный прогон: что будет перенесено и какие нестыковки."""
    problems: list[str] = []
    clients = _records(con, "clients")
    by_key, children = {}, []
    for rec in clients:
        name = _split_name(rec)
        phone = normalize_phone(rec.get("phone", ""))
        if not name:
            problems.append(f"{rec['_where']}: нет имени ребёнка, строка пропущена")
            continue
        if len(phone) < 11:
            problems.append(f"{rec['_where']}: {name}: нет телефона родителя или он неполный")
        if rec.get("birth") and not parse_date(rec["birth"]):
            problems.append(f"{rec['_where']}: {name}: не понял дату рождения «{rec['birth']}»")
        k = _key(rec)
        if k in by_key:
            problems.append(f"{rec['_where']}: {name} повторяется, беру первую строку")
            continue
        by_key[k] = rec
        children.append(rec)
    # семьи: по телефону родителя
    fams = {}
    for rec in children:
        p = normalize_phone(rec.get("phone", ""))[-10:] or ("solo", _key(rec))
        fams.setdefault(p, []).append(rec)
    stats = {"families": len(fams), "children": len(children),
             "members_active": sum(1 for r in children if client_stage(r.get("status", ""))[1] == "active"),
             "members_left": sum(1 for r in children if client_stage(r.get("status", ""))[1] == "left"),
             "leads": sum(1 for r in children if client_stage(r.get("status", ""))[0] == "sales")}

    def find(rec):
        k = _key(rec)
        if k in by_key:
            return by_key[k]
        if k[0] == "id":
            return None
        nm = _norm(_split_name(rec))
        cands = [c for c in children if _norm(_split_name(c)) == nm]
        return cands[0] if len(cands) == 1 else None

    subs = _records(con, "subs")
    pays = _records(con, "payments")
    visits = _records(con, "visits")
    tasks = _records(con, "tasks")
    for label, rows, need in (("Абонементы", subs, ("month",)), ("Оплаты", pays, ("date", "amount")), ("Посещения", visits, ("date",)),
                              ("Задачи", tasks, ())):
        lost = 0
        for rec in rows:
            if not find(rec):
                lost += 1
                if lost <= 15:
                    problems.append(f"{rec['_where']}: {label.lower()}: не нашёл ребёнка «{_split_name(rec) or rec.get('erp_id', '')}» среди клиентов")
            for f in need:
                if f == "month" and not parse_month(rec.get("month", "")):
                    problems.append(f"{rec['_where']}: не понял месяц «{rec.get('month', '')}»")
                if f == "date" and not parse_date(rec.get("date", "")):
                    problems.append(f"{rec['_where']}: не понял дату «{rec.get('date', '')}»")
                if f == "amount" and parse_money(rec.get("amount", "")) is None:
                    problems.append(f"{rec['_where']}: не понял сумму «{rec.get('amount', '')}»")
        if lost > 15:
            problems.append(f"{label}: ещё {lost - 15} строк без найденного ребёнка")
    stats.update({"subs": len(subs), "payments": len(pays), "payments_sum": sum(parse_money(p.get("amount", "")) or 0 for p in pays),
                  "visits": len(visits), "tasks": len(tasks)})
    groups = erp_groups(con)
    unmapped = [g["erp"] for g in groups if not g["group_id"]]
    if unmapped:
        problems.append("Группы без пары в CRM (дети встанут без группы): " + ", ".join(unmapped[:20]))
    by_month: dict[str, int] = {}
    for p in pays:
        d = parse_date(p.get("date", ""))
        if d:
            by_month[d[:7]] = by_month.get(d[:7], 0) + (parse_money(p.get("amount", "")) or 0)
    current = con.execute("SELECT COUNT(*) FROM students").fetchone()[0]
    return {"stats": stats, "problems": problems[:300], "problems_total": len(problems), "groups": groups,
            "pay_by_month": dict(sorted(by_month.items())), "will_remove": current,
            "uploads": [{"filename": u["filename"], "kind_label": u["kind_label"], "count": u["count"]} for u in uploads(con, 0)]}


# ---------------- перенос ----------------

def _track(con, batch: int, tbl: str, rid: int):
    con.execute("INSERT INTO erp_rows(batch_id, tbl, row_id) VALUES (?,?,?)", (batch, tbl, rid))


def wipe_clients(con) -> int:
    """Удаляет всех клиентов, которые были в CRM до переноса (по правилу Юлии до переноса там только выдуманные).
    Сотрудники, группы, настройки и переписки остаются (переписки просто отвязываются от семей)."""
    ids = [r[0] for r in con.execute("SELECT id FROM students")]
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    for t in tables:
        cols = {r[1] for r in con.execute(f'PRAGMA table_info("{t}")')}
        if "student_id" in cols and t not in ("students",) and not t.startswith("erp_"):
            con.execute(f'DELETE FROM "{t}" WHERE student_id IS NOT NULL')
    con.execute("DELETE FROM students")
    for t in tables:
        cols = {r[1] for r in con.execute(f'PRAGMA table_info("{t}")')}
        if "family_id" in cols and t not in ("families", "students"):
            if t in ("conversations", "calls"):
                con.execute(f'UPDATE "{t}" SET family_id=NULL')
            else:
                con.execute(f'DELETE FROM "{t}" WHERE family_id IS NOT NULL')
    con.execute("DELETE FROM families")
    try:
        con.execute("DELETE FROM lesson_marks")
    except Exception:  # noqa: BLE001
        pass
    return len(ids)


def run(con, user: dict, rkn_confirmed: bool, backup_path: str = "") -> dict:
    if not rkn_confirmed:
        raise ImportError_("Отметьте, что изменения в Роскомнадзор поданы и политика опубликована")
    if con.execute("SELECT 1 FROM erp_batches WHERE undone_at IS NULL").fetchone():
        raise ImportError_("Перенос уже сделан. Чтобы перенести заново, сначала отмените прошлый перенос")
    p = plan(con)
    if not p["stats"]["children"]:
        raise ImportError_("Нет клиентов для переноса: загрузите файл с клиентами")
    now = datetime.now()
    nowi = now.isoformat(timespec="minutes")
    removed = wipe_clients(con)
    batch = con.execute("INSERT INTO erp_batches(started_at, user_name, backup) VALUES (?,?,?)",
                        (nowi, user.get("name", ""), backup_path)).lastrowid
    olga = con.execute("SELECT id FROM users WHERE active=1 AND role='operator' AND name LIKE 'Ольга%' ORDER BY id LIMIT 1").fetchone()
    from . import sources as _src
    clients = _records(con, "clients")
    seen, fam_by_phone, sid_by_key, name_index = set(), {}, {}, {}
    st = {"families": 0, "children": 0, "subs": 0, "payments": 0, "visits": 0, "tasks": 0, "comments": 0, "missed_trial": 0}
    month_now = logic.month_of(now.date())
    for rec in clients:
        name = _split_name(rec)
        if not name:
            continue
        k = _key(rec)
        if k in seen:
            continue
        seen.add(k)
        phone_raw = rec.get("phone", "").strip()
        ph = normalize_phone(phone_raw)
        fk = ph[-10:] if len(ph) >= 11 else None
        fid = fam_by_phone.get(fk) if fk else None
        if not fid:
            parent = " ".join((rec.get("parent_name") or "").split())
            fid = con.execute("INSERT INTO families(name) VALUES (?)", (parent or name.split()[-1],)).lastrowid
            _track(con, batch, "families", fid)
            st["families"] += 1
            if fk:
                fam_by_phone[fk] = fid
            pid = con.execute("INSERT INTO parents(family_id, name, phone, phone_norm, email) VALUES (?,?,?,?,?)",
                              (fid, parent or "Родитель", phone_raw, ph, (rec.get("email") or "").strip())).lastrowid
            _track(con, batch, "parents", pid)
            ph2 = rec.get("phone2", "").strip()
            if ph2 and normalize_phone(ph2) != ph:
                pid2 = con.execute("INSERT INTO parents(family_id, name, phone, phone_norm) VALUES (?,?,?,?)",
                                   (fid, "Второй контакт", ph2, normalize_phone(ph2))).lastrowid
                _track(con, batch, "parents", pid2)
        funnel, stage = client_stage(rec.get("status", ""))
        trial = parse_date(rec.get("trial_at", ""))
        if trial and len(trial) == 10:
            trial += "T18:00"
        # «зависшие» с сентября-октября: записаны на пробное, дата прошла, не купили → «Не дошёл до ПУ» (решение Юлии)
        if funnel == "sales" and stage in ("trial_booked",) and trial and trial[:10] < now.date().isoformat():
            stage = "missed_trial"
            st["missed_trial"] += 1
        gid = _group_id(con, rec.get("branch", ""), rec.get("group", ""))
        created = parse_date(rec.get("created", "")) or nowi
        if len(created) == 10:
            created += "T10:00"
        tariff = "flex" if "гибк" in _norm(rec.get("tariff", "")) else "fixed"
        src = (rec.get("source") or "").strip()
        sid = con.execute("""INSERT INTO students(family_id, name, birth, group_id, tariff, funnel, stage, stage_changed_at, trial_at,
                             source, created_at, erp_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (fid, name, parse_date(rec.get("birth", "")) or "", gid, tariff, funnel, stage, nowi, trial,
                           src, created, (rec.get("erp_id") or "").strip())).lastrowid
        _track(con, batch, "students", sid)
        if src:
            try:
                sid_src = _src.find_or_create(con, src) if hasattr(_src, "find_or_create") else None
                if sid_src:
                    con.execute("UPDATE students SET source_id=? WHERE id=?", (sid_src, sid))
            except Exception:  # noqa: BLE001
                pass
        if funnel == "members" and gid:
            eid = con.execute("INSERT INTO enrollments(student_id, group_id, tariff, active, created_at) VALUES (?,?,?,?,?)",
                              (sid, gid, tariff, 0 if stage == "left" else 1, created)).lastrowid
            _track(con, batch, "enrollments", eid)
        sid_by_key[k] = sid
        name_index.setdefault(_norm(name), []).append(sid)
        st["children"] += 1
        hid = con.execute("INSERT INTO history(student_id, ts, user_name, kind, text) VALUES (?,?,?,?,?)",
                          (sid, nowi, "Перенос из ERP", "system", f"Перенесено из ERP ({rec['_where']}), статус в ERP: «{rec.get('status', '')}»")).lastrowid
        _track(con, batch, "history", hid)
        if (rec.get("comment") or "").strip():
            hid = con.execute("INSERT INTO history(student_id, ts, user_name, kind, text) VALUES (?,?,?,?,?)",
                              (sid, created, "ERP", "note", rec["comment"].strip())).lastrowid
            _track(con, batch, "history", hid)
            st["comments"] += 1

    def find_sid(rec):
        k = _key(rec)
        if k in sid_by_key:
            return sid_by_key[k]
        if k[0] == "id":
            return None
        lst = name_index.get(_norm(_split_name(rec)), [])
        return lst[0] if len(lst) == 1 else None

    def enrollment(sid, group_name):
        gid = _group_id(con, "", group_name) if group_name else None
        if not gid:
            r = con.execute("SELECT id FROM enrollments WHERE student_id=? ORDER BY active DESC, id LIMIT 1", (sid,)).fetchone()
            if r:
                return r[0]
            gid = con.execute("SELECT group_id FROM students WHERE id=?", (sid,)).fetchone()[0]
            if not gid:
                return None
        r = con.execute("SELECT id FROM enrollments WHERE student_id=? AND group_id=?", (sid, gid)).fetchone()
        if r:
            return r[0]
        s = con.execute("SELECT tariff, funnel, stage FROM students WHERE id=?", (sid,)).fetchone()
        eid = con.execute("INSERT INTO enrollments(student_id, group_id, tariff, active, created_at) VALUES (?,?,?,0,?)",
                          (sid, gid, s["tariff"], nowi)).lastrowid
        _track(con, batch, "enrollments", eid)
        return eid

    sub_index = {}
    for rec in _records(con, "subs"):
        sid = find_sid(rec)
        ym = parse_month(rec.get("month", ""))
        if not sid or not ym:
            continue
        eid = enrollment(sid, rec.get("group", ""))
        price = parse_money(rec.get("price", "")) or 0
        lessons = parse_money(rec.get("lessons", "")) or 0
        sraw = _norm(rec.get("status", ""))
        status = "cancelled" if "отмен" in sraw else "paid" if ("оплач" in sraw and "не" not in sraw) or sraw in ("да", "1", "+") else "invoiced"
        tariff = "flex" if "гибк" in _norm(rec.get("tariff", "")) else (con.execute("SELECT tariff FROM students WHERE id=?", (sid,)).fetchone()[0])
        sub = con.execute("""INSERT INTO subscriptions(student_id, enrollment_id, month, tariff, lessons, per_lesson, price, status, created_at, paid_at)
                             VALUES (?,?,?,?,?,?,?,?,?,?)""",
                          (sid, eid, ym, tariff, lessons, (price // lessons) if lessons else price, price, status,
                           f"{ym}-01T10:00", f"{ym}-01T10:00" if status == "paid" else None)).lastrowid
        _track(con, batch, "subscriptions", sub)
        sub_index.setdefault((sid, ym), sub)
        st["subs"] += 1
    for rec in _records(con, "payments"):
        sid = find_sid(rec)
        d = parse_date(rec.get("date", ""))
        amt = parse_money(rec.get("amount", ""))
        if not sid or not d or not amt:
            continue
        ym = parse_month(rec.get("month", "")) if rec.get("month") else None
        pid = con.execute("""INSERT INTO payments(student_id, amount, method, paid_at, comment, user_name, confirmed, confirmed_by,
                             confirmed_at, subscription_id, created_at) VALUES (?,?,?,?,?,?,1,'Перенос из ERP',?,?,?)""",
                          (sid, amt, pay_method(rec.get("method", "")), d if len(d) > 10 else d + "T12:00",
                           ((rec.get("comment") or "").strip() + " (из ERP)").strip(), "ERP", nowi,
                           sub_index.get((sid, ym)) if ym else None, nowi)).lastrowid
        _track(con, batch, "payments", pid)
        st["payments"] += 1
    for rec in _records(con, "visits"):
        sid = find_sid(rec)
        d = parse_date(rec.get("date", ""))
        vs = visit_status(rec.get("status", ""))
        if not sid or not d or not vs:
            continue
        gid = _group_id(con, "", rec.get("group", "")) or con.execute("SELECT group_id FROM students WHERE id=?", (sid,)).fetchone()[0]
        if not gid:
            continue
        cur = con.execute("INSERT OR IGNORE INTO attendance(student_id, group_id, date, status) VALUES (?,?,?,?)", (sid, gid, d[:10], vs))
        if cur.rowcount:
            _track(con, batch, "attendance", cur.lastrowid)
            st["visits"] += 1
    from . import tasks as _tasks
    for rec in _records(con, "tasks"):
        sid = find_sid(rec)
        if not sid or not (rec.get("task") or "").strip():
            continue
        due = parse_date(rec.get("due", "")) or nowi
        if len(due) == 10:
            due += "T10:00"
        tid = _tasks.import_erp_task(con, rec["task"], sid, due, (rec.get("comment") or "").strip())
        if tid:
            if olga:  # задачи по пробным на Ольгу (решение Юлии)
                con.execute("UPDATE tasks SET assignee_id=? WHERE id=?", (olga[0], tid))
            _track(con, batch, "tasks", tid)
            st["tasks"] += 1
    # текущий месяц: у учеников с активной группой без абонемента ничего не придумываем, это покажет сверка
    st["removed_before"] = removed
    con.execute("UPDATE erp_batches SET stats=? WHERE id=?", (json.dumps(st, ensure_ascii=False), batch))
    con.execute("INSERT INTO audit(ts, user_id, action, detail) VALUES (?,?,?,?)", (nowi, user.get("id"), "erp.import", json.dumps(st)))
    return {"batch": batch, "stats": st, "month": month_now}


def undo(con, user: dict) -> dict:
    b = con.execute("SELECT id FROM erp_batches WHERE undone_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()
    if not b:
        raise ImportError_("Отменять нечего")
    n = 0
    for tbl in ("tasks", "attendance", "payments", "subscriptions", "history", "enrollments", "students", "parents", "families"):
        ids = [r[0] for r in con.execute("SELECT row_id FROM erp_rows WHERE batch_id=? AND tbl=?", (b[0], tbl))]
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            if tbl == "students":  # всё, что успели завести по этим детям уже в CRM
                q = ",".join("?" * len(part))
                for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                    cols = {r[1] for r in con.execute(f'PRAGMA table_info("{t}")')}
                    if "student_id" in cols and t != "students" and not t.startswith("erp_"):
                        con.execute(f'DELETE FROM "{t}" WHERE student_id IN ({q})', part)
            con.execute(f'DELETE FROM "{tbl}" WHERE id IN ({",".join("?" * len(part))})', part)
            n += len(part)
    con.execute("UPDATE conversations SET family_id=NULL WHERE family_id IS NOT NULL AND family_id NOT IN (SELECT id FROM families)")
    con.execute("UPDATE erp_batches SET undone_at=? WHERE id=?", (datetime.now().isoformat(timespec="minutes"), b[0]))
    con.execute("INSERT INTO audit(ts, user_id, action, detail) VALUES (?,?,?,?)",
                (datetime.now().isoformat(timespec="minutes"), user.get("id"), "erp.undo", str(b[0])))
    return {"removed": n}


def verify(con) -> dict:
    """Сверка после переноса: итоги файлов против CRM."""
    b = con.execute("SELECT * FROM erp_batches WHERE undone_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()
    if not b:
        return {"done": False}
    p = plan(con)
    rows = []

    def line(label, erp, crm):
        rows.append({"label": label, "erp": erp, "crm": crm, "ok": erp == crm})
    st = json.loads(b["stats"])
    bid = b["id"]
    cnt = lambda tbl: con.execute("SELECT COUNT(*) FROM erp_rows r JOIN " + tbl + " t ON t.id=r.row_id WHERE r.batch_id=? AND r.tbl=?",
                                  (bid, tbl)).fetchone()[0]
    line("Дети и лиды", p["stats"]["children"], cnt("students"))
    line("Занимаются сейчас", p["stats"]["members_active"],
         con.execute("SELECT COUNT(*) FROM students WHERE funnel='members' AND stage='active'").fetchone()[0])
    line("Абонементы", p["stats"]["subs"], cnt("subscriptions"))
    line("Оплаты, штук", p["stats"]["payments"], cnt("payments"))
    crm_sum = con.execute("""SELECT COALESCE(SUM(t.amount),0) FROM erp_rows r JOIN payments t ON t.id=r.row_id
                             WHERE r.batch_id=? AND r.tbl='payments'""", (bid,)).fetchone()[0]
    line("Оплаты, сумма ₽", p["stats"]["payments_sum"], crm_sum)
    by_month = []
    for ym, s in p["pay_by_month"].items():
        c = con.execute("""SELECT COALESCE(SUM(t.amount),0) FROM erp_rows r JOIN payments t ON t.id=r.row_id
                           WHERE r.batch_id=? AND r.tbl='payments' AND substr(t.paid_at,1,7)=?""", (bid, ym)).fetchone()[0]
        by_month.append({"month": ym, "erp": s, "crm": c, "ok": s == c})
    groups = [dict(r) for r in con.execute("""SELECT g.name, b.name AS branch, COUNT(DISTINCT e.student_id) AS kids FROM enrollments e
                                              JOIN groups g ON g.id=e.group_id JOIN branches b ON b.id=g.branch_id
                                              WHERE e.active=1 GROUP BY g.id ORDER BY b.name, g.name""")]
    debts = con.execute("SELECT COUNT(*) FROM students WHERE funnel='members' AND stage='active'").fetchone()[0]
    return {"done": True, "batch": dict(b) | {"stats": st}, "rows": rows, "by_month": by_month, "groups": groups, "members": debts}
