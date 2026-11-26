"""Approved templates rendered into drafts; branch rules always require a matching picture."""
import base64
import json
import hashlib
import re
from datetime import datetime
from urllib.parse import urlsplit
from . import comms, tasks, memos

PROFILE_NAMES={'nebo':'ТРЦ «Небо»','western':'СК «Западный»','arctic':'Арктика','toddlers':'ТРЦ «Небо», группа 1,5–3 года'}
MAX_CHANNELS={'nebo':'https://max.ru/id183210983596_biz','western':'https://max.ru/id183210983596_biz3'}
LEGACY_NEBO_OFFERS = (
    'https://msk.footbolika.ru/assets/images/offer/1846/dgw2m3.pdf',
    'https://msk.footbolika.ru/assets/images/offer/1846/j1k2f6.pdf',
)
NEBO_OFFER = 'https://msk.footbolika.ru/assets/images/offer/1846/vzaxum.pdf'
OFFERS={'nebo':NEBO_OFFER,'western':'https://msk.footbolika.ru/assets/images/offer/3867/8of3k6.pdf','arctic':'https://msk.footbolika.ru/assets/images/offer/4005/2zqspv.pdf'}
RULES_TEXT='''❗️ Обязательно подпишитесь на канал «Футболики»
👉 {channel}

Именно в канале мы публикуем важные новости {branch}, анонсы турниров и других мероприятий. Подпишитесь и включите уведомления, чтобы не пропустить объявления и запись на события ⚽️

Добро пожаловать в нашу большую зелёную семью 💚

👆🏼 Собрали правила абонемента в удобную памятку. Пожалуйста, прочитайте её и сохраните, чтобы условия оплаты, отработок и переносов всегда были под рукой. Правила пропусков зависят от выбранного тарифа

📄 Полные условия занятий и оплаты можно посмотреть в оферте
{offer}

Если остались вопросы, напишите нам, поможем разобраться 🤗{contact}

До встречи на тренировках!
С любовью, Футболика ⚽️💚'''


def migrate(con):
    try:saved=json.loads(comms.get(con,'school_rules_profiles') or '{}')
    except (ValueError,TypeError):saved={}
    changed=False
    for key in ('nebo','toddlers'):
        value=saved.get(key)
        if isinstance(value, dict) and value.get('offer') in LEGACY_NEBO_OFFERS:
            value['offer'] = NEBO_OFFER
            changed = True
    if changed:comms.put(con,'school_rules_profiles',json.dumps(saved,ensure_ascii=False))
    return saved


def profiles(con):
    saved=migrate(con)
    out={}
    for key,label in PROFILE_NAMES.items():
        base='nebo' if key=='toddlers' else key
        v=saved.get(key,{})
        out[key]={'name':label,'telegram':v.get('telegram','https://t.me/footbolika_deti' if base=='nebo' else ''),'max':v.get('max',MAX_CHANNELS.get(base,'')),'offer':v.get('offer',OFFERS[base]),'text':v.get('text',RULES_TEXT)}
    # Малыши используют каналы Неба. Оферта при необходимости задаётся отдельно.
    out['toddlers']['telegram']=out['nebo']['telegram'];out['toddlers']['max']=out['nebo']['max']
    return out


def save_profiles(con,values):
    if not isinstance(values,dict):raise comms.CommsError('Ожидается список комплектов правил')
    old=profiles(con)
    for key,v in values.items():
        if key not in PROFILE_NAMES or not isinstance(v,dict):raise comms.CommsError('Неизвестный комплект правил')
        for field in ('telegram','max','offer','text'):
            if field not in v:continue
            value=str(v[field] or '').strip()
            if len(value)>(6000 if field=='text' else 1000):raise comms.CommsError('Слишком длинное значение')
            if value and field!='text':
                parsed=urlsplit(value)
                hosts={'telegram':{'t.me'},'max':{'max.ru','web.max.ru'},'offer':{'msk.footbolika.ru'}}[field]
                if parsed.scheme!='https' or parsed.hostname not in hosts or parsed.username or parsed.password:raise comms.CommsError('Проверьте HTTPS-ссылку на канал или оферту')
            old[key][field]=value
    comms.put(con,'school_rules_profiles',json.dumps(old,ensure_ascii=False))


def group_for(con,sid,gid=None):
    s=con.execute('SELECT * FROM students WHERE id=?',(sid,)).fetchone()
    if not s:raise comms.CommsError('Ребёнок не найден')
    rows=[dict(r) for r in con.execute('''SELECT g.*,b.name AS branch FROM enrollments e JOIN groups g ON g.id=e.group_id JOIN branches b ON b.id=g.branch_id WHERE e.student_id=? AND e.active=1 AND g.is_makeup=0 ORDER BY g.id''',(sid,))]
    if not rows and s['group_id']:
        rows=[dict(r) for r in con.execute('SELECT g.*,b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id WHERE g.id=?',(s['group_id'],))]
    if gid:return dict(s),next((g for g in rows if g['id']==gid),None),rows
    # Арктика+ОФП — один комплект сборной; две независимые группы требуют выбора.
    arena=[g for g in rows if g.get('program_id') and g.get('lesson_kind')=='arena']
    selected=arena[0] if len(arena)==1 and all(g.get('program_id')==arena[0]['program_id'] for g in rows) else rows[0] if len(rows)==1 else None
    return dict(s),selected,rows


