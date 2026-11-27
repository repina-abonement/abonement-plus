const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('crm/static/app.js','utf8');
const fn=source.slice(source.indexOf('async function renderCashReceipts('),source.indexOf('async function renderRevenueLosses('));
let blob,download,button={};const host={isConnected:true,querySelector:()=>button};const form={};
const totals={received:10800,returned:500,net:10300,pending_received:1350,pending_returned:0};
const context={S:{meta:{today:'2026-11-27'}},esc:String,rub:String,monthLabel:String,render(){},URLSearchParams,
 api:async()=>({totals,methods:[{...totals,label:'Точка'}],days:[{...totals,date:'2026-11-20'}]}),
 URL:{createObjectURL:b=>(blob=b,'blob:test'),revokeObjectURL(){}},Blob,setTimeout(){},document:{createElement:()=>({click(){download=this.download}})}};
vm.createContext(context);vm.runInContext(fn,context);
(async()=>{await context.renderCashReceipts({querySelector:s=>s==='form'?form:host});
 assert(host.innerHTML.includes('Ожидающие подтверждения'));button.onclick();
 const csv=await blob.text();assert(csv.includes('"Итого";"2026-11";"10800";"500";"10300";"1350";"0"'));
 assert.equal(download,'receipts-2026-11.csv');assert.equal(csv.split('\r\n').length,4);console.log('Cash receipt UI and CSV passed');})();
