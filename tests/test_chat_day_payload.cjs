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
