"use strict";
/* Абонемент Плюс — чаты и звонки. Подключается после app.js и пользуется его функциями. */

const CH = { telegram: "Telegram", max: "MAX", whatsapp: "WhatsApp" };
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

async function pollCounts() {
  try {
    const c = await api("GET", "/api/inbox/counts");
    const n = c.unread + c.missed;
    document.querySelectorAll("[data-badge]").forEach((b) => { b.hidden = !n; b.textContent = n; });
    S.counts = c;
  } catch (_) { /* тихо */ }
}

/* ---------- сообщение ---------- */
function msgHtml(m) {
  const media = m.media ? (m.kind === "image"
    ? `<a href="/api/messages/${m.id}/media" target="_blank" rel="noopener"><img class="msg-img" loading="lazy" src="/api/messages/${m.id}/media" alt="Фото"></a>`
    : m.kind === "audio" ? `<audio controls preload="none" src="/api/messages/${m.id}/media"></audio>`
    : `<a href="/api/messages/${m.id}/media" target="_blank" rel="noopener">Открыть вложение</a>`) : "";
  const label = !m.text && !media ? ({ sticker: "Стикер", contact: "Контакт", other: "Вложение" }[m.kind] || "") : "";
  const who = m.direction === "out" ? (m.user_name || "") : "";
  return `<div class="msg ${m.direction}${m.status === "error" ? " err" : ""}">
    ${media}${m.text ? `<div class="msg-text">${esc(m.text)}</div>` : ""}${label ? `<div class="msg-text muted">${label}</div>` : ""}
    <div class="msg-meta">${[who, m.channel_label, dtWhen(m.ts), m.status === "error" ? "не доставлено: " + esc(m.error) : "", m.status === "demo" ? "тест, не отправлено" : ""].filter(Boolean).join(" · ")}</div>
  </div>`;
}

