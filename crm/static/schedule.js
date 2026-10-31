"use strict";
/* Абонемент Плюс — расписание, конструктор филиалов и групп, смены, архив. */

S.sched = { mode: "week", date: null, branch: null };

/* ---------- неделя «как постер»: колонки по дням, у каждой пары дней свой цвет ---------- */
const PAT_COLORS = [["#efe4f6", "#9b59b6"], ["#e3e8fb", "#3f56b8"], ["#fbefc9", "#d99a0b"], ["#dcf1f0", "#2a9d9b"], ["#fde4e0", "#d1553f"], ["#e6eefc", "#2c6fd1"]];
const PAT_SPECIAL = { makeup: ["#e2f3e1", "#2e8b57"], ofp: ["#ece6fa", "#7a5bc7"] };

function patColor(pattern, order) {
  if (PAT_SPECIAL[pattern]) return PAT_SPECIAL[pattern];
  let i = order.indexOf(pattern);
  if (i < 0) { order.push(pattern); i = order.length - 1; }
  return PAT_COLORS[i % PAT_COLORS.length];
}


function weekPoster(w, branchId) {
  const order = [];
  const cols = w.days.map((d, i) => {
    const b = d.branches.find((x) => x.id === branchId);
    const lessons = b ? b.lessons : [];
    const main = lessons.filter((l) => !l.is_makeup && l.pattern !== "ofp");
    const color = lessons.length ? patColor((main[0] || lessons[0]).pattern || "", order) : ["#f2f2f5", "#b4b4bc"];
    const team = [...new Set(main.flatMap((l) => [l.coach, l.assistant]).filter(Boolean))];
    return { d, i, lessons, color, team };
  });
  // цвета раздаём в порядке появления пар дней, чтобы Пн и Чт (одна пара) были одного цвета
  const col = (c) => `<div class="wk-col ${c.d.date === S.meta.today ? "today" : ""}" style="--hd:${c.color[1]};--bg:${c.color[0]}">
      <button class="wk-head" data-wday="${c.d.date}">
        <span class="wk-dn">${WD[c.i]}</span><span class="wk-date">${dShort(c.d.date)}</span>
        <span class="wk-team">${c.team.length ? c.team.map((n) => esc(shortName(n))).join(" / ") : "&nbsp;"}</span></button>
      <div class="wk-body">${c.lessons.length ? c.lessons.map((l) => {
        const [bg, fg] = patColor(l.pattern, order);
        const need = Object.values(l.subs || {}).some((x) => x.missing);
        const pct = l.capacity && !l.is_makeup ? Math.min(100, Math.round(100 * l.kids / l.capacity)) : null;
        return `<button class="wk-cell ${need ? "need" : ""}" data-wday="${c.d.date}" style="--cb:${bg};--cf:${fg}">
          <b>${esc(l.time || "—")}</b>
          <span class="wk-age">${l.is_makeup ? "отработки " : l.pattern === "ofp" ? "ОФП " : ""}${esc(l.age ? (/\d$/.test(l.age) ? l.age + " лет" : l.age) : "")}</span>
          <span class="wk-fill">${l.is_makeup ? `${l.kids} зап.` : `${l.kids}/${l.capacity}`}${l.trials ? ` · 🎯${l.trials}` : ""}</span>
          ${pct != null ? `<i class="wk-bar"><i style="width:${pct}%"></i></i>` : ""}
          ${need ? `<span class="wk-warn">⚠️ нужна замена</span>` : ""}</button>`;
      }).join("") : `<div class="wk-empty">${(w.days[c.i].closed || []).length ? "праздник" : "нет занятий"}</div>`}</div></div>`;
  cols.forEach((c) => c.lessons.forEach((l) => patColor(l.pattern, order)));
  const weekdays = cols.slice(0, 5), weekend = cols.slice(5);
  return `<div class="wk-poster">
    <section class="wk-block"><h3>Будни</h3><div class="wk-grid wk5">${weekdays.map(col).join("")}</div></section>
    <section class="wk-block"><h3>Выходные</h3><div class="wk-grid wk2">${weekend.map(col).join("")}</div></section>
  </div>
  <div class="small muted" style="margin-top:8px">Цвет - пара дней одной группы. В ячейке: время, возраст, сколько детей из мест, 🎯 пробные. Нажмите на день, чтобы открыть его подробно: замены, кто на смене, журнал</div>`;
}
const WD_FULL = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"];
const BR_COLORS = ["#1d8a57", "#2f6fdb", "#c2410c", "#7c3aed", "#0891b2", "#b45309"];

function addDays(iso, n) { const d = new Date(iso + "T00:00"); d.setDate(d.getDate() + n); return isoDate(d); }

