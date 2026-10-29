"""Резервные копии базы.

1. Каждую ночь (и по кнопке) копия базы на этом же сервере, последние 14 копий.
2. Если подключён Яндекс Диск: копия сжимается, шифруется паролем (AES-256, openssl) и загружается
   в папку приложения на Диске. На Диске хранятся последние 30 копий. Без пароля шифрования копию не открыть,
   поэтому пароль показывается админу в Настройках, его нужно сохранить отдельно от сервера.

3. Фото (аватарки сотрудников, отчёты тренеров о смене): каждое новое фото один раз шифруется тем же паролем
   и загружается в папку photos на Диске. Список отправленных хранится в настройке backup_photos_sent.

Восстановление (на любом компьютере с openssl):
    openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in crm-ДАТА.sqlite3.gz.enc -out crm.sqlite3.gz -pass pass:ПАРОЛЬ
    gunzip crm.sqlite3.gz      → получится файл базы crm.sqlite3

Запуск службы: python -m crm.backup
"""
import glob
import gzip
import os
import secrets
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime
from urllib.parse import quote

from .db import DB_PATH

DIR = os.environ.get("CRM_BACKUP_DIR", os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "backups"))
KEEP = int(os.environ.get("CRM_BACKUP_KEEP", "14"))
REMOTE_KEEP = 30
YD = "https://cloud-api.yandex.net/v1/disk"


def _get(con, k, d=""):
    r = con.execute("SELECT value FROM integrations WHERE key=?", (k,)).fetchone()
    return r[0] if r else d


def _put(con, k, v):
    con.execute("INSERT INTO integrations(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))


def ensure_key(con) -> str:
    k = _get(con, "backup_key")
    if not k:
        k = "-".join(secrets.token_hex(3) for _ in range(5))
        _put(con, "backup_key", k)
    return k


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


def _yd(con, method, path, token, **kw):
    from . import comms
    code, js = comms.http(method, YD + path, headers={"Authorization": "OAuth " + token}, **kw)
    if code >= 400:
        msg = (js or {}).get("message") if isinstance(js, dict) else None
        raise RuntimeError(f"Яндекс Диск: {msg or code}")
    return js


def upload(con, local: str) -> str:
    """Сжать, зашифровать и загрузить копию на Яндекс Диск. Возвращает имя файла на Диске."""
    token = _get(con, "backup_yadisk_token")
    if not token:
        raise RuntimeError("Яндекс Диск не подключён")
    key = ensure_key(con)
    gz = local + ".gz"
    with open(local, "rb") as f, gzip.open(gz, "wb") as g:
        shutil.copyfileobj(f, g)
    enc = gz + ".enc"
    r = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-salt", "-pbkdf2", "-iter", "200000", "-in", gz, "-out", enc,
                        "-pass", "pass:" + key], capture_output=True, text=True, timeout=300)
    os.remove(gz)
    if r.returncode != 0:
        raise RuntimeError("Не удалось зашифровать копию: " + (r.stderr or "")[:200])
    name = os.path.basename(enc)
    try:
        up = _yd(con, "GET", f"/resources/upload?path={quote('app:/' + name)}&overwrite=true", token)
        with open(enc, "rb") as f:
            data = f.read()
        from urllib.request import Request, urlopen
        with urlopen(Request(up["href"], data=data, method=up.get("method", "PUT")), timeout=600) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"Яндекс Диск не принял файл ({resp.status})")
    finally:
        os.remove(enc)
    # на Диске держим последние REMOTE_KEEP копий
    try:
        lst = _yd(con, "GET", "/resources?path=app:/&limit=200&sort=name", token)
        items = [x["name"] for x in (lst.get("_embedded") or {}).get("items", []) if x["name"].startswith("crm-")]
        for old in sorted(items)[:-REMOTE_KEEP]:
            _yd(con, "DELETE", f"/resources?path={quote('app:/' + old)}&permanently=true", token)
    except Exception:  # noqa: BLE001 — уборка не критична
        pass
    return name


