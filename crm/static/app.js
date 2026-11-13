"use strict";
/* Абонемент Плюс — интерфейс без сборки и зависимостей. */

/* Новый дизайн (проба): хранится в этом браузере, по умолчанию выключен */
function applyTheme(t) { document.documentElement.classList.toggle("theme-soft", t === "soft"); }
try { applyTheme(localStorage.getItem("crm.theme")); } catch (_) {}
const S = { meta: null, user: null, view: "sales", journal: { group: null, date: null }, search: "" };
const $ = (sel, el = document) => el.querySelector(sel);
const app = $("#app");

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const rub = (n) => Number(n || 0).toLocaleString("ru-RU") + " ₽";
const MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];
const monthLabel = (ym) => { const [y, m] = ym.split("-").map(Number); return `${MONTHS[m - 1]} ${y}`; };
const dShort = (iso) => { if (!iso) return ""; const [y, m, d] = iso.slice(0, 10).split("-"); return `${d}.${m}`; };
const dtShort = (iso) => iso ? `${dShort(iso)} ${iso.slice(11, 16)}` : "";
const WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const PAY_METHODS = { cash: "Наличные", card: "Картой", sbp: "СБП", online: "Онлайн-оплата", transfer: "Перевод", unknown: "Перенесено",
  balance: "Перенос с баланса", refund: "Возврат", refund_pending: "Ожидает возврата" };
const PAY_INTERNAL = ["unknown", "balance", "refund", "refund_pending"];  // ставит сама CRM, при приёме оплаты не выбираются

async function api(method, url, body) {
  const opt = { method, headers: {}, credentials: "same-origin" };
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(url, opt);
  let data = null;
  try { data = await r.json(); } catch (_) { /* пусто */ }
  if (r.status === 401 && url !== "/api/login") { renderLogin(); throw new Error("Нужно войти"); }
  if (r.status === 422 && data && data.need && !opt.noNeed) {
    // не хватает обязательных данных (перед пробным или продажей): спросим и повторим то же действие
    if (await askRequired(data.need)) return api(method, url, body);
    throw Object.assign(new Error("Действие не выполнено: не хватает данных"), { status: 422, silent: true });
  }
  if (!r.ok) throw Object.assign(new Error((data && data.error) || "Ошибка"), { status: r.status });
  return data;
}

/* Окошко «Не хватает данных»: имя и фамилия ребёнка, дата рождения, имя и телефон родителя, для продажи ещё почта */
function askRequired(need) {
  return new Promise((resolve) => {
    const types = { child_name: "text", birth: "date", parent_name: "text", phone: "tel", email: "email" };
    const ph = { child_name: "Например: Петя Иванов", parent_name: "Например: Анна", phone: "+7 900 123-45-67", email: "Например: anna@mail.ru" };
    const wrap = document.createElement("div");
    wrap.className = "need-wrap";
    wrap.innerHTML = `<form class="need-card"><h3>${need.kind === "sale" ? "Перед продажей абонемента" : "Перед записью на пробное"}</h3>
      <p class="small muted">${need.kind === "sale" ? "Заполните данные клиента. На почту PayKeeper пришлёт родителю чек" : "Уточните у родителя и заполните. Без этого на пробное не записать"}</p>
      ${need.fields.map((k) => `<div class="field ${need.missing.includes(k) ? "need-miss" : ""}"><label>${esc(need.labels[k])}${need.missing.includes(k) ? " · нужно заполнить" : ""}</label>
        <input name="${k}" type="${types[k]}" value="${esc(need.values[k] || "")}" placeholder="${esc(ph[k] || "")}" ${k === "email" ? 'autocomplete="off"' : ""} required></div>`).join("")}
      <div class="need-err small"></div>
      <div class="row" style="gap:10px;margin-top:6px"><button class="btn primary" style="flex:1">Сохранить и продолжить</button><button type="button" class="btn" data-ncancel style="flex:0 0 auto">Отмена</button></div>
      <button type="button" class="btn ghost" data-nask style="width:100%;margin-top:8px">💬 Данных нет: попросить у родителя</button></form>`;
    document.body.appendChild(wrap);
    const f = wrap.querySelector("form");
    const first = f.querySelector(".need-miss input") || f.querySelector("input"); if (first) setTimeout(() => first.focus(), 50);
    const done = (v) => { wrap.remove(); resolve(v); };
    wrap.querySelector("[data-ncancel]").onclick = () => done(false);
    // данных нет: готовим вежливый запрос родителю ровно о том, чего не хватает, и вставляем его в поле ответа
    wrap.querySelector("[data-nask]").onclick = async () => {
      const miss = need.fields.filter((k) => need.missing.includes(k) || !f.elements[k].value.trim());
      // выбрано время: держим место 10 минут, пока родитель присылает данные
      const sl = S.pendingSlot;
      if (sl && sl.group_id) {
        try { const h = await api("POST", "/api/trial-hold", { student_id: need.student_id, group_id: sl.group_id, date: sl.date, time: sl.time }); sl.until = h.until; sl.minutes = h.minutes; }
        catch (e) { return fail(e); }
      }
      const nm = f.elements.child_name ? f.elements.child_name.value.trim() : "";
      const text = askRequiredText(need.kind, miss, nm && !nm.includes(" "));
      const box = [...document.querySelectorAll("#chat-text, #cm-text")].find((x) => x.offsetParent);
      if (box) {
        // после выбора времени прошлый текст (варианты дат) уже ушёл родителю: заменяем, а не дописываем
        box.value = S.pendingSlot || !box.value.trim() ? text : box.value.trim() + "\n\n" + text;
        box.rows = Math.min(8, box.value.split("\n").length + 1);
        done(false); box.focus(); box.scrollIntoView({ block: "center" });
        toast(sl && sl.until ? `Место держим до ${sl.until.slice(11, 16)}. Запрос вставлен в поле ответа: нажмите «Отправить»` : "Запрос вставлен в поле ответа: проверьте и нажмите «Отправить»");
      } else {
        try { await navigator.clipboard.writeText(text); toast("Текст запроса скопирован: вставьте его в переписку с родителем"); }
        catch (_) { prompt("Скопируйте текст запроса родителю", text); }
        done(false);
      }
    };
    f.onsubmit = async (e) => {
      e.preventDefault();
      const body = Object.fromEntries(need.fields.map((k) => [k, f.elements[k].value.trim()]));
      try {
        const r = await fetch(`/api/students/${need.student_id}/required`, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) { wrap.querySelector(".need-err").textContent = d.error || "Ошибка"; return; }
        toast("Данные сохранены"); done(true);
      } catch (_) { wrap.querySelector(".need-err").textContent = "Нет связи, попробуйте ещё раз"; }
    };
  });
}

function askRequiredText(kind, miss, onlySurname = false) {
  const ask = { child_name: onlySurname ? "фамилию ребёнка" : "имя и фамилию ребёнка", birth: "дату рождения ребёнка", parent_name: "как к вам можно обращаться",
    phone: "номер телефона для связи", email: "электронную почту: на неё придёт чек об оплате" };
  const list = (miss.length ? miss : ["child_name", "birth"]).map((k) => "• " + (ask[k] || k)).join("\n");
  const sl = kind !== "sale" && S.pendingSlot;
  // подробности о пробном (дата, место, что взять) уйдут отдельным сообщением сами, когда админ запишет ребёнка
  if (sl) return `Отлично! Место за вами держим ${sl.minutes || 10} минут. Чтобы записать ребёнка на пробное, пришлите, пожалуйста:\n${list}\nСпасибо! 😊`;
  return kind === "sale"
    ? `Здравствуйте! Чтобы оформить абонемент, пришлите, пожалуйста:\n${list}\nСпасибо!`
    : `Здравствуйте! Чтобы записать ребёнка на пробное занятие, пришлите, пожалуйста:\n${list}\nСпасибо! Как только получим, сразу запишем 😊`;
}

/* Руководитель: всё красное с рабочего стола лично в Telegram (crm/ownerbot.py) */
async function renderOwnerTg(box) {
  let d; try { d = await api("GET", "/api/me/owner-tg"); } catch (e) { box.innerHTML = `<h3>🔴 Красное в Telegram</h3><div class="error">${esc(e.message)}</div>`; return; }
  const head = `<h3>🔴 Красное в Telegram</h3><div class="small muted" style="margin-bottom:8px">Всё, что горит красным на вашем рабочем столе, бот школы пришлёт вам лично. Пропущенные звонки и заявки: только если админы не обработали их за 30 минут. Ночью с 22:00 до 8:00 бот молчит, утром присылает сводку</div>`;
  if (!d.bot) { box.innerHTML = head + `<div class="small">Сначала подключите бота школы: Настройки → «Связь с клиентами» → Telegram</div>`; return; }
  if (d.linked) {
    box.innerHTML = head + `<div>✅ Подключено: пишет бот @${esc(d.bot)}</div><button class="btn sm" id="otg-off" style="margin-top:8px">Отключить</button>`;
    $("#otg-off").onclick = async () => { await api("POST", "/api/me/owner-tg/unlink", {}).catch(fail); renderOwnerTg(box); };
    return;
  }
  box.innerHTML = head + `<button class="btn primary" id="otg-on">Подключить</button><div id="otg-code"></div>`;
  $("#otg-on").onclick = async () => {
    try {
      const r = await api("POST", "/api/me/owner-tg/code", {});
      $("#otg-code").innerHTML = `<ol class="small" style="padding-left:18px;line-height:1.7;margin-top:10px">
        <li>Откройте в Telegram бота школы: <a href="https://t.me/${esc(d.bot)}" target="_blank" rel="noopener">@${esc(d.bot)}</a></li>
        <li>Отправьте ему сообщение: <b>/crm_me ${esc(r.code)}</b> <button class="btn sm" data-copy="/crm_me ${esc(r.code)}">Копировать</button></li>
        <li>Бот ответит «✅ Готово». Код действует 30 минут</li>
        <li>Вернитесь сюда и нажмите <button class="btn sm" id="otg-check">Проверить</button></li></ol>`;
      $("#otg-code").querySelector("[data-copy]").onclick = (e) => { navigator.clipboard.writeText(e.target.dataset.copy).then(() => toast("Скопировано"), () => {}); };
      $("#otg-check").onclick = () => renderOwnerTg(box);
    } catch (e) { fail(e); }
  };
}

/* Старая переписка из Telegram школы: файл экспорта Telegram Desktop (JSON). Браузер ужимает его до текста и шлёт в CRM */
function renderTgImport(box) {
  box.innerHTML = `<h3>💬 Старая переписка из Telegram</h3>
    <div class="small muted" style="margin-bottom:8px">Переписка школьного Telegram до подключения к CRM. В карточки попадут только чаты с теми, кто есть в CRM: по номеру телефона из контактов Telegram, а если номера нет, по фамилии и имени ребёнка в названии чата. Фото и файлы не переносятся, только текст. Повторная загрузка ничего не задвоит</div>
    <details style="margin-bottom:8px"><summary class="small"><b>Как сделать файл (Telegram на компьютере)</b></summary><ol class="small" style="padding-left:18px;line-height:1.6">
      <li>Telegram → Настройки → Продвинутые настройки → «Экспорт данных Telegram»</li>
      <li>Оставьте галочки «Список контактов» и «Личные чаты», остальное выключите (фото, видео, файлы не нужны)</li>
      <li>Внизу «Формат»: «Машиночитаемый JSON» → «Экспортировать»</li>
      <li>Когда закончится, нажмите «Показать мои данные»: в папке будет файл <b>result.json</b></li></ol></details>
    <label class="btn primary">Выбрать result.json<input type="file" id="tg-file" accept=".json,application/json" hidden></label>
    <div id="tg-res" class="small" style="margin-top:8px"></div>`;
  $("#tg-file").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const res = $("#tg-res");
    res.textContent = `Читаю файл (${Math.round(f.size / 1e6)} МБ)…`;
    try {
      const raw = JSON.parse(await f.text());
      const me = String((raw.personal_information || {}).user_id || "");
      const txt = (t) => typeof t === "string" ? t : Array.isArray(t) ? t.map((x) => typeof x === "string" ? x : x.text || "").join("") : "";
      const contacts = (((raw.contacts || {}).list) || []).map((c) => ({ name: [c.first_name, c.last_name].filter(Boolean).join(" "), phone: c.phone_number || "" }));
      const chats = (((raw.chats || {}).list) || []).filter((c) => c.type === "personal_chat").map((c) => ({
        id: c.id, name: c.name || "",
        msgs: (c.messages || []).filter((m) => m.type === "message").map((m) => [m.id, m.date, String(m.from_id || "") === "user" + me, txt(m.text),
          m.media_type || (m.photo ? "фото" : m.file ? "файл" : "")]),
      }));
      if (!chats.length) { res.innerHTML = `<span class="error">В файле нет личных чатов. Проверьте, что выбран формат «Машиночитаемый JSON» и галочка «Личные чаты»</span>`; return; }
      res.textContent = `Загружаю ${chats.length} чатов в CRM…`;
      const r = await api("POST", "/api/import/telegram", { contacts, chats });
      res.innerHTML = `✅ Готово. Чатов в файле: ${r.chats}. Перенесено в карточки: <b>${r.clients}</b> (из них по имени ребёнка: ${r.by_name}), сообщений ${r.messages}.<br>
        Не клиенты CRM: ${r.not_clients}. Не нашли ни по номеру, ни по имени: ${r.no_phone}. Подходят разные семьи (не перенесены, чтобы не перепутать): ${r.ambiguous}`;
    } catch (err) { res.innerHTML = `<span class="error">${esc(err.message || "Не получилось прочитать файл")}</span>`; }
  };
}

let toastTimer;
function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast" + (err ? " err" : ""); t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), 3200);
}
const fail = (e) => { if (!e || !e.silent) toast((e && e.message) || "Ошибка", true); };

/* ---------- быстрый вход по коду ---------- */
function deviceToken(create = false) {
  try {
    let t = localStorage.getItem("crm.device");
    if (!t && create) {
      const a = new Uint8Array(32); crypto.getRandomValues(a);
      t = [...a].map((x) => x.toString(16).padStart(2, "0")).join("");
      localStorage.setItem("crm.device", t);
    }
    return t || "";
  } catch (_) { return ""; }
}

/* экран с цифровой клавиатурой; onCode(code, pad) вызывается после 4 цифр */
function pinPad({ title, sub, onCode, left }) {
  closeDrawer();
  app.innerHTML = `<div class="pin-wrap">
    <div class="pin-logo">Абонемент <span>Плюс</span></div>
    <div class="pin-mid"><h1 class="pin-title">${esc(title)}</h1><div class="pin-sub" id="pin-sub">${esc(sub)}</div>
      <div class="pin-dots" id="pin-dots">${"<i></i>".repeat(4)}</div><div class="pin-err" id="pin-err"></div></div>
    <div class="pin-keys">${[1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => `<button data-k="${n}">${n}</button>`).join("")}
      <button class="pin-link" id="pin-left">${esc(left.label)}</button><button data-k="0">0</button>
      <button id="pin-del" aria-label="Стереть"><svg viewBox="0 0 24 24" width="30" height="30" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M9 5h11v14H9l-6-7 6-7Z"/><path d="m12 9 5 6m0-6-5 6"/></svg></button></div>
  </div>`;
  let code = "";
  const pad = {
    draw() { app.querySelectorAll("#pin-dots i").forEach((d, i) => d.classList.toggle("on", i < code.length)); },
    reset(err = "") { code = ""; pad.draw(); $("#pin-err").textContent = err; if (err) { const d = $("#pin-dots"); d.classList.remove("shake"); void d.offsetWidth; d.classList.add("shake"); if (navigator.vibrate) navigator.vibrate(120); } },
    sub(t) { $("#pin-sub").textContent = t; },
    busy: false,
  };
  const press = async (k) => {
    if (pad.busy || code.length >= 4) return;
    code += k; pad.draw(); $("#pin-err").textContent = "";
    if (code.length === 4) { pad.busy = true; try { await onCode(code, pad); } finally { pad.busy = false; } }
  };
  app.querySelectorAll("[data-k]").forEach((b) => (b.onclick = () => press(b.dataset.k)));
  $("#pin-del").onclick = () => { code = code.slice(0, -1); pad.draw(); };
  $("#pin-left").onclick = left.fn;
  document.onkeydown = (e) => { if (!$("#pin-dots")) { document.onkeydown = null; return; } if (/^\d$/.test(e.key)) press(e.key); if (e.key === "Backspace") $("#pin-del").click(); };
  return pad;
}

async function renderPinLogin(name) {
  pinPad({
    title: `Здравствуйте, ${name}!`, sub: "Введите код быстрого входа",
    left: { label: "Войти по паролю", fn: () => renderLogin(true) },
    onCode: async (code, pad) => {
      const r = await fetch("/api/pin/login", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device: deviceToken(), pin: code }) }).then((x) => x.json()).catch(() => ({ error: "Нет связи, попробуйте ещё раз" }));
      if (r.ok) { document.onkeydown = null; return boot(); }
      if (r.need_password) { toast(r.error, true); return renderLogin(true); }
      pad.reset(r.error || "Неверный код");
    },
  });
}

/* после входа по паролю: предложить придумать код (один раз на устройстве, можно пропустить) */
async function offerPin() {
  const dev = deviceToken(true);
  try { if (localStorage.getItem("crm.pin_skip") === "1") return; } catch (_) {}
  try { if ((await api("GET", "/api/pin?device=" + dev)).has_pin) return; } catch (_) { return; }
  await new Promise((resolve) => {
    let first = "";
    const done = () => { document.onkeydown = null; resolve(); };
    pinPad({
      title: "Быстрый вход", sub: "Придумайте код из 4 цифр. В следующий раз войдёте по нему, без пароля",
      left: { label: "Пропустить", fn: () => { try { localStorage.setItem("crm.pin_skip", "1"); } catch (_) {} done(); } },
      onCode: async (code, pad) => {
        if (!first) { first = code; pad.reset(); pad.sub("Повторите код"); return; }
        if (code !== first) { first = ""; pad.reset("Коды не совпали"); pad.sub("Придумайте код из 4 цифр"); return; }
        try { await api("POST", "/api/pin", { device: dev, pin: code }); toast("Готово! Теперь можно входить по коду"); done(); }
        catch (e) { first = ""; pad.reset(e.message); pad.sub("Придумайте код из 4 цифр"); }
      },
    });
  });
}

/* ---------- вход ---------- */
async function renderLogin(force = false) {
  try {
    if (sessionStorage.getItem("crm.force_password_login") === "1") {
      force = true; sessionStorage.removeItem("crm.force_password_login");
    }
  } catch (_) {}
  closeDrawer();
  if (!force && deviceToken()) {
    const h = await fetch("/api/pin/hello?device=" + encodeURIComponent(deviceToken())).then((r) => r.json()).catch(() => ({}));
    if (h && h.name) return renderPinLogin(h.name);
  }
  app.innerHTML = `
  <div class="login-wrap"><div class="login">
    <div class="login-mark" aria-hidden="true">⚽</div>
    <h1>Абонемент <span>Плюс</span></h1>
    <div class="login-sub">Вход для сотрудников школы</div>
    <form id="login-form">
      <div class="field"><label>Логин</label><input name="login" autocomplete="username" required></div>
      <div class="field"><label>Пароль</label><div class="pw-wrap"><input name="password" type="password" autocomplete="current-password" required>
        <button type="button" class="pw-eye" aria-label="Показать пароль" title="Показать пароль">👁</button></div></div>
      <button class="btn primary" style="width:100%">Войти</button>
      <div class="error" id="login-err"></div>
    </form>
    <div class="login-sub small" style="margin-top:14px"><a href="/legal" style="color:#7fe0ad">Документы и политика конфиденциальности</a></div>
  </div></div>`;
  $("#login-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try { await api("POST", "/api/login", { login: f.get("login"), password: f.get("password") }); await offerPin(); boot(); }
    catch (err) { $("#login-err").textContent = err.message; }
  };
}

/* ---------- каркас ---------- */
const VIEWS = [
  ["today", "Сегодня"], ["sales", "Рабочий стол"], ["inbox", "Чаты и звонки"], ["planner", "Планер"], ["members", "Действующие клиенты"], ["nodeadline", "Без дедлайнов"],
  ["journal", "Журнал"], ["schedule", "Расписание"], ["coaches", "Тренеры"], ["team", "Команда"], ["posts", "Посты в каналы"], ["payments", "Оплаты"], ["fiscal", "Чеки"], ["merch", "Мерч"], ["spend", "Расходы"], ["dashboard", "Дашборд"], ["search", "Клиенты и лиды"], ["docs", "Документы"], ["settings", "Настройки"],
];
const NAV_ICONS = {
  team: '<circle cx="12" cy="6" r="2.5"/><circle cx="5.5" cy="17.5" r="2.5"/><circle cx="18.5" cy="17.5" r="2.5"/><path d="M12 8.5v3.5M5.5 15v-3h13v3"/>',
  sales: '<path d="M4 5h16v14H4z"/><path d="M9 5v14M15 5v14"/>',
  today: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  schedule: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4M8 14h3M13 14h3M8 17h3"/>',
  inbox: '<path d="M4 6h16v10H8l-4 3z"/><path d="M8 10h8M8 13h5"/>',
  nodeadline: '<circle cx="12" cy="12" r="8"/><path d="M10 9v6M14 9v6"/>',
  members: '<circle cx="9" cy="8" r="3"/><path d="M3.5 19c.8-3 3-4.5 5.5-4.5s4.7 1.5 5.5 4.5"/><circle cx="17" cy="9" r="2.3"/><path d="M16 14.6c2.2.2 3.7 1.6 4.4 4.4"/>',
  skips: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4M10 14l4 4M14 14l-4 4"/>',
  journal: '<rect x="5" y="3.5" width="14" height="17" rx="2"/><path d="M9 9l1.5 1.5L13 8M9 14.5l1.5 1.5L13 13.5M15 9.5h1M15 15h1"/>',
  coaches: '<path d="M8 4h8v4a4 4 0 0 1-8 0z"/><path d="M8 5H5v1.5A3 3 0 0 0 8 9.5M16 5h3v1.5a3 3 0 0 1-3 3M12 12v4M9 20h6M10 16h4v4h-4z"/>',
  dashboard: '<path d="M4 19V10M10 19V5M16 19v-7M21 19H3"/>',
  manual: '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M8 8h8M8 12h8M8 16h5"/><path d="M16 15l1.5 1.5L20 14"/>',
  planner: '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M8 9l1.5 1.5L12 8M8 15l1.5 1.5L12 14M14 9.5h3M14 15.5h3"/>',
  spend: '<rect x="3" y="6" width="18" height="13" rx="2"/><path d="M3 10h18M16 15h2M7 3h10"/>',
  fiscal: '<path d="M6 3h12v18l-2-1.5L14 21l-2-1.5L10 21l-2-1.5L6 21z"/><path d="M9 8h6M9 11.5h6M9 15h3"/>',
  merch: '<path d="M8 4l-4 3 2 3 2-1v11h8V9l2 1 2-3-4-3c-.5 1.5-2 2.5-4 2.5S8.5 5.5 8 4z"/>',
  payments: '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M3 10h18M7 15h3"/>',
  search: '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
  docs: '<path d="M7 3h7l4 4v14H7z"/><path d="M14 3v4h4M10 11h5M10 14.5h5M10 18h3"/>',
  posts: '<path d="M4 10v4l3 .5V9.5zM7 9.5l10-4.5v14l-10-4.5"/><path d="M9 15l1 4h2.5l-1-3.6M19 10.5a2.5 2.5 0 0 1 0 3"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8"/>',
  more: '<circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/>',
};
const navIcon = (k) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${NAV_ICONS[k] || ""}</svg>`;

const isCoach = () => S.user && S.user.role === "coach";
/* права по разделам приходят с сервера (perms.py): own - своё, view - смотреть, full - всё */
const LEVELS = { own: 1, view: 2, full: 3 };
const can = (sec, lvl = "own") => (LEVELS[((S.user && S.user.perms) || {})[sec]] || 0) >= LEVELS[lvl];
const isOwner = () => can("settings", "full");
const visibleViews = () => VIEWS.filter(([k]) => can(k === "manual" ? "planner" : k === "nodeadline" ? "sales" : k) && (k !== "today" || !isOwner())
  && (k !== "manual" || !["coach", "trainee"].includes(S.user.role)))  // тренеру задачи приходят на «Сегодня» и в отчёт, отдельная вкладка не нужна  // руководитель смотрит стол тренера через «Как видит»
  .map(([k, t]) => [k, k === "today" && S.user.coach_nebo_only ? "Мой дашборд" : k === "coaches" && !can("coaches", "view") ? "Мои результаты" : k === "members" && !can("members", "full") ? "Мои ученики" : t]);
const SHORT = { today: "Сегодня", sales: "Стол", inbox: "Чаты", coaches: isCoachShort(), journal: "Журнал" };
function isCoachShort() { return "Результаты"; }
const badge = (k) => k === "inbox" ? `<span class="nbadge" data-badge hidden></span>` : "";

/* Боковое меню: главное сверху, остальное по папкам. Открыта папка с текущим разделом и те, что сотрудник раскрыл сам */
const NAV_TOP = ["today", "sales", "inbox", "planner"];
const NAV_FOLDERS = [["Клиенты", ["search", "members", "nodeadline"]], ["Занятия", ["journal", "schedule", "coaches"]],
  ["Деньги", ["payments", "fiscal", "merch", "spend", "dashboard"]], ["Школа", ["team", "posts", "docs", "settings"]]];
const navOpen = () => { try { return JSON.parse(localStorage.getItem("crm.nav_open") || "{}"); } catch (_) { return {}; } };
function sideNav(views) {
  const item = ([k, t]) => `<button data-view="${k}" class="${S.view === k ? "active" : ""}">${navIcon(k)}<span>${t}</span>${badge(k)}</button>`;
  if (views.length <= 8) return views.map(item).join("");
  const has = (k) => views.find(([v]) => v === k);
  const search = has("search") ? `<form id="client-search-form" role="search" autocomplete="off"><textarea class="nav-q" id="nav-q" role="searchbox" rows="1" name="crm-customer-query" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" enterkeyhint="search" data-lpignore="true" data-1p-ignore="true" data-bwignore="true" data-form-type="other" placeholder="🔍 Найти клиента   /" aria-label="Найти клиента" title="Имя, фамилия или телефон. Быстро открыть поиск: клавиша /"></textarea></form>` : "";
  const placed = new Set(["search"]);
  const top = NAV_TOP.filter(has).map((k) => { placed.add(k); return item(has(k)); }).join("");
  const o = navOpen();
  const folders = NAV_FOLDERS.map(([title, keys]) => {
    const list = keys.filter(has); list.forEach((k) => placed.add(k));
    if (!list.length) return "";
    if (list.length === 1) return item(has(list[0]));
    const open = o[title] || list.includes(S.view);
    return `<div class="nav-fold ${open ? "open" : ""}"><button class="nav-fold-h" data-fold="${title}" aria-expanded="${!!open}" aria-controls="nav-folder-${keys[0]}"><span class="nav-arrow">▸</span><span>${title}</span></button>
      <div class="nav-fold-b" id="nav-folder-${keys[0]}">${list.map((k) => item(has(k))).join("")}</div></div>`;
  }).join("");
  const rest = views.filter(([k]) => !placed.has(k)).map(item).join("");
  return search + `<div class="nav-main">${top}</div>` + folders + rest;
}

/* Поиск в меню: клиенты появляются прямо под полем, пока печатаешь. ↑↓ выбрать, Enter открыть, Esc закрыть */
function wireNavSearch(nq) {
  const box = document.createElement("div");
  box.className = "nav-res"; box.hidden = true;
  nq.after(box);
  let t, rows = [], cur = -1, historyMode = false, generation = 0;
  const close = () => { box.hidden = true; cur = -1; };
  const paint = () => {
    box.innerHTML = (historyMode ? `<div class="nav-recent-head"><b>Недавно открывали</b>${rows.length ? `<button type="button" data-clear-recent>Очистить</button>` : ""}</div>` : "") + (rows.length ? rows.slice(0, 8).map((r, i) => `<button class="${i === cur ? "on" : ""}" data-nr="${r.id}"><b>${esc(r.name)}</b>
      <small>${esc([r.parent, r.phone].filter(Boolean).join(" · "))}${r.funnel === "members" ? "" : " · лид"}</small></button>`).join("")
      + (rows.length > 8 ? `<button data-nall><small>Все результаты: ${rows.length} →</small></button>` : "")
      : `<div class="small" style="padding:8px 10px;opacity:.7">${historyMode ? "Здесь появятся последние карточки клиентов" : "Никого не нашли"}</div>`);
    box.style.top = nq.offsetTop + nq.offsetHeight + 4 + "px";
    box.hidden = false;
    box.querySelectorAll("[data-nr]").forEach((b) => (b.onmousedown = (e) => { e.preventDefault(); nq.value = ""; close(); openStudent(+b.dataset.nr); }));
    const clear = box.querySelector("[data-clear-recent]");
    if (clear) {
      clear.onmousedown = e => e.preventDefault();
      clear.onclick = async () => {
        try { await api("DELETE", "/api/students/recent"); if (!nq.value.trim()) { rows = []; cur = -1; paint(); } }
        catch (err) { fail(err); }
      };
    }
    const all = box.querySelector("[data-nall]"); if (all) all.onmousedown = (e) => { e.preventDefault(); goAll(); };
  };
  const goAll = () => { S.search = nq.value.trim(); nq.value = ""; close(); go("search"); setTimeout(() => { const q = $("#q"); if (q) { q.focus(); q.dispatchEvent(new Event("input")); } }, 50); };
  const showHistory = async () => {
    const request = ++generation;
    try {
      const recent = await api("GET", "/api/students/recent");
      if (request !== generation || nq.value.trim() || document.activeElement !== nq) return;
      rows = recent; historyMode = true; cur = rows.length ? 0 : -1; paint();
    } catch (_) { close(); }
  };
  nq.addEventListener("focus", () => { if (!nq.value.trim()) showHistory(); });
  nq.oninput = () => {
    clearTimeout(t); generation++;
    nq.value = nq.value.replace(/[\r\n]+/g, " ");
    const v = nq.value.trim();
    if (!v) return showHistory();
    historyMode = false;
    if (v.length < 2) return close();
    t = setTimeout(async () => { try { rows = await api("GET", "/api/students?q=" + encodeURIComponent(v)); cur = rows.length ? 0 : -1; if (nq.value.trim() === v && document.activeElement === nq) paint(); } catch (_) {} }, 200);
  };
  nq.onkeydown = (e) => {
    if (e.key === "Escape") { nq.value = ""; close(); nq.blur(); }
    else if (e.key === "ArrowDown" && !box.hidden) { e.preventDefault(); cur = Math.min(cur + 1, Math.min(rows.length, 8) - 1); paint(); }
    else if (e.key === "ArrowUp" && !box.hidden) { e.preventDefault(); cur = Math.max(cur - 1, 0); paint(); }
    else if (e.key === "Enter") { e.preventDefault(); if (!box.hidden && rows[cur]) { const id = rows[cur].id; nq.value = ""; close(); openStudent(id); } else goAll(); }
  };
  nq.onblur = () => setTimeout(close, 150);
}

/* Горячие клавиши на компьютере: «/» поиск клиента, «N» новый клиент (работают и в русской раскладке) */
document.addEventListener("keydown", (e) => {
  if (e.ctrlKey || e.metaKey || e.altKey || !S.user) return;
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
  if (document.querySelector(".tour") || !$("#drawer") || !$("#drawer").hidden) return;
  if (e.code === "Slash" || e.key === "/") {
    const nq = $("#nav-q");
    if (nq && nq.offsetParent) { e.preventDefault(); nq.focus(); }
    else if (can("search")) { e.preventDefault(); go("search"); setTimeout(() => $("#q") && $("#q").focus(), 80); }
  } else if (e.code === "KeyN" && $("#new-client")) { e.preventDefault(); openNewClient(); }
});

/* Праздничное оформление по календарю: цвета, значок в меню, украшения в углу и поздравление на столе.
   Выключается в Настройки → Мой профиль (только руководитель, для всех сразу) */
const FESTIVE = [
  { key: "halloween", from: "10-25", to: "11-01", icon: "🎃", deco: "🦇 🎃 👻", title: "С Хэллоуином!",
    text: "Пусть сегодня на тренировках будут только сладости, а гадости пусть достанутся соперникам 🎃⚽" },
  { key: "newyear", from: "12-15", to: "01-10", icon: "🎄", deco: "❄️ 🎄 ✨", title: "С наступающим Новым годом!",
    text: "Пусть в новом году будет полный зал, все продлятся, а мяч всегда летит в девятку 🎄⚽" },
  { key: "feb23", from: "02-22", to: "02-23", icon: "🎖", deco: "⭐ 🎖", title: "С Днём защитника Отечества!",
    text: "Защитникам на поле и в жизни: крепкой обороны и быстрых контратак ⭐" },
  { key: "mar8", from: "03-07", to: "03-08", icon: "🌷", deco: "🌷 💐 🌸", title: "С 8 Марта!",
    text: "Девушки нашей команды, вы лучшие игроки в составе 💐" },
  { key: "june1", from: "06-01", to: "06-01", icon: "🎈", deco: "🎈 🎉", title: "С Днём защиты детей!",
    text: "Сегодня главный праздник наших футболистов 🎈⚽" },
  { key: "sep1", from: "09-01", to: "09-01", icon: "🍁", deco: "🍁 📚 ⚽", title: "С новым сезоном!",
    text: "1 сентября: новые группы, новые дети, новые голы 🍁" },
];
function festiveNow(today) {
  const md = today.slice(5, 10);
  return FESTIVE.find((f) => (f.from <= f.to ? md >= f.from && md <= f.to : md >= f.from || md <= f.to)) || null;
}
function applyFestive() {
  const f = S.meta && S.meta.festive !== false ? festiveNow(S.meta.today) : null;
  document.documentElement.dataset.festive = f ? f.key : "";
  document.querySelectorAll(".festive-deco").forEach((x) => x.remove());
  if (!f) return;
  const brand = document.querySelector(".brand i"); if (brand) brand.textContent = f.icon;
  const d = document.createElement("div"); d.className = "festive-deco"; d.setAttribute("aria-hidden", "true"); d.textContent = f.deco;
  document.body.appendChild(d);
}
function festiveBanner(page) {
  const f = S.meta && S.meta.festive !== false ? festiveNow(S.meta.today) : null;
  if (!f || !page) return;
  const k = `crm.festive.${f.key}.${S.meta.today}`;
  try { if (localStorage.getItem(k)) return; } catch (_) {}
  const head = page.querySelector(".page-head"); if (!head) return;
  head.insertAdjacentHTML("afterend", `<div class="festive-banner"><span class="festive-ic">${f.icon}</span><div class="grow"><b>${esc(f.title)}</b><div class="small">${esc(f.text)}</div></div><button class="icon-btn" data-fclose title="Скрыть до завтра">✕</button></div>`);
  page.querySelector("[data-fclose]").onclick = (e) => { try { localStorage.setItem(k, "1"); } catch (_) {} e.target.closest(".festive-banner").remove(); };
}

