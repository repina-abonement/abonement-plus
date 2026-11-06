"use strict";
/* Абонемент Плюс — планер: свои дела, поручения, повторы, дни рождения сотрудников, контроль команды. */

S.planner = { tab: "week", start: null, user: null, status: "all" };
const WD_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

/* форма новой задачи: кому, когда, повтор */
async function openTaskForm(box, done, preset = {}) {
  if (!box) return;
  if (box.innerHTML) { box.innerHTML = ""; return; }
  let p;
  try { p = await api("GET", "/api/planner"); } catch (e) { return fail(e); }
  const staff = p.staff;
  box.innerHTML = `<div class="sub-form task-form">
    <div class="field"><label>Что сделать</label><input data-tt placeholder="Например: переписать оферту" value="${esc(preset.title || "")}"></div>
    <div class="row" style="flex-wrap:wrap;align-items:flex-end">
      ${p.can_manage ? `<div class="field"><label>Кому</label><select data-ta>${staff.map((u) => `<option value="${u.id}" ${u.id === (preset.assignee_id || S.user.id) ? "selected" : ""}>${esc(u.name)}${u.id === S.user.id ? " (себе)" : ""}</option>`).join("")}</select></div>` : ""}
      <div class="field" style="flex:0 0 150px"><label>Когда</label><input type="date" data-td value="${preset.date || S.meta.today}"></div>
      <div class="field" style="flex:0 0 110px"><label>Время</label><input type="time" data-ttm></div>
      <div class="field" style="flex:0 0 170px"><label>Повтор</label><select data-tr><option value="">Не повторять</option>
        ${Object.entries(p.rules).map(([k, t]) => `<option value="${k}">${t[0].toUpperCase() + t.slice(1)}</option>`).join("")}</select></div>
    </div>
    <div data-twd hidden class="chips" style="margin:-2px 0 8px">${WD_SHORT.map((w, i) => `<button type="button" class="chip day" data-twdi="${i}">${w}</button>`).join("")}</div>
    <div class="field"><label>Подробности</label><textarea data-tn rows="2" placeholder="Необязательно"></textarea></div>
    <div class="row" style="align-items:center;flex-wrap:wrap"><label class="check grow"><input type="checkbox" data-tp> 🔥 Важно</label>
      <button class="btn" data-tcancel style="flex:0">Отмена</button>
      <button class="btn primary" data-tsave style="flex:0">Поставить задачу</button></div>
    <div class="small muted">Без времени задача висит весь день. Повторяющиеся дела сами появляются на столе в нужные дни</div>
  </div>`;
  box.querySelector("[data-tcancel]").onclick = () => { box.innerHTML = ""; };
  const rule = box.querySelector("[data-tr]");
  rule.onchange = () => { box.querySelector("[data-twd]").hidden = rule.value !== "weekly"; };
  box.querySelectorAll("[data-twdi]").forEach((b) => (b.onclick = () => b.classList.toggle("on")));
  box.querySelector("[data-tt]").focus();
  box.querySelector("[data-tsave]").onclick = async () => {
    const a = box.querySelector("[data-ta]");
    const data = { title: box.querySelector("[data-tt]").value, date: box.querySelector("[data-td]").value, time: box.querySelector("[data-ttm]").value,
      note: box.querySelector("[data-tn]").value, priority: box.querySelector("[data-tp]").checked, assignee_id: a ? +a.value : S.user.id };
    if (rule.value) data.repeat = { rule: rule.value, weekdays: [...box.querySelectorAll("[data-twdi].on")].map((x) => +x.dataset.twdi) };
    try { await api("POST", "/api/planner/tasks", data); toast("Задача поставлена"); box.innerHTML = ""; done(); } catch (e) { fail(e); }
  };
}

