/* Sale form regression. Run: node tests/test_invoice_balance_credit.cjs
 * Uses the actual index/app.js and an isolated in-memory API double. No CRM,
 * bank, PayKeeper or fiscal service is contacted. The backend has separate tests.
 * Optional: PLAYWRIGHT_MODULE=/existing/playwright CHROMIUM_PATH=/browser
 */
const fs=require('fs'),path=require('path'),assert=require('assert'),cp=require('child_process');
let playwright;
try{playwright=require(process.env.PLAYWRIGHT_MODULE||'playwright')}
catch(error){const existing='/tmp/period-check/node_modules/playwright';if(!process.env.PLAYWRIGHT_MODULE&&fs.existsSync(existing))playwright=require(existing);else throw Error('Set PLAYWRIGHT_MODULE to an existing Playwright installation')}
const root=path.resolve(__dirname,'..'),staticRoot=path.join(root,'crm','static');
let checks=0;const eq=(a,b,note)=>{assert.strictEqual(a,b,note);checks++},ok=(v,note)=>{assert(v,note);checks++};
(async()=>{
  let browser,state,calls=[],errors=[],held=null,releaseHeld=null;
  const actualPython=`import json
from datetime import date
from unittest.mock import patch
from tests.test_invoice_balance_flows import InvoiceBalanceFlowsTest
from crm import services as sv, balancecredit, money, server
from tests.test_invoice_balance_credit import InvoiceBalanceCreditTest
f=InvoiceBalanceFlowsTest(); f.setUp()
try:
 original=f.con.serialize()
 off=f.quote(False); on=f.quote(True)
 assert f.con.serialize()==original
 created=f.invoice(False); sid=created['id']
 original=f.con.serialize()
 existing_off=sv.payment_quote(f.con,sid,'2026-11-20',False)
 existing_on=sv.payment_quote(f.con,sid,'2026-11-20',True)
 assert f.con.serialize()==original
 balancecredit.apply_to_invoice(f.con,sid,f.u,True,existing_on['balance_preview']['token'],'browser-fixture-apply-001')
 original=f.con.serialize()
 applied=sv.payment_quote(f.con,sid,'2026-11-20',False)
 assert f.con.serialize()==original
 result={'off':off,'on':on,'existing_off':existing_off,'existing_on':existing_on,'applied':applied}
finally:f.doCleanups()
g=InvoiceBalanceCreditTest();g.setUp()
try:
 old=g.invoice(10000,'2026-10',protected=False,status='paid');new=g.invoice()
 pid=g.payment(30000,day='2026-09-28',name='ERP')
 snapshot=g.snapshot([pid],{old:10000});g.fund(snapshot_id=snapshot)
 result['fund_preview']=balancecredit.preview(g.con,1,12150,False,new)
 g.apply(new)
 original=g.con.serialize();result['accounting_details']=money.subscription_payment_details(g.con,1)
 assert g.con.serialize()==original
finally:g.doCleanups()
h=InvoiceBalanceFlowsTest();h.setUp()
try:
 sub=h.invoice(False)['id']
 h.con.execute("UPDATE subscriptions SET created_at='2026-11-20T12:00' WHERE id=?",(sub,))
 h.con.execute('UPDATE price_list SET late_pct=5')
 result['late_native_before']=h.con.execute('SELECT price FROM subscriptions WHERE id=?',(sub,)).fetchone()[0]
 with patch.object(sv,'today',return_value=date(2026,11,26)):
  original=h.con.serialize()
  result['late_off']=sv.payment_quote(h.con,sub,'2026-11-26',False)
  result['late_on']=sv.payment_quote(h.con,sub,'2026-11-26',True)
  assert h.con.serialize()==original
  assert result['late_off']['price']==12758 and result['late_on']['balance_preview']['payable']==10058
  body={'use_balance':True,'expected_balance_token':result['late_on']['balance_preview']['token'],'request_id':'browser-late-apply-001','paid_date':'2026-11-26'}
  route=next(fn for method,rx,fn,role in server.ROUTES if method=='POST' and rx.match(f'/api/subscriptions/{sub}/balance-apply'))
  result['late_reply']=route(h.con,None,h.u,body,{},str(sub))
  original=h.con.serialize();result['late_after']=sv.payment_quote(h.con,sub,'2026-11-26',False)
  assert h.con.serialize()==original
  assert result['late_after']['price']==12758 and result['late_after']['total']==10058
  assert h.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0]==1
finally:h.doCleanups()
print(json.dumps(result,ensure_ascii=False))
`;
  const actual=JSON.parse(cp.execFileSync(process.env.PYTHON||'python',['-c',actualPython],{cwd:root,encoding:'utf8'}));
  const reset=extra=>{state={price:12150,deposit:2700,paid:0,already:0,existing:false,created:0,applications:0,requests:new Map(),...extra};calls=[]};
  const preview=use=>{const apply=use&&!state.already?Math.min(state.deposit,Math.max(0,state.price-state.paid)):0;return {use_balance:use,available_balance:state.deposit,apply_amount:apply,invoice_total:state.price,payable:Math.max(0,state.price-state.paid-apply),remaining_balance:state.deposit-apply,already_applied:state.already,token:[state.price,state.deposit,state.paid,state.already,use?'on':'off'].join(':')}};
  const quote=(month='2026-12',price=state.price)=>({total:price,base:price,lessons_paid:1,per_lesson:price,lessons:1,tariff:'fixed',dates:[month+'-02'],calendar_dates:[month+'-02'],excluded_dates:[],credit_lessons:0,late_fee:0,credit_available:0,pricing:{},month});
  const student=()=>({student:{id:1,name:'Синтетический клиент',group_id:1,tariff:'fixed'},cur_month:'2026-11',next_month:'2026-12',balance:{deposit:state.deposit},enrollments:[{id:1,group_id:1,group_name:'Тестовая группа',tariff:'fixed'}],subscriptions:state.existing?[{id:10,group_id:1,enrollment_id:1,group_name:'Тестовая группа',month:'2026-12',tariff:'fixed',price:state.price,status:'invoiced',kind:'month',lesson_schedule:'[{"dates":["2026-12-02"]}]'}]:[],discounts:[],trial_visits:[]});
  const json=async(route,data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
  reset();
  try{
    const launch={headless:true,args:['--no-sandbox']},executable=process.env.CHROMIUM_PATH||(fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':null);if(executable)launch.executablePath=executable;
    browser=await playwright.chromium.launch(launch);const page=await browser.newPage({viewport:{width:1440,height:1100}});
    page.on('pageerror',e=>errors.push(e.message));
    await page.route('**/*',async route=>{
      const req=route.request(),url=new URL(req.url()),method=req.method(),body=req.postDataJSON();
      if(url.origin!=='http://crm-invoice.test')throw Error('External request forbidden: '+url.origin);
      if(url.pathname.startsWith('/api/')){
        calls.push({method,path:url.pathname,query:Object.fromEntries(url.searchParams),body});
        if(method==='GET'&&url.pathname==='/api/students/1')return json(route,student());
        if(method==='GET'&&url.pathname==='/api/students/1/required')return json(route,{missing:[],optional:[]});
        if(method==='GET'&&url.pathname==='/api/students/1/paylinks')return json(route,{ready:true,links:[]});
        if(method==='GET'&&url.pathname==='/api/students/1/quote'){
          const data=state.actualDtos?structuredClone(actual[url.searchParams.get('use_balance')==='1'?'on':'off']):{...quote(url.searchParams.get('month')),balance_preview:preview(url.searchParams.get('use_balance')==='1')};
          if(state.omitPreview)delete data.balance_preview;
          if(state.holdSelected&&url.searchParams.get('use_balance')==='1'){held=new Promise(resolve=>releaseHeld=resolve);await held}
          return json(route,data);
        }
        if(method==='GET'&&url.pathname==='/api/students/1/single-quote')return json(route,{...quote(),single:true});
        if(method==='GET'&&url.pathname==='/api/subscriptions/10/payment-quote')return json(route,state.actualDtos==='late'?actual[state.already?'late_after':url.searchParams.get('use_balance')==='1'?'late_on':'late_off']:state.actualDtos?actual[state.already?'applied':url.searchParams.get('use_balance')==='1'?'existing_on':'existing_off']:{id:10,price:state.price,paid:state.paid,total:state.price-state.paid,balance_preview:preview(url.searchParams.get('use_balance')==='1')});
        if(method==='POST'&&['/api/students/1/invoice','/api/subscriptions/10/balance-apply'].includes(url.pathname)){
          if(state.actualDtos==='late'&&url.pathname.endsWith('/balance-apply')){
            eq(body.paid_date,'2026-11-26');eq(body.expected_balance_token,actual.late_on.balance_preview.token);eq(body.use_balance,true);
            state.price=actual.late_after.price;state.paid=2700;state.deposit=0;state.already=2700;state.applications++;
            return json(route,actual.late_reply);
          }
          const key=url.pathname+':'+body.request_id;
          if(state.requests.has(key))return json(route,state.requests.get(key));
          if(state.rejectWrite||body.expected_balance_token!==preview(body.use_balance===true).token)return json(route,{error:'Расчёт баланса изменился — обновите форму'},409);
          eq(typeof body.use_balance,'boolean');ok(typeof body.request_id==='string'&&body.request_id.length>8);
          if(url.pathname.endsWith('/invoice')){state.existing=true;state.created++}
          const credit=preview(body.use_balance).apply_amount;
          if(credit){state.deposit-=credit;state.paid+=credit;state.already+=credit;state.applications++}
          const result={id:10,quote:quote(),balance_preview:preview(false)};state.requests.set(key,result);
          if(state.loseApplyResponse&&url.pathname.endsWith('/balance-apply')){state.loseApplyResponse=false;return json(route,{error:'Ответ потерян после сохранения'},500)}
          return json(route,result);
        }
        if(method==='POST'&&url.pathname==='/api/students/1/paylinks'){
          eq(body.amount,state.price-state.paid);eq(body.full_payment,true);
          if(state.failLinkOnce){state.failLinkOnce=false;return json(route,{error:'Временный сбой тестового источника'},503)}
          return json(route,{id:99,amount:body.amount,url:'https://example.invalid/synthetic-link'});
        }
        if(method==='POST'&&url.pathname==='/api/subscriptions/10/pay'){
          if(state.failPayOnce){state.failPayOnce=false;return json(route,{error:'Временный сбой сохранения тестовой оплаты'},503)}
          return json(route,{ok:true});
        }
        if(method==='POST'&&url.pathname==='/api/students/1/multi-month-preview'){
          const balance=preview(body.use_balance===true);return json(route,{months:body.months.map(month=>({month,quote:quote(month,6075),excluded_dates:[]})),total:12150,credit:balance.apply_amount,due:balance.payable,balance_preview:balance,token:'multi:'+balance.token});
        }
        if(method==='POST'&&url.pathname==='/api/students/1/multi-month-sale'){
          eq(typeof body.use_balance,'boolean');eq(body.expected_token,'multi:'+preview(body.use_balance).token);eq(body.mode,'invoice');
          const credit=preview(body.use_balance).apply_amount;state.deposit-=credit;state.paid+=credit;state.applications+=credit?1:0;state.created+=2;
          return json(route,{subscriptions:[{id:10,month:'2026-11',price:6075},{id:11,month:'2026-12',price:6075}],total:12150,due:12150-credit});
        }
        throw Error('Unexpected API: '+method+' '+url.pathname);
      }
      const file=path.join(staticRoot,url.pathname==='/'?'index.html':url.pathname.slice(1));
      if(!file.startsWith(staticRoot+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile())return route.fulfill({status:404,body:''});
      let content=fs.readFileSync(file);if(url.pathname==='/app.js')content=Buffer.from(content.toString().replace(/^boot\(\);$/m,'/* Automatic boot disabled for isolated sale test. */'));
      return route.fulfill({status:200,contentType:{'.js':'text/javascript','.css':'text/css','.html':'text/html','.svg':'image/svg+xml'}[path.extname(file)]||'application/octet-stream',body:content});
    });
    await page.goto('http://crm-invoice.test/');
    await page.evaluate(()=>{S.user={id:1,name:'Тестовый руководитель',role:'admin',perms:{members:'full',payments:'full',export:'full'}};S.meta={today:'2026-11-20',groups:[{id:1,name:'Тестовая группа',weekdays:'2',lesson_kind:'football',per_lesson:12150}],user:S.user};document.querySelector('#app').innerHTML='<div id="sell-box"></div>';window.reloads=0;});
    const open=async()=>{await page.evaluate(async()=>{const d=await api('GET','/api/students/1');await openSell(d,()=>window.reloads++)});await page.waitForFunction(()=>document.querySelector('#sl-balance-preview [data-balance-payable]'))};
    const values=async()=>page.locator('#sl-balance-preview [data-sale-balance-preview] b').allTextContents();
    const toggle=async on=>{await page.locator('#sl-use-balance').setChecked(on);await page.waitForFunction(expected=>document.querySelector('#sl-balance-preview [data-balance-payable]')?.textContent===expected,on?'9 450 ₽':'12 150 ₽')};
    const writes=()=>calls.filter(c=>c.method!=='GET');
    await open();eq(await page.locator('#sl-use-balance').isChecked(),false);assert.deepStrictEqual(await values(),['2 700 ₽','0 ₽','12 150 ₽','2 700 ₽']);checks++;
    eq(writes().length,0);eq(calls.find(c=>c.path.endsWith('/quote')).query.use_balance,'0');
    await toggle(true);assert.deepStrictEqual(await values(),['2 700 ₽','2 700 ₽','9 450 ₽','0 ₽']);checks++;
    eq(writes().length,0);ok((await page.locator('#sl-q .total').innerText()).includes('12 150 ₽'),'The price stays unchanged');
    await toggle(false);eq(writes().length,0);eq(state.deposit,2700);
    await page.locator('#sl-x').click();await open();eq(await page.locator('#sl-use-balance').isChecked(),false);eq(state.deposit,2700);
    await page.locator('#sl-link').click();await page.waitForSelector('#sl-msg');
    eq(calls.find(c=>c.path.endsWith('/invoice')).body.use_balance,false);eq(state.deposit,2700);eq(state.applications,0);eq(state.created,1);ok((await page.locator('#sl-msg').inputValue()).includes('12 150 ₽'));
    reset({failLinkOnce:true});await open();await toggle(true);await page.locator('#sl-link').click();await page.waitForFunction(()=>!document.querySelector('#sl-link').disabled);
    eq(state.created,1);eq(state.applications,1);eq(state.deposit,0);eq(await page.locator('#sl-use-balance').isDisabled(),true);
    await page.locator('#sl-link').click();await page.waitForSelector('#sl-msg');eq(state.created,1);eq(state.applications,1);eq(state.deposit,0);eq(calls.filter(c=>c.path.endsWith('/invoice')).length,1);ok((await page.locator('#sl-msg').inputValue()).includes('9 450 ₽'));
    await open();eq(state.applications,1);eq(await page.locator('#sl-use-balance').isChecked(),false);eq(await page.locator('#sl-use-balance').isDisabled(),true);ok((await page.locator('[data-balance-applied]').innerText()).includes('2 700 ₽'));
    reset({existing:true,loseApplyResponse:true});await open();await toggle(true);eq(writes().length,0);
    await page.locator('#sl-link').click();await page.waitForFunction(()=>!document.querySelector('#sl-link').disabled);eq(state.applications,1);eq(state.deposit,0);
    await page.locator('#sl-link').click();await page.waitForSelector('#sl-msg');eq(state.applications,1);eq(state.created,0);const applies=calls.filter(c=>c.path.endsWith('/balance-apply'));eq(applies.length,2);eq(applies[0].body.request_id,applies[1].body.request_id);eq(applies[0].body.expected_balance_token,applies[1].body.expected_balance_token);
    reset({rejectWrite:true});await open();await toggle(true);await page.locator('#sl-now').click();eq(writes().length,0);eq(await page.locator('#sl-amount').inputValue(),'9450');await page.locator('#sl-ok').click();await page.waitForFunction(()=>document.querySelector('#sl-payment-error').textContent.length>0);eq(state.applications,0);eq(state.created,0);eq(calls.filter(c=>c.path.endsWith('/pay')).length,0);
    reset({failPayOnce:true});await open();await toggle(true);await page.locator('#sl-now').click();await page.locator('#sl-ok').click();await page.waitForFunction(()=>document.querySelector('#sl-payment-error').textContent.length>0);eq(state.applications,1);eq(state.created,1);eq(state.deposit,0);
    await page.locator('#sl-ok').click();await page.waitForFunction(()=>document.querySelector('#sl-ok').disabled);const pays=calls.filter(c=>c.path.endsWith('/pay'));eq(pays.length,2);eq(pays[0].body.request_id,pays[1].body.request_id);eq(pays[0].body.amount,9450);eq(state.applications,1);eq(state.created,1);
    reset();await open();await toggle(true);await page.evaluate(()=>Promise.all([document.querySelector('#sl-link').onclick(),document.querySelector('#sl-link').onclick()]));await page.waitForSelector('#sl-msg');eq(state.created,1);eq(state.applications,1);eq(calls.filter(c=>c.path.endsWith('/invoice')).length,1);
    reset({price:2700});await open();await page.locator('#sl-use-balance').check();await page.waitForFunction(()=>document.querySelector('[data-balance-payable]')?.textContent==='0 ₽');eq(await page.locator('#sl-link').innerText(),'Зачесть с баланса');eq(await page.locator('#sl-link').isDisabled(),false);eq(await page.locator('#sl-now').isDisabled(),true);
    await page.locator('#sl-link').click();await page.waitForFunction(()=>document.querySelector('[data-balance-applied]'));eq(state.applications,1);eq(state.deposit,0);eq(calls.filter(c=>c.path.endsWith('/paylinks')).length,0);eq(calls.filter(c=>c.path.endsWith('/pay')).length,0);
    reset({price:2700,existing:true});await open();await page.locator('#sl-use-balance').check();await page.waitForFunction(()=>document.querySelector('[data-balance-payable]')?.textContent==='0 ₽');eq(await page.locator('#sl-q>b').innerText(),'Абонемент уже оформлен');eq(state.applications,0);eq(writes().length,0);
    reset({omitPreview:true});await page.evaluate(async()=>{const d=await api('GET','/api/students/1');await openSell(d,()=>window.reloads++)});await page.waitForFunction(()=>document.querySelector('#sl-q').textContent.includes('Не удалось проверить баланс'));eq(await page.locator('#sl-link').isDisabled(),true);eq(await page.locator('#sl-now').isDisabled(),true);eq(writes().length,0);
    reset({holdSelected:true});await open();await page.locator('#sl-use-balance').check();await page.waitForFunction(()=>document.querySelector('#sl-use-balance').checked);while(!releaseHeld)await new Promise(resolve=>setTimeout(resolve,5));
    await page.locator('#sl-use-balance').uncheck();await page.waitForFunction(()=>document.querySelector('[data-balance-payable]')?.textContent==='12 150 ₽');releaseHeld();releaseHeld=null;await page.waitForLoadState('networkidle');eq(await page.locator('[data-balance-payable]').innerText(),'12 150 ₽');eq(await page.locator('#sl-use-balance').isChecked(),false);eq(writes().length,0);
    reset();await open();await page.locator('#sl-kind').selectOption('single');await page.waitForFunction(()=>document.querySelector('#sl-balance-box').hidden);eq(await page.locator('#sl-balance-box').isVisible(),false);eq(writes().length,0);
    reset();await open();await page.locator('#sl-kind').selectOption('multi');await page.waitForSelector('[data-multi-quote] [data-balance-payable]');
    eq(await page.locator('[name=use_balance]').isChecked(),false);eq(await page.locator('[data-multi-quote] [data-balance-payable]').innerText(),'12 150 ₽');eq(state.applications,0);eq(writes().filter(c=>!c.path.endsWith('/multi-month-preview')).length,0);
    await page.locator('[name=use_balance]').check();await page.waitForFunction(()=>document.querySelector('[data-multi-quote] [data-balance-payable]')?.textContent==='9 450 ₽');eq(state.deposit,2700);eq(state.applications,0);
    await page.locator('[data-multi-save="invoice"]').click();await page.waitForSelector('[data-multi-result] textarea');eq(state.applications,1);eq(state.deposit,0);eq(calls.find(c=>c.path.endsWith('/multi-month-sale')).body.use_balance,true);
    reset({actualDtos:true});await open();eq(await page.locator('[data-balance-payable]').innerText(),'12 150 ₽');await toggle(true);eq(await page.locator('[data-balance-apply]').innerText(),'2 700 ₽');eq(writes().length,0);ok((await page.locator('#sl-q .total').innerText()).includes('12 150 ₽'));
    reset({actualDtos:true,existing:true});await open();await toggle(true);eq(await page.locator('[data-balance-payable]').innerText(),'9 450 ₽');eq(writes().length,0);
    reset({actualDtos:true,existing:true,already:2700,paid:2700,deposit:0});await open();eq(await page.locator('#sl-use-balance').isChecked(),false);eq(await page.locator('#sl-use-balance').isDisabled(),true);eq(await page.locator('[data-balance-payable]').innerText(),'9 450 ₽');ok((await page.locator('[data-balance-applied]').innerText()).includes('2 700 ₽'));eq(writes().length,0);
    const beforeRenderCalls=calls.length;
    await page.evaluate(data=>{
      const section=document.createElement('section');section.id='accounting-record-test';document.querySelector('#app').append(section);
      const rows=Object.values(data.accounting_details).flat();
      section.innerHTML=subscriptionPaymentDetailsHtml({id:99},{subscription_payment_details:{99:rows}});
      const labels=document.createElement('div');labels.id='accounting-balance-labels';section.append(labels);
      labels.innerHTML=invoiceBalancePreviewHtml(data.fund_preview)+clientBalanceHtml({balance:{deposit:2700,debt:0,upcoming:12150,balance_labels:data.fund_preview.labels},payments:[],family_kids:[]});
      const escaped=document.createElement('div');escaped.id='accounting-escaped';section.append(escaped);
      escaped.innerHTML=subscriptionPaymentDetailsHtml({id:100},{subscription_payment_details:{100:[{record_kind:'balance_credit',payment_id:null,amount:2700,method_label:'Зачтено <из источника>',fund_label:'На следующий турнир <img src=x>',paid_at:null,receipt:{status:'unverified',label:'Чек <исходной оплаты> требует сверки'}},{record_kind:'historical_settlement',payment_id:null,receipt:{status:'unverified'}}]}});
    },actual);
    const accounting=page.locator('#accounting-record-test'),fund=accounting.locator('[data-payment-record="balance_credit"]').first(),historical=accounting.locator('[data-payment-record="historical_settlement"]').first();
    eq(await fund.locator('strong').innerText(),'2 700 ₽');ok((await fund.innerText()).includes('Зачтено с баланса'));ok((await fund.innerText()).includes('На следующий турнир'));ok((await fund.innerText()).includes('Чек исходной оплаты требует сверки'));
    eq(await historical.locator('strong').innerText(),'10 000 ₽');ok((await historical.innerText()).includes('Оплата сохранена по сверке ERP'));
    eq(await accounting.locator('[data-payment-record] [data-fiscal-check]').count(),0);eq(await accounting.locator('[data-payment-record] button').count(),0);eq(await accounting.locator('[data-payment-record] .pill.green').count(),0);
    eq(await accounting.locator('[data-payment-record] [data-receipt-status="unverified"]').count(),4);
    ok(!(await fund.innerText()).includes('0 ₽\n'));ok(!(await fund.innerText()).includes('Дата и время'));ok(!(await fund.innerText()).includes('Зачтено из платежа'));
    ok((await page.locator('#accounting-balance-labels').innerText()).includes('На следующий турнир'));eq(await page.locator('#accounting-balance-labels [data-balance-labels]').count(),2);
    const escaped=page.locator('#accounting-escaped');eq(await escaped.locator('img,script').count(),0);ok((await escaped.innerText()).includes('На следующий турнир <img src=x>'));ok((await escaped.innerText()).includes('Зачтено <из источника>'));ok((await escaped.innerText()).includes('Чек <исходной оплаты> требует сверки'));ok((await escaped.innerText()).includes('Сумма требует сверки'));
    eq(calls.length,beforeRenderCalls,'Accounting-only rows must not issue API requests');
    eq(actual.late_native_before,12150);eq(actual.late_off.price,12758);eq(actual.late_on.balance_preview.invoice_total,12758);eq(actual.late_on.balance_preview.payable,10058);
    reset({existing:true,actualDtos:'late'});await page.evaluate(()=>{S.meta.today='2026-11-26'});await open();eq(await page.locator('#sl-paid-date').inputValue(),'2026-11-26');eq(await page.locator('#sl-balance-preview [data-balance-payable]').innerText(),'12 758 ₽');eq(writes().length,0);
    await page.locator('#sl-use-balance').check();await page.waitForFunction(()=>document.querySelector('#sl-balance-preview [data-balance-payable]')?.textContent==='10 058 ₽');eq(writes().length,0);ok((await page.locator('#sl-q').innerText()).includes('12 758 ₽'));
    await page.locator('#sl-link').click();await page.waitForSelector('#sl-msg');eq(state.applications,1);eq(state.deposit,0);eq(calls.filter(c=>c.path.endsWith('/balance-apply')).length,1);ok((await page.locator('#sl-msg').inputValue()).includes('10 058 ₽'));
    const lateGet=calls.find(c=>c.path.endsWith('/payment-quote')&&c.query.use_balance==='1'),latePost=calls.find(c=>c.path.endsWith('/balance-apply'));eq(latePost.body.paid_date,lateGet.query.paid_date);
    eq(errors.length,0,errors.join('\n'));console.log('Invoice balance credit UI: '+checks+' checks passed; isolated retries plus actual temporary backend DTOs; no live services');
  }finally{if(releaseHeld)releaseHeld();if(browser)await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)});
