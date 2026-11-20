"use strict";
/* Абонемент Плюс — чаты и звонки. Подключается после app.js и пользуется его функциями. */

const CH = { telegram: "Telegram-бот", max: "MAX", whatsapp: "WhatsApp", wazzup_telegram: "Telegram через Wazzup", wazzup_max: "MAX", tg_archive: "Telegram (до CRM)" };
const chLabel = (c) => CH[c] || String(c || "").replace("wazzup_", "");
const CALL_ST = { ringing: "звонит", talking: "разговор", answered: "принят", missed: "пропущен", busy: "занято", failed: "не дозвонились" };
const secs = (n) => n == null ? "" : n < 60 ? `${n} сек` : `${Math.floor(n / 60)} мин ${n % 60 ? (n % 60) + " сек" : ""}`.trim();
const dtWhen = (iso) => {
  if (!iso) return "";
  const today = isoDate(new Date());
  const t = iso.slice(11, 16) === "23:59" ? "" : iso.slice(11, 16);  // задача на день без времени
  return iso.slice(0, 10) === today ? (t || "сегодня") : `${dShort(iso)}${t ? " " + t : ""}`;
};
S.inbox = { tab: "chats", filter: "all", q: "" };

function incomingStatus(counts) {
  const note=document.querySelector('[data-incoming-status]');if(!note)return;
  note.hidden=!counts.incoming_delayed&&!counts.incoming_storage_error;
  note.textContent=counts.incoming_storage_error
    ? 'Не удалось проверить приём новых сообщений. Сообщите руководителю'
    : 'Есть сохранённые сообщения, которые пока не появились в чатах. CRM повторяет обработку';
}

async function pollCounts() {
  try {
    const c = await api("GET", "/api/inbox/counts");
    incomingStatus(c);
    const n = c.unread + c.missed;
    document.querySelectorAll("[data-badge]").forEach((b) => { b.hidden = !n; b.textContent = n; });
    const p = S.counts;
    if(S.view==='inbox'&&S.inbox.tab==='chats'&&p&&(p.last_in!==c.last_in||p.unread!==c.unread))S.inbox.refresh?.().catch(()=>{});
    if (p && p.last_in !== undefined) {
      const what = c.last_lead > p.last_lead ? "🔥 Заявка с сайта" : (c.last_notify_in??c.last_in) > (p.last_notify_in??p.last_in) ? "💬 Новое сообщение" : c.last_missed > p.last_missed ? "📞 Пропущенный звонок" : "";
      if (what) newInboxAlert(what);
    }
    // пропущенный звонок не обработан: напоминаем звуком каждые 3 минуты (дальше «Служба заботы», через 30 минут руководитель)
    if (c.missed > 0 && ["admin", "operator"].includes(S.user.role)) {
      if (!S.missedSince) S.missedSince = Date.now();
      else if (Date.now() - (S.missedDing || S.missedSince) >= 180000) { S.missedDing = Date.now(); newInboxAlert("📞 Пропущенный звонок не обработан"); }
    } else { S.missedSince = 0; S.missedDing = 0; }
    S.counts = c;
    const fresh=$('#inbox-new-count');if(fresh){fresh.textContent=c.unread;fresh.dataset.empty=c.unread?'0':'1'}
  } catch (_) { /* тихо */ }
}

/* Звук и мигающий заголовок вкладки, когда приходит сообщение, пропущенный звонок или заявка. Выключается в «Мой профиль» */
const soundOn = () => { try { return localStorage.getItem("crm.sound") !== "0"; } catch (_) { return true; } };
let audioCtx = null, titleTimer = null;
function ding() {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === "suspended") audioCtx.resume();
    [[880, 0], [1320, 0.16]].forEach(([f, t]) => {
      const o = audioCtx.createOscillator(), g = audioCtx.createGain();
      o.type = "sine"; o.frequency.value = f;
      const at = audioCtx.currentTime + t;
      g.gain.setValueAtTime(0.0001, at); g.gain.exponentialRampToValueAtTime(0.35, at + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, at + 0.35);
      o.connect(g).connect(audioCtx.destination); o.start(at); o.stop(at + 0.4);
    });
  } catch (_) { /* браузер без звука */ }
}
document.addEventListener("click", () => { try { audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)(); if (audioCtx.state === "suspended") audioCtx.resume(); } catch (_) {} }, { once: true });
function newInboxAlert(what) {
  if (soundOn()) ding();
  if (document.hidden && !titleTimer) {
    const base = document.title; let on = false;
    titleTimer = setInterval(() => { document.title = (on = !on) ? what : base; }, 1000);
    const stop = () => { if (!document.hidden) { clearInterval(titleTimer); titleTimer = null; document.title = base; document.removeEventListener("visibilitychange", stop); } };
    document.addEventListener("visibilitychange", stop);
  }
  if (soundOn() && "Notification" in window && Notification.permission === "granted" && document.hidden) {
    try { new Notification("Абонемент Плюс", { body: what }); } catch (_) {}
  }
}

/* ---------- сообщение ---------- */
function chatDateLabel(day) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return "Дата не указана";
  const today = (S.meta && S.meta.today) || isoDate(new Date());
  if (day === today) return "Сегодня";
  if (day === addDays(today, -1)) return "Вчера";
  return new Intl.DateTimeFormat("ru-RU", {day:"numeric",month:"long",year:"numeric"}).format(new Date(day + "T12:00:00"));
}

function chatHistoryHtml(items) {
  let html = "", previous = null;
  for (const item of items) {
    const day = String(item.ts || item.started_at || "").slice(0,10);
    if (day !== previous) {
      if (previous !== null) html += "</section>";
      html += `<section class="chat-day-group" data-chat-day="${esc(day)}"><div class="chat-day-label"><span>${esc(chatDateLabel(day))}</span></div>`;
      previous = day;
    }
    html += item.type === "call" ? callHtml(item) : msgHtml(item);
  }
  return html + (previous !== null ? "</section>" : "");
}

function positionMessageActions(details) {
  const menu=details.querySelector('.msg-actions-menu');if(!details.open||!menu)return;
  const feed=details.closest('.chat-feed')?.getBoundingClientRect();
  const left=Math.max(8,feed?feed.left+6:8),right=Math.min(document.documentElement.clientWidth-8,feed?feed.right-6:Infinity);
  const top=Math.max(8,feed?feed.top+6:8),bottom=Math.min(window.innerHeight-8,feed?feed.bottom-6:Infinity);
  if(right<=left||bottom<=top)return;
  menu.style.transform='';menu.style.maxWidth=(right-left)+'px';menu.style.minWidth=Math.min(190,right-left)+'px';
  menu.style.maxHeight=(bottom-top)+'px';menu.style.overflowY='auto';
  const rect=menu.getBoundingClientRect(),x=Math.max(left,Math.min(rect.left,right-rect.width))-rect.left,y=Math.max(top,Math.min(rect.top,bottom-rect.height))-rect.top;
  menu.style.transform=`translate(${x}px,${y}px)`;
}
document.addEventListener('toggle',event=>{
  const details=event.target;if(details instanceof HTMLDetailsElement&&details.matches('.msg-actions')&&details.open)positionMessageActions(details);
},true);

document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-copy-message]');if(!button||button.disabled)return;
  event.preventDefault();
  const body=button.closest('.msg')?.querySelector('.chat-message-copyable');
  if(!body||!body.isConnected||!body.getClientRects().length||getComputedStyle(body).visibility!=='visible')return;
  const raw=body.textContent;if(!raw.trim())return;
  const menu=button.closest('.msg-actions'),summary=menu?.querySelector('summary'),wasFocused=document.activeElement===button;
  let copied=false;button.disabled=true;
  try{
    copied=await copyTextWithNotice(raw,'Текст скопирован')===true;
    if(copied&&menu?.isConnected){
      menu.open=false;summary?.focus({preventScroll:true});
    }
  }catch(error){fail(error)}finally{
    button.disabled=false;
    if(!copied&&wasFocused&&button.isConnected&&menu?.open&&document.activeElement===document.body)button.focus({preventScroll:true});
  }
});

document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-delete-message]');if(!button||button.disabled)return;
  if(!confirm('Удалить это сообщение у клиента? Отменить удаление нельзя'))return;
  const card=button.closest('#comms-box'),chat=button.closest('[data-chat]');
  button.disabled=true;
  try{
    const result=await api('DELETE',`/api/messages/${button.dataset.deleteMessage}`);
    if(result.ok)toast(result.state==='deleted'?'Сообщение удалено':'Запрос на удаление отправлен');else fail(Error(result.error||'Не удалось подтвердить удаление'));
    if(chat?.isConnected)await openChat(+chat.dataset.chat,true);
    else if(card?.isConnected&&card._reloadComms)await card._reloadComms();
  }catch(error){button.disabled=false;fail(error)}
});

function msgHtml(m) {
  if(['requested','deleted'].includes(m.deletion_status))return `<div class="msg ${m.direction} msg-deleted"><div class="msg-text muted">${m.deletion_status==='deleted'?'Сообщение удалено':'Удаление запрошено — ждём подтверждения'}</div><div class="msg-meta">${dtWhen(m.ts)}</div></div>`;
  const copyable=typeof m.text==='string'&&m.text.trim().length>0;
  const mayDelete=m.direction==='out'&&['admin','operator'].includes(S.user?.role);
  const uncertain=mayDelete&&m.deletion_status==='uncertain';
  const copyAction=copyable?'<button type="button" class="msg-copy" data-copy-message>Скопировать текст</button>':'';
  const deleteAction=mayDelete&&!uncertain?`<button type="button" class="msg-delete" data-delete-message="${m.id}" ${m.can_delete?'':'disabled'} title="${esc(m.can_delete?'Удалить у клиента':m.delete_reason||'Удаление недоступно')}">Удалить сообщение</button>`:'';
  const actions=copyAction||deleteAction?`<details class="msg-actions"><summary aria-label="Действия с сообщением" title="Действия с сообщением">⋯</summary><div class="msg-actions-menu">${copyAction}${deleteAction}</div></details>`:'';
  const deletionHint=uncertain?'<div class="small muted">Удаление не подтверждено — проверьте сообщение в мессенджере</div>':'';
  const media = m.media ? (["image", "sticker"].includes(m.kind)
    ? `<a href="/api/messages/${m.id}/media" target="_blank" rel="noopener"><img class="msg-img${m.kind === "sticker" ? " msg-sticker" : ""}" loading="lazy" src="/api/messages/${m.id}/media" alt="${m.kind === "sticker" ? "Стикер" : "Фото"}">${m.kind === "sticker" ? `<span hidden>Стикер: открыть файл</span>` : ""}</a>`
    : m.kind === "audio" ? `<audio controls preload="none" src="/api/messages/${m.id}/media"></audio>`
    : `<a href="/api/messages/${m.id}/media" target="_blank" rel="noopener">Открыть вложение</a>`) : "";
  const label = !m.text && !media ? ({ sticker: "Стикер", contact: "Контакт", other: "Вложение" }[m.kind] || "") : "";
  const who = m.direction === "out" ? (m.user_name || "") : "";
  return `<div class="msg ${m.direction}${m.status === "error" ? " err" : ""}">
    ${actions}${deletionHint}${media}${m.text ? `<div class="msg-text${copyable?' chat-message-copyable':''}">${esc(m.text)}</div>` : ""}${label ? `<div class="msg-text muted">${label}</div>` : ""}
    <div class="msg-meta">${[esc(who), esc(m.channel_label||""), dtWhen(m.ts), m.direction==='out'?({sent:"Отправлено",delivered:"Доставлено",read:"Прочитано",error:"Ошибка отправки",demo:"тест, не отправлено"}[m.status]||""):""].filter(Boolean).join(" · ")}</div>
    ${m.status==='error'&&m.error?`<details class="msg-error-details"><summary>Подробнее об ошибке</summary><div>${esc(m.error)}</div></details>`:''}
  </div>`;
}

function callHtml(k) {
  const missed = k.direction === "in" && ["missed", "busy"].includes(k.status);
  return `<div class="call-row ${missed ? "missed" : ""}">
    <span class="call-ico">${k.direction === "in" ? "↙" : "↗"}</span>
    <div class="grow"><b>${k.direction === "in" ? "Входящий" : "Исходящий"}</b> · ${esc(CALL_ST[k.status] || k.status)}${k.talk ? " · " + secs(k.talk) : ""}
      <div class="small muted">${dtWhen(k.ts || k.started_at)}${k.operator ? " · участник команды " + esc(k.operator) : ""}</div>
      ${k.summary ? `<div class="call-sum">💬 ${esc(k.summary)}</div>` : ""}
      ${k.has_record ? `<audio controls preload="none" src="/api/calls/${k.id}/record"></audio>` : ""}
      ${aiChip(k)}
    </div></div>`;
}

function aiChip(k) {
  if (k.score != null || k.ai_status === "done") return `<button class="ai-chip" data-ai="${k.id}">Разбор звонка${k.score != null ? ` · <b class="${scoreClass(k.score)}">${k.score}/10</b>` : ""}</button>`;
  if (k.ai_status === "stt" || k.ai_status === "llm") return `<span class="ai-chip wait">Разбираем разговор…</span>`;
  return "";
}
const scoreClass = (n) => n >= 8 ? "s-good" : n >= 5 ? "s-mid" : "s-bad";
document.addEventListener("click", (e) => { const b = e.target.closest("[data-ai]"); if (b) { e.preventDefault(); openCallAi(+b.dataset.ai); } });

async function openCallAi(id) {
  let d;
  try { d = await api("GET", `/api/calls/${id}/ai`); } catch (e) { return fail(e); }
  const a = d.analysis || {}, L = d.checklist_labels;
  const mark = (v) => v === true ? `<span class="ck ok">✓</span>` : v === false ? `<span class="ck no">✕</span>` : `<span class="ck na">—</span>`;
  const bubbles = (list) => `<div class="dlg">${list.map((r) => `<div class="dlg-r ${r.who === "admin" ? "adm" : "cli"}"><span>${r.who === "admin" ? "Администратор" : "Клиент"}</span>${esc(r.text)}</div>`).join("")}</div>`;
  const hasDlg = (a.dialogue || []).length, hasScript = (a.script || []).length;
  openDrawer(`
    <div class="cr-head">
      <div class="cr-score ${d.score != null ? scoreClass(d.score) : ""}">${d.score ?? "—"}<small>/10</small></div>
      <div class="grow"><div class="cr-kicker">Разбор звонка</div><h2>${esc(a.case || "Разговор")}</h2>
        ${a.outcome || a.result ? `<div class="cr-outcome ${d.score != null ? scoreClass(d.score) : ""}">${esc((a.outcome || a.result).toUpperCase())}</div>` : ""}</div></div>
    ${d.ai_status === "error" ? `<div class="alert">Не получилось разобрать: ${esc(d.ai_error)} <button class="btn sm" id="ai-retry">Попробовать снова</button></div>` : ""}
    ${d.ai_status !== "done" && d.ai_status !== "error" ? `<div class="note">Разговор ещё разбирается. В отложенном режиме это может занять до суток</div>` : ""}
    ${a.headline || a.conclusion || a.summary ? `<div class="cr-box ${d.score != null && d.score < 7 ? "amber" : "green"}"><b>${esc(a.headline || "Главный вывод")}</b><div>${esc(a.conclusion || a.summary || "")}</div></div>` : ""}
    ${(a.points || []).length ? `<div class="section"><h3>Результат звонка</h3><ul class="cr-points">${a.points.map((p) => `<li class="${p.ok ? "ok" : "no"}">${esc(p.text)}</li>`).join("")}</ul></div>` : ""}
    ${a.focus ? `<div class="cr-box blue"><b>Ключевой фокус</b><div>${esc(a.focus)}</div></div>` : ""}
    ${!a.focus && (a.tips || []).length ? `<div class="section"><h3>Что сказать иначе</h3><ul class="ai-tips">${a.tips.map((t) => `<li>${esc(t)}</li>`).join("")}</ul></div>` : ""}
    ${hasDlg || hasScript || d.transcript ? `<div class="seg cr-tabs" style="margin:14px 0 8px">
        ${hasDlg || d.transcript ? `<button data-crt="dlg" class="active">Расшифровка</button>` : ""}${hasScript ? `<button data-crt="script" class="${hasDlg || d.transcript ? "" : "active"}">Правильный сценарий</button>` : ""}
        ${a.checklist && Object.keys(a.checklist).length ? `<button data-crt="check">Чек-лист</button>` : ""}</div>
      <div data-crp="dlg">${hasDlg ? bubbles(a.dialogue) : d.transcript ? `<pre class="transcript">${esc(d.transcript)}</pre>` : ""}</div>
      <div data-crp="script" ${hasDlg || d.transcript ? "hidden" : ""}>${hasScript ? `<div class="small muted" style="margin-bottom:6px">Как разговор мог пойти, чтобы закончиться записью</div>${bubbles(a.script)}` : ""}</div>
      <div data-crp="check" hidden>${a.checklist ? `<div class="cklist">${Object.entries(L).map(([k, t]) => `<div>${mark(a.checklist[k])}${esc(t)}</div>`).join("")}</div>
        <div class="small muted" style="margin-top:6px">— значит пункт не подходил к этому разговору</div>` : ""}</div>` : ""}
    ${(a.objections || []).length || a.next_step ? `<div class="section">
      ${(a.objections || []).length ? `<div><b>Сомнения родителя:</b> ${a.objections.map(esc).join(", ")}</div>` : ""}
      ${a.next_step ? `<div style="margin-top:6px"><b>Договорились:</b> ${esc(a.next_step)}</div>` : ""}</div>` : ""}`);
  document.querySelectorAll("[data-crt]").forEach((b) => (b.onclick = () => {
    document.querySelectorAll("[data-crt]").forEach((x) => x.classList.toggle("active", x === b));
    document.querySelectorAll("[data-crp]").forEach((x) => { x.hidden = x.dataset.crp !== b.dataset.crt; });
  }));
  if ($("#ai-retry")) $("#ai-retry").onclick = async () => { await api("POST", `/api/calls/${id}/ai/retry`, {}).catch(fail); toast("Отправим на разбор в течение минуты"); closeDrawer(); };
}

