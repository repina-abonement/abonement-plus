const fs=require('fs'),assert=require('assert'),playwright=require(process.env.PLAYWRIGHT_MODULE||'playwright');
(async()=>{
  const executablePath=process.env.CHROMIUM_PATH||(fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':undefined);
  const browser=await playwright.chromium.launch({headless:true,args:['--no-sandbox'],...(executablePath?{executablePath}:{})});
  try{
    const page=await browser.newPage();page.setDefaultTimeout(5000);const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.setContent('<div id="root"></div>');
    await page.evaluate(()=>{
      window.S={meta:{today:'2026-11-23'}};
      if(!window.crypto.randomUUID)Object.defineProperty(window.crypto,'randomUUID',{value:()=> 'synthetic-request-id'});
      window.esc=value=>String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
      window.dShort=value=>value;window.toast=()=>{};
      window.api=async(method,path)=>{
        if(method==='GET'&&path==='/api/students/7/trial-groups')return {groups:[{id:11,name:'Ср/Вс 17:45',branch_id:3,branch:'ТЦ «Небо»',active:1,trial_ok:1,is_makeup:0,lesson_kind:'football',program_id:null}]};
        throw Error('Unexpected API '+method+' '+path);
      };
    });
    await page.addScriptTag({path:'crm/static/pastentry.js'});
    await page.addScriptTag({path:'crm/static/trialbooking.js'});
    await page.evaluate(()=>openTrialBookingHub({student:{id:7,group_id:null,trial_group_id:null}},document.querySelector('#root'),()=>{},{futureOnly:true}));
    const select=page.locator('select[name="group_id"]');
    assert.equal(await select.count(),1,(await page.locator('#root').innerHTML())+' errors='+JSON.stringify(errors));
    assert.equal(await page.locator('.group-search input').count(),0,'Проблемный поисковый компонент не должен открываться из кнопки ПУ');
    const options=await select.locator('option').allTextContents();
    assert(options.some(text=>text.includes('Небо')&&text.includes('17:45')),JSON.stringify(options));
    await select.selectOption('11');
    assert.equal(await select.inputValue(),'11');
    console.log('PASS the PU button renders a native selectable trial group list');
  }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)});
