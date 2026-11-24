"use strict";
/* Абонемент Плюс — планер: свои дела, поручения, повторы, дни рождения участников команды, контроль команды. */

S.planner = { tab: "week", start: null, user: null, status: "all" };
const PLAN_SHIFTS={common:'Общие задачи дня',morning:'☀️ Утренняя смена',evening:'🔵 Основная смена',both:'Обе смены'};
function planShiftOptions(value){return Object.entries(PLAN_SHIFTS).map(([key,label])=>`<option value="${key}" ${key===value?'selected':''}>${label}</option>`).join('')}
const WD_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

/* форма новой задачи: кому, когда, повтор */
async function openTaskForm(box, done, preset = {}) {
  if (!box) return;
  if (box.innerHTML) { box.innerHTML = ""; return; }
  let p;
  try { p = await api("GET", "/api/planner"); } catch (e) { return fail(e); }
  const staff = p.staff;
  const targetId=+(preset.assignee_id||p.my_board_id||S.user.id),target=staff.find(x=>+x.id===targetId),lockedTarget=!!preset.assignee_id;
  if(lockedTarget&&!target){box.innerHTML='<p class="need-err">Эта доска недоступна для назначения. Обновите страницу и выберите планер заново</p>';return}

  box.innerHTML = `<div class="sub-form task-form">${lockedTarget?`<div class="planner-form-target"><b>Новая задача</b><div>Кому: ${esc(target.name)}</div><input type="hidden" data-ta value="${targetId}"></div>`:''}
    <div class="field"><label>Название задачи</label><input data-tt maxlength="200" placeholder="Коротко: что нужно сделать" value="${esc(preset.title || "")}"><small class="muted">Короткое название действия. Подробности укажите ниже</small></div>
    <div class="row" style="flex-wrap:wrap;align-items:flex-end">
      ${p.can_manage && !lockedTarget ? `<div class="field"><label>Кому</label><select data-ta>${staff.map((u) => `<option value="${u.id}" ${+u.id === targetId ? "selected" : ""}>${esc(u.name)}${u.id === S.user.id ? " (себе)" : ""}</option>`).join("")}</select></div>` : ""}
      <div class="field" style="flex:0 0 150px"><label>Когда</label><input type="date" data-td value="${preset.date || S.meta.today}"></div>
      <div class="field" style="flex:0 0 110px"><label>Время</label><input type="time" data-ttm></div>
      <div class="field" style="flex:0 0 170px"><label>Повтор</label><select data-tr><option value="">Не повторять</option>
        ${Object.entries(p.rules).map(([k, t]) => `<option value="${k}">${t[0].toUpperCase() + t.slice(1)}</option>`).join("")}</select></div>
    </div>
    <div data-twd hidden class="chips" style="margin:-2px 0 8px">${WD_SHORT.map((w, i) => `<button type="button" class="chip day" data-twdi="${i}">${w}</button>`).join("")}</div>
    <div class="field" data-shift-field><label>Смена</label><select data-task-shift>${planShiftOptions(preset.shift||'common')}</select><small class="muted">Утренняя и основная смены отдельно по понедельникам и средам. В другие дни задача выполняется один раз</small></div>
    <div class="field"><label>Подробное описание задачи</label><textarea data-tn rows="4" maxlength="1000" placeholder="Контекст, условия выполнения и ожидаемый результат"></textarea><small class="muted">Вся информация, которая понадобится исполнителю</small></div>
    <div class="field"><label>Подзадачи (каждая с новой строки)</label><textarea data-new-subtasks rows="3" placeholder="Проверить кассу&#10;Проверить уборку"></textarea><small class="muted">У каждого дня и каждой смены будут свои галочки</small></div>
    <div class="row" style="align-items:center;flex-wrap:wrap"><label class="check grow"><input type="checkbox" data-tp> 🔥 Важно</label>
      <button class="btn" data-tcancel style="flex:0">Отмена</button>
      <button class="btn primary" data-tsave style="flex:0">Поставить задачу</button></div>
    <div class="small muted">Без времени задача висит весь день. Повторяющиеся дела сами появляются на столе в нужные дни</div>
  </div>`;
  box.querySelector("[data-tcancel]").onclick = () => { box.innerHTML = ""; };
  const rule = box.querySelector("[data-tr]");
  rule.onchange = () => { box.querySelector("[data-twd]").hidden = rule.value !== "weekly"; };
  box.querySelectorAll("[data-twdi]").forEach((b) => (b.onclick = () => b.classList.toggle("on")));
  const syncShift=()=>{const target=box.querySelector('[data-ta]')?.value||preset.assignee_id||p.my_board_id||S.user.id;const field=box.querySelector('[data-shift-field]');field.hidden=+target!==+p.shift_board_id;if(field.hidden)box.querySelector('[data-task-shift]').value='common'};
  const assignee=box.querySelector('[data-ta]');if(assignee)assignee.onchange=syncShift;syncShift();
  box.querySelector("[data-tt]").focus();
  box.scrollIntoView({block:"nearest",behavior:"smooth"});
  box.querySelector("[data-tsave]").onclick = async () => {
    const a = box.querySelector("[data-ta]");
    const data = { title: box.querySelector("[data-tt]").value, date: box.querySelector("[data-td]").value, time: box.querySelector("[data-ttm]").value,
      subtasks:box.querySelector("[data-new-subtasks]").value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean),shift_mode:box.querySelector("[data-task-shift]").value,note: box.querySelector("[data-tn]").value, priority: box.querySelector("[data-tp]").checked, assignee_id: a ? +a.value : S.user.id };
    if (rule.value) data.repeat = { rule: rule.value, weekdays: [...box.querySelectorAll("[data-twdi].on")].map((x) => +x.dataset.twdi) };
    try { await api("POST", "/api/planner/tasks", data); toast("Задача поставлена"); box.innerHTML = ""; done(); } catch (e) { fail(e); }
  };
}

