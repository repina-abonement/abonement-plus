"""Inbox presentation, manual unread and personal chat preferences."""
import re
from . import comms


def init(con):
    con.execute('CREATE INDEX IF NOT EXISTS idx_msg_reply ON messages(conversation_id,direction,ts,id)')
    con.execute('''CREATE TABLE IF NOT EXISTS conversation_preferences(user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,muted INTEGER NOT NULL DEFAULT 0,archived INTEGER NOT NULL DEFAULT 0,archived_after INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(user_id,conversation_id))''')


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
    return {'ok':True,'reply_overdue':False}


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
