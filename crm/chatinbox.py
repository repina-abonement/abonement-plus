"""Inbox presentation, five-minute unanswered queue and personal chat preferences."""
import re
from datetime import datetime,timedelta
from . import comms

REPLY_SECONDS=300
LIVE_CHANNELS=('telegram','max','whatsapp','wazzup_telegram','wazzup_max')


def init(con):
    con.execute('CREATE INDEX IF NOT EXISTS idx_msg_reply ON messages(conversation_id,direction,ts,id)')
    con.execute('''CREATE TABLE IF NOT EXISTS conversation_preferences(user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,muted INTEGER NOT NULL DEFAULT 0,archived INTEGER NOT NULL DEFAULT 0,archived_after INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(user_id,conversation_id))''')


def waiting(con,now=None):
    # Read-only: opening/settings requests must not compete for the SQLite writer.
    rows=con.execute('''WITH answers AS (
      SELECT m.conversation_id,m.ts,m.id,ROW_NUMBER() OVER(PARTITION BY m.conversation_id ORDER BY m.ts DESC,m.id DESC) AS rank
      FROM messages m WHERE m.direction='out' AND m.status NOT IN ('error','demo') AND m.user_name NOT IN ('Автосообщение','Автоцепочка','Бот')
      AND NOT EXISTS(SELECT 1 FROM message_deletions d WHERE d.message_id=m.id AND d.state='deleted')
    ), incoming AS (
      SELECT m.conversation_id,m.ts,m.id,ROW_NUMBER() OVER(PARTITION BY m.conversation_id ORDER BY m.ts,m.id) AS rank
      FROM messages m JOIN conversations c ON c.id=m.conversation_id
      LEFT JOIN answers a ON a.conversation_id=m.conversation_id AND a.rank=1
      WHERE m.direction='in' AND m.status NOT IN ('error','demo')
      AND c.channel IN ('telegram','max','whatsapp','wazzup_telegram','wazzup_max') AND c.external_id NOT LIKE 'demo-%'
      AND (a.id IS NULL OR (m.ts,m.id)>(a.ts,a.id))
      AND NOT EXISTS(SELECT 1 FROM message_deletions d WHERE d.message_id=m.id AND d.state='deleted')
    ) SELECT conversation_id,ts FROM incoming WHERE rank=1''')
    result={}
    for row in rows:
        try:
            first=datetime.fromisoformat(row['ts'].replace('Z','+00:00'))
            current=now or datetime.now(first.tzinfo)
            if current.tzinfo and not first.tzinfo:current=current.replace(tzinfo=None)
            if not current.tzinfo and first.tzinfo:current=current.replace(tzinfo=first.tzinfo)
            if current-first>=timedelta(seconds=REPLY_SECONDS):result[row['conversation_id']]=row['ts']
        except (ValueError,TypeError):continue
    return result


def preferences(con,user_id):
    return {r['conversation_id']:dict(r) for r in con.execute('SELECT * FROM conversation_preferences WHERE user_id=?',(user_id,))}


def archived(con,c,pref):
    if not pref.get('archived'):return False
    # Like Telegram: a new incoming brings an unmuted archived chat back.
    return bool(pref.get('muted') or not con.execute("SELECT 1 FROM messages WHERE conversation_id=? AND direction='in' AND id>? LIMIT 1",(c['id'],pref.get('archived_after',0))).fetchone())


def set_preference(con,cid,user,body):
    if not con.execute('SELECT 1 FROM conversations WHERE id=?',(cid,)).fetchone():raise comms.CommsError('Чат не найден')
    fields={k:body[k] for k in ('muted','archived') if k in body}
    if not fields or any(not isinstance(v,bool) for v in fields.values()):raise comms.CommsError('Укажите действие с чатом')
    con.execute('INSERT OR IGNORE INTO conversation_preferences(user_id,conversation_id) VALUES (?,?)',(user['id'],cid))
    for key,value in fields.items():con.execute(f'UPDATE conversation_preferences SET {key}=? WHERE user_id=? AND conversation_id=?',(int(value),user['id'],cid))
    if fields.get('archived'):
        last=con.execute('SELECT COALESCE(MAX(id),0) FROM messages WHERE conversation_id=?',(cid,)).fetchone()[0]
        con.execute('UPDATE conversation_preferences SET archived_after=? WHERE user_id=? AND conversation_id=?',(last,user['id'],cid))
    return {'ok':True}