function dayHtml(d, compact = false) {
  if (!d.branches.length) return `<div class="muted small">В этот день тренировок нет</div>`;
  return `<div class="sched-day ${compact ? "compact" : ""}">${d.branches.map((b, i) => `
    <section class="sched-br" style="--br:${BR_COLORS[i % BR_COLORS.length]}">
      <div class="sched-br-head"><b>${esc(b.name)}</b>${b.address && !compact ? `<span class="small muted">${esc(b.address)}</span>` : ""}</div>
      <div class="sched-staff">${b.staff.length ? `На смене: ${b.staff.map((x) => `<b>${esc(x.name)}</b>${x.time_from ? ` ${esc(x.time_from)}–${esc(x.time_to)}` : ""}`).join(", ")}` : `<span class="muted">Смена не назначена</span>`}</div>
      ${b.lessons.map((l) => `<div class="sched-lw"><button class="sched-l" data-jg="${l.id}">
        <span class="sched-t">${esc(l.time || "—")}</span>
        <span class="grow"><b>${esc(l.name)}</b>${l.is_makeup ? ` <span class="pill">отработки</span>` : ""}<span class="small muted"> ${esc(l.age ? (/\d$/.test(l.age) ? l.age + " лет" : l.age) : "")}${l.coach ? ` · ${esc(l.coach)}` : " · тренер не назначен"}${l.assistant ? ` / ${esc(l.assistant)}` : ""}</span>
          ${Object.entries(l.subs || {}).map(([r, x]) => x.missing ? `<span class="pill red">⚠️ нужна замена: ${r === "main" ? "тренер" : "помощник"} ${esc(x.instead)} отсутствует</span>` : x.solo ? `<span class="pill">👤 без ${r === "main" ? "тренера" : "помощника"}: тренер один</span>` : `<span class="pill yellow">🔁 ${esc(x.name)} вместо ${esc(x.instead || "штатного")}</span>`).join(" ")}</span>
        <span class="small">${l.is_makeup ? `${l.kids} записано` : `${l.kids} из ${l.capacity}`}${l.trials ? ` · 🎯 ${l.trials}` : ""}</span>
      </button>${!compact && can("schedule", "full") ? `<button class="btn sm sub-btn" data-sub="${l.id}" title="Поставить другого тренера на этот день">🔁</button>` : ""}</div>`).join("") || `<div class="small muted">Тренировок нет</div>`}
    </section>`).join("")}</div>`;
}

async function scheduleWidget() {
  const d = await api("GET", "/api/schedule/day");
  if (!d.branches.length) return "";
  return `<details class="now sched-widget" open style="margin-bottom:14px"><summary class="now-head"><h3>Сегодня в филиалах</h3>
    <span class="small muted">${d.branches.map((b) => `${esc(b.name)}: ${b.lessons.length} трен.`).join(" · ")}</span></summary>${dayHtml(d, true)}</details>`;
}

async function renderSchedule(page) {
  const C = S.sched;
  if (!C.date) C.date = S.meta.today;
  page.innerHTML = `<div class="page-head"><h2 class="grow">Расписание</h2>
    <div class="seg"><button data-mode="week" class="${C.mode === "week" ? "active" : ""}">Неделя</button><button data-mode="day" class="${C.mode === "day" ? "active" : ""}">День</button></div></div>
    <div id="sched-body"></div>`;
  page.querySelectorAll("[data-mode]").forEach((b) => (b.onclick = () => { step(() => { C.mode = b.dataset.mode; }); }));
  const body = $("#sched-body");
  if (C.mode === "week") {
    const monday = addDays(C.date, -((new Date(C.date + "T00:00").getDay() + 6) % 7));
    const w = await api("GET", "/api/schedule/week?start=" + monday);
    if (!C.branch || !w.branches.some((b) => b.id === C.branch)) C.branch = w.branches.length ? w.branches[0].id : null;
    const end = addDays(w.start, 6), thisMon = addDays(S.meta.today, -((new Date(S.meta.today + "T00:00").getDay() + 6) % 7));
    body.innerHTML = `<div class="wk-nav">
        <button class="btn sm" id="w-prev">←</button>
        <b>${dShort(w.start)} – ${dShort(end)}</b>
        <button class="btn sm" id="w-next">→</button>
        ${w.start !== thisMon ? `<button class="btn sm" id="w-now">Эта неделя</button>` : ""}
      </div>
      <div class="seg wk-br">${w.branches.map((b) => `<button data-wbr="${b.id}" class="${b.id === C.branch ? "active" : ""}">${esc(b.name)}</button>`).join("")}</div>
      ${weekPoster(w, C.branch)}`;
    $("#w-prev").onclick = () => { C.date = addDays(w.start, -7); render(); };
    $("#w-next").onclick = () => { C.date = addDays(w.start, 7); render(); };
    if ($("#w-now")) $("#w-now").onclick = () => { C.date = S.meta.today; render(); };
    body.querySelectorAll("[data-wbr]").forEach((b) => (b.onclick = () => { C.branch = +b.dataset.wbr; render(); }));
    body.querySelectorAll("[data-wday]").forEach((b) => (b.onclick = () => { step(() => { C.mode = "day"; C.date = b.dataset.wday; }); }));
    return;
  } else {
    const d = await api("GET", "/api/schedule/day?date=" + C.date);
    body.innerHTML = `<div class="row" style="max-width:520px;margin-bottom:12px;align-items:center">
        <button class="btn sm" id="d-prev" style="flex:0">←</button>
        <input type="date" id="d-date" value="${C.date}">
        <button class="btn sm" id="d-next" style="flex:0">→</button>
        <button class="btn sm" id="d-today" style="flex:0">Сегодня</button></div>
      <div class="small muted" style="margin-bottom:10px">${WD_FULL[d.weekday]}</div>${dayHtml(d)}`;
    $("#d-prev").onclick = () => { C.date = addDays(C.date, -1); render(); };
    $("#d-next").onclick = () => { C.date = addDays(C.date, 1); render(); };
    $("#d-today").onclick = () => { C.date = S.meta.today; render(); };
    $("#d-date").onchange = (e) => { C.date = e.target.value; render(); };
    body.querySelectorAll("[data-sub]").forEach((b) => (b.onclick = () => subForm(b, d)));
  }
  wireSchedule(body, C.mode === "day" ? C.date : null);
}

