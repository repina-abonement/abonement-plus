/* Посещения клиента: один календарь и запись на отработку. */
const ATTENDANCE_KINDS={subscription:['А','Абонемент'],makeup:['О','Отработка'],trial:['П','Пробное'],single:['Р','Разовое']};
const ATTENDANCE_STATUS={present:'Был',absent:'Пропуск',planned:'Запланировано',warned:'Предупредили, что не придёт'};
function clientAttendanceMarkup(){return `<h3>Посещения и отработки</h3><div id="client-attendance"><div class="small muted">Откройте вкладку, чтобы посмотреть календарь.</div></div>`}
function wireClientAttendance(body,studentData,onChanged){
  const box=body.querySelector('#client-attendance');if(!box)return;
  const sid=studentData.student.id;
  let month=history.state?.studentCard?.attendanceMonth||studentData.cur_month,day=history.state?.studentCard?.attendanceDay||null,data=null,slots=null,generation=0;
  const saveMonth=()=>{if(history.state?.studentCard)try{history.replaceState({...history.state,studentCard:{...history.state.studentCard,attendanceMonth:month,attendanceDay:day}},'')}catch(_){}};
  const refresh=()=>{if(!document.body.contains(box))return;saveMonth();if(onChanged)onChanged();else load()};
  const setMonth=delta=>{const [y,m]=month.split('-').map(Number),date=new Date(y,m-1+delta,1);month=`${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}`;day=null;load()};
  const load=async()=>{
    const request=++generation;box.innerHTML='<p class="muted">Загружаю календарь…</p>';
    try{const value=await api('GET',`/api/students/${sid}/attendance-calendar?month=${month}`);if(request!==generation||!document.body.contains(box))return;data=value;day=day?.startsWith(month)?day:month===data.today.slice(0,7)?data.today:data.events[0]?.date||month+'-01';saveMonth();paint()}
    catch(e){box.innerHTML=`<div class="error">${esc(e.message)}</div>`}
  };
  const paint=()=>{
    const [year,m]=month.split('-').map(Number),first=new Date(year,m-1,1),cells=Array((first.getDay()+6)%7).fill('<span></span>');
    for(let n=1;n<=new Date(year,m,0).getDate();n++){
      const iso=`${month}-${String(n).padStart(2,'0')}`,events=data.events.filter(e=>e.date===iso);
      cells.push(`<div class="attendance-day ${iso===day?'selected':''} ${iso===data.today?'today':''}" data-att-day="${iso}"><button type="button" class="attendance-date" data-att-select="${iso}" aria-label="Открыть ${dShort(iso)}">${n}</button><div class="attendance-day-tags">${events.map(e=>{
        const index=data.events.indexOf(e),editable=can('journal','full')&&e.group_id&&e.kind!=='trial'&&(!e.future||e.kind==='subscription');
        const status=e.status==='present'?'✓ Был':e.status==='absent'?'× Пропуск':e.status==='warned'?'Не придёт':e.future?'По плану':'Отметить';
        return `<div class="attendance-cell-event att-${e.kind} att-status-${e.status}"><strong>${ATTENDANCE_KINDS[e.kind][1]}</strong>${e.time?`<small>${esc(e.time)}</small>`:''}${editable?`<button type="button" class="attendance-cell-mark" data-att-edit="${index}" aria-expanded="false" aria-label="${dShort(iso)}, ${ATTENDANCE_KINDS[e.kind][1]}: ${status}">${status}</button><div class="attendance-cell-menu" data-att-menu="${index}" hidden>${e.future?`<button type="button" data-att-cell-warn="${index}">${e.status==='warned'?'Снова придёт':'Не придёт'}</button>`:['present','absent','clear'].map(v=>`<button type="button" data-att-cell-mark="${index}|${v}">${v==='present'?'✓ Был':v==='absent'?'× Пропуск':'Снять отметку'}</button>`).join('')}</div>`:`<span class="attendance-cell-status">${status}</span>`}</div>`;
      }).join('')}</div></div>`);
    }
    const c=data.counters;
    box.innerHTML=`<div class="attendance-toolbar"><button class="btn sm" data-att-prev aria-label="Предыдущий месяц">←</button><input type="month" id="att-month" value="${month}" aria-label="Месяц посещений"><button class="btn sm" data-att-next aria-label="Следующий месяц">→</button></div>
      <div class="attendance-stats"><div><b>${data.attended}</b><span>посещено за месяц</span></div><div><b>${data.missed}</b><span>пропусков за месяц</span></div><div class="available"><b>${c.available}</b><span>отмеченных пропусков доступно</span></div><div><b>${c.booked}</b><span>записано на отработку</span></div></div>
      <div class="attendance-legend">${Object.entries(ATTENDANCE_KINDS).map(([k,[short,label]])=>`<span><i class="att-${k}" aria-hidden="true">●</i>${label}</span>`).join('')}<span>✓ был · × пропуск · ○ план</span></div>
      <p class="attendance-calendar-hint small muted">Листайте календарь вбок, чтобы увидеть остальные дни недели.</p><div class="attendance-calendar-scroll" tabindex="0" role="region" aria-label="Календарь посещений"><div class="attendance-calendar">${WD.map(d=>`<b class="attendance-weekday">${d}</b>`).join('')}${cells.join('')}</div></div><div id="attendance-day-detail"></div>
      <div class="makeup-panel"><h4>Отработать пропуски</h4><p class="small muted">Только занятия с отметкой «Пропуск» за текущий и прошлый месяц, включая отмеченные будущие пропуски. Прошедшие дни без отметок не учитываются. Уже отработанные и учтённые в перерасчёте сюда не входят.</p>${c.available?`<p class="small muted">Текущий месяц: ${c.available_cur||0}, прошлый: ${c.available_prev||0}. ${c.available_future?`Из них будущих: ${c.available_future}.`:""}</p><div class="sc-actions"><button class="btn primary" id="att-find-makeup">Подобрать отработку</button><button class="btn" id="att-makeup-message">Подготовить сообщение родителю</button></div>`:'<p class="muted">Нет доступных пропусков для отработки</p>'}<div id="attendance-slots"></div><div id="attendance-message"></div></div>`;
    box.querySelector('[data-att-prev]').onclick=()=>setMonth(-1);box.querySelector('[data-att-next]').onclick=()=>setMonth(1);
    box.querySelector('#att-month').onchange=e=>{if(e.target.value){month=e.target.value;day=null;load()}};
    box.querySelectorAll('[data-att-select]').forEach(b=>b.onclick=()=>{day=b.dataset.attSelect;box.querySelectorAll('[data-att-day]').forEach(x=>x.classList.toggle('selected',x.dataset.attDay===day));saveMonth();paintDay()});
    box.querySelectorAll('[data-att-edit]').forEach(b=>b.onclick=()=>{
      const menu=box.querySelector(`[data-att-menu="${b.dataset.attEdit}"]`),open=menu.hidden;
      box.querySelectorAll('[data-att-menu]').forEach(x=>x.hidden=true);box.querySelectorAll('[data-att-edit]').forEach(x=>x.setAttribute('aria-expanded','false'));
      menu.hidden=!open;b.setAttribute('aria-expanded',String(open));
    });
    box.querySelectorAll('[data-att-cell-mark]').forEach(b=>b.onclick=()=>{const [i,status]=b.dataset.attCellMark.split('|');markEvent(data.events[+i],status,b)});
    box.querySelectorAll('[data-att-cell-warn]').forEach(b=>b.onclick=()=>warnEvent(data.events[+b.dataset.attCellWarn],b));
    if(c.available){box.querySelector('#att-find-makeup').onclick=()=>showSlots();box.querySelector('#att-makeup-message').onclick=()=>showMessage()}
    paintDay();
  };
  const paintDay=()=>{
    const root=box.querySelector('#attendance-day-detail'),events=data.events.filter(e=>e.date===day);
    root.innerHTML=`<h4>${dShort(day)} · ${monthLabel(month)}</h4>${events.length?events.map((e,i)=>`<div class="attendance-event"><div><span class="pill att-${e.kind}">${ATTENDANCE_KINDS[e.kind][1]}</span><b>${ATTENDANCE_STATUS[e.status]}</b></div><div class="small muted">${esc([e.time,e.group,e.branch].filter(Boolean).join(' · '))}</div>${e.note?`<p>${esc(e.note)}</p>`:''}
      ${can('journal','full')&&e.group_id&&e.kind!=='trial'&&!e.future?`<div class="sc-actions"><button class="btn attendance-detail-mark" data-att-mark="${i}|present">Был</button><button class="btn attendance-detail-mark" data-att-mark="${i}|absent">Пропуск</button><button class="btn attendance-detail-mark" data-att-mark="${i}|clear">Снять отметку</button></div>`:''}
      ${can('journal','full')&&e.future&&e.kind==='subscription'?`<button class="btn attendance-detail-mark" data-att-warn="${i}">${e.status==='warned'?'Снова придёт':'Не придёт'}</button>`:''}
      ${e.makeup_id&&e.status==='planned'?`<button class="linkish small" data-att-unbook="${e.makeup_id}">Отменить запись на отработку</button>`:''}</div>`).join(''):'<p class="muted">На этот день нет занятий</p>'}`;
    root.querySelectorAll('[data-att-mark]').forEach(b=>b.onclick=()=>{const [i,status]=b.dataset.attMark.split('|');markEvent(events[+i],status,b)});
    root.querySelectorAll('[data-att-warn]').forEach(b=>b.onclick=()=>warnEvent(events[+b.dataset.attWarn],b));
    root.querySelectorAll('[data-att-unbook]').forEach(b=>b.onclick=async()=>{try{await api('POST',`/api/makeups/${b.dataset.attUnbook}/status`,{status:'cancelled'});slots=null;refresh()}catch(err){fail(err)}});
  };
  const markEvent=async(e,status,b)=>{try{b.disabled=true;day=e.date;saveMonth();if(e.makeup_id)await api('POST',`/api/makeups/${e.makeup_id}/status`,{status:status==='present'?'done':status==='absent'?'missed':'booked'});else await api('POST','/api/attendance',{student_id:sid,group_id:e.group_id,date:e.date,status});toast('Отметка сохранена');refresh()}catch(err){b.disabled=false;fail(err)}};
  const warnEvent=async(e,b)=>{try{b.disabled=true;day=e.date;saveMonth();await api('POST','/api/journal/warn',{student_id:sid,group_id:e.group_id,date:e.date,undo:e.status==='warned'});refresh()}catch(err){b.disabled=false;fail(err)}};
  const fetchSlots=async()=>{slots=await api('GET',`/api/students/${sid}/makeup-suggestions`);return slots};
  const showSlots=async()=>{
    const root=box.querySelector('#attendance-slots');root.innerHTML='<p class="muted">Подбираю подходящие группы…</p>';
    try{const result=await fetchSlots();if(!document.body.contains(root))return;
      const list=result.slots;
      root.innerHTML=`<p class="small muted">Сначала группы с наименьшим числом записанных детей, затем ближайшие даты. Число мест проверяется при записи.</p><div class="field"><label>Какой пропуск отрабатываем</label><select id="att-absence">${result.counters.available_ids.map(a=>`<option value="${a.id}">${dShort(a.date)}</option>`).join('')}</select></div><div class="makeup-slot-list">${list.length?list.map((s,i)=>`<div class="makeup-slot ${i>=6?'extra-slot':''}"><div><b>${dShort(s.date)} · ${esc(s.time)}</b><div>${esc(s.group)}</div><div class="small muted">${esc(s.branch)}</div>${s.age_warning?`<div class="small">Согласовать с тренером: ${esc(s.age_warning)}</div>`:''}${s.approval_required?'<div class="small muted">По согласованию с администратором</div>':''}</div><div class="makeup-slot-count"><b>${s.count}${s.limit?' / '+s.limit:''}</b><small>записано детей</small><button class="btn sm" data-att-book="${i}">Записать</button></div></div>`).join(''):'<p class="muted">На ближайшие недели нет подходящих свободных мест. Можно проверить позже или согласовать вариант с тренером.</p>'}</div>${list.length>6?'<button class="linkish" id="att-more-slots">Показать остальные варианты</button>':''}`;
      const more=root.querySelector('#att-more-slots');if(more)more.onclick=()=>{root.querySelectorAll('.extra-slot').forEach(e=>e.classList.remove('extra-slot'));more.remove()};
      root.querySelectorAll('[data-att-book]').forEach(b=>b.onclick=async()=>{const s=list[+b.dataset.attBook];if(!confirm(`Записать на ${dShort(s.date)} в ${s.time}, ${s.group}?${s.age_warning?' Вариант требует согласования с тренером.':''}`))return;try{b.disabled=true;await api('POST',`/api/students/${sid}/makeups`,{group_id:s.group_id,date:s.date,absence_id:+root.querySelector('#att-absence').value});toast('Записан на отработку');month=s.date.slice(0,7);day=s.date;slots=null;refresh()}catch(err){b.disabled=false;fail(err)}});
    }catch(e){root.innerHTML=`<div class="error">${esc(e.message)}</div>`}
  };
  const showMessage=async()=>{
    const root=box.querySelector('#attendance-message');root.innerHTML='<p class="muted">Готовлю сообщение…</p>';
    try{const result=await fetchSlots();if(!document.body.contains(root))return;root.innerHTML=`<label>Сообщение родителю — можно отредактировать</label><textarea id="att-message-text" rows="8">${esc(result.message)}</textarea><div class="sc-actions"><button class="btn" id="att-copy-message">Скопировать</button><button class="btn primary" id="att-to-chat">Вставить в чат с семьёй</button></div>`;
      root.querySelector('#att-copy-message').onclick=()=>copyTextWithNotice(root.querySelector('#att-message-text').value);
      root.querySelector('#att-to-chat').onclick=()=>{const text=root.querySelector('#att-message-text').value;scTab('feed');const input=$('#cm-text');if(!input)return toast('Чат не найден: скопируйте сообщение',true);input.value=text;input.dispatchEvent(new Event('input',{bubbles:true}));input.focus();input.scrollIntoView({block:'center',behavior:'smooth'});toast('Текст вставлен. Проверьте и отправьте')};
    }catch(e){root.innerHTML=`<div class="error">${esc(e.message)}</div>`}
  };
  body.querySelector('[data-sctab=visits]')?.addEventListener('click',()=>{if(!data)load()});
  if(body.querySelector('.sc-grid')?.dataset.tab==='visits')load();
}
