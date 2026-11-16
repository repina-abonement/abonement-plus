"""Выбранные сотрудником файлы: сначала черновик, отдельное явное действие отправки."""
import base64,binascii,json,re,secrets,hashlib
from datetime import datetime,timedelta,timezone
from urllib.parse import quote,urlsplit
from urllib.request import Request,urlopen
from . import comms

MAX_BYTES=5*1024*1024
EXTENSIONS={'jpg','jpeg','png','webp','pdf','txt','csv','doc','docx','xls','xlsx','ppt','pptx','odt','ods','zip'}


def init(con):
    con.execute('''CREATE TABLE IF NOT EXISTS chat_attachments(id TEXT PRIMARY KEY,conversation_id INTEGER NOT NULL REFERENCES conversations(id),user_id INTEGER NOT NULL REFERENCES users(id),filename TEXT NOT NULL,mime TEXT NOT NULL,data BLOB NOT NULL,ticket TEXT NOT NULL UNIQUE,expires_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'draft',message_id INTEGER,error TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,rules_json TEXT NOT NULL DEFAULT '')''')
    if 'rules_json' not in {r[1] for r in con.execute('PRAGMA table_info(chat_attachments)')}:con.execute("ALTER TABLE chat_attachments ADD COLUMN rules_json TEXT NOT NULL DEFAULT ''")


def _conversation(con,cid):
    r=con.execute('SELECT * FROM conversations WHERE id=?',(cid,)).fetchone()
    if not r:raise comms.CommsError('Переписка не найдена')
    if r['channel'] not in ('telegram','whatsapp','wazzup_telegram','wazzup_max','max'):raise comms.CommsError('В этот чат нельзя отправлять файлы')
    return dict(r)


def upload(con,cid,user,filename,encoded):
    conv=_conversation(con,cid)
    name=re.sub(r'[\x00-\x1f\x7f]','',str(filename or '').replace('\\','/').rsplit('/',1)[-1]).strip()[:180]
    if '.' not in name or name.rsplit('.',1)[-1].lower() not in EXTENSIONS:raise comms.CommsError('Выберите фото, PDF или офисный документ; этот тип файла не поддерживается')
    if not isinstance(encoded,str) or len(encoded)>7_100_000:raise comms.CommsError('Файл должен быть не больше 5 МБ')
    try:data=base64.b64decode(encoded,validate=True)
    except (ValueError,binascii.Error):raise comms.CommsError('Не удалось прочитать файл') from None
    if not data or len(data)>MAX_BYTES:raise comms.CommsError('Выберите непустой файл размером до 5 МБ')
    ext=name.rsplit('.',1)[-1].lower();mime='application/octet-stream'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):mime='image/png'
    elif data.startswith(b'\xff\xd8\xff'):mime='image/jpeg'
    elif data[:4]==b'RIFF' and data[8:12]==b'WEBP':mime='image/webp'
    elif ext in ('jpg','jpeg','png','webp'):raise comms.CommsError('Файл не похож на фотографию выбранного формата')
    elif ext=='pdf':
        if not data.startswith(b'%PDF-'):raise comms.CommsError('Файл не похож на PDF')
        mime='application/pdf'
    now=datetime.now(timezone.utc);aid=secrets.token_hex(16)
    con.execute('INSERT INTO chat_attachments(id,conversation_id,user_id,filename,mime,data,ticket,expires_at,created_at) VALUES (?,?,?,?,?,?,?,?,?)',(aid,cid,user['id'],name,mime,data,secrets.token_urlsafe(32),(now+timedelta(hours=24)).isoformat(),now.isoformat()))
    return {'id':aid,'filename':name,'size':len(data)}


def public_file(con,ticket):
    row=con.execute('SELECT * FROM chat_attachments WHERE ticket=?',(ticket,)).fetchone()
    if not row or datetime.fromisoformat(row['expires_at'])<=datetime.now(timezone.utc):raise comms.CommsError('Файл недоступен')
    return bytes(row['data']),row['mime'],row['filename']


