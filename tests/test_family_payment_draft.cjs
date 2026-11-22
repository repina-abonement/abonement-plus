const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const app = fs.readFileSync('crm/static/app.js', 'utf8');
const start = app.indexOf('function pasteIntoFamilyComposer');
const end = app.indexOf('function clientSubscriptionMonth', start);
assert(start >= 0 && end > start);

const events = [];
const context = { Event: function(name, options) { this.name=name; this.options=options; } };
vm.createContext(context);
vm.runInContext(app.slice(start, end), context);

const target = { value: 'Добрый день!\n\nОплата за двоих детей:\nhttps://ab-plus.ru/pay/first',
  dispatchEvent: event => events.push(event.name) };
context.pasteIntoFamilyComposer(target, 'Абонемент\nВторой ребёнок\n\nСсылка для оплаты:\nhttps://ab-plus.ru/pay/second');
assert(target.value.includes('/pay/first'));
assert(target.value.includes('/pay/second'));
assert(target.value.includes('Второй ребёнок'));
const once = target.value;
context.pasteIntoFamilyComposer(target, 'Повтор\nhttps://ab-plus.ru/pay/second');
assert.strictEqual(target.value, once, 'повтор той же ссылки не должен дублироваться');
assert.deepStrictEqual(events, ['input', 'input']);

const comms = fs.readFileSync('crm/static/comms.js', 'utf8');
assert(comms.includes('const familyDraftKey = `${S.user.id}:${familyId}`'));
assert(comms.includes('S.familyDrafts[familyDraftKey]=e.target.value'));
assert(comms.includes('delete S.familyDrafts[familyDraftKey]'));

console.log('PASS family draft survives sibling cards and payment links append without duplicates');
