"""Чеки 54-ФЗ: сверка всех поступлений на расчётные счета с чеками.

Каждые 10 минут модуль сам забирает данные:
  - выписку Точки (входящие платежи: СБП по ссылкам админов, возмещения эквайринга, переводы);
  - платежи и чеки PayKeeper (ссылки на оплату из ERP);
  - чеки из Платформы ОФД (касса Эвотор на филиале и облачная касса PayKeeper).

Дальше сопоставляет и решает:
  - СБП Точки: на каждый платёж должен быть чек на кассе Эвотор с той же суммой. Нет чека 2 часа -
    напоминание контролёру в «Службу заботы». Нет до 12:00 следующего дня - тревога владельцу;
  - PayKeeper: по каждому успешному платежу должен быть успешный чек. Если касса PayKeeper не пробила
    (failed / timeout), модуль сам отправляет чек повторно с тем же составом. Не помогло - тревога;
  - возмещения эквайринга, переводы между своими счетами и деньги от организаций и ИП - чек не нужен.
    Непонятное поступление - один вопрос владельцу: нужен ли чек.

Персональные данные не храним: только сумма, дата, номер операции банка и вид платежа.
Сбер (выписка и реестр эквайринга) - следующий этап: нужен Sber API.
"""
from __future__ import annotations

import base64
import json
import re
import threading
import time
import traceback
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from . import comms

SCHEMA = """
CREATE TABLE IF NOT EXISTS fx_payments (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,                  -- tochka | paykeeper
    ext_id TEXT NOT NULL,                  -- номер операции в банке / id платежа PayKeeper
    pay_date TEXT NOT NULL,                -- дата платежа по банку
    seen_at TEXT NOT NULL,                 -- когда модуль впервые увидел платёж (для СБП ≈ время оплаты)
    amount INTEGER NOT NULL,               -- копейки
    kind TEXT NOT NULL,                    -- sbp | person | acquiring | own | company | paykeeper | unknown
    need INTEGER NOT NULL,                 -- 1 - нужен чек, 0 - не нужен, -1 - непонятно, спросить
    status TEXT NOT NULL DEFAULT 'wait',   -- wait | ok | missing | skip
    receipt_id INTEGER REFERENCES fx_receipts(id),
    level INTEGER NOT NULL DEFAULT 0,      -- 0 тихо, 1 написали контролёру, 2 написали владельцу
    retries INTEGER NOT NULL DEFAULT 0,    -- повторные чеки PayKeeper
    note TEXT NOT NULL DEFAULT '',
    resolved_by TEXT NOT NULL DEFAULT '',
    resolved_at TEXT,
    UNIQUE(source, ext_id)
);
CREATE INDEX IF NOT EXISTS idx_fx_pay_status ON fx_payments(status);

CREATE TABLE IF NOT EXISTS fx_receipts (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,                  -- ofd | paykeeper
    ext_id TEXT NOT NULL,                  -- ФН + номер ФД / id чека PayKeeper
    kkt TEXT NOT NULL DEFAULT '',          -- регистрационный номер кассы или номер ФН
    ts TEXT NOT NULL,
    amount INTEGER NOT NULL,               -- копейки, весь чек
    ecash INTEGER NOT NULL DEFAULT 0,      -- копейки, безналичными
    op INTEGER NOT NULL DEFAULT 1,         -- 1 приход, 2 возврат прихода
    pk_payment TEXT NOT NULL DEFAULT '',   -- для чеков PayKeeper: id платежа
    pk_status TEXT NOT NULL DEFAULT '',
    UNIQUE(source, ext_id)
);

CREATE TABLE IF NOT EXISTS fx_runs (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    source TEXT NOT NULL,
    ok INTEGER NOT NULL,
    msg TEXT NOT NULL DEFAULT ''
);
"""

# ключи в integrations; секреты наружу не отдаём
SECRET_KEYS = ("fx_tochka_token", "fx_pk_password", "fx_ofd_token")
PLAIN_KEYS = ("fx_enabled", "fx_pk_server", "fx_pk_user", "fx_inn", "fx_controller", "fx_owner",
              "fx_auto_retry", "fx_start", "fx_remind_hours")
DEFAULTS = {"fx_enabled": "0", "fx_auto_retry": "1", "fx_remind_hours": "2"}

TOCHKA_API = "https://enter.tochka.com/uapi"
OFD_API = "https://ofv-api-v0-1-1.evotor.ru/v1/client"
DAYS_BACK = 3                # окно сверки
PK_WAIT_MIN = 30             # сколько ждём чек PayKeeper, пока он «в пути»
PK_MAX_RETRIES = 2
SOURCE_DOWN_HOURS = 3        # источник не отвечает столько часов - тревога владельцу