async function renderPlanner(page) {
  const p = await api("GET", "/api/planner");
  const C = S.planner;
  S.tasks.canManage = p.can_manage;
  const tabs = [["week", "Неделя"], ["given", "Я поручила"], ["recurring", "Повторы"]];
  if (p.can_manage) tabs.push(["team", "Команда"], ["bdays", "Дни рождения"]);
  if (!tabs.some(([k]) => k === C.tab)) C.tab = "week";
  let body = "";
  if (C.tab === "week") {
    body = `<div id="pl-week"></div>`;
  } else if (C.tab === "given") {
    const givenRow = (t) => `<div class="task ${t.status !== "open" ? "done" : ""}" data-plan-detail="${t.id}" role="button" tabindex="0"><span class="task-ico">${t.status === "done" ? "✅" : t.due_at.slice(0, 10) < p.today ? "🔴" : "⏳"}</span>
      <div class="grow"><b>${esc(t.title)}</b></div>
      ${t.status === "done" && p.can_manage ? `<button class="btn sm" data-tret="${t.id}">Вернуть</button>` : ""}
      ${t.status === "open" ? `<button class="btn sm" data-tcancel="${t.id}">✕</button>` : ""}</div>`;
    const series = new Map();
    const oneOff = [];
    for (const task of p.given) {
      if (!task.recur_id) {
        oneOff.push(task);
        continue;
      }
      if (!series.has(task.recur_id)) series.set(task.recur_id, []);
      series.get(task.recur_id).push(task);
    }
    body = oneOff.map(givenRow).join("");
    if (series.size) {
      body += `<section class="task-group"><h4>Повторяющиеся задачи · ${series.size}</h4>
        ${[...series.values()].map((items) => `<details class="panel">
          <summary><b>${esc(items[0].title)}</b> <span class="small muted">→ ${esc(items[0].assignee)} · дат: ${items.length}</span></summary>
          ${items.map(givenRow).join("")}</details>`).join("")}</section>`;
    }
    if (!body) body = '<div class="muted">Вы пока никому не ставили задачи</div>';
  } else if (C.tab === "recurring") {
    body = p.recurring.length ? `<div class="list">${p.recurring.map((r) => `<div class="list-row"><div class="grow">🔁 <b>${esc(r.title)}</b>
      <div class="small muted">${esc(r.assignee)}${r.shift_mode&&r.shift_mode!=='common'?' · '+PLAN_SHIFTS[r.shift_mode]:''} · ${r.rule_label}${r.rule === "weekly" ? ": " + r.weekdays.split(",").map((x) => WD_SHORT[+x]).join(", ") : r.rule === "monthly" ? `, ${r.monthday} числа` : r.rule === "yearly" ? `, ${r.yearday.slice(3)}.${r.yearday.slice(0, 2)}` : ""}${r.time ? ` в ${r.time}` : ""}</div></div>
      ${r.can_edit===false?'':`<button class="btn sm" data-rstop="${r.id}">Убрать будущие повторы</button>`}</div>`).join("")}</div>` : `<div class="muted">Повторяющихся дел нет. Поставьте задачу и выберите «Повтор»: например, «Купить воду» каждую неделю</div>`;
  } else if (C.tab === "team") {
    const staffOpts = (p.people||p.staff).filter((u) => u.role !== "coach" && u.role !== "offline_pool").map((u) => `<option value="${u.id}" ${u.id === p.controller ? "selected" : ""}>${esc(u.name)}</option>`).join("");
    body = `${S.user.role === "admin" ? `<div class="row" style="max-width:520px;align-items:flex-end;margin-bottom:12px"><div class="field"><label>Контролёр задач команды</label><select id="pl-ctrl"><option value="">Только я</option>${staffOpts}</select></div></div>` : ""}
      ${S.user.role==='admin'?`<div class="field" style="max-width:520px"><label>Общий Планер офлайн-администраторов — кто ещё может смотреть</label><select id="pl-offline-viewer"><option value="">Только руководитель и офлайн-администраторы</option>${(p.people||p.staff).filter(u=>u.role==='operator').map(u=>`<option value="${u.id}" ${String(p.offline_viewer)===String(u.id)?'selected':''}>${esc(u.name)}</option>`).join('')}</select><span class="small muted">Выберите Варю. Доступ только на просмотр</span></div>`:''}
      ${p.team.length ? p.team.map((u) => `<section class="panel" style="padding:12px 14px;margin-bottom:10px">
        <div class="team-head"><b>${esc(u.name)}</b>
          ${u.overdue.length ? `<span class="pill red">просрочено ${u.overdue.length}</span>` : ""}<span class="pill">сегодня ${u.today.length}</span><span class="pill green">сделано ${u.done_today}</span></div>
        ${[...u.overdue, ...u.today].map((t) => `<div class="small" style="margin-top:6px">${t.due_at.slice(0, 10) < p.today ? "🔴" : "⏳"} ${esc(t.title)} <span class="muted">${dtWhen(t.due_at)}${t.created_by ? `, поставил(а) ${esc(t.created_by)}` : ""}</span></div>`).join("")}
      </section>`).join("") : `<div class="muted">У команды на сегодня всё сделано 🎉</div>`}`;
  } else if (C.tab === "bdays") {
    body = `<div class="small muted" style="margin-bottom:10px">За ${7} дней до дня рождения контролёру придёт задача «Создать чат в Telegram для сбора денег», в сам день «Поздравить»</div>
      <div class="list">${(p.people||p.staff).filter(u=>u.role!=='offline_pool').map((u) => `<div class="list-row"><span class="grow">${esc(u.name)}</span><input type="date" data-ub="${u.id}" value="${esc(u.birth || "")}" style="flex:0 0 170px"></div>`).join("")}</div>`;
  }
  page.innerHTML = `<div class="page-head"><h2 class="grow">Планер</h2><div class="chips planner-header-actions">${["admin","operator"].includes(S.user.role)?'<button class="btn" id="pl-add-event">+ Мероприятие</button>':''}<button class="btn primary" id="pl-add">+ Задача</button></div></div>
    <div id="pl-form"></div>
    <div class="seg" style="margin-bottom:14px;flex-wrap:wrap">${tabs.map(([k, t]) => `<button data-pt="${k}" class="${C.tab === k ? "active" : ""}">${t}</button>`).join("")}</div>
    <div class="tasks-box">${body}</div>`;
  page.querySelectorAll("[data-pt]").forEach((b) => (b.onclick = () => { step(() => { C.tab = b.dataset.pt; }); }));
  $("#pl-add").onclick = () => openTaskForm($("#pl-form"), render, C.tab === "week" && C.user ? { assignee_id: C.user,date:C.layout==='day'?C.start:S.meta.today } : {});
  const addEvent=$('#pl-add-event');if(addEvent)addEvent.onclick=()=>{
    document.querySelectorAll('.planner-event-host').forEach(el=>el.remove());
    const host=document.createElement('div');host.className='planner-event-host';$('#pl-form').before(host);
    openPlannerEventForm(host,C.tab==='week'&&C.layout==='day'?C.start:S.meta.today,day=>{C.tab='week';C.start=day||C.start||S.meta.today;return render()});
  };
  if (C.tab === "week") await renderWeek($("#pl-week"));
  wireTasks(page);
  page.querySelectorAll("[data-plan-detail]").forEach(el => {
    const open = e => { if (!e.target.closest("button, input, label")) showPlannerTask(p.given.find(t => t.id === +el.dataset.planDetail)); };
    el.onclick = open;
    el.onkeydown = e => { if (e.target === el && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); open(e); } };
  });
  page.querySelectorAll("[data-tret]").forEach((b) => (b.onclick = async () => {
    const note = prompt("Что не так? Участник команды увидит этот комментарий");
    if (note === null) return;
    try { await api("POST", `/api/planner/tasks/${b.dataset.tret}/return`, { note }); toast("Задача вернулась участнику команды"); render(); } catch (e) { fail(e); }
  }));
  page.querySelectorAll("[data-rstop]").forEach(b=>b.onclick=()=>stopPlannerRepeat(b.dataset.rstop,b,render));
  page.querySelectorAll("[data-ub]").forEach((i) => (i.onchange = async () => {
    try { await api("PUT", `/api/users/${i.dataset.ub}/birth`, { birth: i.value }); toast("Сохранено"); } catch (e) { fail(e); }
  }));
  if($('#pl-offline-viewer'))$('#pl-offline-viewer').onchange=async e=>{try{await api('PUT','/api/planner/settings',{offline_planner_viewer:e.target.value});toast('Доступ сохранён');render()}catch(err){fail(err)}};
  if ($("#pl-ctrl")) $("#pl-ctrl").onchange = async (e) => {
    try { await api("PUT", "/api/planner/settings", { planner_controller: e.target.value }); toast("Контролёр назначен"); render(); } catch (err) { fail(err); }
  };
}