async function renderPlanner(page) {
  const p = await api("GET", "/api/planner");
  const C = S.planner;
  S.tasks.canManage = p.can_manage;
  const tabs = [["week", "Неделя"], ["given", "Я поручила"], ["recurring", "Повторы"]];
  if (p.can_manage) tabs.push(["team", "Команда"], ["bdays", "Дни рождения"]);
  if (!tabs.some(([k]) => k === C.tab)) C.tab = "week";
  let body = "";
  if (C.tab === "week") {
    body = `<div id="pl-week"></div>`;
  } else if (C.tab === "given") {
    const givenRow = (t) => `<div class="task ${t.status !== "open" ? "done" : ""}"><span class="task-ico">${t.status === "done" ? "✅" : t.due_at.slice(0, 10) < p.today ? "🔴" : "⏳"}</span>
      <div class="grow"><b>${esc(t.title)}</b> <span class="small muted">→ ${esc(t.assignee)}, ${dtWhen(t.due_at)}</span>
        ${t.status === "done" ? `<div class="small">Сделано ${dtWhen(t.done_at)}${t.result ? `: ${esc(t.result)}` : ""}</div>` : ""}</div>
      ${t.status === "done" && p.can_manage ? `<button class="btn sm" data-tret="${t.id}">Вернуть</button>` : ""}
      ${t.status === "open" ? `<button class="btn sm" data-tcancel="${t.id}">✕</button>` : ""}</div>`;
    const series = new Map();
    const oneOff = [];
    for (const task of p.given) {
      if (!task.recur_id) {
        oneOff.push(task);
        continue;
      }
      if (!series.has(task.recur_id)) series.set(task.recur_id, []);
      series.get(task.recur_id).push(task);
    }
    body = oneOff.map(givenRow).join("");
    if (series.size) {
      body += `<section class="task-group"><h4>Повторяющиеся задачи · ${series.size}</h4>
        ${[...series.values()].map((items) => `<details class="panel">
          <summary><b>${esc(items[0].title)}</b> <span class="small muted">→ ${esc(items[0].assignee)} · дат: ${items.length}</span></summary>
          ${items.map(givenRow).join("")}</details>`).join("")}</section>`;
    }
    if (!body) body = '<div class="muted">Вы пока никому не ставили задачи</div>';
  } else if (C.tab === "recurring") {
    body = p.recurring.length ? `<div class="list">${p.recurring.map((r) => `<div class="list-row"><div class="grow">🔁 <b>${esc(r.title)}</b>
      <div class="small muted">${esc(r.assignee)} · ${r.rule_label}${r.rule === "weekly" ? ": " + r.weekdays.split(",").map((x) => WD_SHORT[+x]).join(", ") : r.rule === "monthly" ? `, ${r.monthday} числа` : r.rule === "yearly" ? `, ${r.yearday.slice(3)}.${r.yearday.slice(0, 2)}` : ""}${r.time ? ` в ${r.time}` : ""}</div></div>
      <button class="btn sm" data-rstop="${r.id}">Остановить</button></div>`).join("")}</div>` : `<div class="muted">Повторяющихся дел нет. Поставьте задачу и выберите «Повтор»: например, «Купить воду» каждую неделю</div>`;
  } else if (C.tab === "team") {
    const staffOpts = p.staff.filter((u) => u.role !== "coach").map((u) => `<option value="${u.id}" ${u.id === p.controller ? "selected" : ""}>${esc(u.name)}</option>`).join("");
    body = `${S.user.role === "admin" ? `<div class="row" style="max-width:520px;align-items:flex-end;margin-bottom:12px"><div class="field"><label>Контролёр задач команды</label><select id="pl-ctrl"><option value="">Только я</option>${staffOpts}</select></div></div>` : ""}
      ${p.team.length ? p.team.map((u) => `<section class="panel" style="padding:12px 14px;margin-bottom:10px">
        <div class="team-head"><b>${esc(u.name)}</b>
          ${u.overdue.length ? `<span class="pill red">просрочено ${u.overdue.length}</span>` : ""}<span class="pill">сегодня ${u.today.length}</span><span class="pill green">сделано ${u.done_today}</span></div>
        ${[...u.overdue, ...u.today].map((t) => `<div class="small" style="margin-top:6px">${t.due_at.slice(0, 10) < p.today ? "🔴" : "⏳"} ${esc(t.title)} <span class="muted">${dtWhen(t.due_at)}${t.created_by ? `, поставил(а) ${esc(t.created_by)}` : ""}</span></div>`).join("")}
      </section>`).join("") : `<div class="muted">У команды на сегодня всё сделано 🎉</div>`}`;
  } else if (C.tab === "bdays") {
    body = `<div class="small muted" style="margin-bottom:10px">За ${7} дней до дня рождения контролёру придёт задача «Создать чат в Telegram для сбора денег», в сам день «Поздравить»</div>
      <div class="list">${p.staff.map((u) => `<div class="list-row"><span class="grow">${esc(u.name)}</span><input type="date" data-ub="${u.id}" value="${esc(u.birth || "")}" style="flex:0 0 170px"></div>`).join("")}</div>`;
  }
  page.innerHTML = `<div class="page-head"><h2 class="grow">Планер</h2><button class="btn primary" id="pl-add">+ Задача</button></div>
    <div id="pl-form"></div>
    <div class="seg" style="margin-bottom:14px;flex-wrap:wrap">${tabs.map(([k, t]) => `<button data-pt="${k}" class="${C.tab === k ? "active" : ""}">${t}</button>`).join("")}</div>
    <div class="tasks-box">${body}</div>`;
  page.querySelectorAll("[data-pt]").forEach((b) => (b.onclick = () => { step(() => { C.tab = b.dataset.pt; }); }));
  $("#pl-add").onclick = () => openTaskForm($("#pl-form"), render, C.tab === "week" && C.user ? { assignee_id: C.user } : {});
  if (C.tab === "week") await renderWeek($("#pl-week"));
  wireTasks(page);
  page.querySelectorAll("[data-tret]").forEach((b) => (b.onclick = async () => {
    const note = prompt("Что не так? Сотрудник увидит этот комментарий");
    if (note === null) return;
    try { await api("POST", `/api/planner/tasks/${b.dataset.tret}/return`, { note }); toast("Задача вернулась сотруднику"); render(); } catch (e) { fail(e); }
  }));
  page.querySelectorAll("[data-rstop]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Остановить повтор? Прошлые задачи останутся в истории")) return;
    try { await api("POST", `/api/planner/recurring/${b.dataset.rstop}/stop`, {}); render(); } catch (e) { fail(e); }
  }));
  page.querySelectorAll("[data-ub]").forEach((i) => (i.onchange = async () => {
    try { await api("PUT", `/api/users/${i.dataset.ub}/birth`, { birth: i.value }); toast("Сохранено"); } catch (e) { fail(e); }
  }));
  if ($("#pl-ctrl")) $("#pl-ctrl").onchange = async (e) => {
    try { await api("PUT", "/api/planner/settings", { planner_controller: e.target.value }); toast("Контролёр назначен"); render(); } catch (err) { fail(err); }
  };
}