/* ---------- сводка дня по звонкам оператора ---------- */
async function openDigest(day, operator) {
  let d;
  try { d = await api("GET", `/api/calls/digest?day=${day}&operator=${encodeURIComponent(operator)}`); } catch (e) { return fail(e); }
  openDrawer(`
    <div class="cr-kicker">Разборы звонков · ${esc(d.name)}</div><h2>Сводка за ${dShort(d.day)}</h2>
    <div class="cfacts" style="margin:10px 0 14px"><div class="cfact"><b>${d.calls}</b><span>клиентских звонков</span></div>
      <div class="cfact ${d.avg >= 8 ? "green" : d.avg >= 5 ? "" : "yellow"}"><b>${d.avg ?? "—"}/10</b><span>средняя оценка</span></div>
      <div class="cfact green"><b>${d.booked}</b><span>записей и покупок</span></div></div>
    ${d.overview ? `<div class="cr-box">${esc(d.overview)}</div>` : ""}
    ${(d.funnel || []).length ? `<div class="section"><h3>Что видно по воронке</h3><ul class="cr-points">${d.funnel.map((x) => `<li class="ok">${esc(x)}</li>`).join("")}</ul></div>` : ""}
    ${d.setting ? `<div class="cr-box blue"><b>Главная установка на следующую смену</b><div>${esc(d.setting)}</div></div>` : ""}
    <div class="section"><h3>Карта звонков</h3><table class="tbl"><thead><tr><th>Время</th><th>Кейс</th><th>Оценка</th><th>Итог</th></tr></thead>
      <tbody>${d.map.map((r) => `<tr class="click" data-ai="${r.id}"><td>${esc(r.time)}</td><td>${esc(r.case)}</td><td><b class="${r.score != null ? scoreClass(r.score) : ""}">${r.score ?? "—"}/10</b></td><td>${esc(r.outcome)}</td></tr>`).join("")}</tbody></table>
      <div class="small muted" style="margin-top:6px">Нажмите на строку, чтобы открыть полный разбор звонка</div></div>
    ${(d.training || []).length ? `<div class="section"><h3>Приоритеты обучения</h3><ul class="cr-points">${d.training.map((x) => `<li class="focus">${esc(x)}</li>`).join("")}</ul></div>` : ""}
    ${d.llm_error ? `<div class="small muted">Текст сводки не собрался: ${esc(d.llm_error)}</div>` : ""}
    ${S.user.role === "admin" ? `<button class="btn sm" id="dg-rebuild" style="margin-top:10px">Пересобрать сводку</button>` : ""}`);
  if ($("#dg-rebuild")) $("#dg-rebuild").onclick = async () => { try { await api("POST", "/api/calls/digest", { day, operator }); toast("Сводка пересобрана"); openDigest(day, operator); } catch (e) { fail(e); } };
}
document.addEventListener("click", (e) => { const b = e.target.closest("[data-digest]"); if (b) { e.preventDefault(); const [day, op] = b.dataset.digest.split("|"); openDigest(day, op); } });

async function digestsSection() {
  let d;
  try { d = await api("GET", "/api/calls/digests"); } catch (_) { return ""; }
  if (!d.items.length) return "";
  return `<section class="panel" style="margin-top:14px"><h3>Сводки дня по операторам</h3>
    <div class="list" style="box-shadow:none">${d.items.map((x) => `<div class="list-row click" data-digest="${x.day}|${esc(x.operator)}">
      <div class="ai-score sm ${x.avg != null ? scoreClass(x.avg) : ""}">${x.avg ?? "—"}</div>
      <div class="grow"><b>${dShort(x.day)} · ${esc(x.name)}</b> <span class="small muted">${x.calls} звонков, записей ${x.booked}</span>
        ${x.setting ? `<div class="small ellipsis">${esc(x.setting)}</div>` : ""}</div><span class="small muted">открыть ›</span></div>`).join("")}</div></section>`;
}

/* фокус на сегодня: на рабочем столе оператора */
function callFocusBox(f) {
  if (!f) return "";
  return `<section class="now focus-box" style="margin-bottom:14px"><div class="now-head"><h3>🎯 Фокус на сегодня</h3>
      <button class="linkish small" data-digest="${f.day}|${esc(f.operator)}" style="margin-left:auto">Разбор за ${dShort(f.day)} ›</button></div>
    <ul class="cr-points">${f.focus.map((x) => `<li class="focus">${esc(x)}</li>`).join("")}</ul>
    ${f.setting ? `<div class="small" style="margin-top:6px"><b>Установка:</b> ${esc(f.setting)}</div>` : ""}
    <div class="small muted" style="margin-top:6px">Вчера: ${f.calls} звонков, средняя оценка ${f.avg ?? "—"}/10, записей ${f.booked}</div></section>`;
}

async function dial(phone) {
  if (!S.meta.comms.sipuni) { location.href = "tel:" + phone; return; }
  try { await api("POST", "/api/call", { phone }); toast("Sipuni звонит на ваш телефон, возьмите трубку"); }
  catch (e) { fail(e); }
}

/* ---------- блок в карточке ученика ---------- */
function resizeMessageInput(input) {
  if(!input)return;
  const minimum=parseFloat(getComputedStyle(input).minHeight)||160;
  input.style.height="auto";
  input.style.height=Math.max(minimum,Math.min(input.scrollHeight+2,window.innerHeight*.6))+"px";
}
function clientChannelPriority(channel) {
  return ({wazzup_telegram:0,telegram:0,wazzup_max:1,max:1,whatsapp:2})[channel] ?? 3;
}
function preferredFamilyTarget(conversations,targets,primary,status) {
  // Среди существующих переписок: Telegram → MAX → WhatsApp, затем свежесть.
  const existing=conversations.filter(c=>!['tg_archive','telegram'].includes(c.channel)&&targets.some(t=>t.v==='c'+c.id));
  existing.sort((a,b)=>clientChannelPriority(a.channel)-clientChannelPriority(b.channel)||(b.last_at||'').localeCompare(a.last_at||'')||b.id-a.id);
  if(existing.length)return 'c'+existing[0].id;
  if(!primary)return targets.length===1?targets[0].v:'';
  return ['t','m','w'].map(prefix=>targets.find(t=>t.v===prefix+primary.id)).find(Boolean)?.v||'';
}

function messengerLogo(label) {
  const value=String(label||'').toLowerCase();
  let color,shape;
  if(value.includes('telegram')) {color='#249ed9';shape='<path fill="white" d="m5 11 14-6-3 14-5-4-3 3v-5l8-6-10 5z"/>';}
  else if(value.includes('max')) {color='#5965ed';shape='<path fill="white" d="M12 4a8 8 0 0 0-7 12l-1 4 4-1A8 8 0 1 0 12 4m0 4a4 4 0 1 1-4 4 4 4 0 0 1 4-4"/>';}
  else if(value.includes('whatsapp')) {color='#22b969';shape='<path fill="none" stroke="white" stroke-width="1.5" d="M19 11.5a7 7 0 0 1-10 6.3L5 19l1.2-4A7 7 0 1 1 19 11.5Z"/><path fill="white" d="m9 7 2 3-1 1c1 2 2 3 3 3l1-1 3 2c-1 4-8 0-9-4-1-2 0-3 1-4"/>';}
  else return '';
  return `<svg class="messenger-logo" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><rect width="24" height="24" rx="7" fill="${color}"/>${shape}</svg>`;
}

function replyTargetSummary(label) {
  const parts=String(label||'').split(' · ').map(part=>part.trim()).filter(Boolean);
  const channel=parts.shift()||'';
  const recipient=parts.pop()||'Получатель';
  return `<span class="reply-target-caption">Сообщение получит</span><strong class="reply-target-recipient">${esc(recipient)}</strong><span class="reply-target-details"><span class="reply-target-channel" title="${esc(channel)}" aria-label="${esc(channel)}">${messengerLogo(channel)}</span>${parts.length?`<span class="reply-target-context">${esc(parts.join(' · '))}</span>`:''}</span>`;
}

function replyTargetHtml(id,targets,selected) {
  const current=targets.find(t=>String(t.v)===String(selected));
  targets=[...targets].sort((a,b)=>clientChannelPriority(a.channel||({t:'wazzup_telegram',m:'wazzup_max',w:'whatsapp'})[String(a.v)[0]])-clientChannelPriority(b.channel||({t:'wazzup_telegram',m:'wazzup_max',w:'whatsapp'})[String(b.v)[0]]));
  return `<div class="reply-target"><div class="reply-target-current" ${current?'':'hidden'}><div class="reply-target-summary" id="${id==='chat-to'?'chat-channel-label':'cm-channel-label'}">${replyTargetSummary(current?.t)}</div></div><div class="reply-target-choices reply-channel-shortcuts" aria-label="Выберите мессенджер">${targets.map(t=>{const channel=t.t.split(' · ')[0];return `<button type="button" class="reply-channel-shortcut" data-reply-choice="${esc(t.v)}" aria-pressed="${String(t.v)===String(selected)}" title="${esc(t.t)}" aria-label="${esc(t.t)}">${messengerLogo(channel)}</button>`}).join('')}</div><select id="${id}" aria-label="Мессенджер для ответа" hidden><option value="">Выберите мессенджер</option>${targets.map(t=>`<option value="${esc(t.v)}" ${String(t.v)===String(selected)?'selected':''}>${esc(t.t)}</option>`).join('')}</select></div>`;
}

