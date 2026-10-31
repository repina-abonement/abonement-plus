"""Абонемент Плюс. Время в CRM московское, даже если на сервере стоит UTC (иначе «сегодня», 25 число,
время пробных и сообщений сдвигаются на 3 часа). Другой часовой пояс: переменная CRM_TZ."""
import os
import time

os.environ["TZ"] = os.environ.get("CRM_TZ") or "Europe/Moscow"
if hasattr(time, "tzset"):
    time.tzset()
