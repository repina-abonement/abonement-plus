const fs = require('fs');
const vm = require('vm');

const app = fs.readFileSync('crm/static/app.js', 'utf8');
const start = app.indexOf('function chatDayPayload');
const end = app.indexOf('\nlet toastTimer;', start);
if (start < 0 || end < 0) throw new Error('chatDayPayload not found');
const context = { dShort: value => value };
vm.createContext(context);
vm.runInContext(app.slice(start, end), context);

const care = {name:'Служба заботы',messages:[
  {id:22889,date:'2026-11-22T20:15:29',text:'Леон Микаладзе ОПЛАТА АБОНЕМЕНТА за ноябрь и декабрь\nГруппа: ср/вс 16:45\nСУММА: 9920 (20% скидка после пу)\nСпособ: карта на филиале'},
  {id:22890,date:'2026-11-22T20:17:12',text:'Денис Мастердеев Оплата абонемента на ноябрь и декабрь\nГруппа: ср/вс 17:45\nСумма: 11880 (20% скидка)\nСпособ: картой на филиале'},
]};
const payload = context.chatDayPayload([care,{name:'ТРЕНЕРЫ',messages:[]}], '2026-11-22');
const sales = payload.events.filter(event => event.event_kind === 'subscription_sale');
if (sales.length !== 2) throw new Error(`Expected two sales, got ${sales.length}`);
const leon = sales.find(event => event.student_name === 'Микаладзе Леон');
const denis = sales.find(event => event.student_name === 'Мастердеев Денис');
if (!leon || leon.amount !== 9920 || leon.components.length !== 2) throw new Error('Leon sale not parsed');
if (!denis || denis.amount !== 11880 || denis.components.length !== 2) throw new Error('Denis sale not parsed');
console.log('PASS chat day confirmed sales in natural and reversed name order');

const day23 = context.chatDayPayload([{name:'Служба заботы',messages:[
  {id:22930,date:'2026-11-23T11:45:01',text:'Группа 10.30\n\nДюссек Миша - ПУ. Купили абонемент на декабрь. 10240 (с 20% скидкой). Оплата наличными 10500 руб. 260 отправила клиенту на карту. Деньги положила в конверт. Чек пробила. Группа пон-среда 10.30. Все дни, кроме 30 дек.'},
]}], '2026-11-23');
const dussekSale = day23.events.find(event => event.event_kind === 'subscription_sale');
const dussekResult = day23.events.find(event => event.event_kind === 'trial_result');
if (!dussekSale || dussekSale.student_name !== 'Дюссек Михаил' || dussekSale.amount !== 10240 || dussekSale.payment_method !== 'cash') throw new Error('Dussek sale not parsed');
if (dussekSale.components[0].dates.length !== 8 || dussekSale.components[0].dates.includes('2026-12-30')) throw new Error('Dussek lesson composition is wrong');
if (!dussekResult || !dussekResult.came || dussekResult.outcome !== 'purchased') throw new Error('Dussek trial result not parsed');
console.log('PASS Dussek December cash sale and trial result');

const nov16 = context.chatDayPayload([{name:'Служба заботы',messages:[
  {id:21661,date:'2026-11-16T11:07:53',text:'Оплата Решетняк Матвей 5120 за ноябрь. Декабрь не стали оплачивать, т.к. у него брат ходит и все равно всегда есть скидка 20% за второго ребенка. Оплата картой.'},
]}], '2026-11-16');
const matveyNov = nov16.events.find(event=>event.event_kind==='subscription_sale');
if(!matveyNov||matveyNov.amount!==5120||matveyNov.discount_rule!=='sibling'||matveyNov.components[0].dates.length!==4)throw new Error('Reshetnyak November sale not parsed');
if(!nov16.events.some(event=>event.event_kind==='trial_result'&&event.came&&event.outcome==='purchased'))throw new Error('Reshetnyak trial result not parsed');
const nov23 = context.chatDayPayload([{name:'Служба заботы',messages:[
  {id:22925,date:'2026-11-23T11:15:40',text:'Решетняк Арсений\nПродление на декабрь\nГруппа пн/чт 7800\nОплата через точку банк\n\nРешетняк Матвей\nГруппа пн/среда (малыши)\n\nПродление на декабрь , 9 тренировок - 11520 (14400-20% за второго ребенка)\nОплата через точку банк'},
]}], '2026-11-23');
const matveyDec=nov23.events.find(event=>event.student_name==='Решетняк Матвей');
if(!matveyDec||matveyDec.amount!==11520||matveyDec.payment_method!=='tochka_qr_link'||matveyDec.components[0].dates.length!==9)throw new Error('Reshetnyak December sale not parsed');
console.log('PASS Reshetnyak November and December sales');

const kiselev = context.chatDayPayload([{name:'Служба заботы',messages:[
  {id:21788,date:'2026-11-16T18:16:23',text:'5120 пробить чек @D_Tatyana13\n\nОплата абонемента ноябрь\n\nКиселев Илья ✅\n\n#ВНЕСТИ_В_ЕРП'},
]},{name:'ТРЕНЕРЫ',messages:[]}], '2026-11-16');
const ilyaSale=kiselev.events.find(event=>event.event_kind==='subscription_sale');
if(!ilyaSale||ilyaSale.student_name!=='Киселёв Илья'||ilyaSale.amount!==5120||ilyaSale.payment_method!=='card'||ilyaSale.components[0].dates.length!==4)throw new Error('Kiselev November sale not parsed');
if(!kiselev.events.some(event=>event.event_kind==='trial_result'&&event.outcome==='purchased'))throw new Error('Kiselev trial result not parsed');
console.log('PASS Kiselev November sale and trial result');