function showReplyTargets(select) {
  const choices=select.closest('.reply-target').querySelector('.reply-target-choices');
  choices.hidden=false;
  if(!choices.hidden)choices.querySelector('button')?.focus();
}
function wireReplyTarget(select,onchange) {
  if(!select)return;
  const box=select.closest('.reply-target'),current=box.querySelector('.reply-target-current');
  const change=box.querySelector('[data-change-reply]');
  if(change)change.onclick=()=>showReplyTargets(select);
  box.querySelectorAll('[data-reply-choice]').forEach(button=>button.onclick=()=>{select.value=button.dataset.replyChoice;select.dispatchEvent(new Event('change',{bubbles:true}))});
  select.onchange=()=>{
    const option=select.selectedOptions[0];
    current.hidden=!select.value;
    current.querySelector('.reply-target-summary').innerHTML=replyTargetSummary(option?.textContent);
    select.hidden=true;
    box.querySelector('.reply-target-choices').hidden=false;
    box.querySelectorAll('[data-reply-choice]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.replyChoice===select.value)));
    change?.focus();
    onchange();
  };
}

async function loadFamilyComms(familyId, parents, studentId, kid) {
  const box = $("#comms-box");
  if (!box) return;
  const sameFamily = box.dataset.familyId === String(familyId);
  let draft = sameFamily ? $("#cm-text")?.value || "" : "";
  let d;
  try { d = await api("GET", `/api/families/${familyId}/comms`); } catch (e) { box.innerHTML = `<h3>Связь с семьёй</h3><div class="error">${esc(e.message)}</div>`; return; }
  if ($("#comms-box") !== box) return;
  if (sameFamily) draft = $("#cm-text")?.value || "";
  const attachmentState=sameFamily&&$('#cm-text')?{file:$('#cm-text')._attachmentFile,upload:$('#cm-text')._attachmentUpload,preview:$('#cm-text')._attachmentPreview?.innerHTML,uncertain:$('#cm-text')._uncertainBundle}:null;
  box.dataset.familyId = familyId;
  box._reloadComms=()=>loadFamilyComms(familyId,parents,studentId,kid);
  const st = d.status;
  const targets = d.conversations.filter((c) => !["tg_archive","telegram"].includes(c.channel)).map((c) => ({ v: "c" + c.id, channel:c.channel, t: `${chLabel(c.channel)}${c.title ? " · " + c.title : ""}` }));
  if (st.whatsapp) parents.filter((p) => p.phone && !d.conversations.some((c) => c.channel === "whatsapp" && c.phone_norm && p.phone.replace(/\D/g, "").endsWith(c.phone_norm.slice(1))))
    .forEach((p) => targets.push({ v: "w" + p.id, t: `WhatsApp · ${p.name} (новый чат)` }));
  if (st.wazzup_telegram) parents.filter((p) => p.phone && !d.conversations.some((c) => c.channel === "wazzup_telegram" && c.phone_norm && p.phone.replace(/\D/g, "").endsWith(c.phone_norm.slice(1))))
    .forEach((p) => targets.push({ v: "t" + p.id, t: `Telegram через Wazzup · ${p.name} (новый чат)` }));
  if (st.wazzup_max) parents.filter(p => p.phone && !d.conversations.some(c => c.channel === "wazzup_max" && c.phone_norm && p.phone.replace(/\D/g, "").endsWith(c.phone_norm.slice(1))))
    .forEach(p => targets.push({v:"m" + p.id,t:`MAX · ${p.name} (новый чат)`}));
  S.familySendTargets = S.familySendTargets || {};
  const primary=parents.find(p=>p.is_primary);
  const targetKey = `${S.user.id}:${familyId}:${primary?.id||0}`;
  const primaryTarget=preferredFamilyTarget(d.conversations,targets,primary,st);
  const selectedTarget = targets.some(x => x.v === S.familySendTargets[targetKey]) ? S.familySendTargets[targetKey] : primaryTarget;
  const oq = d.conversations.filter((c) => c.open_q);
  box.innerHTML = `
    <div class="family-chat-header"><h3>Переписка</h3><details class="family-contacts"><summary>Контакты</summary><div class="family-contact-list">${parents.filter(p=>p.phone).map(p=>`<button class="btn sm" data-dial="${esc(p.phone)}">📞 ${esc(p.name)}${p.relation?` · ${esc(p.relation)}`:''}<span>${esc(p.phone)}</span></button>`).join('')||'<span class="small muted">Телефон не указан</span>'}</div></details></div>
    ${oq.map((c) => `<div class="oq-on"><div class="grow"><b>❗ Открытый вопрос</b>${c.open_note ? `: ${esc(c.open_note)}` : ""}
      <div class="small muted">Вернуться к ${esc(dtShort(c.open_due))}${c.open_by ? ` · отметил(а) ${esc(c.open_by)}` : ""}</div></div>
      <button class="btn sm" data-oqchat="${c.id}">Открыть диалог</button></div>`).join("")}
    <div class="feed chat-feed family-chat-feed" id="feed">${d.items.length ? chatHistoryHtml(d.items) : `<div class="muted small">Пока ни переписки, ни звонков</div>`}</div>
    ${kid ? `<div class="family-trial-tools"><div class="to-bar"><button type="button" class="btn sm" id="to-open">Предложить дни ПУ</button><button type="button" class="btn sm" id="to-book">Записать на пробное</button></div><div id="to-panel" hidden></div></div>` : ""}
    <div data-makeup-offer></div>
    ${targets.length ? `
      <div class="composer">
        ${replyTargetHtml('cm-to',targets,selectedTarget)}
        <div class="row"><textarea id="cm-text" rows="3" placeholder="Сообщение родителю…"></textarea><button class="btn primary" id="cm-send" style="flex:0">Отправить</button></div>
      </div>` : `<div class="small muted">Написать отсюда можно, когда родитель напишет школе в Telegram или MAX${st.whatsapp || st.wazzup_telegram ? "" : ". Первыми написать можно после подключения Wazzup"}</div>`}`;
  if ($("#cm-text")) { $("#cm-text").value = draft; $("#cm-text").addEventListener("input",e=>resizeMessageInput(e.target)); resizeMessageInput($("#cm-text")); }
  wireMakeupOffer(box,kid?[kid]:[],"#cm-text");
  const toSelect = $("#cm-to"); wireReplyTarget(toSelect,()=>{S.familySendTargets[targetKey]=toSelect.value});
  const feed = $("#feed"); if (feed) feed.scrollTop = feed.scrollHeight;
  // карточка открыта: новые сообщения подтягиваем сами каждые 10 секунд (раньше были видны только после повторного открытия)
  const lastKey = (items) => items.length ? items[items.length - 1].type + items[items.length - 1].id + ":" + items.length+":"+items.map(m=>(m.deletion_status||"")+":"+(m.status||"")).join(",") : "";
  let seen = lastKey(d.items);
  clearInterval(S.commsTimer);
  S.commsTimer = setInterval(async () => {
    const f = $("#feed");
    if (!f || !document.body.contains(box)) { clearInterval(S.commsTimer); return; }
    if (document.hidden) return;
    let n; try { n = await api("GET", `/api/families/${familyId}/comms?read=0`); } catch (_) { return; }
    const k = lastKey(n.items);
    if ($("#comms-box") !== box) return;
    if (n.conversations.map(c => c.id).sort((a,b) => a-b).join(",") !== d.conversations.map(c => c.id).sort((a,b) => a-b).join(",")) return loadFamilyComms(familyId, parents, studentId, kid);
    if (k === seen || !$("#feed")) return;
    seen = k;
    if (n.conversations.length !== d.conversations.length) return loadFamilyComms(familyId, parents, studentId, kid);  // новый чат: перестроить и список «кому»
    const atEnd = f.scrollHeight - f.scrollTop - f.clientHeight < 80;
    f.innerHTML = chatHistoryHtml(n.items);
    if (atEnd) f.scrollTop = f.scrollHeight;
  }, 10000);
  box.querySelectorAll("[data-dial]").forEach((b) => (b.onclick = () => dial(b.dataset.dial)));
  box.querySelectorAll("[data-oqchat]").forEach((b) => (b.onclick = () => openChat(+b.dataset.oqchat)));
  if (kid) wireTrialOffer({ students: [kid], family_id: familyId, onBooked: kid.onBooked }, "#cm-text");
  if(kid&&box.querySelector('#to-book'))box.querySelector('#to-book').onclick=async()=>{
    try{const detail=await api('GET',`/api/students/${kid.id}`);await openTrialBookingHub(detail,box.querySelector('#to-panel'),kid.onBooked||(()=>loadFamilyComms(familyId,parents,studentId,kid)),{futureOnly:true})}catch(e){fail(e)}
  };
  const resolveFileConversation=async()=>{
    const to=$('#cm-to').value;if(!to)throw Error('Выберите мессенджер для отправки');
    if(to[0]==='c')return +to.slice(1);
    const created=await api('POST',`/api/families/${familyId}/attachment-target`,{parent_id:+to.slice(1),channel:to[0]==='m'?'max':to[0]==='t'?'telegram':'whatsapp'});
    return created.conversation_id;
  };
  const familyChannel=()=>{const to=$('#cm-to')?.value||'';return to[0]==='c'?(d.conversations.find(c=>c.id===+to.slice(1))?.channel||''):to[0]==='m'?'wazzup_max':to[0]==='t'?'wazzup_telegram':to[0]==='w'?'whatsapp':''};
  wireMessagePicker("#cm-text", async (sticker) => {
    const draft = $("#cm-text").value;
    const to = $("#cm-to").value;
    if (!to) throw new Error("Выберите мессенджер для отправки");
    if (to[0] === "c") await api("POST", `/api/conversations/${to.slice(1)}/send`, { sticker });
    else {
      const created = await api("POST", `/api/families/${familyId}/whatsapp`, {parent_id:+to.slice(1),sticker,channel:to[0] === "m" ? "max" : to[0] === "t" ? "telegram" : "whatsapp"});
      if (created.conversation_id) S.familySendTargets[targetKey] = "c" + created.conversation_id;
    }
    await loadFamilyComms(familyId, parents, studentId, kid);
    if ($("#cm-text")) $("#cm-text").value = draft;
  },{student_id:studentId,students:kid?[kid]:[],channel:familyChannel,resolveConversation:resolveFileConversation});
  restoreAttachmentDraft($('#cm-text'),attachmentState);
  const send = $("#cm-send");
  if (send) send.onclick = async () => {
    if(!confirmPendingText($("#cm-text")))return;
    if(await sendComposerAttachment($('#cm-text'),resolveFileConversation,async sentCid=>{S.familySendTargets[targetKey]='c'+sentCid;await loadFamilyComms(familyId,parents,studentId,kid)}))return;
    const text = $("#cm-text").value.trim(), to = $("#cm-to").value;
    if (!text) return;
    if (!to) return toast("Выберите мессенджер для отправки");
    send.disabled = true;
    const sentValue=$('#cm-text').value;
    try {
      if (to[0] === "c") await api("POST", `/api/conversations/${to.slice(1)}/send`, { text });
      else {
        const created = await api("POST", `/api/families/${familyId}/whatsapp`, {parent_id:+to.slice(1),text,channel:to[0] === "m" ? "max" : to[0] === "t" ? "telegram" : "whatsapp"});
        if (created.conversation_id) S.familySendTargets[targetKey] = "c" + created.conversation_id;
      }
      if(box.isConnected&&$('#comms-box')===box&&box.dataset.familyId===String(familyId)){
        clearSentComposerDraft(box.querySelector('#cm-text'),sentValue);
        await loadFamilyComms(familyId, parents, studentId, kid);
      }
    } catch (e) { fail(e); send.disabled = false; }
  };
}

function chatActionsHtml(c,active=false){
  const unread=active||!c.unread;
  return `<button type="button" role="menuitem" data-chat-action="${unread?'unread':'read'}" data-chat-id="${c.id}">${unread?'Отметить непрочитанным':'Отметить прочитанным'}</button><button type="button" role="menuitem" data-chat-action="pin" data-chat-id="${c.id}">${c.pinned?'Открепить':'Закрепить'}</button><button type="button" role="menuitem" data-chat-action="mute" data-chat-id="${c.id}">${c.muted?'Включить звук':'Без звука'}</button><button type="button" role="menuitem" data-chat-action="archive" data-chat-id="${c.id}">${c.archived?'Вернуть из архива':'В архив'}</button>`;
}
function closeChatMenu(){$('#chat-context-menu')?.remove()}
function syncChatViewport(){const v=window.visualViewport;document.documentElement.style.setProperty('--chat-viewport-height',(v?.height||innerHeight)+'px');document.documentElement.style.setProperty('--chat-viewport-top',(v?.offsetTop||0)+'px')}
window.visualViewport?.addEventListener('resize',syncChatViewport);window.visualViewport?.addEventListener('scroll',syncChatViewport);window.addEventListener('resize',syncChatViewport);

function showChatMenu(c,x,y){
  closeChatMenu();const menu=document.createElement('div');menu.id='chat-context-menu';menu.className='chat-context-menu';menu.setAttribute('role','menu');menu.setAttribute('aria-label','Действия с чатом');menu.innerHTML=chatActionsHtml(c,+$('[data-chat]')?.dataset.chat===c.id);document.body.append(menu);
  const r=menu.getBoundingClientRect();menu.style.left=Math.max(8,Math.min(x,innerWidth-r.width-8))+'px';menu.style.top=Math.max(8,Math.min(y,innerHeight-r.height-12))+'px';menu.querySelector('button')?.focus();
}
async function chatAction(c,action){
  if(action==='unread'||action==='read')await api('POST',`/api/conversations/${c.id}/${action}`,{});
  else if(action==='pin')await api('POST',`/api/conversations/${c.id}/pin`,{pinned:!c.pinned});
  else await api('POST',`/api/conversations/${c.id}/preferences`,action==='mute'?{muted:!c.muted}:{archived:!c.archived});
  if(action==='unread'||(action==='archive'&&!c.archived)){
    const active=$('[data-chat]');if(+active?.dataset.chat===c.id){
      rememberChatDraft();stopChatTimer();S.chatOpenRequest=(S.chatOpenRequest||0)+1;
      const panel=$('#inbox-chat-pane');if(panel&&panel.contains(active)){S.inbox.active=null;document.body.classList.remove('chat-fullscreen');panel.closest('.inbox-workspace').classList.remove('chat-open');panel.innerHTML='<div class="chat-placeholder">Выберите диалог слева</div>'}else closeDrawer();
    }
  }
  if(S.view==='inbox')await S.inbox.refresh?.();
  S.counts=await api('GET','/api/inbox/counts');await pollCounts();
  if(action==='unread')toast('Чат отмечен непрочитанным');
}
document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-chat-action]');if(!button)return;
  event.preventDefault();event.stopPropagation();const cid=+button.dataset.chatId,c=S.chatRows?.[cid];if(!c)return;
  button.disabled=true;try{await chatAction(c,button.dataset.chatAction);closeChatMenu();button.closest('details')?.removeAttribute('open')}catch(error){button.disabled=false;fail(error)}
});
document.addEventListener('pointerdown',event=>{if(!event.target.closest('#chat-context-menu,[data-chat-more]'))closeChatMenu()});
document.addEventListener('keydown',event=>{if(event.key==='Escape')closeChatMenu()});
window.addEventListener('resize',closeChatMenu);

/* ---------- раздел «Чаты и звонки» ---------- */
async function renderInbox(page) {
  const I = S.inbox;
  rememberChatDraft();
  document.body.classList.remove("chat-fullscreen");
  page.innerHTML = `
    <div class="page-head"><h2 class="grow">Чаты и звонки</h2>
      <div class="seg">${[["chats", "Переписки"], ["calls", "Звонки"], ["stats", "Аналитика звонков"]].map(([k, t]) => `<button data-tab="${k}" class="${I.tab === k ? "active" : ""}">${t}</button>`).join("")}</div></div>
    <div id="inbox-body"></div>`;
  page.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => { step(() => { I.tab = b.dataset.tab; }); }));
  if (I.tab === "calls") return renderCalls($("#inbox-body"));
  if (I.tab === "stats") return renderCallStats($("#inbox-body"));
  const body = $("#inbox-body");
  body.innerHTML = `
    <div class="note" data-incoming-status role="status" hidden></div>
    <div class="inbox-workspace"><aside class="inbox-list-panel">
      <div class="inbox-search"><input class="search" id="iq" placeholder="Имя, телефон или @ник Telegram" value="${esc(I.q)}"><button type="button" id="start-telegram-username" hidden>Написать в Telegram</button></div>
      <div class="inbox-filters">${[["all","Все"],["unread","Новые"],["open","Вопросы"],["nofamily","Без карточки"],["archived","Архив"]].map(([k,t])=>`<button data-f="${k}" class="${I.filter===k?'active':''}">${t}${k==='unread'?'<span id="inbox-new-count" class="filter-count" aria-label="Непрочитанные чаты">0</span>':''}</button>`).join('')}</div>
      ${connectHint()}<div class="list" id="conv-list"><div class="list-row muted">Загружаю…</div></div>
    </aside><section id="inbox-chat-pane" aria-label="Переписка"><div class="chat-placeholder">Выберите диалог слева</div></section></div>`;
  body.querySelectorAll("[data-f]").forEach((b) => (b.onclick = () => { I.filter = b.dataset.f; render(); }));
  const startTelegram=$('#start-telegram-username');
  const isHandle=()=>/^(?:@|https?:\/\/(?:t\.me|telegram\.me)\/)[A-Za-z0-9_]{5,32}\/?$/i.test(I.q.trim());
  const updateStart=()=>{startTelegram.hidden=!isHandle()};updateStart();
  startTelegram.onclick=async()=>{const username=I.q.trim();startTelegram.disabled=true;try{
    const result=await api('POST','/api/conversations/telegram-username',{username});
    I.q='';$('#iq').value='';I.filter='all';updateStart();await loadList();await openChat(result.conversation_id);
  }catch(error){fail(error)}finally{startTelegram.disabled=false}};
  let t; $("#iq").oninput = (e) => { I.q = e.target.value; updateStart();clearTimeout(t); t = setTimeout(loadList, 250); };

  let listRequest = 0;
  async function loadList() {
    const request = ++listRequest, query = I.q, filter = I.filter;
    const d = await api("GET", `/api/inbox?filter=${filter}&q=${encodeURIComponent(query)}`);
    if (request !== listRequest || I.q !== query || I.filter !== filter || !body.isConnected) return;
    const list = $("#conv-list"); if (!list) return;
    incomingStatus(d);
    S.chatRows=S.chatRows||{};d.conversations.forEach(c=>S.chatRows[c.id]=c);
    const badge=$('#inbox-new-count');if(badge){badge.textContent=d.unread;badge.dataset.empty=d.unread?'0':'1'}
    list.innerHTML = d.conversations.length ? d.conversations.map(c=>{
      const name=c.title||c.username||c.phone||'Без имени';
      const initials=name.trim().split(/\s+/).slice(0,2).map(w=>w[0]).join('').toUpperCase();
      return `<div class="list-row click conv-row ${c.unread?'unread':''} ${I.active===c.id?'selected':''}" data-conv="${c.id}" tabindex="0" role="button" aria-label="${esc(name)}${c.unread?', непрочитанных: '+c.unread:''}">
        <div class="conv-avatar" aria-hidden="true">${esc(initials)}${c.unread?`<span class="conv-unread-count" aria-label="Непрочитанных сообщений: ${c.unread}">${c.unread>99?'99+':c.unread}</span>`:''}</div>
        <div class="conv-copy"><div class="conv-title"><b>${esc(name)}</b><time>${dtWhen(c.last_at)}</time></div><div class="conv-preview"><span>${c.open_q?'Открытый вопрос · ':''}${esc(c.last_text||chLabel(c.channel))}</span>${c.pinned?'<span class="conv-pinned" title="Закреплён">●</span>':''}${c.muted?'<span class="conv-muted" title="Без звука" aria-label="Без звука"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 5 6 9H3v6h3l5 4ZM16 9l5 6m0-6-5 6"/></svg></span>':''}<button type="button" class="conv-more" data-chat-more="${c.id}" aria-label="Действия с чатом">⋯</button></div></div>
      </div>`}).join(''):'<div class="list-row muted">Переписок нет</div>';
    list.querySelectorAll('[data-chat-more]').forEach(button=>button.onclick=e=>{e.stopPropagation();const r=button.getBoundingClientRect();showChatMenu(S.chatRows[+button.dataset.chatMore],r.right,r.bottom)});
    list.querySelectorAll('[data-conv]').forEach(el=>{
      let hold=null,start=null;const clear=()=>{clearTimeout(hold);hold=null};
      el.onclick=e=>{if(e.target.closest('button'))return;if(Date.now()<(el._suppressUntil||0)){e.preventDefault();return}openChat(+el.dataset.conv)};
      el.oncontextmenu=e=>{e.preventDefault();clear();el._suppressUntil=Date.now()+900;showChatMenu(S.chatRows[+el.dataset.conv],e.clientX,e.clientY)};
      el.onpointerdown=e=>{if(e.pointerType==='mouse'||e.target.closest('button'))return;start={x:e.clientX,y:e.clientY};clear();hold=setTimeout(()=>{el._suppressUntil=Date.now()+900;showChatMenu(S.chatRows[+el.dataset.conv],start.x,start.y)},550)};
      el.onpointermove=e=>{if(start&&Math.hypot(e.clientX-start.x,e.clientY-start.y)>10)clear()};el.onpointerup=clear;el.onpointercancel=clear;
      el.onkeydown=e=>{if(e.target!==el)return;if(e.key==='ContextMenu'||(e.shiftKey&&e.key==='F10')){e.preventDefault();const r=el.getBoundingClientRect();showChatMenu(S.chatRows[+el.dataset.conv],r.x+40,r.y+30)}else if(['Enter',' '].includes(e.key)){e.preventDefault();openChat(+el.dataset.conv)}};
    });
  }
  I.refresh=loadList;
  await loadList();
  if(I.active)await openChat(I.active,true);
}

