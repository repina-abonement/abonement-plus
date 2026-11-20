/* Public payment page with real temporary-backend responses. GET only: no
 * checkout, outgoing messages or production service is contacted.
 * Run: node tests/test_payment_page_9450.cjs
 */
const fs=require('fs'),path=require('path'),cp=require('child_process'),assert=require('assert');
let playwright;
try{playwright=require(process.env.PLAYWRIGHT_MODULE||'playwright')}
catch(error){const fallback='/tmp/period-check/node_modules/playwright';if(!process.env.PLAYWRIGHT_MODULE&&fs.existsSync(fallback))playwright=require(fallback);else throw error}
const root=path.resolve(__dirname,'..'),staticRoot=path.join(root,'crm/static');
const python=`import json,io
from contextlib import redirect_stdout
from tests.test_paylinks import PayLinksTest
from crm import paylinks,balancecredit,money
f=PayLinksTest()
with redirect_stdout(io.StringIO()): f.setUp()
try:
 f.con.execute("UPDATE groups SET name='Synthetic group Monday/Thursday 19:45',weekdays='0,3',time='19:45' WHERE id=?",(f.gid,))
 eid=f.con.execute('SELECT id FROM enrollments WHERE student_id=?',(f.sid,)).fetchone()[0]
 sub=f.con.execute("INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,price,status,created_at,ledger_managed,corrected) VALUES (?,?,'2026-12','fixed',7,1350,9450,'invoiced','2026-11-20',1,1)",(f.sid,eid)).lastrowid
 balancecredit.protect_invoice(f.con,sub,f.user)
 link=paylinks.create(f.con,f.sid,9450,f.user,subscription_id=sub,full_payment=True)
 token=link['url'].rsplit('/',1)[1]
 before=f.con.serialize();valid=paylinks.payment_page(f.con,token)
 assert f.con.serialize()==before
 failures={}
 for key,price in [('increased',10800),('decreased',8100)]:
  f.con.execute('UPDATE subscriptions SET price=? WHERE id=?',(price,sub))
  try: paylinks.payment_page(f.con,token);failures[key]=None
  except paylinks.PayLinkError as error: failures[key]=str(error)
 f.con.execute("UPDATE subscriptions SET price=9450,status='cancelled' WHERE id=?",(sub,))
 try: paylinks.payment_page(f.con,token);failures['cancelled']=None
 except paylinks.PayLinkError as error: failures['cancelled']=str(error)
 assert not f.sent, 'Opening the page must not call the payment provider'
 print(json.dumps({'valid':valid,'failures':failures,'provider_calls':len(f.sent)},ensure_ascii=False))
finally:f.doCleanups()
`;
const fixtures=JSON.parse(cp.execFileSync(process.env.PYTHON||'python',['-c',python],{cwd:root,encoding:'utf8',env:{...process.env,CRM_QUIET:'1',CRM_ADMIN_PASSWORD:'synthetic-only'}}));
let checks=0;const eq=(a,b,note)=>{assert.strictEqual(a,b,note);checks++},ok=(v,note)=>{assert(v,note);checks++};
(async()=>{
 let browser;
 try{
  const executablePath=process.env.CHROMIUM_PATH||(fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':undefined);
  browser=await playwright.chromium.launch({headless:true,args:['--no-sandbox'],...(executablePath?{executablePath}:{})});
  eq(fixtures.provider_calls,0,'Backend page inspection has no provider calls');
  eq(fixtures.valid.amount,9450);eq(fixtures.valid.subscription.lessons,7);eq(fixtures.valid.subscription.paid,0);
  for(const width of [1280,360]){
   const context=await browser.newContext({viewport:{width,height:1000}}),page=await context.newPage();
   let mode='valid';const writes=[],errors=[];page.on('pageerror',e=>errors.push(e.message));
   await context.route('**/*',async route=>{
    const req=route.request(),url=new URL(req.url());
    if(req.method()!=='GET'){writes.push(req.url());return route.abort()}
    if(url.origin!=='http://payment-copy.test')return route.abort();
    if(url.pathname.startsWith('/api/public/pay-links/')){
     const error=fixtures.failures[mode];return route.fulfill({status:mode==='valid'?200:410,contentType:'application/json',body:JSON.stringify(mode==='valid'?fixtures.valid:{error})});
    }
    const file=url.pathname.startsWith('/pay/')?'payment.html':url.pathname.slice(1);
    if(!['payment.html','payment.js'].includes(file))return route.fulfill({status:404,body:''});
    return route.fulfill({status:200,contentType:file.endsWith('.js')?'application/javascript':'text/html',body:fs.readFileSync(path.join(staticRoot,file))});
   });
   await page.goto('http://payment-copy.test/pay/'+'a'.repeat(43));
   await page.locator('#payment-form').waitFor({state:'visible'});
   eq((await page.locator('#amount').textContent()).replace(/\s/g,''),'9450₽');
   ok((await page.locator('#continue').textContent()).replace(/\s/g,'').includes('9450₽'));
   ok((await page.locator('#details').textContent()).includes('7'));
   ok((await page.locator('#calculation').textContent()).includes('Уже оплачено'));
   eq(await page.locator('#partial').isVisible(),false);
   eq(await page.locator('#error').textContent(),'');
   for(const next of ['increased','decreased','cancelled']){
    mode=next;ok(fixtures.failures[next],'Backend refuses a stale or cancelled full link');
    await page.reload();await page.locator('#loading').waitFor({state:'hidden'});
    ok((await page.locator('#error').textContent()).length>0);
    eq(await page.locator('#payment-form').isVisible(),false);
    eq(await page.locator('#billing').isVisible(),false);
   }
   eq(writes.length,0,'No checkout or payment is submitted');eq(errors.length,0);
   await context.close();
  }
  console.log('Payment page 9450: '+checks+' checks passed; real temporary backend, seven lessons, zero paid, desktop/mobile, stale amounts blocked, no checkout/provider/customer messages');
 }finally{await browser?.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
