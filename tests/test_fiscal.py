"""Проверки сверки чеков 54-ФЗ. Запуск: python -m tests.test_fiscal"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import comms, db, fiscal, pu  # noqa: E402
from tests.test_crm import check  # noqa: E402

ADM = {"id": 1, "name": "Юлия", "role": "admin"}
SAID = []
DATA = {"tochka": [], "pk_pay": [], "pk_rec": [], "ofd": []}
REPRINTS = []


def tx(tid, amount, desc, inn="", acc="", d="2026-11-03"):
    return {"transactionId": tid, "creditDebitIndicator": "Credit", "status": "Booked", "documentProcessDate": d,
            "description": desc, "Amount": {"amount": amount, "currency": "RUB"},
            "DebtorParty": {"inn": inn}, "DebtorAccount": {"identification": acc}}


def main():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "f.sqlite3"))
    db.init_db(con)
    fiscal.fetch_tochka = lambda c, a, b: DATA["tochka"]
    fiscal.fetch_pk_payments = lambda c, a, b: DATA["pk_pay"]
    fiscal.fetch_pk_receipts = lambda c, a, b: DATA["pk_rec"]
    fiscal.fetch_ofd_receipts = lambda c, a, b: DATA["ofd"]
    fiscal.pk_receipt_raw = lambda c, rid: {"payment_id": "501", "cart": "[]", "sum_cashless": "8000"}
    fiscal.pk_reprint = lambda c, raw, n: REPRINTS.append(n) or f"9{n}"
    pu.notify_chat = lambda c, which, text: SAID.append(text) or True

    print("Выключено - ничего не делает")
    check(fiscal.run(con).get("skipped"), "Пока не включили, модуль молчит")

    fiscal.save_settings(con, {"fx_enabled": "1", "fx_tochka_token": "t-123456", "fx_pk_server": "https://x.paykeeper.ru",
                               "fx_pk_user": "api", "fx_pk_password": "p", "fx_ofd_token": "o", "fx_inn": "770000000001"})
    comms.put(con, "fx_start", "2026-11-01")
    v = fiscal.settings_view(con)
    check(v["fx_tochka_token"] == "••••3456", "Токен на экране только маской")
    fiscal.save_settings(con, {"fx_tochka_token": "••••3456"})
    check(comms.get(con, "fx_tochka_token") == "t-123456", "Маска при сохранении не затирает токен")

    print("Разбор поступлений Точки")
    me = "770000000001"
    c = lambda *a, **k: fiscal.classify_tochka(tx("x", 1, *a, **k), me)
    check(c("Оплата по QR-коду СБП") == ("sbp", 1), "СБП - нужен чек")
    check(c("Возмещение по операциям эквайринга") == ("acquiring", 0), "Возмещение эквайринга - чек не нужен")
    check(c("Перевод собственных средств", inn=me) == ("own", 0), "Перевод себе - чек не нужен")
    check(c("Оплата по счёту 12", inn="7701234567", acc="40702810000000000001") == ("company", 0), "От организации - чек не нужен")
    check(c("Перевод", acc="40817810000000000001") == ("person", 1), "Перевод с личного счёта физлица - нужен чек")
    check(c("Что-то странное") == ("unknown", -1), "Непонятное - спросим")

    t0 = datetime(2026, 11, 3, 15, 0)
    DATA["tochka"] = [tx("T1", "8000.00", "Оплата СБП по ссылке"), tx("T2", "6500.00", "Оплата СБП по ссылке"),
                      tx("T3", "120000.00", "Возмещение по операциям эквайринга"), tx("T4", "300.00", "Непонятно что")]
    DATA["ofd"] = [{"fiscalDriveNumber": "FN-EVO", "fiscalDocumentNumber": 11, "receiptDate": "2026.11.03 15:20:00.000",
                    "totalSum": 800000, "ecashTotalSum": 800000, "operationType": 1, "kktRegId": "R1"}]
    fiscal.run(con, t0 + timedelta(minutes=30))
    st = {r["ext_id"]: r["status"] for r in con.execute("SELECT * FROM fx_payments")}
    check(st["T1"] == "ok", "СБП 8000 нашла свой чек на Эвоторе")
    check(st["T2"] == "wait", "СБП 6500 пока ждёт чек")
    check(st["T3"] == "skip", "Возмещение эквайринга чек не требует")
    check(any("Не понимаю, нужен ли чек" in s for s in SAID), "Про непонятные 300 ₽ спросили владельца")

    print("Эскалация")
    SAID.clear()
    fiscal.run(con, datetime.fromisoformat(con.execute("SELECT seen_at FROM fx_payments WHERE ext_id='T2'").fetchone()[0]) + timedelta(hours=2, minutes=5))
    check(any("Нет чека" in s and "6 500" in s for s in SAID), "Через 2 часа без чека - напоминание контролёру")
    check(con.execute("SELECT 1 FROM alerts WHERE kind='fiscal' AND text LIKE 'Нет чека%'").fetchone(), "Тревога на рабочем столе")
    SAID.clear()
    fiscal.run(con, datetime(2026, 11, 3, 23, 0))
    check(not SAID, "Повторно не дёргает")
    pid = con.execute("SELECT id FROM fx_payments WHERE ext_id='T2'").fetchone()[0]
    seen = datetime.fromisoformat(con.execute("SELECT seen_at FROM fx_payments WHERE id=?", (pid,)).fetchone()[0])
    fiscal.run(con, datetime.combine(seen.date() + timedelta(days=1), datetime.min.time()) + timedelta(hours=12, minutes=1))
    check(any("так и не пробит" in s for s in SAID), "На следующий день в 12:00 - владельцу")
    fiscal.resolve(con, pid, "done", {"name": "Варя", "role": "operator"}, "Пробили 04.11")
    check(con.execute("SELECT status FROM fx_payments WHERE id=?", (pid,)).fetchone()[0] == "ok", "Отметка «пробит вручную» закрывает")
    tid = con.execute("SELECT id FROM fx_payments WHERE ext_id='T4'").fetchone()[0]
    try:
        fiscal.resolve(con, tid, "skip", {"name": "Варя", "role": "operator"})
        check(False, "Не админ не может сказать «чек не нужен»")
    except fiscal.FxError:
        check(True, "«Чек не нужен» - только администратор")

    print("PayKeeper")
    SAID.clear()
    t1 = datetime(2026, 11, 5, 12, 0)
    DATA["tochka"], DATA["ofd"] = [], []
    DATA["pk_pay"] = [{"id": 500, "pay_amount": "8000.00", "status": "success", "success_datetime": "2026-11-05 12:00:00"},
                      {"id": 501, "pay_amount": "8000.00", "status": "success", "success_datetime": "2026-11-05 12:00:00"}]
    DATA["pk_rec"] = [{"id": 70, "payment_id": 500, "status": "success", "sum_cashless": "8000.00", "fn": "FN-PK"},
                      {"id": 71, "payment_id": 501, "status": "failed", "sum_cashless": "8000.00", "fn": "FN-PK"}]
    fiscal.run(con, t1 + timedelta(minutes=10))
    check(not REPRINTS, "Первые 30 минут ждём, не дёргаем кассу")
    fiscal.run(con, t1 + timedelta(minutes=40))
    st = {r["ext_id"]: r for r in con.execute("SELECT * FROM fx_payments WHERE source='paykeeper'")}
    check(st["500"]["status"] == "ok", "Платёж с успешным чеком - ок")
    check(REPRINTS == [1] and st["501"]["retries"] == 1, "Касса не пробила - сами отправили чек повторно")
    check(not SAID, "Пока чинится сам - никого не дёргаем")
    DATA["pk_rec"].append({"id": 91, "payment_id": 501, "status": "success", "sum_cashless": "8000.00", "fn": "FN-PK"})
    fiscal.run(con, t1 + timedelta(minutes=50))
    check(con.execute("SELECT status FROM fx_payments WHERE ext_id='501'").fetchone()[0] == "ok", "Повторный чек прошёл - ок")

    print("Чек облачной кассы PayKeeper не закрывает СБП Точки")
    DATA["tochka"] = [tx("T9", "8000.00", "СБП", d="2026-11-05")]
    DATA["ofd"] = [{"fiscalDriveNumber": "FN-PK", "fiscalDocumentNumber": 5, "receiptDate": "2026.11.05 12:01:00.000",
                    "totalSum": 800000, "ecashTotalSum": 800000, "operationType": 1}]
    fiscal.run(con, t1 + timedelta(hours=1))
    check(con.execute("SELECT status FROM fx_payments WHERE ext_id='T9'").fetchone()[0] == "wait", "СБП ждёт чек именно с Эвотора")

    print("Источник не отвечает")
    SAID.clear()
    def boom(c, a, b):
        raise fiscal.FxError("Точка ответила 401")
    fiscal.fetch_tochka = boom
    for m in (0, 70, 140, 200):
        fiscal.run(con, datetime(2026, 11, 6, 9, 0) + timedelta(minutes=m))
    check(sum("не отвечает" in s for s in SAID) == 1, "Точка молчит 3+ часа - одно сообщение владельцу")

    print("Персональные данные")
    cols = {r[1] for r in con.execute("PRAGMA table_info(fx_payments)")} | {r[1] for r in con.execute("PRAGMA table_info(fx_receipts)")}
    check(not cols & {"payer", "name", "phone", "email", "contact", "description"}, "Ни имени, ни телефона, ни почты не храним")
    print("Готово")


if __name__ == "__main__":
    main()
