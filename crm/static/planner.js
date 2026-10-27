"use strict";
/* Абонемент Плюс — планер: свои дела, поручения, повторы, дни рождения сотрудников, контроль команды. */

S.planner = { tab: "mine" };
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
      <button class="btn primary" data-tsave style="flex:0">Поставить задачу</button></div>
    <div class="small muted">Без времени задача висит весь день. Повторяющиеся дела сами появляются на столе в нужные дни</div>
  </div>`;
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

function plannerDay(iso) {
  const d = iso.slice(0, 10), t = S.meta.today;
  if (d < t) return "Просрочено";
  if (d === t) return "Сегодня";
  if (d === addDays(t, 1)) return "Завтра";
  return `${dShort(d)}, ${["вс", "пн", "вт", "ср", "чт", "пт", "сб"][new Date(d + "T00:00").getDay()]}`;
}

function groupByDay(list) {
  const m = new Map();
  list.forEach((t) => { const k = plannerDay(t.due_at); if (!m.has(k)) m.set(k, []); m.get(k).push(t); });
  return [...m.entries()];
}

async function renderPlanner(page) {
  const p = await api("GET", "/api/planner");
  const C = S.planner;
  S.tasks.canManage = p.can_manage;
  const tabs = [["mine", `Мои дела · ${p.mine.length}`], ["given", "Я поручила"], ["recurring", "Повторы"]];
  if (p.can_manage) tabs.push(["team", "Команда"], ["bdays", "Дни рождения"]);
  if (!tabs.some(([k]) => k === C.tab)) C.tab = "mine";
  let body = "";
  if (C.tab === "mine") {
    body = p.mine.length ? groupByDay(p.mine).map(([day, list]) => `<div class="task-group ${day === "Просрочено" ? "late" : ""}"><h4>${day} · ${list.length}</h4>${list.map(taskRow).join("")}</div>`).join("")
      : `<div class="muted">Дел нет. Добавьте первое кнопкой «+ Задача»</div>`;
  } else if (C.tab === "given") {
    body = p.given.length ? p.given.map((t) => `<div class="task ${t.status !== "open" ? "done" : ""}"><span class="task-ico">${t.status === "done" ? "✅" : t.due_at.slice(0, 10) < p.today ? "🔴" : "⏳"}</span>
      <div class="grow"><b>${esc(t.title)}</b> <span class="small muted">→ ${esc(t.assignee)}, ${dtWhen(t.due_at)}</span>
        ${t.status === "done" ? `<div class="small">Сделано ${dtWhen(t.done_at)}${t.result ? `: ${esc(t.result)}` : ""}</div>` : ""}</div>
      ${t.status === "done" && p.can_manage ? `<button class="btn sm" data-tret="${t.id}">Вернуть</button>` : ""}
      ${t.status === "open" ? `<button class="btn sm" data-tcancel="${t.id}">✕</button>` : ""}</div>`).join("") : `<div class="muted">Вы пока никому не ставили задачи</div>`;
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
  page.querySelectorAll("[data-pt]").forEach((b) => (b.onclick = () => { C.tab = b.dataset.pt; render(); }));
  $("#pl-add").onclick = () => openTaskForm($("#pl-form"), render);
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