def local_file(con,aid):
    r=con.execute('SELECT data,mime FROM chat_attachments WHERE id=?',(aid,)).fetchone()
    if not r:raise comms.CommsError('Вложение не найдено')
    return bytes(r['data']),r['mime'] if r['mime'].startswith('image/') else 'application/octet-stream'


def send(con,cid,aid,user,caption=''):
    conv=_conversation(con,cid)
    r=con.execute('SELECT * FROM chat_attachments WHERE id=? AND conversation_id=? AND user_id=?',(aid,cid,user['id'])).fetchone()
    if not r:raise comms.CommsError('Выбранный файл не относится к этому чату или сотруднику')
    if r['status']=='sent':return {'ok':True,'id':r['message_id'],'already_sent':True}
    if r['status']!='draft':raise comms.CommsError('Предыдущая отправка уже выполнялась. Проверьте доставку в мессенджере; повтор автоматически не выполняется')
    if datetime.fromisoformat(r['expires_at'])<=datetime.now(timezone.utc):raise comms.CommsError('Выберите файл заново: срок черновика истёк')
    if r['rules_json']:
        from . import chatscripts,memos
        meta=json.loads(r['rules_json'])
        family=con.execute('SELECT family_id FROM students WHERE id=?',(meta['student_id'],)).fetchone()
        if not family or family[0]!=conv['family_id']:raise comms.CommsError('Связь чата с семьёй изменилась. Выберите скрипт заново')
        current=chatscripts.rules(con,meta['student_id'],conv['channel'],meta['group_id'])
        _,group,_=chatscripts.group_for(con,meta['student_id'],meta['group_id'])
        png=memos.image(con,memos.key_for_group(con,group)) if group else None
        if current['reason'] or current['text']!=meta['text'] or not png or hashlib.sha256(png).hexdigest()!=meta['image_hash']:raise comms.CommsError('Правила или ссылки изменились. Выберите скрипт заново перед отправкой')
    caption=str(caption or '').strip()
    if len(caption)>1024:raise comms.CommsError('Подпись к файлу должна быть не длиннее 1024 символов')
    base=(comms.get(con,'public_url') or 'https://ab-plus.ru').rstrip('/')
    url=urlsplit(base)
    if url.scheme!='https' or not url.netloc or url.username or url.password or url.query or url.fragment:raise comms.CommsError('Для файлов нужен публичный HTTPS-адрес CRM в настройках подключения')
    media_url=base+'/api/chat-files/'+r['ticket']+'/'+quote(r['filename'],safe='')
    con.execute("UPDATE chat_attachments SET status='sending' WHERE id=?",(aid,))
    photo=r['mime'] in ('image/png','image/jpeg');kind='image' if r['mime'].startswith('image/') else 'file'
    try:
        if conv['external_id'].startswith('demo-'):ext='demo-attachment-'+aid;demo=True
        elif conv['channel']=='telegram':
            payload={'chat_id':int(conv['external_id']),'photo' if photo else 'document':media_url}
            if caption:payload['caption']=caption
            meta=json.loads(conv['meta'] or '{}')
            if meta.get('biz'):payload['business_connection_id']=meta['biz']
            ext=str(comms.tg_call(con,'sendPhoto' if photo else 'sendDocument',payload,timeout=60)['message_id']);demo=False
        elif conv['channel']=='max':
            attachment={'type':'image','payload':{'url':media_url}} if kind=='image' else {'type':'file','payload':{'token':_max_upload_document(con,r)}}
            body={'attachments':[attachment]}
            if caption:body['text']=caption
            response=comms.max_call(con,'POST','messages',query={'chat_id':conv['external_id']},data=body)
            ext=(((response or {}).get('message') or {}).get('body') or {}).get('mid')
            if not ext:raise comms.CommsError('MAX не подтвердил отправку')
            demo=False
        else:
            return _send_wazzup_bundle(con,conv,r,user,caption,kind,media_url)
    except Exception as error:
        detail=str(error) if isinstance(error,comms.CommsError) else 'Не удалось подтвердить отправку файла'
        mid=comms.add_message(con,cid,'out',caption or r['filename'],kind=kind,media='attachment:'+aid,user_name=user.get('name',''),status='error',error=detail)
        con.execute("UPDATE chat_attachments SET status='failed',error=?,message_id=? WHERE id=?",(detail,mid,aid))
        return {'ok':False,'error':detail,'uncertain':True,'id':mid}
    mid=comms.add_message(con,cid,'out',caption or r['filename'],kind=kind,media='attachment:'+aid,user_name=user.get('name',''),external_id=ext,status='demo' if demo else 'sent')
    con.execute("UPDATE chat_attachments SET status='sent',message_id=? WHERE id=?",(mid,aid))
    return {'ok':True,'id':mid,'demo':demo}


