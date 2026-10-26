"use strict";
/* Абонемент Плюс — интерфейс без сборки и зависимостей. */

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

async function api(method, url, body) {
  const opt = { method, headers: {}, credentials: "same-origin" };
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(url, opt);
  let data = null;
  try { data = await r.json(); } catch (_) { /* пусто */ }
  if (r.status === 401 && url !== "/api/login") { renderLogin(); throw new Error("Нужно войти"); }
  if (!r.ok) throw Object.assign(new Error((data && data.error) || "Ошибка"), { status: r.status });
  return data;
}

let toastTimer;
function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast" + (err ? " err" : ""); t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), 3200);
}
const fail = (e) => toast(e.message || "Ошибка", true);

/* ---------- вход ---------- */
function renderLogin() {
  closeDrawer();
  app.innerHTML = `
  <div class="login">
    <h1>Абонемент <span style="color:var(--accent)">Плюс</span></h1>
    <div class="muted">Вход для сотрудников</div>
    <form id="login-form">
      <div class="field"><label>Логин</label><input name="login" autocomplete="username" required></div>
      <div class="field"><label>Пароль</label><input name="password" type="password" autocomplete="current-password" required></div>
      <button class="btn primary" style="width:100%">Войти</button>
      <div class="error" id="login-err"></div>
    </form>
  </div>`;
  $("#login-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try { await api("POST", "/api/login", { login: f.get("login"), password: f.get("password") }); boot(); }
    catch (err) { $("#login-err").textContent = err.message; }
  };
}

/* ---------- каркас ---------- */
const VIEWS = [
  ["dashboard", "Дашборд"], ["sales", "Продажи"], ["members", "Ученики"], ["skips", "Пропуски"],
  ["journal", "Журнал"], ["search", "Все клиенты"], ["settings", "Настройки"],
];

const isCoach = () => S.user && S.user.role === "coach";
const visibleViews = () => isCoach() ? VIEWS.filter(([k]) => k === "journal" || k === "settings")
  : S.user.role === "admin" ? VIEWS : VIEWS.filter(([k]) => k !== "dashboard");

function shell() {
  if (isCoach() && !["journal", "settings"].includes(S.view)) S.view = "journal";
  app.innerHTML = `
  <header class="topbar">
    <div class="brand">Абонемент <span>Плюс</span></div>
    <nav class="nav">${visibleViews().map(([k, t]) => `<button data-view="${k}" class="${S.view === k ? "active" : ""}">${t}</button>`).join("")}</nav>
    ${isCoach() ? "" : `<button class="btn sm" id="new-client">+ Клиент</button>`}
  </header>
  <main class="page" id="page"></main>`;
  app.querySelectorAll("[data-view]").forEach((b) => (b.onclick = () => go(b.dataset.view)));
  if ($("#new-client")) $("#new-client").onclick = openNewClient;
}