def _encrypt(con, src: str, dst: str) -> None:
    r = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-salt", "-pbkdf2", "-iter", "200000", "-in", src, "-out", dst,
                        "-pass", "pass:" + ensure_key(con)], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError("Не удалось зашифровать фото: " + (r.stderr or "")[:200])


def upload_photos(con, limit: int = 300) -> int:
    """Новые фото на Яндекс Диск, каждое один раз. Аватарку при замене загружаем заново (имя с датой изменения)."""
    import json
    from urllib.request import Request, urlopen
    from .profiles import PHOTO_DIR
    token = _get(con, "backup_yadisk_token")
    if not token or not os.path.isdir(PHOTO_DIR):
        return 0
    sent = set(json.loads(_get(con, "backup_photos_sent") or "[]"))
    todo = []
    for name in sorted(os.listdir(PHOTO_DIR)):
        path = os.path.join(PHOTO_DIR, name)
        key = f"{name}@{int(os.path.getmtime(path))}"
        if os.path.isfile(path) and key not in sent:
            todo.append((name, path, key))
    if not todo:
        return 0
    try:
        _yd(con, "PUT", f"/resources?path={quote('app:/photos')}", token)
    except RuntimeError:
        pass  # папка уже есть
    n = 0
    for name, path, key in todo[:limit]:
        enc = os.path.join(DIR, name + ".enc")
        os.makedirs(DIR, exist_ok=True)
        _encrypt(con, path, enc)
        try:
            remote = f"app:/photos/{key.replace('@', '_')}.enc"
            up = _yd(con, "GET", f"/resources/upload?path={quote(remote)}&overwrite=true", token)
            with open(enc, "rb") as f:
                with urlopen(Request(up["href"], data=f.read(), method=up.get("method", "PUT")), timeout=120) as resp:
                    if resp.status >= 400:
                        raise RuntimeError(f"Яндекс Диск не принял фото ({resp.status})")
        finally:
            os.remove(enc)
        sent.add(key)
        n += 1
    _put(con, "backup_photos_sent", json.dumps(sorted(sent)))
    return n


def run(con=None) -> dict:
    """Сделать копию сейчас: локально и, если подключено, на Яндекс Диск. Статус пишется в настройки."""
    own = con is None
    con = con or sqlite3.connect(DB_PATH)
    out = {"local": None, "remote": None, "error": ""}
    try:
        out["local"] = backup_once()
        _put(con, "backup_last_local", datetime.now().isoformat(timespec="minutes"))
        if _get(con, "backup_yadisk_token"):
            try:
                out["remote"] = upload(con, out["local"])
                out["photos"] = upload_photos(con)
                _put(con, "backup_last_remote", datetime.now().isoformat(timespec="minutes") + " · " + out["remote"]
                     + (f", фото: +{out['photos']}" if out["photos"] else ""))
                _put(con, "backup_last_error", "")
            except Exception as e:  # noqa: BLE001
                out["error"] = str(e)[:300]
                _put(con, "backup_last_error", datetime.now().isoformat(timespec="minutes") + " · " + out["error"])
        if own:
            con.commit()
    finally:
        if own:
            con.close()
    return out


def status(con) -> dict:
    return {"last_local": _get(con, "backup_last_local"), "last_remote": _get(con, "backup_last_remote"),
            "last_error": _get(con, "backup_last_error"), "yadisk": bool(_get(con, "backup_yadisk_token")),
            "key": ensure_key(con), "local_count": len(glob.glob(os.path.join(DIR, "crm-*.sqlite3")))}


if __name__ == "__main__":
    while True:
        if os.path.exists(DB_PATH):
            try:
                r = run()
                print("Копия:", r, flush=True)
            except Exception as e:  # noqa: BLE001
                print("Ошибка копии:", e, flush=True)
        time.sleep(24 * 3600)