function connectHint() {
  const c = S.meta.comms;
  if (c.telegram || c.max || c.whatsapp || c.wazzup_telegram || c.wazzup_max) return "";
  return `<div class="note">Мессенджеры пока не подключены, ниже тестовые переписки. ${S.user.role === "admin" ? "Подключить: Настройки → Связь с клиентами" : "Подключает администратор в Настройках"}</div>`;
}

async function renderCalls(body) {
  const I = S.inbox;
  I.callFilter = I.callFilter || "all";
  const rows = await api("GET", "/api/calls?filter=" + I.callFilter);
  body.innerHTML = `
    <div class="row" style="max-width:720px;margin-bottom:12px;align-items:center">
      <div class="seg" style="flex:0 0 auto">${[["all", "Все"], ["missed", "Не перезвонили"]].map(([k, t]) => `<button data-cf="${k}" class="${I.callFilter === k ? "active" : ""}">${t}</button>`).join("")}</div>
      <div class="grow"></div>${rows.some((r) => !r.seen && r.status !== "answered" && r.direction === "in") ? `<button class="btn sm" id="seen-all">Все пропущенные обработаны</button>` : ""}
    </div>
    ${S.meta.comms.sipuni ? "" : `<div class="note">Sipuni пока не подключён, ниже тестовые звонки</div>`}
    <div class="list">${rows.length ? rows.map((k) => {
      const missed = k.direction === "in" && ["missed", "busy"].includes(k.status) && !k.seen;
      return `<div class="list-row ${missed ? "row-missed" : ""}">
        <span class="call-ico">${k.direction === "in" ? "↙" : "↗"}</span>
        <div class="grow"><b>${esc(k.phone)}</b> ${k.family_name ? `<span class="muted small">· семья ${esc(k.family_name)}</span>` : `<span class="pill yellow">нет карточки</span>`}
          <div class="small muted">${dtWhen(k.started_at)} · ${esc(CALL_ST[k.status] || k.status)}${k.talk ? " · " + secs(k.talk) : ""}${k.operator ? " · " + esc(k.operator) : ""}</div>
          ${k.has_record ? `<audio controls preload="none" src="/api/calls/${k.id}/record"></audio>` : ""}
          ${aiChip(k)}
          <div class="chips" style="margin-top:6px">${k.students.map((x) => `<button class="chip" data-open="${x.id}">${esc(displayChildName(x.name))}</button>`).join("")}
            ${k.family_name ? "" : `<button class="chip" data-newlead="${esc(k.phone)}">+ Создать лид</button>`}</div></div>
        <button class="btn sm" data-dial="${esc(k.phone)}">📞 ${missed ? "Перезвонить" : "Позвонить"}</button>
      </div>`;
    }).join("") : `<div class="list-row muted">Звонков нет</div>`}</div>`;
  body.querySelectorAll("[data-cf]").forEach((b) => (b.onclick = () => { I.callFilter = b.dataset.cf; render(); }));
  body.querySelectorAll("[data-dial]").forEach((b) => (b.onclick = async () => { await dial(b.dataset.dial); setTimeout(render, 1500); }));
  body.querySelectorAll("[data-open]").forEach((b) => (b.onclick = () => openStudent(+b.dataset.open)));
  body.querySelectorAll("[data-newlead]").forEach((b) => (b.onclick = () => openNewClient({ phone: b.dataset.newlead })));
  if ($("#seen-all")) $("#seen-all").onclick = async () => { await api("POST", "/api/calls/seen", {}).catch(fail); pollCounts(); render(); };
}

/* ---------- окно переписки ---------- */
let chatTimer = null;
function rememberChatDraft(){
  const input=$('#chat-text'),cid=+(input?.closest('[data-chat]')?.dataset.chat||0);
  if(!cid)return;
  S.chatDrafts=S.chatDrafts||{};
  S.chatDrafts[cid]={text:input.value,attachment:{file:input._attachmentFile,upload:input._attachmentUpload,preview:input._attachmentPreview?.innerHTML,uncertain:input._uncertainBundle}};
}
async function openChat(cid, keepScroll = false) {
  rememberChatDraft();
  const request=S.chatOpenRequest=(S.chatOpenRequest||0)+1;
  const previousFeed = $("#chat-feed");
  const previousScroll = previousFeed ? previousFeed.scrollTop : 0;
  const wasAtEnd = !previousFeed || previousFeed.scrollHeight - previousFeed.scrollTop - previousFeed.clientHeight < 80;
  let d;
  try { d = await api("GET", `/api/conversations/${cid}${keepScroll ? "?read=0" : "?remember=1"}`); } catch (e) { return fail(e); }
  if(request!==S.chatOpenRequest)return;
  const c = d.conversation;
  if(!c.family_id&&!c.phone_norm)d.history=(d.history||[]).filter(item=>item.type==='msg'&&item.conversation_id===cid);
  S.chatRows=S.chatRows||{};S.chatRows[c.id]=c;
  d.targets=(d.targets||[]).filter(x=>x.channel!=="telegram"&&(x.id===cid||x.same_contact===true)).sort((a,b)=>clientChannelPriority(a.channel)-clientChannelPriority(b.channel));
  S.chatSendTargets=S.chatSendTargets||{};
  const replyKey=`${S.user.id}:${cid}`,savedReply=S.chatSendTargets[replyKey];
  const validReply=id=>(d.targets||[]).some(x=>x.id===id);
  const replyId=validReply(savedReply)?savedReply:validReply(d.preferred_reply_id)?d.preferred_reply_id:validReply(cid)?cid:0;
  const reply=(d.targets||[]).find(x=>x.id===replyId)||{id:0,channel:'',label:'Выберите мессенджер для ответа'};
  const sameChat=+($('#chat-text')?.closest('[data-chat]')?.dataset.chat||0)===cid;
  const attachmentState=sameChat&&$('#chat-text')?{file:$('#chat-text')._attachmentFile,upload:$('#chat-text')._attachmentUpload,preview:$('#chat-text')._attachmentPreview?.innerHTML,uncertain:$('#chat-text')._uncertainBundle}:S.chatDrafts?.[cid]?.attachment;
  const draft = sameChat && $("#chat-text") ? $("#chat-text").value : S.chatDrafts?.[cid]?.text||"";
  const html = `
    <div class="chat" data-chat="${cid}">
      <header class="chat-header"><button type="button" class="chat-back btn sm" aria-label="К списку чатов">←</button><div class="grow"><h2>${esc(c.title||c.username||c.phone||'Без имени')}</h2><div class="small muted">${esc(reply.channel?chLabel(reply.channel):c.channel_label)}${c.phone?' · '+esc(c.phone):''}</div></div>${(can('members')||can('search','view'))&&c.students?.length===1?`<button class="btn sm chat-client-link" data-open="${c.students[0].id}">Открыть карточку</button>`:(can('members')||can('search','view'))&&c.students?.length>1?`<details class="chat-client-picker"><summary class="btn sm">Карточки клиентов</summary><div class="chat-client-list">${c.students.map(x=>`<button class="btn sm" data-open="${x.id}">${esc(displayChildName(x.name))}</button>`).join('')}</div></details>`:''}<details class="chat-info-menu"><summary class="btn sm" aria-label="Информация о клиенте">⋯</summary><div class="chat-info-panel"><div class="chat-quick-actions" role="menu">${chatActionsHtml(c,true)}<button type="button" data-change-chat-channel>Канал ответа</button></div>
      <div class="section">
        ${c.family_id ? `<div class="small muted">Семья ${esc(c.family_name)}</div>
          <div class="chips" style="margin-top:6px">${c.students.map((x) => `<button class="chip" data-open="${x.id}">${esc(displayChildName(x.name))}</button>`).join("")}
          <button class="chip" id="unlink">Отвязать</button></div><div id="warn-box"></div>`
        : `<div class="small">Переписка не привязана к карточке</div>
          <div class="row" style="margin-top:8px"><input id="link-q" placeholder="Найти ученика или родителя"><button class="btn sm" id="new-lead" style="flex:0">+ Новый лид</button></div>
          <div id="link-res" class="chips" style="margin-top:6px"></div>`}
      </div>
      <div id="oq-box">${c.open_q ? `<div class="oq-on"><div class="grow"><b>❗ Открытый вопрос</b>${c.open_note ? `: ${esc(c.open_note)}` : ""}
          <div class="small muted">Вернуться к ${esc(dtShort(c.open_due))}${c.open_by ? ` · отметил(а) ${esc(c.open_by)}` : ""}</div></div>
          <button class="btn sm primary" id="oq-close">✅ Вопрос решён</button></div>`
        : `<button class="btn sm" id="oq-open" title="В диалоге осталось незакрытое дело: он появится на рабочем столе у всех администраторов">❗ Открытый вопрос</button>`}</div>
      </div></details></header>
      <div class="feed chat-feed" id="chat-feed">${chatHistoryHtml(d.history || d.messages) || `<div class="muted small">Сообщений нет</div>`}</div>
      <div class="composer sticky">
        <div class="to-bar"><button class="btn sm" id="to-open">📅 Предложить ближайшие ПУ</button></div>
        <div id="to-panel" hidden></div>
        <div data-makeup-offer></div>
        ${replyTargetHtml('chat-to',d.targets.map(x=>({v:x.id,t:x.label,channel:x.channel})),replyId)}

        <div class="row"><textarea id="chat-text" rows="2" placeholder="Ответ…">${esc(draft)}</textarea><button class="btn primary" id="chat-send" aria-label="Отправить сообщение" style="flex:0">Отправить</button></div>
        ${c.channel === "telegram" && !replyId ? `<div class="small muted">Переписка бота сохранена. Для ответа выберите нужного родителя через Wazzup; если канала здесь нет, откройте карточку клиента выше.</div>` : ""}
      </div>
    </div>`;
  const panel=S.view==='inbox'?$('#inbox-chat-pane'):null;
  if(panel){hideDrawer();document.body.classList.add('chat-fullscreen');syncChatViewport();panel.innerHTML=html;S.inbox.active=cid;panel.closest('.inbox-workspace').classList.add('chat-open');document.querySelectorAll('[data-conv]').forEach(el=>el.classList.toggle('selected',+el.dataset.conv===cid));}
  else openDrawer(html);
  const back=$('.chat-back');if(back)back.onclick=()=>{rememberChatDraft();stopChatTimer();if(panel){S.inbox.active=null;document.body.classList.remove('chat-fullscreen');panel.closest('.inbox-workspace').classList.remove('chat-open');panel.innerHTML='<div class="chat-placeholder">Выберите диалог слева</div>';}else closeDrawer();S.inbox.refresh?.()};
  const feed = $("#chat-feed"); feed.scrollTop = keepScroll && !wasAtEnd ? previousScroll : feed.scrollHeight;
  const body = panel||$("#drawer-body");
  const changeChannel=body.querySelector("[data-change-chat-channel]");if(changeChannel)changeChannel.onclick=()=>{const composer=body.querySelector(".composer");composer.classList.add("channel-choice");const select=body.querySelector("#chat-to");showReplyTargets(select);changeChannel.closest("details").open=false};
  const offerButton=body.querySelector("#to-open"),info=body.querySelector(".chat-info-menu .section");
  if(offerButton&&info)info.append(offerButton);
  body.querySelectorAll("[data-open]").forEach((b) => (b.onclick = () => { stopChatTimer(); openStudent(+b.dataset.open); }));
  if (c.family_id) loadWarnBox(c.family_id, body.querySelector("#warn-box"));
  wireTrialOffer(c);
  wireMakeupOffer(body,c.students||[],"#chat-text");
  if ($("#unlink")) $("#unlink").onclick = async () => { await api("POST", `/api/conversations/${cid}/link`, { family_id: null }).catch(fail); openChat(cid); };
  if ($("#new-lead")) $("#new-lead").onclick = () => { stopChatTimer(); openNewClient({ phone: c.phone, parent_name: c.title, conversation_id: cid }); };
  if ($("#link-q")) {
    let t;
    $("#link-q").oninput = (e) => {
      clearTimeout(t);
      t = setTimeout(async () => {
        const rows = e.target.value.trim().length < 2 ? [] : await api("GET", "/api/students?q=" + encodeURIComponent(e.target.value)).catch(() => []);
        $("#link-res").innerHTML = rows.slice(0, 8).map((r) => `<button class="chip" data-link="${r.id}">${esc(displayChildName(r.name))} · ${esc(r.family)}</button>`).join("");
        $("#link-res").querySelectorAll("[data-link]").forEach((b) => (b.onclick = async () => {
          const st = await api("GET", `/api/students/${b.dataset.link}`);
          await api("POST", `/api/conversations/${cid}/link`, { family_id: st.family.id }).catch(fail);
          toast("Переписка привязана к семье " + st.family.name);
          openChat(cid);
        }));
      }, 250);
    };
  }
  wireOpenQuestion(cid, c);
  const chatTo = $("#chat-to");
  wireReplyTarget(chatTo,() => {
    const selected = (d.targets || []).find(x => x.id === +chatTo.value);
    S.chatSendTargets[replyKey]=+chatTo.value;
    chatTo.closest(".composer")?.classList.remove("channel-choice");
    const subtitle=body.querySelector(".chat-header .small");if(subtitle&&selected)subtitle.textContent=chLabel(selected.channel);
    if($("#chat-bot-hint"))$("#chat-bot-hint").hidden=selected?.channel!=="telegram";
  });
  wireMessagePicker("#chat-text", async (sticker) => {
    const to = $("#chat-to") ? +$("#chat-to").value : cid;
    if(!to)throw Error("Выберите мессенджер для ответа");
    await api("POST", `/api/conversations/${to}/send`, { sticker });
    await openChat(cid);
  },{students:c.students||[],channel:()=>{const id=$('#chat-to')?+$('#chat-to').value:cid;return (d.targets||[]).find(x=>x.id===id)?.channel||(id===cid?c.channel:'')},resolveConversation:async()=>$('#chat-to')?+$('#chat-to').value:cid});
  restoreAttachmentDraft($('#chat-text'),attachmentState);
  const editor=$('#chat-text');editor.rows=1;editor.addEventListener('input',()=>resizeMessageInput(editor));resizeMessageInput(editor);
  const sendBtn = $("#chat-send");
  sendBtn.onclick = async () => {
    if(!confirmPendingText($("#chat-text")))return;
    if(await sendComposerAttachment($('#chat-text'),async()=>$('#chat-to')?+$('#chat-to').value:cid,async()=>openChat(cid)))return;
    const text = $("#chat-text").value.trim(); if (!text) return;
    if(!$("#chat-to").value)return toast("Выберите мессенджер для ответа");
    sendBtn.disabled = true;
    const input=$('#chat-text'),sentValue=input.value;
    const to = $("#chat-to") ? +$("#chat-to").value : cid;
    try {
      await api("POST", `/api/conversations/${to}/send`, { text });
      S.chatDrafts=S.chatDrafts||{};
      if(input.isConnected){clearSentComposerDraft(input,sentValue);S.chatDrafts[cid]={text:input.value,attachment:null};if(c.open_q&&confirm('В этом диалоге открытый вопрос — он решён?'))await api('POST',`/api/conversations/${cid}/question/close`,{});openChat(cid)}
      else {if(S.chatDrafts[cid]?.text===sentValue)S.chatDrafts[cid]={text:'',attachment:null};S.inbox.refresh?.();}
    }
    catch (e) { fail(e); sendBtn.disabled = false; }
  };
  $("#chat-text").onkeydown = (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) sendBtn.click(); };
  stopChatTimer();
  chatTimer = setInterval(async () => {
    const open = $(`[data-chat="${cid}"]`);
    if (!open || (!panel&&$("#drawer").hidden)) return stopChatTimer();
    const n = await api("GET", `/api/conversations/${cid}?read=0`).catch(() => null);
    const len = (x) => (x.history || x.messages).length + ":" + JSON.stringify(((x.history || x.messages).slice(-1)[0] || {}).id)+":"+(x.history||x.messages).map(m=>(m.deletion_status||"")+":"+(m.status||"")).join(",");
    if (n && len(n) !== len(d)) { stopChatTimer(); openChat(cid, true); }
  }, 8000);
  pollCounts();
  if (panel && !keepScroll) S.inbox.refresh?.();
}
/* «Открытый вопрос»: отметка на диалоге, видна всем админам на столе, пока не нажмут «Вопрос решён» */
function wireOpenQuestion(cid, c) {
  const close = $("#oq-close");
  if (close) close.onclick = async () => { try { await api("POST", `/api/conversations/${cid}/question/close`, {}); toast("Вопрос закрыт"); openChat(cid); pollCounts(); } catch (e) { fail(e); } };
  const open = $("#oq-open");
  if (!open) return;
  open.onclick = () => {
    const h = new Date().getHours();
    const when = [["Через час", "1h"], ...(h < 17 ? [["Сегодня в 18:00", "eve"]] : []), ["Завтра в 10:00", "morn"], ["Сейчас", "now"]];
    $("#oq-box").innerHTML = `<div class="oq-form">
      <input id="oq-note" maxlength="300" placeholder="Что осталось сделать (необязательно). Например: уточнить у тренера про субботу">
      <div class="row" style="margin-top:6px;flex-wrap:wrap;gap:6px">${when.map(([t, v], i) => `<button class="btn sm ${i ? "" : "primary"}" data-oqw="${v}">${t}</button>`).join("")}
        <button class="btn sm ghost" id="oq-cancel">Отмена</button></div>
      <div class="small muted" style="margin-top:4px">Выберите, когда вернуться: диалог появится на рабочем столе у всех администраторов</div></div>`;
    $("#oq-note").focus();
    $("#oq-cancel").onclick = () => openChat(cid, true);
    $("#oq-box").querySelectorAll("[data-oqw]").forEach((b) => (b.onclick = async () => {
      try { await api("POST", `/api/conversations/${cid}/question`, { note: $("#oq-note").value, due: b.dataset.oqw }); toast("Отмечено: открытый вопрос на рабочем столе"); openChat(cid, true); }
      catch (e) { fail(e); }
    }));
  };
}
function stopChatTimer() { if (chatTimer) clearInterval(chatTimer); chatTimer = null; }

