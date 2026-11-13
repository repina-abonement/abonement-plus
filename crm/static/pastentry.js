"use strict";
async function openPastEntry(d,root,reload) {
  if(!root)return;root.hidden=false;
  if(root.querySelector('form')){root.scrollIntoView({block:'nearest',behavior:'smooth'});return}
  root.textContent='Загружаю форму…';
  try {
    const source=await api('GET',`/api/students/${d.student.id}/past-entry-options`);
    if(!root.isConnected)return;
    const groups=source.groups||[],main=groups.filter(g=>g.lesson_kind!=='ofp'),trials=main.filter(g=>!g.program_id);
    const s=d.student,p=d.parents[0]||{},yesterday=addDays(S.meta.today,-1),existingDay=(s.trial_at||'').slice(0,10);
    root.innerHTML=`<form class="inline-trial-form"><h3>Внести прошедшее ПУ / покупку</h3><p class="small muted">Запишите то, что уже произошло. Даты событий сохраняются отдельно от даты внесения. Новая ссылка на оплату не создаётся.</p><div class="inline-trial-grid"><div class="field"><label>Имя и фамилия ребёнка</label><input name="child_name" required value="${esc(s.name)}"></div><div class="field"><label>Дата рождения</label><input name="birth" type="date" required value="${esc((s.birth||'').slice(0,10))}"></div><div class="field"><label>Имя родителя</label><input name="parent_name" required value="${esc(p.name||'')}"></div><div class="field"><label>Телефон родителя</label><input name="phone" type="tel" required value="${esc(p.phone||'')}"></div><div class="field"><label>Почта родителя (необязательно)</label><input name="email" type="email" value="${esc(p.email||'')}"></div></div><label class="check"><input name="record_trial" type="checkbox" checked> Внести состоявшееся ПУ: ребёнок пришёл</label><div data-past-trial class="inline-trial-grid"><div class="field"><label>Дата ПУ</label><input name="trial_date" type="date" max="${S.meta.today}" value="${esc(existingDay||yesterday)}" required></div><div class="field"><label>Фактическое время ПУ</label><input name="trial_time" type="time" value="${esc((s.trial_at||'').slice(11,16))}" required></div><div class="field"><label>Группа ПУ</label><select name="trial_group_id" required>${trials.map(g=>`<option value="${g.id}" ${g.id===(s.trial_group_id||s.group_id)?'selected':''}>${esc(pastGroupLabel(g))}</option>`).join('')}</select></div></div><label class="check"><input name="sold" type="checkbox"> Абонемент уже продан — внести покупку</label><div data-past-sale hidden><div class="inline-trial-grid"><div class="field"><label>Месяц абонемента</label><input name="month" type="month" value="${esc((existingDay||yesterday).slice(0,7))}"></div><div class="field"><label>Группа абонемента</label><select name="group_id">${main.map(g=>`<option value="${g.id}" ${g.id===s.group_id?'selected':''}>${esc(pastGroupLabel(g))}</option>`).join('')}</select></div><div class="field"><label>Фактически проданный тариф</label><select name="tariff"></select></div><div class="field"><label>Скидка</label><select name="discount_id"><option value="">По правилам автоматически</option><option value="none">Без скидки</option>${(source.discounts||[]).map(x=>`<option value="${x.id}">${esc(x.name)} · ${x.percent}%</option>`).join('')}</select></div><div class="field" data-past-ofp hidden><label>Группа ОФП</label><select name="ofp_group_id"></select></div></div><p class="small muted">Выберите занятия в календаре. Стоимость рассчитывается по тарифу выбранного месяца и правилам на дату оплаты.</p><div data-past-calendar></div><div class="field" data-past-reason hidden><label>Причина ручного изменения дат</label><textarea name="dates_reason" rows="2" maxlength="1000" placeholder="Почему выбран другой состав занятий"></textarea></div><div data-past-balance class="past-balance" hidden><b data-balance-available></b><p data-balance-summary class="small"></p><button type="button" class="btn sm" data-use-balance>Использовать средства с баланса</button></div><div class="inline-trial-grid past-payment-grid"><div class="field"><label>Стоимость абонемента</label><output data-past-total class="total" aria-live="polite">—</output><span class="small muted" data-past-price-lines></span></div><div class="field"><label>Фактически оплачено, ₽</label><input name="amount" type="number" min="0" step="1"></div><div class="field"><label>Способ фактической оплаты</label><select name="method"><option value="cash">Наличные</option><option value="paykeeper">По ссылке PayKeeper · оплата уже учтена</option><option value="card_branch">Картой на филиале</option><option value="sber_qr_branch">По QR Сбера на филиале</option><option value="tochka_qr_link">По QR/ссылке Точки от администратора</option><option value="transfer">Перевод</option></select></div><div class="field"><label>Дата оплаты</label><input name="paid_date" type="date" max="${S.meta.today}" value="${esc(existingDay||yesterday)}"></div></div></div><div class="need-err small" data-past-error role="alert"></div><div class="sc-actions"><button class="btn primary" type="submit">Сохранить прошедшее событие</button><button class="btn" type="button" data-past-close>Отмена</button></div></form>`;
    const form=root.querySelector('form'),f=form.elements,error=root.querySelector('[data-past-error]'),submit=form.querySelector('[type=submit]');
    let generation=0,loading=false,busy=false,components=[],calculation=null,total=0,availableBalance=Number(source.available_balance||0),useBalance=false;
    const requestId=window.crypto?.randomUUID?.()||Date.now()+'-'+Math.random().toString(36).slice(2);
    const group=()=>main.find(g=>g.id===+f.group_id.value);
    const lock=()=>submit.disabled=busy||loading||(!f.record_trial.checked&&!f.sold.checked)||(f.sold.checked&&(!root.querySelector('[data-past-lesson]:checked')||!calculation||total<=0));
    const calculate=()=>{
      const cutoff=f.record_trial.checked?f.trial_date.value:existingDay;
      const manual=[...root.querySelectorAll('[data-past-lesson]')].some(x=>x.checked!==(!cutoff||x.value>cutoff));
      root.querySelector('[data-past-reason]').hidden=!manual;f.dates_reason.required=f.sold.checked&&manual;
      const checked=new Set([...root.querySelectorAll('[data-past-lesson]:checked')].map(x=>x.dataset.group+':'+x.value));
      if(!calculation){total=0;root.querySelector('[data-past-total]').textContent='—';return}
      const rules=calculation;let gross=0;const lines=[];
      for(const c of components){const count=c.dates.filter(day=>checked.has(c.group_id+':'+day)).length;const rate=rules.unit_late?Math.ceil(c.rate*(1+rules.late_pct/100)-1e-9):c.rate;gross+=count*rate;lines.push(`${count} × ${rub(rate)}`)}
      if(!rules.unit_late)gross+=Math.ceil(gross*rules.late_pct/100-1e-9);
      total=gross-Math.floor(gross*rules.discount_percent/100+1e-9);
      root.querySelector('[data-past-total]').textContent=rub(total);
      const balanceBox=root.querySelector('[data-past-balance]'),credit=Math.min(availableBalance,total),remaining=Math.max(0,total-credit);
      balanceBox.hidden=availableBalance<=0;
      root.querySelector('[data-balance-available]').textContent=`На балансе: ${rub(availableBalance)}`;
      root.querySelector('[data-balance-summary]').textContent=`В счёт абонемента зачтётся ${rub(credit)}. Останется доплатить: ${rub(remaining)}. Остаток баланса: ${rub(Math.max(0,availableBalance-credit))}.`;
      if(useBalance){f.amount.value=remaining;}
      const existingPayment=f.method.value==='paykeeper'||(useBalance&&remaining===0);f.amount.disabled=existingPayment;f.amount.required=f.sold.checked&&!existingPayment;

      root.querySelector('[data-past-price-lines]').textContent=lines.join(' + ')+(rules.late_pct?` · надбавка ${rules.late_pct}%`:'')+(rules.discount_percent?` · ${rules.discount_name}: ${rules.discount_percent}%`:'');
    };
    const dates=async()=>{
      if(!f.sold.checked){loading=false;generation++;lock();return}
      const version=++generation;loading=true;components=[];calculation=null;calculate();lock();root.querySelector('[data-past-calendar]').textContent='Загружаю даты занятий…';
      const params=new URLSearchParams({group_id:f.group_id.value,month:f.month.value,tariff:f.tariff.value,ofp_group_id:f.ofp_group_id.value||'',paid_date:f.paid_date.value,trial_date:f.record_trial.checked?f.trial_date.value:'',discount_id:f.discount_id.value});
      try{const r=await api('GET',`/api/students/${s.id}/past-entry-options?${params}`);if(version!==generation||!root.isConnected)return;components=r.components||[];calculation=r.calculation;availableBalance=Number(r.available_balance||0);
        root.querySelector('[data-past-calendar]').innerHTML=components.map(c=>{
          const trialCutoff=f.record_trial.checked?f.trial_date.value:existingDay;
          const excluded=c.dates.filter(day=>trialCutoff&&day<=trialCutoff);
          const selected=c.dates.filter(day=>!excluded.includes(day));
          return `<h4>${esc(pastGroupLabel(groups.find(g=>g.id===c.group_id)||{name:c.group,active:1}))}</h4>`+saleCalendarHtml({calendar_dates:c.dates,dates:selected,excluded_dates:excluded},f.month.value)
            .replace('id="sl-calendar"',`data-past-component="${c.group_id}"`)
            .replaceAll('name="sale-lesson"',`name="past-lesson-${c.group_id}"`)
            .replace(/data-sale-date="([^"]+)"/g,(_,day)=>`data-past-lesson data-group="${c.group_id}" value="${day}"`)
            .replace('Выбрано занятий:', 'В этом абонементе занятий:');
        }).join('');calculate();
      }catch(e){if(version===generation){error.textContent=e.message;root.querySelector('[data-past-calendar]').textContent=''}}finally{if(version===generation){loading=false;lock()}}
    };
    const tariffs=()=>{const g=group();f.tariff.innerHTML=(g?.program_id?[['arena_only','Только Арктика'],['pkg','Арктика + ОФП']]:[['fixed','Негибкий'],['flex','Гибкий']]).map(([v,t])=>`<option value="${v}">${t}</option>`).join('');f.ofp_group_id.innerHTML=groups.filter(x=>x.program_id===g?.program_id&&x.lesson_kind==='ofp').map(x=>`<option value="${x.id}">${esc(pastGroupLabel(x))}</option>`).join('');root.querySelector('[data-past-ofp]').hidden=f.tariff.value!=='pkg';dates()};
    const mode=()=>{root.querySelector('[data-past-trial]').hidden=!f.record_trial.checked;root.querySelector('[data-past-sale]').hidden=!f.sold.checked;['trial_date','trial_time','trial_group_id'].forEach(k=>{f[k].required=f.record_trial.checked;f[k].disabled=!f.record_trial.checked});['month','group_id','tariff','paid_date','amount'].forEach(k=>{f[k].required=f.sold.checked});f.email.required=false;f.amount.disabled=f.method.value==='paykeeper'||(useBalance&&availableBalance>=total);f.amount.required=f.sold.checked&&!f.amount.disabled;dates();lock()};
    root.querySelector('[data-past-calendar]').onchange=e=>{
      if(!e.target.matches('[data-past-lesson]'))return;
      const cell=e.target.closest('.sale-cal-day'),calendar=e.target.closest('[data-past-component]');
      cell.classList.toggle('selected',e.target.checked);cell.classList.toggle('excluded',!e.target.checked);cell.querySelector('small').textContent=e.target.checked?'занятие':'исключено';
      const selected=calendar.querySelectorAll('[data-past-lesson]:checked').length,total=calendar.querySelectorAll('[data-past-lesson]').length;
      calendar.lastElementChild.textContent=`В этом абонементе занятий: ${selected}${total>selected?` · исключено: ${total-selected}`:''}`;
      calculate();lock();
    };
    root.querySelector('[data-use-balance]').onclick=()=>{useBalance=true;calculate();lock()};
    f.method.onchange=()=>{calculate();lock()};
    f.record_trial.onchange=f.sold.onchange=mode;
    f.group_id.onchange=tariffs;f.tariff.onchange=()=>{root.querySelector('[data-past-ofp]').hidden=f.tariff.value!=='pkg';dates()};
    f.month.onchange=f.ofp_group_id.onchange=f.paid_date.onchange=f.discount_id.onchange=dates;f.trial_date.onchange=()=>{f.paid_date.value=f.trial_date.value;dates()};f.trial_group_id.onchange=dates;
    root.querySelector('[data-past-close]').onclick=()=>{if(busy)return;generation++;root.innerHTML='';root.hidden=true};
    form.onsubmit=async e=>{e.preventDefault();if(busy||loading||!form.reportValidity())return;error.textContent='';
      const payload=Object.fromEntries(['child_name','birth','parent_name','phone','email','trial_date','trial_time','trial_group_id','month','group_id','tariff','ofp_group_id','discount_id','dates_reason','paid_date','method'].map(k=>[k,f[k].value]));
      payload.expected_balance=availableBalance;payload.use_balance=useBalance;payload.price=total;payload.sale_date=f.paid_date.value;payload.request_id=requestId;payload.record_trial=f.record_trial.checked;payload.sold=f.sold.checked;payload.amount=f.method.value==='paykeeper'||(useBalance&&availableBalance>=total)?0:Number(f.amount.value);payload.lessons=[...root.querySelectorAll('[data-past-lesson]:checked')].map(x=>({group_id:+x.dataset.group,date:x.value}));
      busy=true;lock();form.querySelectorAll('input,select,button').forEach(x=>x.disabled=true);
      try{const r=await api('POST',`/api/students/${s.id}/past-entry`,payload);toast(r.subscription_id?`Прошедшее событие и абонемент сохранены. Осталось оплатить: ${rub(r.remaining)}`:'Прошедшее ПУ сохранено');reload()}
      catch(e){error.textContent=e.message;busy=false;form.querySelectorAll('input,select,button').forEach(x=>x.disabled=false);lock()}
    };
    splitChildNameFields(form,'child_name',true);tariffs();mode();['trial_group_id','group_id','ofp_group_id'].forEach(name=>pastGroupSearch(f[name]));root.scrollIntoView({block:'nearest',behavior:'smooth'});
  }catch(e){root.textContent=e.message}
}

