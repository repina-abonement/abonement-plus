"""Общие наборы стикеров Telegram. Токены бота никогда не возвращаются браузеру."""
import re
import secrets
from urllib.parse import quote
from urllib.request import urlopen
from urllib.error import HTTPError,URLError

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
    direct=False
    def call(method,payload):
        nonlocal direct
        if not direct:
            try:return comms.tg_call(con,method,payload)
            except comms.CommsError as error:
                transport=isinstance(error,comms.TelegramTransportError) or str(error)=='Telegram: ошибка 403'
                if not transport or not comms.get(con,'tg_relay'):raise
                direct=True
        try:
            code,result=comms.http('POST',f"{comms.TG_API}/bot{comms.get(con,'tg_token')}/{method}",data=payload)
        except comms.CommsError:
            raise comms.CommsError('Telegram-мост недоступен или отклонил запрос; прямое чтение Telegram с сервера CRM тоже недоступно. Проверьте сеть и разрешённый IP моста') from None
        if not isinstance(result,dict) or not result.get('ok'):
            raise comms.CommsError('Не удалось прочитать набор напрямую из Telegram после отказа моста. Проверьте сохранённое подключение Telegram')
        return result['result']
    result = call('getStickerSet', {'name': name})
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
        info = call('getFile', {'file_id': media['file_id']})
        path = info.get('file_path') or ''
        if not path or '..' in path or not path.lower().endswith(('.webp', '.png', '.jpg', '.jpeg')):
            continue
        url = f"{comms.TG_API if direct else comms.tg_api(con)}/file/bot{comms.get(con, 'tg_token')}/{quote(path, safe='/')}"
        try:
            with urlopen(url, timeout=20) as response:
                raw = response.read(1_000_001)
        except (HTTPError,URLError,OSError,TimeoutError):
            if direct or not comms.get(con,'tg_relay'):
                raise comms.CommsError('Не удалось скачать стикер с сервера Telegram. Проверьте соединение с сервера CRM') from None
            direct=True
            try:
                with urlopen(f"{comms.TG_API}/file/bot{comms.get(con,'tg_token')}/{quote(path,safe='/')}",timeout=20) as response:
                    raw=response.read(1_000_001)
            except (HTTPError,URLError,OSError,TimeoutError):
                raise comms.CommsError('Не удалось скачать стикер: недоступны и Telegram-мост, и прямое соединение. Проверьте сеть сервера CRM') from None
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


def allowed(con, pack, user):
    """Personal Olga pack: resolve the real account, never grant by caller name/role."""
    title = pack['title'].casefold()
    personal = (pack['name'].casefold() == 'footbolika_olya'
                or any(word in title for word in ('ольг', 'оля', 'olya', 'olga')))
    if not personal:
        return True
    rows = con.execute("SELECT id FROM users WHERE active=1 AND name='Ольга Авлахова'").fetchall()
    return len(rows) == 1 and user is not None and user.get('id') == rows[0]['id']


def catalog(con, user=None):
    return {'packs': [dict(p) | {'stickers': [
        {'id': 'custom:' + r['id'], 'emoji': r['emoji'], 'animated': bool(r['animated']),
         'image': '/api/sticker-image?t=' + r['id']}
        for r in con.execute('SELECT id,emoji,animated FROM custom_stickers WHERE pack=? ORDER BY rowid', (p['name'],))]}
        for p in con.execute('SELECT name,title FROM sticker_packs ORDER BY rowid') if allowed(con,p,user)]}


def upload_image(con, title, encoded):
    """Browser converts supported images to PNG; no Telegram access or credentials."""
    import base64, hashlib, struct, zlib
    from .comms import CommsError
    title = str(title or "").strip()
    if not title or len(title) > 80:
        raise CommsError('Название набора: от 1 до 80 символов')
    if not isinstance(encoded, str) or len(encoded) > 1_400_000:
        raise CommsError('Стикер слишком большой')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise CommsError('Не удалось прочитать изображение') from None
    if len(raw) > 1_000_000 or len(raw) < 45 or raw[:8] != b'\x89PNG\r\n\x1a\n' or raw[12:16] != b'IHDR':
        raise CommsError('Выберите изображение PNG, WEBP, JPG или GIF')
    width, height = struct.unpack('>II', raw[16:24])
    if not (0 < width <= 512 and 0 < height <= 512):
        raise CommsError('Максимальный размер стикера 512 × 512')
    offset = 8; ended = False
    while offset < len(raw):
        if offset + 12 > len(raw): raise CommsError('Изображение повреждено')
        length = struct.unpack('>I', raw[offset:offset+4])[0]
        end = offset + 12 + length
        if end > len(raw) or zlib.crc32(raw[offset+4:end-4]) & 0xffffffff != struct.unpack('>I',raw[end-4:end])[0]:
            raise CommsError('Изображение повреждено')
        if raw[offset+4:offset+8] == b'IEND':
            ended = end == len(raw); break
        offset = end
    if not ended: raise CommsError('Изображение повреждено')
    pack = 'upload_' + hashlib.sha256(title.casefold().encode()).hexdigest()[:24]
    file_id = 'upload:' + hashlib.sha256(raw).hexdigest()
    existing = con.execute('SELECT id FROM custom_stickers WHERE pack=? AND file_id=?', (pack,file_id)).fetchone()
    if existing: return {'id':'custom:'+existing[0], 'added':0, 'name':pack}
    if con.execute('SELECT count(*) FROM custom_stickers WHERE pack=?',(pack,)).fetchone()[0] >= 120:
        raise CommsError('В наборе уже 120 стикеров')
    sticker_id = secrets.token_hex(16)
    con.execute('INSERT INTO sticker_packs(name,title) VALUES (?,?) ON CONFLICT(name) DO NOTHING',(pack,title))
    con.execute('INSERT INTO custom_stickers(id,pack,file_id,emoji,animated,image,mime) VALUES (?,?,?,?,0,?,?)',
                (sticker_id,pack,file_id,'',raw,'image/png'))
    return {'id':'custom:'+sticker_id, 'added':1, 'name':pack}
