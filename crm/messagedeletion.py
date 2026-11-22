"""Explicit remote deletion through Wazzup, with persistent state and an audit trail."""
from datetime import datetime,timedelta
from urllib.parse import quote
from . import comms


def init(con):
    con.execute("""CREATE TABLE IF NOT EXISTS message_deletions(message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,state TEXT NOT NULL,user_id INTEGER,error TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL)""")


def local_only(message):
    return message['direction']=='out' and message.get('status') in ('error','demo') and not message.get('external_id')


def reason(message,channel):
    if message['direction']!='out':return 'Можно удалить только отправленное сообщение'
    if local_only(message):return ''
    if channel not in comms.WZ_ROUTE:return 'Удалите сообщение в приложении этого мессенджера'
    if not message.get('external_id') or message.get('status') in ('error','demo'):return 'Нет подтверждённого сообщения в мессенджере'
    try:
        sent=datetime.fromisoformat(message['ts'].replace('Z','+00:00'))
        now=datetime.now(sent.tzinfo) if sent.tzinfo else datetime.now()
        if now-sent>=timedelta(hours=24):return 'После отправки прошло больше 24 часов'
        if sent>now+timedelta(minutes=5):return 'Не удалось подтвердить время отправки'
    except (ValueError,TypeError):return 'Не удалось подтвердить время отправки'
    return ''


def public(con,message,channel):
    row=con.execute('SELECT state FROM message_deletions WHERE message_id=?',(message['id'],)).fetchone()
    state=row[0] if row else ''
    return dict(message)|{'deletion_status':state,'can_delete':not state and not reason(dict(message),channel),'delete_reason':reason(dict(message),channel),'local_delete':local_only(dict(message))}


def preview(con,cid,mid,text):
    latest=con.execute('SELECT id FROM messages WHERE conversation_id=? ORDER BY ts DESC,id DESC LIMIT 1',(cid,)).fetchone()
    if latest and latest[0]==mid:con.execute('UPDATE conversations SET last_text=? WHERE id=?',(text,cid))


def refresh_preview(con,cid):
    latest=con.execute('SELECT direction,text,kind,ts FROM messages WHERE conversation_id=? ORDER BY ts DESC,id DESC LIMIT 1',(cid,)).fetchone()
    if not latest:
        con.execute("UPDATE conversations SET last_at=NULL,last_text='' WHERE id=?",(cid,));return
    text=(latest['text'] or '').strip().replace('\n',' ')[:120] or comms.KIND_LABELS.get(latest['kind'],'')
    con.execute('UPDATE conversations SET last_at=?,last_text=? WHERE id=?',(latest['ts'],('Вы: ' if latest['direction']=='out' else '')+text,cid))


def delete(con,mid,user):
    if user.get('role') not in ('admin','operator'):raise comms.CommsError('Нет доступа к удалению сообщений')
    row=con.execute('SELECT m.*,c.channel FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE m.id=?',(mid,)).fetchone()
    if not row:raise comms.CommsError('Сообщение не найдено')
    message=dict(row);existing=con.execute('SELECT state FROM message_deletions WHERE message_id=?',(mid,)).fetchone()
    if existing:
        return {'ok':existing[0] in ('requested','deleted'),'state':existing[0],'already_requested':True,'error':'Предыдущий запрос уже выполнялся — проверьте сообщение в мессенджере'}
    if local_only(message):
        from . import services
        services.audit(con,user,'message.delete_local',f'message={mid};conversation={message["conversation_id"]};status={message["status"]}')
        con.execute('DELETE FROM messages WHERE id=?',(mid,))
        refresh_preview(con,message['conversation_id'])
        return {'ok':True,'state':'deleted','local':True}
    why=reason(message,message['channel'])
    if why:raise comms.CommsError(why)
    if not comms.get(con,'wazzup_key'):raise comms.CommsError('Wazzup не подключён')
    # The request transaction serializes concurrent clicks. Errors return normally so
    # its uncertain state is committed and a lost acknowledgement is never retried.
    con.execute("INSERT INTO message_deletions(message_id,state,user_id,updated_at) VALUES (?,'pending',?,?)",(mid,user['id'],comms.ts_now()))
    from . import services
    services.audit(con,user,'message.delete_request',f'message={mid};conversation={message["conversation_id"]}')
    try:comms.wz_call(con,'DELETE','message/'+quote(str(message['external_id']),safe=''))
    except Exception as error:
        detail=str(error) if isinstance(error,comms.CommsError) else 'Нет подтверждения удаления — проверьте сообщение в мессенджере'
        con.execute("UPDATE message_deletions SET state='uncertain',error=?,updated_at=? WHERE message_id=?",(detail,comms.ts_now(),mid))
        return {'ok':False,'state':'uncertain','error':detail}
    con.execute("UPDATE message_deletions SET state='requested',updated_at=? WHERE message_id=?",(comms.ts_now(),mid))
    preview(con,message['conversation_id'],mid,'Вы: удаление сообщения запрошено')
    return {'ok':True,'state':'requested'}


def confirmed(con,cid,external_id):
    row=con.execute('SELECT id FROM messages WHERE conversation_id=? AND external_id=?',(cid,str(external_id))).fetchone()
    if not row:return
    con.execute("INSERT INTO message_deletions(message_id,state,updated_at) VALUES (?,'deleted',?) ON CONFLICT(message_id) DO UPDATE SET state='deleted',error='',updated_at=excluded.updated_at",(row[0],comms.ts_now()))
    preview(con,cid,row[0],'Сообщение удалено')


def merge(con,source,target):
    """Keep deletion state when an imported echo is deduplicated during chat merging."""
    a=con.execute('SELECT * FROM message_deletions WHERE message_id=?',(source,)).fetchone()
    b=con.execute('SELECT * FROM message_deletions WHERE message_id=?',(target,)).fetchone()
    rank={'pending':1,'uncertain':2,'requested':3,'deleted':4}
    if a and (not b or rank[a['state']]>rank[b['state']]):
        con.execute('INSERT INTO message_deletions(message_id,state,user_id,error,updated_at) VALUES (?,?,?,?,?) ON CONFLICT(message_id) DO UPDATE SET state=excluded.state,user_id=excluded.user_id,error=excluded.error,updated_at=excluded.updated_at',(target,a['state'],a['user_id'],a['error'],a['updated_at']))
