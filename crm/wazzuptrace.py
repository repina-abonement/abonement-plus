"""Read-only investigation of one provider message; never infers a missing webhook."""
import json
import re
from . import comms


def trace(con, message_id, chat_id='', channel_id=''):
    message_id=str(message_id or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',message_id):
        raise comms.CommsError('Укажите идентификатор сообщения Wazzup')
    chat_id=str(chat_id or '').strip();channel_id=str(channel_id or '').strip()
    result={'message_id':message_id,'messages':[],'events':[],'unreadable_candidates':[],
            'replay_hook_ids':[], 'chat_events':[], 'log_limit':300,
            'scope':'Сохранённые сообщения и последние 300 событий Wazzup. Отсутствие события в этом окне не доказывает отсутствие доставки'}
    rows=con.execute("""SELECT m.id,m.conversation_id,m.ts,m.direction,m.kind,c.channel,c.external_id
        FROM messages m JOIN conversations c ON c.id=m.conversation_id
        WHERE m.external_id=? AND c.channel IN ('whatsapp','wazzup_telegram','wazzup_max') ORDER BY m.id""",(message_id,)).fetchall()
    for row in rows:
        d={k:row[k] for k in ('id','conversation_id','ts','direction','kind','channel')}
        d['chat_id_matches']=row['external_id']==chat_id if chat_id else None
        view=comms.conversation(con,row['conversation_id'],mark_read=False)
        d['in_current_history']=any(m.get('type')=='msg' and m.get('id')==row['id'] for m in view['history'])
        d['in_latest_200']=any(m.get('id')==row['id'] for m in view['messages'])
        deletion=con.execute('SELECT state FROM message_deletions WHERE message_id=?',(row['id'],)).fetchone()
        d['deletion_state']=deletion[0] if deletion else ''
        result['messages'].append(d)
    hooks=con.execute("SELECT id,ts,payload,error FROM hook_log WHERE source='wazzup' ORDER BY id DESC LIMIT 300").fetchall()
    result['logs_scanned']=len(hooks)
    for event in hooks:
        try:
            payload=json.loads(event['payload'])
            if not isinstance(payload,dict):continue
        except (ValueError,TypeError):
            if message_id in event['payload']:
                result['unreadable_candidates'].append({'hook_id':event['id'],'at':event['ts']})
            continue
        for category in ('messages','statuses'):
            items=payload.get(category)
            if not isinstance(items,list):continue
            for item in items:
                if not isinstance(item,dict):continue
                if category=='messages' and chat_id and str(item.get('chatId') or '')==chat_id and len(result['chat_events'])<20:
                    candidate=str(item.get('messageId') or '')
                    stamp=str(item.get('dateTime') or '')
                    result['chat_events'].append({'hook_id':event['id'],'at':event['ts'],
                        'message_id':candidate if re.fullmatch(r'[A-Za-z0-9_-]{1,128}',candidate) else '',
                        'provider_time':stamp if re.fullmatch(r'[0-9TZ:+. -]{10,35}',stamp) else '',
                        'is_echo':bool(item.get('isEcho')),
                        'channel_id_matches':str(item.get('channelId') or '')==channel_id if channel_id else None})
                if str(item.get('messageId') or '')!=message_id:continue
                ct={'maxapi':'max','tgapi':'telegram','wapi':'whatsapp'}.get(item.get('chatType'),item.get('chatType'))
                d={'hook_id':event['id'],'at':event['ts'],'category':category,'failed':bool(event['error']),
                   'chat_type':ct if ct in ('max','telegram','whatsapp') else 'unknown',
                   'chat_id_matches':str(item.get('chatId') or '')==chat_id if chat_id else None,
                   'channel_id_matches':str(item.get('channelId') or '')==channel_id if channel_id else None,
                   'is_echo':bool(item.get('isEcho')),'is_deleted':item.get('isDeleted') is True}
                result['events'].append(d)
                if (not rows and category=='messages' and ct=='max' and chat_id and d['chat_id_matches']
                        and (not channel_id or d['channel_id_matches']) and not d['is_echo'] and not d['is_deleted']):
                    result['replay_hook_ids'].append(event['id'])
    result['replay_hook_ids']=sorted(set(result['replay_hook_ids']))
    result['incoming_message_events']=sum(e['category']=='messages' and not e['is_echo'] and not e['is_deleted'] for e in result['events'])
    result['outgoing_echo_events']=sum(e['category']=='messages' and e['is_echo'] for e in result['events'])
    result['state']='stored' if rows else 'event_found_not_stored' if any(e['category']=='messages' for e in result['events']) else 'not_observed_in_retained_log'
    return result


def webhook_status(con, public_base):
    """Official v3 GET /webhooks, without returning the secret destination URI."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from urllib.parse import urlsplit
    result={'configured':bool(comms.get(con,'wazzup_key')),'available':False,
            'checked_at':datetime.now(ZoneInfo('Europe/Moscow')).isoformat(timespec='seconds'),
            'note':'Текущие настройки не подтверждают доставку исторического сообщения'}
    if not result['configured']:
        result['error']='Wazzup не подключён'
        return result
    try:
        data=comms.wz_call(con,'GET','webhooks')
        if not isinstance(data,dict) or not isinstance(data.get('webhooksUri'),str) or not isinstance(data.get('subscriptions'),dict):
            raise ValueError('Unexpected webhook configuration')
        actual=data['webhooksUri'];token=comms.get(con,'hook_token')
        parts=urlsplit(actual)
        if parts.scheme not in ('https','http') or not parts.hostname:
            raise ValueError('Invalid webhook URL')
        enabled=data['subscriptions'].get('messagesAndStatuses')
        if not isinstance(enabled,bool) and enabled not in ('true','false'):
            raise ValueError('Unverified subscription')
        result.update(available=True,destination_host=parts.hostname,
            destination_matches=bool(token) and actual==public_base.rstrip('/')+'/hooks/wazzup/'+token,
            messages_and_statuses=enabled is True or enabled=='true')
    except Exception:
        result['error']='Не удалось проверить текущие настройки доставки Wazzup'
    return result