def mark_unread(con,cid,user):
    if not con.execute('SELECT 1 FROM conversations WHERE id=?',(cid,)).fetchone():raise comms.CommsError('Чат не найден')
    con.execute('UPDATE conversations SET unread=MAX(unread,1) WHERE id=?',(cid,))
    from . import services
    services.audit(con,user,'conversation.mark_unread',str(cid))
    return {'ok':True,'unread':con.execute('SELECT unread FROM conversations WHERE id=?',(cid,)).fetchone()[0]}


def mark_read(con,cid):
    if not con.execute('SELECT 1 FROM conversations WHERE id=?',(cid,)).fetchone():raise comms.CommsError('Чат не найден')
    con.execute('UPDATE conversations SET unread=0 WHERE id=?',(cid,))
    return {'ok':True,'reply_overdue':cid in waiting(con)}


def norm_name(value):return ' '.join(sorted(re.findall(r'[a-zа-яё]+',str(value or '').casefold().replace('ё','е'))))


def named_family(con,title):
    name=norm_name(title)
    if len(name.split())<2:return None
    rows=con.execute('SELECT family_id,name FROM parents UNION ALL SELECT family_id,name FROM students WHERE archived=0')
    families={r['family_id'] for r in rows if norm_name(r['name'])==name}
    return next(iter(families)) if len(families)==1 else None


def display_family(con,c,cache=None):
    if c.get('family_id'):return c['family_id']
    strong=comms.identity_family(con,c['channel'],c.get('phone_norm',''),c.get('username',''))
    if strong:return strong
    # A name must not override a conflicting/ambiguous phone or messenger identity.
    if c.get('phone_norm') and con.execute('SELECT 1 FROM parents WHERE phone_norm=?',(c['phone_norm'],)).fetchone():return None
    if c.get('username') and any(comms.telegram_username(r[0])==comms.telegram_username(c['username']) for r in con.execute("SELECT messenger FROM parents WHERE messenger!=''")):return None
    if cache is None:return named_family(con,c.get('title'))
    name=norm_name(c.get('title'))
    if len(name.split())<2:return None
    if 'names' not in cache:
        cache['names']={}
        for r in con.execute('SELECT family_id,name FROM parents UNION ALL SELECT family_id,name FROM students WHERE archived=0'):
            cache['names'].setdefault(norm_name(r['name']),set()).add(r['family_id'])
    families=cache['names'].get(name,set())
    return next(iter(families)) if len(families)==1 else None


def display_title(con,c,students):
    parents=[dict(r) for r in con.execute('SELECT * FROM parents WHERE family_id=? ORDER BY id',(c['family_id'],))] if c.get('family_id') else []
    exact=[p for p in parents if c.get('phone_norm') and p['phone_norm']==c['phone_norm']]
    if not exact and c.get('username'):
        exact=[p for p in parents if comms.telegram_username(p['messenger'])==comms.telegram_username(c['username'])]
    if not exact:exact=[p for p in parents if norm_name(p['name']) and norm_name(p['name'])==norm_name(c.get('title'))]
    parent=exact[0] if len(exact)==1 else parents[0] if len(parents)==1 else None
    children=[]
    for child in students:
        parts=child['name'].strip().split()
        name=' '.join(parts[1:]+parts[:1]) if len(parts)>1 else child['name']
        if name not in children:children.append(name)
    if not children:return c.get('title') or c.get('username') or c.get('phone') or 'Без имени'
    label=' / '.join(children)
    if parent:
        label+=' · '+parent['name']
        label+=', '+(parent['relation'] or 'родитель')
    return label