/* ---------- неделя колонками, как в Weeek ---------- */
const PL_DAY = new Intl.DateTimeFormat("ru-RU", { weekday: "long", day: "numeric", month: "long" });
function pcard(t) {
  const done = t.status === "done",readOnly=Boolean(S.planner.readOnly);
  const merchOrder=t.auto_key==='merch-stock-order';
  return `<article class="pcard ${done ? "done" : ""} ${t.priority ? "hot" : ""} ${merchOrder?'merch-order':''}" draggable="${done||readOnly ? "false" : "true"}" data-pc="${t.id}" role="button" tabindex="0" aria-label="${esc(t.title)}">
    <label class="pcheck"><input type="checkbox" aria-label="Задача выполнена" data-pdone="${t.id}" ${done ? "checked" : ""} ${readOnly?'disabled':''}></label>
    <div class="grow"><div class="ptitle">${t.priority ? "🔥 " : ""}${esc(t.title)}</div>
      ${t.subtasks?.length?`<div class="small muted" data-checklist-progress>${t.subtasks.filter(x=>x.done).length}/${t.subtasks.length} подзадач</div>`:''}
      ${done?`<div class="small muted">${esc(t.done_by||'')} · ${esc(dtWhen(t.done_at))}</div>`:''}
    </div>
    ${done||readOnly ? "" : `<button class="pmove" data-pmove="${t.id}" title="Перенести на другой день">📅</button>`}
  </article>`;
}