def _max_upload_document(con,r):
    """MAX documents use a signed multipart upload URL, never a file URL in sendMessage."""
    result=comms.max_call(con,'POST','uploads',query={'type':'file'})
    url=str((result or {}).get('url') or '');parsed=urlsplit(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:raise comms.CommsError('MAX не предоставил адрес загрузки документа')
    boundary='crmfile'+secrets.token_hex(16)
    name=re.sub(r'["\\\r\n]','_',r['filename'])
    body=(f'--{boundary}\r\nContent-Disposition: form-data; name="data"; filename="{name}"\r\nContent-Type: {r["mime"]}\r\n\r\n'.encode()+bytes(r['data'])+f'\r\n--{boundary}--\r\n'.encode())
    # Upload URL is signed by MAX. Never forward the bot token to the upload host.
    request=Request(url,data=body,headers={'Content-Type':'multipart/form-data; boundary='+boundary},method='POST')
    try:
        with urlopen(request,timeout=60) as response:data=json.loads(response.read(1_000_000))
    except Exception:raise comms.CommsError('MAX: не удалось загрузить документ') from None
    token=(data or {}).get('token')
    if not isinstance(token,str) or not token:raise comms.CommsError('MAX не подтвердил загрузку документа')
    return token


def _send_wazzup_bundle(con,conv,attachment,user,text,kind,url):
    """Wazzup v3 accepts contentUri OR text. Record each confirmed part separately."""
    aid=attachment['id'];cid=conv['id']
    destination=comms._wz_destination(con,conv)
    destination.update(comms._wz_recipient(conv))
    result=comms.wz_call(con,'POST','message',destination|{'contentUri':url,'crmMessageId':aid}) or {}
    external=comms._wz_ack(con,conv,result)
    if result.get('chatId'):
        destination.pop('phone',None);destination.pop('username',None);destination['chatId']=str(result['chatId'])
    mid=comms.add_message(con,cid,'out',attachment['filename'],kind=kind,media='attachment:'+aid,user_name=user.get('name',''),external_id=external,status='sent')
    text_mid=None
    if text:
        try:
            result=comms.wz_call(con,'POST','message',destination|{'text':text,'crmMessageId':aid+'-text'}) or {}
            external_text=result.get('messageId')
            if not external_text:raise comms.CommsError('Wazzup не подтвердил отправку текста')
            text_mid=comms.add_message(con,cid,'out',text,user_name=user.get('name',''),external_id=external_text,status='sent')
        except Exception as error:
            detail=str(error) if isinstance(error,comms.CommsError) else 'Нет подтверждения отправки текста'
            comms.add_message(con,cid,'out',text,user_name=user.get('name',''),status='error',error=detail)
            con.execute("UPDATE chat_attachments SET status='partial',message_id=?,error=? WHERE id=?",(mid,detail,aid))
            return {'ok':False,'partial':True,'file_sent':True,'uncertain':True,'id':mid,'error':'Файл отправлен, отправка текста не подтверждена. Проверьте доставку текста перед повтором'}
    con.execute("UPDATE chat_attachments SET status='sent',message_id=? WHERE id=?",(mid,aid))
    return {'ok':True,'id':mid,'text_id':text_mid,'demo':False}
