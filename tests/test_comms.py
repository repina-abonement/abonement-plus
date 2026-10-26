"""Проверки связи: заявки с сайта, Sipuni, Telegram, MAX, Wazzup. Запуск: python -m tests.test_comms

Поднимает настоящий HTTP-сервер на временной базе, внешние сервисы подменены.
"""
import json
import os
import sys
import tempfile
import threading
import urllib.request
from http.cookiejar import CookieJar

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["CRM_DB"] = os.path.join(tempfile.mkdtemp(), "c.sqlite3")
os.environ["CRM_ADMIN_PASSWORD"] = "admin-pass-1"
os.environ["CRM_DEMO"] = "1"
os.environ["CRM_QUIET"] = "1"

from crm import comms, db, server  # noqa: E402
from tests.test_crm import check  # noqa: E402

SENT = []


def fake_http(method, url, *, data=None, form=None, headers=None, timeout=12, raw=False):
    SENT.append({"method": method, "url": url, "data": data, "form": form, "headers": headers or {}})
    if raw:
        return 200, b"0123456789" * 10, "audio/mpeg"
    if "api.telegram.org" in url:
        if url.endswith("/getMe"):
            return 200, {"ok": True, "result": {"username": "school_bot"}}
        return 200, {"ok": True, "result": {"message_id": 900 + len(SENT)}}
    if "max.ru" in url:
        if url.endswith("/me"):
            return 200, {"name": "Школа бот"}
        if url.endswith("/subscriptions") and method == "GET":
            return 200, {"subscriptions": []}
        return 200, {"success": True, "message": {"body": {"mid": f"mid.{len(SENT)}"}}}
    if "wazzup" in url:
        if url.endswith("/channels"):
            return 200, [{"channelId": "ch-1", "transport": "whatsapp", "state": "active", "plainId": "79990000000"}]
        return 201, {"messageId": f"wz-{len(SENT)}"}
    if "sipuni" in url:
        return 200, {"success": True}
    return 404, {}


comms.http = fake_http


