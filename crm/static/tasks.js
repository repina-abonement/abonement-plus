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
      <div>${t.priority ? "🔥 " : ""}<b>${esc(t.title)}</b>${t.source === "erp" ? ` <span class="pill">из ERP</span>` : ""}
        ${t.created_by_id && t.created_by_id !== t.assignee_id && t.created_by ? ` <span class="pill blue">от ${esc(firstName(t.created_by))}</span>` : ""}
        ${t.recur_id ? ` <span class="pill" title="Повторяющееся дело">🔁</span>` : ""}</div>
      ${t.returned_note ? `<div class="small" style="color:var(--red)">↩ Вернули: ${esc(t.returned_note)}</div>` : ""}
      ${s.id ? `<button class="linkish" data-open="${s.id}">${esc(s.name)}</button> <span class="small muted">${esc([s.parent, s.phone, trial].filter(Boolean).join(", "))}</span>` : ""}
      ${t.note ? `<div class="small muted">${esc(t.note)}</div>` : ""}
      ${auto ? `<div class="chips" style="margin-top:6px">${auto}</div>` : ""}
      ${S.tasks.scope === "all" && t.assignee ? `<div class="small muted">Ответственный: ${esc(t.assignee)}</div>` : ""}
      <div class="task-done" hidden><input placeholder="Итог: дозвонилась, подтвердили…" data-result><button class="btn sm primary" data-finish>Готово</button></div>
    </div>
    <span class="small muted when">${dtWhen(t.due_at)}</span>
    <div class="now-act">
      ${s.phone ? `<button class="btn sm" data-dial="${esc(s.phone)}">📞</button>` : ""}
      ${t.kind === "rebook" ? `<button class="btn sm primary" data-rebook="${t.id}">Итог звонка</button>` : `<button class="btn sm" data-done>Выполнено</button>`}
      ${t.kind === "plan" && (S.tasks.canManage || t.created_by_id === S.user.id) ? `<button class="btn sm" data-tcancel="${t.id}" title="Убрать задачу">✕</button>` : ""}
    </div>
  </div>`;
}

async function tasksSection() {
  const as = S.viewAs && S.viewAs.id !== S.user.id ? S.viewAs : null;
  const d = await api("GET", as ? "/api/tasks?as_user=" + as.id : "/api/tasks?scope=" + S.tasks.scope);
  S.tasks.canManage = !!d.can_manage;
  const g = d.groups, total = g.overdue.length + g.today.length;
  const block = (title, list, cls = "") => list.length ? `<div class="task-group ${cls}"><h4>${title} · ${list.length}</h4>${list.map(taskRow).join("")}</div>` : "";
  return `<section class="now tasks-box">
    <div class="now-head"><h3>${as ? `Задачи: ${esc(as.name)}` : S.tasks.scope === "all" ? "Задачи команды" : "Мои задачи"}: ${total ? `на сегодня ${total}` : "на сегодня всё сделано 🎉"}</h3>
      <span class="small muted">Выполнено сегодня: ${d.done_today}</span>
      ${!as ? `<button class="btn sm primary" data-tadd style="margin-left:auto">+ Задача</button>` : ""}
      ${d.can_manage && !as ? `<div class="seg"><button data-scope="mine" class="${S.tasks.scope === "mine" ? "active" : ""}">Мои</button><button data-scope="all" class="${S.tasks.scope === "all" ? "active" : ""}">Все сотрудники</button></div>` : ""}
    </div>
    ${d.team.length && S.tasks.scope === "all" ? `<div class="chips" style="margin-bottom:10px">${d.team.map((u) => `<span class="pill ${u.overdue ? "red" : u.open ? "yellow" : "green"}">${esc(u.name)}: ${u.open} на сегодня${u.overdue ? `, просрочено ${u.overdue}` : ""}</span>`).join("")}</div>` : ""}
    <div data-taddbox></div>
    ${block("Просрочено", g.overdue, "late")}${block("Сегодня", g.today)}${block("Завтра", g.tomorrow, "later")}
    ${!total && !g.tomorrow.length ? `<div class="muted small">Задачи по пробным появятся сами: за день до пробного, после пробного и если клиент не пришёл</div>` : ""}
  </section>`;
}

function wireTasks(root) {
  root.querySelectorAll("[data-scope]").forEach((b) => (b.onclick = () => { S.tasks.scope = b.dataset.scope; render(); }));
  const add = root.querySelector("[data-tadd]");
  if (add) add.onclick = () => openTaskForm(root.querySelector("[data-taddbox]"), render);
  root.querySelectorAll("[data-tcancel]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Убрать задачу?")) return;
    try { await api("POST", `/api/planner/tasks/${b.dataset.tcancel}/cancel`, {}); render(); } catch (e) { fail(e); }
  }));
  root.querySelectorAll(".task").forEach((row) => {
    const id = row.dataset.task;
    if (row.querySelector("[data-done]")) row.querySelector("[data-done]").onclick = () => { const f = row.querySelector(".task-done"); f.hidden = !f.hidden; if (!f.hidden) f.querySelector("input").focus(); };
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
const KID_TAG = {
  paid: ["green", "абонемент"], single: ["", "разовое"], unpaid: ["yellow", "без оплаты"], vacation: ["", "в отпуске"],
  makeup: ["", "отработка"], trial: ["blue", "пробное"], warned: ["", "предупредил, не придёт"],
};
const COACH_LINES = [
  "Сегодня твоя команда, тренер. Погнали ⚽", "Сначала разминка, потом магия", "Свисток на шею, улыбку на лицо",
  "Хорошая тренировка начинается с отметки в журнале 😉", "Каждый ребёнок сегодня должен уйти с победой, даже маленькой",
];
const WD_LOW = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"];

async function renderCoachToday(page, coachId, viewBar = "") {
  const d = await api("GET", "/api/coach/today" + (coachId ? "?coach_id=" + coachId : ""));
  const first = firstName((d.coach && d.coach.name) || S.user.name);
  const t = d.totals, wd = (iso) => WD_LOW[(new Date(iso + "T00:00").getDay() + 6) % 7];
  const line = COACH_LINES[new Date(d.date + "T00:00").getDate() % COACH_LINES.length];
  const up = d.upcoming || { covering: [], covered: [] };
  const fact = (n, label, cls = "") => n ? `<div class="cfact ${cls}"><b>${n}</b><span>${label}</span></div>` : "";
  page.innerHTML = `${viewBar}
    <div class="page-head"><h2 class="grow">Привет, ${esc(first)}!</h2><span class="muted small">${dShort(d.date)}, ${wd(d.date)}</span></div>
    <div class="muted" style="margin:-6px 0 14px">${line}</div>
    ${(d.missing_marks || []).map((m) => `<div class="alert" style="margin-bottom:10px"><b>Срочно отметь детей:</b> ${esc(m.group)}, ${dShort(m.date)} ${esc(m.time)}. Без отметки: ${m.missing}
      <button class="btn sm primary" data-j="${m.group_id}" data-d="${m.date}" style="margin-left:8px">Отметить</button></div>`).join("")}
    ${d.today.length ? `<section class="now coach-sum"><div class="now-head"><h3>Сегодня ${d.today.length} ${d.today.length === 1 ? "тренировка" : d.today.length < 5 ? "тренировки" : "тренировок"}, ждём ${t.total} ${kidsWord(t.total)}</h3></div>
      <div class="cfacts">${fact(t.paid, "по абонементу", "green")}${fact(t.trials, "на пробном", "blue")}${fact(t.single, "разовое")}${fact(t.makeups, "на отработке")}${fact(t.unpaid, "без оплаты", "yellow")}${fact(t.vacation, "в отпуске, не придут")}</div></section>` : ""}
    ${checkinBox(d, !!coachId)}
    ${(d.tasks || []).length ? `<section class="now tasks-box" style="margin-bottom:12px"><div class="now-head"><h3>Задачи на сегодня: ${d.tasks.length}</h3></div>${d.tasks.map(taskRow).join("")}</section>` : ""}
    ${d.away ? `<section class="now calm"><div class="now-head"><h3>🏖 Ты сейчас отдыхаешь до ${dShort(d.away.date_to)}</h3></div><div class="small">Тренировки на эти дни передаём другим тренерам</div></section>` : ""}
    ${d.replaced.length ? `<section class="now calm" style="margin-top:12px"><div class="now-head"><h3>Сегодня тебя заменяют</h3></div>
      ${d.replaced.map((r) => `<div class="small">🔁 ${esc(r.time || "")} ${esc(r.name)}, ${esc(r.branch)}: вместо тебя <b>${esc(r.by)}</b></div>`).join("")}</section>` : ""}
    ${d.missing.length ? `<section class="now" style="margin-top:12px"><div class="now-head"><h3>Не отмечено с прошлых тренировок</h3></div>
      <div class="now-list">${d.missing.map((m) => `<div class="now-item red"><span class="ref-card red"></span>
        <div class="grow"><b>${esc(m.name)}</b><div class="small">${dShort(m.date)}: не отмечено ${m.left}</div></div>
        <div class="now-act"><button class="btn sm primary" data-j="${m.group_id}" data-d="${m.date}">Отметить</button></div></div>`).join("")}</div></section>` : ""}
    <h3 style="margin:18px 0 10px">Тренировки сегодня</h3>
    ${d.today.length ? `<div class="today-grid">${d.today.map((g) => {
      const all = g.kids + g.trials, done = g.marked + g.trials_marked, c = g.counts;
      return `<div class="today-card ${done >= all && all ? "ok" : ""} ${g.sub_for ? "is-sub" : ""}">
        ${g.sub_for ? `<div class="sub-flag">🔁 Замена: ты ${g.sub_for.role === "assistant" ? "помощник " : ""}вместо ${esc(g.sub_for.instead || "другого тренера")}${g.sub_for.note ? `. ${esc(g.sub_for.note)}` : ""}</div>` : ""}
        ${!g.sub_for && g.role === "assistant" ? `<div class="small muted">Ты помощник${g.partner ? `, главный: ${esc(g.partner)}` : ""}</div>` : ""}
        ${!g.sub_for && g.role === "main" && g.partner ? `<div class="small muted">Помощник: ${esc(g.partner)}</div>` : ""}
        <div class="today-time">${esc(g.time || "")}</div>
        <div class="today-name">${esc(g.name)}</div>
        <div class="small muted">${esc(g.branch)}${g.age ? `, ${esc(g.age)} лет` : ""}</div>
        <div class="today-facts"><span>🧒 придут ${all} ${kidsWord(all)}${g.is_makeup ? "" : ` из ${g.capacity}`}</span></div>
        <div class="chips" style="margin:6px 0">${[["paid", c.paid], ["trial", c.trials], ["single", c.single], ["makeup", c.makeups], ["unpaid", c.unpaid], ["vacation", c.vacation], ["warned", c.warned]]
          .filter(([, n]) => n).map(([k, n]) => `<span class="pill ${KID_TAG[k][0]}">${KID_TAG[k][1]}: ${n}</span>`).join("")}</div>
        ${(g.birthdays || []).map((b) => `<div class="bday">🎂 <b>${esc(b.name)}</b>: ${b.when === "today" ? "сегодня" : "вчера был"} день рождения, ${b.age} ${yearsWord(b.age)}. Поздравь всей командой!</div>`).join("")}
        ${g.list.length ? `<details class="kid-list"><summary class="small">Кто придёт</summary>${g.list.map((k) => `<div class="kid-row"><span class="grow">${esc(k.name)}</span>${k.note ? `<span class="small muted">${esc(k.note)}</span>` : ""}<span class="pill ${KID_TAG[k.tag][0]}">${KID_TAG[k.tag][1]}</span></div>`).join("")}</details>` : ""}
        ${g.staff && g.time && (d.date < S.meta.today || (new Date().toTimeString().slice(0, 5) >= g.time)) ? staffPicker(g.staff, d.coaches, "c" + g.id) : ""}
        <div class="bar"><i style="width:${all ? Math.round(100 * done / all) : 0}%"></i></div>
        <div class="row" style="align-items:center;margin-top:6px"><div class="small grow">${all ? (done >= all ? "Все отмечены 👍" : `Отмечено ${done} из ${all}`) : "В группе пока нет детей"}</div>
          ${all ? `<button class="btn sm primary" data-j="${g.id}" data-d="${d.date}" style="flex:0">Отметить</button>` : ""}</div>
      </div>`; }).join("")}</div>` : `<div class="panel center" style="padding:30px">Сегодня тренировок нет. Отдыхай 😎</div>`}
    ${up.covering.length || up.covered.length ? `<section class="now calm" style="margin-top:16px"><div class="now-head"><h3>Замены на ближайшие 2 недели</h3></div>
      ${up.covering.map((x) => `<div class="small">🔁 ${dShort(x.date)}, ${wd(x.date)}, ${esc(x.time || "")}: <b>ты выходишь</b> в ${esc(x.group_name)} (${esc(x.branch)}) вместо ${esc(x.own_name || "другого тренера")}</div>`).join("")}
      ${up.covered.map((x) => `<div class="small muted">↪ ${dShort(x.date)}, ${wd(x.date)}, ${esc(x.time || "")}: ${esc(x.group_name)} ведёт ${esc(x.sub_name)}, у тебя выходной</div>`).join("")}</section>` : ""}
    ${merchShiftBox(d, !!coachId)}
    <div class="small muted" style="margin-top:14px">Отмечай детей сразу после тренировки: от этого зависят отработки, перерасчёт абонементов и твоё место в таблице тренеров</div>`;
  page.querySelectorAll("[data-j]").forEach((b) => (b.onclick = () => { S.journal.group = +b.dataset.j; S.journal.date = b.dataset.d; go("journal"); }));
  wireCheckin(page);
  if (!coachId) wireTasks(page);
  wireMerchShift(page, d);
  d.today.forEach((g) => wireStaffPicker(page, "c" + g.id, g.id, d.date, render));
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
    <div class="field"><label>Предложение ближайших пробных (кнопка «📅 Предложить ближайшие ПУ» в переписке). {slots} — список дат</label><textarea data-t="msg_trial_offer" rows="4">${esc(v.msg_trial_offer || "")}</textarea></div>
    <div class="small muted" style="margin-bottom:10px">В текст подставятся: {child} имя ребёнка, {date} дата, {time} время, {place} филиал. Сообщение уходит туда, где семья уже переписывалась со школой (Telegram, MAX или WhatsApp). Если переписки нет, в задаче будет видно «не ушло», и Оля напишет вручную</div>
    <button class="btn primary" id="t-save">Сохранить</button>`;
  $("#t-save").onclick = async () => {
    try { await api("PUT", "/api/tasks/settings", Object.fromEntries([...box.querySelectorAll("[data-t]")].map((i) => [i.dataset.t, i.value]))); toast("Сохранено"); }
    catch (e) { fail(e); }
  };
}
