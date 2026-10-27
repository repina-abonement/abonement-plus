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
const PAY_METHODS = { cash: "Наличные", card: "Картой", sbp: "СБП", online: "Онлайн-оплата", transfer: "Перевод", unknown: "Перенесено" };

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
    try { await api("POST", "/api/login", { login: f.get("login"), password: f.get("password") }); boot(); }
    catch (err) { $("#login-err").textContent = err.message; }
  };
}

/* ---------- каркас ---------- */
const VIEWS = [
  ["today", "Сегодня"], ["sales", "Рабочий стол"], ["inbox", "Чаты и звонки"], ["planner", "Планер"], ["members", "Ученики"], ["skips", "Пропуски"],
  ["journal", "Журнал"], ["schedule", "Расписание"], ["coaches", "Тренеры"], ["payments", "Оплаты"], ["merch", "Мерч"], ["spend", "Расходы"], ["dashboard", "Дашборд"], ["search", "Все клиенты"], ["settings", "Настройки"],
];
const NAV_ICONS = {
  sales: '<path d="M4 5h16v14H4z"/><path d="M9 5v14M15 5v14"/>',
  today: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  schedule: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4M8 14h3M13 14h3M8 17h3"/>',
  inbox: '<path d="M4 6h16v10H8l-4 3z"/><path d="M8 10h8M8 13h5"/>',
  members: '<circle cx="9" cy="8" r="3"/><path d="M3.5 19c.8-3 3-4.5 5.5-4.5s4.7 1.5 5.5 4.5"/><circle cx="17" cy="9" r="2.3"/><path d="M16 14.6c2.2.2 3.7 1.6 4.4 4.4"/>',
  skips: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4M10 14l4 4M14 14l-4 4"/>',
  journal: '<rect x="5" y="3.5" width="14" height="17" rx="2"/><path d="M9 9l1.5 1.5L13 8M9 14.5l1.5 1.5L13 13.5M15 9.5h1M15 15h1"/>',
  coaches: '<path d="M8 4h8v4a4 4 0 0 1-8 0z"/><path d="M8 5H5v1.5A3 3 0 0 0 8 9.5M16 5h3v1.5a3 3 0 0 1-3 3M12 12v4M9 20h6M10 16h4v4h-4z"/>',
  dashboard: '<path d="M4 19V10M10 19V5M16 19v-7M21 19H3"/>',
  planner: '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M8 9l1.5 1.5L12 8M8 15l1.5 1.5L12 14M14 9.5h3M14 15.5h3"/>',
  spend: '<rect x="3" y="6" width="18" height="13" rx="2"/><path d="M3 10h18M16 15h2M7 3h10"/>',
  merch: '<path d="M8 4l-4 3 2 3 2-1v11h8V9l2 1 2-3-4-3c-.5 1.5-2 2.5-4 2.5S8.5 5.5 8 4z"/>',
  payments: '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M3 10h18M7 15h3"/>',
  search: '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8"/>',
  more: '<circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/>',
};
const navIcon = (k) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${NAV_ICONS[k] || ""}</svg>`;

const isCoach = () => S.user && S.user.role === "coach";
const COACH_VIEWS = ["today", "journal", "planner", "coaches", "settings"];
const visibleViews = () => isCoach() ? VIEWS.filter(([k]) => COACH_VIEWS.includes(k)).map(([k, t]) => [k, k === "coaches" ? "Мои результаты" : t])
  : VIEWS.filter(([k]) => k !== "today" && (S.user.role === "admin" || !["dashboard", "payments", "spend"].includes(k)));
const SHORT = { today: "Сегодня", sales: "Стол", inbox: "Чаты", coaches: isCoachShort(), journal: "Журнал" };
function isCoachShort() { return "Результаты"; }
const badge = (k) => k === "inbox" ? `<span class="nbadge" data-badge hidden></span>` : "";

function shell() {
  if (isCoach() && !COACH_VIEWS.includes(S.view)) S.view = "today";
  const views = visibleViews();
  const tabs = views.length <= 5 ? views : [...views.slice(0, 4), ["more", "Ещё"]];
  app.innerHTML = `
  <header class="topbar">
    <div class="brand"><i aria-hidden="true">⚽</i><div>Абонемент <span>Плюс</span></div></div>
    <nav class="nav">${views.map(([k, t]) => `<button data-view="${k}" class="${S.view === k ? "active" : ""}">${navIcon(k)}<span>${t}</span>${badge(k)}</button>`).join("")}</nav>
    ${isCoach() ? "" : `<button class="btn" id="new-client">+ Клиент</button>`}
  </header>
  <main class="page" id="page"></main>
  <nav class="tabbar">${tabs.map(([k, t]) => `<button data-${k === "more" ? "more" : "view"}="${k}" class="${S.view === k ? "active" : ""}">${navIcon(k)}<span>${SHORT[k] || t}</span>${badge(k)}</button>`).join("")}</nav>
  <div class="more-sheet" hidden><div class="sheet">${views.slice(4).map(([k, t]) => `<button data-view="${k}" class="${S.view === k ? "active" : ""}">${navIcon(k)}${t}</button>`).join("")}</div></div>`;
  app.querySelectorAll("[data-view]").forEach((b) => (b.onclick = () => { $(".more-sheet").hidden = true; go(b.dataset.view); }));
  const more = $("[data-more]");
  if (more) more.onclick = () => { $(".more-sheet").hidden = !$(".more-sheet").hidden; };
  $(".more-sheet").onclick = (e) => { if (e.target.classList.contains("more-sheet")) e.currentTarget.hidden = true; };
  if ($("#new-client")) $("#new-client").onclick = openNewClient;
}

/* ---------- навигация «назад»: каждый переход пишется в историю браузера ---------- */
const navSnap = () => JSON.parse(JSON.stringify({ journal: S.journal, sched: S.sched, planner: S.planner, spend: S.spend,
  inbox: S.inbox && { tab: S.inbox.tab, filter: S.inbox.filter } }));
function navRestore(sub) {
  if (!sub) return;
  if (sub.journal) S.journal = sub.journal;
  if (sub.sched) S.sched = sub.sched;
  if (sub.planner) S.planner = sub.planner;
  if (sub.spend) S.spend = sub.spend;
  if (sub.inbox && S.inbox) Object.assign(S.inbox, sub.inbox);
}
const navDepth = () => (history.state && history.state.depth) || 0;
function pushNav() {
  try { history.pushState({ view: S.view, sub: navSnap(), depth: navDepth() + 1 }, "", "#" + S.view); } catch (_) {}
}
function step(fn) { fn(); pushNav(); window.scrollTo(0, 0); render(); }
let drawerInHistory = false, skipPop = false;
window.addEventListener("popstate", (e) => {
  if (skipPop) { skipPop = false; return; }
  if (drawerInHistory) { drawerInHistory = false; hideDrawer(); return; }
  const st = e.state;
  if (!st || !st.view) return;
  navRestore(st.sub);
  go(st.view, true);
});

function go(view, fromHistory = false) {
  S.view = view;
  if (!fromHistory) pushNav();
  try { localStorage.setItem("crm.view", view); } catch (_) {}
  app.querySelectorAll("[data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  const more = $("[data-more]");
  if (more) more.classList.toggle("active", !visibleViews().slice(0, 4).some(([k]) => k === view));
  window.scrollTo(0, 0);
  render();
}

async function render() {
  const page = $("#page");
  if (!page) return;
  await renderView(page);
  if (navDepth() > 0 && !page.querySelector(".back-btn")) {
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
    if (S.view === "spend") return await renderSpend(page);
    if (S.view === "schedule") return await renderSchedule(page);
    if (["members", "skips"].includes(S.view)) return await renderBoard(page, S.view);
    if (S.view === "dashboard") return await renderDashboard(page);
    if (S.view === "inbox") return await renderInbox(page);
    if (S.view === "coaches") return await renderCoaches(page);
    if (S.view === "journal") return await renderJournal(page);
    if (S.view === "search") return await renderSearch(page);
    if (S.view === "settings") return await renderSettings(page);
  } catch (e) { if (e.message !== "Нужно войти") page.innerHTML = `<div class="error">${esc(e.message)}</div>`; }
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
      ${S.dashTab === "sources" ? "" : `<select id="dash-branch"><option value="">Все филиалы</option>${S.meta.branches.map((b) => `<option value="${b.id}" ${String(b.id) === String(S.dashBranch) ? "selected" : ""}>${esc(b.name)}</option>`).join("")}</select>`}
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
const BOARD_TITLES = { sales: "Воронка продаж", members: "Ученики и продления", skips: "Пропуски и отработки" };

function boardHtml(data) {
  return `<div class="board">${data.columns.map((c) => `
      <section class="col" data-stage="${c.key}">
        <div class="col-head"><h3>${esc(c.title)}</h3>${c.auto ? `<span class="auto">сам</span>` : ""}<span class="count">${c.cards.length}</span></div>
        <div class="col-body">${c.cards.length ? c.cards.map((k) => cardHtml(k, data.draggable && !c.auto)).join("") : `<div class="empty">Пусто</div>`}</div>
      </section>`).join("")}
    </div>`;
}

function wireBoard(page, funnel, data) {
  page.querySelectorAll(".card").forEach((el) => (el.onclick = () => openStudent(+el.dataset.id)));
  if (data.draggable) enableDrag(page, funnel, data.columns);
}

async function renderBoard(page, funnel) {
  const data = await api("GET", "/api/board/" + funnel);
  page.innerHTML = `
    <div class="page-head"><h2 class="grow">${BOARD_TITLES[funnel]}</h2>
      <button class="btn sm" id="refresh">Обновить</button></div>
    ${data.note ? `<div class="note">${esc(data.note)}</div>` : ""}
    ${boardHtml(data)}`;
  $("#refresh").onclick = render;
  wireBoard(page, funnel, data);
}

/* ---------- рабочий стол: что горит + воронка ---------- */
const URG_ACTION = { missed_call: "Перезвонить", site_lead: "Открыть", message: "Ответить", trial: "Открыть" };
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
  const [d, tasksHtml, schedHtml] = await Promise.all([api("GET", "/api/workdesk" + (S.viewAs ? "?as_user=" + S.viewAs.id : "")), tasksSection(), scheduleWidget().catch(() => "")]);
  const wasOpen = page.querySelector(".sched-widget") ? page.querySelector(".sched-widget").open : null;
  const u = d.urgent, reds = u.filter((x) => x.level === "red").length;
  const scroll = page.querySelector(".board") ? page.querySelector(".board").scrollLeft : 0;
  page.innerHTML = `
    ${bar}
    <div class="page-head"><h2 class="grow">Рабочий стол${S.viewAs ? `: ${esc(S.viewAs.name)}` : ""}</h2>
      <span class="muted small">Обновляется сам каждые 20 секунд</span></div>
    ${callFocusBox(d.call_focus)}
    ${spendDeskBox(d)}
    ${checkinReport(d.checkins)}
    ${merchAlerts(d.merch_alerts)}
    ${needsSubsBox(d)}
    ${schedHtml}
    ${birthdaysBox(d.birthdays)}
    ${tasksHtml}
    <section class="now ${u.length ? "" : "calm"}" style="margin-top:14px">
      <div class="now-head"><h3>${u.length ? (reds ? `Горит прямо сейчас: ${reds}` : "Ждут ответа") : "Всё обработано"}</h3>
        ${u.length ? `<span class="muted small">Красное сначала, потом сообщения и пробные</span>` : ""}</div>
      ${u.length ? `<div class="now-list">${u.map((x, i) => `
        <div class="now-item ${x.level}">
          <span class="ref-card ${x.level}" aria-hidden="true"></span>
          <div class="grow"><b>${esc(x.title)}</b>${x.count ? ` <span class="nbadge static">${x.count}</span>` : ""}
            <div class="small ellipsis">${esc(x.text)}</div></div>
          <span class="small muted when">${ago(x.ts)}</span>
          <div class="now-act">
            <button class="btn sm ${x.level === "red" ? "primary" : ""}" data-u="${i}">${URG_ACTION[x.kind]}</button>
            ${x.kind === "missed_call" ? `<button class="btn sm" data-useen="${x.id}" title="Уже связались другим способом">Обработан</button>` : ""}
          </div>
        </div>`).join("")}</div>` : `<div class="muted small">Пропущенных звонков, новых заявок и непрочитанных сообщений нет. Можно выдохнуть ⚽</div>`}
    </section>
    <div class="page-head" style="margin-top:22px"><h2 class="grow sub">Воронка продаж</h2></div>
    ${boardHtml(d.board)}`;
  if (page.querySelector(".board")) page.querySelector(".board").scrollLeft = scroll;
  wireBoard(page, "sales", d.board);
  wireTasks(page);
  wireViewAs(page);
  wireNeedsSubs(page, d);
  wireCheckinReport(page);
  wireSpend(page);
  wireMerchAlerts(page);
  if (page.querySelector(".sched-widget")) { if (wasOpen === false) page.querySelector(".sched-widget").open = false; wireSchedule(page.querySelector(".sched-widget"), S.meta.today); }
  page.querySelectorAll(".bday-box [data-open]").forEach((b) => (b.onclick = () => openStudent(+b.dataset.open)));
  page.querySelectorAll("[data-u]").forEach((b) => (b.onclick = async () => {
    const x = u[+b.dataset.u];
    if (x.kind === "missed_call") { await dial(x.phone); if (x.student_id) openStudent(x.student_id); else openNewClient({ phone: x.phone }); }
    else if (x.kind === "message") openChat(x.conversation_id, true);
    else if (x.student_id) openStudent(x.student_id);
  }));
  page.querySelectorAll("[data-useen]").forEach((b) => (b.onclick = async () => {
    await api("POST", `/api/calls/${b.dataset.useen}/seen`, {}).catch(fail); pollCounts(); render();
  }));
}

function cardHtml(k, draggable) {
  const sub = [k.parent, k.phone].filter(Boolean).join(", ");
  const meta2 = [k.group, k.tariff === "flex" ? "гибкий" : (k.group ? "негибкий" : ""), k.siblings ? `братьев и сестёр: ${k.siblings}` : ""].filter(Boolean).join(" · ");
  return `<article class="card ${k.color || ""}" data-id="${k.id}" ${draggable ? 'draggable="true"' : ""}>
    ${k.color ? `<span class="ref-card ${k.color}" title="${k.color === "red" ? "Срочно" : "Пора связаться"}"></span>` : ""}
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
function openDrawer(html) {
  $("#drawer-body").innerHTML = html; $("#drawer").hidden = false; document.body.style.overflow = "hidden";
  if (!drawerInHistory) { try { history.pushState({ ...(history.state || {}), drawer: true }, ""); drawerInHistory = true; } catch (_) {} }
}
function hideDrawer() { const d = $("#drawer"); if (d) d.hidden = true; document.body.style.overflow = ""; }
/* закрыть карточку: крестиком, Esc, после сохранения или кнопкой/свайпом «назад» на телефоне */
function closeDrawer() {
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
  ${s.priority ? `<div class="alert">Заявка с сайта, обработать в первую очередь. Карточка перестанет быть красной, когда вы смените этап или добавите комментарий с итогом звонка</div>` : ""}

  <div class="section">
    <div class="row">
      <div class="field"><label>Этап</label><select id="f-stage">${stageOpts.map(([k, t]) => `<option value="${k}" ${k === s.stage ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></div>
      ${!isMember ? `<div class="field"><label>Тариф</label><select id="f-tariff"><option value="fixed" ${s.tariff === "fixed" ? "selected" : ""}>Негибкий</option><option value="flex" ${s.tariff === "flex" ? "selected" : ""}>Гибкий</option></select></div>` : ""}
    </div>
    ${!isMember ? `<div class="field"><label>Группа для пробного</label><select id="f-group"><option value="">— не выбрана —</option>${groups.map((x) => `<option value="${x.id}" ${x.id === s.group_id ? "selected" : ""}>${esc(x.name)} · ${esc(x.age)} лет</option>`).join("")}</select></div>
    <div class="field"><label>Пробное занятие</label><input id="f-trial" type="datetime-local" value="${esc(s.trial_at || "")}"></div>` : ""}
    <div class="field"><label>Откуда узнали</label>${sourceSelect("f-source", s.source_id, s.source)}</div>
  </div>

  <div class="section">
    <h3>Семья ${esc(d.family.name)}</h3>
    ${consentHtml(d.family)}
    <dl class="kv">${d.parents.map((p) => `<dt>${esc(p.name)}</dt><dd><a href="tel:${esc(p.phone)}">${esc(p.phone)}</a>${p.messenger ? ` <span class="pill">${esc(p.messenger)}</span>` : ""}</dd>`).join("")}</dl>
    ${d.siblings.length ? `<div style="margin-top:10px" class="chips">${d.siblings.map((x) => `<button class="chip" data-open="${x.id}">${esc(x.name)}</button>`).join("")}</div>` : ""}
    <button class="btn sm" style="margin-top:10px" id="add-sibling">+ Брат или сестра</button>
  </div>

  <div class="section" id="comms-box"><h3>Связь с семьёй</h3><div class="muted small">Загружаю переписку…</div></div>

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
    <h3>${isMember ? "Группы и абонементы" : "Абонемент"}</h3>
    ${isMember ? `
      <div class="row"><div class="field"><label>Месяц абонемента</label><select id="q-month">${[d.cur_month, d.next_month].map((m) => `<option value="${m}" ${m === d.next_month ? "selected" : ""}>${monthLabel(m)}</option>`).join("")}</select></div>
        <div class="field"><label>Скидка</label><select id="q-disc"><option value="">Без скидки (или по правилу)</option>${(d.discounts || []).map((x) => `<option value="${x.id}">${esc(x.name)}, ${x.percent}%</option>`).join("")}</select></div></div>
      ${d.enrollments.length ? d.enrollments.map((e) => `
        <div class="enr">
          <div class="enr-head"><b>${esc(e.group_name)}</b><span class="muted small">${esc(e.group_age)} лет</span>
            <button class="icon-btn" data-enoff="${e.id}" title="Больше не ходит в эту группу">✕</button></div>
          <div class="chips" style="margin:6px 0 8px">${e.group_weekdays.split(",").map((wd) => { const on = !e.weekdays || e.weekdays.split(",").includes(wd); return `<button class="chip day ${on ? "on" : ""}" data-enday="${e.id}" data-wd="${wd}">${WD[+wd]}</button>`; }).join("")}
            <select data-entar="${e.id}" style="width:auto;padding:4px 8px"><option value="fixed" ${e.tariff === "fixed" ? "selected" : ""}>Негибкий</option><option value="flex" ${e.tariff === "flex" ? "selected" : ""}>Гибкий</option></select></div>
          <div class="quote muted" data-quote="${e.id}">Считаю…</div>
          <button class="btn primary sm" data-inv="${e.id}">Выставить счёт</button>
        </div>`).join("") : `<div class="muted small">Ученик пока не ходит ни в одну группу.</div>`}
      <div class="row" style="margin-top:12px">
        <div class="field"><label>Добавить группу</label><select id="en-group"><option value="">— выберите —</option>${groups.filter((x) => !d.enrollments.some((e) => e.group_id === x.id)).map((x) => `<option value="${x.id}">${esc(x.name)} · ${esc(x.age)} лет</option>`).join("")}</select></div>
        <div class="field" style="flex:0 0 130px"><label>Тариф</label><select id="en-tariff"><option value="fixed">Негибкий</option><option value="flex">Гибкий</option></select></div>
      </div>
      <button class="btn sm" id="en-add">+ Добавить группу</button>
      <div class="small muted" style="margin-top:6px">Дни можно снять, если ребёнок ходит в группу не во все её дни: цена посчитается только за выбранные.</div>`
    : `<div class="muted small">Абонемент выставляется, когда клиент переходит в этап «Купил абонемент».</div>`}
    ${d.subscriptions.length ? `<ul class="history" style="margin-top:10px">${d.subscriptions.map((x) => { const cov = (d.balance.covered || {})[x.id] || 0; return `<li><b>${x.kind === "single" ? "Разовое занятие" : monthLabel(x.month)}</b>${x.group_name ? " · " + esc(x.group_name) : ""} · ${rub(x.price)}${x.kind === "single" ? "" : ` · ${x.lessons} зан.`}${x.credit_lessons ? ` · перерасчёт ${x.credit_lessons}` : ""}${x.late_fee ? ` · наценка ${rub(x.late_fee)}` : ""}${x.discount_amount ? ` · скидка ${rub(x.discount_amount)}` : ""}
      <span class="pill ${x.status === "paid" ? "green" : x.status === "cancelled" ? "" : "yellow"}">${x.status === "invoiced" && cov ? `оплачено ${rub(cov)} из ${rub(x.price)}` : { paid: "оплачен", invoiced: "ждёт оплаты", cancelled: "отменён" }[x.status]}</span>
      ${x.status === "invoiced" ? `<button class="btn sm primary" data-pay="${x.id}" data-left="${x.price - cov}">Принять оплату</button> <button class="btn sm danger" data-cancel="${x.id}">Отменить</button>` : ""}</li>`; }).join("")}</ul>` : ""}
  </div>

  <div class="section">
    <h3>Баланс и оплаты</h3>
    <div class="chips">
      <span class="pill ${d.balance.debt ? "red" : "green"}">${d.balance.debt ? `Долг ${rub(d.balance.debt)}` : "Долга нет"}</span>
      ${d.balance.upcoming ? `<span class="pill yellow">К оплате за следующий месяц ${rub(d.balance.upcoming)}</span>` : ""}
      ${d.balance.deposit ? `<span class="pill green">Депозит ${rub(d.balance.deposit)}</span>` : ""}
    </div>
    <div class="row" style="margin-top:10px"><button class="btn sm" id="pay-free" style="flex:0">+ Принять оплату</button><button class="btn sm" id="sell-single" style="flex:0">Разовое занятие</button></div>
    <div id="pay-form"></div>
    ${d.payments.length ? `<ul class="history pay-list ${d.payments.length > 4 ? "folded" : ""}" style="margin-top:10px">${d.payments.map((p) => `<li class="${p.cancelled ? "struck" : ""}"><b>${rub(p.amount)}</b> · ${esc(PAY_METHODS[p.method] || p.method)} · ${dtShort(p.paid_at)}${p.comment ? " · " + esc(p.comment) : ""}
      <div class="when">${esc(p.user_name)}${p.cancelled ? ` · отменён (${esc(p.cancelled_by)})` : p.confirmed ? "" : " · ждёт подтверждения"}</div>
      ${S.user.role === "admin" && !p.cancelled && p.method !== "unknown" ? `${p.confirmed ? "" : `<button class="btn sm" data-pconf="${p.id}">Подтвердить</button> `}<button class="btn sm danger" data-pcancel="${p.id}">Отменить платёж</button>` : ""}</li>`).join("")}</ul>
      ${d.payments.length > 4 ? `<button class="linkish small" id="pay-more">Показать все платежи (${d.payments.length})</button>` : ""}` : ""}
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
  </div>
  <div style="margin-top:14px"><button class="btn sm ${s.archived ? "" : "danger"}" id="s-arch">${s.archived ? "Вернуть из архива" : "Перенести в архив"}</button></div>`;
  openDrawer(html);
  const body = $("#drawer-body");
  const reload = () => { openStudent(id); render(); };
  const patch = async (data) => { try { await api("PATCH", `/api/students/${id}`, data); reload(); } catch (e) { fail(e); } };

  $("#f-stage").onchange = async (e) => {
    try { await api("POST", `/api/students/${id}/move`, { funnel: s.funnel, stage: e.target.value }); reload(); } catch (err) { fail(err); }
  };
  if ($("#f-tariff")) $("#f-tariff").onchange = (e) => patch({ tariff: e.target.value });
  if ($("#f-group")) $("#f-group").onchange = (e) => patch({ group_id: e.target.value ? +e.target.value : "" });
  const enPatch = async (eid, data) => { try { await api("PATCH", `/api/enrollments/${eid}`, data); reload(); } catch (e) { fail(e); } };
  body.querySelectorAll("[data-enday]").forEach((b) => (b.onclick = () => {
    const eid = +b.dataset.enday;
    const days = [...body.querySelectorAll(`[data-enday="${eid}"]`)].filter((x) => (x === b ? !x.classList.contains("on") : x.classList.contains("on"))).map((x) => +x.dataset.wd);
    if (!days.length) return toast("Оставьте хотя бы один день", true);
    enPatch(eid, { weekdays: days });
  }));
  body.querySelectorAll("[data-entar]").forEach((sel) => (sel.onchange = () => enPatch(+sel.dataset.entar, { tariff: sel.value })));
  body.querySelectorAll("[data-enoff]").forEach((b) => (b.onclick = () => { if (confirm("Ученик больше не ходит в эту группу?")) enPatch(+b.dataset.enoff, { active: false }); }));
  if ($("#en-add")) $("#en-add").onclick = async () => {
    if (!$("#en-group").value) return toast("Выберите группу", true);
    try { await api("POST", `/api/students/${id}/enrollments`, { group_id: +$("#en-group").value, tariff: $("#en-tariff").value }); toast("Группа добавлена"); reload(); } catch (e) { fail(e); }
  };
  if ($("#f-trial")) $("#f-trial").onchange = (e) => patch({ trial_at: e.target.value });
  $("#f-source").onchange = (e) => patch({ source_id: e.target.value ? +e.target.value : "" });
  body.querySelectorAll("[data-open]").forEach((b) => (b.onclick = () => openStudent(+b.dataset.open)));
  $("#add-sibling").onclick = () => openNewClient({ family_id: s.family_id, family_name: d.family.name });
  loadFamilyComms(s.family_id, d.parents, id);
  wireConsent(d.family, reload);

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
      box.innerHTML = `<div class="total">${rub(q.total)}</div>
        <div class="small">${q.lessons_paid} занятий × ${rub(q.per_lesson)} = ${rub(q.base)}${q.days_label ? ` · только ${esc(q.days_label)}` : ""}${q.vacation_lessons ? ` · отпуск: −${q.vacation_lessons} зан.` : ""}</div>
        ${q.credit_lessons ? `<div class="small">Перерасчёт пропусков (${q.credit_absence_dates.map(dShort).join(", ")}): −${rub(q.credit)}</div>` : ""}
        ${q.late_fee ? `<div class="small">Наценка за оплату после ${S.meta.settings.renewal_deadline_day} числа: +${rub(q.late_fee)}</div>` : ""}
        ${q.discount ? `<div class="small">Скидка «${esc(q.discount.name)}» ${q.discount.percent}%: −${rub(q.discount.amount)}${q.discount.auto ? ". Действует при оплате сегодня" : ""}</div>` : ""}
        <div class="small muted">Даты: ${q.dates.map(dShort).join(", ")}</div>`;
    } catch (e) { box.textContent = e.message; }
  });
  if ($("#q-month")) { $("#q-month").onchange = loadQuotes; $("#q-disc").onchange = loadQuotes; loadQuotes(); }
  body.querySelectorAll("[data-inv]").forEach((b) => (b.onclick = async () => {
    try { const r = await api("POST", `/api/students/${id}/invoice`, { month: $("#q-month").value, enrollment_id: +b.dataset.inv, discount_id: $("#q-disc").value || null }); toast(`Счёт выставлен: ${rub(r.quote.total)}`); reload(); } catch (e) { fail(e); }
  }));
  const payForm = (title, amount, onSubmit) => {
    $("#pay-form").innerHTML = `<div class="quote" style="margin-top:10px"><b>${esc(title)}</b>
      <div class="row" style="margin-top:8px"><div class="field"><label>Сумма, ₽</label><input id="pf-sum" type="number" min="1" value="${amount || ""}"></div>
        <div class="field"><label>Способ</label><select id="pf-m">${Object.entries(PAY_METHODS).filter(([k]) => k !== "unknown").map(([k, t]) => `<option value="${k}">${t}</option>`).join("")}</select></div></div>
      <div class="field"><label>Комментарий</label><input id="pf-c" placeholder="Необязательно"></div>
      <button class="btn primary sm" id="pf-ok">Принять ${amount ? rub(amount) : "оплату"}</button></div>`;
    $("#pf-sum").oninput = () => { $("#pf-ok").textContent = "Принять " + rub($("#pf-sum").value); };
    $("#pf-ok").onclick = async () => { try { await onSubmit(+$("#pf-sum").value, $("#pf-m").value, $("#pf-c").value); toast("Оплата принята"); reload(); } catch (e) { fail(e); } };
    $("#pay-form").scrollIntoView({ block: "center", behavior: "smooth" });
  };
  body.querySelectorAll("[data-pay]").forEach((b) => (b.onclick = () => payForm("Оплата счёта", +b.dataset.left,
    (amount, method) => api("POST", `/api/subscriptions/${b.dataset.pay}/pay`, { amount, method }))));
  if ($("#pay-more")) $("#pay-more").onclick = (e) => { body.querySelector(".pay-list").classList.remove("folded"); e.target.remove(); };
  $("#pay-free").onclick = () => payForm("Оплата на депозит", d.balance.debt || 0,
    (amount, method, comment) => api("POST", `/api/students/${id}/payments`, { amount, method, comment }));
  $("#sell-single").onclick = async () => { try { await api("POST", `/api/students/${id}/single`, {}); toast("Разовое занятие выставлено"); reload(); } catch (e) { fail(e); } };
  body.querySelectorAll("[data-pconf]").forEach((b) => (b.onclick = async () => { try { await api("POST", `/api/payments/${b.dataset.pconf}/confirm`, {}); reload(); } catch (e) { fail(e); } }));
  body.querySelectorAll("[data-pcancel]").forEach((b) => (b.onclick = async () => { if (!confirm("Отменить платёж? Он останется в реестре как отменённый.")) return; try { await api("POST", `/api/payments/${b.dataset.pcancel}/cancel`, {}); reload(); } catch (e) { fail(e); } }));
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
async function renderJournal(page) {
  const groups = S.meta.groups;
  if (!groups.length) { page.innerHTML = `<h2>Журнал посещений</h2><p class="muted">За вами пока не закреплено ни одной группы. Попросите администратора.</p>`; return; }
  if (!S.journal.group || !groups.some((x) => x.id === S.journal.group)) S.journal.group = groups[0].id;
  if (!S.journal.date) S.journal.date = S.meta.today;
  const j = await api("GET", `/api/journal?group_id=${S.journal.group}&date=${S.journal.date}`);
  const g = j.group;
  const st = j.has_lesson && S.journal.date <= S.meta.today ? await api("GET", `/api/lessons/staff?group_id=${g.id}&date=${S.journal.date}`).catch(() => null) : null;
  page.innerHTML = `
    <div class="page-head"><h2 class="grow">Журнал посещений</h2></div>
    <div class="row" style="max-width:640px;margin-bottom:14px">
      <div class="field"><label>Группа</label><select id="j-group">${groups.map((x) => `<option value="${x.id}" ${x.id === S.journal.group ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select></div>
      <div class="field" style="flex:0 0 170px"><label>Дата</label><input id="j-date" type="date" value="${S.journal.date}"></div>
    </div>
    <div class="note">${esc(g.branch)} · ${g.weekdays.split(",").map((x) => WD[+x]).join(", ")} ${esc(g.time)} · ${esc(g.age)} лет${j.has_lesson ? "" : " · <b>в этот день у группы нет занятия</b>"}</div>
    ${st ? `<div class="panel" style="padding:12px 14px;margin-bottom:12px">${staffPicker(st, st.coaches, "j")}</div>` : ""}
    <div class="list">${j.rows.length ? j.rows.map((r) => g.is_makeup ? makeupRow(r) : journalRow(r)).join("") : `<div class="list-row muted">${g.is_makeup ? "На эту дату никто не записан на отработку" : "В группе пока нет учеников"}</div>`}</div>`;
  if (st) wireStaffPicker(page, "j", g.id, S.journal.date, render);
  $("#j-group").onchange = (e) => { step(() => { S.journal.group = +e.target.value; }); };
  $("#j-date").onchange = (e) => { step(() => { S.journal.date = e.target.value; }); };
  if (!isCoach()) page.querySelectorAll("[data-who]").forEach((el) => (el.onclick = () => openStudent(+el.dataset.who)));
  page.querySelectorAll("[data-att]").forEach((b) => (b.onclick = async () => {
    try { await api("POST", "/api/attendance", { student_id: +b.dataset.sid, group_id: S.journal.group, date: S.journal.date, status: b.dataset.att }); render(); } catch (e) { fail(e); }
  }));
  page.querySelectorAll("[data-mk]").forEach((b) => (b.onclick = async () => {
    try { await api("POST", `/api/makeups/${b.dataset.mk}/status`, { status: b.dataset.st }); render(); } catch (e) { fail(e); }
  }));
  page.querySelectorAll("[data-trial]").forEach((b) => (b.onclick = async () => {
    try { await api("POST", `/api/students/${b.dataset.sid}/trial`, { came: b.dataset.trial === "1" }); render(); } catch (e) { fail(e); }
  }));
}

function journalRow(r) {
  if (r.status === "vacation") return `<div class="list-row"><div class="grow click" data-who="${r.id}">${esc(r.name)}</div><span class="pill yellow">Отпуск</span></div>`;
  if (r.status === "trial") return `<div class="list-row"><div class="grow click" data-who="${r.id}">${esc(r.name)} <span class="pill green">пробное ${r.trial_at.slice(11, 16)}</span></div>
    <div class="mark"><button data-trial="1" data-sid="${r.id}" class="${r.trial_came === 1 ? "on-present" : ""}">Пришёл</button><button data-trial="0" data-sid="${r.id}" class="${r.trial_came === 0 ? "on-absent" : ""}">Не пришёл</button></div></div>`;
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
      <input class="search" id="q" placeholder="Имя, фамилия или телефон" value="${esc(S.search)}">
      <button class="btn sm ${S.searchArch ? "primary" : ""}" id="q-arch">Архив</button></div>
    <div id="results" class="list"></div>`;
  const load = async () => {
    const rows = await api("GET", "/api/students?q=" + encodeURIComponent(S.search) + (S.searchArch ? "&archived=1" : ""));
    $("#results").innerHTML = rows.length ? rows.map((r) => `
      <div class="list-row click" data-who="${r.id}"><div class="grow"><b>${esc(r.name)}</b>
        <div class="small muted">${esc([r.parent, r.phone, r.group].filter(Boolean).join(" · "))}</div></div>
        <span class="pill ${r.funnel === "members" ? "green" : ""}">${r.funnel === "members" ? "ученик" : "лид"}</span></div>`).join("")
      : `<div class="list-row muted">Никого не нашли</div>`;
    $("#results").querySelectorAll("[data-who]").forEach((el) => (el.onclick = () => openStudent(+el.dataset.who)));
  };
  let t; $("#q").oninput = (e) => { S.search = e.target.value; clearTimeout(t); t = setTimeout(load, 250); };
  $("#q-arch").onclick = () => { S.searchArch = !S.searchArch; render(); };
  load();
}

/* ---------- настройки ---------- */
async function renderSettings(page) {
  const st = S.meta.settings || {}, admin = S.user.role === "admin";
  const users = admin ? (await api("GET", "/api/users")).filter((u) => u.active) : [];
  page.innerHTML = `
    <div class="page-head"><h2 class="grow">Настройки</h2><button class="btn sm" id="logout">Выйти</button></div>
    <div style="max-width:780px">
    ${admin ? `
    <form class="section" id="set">
      <h3>Цены и правила</h3>
      <div class="row">
        <div class="field"><label>Негибкий, ₽ за занятие</label><input name="price_fixed" type="number" value="${esc(st.price_fixed)}"></div>
        <div class="field"><label>Гибкий, ₽ за занятие</label><input name="price_flex" type="number" value="${esc(st.price_flex)}"></div>
      </div>
      <div class="row"><div class="field"><label>Разовое занятие, ₽</label><input name="price_single" type="number" value="${esc(st.price_single || "")}" placeholder="Уточнить цену"></div><div></div></div>
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
      <div class="list" style="box-shadow:none;margin-bottom:10px">${users.map((u) => `<div class="list-row"><div class="grow">${esc(u.name)} <span class="muted small">${esc(u.login)}</span></div>
        ${u.role === "coach" ? "" : `<input data-sip="${u.id}" value="${esc(u.sip_number || "")}" placeholder="вн. номер" title="Внутренний номер Sipuni: на него придёт звонок из карточки" inputmode="numeric" style="flex:0 0 96px;padding:5px 8px">`}
        <span class="pill">${{ admin: "админ", operator: "оператор", coach: "тренер" }[u.role]}</span>
        ${u.id !== S.user.id ? `<button class="icon-btn" data-upw="${u.id}" title="Задать новый пароль">🔑</button><button class="icon-btn" data-uarch="${u.id}" title="В архив">🗄</button>` : ""}</div>`).join("")}</div>
      <div class="small muted" style="margin:-4px 0 10px">Внутренний номер Sipuni нужен, чтобы звонить клиенту прямо из карточки</div>
      <form id="nu">
        <div class="row"><div class="field"><label>Имя</label><input name="name" required></div><div class="field"><label>Логин</label><input name="login" required></div></div>
        <div class="row"><div class="field"><label>Пароль (от 8 символов)</label><div class="pw-wrap"><input name="password" type="password" minlength="8" required><button type="button" class="pw-eye" aria-label="Показать пароль">👁</button></div></div>
          <div class="field"><label>Роль</label><select name="role"><option value="operator">Оператор</option><option value="coach">Тренер</option><option value="admin">Админ</option></select></div></div>
        <button class="btn sm">Добавить сотрудника</button>
      </form>
    </div>
    <div class="section" id="structure-settings"><h3>Филиалы, группы и смены</h3><div class="muted small">Загружаю…</div></div>
    <div class="section" id="archive-settings"><h3>Архив</h3><div class="muted small">Загружаю…</div></div>
    </div>` : ""}
    <div class="section" id="offers-settings"><h3>Оферты</h3><div class="muted small">Загружаю…</div></div>
    ${admin ? `<div class="section" id="legal-settings"><h3>Юридические документы</h3><div class="muted small">Загружаю…</div></div>` : ""}
    ${admin ? `<div class="section" id="source-settings"><h3>Рекламные каналы и источники</h3><div class="muted small">Загружаю…</div></div>` : ""}
    ${admin ? `<div class="section" id="staff-settings"><h3>Тренеры: приход и оплата</h3><div class="muted small">Загружаю…</div></div>` : ""}
    ${admin ? `<div class="section" id="task-settings"><h3>Задачи и автосообщения по пробным</h3><div class="muted small">Загружаю…</div></div>` : ""}
    ${admin ? `<div class="section" id="comms-settings"><h3>Связь с клиентами</h3><div class="muted small">Загружаю…</div></div>` : ""}
    ${admin ? `<div class="section"><h3>Выдуманные клиенты для проверки</h3>
      <div class="small muted" style="margin-bottom:8px">Добавляют клиентов в настоящие группы: с абонементами, оплатами, посещениями, пропусками и лидами на разных этапах. Все помечены как выдуманные и удаляются одной кнопкой, настоящие клиенты не пострадают</div>
      <div class="row" style="align-items:center;flex-wrap:wrap"><input type="number" id="fake-n" value="150" min="1" max="500" style="flex:0 0 100px">
        <button class="btn sm primary" id="fake-add" style="flex:0">Добавить</button><button class="btn sm" id="fake-real" style="flex:0">Школа с историей за 1,5 года</button><button class="btn sm" id="fake-calls" style="flex:0">+ Звонки с разборами</button><button class="btn sm danger" id="fake-del" style="flex:0">Удалить всех выдуманных</button></div></div>` : ""}
    ${admin && S.meta.demo ? `<div class="section"><h3>Тестовые данные</h3>
      <div class="small muted" style="margin-bottom:8px">Сейчас включён тестовый режим. Кнопка сотрёт всех клиентов и создаст выдуманных заново. Сотрудники и настройки сохранятся.</div>
      <button class="btn sm danger" id="demo-reset">Пересоздать тестовые данные</button></div>` : ""}
    <form class="section" id="pw">
      <h3>Мой пароль</h3>
      <div class="field"><label>Текущий пароль</label><input name="old" type="password" required></div>
      <div class="field"><label>Новый пароль</label><input name="new" type="password" minlength="8" required></div>
      <button class="btn sm">Сменить пароль</button>
    </form>
    <div class="small muted" style="margin-top:12px">Вы вошли как ${esc(S.user.name)}. Версия 0.2, тестовая.</div>
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
  if ($("#comms-settings")) renderCommsSettings($("#comms-settings"), users).catch(fail);
  if ($("#task-settings")) renderTaskSettings($("#task-settings"), users).catch(fail);
  if ($("#staff-settings")) renderStaffSettings($("#staff-settings"), users).catch(fail);
  if ($("#source-settings")) renderSourceSettings($("#source-settings")).catch(fail);
  if ($("#legal-settings")) renderLegalSettings($("#legal-settings")).catch(fail);
  if ($("#offers-settings") && !isCoach()) renderOffers($("#offers-settings")).catch(fail);
  else if ($("#offers-settings")) $("#offers-settings").remove();
  if ($("#structure-settings")) renderStructure($("#structure-settings"), users).catch(fail);
  if ($("#archive-settings")) renderArchive($("#archive-settings")).catch(fail);
  page.querySelectorAll("[data-upw]").forEach((b) => (b.onclick = async () => {
    const pw = prompt("Новый пароль для сотрудника (от 8 символов). Передайте его лично");
    if (!pw) return;
    try { await api("PATCH", `/api/users/${b.dataset.upw}`, { password: pw }); toast("Пароль задан"); } catch (e) { fail(e); }
  }));
  page.querySelectorAll("[data-uarch]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Перенести сотрудника в архив? Он больше не сможет войти, вернуть можно в Архиве")) return;
    try { await api("PATCH", `/api/users/${b.dataset.uarch}`, { active: false }); toast("Сотрудник в архиве"); render(); } catch (e) { fail(e); }
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

/* ---------- старт ---------- */
async function boot() {
  try {
    S.meta = await api("GET", "/api/meta");
    S.user = S.meta.user;
  } catch (e) { if (e.message !== "Нужно войти") renderLogin(); return; }
  S.view = isCoach() ? "today" : "sales";
  try { const v = localStorage.getItem("crm.view"); if (v && visibleViews().some(([k]) => k === v)) S.view = v; } catch (_) {}
  shell();
  try { history.replaceState({ view: S.view, sub: navSnap(), depth: 0 }, "", "#" + S.view); } catch (_) {}
  render();
  setInterval(() => { if ($("#drawer").hidden && ["members", "skips"].includes(S.view)) render(); }, 60000);
  setInterval(() => { if ($("#drawer").hidden && S.view === "sales" && !document.hidden && !document.querySelector(".task-done:not([hidden])")) render(); }, 20000);
  if (!isCoach()) { pollCounts(); setInterval(pollCounts, 20000); }
}
boot();