function shell() {
  const views = visibleViews();
  if (S.view !== "meet" && !views.some(([k]) => k === S.view)) S.view = views[0][0];
  document.body.classList.toggle("no-copy", !can("export"));
  const tabs = views.length <= 5 ? views : [...views.slice(0, 4), ["more", "Ещё"]];
  app.innerHTML = `
  <header class="topbar">
    <div class="brand"><i aria-hidden="true">⚽</i><div>Абонемент <span>Плюс</span></div></div>
    <nav class="nav">${sideNav(views)}</nav>
    ${can("members", "full") ? `<button class="btn" id="new-client">+ Клиент</button>` : ""}
    <div class="sidebar-account">
      <div class="sidebar-account-text"><b>${esc(S.user.name)}</b><small>${esc(S.user.title || "")}</small></div>
      <button type="button" class="btn sm" data-account-logout aria-label="Выйти из аккаунта">Выйти</button>
    </div>
  </header>
  <main class="page" id="page"></main>
  <nav class="tabbar">${tabs.map(([k, t]) => `<button data-${k === "more" ? "more" : "view"}="${k}" class="${S.view === k ? "active" : ""}">${navIcon(k)}<span>${SHORT[k] || t}</span>${badge(k)}</button>`).join("")}</nav>
  <div class="more-sheet" hidden><div class="sheet">${views.slice(4).map(([k, t]) => `<button data-view="${k}" class="${S.view === k ? "active" : ""}">${navIcon(k)}${t}</button>`).join("")}</div></div>`;
  app.querySelectorAll("[data-account-logout]").forEach(b => b.onclick = logoutCurrentAccount);
  app.querySelectorAll("[data-view]").forEach((b) => (b.onclick = () => { $(".more-sheet").hidden = true; go(b.dataset.view); }));
  app.querySelectorAll("[data-fold]").forEach((b) => (b.onclick = () => {
    const f = b.closest(".nav-fold"), open = f.classList.toggle("open");
    b.setAttribute("aria-expanded", String(open));
    try { const o = navOpen(); o[b.dataset.fold] = open; localStorage.setItem("crm.nav_open", JSON.stringify(o)); } catch (_) {}
  }));
  const nq = $("#nav-q");
  if (nq) {
    $("#client-search-form").onsubmit = e => e.preventDefault();
    wireNavSearch(nq);
  }
  const more = $("[data-more]");
  if (more) more.onclick = () => { $(".more-sheet").hidden = !$(".more-sheet").hidden; };
  $(".more-sheet").onclick = (e) => { if (e.target.classList.contains("more-sheet")) e.currentTarget.hidden = true; };
  if ($("#new-client")) $("#new-client").onclick = openNewClient;
}

/* ---------- навигация «назад»: каждый переход пишется в историю браузера ---------- */
const navSnap = () => JSON.parse(JSON.stringify({ meet: S.meet, journal: S.journal, sched: S.sched, planner: S.planner, spend: S.spend, fx: S.fx,
  inbox: S.inbox && { tab: S.inbox.tab, filter: S.inbox.filter } }));
function navRestore(sub) {
  if (!sub) return;
  if (sub.journal) S.journal = sub.journal;
  if (sub.meet) S.meet = sub.meet;
  if (sub.sched) S.sched = sub.sched;
  if (sub.planner) S.planner = sub.planner;
  if (sub.spend) S.spend = sub.spend;
  if (sub.fx) S.fx = sub.fx;
  if (sub.inbox && S.inbox) Object.assign(S.inbox, sub.inbox);
}
const navDepth = () => (history.state && history.state.depth) || 0;
function pushNav(menu = false) {
  try { history.pushState({ view: S.view, sub: navSnap(), depth: navDepth() + 1, menu }, "", "#" + S.view); } catch (_) {}
}
function step(fn) { fn(); pushNav(); window.scrollTo(0, 0); render(); }
let drawerInHistory = false, skipPop = false;
const mayRestoreStudent = card => card && card.userId === S.user?.id && !S.meta?.staff_docs_pending &&
  ["admin", "operator"].includes(S.user?.role) && ["members", "sales", "search", "inbox"].some(section => can(section));
const forgetStudentCard = () => {
  if (!history.state?.studentCard) return;
  const state = { ...history.state }; delete state.studentCard;
  try { history.replaceState(state, ""); } catch (_) {}
};
window.addEventListener("popstate", (e) => {
  if (skipPop) { skipPop = false; return; }
  const st = e.state;
  if (drawerInHistory) {
    drawerInHistory = false; hideDrawer();
    if (!st?.studentCard) return;
  }
  if (!st || !st.view) return;
  navRestore(st.sub);
  go(st.view, true);
  if (st.drawer && mayRestoreStudent(st.studentCard)) {
    drawerInHistory = true;
    openStudent(st.studentCard.id, { tab: st.studentCard.tab, restoring: true });
  }
});

function go(view, fromHistory = false) {
  if (view === "settings" && !fromHistory) S.setTab = null;  // из меню: сначала плитки
  S.view = view;
  if (!fromHistory) pushNav(true);  // переход из меню: кнопку «Назад» не показываем
  try { localStorage.setItem("crm.view", view); } catch (_) {}
  app.querySelectorAll("[data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  app.querySelectorAll(".nav-fold").forEach((f) => { if (f.querySelector(`[data-view="${view}"]`)) { f.classList.add("open"); f.querySelector(".nav-fold-h").setAttribute("aria-expanded", "true"); } });
  const more = $("[data-more]");
  if (more) more.classList.toggle("active", !visibleViews().slice(0, 4).some(([k]) => k === view));
  window.scrollTo(0, 0);
  render();
}

async function render() {
  const page = $("#page");
  if (!page) return;
  page.classList.remove("readonly");
  if (S.meta && S.meta.staff_docs_pending) return renderStaffDocs(page);
  await renderView(page);
  if (["sales", "today"].includes(S.view) && !S.viewAs) festiveBanner(page);
  if (S.meta && S.meta.tour_pending && !S.tourOn) startTour();
  if (navDepth() > 0 && !(history.state && history.state.menu) && !page.querySelector(".back-btn")) {
    page.insertAdjacentHTML("afterbegin", `<button class="back-btn" type="button">← Назад</button>`);
    page.querySelector(".back-btn").onclick = () => history.back();
  }
}

async function renderView(page) {
  try {
    if (S.view === "sales") return await renderWorkdesk(page);
    if (S.view === "today") return await renderCoachToday(page);
    if (S.view === "payments") return await renderPayments(page);
    if (S.view === "merch") return await renderMerch(page);
    if (S.view === "planner") return await renderPlanner(page);
    // «Пропуски» и «Ручные задачи» теперь на рабочем столе, отдельных разделов нет (Юлия, 04.11)
    if (["manual", "skips"].includes(S.view)) { S.view = "sales"; return render(); }
    if (S.view === "spend") return await renderSpend(page);
    if (S.view === "fiscal") return await renderFiscal(page);
    if (S.view === "schedule") return await renderSchedule(page);
    if (S.view === "members" && !can("members", "full")) return await renderCoachKids(page);
    if (["members", "skips"].includes(S.view)) return await renderBoard(page, S.view);
    if (S.view === "nodeadline") return await renderNoDeadline(page);
    if (S.view === "dashboard") return await renderDashboard(page);
    if (S.view === "inbox") return await renderInbox(page);
    if (S.view === "coaches") return await renderCoaches(page);
    if (S.view === "journal") return await renderJournal(page);
    if (S.view === "meet") return await renderMeet(page);
    if (S.view === "team") return await renderTeam(page);
    if (S.view === "posts") return await renderPosts(page);
    if (S.view === "search") return await renderSearch(page);
    if (S.view === "settings") return await renderSettings(page);
    if (S.view === "docs") return await renderDocs(page);
  } catch (e) {
    // часть файлов CRM не догрузилась (например, открыли страницу, пока сервер перезапускался): один раз перезагружаем сами
    if (e instanceof ReferenceError) {
      let again = false;
      try { again = sessionStorage.getItem("crm.reloaded") === "1"; sessionStorage.setItem("crm.reloaded", "1"); } catch (_) {}
      if (!again) { location.reload(); return; }
      page.innerHTML = `<div class="error">CRM загрузилась не полностью. Обновите страницу: потяните её вниз или нажмите ⟳ в адресной строке</div>`;
      return;
    }
    if (e.message !== "Нужно войти") page.innerHTML = `<div class="error">${esc(e.message)}</div>`;
  }
}

/* ---------- дашборд ---------- */
const fmtNum = (n) => Number(n || 0).toLocaleString("ru-RU");
const ICONS = {
  wallet: '<path d="M3 7h15a3 3 0 0 1 3 3v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Zm0 0 12-3v3"/><circle cx="17" cy="14" r="1.2"/>',
  cart: '<path d="M3 4h2l2.5 11h10L20 8H6.2"/><circle cx="9" cy="19" r="1.4"/><circle cx="17" cy="19" r="1.4"/>',
  calc: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h3M9.5 6.5v3M13 8h3M8 15l3 3M11 15l-3 3M13 15.5h3M13 17.5h3"/>',
  pie: '<path d="M12 3v9h9A9 9 0 1 1 12 3Z"/><path d="M15 3.5A9 9 0 0 1 20.5 9H15V3.5Z"/>',
  trend: '<path d="M3 17l5-5 4 3 7-7"/><circle cx="3" cy="17" r="1.2"/><circle cx="8" cy="12" r="1.2"/><circle cx="12" cy="15" r="1.2"/><circle cx="19" cy="8" r="1.2"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1-4 4-6 8-6s7 2 8 6"/>',
};
const icon = (k) => `<svg class="kpi-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${ICONS[k]}</svg>`;
const delta = (v, label) => v === null || v === undefined
  ? `<div class="delta"><span>${label}</span><b class="muted">—</b></div>`
  : `<div class="delta"><span>${label}</span><b class="${v >= 0 ? "up" : "down"}">${v >= 0 ? "▲" : "▼"} ${v}%</b></div>`;

function kpiCard(ic, title, value, k, extra = "") {
  return `<div class="kpi"><div class="kpi-head">${icon(ic)}<span>${esc(title)}</span></div>
    <div class="kpi-body"><div class="kpi-value">${value}</div>
    ${k ? `<div class="kpi-deltas">${delta(k.vs_month, "мес. назад")}${k.vs_year !== undefined ? delta(k.vs_year, "год назад") : ""}</div>` : ""}</div>${extra}</div>`;
}

function lineChart(labels, cur, prev, fmt) {
  const W = 640, H = 230, L = 58, R = 12, T = 14, B = 44;
  const vals = [...cur, ...prev].filter((v) => v !== null && v !== undefined);
  let lo = Math.min(0, ...vals), hi = Math.max(1, ...vals);
  const step = Math.pow(10, Math.floor(Math.log10(hi - lo || 1))) / 2;
  hi = Math.ceil(hi / step) * step;
  const x = (i) => L + (i * (W - L - R)) / (labels.length - 1);
  const y = (v) => T + (H - T - B) * (1 - (v - lo) / (hi - lo || 1));
  const segs = (arr) => { const out = []; let cur = null; arr.forEach((v, i) => { if (v === null || v === undefined) { cur = null; return; } if (!cur) out.push(cur = []); cur.push(i); }); return out; };
  const line = (arr) => segs(arr).map((g) => g.map((i, n) => `${n ? "L" : "M"}${x(i).toFixed(1)},${y(arr[i]).toFixed(1)}`).join("")).join("");
  const area = (arr) => segs(arr).map((g) => g.map((i, n) => `${n ? "L" : "M"}${x(i).toFixed(1)},${y(arr[i]).toFixed(1)}`).join("") + `L${x(g.at(-1))},${y(lo)}L${x(g[0])},${y(lo)}Z`).join("");
  const ticks = [0, 1, 2, 3, 4].map((k) => lo + ((hi - lo) * k) / 4);
  return `<div class="chart-legend"><span class="lg cur"></span>Последние 12 месяцев <span class="lg prev"></span>Год назад</div>
  <svg viewBox="0 0 ${W} ${H}" class="line-chart" role="img">
    ${ticks.map((t) => `<line x1="${L}" x2="${W - R}" y1="${y(t)}" y2="${y(t)}" class="grid"/><text x="${L - 8}" y="${y(t) + 4}" text-anchor="end" class="tick">${fmt(t)}</text>`).join("")}
    ${labels.map((l, i) => `<text x="${x(i)}" y="${H - B + 18}" text-anchor="end" transform="rotate(-30 ${x(i)} ${H - B + 18})" class="tick">${esc(l)}</text>`).join("")}
    <path d="${area(prev)}" class="area prev"/><path d="${line(prev)}" class="ln prev"/>
    <path d="${area(cur)}" class="area cur"/><path d="${line(cur)}" class="ln cur"/>
    ${cur.map((v, i) => v === null || v === undefined ? "" : `<circle cx="${x(i)}" cy="${y(v)}" r="3.2" class="pt cur"><title>${esc(labels[i])}: ${fmt(v)}</title></circle>`).join("")}
    ${prev.map((v, i) => v === null || v === undefined ? "" : `<circle cx="${x(i)}" cy="${y(v)}" r="2.6" class="pt prev"><title>${esc(labels[i])}, прошлый год: ${fmt(v)}</title></circle>`).join("")}
  </svg>`;
}

function funnelChart(rows) {
  const top = Math.max(1, rows[0][1]);
  const colors = ["#8cc9a4", "#4fa576", "#2a8a58", "#1b6b42"];
  return `<div class="funnel2">${rows.map(([l, v], i) => {
    const w = Math.max(6, Math.round((v / top) * 100));
    const p = i === 0 ? 100 : Math.round((v / Math.max(1, rows[i - 1][1])) * 100);
    return `<div class="fn-row"><div class="fn-track"><div class="fn-bar" style="width:${w}%;background:${colors[i % 4]}"></div></div>
      <div class="fn-label">${esc(l)}: <b>${rows[i - 1] && !rows[i - 1][1] ? "—" : p + "%"}</b> (${v})</div></div>`;
  }).join("")}</div>`;
}

function donut(parts) {
  const total = parts.reduce((a, p) => a + p.v, 0);
  const C = 2 * Math.PI * 40;
  let off = 0;
  const arcs = total ? parts.map((p) => { const len = (p.v / total) * C; const a = `<circle r="40" cx="60" cy="60" fill="none" stroke="${p.c}" stroke-width="22" stroke-dasharray="${len} ${C - len}" stroke-dashoffset="${-off}" transform="rotate(-90 60 60)"><title>${esc(p.l)}</title></circle>`; off += len; return a; }).join("") : `<circle r="40" cx="60" cy="60" fill="none" stroke="var(--surface-2)" stroke-width="22"/>`;
  return `<div class="donut-wrap"><svg viewBox="0 0 120 120" class="donut">${arcs}</svg>
    <ul class="donut-legend">${parts.map((p) => `<li><span style="background:${p.c}"></span>${esc(p.l)}: ${total ? Math.round((p.v / total) * 100) : 0}% (${p.fmt ? p.fmt(p.v) : p.v})</li>`).join("")}</ul></div>`;
}

async function renderDashboard(page) {
  if (!S.dashMonth) S.dashMonth = S.meta.today.slice(0, 7);
  if (S.dashBranch === undefined) S.dashBranch = "";
  if (!S.dashSeg) S.dashSeg = "count";
  if (S.dashTab && S.dashTab !== "overview") {
    const ms = [];
    for (let i = 0; i < 18; i++) { const dt = new Date(S.meta.today); dt.setDate(1); dt.setMonth(dt.getMonth() - i); ms.push(isoDate(dt).slice(0, 7)); }
    page.innerHTML = `<div class="dash-filters">
      <select id="dash-month">${ms.map((x) => `<option value="${x}" ${x === S.dashMonth ? "selected" : ""}>${monthLabel(x)}</option>`).join("")}</select>
      ${S.dashTab === "sources" || S.dashTab === "program" ? "" : `<select id="dash-branch"><option value="">Все филиалы</option>${S.meta.branches.map((b) => `<option value="${b.id}" ${String(b.id) === String(S.dashBranch) ? "selected" : ""}>${esc(b.name)}</option>`).join("")}</select>`}
      </div>${anTabsHtml()}<div id="an-box"></div>`;
    $("#dash-month").onchange = (e) => { S.dashMonth = e.target.value; render(); };
    if ($("#dash-branch")) $("#dash-branch").onchange = (e) => { S.dashBranch = e.target.value; render(); };
    wireAnTabs(page);
    return renderAnalytics($("#an-box"));
  }
  const d = await api("GET", `/api/dashboard?month=${S.dashMonth}${S.dashBranch ? "&branch_id=" + S.dashBranch : ""}`);
  const months = [];
  for (let i = 0; i < 18; i++) { const dt = new Date(S.meta.today); dt.setDate(1); dt.setMonth(dt.getMonth() - i); months.push(isoDate(dt).slice(0, 7)); }
  const k = d.kpi, sr = d.series;
  const kRub = (t) => t >= 1e6 ? (t / 1e6).toFixed(1).replace(".0", "") + " млн" : t >= 1000 ? Math.round(t / 1000) + " тыс" : fmtNum(t);
  const segColors = { new: "#8cc9a4", regular: "#1f7a4d", returned: "#d9a441" };
  const seg = d.segments[S.dashSeg];
  page.innerHTML = `
    <div class="dash-filters">
      <select id="dash-month">${months.map((x) => `<option value="${x}" ${x === S.dashMonth ? "selected" : ""}>${monthLabel(x)}</option>`).join("")}</select>
      <select id="dash-branch"><option value="">Все филиалы</option>${S.meta.branches.map((b) => `<option value="${b.id}" ${String(b.id) === String(S.dashBranch) ? "selected" : ""}>${esc(b.name)}</option>`).join("")}</select>
    </div>
    ${anTabsHtml()}

    <div class="kpis">
      ${kpiCard("wallet", "Выручка", `${fmtNum(k.revenue.value)} <small>₽</small>`, k.revenue)}
      ${kpiCard("cart", "Уникальных держателей абонементов", fmtNum(k.holders.value), k.holders, `<div class="kpi-note">ребёнок с абонементами в две группы считается один раз</div>`)}
      ${kpiCard("calc", "Средний чек", `${fmtNum(k.avg.value)} <small>₽</small>`, k.avg, `<div class="kpi-note">средняя сумма одного абонемента</div>`)}
      ${kpiCard("pie", "Заполненность", `<span class="fill2"><span><em>по абонементам</em>${k.fill.subs ?? "—"} <small>%</small></span><span><em>по уникальным</em>${k.fill.value ?? "—"} <small>%</small></span></span>`, { vs_month: k.fill.vs_month }, `<div class="kpi-note">${k.fill.subs_count} абонементов и ${k.holders.value} учеников на ${k.seats} мест</div>`)}
      ${kpiCard("trend", "Конверсия из месяца в месяц", `${k.conversion.value ?? "—"} <small>%</small>`, k.conversion, `<div class="kpi-note">продлили из тех, кто занимался в прошлом месяце</div>`)}
      ${kpiCard("user", "Количество мест", `${fmtNum(k.seats)} <small>шт.</small>`, null, `<div class="kpi-note">сумма мест во всех группах</div>`)}
    </div>

    <div class="dash-row2">
      <div class="panel">
        <table class="day-table"><thead><tr><th></th><th>За вчера</th><th>За сегодня</th></tr></thead><tbody>
          <tr><td>Получено лидов</td><td>${d.days.yesterday.leads} шт.</td><td>${d.days.today.leads} шт.</td></tr>
          <tr><td>Записаны на пробное</td><td>${d.days.yesterday.trial_booked} шт.</td><td>${d.days.today.trial_booked} шт.</td></tr>
          <tr><td>Купили впервые</td><td>${d.days.yesterday.bought_first} шт.</td><td>${d.days.today.bought_first} шт.</td></tr>
        </tbody></table>
      </div>
      <div class="panel center">
        <div class="kpi-head" style="justify-content:center"><span>Записи на пробное на будущие дни</span></div>
        <div class="kpi-value" style="margin-top:8px">${d.future_trials} <small>шт.</small></div>
        <div class="kpi-note">Отработки: можно отработать ${d.makeups.can_make_up}${d.makeups.at_risk_rub ? `, ≈ ${rub(d.makeups.at_risk_rub)} могут уйти в перерасчёт` : ""}</div>
      </div>
    </div>

    <div class="chart-grid">
      <section class="panel"><div class="panel-head">Выручка по месяцам</div>${lineChart(d.months, sr.revenue.cur, sr.revenue.prev, kRub)}</section>
      <section class="panel"><div class="panel-head">Количество уникальных держателей абонементов</div>${lineChart(d.months, sr.holders.cur, sr.holders.prev, fmtNum)}</section>
      <section class="panel"><div class="panel-head">Средний чек по месяцам</div>${lineChart(d.months, sr.avg.cur, sr.avg.prev, fmtNum)}</section>
      <section class="panel"><div class="panel-head">Конверсия из месяца в месяц, %</div>${lineChart(d.months, sr.conversion.cur, sr.conversion.prev, (t) => Math.round(t))}</section>
      <section class="panel"><div class="panel-head">Воронка продаж по лидам · ${esc(d.month_label)}</div>${funnelChart(d.funnel_leads)}</section>
      <section class="panel"><div class="panel-head">Воронка продаж по записанным на пробное · ${esc(d.month_label)}</div>${funnelChart(d.funnel_trials)}</section>
      <section class="panel">
        <div class="panel-tabs"><button data-seg="count" class="${S.dashSeg === "count" ? "active" : ""}">Абонементы</button><button data-seg="revenue" class="${S.dashSeg === "revenue" ? "active" : ""}">Выручка</button></div>
        ${donut([
          { l: "Новые посетители", v: seg.new, c: segColors.new, fmt: S.dashSeg === "revenue" ? rub : null },
          { l: "Постоянные посетители", v: seg.regular, c: segColors.regular, fmt: S.dashSeg === "revenue" ? rub : null },
          { l: "Вернувшиеся посетители", v: seg.returned, c: segColors.returned, fmt: S.dashSeg === "revenue" ? rub : null },
        ])}
      </section>
    </div>`;
  $("#dash-month").onchange = (e) => { S.dashMonth = e.target.value; render(); };
  $("#dash-branch").onchange = (e) => { S.dashBranch = e.target.value; render(); };
  page.querySelectorAll("[data-seg]").forEach((b) => (b.onclick = () => { S.dashSeg = b.dataset.seg; render(); }));
  wireAnTabs(page);
}

/* ---------- воронки ---------- */
const BOARD_TITLES = { sales: "Воронка продаж", members: "Действующие клиенты", skips: "Пропуски и отработки" };

function boardHtml(data) {
  let hidden = 0, later = 0;
  if (data.funnel === "sales" && data.today_end) {
    data = { ...data, columns: data.columns.map((c) => {
      const cards = S.boardAll ? c.cards : c.cards.filter((k) => !k.due || k.due <= data.today_end);
      hidden += c.cards.length - cards.length;
      if (c.key === "later") later = c.cards.length;
      return { ...c, cards };
    }).filter((c) => S.boardAll || c.key !== "later" || c.cards.length) };
  }
  const bar = data.funnel === "sales" && data.today_end ? `<div class="board-bar small">
      ${S.boardAll ? "Показаны все карточки" : "Показаны задачи на сегодня"}${!S.boardAll && hidden ? ` · отложено на другие дни: <b>${hidden}</b>` : ""}${later ? ` · «Пригласим позже»: ${later}` : ""}
      <button class="linkish small" data-boardall>${S.boardAll ? "Только на сегодня" : "Показать все"}</button></div>` : "";
  return `${bar}<div class="board">${data.columns.map((c) => `
      <section class="col" data-stage="${c.key}">
        <div class="col-head"><h3>${esc(c.title)}</h3>${c.auto ? `<span class="auto">сам</span>` : ""}<span class="count">${c.cards.length}</span></div>
        <div class="col-body">${c.cards.length ? c.cards.map((k) => cardHtml(k, data.draggable && !c.auto)).join("") : `<div class="empty">Пусто</div>`}</div>
      </section>`).join("")}
    </div>`;
}

function wireBoard(page, funnel, data) {
  page.querySelectorAll("[data-boardall]").forEach((b) => (b.onclick = () => { S.boardAll = !S.boardAll; render(); }));
  page.querySelectorAll(".card").forEach((el) => (el.onclick = () => openStudent(+el.dataset.id)));
  if (data.draggable) enableDrag(page, funnel, data.columns);
}

async function renderBoard(page, funnel) {
  const data = await api("GET", "/api/board/" + funnel);
  if (funnel === "members") return renderMembersList(page, data);
  page.innerHTML = `
    <div class="page-head"><h2 class="grow">${BOARD_TITLES[funnel]}</h2>
      ${funnel === "members" && can("members", "full") ? `<button class="btn sm" id="arch-btn">🗄 Архив ▾</button>` : ""}
</div>
    <div class="section arch-box" id="arch-box" hidden></div>
    ${data.note ? `<div class="note">${esc(data.note)}</div>` : ""}
    ${boardHtml(data)}`;
  if ($("#arch-btn")) membersArchive(page);
  wireBoard(page, funnel, data);
}

/* «Ученики»: список с поиском, фильтрами и группировкой по филиалам и группам */
S.mf = (() => { try { return JSON.parse(localStorage.getItem("crm.mf") || "null") || {}; } catch (_) { return {}; } })();
const M_STATUS = [["", "Все"], ["single", "Разовые"], ["unpaid_cur", "Не оплатили"], ["renew", "Продлить"], ["invoiced", "Счёт выставлен. Ждём"], ["paid", "Оплачено"], ["left", "Ушли"]];
function renderMembersList(page, data) {
  const cols = Object.fromEntries(data.columns.map((c) => [c.key, c.title]));
  const all = data.columns.flatMap((c) => c.cards);
  const f = S.mf;
  const save = () => { try { localStorage.setItem("crm.mf", JSON.stringify(f)); } catch (_) {} };
  const branches = [...new Set(all.flatMap((k) => k.groups.map((g) => g.branch)))].sort();
  const groups = [...new Map(all.flatMap((k) => k.groups).filter((g) => !f.branch || g.branch === f.branch).map((g) => [String(g.id), g])).values()]
    .sort((a, b) => (a.branch + a.name).localeCompare(b.branch + b.name));
  if (f.group && !groups.some((g) => String(g.id) === f.group)) f.group = "";
  const inPlace = (k) => (!f.branch || k.groups.some((g) => g.branch === f.branch)) && (!f.group || k.groups.some((g) => String(g.id) === f.group));
  const q = (f.q || "").toLowerCase(), qd = q.replace(/\D/g, "");
  const hit = (k) => !q || (k.name + " " + k.parent + " " + k.family).toLowerCase().includes(q) || (qd.length > 2 && (k.phone || "").replace(/\D/g, "").includes(qd));
  const base = all.filter((k) => inPlace(k) && hit(k));
  const cnt = (s) => base.filter((k) => (s ? k.status === s : k.status !== "left")).length;
  const rows = base.filter((k) => (f.status ? k.status === f.status : k.status !== "left"));
  const pill = { single: "", unpaid_cur: "red", renew: "", invoiced: "", paid: "green", left: "" };
  const row = (k, g) => `<div class="m-row ${k.color === "red" ? "m-red" : k.color === "yellow" ? "m-yellow" : ""}" data-open="${k.id}" role="button" tabindex="0">
    <span class="m-name"><b>${esc(k.name)}</b></span>
    <span class="small muted m-parent">${esc(k.parent)}${k.phone ? ", " + esc(k.phone) : ""}</span>
    ${g ? "" : `<span class="small m-group">${esc(k.groups.map((x) => x.branch + " · " + x.name).join(", ") || k.group)}</span>`}
    <span class="m-st"><span class="pill ${pill[k.status]}">${esc(k.badge || cols[k.status] || "")}</span></span></div>`;
  let body;
  if (!rows.length) body = `<div class="muted small" style="padding:12px">Никого не нашли</div>`;
  else if (f.grouped) {
    const by = new Map();
    for (const k of rows) for (const g of (k.groups.length ? k.groups : [{ id: 0, name: "Без группы", branch: "" }])) {
      if (f.group && String(g.id) !== f.group) continue;
      if (f.branch && g.branch !== f.branch) continue;
      const key = g.branch + "|" + g.name;
      if (!by.has(key)) by.set(key, { g, list: [] });
      by.get(key).list.push(k);
    }
    let lastBr = null;
    body = [...by.values()].sort((a, b) => (a.g.branch + a.g.name).localeCompare(b.g.branch + b.g.name)).map(({ g, list }) => {
      const brHead = g.branch !== lastBr ? `<h3 class="m-branch">${esc(g.branch || "Без филиала")}</h3>` : "";
      lastBr = g.branch;
      return `${brHead}<details class="m-grp" open><summary><b>${esc(g.name)}</b> <span class="muted small">${list.length}</span></summary>${list.map((k) => row(k, true)).join("")}</details>`;
    }).join("");
  } else body = rows.map((k) => row(k, false)).join("");
  page.innerHTML = `
    <div class="page-head"><h2 class="grow">${BOARD_TITLES.members}</h2>
      ${can("members", "full") ? `<button class="btn sm" id="dup-btn" hidden>👯 Дубли</button><button class="btn sm" id="arch-btn">🗄 Архив ▾</button>` : ""}
</div>
    <div class="section arch-box" id="arch-box" hidden></div>
    <div class="m-views" id="m-views"></div>
    <div class="m-filters">
      <input id="mf-q" placeholder="Поиск: ребёнок, родитель или телефон" value="${esc(f.q || "")}">
      <select id="mf-branch"><option value="">Все филиалы</option>${branches.map((b) => `<option ${f.branch === b ? "selected" : ""}>${esc(b)}</option>`).join("")}</select>
      <select id="mf-group"><option value="">Все группы</option>${groups.map((g) => `<option value="${g.id}" ${f.group === String(g.id) ? "selected" : ""}>${esc((f.branch ? "" : g.branch + " · ") + g.name)}</option>`).join("")}</select>
      <label class="check small"><input type="checkbox" id="mf-grouped" ${f.grouped ? "checked" : ""}> По филиалам и группам</label>
    </div>
    <div class="chips" style="margin:10px 0">${M_STATUS.map(([k, t]) => `<button class="chip ${(f.status || "") === k ? "on" : ""}" data-mst="${k}">${t} <span class="muted">${cnt(k)}</span></button>`).join("")}</div>
    ${data.note ? `<div class="note">${esc(data.note)}</div>` : ""}
    <div class="section m-list">${body}</div>`;
  const rerender = () => { save(); renderMembersList(page, data); };
  $("#mf-q").oninput = (e) => { f.q = e.target.value; const pos = e.target.selectionStart; rerender(); const i = $("#mf-q"); i.focus(); i.setSelectionRange(pos, pos); };
  $("#mf-branch").onchange = (e) => { f.branch = e.target.value; f.group = ""; rerender(); };
  $("#mf-group").onchange = (e) => { f.group = e.target.value; rerender(); };
  $("#mf-grouped").onchange = (e) => { f.grouped = e.target.checked; rerender(); };
  page.querySelectorAll("[data-mst]").forEach((b) => (b.onclick = () => { f.status = b.dataset.mst; rerender(); }));
  page.querySelectorAll(".m-list [data-open]").forEach((b) => { b.onclick = () => openStudent(+b.dataset.open); b.onkeydown = (e) => { if (e.key === "Enter") openStudent(+b.dataset.open); }; });
  if ($("#arch-btn")) membersArchive(page);
  membersViews(page, f, rerender);
  if ($("#dup-btn")) membersDups($("#dup-btn"));
}

/* Настройки → Подсказки по этапам: что сказать и что сделать, чтобы перейти дальше */
async function renderTipsSettings(box) {
  const d = await api("GET", "/api/stage-tips");
  box.innerHTML = `<h3>Подсказки по этапам</h3><div class="small muted" style="margin-bottom:10px">Видны в карточке лида на жёлтой плашке «💡 Подсказка». Пишите так, как хотите, чтобы говорил администратор</div>
    ${Object.entries(d.tips).map(([k, t]) => `<div class="section" style="box-shadow:none;padding:10px 0;border-top:1px solid var(--line)"><b>${esc(d.labels[k])}</b>
      <div class="field"><label>Что сказать</label><textarea rows="3" data-tsay="${k}">${esc(t.say)}</textarea></div>
      <div class="field"><label>Чтобы перейти дальше</label><input data-tnext="${k}" value="${esc(t.next)}"></div></div>`).join("")}
    <button class="btn primary" id="tips-save">Сохранить</button>`;
  $("#tips-save").onclick = async () => {
    const tips = Object.fromEntries(Object.keys(d.tips).map((k) => [k, { say: box.querySelector(`[data-tsay="${k}"]`).value, next: box.querySelector(`[data-tnext="${k}"]`).value }]));
    try { await api("PUT", "/api/stage-tips", { tips }); toast("Подсказки сохранены"); } catch (e) { fail(e); }
  };
}

function erpWireFiles(box, d) {
  $("#erp-file").onchange = async (e) => {
    for (const f of e.target.files) {
      const data = await new Promise((res) => { const r = new FileReader(); r.onload = () => res(r.result.split(",")[1]); r.readAsDataURL(f); });
      try { const r = await api("POST", "/api/erp/upload", { filename: f.name, data }); toast(`${f.name}: ${r.rows} строк`); } catch (err) { fail(err); }
    }
    renderErp(box);
  };
  box.querySelectorAll("[data-ukind]").forEach((sel) => (sel.onchange = async () => { await api("PUT", `/api/erp/uploads/${sel.dataset.ukind}`, { kind: sel.value }).catch(fail); renderErp(box); }));
  box.querySelectorAll("[data-umap]").forEach((sel) => (sel.onchange = async () => {
    const card = sel.closest("[data-up]"), mapping = {};
    card.querySelectorAll("[data-umap]").forEach((x) => { if (x.value !== "") mapping[x.dataset.field] = +x.value; });
    await api("PUT", `/api/erp/uploads/${card.dataset.up}`, { mapping }).catch(fail);
    const u = d.uploads.find((x) => x.id === +card.dataset.up); u.mapping = mapping;
    const ex = card.querySelector(`[data-ex="${sel.dataset.field}"]`); if (ex) ex.textContent = sel.value !== "" ? (u.preview[0] || [])[+sel.value] || "" : "";
  }));
  box.querySelectorAll("[data-udel]").forEach((b) => (b.onclick = async () => { if (!confirm("Убрать этот файл?")) return; await api("DELETE", `/api/erp/uploads/${b.dataset.udel}`).catch(fail); renderErp(box); }));
}

/* Настройки → Перенос из ERP: файлы, колонки, группы, пробный прогон, перенос, сверка, отмена */
async function renderErp(box) {
  const d = await api("GET", "/api/erp");
  const done = d.last && !d.last.undone_at;
  box.innerHTML = `<h3>Перенос из ERP</h3>
    <div class="small muted" style="margin-bottom:10px">Выгрузите из ERP клиентов, абонементы, оплаты, посещения и задачи в Excel (.xlsx) или CSV и загрузите сюда. Файлы разбираются на нашем сервере, никуда больше не уходят. Можно загружать по одному или все сразу</div>
    ${done ? `<div class="alert" style="background:var(--accent-soft,#e7f6ee);color:inherit">✅ Перенос сделан ${esc(d.last.started_at.replace("T", " "))}. Ниже сверка с файлами</div><div id="erp-verify" class="small muted">Сверяю…</div>
      <div class="erp-step" style="margin-top:14px"><b>🔄 Обновить из ERP (каждый день, пока клиентов ведут в ERP)</b>
        <div class="small muted">Выгрузите из ERP свежие файлы клиентов, абонементов и оплат и загрузите сюда, потом нажмите «Обновить». Новые клиенты добавятся, у старых обновятся этапы, пробные, группы и оплаты. Ничего не удаляется: отметки тренеров, отчёты и задачи в CRM остаются${d.last_update ? `. Последнее обновление: ${esc(d.last_update.at.replace("T", " "))}` : ""}</div>
        <div class="row" style="gap:8px;align-items:center;margin-top:8px"><input type="file" id="erp-file" multiple accept=".xlsx,.csv,.txt" style="flex:1"></div>
        <div id="erp-ups">${d.uploads.map((u) => erpUploadHtml(u, d.kinds)).join("")}</div>
        <button class="btn primary" id="erp-update" style="margin-top:8px" ${d.uploads.length ? "" : "disabled"}>Обновить</button><div id="erp-upd-out" class="small"></div></div>
      <div class="erp-step"><b>📋 Итоги дня для ERP</b>
        <div class="small muted">Что тренеры отметили в CRM: кто был, кто не пришёл и почему, итоги пробных, оплаты на месте. Перенесите в ERP вечером</div>
        <div class="row" style="gap:8px;align-items:center;margin-top:8px"><input type="date" id="erp-day-d" value="${S.meta.today}" style="flex:0 0 170px"><button class="btn" id="erp-day-go" style="flex:0">Показать</button></div>
        <div id="erp-day-out"></div></div>
      <div class="erp-step" id="erp-ri" hidden><b>🔄 Были на пробном давно и не купили</b>
        <div class="small muted">Пробное было больше недели назад, а клиент до сих пор на этапе «Как прошло пробное?». Их правильное место: «Приглашение на ПУ повторно». Дата приглашения берётся из их задачи, если она есть, иначе сегодня: админ перенесёт на нужную</div>
        <button class="btn" id="erp-ri-go" style="margin-top:8px"></button></div>
      <div class="erp-step"><b>🏷 Статусы клиентов в ERP</b>
        <div class="small muted">Какой статус был у клиентов в ERP при переносе и сколько клиентов в каждом. Статус ERP виден и в карточке клиента</div>
        <div id="erp-st-out" class="small muted" style="margin-top:6px">Загружаю…</div></div>
      <details style="margin-top:12px"><summary class="small muted">Отменить перенос</summary><button class="btn danger" id="erp-undo" style="margin-top:8px">Отменить перенос</button></details>` : `
    <div class="erp-step"><b>1. Файлы</b>
      <div class="row" style="gap:8px;align-items:center;margin-top:8px"><input type="file" id="erp-file" multiple accept=".xlsx,.csv,.txt" style="flex:1"></div>
      <div id="erp-ups">${d.uploads.map((u) => erpUploadHtml(u, d.kinds)).join("") || `<div class="small muted" style="margin-top:6px">Файлов пока нет</div>`}</div></div>
    <div class="erp-step"><b>2. Группы</b><div id="erp-groups" class="small muted">${d.uploads.length ? "Загружаю…" : "Появятся после загрузки файлов"}</div></div>
    <div class="erp-step"><b>3. Пробный прогон</b><div class="small muted">Ничего не записывает: показывает, что будет перенесено, и все нестыковки</div>
      <button class="btn" id="erp-plan" style="margin-top:8px" ${d.uploads.length ? "" : "disabled"}>Проверить</button><div id="erp-plan-out"></div></div>
    <div class="erp-step"><b>4. Перенос</b><div class="small muted">Перед переносом CRM сделает резервную копию и удалит всех выдуманных клиентов. Сотрудники, группы, настройки и переписки останутся. Перенос можно отменить целиком</div>
      <label class="check" style="margin-top:8px"><input type="checkbox" id="erp-rkn"> Изменения в Роскомнадзор поданы, политика обработки данных опубликована</label>
      <button class="btn primary" id="erp-run" style="margin-top:8px" disabled>Перенести</button></div>`}`;
  erpTaskTransferSetup(box,d);
  if (done) {
    erpFullSetup(box, d);
    erpWireFiles(box, d);
    api("GET", "/api/erp/reinvite-old").then((r) => {
      if (!r.n) return;
      $("#erp-ri").hidden = false;
      $("#erp-ri-go").textContent = `Перевести ${r.n} в «Приглашение на ПУ повторно»`;
      $("#erp-ri-go").onclick = async () => {
        if (!confirm(`Перевести ${r.n} клиентов в «Приглашение на ПУ повторно»?`)) return;
        try { const x = await api("POST", "/api/erp/reinvite-old", {}); toast(`Переведено: ${x.n}`); renderErp(box); } catch (e) { fail(e); }
      };
    }).catch(() => {});
    api("GET", "/api/erp/statuses").then((r) => {
      $("#erp-st-out").innerHTML = r.items.length ? `<table class="tbl"><thead><tr><th>Статус в ERP</th><th>Клиентов</th><th>Сейчас в CRM: ученики</th><th>лиды</th><th>ушли</th></tr></thead>
        <tbody>${r.items.map((x) => `<tr><td>${esc(x.status)}</td><td><b>${x.n}</b></td><td>${x.members || 0}</td><td>${x.leads || 0}</td><td>${x.left_ || 0}</td></tr>`).join("")}</tbody></table>`
        : "Статусов ERP у клиентов не найдено";
    }).catch((e) => { $("#erp-st-out").textContent = e.message; });
    $("#erp-update").onclick = async () => {
      const b = $("#erp-update"); b.disabled = true; b.textContent = "Обновляю…";
      try {
        const r = await api("POST", "/api/erp/update", {}), t = r.stats;
        toast("Обновлено из ERP");
        $("#erp-upd-out").innerHTML = `✅ Новых клиентов: ${t.new}, сменили этап: ${t.stage}, обновлено карточек: ${t.updated}, пробных: ${t.trials}, изменений в группах: ${t.groups}, абонементов: ${t.subs}, оплат: ${t.payments}`;
        setTimeout(() => renderErp(box), 4000);
      } catch (e) { fail(e); b.disabled = false; b.textContent = "Обновить"; }
    };
    $("#erp-day-go").onclick = async () => {
      try {
        const r = await api("GET", "/api/erp/day?date=" + $("#erp-day-d").value);
        const txt = [`Итоги дня ${dShort(r.date)} для ERP`, ...r.groups.flatMap((g) => [``, `${g.branch} · ${g.group}`, ...g.rows.map((x) => `- ${x.name}${x.erp_id ? ` (ID ${x.erp_id})` : ""}: ${x.mark}`)]),
          ...(r.pays.length ? [``, `Оплаты на месте:`, ...r.pays.map((p) => `- ${p.branch}: ${p.name}, ${p.amount} ₽, ${p.what || ""}`)] : [])].join("\n");
        const task = `Перенеси в ERP Футболики (вкладка, где вы уже вошли) отметки посещений за ${dShort(r.date)} из списка ниже. Для каждой группы открой журнал за эту дату и проставь: «был», «не был» (с причиной, если указана), «отработка», итог пробного. Ищи ребёнка по ID ERP, если он указан, иначе по ФИО. Ничего другого в ERP не меняй и не удаляй. Если ребёнка или группы нет, пропусти и запиши в отчёт. В конце дай короткий отчёт: сколько отметок внесено и что не получилось (без телефонов).\n\n${txt}`;
        $("#erp-day-out").innerHTML = r.groups.length ? `<pre class="erp-day">${esc(txt)}</pre>
          <div class="row" style="gap:8px;flex-wrap:wrap"><button class="btn sm" id="erp-day-copy">Копировать итоги</button><button class="btn sm primary" id="erp-day-prompt">Копировать задание для агента в браузере</button></div>
          <div class="small muted" style="margin-top:6px">Задание вставьте агенту в облачном браузере, где вы вошли в ERP. Это передача данных клиентов внешнему сервису: учтите в уведомлении в Роскомнадзор</div>`
          : `<div class="small muted" style="margin-top:6px">За этот день отметок нет</div>`;
        const cp = (t) => navigator.clipboard.writeText(t).then(() => toast("Скопировано"), () => window.prompt("Скопируйте", t));
        if ($("#erp-day-copy")) { $("#erp-day-copy").onclick = () => cp(txt); $("#erp-day-prompt").onclick = () => cp(task); }
      } catch (e) { fail(e); }
    };
    $("#erp-undo").onclick = async () => {
      if (!confirm("Отменить перенос? Все перенесённые клиенты, абонементы и оплаты будут удалены. Выдуманные клиенты не вернутся")) return;
      try { await api("POST", "/api/erp/undo", {}); toast("Перенос отменён"); renderErp(box); } catch (e) { fail(e); }
    };
    const v = await api("GET", "/api/erp/verify").catch(() => null);
    if (!v) { $("#erp-verify").textContent = "Сверка не загрузилась"; return; }
    $("#erp-verify").innerHTML = `<table class="tbl" style="margin-top:6px"><thead><tr><th></th><th>В файлах ERP</th><th>В CRM</th><th></th></tr></thead><tbody>
      ${v.rows.map((r) => `<tr><td>${esc(r.label)}</td><td>${r.erp.toLocaleString("ru-RU")}</td><td>${r.crm.toLocaleString("ru-RU")}</td><td>${r.ok ? "✅" : "⚠️"}</td></tr>`).join("")}
      ${v.by_month.map((r) => `<tr><td class="muted">Оплаты за ${esc(monthLabel(r.month))}</td><td>${rub(r.erp)}</td><td>${rub(r.crm)}</td><td>${r.ok ? "✅" : "⚠️"}</td></tr>`).join("")}</tbody></table>
      <div style="margin-top:10px"><b>Занимаются по группам</b>${v.groups.map((g) => `<div>${esc(g.branch)} · ${esc(g.name)}: ${g.kids}</div>`).join("")}</div>`;
    return;
  }
  erpWireFiles(box, d);
  if (d.uploads.length) {
    const g = await api("GET", "/api/erp/groups");
    $("#erp-groups").innerHTML = g.erp.length ? `<div class="small muted" style="margin:4px 0 6px">Какой нашей группе соответствует группа из ERP. CRM подобрала сама, проверьте</div>
      ${g.erp.map((x) => `<div class="row erp-grow" style="align-items:center;gap:8px"><span style="flex:1">${esc(x.erp)} <span class="muted">· ${x.count}</span></span>
        <select data-gmap="${esc(x.erp)}" style="flex:1"><option value="">— не переносить в группу —</option>${g.ours.map((o) => `<option value="${o.id}" ${o.id === x.group_id ? "selected" : ""}>${esc(o.branch)} · ${esc(o.name)}</option>`).join("")}</select></div>`).join("")}
      <button class="btn sm" id="erp-gsave" style="margin-top:8px">Сохранить группы</button>` : "В файлах нет колонки с группой";
    if ($("#erp-gsave")) $("#erp-gsave").onclick = async () => {
      const map = {}; box.querySelectorAll("[data-gmap]").forEach((x) => (map[x.dataset.gmap] = x.value));
      try { await api("PUT", "/api/erp/groups", { map }); toast("Группы сохранены"); } catch (e) { fail(e); }
    };
  }
  if ($("#erp-plan")) $("#erp-plan").onclick = async () => {
    if ($("#erp-gsave")) $("#erp-gsave").click();
    const p = await api("GET", "/api/erp/plan").catch(fail); if (!p) return;
    const st = p.stats;
    $("#erp-plan-out").innerHTML = `<div class="erp-stats">
      <div><b>${st.families}</b> семей</div><div><b>${st.children}</b> детей и лидов</div><div><b>${st.members_active}</b> занимаются</div>
      <div><b>${st.members_left}</b> ушли</div><div><b>${st.leads}</b> лидов</div><div><b>${st.subs}</b> абонементов</div>
      <div><b>${st.payments}</b> оплат на ${rub(st.payments_sum)}</div><div><b>${st.visits}</b> посещений</div><div><b>${st.tasks}</b> задач</div></div>
      <div class="small" style="margin-top:6px">Статус и группа ребёнка - по проданным абонементам: занимается, если абонемент есть на текущий или будущий месяц
        (из «занимаются» не оплатили текущий месяц, но платили в прошлом: <b>${st.members_unpaid || 0}</b>), ушёл - если абонементы были раньше.
        Ходят в несколько групп по абонементам этого месяца: <b>${st.multi_group || 0}</b>.
        Лидов без имени ребёнка (сохранятся с пометкой): <b>${st.no_name || 0}</b>. Вводные и отработки не считаются абонементами: <b>${(st.subs_trials || 0) + (st.subs_makeups || 0)}</b>.
        Оплат с датой продажи вместо пустой даты оплаты: <b>${st.pay_date_from_sale || 0}</b></div>
      <div class="small" style="margin-top:6px">Будет удалено клиентов, которые сейчас в CRM (выдуманные): <b>${p.will_remove}</b></div>
      ${p.problems.length ? `<div class="small" style="margin-top:8px"><b>Нестыковки по видам:</b> ${Object.entries(p.problem_types || {}).map(([k, v]) => `${esc(k)}: <b>${v}</b>`).join(" · ")}</div>
        <details class="fold" style="margin-top:6px"><summary><b>Нестыковки: ${p.problems_total}</b>${p.problems_total > p.problems.length ? ` (показаны первые ${p.problems.length})` : ""}</summary><ul class="small">${p.problems.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></details>` : `<div class="small" style="margin-top:6px">✅ Нестыковок нет</div>`}`;
    $("#erp-run").disabled = !$("#erp-rkn").checked;
    S.erpPlanned = true;
  };
  if ($("#erp-rkn")) $("#erp-rkn").onchange = (e) => { $("#erp-run").disabled = !(e.target.checked && S.erpPlanned); };
  if ($("#erp-run")) $("#erp-run").onclick = async () => {
    if (!confirm("Перенести клиентов из ERP? Выдуманные клиенты будут удалены, перед этим CRM сделает резервную копию")) return;
    $("#erp-run").disabled = true; $("#erp-run").textContent = "Переношу…";
    try { const r = await api("POST", "/api/erp/run", { rkn: true }); toast(`Перенесено: ${r.stats.children} детей, ${r.stats.payments} оплат`); S.erpPlanned = false; renderErp(box); }
    catch (e) { fail(e); $("#erp-run").disabled = false; $("#erp-run").textContent = "Перенести"; }
  };
}
function erpUploadHtml(u, kinds) {
  return `<div class="erp-up" data-up="${u.id}"><div class="row" style="align-items:center;gap:8px"><b style="flex:1">${esc(u.filename)} <span class="muted small">· ${u.count} строк</span></b>
      <select data-ukind="${u.id}" style="flex:0 0 auto;width:auto">${Object.entries(kinds).map(([k, t]) => `<option value="${k}" ${k === u.kind ? "selected" : ""}>${esc(t)}</option>`).join("")}</select>
      <button class="icon-btn" data-udel="${u.id}" title="Убрать файл">✕</button></div>
    <details class="fold"><summary class="small">Колонки: угадано ${Object.keys(u.mapping).length} из ${u.fields.length}. Проверить</summary>
      <table class="tbl erp-map"><thead><tr><th>Что это</th><th>Колонка в файле</th><th>Пример из первой строки</th></tr></thead><tbody>
      ${u.fields.map((f) => `<tr><td>${esc(f.label)}</td><td><select data-umap data-field="${f.key}"><option value="">— нет —</option>${u.headers.map((h, i) => `<option value="${i}" ${u.mapping[f.key] === i ? "selected" : ""}>${esc(h)}</option>`).join("")}</select></td>
        <td class="small muted" data-ex="${f.key}">${u.mapping[f.key] !== undefined ? esc((u.preview[0] || [])[u.mapping[f.key]] || "") : ""}</td></tr>`).join("")}</tbody></table></details></div>`;
}

/* Настройки → Автоцепочки: серии сообщений родителям, по умолчанию выключены */
async function renderChainsSettings(box) {
  const d = await api("GET", "/api/chains");
  box.innerHTML = `<h3>Автоцепочки сообщений</h3>
    <div class="small muted" style="margin-bottom:10px">CRM сама пишет родителю серию сообщений в мессенджер, с 10:00 до 20:00. Любой ответ родителя останавливает цепочку и ставит задачу ответить. Цепочка заканчивается сама, когда цель достигнута. В текстах можно писать {child} (имя ребёнка), {group} (группа), {month} (месяц)</div>
    ${Object.entries(d.chains).map(([k, c]) => { const st = d.stats[k]; return `<div class="section chain-box" data-chain="${k}" style="box-shadow:none;border:1px solid var(--line)">
      <div class="row" style="align-items:center;gap:10px"><b class="grow">${esc(c.title)}</b>
        <label class="check" style="flex:0 0 auto"><input type="checkbox" data-cen ${c.enabled ? "checked" : ""}> Включена</label></div>
      <div class="small muted">Когда: ${esc(c.when)}. Остановится сама: ${esc(c.goal)}</div>
      ${st.total ? `<div class="small" style="margin-top:4px">Сейчас идёт: ${st.active} · ответили родители: ${st.replied} · дошли до цели: ${st.goal}</div>` : ""}
      ${c.steps.map((x, i) => `<div class="chain-step"><div class="field" style="flex:0 0 120px"><label>Сообщение ${i + 1}: через, дней</label><input type="number" min="0" max="60" data-cdays value="${x.days}"></div>
        <div class="field" style="flex:1 1 auto"><label>Текст</label><textarea rows="3" data-ctext>${esc(x.text)}</textarea></div></div>`).join("")}
      </div>`; }).join("")}
    <button class="btn primary" id="chains-save">Сохранить</button>`;
  $("#chains-save").onclick = async () => {
    const chains = {};
    box.querySelectorAll("[data-chain]").forEach((el) => {
      chains[el.dataset.chain] = { enabled: el.querySelector("[data-cen]").checked,
        steps: [...el.querySelectorAll(".chain-step")].map((r) => ({ days: +r.querySelector("[data-cdays]").value, text: r.querySelector("[data-ctext]").value })) };
    });
    try { await api("PUT", "/api/chains", { chains }); toast("Сохранено"); renderChainsSettings(box); } catch (e) { fail(e); }
  };
}

/* Мои списки: сохранённые фильтры «Учеников», у каждого сотрудника свои */
async function membersViews(page, f, rerender) {
  const box = $("#m-views"); if (!box) return;
  const items = (await api("GET", "/api/views?section=members").catch(() => ({ items: [] }))).items;
  const keys = ["q", "branch", "group", "status", "grouped"];
  const same = (v) => keys.every((k) => (v.data[k] || "") === (f[k] || ""));
  const hasFilter = keys.some((k) => f[k]);
  box.innerHTML = items.length || hasFilter ? `<span class="small muted">⭐ Мои списки:</span>
    ${items.map((v) => `<span class="chip ${same(v) ? "on" : ""}" data-view-id="${v.id}">${esc(v.name)}<button class="chip-x" data-view-del="${v.id}" title="Удалить список">×</button></span>`).join("")}
    ${hasFilter && !items.some(same) ? `<button class="chip" id="view-save">+ Сохранить этот список</button>` : ""}` : "";
  box.querySelectorAll("[data-view-id]").forEach((c) => (c.onclick = (e) => {
    if (e.target.closest("[data-view-del]")) return;
    const v = items.find((x) => x.id === +c.dataset.viewId);
    keys.forEach((k) => { f[k] = v.data[k] || ""; }); f.grouped = !!v.data.grouped;
    rerender();
  }));
  box.querySelectorAll("[data-view-del]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Удалить этот список? Сами ученики никуда не денутся")) return;
    await api("DELETE", `/api/views/${b.dataset.viewDel}`).catch(fail); membersViews(page, f, rerender);
  }));
  if ($("#view-save")) $("#view-save").onclick = async () => {
    const name = prompt("Как назвать список? Например: «Небо, не оплатили»");
    if (!name) return;
    try { await api("POST", "/api/views", { section: "members", name, data: Object.fromEntries(keys.map((k) => [k, f[k] || ""])) }); toast("Список сохранён"); membersViews(page, f, rerender); }
    catch (e) { fail(e); }
  };
}

