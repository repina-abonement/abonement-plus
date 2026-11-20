/* Owner recovery form, actual renderer and isolated API double. No working data.
 * Run: node tests/test_mamedov_balance_recovery_ui.cjs
 * Optional PLAYWRIGHT_MODULE and CHROMIUM_PATH use existing installations.
 */
const fs=require('fs'),path=require('path'),assert=require('assert'),cp=require('child_process');
let playwright;try{playwright=require(process.env.PLAYWRIGHT_MODULE||'playwright')}catch(error){const p='/tmp/period-check/node_modules/playwright';if(!process.env.PLAYWRIGHT_MODULE&&fs.existsSync(p))playwright=require(p);else throw Error('Set PLAYWRIGHT_MODULE to existing Playwright')}
const root=path.resolve(__dirname,'..');let checks=0;const eq=(a,b,n)=>{assert.strictEqual(a,b,n);checks++},ok=(v,n)=>{assert(v,n);checks++};
const before={invoice:{id:7163,month:'2026-12',price:9450,lessons:7,covered:9450,payable:0},balance:{deposit:12115,free_deposit:12115},payments_count:38,purchases_count:36};
const after={invoice:{id:7163,month:'2026-12',price:9450,lessons:7,covered:0,payable:9450},balance:{deposit:0,free_deposit:0},payments_count:38,purchases_count:36};
const plan={erp_id:'88202',student_id:1719,can_apply:true,plan_ready:true,preview_on_copy:true,production_apply_available:true,token:'synthetic-checked-owner-token',before,without_balance:after,issues:[]};
const run={run_id:71,erp_id:'88202',student_id:1719,state:'applied',before_summary:before,after_summary:after,current_summary:after,can_rollback:true,rollback_token:'synthetic-exact-rollback-token',created_at:'2026-11-20T18:00:00+03:00',backup_filename:'synthetic-safe-copy.sqlite3',payment_link:{id:10,amount:9450,url:'https://ab-plus.ru/pay/synthetic-not-a-real-payment-token'}};
(async()=>{let browser;try{
 const actual=JSON.parse(cp.execFileSync(process.env.PYTHON||'python',['-c',`import json
from tests.test_erp_balance_recovery_web import ERPBalanceRecoveryWebTest
from crm import erpbalancerecoveryweb as web, money
f=ERPBalanceRecoveryWebTest();f.setUp()
try:
 original=f.con.serialize();plan=web.plan(f.con,'88202');assert f.con.serialize()==original
 reply=web.apply(f.con,f.body(),f.user)
 original=f.con.serialize();reopened=web.plan(f.con,'88202');assert f.con.serialize()==original
 f.con.execute("INSERT INTO payments(student_id,amount,method,paid_at,comment,user_name,subscription_id,confirmed,created_at) VALUES(1,9450,'card_branch','2026-11-20','Synthetic later received cash','Synthetic owner',2,1,'2026-11-20')")
 money._reconcile(f.con,1)
 original=f.con.serialize();after_payment=web.plan(f.con,'88202');assert f.con.serialize()==original
 assert after_payment['without_balance']['invoice']['payable']==9450 and after_payment['current_summary']['invoice']['payable']==0
 print(json.dumps({'plan':plan,'apply':reply,'reopened':reopened,'after_payment':after_payment},ensure_ascii=False))
finally:f.doCleanups()
`],{cwd:root,encoding:'utf8'}));
 const launch={headless:true,args:['--no-sandbox']},exe=process.env.CHROMIUM_PATH||(fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':null);if(exe)launch.executablePath=exe;browser=await playwright.chromium.launch(launch);const page=await browser.newPage();
 await page.route('**/*',route=>route.abort());await page.setContent('<main id="box"></main>');await page.addStyleTag({content:fs.readFileSync(path.join(root,'crm/static/style.css'),'utf8')});
 await page.evaluate(f=>{
  window.S={user:{role:'operator'}};window.f=f;window.calls=[];window.opened=[];window.state={run:null,mode:'ready',holdApply:false,holdPlan:false};
  window.esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));window.rub=n=>Number(n).toLocaleString('ru-RU')+' ₽';window.openStudent=(id,options)=>window.opened.push({id,options});
  window.api=async(method,url,body)=>{
   calls.push({method,url,body});
   if(state.actualStage){if(method==='GET'&&url.includes('/context?'))return structuredClone(f.actual[state.actualStage]);if(method==='POST'&&url.endsWith('/plan'))return structuredClone(f.actual[state.actualStage]);if(method==='POST'&&url.endsWith('/apply')){state.actualStage='reopened';return structuredClone(f.actual.apply)}throw Error('Unexpected actual fixture endpoint')}
   if(method==='GET'&&url==='/api/erp/balance-recovery/context?erp_id=88202'){if(state.mode==='context-failed')throw Error('Не удалось перечитать состояние');return {erp_id:'88202',student_id:1719,last_run:structuredClone(state.contextRun||state.run)}}
   if(method==='POST'&&url==='/api/erp/balance-recovery/plan'){
    if(state.holdPlan){state.holdPlan=false;await new Promise(resolve=>window.releasePlan=resolve)}
    if(state.run?.state==='applied')return {...structuredClone(f.plan),already_applied:true,can_apply:false,last_run:structuredClone(state.run)};
    if(state.mode==='blocked')return {...structuredClone(f.plan),can_apply:false,issues:['Финансовые записи изменились. Нужна повторная сверка']};
    if(state.mode==='unsafe')return {...structuredClone(f.plan),preview_on_copy:false};
    return structuredClone(f.plan);
   }
   if(method==='POST'&&url==='/api/erp/balance-recovery/apply'){
    if(state.holdApply){state.holdApply=false;await new Promise(resolve=>window.releaseApply=resolve)}
    if(state.mode==='stale')throw Error('Финансовые записи изменились после проверки');
    state.run=structuredClone(f.run);return {...structuredClone(state.run),backup_created:true,last_run:structuredClone(state.run)};
   }
   if(method==='POST'&&url==='/api/erp/balance-recovery/71/rollback'){
    if(state.mode==='rollback-stale')throw Error('После исправления записи изменились. Отмена заблокирована');
    state.run={...structuredClone(f.run),state:'rolled_back',can_rollback:false,rollback_token:null,before_summary:f.after,after_summary:f.before};return structuredClone(state.run);
   }
   throw Error('Unexpected API; external or working requests forbidden: '+method+' '+url);
  };
 },{plan,run,before,after,actual});
 await page.addScriptTag({content:fs.readFileSync(path.join(root,'crm/static/erphistoricalrefunds.js'),'utf8')});
 const setup=async role=>page.evaluate(role=>{S.user.role=role;document.querySelector('#box').replaceChildren();erpHistoricalRefundsSetup(document.querySelector('#box'))},role);
 const load=()=>page.evaluate(()=>document.querySelector('[data-emb-plan]').onclick());
 const mutations=()=>page.evaluate(()=>calls.filter(c=>c.url.endsWith('/apply')||c.url.endsWith('/rollback')));
 await setup('operator');eq(await page.locator('#erp-mamedov-balance').count(),0);eq(await page.evaluate(()=>calls.length),0);await setup('offline');eq(await page.locator('#erp-mamedov-balance').count(),0);
 await setup('admin');eq(await page.locator('#erp-mamedov-balance').count(),1);eq(await page.evaluate(()=>calls.length),0);eq(await page.locator('[data-emb-open]').isDisabled(),true);await page.locator('#erp-mamedov-balance > summary').click();eq(await page.locator('[data-emb-public]').count(),0);
 await load();eq((await mutations()).length,0);eq(await page.locator('[data-emb-apply]').isDisabled(),true);eq(await page.locator('[data-emb-confirm]').isChecked(),false);
 const rows=await page.locator('[data-emb-comparison] tbody tr').evaluateAll(rows=>Object.fromEntries(rows.map(row=>[row.children[0].textContent,[row.children[1].textContent,row.children[2].textContent]])));
 assert.deepStrictEqual(rows['К оплате за декабрь'],['0 ₽','9 450 ₽']);checks++;assert.deepStrictEqual(rows['Свободный баланс'],['12 115 ₽','0 ₽']);checks++;assert.deepStrictEqual(rows['Оплат в истории'],['38','38']);checks++;assert.deepStrictEqual(rows['Покупок в истории'],['36','36']);checks++;assert.deepStrictEqual(rows['Занятий в декабре'],['7','7']);checks++;
 const note=await page.locator('[data-emb-result]').innerText();ok(note.includes('8 100 ₽'));ok(note.includes('Точные счета ERP не установлены'));ok(note.includes('Новая оплата не создаётся'));
 await page.locator('[data-emb-open]').click();eq(await page.evaluate(()=>opened[0].id),1719);eq(await page.evaluate(()=>opened[0].options.tab),'abon');
 await page.locator('[data-emb-confirm]').check();eq(await page.locator('[data-emb-apply]').isDisabled(),false);await page.evaluate(()=>state.holdApply=true);const applying=page.evaluate(()=>document.querySelector('[data-emb-apply]').onclick());await page.waitForFunction(()=>!!window.releaseApply);await page.evaluate(()=>document.querySelector('[data-emb-apply]').onclick());eq((await mutations()).length,1);eq(await page.locator('[data-emb-plan]').isDisabled(),true);await page.evaluate(()=>window.releaseApply());await applying;
 const applyRequest=(await mutations())[0];eq(applyRequest.body.erp_id,'88202');eq(applyRequest.body.token,plan.token);ok(typeof applyRequest.body.request_id==='string'&&applyRequest.body.request_id.length>=12);ok((await page.locator('[data-emb-run]').innerText()).includes('Исправление применено'));ok((await page.locator('[data-emb-run]').innerText()).includes('резервная копия создана'));eq(await page.locator('[data-emb-public]').count(),1);eq(await page.locator('[data-emb-public]').getAttribute('href'),run.payment_link.url);eq(await page.locator('[data-emb-apply]').count(),0);
 await setup('admin');await page.locator('#erp-mamedov-balance > summary').click();await load();ok((await page.locator('[data-emb-result]').innerText()).includes('Баланс уже исправлен'));eq(await page.locator('[data-emb-apply]').count(),0);eq(await page.locator('[data-emb-rollback]').isDisabled(),true);ok((await page.locator('[data-emb-run]').innerText()).includes('резервная копия создана'));
 await page.locator('[data-emb-rollback]').locator('..').locator('summary').click();await page.locator('[data-emb-rollback-confirm]').check();eq(await page.locator('[data-emb-rollback]').isDisabled(),false);await page.evaluate(()=>document.querySelector('[data-emb-rollback]').onclick());eq((await mutations()).length,2);eq((await mutations())[1].body.token,run.rollback_token);ok((await page.locator('[data-emb-run]').innerText()).includes('Исправление отменено'));eq(await page.locator('[data-emb-public]').count(),0);
 await page.evaluate(()=>{state.run=null;state.mode='blocked'});await load();eq(await page.locator('[data-emb-apply]').count(),0);ok((await page.locator('[data-emb-result]').innerText()).includes('Финансовые записи изменились'));
 await page.evaluate(()=>state.mode='unsafe');await load();eq(await page.locator('[data-emb-apply]').count(),0);
 await page.evaluate(()=>state.mode='ready');await load();await page.locator('[data-emb-confirm]').check();await page.evaluate(()=>state.mode='stale');await page.evaluate(()=>document.querySelector('[data-emb-apply]').onclick());eq(await page.locator('[data-emb-apply]').count(),0);eq(await page.locator('[data-emb-public]').count(),0);ok((await page.locator('[data-emb-status]').innerText()).includes('Финансовые записи изменились'));eq(await page.locator('[data-emb-plan]').isDisabled(),false);
 await page.evaluate(()=>{state.run=structuredClone(f.run);state.mode='rollback-stale'});await load();await page.locator('[data-emb-rollback]').locator('..').locator('summary').click();await page.locator('[data-emb-rollback-confirm]').check();await page.evaluate(()=>document.querySelector('[data-emb-rollback]').onclick());eq(await page.locator('[data-emb-rollback]').count(),0);ok((await page.locator('[data-emb-status]').innerText()).includes('Отмена заблокирована'));
 await page.evaluate(()=>{state.run=structuredClone(f.run);state.mode='ready'});await load();await page.locator('[data-emb-rollback]').locator('..').locator('summary').click();await page.locator('[data-emb-rollback-confirm]').check();eq(await page.locator('[data-emb-rollback]').isDisabled(),false);await page.evaluate(()=>state.mode='context-failed');await load();eq(await page.locator('[data-emb-rollback]').count(),0);eq(await page.locator('[data-emb-open]').isDisabled(),true);
 await page.evaluate(()=>{state.mode='ready';state.contextRun=structuredClone(f.run);state.run={...structuredClone(f.run),can_rollback:false,rollback_token:null}});await load();eq(await page.locator('[data-emb-rollback]').count(),0);await page.evaluate(()=>delete state.contextRun);
 await page.evaluate(()=>{state.run=null;state.mode='ready';state.holdPlan=true});const oldLoad=load();await page.waitForFunction(()=>!!window.releasePlan);await setup('admin');await page.evaluate(()=>window.releasePlan());await oldLoad;eq(await page.locator('[data-emb-result]').innerHTML(),'');eq(await page.locator('[data-emb-apply]').count(),0);
 await page.locator('#erp-mamedov-balance > summary').click();await page.evaluate(()=>{state.actualStage='plan'});await load();eq(await page.locator('[data-emb-apply]').isDisabled(),true);ok((await page.locator('[data-emb-result]').innerText()).includes('9 450 ₽'));await page.locator('[data-emb-open]').click();eq(await page.evaluate(()=>opened.at(-1).id),actual.plan.student_id);
 await page.locator('[data-emb-confirm]').check();await page.evaluate(()=>document.querySelector('[data-emb-apply]').onclick());eq(await page.locator('[data-emb-current-due]').innerText(),'9 450 ₽');ok((await page.locator('[data-emb-run]').innerText()).includes('резервная копия создана'));eq(await page.locator('[data-emb-public]').count(),0,'Backend intentionally returns no public payment URL');
 await page.evaluate(()=>state.actualStage='after_payment');await load();eq(await page.locator('[data-emb-current-due]').innerText(),'0 ₽');eq(await page.locator('[data-emb-rollback]').count(),0);ok((await page.locator('[data-emb-summary-note]').innerText()).includes('Состояние карточки изменилось'));const savedRows=await page.locator('[data-emb-saved-result] tbody tr').evaluateAll(rows=>Object.fromEntries(rows.map(row=>[row.children[0].textContent,row.children[2].textContent])));eq(savedRows['К оплате за декабрь'],'9 450 ₽');eq(savedRows['Оплат в истории'],'5');ok((await page.locator('[data-emb-current]').innerText()).includes('Оплат в истории: 6'));eq(await page.locator('[data-emb-result] [data-emb-comparison]').count(),0);
 eq(await page.evaluate(()=>calls.filter(c=>c.url.includes('/public/')||c.url.includes('/paylinks')).length),0);console.log('Owner balance recovery UI: '+checks+' assertions passed; isolated API double, no production apply');
 }finally{if(browser)await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)});