/* ---------- настройки связи (админ) ---------- */
async function renderCommsSettings(box, users) {
  let d;
  try{d=await api('GET','/api/integrations')}
  catch(error){box.innerHTML=`<h3>Связь с клиентами</h3><p class="error">${esc(error.message||'Не удалось загрузить настройки')}</p><button type="button" class="btn sm" data-retry-comms>Повторить загрузку</button>`;box.querySelector('[data-retry-comms]').onclick=()=>renderCommsSettings(box,users);return;}

  const v = d.values, inf = d.info, st = d.status;
  const ok = (on, text) => `<span class="pill ${on ? "green" : ""}">${on ? "подключено" : text || "не подключено"}</span>`;
  box.innerHTML = `
    <h3>Связь с клиентами</h3>
    <div class="small muted" style="margin-bottom:10px">Ключи хранятся на вашем сервере. Чтобы заменить ключ, вставьте новый и нажмите «Сохранить».</div>

    <div class="int"><div class="int-head"><b>Телефония Sipuni</b>${ok(st.sipuni)}</div>
      <div class="row"><div class="field"><label>Номер кабинета</label><input data-k="sipuni_user" value="${esc(v.sipuni_user)}" placeholder="«Кабинет №…» слева вверху в Sipuni"></div>
        <div class="field"><label>Ключ интеграции</label><input data-k="sipuni_secret" value="${esc(v.sipuni_secret)}"></div></div>
      <div class="field"><label>Адрес для событий (вставить в Sipuni)</label><div class="copy"><input readonly value="${esc(d.hooks.sipuni)}"><button class="btn sm" data-copy="${esc(d.hooks.sipuni)}">Копировать</button></div></div>
      <div class="small muted">Последнее событие: ${inf.sipuni_last ? dtShort(inf.sipuni_last) : "ещё не было"}. Внутренние номера участников команды указываются ниже, в списке участников команды</div>
    </div>

    <div class="int"><div class="int-head"><b>Telegram</b>${ok(st.telegram)}${inf.tg_business_enabled ? `<span class="pill green">бизнес-аккаунт</span>` : ""}</div>
      <div class="field"><label>Токен бота от @BotFather</label><input data-k="tg_token" value="${esc(v.tg_token)}"></div>
      <div class="field"><label>Мост для Telegram (если сервер в России)</label><input data-k="tg_relay" value="${esc(v.tg_relay || "")}" placeholder="https://…"></div>
      ${inf.tg_bot ? `<div class="small">Бот: ${esc(inf.tg_bot)}</div>` : ""}${inf.tg_error ? `<div class="error">${esc(inf.tg_error)}</div>` : ""}
      <button class="btn sm" data-connect="telegram">Сохранить и подключить</button>
      <button type="button" class="btn sm" data-tg-diagnostics>Проверить связь</button>
      <div class="small muted" data-tg-diagnostics-result>Проверка сохранённого подключения без отправки сообщений.</div>
    </div>

    <div class="int"><div class="int-head"><b>MAX</b>${ok(st.max)}</div>
      <div class="field"><label>Токен бота (MAX для партнёров → Чат-боты → Настройки)</label><input data-k="max_token" value="${esc(v.max_token)}"></div>
      ${inf.max_bot ? `<div class="small">Бот: ${esc(inf.max_bot)}</div>` : ""}${inf.max_error ? `<div class="error">${esc(inf.max_error)}</div>` : ""}
      <button class="btn sm" data-connect="max">Сохранить и подключить</button>
    </div>

    <div class="int"><div class="int-head"><b>WhatsApp, MAX и Telegram через Wazzup</b>${ok(st.whatsapp || st.wazzup_telegram || st.wazzup_max)}</div>
      <div class="chips small" style="margin-bottom:8px"><span>WhatsApp: ${ok(st.whatsapp)}</span><span>Telegram: ${ok(st.wazzup_telegram)}</span><span>MAX: ${ok(st.wazzup_max)}</span></div>
      <div class="small muted" style="margin-bottom:6px">Подключает рабочие аккаунты школы. Сообщения родителей в WhatsApp, Telegram и MAX приходят в «Чаты», ответ уходит из CRM. В WhatsApp, Telegram и MAX можно написать родителю первыми по номеру из карточки. Для Telegram через Wazzup отдельный Telegram-бот и мост не нужны.</div>
      <div class="field"><label>API-ключ Wazzup</label><input data-k="wazzup_key" value="${esc(v.wazzup_key)}"></div>
      ${inf.wazzup_channels.length ? `<div class="small">Каналы: ${inf.wazzup_channels.map((c) => esc(`${c.transport} ${c.name} (${c.state})`)).join(", ")}</div>` : ""}
      ${inf.wazzup_error ? `<div class="error">${esc(inf.wazzup_error)}</div>` : ""}
      <button class="btn sm" data-connect="wazzup">Сохранить и подключить</button><button type="button" class="btn sm" data-wz-diagnostics>Проверить каналы</button><div class="small muted" data-wz-diagnostics-result></div>
    </div>

    ${d.review_on ? `<div class="int"><div class="int-head"><b>Отзывы с Яндекс Карт и 2ГИС</b>${ok(!!d.review_last, "ждём первый отзыв")}</div>
      <div class="small muted" style="margin-bottom:6px">Скрипт Google в почте руководителя видит письмо «У вас новый отзыв» и ставит вам красную плашку на рабочий стол. В CRM уходит только площадка и тема письма, без текста отзыва</div>
      <details><summary class="small"><b>Как подключить (5 минут)</b></summary><ol class="small" style="padding-left:18px;line-height:1.6">
        <li>Откройте <b>script.google.com</b> под почтой, куда приходят письма об отзывах, и нажмите «Новый проект»</li>
        <li>Сотрите всё в окне и вставьте текст скрипта (кнопка «Копировать скрипт» ниже)</li>
        <li>Нажмите значок дискеты «Сохранить», затем «Выполнить» и разрешите доступ к почте</li>
        <li>Слева нажмите будильник «Триггеры» → «Добавить триггер» → функция checkReviews, «По времени», «Каждые 5 минут» → «Сохранить»</li></ol></details>
      <button class="btn sm" data-copy="${esc(d.review_script)}">Копировать скрипт</button>
      <div class="small muted" style="margin-top:6px">Последний отзыв: ${d.review_last ? dtShort(d.review_last) : "ещё не было"}</div>
    </div>` : ""}

    <div class="int"><div class="int-head"><b>Заявки с сайта</b>${ok(!!inf.lead_last, "ждём первую заявку")}</div>
      <div class="field"><label>Адрес для заявок: вставляется в форму сайта (webhook) или в скрипт Google</label><div class="copy"><input readonly value="${esc(d.hooks.lead)}"><button class="btn sm" data-copy="${esc(d.hooks.lead)}">Копировать</button></div></div>
      <div class="small muted">Последняя заявка: ${inf.lead_last ? dtShort(inf.lead_last) : "ещё не было"}</div>
      <div class="field" style="margin-top:8px"><label>Сообщение о заявке в чат «Служба заботы»</label><select data-k="lead_notify">
        <option value="0" ${v.lead_notify !== "1" ? "selected" : ""}>Шлёт Google-скрипт (как сейчас)</option>
        <option value="1" ${v.lead_notify === "1" ? "selected" : ""}>Шлёт CRM (после отключения Google-скрипта)</option></select>
        <div class="small muted">Включайте «Шлёт CRM», только когда сайт отправляет заявки прямо в CRM, бот подключён к чату, а Google-скрипт выключен. Иначе сообщения задвоятся</div></div>
    </div>

    ${aiSettingsHtml(v, inf, st)}

    <div class="int"><div class="field"><label>Приветствие бота в Telegram и MAX</label><textarea data-k="bot_greeting" rows="3">${esc(v.bot_greeting)}</textarea></div></div>
    <button class="btn primary" id="int-save">Сохранить</button>
    <details style="margin-top:12px"><summary class="small muted">Журнал входящих событий</summary><div id="hook-log" class="small"></div></details>`;
  const collect = () => Object.fromEntries([...box.querySelectorAll("[data-k]")].map((i) => [i.dataset.k, i.value]));
  $("#int-save").onclick = async () => { try { await api("PUT", "/api/integrations", collect()); S.meta = await api("GET", "/api/meta"); toast("Сохранено"); renderCommsSettings(box, users); } catch (e) { fail(e); } };
  box.querySelector('[data-tg-diagnostics]').onclick=async()=>{
    const button=box.querySelector('[data-tg-diagnostics]'),result=box.querySelector('[data-tg-diagnostics-result]');
    button.disabled=true;result.textContent='Проверяю соединение…';
    try{const report=await api('GET','/api/integrations/telegram/diagnostics');result.textContent=report.configured?report.probes.map(p=>(p.route==='relay'?'Через мост: ':'Напрямую: ')+p.detail).join(' · '):'Telegram не подключён'}
    catch(e){result.textContent=e.message}finally{if(button.isConnected)button.disabled=false}
  };
  const wzCheck=box.querySelector('[data-wz-diagnostics]');
  if(wzCheck)wzCheck.onclick=async()=>{
    const result=box.querySelector('[data-wz-diagnostics-result]');wzCheck.disabled=true;result.textContent='Проверяю каналы…';
    try{
      const report=await api('GET','/api/integrations/wazzup/diagnostics');
      const channelText=report.error||(!report.configured?'Wazzup не подключён':report.channels.map(c=>({telegram:'Telegram',tgapi:'Telegram',max:'MAX',maxapi:'MAX',whatsapp:'WhatsApp',wapi:'WhatsApp'}[c.transport]||c.transport)+' · '+c.state+(c.selected?' · выбран в CRM':' · не выбран в CRM')).join(' · ')||'Wazzup не вернул каналы');
      result.textContent=channelText+(report.missing_selected?' · Выбранный в CRM канал отсутствует в Wazzup':'');
      if(report.failures?.length){
        const details=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre');
        summary.textContent='Последние ошибки отправки';pre.style.whiteSpace='pre-wrap';
        pre.textContent=report.failures.map(f=>[f.at,f.employee,f.transport,f.kind,({phone:'По номеру телефона',chatId:'По ID переписки',username:'По никнейму'}[f.recipient_mode]||f.recipient_mode),f.uses_current_channel===false?'Канал отличается от выбранного в настройках':'',(f.codes.join(', ')||'Код не сохранён'),f.request_id?'requestId='+f.request_id:''].filter(Boolean).join(' · ')).join('\n');
        details.append(summary,pre);result.append(details);
      }
      if(report.incoming_events?.length){
        const details=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre');
        summary.textContent='Приём входящих событий';pre.style.whiteSpace='pre-wrap';
        pre.textContent=report.incoming_scope+'\n'+report.incoming_events.map(e=>[
          '№'+e.hook_id,e.at,e.failed?'Ошибка: '+e.error_type:'Обработано',
          e.payload_unreadable?'Событие сохранено неполностью':'Сообщений: '+e.message_count,
          (e.chat_types||[]).join(', ')
        ].filter(Boolean).join(' · ')).join('\n');
        details.append(summary,pre);result.append(details);
      }
      const copy=document.createElement('button');copy.type='button';copy.className='btn sm';copy.textContent='Скопировать диагностику';
      copy.onclick=()=>navigator.clipboard.writeText(JSON.stringify(report,null,2)).then(()=>toast('Диагностика скопирована'),()=>toast('Не удалось скопировать: выделите текст результата',true));result.append(copy);
    }catch(error){result.textContent=error.message}finally{if(wzCheck.isConnected)wzCheck.disabled=false}
  };

  box.querySelectorAll("[data-connect]").forEach((b) => (b.onclick = async () => {
    b.disabled = true;
    try {
      await api("PUT", "/api/integrations", collect());
      await api("POST", `/api/integrations/${b.dataset.connect}/connect`, {});
      toast("Подключено");
    } catch (e) { fail(e); }
    S.meta = await api("GET", "/api/meta"); renderCommsSettings(box, users);
  }));
  $("#ai-test").onclick = async (e) => {
    e.target.disabled = true;
    try { await api("PUT", "/api/integrations", collect()); const r = await api("POST", "/api/integrations/yandex/test", {}); toast("Яндекс отвечает: " + (r.answer || "ок")); }
    catch (err) { fail(err); }
    S.meta = await api("GET", "/api/meta"); renderCommsSettings(box, users);
  };
  box.querySelectorAll("[data-copy]").forEach((b) => (b.onclick = async () => {
    try { await navigator.clipboard.writeText(b.dataset.copy); toast("Скопировано"); } catch (_) { b.previousElementSibling.select(); }
  }));
  box.querySelector("#hook-log").closest("details").ontoggle = async (e) => {
    if (!e.target.open) return;
    const rows = await api("GET", "/api/integrations/log").catch(() => []);
    $("#hook-log").innerHTML = rows.length ? rows.map((r) => `<div class="log-row"><b>${dtShort(r.ts)} ${esc(r.source)}</b>${r.error ? ` <span class="error">${esc(r.error)}</span>` : ""}<pre>${esc(r.payload)}</pre></div>`).join("") : "Пока пусто";
  };
  compactCommsSettings(box,d);
}