function subForm(btn, d) {
  const l = d.branches.flatMap((b) => b.lessons).find((x) => x.id === +btn.dataset.sub);
  const wrap = btn.closest(".sched-lw");
  const old = wrap.nextElementSibling;
  if (old && old.classList.contains("sub-form")) { old.remove(); return; }
  const f = document.createElement("div");
  f.className = "sub-form";
  wrap.after(f);
  openSubForm(f, { group_id: l.id, name: l.name, date: d.date, own: { main: l.coach_id, assistant: l.assistant_id },
    subs: l.subs || {}, role: (l.subs && l.subs.assistant && l.subs.assistant.missing && !(l.subs.main && l.subs.main.missing)) ? "assistant" : "main",
    hasAssistant: !!l.assistant_id }, d.coaches, render);
}

/* форма замены: используется в расписании и на рабочем столе */
function openSubForm(f, o, coaches, done) {
  const draw = (role) => {
    const cur = o.subs[role] && o.subs[role].coach_id ? o.subs[role].coach_id : o.own[role];
    const sub = o.subs[role] && o.subs[role].id ? o.subs[role] : null;
    f.innerHTML = `<div class="small" style="margin-bottom:6px">Кто ведёт <b>${esc(o.name)}</b> ${dShort(o.date)}</div>
      ${o.hasAssistant ? `<div class="seg" style="margin-bottom:8px"><button type="button" data-r="main" class="${role === "main" ? "active" : ""}">Тренер</button><button type="button" data-r="assistant" class="${role === "assistant" ? "active" : ""}">Помощник</button></div>` : ""}
      <div class="row" style="align-items:flex-end"><div class="field"><label>${role === "main" ? "Тренер" : "Помощник"}</label><select data-c>
        ${o.own[role] ? "" : `<option value="">— выберите —</option>`}${o.own[role === "main" ? "assistant" : "main"] ? `<option value="solo" ${sub && sub.solo ? "selected" : ""}>👤 Никто: второй тренер ${role === "main" ? "проведёт" : "выйдет"} один</option>` : ""}${coaches.map((c) => `<option value="${c.id}" ${c.id === cur ? "selected" : ""} ${c.absent ? "disabled" : ""}>${esc(c.name)}${c.id === o.own[role] ? " (штатный)" : ""}${c.absent ? " (отсутствует)" : ""}</option>`).join("")}</select></div>
      <div class="field"><label>Комментарий</label><input data-n placeholder="Например: Артём в отпуске" value="${esc(sub ? sub.note : "")}"></div>
      <button class="btn sm primary" data-ok style="flex:0">Сохранить</button>${sub ? `<button class="btn sm" data-del style="flex:0">Отменить замену</button>` : ""}</div>
      <div class="small muted">Тренер на замене увидит эту тренировку у себя на столе</div>`;
    f.querySelectorAll("[data-r]").forEach((b) => (b.onclick = () => draw(b.dataset.r)));
    f.querySelector("[data-ok]").onclick = async () => {
      const v = f.querySelector("[data-c]").value, c = +v;
      if (!v) return toast("Выберите тренера");
      try { await api("POST", "/api/substitutions", { group_id: o.group_id, date: o.date, role, coach_id: c, solo: v === "solo", note: f.querySelector("[data-n]").value }); toast("Сохранено"); done(); }
      catch (e) { fail(e); }
    };
    const del = f.querySelector("[data-del]");
    if (del) del.onclick = async () => { try { await api("DELETE", `/api/substitutions/${sub.id}`); toast("Замена отменена"); done(); } catch (e) { fail(e); } };
  };
  draw(o.role || "main");
}

/* блок на рабочем столе: отсутствия тренеров и тренировки без замены (по дням) */
function needsGroups(n) {
  const m = new Map();
  n.forEach((x) => { const k = `${x.date}|${x.absent_id}|${x.role}|${x.branch}`; if (!m.has(k)) m.set(k, { ...x, items: [] }); m.get(k).items.push(x); });
  return [...m.values()];
}
const WD_RU = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
const shortName = (n) => { const p = (n || "").trim().split(/\s+/); return p.length > 1 ? `${p[0]} ${p[1][0]}.` : n; };
const lessonChip = (y) => `${esc(y.time)}${y.is_makeup ? " отработки" : y.age ? ` · ${esc(y.age)}` : ""}`;

