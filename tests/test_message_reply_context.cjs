const assert=require('assert'),fs=require('fs');
const js=fs.readFileSync('crm/static/comms.js','utf8'),css=fs.readFileSync('crm/static/style.css','utf8');
assert(js.includes('class="msg-reply-context"'));
assert(js.includes('data-reply-jump'));
assert(js.includes('target="_blank" rel="noopener"'));
assert(js.includes("event.target.closest('a')"));
assert(css.includes('.msg-reply-highlight'));
console.log('PASS quoted source message is visible, linked and jumpable');
