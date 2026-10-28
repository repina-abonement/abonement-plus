"""Расшифровка и разбор звонков: Yandex SpeechKit (текст) + YandexGPT (разбор по чек-листу продаж).

Работает фоном внутри сервера: раз в минуту берёт состоявшиеся разговоры с записью,
скачивает запись из Sipuni, отправляет на распознавание, потом на разбор.
Всё состояние в таблице calls, поэтому после перезапуска сервер продолжает с того же места.
"""
from __future__ import annotations

import base64
import json
import re
import threading
import time
import traceback
from datetime import datetime, timedelta

from . import comms

STT_URL = "https://stt.api.cloud.yandex.net/stt/v3"
OP_URL = "https://operation.api.cloud.yandex.net/operations"
LLM_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1"

CHECKLIST = [
    ("hello", "Поздоровался, назвал себя и школу"),
    ("child", "Узнал имя и возраст ребёнка"),
    ("needs", "Выяснил, что важно родителю и откуда узнал о школе"),
    ("place", "Подобрал удобный филиал и время"),
    ("value", "Рассказал, чем полезны занятия, а не только цену"),
    ("trial", "Предложил пробное на конкретный день и время"),
    ("objection", "Отработал сомнения и возражения"),
    ("next", "Договорился о следующем шаге"),
]
RESULTS = ["записали на пробное", "купил абонемент", "продлил", "думает", "перезвонить", "отказ", "не целевой", "другое"]

SYSTEM_PROMPT = """Ты руководитель отдела продаж детской футбольной школы. Тебе дают расшифровку телефонного разговора администратора школы с родителем.
Разбери разговор как внутренний разбор для обучения администратора: строго по делу, без воды, по-русски, к администратору на «ты».
Ответь только JSON без пояснений и без markdown, по схеме:
{
 "case": "коротко, о ком звонок: имя родителя и ребёнка, возраст, например «Анна и Тимур, 2,5 года»",
 "summary": "2-3 предложения: кто звонил, зачем, чем закончилось",
 "result": "одно из: %RESULTS%",
 "outcome": "итог в 2-5 словах, например «Запись на пробное в пн 17:00» или «Расписание в WhatsApp, записи нет»",
 "score": целое 0-10 (насколько хорошо администратор провёл разговор с точки зрения продажи),
 "headline": "главный вывод одной фразой, например «Результат достигнут, но родителя нужно было увереннее настроить на пробу»",
 "conclusion": "2-3 предложения: что сделано хорошо и в чём главная потеря",
 "points": [{"ok": true, "text": "что удалось: записан, получены данные, выбран канал подтверждения"}, {"ok": false, "text": "что упущено"}],
 "focus": "ключевой фокус для администратора: одна конкретная установка на следующие звонки, с примером фразы",
 "checklist": {%CHECK_KEYS%},
 "objections": ["сомнения или возражения родителя, если были"],
 "next_step": "о чём договорились и кто что должен сделать",
 "good": "что получилось хорошо, одной фразой",
 "tips": ["1-3 конкретных совета, что сказать иначе в следующий раз, с примером фразы"],
 "dialogue": [{"who": "admin" или "client", "text": "реплика, очищенная только для читаемости, смысл и порядок сохранены"}],
 "script": [{"who": "admin" или "client", "text": "как разговор должен был пойти правильно: ключевые реплики администратора и ожидаемые ответы, 6-12 реплик"}],
 "child": {"name": "", "age": "", "branch": "", "time": ""}
}
В checklist для каждого пункта ставь true, false или null (null если пункт неприменим, например звонок не о продаже).
Пункты: %CHECK_TEXT%.
Если разговор не о продаже (например, болеет ребёнок, перенос, оплата), оцени вежливость и решение вопроса, а пункты продажи ставь null, script можно оставить пустым.
В расшифровке реплики помечены каналами, определи сам, где администратор, а где родитель."""

