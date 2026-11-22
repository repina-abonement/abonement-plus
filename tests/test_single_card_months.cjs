/* Regression of the real card renderer with temporary services.student_detail payloads.
 * Run: node tests/test_single_card_months.cjs
 * Needs Python and Playwright/Chromium already available. Optional environment:
 * PLAYWRIGHT_MODULE=/path/to/playwright CHROMIUM_PATH=/path/to/chromium PYTHON=python3
 * No production site is opened. Every browser request is intercepted; mutations fail.
 */
const fs = require('fs'), os = require('os'), path = require('path'), cp = require('child_process'), assert = require('assert');
let playwright;
try { playwright = require(process.env.PLAYWRIGHT_MODULE || 'playwright'); }
catch (error) {
  const existing = '/tmp/period-check/node_modules/playwright';
  if (!process.env.PLAYWRIGHT_MODULE && fs.existsSync(existing)) playwright = require(existing);
  else throw new Error('Playwright is required; set PLAYWRIGHT_MODULE to an existing installation');
}
const root = path.resolve(__dirname, '..'), staticRoot = path.join(root, 'crm', 'static');
let checks = 0;
const eq = (a,b,message) => { assert.equal(a,b,message); checks++; };
const ok = (condition,message) => { assert(condition,message); checks++; };

