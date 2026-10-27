"use strict";
/* Абонемент Плюс — расписание, конструктор филиалов и групп, смены, архив. */

S.sched = { mode: "day", date: null };
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
          ${Object.entries(l.subs || {}).map(([r, x]) => x.missing ? `<span class="pill red">⚠️ нужна замена: ${r === "main" ? "тренер" : "помощник"} ${esc(x.instead)} отсутствует</span>` : `<span class="pill yellow">🔁 ${esc(x.name)} вместо ${esc(x.instead || "штатного")}</span>`).join(" ")}</span>
        <span class="small">${l.is_makeup ? `${l.kids} записано` : `${l.kids} из ${l.capacity}`}${l.trials ? ` · 🎯 ${l.trials}` : ""}</span>
      </button>${!compact && !isCoach() ? `<button class="btn sm sub-btn" data-sub="${l.id}" title="Поставить другого тренера на этот день">🔁</button>` : ""}</div>`).join("") || `<div class="small muted">Тренировок нет</div>`}
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
    <div class="seg"><button data-mode="day" class="${C.mode === "day" ? "active" : ""}">День</button><button data-mode="week" class="${C.mode === "week" ? "active" : ""}">Неделя</button></div></div>
    <div id="sched-body"></div>`;
  page.querySelectorAll("[data-mode]").forEach((b) => (b.onclick = () => { step(() => { C.mode = b.dataset.mode; }); }));
  const body = $("#sched-body");
  if (C.mode === "week") {
    const w = await api("GET", "/api/schedule/week");
    const brs = [...new Set(w.days.flat().map((x) => x.branch_id))];
    body.innerHTML = `<div class="week">${w.days.map((list, i) => `<div class="week-col"><h4>${WD_FULL[i]}</h4>
      ${list.map((x) => `<button class="week-l" data-jg="${x.id}" data-wd="${i}" style="--br:${BR_COLORS[brs.indexOf(x.branch_id) % BR_COLORS.length]}"><b>${esc(x.time || "")}</b> ${esc(x.name)}<span>${esc(x.branch)}${x.coach ? ` · ${esc(x.coach)}` : ""}${x.assistant ? ` / ${esc(x.assistant)}` : ""}</span></button>`).join("") || `<div class="small muted">Нет</div>`}</div>`).join("")}</div>`;
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
        ${o.own[role] ? "" : `<option value="">— выберите —</option>`}${coaches.map((c) => `<option value="${c.id}" ${c.id === cur ? "selected" : ""} ${c.absent ? "disabled" : ""}>${esc(c.name)}${c.id === o.own[role] ? " (штатный)" : ""}${c.absent ? " (отсутствует)" : ""}</option>`).join("")}</select></div>
      <div class="field"><label>Комментарий</label><input data-n placeholder="Например: Артём в отпуске" value="${esc(sub ? sub.note : "")}"></div>
      <button class="btn sm primary" data-ok style="flex:0">Сохранить</button>${sub ? `<button class="btn sm" data-del style="flex:0">Отменить замену</button>` : ""}</div>
      <div class="small muted">Тренер на замене увидит эту тренировку у себя на столе</div>`;
    f.querySelectorAll("[data-r]").forEach((b) => (b.onclick = () => draw(b.dataset.r)));
    f.querySelector("[data-ok]").onclick = async () => {
      const c = +f.querySelector("[data-c]").value;
      if (!c) return toast("Выберите тренера");
      try { await api("POST", "/api/substitutions", { group_id: o.group_id, date: o.date, role, coach_id: c, note: f.querySelector("[data-n]").value }); toast("Замена сохранена"); done(); }
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
    f.innerHTML = `<div class="row" style="flex-wrap:wrap;align-items:flex-end">
      <div class="field"><label>Кто выйдет ${x.role === "main" ? "тренером" : "помощником"}</label><select data-c><option value="">— выберите —</option>${coaches.filter((c) => !away.has(c.id)).map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("")}</select></div>
      <div class="field"><label>На какие тренировки</label><div class="chips">${x.items.map((y, k) => `<label class="chip"><input type="checkbox" data-it="${k}" checked> ${lessonChip(y)}</label>`).join("")}</div></div>
      <button class="btn sm primary" data-ok style="flex:0">Сохранить</button></div>
      <div class="small muted">Можно поставить одного человека на весь день или разных: снимите галочки и сохраните по очереди</div>`;
    f.querySelector("[data-ok]").onclick = async () => {
      const c = +f.querySelector("[data-c]").value;
      if (!c) return toast("Выберите тренера");
      const its = [...f.querySelectorAll("[data-it]:checked")].map((i) => x.items[+i.dataset.it]);
      try {
        for (const y of its) await api("POST", "/api/substitutions", { group_id: y.group_id, date: y.date, role: y.role, coach_id: c, note: `${y.absent} отсутствует` });
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
async function renderStructure(box, users) {
  const d = await api("GET", "/api/structure");
  const coaches = users.filter((u) => u.role === "coach" && u.active);
  const staff = users.filter((u) => u.active);
  box.innerHTML = `<h3>Филиалы, группы и смены</h3>
    <div class="small muted" style="margin-bottom:10px">Здесь создаются филиалы и группы, назначаются тренеры, места и смены администраторов. Ничего не удаляется: группа или филиал уходят в архив и возвращаются оттуда</div>
    ${d.branches.filter((b) => b.active).map((b) => `<div class="br-block">
      <div class="br-head"><b>${esc(b.name)}</b><span class="small muted">${esc(b.address || "адрес не указан")}</span>
        <button class="btn sm" data-bedit="${b.id}">Изменить</button></div>
      <div class="list" style="box-shadow:none">${b.groups.filter((g) => g.active).map((g) => `<div class="list-row">
        <div class="grow"><b>${g.time && !g.name.includes(g.time) ? esc(g.time) + " " : ""}${esc(g.name)}</b>${g.is_makeup ? ` <span class="pill">отработки</span>` : ""}
          <div class="small muted">${g.weekdays.split(",").map((x) => WD[+x]).join(", ")}${g.age ? ` · ${esc(g.age)} лет` : ""} · ${esc(g.coach_name || "без тренера")}${g.assistant_name ? ` / ${esc(g.assistant_name)}` : ""} · ${g.is_makeup ? "" : `${g.kids} из ${g.capacity} мест`}</div></div>
        <a class="btn sm" href="/api/groups/${g.id}/export" title="Список учеников в Excel">Excel</a>
        <button class="btn sm" data-gedit="${g.id}" data-b="${b.id}">Изменить</button></div>`).join("") || `<div class="list-row muted small">Групп пока нет</div>`}</div>
      <button class="btn sm" data-gadd="${b.id}" style="margin-top:8px">+ Группа</button>
      <div class="small" style="margin-top:12px"><b>Смены</b></div>
      ${b.shifts.map((sh) => `<div class="row" style="align-items:center;margin-top:6px"><div class="small">${esc(sh.user_name)} · ${sh.weekdays.split(",").map((x) => WD[+x]).join(", ")}${sh.time_from ? ` · ${esc(sh.time_from)}–${esc(sh.time_to)}` : ""}</div>
        <button class="btn sm danger" data-shdel="${sh.id}" style="flex:0">Убрать</button></div>`).join("") || `<div class="small muted">Смен пока нет</div>`}
      <div class="shift-form" data-sb="${b.id}">
        <select data-su>${staff.filter((u) => u.role !== "coach").map((u) => `<option value="${u.id}">${esc(u.name)}</option>`).join("")}</select>
        <div class="chips">${WD.map((w, i) => `<button type="button" class="chip day" data-sd="${i}">${w}</button>`).join("")}</div>
        <input data-sf type="time" value="10:00"><input data-st type="time" value="21:00">
        <button class="btn sm" data-shadd>+ Смена</button>
      </div>
    </div>`).join("")}
    <button class="btn" id="b-add" style="margin-top:12px">+ Филиал</button>`;
  const reload = () => renderStructure(box, users);
  const refreshMeta = async () => { S.meta = await api("GET", "/api/meta"); reload(); };
  box.querySelectorAll("[data-gadd]").forEach((b) => (b.onclick = () => groupForm(null, +b.dataset.gadd, d, coaches, refreshMeta)));
  box.querySelectorAll("[data-gedit]").forEach((b) => (b.onclick = () => groupForm(+b.dataset.gedit, +b.dataset.b, d, coaches, refreshMeta)));
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
      <div class="row"><div class="field"><label>Тренер</label><select name="coach_id"><option value="">— без тренера —</option>${coaches.map((u) => `<option value="${u.id}" ${u.id === g.coach_id ? "selected" : ""}>${esc(u.name)}</option>`).join("")}</select></div>
        <div class="field"><label>Помощник</label><select name="assistant_id"><option value="">— без помощника —</option>${coaches.map((u) => `<option value="${u.id}" ${u.id === g.assistant_id ? "selected" : ""}>${esc(u.name)}</option>`).join("")}</select></div></div>
      <div class="field"><label>Заметка</label><input name="note" value="${esc(g.note || "")}" placeholder="Например: ОФП в недели без турнира"></div>
      <label class="check"><input type="checkbox" name="is_makeup" ${g.is_makeup ? "checked" : ""}> Это группа отработок</label>
      <div class="row" style="margin-top:14px"><button class="btn primary">Сохранить</button>
        ${gid ? `<a class="btn" href="/api/groups/${gid}/export">Список в Excel</a><button type="button" class="btn danger" id="g-arch">В архив</button>` : ""}</div>
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
    data.weekdays = [...f.querySelectorAll("[data-gd].on")].map((x) => +x.dataset.gd);
    data.day_times = Object.fromEntries([...f.querySelectorAll("[data-dt]")].filter((x) => x.value).map((x) => [x.dataset.dt, x.value]));
    try { await api(gid ? "PUT" : "POST", gid ? `/api/groups/${gid}` : "/api/groups", data); toast("Группа сохранена"); closeDrawer(); done(); } catch (err) { fail(err); }
  };
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
    ${block("Ученики и лиды", d.students, (x) => `${esc(x.name)} <span class="muted">${esc(x.family)}</span>`, "student")}
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