const kiselevWithProfile = context.chatDayPayload([{name:'Служба заботы',messages:[
  {id:21321,date:'2026-11-13T15:48:01',text:'Кисилев Илья 1.04.2025\n+79191076540 Мария\nИсточник ТРЦ Небо'},
  {id:21788,date:'2026-11-16T18:16:23',text:'5120 пробить чек\nОплата абонемента ноябрь\nКиселев Илья'},
]},{name:'ТРЕНЕРЫ',messages:[]}], '2026-11-16');
const kiselevIdentity=kiselevWithProfile.events.find(event=>event.event_kind==='trial_result');
if(!kiselevIdentity||!kiselevIdentity.create_if_missing||kiselevIdentity.birth!=='2025-04-01'||kiselevIdentity.phone!=='+79191076540')throw new Error('Kiselev spelling variant did not link to identity');
console.log('PASS Kiselev spelling variant links to the verified identity');

const markov = context.chatDayPayload([{name:'Служба заботы',messages:[
  {id:21473,date:'2026-11-14T11:55:50',text:'Внесите запись на ПУ 18.11 среда на 16:45\nТимофей Марков 23.04.2024\nМама Вера\n89261775474 тг и макс'},
  {id:22891,date:'2026-11-22T20:32:01',text:'Отчет 22.11\n• Марков Тимофей 16:45 решили подождать до весны, в апреле исполнится 3, на тренировке не все понимал пока, связь в апреле с ними, группу 1,5-3 неудобно'},
]},{name:'ТРЕНЕРЫ',messages:[]}], '2026-11-22');
const timofey=markov.events.find(event=>event.student_name==='Марков Тимофей');
if(!timofey||!timofey.create_if_missing||timofey.birth!=='2024-04-23'||timofey.phone!=='+79261775474'||timofey.trial_time!=='16:45')throw new Error('Markov identity was not attached to result');
console.log('PASS Markov full identity is attached to trial result');

const reviewed = context.chatDayPayload([{name:'Служба заботы',messages:[
  {id:21989,date:'2026-11-17T19:04:26',text:'Рубайло Михаил 01.02.25\n+79164496354 мама Анастасия\nИсточник: канал Футболики\nЗаписан на 23 ноября в 10:30'},
  {id:22588,date:'2026-11-21T13:10:46',text:'Записан на пробное 22.11 в 16:45\nЕгоров Артур 17.10.23\nМама Октябрина\nИсточник Яндекс карты\n+79142001995'},
  {id:22594,date:'2026-11-21T15:44:01',text:'Записан на пробное 22.11 в 17:45\nМастердеев Денис 4.03.22\nМама Наталия +79859523765'},
  {id:22891,date:'2026-11-22T20:32:01',text:'Отчет 22.11\n• Егоров Артур 16:45 ушли с тренировки, мальчик к тренерам не пошел, перезаписаться и пробовать пока не готовы\n• Мастердеев Денис 17:45 ПРОДАЖА НОЯ+ДЕК 11880 (скидка 20%)'},
]},{name:'ТРЕНЕРЫ',messages:[]}], '2026-11-22');
for(const expected of [['Егоров Артур','2023-10-17','+79142001995'],['Мастердеев Денис','2022-03-04','+79859523765']]){const event=reviewed.events.find(x=>x.student_name===expected[0]);if(!event||!event.create_if_missing||event.birth!==expected[1]||event.phone!==expected[2])throw new Error('Reviewed identity missing: '+expected[0])}
console.log('PASS reviewed clients receive identity from earlier care messages');

const surnameOnly = context.chatDayPayload([{name:'Служба заботы',messages:[
  {id:30001,date:'2026-11-20T10:00:00',text:'Иванов Пётр 01.02.2024\nМама Анна +79001112233'},
  {id:30002,date:'2026-11-22T19:00:00',text:'Отчет 22.11\n• Иванов 16:45 решили подождать до весны'},
]},{name:'ТРЕНЕРЫ',messages:[]}], '2026-11-22');
const ivanov=surnameOnly.events.find(x=>x.student_name==='Иванов Пётр');
if(!ivanov||!ivanov.create_if_missing||ivanov.phone!=='+79001112233')throw new Error('Unique surname was not linked to full identity');
const ambiguousSurname = context.chatDayPayload([{name:'Служба заботы',messages:[
  {id:30101,date:'2026-11-20T10:00:00',text:'Иванов Пётр 01.02.2024\nМама Анна +79001112233'},
  {id:30102,date:'2026-11-20T10:01:00',text:'Иванов Иван 02.03.2023\nМама Ольга +79004445566'},
  {id:30103,date:'2026-11-22T19:00:00',text:'Отчет 22.11\n• Иванов 16:45 решили подождать до весны'},
]},{name:'ТРЕНЕРЫ',messages:[]}], '2026-11-22');
const ambiguousIvanov=ambiguousSurname.events.find(x=>x.student_name==='Иванов');
if(!ambiguousIvanov||ambiguousIvanov.create_if_missing)throw new Error('Ambiguous surname must not create a card');
console.log('PASS surname-only facts link only to one unambiguous identity');
