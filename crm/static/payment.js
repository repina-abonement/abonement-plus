'use strict';
(async()=>{
  const token=location.pathname.split('/').filter(Boolean)[1],endpoint='/api/public/pay-links/'+encodeURIComponent(token);
  const form=document.querySelector('#payment-form'),error=document.querySelector('#error'),button=document.querySelector('#continue');
  const money=value=>Number(value).toLocaleString('ru-RU')+' ₽';
  const day=value=>new Date(value+'T12:00:00Z').toLocaleDateString('ru-RU',{day:'numeric',month:'short',timeZone:'UTC'});
  const fullDay=value=>new Date(value+'T12:00:00Z').toLocaleDateString('ru-RU',{day:'numeric',month:'long',year:'numeric',timeZone:'UTC'});
  const month=value=>new Date(value+'-01T12:00:00Z').toLocaleDateString('ru-RU',{month:'long',year:'numeric',timeZone:'UTC'});
  const node=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e};
  const row=(list,title,value)=>{if(value===null||value===undefined||value==='')return;const r=node('div');r.append(node('dt',title),node('dd',value));list.append(r)};
  try{
    const response=await fetch(endpoint,{cache:'no-store',credentials:'omit'}),data=await response.json();if(!response.ok)throw Error(data.error||'Ссылка недоступна');
    const details=document.querySelector('#details'),info=node('dl');row(info,'Ребёнок',data.child);
    if(data.subscriptions?.length){
      details.append(info);
      for(const sub of data.subscriptions){const block=node('div',undefined,'group');block.append(node('strong',month(sub.month)));if(sub.group?.name)block.append(node('div',sub.group.name));block.append(node('div',sub.lessons+' занятий · '+money(sub.price)));if(sub.discount_amount)block.append(node('div','Скидка «'+sub.discount_name+'» уже учтена: −'+money(sub.discount_amount),'muted'));block.append(node('div','К оплате за месяц: '+money(sub.amount)));details.append(block)}
    }
    const s=data.subscription;
    if(s){row(info,'Период',s.valid_from||s.valid_until?[s.valid_from?'с '+fullDay(s.valid_from):'',s.valid_until?'по '+fullDay(s.valid_until):''].filter(Boolean).join(' '):month(s.month));row(info,'Тариф',({fixed:'Негибкий',flex:'Гибкий',pkg:'Арктика + ОФП',arena_only:'Только Арктика',single:'Разовое занятие'})[s.tariff]||'Абонемент');row(info,'Занятия',s.lessons);details.append(info);
      for(const group of s.groups||[]){const block=node('div',undefined,'group');block.append(node('strong',group.name||group.branch));if(group.branch&&!group.name?.includes(group.branch))block.append(node('div',group.branch,'muted'));if(group.dates?.length){const dates=node('div',undefined,'dates');dates.setAttribute('aria-label','Даты занятий');group.dates.forEach(d=>dates.append(node('span',day(d),'date')));block.append(dates)}details.append(block)}
      if(s.excluded_dates?.length)details.append(node('p','Не входят в абонемент: '+s.excluded_dates.map(day).join(', '),'muted'));
      const calculation=document.querySelector('#calculation');row(calculation,'Итоговая стоимость',money(s.price));if(s.discount_amount)row(calculation,'Скидка уже учтена'+(s.discount_name?' «'+s.discount_name+'»':''),'−'+money(s.discount_amount));if(s.late_fee)row(calculation,'Надбавка уже учтена',money(s.late_fee));row(calculation,'Уже оплачено',money(s.paid));
      if(data.amount<s.remaining){const partial=document.querySelector('#partial');partial.textContent='Частичная оплата · После этого платежа останется '+money(s.remaining-data.amount);partial.hidden=false}
    }else if(!data.subscriptions?.length){row(info,'Назначение',data.purpose);details.append(info)}
    details.hidden=false;document.querySelector('#amount').textContent=money(data.amount);document.querySelector('#billing').hidden=false;document.querySelector('#ready').hidden=!data.invoice_ready;
    form.elements.email.value=data.email||'';if(data.invoice_ready){form.elements.email.readOnly=true;document.querySelector('#email-note').textContent='Email уже указан в созданном счёте и не изменяется при повторном переходе'}else if(data.email)document.querySelector('#email-note').textContent='Подставили сохранённый адрес — при необходимости его можно исправить';
    button.textContent='Оплатить '+money(data.amount);form.hidden=false;
  }catch(e){error.textContent=e.message;return}finally{document.querySelector('#loading').hidden=true}
  form.onsubmit=async event=>{event.preventDefault();if(!form.reportValidity())return;button.disabled=true;error.textContent='';try{const response=await fetch(endpoint+'/checkout',{method:'POST',credentials:'omit',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:form.elements.email.value.trim()})}),data=await response.json();if(!response.ok)throw Error(data.error||'Не удалось открыть оплату');location.assign(data.url)}catch(e){error.textContent=e.message;button.disabled=false}};
})();
