const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(path.resolve(__dirname,'../crm/static/comms.js'),'utf8');
const start=source.indexOf('function chatDateLabel(day)');
const end=source.indexOf('\nfunction chatHistoryHtml',start);
assert(start>=0&&end>start,'real chatDateLabel function is present');
const sandbox={
  S:{meta:{today:'2026-11-20'}},
  isoDate:value=>value.toISOString().slice(0,10),
  addDays:(iso,offset)=>{const value=new Date(iso+'T12:00:00');value.setDate(value.getDate()+offset);return value.toISOString().slice(0,10)},
  Intl,Date,
};
vm.createContext(sandbox);vm.runInContext(source.slice(start,end),sandbox);
assert.strictEqual(sandbox.chatDateLabel('2026-11-20'),'Сегодня (пятница)');
assert.strictEqual(sandbox.chatDateLabel('2026-11-19'),'Вчера (четверг)');
assert.strictEqual(sandbox.chatDateLabel('2026-11-18'),'18 ноября 2026 г. (среда)');
assert.strictEqual(sandbox.chatDateLabel(''),'Дата не указана');
assert(source.includes('${esc(chatDateLabel(day))}'),'all chat surfaces use the shared date label');
console.log('ok: chat date separators include Russian weekday for today, yesterday and full dates');
