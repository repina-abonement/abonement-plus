"""Общие наборы стикеров Telegram. Токены бота никогда не возвращаются браузеру."""
import re
import secrets
from urllib.parse import quote
from urllib.request import urlopen

SCHEMA = """
CREATE TABLE IF NOT EXISTS sticker_packs(name TEXT PRIMARY KEY, title TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS custom_stickers(
 id TEXT PRIMARY KEY, pack TEXT NOT NULL REFERENCES sticker_packs(name),
 file_id TEXT NOT NULL, emoji TEXT NOT NULL DEFAULT '', animated INTEGER NOT NULL DEFAULT 0,
 image BLOB NOT NULL, mime TEXT NOT NULL, UNIQUE(pack,file_id));
"""


def init(con):
    con.executescript(SCHEMA)


def pack_name(value):
    from .comms import CommsError
    match = re.fullmatch(r'(?:https://t\.me/addstickers/)?([A-Za-z0-9_]{1,64})/?', (value or '').strip())
    if not match:
        raise CommsError('Вставьте ссылку вида https://t.me/addstickers/НазваниеНабора')
    return match[1]


def import_pack(con, value):
    from . import comms
    name = pack_name(value)
    result = comms.tg_call(con, 'getStickerSet', {'name': name})
    stickers = result.get('stickers') or []
    if not stickers or len(stickers) > 120:
        raise comms.CommsError('Набор пуст или содержит больше 120 стикеров')
    existing = {r[0] for r in con.execute('SELECT file_id FROM custom_stickers WHERE pack=?', (name,))}
    fetched = []
    for sticker in stickers:
        if sticker['file_id'] in existing:
            continue
        animated = bool(sticker.get('is_animated') or sticker.get('is_video'))
        media = (sticker.get('thumbnail') or sticker.get('thumb')) if animated else sticker
        if not media:
            continue
        info = comms.tg_call(con, 'getFile', {'file_id': media['file_id']})
        path = info.get('file_path') or ''
        if not path or '..' in path or not path.lower().endswith(('.webp', '.png', '.jpg', '.jpeg')):
            continue
        url = f"{comms.tg_api(con)}/file/bot{comms.get(con, 'tg_token')}/{quote(path, safe='/')}"
        try:
            with urlopen(url, timeout=20) as response:
                raw = response.read(1_000_001)
        except Exception:
            raise comms.CommsError('Не удалось скачать стикеры из Telegram. Попробуйте ещё раз') from None
        if len(raw) > 1_000_000:
            raise comms.CommsError('Изображение стикера слишком большое')
        if raw.startswith(b'\x89PNG\r\n\x1a\n'):
            mime = 'image/png'
        elif raw.startswith(b'\xff\xd8\xff'):
            mime = 'image/jpeg'
        elif raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
            mime = 'image/webp'
        else:
            continue
        fetched.append((secrets.token_hex(16), name, sticker['file_id'], sticker.get('emoji') or '', int(animated), raw, mime))
    if not fetched and not existing:
        raise comms.CommsError('В наборе нет доступных изображений для предпросмотра')
    con.execute('INSERT INTO sticker_packs(name,title) VALUES (?,?) ON CONFLICT(name) DO UPDATE SET title=excluded.title',
                (name, result.get('title') or name))
    con.executemany('INSERT INTO custom_stickers(id,pack,file_id,emoji,animated,image,mime) VALUES (?,?,?,?,?,?,?)', fetched)
    return {'name': name, 'added': len(fetched), 'total': len(existing) + len(fetched)}


def catalog(con):
    return {'packs': [dict(p) | {'stickers': [
        {'id': 'custom:' + r['id'], 'emoji': r['emoji'], 'animated': bool(r['animated']),
         'image': '/api/sticker-image?t=' + r['id']}
        for r in con.execute('SELECT id,emoji,animated FROM custom_stickers WHERE pack=? ORDER BY rowid', (p['name'],))]}
        for p in con.execute('SELECT name,title FROM sticker_packs ORDER BY rowid')]}
