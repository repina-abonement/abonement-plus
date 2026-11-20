const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const source = fs.readFileSync('crm/static/app.js', 'utf8');
const helperStart = source.indexOf('const KEYBOARD_EN');
const helperEnd = source.indexOf('const rub', helperStart);
const navStart = source.indexOf('function crmNavigationUrl');
const navEnd = source.indexOf('/* ---------- навигация', navStart);
assert(helperStart >= 0 && helperEnd > helperStart && navStart >= 0 && navEnd > navStart);

let auxclick, mousedown;
const opened = [];
const context = {
  location: { origin: 'https://ab-plus.ru', pathname: '/' },
  document: { addEventListener: (name, callback) => { if (name === 'auxclick') auxclick = callback; if (name === 'mousedown') mousedown = callback; } },
  window: { open: (...args) => opened.push(args) },
  URLSearchParams,
};
vm.createContext(context);
vm.runInContext(source.slice(helperStart, helperEnd) + source.slice(navStart, navEnd), context);

assert.deepStrictEqual([...context.keyboardLayoutVariants('bdfyjd')], ['bdfyjd', 'иванов']);
assert(context.searchTextMatches('Иванов Александр', 'bdfyjd'));
assert(context.searchTextMatches('John Smith', 'Smith'));
assert.strictEqual(context.crmNavigationUrl({ dataset: { view: 'payments' } }), 'https://ab-plus.ru/#payments');
assert.strictEqual(context.crmNavigationUrl({ dataset: { open: '1719' } }), 'https://ab-plus.ru/#members?student=1719');
assert.strictEqual(context.crmNavigationUrl({ dataset: { conv: '42' } }), 'https://ab-plus.ru/#inbox?conversation=42');
assert.strictEqual(context.crmNavigationUrl({ dataset: { tile: 'erp' } }), 'https://ab-plus.ru/#settings?settings_tab=erp');

let prevented = false;
const studentTarget = { dataset: { open: '1719' } };
mousedown({ button: 1, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false,
  target: { closest: () => studentTarget },
  preventDefault: () => { prevented = true; }, stopPropagation: () => {} });
assert(prevented);
assert.deepStrictEqual(opened[0], ['https://ab-plus.ru/#members?student=1719', '_blank', 'noopener']);
auxclick({ button: 1, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false,
  target: { closest: () => studentTarget }, preventDefault: () => {}, stopPropagation: () => {} });
assert.strictEqual(opened.length, 1, 'mousedown and auxclick must not open two tabs');

console.log('PASS wrong-layout matching and middle-click routes for sections, cards, chats, and settings');
