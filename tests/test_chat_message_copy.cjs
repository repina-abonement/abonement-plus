/* Durable regression of the actual chat renderer, selection guard and clipboard fallback.
 * Run: node tests/test_chat_message_copy.cjs
 * Optional: PLAYWRIGHT_MODULE=/path/to/playwright CHROMIUM_PATH=/path/to/chromium
 * Loads the real index/scripts with boot disabled. Every request is intercepted locally;
 * no production connection, new message, deletion or other API mutation is permitted.
 */
const fs = require('fs'), path = require('path'), assert = require('assert');
let playwright;
try { playwright = require(process.env.PLAYWRIGHT_MODULE || 'playwright'); }
catch (error) {
  const existing = '/tmp/period-check/node_modules/playwright';
  if (!process.env.PLAYWRIGHT_MODULE && fs.existsSync(existing)) playwright = require(existing);
  else throw new Error('Playwright is required; set PLAYWRIGHT_MODULE to an existing installation');
}
const root = path.resolve(__dirname, '..'), staticRoot = path.join(root, 'crm', 'static');
let checks = 0;
const eq = (actual, expected, message) => { assert.strictEqual(actual, expected, message); checks++; };
const ok = (value, message) => { assert(value, message); checks++; };
const incoming = '  Привет, «мир» 🌿\nВторая <строка> & "кавычки" и \'апостроф\'\n  ';
const outgoing = 'Ответ администратора 👩🏽‍💻\nБез времени, имени и статуса.  ';
const caption = 'Подпись к фото 📸\n<это текст, а не HTML>';
const messages = [
  {text: incoming, direction: 'in'},
  {text: outgoing, direction: 'out', can_delete: true},
  {text: caption, direction: 'in', kind: 'image', media: true},
  {text: null, direction: 'in', kind: 'image', media: true},
  {text: '', direction: 'in', kind: 'other'},
  {text: ' \n\t ', direction: 'in'},
  {text: 'Hidden original pending removal', direction: 'out', deletion_status: 'requested', can_delete: true},
  {text: 'Hidden original already removed', direction: 'out', deletion_status: 'deleted', can_delete: true},
  {text: 'Удаление пока не подтверждено 🕒', direction: 'out', deletion_status: 'uncertain', can_delete: true},
  {text: 'Текст старого исходящего', direction: 'out', can_delete: false, delete_reason: 'Срок удаления истёк'},
  {text: 'Неотправленный текст\nВсё равно можно скопировать.', direction: 'out', status: 'error', error: 'Synthetic provider diagnostic; not part of message body', can_delete: false},
  {text: 'Да', direction: 'in', reply_context:{message_id:2,external_id:'real-provider-id',text:'Оплатить: https://ab-plus.ru/pay/synthetic',kind:'text'}},
].map((message, index) => ({id: index + 1, type: 'msg', kind: 'text', status: 'read', ts: '2026-11-20T12:55:56', channel_label: 'MAX', user_name: 'Синтетический автор', conversation_id: 7, ...message}));
const conversation = {id:7, title:'Тестовая семья', channel:'wazzup_max', channel_label:'MAX', phone_norm:'79990000001', students:[], last_at:'2026-11-20T12:55:56', last_text:outgoing, unread:0};
const family = {status:{wazzup_max:true}, conversations:[conversation], items:messages};
const conversationDetail = {conversation, targets:[{id:7,channel:'wazzup_max',label:'MAX · Тестовая семья'}], preferred_reply_id:7, history:messages};