function go(view) {
  S.view = view;
  try { localStorage.setItem("crm.view", view); } catch (_) {}
  app.querySelectorAll("[data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  render();
}

async function render() {
  const page = $("#page");
  if (!page) return;
  try {
    if (["sales", "members", "skips"].includes(S.view)) return await renderBoard(page, S.view);
    if (S.view === "dashboard") return await renderDashboard(page);
    if (S.view === "journal") return await renderJournal(page);
    if (S.view === "search") return await renderSearch(page);
    if (S.view === "settings") return await renderSettings(page);
  } catch (e) { if (e.message !== "Нужно войти") page.innerHTML = `<div class="error">${esc(e.message)}</div>`; }
}

/* ---------- дашборд ---------- */
const pct = (v) => (v === null || v === undefined ? "—" : v + "%");
const tile = (label, value, sub, tone = "") => `<div class="tile ${tone}"><div class="tile-label">${esc(label)}</div><div class="tile-value">${value}</div>${sub ? `<div class="tile-sub">${sub}</div>` : ""}</div>`;

async function renderDashboard(page) {
  if (!S.dashMonth) S.dashMonth = S.meta.today.slice(0, 7);
  const d = await api("GET", "/api/dashboard?month=" + S.dashMonth);
  const months = [];
  for (let i = 0; i < 12; i++) { const dt = new Date(S.meta.today); dt.setDate(1); dt.setMonth(dt.getMonth() - i); months.push(isoDate(dt).slice(0, 7)); }
  const st = d.students, m = d.money, sa = d.sales, at = d.attendance;
  const maxRev = Math.max(1, ...d.trend.map((t) => t.revenue));
  const maxBr = Math.max(1, ...st.by_branch.map((b) => b.students));
  page.innerHTML = `
    <div class="page-head"><h2 class="grow">Дашборд</h2>
      <select id="dash-month" style="width:auto">${months.map((x) => `<option value="${x}" ${x === S.dashMonth ? "selected" : ""}>${monthLabel(x)}</option>`).join("")}</select></div>

    <div class="tiles">
      ${tile("Учеников сейчас", st.active, `гибкий ${st.flex} · негибкий ${st.fixed}`)}
      ${tile("Выручка за " + d.month_label.split(" ")[0], rub(m.revenue), `средний чек ${rub(m.avg_check)}`, "accent")}
      ${tile("Продлили на " + d.next_month_label.split(" ")[0], `${m.next_paid} <small>из ${st.active}</small>`, `${pct(m.renewal_pct)} · ${rub(m.next_paid_sum)}`)}
      ${tile("Ещё не продлили", m.not_renewed, m.next_invoiced ? `счёт выставлен: ${m.next_invoiced} на ${rub(m.next_invoiced_sum)}` : "", m.not_renewed ? "warn" : "")}
    </div>

    <div class="dash-grid">
      <section class="section">
        <h3>Выручка по месяцам</h3>
        <div class="bars">${d.trend.map((t) => `<div class="bar-col ${t.month === d.month ? "cur" : ""}">
          <div class="bar-val">${t.revenue ? Math.round(t.revenue / 1000) + "к" : ""}</div>
          <div class="bar" style="height:${Math.round((t.revenue / maxRev) * 100)}%"></div>
          <div class="bar-lbl">${t.label.slice(0, 3)}</div></div>`).join("")}</div>
        <div class="small muted">Оплаченные абонементы, ₽</div>
      </section>

      <section class="section">
        <h3>Продажи за ${esc(d.month_label)}</h3>
        <div class="funnel">
          ${[["Новые лиды", sa.leads], ["Пробные занятия", sa.trials], ["Купили абонемент", sa.bought]].map(([l, v], i, arr) => `
            <div class="f-row"><span>${l}</span><div class="f-bar"><div style="width:${v ? Math.max(4, Math.round((v / Math.max(1, arr[0][1], arr[1][1])) * 100)) : 0}%"></div></div><b>${v}</b></div>`).join("")}
        </div>
        <div class="kv" style="margin-top:10px">
          <dt>Конверсия пробное → покупка</dt><dd><b>${pct(sa.trial_conversion_pct)}</b></dd>
          <dt>Отказы</dt><dd>${sa.refused}</dd>
          <dt>Сейчас в воронке</dt><dd>${sa.in_funnel}</dd>
        </div>
      </section>

      <section class="section">
        <h3>Ученики по филиалам</h3>
        ${st.by_branch.map((b) => `<div class="f-row"><span>${esc(b.branch)}</span><div class="f-bar"><div style="width:${Math.max(4, Math.round((b.students / maxBr) * 100))}%"></div></div><b>${b.students}</b></div>`).join("")}
        <div class="kv" style="margin-top:10px">
          <dt>Не оплатили текущий месяц</dt><dd>${m.unpaid_cur}</dd>
          <dt>Ушли в этом месяце</dt><dd>${st.left}</dd>
        </div>
      </section>

      <section class="section">
        <h3>Посещаемость и отработки</h3>
        <div class="tiles mini">
          ${tile("Посещаемость", pct(at.rate_pct), `${at.present} посещений · ${at.absent} пропусков`)}
          ${tile("Можно отработать", at.can_make_up, at.at_risk_rub ? `≈ ${rub(at.at_risk_rub)} могут уйти в перерасчёт (гибкий тариф)` : "", at.can_make_up ? "warn" : "")}
        </div>
        <div class="kv" style="margin-top:10px">
          <dt>Отработали</dt><dd>${at.makeups_done}</dd>
          <dt>Записаны на отработку</dt><dd>${at.makeups_booked}</dd>
          <dt>Ушло в перерасчёт</dt><dd>${at.credited}</dd>
        </div>
      </section>
    </div>`;
  $("#dash-month").onchange = (e) => { S.dashMonth = e.target.value; render(); };
}

/* ---------- воронки ---------- */
const BOARD_TITLES = { sales: "Воронка продаж", members: "Ученики и продления", skips: "Пропуски и отработки" };

async function renderBoard(page, funnel) {
  const data = await api("GET", "/api/board/" + funnel);
  page.innerHTML = `
    <div class="page-head"><h2 class="grow">${BOARD_TITLES[funnel]}</h2>
      <button class="btn sm" id="refresh">Обновить</button></div>
    ${data.note ? `<div class="note">${esc(data.note)}</div>` : ""}
    <div class="board">${data.columns.map((c) => `
      <section class="col" data-stage="${c.key}">
        <div class="col-head"><h3>${esc(c.title)}</h3>${c.auto ? `<span class="auto">сам</span>` : ""}<span class="count">${c.cards.length}</span></div>
        <div class="col-body">${c.cards.length ? c.cards.map((k) => cardHtml(k, data.draggable && !c.auto)).join("") : `<div class="empty">Пусто</div>`}</div>
      </section>`).join("")}
    </div>`;
  $("#refresh").onclick = render;
  page.querySelectorAll(".card").forEach((el) => (el.onclick = () => openStudent(+el.dataset.id)));
  if (data.draggable) enableDrag(page, funnel, data.columns);
}

function cardHtml(k, draggable) {
  const sub = [k.parent, k.phone].filter(Boolean).join(" · ");
  const meta2 = [k.group, k.tariff === "flex" ? "гибкий" : (k.group ? "негибкий" : ""), k.siblings ? `братьев/сестёр: ${k.siblings}` : ""].filter(Boolean).join(" · ");
  return `<article class="card ${k.color || ""}" data-id="${k.id}" ${draggable ? 'draggable="true"' : ""}>
    <div class="name">${esc(k.name)}</div>
    <div class="meta">${esc(sub)}</div>
    ${meta2 ? `<div class="meta">${esc(meta2)}</div>` : ""}
    ${k.badge ? `<span class="badge">${esc(k.badge)}</span>` : ""}
  </article>`;
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
      try { await api("POST", `/api/students/${dragId}/move`, { funnel, stage }); render(); }
      catch (err) { fail(err); }
    });
  });
}