def rule_profile(con,g):
    if not g:return None
    if g.get('program_id'):
        p=con.execute('SELECT slug FROM programs WHERE id=?',(g['program_id'],)).fetchone()
        return 'arctic' if p and p['slug']=='sbornaya' else None
    name=g['branch'].casefold()
    if 'небо' in name:
        from . import groupterms
        if groupterms.terms(con,g['id'],'2026-11'):return 'toddlers'
        age=re.sub(r'\s+','',str(g.get('age') or '')).replace(',','.').replace('–','-').replace('—','-')
        return 'toddlers' if age in ('1.5-3','1,5-3') else 'nebo'
    if 'запад' in name or 'чобот' in name:return 'western'
    return None


def rules(con,sid,channel,gid=None):
    s,g,groups=group_for(con,sid,gid);key=rule_profile(con,g)
    reason='Выберите группу ребёнка' if not g else 'Для этой группы комплект правил не настроен' if not key else ''
    if reason:return {'key':'rules','title':'Правила школы','text':'','reason':reason,'groups':[{'id':x['id'],'name':x['name']} for x in groups]}
    p=profiles(con)[key];target='max' if channel.endswith('max') else 'telegram'
    link=p[target]
    without_channel=key=='arctic' and target=='max' and not link
    if not channel:reason='Выберите мессенджер'
    elif not link and not without_channel:reason='Не задан канал '+('MAX' if target=='max' else 'Telegram')+' для '+p['name']
    elif not p['offer']:reason='Не задана оферта для '+p['name']
    contact=('\nНаписать нам в MAX: '+tasks.admin_link(con,channel)) if channel.endswith('max') else ''
    template=p['text']
    if without_channel:
        # The user confirmed no Arctic MAX channel exists; omit only the invitation.
        template=template.replace(RULES_TEXT.split('\n\nДобро пожаловать',1)[0],'').lstrip()
        template='\n\n'.join(part for part in template.split('\n\n') if '{channel}' not in part and not part.startswith('Именно в канале'))
    text=template.replace('{contact}',contact).replace('{channel}',link).replace('{branch}',('сборной «Арктика»' if key=='arctic' else 'филиала '+('«Небо»' if key in ('nebo','toddlers') else 'СК «Западный»'))).replace('{offer}',p['offer'])
    if without_channel and tasks.admin_link(con,channel) not in text:text+=contact
    memo_key=memos.key_for_group(con,g)
    image=memos.image(con,memo_key) if memo_key else None
    if not image and not reason:reason='Актуальная картинка правил ещё формируется. Откройте скрипты повторно'
    if re.search(r'\{[a-z_]+\}',text) and not reason:reason='В тексте остались незаполненные поля'
    return {'key':'rules','title':'Правила школы · '+p['name'],'text':text,'reason':reason,'profile':key,'image':('/api/memos/png?key='+memo_key+'&h='+hashlib.sha256(image).hexdigest()[:16]) if image else '', 'groups':[{'id':x['id'],'name':x['name']} for x in groups], 'group_id':g['id']}


def catalog(con,sid=None,channel='',gid=None):
    s=dict(con.execute('SELECT * FROM students WHERE id=?',(sid,)).fetchone() or {}) if sid else {}
    if sid and not s:raise comms.CommsError('Ребёнок не найден')
    try:t=datetime.fromisoformat(s.get('trial_at') or '')
    except ValueError:t=None
    values={'hello':'Добрый день!','admin':tasks.admin_link(con,channel)}
    if s:values['child']=s['name'].split()[0]
    if t:values.update(date=t.strftime('%d.%m'),time=t.strftime('%H:%M'),place=tasks._place_full(con,s))
    items=[]
    titles={'msg_after':'Как прошло пробное','msg_missed':'Пропущенный звонок','msg_booked':'Записан на пробное','msg_day_before':'Напоминание о пробном завтра','msg_2h_before':'Напоминание о пробном сегодня'}
    for key,title in titles.items():
        reason=''
        try:text=(tasks.booked_text(con,s,channel) if key=='msg_booked' else tasks.render_template(con,key,s,channel)) if t else tasks.setting(con,key)
        except comms.CommsError as error:text='';reason=str(error)
        for field,value in values.items():text=text.replace('{'+field+'}',value)
        missing=re.findall(r'\{[a-z_]+\}',text)
        reason=reason or ('Сначала выберите ребёнка' if not s and missing else 'В карточке не указаны данные пробного занятия' if missing else '')
        items.append({'key':key,'title':title,'text':text,'reason':reason})
    if sid:items.insert(0,rules(con,sid,channel,gid))
    return {'items':items}


def stage_rules(con,cid,user,sid,gid=None):
    from . import chatattachments
    c=con.execute('SELECT * FROM conversations WHERE id=?',(cid,)).fetchone()
    s=con.execute('SELECT family_id FROM students WHERE id=?',(sid,)).fetchone()
    if not c or not s or c['family_id']!=s['family_id']:raise comms.CommsError('Чат не связан с семьёй выбранного ребёнка')
    rule=rules(con,sid,c['channel'],gid)
    if rule['reason']:raise comms.CommsError(rule['reason'])
    _,g,_=group_for(con,sid,rule['group_id']);png=memos.image(con,memos.key_for_group(con,g))
    file=chatattachments.upload(con,cid,user,'Правила школы.png',base64.b64encode(png).decode())
    con.execute('UPDATE chat_attachments SET rules_json=? WHERE id=?',(json.dumps({'student_id':sid,'group_id':rule['group_id'],'text':rule['text'],'image_hash':hashlib.sha256(png).hexdigest()},ensure_ascii=False),file['id']))
    return file|{'text':rule['text'],'profile':rule['profile']}
