/* «📋 Задачи» на рабочем столе, как в ERP (Юлия, 03.11): общий список для всех администраторов,
   колонки по статусам ERP, отдельно Просрочено / Сегодня / Завтра. Новые виды задач CRM в колонке «Другое»,
   ручные в «Ручных». «Выполнено» ставит следующую задачу по цепочке */
S.etDay = S.etDay || "today";

async function erpTasksSection(asUser=null, layout="grid") {
  let d;
  try { d = await api("GET", "/api/tasks/erp-board?day=" + S.etDay + (asUser ? "&as_user=" + encodeURIComponent(asUser.id) : "")); } catch (_) { return ""; }
  S.taskById = S.taskById || {};
  d.columns.forEach((c) => c.cards.forEach((t) => (S.taskById[t.id] = t)));
  const exactDay = /^\d{4}-\d{2}-\d{2}$/.test(S.etDay);
  const tabs = [["overdue", "Просрочено"], ["today", "Сегодня"], ["tomorrow", "Завтра"], ["future", "Будущие"]];
  const when = (t) => {
    if(t.client_task||t.student_id||t.student?.id||t.family_id)return `📅 ${dShort(t.due_at)}`;
    const hm = (t.due_at || "").slice(11, 16);
    const tm = hm === "23:59" ? "в течение дня" : hm;
    return d.day === "overdue" ? `⏰ было на ${dShort(t.due_at)}${tm ? ", " + tm : ""}` : `⏰ ${tm}`;
  };
  const card = (t, col, colTitle) => {
    const s = t.student || {};
    return `<article class="et-card ${d.day === "overdue" ? "late" : ""}" data-et="${t.id}" ${s.id ? `data-etopen="${s.id}" role="button" tabindex="0" aria-label="Открыть карточку ${esc(displayChildName(s.name))}"` : ""}>
      <div class="et-card-top"><span class="et-kind">${esc(colTitle)}</span>${t.priority ? '<span class="et-priority" title="Приоритет">🔥</span>' : ""}</div>
      <div class="et-what">${t.icon || "✅"} ${esc(t.title)}</div>
      <div class="et-card-context">${s.name ? `<div class="et-name">${esc(displayChildName(s.name))}</div>` : ""}
        <div class="et-meta ${d.day === "overdue" ? "et-late" : ""}">${when(t)}</div></div>
      <div class="et-act">
        <button class="btn sm primary" data-etdone="${t.id}">✓ Готово</button>
        <button class="btn sm" data-etmove="${t.id}" title="Перенести, передать, поправить">Перенести</button></div>
      <button type="button" class="et-details-toggle" data-etdetails="${t.id}" aria-expanded="false">Подробности</button>
    </article>`;
  };
  const empty = exactDay ? `На ${dShort(S.etDay)} задач нет` : { overdue: "Просроченных задач нет 👍", today: "На сегодня задач нет", tomorrow: "На завтра задач пока нет", future: "Будущих задач пока нет" }[d.day];
  const funnel = layout === "funnel";
  return `<section class="et-box ${funnel ? "et-funnel-layout" : "et-grid-layout"}" id="dw-erptasks">
    <div class="now-head"><h3>📋 Задачи</h3>
      <div class="seg et-tabs">${tabs.map(([k, l]) => `<button data-etday="${k}" class="${d.day === k ? "active" : ""} ${k === "overdue" && d.counts.overdue ? "et-red" : ""}">${l} <b>${d.counts[k]}</b></button>`).join("")}${exactDay?`<button class="active">${dShort(S.etDay)}</button>`:""}</div>
      <button class="btn sm" id="et-add" style="margin-left:auto">+ Ручная задача</button></div>
    <div id="et-addbox"></div>
    ${d.columns.length ? funnel ? `<div class="et-board-navigation" aria-label="Прокрутка воронки задач"><button type="button" class="btn sm" data-etleft aria-label="Предыдущие статусы">← Влево</button><span class="small muted" data-etrange></span><button type="button" class="btn sm" data-etright aria-label="Следующие статусы">Вправо →</button></div>
      <div class="et-board" tabindex="0" role="region" aria-label="Воронка задач по статусам за выбранный день">${d.columns.map((c) => `<section class="et-col ${c.key === "other" ? "et-other" : ""} ${c.key === "unpaid_visit" ? "et-unpaid" : ""}">
        <div class="et-head"><span>${esc(c.title)}</span><span class="cnt">${c.cards.length}</span></div>
        <div class="et-cards">${c.cards.map((t) => card(t, c.key, c.title)).join("")}</div></section>`).join("")}</div>`
      : `<div class="et-summary">${d.columns.map((c) => `<span class="et-summary-kind ${c.key === "unpaid_visit" ? "danger" : ""}">${esc(c.title)} <b>${c.cards.length}</b></span>`).join("")}</div>
      <div class="et-task-grid" role="list" aria-label="Задачи на выбранный день">${d.columns.flatMap((c) => c.cards.map((t) => card(t, c.key, c.title))).join("")}</div>`
      : `<div class="muted small" style="padding:6px 2px">${empty}</div>`}
  </section>`;
}