def main():
    con = db.connect()
    db.init_db(con)
    db.seed_demo(con)
    comms.seed_demo(con)
    con.close()
    srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def call(method, path, body=None, headers=None, expect=200, raw=False):
        data = json.dumps(body).encode() if body is not None else None
        h = {"Content-Type": "application/json"} | (headers or {})
        req = urllib.request.Request(base + path, data=data, method=method, headers=h)
        try:
            with opener.open(req) as r:
                content, code = r.read(), r.status
        except urllib.error.HTTPError as e:
            content, code = e.read(), e.code
        assert code == expect, f"{method} {path}: {code} {content[:300]}"
        return content if raw else json.loads(content or b"null")

    call("POST", "/api/login", {"login": "admin", "password": "admin-pass-1"})
    integ = call("GET", "/api/integrations")
    lead_url = integ["hooks"]["lead"].split("127.0.0.1:" + str(srv.server_port))[1]
    sip_url = integ["hooks"]["sipuni"].split("127.0.0.1:" + str(srv.server_port))[1]
    tok = lead_url.rsplit("/", 1)[1]

    print("Заявки с сайта")
    call("POST", "/hooks/lead/wrong-token", {"phone": "+79001234567"}, expect=403)
    r = call("POST", lead_url, {"id": "gmail-1", "name": "Светлана", "phone": "+7 (912) 345-67-89", "city": "Москва",
                                "branch": "Небо"})
    sid = r["student_id"]
    board = call("GET", "/api/board/sales")
    first = board["columns"][0]["cards"][0]
    check(first["id"] == sid and first["color"] == "red", "Заявка — первая карточка в «Новый лид», красная")
    check("Заявка с сайта" in first["badge"], "На карточке видно, что это заявка с сайта")
    r2 = call("POST", lead_url, {"id": "gmail-1", "name": "Светлана", "phone": "+7 (912) 345-67-89"})
    check(r2.get("duplicate"), "То же письмо второй раз не создаёт дубль")
    r3 = call("POST", lead_url, {"id": "gmail-2", "name": "Светлана", "phone": "89123456789"})
    check(r3["student_id"] == sid, "Повторная заявка с того же телефона поднимает старую карточку")
    call("POST", lead_url, {"id": "gmail-3", "name": "Кто-то", "phone": "—"}, expect=400)
    check(True, "Заявка без телефона отклоняется")
    call("POST", f"/api/students/{sid}/note", {"text": "Дозвонились, записали на пробное"})
    card = [k for c in call("GET", "/api/board/sales")["columns"] for k in c["cards"] if k["id"] == sid][0]
    check(card["color"] != "red", "После комментария карточка уже не красная")

    print("Sipuni")
    call("PUT", "/api/integrations", {"sipuni_user": "064629", "sipuni_secret": "0.secret"})
    me = call("GET", "/api/meta")["user"]
    call("POST", "/api/call", {"phone": "+7 900 111-11-11"}, expect=400)
    call("PATCH", f"/api/users/{me['id']}", {"sip_number": "201"})
    call("POST", "/api/call", {"phone": "+7 900 111-11-11"})
    f = SENT[-1]["form"]
    import hashlib
    check(f["hash"] == hashlib.md5("0+79001111111+0+201+064629+0.secret".encode()).hexdigest(),
          "Звонок из карточки: подпись Sipuni верная")
    q = f"?event=1&call_id=C1&src_num=79123456789&src_type=1&dst_num=201&dst_type=2&timestamp=1793000000"
    call("GET", sip_url + q)
    call("GET", sip_url + "?event=3&call_id=C1&src_num=79123456789&src_type=1&dst_num=201&dst_type=2&timestamp=1793000010")
    call("GET", sip_url + "?event=2&call_id=C1&src_num=79123456789&src_type=1&dst_num=201&dst_type=2&status=ANSWER"
         "&timestamp=1793000070&call_record_link=https%3A%2F%2Fsipuni.com%2Frec%2F1")
    c = call("GET", "/api/calls")
    c1 = [x for x in c if x["external_id"] == "C1"][0]
    check(c1["status"] == "answered" and c1["talk"] == 60 and c1["has_record"], "Входящий звонок: принят, 60 сек, есть запись")
    check(c1["family_id"] is not None, "Звонок сам привязался к семье по телефону")
    audio = call("GET", f"/api/calls/{c1['id']}/record", raw=True, headers={"Range": "bytes=0-9"}, expect=206)
    check(audio == b"0123456789", "Запись звонка отдаётся кусками (для iPhone)")
    call("POST", sip_url, {"event": "2", "call_id": "C2", "src_num": "79990001122", "src_type": "1",
                           "dst_num": "201", "dst_type": "2", "status": "NOANSWER", "timestamp": "1793000100"})
    check(call("GET", "/api/inbox/counts")["missed"] >= 1, "Пропущенный звонок попал в счётчик")
    wd = call("GET", "/api/workdesk")
    check(any(x["kind"] == "missed_call" and x["level"] == "red" for x in wd["urgent"]), "Пропущенный звонок красным на рабочем столе")
    miss = [x for x in wd["urgent"] if x["kind"] == "missed_call"][0]
    call("POST", f"/api/calls/{miss['id']}/seen", {})
    check(all(x.get("id") != miss["id"] for x in call("GET", "/api/workdesk")["urgent"] if x["kind"] == "missed_call"),
          "Кнопка «Обработан» убирает звонок с рабочего стола")

    print("Telegram")
    call("PUT", "/api/integrations", {"tg_token": "123:abc"})
    call("POST", "/api/integrations/telegram/connect")
    wh = [s for s in SENT if s["url"].endswith("/setWebhook")][-1]["data"]
    check(wh["url"].endswith(f"/hooks/telegram/{tok}") and "business_message" in wh["allowed_updates"],
          "Вебхук Telegram поставлен, бизнес-сообщения включены")
    secret = wh["secret_token"]
    call("POST", f"/hooks/telegram/{tok}", {"message": {}}, expect=403)
    upd = {"update_id": 1, "message": {"message_id": 5, "date": 1793000000, "text": "/start",
                                        "chat": {"id": 777, "type": "private", "first_name": "Ольга"},
                                        "from": {"id": 777}}}
    call("POST", f"/hooks/telegram/{tok}", upd, headers={"X-Telegram-Bot-Api-Secret-Token": secret})
    check(SENT[-1]["data"]["reply_markup"]["keyboard"][0][0]["request_contact"], "На /start бот просит номер")
    upd = {"update_id": 2, "message": {"message_id": 6, "date": 1793000005,
                                        "contact": {"phone_number": "+79001111111", "user_id": 777},
                                        "chat": {"id": 777, "type": "private", "first_name": "Ольга"},
                                        "from": {"id": 777}}}
    call("POST", f"/hooks/telegram/{tok}", upd, headers={"X-Telegram-Bot-Api-Secret-Token": secret})
    conv = [x for x in call("GET", "/api/inbox")["conversations"] if x["external_id"] == "777"][0]
    check(conv["family_name"] == "Ивановы", "После «Поделиться номером» переписка привязалась к семье")
    call("POST", f"/api/conversations/{conv['id']}/send", {"text": "Здравствуйте!"})
    check(SENT[-1]["data"] == {"chat_id": 777, "text": "Здравствуйте!"}, "Ответ из CRM ушёл в Telegram")
    biz = {"update_id": 3, "business_message": {"message_id": 8, "date": 1793000100, "text": "Есть места?",
                                                 "business_connection_id": "BC1",
                                                 "chat": {"id": 555, "type": "private", "first_name": "Ира"},
                                                 "from": {"id": 555}}}
    call("POST", f"/hooks/telegram/{tok}", biz, headers={"X-Telegram-Bot-Api-Secret-Token": secret})
    conv2 = [x for x in call("GET", "/api/inbox")["conversations"] if x["external_id"] == "555"][0]
    call("POST", f"/api/conversations/{conv2['id']}/send", {"text": "Да, есть"})
    check(SENT[-1]["data"].get("business_connection_id") == "BC1", "Ответ в бизнес-аккаунт уходит от имени школы")

    print("MAX")
    call("PUT", "/api/integrations", {"max_token": "maxtok"})
    call("POST", "/api/integrations/max/connect")
    sub = [s for s in SENT if s["url"].endswith("/subscriptions") and s["method"] == "POST"][-1]["data"]
    upd = {"update_type": "message_created", "timestamp": 1793000200000, "message": {
        "sender": {"user_id": 42, "first_name": "Марина", "is_bot": False},
        "recipient": {"chat_id": 9001, "chat_type": "dialog"},
        "body": {"mid": "mid.a", "text": "Привет", "attachments": [
            {"type": "contact", "payload": {"vcf_info": "BEGIN:VCARD\nTEL;TYPE=cell:79005555555\nEND:VCARD"}}]}}}
    call("POST", f"/hooks/max/{tok}", upd, headers={"X-Max-Bot-Api-Secret": sub["secret"]})
    conv3 = [x for x in call("GET", "/api/inbox")["conversations"] if x["channel"] == "max" and x["external_id"] == "9001"][0]
    check(conv3["family_name"] == "Смирновы", "MAX: контакт из сообщения привязал переписку к семье")
    call("POST", f"/api/conversations/{conv3['id']}/send", {"text": "Добрый день"})
    check("chat_id=9001" in SENT[-1]["url"], "Ответ ушёл в MAX")

    print("WhatsApp через Wazzup")
    call("PUT", "/api/integrations", {"wazzup_key": "wzkey"})
    call("POST", "/api/integrations/wazzup/connect")
    check(call("GET", "/api/meta")["comms"]["whatsapp"], "Канал WhatsApp найден")
    call("POST", f"/hooks/wazzup/{tok}", {"test": True})
    call("POST", f"/hooks/wazzup/{tok}", {"messages": [{"messageId": "w1", "channelId": "ch-1", "chatType": "whatsapp",
                                                         "chatId": "79002222222", "type": "text", "isEcho": False,
                                                         "text": "Здравствуйте", "contact": {"name": "Олег"},
                                                         "dateTime": "2026-10-26T20:00:00.000"}]})
    conv4 = [x for x in call("GET", "/api/inbox")["conversations"] if x["channel"] == "whatsapp"][0]
    check(conv4["family_name"] == "Петровы" and conv4["unread"] == 1, "WhatsApp: входящее привязалось к семье, непрочитано")
    fam = call("GET", f"/api/students/{call('GET', '/api/students?q=%D0%A2%D0%B8%D0%BC%D1%83%D1%80')[0]['id']}")["family"]["id"]
    comm = call("GET", f"/api/families/{fam}/comms")
    check(any(i["type"] == "msg" and i["text"] == "Здравствуйте" for i in comm["items"]), "Переписка видна в карточке")
    parent = call("GET", f"/api/students/{call('GET', '/api/students?q=%D0%A2%D0%B8%D0%BC%D1%83%D1%80')[0]['id']}")["parents"][0]["id"]
    call("POST", f"/api/families/{fam}/whatsapp", {"parent_id": parent, "text": "Напоминаем про оплату"})
    check(SENT[-1]["data"]["chatId"] == "79002222222" and SENT[-1]["data"]["channelId"] == "ch-1",
          "Сообщение в WhatsApp из карточки ушло через Wazzup")

    print("Права")
    call("POST", "/api/logout", {})
    call("POST", "/api/login", {"login": "trener", "password": "trener-test-1"})
    call("GET", "/api/inbox", expect=403)
    check(True, "Тренер не видит переписки и звонки")
    b = call("GET", "/api/coaches/board")
    check(b["hero"] and any(r["is_me"] for r in b["rows"]), "Тренер видит своё место в таблице")
    check("revenue" not in json.dumps(b) and "price" not in json.dumps(b), "В результатах тренеров нет денег")
    srv.shutdown()


if __name__ == "__main__":
    main()
    print("\nВсе проверки связи пройдены")
