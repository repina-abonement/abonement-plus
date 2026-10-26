"use strict";
/* Абонемент Плюс — чаты и звонки. Подключается после app.js и пользуется его функциями. */

const CH = { telegram: "Telegram", max: "MAX", whatsapp: "WhatsApp" };
const chLabel = (c) => CH[c] || String(c || "").replace("wazzup_", "");
const CALL_ST = { ringing: "звонит", talking: "разговор", answered: "принят", missed: "пропущен", busy: "занято", failed: "не дозвонились" };
const secs = (n) => n == null ? "" : n < 60 ? `${n} сек` : `${Math.floor(n / 60)} мин ${n % 60 ? (n % 60) + " сек" : ""}`.trim();
const dtWhen = (iso) => {
  if (!iso) return "";
  const today = isoDate(new Date());
  return iso.slice(0, 10) === today ? iso.slice(11, 16) : `${dShort(iso)} ${iso.slice(11, 16)}`;
};
S.inbox = { tab: "chats", filter: "all", q: "" };

async function pollCounts() {
  try {
    const c = await api("GET", "/api/inbox/counts");
    const n = c.unread + c.missed, b = $("#inbox-badge");
    if (b) { b.hidden = !n; b.textContent = n; }
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
      ${k.transcript ? `<details class="small"><summary>Расшифровка</summary>${esc(k.transcript)}</details>` : ""}
    </div></div>`;
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
      <div class="seg"><button data-tab="chats" class="${I.tab === "chats" ? "active" : ""}">Переписки</button><button data-tab="calls" class="${I.tab === "calls" ? "active" : ""}">Звонки</button></div></div>
    <div id="inbox-body"></div>`;
  page.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => { I.tab = b.dataset.tab; render(); }));
  if (I.tab === "calls") return renderCalls($("#inbox-body"));
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
          <button class="chip" id="unlink">Отвязать</button></div>`
        : `<div class="small">Переписка не привязана к карточке</div>
          <div class="row" style="margin-top:8px"><input id="link-q" placeholder="Найти ученика или родителя"><button class="btn sm" id="new-lead" style="flex:0">+ Новый лид</button></div>
          <div id="link-res" class="chips" style="margin-top:6px"></div>`}
      </div>
      <div class="feed chat-feed" id="chat-feed">${d.messages.map((m) => msgHtml(m)).join("") || `<div class="muted small">Сообщений нет</div>`}</div>
      <div class="composer sticky">
        <div class="row"><textarea id="chat-text" rows="2" placeholder="Ответ…">${esc(draft)}</textarea><button class="btn primary" id="chat-send" style="flex:0">Отправить</button></div>
        ${c.channel === "telegram" && !c.meta.biz ? `<div class="small muted">Ответ уйдёт от бота школы</div>` : ""}
      </div>
    </div>`;
  openDrawer(html);
  const feed = $("#chat-feed"); feed.scrollTop = feed.scrollHeight;
  const body = $("#drawer-body");
  body.querySelectorAll("[data-open]").forEach((b) => (b.onclick = () => { stopChatTimer(); openStudent(+b.dataset.open); }));
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
  box.querySelectorAll("[data-copy]").forEach((b) => (b.onclick = async () => {
    try { await navigator.clipboard.writeText(b.dataset.copy); toast("Скопировано"); } catch (_) { b.previousElementSibling.select(); }
  }));
  box.querySelector("details").ontoggle = async (e) => {
    if (!e.target.open) return;
    const rows = await api("GET", "/api/integrations/log").catch(() => []);
    $("#hook-log").innerHTML = rows.length ? rows.map((r) => `<div class="log-row"><b>${dtShort(r.ts)} ${esc(r.source)}</b>${r.error ? ` <span class="error">${esc(r.error)}</span>` : ""}<pre>${esc(r.payload)}</pre></div>`).join("") : "Пока пусто";
  };
}