function wireErpTasks(page) {
  const box = page.querySelector("#dw-erptasks");
  document.querySelector("#et-details-popover")?.remove();
  if (!box) return;
  wireErpTaskDetails(box);
  const board=box.querySelector(".et-board");
  if(board){const left=box.querySelector("[data-etleft]"),right=box.querySelector("[data-etright]"),range=box.querySelector("[data-etrange]");
    const sync=()=>{const max=board.scrollWidth-board.clientWidth;left.disabled=board.scrollLeft<2;right.disabled=board.scrollLeft>=max-2;const r=board.getBoundingClientRect(),cols=[...board.children],visible=cols.map((el,i)=>({i,r:el.getBoundingClientRect()})).filter(x=>x.r.right>r.left+5&&x.r.left<r.right-5);range.textContent=visible.length?`${visible[0].i+1}–${visible.at(-1).i+1} из ${cols.length} колонок`:"";box.querySelector(".et-board-navigation").hidden=max<2};
    left.onclick=()=>board.scrollBy({left:-Math.max(224,board.clientWidth*.8),behavior:"smooth"});right.onclick=()=>board.scrollBy({left:Math.max(224,board.clientWidth*.8),behavior:"smooth"});board.addEventListener("scroll",sync,{passive:true});const ro=new ResizeObserver(sync);ro.observe(board);const mo=new MutationObserver(()=>{if(!board.isConnected){ro.disconnect();mo.disconnect()}});mo.observe(document.body,{childList:true,subtree:true});requestAnimationFrame(sync);
  }
  box.querySelectorAll("[data-etday]").forEach((b) => (b.onclick = () => { S.etDay = b.dataset.etday; render(); }));
  const add = box.querySelector("#et-add");
  if (add) add.onclick = () => openClientTaskForm(box.querySelector("#et-addbox"), render);
  box.querySelectorAll("[data-etopen]").forEach((b) => (b.onclick = (e) => { if (!e.target.closest("button")) openStudent(+b.dataset.etopen); }));
  box.querySelectorAll("[data-etopen]").forEach(b=>b.addEventListener("keydown",e=>{if(e.target===b&&(e.key==="Enter"||e.key===" ")){e.preventDefault();openStudent(+b.dataset.etopen)}}));
  box.querySelectorAll("[data-etmove]").forEach((b) => (b.onclick = () => openTaskEdit(S.taskById[b.dataset.etmove], render)));
  box.querySelectorAll("[data-etdone]").forEach((b) => (b.onclick = async () => {
    const t = S.taskById[b.dataset.etdone];
    if (t && t.kind === "rebook" && typeof openRebook === "function") return openRebook(t.id);
    if (t && t.kind === "reinvite_pu") return reinviteDone(t);
    if (t && ["single_remind", "single_next", "single_paid_next"].includes(t.kind)) return singleTaskDialog(t, render);
    if(t.erp_outcomes?.length)return erpOutcomeDialog(t,render);
    const result = window.prompt("Итог (необязательно): дозвонилась, подтвердили…", "");
    if (result === null) return;
    try { await api("POST", `/api/tasks/${t.id}/done`, { result }); toast("Задача выполнена"); render(); } catch (e) { fail(e); }
  }));
}

function erpOutcomeDialog(t,done){
  openDrawer(`<h2>${esc(t.title||"Задача")}</h2><div class="small muted">Выберите результат — CRM поставит следующую задачу точно по цепочке ERP</div>
    <div class="section" id="erp-outcomes" style="display:grid;gap:8px">${t.erp_outcomes.map((x,i)=>`<button class="btn ${x==="Закрыть задачу"?"":"primary"}" data-out="${i}">${esc(x)}</button>`).join("")}</div>`);
  document.querySelectorAll("#erp-outcomes [data-out]").forEach(b=>b.onclick=async()=>{b.disabled=true;try{await api("POST",`/api/tasks/${t.id}/done`,{result:t.erp_outcomes[+b.dataset.out]});closeDrawer();toast("Задача выполнена");done()}catch(e){b.disabled=false;fail(e)}});
}