DIGEST_PROMPT = """Ты руководитель отдела продаж детской футбольной школы. Тебе дают разборы всех клиентских звонков одного администратора за день (JSON-список).
Сделай сводку дня для обучения. Только JSON без пояснений и без markdown:
{
 "overview": "2-3 предложения: что за день, сколько разговоров, общее впечатление",
 "funnel": ["4-6 наблюдений по воронке: что стабильно приводит к записи, где главные потери, что повторяется"],
 "training": ["3-5 приоритетов обучения: короткие конкретные действия, например «Закрывать на конкретное время двумя вариантами»"],
 "setting": "главная установка на следующую смену: 1-2 предложения",
 "focus_today": ["2-3 очень коротких пункта, которые администратор увидит утром на рабочем столе"]
}
Пиши по-русски, к администратору на «ты», коротко и по делу."""


def _prompt() -> str:
    return (SYSTEM_PROMPT.replace("%RESULTS%", ", ".join(RESULTS))
            .replace("%CHECK_KEYS%", ", ".join(f'"{k}": true|false|null' for k, _ in CHECKLIST))
            .replace("%CHECK_TEXT%", "; ".join(f"{k} = {t}" for k, t in CHECKLIST)))


def init(con):
    cols = {r[1] for r in con.execute("PRAGMA table_info(calls)")}
    for col in ("ai_status TEXT NOT NULL DEFAULT ''", "ai_op TEXT NOT NULL DEFAULT ''", "ai_error TEXT NOT NULL DEFAULT ''",
                "ai_tries INTEGER NOT NULL DEFAULT 0", "ai_at TEXT", "analysis TEXT NOT NULL DEFAULT ''", "score INTEGER"):
        if col.split()[0] not in cols:
            con.execute(f"ALTER TABLE calls ADD COLUMN {col}")


def enabled(con) -> bool:
    return comms.get(con, "ai_enabled") == "1" and bool(comms.get(con, "yc_api_key"))


def _headers(con) -> dict:
    h = {"Authorization": f"Api-Key {comms.get(con, 'yc_api_key')}"}
    if comms.get(con, "yc_folder"):
        h["x-folder-id"] = comms.get(con, "yc_folder")
    return h


def _check(code, js, what):
    if code >= 300:
        msg = (js.get("message") or js.get("error") or js.get("_text")) if isinstance(js, dict) else None
        raise comms.CommsError(f"{what}: {msg or 'ошибка ' + str(code)}")
    return js


def model_uri(con) -> str:
    folder = comms.get(con, "yc_folder")
    if not folder:
        raise comms.CommsError("Не указан ID каталога Yandex Cloud")
    model = comms.get(con, "ai_model") or "yandexgpt"
    return f"gpt://{folder}/{model}/latest"


def test_connection(con) -> dict:
    """Короткий запрос к YandexGPT: проверяет ключ и каталог."""
    code, js = comms.http("POST", f"{LLM_URL}/completion", headers=_headers(con), timeout=30, data={
        "modelUri": model_uri(con), "completionOptions": {"temperature": 0, "maxTokens": "20"},
        "messages": [{"role": "user", "text": "Ответь одним словом: работает"}]})
    _check(code, js, "YandexGPT")
    text = ((js.get("result") or {}).get("alternatives") or [{}])[0].get("message", {}).get("text", "")
    return {"ok": True, "answer": text.strip()}


# ---------------- шаги конвейера ----------------

def _guess_format(content: bytes, ctype: str) -> str:
    if content[:4] == b"RIFF" or "wav" in (ctype or ""):
        return "WAV"
    if content[:4] == b"OggS" or "ogg" in (ctype or ""):
        return "OGG_OPUS"
    return "MP3"


def submit_stt(con, call: dict):
    content, ctype = comms.record_for(con, call["id"])
    mode = comms.get(con, "ai_mode") or "deferred"
    code, js = comms.http("POST", f"{STT_URL}/recognizeFileAsync", headers=_headers(con), timeout=60, data={
        "content": base64.b64encode(content).decode(),
        "recognitionModel": {
            "model": "deferred-general" if mode == "deferred" else "general",
            "audioFormat": {"containerAudio": {"containerAudioType": _guess_format(content, ctype)}},
            "textNormalization": {"textNormalization": "TEXT_NORMALIZATION_ENABLED", "literatureText": True},
            "languageRestriction": {"restrictionType": "WHITELIST", "languageCode": ["ru-RU"]},
        },
    })
    _check(code, js, "SpeechKit")
    return js["id"]


def _op(con, op_id: str) -> dict:
    code, js = comms.http("GET", f"{OP_URL}/{op_id}", headers=_headers(con), timeout=30)
    return _check(code, js, "Yandex Cloud")