function callHtml(k) {
  const missed = k.direction === "in" && ["missed", "busy"].includes(k.status);
  return `<div class="call-row ${missed ? "missed" : ""}">
    <span class="call-ico">${k.direction === "in" ? "↙" : "↗"}</span>
    <div class="grow"><b>${k.direction === "in" ? "Входящий" : "Исходящий"}</b> · ${esc(CALL_ST[k.status] || k.status)}${k.talk ? " · " + secs(k.talk) : ""}
      <div class="small muted">${dtWhen(k.ts || k.started_at)}${k.operator ? " · сотрудник " + esc(k.operator) : ""}</div>
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
async function loadFamilyComms(familyId, parents, studentId) {
  const box = $("#comms-box");
  if (!box) return;
  let d;
  try { d = await api("GET", `/api/families/${familyId}/comms`); } catch (e) { box.innerHTML = `<h3>Связь с семьёй</h3><div class="error">${esc(e.message)}</div>`; return; }
  if (!$("#comms-box")) return;
  const st = d.status;
  const targets = d.conversations.map((c) => ({ v: "c" + c.id, t: `${chLabel(c.channel)}${c.title ? " · " + c.title : ""}` }));
  if (st.whatsapp) parents.filter((p) => p.phone && !d.conversations.some((c) => c.channel === "whatsapp" && c.phone_norm && p.phone.replace(/\D/g, "").endsWith(c.phone_norm.slice(1))))
    .forEach((p) => targets.push({ v: "w" + p.id, t: `WhatsApp · ${p.name} (новый чат)` }));
  box.innerHTML = `
    <h3>Связь с семьёй</h3>
    <div class="chips" style="margin-bottom:10px">${parents.filter((p) => p.phone).map((p) => `<button class="btn sm" data-dial="${esc(p.phone)}">📞 ${esc(p.name.split(" ")[0])} · ${esc(p.phone)}</button>`).join("")}</div>
    <div class="feed" id="feed">${d.items.length ? d.items.map((i) => i.type === "call" ? callHtml(i) : msgHtml(i)).join("") : `<div class="muted small">Пока ни переписки, ни звонков</div>`}</div>
    ${targets.length ? `
      <div class="composer">
        ${targets.length > 1 ? `<select id="cm-to">${targets.map((x) => `<option value="${x.v}">${esc(x.t)}</option>`).join("")}</select>` : `<div class="small muted">Ответ уйдёт: ${esc(targets[0].t)}</div><input type="hidden" id="cm-to" value="${targets[0].v}">`}
        <div class="row"><textarea id="cm-text" rows="2" placeholder="Сообщение родителю…"></textarea><button class="btn primary" id="cm-send" style="flex:0">Отправить</button></div>
      </div>` : `<div class="small muted">Написать отсюда можно, когда родитель напишет школе в Telegram или MAX${st.whatsapp ? "" : ", а в WhatsApp после подключения Wazzup"}</div>`}`;
  const feed = $("#feed"); if (feed) feed.scrollTop = feed.scrollHeight;
  box.querySelectorAll("[data-dial]").forEach((b) => (b.onclick = () => dial(b.dataset.dial)));
  const send = $("#cm-send");
  if (send) send.onclick = async () => {
    const text = $("#cm-text").value.trim(), to = $("#cm-to").value;
    if (!text) return;
    send.disabled = true;
    try {
      if (to[0] === "c") await api("POST", `/api/conversations/${to.slice(1)}/send`, { text });
      else await api("POST", `/api/families/${familyId}/whatsapp`, { parent_id: +to.slice(1), text });
      loadFamilyComms(familyId, parents, studentId);
    } catch (e) { fail(e); send.disabled = false; }
  };
}

/* ---------- раздел «Чаты и звонки» ---------- */
async function renderInbox(page) {
  const I = S.inbox;
  page.innerHTML = `
    <div class="page-head"><h2 class="grow">Чаты и звонки</h2>
      <div class="seg">${[["chats", "Переписки"], ["calls", "Звонки"], ["stats", "Аналитика звонков"]].map(([k, t]) => `<button data-tab="${k}" class="${I.tab === k ? "active" : ""}">${t}</button>`).join("")}</div></div>
    <div id="inbox-body"></div>`;
  page.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => { step(() => { I.tab = b.dataset.tab; }); }));
  if (I.tab === "calls") return renderCalls($("#inbox-body"));
  if (I.tab === "stats") return renderCallStats($("#inbox-body"));
  const body = $("#inbox-body");
  body.innerHTML = `
    <div class="row" style="max-width:720px;margin-bottom:12px;align-items:center">
      <div class="seg" style="flex:0 0 auto">${[["all", "Все"], ["unread", "Непрочитанные"], ["nofamily", "Без карточки"]].map(([k, t]) => `<button data-f="${k}" class="${I.filter === k ? "active" : ""}">${t}</button>`).join("")}</div>
      <input class="search" id="iq" placeholder="Имя, телефон, семья" value="${esc(I.q)}">
    </div>
    ${connectHint()}
    <div class="list" id="conv-list"><div class="list-row muted">Загружаю…</div></div>`;
  body.querySelectorAll("[data-f]").forEach((b) => (b.onclick = () => { I.filter = b.dataset.f; render(); }));
  let t; $("#iq").oninput = (e) => { I.q = e.target.value; clearTimeout(t); t = setTimeout(loadList, 250); };
  async function loadList() {
    const d = await api("GET", `/api/inbox?filter=${I.filter}&q=${encodeURIComponent(I.q)}`);
    const list = $("#conv-list"); if (!list) return;
    list.innerHTML = d.conversations.length ? d.conversations.map((c) => `
      <div class="list-row click" data-conv="${c.id}">
        <span class="pill ch-${esc(c.channel)}">${esc(chLabel(c.channel))}</span>
        <div class="grow"><b>${esc(c.title || c.username || c.phone || "Без имени")}</b>${c.family_name ? ` <span class="muted small">· семья ${esc(c.family_name)}</span>` : ` <span class="pill yellow">нет карточки</span>`}${c.demo ? ` <span class="pill">тест</span>` : ""}
          <div class="small muted ellipsis">${esc(c.last_text)}</div></div>
        <div class="right"><div class="small muted">${dtWhen(c.last_at)}</div>${c.unread ? `<span class="nbadge static">${c.unread}</span>` : ""}</div>
      </div>`).join("") : `<div class="list-row muted">Переписок нет</div>`;
    list.querySelectorAll("[data-conv]").forEach((el) => (el.onclick = () => openChat(+el.dataset.conv)));
  }
  await loadList();
}

function connectHint() {
  const c = S.meta.comms;
  if (c.telegram || c.max || c.whatsapp) return "";
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
          <div class="chips" style="margin-top:6px">${k.students.map((x) => `<button class="chip" data-open="${x.id}">${esc(x.name)}</button>`).join("")}
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
async function openChat(cid, keepScroll = false) {
  let d;
  try { d = await api("GET", `/api/conversations/${cid}`); } catch (e) { return fail(e); }
  const c = d.conversation;
  const draft = $("#chat-text") ? $("#chat-text").value : "";
  const html = `
    <div class="chat" data-chat="${cid}">
      <div><span class="pill ch-${esc(c.channel)}">${esc(c.channel_label)}</span>${c.demo ? ` <span class="pill">тест</span>` : ""}</div>
      <h2 style="margin-top:8px">${esc(c.title || c.username || c.phone || "Без имени")}</h2>
      <div class="small muted">${[c.username ? "@" + c.username : "", c.phone].filter(Boolean).map(esc).join(" · ")}</div>
      <div class="section">
        ${c.family_id ? `<div class="small muted">Семья ${esc(c.family_name)}</div>
          <div class="chips" style="margin-top:6px">${c.students.map((x) => `<button class="chip" data-open="${x.id}">${esc(x.name)}</button>`).join("")}
          <button class="chip" id="unlink">Отвязать</button></div><div id="warn-box"></div>`
        : `<div class="small">Переписка не привязана к карточке</div>
          <div class="row" style="margin-top:8px"><input id="link-q" placeholder="Найти ученика или родителя"><button class="btn sm" id="new-lead" style="flex:0">+ Новый лид</button></div>
          <div id="link-res" class="chips" style="margin-top:6px"></div>`}
      </div>
      <div class="feed chat-feed" id="chat-feed">${d.messages.map((m) => msgHtml(m)).join("") || `<div class="muted small">Сообщений нет</div>`}</div>
      <div class="composer sticky">
        <div class="to-bar"><button class="btn sm" id="to-open">📅 Предложить ближайшие ПУ</button></div>
        <div id="to-panel" hidden></div>
        <div class="row"><textarea id="chat-text" rows="2" placeholder="Ответ…">${esc(draft)}</textarea><button class="btn primary" id="chat-send" style="flex:0">Отправить</button></div>
        ${c.channel === "telegram" && !c.meta.biz ? `<div class="small muted">Ответ уйдёт от бота школы</div>` : ""}
      </div>
    </div>`;
  openDrawer(html);
  const feed = $("#chat-feed"); feed.scrollTop = feed.scrollHeight;
  const body = $("#drawer-body");
  body.querySelectorAll("[data-open]").forEach((b) => (b.onclick = () => { stopChatTimer(); openStudent(+b.dataset.open); }));
  if (c.family_id) loadWarnBox(c.family_id, $("#warn-box"));
  wireTrialOffer(c);
  if ($("#unlink")) $("#unlink").onclick = async () => { await api("POST", `/api/conversations/${cid}/link`, { family_id: null }).catch(fail); openChat(cid); };
  if ($("#new-lead")) $("#new-lead").onclick = () => { stopChatTimer(); openNewClient({ phone: c.phone, parent_name: c.title, conversation_id: cid }); };
  if ($("#link-q")) {
    let t;
    $("#link-q").oninput = (e) => {
      clearTimeout(t);
      t = setTimeout(async () => {
        const rows = e.target.value.trim().length < 2 ? [] : await api("GET", "/api/students?q=" + encodeURIComponent(e.target.value)).catch(() => []);
        $("#link-res").innerHTML = rows.slice(0, 8).map((r) => `<button class="chip" data-link="${r.id}">${esc(r.name)} · ${esc(r.family)}</button>`).join("");
        $("#link-res").querySelectorAll("[data-link]").forEach((b) => (b.onclick = async () => {
          const st = await api("GET", `/api/students/${b.dataset.link}`);
          await api("POST", `/api/conversations/${cid}/link`, { family_id: st.family.id }).catch(fail);
          toast("Переписка привязана к семье " + st.family.name);
          openChat(cid);
        }));
      }, 250);
    };
  }
  const sendBtn = $("#chat-send");
  sendBtn.onclick = async () => {
    const text = $("#chat-text").value.trim(); if (!text) return;
    sendBtn.disabled = true;
    try { await api("POST", `/api/conversations/${cid}/send`, { text }); $("#chat-text").value = ""; openChat(cid); }
    catch (e) { fail(e); sendBtn.disabled = false; }
  };
  $("#chat-text").onkeydown = (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) sendBtn.click(); };
  stopChatTimer();
  chatTimer = setInterval(async () => {
    const open = $(`#drawer-body [data-chat="${cid}"]`);
    if (!open || $("#drawer").hidden) return stopChatTimer();
    const n = await api("GET", `/api/conversations/${cid}`).catch(() => null);
    if (n && n.messages.length !== d.messages.length) { stopChatTimer(); openChat(cid); }
  }, 8000);
  pollCounts();
  if (S.view === "inbox" && !keepScroll) render();
}
function stopChatTimer() { if (chatTimer) clearInterval(chatTimer); chatTimer = null; }