/* «Пригласить на ПУ повторно» выполнено: что получилось */
function reinviteDone(t) {
  const sid = t.student && t.student.id;
  openDrawer(`<h2>🔄 Пригласили на пробное повторно</h2>
    <div class="small muted">${esc((t.student && t.student.name) || "")}. Чем закончилось?</div>
    <div class="section" style="display:grid;gap:8px">
      <button class="btn primary" id="rd-book">📅 Записали на пробное</button>
      <button class="btn" id="rd-later">🔄 Пока нет, пригласить ещё раз позже</button>
      <button class="btn" id="rd-ref">✖ Отказ</button></div>`);
  $("#rd-book").onclick = () => { closeDrawer(); if (sid) openStudent(sid); toast("Нажмите «Записать на пробное» в карточке"); };
  $("#rd-later").onclick = () => askReinvite(sid, render);
  $("#rd-ref").onclick = () => askRefusal(sid, render);
}


/* Подробности вынесены из карточек: окно не обрезается горизонтальными колонками. */
function wireErpTaskDetails(box) {
  const popup=document.createElement('aside');popup.id='et-details-popover';popup.className='et-details-popover task-copyable';popup.hidden=true;popup.setAttribute('role','region');popup.setAttribute('aria-label','Подробности задачи');document.body.appendChild(popup);
  let current=null,pinned=false,timer;
  const hide=()=>{clearTimeout(timer);popup.hidden=true;current?.querySelector('[data-etdetails]')?.setAttribute('aria-expanded','false');current?.removeAttribute('aria-describedby');current=null;pinned=false};
  const position=()=>{if(!current?.isConnected)return hide();const r=current.getBoundingClientRect(),w=Math.min(360,innerWidth-24);popup.style.width=w+'px';popup.style.left=Math.max(12,Math.min(innerWidth-w-12,r.right+8))+'px';const h=popup.getBoundingClientRect().height;popup.style.top=Math.max(12,Math.min(innerHeight-h-12,r.top))+'px'};
  const show=(card,pin=false)=>{
    clearTimeout(timer);if(current!==card)hide();current=card;pinned=pin||pinned;
    const t=S.taskById[card.dataset.et];if(!t)return hide();const s=t.student||{};
    popup.innerHTML=`<div class="et-details-head"><strong>${esc(t.title||'Задача')}</strong><button type="button" class="icon-btn" data-etclose aria-label="Закрыть подробности">×</button></div>${s.name?`<b>${esc(displayChildName(s.name))}</b>`:''}<dl>${s.parent?`<div><dt>Родитель</dt><dd>${esc(s.parent)}</dd></div>`:''}${s.phone?`<div><dt>Телефон</dt><dd>${esc(s.phone)}</dd></div>`:''}${t.due_at?`<div><dt>Срок</dt><dd>${dShort(t.due_at)}${t.client_task||t.student_id||t.student?.id||t.family_id?'':' · '+esc(t.due_at.slice(11,16)==='23:59'?'в течение дня':t.due_at.slice(11,16))}</dd></div>`:''}</dl>${t.note?`<div class="et-details-note">${esc(t.note)}</div>`:'<p class="muted small">Дополнительного описания нет</p>'}`;
    popup.hidden=false;card.setAttribute('aria-describedby',popup.id);card.querySelector('[data-etdetails]')?.setAttribute('aria-expanded','true');popup.querySelector('[data-etclose]').onclick=hide;position();
  };
  const later=()=>{if(!pinned){clearTimeout(timer);timer=setTimeout(()=>{if(!current?.matches(':focus-within'))hide()},180)}};
  box.querySelectorAll('.et-card').forEach(card=>{
    card.addEventListener('mouseenter',()=>{if(matchMedia('(hover:hover)').matches)show(card)});card.addEventListener('mouseleave',later);
    card.addEventListener('focusin',e=>{if(e.target===card)show(card)});card.addEventListener('focusout',later);
    card.querySelector('[data-etdetails]').onclick=e=>{e.stopPropagation();if(current===card&&pinned)hide();else show(card,true)};
  });
  box.addEventListener('click',e=>{if(!e.target.closest('[data-etdetails]'))hide()},{capture:true});
  box.addEventListener('keydown',e=>{if((e.key==='Enter'||e.key===' ')&&e.target.matches('[data-etopen]'))hide()},{capture:true});
  popup.onmouseenter=()=>clearTimeout(timer);popup.onmouseleave=later;
  // Abort listeners when the board is replaced, including navigation away from dashboard.
  const controller=new AbortController(),signal=controller.signal;
  document.addEventListener('keydown',e=>{if(e.key==='Escape')hide()},{signal});
  document.addEventListener('pointerdown',e=>{if(current&&!popup.contains(e.target)&&!current.contains(e.target))hide()},{signal});
  window.addEventListener('resize',position,{signal});window.addEventListener('scroll',()=>{if(!popup.hidden)position()},{capture:true,passive:true,signal});
  const observer=new MutationObserver(()=>{if(!box.isConnected){hide();popup.remove();controller.abort();observer.disconnect()}});observer.observe(document.body,{childList:true,subtree:true});
}
