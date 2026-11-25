const assert=require('assert'),fs=require('fs'),vm=require('vm');
const source=fs.readFileSync('crm/static/comms.js','utf8');
const start=source.indexOf('function clientChannelPriority');
const end=source.indexOf('function messengerLogo',start);
assert(start>=0&&end>start);
const context={};vm.createContext(context);vm.runInContext(source.slice(start,end),context);
const primary={id:9},targets=[{v:'c12',channel:'whatsapp'},{v:'m9'},{v:'t9'}];
assert.strictEqual(context.preferredFamilyTarget([{id:12,channel:'whatsapp',established:false}],targets,primary,null),'m9');
assert.strictEqual(context.preferredFamilyTarget([{id:12,channel:'wazzup_telegram',established:true,last_at:'2026-11-22'}],[{v:'c12',channel:'wazzup_telegram'},{v:'m9'}],primary,null),'c12');
const chats=[{id:12,channel:'wazzup_telegram',established:true,last_at:'2026-11-25'},
  {id:13,channel:'wazzup_max',established:true,last_at:'2026-11-24'}];
const choices=chats.map(chat=>({v:'c'+chat.id,channel:chat.channel}));
assert.strictEqual(context.preferredFamilyTarget(chats,choices,primary,13),'c13');
assert.strictEqual(context.preferredFamilyTarget(chats,choices,primary,12),'c12');
assert.strictEqual(context.preferredFamilyTarget(chats,choices,primary,0),'');
assert(source.includes('{m:0,t:1,w:2}'));
console.log('PASS failed-only WhatsApp does not override MAX → Telegram → WhatsApp priority');
