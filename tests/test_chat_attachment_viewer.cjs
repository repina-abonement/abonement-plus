/* Real chat renderer and modal, isolated from production and all mutations.
 * Run: node tests/test_chat_attachment_viewer.cjs
 * Optional: PLAYWRIGHT_MODULE and CHROMIUM_PATH point to existing installations.
 */
const fs=require('fs'),path=require('path'),assert=require('assert');
let playwright;
try{playwright=require(process.env.PLAYWRIGHT_MODULE||'playwright');}
catch(error){const fallback='/tmp/period-check/node_modules/playwright';if(!process.env.PLAYWRIGHT_MODULE&&fs.existsSync(fallback))playwright=require(fallback);else throw error;}
const staticRoot=path.resolve(__dirname,'../crm/static');
let checks=0;
const eq=(a,b,message)=>{assert.strictEqual(a,b,message);checks++;};
const ok=(value,message)=>{assert(value,message);checks++;};
const messages=[
  {id:9001,kind:'image',media:true,text:'Подпись к фото'},
  {id:9002,kind:'sticker',media:true,text:''},
  {id:9003,kind:'image',media:true,text:'Недоступное фото'},
  {id:9004,kind:'audio',media:true,text:''},
  {id:9005,kind:'file',media:true,text:'Документ'},
  {id:9006,kind:'video',media:true,text:'Видео'},
  {id:9007,kind:'image',media:true,text:'Удалённое фото',deletion_status:'deleted'},
].map(m=>({type:'msg',direction:'in',status:'read',conversation_id:7,ts:'2026-11-20T12:55:00',channel_label:'MAX',...m}));
const conversation={id:7,title:'Синтетическая семья',channel:'wazzup_max',channel_label:'MAX',phone_norm:'79990000001',students:[],last_at:'2026-11-20T12:55:00',last_text:'Фото',unread:0};

