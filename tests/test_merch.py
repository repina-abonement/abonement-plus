"""Проверки мерча. Запуск: python -m tests.test_merch"""
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import db, merch, schedule  # noqa: E402
from tests.test_crm import check  # noqa: E402


def main():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "m.sqlite3"))
    db.init_db(con)
    print("Мерч")
    its = {i["name"]: i for i in merch.items(con)}
    check(its["Форма"]["price"] == 3800 and its["Тату"]["price"] == 330 and len(its) == 12, "Прайс из 12 позиций загружен")
    b = schedule.save_branch(con, {"name": "Небо-тест"})
    coach = {"id": con.execute("INSERT INTO users(login,name,role,pw_hash) VALUES ('c','Никита','coach','x$y')").lastrowid, "role": "coach"}
    try:
        merch.sell(con, coach, {"branch_id": b, "lines": [{"item_id": its["Шарф"]["id"], "qty": 1}]})
        check(False, "Без способа оплаты продажа не сохраняется")
    except merch.MerchError:
        check(True, "Без способа оплаты продажа не сохраняется")
    d = date.today().isoformat()
    merch.sell(con, coach, {"branch_id": b, "method": "qr", "lines": [{"item_id": its["Шарф"]["id"], "qty": 1}, {"item_id": its["Носки"]["id"], "qty": 2}]})
    merch.sell(con, coach, {"branch_id": b, "method": "cash", "lines": [{"item_id": its["Мяч"]["id"], "qty": 1}]})
    r = merch.registry(con, d, d)
    check(r["total"] == 2000 + 900 + 3850 and r["by_method"]["qr"] == 2900, "Итог: 6750 ₽, по QR 2900 ₽")
    check(len(r["qr_no_receipt"]) == 2, "QR-продажи без чека подсвечены")
    check(r["by_seller"][0]["cash_not_handed"] == 3850, "Наличные 3850 ₽ числятся у тренера, пока не сдал")
    for x in r["qr_no_receipt"]:
        merch.set_flag(con, x["id"], "receipt_done", True)
    check(not merch.registry(con, d, d)["qr_no_receipt"], "После «Чек пробит» подсветка пропала")
    check(merch.my_shift(con, coach["id"], d)["reports"][b] == 1, "Итог смены отмечен сам: продажи были")


if __name__ == "__main__":
    main()
    print("\nВсе проверки мерча пройдены")
