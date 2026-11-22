"use strict";
/* Запись из карточки без смены вкладки и перехода в переписку. */
function openInlineTrialBooking(d, root, reload) {
  if (!root) return;
  root.hidden=false;
  if(root.querySelector('form')) {root.scrollIntoView({block:'nearest',behavior:'smooth'});return;}
  const p=d.parents[0]||{},s=d.student;
  const branches=(S.meta.branches||[]).filter(b=>b.active!==0);
  root.innerHTML=`<form class="inline-trial-form"><h3>Запись на пробное</h3><p class="small muted">Заполните данные и выберите свободное занятие. Запись сохранится после подтверждения</p><div class="inline-trial-grid"><div class="field"><label>Имя и фамилия ребёнка</label><input name="child_name" value="${esc(s.name||'')}" required autocomplete="off"></div><div class="field"><label>Дата рождения ребёнка</label><input name="birth" type="date" value="${esc((s.birth||'').slice(0,10))}" max="${esc(S.meta.today)}" required></div><div class="field"><label>Имя родителя</label><input name="parent_name" value="${esc(p.name||'')}" required></div><div class="field"><label>Телефон родителя</label><input name="phone" type="tel" value="${esc(p.phone||'')}" required></div><div class="field"><label>Почта родителя · необязательно</label><input name="email" type="email" value="${esc(p.email||'')}"></div><div class="field"><label>Филиал</label><select name="branch_id"><option value="">Все филиалы</option>${branches.map(b=>`<option value="${b.id}" ${b.id===s.branch_id?'selected':''}>${esc(b.name)}</option>`).join('')}</select></div><div class="field"><label>День занятия</label><input name="date" type="date" min="${esc(S.meta.today)}" required></div><div class="field"><label>Группа</label><select name="group_id" required disabled><option value="">Сначала заполните дату рождения</option></select></div><div class="field"><label>Время занятия</label><select name="time" required disabled><option value="">Выберите группу</option></select></div></div><div class="small muted" data-trial-availability aria-live="polite"></div><div class="need-err small" data-trial-error role="alert"></div><div class="sc-actions"><button class="btn primary" type="submit" disabled>Подтвердить запись</button><button class="btn" type="button" data-trial-cancel>Отмена</button></div></form>`;
  const form=root.querySelector('form'),f=form.elements,submit=form.querySelector('[type=submit]'),notice=root.querySelector('[data-trial-availability]'),error=root.querySelector('[data-trial-error]');
  let slots=[],generation=0,busy=false;
  const drawTimes=()=>{
    const times=slots.filter(x=>x.date===f.date.value&&x.group_id===+f.group_id.value);
    f.time.innerHTML=times.map(x=>`<option value="${esc(x.time)}">${esc(x.time)} · свободно мест: ${x.free}</option>`).join('')||'<option value="">Нет свободного времени</option>';
    f.time.disabled=!times.length;submit.disabled=busy||!times.length;
  };
  const drawGroups=()=>{
    const available=slots.filter(x=>x.date===f.date.value),prev=f.group_id.value,groups=[...new Map(available.map(x=>[x.group_id,x])).values()];
    f.group_id.innerHTML=groups.map(x=>`<option value="${x.group_id}">${esc(x.group)} · ${esc(x.branch)}</option>`).join('')||'<option value="">Нет подходящих групп</option>';
    if(groups.some(g=>g.group_id===+prev))f.group_id.value=prev;
    f.group_id.disabled=!groups.length;drawTimes();
  };
  const load=async()=>{
    const current=++generation;slots=[];submit.disabled=true;f.group_id.disabled=true;f.time.disabled=true;error.textContent='';
    if(!f.birth.value){notice.textContent='Укажите дату рождения в карточке клиента, чтобы увидеть дни и время';return;}
    notice.textContent='Проверяю расписание и свободные места…';
    const params=new URLSearchParams({birth:f.birth.value});if(f.date.value)params.set('date',f.date.value);if(f.branch_id.value)params.set('branch_id',f.branch_id.value);
    try{const r=await api('GET',`/api/students/${s.id}/trial-options?${params}`);if(current!==generation||!root.isConnected)return;slots=r.slots||[];if(!f.date.value&&slots.length)f.date.value=slots[0].date;notice.textContent=r.error||'Доступные занятия по расписанию. Места повторно проверяются при записи.';drawGroups()}
    catch(e){if(current===generation)error.textContent=e.message}
  };
  f.birth.onchange=()=>{slots=[];f.date.value='';load()};f.branch_id.onchange=load;f.date.onchange=()=>{if(slots.some(x=>x.date===f.date.value)){error.textContent='';drawGroups()}else load()};f.group_id.onchange=drawTimes;
  root.querySelector('[data-trial-cancel]').onclick=()=>{if(busy)return;generation++;root.innerHTML='';root.hidden=true};
  form.onsubmit=async e=>{
    e.preventDefault();if(busy||submit.disabled||!form.reportValidity())return;busy=true;submit.disabled=true;error.textContent='';
    const payload=Object.fromEntries(['child_name','birth','parent_name','phone','email','date','time'].map(k=>[k,f[k].value]));payload.group_id=+f.group_id.value;
    form.querySelectorAll('input,select,button').forEach(el=>el.disabled=true);
    try{const r=await api('POST',`/api/students/${s.id}/trial-booking`,payload);toast(`Записан на пробное: ${dShort(r.slot.date)} в ${r.slot.time}, ${r.slot.group}`);reload()}
    catch(e){busy=false;form.querySelectorAll('input,select,button').forEach(el=>el.disabled=false);error.textContent=e.message;submit.disabled=false}
  };
  hideTrialClientFields(form);load();root.scrollIntoView({block:'nearest',behavior:'smooth'});f.date.focus({preventScroll:true});
}


