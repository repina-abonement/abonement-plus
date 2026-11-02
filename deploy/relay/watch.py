#!/usr/bin/env python3
"""Сторож CRM на мосту в Нидерландах (Юлия, 02.11: «как избежать падения, как с ERP»).

CRM раз в минуту присылает сюда сигнал «жива» вместе с ботом и чатами руководителя (POST /watch/beat, только с адреса
сервера CRM). Сторож раз в минуту сам открывает CRM. Если сигнала нет 4 минуты или CRM не открывается 3 раза подряд,
руководителю в Telegram: «🔴 CRM не отвечает», дальше напоминание раз в 30 минут; когда заработала: «✅ CRM снова работает».
Бот шлёт напрямую в Telegram (отсюда он доступен), поэтому сообщение дойдёт, даже если сервер CRM лежит целиком.
"""
import json
import os
import ssl
import threading
import time
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = "/var/lib/crm-watch/state.json"
CRM_URL = os.environ.get("CRM_URL", "https://185-207-65-245.sslip.io/")
BEAT_MIN, FAILS, REPEAT_MIN = 4, 3, 30
lock = threading.Lock()


def load():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(s):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, STATE)


def tg(s, text):
    tok = s.get("token")
    for chat in s.get("chats") or []:
        try:
            req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage",
                                         data=json.dumps({"chat_id": chat, "text": text}).encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=20).read()
        except Exception as e:  # noqa: BLE001
            print("telegram:", e, flush=True)


def site_ok():
    try:
        with urllib.request.urlopen(CRM_URL, timeout=20, context=ssl.create_default_context()) as r:
            return r.status < 500
    except Exception:  # noqa: BLE001
        return False


def loop():
    fails = 0
    while True:
        time.sleep(60)
        ok_site = site_ok()
        fails = 0 if ok_site else fails + 1
        with lock:
            s = load()
            if not s.get("token"):
                continue
            beat_age = (time.time() - s.get("beat", 0)) / 60
            down = beat_age > BEAT_MIN or fails >= FAILS
            now = time.time()
            if down:
                if not s.get("down_since"):
                    s["down_since"] = now
                    why = "не открывается" if fails >= FAILS else f"не присылает сигнал {int(beat_age)} мин"
                    tg(s, f"🔴 CRM не отвечает ({why}), с {datetime.now():%H:%M}. Если это надолго: сервер Timeweb, "
                          f"проверьте баланс и статус. Инструкция на случай аварии в документе «Всё о CRM»")
                    s["last_alert"] = now
                elif now - s.get("last_alert", 0) > REPEAT_MIN * 60:
                    tg(s, f"🔴 CRM всё ещё не отвечает, уже {int((now - s['down_since']) / 60)} мин")
                    s["last_alert"] = now
            elif s.get("down_since"):
                tg(s, f"✅ CRM снова работает. Не работала {int((now - s['down_since']) / 60)} мин")
                s["down_since"] = None
            save(s)


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path.rstrip("/") != "/watch/beat":
            self.send_response(404); self.end_headers(); return
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 20000)) or b"{}")
        except ValueError:
            body = {}
        with lock:
            s = load()
            s["beat"] = time.time()
            if body.get("token"):
                s["token"] = str(body["token"])
            if isinstance(body.get("chats"), list):
                s["chats"] = [str(c) for c in body["chats"]][:10]
            save(s)
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")

    def do_GET(self):
        s = load()
        self.send_response(200); self.end_headers()
        self.wfile.write(json.dumps({"beat_min_ago": round((time.time() - s.get("beat", 0)) / 60, 1),
                                     "down_since": s.get("down_since"), "chats": len(s.get("chats") or [])}).encode())

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", 8099), H).serve_forever()