function needsSubsBox(d) {
  const n = d.needs_subs || [], ab = d.absences || [];
  if (!n.length && !ab.length) return `<div style="margin:-6px 0 12px"><button class="linkish small" id="abs-add">+ Тренер будет отсутствовать</button><div id="abs-form"></div></div>`;
  const gr = needsGroups(n);
  const showAll = S.needsAll, list = showAll ? gr : gr.slice(0, 3);
  const days = new Set(gr.map((x) => x.date)).size;
  return `<section class="now ${n.length ? "" : "calm"} subs-box" style="margin-bottom:14px">
    <div class="now-head"><h3>${n.length ? `Нужны замены · ${days} ${days === 1 ? "день" : days < 5 ? "дня" : "дней"}` : "Замены подобраны 👍"}</h3>
      <button class="btn sm" id="abs-add" style="margin-left:auto">+ Отсутствие</button></div>
    <div id="abs-form"></div>
    ${ab.length ? `<div class="abs-list">${ab.map((a) => `<span class="abs-chip">🏖 <b>${esc(shortName(a.user_name))}</b> ${dShort(a.date_from)}${a.date_to !== a.date_from ? `–${dShort(a.date_to)}` : ""}<button data-abdel="${a.id}" title="Убрать отсутствие">✕</button></span>`).join("")}</div>` : ""}
    ${list.length ? `<div class="need-list">${list.map((x, i) => { const hot = x.date <= addDays(S.meta.today, 2) ? "red" : "yellow"; return `<div class="need ${hot}">
      <div class="need-top"><div class="need-date"><b>${dShort(x.date)}</b><span>${WD_RU[new Date(x.date + "T00:00").getDay()]}</span></div>
        <div class="grow"><div class="need-who">Нужен ${x.role === "main" ? "тренер" : "помощник"} вместо <b>${esc(shortName(x.absent))}</b></div>
          <div class="need-br">${esc(x.branch)}</div></div>
        <button class="btn sm ${hot === "red" ? "primary" : ""}" data-nsub="${gr.indexOf(x)}">Подобрать</button></div>
      <div class="need-chips">${x.items.map((y) => `<span>${lessonChip(y)}</span>`).join("")}</div>
      <div class="sub-form" data-nf="${gr.indexOf(x)}" hidden></div></div>`; }).join("")}</div>
      ${gr.length > 3 ? `<button class="linkish small" id="needs-more" style="margin-top:8px">${showAll ? "Свернуть" : `Показать ещё ${gr.length - 3}`}</button>` : ""}` : ""}
  </section>`;
}

function wireNeedsSubs(page, d) {
  const gr = needsGroups(d.needs_subs || []);
  const coaches = d.coaches || [];
  page.querySelectorAll("[data-nsub]").forEach((b) => (b.onclick = () => {
    const x = gr[+b.dataset.nsub], f = page.querySelector(`[data-nf="${b.dataset.nsub}"]`);
    f.hidden = !f.hidden;
    if (f.hidden) return;
    const away = new Set((d.absences || []).filter((a) => a.date_from <= x.date && a.date_to >= x.date).map((a) => a.user_id));
    const partner = x.items.every((y) => y.partner_id === x.partner_id) && x.partner_id && !away.has(x.partner_id) ? coaches.find((c) => c.id === x.partner_id) : null;
    f.innerHTML = `<div class="row" style="flex-wrap:wrap;align-items:flex-end">
      <div class="field"><label>Кто выйдет ${x.role === "main" ? "тренером" : "помощником"}</label><select data-c><option value="">— выберите —</option>
        ${partner ? `<option value="solo">👤 Никто: ${esc(partner.name)} ${x.role === "main" ? "проведёт" : "выйдет"} один</option>` : ""}
        ${coaches.filter((c) => !away.has(c.id)).map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("")}</select></div>
      <div class="field"><label>На какие тренировки</label><div class="chips">${x.items.map((y, k) => `<label class="chip"><input type="checkbox" data-it="${k}" checked> ${lessonChip(y)}</label>`).join("")}</div></div>
      <button class="btn sm primary" data-ok style="flex:0">Сохранить</button></div>
      <div class="small muted">Можно поставить одного человека на весь день или разных: снимите галочки и сохраните по очереди</div>`;
    f.querySelector("[data-ok]").onclick = async () => {
      const v = f.querySelector("[data-c]").value, c = +v;
      if (!v) return toast("Выберите тренера");
      const its = [...f.querySelectorAll("[data-it]:checked")].map((i) => x.items[+i.dataset.it]);
      try {
        for (const y of its) await api("POST", "/api/substitutions", v === "solo" ? { group_id: y.group_id, date: y.date, role: y.role, solo: true, note: `${y.absent} отсутствует` }
          : { group_id: y.group_id, date: y.date, role: y.role, coach_id: c, note: `${y.absent} отсутствует` });
        toast("Замена сохранена"); render();
      } catch (e) { fail(e); }
    };
  }));
  page.querySelectorAll("[data-abdel]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Убрать отсутствие? Уже поставленные замены останутся")) return;
    try { await api("DELETE", `/api/absences/${b.dataset.abdel}`); render(); } catch (e) { fail(e); }
  }));
  const more = page.querySelector("#needs-more");
  if (more) more.onclick = () => { S.needsAll = !S.needsAll; render(); };
  const add = page.querySelector("#abs-add");
  if (add) add.onclick = () => {
    const box = page.querySelector("#abs-form");
    if (box.innerHTML) { box.innerHTML = ""; return; }
    box.innerHTML = `<div class="sub-form"><div class="row" style="flex-wrap:wrap;align-items:flex-end">
      <div class="field"><label>Кто отсутствует</label><select data-au>${coaches.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("")}</select></div>
      <div class="field" style="flex:0 0 150px"><label>С</label><input type="date" data-af value="${S.meta.today}"></div>
      <div class="field" style="flex:0 0 150px"><label>По (включительно)</label><input type="date" data-at value="${S.meta.today}"></div>
      <div class="field"><label>Причина</label><input data-an placeholder="Отпуск, болезнь, турнир"></div>
      <button class="btn sm primary" data-asave style="flex:0">Сохранить</button></div>
      <div class="small muted">CRM найдёт все его тренировки в эти дни и покажет здесь, кому нужно подобрать замену</div></div>`;
    box.querySelector("[data-asave]").onclick = async () => {
      try { await api("POST", "/api/absences", { user_id: +box.querySelector("[data-au]").value, date_from: box.querySelector("[data-af]").value,
        date_to: box.querySelector("[data-at]").value, note: box.querySelector("[data-an]").value }); toast("Отсутствие сохранено"); render(); }
      catch (e) { fail(e); }
    };
  };
}