/* Дубли семей: одна семья заведена дважды. Склеить одной кнопкой */
async function membersDups(btn) {
  const d = await api("GET", "/api/duplicates").catch(() => ({ items: [] }));
  if (!d.items.length) return;
  btn.hidden = false; btn.textContent = `👯 Дубли (${d.items.length})`;
  btn.onclick = () => openDups(d.items);
}
function openDups(items) {
  const fam = (x) => `<div class="dup-fam"><b>${esc(x.name || "Семья")}</b>
    <div class="small">${x.kids.map((k) => esc(k.name) + (k.birth ? ` (${dShort(k.birth)})` : "") + (k.funnel === "sales" ? " · лид" : "")).join(", ") || "детей нет"}</div>
    <div class="small muted">${x.parents.map((p) => esc(p.name) + (p.phone ? " " + esc(p.phone) : "")).join(", ")}</div>
    <div class="small muted">оплат: ${x.payments}</div></div>`;
  openDrawer(`<h2>👯 Похоже на одну семью</h2>
    <p class="muted">Одна и та же семья заведена дважды. Выберите, какую карточку оставить: всё из второй (дети, родители, переписка, звонки, оплаты) переедет в неё. Отменить склейку нельзя.</p>
    ${items.map((x, i) => `<div class="section dup-pair"><div class="small muted" style="margin-bottom:8px">Почему: ${esc(x.why)}</div>
      <div class="dup-grid">${fam(x.a)}${fam(x.b)}</div>
      <div class="row" style="gap:8px;flex-wrap:wrap;margin-top:10px">
        <button class="btn sm primary" data-merge="${i}" data-keep="a">Оставить левую</button>
        <button class="btn sm primary" data-merge="${i}" data-keep="b">Оставить правую</button>
        <button class="btn sm" data-dskip="${i}">Это разные семьи</button></div></div>`).join("")}`);
  const body = $("#drawer-body");
  body.querySelectorAll("[data-merge]").forEach((b) => (b.onclick = async () => {
    const x = items[+b.dataset.merge], keep = b.dataset.keep === "a" ? x.a : x.b, drop = b.dataset.keep === "a" ? x.b : x.a;
    if (!confirm(`Склеить? Останется семья «${keep.name}», всё из «${drop.name}» переедет в неё`)) return;
    try { await api("POST", "/api/families/merge", { keep: keep.id, drop: drop.id }); toast("Склеено"); closeDrawer(); render(); } catch (e) { fail(e); }
  }));
  body.querySelectorAll("[data-dskip]").forEach((b) => (b.onclick = async () => {
    const x = items[+b.dataset.dskip];
    await api("POST", "/api/duplicates/skip", { a: x.a.id, b: x.b.id }).catch(fail);
    b.closest(".dup-pair").remove(); toast("Больше не предложу");
  }));
}

/* Архив учеников и лидов: кнопка над доской «Ученики», список раскрывается под ней */
async function membersArchive(page) {
  const btn = $("#arch-btn"), box = $("#arch-box");
  let list = await api("GET", "/api/archive/students").catch(() => []);
  const label = () => { btn.textContent = `🗄 Архив (${list.length}) ${box.hidden ? "▾" : "▴"}`; };
  const draw = (q = "") => {
    const n = q.toLowerCase().replace(/\D/g, "") ? q.replace(/\D/g, "") : "";
    const rows = list.filter((x) => !q || (x.name + " " + x.family).toLowerCase().includes(q.toLowerCase()) || (n && (x.phone || "").replace(/\D/g, "").includes(n)));
    $("#arch-rows").innerHTML = rows.length ? rows.map((x) => `<div class="arch-row"><button class="linkish grow" data-aopen="${x.id}"><b>${esc(x.name)}</b> <span class="muted small">${esc(x.family)}${x.funnel === "sales" ? " · лид" : ""}</span></button>
      <button class="btn sm" data-aback="${x.id}">Вернуть</button></div>`).join("") : `<div class="small muted">${list.length ? "Никого не нашли" : "В архиве пусто"}</div>`;
    box.querySelectorAll("[data-aopen]").forEach((b) => (b.onclick = () => openStudent(+b.dataset.aopen)));
    box.querySelectorAll("[data-aback]").forEach((b) => (b.onclick = async () => {
      try { await api("POST", `/api/students/${b.dataset.aback}/archive`, { archived: false }); toast("Вернули из архива"); render(); } catch (e) { fail(e); }
    }));
  };
  box.innerHTML = `<input id="arch-q" placeholder="Поиск по архиву: имя или телефон"><div id="arch-rows" style="margin-top:8px"></div>`;
  $("#arch-q").oninput = (e) => draw(e.target.value.trim());
  btn.onclick = () => { box.hidden = !box.hidden; label(); if (!box.hidden) $("#arch-q").focus(); };
  label(); draw();
}

/* ---------- рабочий стол: что горит + воронка ---------- */
const URG_ACTION = { missed_call: "Перезвонить", site_lead: "Открыть", message: "Ответить", trial: "Открыть", open_q: "Открыть диалог" };
const ago = (iso) => {
  if (!iso) return "";
  const m = Math.max(0, Math.round((Date.now() - new Date(iso.length <= 16 ? iso + ":00" : iso).getTime()) / 60000));
  return m < 1 ? "только что" : m < 60 ? `${m} мин назад` : m < 1440 ? `${Math.floor(m / 60)} ч назад` : dtShort(iso);
};

/* «Смотреть как»: админ выбирает сотрудника и видит рабочий стол его глазами */
S.viewAs = (() => { try { return JSON.parse(localStorage.getItem("crm.viewAs") || "null"); } catch (_) { return null; } })();
async function viewAsBar() {
  if (S.user.role !== "admin") return "";
  if (!S.staff) S.staff = (await api("GET", "/api/users")).filter((u) => u.active);
  const cur = S.viewAs ? S.viewAs.id : S.user.id;
  const opt = (u) => `<option value="${u.id}" ${u.id === cur ? "selected" : ""}>${esc(u.name)}${u.id === S.user.id ? " (я)" : ""}</option>`;
  const grp = (role, title) => { const l = S.staff.filter((u) => u.role === role); return l.length ? `<optgroup label="${title}">${l.map(opt).join("")}</optgroup>` : ""; };
  return `<div class="viewas ${S.viewAs && S.viewAs.id !== S.user.id ? "on" : ""}"><span>👀 Как видит стол</span>
    <select id="view-as">${grp("admin", "Администраторы")}${grp("operator", "Операторы")}${grp("coach", "Тренеры")}</select>
    ${S.viewAs && S.viewAs.id !== S.user.id ? `<button class="btn sm" id="view-me">Вернуться к своему</button>` : ""}</div>`;
}
function wireViewAs(page) {
  const sel = page.querySelector("#view-as");
  if (!sel) return;
  sel.onchange = () => {
    const u = S.staff.find((x) => x.id === +sel.value);
    S.viewAs = u && u.id !== S.user.id ? { id: u.id, name: u.name, role: u.role } : null;
    try { localStorage.setItem("crm.viewAs", JSON.stringify(S.viewAs)); } catch (_) {}
    render();
  };
  const me = page.querySelector("#view-me");
  if (me) me.onclick = () => { sel.value = S.user.id; sel.onchange(); };
}

async function renderWorkdesk(page) {
  if (S.user.role !== "admin") S.viewAs = null;
  const bar = await viewAsBar().catch(() => "");
  if (S.viewAs && S.viewAs.role === "coach") { await renderCoachToday(page, S.viewAs.id, bar); return wireViewAs(page); }
  const [d, tasksHtml, schedHtml, erpHtml, skips, members] = await Promise.all([api("GET", "/api/workdesk" + (S.viewAs ? "?as_user=" + S.viewAs.id : "")), tasksSection(), scheduleWidget().catch(() => ""),
    can("sales") ? erpTasksSection() : Promise.resolve(""), can("skips") ? api("GET", "/api/board/skips").catch(() => null) : Promise.resolve(null),
    can("members", "full") ? api("GET", "/api/board/members").catch(() => null) : Promise.resolve(null)]);
  // воронка «Продления» (Юлия, 04.11): разовые, не оплачен, продлить (с 20 числа), счёт выставлен, ушёл. Оплатившие не попадают
  const since30 = addDays(S.meta.today, -30);
  const renew = members ? { ...members, draggable: false, note: "", columns: members.columns.filter((c) => c.board !== false)
    .map((c) => c.key === "left" ? { ...c, title: "Ушёл (за 30 дней)", cards: c.cards.filter((k) => (k.stage_changed_at || "") >= since30) } : c) } : null;
  const wasOpen = page.querySelector(".sched-widget") ? page.querySelector(".sched-widget").open : null;
  const u = d.urgent, reds = u.filter((x) => x.level === "red").length;
  const scroll = page.querySelector(".board") ? page.querySelector(".board").scrollLeft : 0;
  // кто смотрит стол: руководитель, контролёр (проверяет приход тренеров) или администратор
  const who = S.viewAs ? (d.checkins ? "control" : "operator") : S.user.role === "admin" ? "owner" : d.checkins ? "control" : "operator";
  const W = {
    call: { html: callFocusBox(d.call_focus), icon: "📞", label: "Звонки" },
    spend: { html: spendDeskBox(d), icon: "💳", label: "Платежи", n: (d.bills_due || []).filter((x) => x.level).length, red: (d.bills_due || []).some((x) => x.level === "red") },
    checkins: { html: checkinReport(d.checkins), icon: "🧑‍🏫", label: "Приход тренеров", n: d.checkins ? d.checkins.rows.filter((x) => x.state === "late" || x.state === "absent").length : 0, red: true },
    merch: { html: merchAlerts(d.merch_alerts), icon: "👕", label: "Мерч", n: d.merch_alerts ? d.merch_alerts.qr_no_receipt.length + d.merch_alerts.missing_reports.length + d.merch_alerts.cash_not_handed.length : 0 },
    marks: { html: marksBox(d), icon: "📋", label: "Отметки в журнале", n: (d.missing_marks || []).length, red: true },
    subs: { html: needsSubsBox(d), icon: "🔁", label: "Замены", n: (d.needs_subs || []).length, red: true },
    wait: { html: waitlistBox(d.waitlist), icon: "⏳", label: "Лист ожидания" },
    sched: { html: schedHtml, icon: "📅", label: "Сегодня в филиалах" },
    bday: { html: birthdaysBox(d.birthdays), icon: "🎂", label: "Дни рождения" },
    // Юлия, 04.11: задачи по клиентам у администраторов общие, они в «📋 Задачи»; «Мои задачи» на столе не нужны
    tasks: { html: erpHtml ? "" : tasksHtml, icon: "✅", label: "Задачи" },
    answers: { html: coachAnswersBox(d.coach_answers), icon: "💬", label: "Ответы тренеров", n: (d.coach_answers || []).length },
    reports: { html: shiftReportsBox(d.shift_reports), icon: "📝", label: "Отчёты о смене",
      n: d.shift_reports ? d.shift_reports.late.length + d.shift_reports.issues.length : 0, red: true },
  };
  const ORDER = {
    owner: ["spend", "reports", "answers", "marks", "subs", "checkins", "tasks", "call", "wait", "merch", "sched", "bday"],
    control: ["reports", "answers", "marks", "checkins", "subs", "merch", "tasks", "wait", "sched", "bday", "call", "spend"],
    operator: ["tasks", "answers", "call", "wait", "sched", "bday", "marks", "subs", "merch", "spend", "checkins", "reports"],
  }[who];
  const widgets = ORDER.filter((k) => W[k].html);
  // на виду только то, где есть что делать, и задачи; остальное свёрнуто строкой внизу (раскрывается по нажатию)
  S.dwOpen = S.dwOpen || {};
  const dwShown = (k) => k === "tasks" || !!W[k].n || (k === "call" && who === "operator");
  const tiles = widgets.filter((k) => W[k].n).map((k) => `<button class="dtile ${W[k].red ? "red" : "yellow"}" data-jump="dw-${k}"><span>${W[k].icon}</span><b>${W[k].n}</b><small>${W[k].label}</small></button>`);
  const hr = new Date().getHours();
  const hello = hr < 5 ? "Доброй ночи" : hr < 12 ? "Доброе утро" : hr < 18 ? "Добрый день" : "Добрый вечер";
  const firstName = (S.user.name || "").split(" ")[0];
  const dateTxt = new Date().toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" }).replace(/^./, (c) => c.toUpperCase());
  const hot = `<section class="now ${u.length ? "" : "calm"}" id="dw-hot">
      <div class="now-head"><h3>${u.length ? (reds ? `🔥 Горит прямо сейчас: ${reds}` : "Ждут ответа") : "Всё обработано"}</h3>
        ${u.length ? `<span class="muted small">Красное сначала, потом сообщения и пробные</span>` : ""}</div>
      ${u.length ? `<div class="now-list">${u.slice(0, S.hotAll ? u.length : 3).map((x, i) => `
        <div class="now-item ${x.level}">
          <span class="ref-card ${x.level}" aria-hidden="true"></span>
          <div class="grow"><b>${esc(x.title)}</b>${x.count ? ` <span class="nbadge static">${x.count}</span>` : ""}
            <div class="small ellipsis">${esc(x.text)}</div></div>
          <span class="small muted when">${ago(x.ts)}</span>
          <div class="now-act">
            <button class="btn sm ${x.level === "red" ? "primary" : ""}" data-u="${i}">${URG_ACTION[x.kind]}</button>
            ${x.kind === "missed_call" ? `<button class="btn sm" data-useen="${x.id}" title="Уже связались другим способом">Обработан</button>` : ""}
          </div>
        </div>`).join("")}</div>${u.length > 3 ? `<button class="btn sm ghost now-more" id="hot-more">${S.hotAll ? "Свернуть" : `Ещё ${u.length - 3}`}</button>` : ""}` : `<div class="muted small">Пропущенных звонков, новых заявок и непрочитанных сообщений нет. Можно выдохнуть ⚽</div>`}
    </section>`;
  page.innerHTML = `
    ${bar}
    <div class="desk-hello">
      <div class="grow"><h2>${S.viewAs ? `Рабочий стол: ${esc(S.viewAs.name)}` : `${hello}, ${esc(firstName)}`}</h2>
        <div class="muted small">${esc(dateTxt)}${u.length || tiles.length ? "" : " · всё спокойно ⚽"}</div>
        ${S.viewAs ? "" : `<div style="margin-top:8px">${envelopeButton()}</div>`}</div>
      ${tiles.length || reds ? `<div class="dtiles">${reds ? `<button class="dtile red" data-jump="dw-hot"><span>🔥</span><b>${reds}</b><small>Горит</small></button>` : ""}${tiles.join("")}</div>` : ""}
    </div>
    ${(d.reviews || []).map((r) => `<div class="rv-alert"><span class="rv-star">⭐</span>
      <div class="grow"><b>Новый отзыв на ${esc(r.label)}</b><div class="small">${esc(r.subject)} · пришёл ${esc(dtShort(r.created_at))}</div></div>
      <a class="btn sm" href="${esc(r.link)}" target="_blank" rel="noopener">Открыть отзывы</a>
      <button class="btn sm primary" data-rvclose="${r.id}">Ответили ✓</button></div>`).join("")}
    ${erpHtml}
    <div class="desk">
      <div class="desk-main">
        ${hot}
        <section class="desk-funnel">
          <div class="now-head"><h3>Воронка продаж</h3></div>
          ${boardHtml(d.board)}
        </section>
        ${renew ? `<section class="desk-funnel" id="dw-renew">
          <div class="now-head"><h3>🔁 Продления</h3><span class="small muted">${renew.columns.reduce((a, c) => a + c.cards.length, 0) || "пусто"}</span></div>
          ${boardHtml(renew)}</section>` : ""}
        ${skips ? `<section class="desk-funnel" id="dw-skips">
          <div class="now-head"><h3>🩹 Пропуски и отработки</h3><span class="small muted">${skips.columns.reduce((a, c) => a + c.cards.length, 0) || "пусто"}</span></div>
          ${skips.note ? `<div class="note">${esc(skips.note)}</div>` : ""}${boardHtml(skips)}</section>` : ""}
      </div>
      <div class="desk-side">${widgets.filter(dwShown).map((k) => `<div class="dw" id="dw-${k}">${W[k].html}</div>`).join("")}
        ${widgets.some((k) => !dwShown(k)) ? `<section class="dw-folded"><div class="dwf-title small muted">Ещё на столе: сейчас там пусто или не срочно</div>${widgets.filter((k) => !dwShown(k)).map((k) => `<details class="dwf" data-dwk="${k}" ${S.dwOpen[k] ? "open" : ""}>
          <summary><span class="dwf-ic">${W[k].icon}</span><span class="grow">${W[k].label}</span><span class="dwf-chev">›</span></summary><div class="dw" id="dw-${k}">${W[k].html}</div></details>`).join("")}</section>` : ""}</div>
    </div>`;
  page.querySelectorAll("[data-rvclose]").forEach((b) => (b.onclick = async () => { try { await api("POST", `/api/reviews/${b.dataset.rvclose}/close`, {}); render(); } catch (e) { fail(e); } }));
  const hm = $("#hot-more"); if (hm) hm.onclick = () => { S.hotAll = !S.hotAll; render(); };
  page.querySelectorAll(".dwf").forEach((x) => x.addEventListener("toggle", () => { S.dwOpen[x.dataset.dwk] = x.open; }));
  page.querySelectorAll("[data-jump]").forEach((b) => (b.onclick = () => { const el = document.getElementById(b.dataset.jump); if (el) el.scrollIntoView({ behavior: "smooth", block: "start" }); }));
  if (page.querySelector(".board")) page.querySelector(".board").scrollLeft = scroll;
  wireBoard(page.querySelector(".desk-funnel") || page, "sales", d.board);
  if (skips) wireBoard(page.querySelector("#dw-skips"), "skips", skips);
  if (renew) wireBoard(page.querySelector("#dw-renew"), "members", renew);
  wireErpTasks(page);
  wireTasks(page);
  wireViewAs(page);
  wireNeedsSubs(page, d);
  wireWaitlist(page);
  wireMarks(page);
  wireCheckinReport(page);
  wireShiftReports(page, d);
  wireCoachAnswers(page);
  wireEnvelope(page);
  wireSpend(page);
  wireMerchAlerts(page);
  if (page.querySelector(".sched-widget")) { if (wasOpen === false) page.querySelector(".sched-widget").open = false; wireSchedule(page.querySelector(".sched-widget"), S.meta.today); }
  page.querySelectorAll(".bday-box [data-open]").forEach((b) => (b.onclick = () => openStudent(+b.dataset.open)));
  page.querySelectorAll("[data-u]").forEach((b) => (b.onclick = async () => {
    const x = u[+b.dataset.u];
    if (x.kind === "missed_call") { await dial(x.phone); if (x.student_id) openStudent(x.student_id); else openNewClient({ phone: x.phone }); }
    else if (x.kind === "message" || x.kind === "open_q") openChat(x.conversation_id, true);
    else if (x.student_id) openStudent(x.student_id);
  }));
  page.querySelectorAll("[data-useen]").forEach((b) => (b.onclick = async () => {
    await api("POST", `/api/calls/${b.dataset.useen}/seen`, {}).catch(fail); pollCounts(); render();
  }));
}