function compactCommsSettings(box,d) {
  box.classList.add('comms-settings');
  box.querySelector(':scope > .small.muted')?.remove();
  const blocks=[...box.querySelectorAll(':scope > .int')];
  const main=document.createElement('div'),extra=document.createElement('div'),extraToggle=document.createElement('button');
  main.className='connection-list';extra.className='connection-list connection-extra';extra.hidden=true;
  extraToggle.type='button';extraToggle.className='btn sm connection-extra-toggle';extraToggle.textContent='Дополнительные настройки';extraToggle.setAttribute('aria-expanded','false');
  extraToggle.onclick=()=>{extra.hidden=!extra.hidden;extraToggle.setAttribute('aria-expanded',String(!extra.hidden))};
  const footer=document.createElement('div');footer.className='connection-editor-save';footer.hidden=true;footer.append(box.querySelector('#int-save'));
  const panels=[];
  function card(block,title,summary,target=main){
    const header=block.querySelector(':scope > .int-head');if(header)header.remove();
    const section=document.createElement('section'),top=document.createElement('div'),copy=document.createElement('div'),heading=document.createElement('h4'),button=document.createElement('button');
    section.className='connection-card';top.className='connection-card-top';heading.textContent=title;copy.append(heading);button.type='button';button.className='btn sm';button.textContent='Настроить';button.setAttribute('aria-expanded','false');
    if(summary){const label=document.createElement('div');label.className='small muted';label.textContent=summary;copy.append(label)}
    top.append(copy,button);section.append(top);block.hidden=true;block.classList.add('connection-editor');section.append(block);target.append(section);panels.push({block,button});
    button.onclick=()=>{const opening=block.hidden;for(const p of panels){p.block.hidden=true;p.button.setAttribute('aria-expanded','false');p.button.textContent='Настроить'}footer.hidden=!opening;if(opening){block.hidden=false;button.setAttribute('aria-expanded','true');button.textContent='Закрыть';block.append(footer)}else box.append(footer)};
    return section;
  }
  const wz=blocks.find(b=>b.querySelector('[data-k="wazzup_key"]'));
  if(wz){
    const section=card(wz,'Мессенджеры','Переписка через Wazzup');
    const rows=document.createElement('div');rows.className='connection-channel-list';
    rows.innerHTML=[['Telegram',d.status.wazzup_telegram],['WhatsApp',d.status.whatsapp],['MAX',d.status.wazzup_max]].map(([name,on])=>`<div class="connection-channel"><span>${name}</span><span class="connection-state ${on?'on':''}">${on?'Подключено':'Не подключено'}</span></div>`).join('');
    section.insertBefore(rows,wz);
    const quick=document.createElement('div');quick.className='connection-card-check';const check=wz.querySelector('[data-wz-diagnostics]'),result=wz.querySelector('[data-wz-diagnostics-result]');if(check)quick.append(check);if(result)quick.append(result);section.insertBefore(quick,wz);
    wz.querySelector('.chips')?.remove();
  }
  const phone=blocks.find(b=>b.querySelector('[data-k="sipuni_user"]'));if(phone)card(phone,'Телефония',d.status.sipuni?'Подключена':'Не подключена');
  const lead=blocks.find(b=>b.querySelector('[data-k="lead_notify"]'));if(lead)card(lead,'Заявки с сайта',d.info.lead_last?'Последняя заявка: '+dtShort(d.info.lead_last):'Ожидание новой заявки');
  for(const block of blocks){
    if([wz,phone,lead].includes(block))continue;
    const title=block.querySelector('.int-head b')?.textContent||'Приветствие служебных ботов';
    const label=block.querySelector('[data-k="tg_token"]')?'Отдельный Telegram-бот':block.querySelector('[data-k="max_token"]')?'Отдельный MAX-бот':title;
    card(block,label,'',extra);
  }
  const log=box.querySelector('#hook-log')?.closest('details');if(log)extra.append(log);
  box.append(main,extraToggle,extra,footer);
  for(const input of box.querySelectorAll('[data-k]'))if(['sipuni_secret','tg_token','max_token','wazzup_key','yc_api_key'].includes(input.dataset.k)){input.type='password';input.autocomplete='off';}
}

/* ---------- аналитика звонков ---------- */
async function renderCallStats(body) {
  const I = S.inbox;
  I.period = I.period || "week";
  const d = await api("GET", "/api/calls/analytics?period=" + I.period);
  const sm = d.summary, dl = d.delta;
  const trend = (v, goodDown) => v == null || v === 0 ? "" : `<span class="trend ${(v < 0) === !!goodDown ? "good" : "bad"}">${v > 0 ? "▲" : "▼"} ${Math.abs(v)}</span>`;
  const maxH = Math.max(1, ...d.missed_by_hour);
  const hours = d.missed_by_hour.map((n, h) => ({ n, h })).filter((x) => (x.h >= 8 && x.h <= 22) || x.n);
  body.innerHTML = `
    <div class="row" style="max-width:720px;margin-bottom:14px;align-items:center">
      <div class="seg" style="flex:0 0 auto">${[["today", "Сегодня"], ["week", "7 дней"], ["month", "30 дней"]].map(([k, t]) => `<button data-p="${k}" class="${I.period === k ? "active" : ""}">${t}</button>`).join("")}</div>
      <span class="muted small">Сравнение с таким же периодом до этого</span>
    </div>
    ${S.meta.comms.sipuni ? "" : `<div class="note">Sipuni пока не подключён, цифры по тестовым звонкам</div>`}
    <div class="cstats">
      <div class="cstat"><span>Входящих</span><b>${sm.incoming}</b>${trend(dl.incoming)}</div>
      <div class="cstat ${sm.missed ? "warn" : ""}"><span>Пропущено</span><b>${sm.missed}${sm.missed_pct != null ? `<small> из ${sm.incoming}</small>` : ""}</b>${trend(dl.missed, true)}</div>
      <div class="cstat"><span>Перезваниваем в среднем через</span><b>${sm.median_callback_label || "—"}</b>${trend(dl.median_callback, true)}</div>
      <div class="cstat ${sm.never_called_back ? "warn" : ""}"><span>Так и не перезвонили</span><b>${sm.never_called_back}</b></div>
      <div class="cstat"><span>Исходящих</span><b>${sm.outgoing}${sm.outgoing ? `<small> дозвонились ${sm.out_answered}</small>` : ""}</b>${trend(dl.outgoing)}</div>
      <div class="cstat"><span>Время в разговорах</span><b>${esc(sm.talk_total_label)}</b></div>
    </div>

    ${d.waiting.length ? `<section class="panel" style="margin-top:14px">
      <h3>Ждут перезвона</h3>
      <div class="small muted" style="margin-bottom:8px">Сначала те, кто ждёт дольше всех</div>
      <div class="list" style="box-shadow:none">${d.waiting.map((w) => `<div class="list-row">
        <span class="ref-card red" aria-hidden="true"></span>
        <div class="grow"><b>${esc(w.phone)}</b> ${w.family_name ? `<span class="muted small">семья ${esc(w.family_name)}</span>` : `<span class="pill yellow">нет карточки</span>`}
          <div class="small muted">ждёт ${esc(w.waiting)}</div></div>
        <button class="btn sm primary" data-dial="${esc(w.phone)}" data-sid="${w.student_id || ""}">Перезвонить</button></div>`).join("")}</div>
    </section>` : ""}

    <section class="panel" style="margin-top:14px">
      <h3>Операторы</h3>
      ${d.unassigned ? `<div class="small muted" style="margin-bottom:8px">Часть номеров без имени: впишите внутренние номера участникам команды в Настройках → Команда</div>` : ""}
      ${d.operators.length ? `<div class="table-wrap"><table class="optable">
        <thead><tr><th>Оператор</th><th>Исходящие</th><th>Дозвон</th><th>Принял входящих</th><th>Средний разговор</th><th>Поговорил с лидами</th><th>Записались на пробное</th><th>Купили</th></tr></thead>
        <tbody>${d.operators.map((o) => `<tr>
          <td><b>${esc(o.name)}</b><div class="small muted">вн. ${esc(o.number)}</div></td>
          <td>${o.out}</td><td>${o.reach != null ? `${o.out_answered} из ${o.out}` : "—"}</td><td>${o.in_answered}</td>
          <td>${o.avg_talk != null ? secs(o.avg_talk) : "—"}</td><td>${o.leads}</td>
          <td>${o.leads ? `<b>${o.trial}</b> из ${o.leads}` : "—"}</td><td>${o.leads ? `${o.bought} из ${o.leads}` : "—"}</td></tr>`).join("")}</tbody>
      </table></div>` : `<div class="muted small">За этот период звонков с операторами нет</div>`}
      <div class="small muted" style="margin-top:8px">«Записались на пробное» и «Купили» считаются по лидам, с которыми оператор поговорил, если запись или покупка случилась после разговора</div>
    </section>

    <section class="panel" style="margin-top:14px">
      <h3>Когда пропускаем звонки</h3>
      <div class="small muted" style="margin-bottom:10px">Пропущенные по часам. Высокий столбик значит, что в это время не хватает рук</div>
      <div class="hours">${hours.map((x) => `<div class="hcol" title="${x.h}:00 · пропущено ${x.n}"><i style="height:${Math.round(100 * x.n / maxH)}%" class="${x.n ? "" : "zero"}"></i><span>${x.h}</span></div>`).join("")}</div>
    </section>

    ${aiBlock(d.ai)}
    ${await digestsSection()}`;
  body.querySelectorAll("[data-p]").forEach((b) => (b.onclick = () => { I.period = b.dataset.p; render(); }));
  body.querySelectorAll("[data-aiop]").forEach((b) => (b.onclick = () => { I.aiOp = I.aiOp === b.dataset.aiop ? null : b.dataset.aiop; render(); }));
  body.querySelectorAll("[data-dial]").forEach((b) => (b.onclick = async () => { await dial(b.dataset.dial); if (b.dataset.sid) openStudent(+b.dataset.sid); }));
}

function aiBlock(x) {
  if (!x.enabled && !x.analyzed) return `<section class="panel soon" style="margin-top:14px">
      <h3>Разбор разговоров</h3>
      <div class="small">Каждый состоявшийся разговор будет превращаться в текст, а под ним появится разбор по чек-листу продаж: поздоровался ли, выяснил ли возраст и филиал, предложил ли пробное на конкретное время, как отработал сомнения, чем закончился звонок</div>
      <div class="small muted" style="margin-top:8px">${S.user.role === "admin" ? "Включается в Настройках → Связь с клиентами → Расшифровка и разбор звонков" : "Включает администратор в Настройках"}</div>
    </section>`;
  const I = S.inbox;
  const STEP_NOUN = { hello: "Приветствие", child: "Знакомство с ребёнком", needs: "Выяснение потребностей", place: "Подбор филиала и времени",
    value: "Рассказ о пользе занятий", trial: "Запись на пробное", objection: "Работа с сомнениями и возражениями", next: "Договорённость о следующем шаге" };
  const weak = x.checklist.find((c) => c.key === x.weakest);
  const op = I.aiOp ? x.operators.find((o) => o.number === I.aiOp) : null;
  const steps = op ? op.steps : x.team;
  return `
    <section class="panel" style="margin-top:14px">
      <div class="row" style="align-items:center"><h3 class="grow" style="flex:1">Разбор разговоров</h3>
        <span class="small muted" style="flex:0 0 auto">Разобрано ${x.analyzed}${x.pending ? `, ещё ${x.pending} в работе` : ""}</span></div>
      ${!x.analyzed ? `<div class="small muted">Разборов за этот период пока нет</div>`
        : weak && x.team[weak.key] < 80 ? (() => {
          const ks = (x.weakest_all && x.weakest_all.length ? x.weakest_all : [weak.key]);
          const names = ks.map((k) => `<b>«${esc(STEP_NOUN[k] || k)}»</b>`);
          const list = names.length > 1 ? names.slice(0, -1).join(", ") + " и " + names[names.length - 1] : names[0];
          return `<div class="ai-weak">Чаще всего теряем клиента на ${ks.length > 1 ? "этапах" : "этапе"} ${list}: администраторы делают ${ks.length > 1 ? "каждый из них" : "это"} только в ${x.team[weak.key]}% разговоров</div>`;
        })()
        : `<div class="ai-good" style="margin-top:8px">Все шаги разговора выполняются хорошо, так держать</div>`}
      ${x.operators.length ? `
      <div class="chips" style="margin:12px 0 8px"><span class="small muted" style="align-self:center">Показать:</span>
        <button class="chip ${!op ? "on-chip" : ""}" data-aiop="${I.aiOp || ""}">Вся команда</button>
        ${x.operators.map((o) => `<button class="chip ${op && op.number === o.number ? "on-chip" : ""}" data-aiop="${esc(o.number)}">${esc(o.name)} · ${o.avg ?? "—"}/10</button>`).join("")}</div>
      <div class="steps">${x.checklist.map((c) => { const v = steps[c.key]; return `
        <div class="step ${(x.weakest_all || [x.weakest]).includes(c.key) && !op ? "weak" : ""}"><span>${esc(c.title)}</span>
          <div class="bar"><i style="width:${v ?? 0}%" class="${v == null ? "" : v >= 80 ? "s-good" : v >= 50 ? "s-mid" : "s-bad"}"></i></div>
          <b>${v == null ? "—" : v + "%"}</b></div>`; }).join("")}</div>` : ""}
    </section>
    ${x.recent.length ? `<section class="panel" style="margin-top:14px"><h3>Последние разборы</h3>
      <div class="list" style="box-shadow:none">${x.recent.map((r) => `<div class="list-row click" data-ai="${r.id}">
        <div class="ai-score sm ${r.score != null ? scoreClass(r.score) : ""}">${r.score ?? "—"}</div>
        <div class="grow"><b>${esc(r.family_name ? "Семья " + r.family_name : r.phone)}</b> <span class="small muted">${esc(r.operator)} · ${dtWhen(r.started_at)}</span>
          <div class="small ellipsis">${esc(r.summary || "")}</div></div>
        ${r.result ? `<span class="pill">${esc(r.result)}</span>` : ""}</div>`).join("")}</div></section>` : ""}`;
}

/* ---------- настройки разбора звонков ---------- */
function aiSettingsHtml(v, inf, st) {
  return `<div class="int"><div class="int-head"><b>Расшифровка и разбор звонков (Яндекс)</b><span class="pill ${st.ai ? "green" : ""}">${st.ai ? "включено" : "выключено"}</span></div>
    <div class="row"><div class="field"><label>API-ключ сервисного аккаунта</label><input data-k="yc_api_key" value="${esc(v.yc_api_key)}"></div>
      <div class="field"><label>ID каталога</label><input data-k="yc_folder" value="${esc(v.yc_folder)}" placeholder="b1g..."></div></div>
    <div class="row">
      <div class="field"><label>Когда расшифровывать</label><select data-k="ai_mode"><option value="deferred" ${v.ai_mode === "deferred" ? "selected" : ""}>До суток, в 4 раза дешевле</option><option value="general" ${v.ai_mode === "general" ? "selected" : ""}>Сразу после звонка</option></select></div>
      <div class="field"><label>Модель для разбора</label><select data-k="ai_model"><option value="yandexgpt" ${v.ai_model === "yandexgpt" ? "selected" : ""}>YandexGPT Pro, точнее</option><option value="yandexgpt-lite" ${v.ai_model === "yandexgpt-lite" ? "selected" : ""}>YandexGPT Lite, дешевле</option></select></div>
    </div>
    <div class="row">
      <div class="field"><label>Разбирать разговоры длиннее, сек</label><input data-k="ai_min_talk" type="number" min="5" value="${esc(v.ai_min_talk)}"></div>
      <div class="field"><label>Разбор звонков</label><select data-k="ai_enabled"><option value="0" ${v.ai_enabled !== "1" ? "selected" : ""}>Выключен</option><option value="1" ${v.ai_enabled === "1" ? "selected" : ""}>Включён</option></select></div>
    </div>
    ${inf.ai_last_error ? `<div class="error" style="margin-bottom:8px">Последняя ошибка: ${esc(inf.ai_last_error)}</div>` : ""}
    <button class="btn sm" id="ai-test">Сохранить и проверить</button>
  </div>`;
}