(async()=>{
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'single-card-months-')); let browser;
  try {
    const fixturePath = path.join(folder, 'fixture.json');
    cp.execFileSync(process.env.PYTHON || 'python', ['-m','tests.export_single_card_months_fixture',fixturePath], {cwd:root,stdio:'pipe'});
    const fixture = JSON.parse(fs.readFileSync(fixturePath,'utf8'));
    const launch = {headless:true,args:['--no-sandbox']}, executable = process.env.CHROMIUM_PATH || (fs.existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : null);
    if (executable) launch.executablePath = executable;
    browser = await playwright.chromium.launch(launch);
    const page = await browser.newPage({viewport:{width:1440,height:1000}}), mutations = [], errors = [], studentGets = [];
    let selectedCase = 'recovered';
    page.on('pageerror', error=>errors.push(error.stack || error.message));
    await page.route('**/*', async route=>{
      const req = route.request(), url = new URL(req.url());
      if (req.method() !== 'GET') { mutations.push({method:req.method(),path:url.pathname}); return route.fulfill({status:403,contentType:'application/json',body:'{"error":"Test forbids mutations"}'}); }
      if (url.pathname.startsWith('/api/')) {
        let data;
        if (url.pathname === `/api/students/${fixture.student_id}`) { studentGets.push(selectedCase); data = fixture.cases[selectedCase].detail; }
        else if (url.pathname.endsWith('/attendance-calendar')) data = fixture.cases[selectedCase].calendar[url.searchParams.get('month') || '2026-11'];
        else if (url.pathname.endsWith('/paylinks')) data = {ready:false,links:[]};
        else if (/^\/api\/families\/\d+\/comms$/.test(url.pathname)) data = {status:{},conversations:[],items:[]};
        else if (url.pathname === '/api/meta') data = fixture.meta;
        else data = {rows:[],items:[],links:[],threads:[],channels:[],messages:[],total:0};
        return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
      }
      const file = path.join(staticRoot, url.pathname === '/' ? 'index.html' : url.pathname.slice(1));
      if (!file.startsWith(staticRoot + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) return route.fulfill({status:404,body:''});
      let body = fs.readFileSync(file);
      if (url.pathname === '/app.js') body = Buffer.from(body.toString().replace(/^boot\(\);$/m, '/* Automatic login/navigation disabled for isolated card test. */'));
      const extension = path.extname(file), type = {'.js':'text/javascript','.css':'text/css','.html':'text/html','.json':'application/json','.png':'image/png','.svg':'image/svg+xml','.webp':'image/webp'}[extension] || 'application/octet-stream';
      return route.fulfill({status:200,contentType:type,body});
    });
    await page.goto('http://crm-card.test/');
    await page.evaluate(meta=>{
      S.meta = meta; S.meta.staff = []; S.user = {id:1,name:'Синтетический руководитель',role:'admin',perms:{members:'full',payments:'full',settings:'full',journal:'full',export:'full',planner:'full'}};
      S.meta.user=S.user;
    },fixture.meta);
    const open = async()=>{ await page.evaluate(id=>openStudent(id,{tab:'abon'}),fixture.student_id); };
    const tile = month=>page.locator(`[data-sub-month="${month}"]`);
    const live = ()=>page.locator('.subscription-live-list .subscription-card');
    const waitMonth = async month=>{ await page.waitForFunction(m=>document.querySelector(`[data-sub-month="${m}"]`)?.getAttribute('aria-pressed')==='true',month); };
    const clickMonth = async month=>{ await tile(month).click(); await waitMonth(month); };
    const clearMonth = async()=>page.evaluate(()=>{S.subscriptionCalendars={}});

    await open();
    eq(await tile('2026-11').locator('.subscription-month-status').innerText(),'3 разовых');
    eq(await tile('2026-12').locator('.subscription-month-status').innerText(),'1 разовое');
    ok((await tile('2026-11').innerText()).includes('7 500 ₽'));
    ok((await tile('2026-12').innerText()).includes('2 500 ₽'));
    eq(await live().count(),3);
    ok(!(await page.locator('.subscription-live-list').innerText()).includes('Негибкий'));
    ok(!(await page.locator('.subscription-live-list').innerText()).includes('02.12'));
    eq(await page.locator('.subscription-cancelled-history .is-cancelled').count(),1);
    eq(await page.locator('.subscription-cancelled-history').getAttribute('open'),null);
    ok((await page.locator('.subscription-cancelled-history').textContent()).includes('Отменён'));
    eq(await page.locator('.sc-head .pill').filter({hasText:'Требует оплаты'}).count(),0);
    eq(await page.locator('[data-sc="balance"] .finance-note').count(),0);
    eq(await page.locator('[data-sc="balance"] .has-debt').count(),0);
    eq(await page.locator('.payment-card').count(),4);
    ok((await page.locator('.payment-card time').allTextContents()).every(s=>s.includes('28.10')));
    await clickMonth('2026-12');
    eq(await live().count(),1);
    ok((await live().innerText()).includes('02.12'));
    ok((await live().innerText()).includes('18:45'));
    ok(!(await live().innerText()).includes('Негибкий'));
    await live().locator('.finance-detail > summary').click();
    const details = await live().locator('.finance-detail').innerText();
    ok(details.includes('Исходный месяц покупки: ноябрь 2026'));
    ok(details.includes('Дата в источнике ERP: 04.11'));
    ok(details.includes('Дата занятия после переноса: 02.12'));
    ok(details.includes('28.10.2026'));
    const getsBeforeReopen = studentGets.length;
    await page.evaluate(()=>hideDrawer()); await open();
    ok(studentGets.length > getsBeforeReopen,'Reopening must fetch the real detail API again');
    eq(await tile('2026-12').getAttribute('aria-pressed'),'true');
    eq(await live().count(),1);
    ok((await live().innerText()).includes('02.12'));
    await clickMonth('2026-11');
    eq(await live().count(),3);
    eq(await tile('2026-11').locator('.subscription-month-status').innerText(),'3 разовых');
    eq(await tile('2026-12').locator('.subscription-month-status').innerText(),'1 разовое');
    ok((await tile('2026-11').innerText()).includes('7 500 ₽'));
    ok((await tile('2026-12').innerText()).includes('2 500 ₽'));
    eq(await page.locator('.sc-head .pill').filter({hasText:'Требует оплаты'}).count(),0);
    eq(await page.locator('[data-sc="balance"] .finance-note').count(),0);
    eq(await page.locator('.payment-card').count(),4);
    ok((await page.locator('.payment-card time').allTextContents()).every(s=>s.includes('28.10')));
    eq((await page.locator('.payment-card .payment-amount').allTextContents()).reduce((sum,s)=>sum+Number(s.replace(/[^\d-]/g,'')),0),10000);
    const novemberTitles = await live().locator('h4').allTextContents();
    ok(['11.11','18.11','25.11'].every(day=>novemberTitles.some(title=>title.includes(day)&&title.includes('18:45'))));
    const movedCalendar = await page.evaluate(d=>{
      const moved = d.subscriptions.find(s=>s.kind==='single'&&s.display_month==='2026-12');
      const fragment=document.createElement('div');fragment.innerHTML=existingSaleCalendarHtml(moved,d);
      return {text:fragment.textContent,dates:[...fragment.querySelectorAll('[data-sale-date]')].map(input=>input.dataset.saleDate)};
    },fixture.cases.recovered.detail);
    ok(movedCalendar.text.includes('декабрь 2026'));
    eq(movedCalendar.dates.join(','),'2026-12-02');

    selectedCase='monthly'; await clearMonth(); await open();
    eq(await live().count(),1);
    ok((await live().innerText()).includes('Негибкий'));
    eq(await tile('2026-11').locator('.subscription-month-status').innerText(),'Оплачен');
    ok((await tile('2026-11').innerText()).includes('10 000 ₽'));

    selectedCase='monthly_debt'; await clearMonth(); await open();
    eq(await live().count(),4);
    ok((await tile('2026-11').innerText()).includes('16 500 ₽'));
    ok((await page.locator('[data-sc="balance"] .has-debt').innerText()).includes('9 000 ₽'));
    eq(await page.locator('.sc-head .pill').filter({hasText:'Требует оплаты'}).count(),0);
    eq(await page.locator('[data-sc="balance"] .finance-note').count(),1);

    const manualProof=await page.evaluate(()=>{const box=document.createElement('div');box.innerHTML=subscriptionPaymentDetailsHtml({id:999},{subscription_payment_details:{999:[{payment_id:77,amount:9450,payment_total:9450,method:'tochka_qr_link',method_label:'По QR/ссылке Точки от администратора',paid_at:'2026-11-20T15:30',receipt:{status:'confirmed',label:'Чек Эвотор найден в ОФД',receipt_id:'fn:42'},reconciliation:{bank:{status:'confirmed',label:'Поступление подтверждено выпиской Точки',checked_at:'2026-11-23T00:42'},ofd:{status:'confirmed',label:'Чек Эвотор найден в ОФД'}}}]}});return {text:box.innerText,rows:box.querySelectorAll('.manual-proof-row').length,buttons:box.querySelectorAll('[data-manual-payment-check]').length,hint:box.textContent.includes('PayKeeper') }});
    eq(manualProof.rows,2);eq(manualProof.buttons,0);eq(manualProof.hint,false);eq((manualProof.text.match(/Чек Эвотор/g)||[]).length,1);ok(manualProof.text.includes('Поступление в Точку'));ok(manualProof.text.includes('Подтверждён в ОФД'));

    selectedCase='partial'; await clearMonth(); await open();
    ok((await page.locator('[data-sc="balance"] .has-debt').innerText()).includes('7 000 ₽'));
    const partial = live().filter({hasText:'Негибкий'});
    ok((await partial.innerText()).includes('Оплачен частично'));
    ok((await partial.locator('.subscription-money').innerText()).includes('2 000 ₽'));
    ok((await partial.locator('.subscription-money').innerText()).includes('7 000 ₽'));
    eq(await page.locator('.sc-head .pill').filter({hasText:'Требует оплаты'}).count(),0);

    selectedCase='stale_paid'; await clearMonth(); await open();
    const stale = live().filter({hasText:'Негибкий'});
    ok((await stale.innerText()).includes('Оплачен частично'),'Stale paid status must not hide ledger shortfall');
    ok((await stale.locator('.subscription-money').innerText()).includes('7 000 ₽'));
    eq(await page.locator('.sc-head .pill').filter({hasText:'Требует оплаты'}).count(),0);
    eq(mutations.length,0,JSON.stringify(mutations));
    eq(errors.length,0,errors.join('\n'));
    console.log(`Single-card months: ${checks} assertions passed; actual openStudent GET, five services.student_detail fixtures, no mutations`);
  } finally {
    if (browser) await browser.close();
    fs.rmSync(folder,{recursive:true,force:true});
  }
})().catch(error=>{console.error(error);process.exitCode=1});