async function openTrialBookingHub(d, root, reload, options={}) {
  if(!root)return;
  root.hidden=false;
  const futureOnly=!!options.futureOnly;
  if(root.dataset.futureOnly!==String(futureOnly)){root.replaceChildren();root.dataset.futureOnly=String(futureOnly)}
  if(root.querySelector('[data-trial-calendar]')){root.scrollIntoView({block:'nearest',behavior:'smooth'});return}
  root.innerHTML='<p class="muted">Загружаю календарь…</p>';
  try{
    const data=await api('GET',`/api/students/${d.student.id}/past-entry-options`);
    if(!root.isConnected)return;
    const groups=(data.groups||[]).filter(g=>g.active!==0&&g.lesson_kind!=='ofp'&&!g.program_id);
    root.innerHTML=`<form data-trial-calendar><h3>Пробное занятие</h3><div class="row"><div class="field"><label>Группа</label><select name="group_id" required><option value="">Выберите группу</option>${groups.map(g=>`<option value="${g.id}" ${g.id===(d.student.trial_group_id||d.student.group_id)?'selected':''}>${esc(pastGroupLabel(g))}</option>`).join('')}</select></div><div class="field"><label>Месяц</label><input name="month" type="month" required value="${esc(S.meta.today.slice(0,7))}"></div></div><div class="trial-calendar-days" data-trial-days></div><p class="small" data-trial-selection aria-live="polite">Выберите день и время занятия</p><p class="need-err" data-trial-error role="alert"></p><div class="sc-actions"><button type="submit" class="btn primary" disabled>Подтвердить запись</button><button type="button" class="btn" data-close-trial>Отмена</button></div></form>`;
    const form=root.querySelector('form'),f=form.elements,days=form.querySelector('[data-trial-days]'),error=form.querySelector('[data-trial-error]'),submit=form.querySelector('[type=submit]'),selection=form.querySelector('[data-trial-selection]');
    let selected=null,generation=0,busy=false;
    const requestId=crypto.randomUUID();
    const load=async()=>{
      const version=++generation;selected=null;submit.disabled=true;error.textContent='';selection.textContent='Выберите день и время занятия';
      if(!f.group_id.value){days.replaceChildren();return}
      days.textContent='Проверяю расписание…';
      try{
        const result=await api('GET',`/api/students/${d.student.id}/trial-calendar?`+new URLSearchParams({group_id:f.group_id.value,month:f.month.value,future_only:futureOnly?'1':'0'}));
        if(version!==generation||!form.isConnected)return;
        const slots=(result.slots||[]).filter(s=>!futureOnly||!s.past);
        const first=new Date(f.month.value+'-01T12:00:00'),offset=(first.getDay()+6)%7,count=new Date(first.getFullYear(),first.getMonth()+1,0).getDate();
        days.innerHTML='<div class="trial-calendar-grid">'+['Пн','Вт','Ср','Чт','Пт','Сб','Вс'].map(w=>`<span class="small muted">${w}</span>`).join('')+Array.from({length:offset},()=>'<span></span>').join('')+Array.from({length:count},(_,i)=>{
          const date=f.month.value+'-'+String(i+1).padStart(2,'0');
          return `<div class="trial-calendar-day"><span>${i+1}</span>${slots.map((s,index)=>s.date===date?`<button type="button" class="btn sm" data-slot="${index}">${esc(s.time)}${s.past?'<small>Было ПУ</small>':''}</button>`:'').join('')}</div>`;
        }).join('')+'</div>';
        if(!slots.length)selection.textContent='В этом месяце нет доступных занятий — выберите другой месяц или группу';
        days.querySelectorAll('[data-slot]').forEach(b=>b.onclick=()=>{
          selected=slots[+b.dataset.slot];submit.disabled=false;
          days.querySelectorAll('[data-slot]').forEach(x=>x.classList.toggle('on',x===b));
          selection.textContent=`${dShort(selected.date)} в ${selected.time} · ${selected.past?'Прошедшее ПУ — ребёнок пришёл':'Запись на будущее ПУ'}`;
          submit.textContent=selected.past?'Сохранить прошедшее ПУ':'Записать на ПУ';
        });
      }catch(e){if(version===generation){days.replaceChildren();error.textContent=e.message}}
    };
    f.group_id.onchange=f.month.onchange=load;
    if(typeof pastGroupSearch==='function')pastGroupSearch(f.group_id);
    form.querySelector('[data-close-trial]').onclick=()=>{if(!busy){generation++;root.replaceChildren();root.hidden=true}};
    form.onsubmit=async e=>{
      e.preventDefault();if(busy||!selected)return;
      if(selected.past&&!confirm(`Подтвердить: ребёнок пришёл на ПУ ${dShort(selected.date)} в ${selected.time}?`))return;
      busy=true;submit.disabled=true;f.group_id.disabled=true;f.month.disabled=true;error.textContent='';
      try{await api('POST',`/api/students/${d.student.id}/trial-calendar`,{group_id:selected.group_id,date:selected.date,time:selected.time,request_id:requestId,future_only:futureOnly});toast(selected.past?'Прошедшее ПУ сохранено':'Записан на ПУ');reload()}
      catch(e){error.textContent=e.message;busy=false;submit.disabled=false;f.group_id.disabled=false;f.month.disabled=false}
    };
    await load();root.scrollIntoView({block:'nearest',behavior:'smooth'});
  }catch(e){root.textContent=e.message}
}


function hideTrialClientFields(form) {
  ['child_name','birth','parent_name','phone','email'].forEach(name=>{
    const input=form.elements[name];if(!input)return;
    const field=input.closest('.field');if(field)field.hidden=true;
    input.required=false;input.type='hidden';
  });
}