// Не определяем пол по окончанию: Никита и Илья не должны стать «чемпионкой»
function trialAgeQuestion(kid = {}) {
  const girls = new Set('агата аглая ада аделина адель азалия айлин аксинья александра алина алиса алия алла альбина амелия амина анастасия ангелина анжелика анна аня ариана арина ася афина валентина валерия варвара варя василиса вера вероника виктория вика виолетта галина дарина дарья даша диана ева евгения евдокия екатерина елена елизавета есения жанна злата зоя инна ирина карина катя кира кристина ксения ксюша лада лариса лера лиза лилия любовь людмила майя маргарита марианна марина мария марта маша милана милена мила мия надежда наталья нелли ника нина олеся ольга оля полина регина рита роза сабина светлана серафима софия софья соня стефания таисия тамара татьяна ульяна эвелина элина элла эмилия эмма юлия юля яна ярослава'.split(' '));
  const words = String(kid.first_name || kid.name || '').toLocaleLowerCase('ru').replace(/ё/g, 'е').split(/[\s-]+/);
  const female = kid.gender === 'female' || words.some(word => girls.has(word));
  const address = female ? 'вашей чемпионке' : 'вашему чемпиону';
  return `Здравствуйте! Подскажите, пожалуйста, сколько лет ${address}? Подберём группу по возрасту и ближайшие свободные тренировки ⚽`;
}

function wireMakeupOffer(root,students,textSel) {
  const host=root.querySelector('[data-makeup-offer]');if(!host)return;
  host.innerHTML='<button type="button" class="btn sm makeup-offer-button" aria-expanded="false">Предложить отработку</button><div class="makeup-offer-panel" hidden></div>';
  const button=host.querySelector('button'),panel=host.querySelector('.makeup-offer-panel');
  let summaries=[],request=0,insertedDraft=null;
  const label=()=>{const count=summaries.reduce((sum,x)=>sum+(x.counters?.available||0),0);button.classList.toggle('has-makeups',count>0);button.textContent='Предложить отработку'+(count?' · '+count:'');button.title=count?'Есть доступные отработки':summaries.some(x=>x.error)?'Не удалось проверить остаток отработок':'Нет доступных отработок'};
  Promise.all(students.map(async kid=>{try{return {...await api('GET',`/api/students/${kid.id}/makeup-offer?summary=1`),kid}}catch(e){return {kid,error:e.message}}})).then(rows=>{if(!host.isConnected)return;summaries=rows;label()});
  button.onclick=async()=>{
    if(!students.length){toast('Сначала свяжите чат с карточкой ребёнка',true);return}
    panel.hidden=!panel.hidden;button.setAttribute('aria-expanded',String(!panel.hidden));if(panel.hidden)return;
    const preferred=summaries.find(x=>x.counters?.available>0)?.kid.id||students[0].id;
    panel.innerHTML=`${students.length>1?`<label>Ребёнок<select data-makeup-kid>${students.map(k=>`<option value="${k.id}" ${k.id===preferred?'selected':''}>${esc(k.name)}</option>`).join('')}</select></label>`:''}<div data-makeup-result></div>`;
    const load=async()=>{
      const version=++request,sid=+(panel.querySelector('select')?.value||students[0].id),result=panel.querySelector('[data-makeup-result]');result.textContent='Подбираю ближайшие занятия…';
      try{
        const data=await api('GET',`/api/students/${sid}/makeup-offer`);if(version!==request||!host.isConnected)return;
        const row=summaries.find(x=>x.kid.id===sid);if(row)row.counters=data.counters;label();
        if(!data.message){result.textContent=data.reason;return}
        result.innerHTML=`<p class="small muted">Ближайшие варианты с учётом правил и свободных мест</p><textarea rows="8" aria-label="Предложение отработки"></textarea><button type="button" class="btn sm primary" data-use-makeup>Вставить в сообщение</button><p class="small muted">Сначала согласуйте время с родителем — это предложение, запись ещё не создана</p>`;
        result.querySelector('textarea').value=data.message;
        const input=root.querySelector(textSel);
        const insert=focus=>{if(!input)return;input.value=result.querySelector('textarea').value;insertedDraft=input.value;input.dispatchEvent(new Event('input',{bubbles:true}));if(focus)input.focus()};
        if(input&&!panel.hidden&&(!input.value.trim()||input.value===insertedDraft)){insert(false);if(students.length===1){panel.hidden=true;button.setAttribute('aria-expanded','false')}toast('Предложение уже в поле ответа — проверьте перед отправкой')}
        result.querySelector('[data-use-makeup]').onclick=()=>{if(!input)return;const text=result.querySelector('textarea').value;if(input.value.trim()&&input.value!==text&&!confirm('Заменить текст в поле ответа предложением отработки?'))return;insert(true);panel.hidden=true;button.setAttribute('aria-expanded','false');toast('Предложение вставлено — проверьте и отправьте родителю')};
      }catch(error){if(version===request)result.textContent=error.message}
    };
    const select=panel.querySelector('select');if(select)select.onchange=load;
    load();
  };
}

/* «Предложить ближайшие ПУ»: подбор групп по возрасту и ближайших дат со свободными местами, текст сразу в поле ответа */
function wireTrialOffer(c, textSel = "#chat-text") {
  const root=document.querySelector(textSel)?.closest(".chat,#comms-box")||document;
  const $=selector=>root.querySelector(selector);
  const btn = $("#to-open"), panel = $("#to-panel");
  if (!btn) return;
  if ($("#to-book")) $("#to-book").onclick = () => { if (panel.hidden) btn.onclick(); };
  const leads = (c.students || []).filter((x) => !x.archived);
  const ageOf = (b) => (b ? parseInt(ageText(b)) : NaN);
  btn.onclick = () => {
    panel.hidden = !panel.hidden;
    if (panel.hidden) return;
    const kid = leads[0];
    panel.innerHTML = `<div class="to-box">
      <div class="row" style="flex-wrap:wrap;align-items:flex-end">
        ${leads.length > 1 ? `<div class="field"><label>Ребёнок</label><select id="to-kid">${leads.map((k) => `<option value="${k.id}">${esc(k.name)}</option>`).join("")}</select></div>` : ""}
        <div class="field" style="flex:0 0 110px"><label>Возраст, лет</label><input id="to-age" type="number" min="2" max="17" value="${kid && isFinite(ageOf(kid.birth)) ? ageOf(kid.birth) : ""}" placeholder="4"></div>
        <div class="field"><label>Филиал</label><select id="to-br"><option value="">Любой, где раньше</option>${S.meta.branches.map((b) => `<option value="${b.id}">${esc(b.name)}</option>`).join("")}</select></div>
        <button class="btn sm primary" id="to-go" style="flex:0">Подобрать</button>
        <button class="btn sm" id="to-ask-age" style="flex:0" title="Возраст неизвестен: спросить у родителя">💬 Спросить возраст</button></div>
      <div id="to-res"></div></div>`;
    const go = async () => {
      const age = $("#to-age").value;
      if (!age) return toast("Укажите возраст ребёнка", true);
      const kidId = $("#to-kid") ? +$("#to-kid").value : (kid ? kid.id : null);
      try {
        const r = await api("GET", `/api/trial-offer?age=${age}${$("#to-br").value ? "&branch_id=" + $("#to-br").value : ""}`);
        if (!r.slots.length) { $("#to-res").innerHTML = `<div class="small muted" style="margin-top:8px">${esc(r.error).replace(/\n/g,'<br>')}</div>`; return; }
        const ta = $(textSel);
        if (ta) ta.value = r.text;
        $("#to-res").innerHTML = `<div class="small muted" style="margin:8px 0 4px">${ta ? "Текст с вариантами уже в поле сообщения: проверьте и нажмите «Отправить». " : ""}Когда родитель выберет, запишите одним нажатием:</div>
          ${r.slots.map((x, i) => `<div class="to-slot"><span class="grow">${esc(x.label)} · ${esc(x.group)} · ${esc(x.branch)} <span class="small muted">мест ${x.free}</span></span>
            ${kidId ? `<button class="btn sm" data-to-book="${i}">Записать</button>` : ""}</div>`).join("")}`;
        $("#to-res").querySelectorAll("[data-to-book]").forEach((b) => (b.onclick = async () => {
          const x = r.slots[+b.dataset.toBook];
          // шаг 3: если данных не хватает, запрос родителю будет уже с выбранным временем
          const kidRow = leads.find((k) => k.id === kidId) || kid || {};
          S.pendingSlot = { label: x.label, group: x.group, branch: x.branch, kid: (kidRow.name || "").split(" ")[0], group_id: x.group_id, date: x.date, time: x.time, student_id: kidId };
          try {
            await api("PATCH", `/api/students/${kidId}`, { group_id: x.group_id, trial_at: `${x.date}T${x.time}` });
            S.pendingSlot = null;
            toast(`Записан на пробное: ${x.label}`); panel.hidden = true;
            if (c.onBooked) c.onBooked(); else loadWarnBox(c.family_id, $("#warn-box"));
          } catch (e) { S.pendingSlot = null; fail(e); }
        }));
      } catch (e) { fail(e); }
    };
    $("#to-go").onclick = go;
    // шаг 1: возраст неизвестен, сначала спрашиваем его
    $("#to-ask-age").onclick = () => {
      const ta = $(textSel);
      const selectedId = $("#to-kid") ? +$("#to-kid").value : kid?.id;
      const text = trialAgeQuestion(leads.find(child => child.id === selectedId) || kid || {});
      if (ta) { ta.value = text; ta.focus(); toast("Вопрос вставлен в поле ответа: нажмите «Отправить»"); }
      else navigator.clipboard.writeText(text).then(() => toast("Текст скопирован"), () => prompt("Скопируйте текст", text));
    };
    if ($("#to-age").value) go();
  };
}

function administratorGreeting(user=S.user) {
  const words=String(user?.first_name||user?.name||'').trim().split(/\s+/).filter(Boolean);
  // Варвара представляется клиентам как Варя, в том числе при фамилии первым словом.
  const varya=words.find(word=>/^(варвара|варя)$/iu.test(word));
  const name=varya?'Варя':words[0];
  if(!name||/^(администратор|оператор|руководитель)$/iu.test(name))return '';
  return `Добрый день!\n\nНа связи администратор ${name} 💚`;
}

function insertAdministratorGreeting(input,user=S.user) {
  const text=administratorGreeting(user);
  if(!text){toast('Укажите имя участника команды в настройках аккаунта');return;}
  const draft=input.value;
  if(draft!==text&&!draft.startsWith(text+'\n'))input.value=text+(draft?'\n\n'+draft:'');
  input.focus();input.setSelectionRange(input.value.length,input.value.length);
  input.dispatchEvent(new Event('input',{bubbles:true}));
}

function restoreAttachmentDraft(input,state) {
  if(!input||!state)return;
  input._uncertainBundle=state.uncertain;
  if(!state.file){if(state.uncertain)input._attachmentPreview.innerHTML=state.preview||'';return;}
  input._attachmentFile=state.file;input._attachmentUpload=state.upload;
  input._attachmentPreview.innerHTML=state.preview||'';
  const remove=input._attachmentPreview.querySelector('[data-remove-file]');
  if(remove)remove.onclick=()=>{if(!input._attachmentBusy){input._attachmentFile=null;input._attachmentUpload=null;input._attachmentPreview.replaceChildren()}};
}

function confirmPendingText(input) {
  if(!input?._uncertainBundle)return true;
  if(!confirm('Файл уже отправлен, повторится только текст. Его предыдущая отправка не подтверждена. Вы проверили чат и хотите отправить текст ещё раз?'))return false;
  input._uncertainBundle=false;input._attachmentPreview?.replaceChildren();return true;
}

async function sendComposerAttachment(input,resolveConversation,done) {
  if(!input?._attachmentFile)return false;
  if(input._attachmentBusy)return true;
  input._attachmentBusy=true;
  const button=$(input.id==='cm-text'?'#cm-send':'#chat-send'),target=$(input.id==='cm-text'?'#cm-to':'#chat-to');
  const disabled=input.disabled,targetDisabled=target?.disabled;
  input.disabled=true;if(button)button.disabled=true;if(target)target.disabled=true;
  try {
    const cid=await resolveConversation(),file=input._attachmentFile,caption=input.value;
    if(!cid)throw Error('Выберите мессенджер для отправки');
    if(input._attachmentUpload&&input._attachmentUpload.cid!==cid)throw Error('Получатель изменился. Уберите вложение и выберите файл заново');
    if(!input._attachmentUpload){
      const encoded=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(Error('Не удалось прочитать файл'));reader.readAsDataURL(file)});
      const uploaded=await api('POST',`/api/conversations/${cid}/attachments`,{filename:file.name,data:encoded});
      input._attachmentUpload={id:uploaded.id,cid};
    }
    const result=await api('POST',`/api/conversations/${cid}/attachments/${input._attachmentUpload.id}/send`,{caption});
    if(result.partial&&result.file_sent){input._attachmentFile=null;input._attachmentUpload=null;input._uncertainBundle=true;if(input._attachmentPreview)input._attachmentPreview.textContent=result.error;}
    if(!result.ok)throw Error((result.error||'Не удалось подтвердить отправку')+'. Проверьте доставку в мессенджере перед повторной попыткой');
    input._attachmentFile=null;input._attachmentUpload=null;input.value='';input._attachmentPreview?.replaceChildren();
    input.dispatchEvent(new Event('input',{bubbles:true}));
    if(input.isConnected)await done(cid);
  }catch(error){fail(error)}
  finally{input._attachmentBusy=false;input.disabled=disabled;if(button?.isConnected)button.disabled=false;if(target?.isConnected)target.disabled=targetDisabled}
  return true;
}