KIND_LABELS = {"sbp": "СБП", "person": "Перевод от физлица", "acquiring": "Возмещение эквайринга",
               "own": "Перевод между своими счетами", "company": "От организации или ИП",
               "paykeeper": "PayKeeper", "unknown": "Непонятное поступление"}
STATUS_LABELS = {"wait": "Ждём чек", "ok": "Есть чек", "missing": "Нет чека", "skip": "Чек не нужен"}


class FxError(ValueError):
    pass


# ---------- сертификат Минцифры ----------
# Точка (и часть российских сервисов) работает на сертификате «Russian Trusted Root CA», которого нет в стандартном
# списке Python. Скачиваем его с сайта Госуслуг и принимаем, только если отпечаток совпал с опубликованным:
# SHA-256 D26D2D02…CA8ECF31 (так же он указан у Сбера на developers.sber.ru). Подменить его по дороге нельзя.
RU_CA_URL = "https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt"
RU_CA_SHA256 = "d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31"
_CTX = None
_CA_TRIED = 0.0


def _ca_path() -> str:
    import os
    from . import db
    d = os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), "ca")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "russian_trusted_root_ca.pem")


def _pem_ok(pem: str) -> bool:
    import hashlib
    import ssl
    try:
        return hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem.strip())).hexdigest() == RU_CA_SHA256
    except ValueError:
        return False


