"""Резервная копия базы раз в сутки, хранит последние 14 копий.

Запуск: python -m crm.backup  (в docker-compose работает отдельным сервисом)
"""
import glob
import os
import sqlite3
import time
from datetime import datetime

from .db import DB_PATH

DIR = os.environ.get("CRM_BACKUP_DIR", os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "backups"))
KEEP = int(os.environ.get("CRM_BACKUP_KEEP", "14"))


def backup_once() -> str:
    os.makedirs(DIR, exist_ok=True)
    dst = os.path.join(DIR, f"crm-{datetime.now():%Y-%m-%d_%H%M}.sqlite3")
    src = sqlite3.connect(DB_PATH)
    out = sqlite3.connect(dst)
    with out:
        src.backup(out)
    out.close()
    src.close()
    for old in sorted(glob.glob(os.path.join(DIR, "crm-*.sqlite3")))[:-KEEP]:
        os.remove(old)
    return dst


if __name__ == "__main__":
    while True:
        if os.path.exists(DB_PATH):
            print("Копия:", backup_once(), flush=True)
        time.sleep(24 * 3600)