const CHAT_EMOJI_GROUPS = [
  ['popular','Часто нужны','💚~сердце зеленое любовь|🤍~сердце белое любовь|😊~улыбка радость|🙏~спасибо пожалуйста|👍~отлично хорошо|⚽~футбол мяч тренировка|✅~готово да галочка|❤️~сердце красное любовь|🎉~праздник поздравляем|💪~сила молодец'],
  ['faces','Смайлы и люди','😀~улыбка радость|😃~улыбка радость|😄~смех радость|😁~улыбка|😆~смех|😅~смех пот|😂~смех слезы|🤣~смех|😉~подмигивание|😊~улыбка|🥰~любовь|😍~любовь|😘~поцелуй|😋~вкусно|😎~круто|🤩~восторг|🥳~поздравляем праздник|🙂~улыбка|🤗~объятия|🤔~думаю вопрос|🤭~ой|🥺~просьба|😢~грустно слезы|😭~плач|😴~сон|😮~удивление|😬~неловко|🙃~улыбка'],
  ['hearts','Сердца и жесты','💚~сердце зеленое любовь|🤍~сердце белое любовь|❤️~сердце красное любовь|💛~сердце желтое|💙~сердце синее|💜~сердце фиолетовое|🩷~сердце розовое|🧡~сердце оранжевое|🫶~любовь сердце руки|💌~письмо любовь|👍~да отлично|👎~нет|👏~молодец аплодисменты|🙌~ура руки|🙏~спасибо пожалуйста|🤝~договорились|👋~привет пока|👌~хорошо ок|✌️~победа|💪~сила спорт'],
  ['sport','Спорт и праздники','⚽~футбол мяч|🏆~кубок победа|🥇~медаль золото чемпион|🥈~медаль серебро|🥉~медаль бронза|🏅~медаль награда|🎯~цель|🏃~бег|🏟️~стадион|🎉~праздник поздравляем|🎊~праздник|🎁~подарок|🎈~шар праздник|🎂~день рождения|🌷~цветок|🌸~цветок|☀️~солнце|✨~блеск|🔥~огонь|🚀~ракета'],
  ['symbols','Полезные символы','✅~да готово галочка|☑️~галочка|❌~нет отмена|❗~важно внимание|❓~вопрос|⚠️~внимание|📅~календарь дата|⏰~время часы|📍~адрес место|📞~телефон звонок|💬~чат сообщение|ℹ️~информация|🟢~зеленый круг|🔴~красный круг|➡️~стрелка вправо|⬇️~стрелка вниз']
];
function emojiPickerHtml() {
  const tabs=[['all','Все','☰'],['faces','Смайлы','😊'],['hearts','Сердца и жесты','♡'],['sport','Спорт и праздники','⚽'],['symbols','Символы','✓']];
  return `<div class="emoji-picker-header"><b>Эмодзи</b><button type="button" data-emoji-close aria-label="Закрыть эмодзи">×</button></div><div class="emoji-search-wrap"><input type="search" data-emoji-search placeholder="Поиск эмодзи" aria-label="Поиск эмодзи" autocomplete="off"></div><nav class="emoji-categories" aria-label="Категории эмодзи">${tabs.map(([key,label,icon])=>`<button type="button" data-emoji-category="${key}" title="${label}" aria-label="${label}" aria-pressed="${key==='all'}">${icon}</button>`).join('')}</nav><div class="emoji-scroll">${CHAT_EMOJI_GROUPS.map(([key,title,list])=>`<section data-emoji-group="${key}"><h4>${title}</h4><div class="emoji-grid">${list.split('|').map(entry=>{const [emoji,words]=entry.split('~');return `<button type="button" data-emoji="${emoji}" data-emoji-keywords="${esc(words)}" title="${esc(words)}" aria-label="${esc(words)}">${emoji}</button>`}).join('')}</div></section>`).join('')}<div class="emoji-empty" hidden>Ничего не найдено</div></div>`;
}
function wireEmojiPicker(panel,input) {
  const root=panel.querySelector('[data-picker-panel="emoji"]');
  let category='all';
  const trigger=panel.querySelector('[data-picker-tab="emoji"]');
  const floating=typeof root.showPopover==='function';
  if(floating)root.setAttribute('popover','manual');
  const position=()=>{
    if(!floating||root.hidden)return;
    const viewport=window.visualViewport,top=viewport?.offsetTop||0,available=viewport?.height||innerHeight;
    root.style.setProperty('--emoji-room',Math.max(60,available-160)+'px');
    const rect=trigger.getBoundingClientRect(),height=root.getBoundingClientRect().height,width=root.getBoundingClientRect().width;
    root.style.left=Math.max(12,Math.min(rect.left,innerWidth-width-12))+'px';
    root.style.top=Math.max(top+12,Math.min(rect.top-height-10,top+available-height-12))+'px';
  };
  const dismiss=event=>{if(!root.contains(event.target)&&!trigger.contains(event.target))root._setEmojiOpen(false)};
  const escape=event=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close()}};
  root._setEmojiOpen=opening=>{
    document.removeEventListener('pointerdown',dismiss,true);document.removeEventListener('keydown',escape,true);window.removeEventListener('resize',position);window.visualViewport?.removeEventListener('resize',position);
    if(!opening&&floating&&root.matches(':popover-open'))root.hidePopover();
    root.hidden=!opening;trigger.setAttribute('aria-expanded',String(opening));
    if(opening){if(floating)root.showPopover();position();document.addEventListener('pointerdown',dismiss,true);document.addEventListener('keydown',escape,true);window.addEventListener('resize',position);window.visualViewport?.addEventListener('resize',position)}
  };
  const normalize=value=>String(value).toLocaleLowerCase('ru').replace(/ё/g,'е').trim();
  const filter=()=>{
    const terms=normalize(root.querySelector('[data-emoji-search]').value).split(/\s+/).filter(Boolean);let found=0;
    root.querySelectorAll('[data-emoji-group]').forEach(section=>{
      let count=0;const included=(category==='all'||section.dataset.emojiGroup===category)&&!(terms.length&&section.dataset.emojiGroup==='popular');
      section.querySelectorAll('[data-emoji]').forEach(button=>{button.hidden=!included||!terms.every(term=>normalize(button.dataset.emojiKeywords+' '+button.dataset.emoji).includes(term));if(!button.hidden)count++});
      section.hidden=!count;found+=count;
    });
    root.querySelector('.emoji-empty').hidden=!!found;position();
  };
  root.querySelector('[data-emoji-search]').oninput=filter;
  root.querySelectorAll('[data-emoji-category]').forEach(button=>button.onclick=()=>{
    category=button.dataset.emojiCategory;root.querySelectorAll('[data-emoji-category]').forEach(tab=>tab.setAttribute('aria-pressed',String(tab===button)));filter();root.querySelector('.emoji-scroll').scrollTop=0;
  });
  const close=()=>{root._setEmojiOpen(false);input.focus()};
  root.querySelector('[data-emoji-close]').onclick=close;
  root.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close()}});
  root.querySelectorAll('[data-emoji]').forEach(button=>{
    button.onmousedown=event=>event.preventDefault();
    button.onclick=()=>{input.setRangeText(button.dataset.emoji,input.selectionStart,input.selectionEnd,'end');input.focus();input.dispatchEvent(new Event('input',{bubbles:true}))};
  });
}

/* One picker shared by the inbox and the client card. */
function wireMessagePicker(selector, sendSticker, context={}) {
  const input = $(selector); if (!input) return;
  const panel = document.createElement("div");
  panel.className = "message-picker";

  panel.innerHTML = `<div class="chips"><button type="button" class="btn sm" data-attach-file title="Прикрепить фото или файл" aria-label="Прикрепить фото или файл">📎</button><input type="file" data-attach-input hidden accept=".jpg,.jpeg,.png,.webp,.pdf,.txt,.csv,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.odt,.ods,.zip"><button type="button" class="btn sm" data-administrator-greeting title="Приветствие" aria-label="Вставить приветствие">👋</button><button type="button" class="btn sm" data-picker-tab="scripts" title="Скрипты" aria-label="Скрипты" aria-expanded="false">📝</button><button type="button" class="btn sm" data-picker-tab="emoji" title="Смайлы" aria-label="Смайлы" aria-expanded="false">😊</button><button type="button" class="btn sm" data-picker-tab="sticker" title="Стикеры" aria-label="Стикеры" aria-expanded="false">🖼️</button></div>
    <div class="picker-options" data-picker-panel="scripts" hidden></div>
    <div class="picker-options emoji-picker" data-picker-panel="emoji" hidden>${emojiPickerHtml()}</div>
    <div class="picker-options" data-picker-panel="sticker" hidden><div data-custom-packs></div></div>`;
  panel.querySelectorAll("[data-picker-tab]").forEach(b => {
    b.onmousedown = e => e.preventDefault();
    b.onclick = () => {
      const opening = b.getAttribute("aria-expanded") !== "true";
      if (opening && b.dataset.pickerTab === "sticker") loadCustomPacks().catch(fail);
      if (opening && b.dataset.pickerTab === "scripts") loadScripts().catch(fail);
      panel.querySelectorAll("[data-picker-tab]").forEach(x => x.setAttribute("aria-expanded", String(opening && x === b)));
      panel.querySelectorAll("[data-picker-panel]").forEach(x => { const show=opening&&x.dataset.pickerPanel===b.dataset.pickerTab;if(x._setEmojiOpen)x._setEmojiOpen(show);else x.hidden=!show; });
    };
  });
  const editorRow=input.closest('.row');editorRow.after(panel);
  const preview=document.createElement('div');preview.className='small';panel.append(preview);input._attachmentPreview=preview;
  const fileInput=panel.querySelector('[data-attach-input]');
  panel.querySelector('[data-attach-file]').onclick=()=>{if(!input._attachmentBusy)fileInput.click()};
  fileInput.onchange=()=>{
    const file=fileInput.files[0];fileInput.value='';if(!file)return;
    if(file.size>5*1024*1024){toast('Файл должен быть не больше 5 МБ');return;}
    input._attachmentFile=file;input._attachmentUpload=null;
    preview.innerHTML='<span></span> <button type="button" class="btn sm" data-remove-file>Убрать</button>';
    preview.querySelector('span').textContent=file.name+' · '+Math.ceil(file.size/1024)+' КБ · отправится по кнопке «Отправить»';
    preview.querySelector('button').onclick=()=>{if(!input._attachmentBusy){input._attachmentFile=null;input._attachmentUpload=null;preview.replaceChildren()}};
  };
  panel.querySelector('[data-administrator-greeting]').onclick=()=>insertAdministratorGreeting(input);
  wireEmojiPicker(panel,input);
  let scriptsRequest=0;
  const loadScripts = async (chosenStudent,chosenGroup) => {
    const request=++scriptsRequest;
    const root=panel.querySelector('[data-picker-panel="scripts"]');
    const students=context.students||[];
    const sid=chosenStudent||context.student_id||(students.length===1?students[0].id:0);
    root.replaceChildren();
    if(students.length>1){
      const label=document.createElement('label');label.textContent='Для кого';
      const select=document.createElement('select');select.innerHTML='<option value="">Выберите ребёнка</option>'+students.map(k=>`<option value="${k.id}" ${k.id===+sid?'selected':''}>${esc(displayChildName(k.name))}</option>`).join('');
      select.onchange=()=>loadScripts(+select.value).catch(fail);label.append(select);root.append(label);
    }
    const channel=context.channel?context.channel():'';
    if(typeof memoSync==='function')await memoSync();
    const d=await api('GET','/api/chat-scripts?'+new URLSearchParams({student_id:sid||'',group_id:chosenGroup||'',channel}));
    if(!panel.isConnected||request!==scriptsRequest)return;
    const rules=d.items.find(x=>x.key==='rules');
    if(rules?.groups?.length>1){
      const label=document.createElement('label');label.textContent='Группа';const select=document.createElement('select');
      select.innerHTML='<option value="">Выберите группу</option>'+rules.groups.map(g=>`<option value="${g.id}" ${g.id===+rules.group_id?'selected':''}>${esc(g.name)}</option>`).join('');
      select.onchange=()=>loadScripts(+sid,+select.value).catch(fail);label.append(select);root.append(label);
    }
    for(const item of d.items){
      const details=document.createElement('details');details.style.marginTop='8px';
      const summary=document.createElement('summary');summary.textContent=item.title;details.append(summary);
      if(item.image){const img=document.createElement('img');img.src=item.image;img.alt=item.title;img.style.cssText='max-width:200px;max-height:260px;object-fit:contain';details.append(img)}
      const text=document.createElement('div');text.style.cssText='white-space:pre-wrap;margin:8px 0';text.textContent=item.reason||item.text;details.append(text);
      const button=document.createElement('button');button.type='button';button.className='btn sm';button.textContent='Вставить в сообщение';button.disabled=!!item.reason;details.append(button);
      button.onclick=async()=>{
        if(input._attachmentBusy)return;
        if((input.value.trim()||input._attachmentFile)&&!confirm('Заменить текущий черновик выбранным скриптом?'))return;
        button.disabled=true;
        try{
          if(context.channel&&context.channel()!==channel)throw Error('Мессенджер изменился. Откройте скрипты заново');
          if(item.key==='rules'){
            const cid=await context.resolveConversation();
            const staged=await api('POST',`/api/conversations/${cid}/rules-draft`,{student_id:+sid,group_id:item.group_id});
            input._attachmentUpload={id:staged.id,cid};input._attachmentFile={name:staged.filename,size:staged.size};
            preview.innerHTML='<span></span><br><img style="max-width:160px;max-height:210px" alt="Правила школы">';
            preview.querySelector('span').textContent=staged.filename+' · вместе с текстом по кнопке «Отправить»';preview.querySelector('img').src=item.image;
            input.value=staged.text;
          }else{
            input._attachmentFile=null;input._attachmentUpload=null;preview.replaceChildren();input.value=item.text;
          }
          input.focus();input.dispatchEvent(new Event('input',{bubbles:true}));root.hidden=true;panel.querySelector('[data-picker-tab="scripts"]').setAttribute('aria-expanded','false');
        }catch(e){fail(e)}finally{button.disabled=!!item.reason}
      };
      root.append(details);
    }
  };
  const loadCustomPacks = async () => {
    const root = panel.querySelector("[data-custom-packs]");
    const data = await api("GET", "/api/sticker-packs");
    if (!panel.isConnected) return;
    root.innerHTML = data.packs.map(p => `<div class="custom-sticker-pack"><b>${esc(p.title)}</b><div class="sticker-grid">${p.stickers.map(s => `<button type="button" class="sticker-choice" data-sticker="${esc(s.id)}" title="Отправить стикер ${esc(s.emoji)}"><img src="${esc(s.image)}" width="72" height="72" loading="lazy" alt="${esc(s.emoji || "Стикер")}"><span>${esc(s.emoji)}${s.animated ? " · анимация в TG" : ""}</span></button>`).join("")}</div></div>`).join("") || `<p class="small muted">Нет доступных наборов</p>`;
  };
  const importer = panel.querySelector("[data-pack-import]");
  if (importer) importer.onclick = async () => {
    const status = panel.querySelector("[data-pack-status]");
    importer.disabled = true; status.textContent = "Импортирую набор…";
    try {
      const result = await api("POST", "/api/sticker-packs", {url: panel.querySelector("[data-pack-url]").value});
      await loadCustomPacks(); status.textContent = `Набор добавлен: ${result.total} стикеров`;
    } catch (e) { status.textContent = e.message; }
    finally { importer.disabled = false; }
  };
  const uploads=panel.querySelector('[data-upload-stickers]');
  if(uploads)uploads.onchange=async()=>{
    const files=Array.from(uploads.files||[]),status=panel.querySelector('[data-upload-status]'),title=panel.querySelector('[data-upload-pack-title]').value.trim();
    if(!files.length)return;if(!title){status.textContent='Укажите название набора';uploads.value='';return}
    if(files.length>120){status.textContent='Выберите не больше 120 картинок';uploads.value='';return}
    uploads.disabled=true;let added=0,duplicates=0,failed=0;
    for(let i=0;i<files.length;i++){
      status.textContent=`Загружаю ${i+1} из ${files.length}`;
      try{
        const file=files[i];if(file.size>10_000_000||!['image/png','image/jpeg','image/webp','image/gif'].includes(file.type))throw new Error('Выберите PNG, WEBP, JPG или GIF размером до 10 МБ');
        const image=await createImageBitmap(file);let data;
        try{const scale=Math.min(1,512/image.width,512/image.height),canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(image.width*scale));canvas.height=Math.max(1,Math.round(image.height*scale));canvas.getContext('2d').drawImage(image,0,0,canvas.width,canvas.height);data=canvas.toDataURL('image/png').split(',')[1]}finally{image.close()}
        const r=await api('POST','/api/sticker-packs/upload',{title,data});if(r.added)added++;else duplicates++;
      }catch(error){failed++;status.dataset.lastError=error.message}
    }
    status.textContent=`Добавлено: ${added}${duplicates?`, уже были: ${duplicates}`:''}${failed?`, не загружено: ${failed} · ${status.dataset.lastError}`:''}`;
    uploads.disabled=false;uploads.value='';try{await loadCustomPacks()}catch(error){fail(error)}
  };
  panel.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-sticker]"); if (!b || b.disabled) return;
    b.disabled = true;
    try { await sendSticker(b.dataset.sticker); panel.querySelectorAll("[data-picker-panel]").forEach(x => x.hidden = true); panel.querySelectorAll("[data-picker-tab]").forEach(x => x.setAttribute("aria-expanded", "false")); }
    catch (e) { fail(e); }
    finally { b.disabled = false; }
  });
}

// Unsupported animated sticker formats remain available as downloadable files.
document.addEventListener("error", e => {
  if (e.target instanceof HTMLImageElement && e.target.classList.contains("msg-sticker")) {
    e.target.hidden = true;
    if (e.target.nextElementSibling) e.target.nextElementSibling.hidden = false;
  }
}, true);


function clearSentComposerDraft(input,sentValue) {
  if(!input||input.value!==sentValue)return false;
  input.value='';input.dispatchEvent(new Event('input',{bubbles:true}));
  if(typeof resizeMessageInput==='function')resizeMessageInput(input);
  return true;
}