function openPlannerEventForm(host,day,refresh,event={}){
  host.innerHTML=`<form class="planner-event-form sub-form"><h3>${event.id?'Мероприятие':'Новое мероприятие'}</h3><label>Название<input name="title" required maxlength="200" value="${esc(event.title||'')}"></label><label>Дата<input name="day" type="date" required value="${esc(event.day||day)}"></label><div class="row"><label>С<input name="start_time" type="time" value="${esc(event.start_time||'')}"></label><label>До<input name="end_time" type="time" value="${esc(event.end_time||'')}"></label></div><label>Филиал<select name="branch_id"><option value="">Не указан</option>${(S.meta.branches||[]).map(b=>`<option value="${b.id}" ${b.id===event.branch_id?'selected':''}>${esc(b.name)}</option>`).join('')}</select></label><label>Подробности<textarea name="note" maxlength="2000">${esc(event.note||(!event.id?event.participants:'')||'')}</textarea></label><p class="need-err" data-event-error></p><div class="chips"><button class="btn primary" type="submit">Сохранить</button><button class="btn" type="button" data-event-close>Закрыть</button>${event.id?'<button class="btn danger" type="button" data-event-cancel>Отменить мероприятие</button>':''}</div></form>`;
  const form=host.querySelector('form');form.querySelector('[data-event-close]').onclick=()=>host.remove();
  const cancel=form.querySelector('[data-event-cancel]');if(cancel)cancel.onclick=async()=>{if(!confirm('Отменить мероприятие? Оно останется в истории'))return;try{await api('PATCH',`/api/planner/events/${event.id}`,{cancelled:true});refresh()}catch(e){form.querySelector('[data-event-error]').textContent=e.message}};
  form.onsubmit=async e=>{e.preventDefault();const button=form.querySelector('[type=submit]');button.disabled=true;try{const data=Object.fromEntries(new FormData(form));data.branch_id=data.branch_id?+data.branch_id:null;await api(event.id?'PATCH':'POST',`/api/planner/events${event.id?'/'+event.id:''}`,data);refresh(data.day)}catch(error){form.querySelector('[data-event-error]').textContent=error.message;button.disabled=false}};
  form.querySelector('input').focus();host.scrollIntoView({block:'nearest'});
}