function wireSchedule(root, date) {
  root.querySelectorAll("[data-jg]").forEach((b) => (b.onclick = () => {
    S.journal.group = +b.dataset.jg;
    if (date) S.journal.date = date;
    else { const t = new Date(S.meta.today + "T00:00"); const diff = (+b.dataset.wd - ((t.getDay() + 6) % 7) + 7) % 7; S.journal.date = addDays(S.meta.today, diff); }
    go("journal");
  }));
}

/* ---------- конструктор: филиалы, группы, смены ---------- */
/* Филиалы и группы: филиал сворачивается, группы разложены по дням компактной таблицей */
const strOpen = () => { try { return JSON.parse(localStorage.getItem("crm.br_open") || "{}"); } catch (_) { return {}; } };
function strDays(gs) {
  const by = {};
  gs.forEach((g) => { const k = g.weekdays || ""; (by[k] = by[k] || []).push(g); });
  return Object.entries(by).sort(([a], [b]) => (+(a.split(",")[0] || 9)) - (+(b.split(",")[0] || 9)) || a.localeCompare(b))
    .map(([k, list]) => [k ? k.split(",").map((x) => WD[+x]).join(" / ") : "Дни не указаны", list.sort((x, y) => (x.time || "").localeCompare(y.time || ""))]);
}
/* что в названии группы сверх филиала, дней, времени и возраста: например «ОФП» или «ПУП» */
function strExtra(g, b) {
  const first = (b.name || "").replace(/[«»"]/g, " ").split(/\s+/).filter((w) => w && !/^(тц|ск)$/i.test(w))[0] || "";
  return (g.name || "").split(/\s+/).filter((w) => w
    && w.toLowerCase() !== first.toLowerCase()
    && !/^((пн|вт|ср|чт|пт|сб|вс)\/?)+$/i.test(w)
    && !/^\d{1,2}:\d{2}$/.test(w)
    && !/^\d+([–-]\d+)?$/.test(w)).join(" ");
}
const plural = window.plural || ((n, a, b, c) => { const m = n % 10, h = n % 100; return m === 1 && h !== 11 ? a : m >= 2 && m <= 4 && (h < 12 || h > 14) ? b : c; });

async function renderStructure(box, users) {
  const d = await api("GET", "/api/structure");
  const coaches = users.filter((u) => u.role === "coach" && u.active);
  const staff = users.filter((u) => u.active);
  box.innerHTML = `<h3>Филиалы, группы и смены</h3>
    <div class="small muted" style="margin-bottom:10px">Здесь создаются филиалы и группы, назначаются тренеры, места и смены администраторов. Ничего не удаляется: группа или филиал уходят в архив и возвращаются оттуда</div>
    ${d.branches.filter((b) => b.active).map((b) => {
      const gs = b.groups.filter((g) => g.active), main = gs.filter((g) => !g.is_makeup);
      const kids = main.reduce((a, g) => a + (g.kids || 0), 0), cap = main.reduce((a, g) => a + (g.capacity || 0), 0);
      const open = strOpen()[b.id];
      return `<div class="br-block ${open ? "open" : ""}" data-brb="${b.id}">
      <div class="br-head" data-brt="${b.id}"><span class="br-arrow">▸</span><b>${esc(b.name)}</b>
        <span class="small muted">${gs.length} ${plural(gs.length, "группа", "группы", "групп")}${cap ? ` · ${kids} из ${cap} мест` : ""}</span>
        <button class="btn sm" data-bedit="${b.id}">Изменить</button></div>
      <div class="br-body">
        <div class="small muted" style="margin:-2px 0 8px 26px">${esc(b.address || "адрес не указан")}</div>
        ${strDays(gs).map(([lbl, list]) => `<div class="gt-day">${lbl}</div>
          ${list.map((g) => { const pc = g.capacity ? Math.min(100, Math.round(100 * g.kids / g.capacity)) : 0, extra = g.is_makeup ? "" : strExtra(g, b);
            return `<div class="gt-row" data-gedit="${g.id}" data-b="${b.id}" title="${esc(g.name)}: нажмите, чтобы изменить">
            <b class="gt-time">${esc(g.time || "—")}</b><span class="gt-age">${g.age ? esc(g.age) : ""}</span>
            <span class="gt-coach">${esc(g.coach_name || "без тренера")}${g.assistant_name ? ` / ${esc(g.assistant_name)}` : ""}${extra ? ` <span class="pill">${esc(extra)}</span>` : ""}${g.is_makeup ? ` <span class="pill">отработки</span>` : ""}</span>
            ${g.is_makeup ? `<span></span>` : `<span class="gt-fill ${g.kids > g.capacity ? "over" : ""}"><i style="width:${pc}%"></i></span><span class="gt-num">${g.kids}/${g.capacity}</span>`}</div>`; }).join("")}`).join("") || `<div class="small muted">Групп пока нет</div>`}
        <button class="btn sm" data-gadd="${b.id}" style="margin-top:10px">+ Группа</button>
        <details class="br-shifts" data-shb="${b.id}" ${(S.shiftOpen || {})[b.id] ? "open" : ""}><summary><b>Смены администраторов</b> <span class="muted">(${b.shifts.length})</span></summary>
        ${b.shifts.map((sh) => `<div class="row" style="align-items:center;margin-top:6px"><div class="small">${esc(sh.user_name)} · ${sh.weekdays.split(",").map((x) => WD[+x]).join(", ")}${sh.time_from ? ` · ${esc(sh.time_from)}–${esc(sh.time_to)}` : ""}</div>
          <button class="btn sm danger" data-shdel="${sh.id}" style="flex:0">Убрать</button></div>`).join("") || `<div class="small muted">Смен пока нет</div>`}
        <div class="shift-form" data-sb="${b.id}">
          <select data-su>${staff.filter((u) => u.role !== "coach").map((u) => `<option value="${u.id}">${esc(u.name)}</option>`).join("")}</select>
          <div class="chips">${WD.map((w, i) => `<button type="button" class="chip day" data-sd="${i}">${w}</button>`).join("")}</div>
          <input data-sf type="time" value="10:00"><input data-st type="time" value="21:00">
          <button class="btn sm" data-shadd>+ Смена</button>
        </div></details>
      </div>
    </div>`; }).join("")}
    <button class="btn" id="b-add" style="margin-top:12px">+ Филиал</button>`;
  const reload = () => renderStructure(box, users);
  const refreshMeta = async () => { S.meta = await api("GET", "/api/meta"); reload(); };
  box.querySelectorAll("[data-gadd]").forEach((b) => (b.onclick = () => groupForm(null, +b.dataset.gadd, d, coaches, refreshMeta)));
  box.querySelectorAll("[data-gedit]").forEach((b) => (b.onclick = () => groupForm(+b.dataset.gedit, +b.dataset.b, d, coaches, refreshMeta)));
  // «Смены администраторов» остаются раскрытыми после добавления или удаления смены
  box.querySelectorAll("[data-shb]").forEach((d) => d.addEventListener("toggle", () => { S.shiftOpen = S.shiftOpen || {}; S.shiftOpen[d.dataset.shb] = d.open; }));
  box.querySelectorAll("[data-brt]").forEach((h) => (h.onclick = (e) => {
    if (e.target.closest("[data-bedit]")) return;
    const blk = h.closest(".br-block"), on = blk.classList.toggle("open");
    try { const o = strOpen(); o[h.dataset.brt] = on; localStorage.setItem("crm.br_open", JSON.stringify(o)); } catch (_) {}
  }));
  box.querySelectorAll("[data-bedit]").forEach((b) => (b.onclick = () => branchForm(d.branches.find((x) => x.id === +b.dataset.bedit), refreshMeta)));
  $("#b-add").onclick = () => branchForm(null, refreshMeta);
  box.querySelectorAll("[data-sd]").forEach((b) => (b.onclick = () => b.classList.toggle("on")));
  box.querySelectorAll(".shift-form").forEach((f) => (f.querySelector("[data-shadd]").onclick = async () => {
    const days = [...f.querySelectorAll("[data-sd].on")].map((x) => +x.dataset.sd);
    try { await api("POST", "/api/shifts", { user_id: +f.querySelector("[data-su]").value, branch_id: +f.dataset.sb, weekdays: days, time_from: f.querySelector("[data-sf]").value, time_to: f.querySelector("[data-st]").value }); toast("Смена добавлена"); reload(); }
    catch (e) { fail(e); }
  }));
  box.querySelectorAll("[data-shdel]").forEach((b) => (b.onclick = async () => { await api("DELETE", `/api/shifts/${b.dataset.shdel}`).catch(fail); reload(); }));
}

function groupForm(gid, branchId, d, coaches, done) {
  const g = gid ? d.branches.flatMap((b) => b.groups).find((x) => x.id === gid) : { weekdays: "", capacity: 12, branch_id: branchId };
  const days = (g.weekdays || "").split(",").filter(Boolean).map(Number);
  openDrawer(`<h2>${gid ? "Группа" : "Новая группа"}</h2>
    <form class="section" id="gf">
      <div class="field"><label>Название</label><input name="name" required value="${esc(g.name || "")}" placeholder="Небо Пн/Чт 17:45"></div>
      <div class="field"><label>Филиал</label><select name="branch_id">${d.branches.filter((b) => b.active).map((b) => `<option value="${b.id}" ${b.id === (g.branch_id || branchId) ? "selected" : ""}>${esc(b.name)}</option>`).join("")}</select></div>
      <div class="field"><label>Дни</label><div class="chips">${WD.map((w, i) => `<button type="button" class="chip day ${days.includes(i) ? "on" : ""}" data-gd="${i}">${w}</button>`).join("")}</div></div>
      <div class="row"><div class="field"><label>Время начала</label><input name="time" type="time" value="${esc(g.time || "")}"></div>
        <div class="field"><label>Возраст</label><input name="age" value="${esc(g.age || "")}" placeholder="4–5"></div>
        <div class="field"><label>Мест</label><input name="capacity" type="number" min="0" max="200" value="${g.capacity ?? 12}"></div></div>
      <div id="g-dt"></div>
      ${gid ? `<div class="field"><label>Новые дни и время действуют с</label><input type="date" name="valid_from" min="${S.meta.today}">
        <div class="small muted">Пусто: меняем сразу. С датой (например, с 1 декабря): до неё всё по-старому, проданные абонементы сами переедут на новые даты</div></div>
        ${(g.versions || []).map((v) => `<div class="alert" style="margin-bottom:8px">С ${dShort(v.valid_from)}: ${v.weekdays.split(",").map((x) => WD[+x]).join(", ")}${v.time ? " " + esc(v.time) : ""}
          <button type="button" class="btn sm" data-vcancel="${v.valid_from}" style="margin-left:6px">Отменить</button></div>`).join("")}` : ""}
      <div class="row"><div class="field"><label>Тренер</label><select name="coach_id"><option value="">— без тренера —</option>${coaches.map((u) => `<option value="${u.id}" ${u.id === g.coach_id ? "selected" : ""}>${esc(u.name)}</option>`).join("")}</select></div>
        <div class="field"><label>Помощник</label><select name="assistant_id"><option value="">— без помощника —</option>${coaches.map((u) => `<option value="${u.id}" ${u.id === g.assistant_id ? "selected" : ""}>${esc(u.name)}</option>`).join("")}</select></div></div>
      <div class="field"><label>Заметка</label><input name="note" value="${esc(g.note || "")}" placeholder="Например: ОФП в недели без турнира"></div>
      <label class="check"><input type="checkbox" name="is_makeup" ${g.is_makeup ? "checked" : ""}> Это группа отработок</label>
      <label class="check"><input type="checkbox" name="trial_ok" ${g.trial_ok === 0 ? "" : "checked"}> Можно записывать на пробное</label>
      <div class="row" style="margin-top:14px"><button class="btn primary">Сохранить</button>
        ${gid ? `${can("export") ? `<a class="btn" href="/api/groups/${gid}/export">Список в Excel</a>` : ""}<button type="button" class="btn danger" id="g-arch">В архив</button>` : ""}</div>
    </form>`);
  const f = $("#gf");
  const dt = Object.fromEntries((g.day_times || "").split(",").filter((x) => x.includes("=")).map((x) => x.split("=")));
  const drawDt = () => {
    const on = [...f.querySelectorAll("[data-gd].on")].map((x) => +x.dataset.gd);
    $("#g-dt").innerHTML = on.length > 1 ? `<div class="small muted" style="margin:-4px 0 6px">Если в какой-то день время другое, впишите его. Пустое поле = как время начала</div>
      <div class="row" style="flex-wrap:wrap">${on.map((i) => `<div class="field" style="flex:0 0 110px"><label>${WD[i]}</label><input type="time" data-dt="${i}" value="${esc(dt[i] || "")}"></div>`).join("")}</div>` : "";
    f.querySelectorAll("[data-dt]").forEach((x) => (x.onchange = () => { dt[x.dataset.dt] = x.value; }));
  };
  drawDt();
  f.querySelectorAll("[data-gd]").forEach((b) => (b.onclick = () => { b.classList.toggle("on"); drawDt(); }));
  f.onsubmit = async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(f));
    data.is_makeup = f.elements.is_makeup.checked;
    data.trial_ok = f.elements.trial_ok.checked;
    data.weekdays = [...f.querySelectorAll("[data-gd].on")].map((x) => +x.dataset.gd);
    data.day_times = Object.fromEntries([...f.querySelectorAll("[data-dt]")].filter((x) => x.value).map((x) => [x.dataset.dt, x.value]));
    const from = data.valid_from; delete data.valid_from;
    try {
      if (gid && from) {  // дни и время с даты: карточку сохраняем со старым расписанием, новое планируем
        await api("PUT", `/api/groups/${gid}`, { ...data, weekdays: g.weekdays.split(",").map(Number), time: g.time, day_times: g.day_times || "" });
        const r = await api("POST", `/api/groups/${gid}/version`, { valid_from: from, weekdays: data.weekdays, time: data.time, day_times: data.day_times });
        toast(`Новое расписание с ${dShort(from)}${r.repriced ? `, абонементов пересчитано: ${r.repriced}` : ""}`);
      } else {
        await api(gid ? "PUT" : "POST", gid ? `/api/groups/${gid}` : "/api/groups", data); toast("Группа сохранена");
      }
      closeDrawer(); done();
    } catch (err) { fail(err); }
  };
  document.querySelectorAll("[data-vcancel]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Отменить запланированное изменение расписания?")) return;
    try { await api("POST", `/api/groups/${gid}/version`, { cancel: b.dataset.vcancel }); toast("Изменение отменено"); closeDrawer(); done(); } catch (err) { fail(err); }
  }));
  if ($("#g-arch")) $("#g-arch").onclick = async () => {
    if (!confirm("Перенести группу в архив? Вернуть можно в Настройках → Архив")) return;
    try { await api("POST", `/api/groups/${gid}/archive`, { active: false }); toast("Группа в архиве"); closeDrawer(); done(); } catch (err) { fail(err); }
  };
}

