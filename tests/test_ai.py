"""Проверки расшифровки и разбора звонков. Запуск: python -m tests.test_ai"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import ai, comms, db  # noqa: E402
from tests.test_crm import check  # noqa: E402

CALLS = []
STREAM = "\n".join(json.dumps({"result": x}, ensure_ascii=False) for x in [
    {"channelTag": "0", "final": {"alternatives": [{"text": "футболика здравствуйте меня зовут оля", "startTimeMs": "0"}]}},
    {"channelTag": "0", "finalRefinement": {"finalIndex": "0", "normalizedText": {"alternatives": [
        {"text": "Футболика, здравствуйте! Меня зовут Оля", "startTimeMs": "0"}]}}},
    {"channelTag": "1", "final": {"alternatives": [{"text": "Здравствуйте, хочу записать сына на пробное", "startTimeMs": "3500"}]}},
    {"channelTag": "0", "final": {"alternatives": [{"text": "Сколько лет ребёнку?", "startTimeMs": "7000"}]}},
])
ANSWER = json.dumps({"summary": "Мама хочет записать сына", "result": "записали на пробное", "score": "8",
                     "checklist": {"hello": True, "child": True, "trial": True, "objection": None},
                     "objections": "цена", "next_step": "Пробное в среду", "good": "Чётко", "tips": ["Назови цену позже"],
                     "child": {"name": "", "age": "5", "branch": "Небо", "time": ""}}, ensure_ascii=False)
STATE = {"stt_done": False, "llm_done": False}


def fake(method, url, *, data=None, form=None, headers=None, timeout=12, raw=False):
    CALLS.append((method, url, data, headers))
    if raw and "getRecognition" in url:
        return 200, STREAM.encode(), "application/json"
    if raw:
        return 200, b"ID3fake-mp3", "audio/mpeg"
    if url.endswith("recognizeFileAsync"):
        return 200, {"id": "stt-op-1", "done": False}
    if url.endswith("completionAsync"):
        return 200, {"id": "llm-op-1", "done": False}
    if "/operations/stt-op-1" in url:
        return 200, {"id": "stt-op-1", "done": STATE["stt_done"]}
    if "/operations/llm-op-1" in url:
        return 200, {"id": "llm-op-1", "done": STATE["llm_done"],
                     "response": {"alternatives": [{"message": {"role": "assistant", "text": "```json\n" + ANSWER + "\n```"}}]}}
    return 404, {}


def main():
    comms.http = fake
    con = db.connect(os.path.join(tempfile.mkdtemp(), "a.sqlite3"))
    db.init_db(con)
    print("Конвейер разбора звонков")
    con.execute("""INSERT INTO calls(external_id, direction, phone, phone_norm, operator, status, started_at, talk, record_url)
                   VALUES ('X1','out','79001112233','79001112233','201','answered',datetime('now','localtime'),95,'https://sipuni.com/r/1')""")
    con.execute("""INSERT INTO calls(external_id, direction, phone, phone_norm, operator, status, started_at, talk, record_url)
                   VALUES ('X2','out','79001112234','79001112234','201','answered',datetime('now','localtime'),8,'https://sipuni.com/r/2')""")
    check(ai.tick(con) == 0, "Пока не включено в настройках, ничего не отправляется")
    comms.put(con, "yc_api_key", "AQVN-test")
    comms.put(con, "yc_folder", "b1gfolder")
    comms.put(con, "ai_enabled", "1")
    ai.tick(con)
    st = dict(con.execute("SELECT * FROM calls WHERE external_id='X1'").fetchone())
    check(st["ai_status"] == "stt", "Разговор отправлен на расшифровку")
    req = [c for c in CALLS if c[1].endswith("recognizeFileAsync")][0]
    check(req[2]["recognitionModel"]["model"] == "deferred-general" and req[3]["Authorization"] == "Api-Key AQVN-test",
          "По умолчанию дешёвый отложенный режим, ключ в заголовке")
    check(con.execute("SELECT ai_status FROM calls WHERE external_id='X2'").fetchone()[0] == "",
          "Короткий звонок (8 сек) не расшифровывается")
    ai.tick(con)
    check(con.execute("SELECT ai_status FROM calls WHERE external_id='X1'").fetchone()[0] == "stt", "Ждём, пока Яндекс распознает")
    STATE["stt_done"] = True
    ai.tick(con)
    st = dict(con.execute("SELECT * FROM calls WHERE external_id='X1'").fetchone())
    check(st["ai_status"] == "llm" and "Канал 1: Футболика, здравствуйте!" in st["transcript"],
          "Текст собран по каналам, с нормализацией")
    check(st["transcript"].splitlines()[1].startswith("[00:03] Канал 2"), "Реплики по времени: [00:03] родитель")
    llm = [c for c in CALLS if c[1].endswith("completionAsync")][0]
    check(llm[2]["modelUri"] == "gpt://b1gfolder/yandexgpt/latest", "Разбор идёт в YandexGPT Pro по каталогу")
    STATE["llm_done"] = True
    ai.tick(con)
    d = ai.call_ai(con, st["id"])
    check(d["ai_status"] == "done" and d["score"] == 8, "Разбор сохранён, оценка 8 из 10")
    check(d["analysis"]["checklist"]["trial"] is True and d["analysis"]["checklist"]["value"] is None,
          "Чек-лист: пробное предложено, неупомянутые пункты пустые")
    check(d["analysis"]["objections"] == ["цена"], "Возражения приведены к списку")
    ts = ai.team_stats(con, "2000-01-01", "2100-01-01")
    check(ts["analyzed"] == 1 and ts["team"]["trial"] == 100, "Сводка по команде считается")


if __name__ == "__main__":
    main()
    print("\nВсе проверки разбора пройдены")