function cardHtml(k, draggable) {
  const sub = [k.parent, k.phone].filter(Boolean).join(", ");
  const meta2 = [k.group || (k.branch ? "📍 " + k.branch : ""), k.tariff === "flex" ? "гибкий" : (k.group ? "негибкий" : ""), k.siblings ? `братьев и сестёр: ${k.siblings}` : ""].filter(Boolean).join(" · ");
  return `<article class="card ${k.color || ""}" data-id="${k.id}" ${draggable ? 'draggable="true"' : ""}>
    ${k.color ? `<span class="ref-card ${k.color}" title="${k.color === "red" ? "Срочно" : "Пора связаться"}"></span>` : ""}
    <div class="name">${esc(k.name)}</div>
    <div class="meta">${esc(sub)}</div>
    ${meta2 ? `<div class="meta">${esc(meta2)}</div>` : ""}
    ${k.badge ? `<span class="badge">${esc(k.badge)}</span>` : ""}
    ${k.score ? `<span class="score ${k.score}">${{ hot: "🔥 горячий", warm: "🌤 тёплый", cold: "❄ холодный" }[k.score]}</span>` : ""}
    ${k.due ? `<div class="due ${k.due_color || ""}" title="${esc(k.due_title || "")}">⏰ ${dueText(k.due)}</div>` : ""}
  </article>`;
}
function dueText(iso) {
  const t = S.meta.today;
  const d = iso.slice(0, 10), hm = iso.slice(11, 16);
  if (d < t) return `просрочено с ${dShort(d)} ${hm}`;
  if (d === t) return (iso.slice(0, 16) < new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16) ? "просрочено, " : "сегодня до ") + hm;
  return `${dShort(d)} ${hm}`;
}

function enableDrag(page, funnel, columns) {
  const auto = new Set(columns.filter((c) => c.auto).map((c) => c.key));
  if (funnel === "skips") auto.add("booked");
  let dragId = null;
  page.querySelectorAll(".card[draggable]").forEach((el) => {
    el.addEventListener("dragstart", (e) => { dragId = el.dataset.id; el.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; });
    el.addEventListener("dragend", () => el.classList.remove("dragging"));
  });
  page.querySelectorAll(".col").forEach((col) => {
    const stage = col.dataset.stage;
    if (auto.has(stage)) return;
    col.addEventListener("dragover", (e) => { e.preventDefault(); col.classList.add("drop"); });
    col.addEventListener("dragleave", () => col.classList.remove("drop"));
    col.addEventListener("drop", async (e) => {
      e.preventDefault(); col.classList.remove("drop");
      if (!dragId) return;
      try {
        if (funnel === "sales" && stage === "refused") return askRefusal(dragId, render);
        if (funnel === "sales" && stage === "reinvite_pu") return askReinvite(dragId, render);
        await api("POST", `/api/students/${dragId}/move`, { funnel, stage }); render();
      } catch (err) { fail(err); }
    });
  });
}

/* ---------- карточка ---------- */
/* Почему не купили после пробного: причина обязательна. «Думают» и «заболели/уезжают» — не отказ, а задача */
function askRefusal(sid, done) {
  const rs = S.meta.trial_refusals || [];
  openDrawer(`<h2>Почему не купили?</h2>
    <form class="section" id="rf">
      <div class="chips" style="flex-direction:column;align-items:stretch">${rs.map((r, i) => `<label class="check"><input type="radio" name="reason" value="${esc(r)}" ${i ? "" : "checked"}> ${esc(r)}</label>`).join("")}</div>
      <div class="small muted" style="margin:8px 0">«Думают» - CRM поставит задачу перезвонить через 2 дня. «Заболели или уезжают» - карточка уйдёт в «Пригласим позже» с задачей пригласить снова. Остальное - отказ</div>
      <div class="field"><label>Комментарий</label><input name="comment" placeholder="Что сказал родитель"></div>
      <button class="btn primary">Сохранить</button>
    </form>`);
  $("#rf").onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    try {
      const r = await api("POST", `/api/students/${sid}/move`, { funnel: "sales", stage: "refused", reason: f.reason, comment: f.comment });
      toast(r.stage === "how_was" ? "Задача: перезвонить " + dShort(r.due.slice(0, 10)) : r.stage === "later" ? "Пригласим снова " + dShort(r.due.slice(0, 10)) : "Отказ сохранён");
      closeDrawer(); done();
    } catch (err) { fail(err); }
  };
}

/* «Приглашение на ПУ повторно» (Юлия, 04.11): был на пробном, не сложилось. Админ выбирает, когда пригласить снова */
function askReinvite(sid, done) {
  const plus = (n) => addDays(S.meta.today, n);
  const y = +S.meta.today.slice(0, 4), aug = `${y}-08-01`, jan = `${y + 1}-01-05`, janNow = `${y}-01-05`;
  const intake = [janNow, aug, jan, `${y + 1}-08-01`].find((d) => d > S.meta.today);
  openDrawer(`<h2>🔄 Пригласить на пробное повторно</h2>
    <div class="small muted">Ребёнок уже был на пробном, но не сложилось. Когда пригласить снова? В этот день в 11:00 у админов появится задача</div>
    <form class="section" id="ri">
      <div class="chips" style="margin-bottom:10px">${[[7, "Через неделю"], [14, "Через 2 недели"], [30, "Через месяц"]].map(([n, t]) => `<button type="button" class="chip" data-rid="${plus(n)}">${t}</button>`).join("")}
        <button type="button" class="chip" data-rid="${intake}">Ближайший набор (${dShort(intake)})</button></div>
      <div class="field"><label>Дата</label><input type="date" name="due" min="${S.meta.today}" value="${plus(14)}" required></div>
      <div class="field"><label>Комментарий</label><input name="comment" placeholder="Почему не сложилось, что предложить"></div>
      <button class="btn primary">Сохранить</button>
    </form>`);
  $("#ri").querySelectorAll("[data-rid]").forEach((b) => (b.onclick = () => { $("#ri").elements.due.value = b.dataset.rid; }));
  $("#ri").onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    try {
      await api("POST", `/api/students/${sid}/move`, { funnel: "sales", stage: "reinvite_pu", due: f.due + "T11:00", comment: f.comment });
      toast("Пригласим снова " + dShort(f.due)); closeDrawer(); done();
    } catch (err) { fail(err); }
  };
}

/* Ушёл: причина обязательна. Что дальше, зависит от причины (Юлия, 03.11) */
function askLeftReason(sid, done) {
  const y = +S.meta.today.slice(0, 4);
  const intake = [`${y}-01-05`, `${y}-08-01`, `${y + 1}-01-05`, `${y + 1}-08-01`].find((d) => d > S.meta.today);
  const rs = [["Дорого", "В воронку «Ждут акцию»: позвоним, когда будет акция"], ["Неудобное расписание", "Пригласить вернуться через 2 недели"],
    ["Далеко ездить", `Пригласить в ближайший набор, ${dShort(intake)}`], ["Ребёнку не нравится", `Пригласить в ближайший набор, ${dShort(intake)}`],
    ["Перешли в другую секцию", `Пригласить в ближайший набор, ${dShort(intake)}`], ["Переезд", "Карточка уйдёт в архив, звонить не будем"],
    ["Болезнь или травма", "Уточните, когда пригласить, и выберите дату"], ["Сезон, каникулы", "Уточните, когда пригласить, и выберите дату"],
    ["Другое", "Пригласить вернуться через 2 месяца"]];
  openDrawer(`<h2>Почему ушёл?</h2>
    <form class="section" id="lf">
      <div class="chips" style="flex-direction:column;align-items:stretch">${rs.map(([r, h], i) => `<label class="check"><input type="radio" name="reason" value="${esc(r)}" ${i ? "" : "checked"}> ${esc(r)}<span class="small muted" style="margin-left:6px">${esc(h)}</span></label>`).join("")}</div>
      <div class="field" id="lf-due" hidden style="margin-top:8px"><label>Когда пригласить вернуться</label><input type="date" name="due" min="${S.meta.today}"></div>
      <div class="field"><label>Комментарий</label><input name="comment" placeholder="Что сказал родитель"></div>
      <button class="btn primary">Сохранить</button>
    </form>`);
  const admin = ["Болезнь или травма", "Сезон, каникулы"];
  const sync = () => { const r = $("#lf").reason.value, on = admin.includes(r); $("#lf-due").hidden = !on; $("#lf").due.required = on; };
  $("#lf").querySelectorAll("[name=reason]").forEach((x) => (x.onchange = sync)); sync();
  $("#lf").onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    try {
      const r = await api("POST", `/api/students/${sid}/move`, { funnel: "members", stage: "left", reason: f.reason, comment: f.comment, due: f.due ? f.due + "T11:00" : "" });
      toast(r && r.plan === "promo" ? "Ушёл. В воронке «Ждут акцию»" : r && r.plan === "archive" ? "Ушёл. Карточка в архиве" : r && r.due ? `Ушёл. Пригласить вернуться ${dShort(r.due)}` : "Сохранено: ушёл");
      closeDrawer(); done();
    } catch (err) { fail(err); }
  };
}

function openDrawer(html, size) {
  S.studentCardRequest=(S.studentCardRequest||0)+1;
  $("#drawer-body").innerHTML = html; $("#drawer").hidden = false; document.body.style.overflow = "hidden";
  const panel = $("#drawer .drawer-panel"); if (panel) panel.classList.toggle("wide", size === "wide");
  const state = { ...(history.state || {}), drawer: true }; delete state.studentCard;
  try {
    if (!drawerInHistory) { history.pushState(state, ""); drawerInHistory = true; }
    else history.replaceState(state, "");
  } catch (_) {}
}
function hideDrawer() { S.studentCardRequest=(S.studentCardRequest||0)+1; const d = $("#drawer"); if (d) d.hidden = true; document.body.style.overflow = ""; }
/* закрыть карточку: крестиком, Esc, после сохранения или кнопкой/свайпом «назад» на телефоне */
function closeDrawer() {
  forgetStudentCard();
  hideDrawer();
  if (drawerInHistory) { drawerInHistory = false; skipPop = true; history.back(); }
}
document.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) { closeDrawer(); } });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
/* глазик в поле пароля: показать или скрыть, что вводишь */
document.addEventListener("click", (e) => {
  const b = e.target.closest(".pw-eye");
  if (!b) return;
  const i = b.previousElementSibling, show = i.type === "password";
  i.type = show ? "text" : "password";
  b.textContent = show ? "🙈" : "👁";
  b.setAttribute("aria-label", show ? "Скрыть пароль" : "Показать пароль");
  i.focus();
});

/* Карточка клиента: общение, абонементы и данные на отдельных вкладках. */
function layoutStudentCard(body, isMember) {
  const grid = body.querySelector(".sc-grid"), left = grid.querySelector(".sc-left"), right = grid.querySelector(".sc-right"), info = grid.querySelector(".sc-info");
  const take = (k) => body.querySelector(`[data-sc="${k}"]`);
  const fold = (k, pane, open = false) => {
    const sec = take(k); if (!sec) return;
    const det = document.createElement("details"); det.className = "sc-fold"; det.dataset.fold = k; det.open = open;
    det.innerHTML = `<summary>${sec.dataset.title || (k === "stage" ? "Этап и источник" : k === "balance" ? "Баланс и оплаты" : k === "subs" ? "Абонементы" : k)}</summary>`;
    det.appendChild(sec); pane.appendChild(det);
  };
  if (!isMember && take("pu")) left.appendChild(take("pu"));
  left.appendChild(body.querySelector("#comms-box"));
  ["subs", "balance"].forEach(k => { if (take(k)) right.appendChild(take(k)); });
  ["vac"].forEach(k => fold(k, right));
  if(take("visits")) grid.querySelector(".sc-attendance").appendChild(take("visits"));
  ["family", "stage", "hist"].forEach(k => fold(k, info, k === "family"));
  if (take("arch")) info.appendChild(take("arch"));
  body.querySelectorAll("[data-sctab]").forEach((b) => (b.onclick = () => {
    grid.dataset.tab = b.dataset.sctab;
    if (history.state?.studentCard) {
      try { history.replaceState({ ...history.state, studentCard: { ...history.state.studentCard, tab: b.dataset.sctab } }, ""); } catch (_) {}
    }
    body.querySelectorAll("[data-sctab]").forEach((x) => { x.classList.toggle("on", x === b); x.setAttribute("aria-selected", String(x === b)); });
  }));
}
const scTab = (tab) => { const b = document.querySelector(`#drawer-body [data-sctab="${tab}"]`); if (b) b.click(); };
const openFold = (body, k) => {
  if(["visits","skips"].includes(k)){scTab("visits");body.querySelector('[data-sc="visits"]')?.scrollIntoView({block:"start",behavior:"smooth"});return}
  scTab(["family","stage","hist"].includes(k)?"info":"abon");const f=body.querySelector(`[data-fold="${k}"]`);if(f){f.open=true;f.scrollIntoView({block:"start",behavior:"smooth"})}
};

function quoteHtml(q) {
  return `<div class="total">${rub(q.total)}</div>
    <div class="small">${q.lessons_paid} занятий × ${rub(q.per_lesson)} = ${rub(q.base)}${q.days_label ? ` · только ${esc(q.days_label)}` : ""}${q.vacation_lessons ? ` · отпуск: −${q.vacation_lessons} зан.` : ""}</div>
    ${q.credit_lessons ? `<div class="small">Перерасчёт пропусков (${q.credit_absence_dates.map(dShort).join(", ")}): −${rub(q.credit)}</div>` : ""}
    ${q.late_fee ? `<div class="small">Оплата после ${dShort(q.pricing.deadline)}: +${String(q.pricing.late_pct).replace(".", ",")}% (+${rub(q.late_fee)}), скидки не действуют</div>` : ""}
    ${q.pricing && q.pricing.newcomer ? `<div class="small">Новичок: только оставшиеся занятия этого месяца, без надбавки</div>` : ""}
    ${q.pricing && !q.pricing.late && q.pricing.late_pct && q.pricing.late_total > q.total ? `<div class="small muted">При оплате после ${dShort(q.pricing.deadline)}: ${rub(q.pricing.late_total)} без скидок</div>` : ""}
    ${q.discount ? `<div class="small">Скидка «${esc(q.discount.name)}» ${q.discount.percent}%: −${rub(q.discount.amount)}${q.discount.auto ? ". Действует при оплате сегодня" : ""}</div>` : ""}
    ${q.waitlist ? `<div class="alert" style="margin-top:6px">Оплата после срока: место не гарантировано, в листе ожидания ${q.waitlist}</div>` : ""}`;
}

/* «Продать абонемент»: месяц, группа, тариф, скидка → цена по всем правилам → ссылка на оплату, оплата сейчас или только счёт */
/* перед продажей и ссылкой на оплату: все данные клиента и почта для чека */
async function ensureRequired(sid, kind) {
  const info = await api("GET", `/api/students/${sid}/required?for=${kind}`).catch(() => null);
  return !info || !info.missing.length || await askRequired(info);
}
function saleCalendarHtml(q, ym) {
  const dates = new Set(q.calendar_dates || q.dates), selected = new Set(q.dates), excluded = new Set(q.excluded_dates || []);
  const [year, month] = ym.split('-').map(Number), offset = (new Date(year, month - 1, 1).getDay() + 6) % 7;
  const cells = Array(offset).fill('<span></span>');
  for (let day = 1; day <= new Date(year, month, 0).getDate(); day++) {
    const iso = `${ym}-${String(day).padStart(2,'0')}`, checked = q.single ? selected.has(iso) : !excluded.has(iso);
    cells.push(dates.has(iso) ? `<label class="sale-cal-day ${checked ? 'selected' : q.single ? 'available' : 'excluded'}"><span>${day}</span><input type="${q.single ? 'radio' : 'checkbox'}" name="sale-lesson" data-sale-date="${iso}" ${checked ? 'checked' : ''} aria-label="Занятие ${dShort(iso)}"><small>${checked ? 'занятие' : q.single ? 'выбрать' : 'исключено'}</small></label>` : `<span class="sale-cal-day off">${day}</span>`);
  }
  return `<div class="sale-calendar" id="sl-calendar"><b>${esc(monthLabel(ym))}</b><div class="small muted">${q.single ? 'Выберите одно занятие.' : 'Отмеченные дни входят в абонемент. Снимайте отметки только для согласованных исключений.'}</div><div class="sale-cal-grid">${WD.map(d=>`<b class="sale-cal-week">${d}</b>`).join('')}${cells.join('')}</div><div class="small">Выбрано занятий: <b>${q.dates.length}</b>${excluded.size ? ` · исключено: ${excluded.size}` : ''}</div></div>`;
}
const clientGroupLabel = e => e.lesson_kind === 'arena' || /сборная.*арктика/i.test(e.group_name || e.name || '') ? 'Сборная Арктика' : e.group_name || e.name || '';
async function openSell(d, reload) {
  if (!(await ensureRequired(d.student.id,'sale'))) return;
  const id=d.student.id;
  d=await api('GET',`/api/students/${id}`).catch(()=>d);
  const box=$('#sell-box'); if(!box)return;
  const session=String(S.saleSequence=(S.saleSequence||0)+1);box.dataset.saleSession=session;
  const isCurrent=()=>box.isConnected&&box.dataset.saleSession===session&&$('#sell-box')===box;
  scTab('abon');
  const ens=d.enrollments.filter(e=>e.lesson_kind!=='ofp');
  const previous=(d.subscriptions||[]).filter(s=>s.status!=='cancelled'&&s.kind!=='single'&&s.month<d.next_month&&(!ens.length||ens.some(e=>e.id===s.enrollment_id))).sort((a,b)=>Number(b.status==='paid')-Number(a.status==='paid')||b.month.localeCompare(a.month)||b.id-a.id);
  const preferred=ens.find(e=>e.id===previous[0]?.enrollment_id)||ens.find(e=>e.group_id===d.student.group_id)||ens[0]||{id:null,tariff:previous[0]?.tariff||d.student.tariff||'fixed',group_id:previous[0]?.group_id,program_id:(S.meta.groups||[]).find(g=>g.id===previous[0]?.group_id)?.program_id};
  const choices=(S.meta.groups||[]).filter(g=>!g.is_makeup&&g.lesson_kind!=='ofp'&&(!ens.length||(g.program_id||null)===(preferred.program_id||null)||ens.some(e=>e.group_id===g.id))).map(g=>{
    const own=ens.find(e=>e.group_id===g.id);
    return {...preferred,...own,id:own?.id||preferred.id,group_id:g.id,group_name:g.name,program_id:g.program_id,lesson_kind:g.lesson_kind,new_group:!own};
  });
  ens.forEach(e=>{if(!choices.some(c=>c.group_id===e.group_id))choices.push(e)});
  if(!choices.length)return toast('Нет доступных групп для продажи',true);
  const months=[...new Set([d.cur_month,d.next_month,...d.subscriptions.filter(s=>s.status==='invoiced').map(s=>s.month)])].sort().reverse();
  const pendingSingles=d.subscriptions.filter(s=>s.kind==='single'&&s.status==='invoiced');
  const methods=Object.entries(PAY_METHODS).filter(([k])=>!PAY_INTERNAL.includes(k)&&k!=='online');
  box.innerHTML=`<div class="section sell"><div class="row" style="align-items:center"><h3 class="grow" style="margin:0">Продать абонемент</h3><button class="icon-btn" id="sl-x" title="Закрыть">✕</button></div>
    <div class="field" style="margin-top:14px"><label>Что оформляем</label><select id="sl-kind"><option value="month">На месяц</option><option value="single">Разовое занятие</option></select></div>
    <div class="field" id="sl-single-invoice-field" hidden><label>Разовое занятие</label><select id="sl-single-invoice"><option value="">Оформить новое занятие</option>${pendingSingles.map(s=>`<option value="${s.id}">Уже выставлено: ${s.single_date?dShort(s.single_date):monthLabel(s.month)} · ${rub(s.price)}</option>`).join('')}</select></div>
    <div class="row"><div class="field" id="sl-month-field"><label>Месяц</label><select id="sl-month">${months.map(m=>`<option value="${m}" ${m===d.next_month?'selected':''}>${monthLabel(m)}</option>`).join('')}</select></div><div class="field"><label>Группа</label><select id="sl-en">${choices.map(e=>`<option value="${e.group_id}" ${e.group_id===preferred.group_id?'selected':''}>${esc(e.group_name)}${e.new_group&&ens.length?' · другая группа':''}</option>`).join('')}</select></div></div>
    <div class="row"><div class="field" id="sl-tar-field"><label>Тариф</label><select id="sl-tar"></select></div><div class="field" id="sl-disc-field"><label>Скидка</label><select id="sl-disc"><option value="">По правилам</option>${(d.discounts||[]).map(x=>`<option value="${x.id}">${esc(x.name)}, ${x.percent}%</option>`).join('')}</select></div></div>
    <div class="small muted" id="sl-admission" hidden>Допуск в сборную — по решению тренера. Разовых занятий нет.</div>
    <div class="field" id="sl-ofp-box" hidden><label>День ОФП</label><select id="sl-ofp"></select></div>
    <div class="field" id="sl-group-action-box" hidden><label>Как изменить группу с начала месяца</label><select id="sl-group-action"><option value="transfer">Перевести из прежней группы</option><option value="add">Добавить вторую, сохранить прежнюю</option></select><small class="muted">Изменение сохранится при оформлении.</small></div>
    <label class="sale-credit" id="sl-credit-box" hidden><input type="checkbox" id="sl-credit"> Клиент запросил перерасчёт пропусков прошлого месяца <span class="small muted" id="sl-credit-info"></span></label>
    <div class="quote" id="sl-q">Считаю…</div><div class="sc-actions"><button class="btn primary" id="sl-link">Ссылка на оплату</button><button class="btn" id="sl-now">Оплачено сейчас</button></div><div id="sl-res"></div></div>`;
  const en=()=>choices.find(e=>e.group_id===+$('#sl-en').value), single=()=>$('#sl-kind').value==='single';
  let total=0,version=0,singleDay=null,existing=null;
  const exclusions=new Set();
  const busy=on=>box.querySelectorAll('.sc-actions button').forEach(b=>b.disabled=on);
  const lock=on=>{box.querySelectorAll('select,input').forEach(e=>e.disabled=on);if(!on)syncFields()};
  const syncFields=()=>{
    $('#sl-kind option[value=single]').disabled=!!en().program_id&&!pendingSingles.length;
    $('#sl-single-invoice option[value=""]').disabled=!!en().program_id;
    if(en().program_id&&single()&&!$('#sl-single-invoice').value){if(pendingSingles.length)$('#sl-single-invoice').value=String(pendingSingles[0].id);else $('#sl-kind').value='month'}
    $('#sl-single-invoice-field').hidden=!single()||!pendingSingles.length;
    $('#sl-month-field').hidden=single()&&!!$('#sl-single-invoice').value;
    $('#sl-tar-field').hidden=single();$('#sl-disc-field').hidden=single();
    $('#sl-admission').hidden=!en().program_id;
    $('#sl-ofp-box').hidden=single()||!en().program_id||$('#sl-tar').value!=='pkg';
    $('#sl-group-action-box').hidden=single()||!en().new_group||!ens.length;
  };
  const recommend=()=>{
    const p=(d.program?.programs||[]).find(p=>p.id===en().program_id),plan=p?.plan;
    const effective=plan?.next&&$('#sl-month').value>=plan.next.from?plan.next:plan;
    const last=previous.find(s=>s.enrollment_id===en().id&&s.month<$('#sl-month').value);
    $('#sl-tar').innerHTML=en().program_id?'<option value="arena_only">Только Арктика</option><option value="pkg">Арктика + ОФП</option>':'<option value="fixed">Негибкий</option><option value="flex">Гибкий</option>';
    $('#sl-tar').value=en().program_id?effective?.tariff||last?.tariff||'arena_only':last?.tariff||en().tariff||'fixed';
    $('#sl-ofp').innerHTML=(p?.ofp_groups||[]).map(g=>`<option value="${g.id}" ${g.id===effective?.ofp_group_id?'selected':''}>${esc(g.name)}</option>`).join('');syncFields();
  };
  const quote=async()=>{
    const request=++version;busy(true);existing=null;$('#sl-res').innerHTML='';$('#sl-q').innerHTML='Считаю…';
    ['#sl-tar','#sl-disc','#sl-ofp'].forEach(s=>$(s).disabled=false);$('#sl-credit-box').hidden=true;
    const own=ens.find(e=>e.group_id===en().group_id);
    const sub=single()?pendingSingles.find(s=>s.id===+$('#sl-single-invoice').value):own&&d.subscriptions.find(s=>s.enrollment_id===own.id&&s.month===$('#sl-month').value&&s.kind!=='single'&&s.status!=='cancelled');
    if(sub){
      existing=sub;let current;try{current=await api('GET',`/api/subscriptions/${sub.id}/payment-quote`)}catch(e){if(request===version&&isCurrent())$('#sl-q').textContent=e.message;return}if(request!==version||!isCurrent())return;const paid=current.paid;total=current.total;
      $('#sl-tar').value=sub.tariff;syncFields();['#sl-tar','#sl-disc','#sl-ofp'].forEach(s=>$(s).disabled=true);
      $('#sl-q').innerHTML=`<b>${single()?(total?'Разовое занятие уже оформлено':'Разовое занятие оплачено'):(total?'Абонемент уже оформлен':'Абонемент оплачен')}</b><div class="finance-numbers"><div><small>Стоимость</small><b>${rub(current.price)}</b></div><div><small>${cancelled?'Оплачено до отмены':'Оплачено'}</small><b>${cancelled&&!cancellation?.period_verified?'См. историю':rub(paid)}</b></div><div><small>Осталось оплатить</small><b>${rub(total)}</b></div></div><p class="small muted">Повторный абонемент создаваться не будет.</p>`;busy(total<=0);return;
    }
    try{
      const params=new URLSearchParams({month:$('#sl-month').value,group_id:en().group_id});
      if(single()){if(singleDay)params.set('date',singleDay)}else{
        params.set('enrollment_id',en().id||'');params.set('tariff',$('#sl-tar').value);params.set('ofp_group_id',$('#sl-ofp').value||'');params.set('excluded_dates',[...exclusions].join(','));params.set('credit_requested',$('#sl-credit').checked?'1':'0');if($('#sl-disc').value)params.set('discount_id',$('#sl-disc').value);
      }
      const q=await api('GET',`/api/students/${id}/${single()?'single-quote':'quote'}?${params}`);if(request!==version||!isCurrent())return;
      total=q.total;if(q.single)singleDay=q.dates[0];q.calendar_picker=true;
      $('#sl-q').innerHTML=(q.single?`<div class="total">${rub(q.total)}</div><div class="small">Разовое занятие · ${dShort(singleDay)}</div>`:q.program?programQuoteHtml(q):quoteHtml(q))+saleCalendarHtml(q,$('#sl-month').value);
      $('#sl-credit-box').hidden=!q.credit_available;$('#sl-credit-info').textContent=q.credit_available?`(до ${q.credit_available} занятий)`:'';
      $('#sl-calendar').onchange=e=>{const day=e.target.dataset.saleDate;if(!day)return;if(single()){singleDay=day;quote();return}if(!e.target.checked&&!$('#sl-calendar').querySelector('input:checked')){e.target.checked=true;return toast('Оставьте хотя бы одно занятие',true)}if(e.target.checked)exclusions.delete(day);else exclusions.add(day);quote()};busy(total<=0);
    }catch(e){if(request===version&&isCurrent())$('#sl-q').textContent=e.message}
  };
  const reset=()=>{exclusions.clear();singleDay=null;$('#sl-credit').checked=false};
  $('#sl-single-invoice').onchange=()=>{reset();syncFields();quote()};
  $('#sl-kind').onchange=()=>{reset();if(single())$('#sl-month').value=d.cur_month;syncFields();quote()};
  $('#sl-month').onchange=$('#sl-en').onchange=()=>{reset();recommend();quote()};
  $('#sl-tar').onchange=()=>{reset();syncFields();quote()};$('#sl-ofp').onchange=()=>{reset();quote()};$('#sl-disc').onchange=$('#sl-credit').onchange=quote;
  $('#sl-x').onclick=()=>{version++;box.dataset.saleSession='';box.innerHTML=''};
  const selection=()=>({single:single(),month:$('#sl-month').value,date:existing?.single_date||singleDay,group_id:en().group_id,enrollment_id:en().id,group_action:$('#sl-group-action').value,tariff:$('#sl-tar').value,ofp_group_id:$('#sl-tar').value==='pkg'?+$('#sl-ofp').value:null,excluded_dates:[...exclusions],credit_requested:$('#sl-credit').checked,discount_id:$('#sl-disc').value||null,group:existing?.group_name?clientGroupLabel(existing):existing?.kind==='single'?'':clientGroupLabel(en()),tariffLabel:single()?'Разовое занятие':$('#sl-tar').selectedOptions[0]?.textContent,existing_id:existing?.id});
  const invoice=async order=>{
    let result=order.existing_id?{id:order.existing_id,quote:{}}:await api('POST',`/api/students/${id}/${order.single?'single':'invoice'}`,order);
    const current=await api('GET',`/api/subscriptions/${result.id}/payment-quote`);
    return {...result,quote:{...result.quote,total:current.total}};
  };
  $('#sl-now').onclick=async()=>{
    const order=selection();busy(true);lock(true);
    try{
      const prepared=await invoice(order);if(!isCurrent())return;
      if(prepared.quote.total<=0){toast('Счёт уже оплачен с баланса');reload();return}
      $('#sl-res').innerHTML=`<div class="row" style="margin-top:14px;align-items:flex-end"><div class="field"><label>Сумма, ₽</label><input type="number" id="sl-amount" min="1" step="1" value="${prepared.quote.total}"></div><div class="field"><label>Как оплатили</label><select id="sl-m">${methods.map(([k,t])=>`<option value="${k}">${t}</option>`).join('')}</select></div></div><button class="btn primary" id="sl-ok">Принять ${rub(prepared.quote.total)}</button><p class="small muted">Можно указать частичную оплату. Остаток останется к оплате.</p>`;
      $('#sl-amount').oninput=()=>$('#sl-ok').textContent='Принять '+rub($('#sl-amount').value);
      $('#sl-ok').onclick=async()=>{const amount=Number($('#sl-amount').value),method=$('#sl-m').value;if(!Number.isInteger(amount)||amount<=0)return toast('Укажите сумму в рублях',true);$('#sl-ok').disabled=true;try{await api('POST',`/api/subscriptions/${prepared.id}/pay`,{amount,method});toast('Оплата принята');if(isCurrent())reload()}catch(e){fail(e);if(isCurrent()&&$('#sl-ok'))$('#sl-ok').disabled=false}};
    }catch(e){fail(e);if(isCurrent()){lock(false);busy(false)}}
  };
  $('#sl-link').onclick=async()=>{
    const order=selection();busy(true);lock(true);
    try{
      const st=await api('GET',`/api/students/${id}/paylinks`);if(!st.ready)throw Error('PayKeeper не подключён: Чеки → Подключение');
      const r=await invoice(order);if(!isCurrent())return;if(r.quote.total<=0){toast('Счёт уже оплачен с баланса');reload();return}
      const title=order.single?`Разовое занятие${order.date?' '+dShort(order.date):''}`:`Абонемент на ${monthLabel(order.month)}`;
      const previousLink=(st.links||[]).find(l=>l.status==='open'&&l.subscription_id===r.id&&l.amount===r.quote.total);
      const l=previousLink||await api('POST',`/api/students/${id}/paylinks`,{amount:r.quote.total,purpose:`${title}: ${d.student.name}`,subscription_id:r.id,full_payment:true});if(!isCurrent())return;
      const msg=`${title}\n${d.student.name}\n\n${order.group?'Группа: '+order.group+'\n':''}Тариф: ${order.tariffLabel}\n\nК оплате: ${rub(l.amount)}\n\nСсылка для оплаты:\n${l.url}`;
      $('#sl-res').innerHTML=`<label style="margin-top:14px">Сообщение родителю</label><textarea id="sl-msg" rows="9">${esc(msg)}</textarea><div class="sc-actions" style="margin-top:10px"><button class="btn primary" id="sl-send">Вставить в чат с семьёй</button><button class="btn" id="sl-copy">Скопировать</button><button class="btn" id="sl-done">Готово</button></div>`;
      resizeMessageInput($('#sl-msg'));
      $('#sl-copy').onclick=()=>copyTextWithNotice($('#sl-msg').value,'Текст скопирован');
      $('#sl-send').onclick=()=>{scTab('feed');const t=$('#cm-text');if(!t)return toast('Нет открытого чата: скопируйте сообщение',true);t.value=$('#sl-msg').value;t.dispatchEvent(new Event('input',{bubbles:true}));t.focus();t.scrollIntoView({block:'center',behavior:'smooth'});toast('Сообщение вставлено. Проверьте и отправьте')};$('#sl-done').onclick=reload;
    }catch(e){fail(e);if(isCurrent()){lock(false);busy(false)}}
  };
  recommend();quote();box.scrollIntoView({block:'start',behavior:'smooth'});
}