function branchForm(b, done) {
  openDrawer(`<h2>${b ? "Филиал" : "Новый филиал"}</h2>
    <form class="section" id="bf">
      <div class="field"><label>Название</label><input name="name" required value="${esc(b ? b.name : "")}"></div>
      <div class="field"><label>Адрес</label><input name="address" value="${esc(b ? b.address : "")}" placeholder="Подставляется в напоминания о пробном"></div>
      <div class="field"><label>Не больше детей на одной тренировке</label><input name="lesson_limit" type="number" min="0" value="${b && b.lesson_limit ? b.lesson_limit : ""}" placeholder="Без ограничения">
        <div class="small muted">CRM не даст записать больше: ни учеником, ни на пробное, ни на отработку. Кто предупредил, что не придёт, место не занимает</div></div>
      <div class="row"><button class="btn primary">Сохранить</button>${b ? `<button type="button" class="btn danger" id="b-arch">В архив</button>` : ""}</div>
    </form>`);
  $("#bf").onsubmit = async (e) => {
    e.preventDefault();
    try { await api(b ? "PUT" : "POST", b ? `/api/branches/${b.id}` : "/api/branches", Object.fromEntries(new FormData(e.target))); toast("Сохранено"); closeDrawer(); done(); } catch (err) { fail(err); }
  };
  if ($("#b-arch")) $("#b-arch").onclick = async () => {
    try { await api("POST", `/api/branches/${b.id}/archive`, { active: false }); toast("Филиал в архиве"); closeDrawer(); done(); } catch (err) { fail(err); }
  };
}