/* ---------- настройки связи (админ) ---------- */
async function renderCommsSettings(box, users) {
  const d = await api("GET", "/api/integrations");
  const v = d.values, inf = d.info, st = d.status;
  const ok = (on, text) => `<span class="pill ${on ? "green" : ""}">${on ? "подключено" : text || "не подключено"}</span>`;
  box.innerHTML = `
    <h3>Связь с клиентами</h3>
    <div class="small muted" style="margin-bottom:10px">Ключи хранятся на вашем сервере. Чтобы заменить ключ, вставьте новый и нажмите «Сохранить».</div>

    <div class="int"><div class="int-head"><b>Телефония Sipuni</b>${ok(st.sipuni)}</div>
      <div class="row"><div class="field"><label>Номер кабинета</label><input data-k="sipuni_user" value="${esc(v.sipuni_user)}" placeholder="064629"></div>
        <div class="field"><label>Ключ интеграции</label><input data-k="sipuni_secret" value="${esc(v.sipuni_secret)}"></div></div>
      <div class="field"><label>Адрес для событий (вставить в Sipuni)</label><div class="copy"><input readonly value="${esc(d.hooks.sipuni)}"><button class="btn sm" data-copy="${esc(d.hooks.sipuni)}">Копировать</button></div></div>
      <div class="small muted">Последнее событие: ${inf.sipuni_last ? dtShort(inf.sipuni_last) : "ещё не было"}. Внутренние номера сотрудников указываются ниже, в списке сотрудников</div>
    </div>

    <div class="int"><div class="int-head"><b>Telegram</b>${ok(st.telegram)}${inf.tg_business_enabled ? `<span class="pill green">бизнес-аккаунт</span>` : ""}</div>
      <div class="field"><label>Токен бота от @BotFather</label><input data-k="tg_token" value="${esc(v.tg_token)}"></div>
      ${inf.tg_bot ? `<div class="small">Бот: ${esc(inf.tg_bot)}</div>` : ""}${inf.tg_error ? `<div class="error">${esc(inf.tg_error)}</div>` : ""}
      <button class="btn sm" data-connect="telegram">Сохранить и подключить</button>
    </div>

    <div class="int"><div class="int-head"><b>MAX</b>${ok(st.max)}</div>
      <div class="field"><label>Токен бота (MAX для партнёров → Чат-боты → Настройки)</label><input data-k="max_token" value="${esc(v.max_token)}"></div>
      ${inf.max_bot ? `<div class="small">Бот: ${esc(inf.max_bot)}</div>` : ""}${inf.max_error ? `<div class="error">${esc(inf.max_error)}</div>` : ""}
      <button class="btn sm" data-connect="max">Сохранить и подключить</button>
    </div>

    <div class="int"><div class="int-head"><b>WhatsApp через Wazzup</b>${ok(st.whatsapp)}</div>
      <div class="field"><label>API-ключ Wazzup</label><input data-k="wazzup_key" value="${esc(v.wazzup_key)}"></div>
      ${inf.wazzup_channels.length ? `<div class="small">Каналы: ${inf.wazzup_channels.map((c) => esc(`${c.transport} ${c.name} (${c.state})`)).join(", ")}</div>` : ""}
      ${inf.wazzup_error ? `<div class="error">${esc(inf.wazzup_error)}</div>` : ""}
      <button class="btn sm" data-connect="wazzup">Сохранить и подключить</button>
    </div>

    <div class="int"><div class="int-head"><b>Заявки с сайта</b>${ok(!!inf.lead_last, "ждём первую заявку")}</div>
      <div class="field"><label>Адрес для заявок (уже вписан в скрипт Google)</label><div class="copy"><input readonly value="${esc(d.hooks.lead)}"><button class="btn sm" data-copy="${esc(d.hooks.lead)}">Копировать</button></div></div>
      <div class="small muted">Последняя заявка: ${inf.lead_last ? dtShort(inf.lead_last) : "ещё не было"}</div>
    </div>

    ${aiSettingsHtml(v, inf, st)}

    <div class="int"><div class="field"><label>Приветствие бота в Telegram и MAX</label><textarea data-k="bot_greeting" rows="3">${esc(v.bot_greeting)}</textarea></div></div>
    <button class="btn primary" id="int-save">Сохранить</button>
    <details style="margin-top:12px"><summary class="small muted">Журнал входящих событий</summary><div id="hook-log" class="small"></div></details>`;
  const collect = () => Object.fromEntries([...box.querySelectorAll("[data-k]")].map((i) => [i.dataset.k, i.value]));
  $("#int-save").onclick = async () => { try { await api("PUT", "/api/integrations", collect()); S.meta = await api("GET", "/api/meta"); toast("Сохранено"); renderCommsSettings(box, users); } catch (e) { fail(e); } };
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
  box.querySelector("details").ontoggle = async (e) => {
    if (!e.target.open) return;
    const rows = await api("GET", "/api/integrations/log").catch(() => []);
    $("#hook-log").innerHTML = rows.length ? rows.map((r) => `<div class="log-row"><b>${dtShort(r.ts)} ${esc(r.source)}</b>${r.error ? ` <span class="error">${esc(r.error)}</span>` : ""}<pre>${esc(r.payload)}</pre></div>`).join("") : "Пока пусто";
  };
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
      ${d.unassigned ? `<div class="small muted" style="margin-bottom:8px">Часть номеров без имени: впишите внутренние номера сотрудникам в Настройках → Сотрудники</div>` : ""}
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
  const weak = x.checklist.find((c) => c.key === x.weakest);
  const op = I.aiOp ? x.operators.find((o) => o.number === I.aiOp) : null;
  const steps = op ? op.steps : x.team;
  return `
    <section class="panel" style="margin-top:14px">
      <div class="row" style="align-items:center"><h3 class="grow" style="flex:1">Разбор разговоров</h3>
        <span class="small muted" style="flex:0 0 auto">Разобрано ${x.analyzed}${x.pending ? `, ещё ${x.pending} в работе` : ""}</span></div>
      ${!x.analyzed ? `<div class="small muted">Разборов за этот период пока нет</div>`
        : weak && x.team[weak.key] < 80 ? `<div class="ai-weak">Чаще всего теряем клиента на шаге: <b>${esc(weak.title.toLowerCase())}</b>. Делают только в ${x.team[weak.key]}% разговоров</div>`
        : `<div class="ai-good" style="margin-top:8px">Все шаги разговора выполняются хорошо, так держать</div>`}
      ${x.operators.length ? `
      <div class="chips" style="margin:12px 0 8px"><span class="small muted" style="align-self:center">Показать:</span>
        <button class="chip ${!op ? "on-chip" : ""}" data-aiop="${I.aiOp || ""}">Вся команда</button>
        ${x.operators.map((o) => `<button class="chip ${op && op.number === o.number ? "on-chip" : ""}" data-aiop="${esc(o.number)}">${esc(o.name)} · ${o.avg ?? "—"}/10</button>`).join("")}</div>
      <div class="steps">${x.checklist.map((c) => { const v = steps[c.key]; return `
        <div class="step ${c.key === x.weakest && !op ? "weak" : ""}"><span>${esc(c.title)}</span>
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


/* «Предложить ближайшие ПУ»: подбор групп по возрасту и ближайших дат со свободными местами, текст сразу в поле ответа */
function wireTrialOffer(c) {
  const btn = $("#to-open"), panel = $("#to-panel");
  if (!btn) return;
  const leads = (c.students || []).filter((x) => x.funnel === "sales");
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
        <button class="btn sm primary" id="to-go" style="flex:0">Подобрать</button></div>
      <div id="to-res"></div></div>`;
    const go = async () => {
      const age = $("#to-age").value;
      if (!age) return toast("Укажите возраст ребёнка", true);
      const kidId = $("#to-kid") ? +$("#to-kid").value : (kid ? kid.id : null);
      try {
        const r = await api("GET", `/api/trial-offer?age=${age}${$("#to-br").value ? "&branch_id=" + $("#to-br").value : ""}`);
        if (!r.slots.length) { $("#to-res").innerHTML = `<div class="small muted" style="margin-top:8px">${esc(r.error)}</div>`; return; }
        $("#chat-text").value = r.text;
        $("#to-res").innerHTML = `<div class="small muted" style="margin:8px 0 4px">Текст уже в поле ответа: проверьте и нажмите «Отправить». Когда родитель выберет, запишите одним нажатием:</div>
          ${r.slots.map((x, i) => `<div class="to-slot"><span class="grow">${esc(x.label)} · ${esc(x.group)} · ${esc(x.branch)} <span class="small muted">мест ${x.free}</span></span>
            ${kidId ? `<button class="btn sm" data-to-book="${i}">Записать</button>` : ""}</div>`).join("")}`;
        $("#to-res").querySelectorAll("[data-to-book]").forEach((b) => (b.onclick = async () => {
          const x = r.slots[+b.dataset.toBook];
          try {
            await api("PATCH", `/api/students/${kidId}`, { group_id: x.group_id, trial_at: `${x.date}T${x.time}` });
            toast(`Записан на пробное: ${x.label}`); panel.hidden = true; loadWarnBox(c.family_id, $("#warn-box"));
          } catch (e) { fail(e); }
        }));
      } catch (e) { fail(e); }
    };
    $("#to-go").onclick = go;
    if ($("#to-age").value) go();
  };
}
