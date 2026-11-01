"""Новые отзывы на Яндекс Картах и 2ГИС: красная плашка руководителю на рабочем столе (Юлия, 01.11.2026).

Письма площадок об отзывах приходят на почту руководителя. Небольшой скрипт Google в этой почте раз в 5 минут
находит новые такие письма и сообщает CRM только факт: какая площадка и тема письма. Текст отзыва и данные
клиентов в CRM не передаются. Плашка висит, пока руководитель не нажмёт «Ответили».
"""
from __future__ import annotations

from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS review_alerts (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,              -- yandex | 2gis
    subject TEXT NOT NULL DEFAULT '',
    msg_id TEXT UNIQUE,                -- номер письма в Gmail: одно письмо = одна плашка
    created_at TEXT NOT NULL,
    closed_at TEXT,
    closed_by TEXT NOT NULL DEFAULT ''
);
"""
LABELS = {"yandex": "Яндекс Картах", "2gis": "2ГИС"}
LINKS = {"yandex": "https://yandex.ru/sprav/", "2gis": "https://account.2gis.com/"}


# Юлия, 01.11: сначала показать, включать после её «да». Пока False, плашек нет и сигналы не принимаются
ENABLED = False


def init(con):
    con.executescript(SCHEMA)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def hook(con, payload: dict) -> dict:
    if not ENABLED:
        return {"ok": False, "error": "disabled"}
    src = str(payload.get("source") or "").lower()
    src = "2gis" if "2gis" in src or "2гис" in src else "yandex" if "yandex" in src or "яндекс" in src else ""
    if not src:
        return {"ok": False, "error": "unknown source"}
    con.execute("INSERT OR IGNORE INTO review_alerts(source, subject, msg_id, created_at) VALUES (?,?,?,?)",
                (src, str(payload.get("subject") or "")[:200], str(payload.get("id") or "") or None, _now()))
    return {"ok": True}


def open_list(con) -> list[dict]:
    if not ENABLED:
        return []
    out = []
    for r in con.execute("SELECT * FROM review_alerts WHERE closed_at IS NULL ORDER BY id DESC LIMIT 20"):
        out.append(dict(r) | {"label": LABELS[r["source"]], "link": LINKS[r["source"]]})
    return out


def close(con, rid: int, user: dict) -> None:
    con.execute("UPDATE review_alerts SET closed_at=?, closed_by=? WHERE id=? AND closed_at IS NULL", (_now(), user.get("name", ""), rid))


def script(hook_url: str) -> str:
    """Скрипт Google для почты руководителя: находит письма об отзывах и сообщает CRM."""
    return f"""// Абонемент Плюс: новые отзывы с Яндекс Карт и 2ГИС -> красная плашка в CRM
// Запуск раз в 5 минут (триггер по времени). В CRM уходит только площадка и тема письма.
function checkReviews() {{
  var url = '{hook_url}';
  var label = GmailApp.getUserLabelByName('crm-отзыв') || GmailApp.createLabel('crm-отзыв');
  var searches = [
    ['yandex', 'subject:(отзыв) from:(yandex) newer_than:2d -label:crm-отзыв'],
    ['2gis', 'subject:(отзыв) from:(2gis) newer_than:2d -label:crm-отзыв']
  ];
  searches.forEach(function (s) {{
    GmailApp.search(s[1], 0, 20).forEach(function (t) {{
      var m = t.getMessages()[0];
      UrlFetchApp.fetch(url, {{method: 'post', contentType: 'application/json', muteHttpExceptions: true,
        payload: JSON.stringify({{source: s[0], subject: m.getSubject(), id: m.getId()}})}});
      t.addLabel(label);
    }});
  }});
}}
"""