def fetch_transcript(con, op_id: str) -> str:
    code, content, _ = comms.http("GET", f"{STT_URL}/getRecognition?operationId={op_id}", headers=_headers(con),
                                  timeout=60, raw=True)
    if code >= 300:
        raise comms.CommsError(f"SpeechKit: ошибка {code} при получении текста")
    return transcript_from_stream(content.decode("utf-8", errors="replace"))


def transcript_from_stream(raw: str) -> str:
    """Ответ getRecognition — JSON-объекты подряд. Собираем фразы по каналам и сортируем по времени."""
    objs = []
    for line in raw.splitlines():
        line = line.strip()
        if line:
            try:
                objs.append(json.loads(line))
            except ValueError:
                pass
    if not objs:
        try:
            whole = json.loads(raw)
            objs = whole if isinstance(whole, list) else [whole]
        except ValueError:
            return ""
    finals: dict[str, list] = {}
    for o in objs:
        o = o.get("result", o)
        ch = str(o.get("channelTag") or "0")
        if o.get("final"):
            alt = (o["final"].get("alternatives") or [{}])[0]
            if alt.get("text"):
                finals.setdefault(ch, []).append([int(alt.get("startTimeMs") or 0), alt["text"]])
        ref = (o.get("finalRefinement") or {})
        if ref.get("normalizedText"):
            alt = (ref["normalizedText"].get("alternatives") or [{}])[0]
            idx = int(ref.get("finalIndex") or 0)
            lst = finals.setdefault(ch, [])
            if alt.get("text") and idx < len(lst):
                lst[idx][1] = alt["text"]
    many = len(finals) > 1
    lines = sorted(((t, ch, txt) for ch, lst in finals.items() for t, txt in lst))
    out = []
    for t, ch, txt in lines:
        mm, ss = divmod(t // 1000, 60)
        out.append(f"[{mm:02d}:{ss:02d}]" + (f" Канал {int(ch) + 1 if ch.isdigit() else ch}:" if many else "") + f" {txt}")
    return "\n".join(out)


def submit_llm(con, transcript: str) -> str:
    code, js = comms.http("POST", f"{LLM_URL}/completionAsync", headers=_headers(con), timeout=30, data={
        "modelUri": model_uri(con),
        "completionOptions": {"temperature": 0.2, "maxTokens": "6000"},
        "messages": [{"role": "system", "text": _prompt()}, {"role": "user", "text": transcript[:30000]}],
    })
    _check(code, js, "YandexGPT")
    return js["id"]


def parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise comms.CommsError("YandexGPT ответил не в том формате")
    return json.loads(m.group(0))


def parse_analysis(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise comms.CommsError("YandexGPT ответил не в том формате")
    a = json.loads(m.group(0))
    a["checklist"] = {k: a.get("checklist", {}).get(k) for k, _ in CHECKLIST}
    try:
        a["score"] = max(0, min(10, int(a.get("score"))))
    except (TypeError, ValueError):
        a["score"] = None
    for k in ("objections", "tips", "points", "dialogue", "script"):
        if not isinstance(a.get(k), list):
            a[k] = [a[k]] if a.get(k) else []
    a["points"] = [p if isinstance(p, dict) else {"ok": True, "text": str(p)} for p in a["points"]]
    a["dialogue"] = [d for d in a["dialogue"] if isinstance(d, dict) and d.get("text")]
    a["script"] = [d for d in a["script"] if isinstance(d, dict) and d.get("text")]
    return a


# ---------------- фоновый цикл ----------------

def _set(con, cid, **kw):
    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute(f"UPDATE calls SET {', '.join(k + '=?' for k in kw)} WHERE id=?", (*kw.values(), cid))
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def _fail(con, call, err: str, retry: bool):
    tries = (call["ai_tries"] or 0) + 1
    if retry and tries < 8:
        _set(con, call["id"], ai_tries=tries, ai_error=err, ai_status=call["ai_status"])
    else:
        _set(con, call["id"], ai_tries=tries, ai_error=err, ai_status="error")


def tick(con) -> int:
    """Один проход конвейера. Возвращает, сколько звонков сдвинулось."""
    if not enabled(con):
        return 0
    min_talk = int(comms.get(con, "ai_min_talk") or 25)
    since = (datetime.now() - timedelta(days=3)).isoformat(timespec="seconds")
    moved = 0
    # 1) новые разговоры → на распознавание
    for call in [dict(r) for r in con.execute(
            """SELECT * FROM calls WHERE ai_status='' AND status='answered' AND record_url != '' AND talk >= ?
               AND started_at >= ? AND external_id NOT LIKE 'demo-%' ORDER BY started_at LIMIT 5""", (min_talk, since))]:
        try:
            op = submit_stt(con, call)
            _set(con, call["id"], ai_status="stt", ai_op=op, ai_error="", ai_at=comms.ts_now())
            moved += 1
        except comms.CommsError as e:  # запись может быть ещё не готова — попробуем позже
            _fail(con, call, str(e), retry=True)
    # 2) распознавание готово → текст → на разбор
    for call in [dict(r) for r in con.execute("SELECT * FROM calls WHERE ai_status='stt' LIMIT 20")]:
        try:
            op = _op(con, call["ai_op"])
            if not op.get("done"):
                continue
            if op.get("error"):
                raise comms.CommsError("SpeechKit: " + str(op["error"].get("message") or op["error"]))
            text = fetch_transcript(con, call["ai_op"])
            if not text.strip():
                _set(con, call["id"], ai_status="done", transcript="", analysis=json.dumps(
                    {"summary": "В записи не удалось разобрать речь", "checklist": {}}, ensure_ascii=False))
                continue
            llm = submit_llm(con, text)
            _set(con, call["id"], ai_status="llm", ai_op=llm, transcript=text, ai_error="")
            moved += 1
        except comms.CommsError as e:
            _fail(con, call, str(e), retry=True)
    # 3) разбор готов → сохранить
    for call in [dict(r) for r in con.execute("SELECT * FROM calls WHERE ai_status='llm' LIMIT 20")]:
        try:
            op = _op(con, call["ai_op"])
            if not op.get("done"):
                continue
            if op.get("error"):
                raise comms.CommsError("YandexGPT: " + str(op["error"].get("message") or op["error"]))
            alt = ((op.get("response") or {}).get("alternatives") or [{}])[0]
            a = parse_analysis((alt.get("message") or {}).get("text", ""))
            _set(con, call["id"], ai_status="done", analysis=json.dumps(a, ensure_ascii=False), score=a["score"],
                 ai_error="", ai_at=comms.ts_now())
            moved += 1
        except (comms.CommsError, ValueError) as e:
            _fail(con, call, str(e), retry=isinstance(e, comms.CommsError))
    return moved


def start_worker(connect):
    def loop():
        con = connect()
        while True:
            try:
                tick(con)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            try:
                from . import calldigest
                con.execute("BEGIN IMMEDIATE")
                try:
                    calldigest.tick(con)
                    con.execute("COMMIT")
                except BaseException:
                    con.execute("ROLLBACK")
                    raise
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(60)
    threading.Thread(target=loop, name="ai-worker", daemon=True).start()


# ---------------- для интерфейса ----------------

def call_ai(con, cid: int) -> dict:
    r = con.execute("SELECT id, ai_status, ai_error, transcript, analysis, score FROM calls WHERE id=?", (cid,)).fetchone()
    if not r:
        raise comms.CommsError("Звонок не найден")
    d = dict(r)
    d["analysis"] = json.loads(d["analysis"]) if d["analysis"] else None
    d["checklist_labels"] = dict(CHECKLIST)
    return d


def retry(con, cid: int):
    con.execute("UPDATE calls SET ai_status='', ai_op='', ai_error='', ai_tries=0 WHERE id=?", (cid,))


def team_stats(con, start: str, end: str) -> dict:
    """Средние оценки и выполнение чек-листа по операторам за период."""
    names = {r[0]: r[1] for r in con.execute("SELECT sip_number, name FROM users WHERE sip_number != ''")}
    rows = [dict(r) for r in con.execute(
        """SELECT c.id, c.operator, c.started_at, c.phone, c.score, c.analysis, f.name AS family_name FROM calls c
           LEFT JOIN families f ON f.id=c.family_id WHERE c.ai_status='done' AND c.started_at BETWEEN ? AND ?
           ORDER BY c.started_at DESC""", (start, end))]
    ops: dict[str, dict] = {}
    team = {k: [0, 0] for k, _ in CHECKLIST}
    recent = []
    for r in rows:
        a = json.loads(r["analysis"] or "{}")
        o = ops.setdefault(r["operator"] or "?", {"number": r["operator"], "name": names.get(r["operator"], f"Номер {r['operator']}"),
                                                    "calls": 0, "scores": [], "steps": {k: [0, 0] for k, _ in CHECKLIST}})
        o["calls"] += 1
        if r["score"] is not None:
            o["scores"].append(r["score"])
        for k, v in (a.get("checklist") or {}).items():
            if v is None or k not in team:
                continue
            o["steps"][k][1] += 1
            team[k][1] += 1
            if v:
                o["steps"][k][0] += 1
                team[k][0] += 1
        if len(recent) < 30:
            recent.append({"id": r["id"], "started_at": r["started_at"], "phone": r["phone"], "family_name": r["family_name"],
                           "operator": o["name"], "score": r["score"], "result": a.get("result"), "summary": a.get("summary")})
    pct = lambda x: round(100 * x[0] / x[1]) if x[1] else None
    op_list = []
    for o in ops.values():
        op_list.append({"number": o["number"], "name": o["name"], "calls": o["calls"],
                        "avg": round(sum(o["scores"]) / len(o["scores"]), 1) if o["scores"] else None,
                        "steps": {k: pct(v) for k, v in o["steps"].items()}})
    op_list.sort(key=lambda o: -(o["avg"] or 0))
    team_pct = {k: pct(v) for k, v in team.items()}
    # самый слабый шаг; при равенстве - тот, что ближе к продаже (потеря там дороже)
    order = {k: i for i, (k, _) in enumerate(CHECKLIST)}
    known = [(v, -order[k], k) for k, v in team_pct.items() if v is not None]
    weakest = min(known)[2] if known else None
    pending = con.execute("SELECT COUNT(*) FROM calls WHERE ai_status IN ('stt','llm')").fetchone()[0]
    return {"checklist": [{"key": k, "title": t} for k, t in CHECKLIST], "operators": op_list, "team": team_pct,
            "weakest": weakest, "recent": recent, "analyzed": len(rows), "pending": pending,
            "enabled": enabled(con)}


DEMO_TRANSCRIPT = """[00:00] Канал 1: Футболика, здравствуйте, меня зовут Оля, слушаю вас
[00:03] Канал 2: Здравствуйте, я по поводу Миши, он в пятницу не сможет, заболел
[00:07] Канал 1: Поняла, отмечу. Скорейшего выздоровления! Хотите, запишу на отработку в субботу в 14:00 на Небе?
[00:14] Канал 2: А можно в следующую субботу?
[00:17] Канал 1: Да, конечно, записала на субботу 7 ноября в 14:00. Накануне напомню сообщением
[00:24] Канал 2: Спасибо большое
[00:26] Канал 1: Всего доброго, выздоравливайте"""

DEMO_ANALYSIS = {
    "summary": "Мама сообщила, что Миша заболел и пропустит пятницу. Администратор сразу предложила отработку и записала на субботу 7 ноября",
    "result": "другое", "score": 9,
    "checklist": {"hello": True, "child": None, "needs": None, "place": True, "value": None, "trial": None,
                  "objection": None, "next": True},
    "objections": [], "next_step": "Отработка в субботу 7 ноября в 14:00, напомнить накануне",
    "good": "Не просто отметила пропуск, а сразу предложила отработку с конкретным временем",
    "tips": ["Можно уточнить, всё ли в порядке с оплатой следующего месяца: «Кстати, продление до 25 числа, напомнить вам?»"],
    "child": {"name": "Миша", "age": "", "branch": "Небо", "time": "суббота 14:00"},
}


def seed_demo(con):
    """Тестовый режим: пример готового разбора на выдуманном звонке."""
    r = con.execute("SELECT id, analysis FROM calls WHERE external_id='demo-call-2'").fetchone()
    if r and not r[1]:
        con.execute("UPDATE calls SET ai_status='done', transcript=?, analysis=?, score=? WHERE id=?",
                    (DEMO_TRANSCRIPT, json.dumps(DEMO_ANALYSIS, ensure_ascii=False), DEMO_ANALYSIS["score"], r[0]))