async function renderArchive(box) {
  const d = await api("GET", "/api/archive");
  const block = (title, list, render, kind) => `<div class="small" style="margin-top:10px"><b>${title}</b></div>
    ${list.length ? `<div class="chips" style="margin-top:6px">${list.map((x) => `<span class="chip">${render(x)} <button class="linkish" data-restore="${kind}:${x.id}">вернуть</button></span>`).join("")}</div>` : `<div class="small muted">Пусто</div>`}`;
  box.innerHTML = `<h3>Архив</h3><div class="small muted">Сюда попадает всё, что убрали. Ничего не удаляется навсегда</div>
    <div class="small muted" style="margin-top:6px">Ученики и лиды из архива: раздел «Ученики» → кнопка «Архив»</div>
    ${block("Группы", d.groups, (x) => `${esc(x.name)} <span class="muted">${esc(x.branch)}</span>`, "group")}
    ${block("Филиалы", d.branches, (x) => esc(x.name), "branch")}
    ${block("Сотрудники", d.users, (x) => `${esc(x.name)} <span class="muted">${esc(x.login)}</span>`, "user")}`;
  box.querySelectorAll("[data-restore]").forEach((b) => (b.onclick = async () => {
    const [k, id] = b.dataset.restore.split(":");
    const call = { student: ["POST", `/api/students/${id}/archive`, { archived: false }], group: ["POST", `/api/groups/${id}/archive`, { active: true }],
      branch: ["POST", `/api/branches/${id}/archive`, { active: true }], user: ["PATCH", `/api/users/${id}`, { active: true }] }[k];
    try { await api(...call); toast("Возвращено"); S.meta = await api("GET", "/api/meta"); renderArchive(box); } catch (e) { fail(e); }
  }));
}
