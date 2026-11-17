'use strict';
(async()=>{
  const token=location.pathname.split('/').filter(Boolean)[1],endpoint='/api/public/pay-links/'+encodeURIComponent(token);
  const form=document.querySelector('#payment-form'),error=document.querySelector('#error'),button=document.querySelector('#continue');
  try{const response=await fetch(endpoint,{cache:'no-store',credentials:'omit'}),data=await response.json();if(!response.ok)throw Error(data.error||'Ссылка недоступна');document.querySelector('#amount').textContent=Number(data.amount).toLocaleString('ru-RU')+' ₽';document.querySelector('#ready').hidden=!data.invoice_ready;form.hidden=false}catch(e){document.querySelector('#amount').textContent='';error.textContent=e.message;return}
  form.onsubmit=async event=>{event.preventDefault();if(!form.reportValidity())return;button.disabled=true;error.textContent='';try{const response=await fetch(endpoint+'/checkout',{method:'POST',credentials:'omit',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:form.elements.email.value.trim()})}),data=await response.json();if(!response.ok)throw Error(data.error||'Не удалось открыть оплату');location.assign(data.url)}catch(e){error.textContent=e.message;button.disabled=false}};
})();
