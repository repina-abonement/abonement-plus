"""Сводка дня по звонкам оператора — как в разборах-PDF: средняя оценка, записи, карта звонков,
что видно по воронке, приоритеты обучения, главная установка на следующую смену и «фокус на сегодня»
для рабочего стола оператора.

Цифры считаются из разборов звонков. Текстовую часть пишет YandexGPT (если разбор звонков включён),
иначе сводка остаётся с цифрами и картой звонков.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from . import ai, comms

SCHEMA = """
CREATE TABLE IF NOT EXISTS call_digests (
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,
    operator TEXT NOT NULL,
    data TEXT NOT NULL,
    calls_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(day, operator)
);
"""
BOOKED = ("записали на пробное", "купил абонемент", "продлил")


def init(con):
    con.executescript(SCHEMA)


def _rows(con, day: str, operator: str) -> list[dict]:
    out = []
    for r in con.execute("""SELECT id, started_at, score, analysis, phone FROM calls WHERE ai_status='done' AND operator=?
                            AND substr(started_at,1,10)=? ORDER BY started_at""", (operator, day)):
        a = json.loads(r["analysis"] or "{}")
        out.append({"id": r["id"], "time": r["started_at"][11:16], "score": r["score"], "case": a.get("case") or a.get("summary", "")[:40],
                    "outcome": a.get("outcome") or a.get("result") or "", "result": a.get("result") or "",
                    "summary": a.get("summary", ""), "tips": a.get("tips", []), "focus": a.get("focus", ""),
                    "points": a.get("points", [])})
    return out


def _llm(con, rows: list[dict]) -> dict:
    compact = [{k: r[k] for k in ("case", "score", "outcome", "summary", "tips", "focus", "points")} for r in rows]
    code, js = comms.http("POST", f"{ai.LLM_URL}/completion", headers=ai._headers(con), timeout=120, data={
        "modelUri": ai.model_uri(con), "completionOptions": {"temperature": 0.2, "maxTokens": "2000"},
        "messages": [{"role": "system", "text": ai.DIGEST_PROMPT},
                     {"role": "user", "text": json.dumps(compact, ensure_ascii=False)[:28000]}]})
    ai._check(code, js, "YandexGPT")
    text = ((js.get("result") or {}).get("alternatives") or [{}])[0].get("message", {}).get("text", "")
    return ai.parse_json(text)


def build(con, day: str, operator: str, use_llm: bool = True) -> dict | None:
    rows = _rows(con, day, operator)
    if not rows:
        return None
    scores = [r["score"] for r in rows if r["score"] is not None]
    data = {"day": day, "operator": operator, "calls": len(rows),
            "avg": round(sum(scores) / len(scores), 1) if scores else None,
            "booked": sum(1 for r in rows if r["result"] in BOOKED),
            "map": [{k: r[k] for k in ("id", "time", "case", "score", "outcome")} for r in rows]}
    if use_llm and ai.enabled(con):
        try:
            data.update(_llm(con, rows))
        except Exception as e:  # noqa: BLE001 — сводка с цифрами всё равно полезна
            data["llm_error"] = str(e)[:200]
    save(con, day, operator, data)
    return data


def save(con, day: str, operator: str, data: dict):
    con.execute("""INSERT INTO call_digests(day, operator, data, calls_count, created_at) VALUES (?,?,?,?,?)
                   ON CONFLICT(day, operator) DO UPDATE SET data=excluded.data, calls_count=excluded.calls_count,
                   created_at=excluded.created_at""",
                (day, operator, json.dumps(data, ensure_ascii=False), data.get("calls", 0), datetime.now().isoformat(timespec="minutes")))


def tick(con, now: datetime | None = None) -> int:
    """Вечером (после 21:00) собирает сводки за сегодня; утром добирает вчерашние, если не успели."""
    now = now or datetime.now()
    days = [(now.date() - timedelta(days=1)).isoformat()] + ([now.date().isoformat()] if now.hour >= 21 else [])
    made = 0
    for day in days:
        for (op, n) in con.execute("""SELECT operator, COUNT(*) FROM calls WHERE ai_status='done' AND operator!=''
                                      AND substr(started_at,1,10)=? GROUP BY operator""", (day,)).fetchall():
            have = con.execute("SELECT calls_count FROM call_digests WHERE day=? AND operator=?", (day, op)).fetchone()
            if not have or have[0] < n:
                build(con, day, op)
                made += 1
    return made


def get(con, day: str, operator: str) -> dict | None:
    r = con.execute("SELECT data FROM call_digests WHERE day=? AND operator=?", (day, operator)).fetchone()
    return json.loads(r[0]) if r else None


def listing(con, days: int = 21) -> list[dict]:
    names = {r[0]: r[1] for r in con.execute("SELECT sip_number, name FROM users WHERE sip_number != ''")}
    since = (date.today() - timedelta(days=days)).isoformat()
    out = []
    for r in con.execute("SELECT day, operator, data FROM call_digests WHERE day>=? ORDER BY day DESC, operator", (since,)):
        d = json.loads(r["data"])
        out.append({"day": r["day"], "operator": r["operator"], "name": names.get(r["operator"], f"Номер {r['operator']}"),
                    "calls": d.get("calls"), "avg": d.get("avg"), "booked": d.get("booked"), "setting": d.get("setting", "")})
    return out


def focus_for(con, sip: str) -> dict | None:
    """Для стола оператора: последняя сводка (вчера или раньше) — что держать в голове сегодня."""
    if not sip:
        return None
    r = con.execute("""SELECT day, data FROM call_digests WHERE operator=? AND day<? ORDER BY day DESC LIMIT 1""",
                    (sip, date.today().isoformat())).fetchone()
    if not r:
        return None
    d = json.loads(r["data"])
    if not (d.get("focus_today") or d.get("training") or d.get("setting")):
        return None
    return {"day": r["day"], "operator": sip, "focus": d.get("focus_today") or d.get("training", [])[:3], "setting": d.get("setting", ""),
            "avg": d.get("avg"), "calls": d.get("calls"), "booked": d.get("booked")}