(async () => {
  let browser;
  try {
    const launch = {headless:true,args:['--no-sandbox']};
    const executable = process.env.CHROMIUM_PATH || (fs.existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : null);
    if (executable) launch.executablePath = executable;
    browser = await playwright.chromium.launch(launch);
    const page = await browser.newPage({viewport:{width:1440,height:1000}}), mutations = [], requests = [], errors = [];
    page.on('pageerror', error => errors.push(error.stack || error.message));
    await page.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      requests.push({method:request.method(),path:url.pathname});
      if (request.method() !== 'GET') {
        mutations.push({method:request.method(),path:url.pathname});
        return route.fulfill({status:403,contentType:'application/json',body:'{"error":"Test forbids mutations"}'});
      }
      if (url.origin !== 'http://crm-copy.test') return route.fulfill({status:403,body:''});
      if (url.pathname.startsWith('/api/')) {
        if (/^\/api\/messages\/\d+\/media$/.test(url.pathname)) return route.fulfill({status:200,contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"><rect width="40" height="40" fill="#cfe5d4"/></svg>'});
        let data;
        if (url.pathname === '/api/conversations/7') data = conversationDetail;
        else if (url.pathname === '/api/families/42/comms') data = family;
        else if (url.pathname === '/api/inbox') data = {conversations:[conversation],unread:0,incoming_delayed:0,incoming_storage_error:0};
        else if (url.pathname === '/api/inbox/counts') data = {unread:0,missed:0};
        else data = {rows:[],items:[],links:[],threads:[],channels:[],messages:[],total:0};
        return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
      }
      const file = path.join(staticRoot,url.pathname === '/' ? 'index.html' : url.pathname.slice(1));
      if (!file.startsWith(staticRoot + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) return route.fulfill({status:404,body:''});
      let body = fs.readFileSync(file);
      if (url.pathname === '/app.js') {
        ok(/^boot\(\);$/m.test(body.toString()),'Disable only the automatic boot call; keep real functions/listeners');
        body = Buffer.from(body.toString().replace(/^boot\(\);$/m,'/* Boot disabled for the isolated chat-copy test. */'));
      }
      const extension = path.extname(file), type = {'.js':'text/javascript','.css':'text/css','.html':'text/html','.json':'application/json','.png':'image/png','.svg':'image/svg+xml','.webp':'image/webp'}[extension] || 'application/octet-stream';
      return route.fulfill({status:200,contentType:type,body});
    });
    await page.goto('http://crm-copy.test/');
    await page.evaluate(() => {
      localStorage.setItem('crm.sound','0');
      window.__copyWrites=[];window.__execCalls=[];window.__manualPrompts=[];window.__copyEvents=[];
      window.prompt=(label,text)=>{window.__manualPrompts.push({label,text});return null};
      document.addEventListener('copy',event=>window.__copyEvents.push({blocked:event.defaultPrevented,text:window.getSelection().toString()}));
      window.__nativeExecCommand=document.execCommand.bind(document);
    });
    const configureCopy = async (clipboard='ok',exec='ok') => page.evaluate(({clipboard,exec}) => {
      window.__copyWrites=[];window.__execCalls=[];window.__manualPrompts=[];
      Object.defineProperty(navigator,'clipboard',{configurable:true,value:clipboard==='missing'?undefined:{writeText:async text=>{
        window.__copyWrites.push(text);if(clipboard==='reject')throw Error('Synthetic clipboard rejection');
      }}});
      document.execCommand=command=>{
        window.__execCalls.push({command,text:document.activeElement?.value});
        if(exec==='throw')throw Error('Synthetic execCommand rejection');
        return exec==='ok';
      };
      document.querySelector('#toast').hidden=true;
    },{clipboard,exec});
    const openSurface = async (role,surface) => {
      await page.evaluate(async ({role,surface}) => {
        stopChatTimer();clearInterval(S.commsTimer);hideDrawer();
        S.user={id:role==='admin'?1:2,name:'Синтетический тест',role,perms:{members:'full',inbox:'full',export:role==='admin'?'full':'none'}};
        S.meta={today:'2026-11-20',user:S.user,comms:{wazzup_max:true},groups:[],branches:[],settings:{},staff:[]};
        document.body.classList.toggle('no-copy',!can('export'));
        document.querySelector('#app').innerHTML='<main class="page" id="copy-test-page"></main>';
        S.inbox={tab:'chats',filter:'all',q:''};S.familySendTargets={};S.chatSendTargets={};
        if(surface==='chat'){
          S.view='inbox';await renderInbox(document.querySelector('#copy-test-page'));await openChat(7);
        }else{
          S.view='search';document.body.classList.remove('chat-fullscreen');
          openDrawer('<h2>Тестовая карточка клиента</h2><div id="comms-box"></div>');
          await loadFamilyComms(42,[],1,null);
        }
        stopChatTimer();clearInterval(S.commsTimer);
      },{role,surface});
      return page.locator(surface==='chat'?'#chat-feed':'#comms-box .family-chat-feed');
    };
    const copyMessage = async (feed,index,expected) => {
      const message=feed.locator('.msg').nth(index), menu=message.locator('.msg-actions');
      await menu.locator('summary').click();
      await message.locator('[data-copy-message]').click();
      await page.waitForFunction(()=>window.__copyWrites.length>0);
      eq((await page.evaluate(()=>window.__copyWrites)).at(-1),expected,'Copy exactly the visible raw body, including line breaks/spacing/emoji');
      await page.waitForFunction(()=>!document.querySelector('[data-copy-message]:disabled'));
      eq(await menu.getAttribute('open'),null,'Successful copy closes message menu');
      ok(await menu.locator('summary').evaluate(node=>node===document.activeElement),'Successful copy restores menu-summary focus');
      eq(await page.locator('#toast').innerText(),'Текст скопирован');
    };

    for(const role of ['admin','operator'])for(const surface of ['chat','family']){
      const feed=await openSurface(role,surface);
      eq(await page.locator('body').evaluate(node=>node.classList.contains('no-copy')),role==='operator');
      eq(await page.evaluate(()=>can('export')),role==='admin');
      eq(await feed.locator('.msg').count(),messages.length);
      eq(await feed.locator('.chat-message-copyable').count(),7);
      eq(await feed.locator('[data-copy-message]').count(),7,'Copy works for incoming and outgoing, independent of export permission');
      for(const index of [3,4,5,6,7])eq(await feed.locator('.msg').nth(index).locator('[data-copy-message],.chat-message-copyable').count(),0,'No copying media-only, placeholder, whitespace or removed text');
      ok(!(await feed.innerHTML()).includes('Hidden original'),'Deleted/requested original content is not placed in DOM');
      eq(await feed.locator('.msg').nth(0).locator('[data-delete-message]').count(),0,'Incoming deletion eligibility is unchanged');
      eq(await feed.locator('.msg').nth(1).locator('[data-delete-message]').isDisabled(),false);
      eq(await feed.locator('.msg').nth(9).locator('[data-delete-message]').isDisabled(),true);
      eq(await feed.locator('.msg').nth(8).locator('[data-delete-message]').count(),0,'Uncertain deletion cannot be submitted again');
      ok((await feed.locator('.msg').nth(8).innerText()).includes('Удаление не подтверждено'));
      const quote=feed.locator('.msg').nth(11).locator('.msg-reply-context');
      eq(await quote.count(),1,'Reply renders its source message');
      eq(await quote.locator('a').getAttribute('href'),'https://ab-plus.ru/pay/synthetic','Source payment link stays clickable');
      await quote.click({position:{x:5,y:5}});
      ok(await feed.locator('.msg').nth(1).evaluate(node=>node.classList.contains('msg-reply-highlight')),'Source quote jumps to the original message');
      eq(await feed.locator('.msg').nth(0).locator('.chat-message-copyable').evaluate(node=>getComputedStyle(node).userSelect),'text','Native selection remains enabled under no-copy CSS');
      for(const index of [0,1,2,8,9,10,11]){
        await configureCopy();await copyMessage(feed,index,messages[index].text);
        eq(await page.evaluate(()=>window.__execCalls.length),0,'Normal clipboard path does not need legacy fallback');
        eq(await page.evaluate(()=>window.__manualPrompts.length),0);
      }
      // A stale/hidden body must not be copied even if a button event is dispatched directly.
      await configureCopy();
      await feed.locator('.msg').nth(0).evaluate(node=>{node.querySelector('.chat-message-copyable').hidden=true;node.querySelector('[data-copy-message]').click()});
      eq(await page.evaluate(()=>window.__copyWrites.length),0,'Hidden message body cannot be copied through its old menu');
      await feed.locator('.msg').nth(0).evaluate(node=>{node.querySelector('.chat-message-copyable').hidden=false});
    }

    // Short incoming bubbles and final messages must not hide the new menu inside
    // the scrolling feed. Keep the real page/drawer layout on desktop and mobile.
    for(const width of [1440,280])for(const surface of ['chat','family']){
      await page.setViewportSize({width,height:1000});
      const visibleFeed=await openSurface('operator',surface);
      for(const index of [0,1,11]){
        await visibleFeed.locator('.msg-actions').evaluateAll(nodes=>nodes.forEach(node=>node.open=false));
        const message=visibleFeed.locator('.msg').nth(index);
        await message.locator('.msg-actions > summary').click();
        const bounds=await message.evaluate(async node=>{
          await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
          const popup=node.querySelector('.msg-actions-menu'),feed=node.closest('.chat-feed'),r=popup.getBoundingClientRect(),f=feed.getBoundingClientRect();
          return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height,
            minX:Math.max(0,f.left),maxX:Math.min(innerWidth,f.right),minY:Math.max(0,f.top),maxY:Math.min(innerHeight,f.bottom),overflow:getComputedStyle(feed).overflowY};
        });
        const label=`${surface} menu for message ${index} at ${width}px: ${JSON.stringify(bounds)}`;
        ok(bounds.width>0&&bounds.height>0,label);
        ok(bounds.left>=bounds.minX-1&&bounds.right<=bounds.maxX+1,`Horizontal clipping: ${label}`);
        ok(bounds.top>=bounds.minY-1&&bounds.bottom<=bounds.maxY+1,`Vertical clipping: ${label}`);
        ok(['auto','scroll'].includes(bounds.overflow),`Use real scrolling feed: ${label}`);
      }
    }
    await page.setViewportSize({width:1440,height:1000});

    const feed=await openSurface('operator','chat');
    // Exercise actual Ctrl+C, rather than merely calling the guard helper.
    const selectRange=async (kind) => page.evaluate(kind=>{
      const bodies=[...document.querySelectorAll('#chat-feed .chat-message-copyable')],selection=getSelection(),range=document.createRange();
      document.activeElement?.blur();
      if(kind==='inside'){range.setStart(bodies[0].firstChild,2);range.setEnd(bodies[0].firstChild,14)}
      else if(kind==='cross'){range.setStart(bodies[0].firstChild,0);range.setEnd(bodies[1].firstChild,4)}
      else{range.selectNodeContents(document.querySelector('.conv-title b'))}
      selection.removeAllRanges();selection.addRange(range);window.__copyEvents=[];
    },kind);
    for(const [kind,blocked] of [['inside',false],['outside',true],['cross',true]]){
      await selectRange(kind);await page.keyboard.press('Control+C');
      await page.waitForFunction(()=>window.__copyEvents.length>0);
      eq((await page.evaluate(()=>window.__copyEvents)).at(-1).blocked,blocked,`Ctrl+C ${kind} is ${blocked?'restricted':'allowed'} for operator`);
    }
    // Existing task text remains selectable, while input fields keep their normal exception.
    const otherSelections=await page.evaluate(()=>{
      const host=document.querySelector('#copy-test-page');host.insertAdjacentHTML('beforeend','<div class="task-copyable" id="test-task">Задача для копирования</div>');
      const selection=getSelection(),range=document.createRange();range.selectNodeContents(document.querySelector('#test-task'));selection.removeAllRanges();selection.addRange(range);
      document.activeElement?.blur();const task=new ClipboardEvent('copy',{bubbles:true,cancelable:true});document.dispatchEvent(task);
      const input=document.querySelector('#chat-text');input.focus();input.value='Черновик';input.select();const typing=new ClipboardEvent('copy',{bubbles:true,cancelable:true});input.dispatchEvent(typing);
      return {task:task.defaultPrevented,input:typing.defaultPrevented};
    });
    eq(otherSelections.task,false);eq(otherSelections.input,false);
    await openSurface('admin','chat');await selectRange('outside');await page.keyboard.press('Control+C');
    await page.waitForFunction(()=>window.__copyEvents.length>0);
    eq((await page.evaluate(()=>window.__copyEvents)).at(-1).blocked,false,'Owner export permission still permits ordinary copy');

    // Fallbacks run through the actual message menu and the shared helper.
    const fallbackFeed=await openSurface('operator','family');
    for(const [clipboard,exec] of [['missing','ok'],['reject','ok'],['missing','false'],['reject','throw']]){
      await configureCopy(clipboard,exec);
      const message=fallbackFeed.locator('.msg').nth(0),menu=message.locator('.msg-actions'),button=message.locator('[data-copy-message]');
      await menu.locator('summary').click();
      const textareaCount=await page.locator('textarea').count();
      await button.click();
      await page.waitForFunction(()=>window.__execCalls.length>0&&!document.querySelector('[data-copy-message]:disabled'));
      const state=await page.evaluate(()=>({exec:window.__execCalls,prompts:window.__manualPrompts,writes:window.__copyWrites,toast:document.querySelector('#toast').hidden}));
      eq(state.exec.length,1);eq(state.exec[0].command,'copy');eq(state.exec[0].text,incoming,'Fallback selects the exact body');
      eq(await page.locator('textarea').count(),textareaCount,'Temporary fallback textarea is removed, including thrown execCommand');
      eq(await button.isDisabled(),false);
      if(exec==='ok'){
        eq(await menu.getAttribute('open'),null);ok(await menu.locator('summary').evaluate(node=>node===document.activeElement));eq(state.prompts.length,0);eq(state.toast,false);
      }else{
        ok(await menu.evaluate(node=>node.open),'Manual fallback keeps menu open');eq(state.prompts.length,1);eq(state.prompts[0].label,'Скопируйте текст');eq(state.prompts[0].text,incoming);
        ok(await button.evaluate(node=>node===document.activeElement),'Failed fallback restores the copy-button focus');eq(state.toast,true,'No false success notice after failed automatic copy');
        await menu.locator('summary').click();
      }
    }
    // The shared fallback restores a pre-existing text selection and focus even on exception.
    for(const exec of ['ok','throw']){
      await configureCopy('missing',exec);
      const restored=await page.evaluate(async()=>{
        const body=document.querySelector('.family-chat-feed .chat-message-copyable'),summary=body.closest('.msg').querySelector('.msg-actions summary');
        summary.focus();const selection=getSelection(),range=document.createRange();range.setStart(body.firstChild,2);range.setEnd(body.firstChild,14);selection.removeAllRanges();selection.addRange(range);
        const before=selection.toString(),count=document.querySelectorAll('textarea').length;
        const result=await copyTextWithNotice('Запасной текст\n🌿');
        return {result,before,after:selection.toString(),focus:document.activeElement===summary,countBefore:count,countAfter:document.querySelectorAll('textarea').length};
      });
      eq(restored.result,exec==='ok');eq(restored.after,restored.before);eq(restored.focus,true);eq(restored.countAfter,restored.countBefore);
    }
    eq(mutations.length,0,JSON.stringify(mutations));
    eq(requests.filter(request=>/\/(?:send|delete|read|preferences|link|question)(?:\/|$)/.test(request.path)).length,0,'Copy never sends/deletes messages or changes read/link/preferences state');
    ok(requests.some(request=>request.path==='/api/conversations/7'));
    ok(requests.some(request=>request.path==='/api/families/42/comms'));
    eq(errors.length,0,errors.join('\n'));
    console.log(`Chat-message copy: ${checks} assertions passed; owner/operator, inbox/family, exact text, selection guard and safe fallbacks; no API mutations or outgoing messages`);
  } finally { if(browser)await browser.close(); }
})().catch(error=>{console.error(error.stack||error);process.exitCode=1});
