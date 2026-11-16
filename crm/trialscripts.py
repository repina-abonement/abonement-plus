"""Confirmed branch scripts; dates and messenger links come from the actual booking."""
from datetime import datetime
from zoneinfo import ZoneInfo

DEFAULTS={
'msg_booked_western':'''{hello} 💚

Ваш Чемпион записан на пробное занятие в «Футболике» ⚽️

📅 {date_time}
📍 СК «Западный», ул. Чоботовская, 4с2

У входа в спорткомплекс вас встретят наши тренеры. Вы узнаете их по зелёным футболкам 💚

❗️ Обязательно присоединитесь к группе для родителей филиала в Новопеределкино, чтобы не пропустить важные новости, объявления и анонсы мероприятий
👉 {channel}

👜 Возьмите с собой
- шорты, футболку и носки
- чистую сменную спортивную обувь
- бутылку воды

🎁 В день пробного занятия вы можете приобрести абонемент со скидкой 20%

Если появятся вопросы, напишите нам, будем рады помочь 🤗

До встречи на тренировке! ⚽️💚''',
'msg_2h_before_western':'''{hello} 🤗

Напоминаем, что сегодня в {time} у вашего Чемпиона пробное занятие в «Футболике» ⚽️

📍 СК «Западный», ул. Чоботовская, 4с2

У входа в спорткомплекс вас встретят наши тренеры в зелёных футболках 💚

❗️ Обязательно подпишитесь на канал «Футболики», чтобы не пропустить важные новости, анонсы турниров и других мероприятий
👉 {channel}

👜 Возьмите с собой
- шорты, футболку и носки
- чистую сменную спортивную обувь
- бутылку воды

🎁 Сегодня после пробного занятия вы можете приобрести абонемент со скидкой 20%

До встречи на тренировке! ⚽️💚''',
'msg_day_before_nebo':'''{hello} 😊

Напоминаем, что завтра в {time} у вашего Чемпиона пробное занятие в «Футболике» ⚽️

📍 ТРЦ «Небо», ул. Авиаторов, 3А
Мы на 3-м этаже, рядом с центральным лифтом

❗️ Обязательно подпишитесь на канал «Футболики», чтобы не пропустить важные новости, анонсы турниров и других мероприятий
👉 {channel}

👜 Возьмите с собой
- шорты и футболку
- чистую сменную спортивную обувь
- бутылку воды

🎁 В день пробного занятия вы можете приобрести абонемент со скидкой 20%

До встречи на тренировке! ⚽️💚'''}
LABELS={'msg_booked_western':'Западный: запись на пробное','msg_2h_before_western':'Западный: за два часа','msg_day_before_nebo':'Небо: за день'}
AFTER_TEXT='Добрый день\n\nНа связи администратор Оля💚\n\nВы были у нас сегодня на пробном занятии. Подскажите как всё прошло?'


def render(con,key,student,channel):
    from . import chatscripts,tasks,comms
    group=con.execute('SELECT g.branch_id,b.name FROM groups g JOIN branches b ON b.id=g.branch_id WHERE g.id=?',(student.get('group_id') or 0,)).fetchone()
    name=group['name'].casefold() if group else ''
    branch='nebo' if 'небо' in name else 'western' if 'запад' in name or 'чобот' in name else ''
    specific=key+'_'+branch
    if specific not in DEFAULTS:return None
    t=datetime.fromisoformat(student['trial_at'])
    links=chatscripts.profiles(con)[branch]
    target='max' if channel.endswith('max') else 'telegram'
    link=links[target] or tasks.booked_places(con).get(str(group['branch_id']),{}).get('max' if target=='max' else 'tg','')
    if not link:raise comms.CommsError('Не задана ссылка на канал '+('MAX' if target=='max' else 'Telegram')+' филиала в настройках скриптов')
    hour=datetime.now(ZoneInfo('Europe/Moscow')).hour
    hello='Доброе утро!' if 5<=hour<12 else 'Добрый день!' if 12<=hour<18 else 'Добрый вечер!'
    values={'hello':hello,'time':t.strftime('%H:%M'),'date_time':f'{t.day} {tasks.MON_GEN[t.month-1]} в {t.strftime("%H:%M")}','channel':link}
    text=tasks.setting(con,specific)
    for field,value in values.items():text=text.replace('{'+field+'}',value)
    return text
