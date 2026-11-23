const fs=require('fs'),assert=require('assert');
const js=fs.readFileSync('crm/static/comms.js','utf8');
assert(js.includes("action('Ответить'"));
assert(js.includes('data-reply-message'));
assert(js.includes("reply_message_id:input._replyMessageId||null"));
assert(js.includes("reply_message_id:$('#cm-text')._replyMessageId||null"));
assert(js.includes("m.can_edit?action('Редактировать'"));
console.log('PASS reply action and supported edit controls are rendered');