async function renderWeek(box) {
  const C = S.planner;
  if (!C.start) C.start = S.meta.today;
  const w = await api("GET", `/api/planner/week?days=4&start=${C.start}${C.user ? "&user=" + C.user : ""}`);
  const end = addDays(w.start, 3), today = S.meta.today;
  C.readOnly=w.can_edit===false;C.user=w.user_id;
  const addButton=$('#pl-add');if(addButton)addButton.disabled=w.can_add===undefined?C.readOnly:!w.can_add;
  const visible = (t) => (C.status === "all" || t.status === C.status) && (!w.shifts_enabled || !C.shift || C.shift==='all' || !t.shift || t.shift==='common' || t.shift===C.shift);
  const overdue = w.overdue.filter(visible);
  const days = w.days.map((day) => ({ ...day, tasks: day.tasks.filter(visible) }));
  const plainGroups = (tasks) => {
    if (!tasks.length) return '<div class="empty">Свободно</div>';
    const recurring = tasks.filter((task) => task.recur_id);
    if (!recurring.length) return tasks.map(pcard).join("");
    const other = tasks.filter((task) => !task.recur_id);
    return [[recurring, "Повторяемые"], [other, "Прочие"]]
      .filter(([items]) => items.length)
      .map(([items, title]) => `<div class="small muted">${title}</div>${items.map(pcard).join("")}`)
      .join("");
  };
  const groups=(items,day)=>{
    if(!w.shifts_enabled || ![1,3].includes(new Date(day+'T12:00:00').getDay()))return plainGroups(items);
    return ['morning','evening','common'].filter(shift=>!C.shift||C.shift==='all'||shift==='common'||shift===C.shift).map(shift=>`<section class="planner-shift planner-shift-${shift}" data-drop-shift="${shift}"><div class="planner-shift-heading"><b>${PLAN_SHIFTS[shift]}</b>${w.can_add?`<button type="button" class="btn sm" data-pq="${day}" data-pqshift="${shift}" aria-label="Добавить: ${PLAN_SHIFTS[shift]}">＋</button>`:''}</div>${plainGroups(items.filter(t=>(t.shift||'common')===shift))}</section>`).join('');
  };
  box.innerHTML = `<div class="week-nav">
      <button class="btn sm" data-wk="-7">‹</button>
      <b>${dShort(w.start)} – ${dShort(end)}</b>
      <button class="btn sm" data-wk="7">›</button>
      <button class="btn sm" data-wk="0">Эта неделя</button>
      <label>С даты <input type="date" id="wk-start" value="${w.start}"></label>
      <label>Статус <select id="wk-status"><option value="all">Все задачи</option><option value="open">Открытые</option><option value="done">Выполненные</option></select></label>
      ${w.can_manage ? `<select id="wk-user">${w.staff.map((u) => `<option value="${u.id}" ${u.id === w.user_id ? "selected" : ""}>${esc(u.name)}${u.id === S.user.id ? " (я)" : ""}</option>`).join("")}</select>` : ""}
    </div>
    ${w.shifts_enabled?`<label class="planner-shift-filter">Показать <select id="wk-shift"><option value="all">Все смены</option><option value="morning">Утро и общие задачи</option><option value="evening">Основная и общие задачи</option></select></label>`:''}
    ${overdue.length ? `<section class="col overdue-col"><div class="col-head"><h3>Просрочено: перетащите на нужный день</h3><span class="count">${overdue.length}</span></div>
      <div class="col-body ov-body">${overdue.map(pcard).join("")}</div></section>` : ""}
    <div class="board week-board">${days.map((d) => `<section class="col ${d.date === today ? "is-today" : ""} ${d.date < today ? "past" : ""}" data-day="${d.date}">
      <div class="col-head"><h3>${PL_DAY.format(new Date(d.date + "T00:00"))}</h3><span class="count">${d.tasks.length+(d.events||[]).length}</span></div>
      ${(d.events||[]).length?`<section class="planner-events-section" aria-label="Мероприятия"><div class="planner-section-heading"><b>Мероприятия</b><span>${(d.events||[]).length}</span></div>
      <div class="planner-events">${(d.events||[]).map(event=>`<button type="button" class="planner-event" data-event-edit="${event.id}"><b>🎉 ${esc(event.title)}</b><span>${event.start_time?esc(event.start_time+'–'+event.end_time):'Время не указано'}${event.branch_name?' · '+esc(event.branch_name):''}</span></button>`).join('')}</div>
      </section>`:''}
      <section class="planner-tasks-section" aria-label="Задачи"><div class="planner-section-heading"><b>Задачи</b><span>${d.tasks.length}</span></div><button type="button" class="btn sm pquick" data-pq="${d.date}" ${(w.can_add===undefined?C.readOnly:!w.can_add)?'disabled':''}>+ Добавить задачу</button><div class="col-body">${groups(d.tasks,d.date)}</div></section>
    </section>`).join("")}</div>
    <input type="date" id="pmove-date" style="position:absolute;opacity:0;pointer-events:none;width:1px;height:1px">`;
  box.querySelectorAll("[data-wk]").forEach((b) => (b.onclick = () => { C.start = +b.dataset.wk ? addDays(w.start, +b.dataset.wk) : S.meta.today; renderWeek(box); }));
  if ($("#wk-user")) $("#wk-user").onchange = (e) => { C.user = +e.target.value; renderWeek(box); };
  box.querySelector("#wk-start").onchange = (e) => {
    if (e.target.value) { C.start = e.target.value; renderWeek(box); }
  };
  const status = box.querySelector("#wk-status");
  status.value = C.status;
  status.onchange = () => { C.status = status.value; renderWeek(box); };
  const shiftFilter=box.querySelector('#wk-shift');if(shiftFilter){shiftFilter.value=C.shift||'all';shiftFilter.onchange=()=>{C.shift=shiftFilter.value;renderWeek(box)}}
  const reload = () => renderWeek(box);
  const allTasks = [...(w.overdue || []), ...w.days.flatMap(d => d.tasks)];
  box.querySelectorAll("[data-pc]").forEach(el => {
    const open = e => {
      if (e.target.closest("button, input, label")) return;
      const t = allTasks.find(t => t.id === +el.dataset.pc); if (!t) return;
      showPlannerTask(t);
    };
    el.onclick = open;
    el.onkeydown = e => { if (e.target === el && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); open(e); } };
  });
  const tc = box.querySelector(".week-board .is-today");  // на телефоне сразу показываем сегодня
  if (tc && window.innerWidth < 1100) box.querySelector(".week-board").scrollLeft = tc.offsetLeft - 12;
  // галочка «сделано»
  box.querySelectorAll("[data-pdone]").forEach((cb) => (cb.onchange = async () => {
    try {
      if (cb.checked) await api("POST", `/api/tasks/${cb.dataset.pdone}/done`, { result: "" });
      else await api("POST", `/api/planner/tasks/${cb.dataset.pdone}/reopen`, {});
      reload();
    } catch (e) { fail(e); cb.checked = !cb.checked; }
  }));
  // Быстрое добавление открывает одну широкую форму над неделей. Внутри
  // узкой колонки поля становились почти непригодными для заполнения.
  box.querySelectorAll("[data-pq]").forEach((button) => (button.onclick = () => {
    document.querySelectorAll('.planner-day-form').forEach(el=>el.remove());
    const form=$('#pl-form');
    if(!form)return;
    form.innerHTML='';
    openTaskForm(form, () => {
      if (C.status === "done") C.status = "all";
      reload();
    }, { date: button.dataset.pq, assignee_id: w.user_id,shift:button.dataset.pqshift||'common' });
  }));
  const eventForm=(button,day,event)=>{box.querySelectorAll('.planner-event-host').forEach(el=>el.remove());const host=document.createElement('div');host.className='planner-event-host';button.after(host);openPlannerEventForm(host,day,reload,event)};
  box.querySelectorAll('[data-event-edit]').forEach(button=>button.onclick=()=>{const event=w.days.flatMap(day=>day.events||[]).find(e=>e.id===+button.dataset.eventEdit);if(event)eventForm(button,event.day,event)});
  if(w.can_manage_events&&S.pendingPlannerEvent){
    const draft=S.pendingPlannerEvent;
    if(w.days.some(day=>day.date===draft.day)){delete S.pendingPlannerEvent;const branches=(S.meta.branches||[]).filter(b=>String(b.name).toLocaleLowerCase().includes(draft.branch_name.toLocaleLowerCase()));if(draft.branch_name&&branches.length===1)draft.branch_id=branches[0].id;const host=document.createElement('div');host.className='planner-event-host';const target=$('#pl-form');if(target)target.before(host);else box.prepend(host);openPlannerEventForm(host,draft.day,day=>{if(day)C.start=day;reload()},draft)}
  }
  // перенос: перетаскиванием (компьютер) или кнопкой 📅 (телефон)
  let dragId = null;
  box.querySelectorAll(".pcard[draggable=true]").forEach((el) => {
    el.addEventListener("dragstart", (e) => { dragId = el.dataset.pc; el.classList.add("dragging"); e.dataTransfer.effectAllowed = "move";e.dataTransfer.setData("text/plain",dragId); });
    el.addEventListener("dragend", () => {dragId=null;el.classList.remove("dragging");box.querySelectorAll(".drop").forEach(x=>x.classList.remove("drop"))});
  });
  box.querySelectorAll(".week-board .col").forEach((col) => {
    col.addEventListener("dragover", (e) => { if(!dragId||C.readOnly)return;e.preventDefault();box.querySelectorAll(".drop").forEach(x=>x.classList.remove("drop"));(e.target.closest("[data-drop-shift]")||col).classList.add("drop"); });
    col.addEventListener("dragleave", () => col.classList.remove("drop"));
    col.addEventListener("drop", async (e) => {
      e.preventDefault();box.querySelectorAll(".drop").forEach(x=>x.classList.remove("drop"));
      if (!dragId||C.readOnly) return;
      const target=e.target.closest('[data-drop-shift]'),data={date:col.dataset.day};if(target)data.shift=target.dataset.dropShift;
      const moved=dragId;dragId=null;
      try { await api("POST", `/api/planner/tasks/${moved}/move`, data); reload(); } catch (err) { fail(err); }
    });
  });
  const picker = $("#pmove-date");
  box.querySelectorAll("[data-pmove]").forEach((b) => (b.onclick = () => {
    picker.value = S.meta.today;
    picker.onchange = async () => { try { await api("POST", `/api/planner/tasks/${b.dataset.pmove}/move`, { date: picker.value }); toast("Перенесено"); reload(); } catch (e) { fail(e); } };
    try { picker.showPicker(); } catch (_) { const d = prompt("На какую дату? ГГГГ-ММ-ДД", addDays(S.meta.today, 1)); if (d) { picker.value = d; picker.onchange(); } }
  }));
}