/* ---------- неделя колонками, как в Weeek ---------- */
const PL_DAY = new Intl.DateTimeFormat("ru-RU", { weekday: "long", day: "numeric", month: "long" });
function pcard(t) {
  const done = t.status === "done";
  const time = t.due_at.slice(11, 16) === "23:59" ? "" : t.due_at.slice(11, 16);
  return `<article class="pcard ${done ? "done" : ""} ${t.priority ? "hot" : ""}" draggable="${done ? "false" : "true"}" data-pc="${t.id}">
    <label class="pcheck"><input type="checkbox" aria-label="Задача выполнена" data-pdone="${t.id}" ${done ? "checked" : ""}></label>
    <div class="grow"><div class="ptitle">${t.priority ? "🔥 " : ""}${esc(t.title)}</div>
      <div class="pmeta">${time ? `<span>🕒 ${time}</span>` : ""}${t.recur_id ? `<span>🔁</span>` : ""}${t.student ? `<span>${esc(t.student.name)}</span>` : ""}
        ${t.created_by_id && t.created_by_id !== t.assignee_id && t.created_by ? `<span>от ${esc(firstName(t.created_by))}</span>` : ""}
        ${t.returned_note ? `<span style="color:var(--red)">↩ ${esc(t.returned_note)}</span>` : ""}</div>
      ${t.note ? `<div class="pnote">${esc(t.note)}</div>` : ""}</div>
    ${done ? "" : `<button class="pmove" data-pmove="${t.id}" title="Перенести на другой день">📅</button>`}
  </article>`;
}