/* ---------- карточка ---------- */
function openDrawer(html) { $("#drawer-body").innerHTML = html; $("#drawer").hidden = false; document.body.style.overflow = "hidden"; }
function closeDrawer() { const d = $("#drawer"); if (d) d.hidden = true; document.body.style.overflow = ""; }
document.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) { closeDrawer(); } });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

async function openStudent(id) {
  let d;
  try { d = await api("GET", `/api/students/${id}`); } catch (e) { return fail(e); }
  const s = d.student, g = d.group, c = d.counters;
  const groups = S.meta.groups.filter((x) => !x.is_makeup);
  const makeupGroups = S.meta.groups.filter((x) => x.is_makeup);
  const isMember = s.funnel === "members";

  const stageOpts = isMember
    ? [["active", "Занимается"], ["left", "Ушёл"]]
    : S.meta.sales_stages.filter((x) => !x.auto).map((x) => [x.key, x.title]);

  const html = `
  <h2>${esc(s.name)}</h2>
  <div class="muted small" style="margin-top:4px">${esc(d.stage_label)}${s.birth ? " · " + esc(ageText(s.birth)) : ""}</div>

  <div class="section">
    <div class="row">
      <div class="field"><label>Этап</label><select id="f-stage">${stageOpts.map(([k, t]) => `<option value="${k}" ${k === s.stage ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></div>
      <div class="field"><label>Тариф</label><select id="f-tariff"><option value="fixed" ${s.tariff === "fixed" ? "selected" : ""}>Негибкий</option><option value="flex" ${s.tariff === "flex" ? "selected" : ""}>Гибкий</option></select></div>
    </div>
    <div class="field"><label>Группа</label><select id="f-group"><option value="">— не выбрана —</option>${groups.map((x) => `<option value="${x.id}" ${x.id === s.group_id ? "selected" : ""}>${esc(x.name)} · ${esc(x.age)} лет</option>`).join("")}</select></div>
    ${!isMember ? `<div class="field"><label>Пробное занятие</label><input id="f-trial" type="datetime-local" value="${esc(s.trial_at || "")}"></div>` : ""}
  </div>

  <div class="section">
    <h3>Семья ${esc(d.family.name)}</h3>
    <dl class="kv">${d.parents.map((p) => `<dt>${esc(p.name)}</dt><dd><a href="tel:${esc(p.phone)}">${esc(p.phone)}</a>${p.messenger ? ` <span class="pill">${esc(p.messenger)}</span>` : ""}</dd>`).join("")}</dl>
    ${d.siblings.length ? `<div style="margin-top:10px" class="chips">${d.siblings.map((x) => `<button class="chip" data-open="${x.id}">${esc(x.name)}</button>`).join("")}</div>` : ""}
    <button class="btn sm" style="margin-top:10px" id="add-sibling">+ Брат или сестра</button>
  </div>

  ${isMember ? `
  <div class="section">
    <h3>Пропуски и отработки</h3>
    <div class="counters">
      <div class="counter"><b>${c.absent_cur}</b><span>пропущено в этом месяце</span></div>
      <div class="counter"><b>${c.absent_prev}</b><span>пропущено в прошлом</span></div>
      <div class="counter"><b>${c.made_up}</b><span>отработано</span></div>
      <div class="counter hl"><b>${c.available}</b><span>можно отработать</span></div>
    </div>
    ${c.booked ? `<div class="small muted" style="margin-top:8px">Записан на отработку: ${c.booked}</div>` : ""}
    ${c.credited ? `<div class="small muted" style="margin-top:4px">Ушло в перерасчёт: ${c.credited}</div>` : ""}
    ${c.available ? `
      <div class="row" style="margin-top:12px">
        <div class="field"><label>Группа отработок</label><select id="mk-group">${makeupGroups.map((x) => `<option value="${x.id}">${esc(x.name)} · ${esc(x.age)} лет</option>`).join("")}</select></div>
        <div class="field"><label>Дата</label><select id="mk-date">${nextSaturdays(5).map((x) => `<option value="${x}">${dShort(x)}</option>`).join("")}</select></div>
      </div>
      <button class="btn primary sm" id="mk-book">Записать на отработку</button>` : ""}
    ${d.makeups.length ? `<ul class="history" style="margin-top:10px">${d.makeups.map((m) => `<li>${dShort(m.date)} · ${esc(m.group_name)} · за пропуск ${dShort(m.absence_date)} <span class="pill ${m.status === "done" ? "green" : m.status === "missed" ? "red" : ""}">${{ booked: "записан", done: "отработал", missed: "не пришёл", cancelled: "отменена" }[m.status]}</span>
      ${m.status === "booked" ? `<button class="btn sm" data-mk="${m.id}" data-st="cancelled">Отменить</button>` : ""}</li>`).join("")}</ul>` : ""}
  </div>` : ""}

  <div class="section">
    <h3>Абонемент</h3>
    ${s.group_id ? `
      <div class="row"><div class="field"><label>Месяц</label><select id="q-month">${[d.cur_month, d.next_month].map((m) => `<option value="${m}" ${m === d.next_month ? "selected" : ""}>${monthLabel(m)}</option>`).join("")}</select></div></div>
      <div id="quote" class="quote muted">Считаю…</div>
      <button class="btn primary sm" id="invoice">Выставить счёт</button>` : `<div class="muted small">Выберите группу, чтобы посчитать цену.</div>`}
    ${d.subscriptions.length ? `<ul class="history" style="margin-top:10px">${d.subscriptions.map((x) => `<li><b>${monthLabel(x.month)}</b> · ${rub(x.price)} · ${x.lessons} зан.${x.credit_lessons ? ` · перерасчёт ${x.credit_lessons}` : ""}${x.late_fee ? ` · наценка ${rub(x.late_fee)}` : ""}
      <span class="pill ${x.status === "paid" ? "green" : x.status === "cancelled" ? "" : "yellow"}">${{ paid: "оплачен", invoiced: "ждёт оплаты", cancelled: "отменён" }[x.status]}</span>
      ${x.status === "invoiced" ? `<button class="btn sm" data-pay="${x.id}">Оплачен</button> <button class="btn sm danger" data-cancel="${x.id}">Отменить</button>` : ""}</li>`).join("")}</ul>` : ""}
  </div>

  <div class="section">
    <h3>Отпуск</h3>
    <div class="row">
      <div class="field"><label>С</label><input id="v-start" type="date"></div>
      <div class="field"><label>По</label><input id="v-end" type="date"></div>
    </div>
    <button class="btn sm" id="v-add">Добавить отпуск</button>
    ${d.vacations.length ? `<ul class="history" style="margin-top:10px">${d.vacations.map((v) => `<li>${dShort(v.start)} — ${dShort(v.end)} <button class="btn sm danger" data-vdel="${v.id}">Удалить</button></li>`).join("")}</ul>` : ""}
    <div class="small muted" style="margin-top:6px">Дни отпуска не входят в цену абонемента. Предупредить нужно до оплаты месяца.</div>
  </div>

  <div class="section">
    <h3>История</h3>
    <div class="row" style="margin-bottom:10px"><input id="note" placeholder="Комментарий, итог звонка…"><button class="btn sm" id="note-add" style="flex:0">Добавить</button></div>
    <ul class="history">${d.history.map((h) => `<li><div class="when">${dtShort(h.ts)} · ${esc(h.user_name)}</div>${esc(h.text)}</li>`).join("")}</ul>
  </div>`;
  openDrawer(html);
  const body = $("#drawer-body");
  const reload = () => { openStudent(id); render(); };
  const patch = async (data) => { try { await api("PATCH", `/api/students/${id}`, data); reload(); } catch (e) { fail(e); } };

  $("#f-stage").onchange = async (e) => {
    try { await api("POST", `/api/students/${id}/move`, { funnel: s.funnel, stage: e.target.value }); reload(); } catch (err) { fail(err); }
  };
  $("#f-tariff").onchange = (e) => patch({ tariff: e.target.value });
  $("#f-group").onchange = (e) => patch({ group_id: e.target.value ? +e.target.value : "" });
  if ($("#f-trial")) $("#f-trial").onchange = (e) => patch({ trial_at: e.target.value });
  body.querySelectorAll("[data-open]").forEach((b) => (b.onclick = () => openStudent(+b.dataset.open)));
  $("#add-sibling").onclick = () => openNewClient({ family_id: s.family_id, family_name: d.family.name });

  if ($("#mk-book")) $("#mk-book").onclick = async () => {
    try { await api("POST", `/api/students/${id}/makeups`, { group_id: +$("#mk-group").value, date: $("#mk-date").value }); toast("Записан на отработку"); reload(); } catch (e) { fail(e); }
  };
  body.querySelectorAll("[data-mk]").forEach((b) => (b.onclick = async () => {
    try { await api("POST", `/api/makeups/${b.dataset.mk}/status`, { status: b.dataset.st }); reload(); } catch (e) { fail(e); }
  }));

  const loadQuote = async () => {
    const box = $("#quote"); if (!box) return;
    try {
      const q = await api("GET", `/api/students/${id}/quote?month=${$("#q-month").value}`);
      box.classList.remove("muted");
      box.innerHTML = `<div class="total">${rub(q.total)}</div>
        <div class="small">${q.lessons_paid} занятий × ${rub(q.per_lesson)} = ${rub(q.base)}${q.vacation_lessons ? ` · отпуск: −${q.vacation_lessons} зан.` : ""}</div>
        ${q.credit_lessons ? `<div class="small">Перерасчёт пропусков (${q.credit_absence_dates.map(dShort).join(", ")}): −${rub(q.credit)}</div>` : ""}
        ${q.late_fee ? `<div class="small">Наценка за оплату после ${S.meta.settings.renewal_deadline_day} числа: +${rub(q.late_fee)}</div>` : ""}
        <div class="small muted">Даты: ${q.dates.map(dShort).join(", ")}</div>`;
    } catch (e) { box.textContent = e.message; }
  };
  if ($("#q-month")) { $("#q-month").onchange = loadQuote; loadQuote(); }
  if ($("#invoice")) $("#invoice").onclick = async () => {
    try { const r = await api("POST", `/api/students/${id}/invoice`, { month: $("#q-month").value }); toast(`Счёт выставлен: ${rub(r.quote.total)}`); reload(); } catch (e) { fail(e); }
  };
  body.querySelectorAll("[data-pay]").forEach((b) => (b.onclick = async () => {
    try { await api("POST", `/api/subscriptions/${b.dataset.pay}/pay`, {}); toast("Оплата отмечена"); reload(); } catch (e) { fail(e); }
  }));
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
  $("#note-add").onclick = async () => {
    const t = $("#note").value.trim(); if (!t) return;
    try { await api("POST", `/api/students/${id}/note`, { text: t }); openStudent(id); } catch (e) { fail(e); }
  };
}

function ageText(birth) {
  const b = new Date(birth + "T00:00:00"), n = new Date();
  let y = n.getFullYear() - b.getFullYear();
  if (n < new Date(n.getFullYear(), b.getMonth(), b.getDate())) y--;
  return isFinite(y) ? `${y} ${y % 10 === 1 && y % 100 !== 11 ? "год" : [2, 3, 4].includes(y % 10) && ![12, 13, 14].includes(y % 100) ? "года" : "лет"}` : "";
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
      <div class="field"><label>Телефон родителя</label><input name="phone" type="tel" required placeholder="+7 900 000-00-00"></div>
      <div id="dup"></div>
      <div class="field"><label>Имя родителя</label><input name="parent_name" required></div>
      <div class="field"><label>Мессенджер</label><select name="messenger"><option>WhatsApp</option><option>Telegram</option><option>MAX</option><option value="">—</option></select></div>`}
    <div class="field"><label>Имя ребёнка</label><input name="name" required></div>
    <div class="row">
      <div class="field"><label>Дата рождения</label><input name="birth" type="date"></div>
      <div class="field"><label>Куда</label><select name="funnel"><option value="sales">Лид (воронка продаж)</option><option value="members">Уже занимается</option></select></div>
    </div>
    <div class="field"><label>Группа</label><select name="group_id"><option value="">— позже —</option>${groups.map((x) => `<option value="${x.id}">${esc(x.name)} · ${esc(x.age)} лет</option>`).join("")}</select></div>
    <div class="row">
      <div class="field"><label>Тариф</label><select name="tariff"><option value="fixed">Негибкий</option><option value="flex">Гибкий</option></select></div>
      <div class="field"><label>Источник</label><input name="source" placeholder="Сайт, промоутер…"></div>
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
    if (familyId) f.family_id = familyId;
    try { const r = await api("POST", "/api/students", f); toast("Клиент создан"); render(); openStudent(r.id); }
    catch (err) { fail(err); }
  };
}