async function copyTextWithNotice(text, notice='Текст скопирован') {
  try { await navigator.clipboard.writeText(text); }
  catch (_) {
    const input=document.createElement('textarea'),previous=document.activeElement;
    input.value=text;input.style.cssText='position:fixed;opacity:0;width:1px;height:1px';document.body.appendChild(input);input.focus();input.select();
    const copied=document.execCommand('copy');input.remove();previous?.focus({preventScroll:true});
    if(!copied){prompt('Скопируйте текст',text);return}
  }
  toast(notice);
}
function clientSubscriptionsHtml(d) {
  const tariffs={fixed:'Негибкий',flex:'Гибкий',pkg:'Арктика + ОФП',arena_only:'Только Арктика',single:'Разовое занятие'};
  const card=x=>{
    const cancellation=(d.subscription_cancellations||[]).find(c=>c.subscription_id===x.id);
    const paid=x.status==='cancelled'&&cancellation?.period_verified?cancellation.paid_before:x.status==='paid'?x.price:Math.min(x.price,(d.balance.covered||{})[x.id]||0),left=Math.max(0,x.price-paid),cancelled=x.status==='cancelled';
    const status=cancelled?'Отменён':!left?'Оплачен':paid?'Оплачен частично':'Ожидает оплаты';
    const own=d.enrollments.find(e=>e.id===x.enrollment_id),active=own&&x.kind!=='single'&&!cancelled&&x.month>=d.cur_month;
    return `<li class="subscription-card ${cancelled?'is-cancelled':''}"><div class="finance-card-head"><div><h4>${x.kind==='single'?`Разовое занятие${x.single_date?' · '+dShort(x.single_date):''}`:monthLabel(x.month)}</h4><div class="muted">${esc(clientGroupLabel(x))}</div></div><span class="pill ${!left&&!cancelled?'green':cancelled?'':'yellow'}">${status}</span></div>
      <div class="small muted subscription-meta">${esc(tariffs[x.tariff]||'Абонемент')} · ${x.lessons} ${plural(x.lessons,'занятие','занятия','занятий')}</div>
      <div class="finance-numbers"><div><small>Стоимость</small><b>${rub(x.price)}</b></div><div><small>Оплачено</small><b>${rub(paid)}</b></div><div><small>${cancelled?'Статус':'Осталось оплатить'}</small><b class="${left&&!cancelled?'finance-due':''}">${cancelled?'Отменён':rub(left)}</b></div></div>
      ${x.credit_lessons||x.late_fee||x.discount_amount||x.corrected||x.excluded_dates&&x.excluded_dates!=='[]'?`<details class="finance-detail"><summary>Расчёт и условия</summary>${x.credit_lessons?`<div>Перерасчёт пропусков: ${x.credit_lessons}</div>`:''}${x.late_fee?`<div>Надбавка: ${rub(x.late_fee)}</div>`:''}${x.discount_amount?`<div>Скидка: ${rub(x.discount_amount)}</div>`:''}${x.corrected?'<div>Сумма скорректирована</div>':''}${x.excluded_dates&&x.excluded_dates!=='[]'?`<div>Исключены: ${JSON.parse(x.excluded_dates).map(dShort).join(', ')}</div>`:''}</details>`:''}
      ${!cancelled&&can('members','full')?`<div class="sc-actions"><button type="button" class="btn sm danger" data-pcanc="${x.id}">Удалить абонемент</button></div><div data-sub-cancel-box="${x.id}"></div>`:''}
      ${active?`<button type="button" class="btn sm" data-sub-transfer="${x.id}">Поменять группу</button><div class="subscription-transfer" data-sub-transfer-box="${x.id}" hidden></div>`:''}</li>`;
  };
  return `<h3>Абонементы</h3><p class="small muted">Условия и состояние оплаты каждого абонемента.</p>${d.subscriptions.length?`<ul class="sub-list ${d.subscriptions.length>3?'folded':''}">${d.subscriptions.map(card).join('')}</ul>`:'<p class="muted">Абонементов пока нет. Оформите первый кнопкой «Продать абонемент».</p>'}${d.subscriptions.length>3?`<button class="linkish" id="sub-more">Показать историю (${d.subscriptions.length})</button>`:''}${subscriptionRefundsHtml(d)}`;
}
function subscriptionCancellationsHtml(d) {
  const rows=d.subscription_cancellations||[];if(!rows.length)return '';
  return `<h4>Расчёты после отмены</h4>${rows.map(c=>`<article class="subscription-refund"><b>Абонемент №${c.subscription_id} · ${c.mode==='balance'?'Средства оставлены на балансе':'Возврат клиенту'}</b>${!c.period_verified?'<p class="pill yellow">Требуется сверка будущего периода: отмена оформлена по старому расчёту. Сумму пока нельзя считать проверенной.</p>':`<p>Занятия прекращены с ${esc(c.effective_date)}.</p><div class="finance-numbers"><div><small>Прошедший период</small><b>${rub(c.retained_amount)}</b></div><div><small>${c.mode==='balance'?'Средства оставлены на балансе':'Сумма к возврату'}</small><b>${rub(c.amount)}</b></div></div>${c.mode==='balance'?`<p>В момент отмены: ${rub(c.balance_available)} осталось свободно, ${rub(c.applied_to_invoices)} зачтено в другие абонементы.</p>`:''}`}<p class="small muted">Свободный баланс сейчас: <b>${rub(d.balance.deposit)}</b>. Деньги, зачтённые в другие счета, уже отражены в их оплате.</p></article>`).join('')}`;
}

function subscriptionRefundsHtml(d) {
  const rows=d.subscription_refunds||[];
  if(!rows.length)return '';
  return `<h4>Возвраты по отменённым абонементам</h4><div class="subscription-refunds">${rows.map(r=>`<article class="subscription-refund"><div class="finance-card-head"><b>${rub(r.amount)}</b><span class="pill ${r.status==='done'?'green':'yellow'}">${r.status==='done'?'Возврат завершён':r.status==='receipt_pending'?'Деньги возвращены · ждём чек':'Ожидает возврата'}</span></div><p>${r.method==='paykeeper'?`PayKeeper · исходный платёж №${esc(r.pk_payment_id)} · на исходный платёжный инструмент`:esc(PAY_METHODS[r.method]||r.method)}</p>${r.method==='paykeeper'&&r.status!=='done'&&r.paykeeper_url?`<a class="btn sm" href="${esc(r.paykeeper_url)}" target="_blank" rel="noopener noreferrer">Открыть PayKeeper</a><p class="small">Найдите исходный платёж по ID выше. Выполните возврат согласованной суммы на исходный платёжный инструмент и сформируйте фискальный чек «Возврат прихода». Затем нажмите «Проверить возврат и фискальный чек».</p>`:''}${r.status==='pending'?'<p class="small muted">Сумма зарезервирована и недоступна для новых занятий. Деньги ещё не отмечены как возвращённые.</p>':''}${r.receipt_id?`<p class="small">Чек возврата: ${esc(r.receipt_id)}</p>`:''}${r.proof?`<p class="small">${esc(r.proof)}</p>`:''}${r.status!=='done'&&can('payments','view')?`<button class="btn sm" data-refund-confirm="${r.id}">${r.method==='paykeeper'?'Проверить возврат и фискальный чек':r.status==='receipt_pending'?'Внести фискальный чек':'Подтвердить фактический возврат'}</button><div data-refund-form="${r.id}"></div>`:''}</article>`).join('')}</div>`;
}

async function openSubscriptionCancel(subid,button,reload) {
  const box=document.querySelector(`[data-sub-cancel-box="${subid}"]`);
  if(!box)return;
  button.disabled=true;
  try {
    const q=await api('GET',`/api/subscriptions/${subid}/cancel-preview`);
    if(q.cancelled){toast('Абонемент уже отменён',true);reload();return}
    let period=q;
    box.innerHTML=`<form class="subscription-cancel-form"><h4>Удаление абонемента</h4><p>Абонемент будет отменён и останется в истории. Оплачено: <b>${rub(q.paid)}</b>.</p><div class="field"><label>Прекратить будущие занятия с</label><input name="effective_date" type="date" required min="${esc(q.effective_date||addDays(S.meta.today,1))}" value="${esc(q.effective_date||addDays(S.meta.today,1))}"></div><div data-period-summary></div><p class="cancel-approval-warning">ПЕРЕД ЭТИМ ДЕЙСТВИЕМ СОГЛАСОВАТЬ С ЮЛИЕЙ</p><label class="check"><input name="approved" type="checkbox" required> Я согласовал(а) отмену и порядок расчёта с Юлией</label><div class="field"><label>Дата согласования</label><input name="approval" type="date" required></div><div class="field"><label>Причина отмены</label><textarea name="reason" required rows="2" maxlength="1000"></textarea></div><div class="field"><label>Как поступить с деньгами</label><select name="mode"><option value="balance">Оставить средства на балансе</option>${can('payments','view')?'<option value="refund">Вернуть клиенту</option>':''}</select></div>${q.refund_blocker?`<p class="small">Возврат пока недоступен: ${esc(q.refund_blocker)}</p>`:''}<div data-return-info hidden><p>Возвращается только оплата будущего периода, указанная в расчёте выше. Прошедшие даты и сегодняшние занятия не возвращаются.</p>${q.sources.map(p=>`<p>${rub(p.amount)} · ${p.method==='paykeeper'?`PayKeeper, исходный платёж №${esc(p.pk_payment_id)}. Возврат выполняется в кабинете на тот же платёжный инструмент. Обязательно сформируйте чек «Возврат прихода».`:esc(PAY_METHODS[p.method]||p.method)+'. Возврат исходным способом с документом и чеком.'}</p>`).join('')}<p class="small muted">После отмены сумма будет зарезервирована. Подтвердите фактический возврат и чек в блоке возвратов.</p></div><div class="small need-err"></div><div class="sc-actions"><button class="btn danger" type="submit">Отменить абонемент</button><button class="btn" type="button" data-cancel-close>Назад</button></div></form>`;
    const form=box.querySelector('form');
    const drawPeriod=()=>{
      box.querySelector('[data-period-summary]').innerHTML=period.period_blocker?`<p class="need-err">${esc(period.period_blocker)}</p>`:`<div class="finance-numbers"><div><small>Удержано за прошедший период</small><b>${rub(period.retained_amount)}</b></div><div><small>Стоимость будущих занятий</small><b>${rub(period.future_amount)}</b></div><div><small>Можно сохранить или вернуть</small><b>${rub(period.releasable)}</b></div></div><p class="small muted">Остаётся в расчёте ${period.retained_lessons} занятий; отменяется ${period.future_lessons}. При частичной оплате сначала покрывается прошедший период.</p>`;
      const isRefund=form.elements.mode.value==='refund';
      box.querySelector('[data-return-info]').hidden=!isRefund;
      if(isRefund)box.querySelector('[data-return-info]').innerHTML=`<p>Сумма возврата за будущий период: <b>${rub(period.releasable||0)}</b>.</p>${(period.sources||[]).map(p=>`<p>${rub(p.amount)} · ${p.method==='paykeeper'?`PayKeeper, исходный платёж №${esc(p.pk_payment_id)}. Возврат на исходный платёжный инструмент с фискальным чеком «Возврат прихода».`:esc(PAY_METHODS[p.method]||p.method)}</p>`).join('')}${period.refund_blocker?`<p class="need-err">${esc(period.refund_blocker)}</p>`:''}`;
      form.querySelector('[type=submit]').disabled=!!period.period_blocker||(isRefund&&(!!period.refund_blocker||!period.releasable));
    };
    let generation=0;
    form.elements.effective_date.onchange=async()=>{
      const current=++generation;form.querySelector('[type=submit]').disabled=true;
      try{const next=await api('GET',`/api/subscriptions/${subid}/cancel-preview?effective_date=${encodeURIComponent(form.elements.effective_date.value)}`);if(current!==generation)return;period=next;drawPeriod()}
      catch(e){if(current===generation)box.querySelector('.need-err').textContent=e.message}
    };
    form.elements.mode.onchange=drawPeriod;drawPeriod();
    box.querySelector('[data-cancel-close]').onclick=()=>{box.innerHTML='';button.disabled=false};
    form.onsubmit=async e=>{
      e.preventDefault();const submit=form.querySelector('[type=submit]');submit.disabled=true;
      try {const result=await api('POST',`/api/subscriptions/${subid}/cancel`,{mode:form.elements.mode.value,approved_by_yulia:form.elements.approved.checked,approval:form.elements.approval.value,reason:form.elements.reason.value,effective_date:form.elements.effective_date.value,expected_releasable:period.releasable});toast(form.elements.mode.value==='refund'?'Абонемент отменён. Теперь выполните возврат исходным способом и проверьте чек':`Средства оставлены на балансе: ${rub(result.amount||0)}. Свободно: ${rub(result.balance_available||0)}, зачтено в другие счета: ${rub(result.applied_to_invoices||0)}`);reload()}
      catch(err){box.querySelector('.need-err').textContent=err.message;submit.disabled=false}
    };
  }catch(e){fail(e);button.disabled=false}
}

async function confirmSubscriptionRefund(rid,button,d,reload) {
  const r=(d.subscription_refunds||[]).find(r=>r.id===rid);if(!r)return;
  if(r.method==='paykeeper') {
    button.disabled=true;
    try {const result=await api('POST',`/api/subscription-refunds/${rid}/confirm`,{});toast(result.message||'Возврат и фискальный чек подтверждены',result.status!=='done');reload()}
    catch(e){fail(e);button.disabled=false}return;
  }
  const box=document.querySelector(`[data-refund-form="${rid}"]`);
  box.innerHTML=`<form class="subscription-cancel-form">${r.status==='pending'?`<p>Верните ${rub(r.amount)} исходным способом: ${esc(PAY_METHODS[r.method]||r.method)}.</p><label class="check"><input name="returned" type="checkbox" required> Деньги фактически возвращены</label><div class="field"><label>Документ или номер операции возврата</label><input name="proof" required maxlength="500"></div>`:''}<p>Сформируйте фискальный чек «Возврат прихода» и внесите его реквизиты. Если чек ещё не готов, можно сначала подтвердить возврат денег.</p><div class="row"><div class="field"><label>ФН</label><input name="receipt_fn" inputmode="numeric"></div><div class="field"><label>ФД</label><input name="receipt_fd" inputmode="numeric"></div><div class="field"><label>ФП</label><input name="receipt_fp" inputmode="numeric"></div></div><div class="small need-err"></div><button class="btn" type="submit">Сохранить подтверждение</button></form>`;
  box.querySelector('form').onsubmit=async e=>{e.preventDefault();const form=e.target,submit=form.querySelector('button');submit.disabled=true;try{const result=await api('POST',`/api/subscription-refunds/${rid}/confirm`,{actually_returned:form.elements.returned?.checked,proof:form.elements.proof?.value,receipt_fn:form.elements.receipt_fn.value,receipt_fd:form.elements.receipt_fd.value,receipt_fp:form.elements.receipt_fp.value});toast(result.message||'Подтверждение сохранено',result.status!=='done');reload()}catch(err){box.querySelector('.need-err').textContent=err.message;submit.disabled=false}};
}

function clientBalanceHtml(d) {
  return `<h3>Оплаты и баланс</h3><div class="balance-summary"><div class="${d.balance.debt?'has-debt':''}"><small>Задолженность</small><b>${rub(d.balance.debt)}</b><span>По прошедшим и текущим счетам</span></div><div><small>Будущие занятия</small><b>${rub(d.balance.upcoming)}</b><span>К оплате за следующий месяц</span></div><div><small>Деньги на балансе</small><b>${rub(d.balance.deposit)}</b><span>Доступны для оплаты занятий</span></div></div>
    ${(d.unpaid_cur||[]).length?`<div class="finance-note">За текущий месяц ещё не оформлен или не оплачен абонемент: ${(d.unpaid_cur||[]).map(esc).join(', ')}</div>`:''}
    ${subscriptionCancellationsHtml(d)}<div id="pay-form"></div><div id="pay-links"></div>
    <h4 class="finance-history-title">История денег</h4>${d.payments.length?`<ul class="pay-list ${d.payments.length>4?'folded':''}">${d.payments.map(p=>`<li class="payment-card ${p.cancelled?'is-cancelled':''}"><div class="finance-card-head"><div><b class="payment-amount ${p.amount<0?'refund':''}">${p.amount<0?'−':'+'}${rub(Math.abs(p.amount))}</b><div class="small muted">${p.method==='refund_pending'?'Зарезервировано для возврата':p.amount<0?'Возврат':'Поступление'} · ${esc(PAY_METHODS[p.method]||p.method)}</div></div><time>${dtShort(p.paid_at)}</time></div>${p.comment?`<div class="payment-comment">${esc(p.comment)}</div>`:''}<div class="small muted">${esc(p.user_name||'')}${p.cancelled?' · отменён':p.confirmed?'':' · ждёт подтверждения'}</div>${S.user.role==='admin'&&!p.cancelled&&!['unknown','refund_pending','refund'].includes(p.method)?`<details class="finance-detail"><summary>Действия с платежом</summary>${p.confirmed?'':`<button class="btn sm" data-pconf="${p.id}">Подтвердить</button>`}<button class="btn sm danger" data-pcancel="${p.id}">Отменить платёж</button></details>`:''}</li>`).join('')}</ul>`:'<p class="muted">Платежей пока нет</p>'}${d.payments.length>4?`<button class="linkish" id="pay-more">Показать всю историю (${d.payments.length})</button>`:''}
    ${d.family_kids.length||d.balance.deposit?`<details class="finance-detail"><summary>Другие операции</summary><div class="sc-actions">${d.family_kids.length?'<button class="btn sm" id="pay-fam">Оплата за семью</button>':''}${d.balance.deposit&&d.family_kids.length?'<button class="btn sm" id="bal-move">Перенести баланс другому ребёнку</button>':''}${d.balance.deposit&&can('payments','view')?'<button class="btn sm" id="bal-refund">Вернуть деньги с баланса</button>':''}</div></details>`:''}`;
}
async function loadClientPaymentLinks(sid) {
  const box=$('#pay-links');if(!box)return;
  const data=await api('GET',`/api/students/${sid}/paylinks`).catch(()=>({links:[]}));if($('#pay-links')!==box)return;
  const links=data.links.filter(l=>l.status==='open');
  box.innerHTML=links.length?`<details class="finance-detail payment-links"><summary>Ссылки, по которым ждём оплату (${links.length})</summary>${links.map(l=>`<div class="payment-link-card"><b>${rub(l.amount)}</b><div>${esc(l.purpose)}</div><div class="small muted">Создана ${dtShort(l.created_at)}</div><button class="btn sm" data-plcopy="${esc(l.url)}">Скопировать ссылку</button><details class="finance-detail"><summary>Другие действия</summary><button class="btn sm" data-plx="${l.id}">Отменить ссылку</button></details></div>`).join('')}</details>`:'';
  box.querySelectorAll('[data-plcopy]').forEach(b=>b.onclick=()=>copyTextWithNotice(b.dataset.plcopy,'Ссылка скопирована'));
  box.querySelectorAll('[data-plx]').forEach(b=>b.onclick=async()=>{await api('POST',`/api/paylinks/${b.dataset.plx}/cancel`,{});loadClientPaymentLinks(sid)});
}