async function stopPlannerRepeat(id, button, done) {
  if (!confirm("Остановить серию и убрать её незакрытые задачи на сегодня и будущие даты? Выполненные задачи и прошлые просроченные останутся в истории.")) return;
  button.disabled=true;
  try {
    await api("POST", `/api/planner/recurring/${id}/stop`, {});
    toast("Повторы остановлены. Выполненные задачи сохранены");
    await done();
  } catch(e) { fail(e); }
  finally { if(button.isConnected) button.disabled=false; }
}

async function showPlannerTask(t) {
  if (!t) return;
  openDrawer(`<div class="task-copyable"><h2>${esc(t.title)}</h2><div class="small muted">${esc(dtWhen(t.due_at))} · ${esc(t.assignee || "")}</div><div class="row" style="align-items:center;justify-content:space-between;gap:10px;margin-top:14px"><h3>Описание</h3>${t.note ? `<button type="button" class="btn sm" data-copy-task-description>Скопировать описание</button>` : ""}</div><div class="task-copyable" style="white-space:pre-wrap">${esc(t.note || "Описание не добавлено")}</div>${renewalReasonHtml(t)}<h3>Комментарии</h3>${t.returned_note ? `<p style="white-space:pre-wrap"><b>Возврат на доработку:</b> ${esc(t.returned_note)}</p>` : ""}${t.result ? `<p style="white-space:pre-wrap"><b>Результат:</b> ${esc(t.result)}</p>` : ""}${!t.returned_note && !t.result ? `<p class="muted">Комментариев пока нет</p>` : ""}</div><div data-task-checklist="${t.id}"></div><div data-task-settings="${t.id}"></div>`);
  const copy = $("[data-copy-task-description]");
  if (copy) copy.onclick = () => copyTaskDescription(t.note);
  loadPlannerTaskSettings(t.id);
}


