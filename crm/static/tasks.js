"use strict";
/* Абонемент Плюс — задачи сотрудников, автосообщения, стол тренера. */

S.tasks = { scope: "mine" };
const AUTO_LABEL = { day_before: "за день", "2h_before": "за 2 часа", after: "после пробного" };

function taskRow(t) {
  const s = t.student || {};
  const trial = s.trial_at ? `пробное ${dtWhen(s.trial_at)}` : "";
  const auto = (t.auto || []).map((a) => `<span class="pill ${a.ok ? "green" : "red"}" title="${esc(a.error || "")}">✉️ ${AUTO_LABEL[a.kind] || a.kind}: ${a.ok ? "ушло" : "не ушло"}</span>`).join("");
  return `<div class="task" data-task="${t.id}">
    <span class="task-ico" aria-hidden="true">${t.icon}</span>
    <div class="grow">
      <div><b>${esc(t.title)}</b>${t.source === "erp" ? ` <span class="pill">из ERP</span>` : ""}</div>
      ${s.id ? `<button class="linkish" data-open="${s.id}">${esc(s.name)}</button> <span class="small muted">${esc([s.parent, s.phone, trial].filter(Boolean).join(", "))}</span>` : ""}
      ${t.note ? `<div class="small muted">${esc(t.note)}</div>` : ""}
      ${auto ? `<div class="chips" style="margin-top:6px">${auto}</div>` : ""}
      ${S.tasks.scope === "all" && t.assignee ? `<div class="small muted">Ответственный: ${esc(t.assignee)}</div>` : ""}
      <div class="task-done" hidden><input placeholder="Итог: дозвонилась, подтвердили…" data-result><button class="btn sm primary" data-finish>Готово</button></div>
    </div>
    <span class="small muted when">${dtWhen(t.due_at)}</span>
    <div class="now-act">
      ${s.phone ? `<button class="btn sm" data-dial="${esc(s.phone)}">📞</button>` : ""}
      <button class="btn sm" data-done>Выполнено</button>
    </div>
  </div>`;
}

async function tasksSection() {
  const d = await api("GET", "/api/tasks?scope=" + S.tasks.scope);
  const g = d.groups, total = g.overdue.length + g.today.length;
  const block = (title, list, cls = "") => list.length ? `<div class="task-group ${cls}"><h4>${title} · ${list.length}</h4>${list.map(taskRow).join("")}</div>` : "";
  return `<section class="now tasks-box">
    <div class="now-head"><h3>${S.tasks.scope === "all" ? "Задачи команды" : "Мои задачи"}: ${total ? `на сегодня ${total}` : "на сегодня всё сделано 🎉"}</h3>
      <span class="small muted">Выполнено сегодня: ${d.done_today}</span>
      ${S.user.role === "admin" ? `<div class="seg" style="margin-left:auto"><button data-scope="mine" class="${S.tasks.scope === "mine" ? "active" : ""}">Мои</button><button data-scope="all" class="${S.tasks.scope === "all" ? "active" : ""}">Все сотрудники</button></div>` : ""}
    </div>
    ${d.team.length && S.tasks.scope === "all" ? `<div class="chips" style="margin-bottom:10px">${d.team.map((u) => `<span class="pill ${u.overdue ? "red" : u.open ? "yellow" : "green"}">${esc(u.name)}: ${u.open} на сегодня${u.overdue ? `, просрочено ${u.overdue}` : ""}</span>`).join("")}</div>` : ""}
    ${block("Просрочено", g.overdue, "late")}${block("Сегодня", g.today)}${block("Завтра", g.tomorrow, "later")}
    ${!total && !g.tomorrow.length ? `<div class="muted small">Задачи по пробным появятся сами: за день до пробного, после пробного и если клиент не пришёл</div>` : ""}
  </section>`;
}

function wireTasks(root) {
  root.querySelectorAll("[data-scope]").forEach((b) => (b.onclick = () => { S.tasks.scope = b.dataset.scope; render(); }));
  root.querySelectorAll(".task").forEach((row) => {
    const id = row.dataset.task;
    row.querySelector("[data-done]").onclick = () => { const f = row.querySelector(".task-done"); f.hidden = !f.hidden; if (!f.hidden) f.querySelector("input").focus(); };
    const finish = async () => {
      try { await api("POST", `/api/tasks/${id}/done`, { result: row.querySelector("[data-result]").value }); toast("Задача выполнена"); render(); }
      catch (e) { fail(e); }
    };
    row.querySelector("[data-finish]").onclick = finish;
    row.querySelector("[data-result]").onkeydown = (e) => { if (e.key === "Enter") finish(); };
    const dl = row.querySelector("[data-dial]");
    if (dl) dl.onclick = () => dial(dl.dataset.dial);
    const op = row.querySelector("[data-open]");
    if (op) op.onclick = () => openStudent(+op.dataset.open);
  });
}

const yearsWord = (n) => n % 10 === 1 && n % 100 !== 11 ? "год" : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? "года" : "лет";

function birthdaysBox(list) {
  if (!list || !list.length) return "";
  return `<section class="now bday-box" style="margin-bottom:14px"><div class="now-head"><h3>🎂 Дни рождения</h3></div>
    <div class="chips">${list.map((b) => `<button class="chip" data-open="${b.id}">${b.when === "today" ? "Сегодня" : "Вчера"}: <b>${esc(b.name)}</b>, ${b.age} ${yearsWord(b.age)}${b.group ? ` · ${esc(b.group)}` : ""}</button>`).join("")}</div>
    <div class="small muted" style="margin-top:6px">Поздравьте в мессенджере, а тренер поздравит на тренировке</div></section>`;
}