async function renderWeek(box) {
  const C = S.planner;
  if (!C.start) C.start = S.meta.today;
  const w = await api("GET", `/api/planner/week?rolling=1&start=${C.start}${C.user ? "&user=" + C.user : ""}`);
  const end = addDays(w.start, 6), today = S.meta.today;
  const visible = (t) => C.status === "all" || t.status === C.status;
  const overdue = w.overdue.filter(visible);
  const days = w.days.map((day) => ({ ...day, tasks: day.tasks.filter(visible) }));
  const groups = (tasks) => {
    if (!tasks.length) return '<div class="empty">Свободно</div>';
    const recurring = tasks.filter((task) => task.recur_id);
    if (!recurring.length) return tasks.map(pcard).join("");
    const other = tasks.filter((task) => !task.recur_id);
    return [[recurring, "Повторяемые"], [other, "Прочие"]]
      .filter(([items]) => items.length)
      .map(([items, title]) => `<div class="small muted">${title}</div>${items.map(pcard).join("")}`)
      .join("");
  };
  box.innerHTML = `<div class="week-nav">
      <button class="btn sm" data-wk="-7">‹</button>
      <b>${dShort(w.start)} – ${dShort(end)}</b>
      <button class="btn sm" data-wk="7">›</button>
      <button class="btn sm" data-wk="0">Эта неделя</button>
      <label>С даты <input type="date" id="wk-start" value="${w.start}"></label>
      <label>Статус <select id="wk-status"><option value="all">Все задачи</option><option value="open">Открытые</option><option value="done">Выполненные</option></select></label>
      ${w.can_manage ? `<select id="wk-user">${w.staff.map((u) => `<option value="${u.id}" ${u.id === w.user_id ? "selected" : ""}>${esc(u.name)}${u.id === S.user.id ? " (я)" : ""}</option>`).join("")}</select>` : ""}
    </div>
    ${overdue.length ? `<section class="col overdue-col"><div class="col-head"><h3>Просрочено: перетащите на нужный день</h3><span class="count">${overdue.length}</span></div>
      <div class="col-body ov-body">${overdue.map(pcard).join("")}</div></section>` : ""}
    <div class="board week-board">${days.map((d) => `<section class="col ${d.date === today ? "is-today" : ""} ${d.date < today ? "past" : ""}" data-day="${d.date}">
      <div class="col-head"><h3>${PL_DAY.format(new Date(d.date + "T00:00"))}</h3><span class="count">${d.tasks.length}</span></div>
      <button type="button" class="btn sm pquick" data-pq="${d.date}">+ Добавить задачу</button>
      <div class="col-body">${groups(d.tasks)}</div>
    </section>`).join("")}</div>
    <input type="date" id="pmove-date" style="position:absolute;opacity:0;pointer-events:none;width:1px;height:1px">`;
  box.querySelectorAll("[data-wk]").forEach((b) => (b.onclick = () => { C.start = +b.dataset.wk ? addDays(w.start, +b.dataset.wk) : S.meta.today; renderWeek(box); }));
  if ($("#wk-user")) $("#wk-user").onchange = (e) => { C.user = +e.target.value; renderWeek(box); };
  box.querySelector("#wk-start").onchange = (e) => {
    if (e.target.value) { C.start = e.target.value; renderWeek(box); }
  };
  const status = box.querySelector("#wk-status");
  status.value = C.status;
  status.onchange = () => { C.status = status.value; renderWeek(box); };
  const reload = () => renderWeek(box);
  const tc = box.querySelector(".week-board .is-today");  // на телефоне сразу показываем сегодня
  if (tc && window.innerWidth < 1100) box.querySelector(".week-board").scrollLeft = tc.offsetLeft - 12;
  // галочка «сделано»
  box.querySelectorAll("[data-pdone]").forEach((cb) => (cb.onchange = async () => {
    try {
      if (cb.checked) await api("POST", `/api/tasks/${cb.dataset.pdone}/done`, { result: "" });
      else await api("POST", `/api/planner/tasks/${cb.dataset.pdone}/reopen`, {});
      reload();
    } catch (e) { fail(e); cb.checked = !cb.checked; }
  }));
  // быстрое добавление в колонку дня
  box.querySelectorAll("[data-pq]").forEach((button) => (button.onclick = () => {
    const form = $("#pl-form");
    form.innerHTML = "";
    openTaskForm(form, () => {
      if (C.status === "done") C.status = "all";
      reload();
    }, { date: button.dataset.pq, assignee_id: w.user_id });
  }));
  // перенос: перетаскиванием (компьютер) или кнопкой 📅 (телефон)
  let dragId = null;
  box.querySelectorAll(".pcard[draggable=true]").forEach((el) => {
    el.addEventListener("dragstart", (e) => { dragId = el.dataset.pc; el.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; });
    el.addEventListener("dragend", () => el.classList.remove("dragging"));
  });
  box.querySelectorAll(".week-board .col").forEach((col) => {
    col.addEventListener("dragover", (e) => { e.preventDefault(); col.classList.add("drop"); });
    col.addEventListener("dragleave", () => col.classList.remove("drop"));
    col.addEventListener("drop", async (e) => {
      e.preventDefault(); col.classList.remove("drop");
      if (!dragId) return;
      try { await api("POST", `/api/planner/tasks/${dragId}/move`, { date: col.dataset.day }); dragId = null; reload(); } catch (err) { fail(err); }
    });
  });
  const picker = $("#pmove-date");
  box.querySelectorAll("[data-pmove]").forEach((b) => (b.onclick = () => {
    picker.value = S.meta.today;
    picker.onchange = async () => { try { await api("POST", `/api/planner/tasks/${b.dataset.pmove}/move`, { date: picker.value }); toast("Перенесено"); reload(); } catch (e) { fail(e); } };
    try { picker.showPicker(); } catch (_) { const d = prompt("На какую дату? ГГГГ-ММ-ДД", addDays(S.meta.today, 1)); if (d) { picker.value = d; picker.onchange(); } }
  }));
}
