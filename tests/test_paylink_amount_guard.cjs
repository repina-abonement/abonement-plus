/* Actual sale/link UI, isolated mutable API double; no production service opens.
 * Run: node tests/test_paylink_amount_guard.cjs
 * Optional PLAYWRIGHT_MODULE and CHROMIUM_PATH point to existing installations.
 */
const fs=require('fs'),path=require('path'),assert=require('assert'),cp=require('child_process');
let playwright;try{playwright=require(process.env.PLAYWRIGHT_MODULE||'playwright')}catch(error){const p='/tmp/period-check/node_modules/playwright';if(!process.env.PLAYWRIGHT_MODULE&&fs.existsSync(p))playwright=require(p);else throw Error('Set PLAYWRIGHT_MODULE to existing Playwright')}
const root=path.resolve(__dirname,'..'),assets=path.join(root,'crm','static');
let checks=0;const eq=(a,b,n)=>{assert.strictEqual(a,b,n);checks++},ok=(a,n)=>{assert(a,n);checks++};
(async()=>{
 let browser,state,calls=[],errors=[],release=null,releaseList=null;
 const actual=JSON.parse(cp.execFileSync(process.env.PYTHON||'python',['-c',`import json
from tests.test_paylink_current_due_guard import PaylinkCurrentDueGuardTest
from crm import paylinks
f=PaylinkCurrentDueGuardTest();f.setUp()
try:
 sub=f.seven();link=f.link(sub);original=f.con.serialize()
 valid=f.check(link,student_id=f.sid,subscription_id=sub,require_full=True)
 public=paylinks.payment_page(f.con,f.token(link));assert f.con.serialize()==original
 f.con.execute('UPDATE subscriptions SET price=10000 WHERE id=?',(sub,))
 invalid=f.check(link,student_id=f.sid,subscription_id=sub,require_full=True)
 print(json.dumps({'link':link,'student_id':f.sid,'subscription_id':sub,'valid':valid,'invalid':invalid,'public':public},ensure_ascii=False))
 f.tearDown()
finally:f.doCleanups()
`],{cwd:root,encoding:'utf8'}));
 const reset=extra=>{state={due:9450,bundleDue:18900,links:[],serial:99,invalidIds:new Set(),...extra};if(state.links.length)state.serial=Math.max(state.serial,...state.links.map(l=>l.id+1));calls=[];release=null};
 const link=(id=99,amount=9450)=>({id,amount,url:'http://crm-link-guard.test/pay/local-'+id,student_id:1,subscription_id:10,status:'open',purpose:'Синтетический абонемент',created_at:'2026-11-20T12:00'});
 const balance=()=>({use_balance:false,available_balance:0,apply_amount:0,invoice_total:state.due,payable:state.due,remaining_balance:0,already_applied:0,token:'synthetic-zero-balance-'+state.due});
 const detail=()=>({student:{id:1,name:'Синтетический клиент',group_id:1,tariff:'fixed'},cur_month:'2026-11',next_month:'2026-12',balance:{deposit:0},enrollments:[{id:1,group_id:1,group_name:'Синтетическая группа',tariff:'fixed'}],subscriptions:[{id:10,group_id:1,enrollment_id:1,group_name:'Синтетическая группа',month:'2026-12',tariff:'fixed',price:state.due,lessons:7,status:'invoiced',kind:'month',lesson_schedule:'[{"dates":["2026-12-03","2026-12-07","2026-12-10","2026-12-14","2026-12-17","2026-12-21","2026-12-24"]}]'}],discounts:[],trial_visits:[]});
 reset();
 try{
  const launch={headless:true,args:['--no-sandbox']},executable=process.env.CHROMIUM_PATH||(fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':null);if(executable)launch.executablePath=executable;
  browser=await playwright.chromium.launch(launch);const page=await browser.newPage({viewport:{width:1400,height:1050}});page.setDefaultTimeout(6000);page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url()),method=request.method(),body=request.postDataJSON();
   if(url.origin!=='http://crm-link-guard.test')throw Error('External/provider request forbidden');
   const json=(data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
   if(url.pathname.startsWith('/api/')){
    calls.push({method,path:url.pathname,query:Object.fromEntries(url.searchParams),body});
    if(method==='GET'&&url.pathname==='/api/students/1')return json(detail());
    if(method==='GET'&&url.pathname==='/api/students/1/required')return json({missing:[],optional:[]});
    if(method==='GET'&&url.pathname==='/api/students/1/paylinks'){if(state.holdListing){state.holdListing=false;await new Promise(resolve=>releaseList=resolve)}return json({ready:true,links:state.links})}
    if(method==='GET'&&url.pathname==='/api/subscriptions/10/payment-quote')return json({id:10,price:state.due,paid:0,total:state.due,balance_preview:balance()});
    if(method==='GET'&&/^\/api\/paylinks\/\d+\/validate$/.test(url.pathname)){
     if(state.actualGuard)return json(actual[state.actualGuard]);
     const id=Number(url.pathname.split('/')[3]),stored=state.links.find(l=>l.id===id);
     if(state.guardFails){state.guardFails=false;return json({error:'Сервис проверки временно недоступен'},503)}
     const due=stored?.bundleIds?state.bundleDue:state.due,valid=!!stored&&stored.status==='open'&&stored.amount===due&&!state.invalidIds.has(id);
     const data={valid,reason:valid?'':'Сумма или состояние ссылки изменились',link_id:id,student_id:1,subscription_id:stored?.subscription_id??null,amount:stored?.amount,current_due:due,url:stored?.url,read_only:true,is_full_payment:true,scope_kind:stored?.bundleIds?'bundle':'invoice',invoice_scope:(stored?.bundleIds||[10]).map(subscription_id=>({subscription_id,amount:stored?.amount,due}))};
     if(state.bundleWrongScope)data.invoice_scope=[{subscription_id:10},{subscription_id:12}];
     if(state.invalidSuccess){data.valid=true;data.current_due=state.due+500}
     if(state.foreignSuccess){data.valid=true;data.student_id=2}
     if(state.holdGuard){state.holdGuard=false;await new Promise(resolve=>release=resolve)}
     return json(data);
    }
    if(method==='POST'&&url.pathname==='/api/students/1/paylinks'){
     eq(body.subscription_id,10);eq(body.full_payment,true);eq(body.amount,state.due);
     if(state.createFails){state.createFails=false;return json({error:'Временная ошибка создания ссылки'},503)}
     const result=link(state.serial++,state.wrongIssuedAmount||body.amount);state.links.push(result);state.wrongIssuedAmount=null;
     if(state.holdCreate){state.holdCreate=false;await new Promise(resolve=>release=resolve)}
     return json(result);
    }
    if(method==='POST'&&url.pathname==='/api/students/1/multi-month-preview')return json({months:body.months.map(month=>({month,quote:{total:9450,base:9450,per_lesson:1350,lessons_paid:7,tariff:'fixed',credit_lessons:0,late_fee:0,pricing:{},dates:[month+'-03'],calendar_dates:[month+'-03']},excluded_dates:[]})),total:18900,credit:0,due:state.bundleDue,token:'synthetic-bundle-'+state.bundleDue,balance_preview:{...balance(),invoice_total:18900,payable:state.bundleDue}});
    if(method==='POST'&&url.pathname==='/api/students/1/multi-month-sale'){
     eq(body.mode,'links');eq(body.use_balance,false);const bundle={...link(state.serial++,state.wrongIssuedAmount||state.bundleDue),subscription_id:null,bundleIds:[10,11]};state.links.push(bundle);if(state.holdBundleCreate){state.holdBundleCreate=false;await new Promise(resolve=>release=resolve)}return json({link:bundle,subscriptions:[{id:10,month:body.months[0],price:9450},{id:11,month:body.months[1],price:9450}],total:18900,due:state.bundleDue});
    }
    throw Error('Unexpected mutation or API: '+method+' '+url.pathname);
   }
   const file=path.join(assets,url.pathname==='/'?'index.html':url.pathname.slice(1));if(!file.startsWith(assets+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile())return route.fulfill({status:404,body:''});
   let content=fs.readFileSync(file);if(url.pathname==='/app.js')content=Buffer.from(content.toString().replace(/^boot\(\);$/m,'/* No automatic boot in isolated payment-link UI test. */'));
   return route.fulfill({status:200,contentType:{'.js':'text/javascript','.css':'text/css','.html':'text/html','.svg':'image/svg+xml'}[path.extname(file)]||'application/octet-stream',body:content});
  });
  await page.goto('http://crm-link-guard.test/');
  await page.evaluate(()=>{S.user={id:1,role:'admin',name:'Синтетический руководитель',perms:{members:'full',payments:'full',export:'full'}};S.meta={today:'2026-11-20',groups:[{id:1,name:'Синтетическая группа',weekdays:'0,3'}],user:S.user};document.querySelector('#app').innerHTML='<div id="sell-box"></div><textarea id="cm-text"></textarea><div id="pay-links"></div>';window.copied=[];window.pasted=0;Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async value=>window.copied.push(value)}});document.querySelector('#cm-text').addEventListener('input',()=>window.pasted++);window.reloads=0;});
  const open=async()=>{await page.evaluate(async()=>{document.querySelector('#cm-text').value='Исходный черновик';window.copied=[];window.pasted=0;const d=await api('GET','/api/students/1');await openSell(d,()=>window.reloads++)});await page.waitForFunction(()=>document.querySelector('#sl-balance-preview [data-balance-payable]'))};
  const issue=async()=>{await page.locator('#sl-link').click();await page.waitForSelector('#sl-msg',{state:'visible'})};
  const openMulti=async()=>{await page.evaluate(async()=>{document.querySelector('#cm-text').value='Исходный черновик';window.copied=[];window.pasted=0;const d=await api('GET','/api/students/1');await openMultiMonthSale(d,1,document.querySelector('#sell-box'),()=>window.reloads++,{group_id:1,tariff:'fixed'})});await page.waitForSelector('[data-multi-save="links"]:not([disabled])')};
  const issueMulti=async()=>{await page.locator('[data-multi-save="links"]').click();await page.waitForSelector('[data-multi-result] textarea',{state:'visible'})};
  const copies=()=>page.evaluate(()=>window.copied.length),pastes=()=>page.evaluate(()=>window.pasted);
  const creates=()=>calls.filter(c=>c.method==='POST'&&c.path.endsWith('/paylinks'));
  const guardCalls=()=>calls.filter(c=>c.path.endsWith('/validate'));
  const waitHeld=async()=>{for(let i=0;i<200&&!release;i++)await new Promise(resolve=>setTimeout(resolve,5));ok(release,'Expected controlled in-flight request')};
  await open();eq(await page.locator('#sl-use-balance').isChecked(),false);eq(await page.locator('[data-balance-available]').innerText(),'0 ₽');eq(creates().length,0);
  await issue();eq(creates()[0].body.amount,9450);eq(await page.locator('#sl-msg').isEditable(),true);ok((await page.locator('#sl-msg').inputValue()).includes('9 450 ₽'));eq(guardCalls()[0].query.require_full,'1');eq(guardCalls()[0].query.student_id,'1');eq(guardCalls()[0].query.subscription_id,'10');eq(await copies(),0);eq(await pastes(),0);
  await page.locator('#sl-msg').fill('Здравствуйте! 😊\n'+await page.locator('#sl-msg').inputValue()+'\nДо встречи на занятиях!');
  await page.locator('#sl-copy').click();await page.waitForFunction(()=>window.copied.length===1);eq(await copies(),1);ok(await page.evaluate(()=>window.copied[0].startsWith('Здравствуйте! 😊')));eq(guardCalls().length,2);await page.locator('#sl-send').click();await page.waitForFunction(()=>window.pasted===1);eq(await pastes(),1);eq(guardCalls().length,3);ok((await page.locator('#cm-text').inputValue()).includes('9 450 ₽'));ok((await page.locator('#cm-text').inputValue()).includes('До встречи на занятиях!'));
  reset();await open();await issue();state.due=10000;await page.locator('#sl-copy').click();await page.waitForSelector('#sl-link-warning',{state:'visible'});eq(await copies(),0);eq(await pastes(),0);eq(await page.locator('#sl-msg').inputValue(),'');eq(await page.locator('#sl-copy').isDisabled(),true);ok((await page.locator('#sl-link-warning').innerText()).includes('10 000 ₽'));eq(creates().length,1);
  await page.locator('#sl-refresh-link').click();await page.waitForSelector('#sl-msg',{state:'visible'});eq(creates().length,2);eq(creates()[1].body.amount,10000);eq(await copies(),0);eq(await pastes(),0);ok((await page.locator('#sl-msg').inputValue()).includes('10 000 ₽'));await page.locator('#sl-copy').click();await page.waitForFunction(()=>window.copied.length===1);eq(await copies(),1);
  reset();await open();await issue();state.due=5000;await page.locator('#sl-send').click();await page.waitForSelector('#sl-link-warning',{state:'visible'});eq(await pastes(),0);eq(await page.locator('#cm-text').inputValue(),'Исходный черновик');eq(await copies(),0);eq(creates().length,1);
  reset({wrongIssuedAmount:8100});await open();await page.locator('#sl-link').click();await page.waitForSelector('#sl-link-warning',{state:'visible'});eq(await page.locator('#sl-link-content').isVisible(),false);eq(await page.locator('#sl-msg').inputValue(),'');eq(await copies(),0);eq(await pastes(),0);eq(creates().length,1);
  const reused=link();reset({links:[reused],invalidIds:new Set([99])});await open();await page.locator('#sl-link').click();await page.waitForSelector('#sl-link-warning',{state:'visible'});eq(creates().length,0);eq(await copies(),0);eq(await page.locator('#sl-link-content').isVisible(),false);
  state.invalidIds.clear();state.links=[];await page.locator('#sl-refresh-link').click();await page.waitForSelector('#sl-msg',{state:'visible'});eq(creates().length,1);eq(await copies(),0);
  reset({guardFails:true});await open();await page.locator('#sl-link').click();await page.waitForSelector('#sl-link-warning',{state:'visible'});eq(await copies(),0);eq(await page.locator('#sl-link-content').isVisible(),false);await page.locator('#sl-refresh-link').click();await page.waitForSelector('#sl-msg',{state:'visible'});eq(await copies(),0);
  reset({invalidSuccess:true});await open();await page.locator('#sl-link').click();await page.waitForSelector('#sl-link-warning',{state:'visible'});eq(await page.locator('#sl-link-content').isVisible(),false);eq(await copies(),0);
  reset({foreignSuccess:true});await open();await page.locator('#sl-link').click();await page.waitForSelector('#sl-link-warning',{state:'visible'});eq(await copies(),0);eq(await pastes(),0);
  reset();await open();await issue();await page.evaluate(()=>document.querySelector('#sl-msg').value='Старая сумма 1 ₽ http://crm-link-guard.test/pay/old');await page.locator('#sl-copy').click();await page.waitForSelector('#sl-link-warning',{state:'visible'});eq(await copies(),0);ok((await page.locator('#sl-link-warning').innerText()).includes('Ссылка в сообщении изменена'));
  reset();await open();await issue();await page.locator('#sl-msg').fill(await page.locator('#sl-msg').inputValue()+'\nhttp://crm-link-guard.test/pay/another');await page.locator('#sl-send').click();await page.waitForSelector('#sl-link-warning',{state:'visible'});eq(await copies(),0);eq(await pastes(),0);ok((await page.locator('#sl-link-warning').innerText()).includes('другая ссылка на оплату'));
  reset();await open();await issue();state.holdGuard=true;const pendingCopy=page.locator('#sl-copy').click();await waitHeld();await open();const unblock=release;release=null;unblock();await pendingCopy;eq(await copies(),0);eq(await pastes(),0);eq(await page.locator('#sl-use-balance').isChecked(),false);
  reset({holdCreate:true});await open();const pendingCreate=page.locator('#sl-link').click();await waitHeld();await open();const unblockCreate=release;release=null;unblockCreate();await pendingCreate;eq(await page.locator('#sl-msg').count(),0);eq(await copies(),0);eq(await pastes(),0);
  reset({createFails:true});await open();await page.locator('#sl-link').click();await page.waitForFunction(()=>!document.querySelector('#sl-link').disabled);await page.locator('#sl-link').click();await page.waitForSelector('#sl-msg',{state:'visible'});eq(creates().length,2);eq(await copies(),0);eq(await pastes(),0);
  reset();await open();await issue();state.due=0;await page.locator('#sl-copy').click();await page.waitForSelector('#sl-link-warning',{state:'visible'});await page.locator('#sl-refresh-link').click();await page.waitForFunction(()=>!document.querySelector('#sl-refresh-link').disabled);eq(creates().length,1);eq(await copies(),0);eq(await pastes(),0);
  reset({links:[link()]});await page.evaluate(()=>{window.copied=[];window.pasted=0;return loadClientPaymentLinks(1)});await page.locator('#pay-links > details > summary').click();state.due=10000;await page.locator('[data-plcopy]').click();await page.waitForSelector('[data-pl-warning]',{state:'visible'});eq(await copies(),0);eq(await page.locator('[data-plcopy]').isDisabled(),true);eq(guardCalls().at(-1).query.require_full,'0');await page.locator('[data-pl-renew]').click();await page.waitForFunction(()=>document.querySelectorAll('[data-plcopy]').length===2);eq(creates().length,1);eq(creates()[0].body.amount,10000);eq(await copies(),0);
  await page.locator('#pay-links > details > summary').click();await page.locator('[data-plcopy="100"]').click();await page.waitForFunction(()=>window.copied.length===1);eq(await copies(),1);
  reset({links:[link()]});await page.evaluate(()=>{window.copied=[];return loadClientPaymentLinks(1)});await page.locator('#pay-links > details > summary').click();state.holdGuard=true;const oldListCopy=page.locator('[data-plcopy]').click();await waitHeld();state.holdListing=true;const listingRefresh=page.evaluate(()=>loadClientPaymentLinks(1));for(let i=0;i<200&&!releaseList;i++)await new Promise(resolve=>setTimeout(resolve,5));ok(releaseList);eq(await page.locator('[data-plcopy]').count(),1);
  const guardResponse=page.waitForResponse(response=>response.url().includes('/validate'));const unblockList=release;release=null;unblockList();await guardResponse;await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));eq(await copies(),0,'A late guard must not copy while a newer listing fetch is still pending');const finishList=releaseList;releaseList=null;finishList();await listingRefresh;await oldListCopy;eq(await copies(),0);
  reset();await openMulti();eq(calls.filter(c=>c.path.endsWith('/multi-month-sale')).length,0);await issueMulti();eq(guardCalls().length,1);eq(guardCalls()[0].query.require_full,'1');eq(guardCalls()[0].query.subscription_ids,'10,11');eq(guardCalls()[0].query.subscription_id,undefined);eq(await copies(),0);eq(await pastes(),0);
  const multiMessage=page.locator('[data-multi-result] textarea');eq(await multiMessage.isEditable(),true);await multiMessage.fill('Здравствуйте! 😊\n'+await multiMessage.inputValue()+'\nДо встречи!');await page.locator('[data-multi-result] [data-copy]').click();await page.waitForFunction(()=>window.copied.length===1);ok(await page.evaluate(()=>window.copied[0].startsWith('Здравствуйте! 😊')));eq(guardCalls().length,2);await page.locator('[data-multi-result] [data-chat]').click();await page.waitForFunction(()=>window.pasted===1);ok((await page.locator('#cm-text').inputValue()).endsWith('До встречи!'));eq(guardCalls().length,3);
  reset();await openMulti();await issueMulti();state.bundleDue=20000;await page.locator('[data-multi-result] [data-copy]').click();await page.waitForSelector('[data-multi-link-warning]',{state:'visible'});eq(await copies(),0);eq(await pastes(),0);eq(await page.locator('[data-multi-result] [data-copy]').isDisabled(),true);eq(await page.locator('[data-multi-result] [data-chat]').isDisabled(),true);ok((await page.locator('[data-multi-link-warning]').innerText()).includes('20 000 ₽'));eq(calls.filter(c=>c.path.endsWith('/multi-month-sale')).length,1);
  reset();await openMulti();await issueMulti();state.bundleDue=10000;await page.locator('[data-multi-result] [data-chat]').click();await page.waitForSelector('[data-multi-link-warning]',{state:'visible'});eq(await pastes(),0);eq(await page.locator('#cm-text').inputValue(),'Исходный черновик');eq(await copies(),0);
  reset({wrongIssuedAmount:18000});await openMulti();await page.locator('[data-multi-save="links"]').click();await page.waitForSelector('[data-multi-link-warning]',{state:'visible'});eq(await page.locator('[data-multi-result] textarea').isVisible(),false);eq(await copies(),0);eq(await pastes(),0);
  reset({bundleWrongScope:true});await openMulti();await page.locator('[data-multi-save="links"]').click();await page.waitForSelector('[data-multi-link-warning]',{state:'visible'});eq(await page.locator('[data-multi-result] textarea').isVisible(),false);eq(await copies(),0);
  reset();await openMulti();await issueMulti();await multiMessage.fill(await multiMessage.inputValue()+'\nhttp://crm-link-guard.test/pay/another');await page.locator('[data-multi-result] [data-chat]').click();await page.waitForSelector('[data-multi-link-warning]',{state:'visible'});eq(await pastes(),0);eq(await copies(),0);
  reset();await openMulti();await issueMulti();state.holdGuard=true;const oldBundleCopy=page.locator('[data-multi-result] [data-copy]').click();await waitHeld();await openMulti();const oldBundleResponse=page.waitForResponse(response=>response.url().includes('/validate'));const unblockBundle=release;release=null;unblockBundle();await oldBundleResponse;await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));await oldBundleCopy;eq(await copies(),0);eq(await pastes(),0);eq(await page.locator('[data-multi-result] textarea').count(),0);
  reset({holdBundleCreate:true});await openMulti();const oldBundleCreate=page.locator('[data-multi-save="links"]').click();await waitHeld();await openMulti();const oldSaleResponse=page.waitForResponse(response=>response.url().endsWith('/multi-month-sale'));const unblockBundleCreate=release;release=null;unblockBundleCreate();await oldSaleResponse;await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));await oldBundleCreate;eq(await page.locator('[data-multi-result] textarea').count(),0);eq(guardCalls().length,0);eq(await copies(),0);eq(await pastes(),0);
  reset({actualGuard:'valid'});const checked=await page.evaluate(d=>validatePaymentLinkForSharing(d.link,d.student_id,d.subscription_id,true),actual);eq(checked.amount,9450);eq(checked.current_due,9450);eq(checked.read_only,true);eq(guardCalls()[0].query.require_full,'1');
  state.actualGuard='invalid';const blocked=await page.evaluate(async d=>{try{await validatePaymentLinkForSharing(d.link,d.student_id,d.subscription_id,true);return false}catch(error){return !!error.message}},actual);eq(blocked,true);eq(await copies(),0);
  const publicPage=await browser.newPage();let checkoutCalls=0;await publicPage.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url());if(url.origin!=='http://crm-public-fixture.test')throw Error('External public-page request forbidden');
   if(request.method()!=='GET'){checkoutCalls++;return route.fulfill({status:403,body:'No checkout is permitted'})}
   if(url.pathname.startsWith('/api/public/pay-links/'))return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(actual.public)});
   const file=path.join(assets,url.pathname.startsWith('/pay/')?'payment.html':url.pathname.slice(1));return route.fulfill({status:200,contentType:path.extname(file)==='.js'?'text/javascript':'text/html',body:fs.readFileSync(file)});
  });await publicPage.goto('http://crm-public-fixture.test/pay/synthetic-local-token');await publicPage.waitForSelector('#billing',{state:'visible'});eq(await publicPage.locator('#amount').innerText(),'9 450 ₽');ok((await publicPage.locator('#details').innerText()).includes('7'));eq(await publicPage.locator('#partial').isVisible(),false);eq(await publicPage.locator('#continue').innerText(),'Оплатить 9 450 ₽');eq(checkoutCalls,0);await publicPage.close();
  eq(calls.filter(c=>c.method==='POST'&&!c.path.endsWith('/paylinks')).length,0);eq(errors.length,0,errors.join('\n'));console.log('Payment-link amount guard UI: '+checks+' assertions passed; no real provider or client messages');
 }finally{if(release)release();if(releaseList)releaseList();if(browser)await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