(async()=>{
  let browser;
  try{
    const executablePath=process.env.CHROMIUM_PATH||(fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':undefined);
    browser=await playwright.chromium.launch({headless:true,args:['--no-sandbox'],...(executablePath?{executablePath}:{})});
    const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();
    const requests=[],mutations=[],errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    await context.route('**/*',async route=>{
      const request=route.request(),url=new URL(request.url());requests.push({method:request.method(),path:url.pathname,origin:url.origin});
      if(request.method()!=='GET'){mutations.push(request.url());return route.fulfill({status:403,body:'Mutations forbidden'});}
      if(url.origin!=='http://crm-media.test')return route.fulfill({status:403,body:''});
      if(/^\/api\/messages\/\d+\/media$/.test(url.pathname)){
        if(url.pathname.includes('/9003/'))return route.fulfill({status:403,body:'Protected media unavailable'});
        return route.fulfill({status:200,contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="1600"><rect width="1000" height="1600" fill="#badfd0"/></svg>'});
      }
      if(url.pathname.startsWith('/api/')){
        let data={rows:[],items:[],links:[],threads:[],channels:[],messages:[],total:0};
        if(url.pathname==='/api/conversations/7')data={conversation,targets:[{id:7,channel:'wazzup_max',label:'MAX'}],preferred_reply_id:7,history:messages};
        else if(url.pathname==='/api/families/42/comms')data={status:{wazzup_max:true},conversations:[conversation],items:messages};
        else if(url.pathname==='/api/inbox')data={conversations:[conversation],unread:0,incoming_delayed:0,incoming_storage_error:0};
        else if(url.pathname==='/api/inbox/counts')data={unread:0,missed:0};
        return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
      }
      const file=path.join(staticRoot,url.pathname==='/'?'index.html':url.pathname.slice(1));
      if(!file.startsWith(staticRoot+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile())return route.fulfill({status:404,body:''});
      let body=fs.readFileSync(file);
      if(url.pathname==='/app.js')body=Buffer.from(body.toString().replace(/^boot\(\);$/m,'/* Isolated test: no automatic login or boot. */'));
      const type={'.html':'text/html','.js':'text/javascript','.css':'text/css','.json':'application/json','.svg':'image/svg+xml','.png':'image/png','.webp':'image/webp'}[path.extname(file)]||'application/octet-stream';
      return route.fulfill({status:200,contentType:type,body});
    });
    await page.goto('http://crm-media.test/');
    await page.evaluate(()=>localStorage.setItem('crm.sound','0'));
    async function surface(role,kind){
      await page.evaluate(async({role,kind})=>{
        closeChatImageViewer?.();stopChatTimer();clearInterval(S.commsTimer);hideDrawer();
        S.user={id:1,name:'Synthetic User',role,perms:{members:'full',inbox:'full',export:role==='admin'?'full':'none'}};
        S.meta={today:'2026-11-20',user:S.user,comms:{wazzup_max:true},groups:[],branches:[],settings:{},staff:[]};
        document.body.classList.toggle('no-copy',!can('export'));
        document.querySelector('#app').innerHTML='<main class="page" id="media-test-page"></main>';
        S.inbox={tab:'chats',filter:'all',q:''};S.familySendTargets={};S.chatSendTargets={};
        if(kind==='chat'){S.view='inbox';await renderInbox(document.querySelector('#media-test-page'));await openChat(7);}
        else{S.view='search';document.body.classList.remove('chat-fullscreen');openDrawer('<h2>Тестовая карточка</h2><div id="comms-box"></div>');await loadFamilyComms(42,[],1,null);}
        stopChatTimer();clearInterval(S.commsTimer);
        const feed=document.querySelector(kind==='chat'?'#chat-feed':'#comms-box .family-chat-feed');
        feed.insertAdjacentHTML('afterbegin','<div style="height:700px;flex:none">Предыдущая история</div>');
        feed.insertAdjacentHTML('beforeend','<div style="height:700px;flex:none">Следующая история</div>');
      },{role,kind});
      return page.locator(kind==='chat'?'#chat-feed':'#comms-box .family-chat-feed');
    }
    const waitOpen=async()=>{await page.waitForSelector('.chat-media-viewer[open]');await page.waitForFunction(()=>!document.querySelector('.chat-media-viewer img').hidden);};
    const waitClosed=async()=>page.waitForSelector('.chat-media-viewer',{state:'detached'});
    for(const width of [1440,390])for(const role of ['admin','operator'])for(const kind of ['chat','family']){
      await page.setViewportSize({width,height:width===390?844:1000});
      const feed=await surface(role,kind),photo=feed.locator('a[href="/api/messages/9001/media"]'),sticker=feed.locator('a[href="/api/messages/9002/media"]');
      eq(await photo.count(),1,'Actual renderer exposes one image link');
      eq(await feed.locator('[data-chat-image]').count(),3,'Only images/stickers opt into the viewer; removed image remains hidden');
      eq(await feed.locator('audio[src="/api/messages/9004/media"]').count(),1,'Audio controls remain in the chat');
      for(const id of [9005,9006])eq(await feed.locator(`a[href="/api/messages/${id}/media"][target="_blank"]:not([data-chat-image])`).count(),1,'Other file types retain their existing file links');
      await photo.scrollIntoViewIfNeeded();await photo.focus();
      const before=await feed.evaluate(node=>({scroll:node.scrollTop,y:window.scrollY,bodyOverflow:document.body.style.overflow,rootOverflow:document.documentElement.style.overflow}));
      const currentUrl=page.url(),pageCount=context.pages().length;
      await photo.click();await waitOpen();
      eq(page.url(),currentUrl,'Image opens over the existing chat');eq(context.pages().length,pageCount,'No new tab is opened');
      eq(await page.locator('.chat-media-viewer').getAttribute('aria-label'),'Просмотр вложения');
      eq(await page.locator('.chat-media-close').isVisible(),true,'Close control remains visible');
      eq(await page.locator('.chat-media-close').evaluate(node=>node===document.activeElement),true,'Keyboard focus starts on Close');
      const geometry=await page.locator('.chat-media-close').boundingBox();ok(geometry.x>=0&&geometry.y>=0&&geometry.x+geometry.width<=width+1&&geometry.height>=44,'Close fits desktop/mobile with a usable target');
      const imageBox=await page.locator('.chat-media-stage>img').boundingBox();ok(imageBox.x>=0&&imageBox.y>=0&&imageBox.x+imageBox.width<=width+1&&imageBox.y+imageBox.height<= (width===390?844:1000)+1,'Tall image fits inside the viewer');
      if(process.env.VIEWER_SCREENSHOT_DIR&&role==='admin'&&kind==='chat')await page.screenshot({path:path.join(process.env.VIEWER_SCREENSHOT_DIR,`crm173-viewer-${width}.png`)});
      await page.keyboard.press('Tab');eq(await page.locator('.chat-media-close').evaluate(node=>node===document.activeElement),true,'Tab stays within the modal');
      await page.locator('.chat-media-stage>img').click();eq(await page.locator('.chat-media-viewer').count(),1,'Clicking image does not close it');
      await page.locator('.chat-media-close').click();await waitClosed();
      eq(await photo.evaluate(node=>node===document.activeElement),true,'Close restores focus to the attachment');
      const after=await feed.evaluate(node=>({scroll:node.scrollTop,y:window.scrollY,bodyOverflow:document.body.style.overflow,rootOverflow:document.documentElement.style.overflow}));
      assert.deepStrictEqual(after,before,'Close preserves chat/document position and existing scroll lock');checks++;
      await photo.click();await waitOpen();await page.keyboard.press('Escape');await waitClosed();
      if(kind==='family')eq(await page.locator('#drawer').evaluate(node=>node.hidden),false,'Escape closes only the image; client card stays open');
      eq(await photo.evaluate(node=>node===document.activeElement),true,'Escape restores attachment focus');
      await sticker.scrollIntoViewIfNeeded();await sticker.click();await waitOpen();
      eq(await page.locator('.chat-media-toolbar>span').textContent(),'Стикер');
      await page.locator('.chat-media-stage').click({position:{x:2,y:2}});await waitClosed();
      const broken=feed.locator('a[href="/api/messages/9003/media"]');await broken.scrollIntoViewIfNeeded();await broken.click();
      await page.waitForFunction(()=>document.querySelector('.chat-media-status')?.textContent.startsWith('Не удалось'));
      eq(await page.locator('.chat-media-close').isVisible(),true,'Access/load failure never hides Close');
      await page.locator('.chat-media-close').click();await waitClosed();
      const modifier=await photo.evaluate(link=>{
        let result;const intercept=event=>{result={defaultPrevented:event.defaultPrevented,modal:!!document.querySelector('.chat-media-viewer')};event.preventDefault();};
        window.addEventListener('click',intercept,{once:true});
        link.dispatchEvent(new MouseEvent('click',{button:0,ctrlKey:true,bubbles:true,cancelable:true}));return result;
      });eq(modifier.defaultPrevented,false,'Ctrl-click retains native open-in-new-tab choice');eq(modifier.modal,false);
      const unsafe=await photo.evaluate(link=>{const saved=link.href;link.href='https://untrusted.invalid/file';const opened=openChatImageViewer(link);link.href=saved;return opened;});
      eq(unsafe,false,'Viewer rejects anything outside the authenticated message media path');
    }
    // Hash navigation removes the viewer and a detached chat does not break cleanup.
    const feed=await surface('admin','chat'),photo=feed.locator('a[href="/api/messages/9001/media"]');
    await photo.scrollIntoViewIfNeeded();await photo.click();await waitOpen();
    await page.evaluate(()=>window.dispatchEvent(new HashChangeEvent('hashchange')));await waitClosed();
    eq(await page.evaluate(()=>document.documentElement.style.overflow),'','Navigation restores the previous root overflow');
    await photo.click();await waitOpen();await photo.evaluate(node=>node.remove());await page.keyboard.press('Escape');await waitClosed();
    eq(mutations.length,0,'Viewing/closing never writes data or sends a message');
    eq(requests.filter(r=>r.origin!=='http://crm-media.test').length,0,'No external file URL is requested');
    eq(errors.length,0,errors.join('\n'));
    console.log(`Chat-attachment viewer: ${checks} assertions passed; desktop/mobile, owner/operator, inbox/client card, close/Escape/background, focus/scroll, protected media and errors; no API mutations`);
  }finally{if(browser)await browser.close();}
})().catch(error=>{console.error(error.stack||error);process.exitCode=1;});