const NEXT_GO = { reply: "Ответить", call: "Позвонить", offer: "Предложить дни", trial: "Записать", sell: "Продать", link: "Ссылка на оплату", makeup: "Отработка", task: "К задаче" };
async function openStudent(id, options = {}) {
  const request=S.studentCardRequest=(S.studentCardRequest||0)+1;
  const previous = history.state?.studentCard;
  const tab = options.tab || (previous?.id === +id && previous.userId === S.user?.id ? previous.tab : "feed") || "feed";
  let d;
  try { d = await api("GET", `/api/students/${id}`); }
  catch (e) { if(request!==S.studentCardRequest)return; if (options.restoring) closeDrawer(); return fail(e); }
  if(request!==S.studentCardRequest)return;
  const s = d.student, g = d.group, c = d.counters;
  const groups = S.meta.groups.filter((x) => !x.is_makeup && !x.program_id);
  const makeupGroups = S.meta.groups.filter((x) => x.is_makeup);
  const isMember = s.funnel === "members";

  const stageOpts = isMember
    ? [["active", "Занимается"], ["left", "Ушёл"]]
    : S.meta.sales_stages.filter((x) => !x.auto).map((x) => [x.key, x.title]);

  const regular = d.enrollments.filter((e) => e.lesson_kind !== "ofp");
  const full = can("members", "full");
  const alerts = [
    s.priority ? `<span class="pill red">🔥 Заявка с сайта: обработать первой</span>` : "",





    ...(d.trial_hold || []).map((h) => `<span class="pill yellow" title="Пока родитель присылает данные, другим это время не предлагается">⏳ Держим место на пробное ${esc(dShort(h.date))} в ${esc(h.time)} до ${esc(h.until.slice(11, 16))}</span>`),
    ...(d.unpaid_cur || []).map((g) => `<span class="pill red" title="Абонемент за этот месяц по этой группе не оплачен и счёт не выставлен">Не оплачен ${esc(monthLabel(S.meta.today.slice(0, 7)).split(" ")[0])}: ${esc(g)}</span>`),


    !s.birth ? `<span class="pill yellow">Нет даты рождения</span>` : "",
    ...d.enrollments.filter((e) => e.age_warn).map((e) => `<span class="pill red">⚠️ ${esc(e.group_name)}: ${esc(e.age_warn)}</span>`),
    d.trial_age_warn ? `<span class="pill red">⚠️ Пробное ${esc(d.trial_age_warn)}</span>` : "",
    ...(d.chains || []).map((r) => `<span class="pill chain-pill">🔁 Автоцепочка «${esc(r.title)}»: отправлено ${r.step} из ${r.total} <button class="chip-x" data-chstop="${r.id}" title="Остановить цепочку">×</button></span>`),
  ].filter(Boolean).join("");
  const html = `
  <div class="sc-head">
    <div class="sc-title"><h2>${esc(s.name)}</h2>
      <span class="muted">${esc(d.stage_label)}${s.birth ? " · " + esc(ageText(s.birth)) : ""}${s.archived ? " · в архиве" : ""}</span>
      ${d.lead_score ? `<span class="score ${d.lead_score.level}" title="${esc(d.lead_score.why.join(", ") || "")}">${d.lead_score.label}</span>` : ""}</div>
    ${d.siblings.length ? `<div class="chips sc-kids"><span class="chip on">${esc(s.name.split(" ")[0])}</span>${d.siblings.map((x) => `<button class="chip" data-open="${x.id}">${esc(x.name)}</button>`).join("")}</div>` : ""}
    <div class="sc-meta">${isMember ? (regular.length ? regular.map((e) => `<span class="pill">${esc(e.group_name)}</span>`).join(" ") : `<span class="muted small">Группа не выбрана</span>`)
      : `${s.trial_at ? `<span class="pill">Пробное ${dtShort(s.trial_at)}</span>` : ""}`}</div>
    <div class="sc-parents">${d.parents.map((p) => `<span class="sc-parent"><b>${esc(p.name)}</b> <button class="rel-chip ${p.relation ? "" : "empty"}" data-rel="${p.id}" title="Кем приходится ребёнку">${p.relation ? esc(p.relation) : "кем приходится?"}</button>${p.phone ? ` <button class="linkish" data-copy-phone="${esc(p.phone)}" title="Скопировать номер">${esc(p.phone)}</button> <button class="icon-btn" data-dial="${esc(p.phone)}" title="Позвонить через Sipuni">📞</button>` : ""}${p.messenger ? ` <span class="pill">${esc(p.messenger)}</span>` : ""}${p.email ? ` <span class="small muted">✉ ${esc(p.email)}</span>` : ""}</span>`).join("")}</div>
    ${isMember && d.balance.debt ? `<div class="sc-debt-summary">Задолженность <b>${rub(d.balance.debt)}</b></div>` : ""}
    ${d.family.note ? `<div class="sc-staff-note"><b>Заметка для команды</b><div>${esc(d.family.note)}</div></div>` : ""}
    ${alerts ? `<div class="sc-alerts">${alerts}</div>` : ""}
    ${d.next_step && !(d.next_step.action === "task" && d.tasks?.open?.length) ? `<div class="next-step"><span><b>Задача:</b> ${esc(d.next_step.text)}</span><button class="btn sm primary" id="ns-go">${NEXT_GO[d.next_step.action] || "Сделать"}</button></div>` : ""}
    ${!isMember && d.stage_tip ? `<details class="stage-tip" ${S.tipsOpen === true ? "open" : ""}><summary>💡 Подсказка на этапе «${esc(d.stage_label)}»</summary>
      <div><b>Что сказать.</b> ${esc(d.stage_tip.say)}</div><div style="margin-top:4px"><b>Чтобы перейти дальше:</b> ${esc(d.stage_tip.next)}</div></details>` : ""}
    <div class="sc-actions">
      ${isMember && full ? `<button class="btn primary" id="act-sell">🛒 Продать абонемент</button>` : ""}
      ${!isMember ? `<button class="btn primary" id="act-trial">📅 Записать на пробное</button>` : ""}
      ${d.parents.some((p) => p.phone) ? `<button class="btn" id="act-call">📞 Позвонить</button>` : ""}
      ${isMember ? `<details class="sc-extra-actions"><summary class="btn">Ещё</summary><div><button class="btn" id="act-vac">Отпуск</button></div></details>` : ""}
    </div>
    ${!isMember ? '<div id="sc-trial-booking" hidden></div>' : ""}
  </div>
  <div class="sc-tasks">${cardTasksBlock(d)}</div>
  <div class="sc-tabs" role="tablist" aria-label="Разделы карточки"><button class="on" role="tab" aria-selected="true" data-sctab="feed">Общение</button><button role="tab" aria-selected="false" data-sctab="abon">Абонементы и оплаты</button><button role="tab" aria-selected="false" data-sctab="visits">Посещения</button><button role="tab" aria-selected="false" data-sctab="info">Данные клиента</button></div>
  <div class="sc-grid" data-tab="feed"><div class="sc-left"></div><div class="sc-right"><div id="sell-box"></div></div><div class="sc-info"></div><div class="sc-attendance"></div></div>

  <div class="section" data-sc="stage">
    ${s.erp_status ? `<p class="small muted">Статус при переносе из ERP: ${esc(s.erp_status)}</p>` : ""}
    ${(d.nodeadline || []).length ? d.nodeadline.map((n) => `<div class="nd-in">⏸ В воронке без дедлайнов <b>«${esc(n.title)}»</b> с ${dShort(n.added_at)}${n.note ? `<div class="small muted">${esc(n.note)}</div>` : ""}
      <button class="btn sm" id="nd-out" type="button">Убрать из воронки</button></div>`).join("")
      : `<button class="btn sm" id="nd-in" type="button" style="margin-bottom:10px" title="Без задач по сроку: позвоним, когда появится акция">⏸ В «Ждут акцию»</button>`}
    <div class="row">
      <div class="field"><label>Этап</label><select id="f-stage">${stageOpts.map(([k, t]) => `<option value="${k}" ${k === s.stage ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></div>
      ${!isMember ? `<div class="field"><label>Тариф</label><select id="f-tariff"><option value="fixed" ${s.tariff === "fixed" ? "selected" : ""}>Негибкий</option><option value="flex" ${s.tariff === "flex" ? "selected" : ""}>Гибкий</option></select></div>` : ""}
    </div>
    ${!isMember ? `<div class="field"><label>Филиал</label><select id="f-branch"><option value="">— не выбран —</option>${S.meta.branches.filter((b) => b.active).map((b) => `<option value="${b.id}" ${b.id === s.branch_id ? "selected" : ""}>${esc(b.name)}</option>`).join("")}</select></div>` : ""}
    ${!isMember ? `<div class="field"><label>Группа для пробного</label><select id="f-group"><option value="">— не выбрана —</option>${groups.filter((x) => x.trial_ok !== 0 || x.id === s.group_id).map((x) => `<option value="${x.id}" ${x.id === s.group_id ? "selected" : ""}>${esc(x.name)} · ${esc(x.age)} лет</option>`).join("")}</select></div>
    ${d.trial_age_warn ? `<div class="alert" style="margin-bottom:8px">⚠️ Группа для пробного ${esc(d.trial_age_warn)}. Проверьте, точно ли сюда</div>` : ""}
    <div class="field"><label>Пробное занятие</label><input id="f-trial" type="datetime-local" value="${esc(s.trial_at || "")}"></div>
    <div id="trial-occ" class="small"></div><div id="trial-slots"></div>` : ""}
    <div class="field"><label>Откуда узнали</label>${sourceSelect("f-source", s.source_id, s.source)}</div>
  </div>

  <div class="section" data-sc="family" data-title="👪 Семья и заметка">
    <h3>Семья ${esc(d.family.name)}</h3>
    <div class="field"><label>Заметка для персонала (видна в шапке карточки)</label><textarea id="fam-note" rows="2" placeholder="Например: аллергия, звонить только маме">${esc(d.family.note || "")}</textarea></div>
    ${consentHtml(d.family)}
    <dl class="sc-family-contacts">${d.parents.map((p) => `<div class="sc-contact-line"><dt>${esc(p.name)}</dt><dd>${p.phone ? `<button class="linkish" data-copy-phone="${esc(p.phone)}" title="Скопировать номер">${esc(p.phone)}</button>` : `<span class="muted">Телефон не указан</span>`}${p.messenger ? ` <span class="pill">${esc(p.messenger)}</span>` : ""}</dd></div>`).join("")}</dl>
    ${d.siblings.length ? `<div style="margin-top:10px" class="chips">${d.siblings.map((x) => `<button class="chip" data-open="${x.id}">${esc(x.name)}</button>`).join("")}</div>` : ""}
    <button class="btn sm" style="margin-top:10px" id="add-sibling">+ Брат или сестра</button>
  </div>

  <div data-sc="pu">${puCardBlock(d)}</div>
  <div class="section" id="comms-box"><h3>Связь с семьёй</h3><div class="muted small">Загружаю переписку…</div></div>

  <div class="section" data-sc="visits" data-title="Посещения и отработки">${clientAttendanceMarkup()}</div>

  <div class="section" data-sc="subs">${clientSubscriptionsHtml(d)}</div>
  <div class="section" data-sc="balance">${clientBalanceHtml(d)}</div>

  <div class="section" data-sc="vac" data-title="🌴 Отпуск">
    <h3>Отпуск</h3>
    <div class="row">
      <div class="field"><label>С</label><input id="v-start" type="date"></div>
      <div class="field"><label>По</label><input id="v-end" type="date"></div>
    </div>
    <button class="btn sm" id="v-add">Добавить отпуск</button>
    ${d.vacations.length ? `<ul class="history" style="margin-top:10px">${d.vacations.map((v) => `<li>${dShort(v.start)}–${dShort(v.end)} <button class="btn sm danger" data-vdel="${v.id}">Удалить</button></li>`).join("")}</ul>` : ""}
    <div class="small muted" style="margin-top:6px">Дни отпуска не входят в цену абонемента. Предупредить нужно до оплаты месяца.</div>
  </div>

  <div class="section" data-sc="hist" data-title="🕘 История и комментарии">
    <h3>История</h3>
    ${isOwner() ? `<button class="btn" id="erp-full-client">Исходные данные ERP</button><div id="erp-full-client-out"></div>` : ''}
    <div class="row" style="margin-bottom:10px"><input id="note" placeholder="Комментарий, итог звонка…"><button class="btn sm" id="note-add" style="flex:0">Добавить</button></div>
    <ul class="history">${d.history.map((h) => `<li><div class="when">${dtShort(h.ts)} · ${esc(h.user_name)}</div>${esc(h.text)}</li>`).join("")}</ul>
  </div>
  <div style="margin-top:14px" data-sc="arch"><button class="btn sm ${s.archived ? "" : "danger"}" id="s-arch">${s.archived ? "Вернуть из архива" : "Перенести в архив"}</button></div>`;
  openDrawer(html, "wide");
  try { history.replaceState({ ...history.state, studentCard: { ...(previous?.id === +id ? previous : {}), id: +id, userId: S.user.id, tab } }, ""); } catch (_) {}
  const body = $("#drawer-body");
  erpFullClient(body, id);
  layoutStudentCard(body, isMember);
  scTab(tab);
  const reload = () => { openStudent(id); render(); };
  wireClientAttendance(body,d,reload);
  body.querySelectorAll("[data-sub-transfer]").forEach(b => b.onclick = () => {
    const sub=d.subscriptions.find(s=>s.id===+b.dataset.subTransfer), e=d.enrollments.find(e=>e.id===sub.enrollment_id), box=body.querySelector(`[data-sub-transfer-box="${sub.id}"]`);
    openTransfer(e.id,box,d,groups,reload,sub.month>d.cur_month?sub.month+'-01':S.meta.today);
  });
  const patch = async (data) => { try { await api("PATCH", `/api/students/${id}`, data); reload(); } catch (e) { fail(e); } };

  $("#f-stage").onchange = async (e) => {
    if (s.funnel === "sales" && e.target.value === "refused") return askRefusal(id, reload);
    if (s.funnel === "sales" && e.target.value === "reinvite_pu") return askReinvite(id, reload);
    if (s.funnel === "members" && e.target.value === "left") return askLeftReason(id, reload);
    try { await api("POST", `/api/students/${id}/move`, { funnel: s.funnel, stage: e.target.value }); reload(); } catch (err) { fail(err); }
  };
  if ($("#f-tariff")) $("#f-tariff").onchange = (e) => patch({ tariff: e.target.value });
  if ($("#f-branch")) $("#f-branch").onchange = (e) => patch({ branch_id: e.target.value ? +e.target.value : "" });
  if ($("#f-group")) $("#f-group").onchange = (e) => patch({ group_id: e.target.value ? +e.target.value : "" });
  const enPatch = async (eid, data) => { try { await api("PATCH", `/api/enrollments/${eid}`, data); reload(); } catch (e) { fail(e); } };
  body.querySelectorAll("[data-enday]").forEach((b) => (b.onclick = () => {
    const eid = +b.dataset.enday;
    const days = [...body.querySelectorAll(`[data-enday="${eid}"]`)].filter((x) => (x === b ? !x.classList.contains("on") : x.classList.contains("on"))).map((x) => +x.dataset.wd);
    if (!days.length) return toast("Оставьте хотя бы один день", true);
    enPatch(eid, { weekdays: days });
  }));
  body.querySelectorAll("[data-entar]").forEach((sel) => (sel.onchange = () => enPatch(+sel.dataset.entar, { tariff: sel.value })));
  body.querySelectorAll("[data-entr]").forEach((b) => (b.onclick = () => openTransfer(+b.dataset.entr, body.querySelector(`[data-trbox="${b.dataset.entr}"]`), d, groups, reload)));
  body.querySelectorAll("[data-enoff]").forEach((b) => (b.onclick = () => { if (confirm("Ученик больше не ходит в эту группу?")) enPatch(+b.dataset.enoff, { active: false }); }));
  if ($("#en-add")) $("#en-add").onclick = async () => {
    if (!$("#en-group").value) return toast("Выберите группу", true);
    try { await api("POST", `/api/students/${id}/enrollments`, { group_id: +$("#en-group").value, tariff: $("#en-tariff").value }); toast("Группа добавлена"); reload(); } catch (e) { fail(e); }
  };
  if ($("#f-trial")) $("#f-trial").onchange = (e) => patch({ trial_at: e.target.value });
  $("#f-source").onchange = (e) => patch({ source_id: e.target.value ? +e.target.value : "" });
  body.querySelectorAll("[data-open]").forEach((b) => (b.onclick = () => openStudent(+b.dataset.open)));
  $("#add-sibling").onclick = () => openNewClient({ family_id: s.family_id, family_name: d.family.name });
  loadFamilyComms(s.family_id, d.parents, id, { id, name: s.name, funnel: s.funnel, birth: s.birth, onBooked: reload });
  if (document.querySelector(".sc-tasks")) wireCardTasks(document.querySelector(".sc-tasks"), reload, id);
  // кем приходится ребёнку: нажали на метку, выбрали из списка
  document.querySelectorAll("[data-rel]").forEach((b) => (b.onclick = () => {
    const REL = ["мама", "папа", "бабушка", "дедушка", "тётя", "дядя", "сестра", "брат", "няня", "опекун", "другое"];
    const sel = document.createElement("select");
    sel.className = "rel-chip";
    sel.innerHTML = `<option value="">кем приходится?</option>` + REL.map((r) => `<option ${b.textContent === r ? "selected" : ""}>${r}</option>`).join("");
    b.replaceWith(sel); sel.focus();
    sel.onchange = async () => { try { await api("PATCH", `/api/parents/${b.dataset.rel}/relation`, { relation: sel.value }); toast("Сохранено"); reload(); } catch (e) { fail(e); } };
  }));
  wireConsent(d.family, reload);
  body.querySelectorAll(".sc-head [data-dial]").forEach((b) => (b.onclick = () => dial(b.dataset.dial)));
  const firstPhone = (d.parents.find((p) => p.phone) || {}).phone;
  if ($("#act-call")) $("#act-call").onclick = () => dial(firstPhone);
  if ($("#act-sell")) $("#act-sell").onclick = () => openSell(d, reload);
  if ($("#act-vac")) $("#act-vac").onclick = () => openFold(body, "vac");
  if ($("#act-move")) $("#act-move").onclick = () => { openFold(body, "abon"); const b = body.querySelector("[data-entr]"); if (b) { b.click(); b.scrollIntoView({ block: "center", behavior: "smooth" }); } };
  if ($("#act-trial")) $("#act-trial").onclick = () => openInlineTrialBooking(d, body.querySelector("#sc-trial-booking"), reload);
  body.querySelectorAll("[data-chstop]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Остановить автоцепочку для этого клиента? Больше сообщений по ней не уйдёт")) return;
    try { await api("POST", `/api/chain-runs/${b.dataset.chstop}/stop`, {}); toast("Цепочка остановлена"); reload(); } catch (e) { fail(e); }
  }));
  const tip = body.querySelector(".stage-tip"); if (tip) tip.addEventListener("toggle", () => { S.tipsOpen = tip.open; });
  if ($("#ns-go")) $("#ns-go").onclick = () => {
    const a = d.next_step.action, click = (sel) => { const b = $(sel); if (b) { b.click(); return true; } return false; };
    if (a === "reply") { scTab("feed"); const t = $("#cm-text"); if (t) { t.scrollIntoView({ block: "center", behavior: "smooth" }); t.focus(); } }
    else if (a === "call") dial(firstPhone);
    else if (a === "offer") { scTab("feed"); click("#to-open"); }
    else if (a === "trial") click("#act-trial");
    else if (a === "sell") { if (!click("#act-sell")) { openFold(body, "stage"); const f = $("#f-stage"); if (f) { f.scrollIntoView({ block: "center", behavior: "smooth" }); f.focus(); } } }
    else if (a === "link") { click("#act-sell"); }
    else if (a === "makeup") openFold(body, "skips");
    else if (a === "task") { closeDrawer(); go("planner"); }
  };
  if ($("#fam-note")) $("#fam-note").onchange = async (e) => { try { await api("PATCH", `/api/families/${s.family_id}`, { note: e.target.value }); toast("Заметка сохранена"); reload(); } catch (err) { fail(err); } };

  if (isMember) loadWarnBox(s.family_id, $("#warn-box"), id);
  if ($("#mk-slots")) loadSlots($("#mk-slots"), async (x) => {
    if (!confirm(`Записать ${s.name} на отработку ${dayLabel(x.date)} ${x.time} в группу ${x.group}?`)) return;
    try { await api("POST", `/api/students/${id}/makeups`, { group_id: x.group_id, date: x.date }); toast("Записан на отработку"); reload(); } catch (e) { fail(e); }
  }, "Или на свободное место в обычной группе (жёлтые освободились, потому что кто-то предупредил):", s.birth);
  if ($("#trial-slots")) {
    if (s.group_id && s.trial_at) api("GET", `/api/occupancy?group_id=${s.group_id}&date=${s.trial_at.slice(0, 10)}`).then((o) => {
      if (o.limit) $("#trial-occ").innerHTML = `На эту тренировку записано <b>${o.count} из ${o.limit}</b>${o.warned.length ? ` (предупредили, что не придут: ${esc(o.warned.join(", "))})` : ""}`;
    }).catch(() => {});
    loadSlots($("#trial-slots"), async (x) => {
      if (!confirm(`Записать на пробное ${dayLabel(x.date)} ${x.time}, группа ${x.group}?`)) return;
      patch({ group_id: x.group_id, trial_at: `${x.date}T${x.time || "18:00"}` });
    }, "Свободные места на пробное (подходят по возрасту):", s.birth);
  }
  if ($("#cm-group")) wireCardMark(id, reload);
  wireSingles(id, reload);
  if ($("#nd-in")) $("#nd-in").onclick = async () => {
    const note = window.prompt("Почему ждём акцию? (необязательно)", "");
    if (note === null) return;
    try { await api("POST", "/api/nodeadline/add", { student_id: id, funnel: "promo", note }); toast("В воронке «Ждут акцию»"); reload(); } catch (e) { fail(e); }
  };
  if ($("#nd-out")) $("#nd-out").onclick = async () => {
    try { await api("POST", "/api/nodeadline/remove", { student_id: id, why: "вручную" }); toast("Убран из воронки"); reload(); } catch (e) { fail(e); }
  };
  if ($("#mk-book")) $("#mk-book").onclick = async () => {
    try { await api("POST", `/api/students/${id}/makeups`, { group_id: +$("#mk-group").value, date: $("#mk-date").value }); toast("Записан на отработку"); reload(); } catch (e) { fail(e); }
  };
  body.querySelectorAll("[data-mk]").forEach((b) => (b.onclick = async () => {
    try { await api("POST", `/api/makeups/${b.dataset.mk}/status`, { status: b.dataset.st }); reload(); } catch (e) { fail(e); }
  }));

  const loadQuotes = () => body.querySelectorAll("[data-quote]").forEach(async (box) => {
    const eid = box.dataset.quote;
    try {
      const q = await api("GET", `/api/students/${id}/quote?month=${$("#q-month").value}&enrollment_id=${eid}${$("#q-disc").value ? "&discount_id=" + $("#q-disc").value : ""}`);
      box.classList.remove("muted");
      box.innerHTML = q.program ? programQuoteHtml(q) : quoteHtml(q);
    } catch (e) { box.textContent = e.message; }
  });
  wireProgramBlock(body, id, reload);
  wireWaitlistCard(body, id, reload);
  wirePuCard(body, id, reload);
  if ($("#q-month")) { $("#q-month").onchange = loadQuotes; $("#q-disc").onchange = loadQuotes; loadQuotes(); }
  body.querySelectorAll("[data-corr]").forEach((b) => (b.onclick = async () => {
    const price = prompt(`Корректировка цены (сейчас ${rub(+b.dataset.price)}). Новая сумма, ₽:`, b.dataset.price);
    if (price === null) return;
    const comment = prompt("Почему? Причина попадёт в отчёт руководителю");
    if (comment === null) return;
    try { await api("POST", `/api/subscriptions/${b.dataset.corr}/correct`, { price: +price, comment }); toast("Цена скорректирована"); reload(); } catch (e) { fail(e); }
  }));
  body.querySelectorAll("[data-inv]").forEach((b) => (b.onclick = async () => {
    try { const r = await api("POST", `/api/students/${id}/invoice`, { month: $("#q-month").value, enrollment_id: +b.dataset.inv, discount_id: $("#q-disc").value || null }); toast(`Счёт выставлен: ${rub(r.quote.total)}`); reload(); } catch (e) { fail(e); }
  }));
  const payForm = (title, amount, onSubmit) => {
    $("#pay-form").innerHTML = `<div class="quote" style="margin-top:10px"><b>${esc(title)}</b>
      <div class="row" style="margin-top:8px"><div class="field"><label>Сумма, ₽</label><input id="pf-sum" type="number" min="1" value="${amount || ""}"></div>
        <div class="field"><label>Способ</label><select id="pf-m">${Object.entries(PAY_METHODS).filter(([k]) => !PAY_INTERNAL.includes(k)).map(([k, t]) => `<option value="${k}">${t}</option>`).join("")}</select></div></div>
      <div class="field"><label>Комментарий</label><input id="pf-c" placeholder="Необязательно"></div>
      <button class="btn primary sm" id="pf-ok">Принять ${amount ? rub(amount) : "оплату"}</button></div>`;
    $("#pf-sum").oninput = () => { $("#pf-ok").textContent = "Принять " + rub($("#pf-sum").value); };
    $("#pf-ok").onclick = async () => { try { await onSubmit(+$("#pf-sum").value, $("#pf-m").value, $("#pf-c").value); toast("Оплата принята"); reload(); } catch (e) { fail(e); } };
    $("#pay-form").scrollIntoView({ block: "center", behavior: "smooth" });
  };
  body.querySelectorAll("[data-pay]").forEach((b) => (b.onclick = () => payForm("Оплата счёта", +b.dataset.left,
    (amount, method) => api("POST", `/api/subscriptions/${b.dataset.pay}/pay`, { amount, method }))));
  if ($("#sub-more")) $("#sub-more").onclick = (e) => { body.querySelector(".sub-list").classList.remove("folded"); e.target.remove(); };
  if ($("#pay-more")) $("#pay-more").onclick = (e) => { body.querySelector(".pay-list").classList.remove("folded"); e.target.remove(); };
  loadClientPaymentLinks(id);
  body.querySelectorAll("[data-pconf]").forEach((b) => (b.onclick = async () => { try { await api("POST", `/api/payments/${b.dataset.pconf}/confirm`, {}); reload(); } catch (e) { fail(e); } }));
  body.querySelectorAll("[data-pcancel]").forEach((b) => (b.onclick = async () => { if (!confirm("Отменить платёж? Он останется в реестре как отменённый.")) return; try { await api("POST", `/api/payments/${b.dataset.pcancel}/cancel`, {}); reload(); } catch (e) { fail(e); } }));
  body.querySelectorAll("[data-pcanc]").forEach(b=>b.onclick=()=>openSubscriptionCancel(+b.dataset.pcanc,b,reload));
  body.querySelectorAll('[data-refund-confirm]').forEach(b=>b.onclick=()=>confirmSubscriptionRefund(+b.dataset.refundConfirm,b,d,reload));
  body.querySelectorAll("[data-cert]").forEach((x) => (x.onchange = async () => {
    try { await api("POST", `/api/attendance/${x.dataset.cert}/cert`, { cert: x.checked }); toast(x.checked ? "Справка отмечена" : "Справка снята"); } catch (e) { fail(e); x.checked = !x.checked; }
  }));
  if ($("#pay-fam")) $("#pay-fam").onclick = () => payForm("Оплата за семью: закроет счета всех детей, остаток на баланс", 0,
    async (amount, method, comment) => { const r = await api("POST", `/api/students/${id}/pay-family`, { amount, method, comment });
      toast(r.parts.map((p) => `${p.name} ${rub(p.amount)}`).join(", ")); });
  if ($("#bal-move")) $("#bal-move").onclick = async () => {
    const kids = d.family_kids, pick = kids.length === 1 ? kids[0] : kids.find((k) => k.name === prompt("Кому перенести? " + kids.map((k) => k.name).join(", ")));
    if (!pick) return;
    const sum = prompt(`Сколько перенести на ${pick.name}? На балансе ${rub(d.balance.deposit)}`, d.balance.deposit);
    if (!sum) return;
    try { await api("POST", `/api/students/${id}/move-balance`, { to: pick.id, amount: +sum }); toast("Перенесено"); reload(); } catch (e) { fail(e); }
  };
  if ($("#bal-refund")) $("#bal-refund").onclick = async () => {
    const sum = prompt(`Сколько вернуть родителю? На балансе ${rub(d.balance.deposit)}`, d.balance.deposit); if (!sum) return;
    const comment = prompt("Почему возврат и как вернули деньги?"); if (!comment) return;
    try { await api("POST", `/api/students/${id}/refund`, { amount: +sum, comment }); toast("Возврат записан. Пробейте чек возврата"); reload(); } catch (e) { fail(e); }
  };
  body.querySelectorAll("[data-cancel]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Отменить счёт? Пропуски из перерасчёта вернутся.")) return;
    try { await api("POST", `/api/subscriptions/${b.dataset.cancel}/cancel`, {}); reload(); } catch (e) { fail(e); }
  }));

  $("#v-add").onclick = async () => {
    try { await api("POST", `/api/students/${id}/vacations`, { start: $("#v-start").value, end: $("#v-end").value }); toast("Отпуск добавлен"); reload(); } catch (e) { fail(e); }
  };
  body.querySelectorAll("[data-vdel]").forEach((b) => (b.onclick = async () => {
    try { await api("DELETE", `/api/vacations/${b.dataset.vdel}`); reload(); } catch (e) { fail(e); }
  }));
  $("#s-arch").onclick = async () => {
    if (!s.archived && !confirm("Перенести карточку в архив? Она пропадёт из воронок, вернуть можно в любой момент")) return;
    try { await api("POST", `/api/students/${id}/archive`, { archived: !s.archived }); toast(s.archived ? "Карточка возвращена" : "Карточка в архиве"); closeDrawer(); render(); } catch (e) { fail(e); }
  };
  $("#note-add").onclick = async () => {
    const t = $("#note").value.trim(); if (!t) return;
    try { await api("POST", `/api/students/${id}/note`, { text: t }); openStudent(id); } catch (e) { fail(e); }
  };
}

/* «Мои ученики» тренера: только просмотр, телефон родителя - позвонить одним нажатием */
async function renderCoachKids(page) {
  const d = await api("GET", "/api/coach/kids");
  const n = d.groups.reduce((a, g) => a + g.kids.length, 0);
  page.innerHTML = `<div class="page-head"><h2 class="grow">Мои ученики</h2><span class="muted small">${n} ${n % 10 === 1 && n % 100 !== 11 ? "ребёнок" : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? "ребёнка" : "детей"}</span></div>
    ${d.groups.length ? d.groups.map((g) => `<div class="section"><h3>${esc(g.name)} <span class="muted small">${esc(g.branch)} · ${esc(g.age || "")}${/\d$/.test(g.age || "") ? " лет" : ""} · ${g.main ? "ты главный тренер" : "ты помощник"}</span></h3>
      <div class="list" style="box-shadow:none">${g.kids.map((k) => `<div class="list-row"><div class="grow"><b>${esc(k.name)}</b>${k.birth ? ` <span class="muted small">${ageText(k.birth)}</span>` : ""}</div>
        <div class="small" style="text-align:right">${k.parents.map((p) => `${esc(p.name || "Родитель")}${p.phone ? ` <a href="tel:${esc(p.phone)}">${esc(fmtPhone(p.phone))}</a>` : ""}`).join("<br>") || `<span class="muted">телефона нет</span>`}</div></div>`).join("")
        || `<div class="list-row muted small">В группе пока нет детей</div>`}</div></div>`).join("")
      : `<div class="panel center" style="padding:40px">За тобой пока не закреплено ни одной группы. Спроси у старшего администратора</div>`}`;
}

function ageText(birth) {
  return ageYM(birth);
}

function isoDate(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; }
function nextSaturdays(n) {
  const out = [], d = new Date(); d.setHours(0, 0, 0, 0);
  while (d.getDay() !== 6) d.setDate(d.getDate() + 1);
  for (let i = 0; i < n; i++) { out.push(isoDate(d)); d.setDate(d.getDate() + 7); }
  return out;
}

/* ---------- новый клиент ---------- */
function openNewClient(preset = {}) {
  const groups = S.meta.groups.filter((x) => !x.is_makeup);
  openDrawer(`
  <h2>${preset.family_id ? "Ребёнок в семью " + esc(preset.family_name) : "Новый клиент"}</h2>
  <form id="nc" class="section">
    ${preset.family_id ? "" : `
      <div class="field"><label>Телефон родителя</label><input name="phone" type="tel" required placeholder="+7 900 000-00-00" value="${esc(preset.phone || "")}"></div>
      <div id="dup"></div>
      <div class="field"><label>Имя родителя</label><input name="parent_name" required value="${esc(preset.parent_name || "")}"></div>
      <div class="field"><label>Мессенджер</label><select name="messenger"><option>WhatsApp</option><option>Telegram</option><option>MAX</option><option value="">—</option></select></div>`}
    <div class="field"><label>Имя ребёнка</label><input name="name" required></div>
    <div class="row">
      <div class="field"><label>Дата рождения</label><input name="birth" type="date"></div>
      <div class="field"><label>Куда</label><select name="funnel"><option value="sales">Лид (воронка продаж)</option><option value="members">Уже занимается</option></select></div>
    </div>
    <div class="field"><label>Группа</label><select name="group_id"><option value="">— позже —</option>${groups.map((x) => `<option value="${x.id}">${esc(x.name)} · ${esc(x.age)} лет</option>`).join("")}</select></div>
    <div class="row">
      <div class="field"><label>Тариф</label><select name="tariff"><option value="fixed">Негибкий</option><option value="flex">Гибкий</option></select></div>
      <div class="field"><label>Откуда узнали</label>${sourceSelect("source_id", null)}</div>
    </div>
    <button class="btn primary">Создать</button>
  </form>`);
  let familyId = preset.family_id || null;
  const form = $("#nc");
  const phone = form.elements.phone;
  if (phone) phone.onblur = async () => {
    const r = await api("GET", "/api/check-phone?phone=" + encodeURIComponent(phone.value)).catch(() => []);
    $("#dup").innerHTML = r.length ? `<div class="quote small">Этот телефон уже есть: ${r.map((f) => `<button type="button" class="chip" data-fam="${f.id}">Семья ${esc(f.name)} (${esc(f.children.join(", "))})</button>`).join(" ")}<div class="muted" style="margin-top:6px">Нажмите на семью, чтобы добавить ребёнка в неё.</div></div>` : "";
    $("#dup").querySelectorAll("[data-fam]").forEach((b) => (b.onclick = () => {
      familyId = +b.dataset.fam;
      $("#dup").innerHTML = `<div class="quote small">Ребёнок будет добавлен в выбранную семью.</div>`;
      form.elements.parent_name.required = false;
    }));
  };
  form.onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(form));
    if (f.group_id) f.group_id = +f.group_id;
    if (f.source_id) f.source_id = +f.source_id; else delete f.source_id;
    if (familyId) f.family_id = familyId;
    if (preset.conversation_id) f.conversation_id = preset.conversation_id;
    try { const r = await api("POST", "/api/students", f); toast("Клиент создан"); render(); openStudent(r.id); }
    catch (err) { fail(err); }
  };
}

/* ---------- журнал ---------- */
/* в списке групп внутри филиала название филиала лишнее (Юлия, 04.11): «Небо Ср/Вс 16:45» → «Ср/Вс 16:45» */
function groupNoBranch(x) {
  const w = (x.name || "").split(" ")[0];
  return w && (x.branch || "").includes(w) && x.name.length > w.length ? x.name.slice(w.length).trim() : x.name;
}
/* весь месяц по группе: сколько детей в каждую дату + крупная плашка «на абонементе» */
async function renderJournalMonth(page, groups) {
  const g = groups.find((x) => x.id === S.journal.group);
  const ym = S.journal.date.slice(0, 7);
  const m = await api("GET", `/api/journal/month?group_id=${g.id}&month=${ym}`);
  const today = S.meta.today;
  const tile = (d) => {
    const past = d.date <= today;
    const wd = WD[(new Date(d.date + "T12:00").getDay() + 6) % 7];
    if (d.closed) return `<div class="jm-day closed"><div class="jm-date">${dShort(d.date)} <span class="muted">${wd}</span></div><div class="muted small">Праздник, занятия нет</div></div>`;
    const unmarked = past ? d.list - d.present - d.absent : 0;
    return `<button class="jm-day ${d.date === today ? "today" : ""}" data-jmday="${d.date}">
      <div class="jm-date">${dShort(d.date)} <span class="muted">${wd}</span></div>
      <div class="jm-big">${past ? d.present + d.trials_came : d.list + d.trials}<span> ${past ? "были" : "ждём"}</span></div>
      <div class="small muted">${[past ? `по списку ${d.list}` : "", past && d.absent ? `пропуск ${d.absent}` : "", d.vacation ? `отпуск ${d.vacation}` : "",
        d.trials ? `пробных ${d.trials}` : "", d.makeups ? `отработка ${d.makeups}` : "", unmarked > 0 ? `<b class="red">не отмечено ${unmarked}</b>` : ""].filter(Boolean).join(" · ")}</div>
    </button>`;
  };
  const sb = m.subs;
  page.innerHTML = `${journalDayHead()}
    <div class="row" style="max-width:720px;margin-bottom:14px">
      ${jBranchSel(groups, g.branch_id)}
      <div class="field"><label>Группа</label><select id="j-group">${groups.filter((x) => x.branch_id === g.branch_id).map((x) => `<option value="${x.id}" ${x.id === S.journal.group ? "selected" : ""}>${esc(groupNoBranch(x))}</option>`).join("")}</select></div>
      <div class="field" style="flex:0 0 170px"><label>Месяц</label><input id="j-month" type="month" value="${ym}"></div>
    </div>
    <div class="jm-sub">
      <div class="jm-sub-n">${sb.paid}</div>
      <div><b>${plural(sb.paid, "ребёнок", "ребёнка", "детей")} на абонементе</b>
        ${sb.single ? `<div><b>${sb.single} ${plural(sb.single, "разовик", "разовика", "разовиков")}</b></div>` : ""}</div>
    </div>
    ${m.days.length ? `<div class="jm-grid">${m.days.map(tile).join("")}</div>` : `<div class="muted">В этом месяце у группы нет занятий</div>`}`;
  wireJournalDayHead(page);
  page.querySelector("#j-group").onchange = (e) => step(() => { S.journal.group = +e.target.value; });
  const jb = page.querySelector("#j-branch");
  if (jb) jb.onchange = (e) => step(() => { const f = groups.find((x) => x.branch_id === +e.target.value && !x.is_makeup) || groups.find((x) => x.branch_id === +e.target.value); if (f) S.journal.group = f.id; });
  page.querySelector("#j-month").onchange = (e) => { if (e.target.value) step(() => { S.journal.date = e.target.value + "-01"; }); };
  page.querySelectorAll("[data-jmday]").forEach((b) => (b.onclick = () => step(() => { S.journal.mode = "group"; S.journal.date = b.dataset.jmday; })));
}

function jBranchSel(groups, branchId) {
  const br = [...new Map(groups.map((x) => [x.branch_id, { id: x.branch_id, name: x.branch }])).values()];
  return br.length > 1 ? `<div class="field" style="flex:0 0 200px"><label>Филиал</label><select id="j-branch">${br.map((b) => `<option value="${b.id}" ${b.id === branchId ? "selected" : ""}>${esc(b.name)}</option>`).join("")}</select></div>` : "";
}

async function renderJournal(page) {
  const groups = S.meta.groups;
  if (!groups.length) { page.innerHTML = `<h2>Журнал посещений</h2><p class="muted">За вами пока не закреплено ни одной группы. Попросите администратора.</p>`; return; }
  if (S.journal.group && !groups.some((x) => x.id === S.journal.group)) S.journal.group = null;
  if (S.journal.mode === "month" && !S.journal.group) S.journal.group = (groups.find((x) => x.branch_id === S.journal.branch) || groups[0]).id;
  if (!S.journal.date) S.journal.date = S.meta.today;
  if (S.journal.mode === "dayall") return renderJournalDay(page, groups);
  if (S.journal.mode === "month") return renderJournalMonth(page, groups);
  // в списке только группы, у которых в выбранный день есть занятие (Юлия, 04.11)
  const wdSel = (new Date(S.journal.date + "T12:00").getDay() + 6) % 7;
  const dayGroups = groups.filter((x) => String(x.weekdays || "").split(",").map(Number).includes(wdSel));
  if (!dayGroups.length) {
    page.innerHTML = `${journalDayHead()}<div class="row" style="max-width:640px;margin-bottom:14px">
      <div class="field" style="flex:0 0 170px"><label>Дата</label><input id="j-date" type="date" value="${S.journal.date}"></div></div>
      <div class="muted">${dShort(S.journal.date)} занятий нет ни в одном филиале. Выберите другую дату</div>`;
    wireJournalDayHead(page);
    $("#j-date").onchange = (e) => { step(() => { S.journal.date = e.target.value; }); };
    return;
  }
  // у выбранной группы в этот день занятия нет или группа не выбрана: ничего не открываем, просим выбрать (Юлия, 04.11)
  if (!dayGroups.some((x) => x.id === S.journal.group)) {
    const was = groups.find((x) => x.id === S.journal.group);
    if (was) S.journal.branch = was.branch_id;
    S.journal.group = null;
    const brs = [...new Map(dayGroups.map((x) => [x.branch_id, { id: x.branch_id, name: x.branch }])).values()];
    const bid = brs.some((b) => b.id === S.journal.branch) ? S.journal.branch : brs[0].id;
    const inBr = dayGroups.filter((x) => x.branch_id === bid);
    page.innerHTML = `${journalDayHead()}<div class="row" style="max-width:640px;margin-bottom:14px">
      ${brs.length > 1 ? `<div class="field" style="flex:0 0 200px"><label>Филиал</label><select id="j-branch">${brs.map((b) => `<option value="${b.id}" ${b.id === bid ? "selected" : ""}>${esc(b.name)}</option>`).join("")}</select></div>` : ""}
      <div class="field"><label>Группа</label><select id="j-group"><option value="">Выберите группу</option>${inBr.map((x) => `<option value="${x.id}">${esc(groupNoBranch(x))}</option>`).join("")}</select></div>
      <div class="field" style="flex:0 0 170px"><label>Дата</label><input id="j-date" type="date" value="${S.journal.date}"></div></div>
      <div class="muted">${was ? `У группы ${esc(groupNoBranch(was))} ${dShort(S.journal.date)} занятия нет. ` : ""}Выберите группу</div>`;
    wireJournalDayHead(page);
    $("#j-group").onchange = (e) => { if (e.target.value) step(() => { S.journal.group = +e.target.value; }); };
    if ($("#j-branch")) $("#j-branch").onchange = (e) => step(() => { S.journal.branch = +e.target.value; });
    $("#j-date").onchange = (e) => { step(() => { S.journal.date = e.target.value; }); };
    return;
  }
  const j = await api("GET", `/api/journal?group_id=${S.journal.group}&date=${S.journal.date}`);
  const g = j.group;
  if (g.branch_id == null) g.branch_id = (groups.find((x) => x.id === g.id) || {}).branch_id;
  const jBranches = [...new Map(dayGroups.map((x) => [x.branch_id, { id: x.branch_id, name: x.branch }])).values()];
  // до начала тренировки вместо «Был / Пропуск» кнопка «Не придёт» (Юлия, 04.11)
  const before = j.has_lesson && (S.journal.date > S.meta.today || (S.journal.date === S.meta.today && new Date().toTimeString().slice(0, 5) < g.time));
  // нумерация: сначала те, кого ждём, потом предупредившие и отпуск; плашка «всего / ждём» (Юлия, 04.11)
  const away = (r) => r.warned || r.status === "vacation";
  j.rows.sort((a, b) => away(a) - away(b));
  j.rows.forEach((r, i) => (r.n = i + 1));
  const jTotal = j.rows.length, jAway = j.rows.filter(away).length;
  const jCnt = (f) => j.rows.filter(f).length;
  const jSum = j.has_lesson && jTotal ? `<div class="jm-sub" style="max-width:none">
      <div class="jm-sub-n">${jTotal - jAway}</div>
      <div><b>ждём на тренировке</b> из ${jTotal} по списку${jAway ? `: ${jAway} ${plural(jAway, "предупредил", "предупредили", "предупредили")} или в отпуске` : ""}
        <div class="small muted">${[[jCnt((r) => r.tag === "paid" && !away(r)), "абонемент"], [jCnt((r) => r.tag === "single" && !away(r)), "разовое"], [jCnt((r) => r.tag === "unpaid" && !away(r)), "не оплачен"],
          [jCnt((r) => r.status === "trial"), "ПУ"], [jCnt((r) => r.status === "makeup" || r.makeup_id), "отработка"]].filter(([n]) => n).map(([n, t]) => `${t}: ${n}`).join(" · ")}</div></div></div>` : "";
  const st = j.has_lesson && S.journal.date <= S.meta.today ? await api("GET", `/api/lessons/staff?group_id=${g.id}&date=${S.journal.date}`).catch(() => null) : null;
  page.innerHTML = `
    ${journalDayHead()}
    <div class="row" style="max-width:640px;margin-bottom:14px">
      ${jBranches.length > 1 ? `<div class="field" style="flex:0 0 200px"><label>Филиал</label><select id="j-branch">${jBranches.map((b) => `<option value="${b.id}" ${b.id === g.branch_id ? "selected" : ""}>${esc(b.name)}</option>`).join("")}</select></div>` : ""}
      <div class="field"><label>Группа</label><select id="j-group">${dayGroups.filter((x) => x.branch_id === g.branch_id).map((x) => `<option value="${x.id}" ${x.id === S.journal.group ? "selected" : ""}>${esc(groupNoBranch(x))}</option>`).join("")}</select></div>
      <div class="field" style="flex:0 0 170px"><label>Дата</label><input id="j-date" type="date" value="${S.journal.date}"></div>
    </div>
    <div class="note">${esc(g.branch)} · ${g.weekdays.split(",").map((x) => WD[+x]).join(", ")} ${esc(g.time)} · ${esc(g.age)} лет${j.closed ? " · <b>праздник, занятия нет</b>" : j.has_lesson ? "" : " · <b>в этот день у группы нет занятия</b>"}</div>
    ${j.has_lesson && !g.is_makeup && S.journal.date <= S.meta.today ? `<button class="btn primary meet-open ${j.overdue_marks ? "late" : ""}" id="j-meet">${meetLabel(S.journal.date, g.time, j.overdue_marks)}${lessonOver(S.journal.date, g.time) ? "" : ": отметить крупными кнопками"}</button>` : ""}
    ${jSum}
    ${j.has_lesson ? `<div class="row" style="gap:8px;margin:0 0 12px;flex-wrap:wrap"><button class="btn sm" id="j-copy" style="flex:0 0 auto">📋 Скопировать списки смены</button><button class="btn sm" id="j-print" style="flex:0 0 auto">🖨 Лист для печати</button><span class="small muted" style="align-self:center">все группы ${esc(g.branch)} на ${dShort(S.journal.date)}</span></div>` : ""}
    ${j.overdue_marks ? `<div class="alert">🔴 Тренировка закончилась ${j.overdue_marks.minutes} мин назад, не отмечено детей: ${j.overdue_marks.missing}</div>` : ""}
    ${st ? `<div class="panel" style="padding:12px 14px;margin-bottom:12px">${staffPicker(st, st.coaches, "j", S.journal.date === S.meta.today && new Date().toTimeString().slice(0, 5) < g.time)}</div>` : ""}
    <div class="list">${j.rows.length ? j.rows.map((r) => g.is_makeup && r.makeup_id ? makeupRow(r) : journalRow(r, before)).join("") : `<div class="list-row muted">${g.is_makeup ? "На эту дату никто не записан на отработку" : "В группе пока нет учеников"}</div>`}</div>
    ${(j.hidden_unpaid || []).length ? `<details class="small muted" style="margin-top:10px"><summary>Не оплатили: ${j.hidden_unpaid.length}. В журнал не попадают</summary>
      <div style="margin-top:6px">${j.hidden_unpaid.map((x) => `<span class="${isCoach() ? "" : "click"}" data-who="${x.id}" style="cursor:pointer">${esc(x.name)}</span>`).join(", ")}</div></details>` : ""}`;
  if (st) wireStaffPicker(page, "j", g.id, S.journal.date, render);
  if ($("#j-meet")) $("#j-meet").onclick = () => openMeet(g.id, S.journal.date);
  wireJournalDayHead(page);
  if ($("#j-copy")) $("#j-copy").onclick = () => shiftCopy(g.branch_id, S.journal.date);
  if ($("#j-print")) $("#j-print").onclick = () => shiftPrint(g.branch_id, S.journal.date);
  $("#j-group").onchange = (e) => { step(() => { S.journal.group = +e.target.value; }); };
  if ($("#j-branch")) $("#j-branch").onchange = (e) => { step(() => { S.journal.branch = +e.target.value; S.journal.group = null; }); };
  $("#j-date").onchange = (e) => { step(() => { S.journal.date = e.target.value; }); };
  page.classList.toggle("readonly", !can("journal", "full") && !isCoach());
  if (!isCoach()) page.querySelectorAll("[data-who]").forEach((el) => (el.onclick = () => openStudent(+el.dataset.who)));
  page.querySelectorAll("[data-att]").forEach((b) => (b.onclick = async () => {
    try { await api("POST", "/api/attendance", { student_id: +b.dataset.sid, group_id: S.journal.group, date: S.journal.date, status: b.dataset.att }); render(); } catch (e) { fail(e); }
  }));
  page.querySelectorAll("[data-warn]").forEach((b) => (b.onclick = async () => {
    const note = window.prompt("Что сказали родители? (необязательно): заболел, уехали…", "");
    if (note === null) return;
    try { await api("POST", "/api/journal/warn", { student_id: +b.dataset.warn, group_id: S.journal.group, date: S.journal.date, note }); toast("Отмечено: не придёт. Место свободно для отработки"); render(); } catch (e) { fail(e); }
  }));
  page.querySelectorAll("[data-unwarn]").forEach((b) => (b.onclick = async () => {
    try { await api("POST", "/api/journal/warn", { student_id: +b.dataset.unwarn, group_id: S.journal.group, date: S.journal.date, undo: true }); toast("Ждём на тренировке"); render(); } catch (e) { fail(e); }
  }));
  page.querySelectorAll("[data-mk]").forEach((b) => (b.onclick = async () => {
    try { await api("POST", `/api/makeups/${b.dataset.mk}/status`, { status: b.dataset.st }); render(); } catch (e) { fail(e); }
  }));
  page.querySelectorAll("[data-trial]").forEach((b) => (b.onclick = async () => {
    try { await api("POST", `/api/students/${b.dataset.sid}/trial`, { came: b.dataset.trial === "1" }); render(); } catch (e) { fail(e); }
  }));
}

/* возраст рядом с именем (Юлия, 04.11) */
const J_TAGS = { paid: ["абонемент", "jt-paid"], single: ["разовое", "jt-single"], unpaid: ["не оплачен", "jt-unpaid"] };
const jTag = (r) => { const t = J_TAGS[r.tag]; return t && !(r.tag === "unpaid" && r.unpaid) ? `<span class="pill ${t[1]}">${t[0]}</span>` : ""; };
const jNum = (r) => r.n ? `<span class="jn">${r.n}.</span>` : "";
/* возраст с месяцами: «3 года 5 мес.» (Юлия, 04.11) */
function ageYM(birth, on) {
  const today = on || new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Moscow" }).format(new Date());
  const b = new Date(String(birth).slice(0, 10) + "T12:00"), n = new Date(today + "T12:00");
  if (!isFinite(b) || !isFinite(n) || b > n) return "";
  const months = (n.getFullYear() - b.getFullYear()) * 12 + n.getMonth() - b.getMonth() - (n.getDate() < b.getDate() ? 1 : 0);
  const y = Math.floor(months / 12), m = months % 12;
  return `${y} ${plural(y, "год", "года", "лет")} ${m} ${plural(m, "месяц", "месяца", "месяцев")}`;
}
const jAge = (r) => r.birth ? ` <span class="small muted">· ${ageYM(r.birth, S.journal.date)}</span>` : "";

function journalRow(r, before) {
  if (r.status === "makeup" || r.makeup_id) return makeupRow({ ...r, status: r.attendance_id ? r.status : r.mk_status, home_group: "отработка" + (r.home_group ? ", из группы " + r.home_group : "") });
  if (r.warned) return `<div class="list-row"><div class="grow click" data-who="${r.id}" style="cursor:pointer">${jNum(r)}${esc(r.name)}${jAge(r)} <span class="pill yellow">предупредил, не придёт</span>${r.note ? `<div class="small muted">${esc(r.note)}</div>` : ""}</div>
    <div class="mark">${before ? `<button data-unwarn="${r.id}">Отменить, придёт</button>` : `<button data-att="present" data-sid="${r.id}">Всё-таки был</button>`}</div></div>`;
  if (r.status === "vacation") return `<div class="list-row"><div class="grow click" data-who="${r.id}">${jNum(r)}${esc(r.name)}${jAge(r)}</div><span class="pill yellow">Отпуск</span></div>`;
  if (r.status === "trial") return `<div class="list-row"><div class="grow click" data-who="${r.id}">${jNum(r)}${esc(r.name)}${jAge(r)} <span class="pill jt-trial">ПУ ${r.trial_at.slice(11, 16)}</span></div>
    <div class="mark"><button data-trial="1" data-sid="${r.id}" class="${r.trial_came === 1 ? "on-present" : ""}">Пришёл</button><button data-trial="0" data-sid="${r.id}" class="${r.trial_came === 0 ? "on-absent" : ""}">Не пришёл</button></div></div>`;
  const unpaidPill = r.unpaid ? ` <span class="pill unpaid-flag">не оплачен, не допускать</span>` : "";
  const locked = r.resolved ? `<span class="pill">${r.resolved === "credit" ? "в перерасчёте" : "отработан"}</span>` : "";
  if (before) return `<div class="list-row"><div class="grow click" data-who="${r.id}" style="cursor:pointer">${jNum(r)}${esc(r.name)}${jAge(r)} ${jTag(r)} ${unpaidPill}</div>
    <div class="mark"><button data-warn="${r.id}" title="Родители заранее сказали, что не придёт">🙅 Не придёт</button></div></div>`;
  return `<div class="list-row"><div class="grow click" data-who="${r.id}" style="cursor:pointer">${jNum(r)}${esc(r.name)}${jAge(r)} ${jTag(r)} ${locked}${unpaidPill}</div>
    <div class="mark">
      <button data-att="present" data-sid="${r.id}" class="${r.status === "present" ? "on-present" : ""}">Был</button>
      <button data-att="absent" data-sid="${r.id}" class="${r.status === "absent" ? "on-absent" : ""}">Пропуск</button>
    </div></div>`;
}

function makeupRow(r) {
  const mark=r.status==='present'?'done':r.status==='absent'?'missed':r.status;
  return `<div class="list-row"><div class="grow click" data-who="${r.id}" style="cursor:pointer">${jNum(r)}${esc(r.name)}${jAge(r)} <span class="pill jt-makeup">отработка</span>
      <div class="small muted">${esc(r.home_group || "")} · за пропуск ${dShort(r.absence_date)}</div></div>
    <div class="mark">
      <button data-mk="${r.makeup_id}" data-st="done" class="${mark === "done" ? "on-present" : ""}">Пришёл</button>
      <button data-mk="${r.makeup_id}" data-st="missed" class="${mark === "missed" ? "on-absent" : ""}">Не пришёл</button>
    </div></div>`;
}

/* история посещений в карточке: за этот месяц коротко цифрами, ниже список дат */
function visitsHtml(v, cur) {
  if (!v.length) return `<div class="small muted" style="margin-top:8px">Отметок за последние 3 месяца нет</div>`;
  const m = v.filter((x) => x.date.slice(0, 7) === cur);
  const st = (x) => x.warned ? ["предупредил, не пришёл", "yellow"] : x.status === "present" ? ["был", "green"] : ["пропуск", "red"];
  return `<div class="small" style="margin:10px 0 6px">В этом месяце: был <b>${m.filter((x) => x.status === "present").length}</b>, пропусков <b>${m.filter((x) => x.status === "absent").length}</b></div>
    <ul class="history visits">${v.map((x) => `<li><b>${dShort(x.date)}</b> ${WD[(new Date(x.date + "T12:00").getDay() + 6) % 7]} · ${esc(groupNoBranch({ name: x.group_name, branch: x.branch }))}
      <span class="pill ${st(x)[1]}">${st(x)[0]}</span>${x.resolved ? ` <span class="pill">${x.resolved === "credit" ? "в перерасчёте" : "отработан"}</span>` : ""}${x.note ? ` <span class="small muted">${esc(x.note)}</span>` : ""}</li>`).join("")}</ul>`;
}

/* отметка посещения прямо из карточки (Юлия, 04.11): ближайшие тренировки группы ребёнка, «Был / Пропуск» или «Не придёт» */
function wireCardMark(sid, reload) {
  const gSel = $("#cm-group"), dSel = $("#cm-date");
  const fillDates = () => {
    const wd = (gSel.selectedOptions[0].dataset.wd || "").split(",").filter((x) => x !== "").map(Number);
    const t = new Date(S.meta.today + "T12:00"), out = [];
    for (let i = -14; i <= 14; i++) { const x = new Date(t); x.setDate(t.getDate() + i); if (wd.includes((x.getDay() + 6) % 7)) out.push(isoDate(x)); }
    const near = out.filter((x) => x <= S.meta.today).pop() || out[0];
    dSel.innerHTML = out.map((x) => `<option value="${x}" ${x === near ? "selected" : ""}>${dShort(x)} ${WD[(new Date(x + "T12:00").getDay() + 6) % 7]}${x === S.meta.today ? " (сегодня)" : ""}</option>`).join("");
  };
  const show = async () => {
    const gid = +gSel.value, day = dSel.value;
    if (!day) { $("#cm-btns").innerHTML = ""; return; }
    let r = null;
    try { r = (await api("GET", `/api/journal?group_id=${gid}&date=${day}`)).rows.find((x) => x.id === sid); } catch (_) {}
    const st = !r ? "нет в журнале на эту дату (не оплачено или не его день)" : r.warned ? "предупредил, не придёт" : r.status === "vacation" ? "в отпуске"
      : r.status === "present" ? "был ✓" : r.status === "absent" ? "пропуск" : "пока не отмечен";
    $("#cm-state").textContent = "Сейчас: " + st;
    const future = day > S.meta.today;
    $("#cm-btns").innerHTML = future
      ? (r && r.warned ? `<button data-cm="unwarn">Отменить, придёт</button>` : `<button data-cm="warn">🙅 Не придёт</button>`)
      : `<button data-cm="present" class="${r && r.status === "present" ? "on-present" : ""}">Был</button><button data-cm="absent" class="${r && r.status === "absent" ? "on-absent" : ""}">Пропуск</button>`;
    $("#cm-btns").querySelectorAll("[data-cm]").forEach((b) => (b.onclick = async () => {
      const a = b.dataset.cm;
      try {
        if (a === "warn") { const note = window.prompt("Что сказали родители? (необязательно)", ""); if (note === null) return; await api("POST", "/api/journal/warn", { student_id: sid, group_id: gid, date: day, note }); }
        else if (a === "unwarn") await api("POST", "/api/journal/warn", { student_id: sid, group_id: gid, date: day, undo: true });
        else await api("POST", "/api/attendance", { student_id: sid, group_id: gid, date: day, status: a });
        toast("Отмечено"); if (reload) reload(); else show();
      } catch (e) { fail(e); }
    }));
  };
  gSel.onchange = () => { fillDates(); show(); };
  dSel.onchange = show;
  fillDates(); show();
}

/* ---------- поиск ---------- */
/* «Клиенты и лиды» (Юлия, 04.11): один раздел. Лид: ни одной покупки. У клиентов фильтр по статусам карточки и ERP */
S.pf = S.pf || { kind: "all", status: "", erp: "" };
async function renderSearch(page) {
  const f = S.pf;
  const d = await api("GET", `/api/people?kind=${f.kind}&status=${encodeURIComponent(f.status)}&erp=${encodeURIComponent(f.erp)}&q=${encodeURIComponent(S.search)}${S.searchArch ? "&archived=1" : ""}`);
  const kinds = [["all", "Все", d.counts.leads + d.counts.clients], ["leads", "Лиды", d.counts.leads], ["clients", "Клиенты", d.counts.clients]];
  const sts = f.kind === "leads" ? d.statuses.leads : f.kind === "clients" ? d.statuses.clients : [];
  const pillCls = { active: "green", single: "", unpaid: "red", left: "", promo: "yellow" };
  page.innerHTML = `
    <div class="page-head"><h2 class="grow">Клиенты и лиды</h2>
      <input class="search" id="q" placeholder="Имя, фамилия или телефон" value="${esc(S.search)}">
      <button class="btn sm ${S.searchArch ? "primary" : ""}" id="q-arch">Архив</button></div>
    <div class="seg" style="margin-bottom:10px">${kinds.map(([k, t, n]) => `<button data-pk="${k}" class="${f.kind === k ? "active" : ""}">${t} <b>${n}</b></button>`).join("")}</div>
    ${sts.length ? `<div class="chips" style="margin-bottom:10px"><button class="chip ${f.status ? "" : "on"}" data-ps="">Все статусы</button>${sts.filter(([k]) => d.counts[k] || f.status === k).map(([k, t]) => `<button class="chip ${f.status === k ? "on" : ""}" data-ps="${k}">${esc(t)} <span class="muted">${d.counts[k] || 0}</span></button>`).join("")}</div>` : ""}
    ${d.erp.length ? `<div class="row" style="max-width:420px;margin-bottom:10px"><div class="field"><label>Статус в ERP</label><select id="p-erp"><option value="">Любой</option>${d.erp.map(([e, n]) => `<option value="${esc(e)}" ${f.erp === e ? "selected" : ""}>${esc(e)} (${n})</option>`).join("")}</select></div></div>` : ""}
    <div class="small muted" style="margin-bottom:6px">Найдено: ${d.total}${d.total > d.limit ? `, показаны первые ${d.limit}. Уточните поиск или фильтр` : ""}</div>
    <div id="results" class="list">${d.rows.length ? d.rows.map((r) => `
      <div class="list-row click" data-who="${r.id}"><div class="grow"><b>${esc(r.name)}</b>
        <div class="small muted">${esc([r.parent, r.phone, r.group].filter(Boolean).join(" · "))}${r.erp_status ? ` · ERP: ${esc(r.erp_status)}` : ""}</div></div>
        <span class="pill ${r.status.startsWith("lead:") ? "" : pillCls[r.status] || ""}">${r.status.startsWith("lead:") ? "Лид: " : ""}${esc(r.status_label)}</span></div>`).join("")
      : `<div class="list-row muted">Никого не нашли</div>`}</div>`;
  page.querySelectorAll("[data-who]").forEach((el) => (el.onclick = () => openStudent(+el.dataset.who)));
  page.querySelectorAll("[data-pk]").forEach((b) => (b.onclick = () => { f.kind = b.dataset.pk; f.status = ""; render(); }));
  page.querySelectorAll("[data-ps]").forEach((b) => (b.onclick = () => { f.status = b.dataset.ps; render(); }));
  if ($("#p-erp")) $("#p-erp").onchange = (e) => { f.erp = e.target.value; render(); };
  let t; $("#q").oninput = (e) => { S.search = e.target.value; clearTimeout(t); t = setTimeout(async () => { await render(); const q = $("#q"); if (q) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); } }, 350); };
  $("#q-arch").onclick = () => { S.searchArch = !S.searchArch; render(); };
}

/* ---------- настройки ---------- */
const SET_TABS = [
  ["users", "👥", "Сотрудники", "Должности, телефоны, пароли"], ["prices", "💰", "Цены и правила", "Прайс, скидки, сборная ПРО, дни без занятий"],
  ["structure", "🏟", "Филиалы, группы и смены", "Расписание и лимиты"], ["resp", "🎯", "Ответственные по филиалам", "Кому задачи по пробным"],
  ["comms", "💬", "Связь с клиентами", "Telegram, MAX, WhatsApp, Sipuni, заявки"], ["trials", "📝", "Скрипты", "Сообщения родителям и автосообщения"],
  ["tips", "💡", "Подсказки по этапам", "Что сказать на каждом этапе воронки"], ["chains", "🔁", "Автоцепочки сообщений", "Серии сообщений родителям"],
  ["erp", "📦", "Перенос из ERP", "Клиенты, абонементы, оплаты, история"],
  ["coachpay", "⏱", "Тренеры: приход и оплата", "Отметка прихода, ставки"],
  ["sources", "📣", "Рекламные каналы", "Источники лидов"], ["offers", "📄", "Оферты", "Редакции и ссылки"],
  ["backup", "💾", "Резервные копии", "Яндекс Диск, копия сейчас"], ["archive", "🗄", "Архив", "Вернуть из архива"],
  ["test", "🧪", "Тестовые клиенты", "Выдуманные клиенты для проверки"], ["diag", "🩺", "Проверка соединений", "Почему у кого-то не открывается CRM"],
  ["profile", "👤", "Мой профиль", "Вид, код входа, пароль"],
];

function settingsTiles(page) {
  const parts = (k) => page.querySelectorAll(`[data-tab="${k}"]`);
  const tabs = SET_TABS.filter(([k]) => parts(k).length);
  if (S.setTab && !tabs.some(([k]) => k === S.setTab)) S.setTab = null;
  page.querySelectorAll("[data-tab]").forEach((el) => { el.hidden = el.dataset.tab !== S.setTab; });
  const box = $("#set-tiles");
  const cur = tabs.find(([k]) => k === S.setTab);
  box.innerHTML = cur
    ? `<div class="row" style="align-items:center;gap:10px;margin-bottom:12px"><button class="btn sm" id="set-back" style="flex:0 0 auto;width:auto">← Все настройки</button><b>${cur[1]} ${esc(cur[2])}</b></div>`
    : `<div class="set-tiles">${tabs.map(([k, e, t, d]) => `<button class="set-tile" data-tile="${k}"><span class="set-tile-emoji">${e}</span><b>${esc(t)}</b><span class="small muted">${esc(d)}</span></button>`).join("")}</div>`;
  box.querySelectorAll("[data-tile]").forEach((b) => (b.onclick = () => { S.setTab = b.dataset.tile; settingsTiles(page); window.scrollTo(0, 0); }));
  if ($("#set-back")) $("#set-back").onclick = () => { S.setTab = null; settingsTiles(page); window.scrollTo(0, 0); };
}

async function renderSettings(page) {
  const st = S.meta.settings || {}, admin = isOwner();
  const users = admin || can("schedule", "full") ? (await api("GET", "/api/users")).filter((u) => u.active) : [];
  page.innerHTML = `
    <div class="page-head"><h2 class="grow">Настройки</h2><button class="btn sm" id="logout">Выйти</button></div>
    <div style="max-width:780px">
    <div id="set-tiles"></div>
    <div class="section" data-tab="profile"><h3>Внешний вид</h3>
      <label class="check"><input type="checkbox" id="theme-soft" ${document.documentElement.classList.contains("theme-soft") ? "checked" : ""}> Новый дизайн (проба)</label>
      <div class="small muted" style="margin-top:6px">Светлый спокойный вид. Включается только на этом устройстве, выключить можно здесь же в любой момент</div>
      ${can("inbox") ? `<label class="check" style="margin-top:12px"><input type="checkbox" id="sound-on" ${soundOn() ? "checked" : ""}> 🔔 Звук, когда приходит сообщение, пропущенный звонок или заявка</label>
      <div class="row" style="gap:8px;margin-top:6px"><button class="btn sm" id="sound-test" style="flex:0 0 auto">Проверить звук</button>
        ${"Notification" in window && Notification.permission !== "granted" ? `<button class="btn sm" id="notif-on" style="flex:0 0 auto">Показывать уведомления, когда CRM свёрнута</button>` : ""}</div>` : ""}</div>
    <div class="section" data-tab="profile"><h3>Быстрый вход по коду</h3><div id="pin-set" class="small muted">…</div></div>
    <div class="section" data-tab="profile"><h3>Обучение</h3><div class="small muted" style="margin-bottom:8px">Короткая экскурсия по твоим разделам CRM</div>
      <button class="btn sm" id="tour-again">🎓 Пройти обучение ещё раз</button></div>
    ${S.user.role === "admin" ? `<div class="section" data-tab="profile" id="owner-tg"><h3>🔴 Красное в Telegram</h3><div class="small muted">…</div></div>` : ""}
    ${S.user.role === "admin" ? `<div class="section" data-tab="profile"><h3>Праздничное оформление</h3>
      <label class="check"><input type="checkbox" id="festive-on" ${S.meta.festive !== false ? "checked" : ""}> 🎉 Украшать CRM к праздникам по календарю (у всех сотрудников)</label>
      <div class="small muted" style="margin-top:4px">Хэллоуин, Новый год, 23 февраля, 8 марта, 1 июня, 1 сентября</div></div>` : ""}
    ${admin ? `
    <form class="section" id="set" data-tab="prices">
      <h3>Цены и правила</h3>
      <div id="price-list" class="small muted" style="margin-bottom:10px">Загружаю прайс…</div>
      <div class="row"><div class="field"><label>Разовое занятие, ₽</label><input name="price_single" type="number" value="${esc(st.price_single || "")}" placeholder="Уточнить цену"></div><div></div></div>
      <div class="row">
        <div class="field"><label>Продлить до числа</label><input name="renewal_deadline_day" type="number" min="1" max="28" value="${esc(st.renewal_deadline_day)}"></div>
        <div class="field"><label>Длительность пробного, мин</label><input name="trial_minutes" type="number" value="${esc(st.trial_minutes)}"></div>
      </div>
      <button class="btn primary">Сохранить</button>
    </form>
    ${can("payments", "full") ? `<div class="section" id="discount-settings" data-tab="prices"></div>` : ""}
    <div id="program-settings" data-tab="prices"></div>
    ${can("members", "full") ? `<div class="section" id="memo-settings" data-tab="prices"></div>` : ""}
    ${S.user.role === "admin" ? `<div class="section" id="tips-settings" data-tab="tips"></div>` : ""}
    ${S.user.role === "admin" ? `<div class="section" id="chains-settings" data-tab="chains"></div>` : ""}
    ${S.user.role === "admin" ? `<div class="section" id="erp-settings" data-tab="erp"></div>` : ""}
    ${S.user.role === "admin" ? `<div class="section" id="tg-import" data-tab="erp"></div>` : ""}
    <div class="section" id="backup-settings" data-tab="backup"></div>
    ${isOwner() ? `<div class="section" id="diag-box" data-tab="diag"></div>` : ""}
    <div class="section" id="resp-settings" data-tab="resp"></div>
    <div class="section" data-tab="users">
      <h3>Сотрудники</h3>
      <div class="list" style="box-shadow:none;margin-bottom:10px">${users.map((u) => `<div class="list-row staff-row"><div class="grow"><span class="who" data-prof="${u.id}">${avatar(u.id, u.name, 34)}<span>${esc(u.name)} <button type="button" class="linkish small muted" data-ulogin="${u.id}" data-login="${esc(u.login)}" data-name="${esc(u.name)}" title="Изменить логин">${esc(u.login)} ✎</button></span></span></div>
        <input data-uph="${u.id}" value="${esc(u.phone || "")}" placeholder="телефон" title="Телефон сотрудника" inputmode="tel" style="flex:0 0 130px;padding:5px 8px">
        <input data-tgu="${u.id}" value="${esc(u.tg_username ? "@" + u.tg_username : "")}" placeholder="@telegram" title="Ник в Telegram: CRM отметит сотрудника в рабочих чатах" style="flex:0 0 120px;padding:5px 8px">
        ${u.role === "coach" ? "" : `<input data-sip="${u.id}" value="${esc(u.sip_number || "")}" placeholder="вн. номер" title="Внутренний номер Sipuni: на него придёт звонок из карточки" inputmode="numeric" style="flex:0 0 96px;padding:5px 8px">`}
        <select data-upost="${u.id}" title="Должность: от неё зависят разделы и права" style="flex:0 0 auto;width:auto;padding:5px 8px" ${u.id === S.user.id ? "disabled" : ""}>${POSTS.map(([k, t]) => `<option value="${k}" ${postOf(u) === k ? "selected" : ""}>${t}</option>`).join("")}</select>
        <label class="check small" title="Промоутер: права промоутера добавляются к должности" style="flex:0 0 auto"><input type="checkbox" data-uprom="${u.id}" ${u.is_promoter ? "checked" : ""}> промоутер</label>
        ${["operator"].includes(u.role) ? `<label class="check small" title="Отмечает в журнале детей и тренеров, как тренер (например, администратор филиала)" style="flex:0 0 auto"><input type="checkbox" data-ujour="${u.id}" ${u.is_journal ? "checked" : ""}> журнал</label>` : ""}
        ${u.id !== S.user.id ? `<button class="icon-btn" data-ucopy="${u.id}" data-login="${esc(u.login)}" data-name="${esc(u.name)}" title="Скопировать вход: новый пароль и сообщение с логином">📋</button><button class="icon-btn" data-upw="${u.id}" title="Задать новый пароль">🔑</button><button class="icon-btn" data-uarch="${u.id}" title="В архив">🗄</button>` : ""}</div>`).join("")}</div>
      <div class="small muted" style="margin:-4px 0 10px">Внутренний номер Sipuni нужен, чтобы звонить клиенту прямо из карточки</div>
      <form id="nu">
        <div class="row"><div class="field"><label>Имя и фамилия</label><input name="name" autocomplete="off" required></div><div class="field"><label>Телефон</label><input name="phone" type="tel" placeholder="Необязательно"></div></div>
        <div class="field"><label>Должность</label><select name="post">${POSTS.map(([k, t]) => `<option value="${k}" ${k === "operator" ? "selected" : ""}>${t}</option>`).join("")}</select></div>
        <p class="small muted">Логин и случайный пароль CRM создаст сама и покажет после добавления сотрудника.</p>
        <label class="check small" style="margin-bottom:8px"><input type="checkbox" name="is_promoter" value="1"> Ещё и промоутер</label>
        <button class="btn sm">Добавить сотрудника</button>
      </form>
      <details style="margin-top:12px"><summary class="linkish small">Завести команду списком (пароли создаст CRM)</summary>
        <div class="small muted" style="margin:6px 0">По строке на человека: Имя Фамилия; должность; телефон. Должность: администратор, старший администратор, тренер, промоутер, юрист или руководитель. Если человек уже есть, ему просто выдастся новый пароль</div>
        <textarea id="bulk-users" rows="6" placeholder="Ольга Авлахова; администратор; +7 911 831-27-83"></textarea>
        <button class="btn sm primary" id="bulk-go" style="margin-top:6px">Завести и выдать пароли</button>
        <div id="bulk-res"></div></details>
    </div>
    <div class="section" id="structure-settings" data-tab="structure"><h3>Филиалы, группы и смены</h3><div class="muted small">Загружаю…</div></div>
    <div class="section" id="archive-settings" data-tab="archive"><h3>Архив</h3><div class="muted small">Загружаю…</div></div>
    </div>` : can("schedule", "full") ? `<div class="section" id="structure-settings" data-tab="structure"><h3>Филиалы, группы и смены</h3><div class="muted small">Загружаю…</div></div>` : ""}
    <div class="section" id="offers-settings" data-tab="offers"><h3>Оферты</h3><div class="muted small">Загружаю…</div></div>
    ${admin ? `<div class="section" id="source-settings" data-tab="sources"><h3>Рекламные каналы и источники</h3><div class="muted small">Загружаю…</div></div>` : ""}
    ${admin ? `<div class="section" id="staff-settings" data-tab="coachpay"><h3>Тренеры: приход и оплата</h3><div class="muted small">Загружаю…</div></div>` : ""}
    ${admin ? `<div class="section" id="task-settings" data-tab="trials"><h3>Скрипты</h3><div class="muted small">Загружаю…</div></div>` : ""}
    ${admin ? `<div class="section" id="comms-settings" data-tab="comms"><h3>Связь с клиентами</h3><div class="muted small">Загружаю…</div></div>` : ""}
    ${admin ? `<div class="section" data-tab="test"><h3>Выдуманные клиенты для проверки</h3>
      <div class="small muted" style="margin-bottom:8px">Добавляют клиентов в настоящие группы: с абонементами, оплатами, посещениями, пропусками и лидами на разных этапах. Все помечены как выдуманные и удаляются одной кнопкой, настоящие клиенты не пострадают</div>
      <div class="row" style="align-items:center;flex-wrap:wrap"><input type="number" id="fake-n" value="150" min="1" max="500" style="flex:0 0 100px">
        <button class="btn sm primary" id="fake-add" style="flex:0">Добавить</button><button class="btn sm" id="fake-real" style="flex:0">Школа с историей за 1,5 года</button><button class="btn sm" id="fake-calls" style="flex:0">+ Звонки с разборами</button><button class="btn sm danger" id="fake-del" style="flex:0">Удалить всех выдуманных</button></div></div>` : ""}
    ${admin && S.meta.demo ? `<div class="section" data-tab="test"><h3>Тестовые данные</h3>
      <div class="small muted" style="margin-bottom:8px">Сейчас включён тестовый режим. Кнопка сотрёт всех клиентов и создаст выдуманных заново. Сотрудники и настройки сохранятся.</div>
      <button class="btn sm danger" id="demo-reset">Пересоздать тестовые данные</button></div>` : ""}
    <form class="section" id="pw" data-tab="profile">
      <h3>Мой пароль</h3>
      <div class="field"><label>Текущий пароль</label><input name="old" type="password" autocomplete="current-password" required></div>
      <div class="field"><label>Новый пароль</label><input name="new" type="password" autocomplete="new-password" minlength="8" required></div>
      <button class="btn sm">Сменить пароль</button>
    </form>
    <div class="small muted" data-tab="profile" style="margin-top:12px">Вы вошли как ${esc(S.user.name)}. Версия 0.2, тестовая.</div>
    </div>`;
  $("#logout").onclick = logoutCurrentAccount;
  settingsTiles(page);
  if ($("#set")) $("#set").onsubmit = async (e) => {
    e.preventDefault();
    try { S.meta.settings = await api("PUT", "/api/settings", Object.fromEntries(new FormData(e.target))); toast("Сохранено"); } catch (err) { fail(err); }
  };
  if ($("#nu")) $("#nu").onsubmit = async (e) => {
    e.preventDefault();
    const button = e.target.querySelector("button[type=submit], button:not([type])");
    if (button) button.disabled = true;
    try {
      const r = await api("POST", "/api/users", Object.fromEntries(new FormData(e.target)));
      await render();
      const text = `${r.name}\nВход в CRM: ${location.origin}\nЛогин: ${r.login}\nПароль: ${r.password}`;
      openDrawer(`<h2>Сотрудник добавлен</h2><p>${esc(r.name)}</p><dl class="kv"><dt>Логин</dt><dd><b>${esc(r.login)}</b></dd><dt>Пароль</dt><dd><code>${esc(r.password)}</code></dd></dl><p class="small muted">Скопируйте данные для входа перед закрытием этой карточки. Пароль показывается только сейчас.</p><button type="button" class="btn primary" id="new-staff-copy">Скопировать данные для входа</button>`);
      $("#new-staff-copy").onclick = async () => {
        try { await navigator.clipboard.writeText(text); toast("Данные для входа скопированы"); }
        catch (_) { prompt("Скопируйте данные для входа", text); }
      };
    } catch (err) { fail(err); }
    finally { if (button) button.disabled = false; }
  };
  if ($("#tour-again")) $("#tour-again").onclick = () => startTour(true);
  if ($("#owner-tg")) renderOwnerTg($("#owner-tg"));
  if ($("#festive-on")) $("#festive-on").onchange = async (e) => {
    try { await api("PUT", "/api/settings", { festive: e.target.checked ? "1" : "0" }); S.meta.festive = e.target.checked; shell(); applyFestive(); render(); toast(e.target.checked ? "Праздники включены 🎉" : "Праздничное оформление выключено"); } catch (err) { fail(err); }
  };
  (async () => {
    const box = $("#pin-set"); if (!box) return;
    const dev = deviceToken(true);
    const has = (await api("GET", "/api/pin?device=" + dev).catch(() => ({}))).has_pin;
    box.innerHTML = `<div style="margin-bottom:8px">${has ? "На этом устройстве вход по коду из 4 цифр включён" : "Вход по коду на этом устройстве выключен"}</div>
      <div class="row" style="gap:8px;flex-wrap:wrap"><button class="btn sm primary" id="pin-new">${has ? "Сменить код" : "Включить"}</button>${has ? `<button class="btn sm" id="pin-off">Отключить</button>` : ""}</div>`;
    $("#pin-new").onclick = async () => { try { localStorage.removeItem("crm.pin_skip"); } catch (_) {} if (has) await api("POST", "/api/pin", { device: dev, remove: true }); await offerPin(); shell(); render(); };
    if ($("#pin-off")) $("#pin-off").onclick = async () => { await api("POST", "/api/pin", { device: dev, remove: true }); try { localStorage.setItem("crm.pin_skip", "1"); } catch (_) {} toast("Вход по коду отключён"); render(); };
  })();
  if ($("#sound-on")) $("#sound-on").onchange = (e) => { try { localStorage.setItem("crm.sound", e.target.checked ? "1" : "0"); } catch (_) {} toast(e.target.checked ? "Звук включён" : "Звук выключен"); };
  if ($("#sound-test")) $("#sound-test").onclick = ding;
  if ($("#notif-on")) $("#notif-on").onclick = async () => { const r = await Notification.requestPermission(); toast(r === "granted" ? "Уведомления включены" : "Браузер не разрешил уведомления"); render(); };
  if ($("#theme-soft")) $("#theme-soft").onchange = (e) => {
    const t = e.target.checked ? "soft" : "";
    try { localStorage.setItem("crm.theme", t); } catch (_) {}
    applyTheme(t); toast(t ? "Новый дизайн включён" : "Вернули прежний вид");
  };
  if ($("#price-list")) renderPriceList($("#price-list")).catch(fail);
  if ($("#comms-settings")) renderCommsSettings($("#comms-settings"), users).catch(fail);
  if ($("#task-settings")) renderTaskSettings($("#task-settings"), users).catch(fail);
  if ($("#staff-settings")) renderStaffSettings($("#staff-settings"), users).catch(fail);
  if ($("#source-settings")) renderSourceSettings($("#source-settings")).catch(fail);
  if ($("#legal-settings")) renderLegalSettings($("#legal-settings")).catch(fail);
  if ($("#offers-settings") && can("sales") && !can("docs")) renderOffers($("#offers-settings")).catch(fail);  // администраторам: ссылки на оферты для родителей
  if ($("#memo-settings")) renderMemosSettings($("#memo-settings")).catch(fail);
  if ($("#program-settings")) renderProgramSettings($("#program-settings")).catch(fail);
  if ($("#resp-settings")) renderResponsibles($("#resp-settings")).catch(fail);
  if ($("#diag-box")) renderDiag($("#diag-box"));
  if ($("#backup-settings")) renderBackup($("#backup-settings")).catch(() => { $("#backup-settings").remove(); $('[data-tile="backup"]')?.remove(); });
  else if ($("#offers-settings")) $("#offers-settings").remove();
  if ($("#erp-settings")) renderErp($("#erp-settings")).catch(fail);
  if ($("#tg-import")) renderTgImport($("#tg-import"));
  if ($("#chains-settings")) renderChainsSettings($("#chains-settings")).catch(fail);
  if ($("#tips-settings")) renderTipsSettings($("#tips-settings")).catch(fail);
  if ($("#discount-settings")) renderDiscounts($("#discount-settings")).catch(() => $("#discount-settings").remove());
  if ($("#structure-settings")) renderStructure($("#structure-settings"), users).catch(fail);
  if ($("#archive-settings")) renderArchive($("#archive-settings")).catch(fail);
  page.querySelectorAll("[data-upw]").forEach((b) => (b.onclick = async () => {
    const pw = prompt("Новый пароль для сотрудника (от 8 символов). Передайте его лично");
    if (!pw) return;
    try { await api("PATCH", `/api/users/${b.dataset.upw}`, { password: pw }); toast("Пароль задан"); } catch (e) { fail(e); }
  }));
  page.querySelectorAll("[data-ucopy]").forEach((b) => (b.onclick = async () => {
    const name = b.dataset.name.split(" ")[0];
    if (!confirm(`Создать ${name} новый пароль и скопировать вход?\n\nСтарый пароль перестанет работать. Если ${name} сейчас в CRM, его не выкинет, и код быстрого входа на его телефоне останется.`)) return;
    const abc = "abcdefghjkmnpqrstuvwxyz23456789", rnd = crypto.getRandomValues(new Uint32Array(10));
    const pw = [...rnd].map((x) => abc[x % abc.length]).join("").replace(/(.{5})/, "$1-");
    try { await api("PATCH", `/api/users/${b.dataset.ucopy}`, { password: pw, keep_sessions: true }); } catch (e) { return fail(e); }
    const text = `Вход в CRM «Абонемент Плюс»\nСсылка: ${location.origin}\nЛогин: ${b.dataset.login}\nПароль: ${pw}`;
    let ok = false;
    try { await navigator.clipboard.writeText(text); ok = true; } catch (_) {}
    if (ok) return toast(`Скопировано! Вставьте и отправьте ${name} лично`);
    openDrawer(`<h2>Вход для ${esc(name)}</h2><p class="muted">Браузер не дал скопировать сам: выделите текст и скопируйте</p>
      <textarea readonly rows="5" style="width:100%" onclick="this.select()">${esc(text)}</textarea>`);
  }));
  page.querySelectorAll("[data-uarch]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Перенести сотрудника в архив? Он больше не сможет войти, вернуть можно в Архиве")) return;
    try { await api("PATCH", `/api/users/${b.dataset.uarch}`, { active: false }); toast("Сотрудник в архиве"); render(); } catch (e) { fail(e); }
  }));
  if ($("#bulk-go")) $("#bulk-go").onclick = async () => {
    try {
      const r = await api("POST", "/api/users/bulk", { text: $("#bulk-users").value });
      const txt = r.users.map((u) => `${u.name}\nЛогин: ${u.login}\nПароль: ${u.password}`).join("\n\n");
      $("#bulk-res").innerHTML = `<div class="alert" style="margin-top:8px">Пароли показываются только сейчас. Скопируйте и раздайте сотрудникам лично</div>
        <table class="tbl"><thead><tr><th>Сотрудник</th><th>Логин</th><th>Пароль</th><th></th></tr></thead><tbody>${r.users.map((u) => `<tr><td>${esc(u.name)}</td><td><b>${esc(u.login)}</b></td><td><code>${esc(u.password)}</code></td><td class="small muted">${esc(u.status)}</td></tr>`).join("")}</tbody></table>
        <button class="btn sm" id="bulk-copy" style="margin-top:6px">Копировать всё</button>`;
      $("#bulk-copy").onclick = () => { navigator.clipboard.writeText(txt).then(() => toast("Скопировано")); };
    } catch (e) { fail(e); }
  };
  page.querySelectorAll("[data-prof]").forEach((el) => (el.onclick = () => openStaffProfile(+el.dataset.prof)));
  page.querySelectorAll("[data-uph]").forEach((inp) => (inp.onchange = async () => {
    try { await api("PATCH", `/api/users/${inp.dataset.uph}`, { phone: inp.value }); toast("Телефон сохранён"); } catch (err) { fail(err); }
  }));
  page.querySelectorAll("[data-ulogin]").forEach(b => b.onclick = async e => {
    e.stopPropagation();
    const value = prompt(`Новый логин для ${b.dataset.name}\nЛатинские буквы, цифры, точка, дефис или подчёркивание. Пароль останется прежним.`, b.dataset.login);
    if (value === null || value.trim().toLowerCase() === b.dataset.login) return;
    b.disabled = true;
    try {
      await api("PATCH", `/api/users/${b.dataset.ulogin}`, {login:value});
      if (+b.dataset.ulogin === S.user.id) S.user.login = value.trim().toLowerCase();
      toast("Логин изменён. Пароль прежний"); await render();
    } catch (err) { fail(err); }
    finally { b.disabled = false; }
  });
  page.querySelectorAll("[data-upost]").forEach((sel) => (sel.onchange = async () => {
    const t = POSTS.find(([k]) => k === sel.value)[1];
    if (!confirm(`Сделать должность «${t}»? Разделы и права поменяются сразу, сотруднику нужно будет войти заново`)) { render(); return; }
    try { await api("PATCH", `/api/users/${sel.dataset.upost}`, { post: sel.value }); toast("Должность изменена"); } catch (err) { fail(err); render(); }
  }));
  page.querySelectorAll("[data-ujour]").forEach((c) => (c.onchange = async () => {
    try { await api("PATCH", `/api/users/${c.dataset.ujour}`, { is_journal: c.checked }); toast(c.checked ? "Теперь отмечает детей и тренеров в журнале" : "Журнал снова только для просмотра"); } catch (err) { fail(err); }
  }));
  page.querySelectorAll("[data-uprom]").forEach((c) => (c.onchange = async () => {
    try { await api("PATCH", `/api/users/${c.dataset.uprom}`, { is_promoter: c.checked }); toast(c.checked ? "Отмечен промоутером" : "Больше не промоутер"); } catch (err) { fail(err); }
  }));
  page.querySelectorAll("[data-tgu]").forEach((inp) => (inp.onchange = async () => {
    try { await api("PATCH", `/api/users/${inp.dataset.tgu}`, { tg_username: inp.value }); toast("Ник в Telegram сохранён"); } catch (err) { fail(err); }
  }));
  page.querySelectorAll("[data-sip]").forEach((inp) => (inp.onchange = async () => {
    try { await api("PATCH", `/api/users/${inp.dataset.sip}`, { sip_number: inp.value }); toast("Внутренний номер сохранён"); S.meta = await api("GET", "/api/meta"); S.user = S.meta.user; } catch (err) { fail(err); }
  }));
  page.querySelectorAll("[data-cap]").forEach((inp) => (inp.onchange = async () => {
    try { await api("PATCH", `/api/groups/${inp.dataset.cap}`, { capacity: +inp.value }); S.meta.groups.find((g) => g.id === +inp.dataset.cap).capacity = +inp.value; toast("Мест: " + inp.value); } catch (err) { fail(err); }
  }));
  page.querySelectorAll("[data-gc]").forEach((sel) => (sel.onchange = async () => {
    try {
      await api("PATCH", `/api/groups/${sel.dataset.gc}`, { coach_id: sel.value ? +sel.value : null });
      S.meta.groups.find((g) => g.id === +sel.dataset.gc).coach_id = sel.value ? +sel.value : null;
      toast("Тренер назначен");
    } catch (err) { fail(err); }
  }));
  if ($("#fake-add")) $("#fake-add").onclick = async (e) => {
    e.target.disabled = true; e.target.textContent = "Добавляю…";
    try { const r = await api("POST", "/api/fake-clients", { count: +$("#fake-n").value }); toast(`Добавлено: занимаются ${r.members}, лидов ${r.leads}`); }
    catch (err) { fail(err); }
    e.target.disabled = false; e.target.textContent = "Добавить";
  };
  if ($("#fake-real")) $("#fake-real").onclick = async (e) => {
    if (!confirm("Удалить прежних выдуманных и создать школу с историей за полтора года (лиды, пробные, покупки, отток, оплаты)? Настоящих клиентов это не трогает.")) return;
    e.target.disabled = true; e.target.textContent = "Создаю, до минуты…";
    try { const r = await api("POST", "/api/fake-clients/realistic", {}); toast(`Готово: занимаются ${r.members_now}, лидов за всё время ${r.leads}, ушли ${r.left}`); }
    catch (err) { fail(err); }
    e.target.disabled = false; e.target.textContent = "Школа с историей за 1,5 года";
  };
  if ($("#fake-calls")) $("#fake-calls").onclick = async () => {
    try { const r = await api("POST", "/api/fake-clients/calls", {}); toast(`Звонков: ${r.calls}, оператор ${r.operator}. Сводка дня в «Звонки» → «Сводки по дням»`); } catch (err) { fail(err); }
  };
  if ($("#fake-del")) $("#fake-del").onclick = async () => {
    if (!confirm("Удалить всех выдуманных клиентов вместе с их оплатами и посещениями?")) return;
    try { const r = await api("POST", "/api/fake-clients/remove", {}); toast(`Удалено: ${r.removed}`); } catch (err) { fail(err); }
  };
  if ($("#demo-reset")) $("#demo-reset").onclick = async () => {
    if (!confirm("Стереть всех клиентов и создать тестовых заново?")) return;
    try { await api("POST", "/api/demo/reset", {}); S.meta = await api("GET", "/api/meta"); toast("Тестовые данные пересозданы"); render(); } catch (err) { fail(err); }
  };
  $("#pw").onsubmit = async (e) => {
    e.preventDefault();
    try { await api("POST", "/api/me/password", Object.fromEntries(new FormData(e.target))); toast("Пароль изменён"); e.target.reset(); } catch (err) { fail(err); }
  };
}

/* Прайс обычных групп с датой начала (pricing.py): старая и новая цена живут параллельно */
async function renderPriceList(box) {
  const d = await api("GET", "/api/prices");
  const GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
  const ml = (ym) => ym <= "2000-01" ? "с самого начала" : `с 1 ${GEN[+ym.slice(5, 7) - 1]} ${ym.slice(0, 4)}`;
  box.classList.remove("muted");
  box.innerHTML = `<table class="tbl"><thead><tr><th>Для абонементов</th><th>Негибкий</th><th>Гибкий</th><th>После ${esc(String(S.meta.settings.renewal_deadline_day || 25))} числа</th></tr></thead>
    <tbody>${d.items.map((p) => `<tr><td>${ml(p.valid_from)}</td><td>${rub(p.fixed)}</td><td>${rub(p.flex)}</td><td>${p.late_pct ? `+${String(p.late_pct).replace(".", ",")}%, без скидок` : "без надбавки"}</td></tr>`).join("")}</tbody></table>
    <div class="row" style="margin-top:8px;align-items:flex-end">
      <div class="field"><label>С месяца</label><input type="month" id="pl-m"></div>
      <div class="field"><label>Негибкий, ₽</label><input type="number" id="pl-fx"></div>
      <div class="field"><label>Гибкий, ₽</label><input type="number" id="pl-fl"></div>
      <div class="field"><label>Надбавка, %</label><input type="number" id="pl-pct" value="5"></div>
      <button type="button" class="btn sm" id="pl-add" style="flex:0 0 auto">Добавить цены</button></div>
    <div class="small muted">Цена берётся по месяцу абонемента. При оплате после срока надбавка со всей суммы, скидки не действуют. Исключение только кнопкой «Корректировка» в карточке</div>`;
  $("#pl-add").onclick = async () => {
    try { await api("POST", "/api/prices", { valid_from: $("#pl-m").value, fixed: $("#pl-fx").value, flex: $("#pl-fl").value, late_pct: $("#pl-pct").value }); toast("Прайс сохранён"); renderPriceList(box); } catch (e) { fail(e); }
  };
}

/* должности для Настроек → Сотрудники (perms.py) */
const POSTS = [["owner", "Руководитель"], ["senior", "Старший администратор"], ["operator", "Администратор"], ["coach", "Тренер"], ["lawyer", "Юрист"], ["trainee", "Стажёр (только Планер)"]];
const postOf = (u) => u.role === "admin" ? "owner" : u.role === "operator" ? (u.is_senior ? "senior" : "operator") : u.role;

/* ---------- старт ---------- */
async function boot() {
  const previousState = history.state;
  try {
    S.meta = await api("GET", "/api/meta");
    S.user = S.meta.user;
  } catch (e) { if (e.message !== "Нужно войти") renderLogin(); return; }
  S.view = visibleViews()[0][0];
  const requestedView = location.hash.slice(1);
  if (visibleViews().some(([k]) => k === requestedView)) S.view = requestedView;
  else try { const v = localStorage.getItem("crm.view"); if (v && visibleViews().some(([k]) => k === v)) S.view = v; } catch (_) {}
  shell();
  applyFestive();
  const restoreCard = previousState?.drawer && mayRestoreStudent(previousState.studentCard) ? previousState.studentCard : null;
  try { history.replaceState({ view: S.view, sub: navSnap(), depth: previousState?.depth || 0,
    ...(restoreCard ? { drawer: true, studentCard: restoreCard } : {}) }, "", "#" + S.view); } catch (_) {}
  await render();
  if (restoreCard) {
    drawerInHistory = true;
    await openStudent(restoreCard.id, { tab: restoreCard.tab, restoring: true });
  }
  setInterval(() => { if ($("#drawer").hidden && ["members", "skips"].includes(S.view)) render(); }, 60000);
  setInterval(() => { if ($("#drawer").hidden && S.view === "sales" && !document.hidden && !document.querySelector(".task-done:not([hidden])")) render(); }, 20000);
  if (can("inbox")) { pollCounts(); setInterval(pollCounts, 10000); }
  if (!S.meta.tour_pending && !restoreCard) offerAlerts();
  setTimeout(() => { if (typeof memoSync === "function") memoSync(); }, 4000);  // памятки для родителей: перерисовать, если поменялись цены
}
boot();

/* Руководителю и администраторам при каждом входе, пока не настроено: включить звук и уведомления.
   «Пропустить» откладывает до следующего входа. Настройка живёт в браузере, поэтому на каждом устройстве своя */
const notifReady = () => !("Notification" in window) || Notification.permission === "granted";
function offerAlerts() {
  if (!S.user || !["admin", "operator"].includes(S.user.role) || !can("inbox")) return;
  if (soundOn() && notifReady()) return;
  const draw = () => {
    const denied = "Notification" in window && Notification.permission === "denied";
    openDrawer(`<h2>🔔 Чтобы не пропустить клиента</h2>
      <p class="muted">Включите звук и уведомления: тогда CRM даст знать о новом сообщении, пропущенном звонке или заявке, даже если она свёрнута. Это займёт полминуты.</p>
      <div class="section"><h3>1. Звук</h3>
        <label class="check"><input type="checkbox" id="oa-sound" ${soundOn() ? "checked" : ""}> Звук, когда приходит сообщение, пропущенный звонок или заявка</label>
        <button class="btn" id="oa-test" style="margin-top:10px">🔊 Проверить звук</button>
        <div class="small muted" style="margin-top:6px">Не слышно? Проверьте громкость на компьютере или телефоне.</div></div>
      ${"Notification" in window ? `<div class="section"><h3>2. Уведомления</h3>
        ${notifReady() ? `<div>✅ Уведомления включены</div>`
          : denied ? `<div class="small">Браузер сейчас запрещает уведомления. Нажмите на значок слева от адреса сайта (замочек или настройки), найдите «Уведомления» и выберите «Разрешить». Потом обновите страницу.</div>`
          : `<button class="btn primary" id="oa-notif">Разрешить уведомления</button>
             <div class="small muted" style="margin-top:6px">Браузер спросит разрешение: нажмите «Разрешить».</div>`}</div>` : ""}
      <div class="row" style="gap:10px;margin-top:18px">
        <button class="btn primary" id="oa-done" style="flex:1">Готово</button>
        <button class="btn" id="oa-skip" style="flex:1">Пропустить, напомнить в следующий раз</button></div>`);
    $("#oa-sound").onchange = (e) => { try { localStorage.setItem("crm.sound", e.target.checked ? "1" : "0"); } catch (_) {} };
    $("#oa-test").onclick = () => ding();
    if ($("#oa-notif")) $("#oa-notif").onclick = async () => { await Notification.requestPermission(); draw(); };
    $("#oa-skip").onclick = () => closeDrawer();
    $("#oa-done").onclick = () => {
      closeDrawer();
      toast(soundOn() && notifReady() ? "Готово! Теперь CRM даст знать о новых сообщениях" : "Не всё включено: напомню при следующем входе");
    };
  };
  draw();
}

/* Выгружать и копировать списки может только руководитель: остальным копирование из CRM не даём (кроме полей ввода) */
document.addEventListener("copy", (e) => {
  if (!S.user || can("export")) return;
  const t = document.activeElement;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;
  const selection = window.getSelection();
  const region = selection?.anchorNode?.parentElement?.closest(".task-copyable");
  if (region && selection.rangeCount && region.contains(selection.focusNode)) return;
  e.preventDefault();
  toast("Копировать данные из CRM может только руководитель", true);
});

/* Документы: юридические документы, оферты, подписи сотрудников (руководитель и юрист) */
async function renderDocs(page) {
  const tabs = [...(can("docs", "full") ? [["legal", "Юридические документы"], ["offers", "Оферты"]] : []), ...(can("staffdocs", "view") ? [["staff", "Документы сотрудников"]] : [])];
  if (!tabs.some(([k]) => k === S.docTab)) S.docTab = tabs[0] && tabs[0][0];
  page.innerHTML = `<div class="page-head"><h2 class="grow">Документы</h2></div><div style="max-width:880px">
    ${tabs.length > 1 ? `<div class="seg" style="margin-bottom:14px">${tabs.map(([k, t]) => `<button data-dtab="${k}" class="${S.docTab === k ? "active" : ""}">${t}</button>`).join("")}</div>` : ""}
    <div class="section" id="doc-box"><div class="muted small">Загружаю…</div></div></div>`;
  page.querySelectorAll("[data-dtab]").forEach((b) => (b.onclick = () => { S.docTab = b.dataset.dtab; render(); }));
  const box = $("#doc-box");
  const fn = { legal: renderLegalSettings, offers: renderOffers, staff: renderStaffDocsAdmin }[S.docTab];
  if (fn) fn(box).catch(fail);
}


/* Перевод в другую группу вместе с абонементом: сначала показываем, что станет с оплатой, потом переводим */
function openTransfer(eid, box, d, groups, reload, initialDate) {
  if (!box.hidden) { box.hidden = true; return; }
  const e = d.enrollments.find((x) => x.id === eid);
  const t = new Date(), first = new Date(t.getFullYear(), t.getMonth() + 1, 1);
  const iso = (x) => `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}-${String(x.getDate()).padStart(2, "0")}`;
  const opts = groups.filter((g) => !g.program_id && !g.is_makeup && g.id !== e.group_id && !d.enrollments.some((x) => x.group_id === g.id));
  box.hidden = false;
  box.innerHTML = `<div class="row" style="margin-top:8px">
      <div class="field"><label>В группу</label><select data-trg><option value="">Выберите группу</option>${opts.map((g) => `<option value="${g.id}">${esc(g.name)}${g.age?' · '+esc(g.age)+' лет':''}</option>`).join("")}</select></div>
      <div class="field" style="flex:0 0 150px"><label>С какого дня</label><input type="date" data-trd value="${initialDate || iso(first)}" min="${iso(new Date(t.getFullYear(), t.getMonth(), 1))}"></div></div>
    ${e.program_id?'<div class="field"><label>Тариф новой группы</label><select data-trtar><option value="fixed">Негибкий</option><option value="flex">Гибкий</option></select></div>':""}
    <div data-trplan class="small muted">Выберите группу: покажу, что станет с абонементом и оплатой</div>`;
  const sel = box.querySelector("[data-trg]"), dt = box.querySelector("[data-trd]"), out = box.querySelector("[data-trplan]");
  let transferVersion=0;
  const calc = async () => {
    const request=++transferVersion;
    if (!sel.value) {out.textContent="Выберите группу";return;}
    const target=+sel.value,start=dt.value,tariff=box.querySelector("[data-trtar]")?.value||"fixed";
    out.textContent = "Считаю…";
    try {
      const p = await api("GET", `/api/enrollments/${eid}/transfer?group_id=${target}&date=${start}&tariff=${tariff}`);
      if(request!==transferVersion||!box.isConnected)return;
      const rows = p.months.map((m) => p.program_transfer?`<li><b>${esc(m.label)}</b><div>Остаток Арктики: ${rub(m.arena_credit)} · ОФП: ${rub(m.ofp_credit)}</div><div>Зачёт стоимости: ${rub(m.credit)} · Новая группа: ${m.new_lessons} занятий, ${rub(m.new_price)}</div></li>`:`<li><b>${esc(m.label)}</b>: было ${m.old_lessons} зан. за ${rub(m.old_price)} →
        ${m.keep_lessons ? `${m.keep_lessons} зан. в старой группе (${rub(m.keep_price)}) и ` : ""}${m.new_lessons} зан. в новой (${rub(m.new_price)})</li>`).join("");
      out.innerHTML = `<div style="margin:6px 0">С ${dShort(p.start)} в группу <b>${esc(p.to_group)}</b>. До этой даты ребёнок остаётся в журнале старой группы</div>
        ${p.program_transfer?'<p>Арктика и ОФП прекращаются с даты перевода. Остатки обоих направлений учитываются вместе. Если прежний абонемент оплачен частично, в балансе учитываются только реальные платежи.</p>':""}
        ${rows ? `<ul style="margin:4px 0 6px 18px">${rows}</ul><div>${p.program_transfer?`После перевода по всем счетам клиента: к оплате ${rub(p.projected_due)}, остаток на балансе ${rub(p.projected_deposit)}`:p.diff > 0 ? `Доплата ${rub(p.diff)}: появится как долг` : p.diff < 0 ? `Разница ${rub(-p.diff)} уйдёт на депозит` : "По деньгам без изменений"}</div>`
          : `<div>Абонементов с этой даты нет, переносить нечего</div>`}
        <button class="btn primary sm" data-trgo style="margin-top:8px">Перевести</button>`;
      out.querySelector("[data-trgo]").onclick = async () => {
        try { out.querySelector("[data-trgo]").disabled=true; await api("POST", `/api/enrollments/${eid}/transfer`, { group_id: target, date: start, tariff, expected:{diff:p.diff,credit_total:p.credit_total??null,months:p.months.map(m=>[m.sub_id,m.old_price,m.keep_price,m.new_price,m.new_lessons])} }); toast("Ученик переведён"); reload(); } catch (err) { fail(err); if(out.querySelector("[data-trgo]"))out.querySelector("[data-trgo]").disabled=false; }
      };
    } catch (err) { if(request===transferVersion)out.innerHTML = `<span style="color:#b42318">${esc(err.message)}</span>`; }
  };
  sel.onchange = calc; dt.onchange = calc; if(box.querySelector("[data-trtar]"))box.querySelector("[data-trtar]").onchange=calc;
}

/* Первый вход сотрудника: принять документы (NDA, правила работы с данными, согласие, журнал, ПЭП) */
async function renderStaffDocs(page) {
  const d = await api("GET", "/api/staff-docs");
  const need = d.docs.filter((x) => !x.accepted_at);
  page.innerHTML = `<div class="sd-wrap"><h2>Добро пожаловать в CRM, ${esc(d.name.split(" ")[0])}!</h2>
    <p class="muted">Перед началом работы прочитайте и примите документы. Они защищают данные наших клиентов и школу. Без этого CRM не откроется</p>
    ${need.map((x, i) => `<div class="section sd-doc"><label class="sd-check"><input type="checkbox" data-sd="${x.slug}"> <b>${esc(x.title)}</b></label>
      <details ${i === 0 ? "open" : ""}><summary class="linkish small">Прочитать полностью</summary><div class="legal-text">${x.html}</div></details></div>`).join("")}
    <div class="section"><div class="field"><label>Впишите свои фамилию и имя: это ваша подпись</label><input id="sd-name" placeholder="${esc(d.name)}" autocomplete="off"></div>
      <button class="btn primary" id="sd-ok" disabled>Принимаю и подписываю</button>
      <div class="small muted" style="margin-top:6px">Мы сохраним дату, время и IP-адрес подписания.</div></div></div>`;
  const ok = $("#sd-ok");
  const sync = () => { ok.disabled = !([...page.querySelectorAll("[data-sd]")].every((c) => c.checked) && $("#sd-name").value.trim().length > 2); };
  page.querySelectorAll("[data-sd]").forEach((c) => (c.onchange = sync));
  $("#sd-name").oninput = sync;
  ok.onclick = async () => {
    try {
      await api("POST", "/api/staff-docs/accept", { docs: need.map((x) => x.slug), signed_name: $("#sd-name").value });
      S.meta.staff_docs_pending = 0; toast("Спасибо! Документы подписаны"); render();
    } catch (e) { fail(e); }
  };
}

async function renderStaffDocsAdmin(box) {
  const d = await api("GET", "/api/staff-docs/overview");
  box.innerHTML = `<h3>Документы сотрудников</h3><div class="small muted">NDA, правила работы с данными, согласие, журнал действий, электронная подпись. Каждый подписывает при первом входе</div>
    ${d.items.map((u) => `<div class="list-row"><div class="grow">${esc(u.name)}</div>
      ${u.pending ? `<span class="pill red">не подписал${u.last ? " новую редакцию" : ""}</span>` : `<span class="pill green">подписал ${dtWhen(u.last)}</span>`}
      ${u.tour ? `<span class="pill green" title="Обучение в CRM">обучение ${dtWhen(u.tour)}</span>` : `<span class="pill">обучение не пройдено</span>`}
      ${u.last ? `<button class="btn sm" data-sdlog="${u.id}">Лист подписания</button>` : ""}</div>`).join("") || `<div class="small muted">Сотрудников пока нет</div>`}`;
  box.querySelectorAll("[data-sdlog]").forEach((b) => (b.onclick = async () => {
    const l = await api("GET", `/api/staff-docs/log/${b.dataset.sdlog}`);
    openDrawer(`<h2>Лист подписания</h2><div class="muted">${esc(l.name)}</div>
      <table class="tbl" style="margin-top:10px"><thead><tr><th>Документ</th><th>Подпись</th><th>Когда</th><th>IP</th><th>Отпечаток</th></tr></thead>
      <tbody>${l.items.map((x) => `<tr><td>${esc(x.title)}</td><td>${esc(x.signed_name)}</td><td>${esc(x.accepted_at.replace("T", " "))}</td><td>${esc(x.ip)}</td><td><code>${esc(x.sha256.slice(0, 16))}</code></td></tr>`).join("")}</tbody></table>
      <div class="small muted" style="margin-top:8px">Отпечаток подтверждает, какой именно текст подписан. Для печати: меню браузера → Печать</div>`);
  }));
}

/* Настройки → Резервные копии */
/* Настройки → «🩺 Проверка соединений» (только владелец): кто и как подключался к CRM, по журналу сервера */
async function renderDiag(box, ip = "") {
  box.innerHTML = `<h3>🩺 Проверка соединений</h3>
    <div class="small muted">Попросите сотрудника открыть ab-plus.ru и прислать скриншот с сайта 2ip.ru (там его IP-адрес). Впишите IP сюда и нажмите «Показать». Видно, дошёл ли он до сервера и на каком шаге оборвалось</div>
    <div class="row" style="gap:8px;margin-top:8px"><input id="dg-ip" placeholder="IP-адрес, например 95.24.1.2" value="${esc(ip)}">
      <button class="btn primary" id="dg-go" style="flex:0 0 auto">Показать</button><button class="btn" id="dg-all" style="flex:0 0 auto">Все за последнее время</button></div>
    <div id="dg-out" class="small muted" style="margin-top:10px">Загружаю…</div>`;
  const run = async (v) => {
    const out = $("#dg-out");
    try {
      const d = await api("GET", "/api/diag/conn?ip=" + encodeURIComponent(v || ""));
      if (!d.enabled) { out.textContent = "Журнал ещё не включился на сервере: подождите 5-10 минут и откройте снова"; return; }
      const verdict = v ? (d.rows.length ? `✅ Соединения с ${esc(v)} доходят до сервера: ${d.rows.length}` : d.errors.length ? `⚠️ С ${esc(v)} были попытки, но рукопожатие не завершилось (см. ниже)` : `❌ С ${esc(v)} ничего не дошло до сервера`) : "";
      out.innerHTML = `${verdict ? `<p><b>${verdict}</b></p>` : ""}
        ${!v && d.top_ips.length ? `<div>Чаще всего: ${d.top_ips.map(([a, n]) => `<button class="chip" data-dgip="${esc(a)}">${esc(a)} · ${n}</button>`).join(" ")}</div>` : ""}
        ${d.rows.length ? `<table class="tbl" style="margin-top:8px"><thead><tr><th>Когда</th><th>IP</th><th>TLS</th><th>Ключ</th><th>Ответ</th><th>Байт</th><th>Сек</th><th>Что</th></tr></thead>
          <tbody>${d.rows.map((r) => `<tr><td>${esc(r.at.slice(5))}</td><td>${esc(r.ip)}</td><td>${esc(r.tls)}</td><td>${esc(r.key)}</td><td>${r.status}</td><td>${r.bytes}</td><td>${esc(r.secs)}</td><td title="${esc(r.ua)}">${esc(r.req)}</td></tr>`).join("")}</tbody></table>` : ""}
        ${d.errors.length ? `<h4 style="margin:12px 0 4px">Обрывы и ошибки</h4><pre class="small" style="white-space:pre-wrap">${d.errors.map(esc).join("\n")}</pre>` : ""}`;
      out.querySelectorAll("[data-dgip]").forEach((b) => (b.onclick = () => renderDiag(box, b.dataset.dgip)));
    } catch (e) { out.textContent = e.message; }
  };
  // зелёной подсвечена та кнопка, чей результат сейчас на экране
  const mark = (all) => { $("#dg-all").classList.toggle("primary", all); $("#dg-go").classList.toggle("primary", !all); };
  $("#dg-go").onclick = () => { mark(false); run($("#dg-ip").value.trim()); };
  $("#dg-all").onclick = () => { mark(true); $("#dg-ip").value = ""; run(""); };
  mark(!ip);
  run(ip);
}

async function renderBackup(box) {
  const d = await api("GET", "/api/backup");
  box.innerHTML = `<h3>Резервные копии</h3>
    <div class="small">На сервере: каждый час, хранится ${d.local_count} ${d.local_count === 1 ? "копия" : "копий"}${d.last_local ? `, последняя ${dtWhen(d.last_local)}` : ""}</div>
    <div class="small">${d.yadisk ? `На Яндекс Диске (зашифрованные): ${d.last_remote ? "последняя " + esc(d.last_remote) : "ещё не было"}` : `<b style="color:#b42318">Яндекс Диск не подключён: копии только на этом сервере</b>`}</div>
    <div class="small">Excel со всей базой: каждый час${d.excel_last ? `, последний ${esc(d.excel_last.replace("T", " "))}` : ", ещё не было"}${d.yadisk ? " (на Диске в папке Excel)" : ""}</div>
    ${d.last_error ? `<div class="alert" style="margin-top:6px">${esc(d.last_error)}</div>` : ""}
    ${d.excel_error ? `<div class="alert" style="margin-top:6px">Excel: ${esc(d.excel_error)}</div>` : ""}
    <div class="field" style="margin-top:10px"><label>Токен Яндекс Диска</label>
      <div class="row"><input id="bk-token" type="password" autocomplete="off" placeholder="${d.yadisk ? "подключён, вставьте новый, чтобы заменить" : "вставьте токен"}"><button class="btn sm" id="bk-save" style="flex:0">Сохранить</button></div>
      <div class="small muted">Как получить: откройте yandex.ru/dev/disk/poligon под аккаунтом, где хотите хранить копии, нажмите «Получить OAuth-токен», скопируйте его и вставьте сюда</div></div>
    <div class="field"><label>Пароль шифрования копий</label><div class="row"><code id="bk-key" style="flex:1;padding:8px;background:var(--surface-2);border-radius:8px">${esc(d.key)}</code>
      <button class="btn sm" id="bk-copy" style="flex:0">Копировать</button></div>
      <div class="small" style="color:#b42318">Сохраните этот пароль отдельно от CRM, например в заметках телефона. Без него копию с Диска не открыть</div></div>
    <button class="btn sm primary" id="bk-run">Сделать копию сейчас</button>
    ${can("export") ? `<div class="field" style="margin-top:14px"><label>Копия, которую можно прочитать</label>
      <div class="row" style="gap:8px;flex-wrap:wrap"><a class="btn sm" style="flex:0 0 auto" href="/api/export/all?t=${Date.now()}">📥 Вся база в Excel</a>
        <a class="btn sm" style="flex:0 0 auto" href="/api/export/all?part=contacts&t=${Date.now()}">📞 Только контакты</a></div>
      <div class="small muted" style="margin-top:6px"><b>Вся база:</b> дети и родители с телефонами, абонементы, оплаты, посещения, группы, сотрудники.<br>
        <b>Только контакты:</b> один лист для обзвона: ребёнок, родитель, телефон, группа, ученик или лид, пометка «не звонить». Без оплат.<br>
        Храните файлы в надёжном месте: в них персональные данные клиентов</div></div>` : ""}`;
  $("#bk-copy").onclick = () => navigator.clipboard.writeText(d.key).then(() => toast("Пароль скопирован"));
  $("#bk-save").onclick = async () => { try { await api("POST", "/api/backup", { yadisk_token: $("#bk-token").value }); toast("Сохранено"); renderBackup(box); } catch (e) { fail(e); } };
  $("#bk-run").onclick = async (e) => {
    e.target.disabled = true; e.target.textContent = "Делаю копию…";
    try { const r = await api("POST", "/api/backup", { run: true }); toast(r.result.error ? "Копия на сервере есть, на Диск не ушла" : r.result.remote ? "Копия сохранена и отправлена на Яндекс Диск" : "Копия сохранена на сервере"); renderBackup(box); } catch (err) { fail(err); }
  };
}

function normalizedCopyPhone(phone) {
  let digits = String(phone).replace(/\D/g, "");
  if (digits.length === 10) digits = "7" + digits;
  if (digits.length === 11 && digits[0] === "8") digits = "7" + digits.slice(1);
  if (!/^7\d{10}$/.test(digits)) throw new Error("Проверьте номер телефона");
  return "+" + digits;
}
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-copy-phone]");
  if (!b) return;
  e.preventDefault();
  try {
    const text = normalizedCopyPhone(b.dataset.copyPhone);
    try { await navigator.clipboard.writeText(text); }
    catch (_) {
      const input = document.createElement("textarea"); input.value = text;
      input.style.position = "fixed"; input.style.opacity = "0";
      document.body.appendChild(input); input.select();
      const ok = document.execCommand("copy"); input.remove();
      if (!ok) throw new Error("Не удалось скопировать номер");
    }
    toast("Номер скопирован");
  } catch (error) { fail(error); }
});


async function logoutCurrentAccount() {
  const buttons = [...document.querySelectorAll("[data-account-logout], #logout")];
  buttons.forEach(b => b.disabled = true);
  try {
    try { await api("POST", "/api/logout", {}); }
    catch (err) { if (err.message !== "Нужно войти") throw err; }
    try { sessionStorage.setItem("crm.force_password_login", "1"); } catch (_) {}
    // A full reload clears private in-memory data, timers and the previous user's drafts.
    location.reload();
  } catch (err) { buttons.forEach(b => b.disabled = false); fail(err); }
}