function showTaskChecklist(id,detail){
  const host=$(`[data-task-checklist="${id}"]`);if(!host)return;
  const items=detail.task.subtasks||[],canEdit=detail.can_check_subtasks;
  host.innerHTML=`<section class="task-checklist"><h3>Подзадачи <span class="small muted">${items.filter(x=>x.done).length}/${items.length}</span></h3>${items.map(item=>`<div class="task-checklist-row"><label><input type="checkbox" data-subtask="${item.id}" ${item.done?'checked':''} ${canEdit?'':'disabled'}><span class="${item.done?'subtask-done':''}">${esc(item.title)}</span></label>${item.done?`<small class="muted">${esc(item.done_by)} · ${esc(dtWhen(item.done_at))}</small>`:''}${canEdit?`<button type="button" class="icon-btn" data-remove-subtask="${item.id}" aria-label="Удалить подзадачу">×</button>`:''}</div>`).join('')||'<p class="small muted">Подзадач пока нет</p>'}${canEdit?'<form data-subtask-add class="row"><input name="title" placeholder="Новая подзадача" maxlength="200" required><button class="btn sm" type="submit">Добавить</button></form>':''}</section>`;
  const updateProgress=d=>{showTaskChecklist(id,d);document.querySelectorAll(`[data-pc="${id}"] [data-checklist-progress]`).forEach(el=>el.textContent=`${d.task.subtasks.filter(x=>x.done).length}/${d.task.subtasks.length} подзадач`)};
  host.querySelectorAll('[data-subtask]').forEach(input=>input.onchange=async()=>{input.disabled=true;try{updateProgress(await api('PATCH',`/api/planner/tasks/${id}/subtasks/${input.dataset.subtask}`,{done:input.checked}))}catch(e){input.checked=!input.checked;input.disabled=false;fail(e)}});
  host.querySelectorAll('[data-remove-subtask]').forEach(button=>button.onclick=async()=>{button.disabled=true;try{updateProgress(await api('PATCH',`/api/planner/tasks/${id}/subtasks/${button.dataset.removeSubtask}`,{remove:true}))}catch(e){button.disabled=false;fail(e)}});
  const form=host.querySelector('[data-subtask-add]');if(form)form.onsubmit=async e=>{e.preventDefault();const button=form.querySelector('button');button.disabled=true;try{updateProgress(await api('POST',`/api/planner/tasks/${id}/subtasks`,{title:form.elements.title.value}))}catch(error){button.disabled=false;fail(error)}};
}