/* ---------- стол тренера ---------- */
async function renderCoachToday(page) {
  const d = await api("GET", "/api/coach/today");
  const first = firstName(S.user.name);
  page.innerHTML = `
    <div class="page-head"><h2 class="grow">Привет, ${esc(first)}!</h2><span class="muted small">${dShort(d.date)}, ${["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"][(new Date(d.date + "T00:00").getDay() + 6) % 7]}</span></div>
    ${d.missing.length ? `<section class="now"><div class="now-head"><h3>Не отмечено с прошлых тренировок</h3></div>
      <div class="now-list">${d.missing.map((m) => `<div class="now-item red"><span class="ref-card red"></span>
        <div class="grow"><b>${esc(m.name)}</b><div class="small">${dShort(m.date)}: не отмечено ${m.left}</div></div>
        <div class="now-act"><button class="btn sm primary" data-j="${m.group_id}" data-d="${m.date}">Отметить</button></div></div>`).join("")}</div></section>` : ""}
    <h3 style="margin:18px 0 10px">Тренировки сегодня</h3>
    ${d.today.length ? `<div class="today-grid">${d.today.map((g) => {
      const all = g.kids + g.trials, done = g.marked + g.trials_marked;
      return `<button class="today-card ${done >= all && all ? "ok" : ""}" data-j="${g.id}" data-d="${d.date}">
        <div class="today-time">${esc(g.time || "")}</div>
        <div class="today-name">${esc(g.name)}</div>
        <div class="small muted">${esc(g.branch)}${g.age ? `, ${esc(g.age)} лет` : ""}</div>
        <div class="today-facts"><span>🧒 ${g.kids} ${kidsWord(g.kids)}</span>${g.trials ? `<span>🎯 пробных: ${g.trials}</span>` : ""}</div>
        ${(g.birthdays || []).map((b) => `<div class="bday">🎂 <b>${esc(b.name)}</b>: ${b.when === "today" ? "сегодня" : "вчера был"} день рождения, ${b.age} ${yearsWord(b.age)}. Поздравь всей командой!</div>`).join("")}
        <div class="bar"><i style="width:${all ? Math.round(100 * done / all) : 0}%"></i></div>
        <div class="small">${all ? (done >= all ? "Все отмечены 👍" : `Отмечено ${done} из ${all}. Нажми, чтобы отметить`) : "В группе пока нет детей"}</div>
      </button>`; }).join("")}</div>` : `<div class="panel center" style="padding:30px">Сегодня тренировок нет. Отдыхай 😎</div>`}
    <div class="small muted" style="margin-top:14px">Отмечай детей сразу после тренировки: от этого зависят отработки, перерасчёт абонементов и твоё место в таблице тренеров</div>`;
  page.querySelectorAll("[data-j]").forEach((b) => (b.onclick = () => { S.journal.group = +b.dataset.j; S.journal.date = b.dataset.d; go("journal"); }));
}

/* ---------- настройки задач (админ) ---------- */
async function renderTaskSettings(box, users) {
  const d = await api("GET", "/api/tasks/settings");
  const v = d.values;
  const staff = users.filter((u) => u.role !== "coach" && u.active);
  const yn = (k) => `<select data-t="${k}"><option value="1" ${v[k] === "1" ? "selected" : ""}>Отправлять</option><option value="0" ${v[k] !== "1" ? "selected" : ""}>Не отправлять</option></select>`;
  box.innerHTML = `
    <h3>Задачи и автосообщения по пробным</h3>
    <div class="field"><label>Кто отвечает за задачи по пробным</label>
      <select data-t="task_owner_trials">${staff.map((u) => `<option value="${u.id}" ${String(u.id) === v.task_owner_trials ? "selected" : ""}>${esc(u.name)}</option>`).join("")}</select></div>
    <div class="small muted" style="margin:-4px 0 12px">На этого сотрудника встают все задачи по пробным, и при переносе из ERP задачи со статусами: ${d.erp_statuses.map((x) => `«${esc(x)}»`).join(", ")}</div>
    <div class="row">
      <div class="field"><label>Напоминание за день</label>${yn("auto_day_before")}</div>
      <div class="field" style="flex:0 0 120px"><label>Во сколько, ч</label><input data-t="day_before_hour" type="number" min="8" max="21" value="${esc(v.day_before_hour)}"></div>
    </div>
    <div class="field"><label>Текст за день</label><textarea data-t="msg_day_before" rows="3">${esc(v.msg_day_before)}</textarea></div>
    <div class="field"><label>Напоминание за 2 часа</label>${yn("auto_2h_before")}</div>
    <div class="field"><label>Текст за 2 часа</label><textarea data-t="msg_2h_before" rows="3">${esc(v.msg_2h_before)}</textarea></div>
    <div class="field"><label>Через 10 минут после пробного</label>${yn("auto_after")}</div>
    <div class="field"><label>Текст после пробного</label><textarea data-t="msg_after" rows="2">${esc(v.msg_after)}</textarea></div>
    <div class="small muted" style="margin-bottom:10px">В текст подставятся: {child} имя ребёнка, {date} дата, {time} время, {place} филиал. Сообщение уходит туда, где семья уже переписывалась со школой (Telegram, MAX или WhatsApp). Если переписки нет, в задаче будет видно «не ушло», и Оля напишет вручную</div>
    <button class="btn primary" id="t-save">Сохранить</button>`;
  $("#t-save").onclick = async () => {
    try { await api("PUT", "/api/tasks/settings", Object.fromEntries([...box.querySelectorAll("[data-t]")].map((i) => [i.dataset.t, i.value]))); toast("Сохранено"); }
    catch (e) { fail(e); }
  };
}