/* ---------- журнал ---------- */
async function renderJournal(page) {
  const groups = S.meta.groups;
  if (!groups.length) { page.innerHTML = `<h2>Журнал посещений</h2><p class="muted">За вами пока не закреплено ни одной группы. Попросите администратора.</p>`; return; }
  if (!S.journal.group || !groups.some((x) => x.id === S.journal.group)) S.journal.group = groups[0].id;
  if (!S.journal.date) S.journal.date = S.meta.today;
  const j = await api("GET", `/api/journal?group_id=${S.journal.group}&date=${S.journal.date}`);
  const g = j.group;
  page.innerHTML = `
    <div class="page-head"><h2 class="grow">Журнал посещений</h2></div>
    <div class="row" style="max-width:640px;margin-bottom:14px">
      <div class="field"><label>Группа</label><select id="j-group">${groups.map((x) => `<option value="${x.id}" ${x.id === S.journal.group ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select></div>
      <div class="field" style="flex:0 0 170px"><label>Дата</label><input id="j-date" type="date" value="${S.journal.date}"></div>
    </div>
    <div class="note">${esc(g.branch)} · ${g.weekdays.split(",").map((x) => WD[+x]).join(", ")} ${esc(g.time)} · ${esc(g.age)} лет${j.has_lesson ? "" : " · <b>в этот день у группы нет занятия</b>"}</div>
    <div class="list">${j.rows.length ? j.rows.map((r) => g.is_makeup ? makeupRow(r) : journalRow(r)).join("") : `<div class="list-row muted">${g.is_makeup ? "На эту дату никто не записан на отработку" : "В группе пока нет учеников"}</div>`}</div>`;
  $("#j-group").onchange = (e) => { S.journal.group = +e.target.value; render(); };
  $("#j-date").onchange = (e) => { S.journal.date = e.target.value; render(); };
  if (!isCoach()) page.querySelectorAll("[data-who]").forEach((el) => (el.onclick = () => openStudent(+el.dataset.who)));
  page.querySelectorAll("[data-att]").forEach((b) => (b.onclick = async () => {
    try { await api("POST", "/api/attendance", { student_id: +b.dataset.sid, group_id: S.journal.group, date: S.journal.date, status: b.dataset.att }); render(); } catch (e) { fail(e); }
  }));
  page.querySelectorAll("[data-mk]").forEach((b) => (b.onclick = async () => {
    try { await api("POST", `/api/makeups/${b.dataset.mk}/status`, { status: b.dataset.st }); render(); } catch (e) { fail(e); }
  }));
}

function journalRow(r) {
  if (r.status === "vacation") return `<div class="list-row"><div class="grow click" data-who="${r.id}">${esc(r.name)}</div><span class="pill yellow">Отпуск</span></div>`;
  if (r.status === "trial") return `<div class="list-row"><div class="grow click" data-who="${r.id}">${esc(r.name)}</div><span class="pill green">Пробное ${r.trial_at.slice(11, 16)}</span></div>`;
  const locked = r.resolved ? `<span class="pill">${r.resolved === "credit" ? "в перерасчёте" : "отработан"}</span>` : "";
  return `<div class="list-row"><div class="grow click" data-who="${r.id}" style="cursor:pointer">${esc(r.name)} ${locked}</div>
    <div class="mark">
      <button data-att="present" data-sid="${r.id}" class="${r.status === "present" ? "on-present" : ""}">Был</button>
      <button data-att="absent" data-sid="${r.id}" class="${r.status === "absent" ? "on-absent" : ""}">Пропуск</button>
    </div></div>`;
}

function makeupRow(r) {
  return `<div class="list-row"><div class="grow click" data-who="${r.id}" style="cursor:pointer">${esc(r.name)}
      <div class="small muted">${esc(r.home_group || "")} · за пропуск ${dShort(r.absence_date)}</div></div>
    <div class="mark">
      <button data-mk="${r.makeup_id}" data-st="done" class="${r.status === "done" ? "on-present" : ""}">Пришёл</button>
      <button data-mk="${r.makeup_id}" data-st="missed" class="${r.status === "missed" ? "on-absent" : ""}">Не пришёл</button>
    </div></div>`;
}

/* ---------- поиск ---------- */
async function renderSearch(page) {
  page.innerHTML = `
    <div class="page-head"><h2 class="grow">Все клиенты</h2>
      <input class="search" id="q" placeholder="Имя, фамилия или телефон" value="${esc(S.search)}"></div>
    <div id="results" class="list"></div>`;
  const load = async () => {
    const rows = await api("GET", "/api/students?q=" + encodeURIComponent(S.search));
    $("#results").innerHTML = rows.length ? rows.map((r) => `
      <div class="list-row click" data-who="${r.id}"><div class="grow"><b>${esc(r.name)}</b>
        <div class="small muted">${esc([r.parent, r.phone, r.group].filter(Boolean).join(" · "))}</div></div>
        <span class="pill ${r.funnel === "members" ? "green" : ""}">${r.funnel === "members" ? "ученик" : "лид"}</span></div>`).join("")
      : `<div class="list-row muted">Никого не нашли</div>`;
    $("#results").querySelectorAll("[data-who]").forEach((el) => (el.onclick = () => openStudent(+el.dataset.who)));
  };
  let t; $("#q").oninput = (e) => { S.search = e.target.value; clearTimeout(t); t = setTimeout(load, 250); };
  load();
}

/* ---------- настройки ---------- */
async function renderSettings(page) {
  const st = S.meta.settings || {}, admin = S.user.role === "admin";
  const users = admin ? await api("GET", "/api/users") : [];
  page.innerHTML = `
    <div class="page-head"><h2 class="grow">Настройки</h2><button class="btn sm" id="logout">Выйти</button></div>
    <div style="max-width:560px">
    ${admin ? `
    <form class="section" id="set">
      <h3>Цены и правила</h3>
      <div class="row">
        <div class="field"><label>Негибкий, ₽ за занятие</label><input name="price_fixed" type="number" value="${esc(st.price_fixed)}"></div>
        <div class="field"><label>Гибкий, ₽ за занятие</label><input name="price_flex" type="number" value="${esc(st.price_flex)}"></div>
      </div>
      <div class="row">
        <div class="field"><label>Продлить до числа</label><input name="renewal_deadline_day" type="number" min="1" max="28" value="${esc(st.renewal_deadline_day)}"></div>
        <div class="field"><label>Длительность пробного, мин</label><input name="trial_minutes" type="number" value="${esc(st.trial_minutes)}"></div>
      </div>
      <div class="row">
        <div class="field"><label>Наценка за позднюю оплату</label><select name="late_fee_enabled"><option value="0" ${st.late_fee_enabled === "0" ? "selected" : ""}>Выключена</option><option value="1" ${st.late_fee_enabled === "1" ? "selected" : ""}>Включена</option></select></div>
        <div class="field"><label>Наценка, %</label><input name="late_fee_percent" type="number" value="${esc(st.late_fee_percent)}"></div>
      </div>
      <button class="btn primary">Сохранить</button>
    </form>
    <div class="section">
      <h3>Сотрудники</h3>
      <div class="list" style="box-shadow:none;margin-bottom:10px">${users.map((u) => `<div class="list-row"><div class="grow">${esc(u.name)} <span class="muted small">${esc(u.login)}</span></div><span class="pill">${{ admin: "админ", operator: "оператор", coach: "тренер" }[u.role]}</span></div>`).join("")}</div>
      <form id="nu">
        <div class="row"><div class="field"><label>Имя</label><input name="name" required></div><div class="field"><label>Логин</label><input name="login" required></div></div>
        <div class="row"><div class="field"><label>Пароль (от 8 символов)</label><input name="password" type="password" minlength="8" required></div>
          <div class="field"><label>Роль</label><select name="role"><option value="operator">Оператор</option><option value="coach">Тренер</option><option value="admin">Админ</option></select></div></div>
        <button class="btn sm">Добавить сотрудника</button>
      </form>
    </div>
    <div class="section">
      <h3>Тренеры групп</h3>
      <div class="small muted" style="margin-bottom:8px">Тренер видит и отмечает в журнале только свои группы.</div>
      ${S.meta.groups.map((g) => `<div class="row" style="align-items:center;margin-bottom:8px"><div>${esc(g.name)}</div>
        <select data-gc="${g.id}" style="flex:0 0 190px"><option value="">— без тренера —</option>${users.filter((u) => u.role === "coach").map((u) => `<option value="${u.id}" ${u.id === g.coach_id ? "selected" : ""}>${esc(u.name)}</option>`).join("")}</select></div>`).join("")}
    </div>` : ""}
    <form class="section" id="pw">
      <h3>Мой пароль</h3>
      <div class="field"><label>Текущий пароль</label><input name="old" type="password" required></div>
      <div class="field"><label>Новый пароль</label><input name="new" type="password" minlength="8" required></div>
      <button class="btn sm">Сменить пароль</button>
    </form>
    <div class="small muted" style="margin-top:12px">Вы вошли как ${esc(S.user.name)}. Версия 0.1, тестовая.</div>
    </div>`;
  $("#logout").onclick = async () => { await api("POST", "/api/logout", {}).catch(() => {}); renderLogin(); };
  if ($("#set")) $("#set").onsubmit = async (e) => {
    e.preventDefault();
    try { S.meta.settings = await api("PUT", "/api/settings", Object.fromEntries(new FormData(e.target))); toast("Сохранено"); } catch (err) { fail(err); }
  };
  if ($("#nu")) $("#nu").onsubmit = async (e) => {
    e.preventDefault();
    try { await api("POST", "/api/users", Object.fromEntries(new FormData(e.target))); toast("Сотрудник добавлен"); render(); } catch (err) { fail(err); }
  };
  page.querySelectorAll("[data-gc]").forEach((sel) => (sel.onchange = async () => {
    try {
      await api("PATCH", `/api/groups/${sel.dataset.gc}`, { coach_id: sel.value ? +sel.value : null });
      S.meta.groups.find((g) => g.id === +sel.dataset.gc).coach_id = sel.value ? +sel.value : null;
      toast("Тренер назначен");
    } catch (err) { fail(err); }
  }));
  $("#pw").onsubmit = async (e) => {
    e.preventDefault();
    try { await api("POST", "/api/me/password", Object.fromEntries(new FormData(e.target))); toast("Пароль изменён"); e.target.reset(); } catch (err) { fail(err); }
  };
}

/* ---------- старт ---------- */
async function boot() {
  try {
    S.meta = await api("GET", "/api/meta");
    S.user = S.meta.user;
  } catch (e) { if (e.message !== "Нужно войти") renderLogin(); return; }
  if (S.user.role === "admin") S.view = "dashboard";
  try { const v = localStorage.getItem("crm.view"); if (v && visibleViews().some(([k]) => k === v)) S.view = v; } catch (_) {}
  shell();
  render();
  setInterval(() => { if ($("#drawer").hidden && ["sales", "members", "skips"].includes(S.view)) render(); }, 60000);
}
boot();