async function loadPlannerTaskSettings(id) {
  const host = $(`[data-task-settings="${id}"]`); if (!host) return;
  let d;
  try { d = await api("GET", `/api/planner/tasks/${id}`); }
  catch (e) { if (host.isConnected) host.textContent = e.message; return; }
  if (!host.isConnected) return;
  if (d.merch_stock) {
    host.parentElement?.querySelector('.planner-merch-stock')?.remove();
    const section=document.createElement('section');
    section.className='planner-merch-stock';
    section.innerHTML=`<h3>Еженедельный учёт мерча</h3><p class="small muted">Задача в Планере: ${esc(d.merch_stock.schedule)}. Заполните фактический остаток каждой позиции</p>${merchStockHtml(d.merch_stock)}`;
    host.before(section);
    wireMerchStock(section,d.merch_stock,()=>loadPlannerTaskSettings(id));
  }
  if (d.can_stop_repeat) {
    const controls=document.createElement('div');
    controls.className='row';controls.style.marginTop='16px';
    controls.innerHTML='<button type="button" class="btn" data-stop-task-repeat>Убрать будущие повторы</button>';
    host.before(controls);
    const button=controls.querySelector('button');
    button.onclick=()=>stopPlannerRepeat(d.repeat.id,button,()=>{closeDrawer();return render()});
  } else if(d.repeat && d.repeat.id && !d.repeat.active) {
    const note=document.createElement('p');note.className='small muted';note.textContent='Повторы остановлены';host.before(note);
  }
  showTaskChecklist(id,d);
  if(d.can_delete){
    const controls=document.createElement('div');controls.className='row';controls.style.marginTop='16px';
    controls.innerHTML='<button type="button" class="btn danger" data-delete-planner-task>Удалить задачу</button>';
    host.before(controls);const button=controls.querySelector('button');button.onclick=async()=>{
      if(!confirm('Удалить задачу? Она исчезнет из планера и не будет отмечена выполненной.'))return;
      button.disabled=true;try{await api('DELETE',`/api/tasks/${id}`);toast('Задача удалена');closeDrawer();render()}catch(error){button.disabled=false;fail(error)}
    };
  }
  if (!d.can_edit) return;
  const t = d.task, repeat = d.repeat || {}, weekdays = repeat.weekdays || [];
  host.innerHTML = `<details class="planner-task-settings" style="margin-top:24px;padding-top:12px;border-top:1px solid var(--line)">
    <summary class="small muted" style="cursor:pointer">Настройки задачи</summary>
    <form style="margin-top:14px">
      <div class="field"><label>Название</label><input name="title" maxlength="200" value="${esc(t.title)}" required></div>
      <div class="field"><label>Описание</label><textarea name="note" rows="3" maxlength="1000">${esc(t.note || "")}</textarea></div>
      ${t.recur_id ? `<div class="field"><label>Применить изменения</label><select name="scope"><option value="one">Только эта задача</option><option value="future">Эта и будущие задачи</option></select></div>` : `<input name="scope" type="hidden" value="one">`}
      <div class="row" style="flex-wrap:wrap"><div class="field"><label>Дата</label><input name="date" type="date" value="${esc(t.due_at.slice(0,10))}" required></div>
        <div class="field"><label>Время</label><input name="time" type="time" value="${t.due_at.slice(11,16) === "23:59" ? "" : esc(t.due_at.slice(11,16))}"></div></div>
      <div class="field"><label>Исполнитель</label><select name="assignee_id" ${d.can_assign ? "" : "disabled"}>${d.staff.map(u => `<option value="${u.id}" ${u.id === t.assignee_id ? "selected" : ""}>${esc(u.name)}</option>`).join("")}</select></div>
      ${t.assignee_id===d.staff.find(x=>x.role==='offline_pool')?.id?`<div class="field"><label>Смена</label><select name="shift_mode">${planShiftOptions(t.shift||'common')}</select><small class="muted">Для двух смен у повтора выберите «Эта и будущие задачи». В другие дни задача остаётся общей</small></div>`:''}
      <div class="field"><label>Повтор</label><select name="rule"><option value="">Не повторять</option>${Object.entries({daily:"Каждый день",weekly:"Каждую неделю",monthly:"Каждый месяц",yearly:"Каждый год"}).map(([k,v]) => `<option value="${k}" ${repeat.rule === k ? "selected" : ""}>${v}</option>`).join("")}</select></div>
      <div class="chips" data-edit-weekdays>${WD_SHORT.map((label,i) => `<label class="check"><input type="checkbox" data-edit-weekday="${i}" ${weekdays.includes(i) ? "checked" : ""}>${label}</label>`).join("")}</div>
      ${t.recur_id ? `<p class="small muted" data-repeat-hint>Чтобы изменить повтор, выберите «Эта и будущие задачи»</p>` : ""}
      <div class="field"><label>Список подзадач (каждая с новой строки)</label><textarea name="subtasks" rows="4">${esc((t.subtasks||[]).map(x=>x.title).join('\n'))}</textarea><small class="muted">Для изменения списка в следующих повторах выберите «Эта и будущие задачи». Отметки сохранятся у неизменённых пунктов этой задачи</small></div>
      <label class="check"><input name="priority" type="checkbox" ${t.priority ? "checked" : ""}>Важно</label>
      <div class="error small" data-edit-error style="margin-top:8px"></div>
      <button type="submit" class="btn primary" style="margin-top:12px">Сохранить изменения</button>
    </form></details>`;
  const form = host.querySelector("form"), rule = form.elements.rule;
  const sync = () => {
    const locked = !!t.recur_id && form.elements.scope.value === "one";
    rule.disabled = locked;
    host.querySelector("[data-edit-weekdays]").hidden = rule.value !== "weekly";
    host.querySelectorAll("[data-edit-weekday]").forEach(x => x.disabled = locked);
    const hint = host.querySelector("[data-repeat-hint]"); if (hint) hint.hidden = !locked;
  };
  form.elements.scope.onchange = () => {
    if (form.elements.scope.value === "one") {
      rule.value = repeat.rule || "";
      if(form.elements.shift_mode)form.elements.shift_mode.value=t.shift||'common';
      host.querySelectorAll("[data-edit-weekday]").forEach(x => x.checked = weekdays.includes(+x.dataset.editWeekday));
    }
    if(form.elements.shift_mode&&form.elements.scope.value==='future')form.elements.shift_mode.value=repeat.shift_mode||t.shift||'common';
    sync();
  };
  rule.onchange = () => {
    if (rule.value === "weekly" && !host.querySelector("[data-edit-weekday]:checked")) {
      const day = (new Date(form.elements.date.value + "T12:00:00").getDay() + 6) % 7;
      const box = host.querySelector(`[data-edit-weekday="${day}"]`); if (box) box.checked = true;
    }
    sync();
  };
  sync();
  form.onsubmit = async e => {
    e.preventDefault(); const button = form.querySelector("button[type=submit]"); button.disabled = true;
    const body = {title:form.elements.title.value,note:form.elements.note.value,date:form.elements.date.value,time:form.elements.time.value,
      subtasks:form.elements.subtasks.value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean),shift_mode:form.elements.shift_mode?.value||'common',assignee_id:+form.elements.assignee_id.value,priority:form.elements.priority.checked,scope:form.elements.scope.value,
      repeat:{rule:rule.value,weekdays:[...host.querySelectorAll("[data-edit-weekday]:checked")].map(x => +x.dataset.editWeekday)}};
    try {
      await api("PATCH", `/api/planner/tasks/${id}`, body);
      toast("Задача сохранена"); await render();
      const fresh = await api("GET", `/api/planner/tasks/${id}`); showPlannerTask(fresh.task);
    } catch (err) { if (host.isConnected) host.querySelector("[data-edit-error]").textContent = err.message; }
    finally { button.disabled = false; }
  };
}


async function copyTaskDescription(text) {
  try {
    try { await navigator.clipboard.writeText(text); }
    catch (_) {
      const previous = document.activeElement;
      const input = document.createElement("textarea");
      input.value = text; input.readOnly = true;
      input.style.cssText = "position:fixed;left:0;top:0;width:1px;height:1px;opacity:0";
      document.body.appendChild(input);
      let copied;
      try { input.focus({preventScroll:true}); input.select(); input.setSelectionRange(0,input.value.length); copied = document.execCommand("copy"); }
      finally { input.remove(); if (previous) previous.focus({preventScroll:true}); }
      if (!copied) { prompt("Скопируйте описание задачи", text); return; }
    }
    toast("Описание скопировано");
  } catch (err) { fail(err); }
}