def ssl_ctx():
    """Стандартные сертификаты + корневой сертификат Минцифры (проверенный по отпечатку)."""
    global _CTX, _CA_TRIED
    if _CTX is not None:
        return _CTX
    import os
    import ssl
    from urllib.request import urlopen
    ctx = ssl.create_default_context()
    path = _ca_path()
    pem = open(path).read() if os.path.exists(path) else ""
    if not _pem_ok(pem) and time.time() - _CA_TRIED > 600:
        _CA_TRIED = time.time()
        try:
            raw = urlopen(RU_CA_URL, timeout=20, context=ssl._create_unverified_context()).read()  # noqa: S323 - проверяем отпечатком
            txt = raw.decode("ascii", "ignore")
            m = re.search(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", txt, re.S)
            pem = m.group(0) if m else ssl.DER_cert_to_PEM_cert(raw)
        except Exception:  # noqa: BLE001
            pem = ""
        if _pem_ok(pem):
            with open(path, "w") as f:
                f.write(pem.strip() + "\n")
    if _pem_ok(pem):
        ctx.load_verify_locations(cadata=pem)
        _CTX = ctx
    return ctx


def init(con):
    con.executescript(SCHEMA)


def _now() -> datetime:
    return datetime.now()


def _iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M:%S")


def cfg(con, key: str) -> str:
    return comms.get(con, key) or DEFAULTS.get(key, "")


def kop(v) -> int:
    """Рубли (строка или число) → копейки."""
    if v is None or v == "":
        return 0
    return int(round(float(str(v).replace(" ", "").replace(",", ".")) * 100))


def rub(k: int) -> str:
    r = k / 100
    s = f"{r:,.2f}".replace(",", " ").replace(".00", "")
    return s.replace(".", ",") + " ₽"


# ======================= источники =======================

def _log_run(con, source: str, ok: bool, msg: str = "", now: datetime | None = None):
    con.execute("INSERT INTO fx_runs(ts, source, ok, msg) VALUES (?,?,?,?)", (_iso(now or _now()), source, int(ok), msg[:300]))
    con.execute("DELETE FROM fx_runs WHERE id NOT IN (SELECT id FROM fx_runs ORDER BY id DESC LIMIT 500)")


# ---------- Точка ----------

def tochka_get(con, path: str, method: str = "GET", data=None):
    tok = cfg(con, "fx_tochka_token")
    code, js = comms.http(method, TOCHKA_API + path, data=data, headers={"Authorization": f"Bearer {tok}"}, timeout=30, context=ssl_ctx())
    if code >= 400:
        msg = json.dumps(js, ensure_ascii=False)[:200] if js else ""
        raise FxError(f"Точка ответила {code}{': ' + msg if msg else ''}")
    return js


def fetch_tochka(con, d_from: date, d_to: date) -> list[dict]:
    """Входящие операции по всем рублёвым счетам Точки за период."""
    accs = (tochka_get(con, "/open-banking/v1.0/accounts").get("Data") or {}).get("Account") or []
    out = []
    for a in accs:
        acc = a.get("accountId")
        if not acc or (a.get("currency") not in (None, "RUB", "643")):
            continue
        init_js = tochka_get(con, "/open-banking/v1.0/statements", "POST", {"Data": {"Statement": {
            "accountId": acc, "startDateTime": d_from.isoformat(), "endDateTime": d_to.isoformat()}}})
        sid = ((init_js.get("Data") or {}).get("Statement") or {}).get("statementId")
        st = None
        for _ in range(15):
            js = tochka_get(con, f"/open-banking/v1.0/accounts/{acc}/statements/{sid}")
            st = ((js.get("Data") or {}).get("Statement") or [None])[0]
            if st and st.get("status") == "Ready":
                break
            if st and st.get("status") == "Error":
                raise FxError("Точка не смогла сформировать выписку")
            time.sleep(2)
        else:
            raise FxError("Точка долго формирует выписку, попробуем в следующий раз")
        for t in st.get("Transaction") or []:
            if t.get("creditDebitIndicator") != "Credit" or t.get("status") != "Booked":
                continue
            out.append(t)
    return out


SBP_RE = re.compile(r"сбп|сист\w* быстрых платеж|по qr|qr-?код|\bc2b\b", re.I)
ACQ_RE = re.compile(r"эквайр|возмещен|paykeeper|пейкипер|по операциям с (использованием )?(банковских )?карт|торгов\w* уступ", re.I)


def classify_tochka(t: dict, my_inn: str) -> tuple[str, int]:
    """Вид поступления и нужен ли чек: 1 да, 0 нет, -1 спросить."""
    desc = t.get("description") or ""
    payer = t.get("DebtorParty") or {}
    inn = (payer.get("inn") or "").strip()
    acc = ((t.get("DebtorAccount") or {}).get("identification") or "").strip()
    if my_inn and inn == my_inn:
        return "own", 0
    if ACQ_RE.search(desc):
        return "acquiring", 0
    if SBP_RE.search(desc):
        return "sbp", 1
    if acc.startswith(("40817", "40820", "423", "426")):  # счета физлиц
        return "person", 1
    if len(inn) == 10 or acc.startswith(("40702", "40701", "40703", "40802", "40807", "40502", "40602", "40603")):
        return "company", 0  # безнал между организациями и ИП без карты - чек не нужен
    return "unknown", -1


# ---------- PayKeeper ----------

def pk_call(con, path: str, params=None, form=None):
    server = cfg(con, "fx_pk_server").rstrip("/")
    if not server:
        raise FxError("Не указан адрес PayKeeper")
    auth = base64.b64encode(f"{cfg(con, 'fx_pk_user')}:{cfg(con, 'fx_pk_password')}".encode()).decode()
    url = server + path + ("?" + urlencode(params, doseq=True) if params else "")
    code, js = comms.http("POST" if form is not None else "GET", url, form=form,
                          headers={"Authorization": "Basic " + auth}, timeout=30, context=ssl_ctx())
    if code == 401:
        raise FxError("PayKeeper: неверный логин или пароль")
    if code >= 400:
        raise FxError(f"PayKeeper ответил {code}")
    if isinstance(js, dict) and js.get("result") == "fail":
        raise FxError(f"PayKeeper: {js.get('msg') or 'ошибка'}")
    return js


def _pk_all(con, path: str, params: dict, key: str | None = None) -> list:
    out, frm = [], 0
    while True:
        js = pk_call(con, path, params | {"limit": 100, "from": frm})
        rows = (js.get(key) if key and isinstance(js, dict) else js) or []
        out += rows
        if len(rows) < 100:
            return out
        frm += 100


def fetch_pk_payments(con, d_from: date, d_to: date) -> list[dict]:
    return _pk_all(con, "/info/payments/bydate/", {
        "start": d_from.isoformat(), "end": d_to.isoformat(),
        "status[]": ["success", "obtained", "stuck"]})


def pk_diag(con, d_from: date, d_to: date) -> str:
    """Без персональных данных: только тип ответа, ключи и количество строк при разных фильтрах."""
    base = {"start": d_from.isoformat(), "end": d_to.isoformat(), "limit": 100, "from": 0}
    variants = {"без фильтров": {}, "статус success": {"status[]": ["success"]},
                "все системы": {"status[]": ["success", "obtained", "stuck"], "payment_system_id[]": list(range(1, 60))}}
    parts = []
    for name, extra in variants.items():
        try:
            js = pk_call(con, "/info/payments/bydate/", base | extra)
            if isinstance(js, list):
                parts.append(f"{name}: {len(js)} шт")
            elif isinstance(js, dict):
                parts.append(f"{name}: объект {sorted(js)[:6]}")
            else:
                parts.append(f"{name}: {type(js).__name__}")
        except Exception as e:  # noqa: BLE001
            parts.append(f"{name}: {e}")
    try:
        js = pk_call(con, "/info/payments/bydatecount/", {"start": base["start"], "end": base["end"]})
        parts.append("счётчик: " + json.dumps(js, ensure_ascii=False)[:200])
    except Exception as e:  # noqa: BLE001
        parts.append(f"счётчик: {e}")
    return "; ".join(parts)


def fetch_pk_receipts(con, d_from: date, d_to: date) -> list[dict]:
    return _pk_all(con, "/info/receipts/bydate/", {
        "start": d_from.isoformat(), "end": d_to.isoformat(),
        "receipt_type[]": ["sale"], "sum_type[]": ["sum_cashless", "sum_cash", "sum_advance", "sum_credit"]}, "receipts")


def pk_reprint(con, failed: dict, attempt: int) -> str:
    """Повторный чек по платежу PayKeeper: тот же состав, что в неудачном чеке, новый ключ."""
    tok = pk_call(con, "/info/settings/token/")
    tok = tok.get("token") if isinstance(tok, dict) else ""
    form = {"token": tok, "payment_id": str(failed["payment_id"]), "type": "sale",
            "is_post_sale": "true" if str(failed.get("is_post_sale")).lower() in ("1", "true") else "false",
            "contact": failed.get("contact") or "",
            "sum_cashless": failed.get("sum_cashless") or "0", "sum_cash": failed.get("sum_cash") or "0",
            "sum_advance": failed.get("sum_advance") or "0", "sum_credit": failed.get("sum_credit") or "0",
            "cart": failed["cart"] if isinstance(failed.get("cart"), str) else json.dumps(failed.get("cart") or [], ensure_ascii=False),
            "receipt_key": f"fx{failed['payment_id']}r{attempt}"}
    if failed.get("receipt_properties"):
        rp = failed["receipt_properties"]
        form["receipt_properties"] = rp if isinstance(rp, str) else json.dumps(rp, ensure_ascii=False)
    js = pk_call(con, "/change/receipt/print/", form=form)
    return str((js or {}).get("receipt_id", ""))


# ---------- Платформа ОФД ----------

def ofd_get(con, path: str, params=None):
    url = OFD_API + path + ("?" + urlencode(params) if params else "")
    code, js = comms.http("GET", url, headers={"Token": cfg(con, "fx_ofd_token"), "Accept": "application/json"}, timeout=30, context=ssl_ctx())
    if code in (401, 403):
        raise FxError("Платформа ОФД: токен не подошёл")
    if code >= 400:
        raise FxError(f"Платформа ОФД ответила {code}")
    return js or {}


def fetch_ofd_kkts(con) -> list[str]:
    js = ofd_get(con, "/kkts")
    lst = js.get("kktList") or js
    out = []
    for br in (lst.get("orgBranches") or []) if isinstance(lst, dict) else []:
        for k in br.get("kkts") or []:
            if k.get("kktRegId"):
                out.append(k["kktRegId"])
    return out


def fetch_ofd_receipts(con, d_from: date, d_to: date) -> list[dict]:
    out = []
    for rnm in fetch_ofd_kkts(con):
        js = ofd_get(con, "/receipts", {"dateFrom": f"{d_from} 00:00:00", "dateTo": f"{d_to} 23:59:59",
                                        "dateType": 1, "kktRegId": rnm})
        for r in js.get("receipts") or []:
            r.setdefault("kktRegId", rnm)
            out.append(r)
    return out


def _ofd_ts(r: dict) -> str:
    v = r.get("receiptDate") or r.get("dateTime")
    if isinstance(v, (int, float)):
        return _iso(datetime.fromtimestamp(v))
    s = str(v or "")
    m = re.match(r"(\d{4})[.-](\d{2})[.-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})", s)
    return f"{m[1]}-{m[2]}-{m[3]} {m[4]}:{m[5]}:{m[6]}" if m else _iso(_now())


# ======================= сохранение =======================

def store_tochka(con, txns: list[dict], now: datetime):
    my_inn = cfg(con, "fx_inn").strip()
    for t in txns:
        ext = t.get("transactionId") or t.get("paymentId") or t.get("documentNumber")
        if not ext:
            continue
        kind, need = classify_tochka(t, my_inn)
        con.execute("""INSERT OR IGNORE INTO fx_payments(source, ext_id, pay_date, seen_at, amount, kind, need, status)
                       VALUES ('tochka',?,?,?,?,?,?,?)""",
                    (str(ext), t.get("documentProcessDate") or now.date().isoformat(), _iso(now),
                     kop((t.get("Amount") or {}).get("amount")), kind, need, "wait" if need == 1 else ("skip" if need == 0 else "missing")))


def store_pk(con, pays: list[dict], recs: list[dict], now: datetime):
    for p in pays:
        if p.get("status") not in ("success", "obtained", "stuck"):
            continue
        ts = (p.get("success_datetime") or p.get("obtain_datetime") or _iso(now))[:19]
        con.execute("""INSERT OR IGNORE INTO fx_payments(source, ext_id, pay_date, seen_at, amount, kind, need, status)
                       VALUES ('paykeeper',?,?,?,?,'paykeeper',1,'wait')""",
                    (str(p["id"]), ts[:10], ts, kop(p.get("pay_amount"))))
    for r in recs:
        amt = sum(kop(r.get(k)) for k in ("sum_cashless", "sum_cash", "sum_advance", "sum_credit"))
        con.execute("""INSERT INTO fx_receipts(source, ext_id, kkt, ts, amount, ecash, op, pk_payment, pk_status)
                       VALUES ('paykeeper',?,?,?,?,?,1,?,?)
                       ON CONFLICT(source, ext_id) DO UPDATE SET pk_status=excluded.pk_status, kkt=excluded.kkt""",
                    (str(r["id"]), str(r.get("fn") or ""), (r.get("obtain_datetime") or r.get("request_datetime") or _iso(now))[:19],
                     amt, kop(r.get("sum_cashless")), str(r.get("payment_id") or ""), r.get("status") or ""))


def store_ofd(con, recs: list[dict]):
    for r in recs:
        fn = str(r.get("fiscalDriveNumber") or r.get("kktFN") or "")
        fd = str(r.get("fiscalDocumentNumber") or r.get("requestNumber") or "")
        if not fd:
            continue
        op = int(r.get("operationType") or 1)
        con.execute("""INSERT OR IGNORE INTO fx_receipts(source, ext_id, kkt, ts, amount, ecash, op)
                       VALUES ('ofd',?,?,?,?,?,?)""",
                    (f"{fn}:{fd}", fn or str(r.get("kktRegId") or ""), _ofd_ts(r),
                     int(r.get("totalSum") or 0), int(r.get("ecashTotalSum") or 0), op))


# ======================= сверка =======================

def _pk_fns(con) -> set[str]:
    """Номера ФН облачной кассы PayKeeper: её чеки не должны закрывать СБП Точки."""
    return {r[0] for r in con.execute("SELECT DISTINCT kkt FROM fx_receipts WHERE source='paykeeper' AND kkt<>''")}


def match_tochka(con):
    pk_fns = _pk_fns(con)
    for p in con.execute("SELECT * FROM fx_payments WHERE source='tochka' AND need=1 AND status IN ('wait','missing') ORDER BY seen_at").fetchall():
        d = date.fromisoformat(p["pay_date"])
        lo, hi = _iso(datetime.combine(d - timedelta(days=1), datetime.min.time())), _iso(datetime.combine(d + timedelta(days=2), datetime.min.time()))
        best = None
        for r in con.execute("""SELECT * FROM fx_receipts WHERE source='ofd' AND op=1 AND ecash=? AND ts>=? AND ts<?
                                AND id NOT IN (SELECT receipt_id FROM fx_payments WHERE receipt_id IS NOT NULL)""",
                             (p["amount"], lo, hi)):
            if r["kkt"] in pk_fns:
                continue
            gap = abs((datetime.fromisoformat(r["ts"]) - datetime.fromisoformat(p["seen_at"])).total_seconds())
            if best is None or gap < best[0]:
                best = (gap, r["id"])
        if best:
            con.execute("UPDATE fx_payments SET status='ok', receipt_id=? WHERE id=?", (best[1], p["id"]))


def match_pk(con, now: datetime, auto_retry: bool) -> list[str]:
    """Платежи PayKeeper: успешный чек есть - ок; касса не пробила - повторяем сами."""
    errors = []
    for p in con.execute("SELECT * FROM fx_payments WHERE source='paykeeper' AND status IN ('wait','missing')").fetchall():
        recs = [dict(r) for r in con.execute("SELECT * FROM fx_receipts WHERE source='paykeeper' AND pk_payment=? ORDER BY id", (p["ext_id"],))]
        good = [r for r in recs if r["pk_status"] == "success"]
        if good:
            con.execute("UPDATE fx_payments SET status='ok', receipt_id=? WHERE id=?", (good[-1]["id"], p["id"]))
            continue
        pending = [r for r in recs if r["pk_status"] in ("created", "sending", "request_sent")]
        age_min = (now - datetime.fromisoformat(p["seen_at"])).total_seconds() / 60
        if pending or age_min < PK_WAIT_MIN:
            continue
        failed = [r for r in recs if r["pk_status"] in ("failed", "timeout")]
        if auto_retry and failed and p["retries"] < PK_MAX_RETRIES:
            try:
                raw = pk_receipt_raw(con, failed[-1]["ext_id"])
                rid = pk_reprint(con, raw, p["retries"] + 1)
                con.execute("UPDATE fx_payments SET retries=retries+1, note=? WHERE id=?",
                            (f"Повторный чек отправлен в кассу PayKeeper (№{rid})", p["id"]))
                if rid:
                    con.execute("""INSERT OR IGNORE INTO fx_receipts(source, ext_id, ts, amount, ecash, pk_payment, pk_status)
                                   VALUES ('paykeeper',?,?,?,?,?,'created')""", (rid, _iso(now), p["amount"], p["amount"], p["ext_id"]))
                continue
            except Exception as e:  # noqa: BLE001
                errors.append(f"PayKeeper, повтор чека по платежу {p['ext_id']}: {e}")
        why = ("касса PayKeeper не пробила чек" + (" и после повторов" if p["retries"] else "")) if failed or p["retries"] else "PayKeeper не создал чек"
        con.execute("UPDATE fx_payments SET status='missing', note=? WHERE id=?", (why, p["id"]))
    return errors


def pk_receipt_raw(con, rid: str) -> dict:
    js = pk_call(con, "/info/receipts/byid/", {"id": rid})
    if isinstance(js, list):
        js = js[0] if js else {}
    if not js or not js.get("payment_id"):
        raise FxError("PayKeeper не отдал состав чека")
    return js


# ======================= уведомления =======================

def _tag(con, uid) -> str:
    if not uid or not str(uid).isdigit():
        return ""
    r = con.execute("SELECT name, tg_username FROM users WHERE id=?", (int(uid),)).fetchone()
    if not r:
        return ""
    return "@" + r["tg_username"].lstrip("@") if r["tg_username"] else r["name"]


def _owner(con):
    v = cfg(con, "fx_owner")
    if v.isdigit():
        return int(v)
    r = con.execute("SELECT id FROM users WHERE role='admin' AND active=1 ORDER BY id LIMIT 1").fetchone()
    return r[0] if r else None


def _say(con, text: str):
    from . import pu
    pu.notify_chat(con, "care", text)


def _alert(con, key: str, text: str):
    from . import pu
    pu.add_alert(con, "fiscal", key, text)


def describe(p) -> str:
    when = p["seen_at"][8:10] + "." + p["seen_at"][5:7] + " " + p["seen_at"][11:16]
    src = "Точка" if p["source"] == "tochka" else "PayKeeper"
    return f"{rub(p['amount'])}, {KIND_LABELS.get(p['kind'], p['kind'])} ({src}), {when}, операция {p['ext_id'][-8:]}"


def escalate(con, now: datetime, sources: tuple = ("tochka", "paykeeper")):
    remind = float(cfg(con, "fx_remind_hours") or 2)
    ctl, own = cfg(con, "fx_controller"), _owner(con)
    start = cfg(con, "fx_start") or "2000-01-01"
    for p in con.execute("SELECT * FROM fx_payments WHERE need<>0 AND status IN ('wait','missing') AND pay_date>=?", (start,)).fetchall():
        if p["source"] not in sources and p["need"] == 1:
            continue
        seen = datetime.fromisoformat(p["seen_at"])
        if p["need"] == -1:  # непонятное поступление: сразу вопрос владельцу, один раз
            if p["level"] < 2:
                _alert(con, f"fx:{p['id']}", f"Чеки: непонятное поступление {describe(p)}. Нужен ли чек? Раздел «Чеки»")
                _say(con, f"❓ {_tag(con, own)} Пришло {describe(p)}. Не понимаю, нужен ли чек. Отметьте в CRM → Чеки")
                con.execute("UPDATE fx_payments SET level=2 WHERE id=?", (p["id"],))
            continue
        hours = (now - seen).total_seconds() / 3600
        deadline2 = datetime.combine(seen.date() + timedelta(days=1), datetime.min.time()) + timedelta(hours=12)
        if p["level"] < 1 and hours >= remind:
            con.execute("UPDATE fx_payments SET status='missing', level=1 WHERE id=?", (p["id"],))
            what = ("Пробейте чек на кассе Эвотор" if p["source"] == "tochka" else "Касса PayKeeper не пробила чек, пробейте на Эвоторе")
            _say(con, f"🧾 {_tag(con, ctl) or _tag(con, own)} Нет чека: {describe(p)}. {what} и отметьте в CRM → Чеки")
            _alert(con, f"fx:{p['id']}", f"Нет чека: {describe(p)}")
        if p["level"] < 2 and now >= deadline2:
            con.execute("UPDATE fx_payments SET status='missing', level=2 WHERE id=?", (p["id"],))
            _say(con, f"🔴 {_tag(con, own)} Чек так и не пробит: {describe(p)}. Нужен чек коррекции")


def check_sources(con, now: datetime):
    """Если источник не отвечает несколько часов - сообщаем, иначе тишина будет обманчивой."""
    own = _owner(con)
    for src, label in (("tochka", "Точка"), ("paykeeper", "PayKeeper"), ("ofd", "Платформа ОФД")):
        last_ok = con.execute("SELECT MAX(ts) FROM fx_runs WHERE source=? AND ok=1", (src,)).fetchone()[0]
        last = con.execute("SELECT ok, msg FROM fx_runs WHERE source=? ORDER BY id DESC LIMIT 1", (src,)).fetchone()
        if not last or last["ok"]:
            continue
        since = datetime.fromisoformat(last_ok) if last_ok else None
        first_fail = con.execute("SELECT MIN(ts) FROM fx_runs WHERE source=? AND ok=0 AND ts>?", (src, last_ok or "")).fetchone()[0]
        if first_fail and (now - datetime.fromisoformat(first_fail)).total_seconds() >= SOURCE_DOWN_HOURS * 3600:
            key = f"fxsrc:{src}:{(since or now).strftime('%Y%m%d%H%M')}"
            if con.execute("SELECT 1 FROM alerts WHERE key=?", (key,)).fetchone():
                continue
            _alert(con, key, f"Чеки: {label} не отвечает {SOURCE_DOWN_HOURS}+ ч ({last['msg']})")
            _say(con, f"⚠️ {_tag(con, own)} Сверка чеков: {label} не отвечает больше {SOURCE_DOWN_HOURS} ч. {last['msg']}")


def weekly_ping(con, now: datetime):
    """По понедельникам в 10:00 - короткое «я работаю», чтобы тишина значила «всё в порядке»."""
    if now.weekday() != 0 or now.hour != 10:
        return
    key = "fx_week_" + now.date().isoformat()
    if comms.get(con, key):
        return
    comms.put(con, key, "1")
    wk = (now.date() - timedelta(days=7)).isoformat()
    n = con.execute("SELECT COUNT(*) FROM fx_payments WHERE need=1 AND pay_date>=?", (wk,)).fetchone()[0]
    bad = con.execute("SELECT COUNT(*) FROM fx_payments WHERE need<>0 AND status IN ('missing','wait') AND pay_date>=?", (wk,)).fetchone()[0]
    _say(con, f"✅ {_tag(con, _owner(con))} Сверка чеков за неделю: платежей {n}, без чека {bad}" if bad else
         f"✅ Сверка чеков за неделю: платежей {n}, по всем есть чеки")


# ======================= проход =======================

def configured(con) -> dict:
    return {"tochka": bool(cfg(con, "fx_tochka_token")),
            "paykeeper": bool(cfg(con, "fx_pk_server") and cfg(con, "fx_pk_user") and cfg(con, "fx_pk_password")),
            "ofd": bool(cfg(con, "fx_ofd_token"))}


def run(con, now: datetime | None = None) -> dict:
    now = now or _now()
    if cfg(con, "fx_enabled") != "1":
        return {"skipped": True}
    on = configured(con)
    d_from, d_to = now.date() - timedelta(days=DAYS_BACK), now.date()
    res = {"errors": []}
    steps = [("ofd", lambda: store_ofd(con, fetch_ofd_receipts(con, d_from, d_to))),
             ("paykeeper", lambda: store_pk(con, fetch_pk_payments(con, d_from, d_to), fetch_pk_receipts(con, d_from, d_to), now)),
             ("tochka", lambda: store_tochka(con, fetch_tochka(con, d_from, d_to), now))]
    for src, fn in steps:
        if not on[src]:
            continue
        try:
            fn()
            _log_run(con, src, True, now=now)
        except Exception as e:  # noqa: BLE001
            _log_run(con, src, False, str(e), now)
            res["errors"].append(f"{src}: {e}")
    # без чеков ОФД сверять СБП нечем - иначе будут ложные тревоги
    if on["ofd"]:
        match_tochka(con)
    if on["paykeeper"]:
        res["errors"] += match_pk(con, now, cfg(con, "fx_auto_retry") == "1")
    escalate(con, now, tuple(s for s, ok in (("tochka", on["ofd"] and on["tochka"]), ("paykeeper", on["paykeeper"])) if ok))
    check_sources(con, now)
    weekly_ping(con, now)
    return res


def start_worker(connect):
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
            time.sleep(600)
    threading.Thread(target=loop, name="fiscal-worker", daemon=True).start()


# ======================= для экрана =======================

def view(con, days: int = 30) -> dict:
    since = (_now().date() - timedelta(days=days)).isoformat()
    rows = []
    for p in con.execute("""SELECT p.*, r.ts AS r_ts, r.source AS r_src FROM fx_payments p
                            LEFT JOIN fx_receipts r ON r.id=p.receipt_id
                            WHERE p.pay_date>=? ORDER BY p.seen_at DESC LIMIT 500""", (since,)):
        d = dict(p)
        d["kind_label"] = KIND_LABELS.get(d["kind"], d["kind"])
        d["status_label"] = STATUS_LABELS.get(d["status"], d["status"])
        rows.append(d)
    runs = {}
    for src in ("tochka", "paykeeper", "ofd"):
        r = con.execute("SELECT ts, ok, msg FROM fx_runs WHERE source=? ORDER BY id DESC LIMIT 1", (src,)).fetchone()
        runs[src] = dict(r) if r else None
    problems = sum(1 for r in rows if r["status"] in ("missing",) or (r["need"] == -1 and r["status"] != "skip"))
    return {"rows": rows, "runs": runs, "problems": problems, "settings": settings_view(con), "configured": configured(con)}


def settings_view(con) -> dict:
    v = {k: cfg(con, k) for k in PLAIN_KEYS}
    v.update({k: ("••••" + cfg(con, k)[-4:]) if cfg(con, k) else "" for k in SECRET_KEYS})
    return v


def save_settings(con, data: dict):
    for k, val in data.items():
        if k in PLAIN_KEYS:
            comms.put(con, k, str(val or "").strip())
        elif k in SECRET_KEYS:
            s = str(val or "").strip()
            if s and not s.startswith("••••"):  # маску не сохраняем
                comms.put(con, k, s)
            elif s == "":
                comms.put(con, k, "")
    if data.get("fx_enabled") == "1" and not cfg(con, "fx_start"):
        comms.put(con, "fx_start", _now().date().isoformat())


def resolve(con, pid: int, action: str, user: dict, note: str = ""):
    """Ручное решение: чек пробит вручную / чек не нужен / чек нужен (для непонятных)."""
    p = con.execute("SELECT * FROM fx_payments WHERE id=?", (pid,)).fetchone()
    if not p:
        raise FxError("Платёж не найден")
    who, ts = user.get("name", ""), _iso(_now())
    if action == "done":
        con.execute("UPDATE fx_payments SET status='ok', note=?, resolved_by=?, resolved_at=? WHERE id=?",
                    (note or "Чек пробит вручную", who, ts, pid))
    elif action == "skip":
        if user.get("role") != "admin":
            raise FxError("Отметить «чек не нужен» может только администратор")
        con.execute("UPDATE fx_payments SET status='skip', need=0, note=?, resolved_by=?, resolved_at=? WHERE id=?",
                    (note or "Чек не нужен", who, ts, pid))
    elif action == "need":
        con.execute("UPDATE fx_payments SET status='wait', need=1, level=0, resolved_by=?, resolved_at=? WHERE id=?", (who, ts, pid))
    else:
        raise FxError("Неизвестное действие")
    con.execute("UPDATE alerts SET closed_at=?, closed_by=? WHERE key=? AND closed_at IS NULL", (ts, who, f"fx:{pid}"))


def test_sources(con) -> dict:
    """Кнопка «Проверить подключение»: по каждому источнику - отвечает или нет."""
    out, today = {}, _now().date()
    on = configured(con)
    if on["tochka"]:
        try:
            accs = (tochka_get(con, "/open-banking/v1.0/accounts").get("Data") or {}).get("Account") or []
            out["tochka"] = f"ок, счетов: {len(accs)}"
        except Exception as e:  # noqa: BLE001
            out["tochka"] = f"ошибка: {e}"
    if on["paykeeper"]:
        try:
            n = len(fetch_pk_payments(con, today - timedelta(days=7), today))
            out["paykeeper"] = f"ок, платежей за неделю: {n}"
            if n == 0:
                out["paykeeper"] += " | разбор: " + pk_diag(con, today - timedelta(days=7), today)
        except Exception as e:  # noqa: BLE001
            out["paykeeper"] = f"ошибка: {e}"
    if on["ofd"]:
        try:
            out["ofd"] = f"ок, касс: {len(fetch_ofd_kkts(con))}"
        except Exception as e:  # noqa: BLE001
            out["ofd"] = f"ошибка: {e}"
    return out
