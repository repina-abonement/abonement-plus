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
        <span class="grow"><b>${esc(l.name)}</b>${l.is_makeup ? ` <span class="pill">отработки</span>` : ""}<span class="small muted"> ${esc(l.age ? l.age + " лет" : "")}${l.coach ? ` · ${esc(l.coach)}` : " · тренер не назначен"}</span>
          ${l.sub ? `<span class="pill yellow">🔁 замена вместо ${esc(l.sub.instead || "штатного")}</span>` : ""}</span>
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
  page.querySelectorAll("[data-mode]").forEach((b) => (b.onclick = () => { C.mode = b.dataset.mode; render(); }));
  const body = $("#sched-body");
  if (C.mode === "week") {
    const w = await api("GET", "/api/schedule/week");
    const brs = [...new Set(w.days.flat().map((x) => x.branch_id))];
    body.innerHTML = `<div class="week">${w.days.map((list, i) => `<div class="week-col"><h4>${WD_FULL[i]}</h4>
      ${list.map((x) => `<button class="week-l" data-jg="${x.id}" data-wd="${i}" style="--br:${BR_COLORS[brs.indexOf(x.branch_id) % BR_COLORS.length]}"><b>${esc(x.time || "")}</b> ${esc(x.name)}<span>${esc(x.branch)}${x.coach ? ` · ${esc(x.coach)}` : ""}</span></button>`).join("") || `<div class="small muted">Нет</div>`}</div>`).join("")}</div>`;
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
  const cur = l.sub ? l.sub.coach_id : l.coach_id;
  const f = document.createElement("div");
  f.className = "sub-form";
  f.innerHTML = `<div class="small" style="margin-bottom:6px">Кто ведёт <b>${esc(l.name)}</b> ${dShort(d.date)}${l.sub ? `. Сейчас замена вместо ${esc(l.sub.instead)}` : ""}</div>
    <div class="row" style="align-items:flex-end"><div class="field"><label>Тренер</label><select data-c>${d.coaches.map((c) => `<option value="${c.id}" ${c.id === cur ? "selected" : ""}>${esc(c.name)}${c.id === l.coach_id ? " (штатный)" : ""}</option>`).join("")}</select></div>
    <div class="field"><label>Комментарий</label><input data-n placeholder="Например: Никита на турнире" value="${esc(l.sub ? l.sub.note : "")}"></div>
    <button class="btn sm primary" data-ok style="flex:0">Сохранить</button>${l.sub ? `<button class="btn sm" data-del style="flex:0">Отменить замену</button>` : ""}</div>
    <div class="small muted">Тренер на замене увидит эту тренировку у себя на столе и сможет отметить детей</div>`;
  wrap.after(f);
  f.querySelector("[data-ok]").onclick = async () => {
    try { await api("POST", "/api/substitutions", { group_id: l.id, date: d.date, coach_id: +f.querySelector("[data-c]").value, note: f.querySelector("[data-n]").value }); toast("Сохранено"); render(); }
    catch (e) { fail(e); }
  };
  const del = f.querySelector("[data-del]");
  if (del) del.onclick = async () => { try { await api("DELETE", `/api/substitutions/${l.sub.id}`); toast("Замена отменена"); render(); } catch (e) { fail(e); } };
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
          <div class="small muted">${g.weekdays.split(",").map((x) => WD[+x]).join(", ")}${g.age ? ` · ${esc(g.age)} лет` : ""} · ${esc(g.coach_name || "без тренера")} · ${g.is_makeup ? "" : `${g.kids} из ${g.capacity} мест`}</div></div>
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
      <div class="field"><label>Тренер</label><select name="coach_id"><option value="">— без тренера —</option>${coaches.map((u) => `<option value="${u.id}" ${u.id === g.coach_id ? "selected" : ""}>${esc(u.name)}</option>`).join("")}</select></div>
      <label class="check"><input type="checkbox" name="is_makeup" ${g.is_makeup ? "checked" : ""}> Это группа отработок</label>
      <div class="row" style="margin-top:14px"><button class="btn primary">Сохранить</button>
        ${gid ? `<a class="btn" href="/api/groups/${gid}/export">Список в Excel</a><button type="button" class="btn danger" id="g-arch">В архив</button>` : ""}</div>
    </form>`);
  const f = $("#gf");
  f.querySelectorAll("[data-gd]").forEach((b) => (b.onclick = () => b.classList.toggle("on")));
  f.onsubmit = async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(f));
    data.is_makeup = f.elements.is_makeup.checked;
    data.weekdays = [...f.querySelectorAll("[data-gd].on")].map((x) => +x.dataset.gd);
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
