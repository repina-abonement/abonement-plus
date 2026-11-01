"""Новый отзыв на Яндекс Картах / 2ГИС: плашка руководителю. Запуск: python -m tests.test_reviews"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import db, reviews
from tests.test_crm import check

con = db.connect(os.path.join(tempfile.mkdtemp(), "r.sqlite3")); db.init_db(con)
check(reviews.hook(con, {"source": "yandex", "id": "z"})["ok"] is False or reviews.ENABLED, "Пока не включено, сигналы не принимаем")
reviews.ENABLED = True
reviews.hook(con, {"source": "yandex", "subject": "У вас новый отзыв для Футболика", "id": "a"})
reviews.hook(con, {"source": "yandex", "subject": "У вас новый отзыв для Футболика", "id": "a"})
reviews.hook(con, {"source": "2gis", "subject": "Новый отзыв", "id": "b"})
check(reviews.hook(con, {"source": "spam"})["ok"] is False, "Чужой источник не принимаем")
o = reviews.open_list(con)
check(len(o) == 2 and {x["label"] for x in o} == {"Яндекс Картах", "2ГИС"}, "Одно письмо = одна плашка, обе площадки")
reviews.close(con, o[0]["id"], {"name": "Юлия"})
check(len(reviews.open_list(con)) == 1, "«Ответили»: плашка ушла")
check("checkReviews" in reviews.script("https://x/hooks/review/t"), "Скрипт Google с адресом CRM")
print("ok")
