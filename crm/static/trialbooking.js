"use strict";
/* Запись из карточки без смены вкладки и перехода в переписку. */
function openInlineTrialBooking(d, root, reload) {
  if (!root) return;
  root.hidden=false;
  if(root.querySelector('form')) {root.scrollIntoView({block:'nearest',behavior:'smooth'});return;}
  const p=d.parents[0]||{},s=d.student;
  const branches=(S.meta.branches||[]).filter(b=>b.active!==0);
  root.innerHTML=`<form class="inline-trial-form"><h3>Запись на пробное</h3><p class="small muted">Заполните данные и выберите свободное занятие. Запись сохранится после подтверждения.</p><div class="inline-trial-grid"><div class="field"><label>Имя и фамилия ребёнка</label><input name="child_name" value="${esc(s.name||'')}" required autocomplete="off"></div><div class="field"><label>Дата рождения ребёнка</label><input name="birth" type="date" value="${esc((s.birth||'').slice(0,10))}" max="${esc(S.meta.today)}" required></div><div class="field"><label>Имя родителя</label><input name="parent_name" value="${esc(p.name||'')}" required></div><div class="field"><label>Телефон родителя</label><input name="phone" type="tel" value="${esc(p.phone||'')}" required></div><div class="field"><label>Почта родителя · необязательно</label><input name="email" type="email" value="${esc(p.email||'')}"></div><div class="field"><label>Филиал</label><select name="branch_id"><option value="">Все филиалы</option>${branches.map(b=>`<option value="${b.id}" ${b.id===s.branch_id?'selected':''}>${esc(b.name)}</option>`).join('')}</select></div><div class="field"><label>День занятия</label><input name="date" type="date" min="${esc(S.meta.today)}" required></div><div class="field"><label>Группа</label><select name="group_id" required disabled><option value="">Сначала заполните дату рождения</option></select></div><div class="field"><label>Время занятия</label><select name="time" required disabled><option value="">Выберите группу</option></select></div></div><div class="small muted" data-trial-availability aria-live="polite"></div><div class="need-err small" data-trial-error role="alert"></div><div class="sc-actions"><button class="btn primary" type="submit" disabled>Подтвердить запись</button><button class="btn" type="button" data-trial-cancel>Отмена</button></div></form>`;
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
    if(!f.birth.value){notice.textContent='Заполните дату рождения ребёнка, чтобы увидеть дни и время.';return;}
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
  splitChildNameFields(form,'child_name',true);load();root.scrollIntoView({block:'nearest',behavior:'smooth'});f.child_name_last.focus({preventScroll:true});
}
