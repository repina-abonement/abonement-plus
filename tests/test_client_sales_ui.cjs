/* Actual client card + role-specific server meta on temporary synthetic data.
 * Run: node tests/test_client_sales_ui.cjs
 * Optional PLAYWRIGHT_MODULE/CHROMIUM_PATH/PYTHON use existing installations.
 * No working database, provider or client message is used.
 */
const fs=require('fs'),path=require('path'),assert=require('assert'),cp=require('child_process');
let pw;try{pw=require(process.env.PLAYWRIGHT_MODULE||'playwright')}catch(e){const p='/tmp/period-check/node_modules/playwright';if(!process.env.PLAYWRIGHT_MODULE&&fs.existsSync(p))pw=require(p);else throw Error('Set PLAYWRIGHT_MODULE to existing Playwright')}
const root=path.resolve(__dirname,'..'),assets=path.join(root,'crm/static');let count=0;const eq=(a,b,n)=>{assert.strictEqual(a,b,n);count++},ok=(v,n)=>{assert(v,n);count++};
const fixture=JSON.parse(cp.execFileSync(process.env.PYTHON||'python',['-c',`import json
from tests.test_invoice_balance_flows import InvoiceBalanceFlowsTest
from crm import server, services as sv, clientattendance, smart
f=InvoiceBalanceFlowsTest();f.setUp()
try:
 roles={}
 for key,name,offline in [('ordinary_operator_model','Synthetic Varvara role model',0),('offline','Synthetic Offline',1)]:
  uid=f.con.execute("INSERT INTO users(login,name,role,pw_hash,is_offline_admin) VALUES (?,?,'operator','disabled',?)",('synthetic-sale-'+key,name,offline)).lastrowid
  user=dict(f.con.execute('SELECT id,login,name,role,is_senior,is_offline_admin,is_promoter,is_journal FROM users WHERE id=?',(uid,)).fetchone())
  meta=server.meta(f.con,None,user,{},{});new=server.student_get(f.con,None,user,{}, {},str(f.sid))
  roles[key]={'meta':meta,'new':new,'user':user}
 quote=f.quote(False);required=smart.required_info(f.con,f.sid,'sale');created=f.invoice(False)
 for item in roles.values():item['existing']=server.student_get(f.con,None,item['user'],{}, {},str(f.sid))
 result={'roles':roles,'student_id':f.sid,'subscription_id':created['id'],'quote':quote,'existing_quote':sv.payment_quote(f.con,created['id'],'2026-11-20',False),'required':required,'created':created,'calendar':{m:clientattendance.calendar(f.con,f.sid,m) for m in ('2026-11','2026-12')}}
 print(json.dumps(result,ensure_ascii=False))
finally:f.doCleanups()
`],{cwd:root,encoding:'utf8'}));
(async()=>{let browser;try{
 const launch={headless:true,args:['--no-sandbox']},exe=process.env.CHROMIUM_PATH||(fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':null);if(exe)launch.executablePath=exe;browser=await pw.chromium.launch(launch);const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(6000);
 let role='ordinary_operator_model',stage='new';const requests=[],errors=[],url='http://crm-sale-roles.test',sid=fixture.student_id,sub=fixture.subscription_id;
 page.on('pageerror',e=>errors.push(e.message));await page.route('**/*',async route=>{
  const req=route.request(),u=new URL(req.url()),method=req.method(),body=req.postDataJSON();if(u.origin!==url)throw Error('External access forbidden');
  if(u.pathname.startsWith('/api/')){
   requests.push({role,method,path:u.pathname,query:Object.fromEntries(u.searchParams),body});let data;
   if(method==='GET'){
    if(u.pathname===`/api/students/${sid}`)data=fixture.roles[role][stage];
    else if(u.pathname===`/api/students/${sid}/required`)data=fixture.required;
    else if(u.pathname===`/api/students/${sid}/quote`)data=fixture.quote;
    else if(u.pathname===`/api/subscriptions/${sub}/payment-quote`)data=fixture.existing_quote;
    else if(u.pathname===`/api/students/${sid}/paylinks`)data={ready:true,links:[]};
    else if(u.pathname==='/api/paylinks/42/validate')data={valid:true,read_only:true,link_id:42,student_id:sid,subscription_id:sub,amount:fixture.existing_quote.total,current_due:fixture.existing_quote.total,url:url+'/pay/synthetic-sale-role-token',invoice_scope:[{subscription_id:sub}]};
    else if(u.pathname.endsWith('/attendance-calendar'))data=fixture.calendar[u.searchParams.get('month')||'2026-11'];
    else if(u.pathname.startsWith('/api/families/')&&u.pathname.endsWith('/comms'))data={status:{},conversations:[],items:[]};
    else data={rows:[],items:[],links:[],threads:[],channels:[],messages:[],total:0};
   }else if(method==='POST'&&u.pathname===`/api/students/${sid}/paylinks`){eq(body.amount,fixture.existing_quote.total);eq(body.full_payment,true);data={id:42,amount:body.amount,url:url+'/pay/synthetic-sale-role-token'}}
   else throw Error('Unexpected mutation; sale not confirmed by this test: '+method+' '+u.pathname);
   return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
  }
  const file=path.join(assets,u.pathname==='/'?'index.html':u.pathname.slice(1));if(!file.startsWith(assets+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile())return route.fulfill({status:404,body:''});let data=fs.readFileSync(file);if(u.pathname==='/app.js')data=Buffer.from(data.toString().replace(/^boot\(\);$/m,'/* No automatic navigation in isolated role test. */'));return route.fulfill({status:200,contentType:{'.js':'text/javascript','.css':'text/css','.html':'text/html','.svg':'image/svg+xml'}[path.extname(file)]||'application/octet-stream',body:data});
 });
 await page.goto(url+'/');
 const configure=async()=>page.evaluate(meta=>{S.meta=meta;S.user=meta.user;S.view='inbox';S.subscriptionCalendars={};window.copied=[];Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>window.copied.push(text)}})},fixture.roles[role].meta);
 const open=()=>page.evaluate(id=>openStudent(id,{tab:'abon'}),sid);
 for(const key of ['ordinary_operator_model','offline']){
  role=key;stage='new';await configure();await page.evaluate(()=>shell());eq(await page.locator('#new-client').count(),1);eq(await page.locator('#new-client').isVisible(),true);await open();eq(await page.locator('#act-sell').count(),1);eq(await page.locator('#act-sell').isVisible(),true);eq(fixture.roles[role].meta.user.perms.client_sales,'full');eq(fixture.roles[role].meta.user.perms.client_create,'full');
  const views=await page.evaluate(()=>visibleViews().map(([key])=>key));ok(!views.includes('client_sales'),'Capability must not become a menu section');
  if(key==='offline'){
   for(const forbidden of ['sales','settings','dashboard','payments','fiscal','team'])ok(!views.includes(forbidden),forbidden);
   eq(await page.locator('.sc-header-tools [data-profile-edit],.sc-header-tools [data-client-add],#act-delete-error').count(),0);eq(await page.locator('[data-pcanc]').count(),0);
   const parents=page.locator('.sc-parents [data-profile-edit]').first();if(await parents.count()){await parents.click();eq(await page.locator('#sc-profile-edit').isVisible(),false)}
  }
  await page.locator('#act-sell').click();await page.waitForSelector('#sl-balance-preview [data-balance-payable]');eq(await page.locator('#sl-kind').inputValue(),'month');ok(await page.locator('#sl-en option').count()>0);eq(await page.locator('#sl-en').inputValue(),String(fixture.quote.group_id||fixture.roles[role].new.enrollments[0].group_id));eq(await page.locator('#sl-use-balance').isChecked(),false);eq(await page.locator('#sl-link').isDisabled(),false);eq(await page.locator('#sl-now').isDisabled(),false);
  eq(requests.filter(r=>r.role===role&&r.method!=='GET').length,0);await page.locator('#sl-now').click();eq(await page.locator('#sl-ok').isVisible(),true);eq(await page.locator('#sl-amount').inputValue(),String(fixture.quote.balance_preview.payable));eq(requests.filter(r=>r.role===role&&r.method!=='GET').length,0);
  stage='existing';await open();await page.locator('#act-sell').click();await page.waitForSelector('#sl-balance-preview [data-balance-payable]');eq(await page.locator('#sl-use-balance').isChecked(),false);ok((await page.locator('#sl-q').innerText()).includes('Абонемент уже оформлен'));await page.locator('#sl-link').click();await page.waitForSelector('#sl-msg',{state:'visible'});await page.locator('#sl-copy').click();await page.waitForFunction(()=>window.copied.length===1);ok((await page.locator('#sl-msg').inputValue()).includes('12 150 ₽'));eq(requests.filter(r=>r.role===role&&r.path.endsWith('/validate')).length,2);eq(await page.evaluate(()=>can('members','full')),key!=='offline');
 }
 // Compatibility: an old full-members view does not need the new metadata key.
 role='ordinary_operator_model';stage='new';await configure();await page.evaluate(()=>delete S.user.perms.client_sales);await open();eq(await page.locator('#act-sell').count(),1);
 // No full sales or members permission: the cart stays hidden.
 role='offline';await configure();await page.evaluate(()=>delete S.user.perms.client_sales);await open();eq(await page.locator('#act-sell').count(),0);eq(await page.locator('.sc-header-tools [data-profile-edit],.sc-header-tools [data-client-add]').count(),0);
 eq(requests.filter(r=>r.method==='PATCH'||r.path.endsWith('/send')||r.path.endsWith('/pay')||r.path.endsWith('/invoice')).length,0);eq(errors.length,0,errors.join('\n'));console.log('Client sales UI: '+count+' assertions passed; actual server meta/card DTOs for modeled operator/offline; no live writes');
 }finally{if(browser)await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