// Короткое название только в интерфейсе: исходные имена и ID не меняем.
function pastGroupLabel(g) {
  const raw=String(g.branch||'').trim();
  const branch=/небо/i.test(raw)?'Небо':/западн|чоботов/i.test(raw)?'Западный':/арктик/i.test(raw)?'Арктика':raw.replace(/^(?:ТЦ|ТРЦ|СК|ФОК)\s*/i,'').replace(/[«»"]/g,'').trim();
  let name=String(g.name||'').trim();
  for(const prefix of [raw,branch])if(prefix&&name.toLowerCase().startsWith(prefix.toLowerCase()))name=name.slice(prefix.length).replace(/^[\s·,:—–-]+/,'');
  return [branch,name].filter(Boolean).join(' · ')+(g.active?'':' · архив');
}

function pastGroupSearch(select) {
  const box=document.createElement('div');box.className='group-search';
  select.before(box);box.append(select);select.hidden=true;
  const input=document.createElement('input');input.type='text';input.autocomplete='off';input.placeholder='Введите день, время или филиал';input.setAttribute('role','combobox');input.setAttribute('aria-autocomplete','list');input.setAttribute('aria-expanded','false');
  const list=document.createElement('div');list.className='group-search-list';list.hidden=true;list.setAttribute('role','listbox');list.id='past-groups-'+select.name;input.setAttribute('aria-controls',list.id);input.setAttribute('aria-label',select.closest('.field')?.querySelector('label')?.textContent||'Группа');
  box.append(input,list);let current=-1;
  const close=()=>{list.hidden=true;input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');current=-1};
  const sync=()=>{input.value=select.selectedOptions[0]?.textContent||'';input.disabled=select.disabled;close()};
  const choose=option=>{select.value=option.value;sync();select.dispatchEvent(new Event('change',{bubbles:true}))};
  const show=query=>{
    const terms=query.toLocaleLowerCase('ru').trim().split(/\s+/).filter(Boolean);
    const options=[...select.options].filter(o=>!o.disabled&&terms.every(t=>o.textContent.toLocaleLowerCase('ru').includes(t)));
    list.replaceChildren();current=-1;input.removeAttribute('aria-activedescendant');
    for(const [index,option] of options.entries()){
      const row=document.createElement('button');row.type='button';row.className='group-search-option';row.textContent=option.textContent;row.setAttribute('role','option');row.setAttribute('aria-selected',String(option.selected));row.id=list.id+'-'+index;row.tabIndex=-1;
      row.onmousedown=e=>e.preventDefault();row.onclick=()=>{choose(option);input.focus();close()};list.append(row);
    }
    if(!options.length){const empty=document.createElement('div');empty.className='muted small';empty.textContent='Группы не найдены';list.append(empty)}
    list.hidden=false;input.setAttribute('aria-expanded','true');
  };
  input.onfocus=()=>{input.select();show('')};input.onclick=()=>{if(list.hidden)show('')};input.oninput=()=>show(input.value);
  input.onkeydown=e=>{
    if(e.key==='Escape'){e.preventDefault();sync();return}
    if(!['ArrowDown','ArrowUp','Enter'].includes(e.key))return;
    if(list.hidden){if(e.key==='Enter')return;show('')}
    const rows=[...list.querySelectorAll('button')];
    if(e.key==='Enter'){e.preventDefault();if(rows[current<0?0:current])rows[current<0?0:current].click();return}
    e.preventDefault();if(!rows.length)return;current=(current+(e.key==='ArrowDown'?1:-1)+rows.length)%rows.length;
    rows.forEach((row,i)=>row.classList.toggle('active',i===current));input.setAttribute('aria-activedescendant',rows[current].id);rows[current].scrollIntoView({block:'nearest'});
  };
  input.onblur=()=>setTimeout(()=>{if(!box.contains(document.activeElement))sync()},0);
  select.addEventListener('change',sync);
  new MutationObserver(sync).observe(select,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled']});
  sync();
}
