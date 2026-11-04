"use strict";
/* Абонемент Плюс — задачи сотрудников, автосообщения, стол тренера. */

S.tasks = { scope: "mine" };
const AUTO_LABEL = { day_before: "за день", "2h_before": "за 2 часа", after: "после пробного" };

function taskRow(t) {
  const s = t.student || {};
  const trial = s.trial_at ? `пробное ${dtWhen(s.trial_at)}` : "";
  const auto = (t.auto || []).map((a) => `<span class="pill ${a.ok ? "green" : "red"}" title="${esc(a.error || "")}">✉️ ${AUTO_LABEL[a.kind] || a.kind}: ${a.ok ? "ушло" : "не ушло"}</span>`).join("");
  S.taskById = S.taskById || {}; S.taskById[t.id] = t;
  return `<div class="task" data-task="${t.id}">
    <span class="task-ico" aria-hidden="true">${t.icon}</span>
    <div class="grow">
      <div>${t.priority ? "🔥 " : ""}<b>${esc(t.title)}</b> ${kindPill(t)}${t.source === "erp" ? ` <span class="pill">из ERP</span>` : ""}
        ${t.created_by_id && t.created_by_id !== t.assignee_id && t.created_by ? ` <span class="pill blue">от ${esc(firstName(t.created_by))}</span>` : ""}
        ${t.recur_id ? ` <span class="pill" title="Повторяющееся дело">🔁</span>` : ""}</div>
      ${t.returned_note ? `<div class="small" style="color:var(--red)">↩ Вернули: ${esc(t.returned_note)}</div>` : ""}
      ${s.id ? `<button class="linkish" data-open="${s.id}">${esc(s.name)}</button> <span class="small muted">${esc([s.parent, s.phone, trial].filter(Boolean).join(", "))}</span>` : ""}
      ${t.note ? `<div class="small muted">${esc(t.note)}</div>` : ""}
      ${auto ? `<div class="chips" style="margin-top:6px">${auto}</div>` : ""}
      ${t.assignee && (S.tasks.scope === "all" || t.assignee_id !== S.user.id) ? `<div class="small muted">Исполнитель: ${esc(t.assignee)}</div>` : ""}
      <div class="task-done" hidden><input placeholder="Итог: дозвонилась, подтвердили…" data-result><button class="btn sm primary" data-finish>Готово</button></div>
    </div>
    <span class="small muted when">${dtWhen(t.due_at)}</span>
    <div class="now-act">
      ${s.phone ? `<button class="btn sm" data-dial="${esc(s.phone)}">📞</button>` : ""}
      ${t.kind === "rebook" ? `<button class="btn sm primary" data-rebook="${t.id}">Итог звонка</button>` : S.user.role === "coach" ? "" : `<button class="btn sm" data-done>Выполнено</button>`}
      ${["admin", "operator"].includes(S.user.role) ? `<button class="btn sm" data-tpass title="Передать другому сотруднику">Передать</button>` : ""}
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
    const pass = row.querySelector("[data-tpass]");
    if (pass) pass.onclick = () => openTaskEdit(S.taskById[id], render, "assignee");
    // нажали на саму задачу (не на кнопку): открыть правку
    row.addEventListener("click", (e) => {
      if (e.target.closest("button, a, input, .task-done") || !["admin", "operator"].includes(S.user.role)) return;
      openTaskEdit(S.taskById[id], render);
    });
  });
}

const kindPill = (t) => t.manual ? `<span class="pill kp-man" title="Поставил сотрудник">ручная</span>` : `<span class="pill kp-auto" title="CRM поставила сама по цепочке событий">авто</span>`;

/* Воронка «Ручные задачи»: задача появляется в свой день. Просрочено / Сегодня. Выполненная сразу пропадает */
S.manual = { scope: "mine" };
async function renderManualBoard(page) {
  const d = await api("GET", "/api/tasks/manual?scope=" + S.manual.scope);
  S.taskById = S.taskById || {};
  d.columns.forEach((c) => c.cards.forEach((t) => (S.taskById[t.id] = t)));
  const card = (t, col) => `<article class="card mt-card ${col === "overdue" ? "red" : ""} ${col === "done" ? "done" : ""}" data-mt="${t.id}" data-col="${col}">
    <div class="name">${esc(t.title)}</div>
    ${t.student ? `<div class="meta">${esc(t.student.name)}${t.student.parent ? ` · ${esc(t.student.parent)}` : ""}</div>` : ""}
    ${t.note ? `<div class="meta">${esc(t.note)}</div>` : ""}
    <div class="meta">👤 ${esc(t.assignee || "")}${col === "done" ? ` · сделано ${esc((t.done_at || "").slice(11, 16))}${t.result ? `: ${esc(t.result)}` : ""}` : ` · ${col === "overdue" ? "⏰ " + dShort(t.due_at) + " " : ""}${(t.due_at || "").slice(11, 16) === "23:59" ? "в течение дня" : (t.due_at || "").slice(11, 16)}`}</div>
    ${col !== "done" ? `<div class="mt-act"><button class="btn sm" data-mtdone="${t.id}">Выполнено</button></div>` : ""}
  </article>`;
  page.innerHTML = `<div class="page-head"><h2 class="grow">Ручные задачи</h2>
      ${d.can_manage ? `<div class="seg"><button data-ms="mine" class="${d.scope === "mine" ? "active" : ""}">Мои</button><button data-ms="all" class="${d.scope === "all" ? "active" : ""}">Все сотрудники</button></div>` : ""}
      <button class="btn primary" id="mt-add">+ Задача</button></div>
    <div class="small muted" style="margin:-6px 0 12px">Задачи, которые поставили сотрудники. Каждая появляется здесь в свой день${d.later ? `. Запланировано на следующие дни: ${d.later} (видно в «Планере»)` : ""}. Нажмите на задачу, чтобы изменить или передать</div>
    <div id="mt-addbox"></div>
    <div class="board">${d.columns.map((c) => `<section class="col"><div class="col-head"><span>${c.title}</span><span class="cnt">${c.cards.length}</span></div>
      <div class="cards">${c.cards.map((t) => card(t, c.key)).join("") || `<div class="small muted" style="padding:8px">${c.key === "today" ? "На сегодня ручных задач нет" : c.key === "overdue" ? "Просроченных нет 👍" : "Пока пусто"}</div>`}</div></section>`).join("")}</div>`;
  page.querySelectorAll("[data-ms]").forEach((b) => (b.onclick = () => { S.manual.scope = b.dataset.ms; render(); }));
  $("#mt-add").onclick = () => openTaskForm($("#mt-addbox"), render);
  page.querySelectorAll("[data-mt]").forEach((el) => (el.onclick = (e) => {
    if (e.target.closest("button") || el.dataset.col === "done") return;
    if (["admin", "operator"].includes(S.user.role)) openTaskEdit(S.taskById[el.dataset.mt], render);
  }));
  page.querySelectorAll("[data-mtdone]").forEach((b) => (b.onclick = async () => {
    const result = prompt("Итог (необязательно)", "");
    if (result === null) return;
    try { await api("POST", `/api/tasks/${b.dataset.mtdone}/done`, { result }); toast("Задача выполнена"); render(); } catch (e) { fail(e); }
  }));
}

/* Правка задачи: что сделать, срок, исполнитель, заметка. Передать можно любому сотруднику, включая тренера */
const TASK_ROLE = { admin: "руководитель", operator: "администратор", coach: "тренер" };
function openTaskEdit(t, onDone, focus) {
  if (!t) return;
  const staff = ((S.meta && S.meta.staff) || []).slice().sort((a, b) => (a.role === "coach") - (b.role === "coach") || a.name.localeCompare(b.name));
  const wrap = document.createElement("div");
  wrap.className = "need-wrap";
  wrap.innerHTML = `<form class="need-card"><h3>${t.icon || "✅"} Задача</h3>
    ${t.student ? `<div class="small muted" style="margin-bottom:8px">${esc(t.student.name)}${t.student.family ? ` · семья ${esc(t.student.family)}` : ""}</div>` : ""}
    <div class="field"><label>Что сделать</label><input name="title" value="${esc(t.title)}" required maxlength="200"></div>
    <div class="row"><div class="field"><label>Срок</label><input name="due_at" type="datetime-local" value="${esc((t.due_at || "").slice(0, 16))}" required></div>
      <div class="field"><label>Исполнитель</label><select name="assignee_id">${staff.map((u) => `<option value="${u.id}" ${u.id === t.assignee_id ? "selected" : ""}>${esc(u.name)} (${TASK_ROLE[u.role] || u.role})</option>`).join("")}
        ${t.assignee_id && !staff.some((u) => u.id === t.assignee_id) ? `<option value="" selected disabled>${esc(t.assignee || "Сотрудник в архиве")}: выберите нового</option>` : ""}</select></div></div>
    <div class="field"><label>Заметка</label><textarea name="note" rows="2" maxlength="1000" placeholder="Что важно знать исполнителю">${esc(t.note || "")}</textarea></div>
    <div class="need-err small"></div>
    <div class="row" style="gap:8px;margin-top:6px;flex-wrap:wrap"><button class="btn primary" style="flex:1">Сохранить</button>
      ${t.kind === "rebook" ? `<button type="button" class="btn" data-rebook="${t.id}" data-tclose>Итог звонка</button>` : `<button type="button" class="btn" data-tfinish>Выполнено</button>`}
      <button type="button" class="btn" data-tcancelf style="flex:0 0 auto">Отмена</button></div>
    <div class="task-done" hidden style="margin-top:8px"><input placeholder="Итог: дозвонилась, подтвердили…" data-tresult><button type="button" class="btn sm primary" data-tok>Готово</button></div></form>`;
  document.body.appendChild(wrap);
  const f = wrap.querySelector("form"), close = () => wrap.remove();
  setTimeout(() => (focus === "assignee" ? f.elements.assignee_id : f.elements.title).focus(), 50);
  wrap.querySelector("[data-tcancelf]").onclick = close;
  wrap.addEventListener("click", (e) => { if (e.target === wrap) close(); });
  const cl = wrap.querySelector("[data-tclose]"); if (cl) cl.addEventListener("click", () => setTimeout(close, 0));
  const fin = wrap.querySelector("[data-tfinish]");
  if (fin) fin.onclick = () => { const b = wrap.querySelector(".task-done"); b.hidden = false; b.querySelector("input").focus(); };
  const ok = wrap.querySelector("[data-tok]");
  if (ok) ok.onclick = async () => {
    try { await api("POST", `/api/tasks/${t.id}/done`, { result: wrap.querySelector("[data-tresult]").value }); toast("Задача выполнена"); close(); onDone && onDone(); }
    catch (e) { wrap.querySelector(".need-err").textContent = e.message; }
  };
  f.onsubmit = async (e) => {
    e.preventDefault();
    const body = { title: f.elements.title.value, due_at: f.elements.due_at.value, note: f.elements.note.value };
    if (f.elements.assignee_id.value) body.assignee_id = +f.elements.assignee_id.value;
    try {
      const r = await api("PATCH", `/api/tasks/${t.id}`, body);
      toast(r.assignee_id !== t.assignee_id ? `Задача передана: ${r.assignee}` : "Задача сохранена");
      close(); onDone && onDone();
    } catch (err) { wrap.querySelector(".need-err").textContent = err.message; }
  };
}

/* Блок задач наверху карточки клиента: ближайшая задача видна сразу, остальные по кнопке, последние записи истории */
function cardTasksBlock(d) {
  const T = d.tasks || { open: [], done: [] };
  (T.open || []).concat(T.done || []).forEach((t) => { S.taskById = S.taskById || {}; S.taskById[t.id] = t; });
  const now = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  const row = (t, main) => `<div class="ct-row ${main ? "main" : ""} ${t.due_at < now ? "late" : ""}" data-ctask="${t.id}" role="button" tabindex="0" title="Нажмите, чтобы изменить или передать">
    <span class="ct-ico">${t.icon || "✅"}</span><div class="grow"><b>${esc(t.title)}</b> ${kindPill(t)}
    <div class="small muted">${t.due_at < now ? "⏰ просрочено, " : ""}${dtWhen(t.due_at)}${t.assignee ? ` · ${esc(t.assignee)}` : ""}${t.note ? ` · ${esc(t.note)}` : ""}</div></div>
    <span class="ct-edit">✎</span></div>`;
  const last = (d.history || []).slice(0, 2);
  return `<div class="ct-box">
    <div class="ct-head"><b>✅ Задачи</b>${T.open.length > 1 ? `<span class="small muted">открыто ${T.open.length}</span>` : ""}
      <button class="btn sm" data-ctadd style="margin-left:auto">+ Задача</button></div>
    ${T.open.length ? row(T.open[0], true) : `<div class="small muted">Открытых задач нет</div>`}
    ${T.open.length > 1 ? `<details class="ct-more"><summary class="small">Ещё ${T.open.length - 1}</summary>${T.open.slice(1).map((t) => row(t)).join("")}</details>` : ""}
    <div data-ctaddbox></div>
    ${last.length ? `<div class="ct-hist small">${last.map((h) => `<div><span class="muted">${dtShort(h.ts)} · ${esc(h.user_name || "")}</span> ${esc(h.text)}</div>`).join("")}
      <button class="linkish small" data-cthist>Вся история</button></div>` : ""}
  </div>`;
}
function wireCardTasks(root, reload, sid) {
  root.querySelectorAll("[data-ctask]").forEach((el) => {
    const open = () => {
      const t = S.taskById[el.dataset.ctask];
      if (t && ["single_remind", "single_next"].includes(t.kind) && typeof singleTaskDialog === "function") return singleTaskDialog(t, reload);
      if (["admin", "operator"].includes(S.user.role)) openTaskEdit(t, reload);
    };
    el.onclick = open; el.onkeydown = (e) => { if (e.key === "Enter") open(); };
  });
  const add = root.querySelector("[data-ctadd]");
  if (add) add.onclick = () => {
    const box = root.querySelector("[data-ctaddbox]");
    const shut = () => { box.innerHTML = ""; add.textContent = "+ Задача"; };
    if (box.innerHTML) return shut();
    add.textContent = "✕ Закрыть";
    const staff = ((S.meta && S.meta.staff) || []).slice().sort((a, b) => (a.role === "coach") - (b.role === "coach") || a.name.localeCompare(b.name));
    const soon = new Date(Date.now() + 3600e3 - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);
    box.innerHTML = `<div class="sub-form" style="margin-top:8px">
      <div class="field"><label>Что сделать</label><input data-ctt placeholder="Например: перезвонить после 18:00"></div>
      <div class="row"><div class="field"><label>Срок</label><input type="datetime-local" data-ctd value="${soon}"></div>
        <div class="field"><label>Исполнитель</label><select data-cta>${staff.map((u) => `<option value="${u.id}" ${u.id === S.user.id ? "selected" : ""}>${esc(u.name)} (${TASK_ROLE[u.role] || u.role})</option>`).join("")}</select></div></div>
      <div class="row" style="gap:8px"><button class="btn primary sm" data-cts style="flex:0 0 auto">Поставить задачу</button>
        <button class="btn sm" data-ctx style="flex:0 0 auto">Отмена</button></div></div>`;
    box.querySelector("[data-ctx]").onclick = shut;
    box.querySelector("[data-ctt]").focus();
    box.querySelector("[data-cts]").onclick = async () => {
      const title = box.querySelector("[data-ctt]").value.trim();
      if (!title) return toast("Напишите, что нужно сделать", true);
      try { await api("POST", `/api/students/${sid}/tasks`, { title, due: box.querySelector("[data-ctd]").value, assignee: +box.querySelector("[data-cta]").value }); toast("Задача поставлена"); reload(); }
      catch (e) { fail(e); }
    };
  };
  const h = root.querySelector("[data-cthist]");
  // история лежит в свёрнутом блоке «🕘 История и комментарии»: раскрываем его и прокручиваем к нему
  if (h) h.onclick = () => {
    const body = document.querySelector("#drawer-body") || document;
    if (body.querySelector('[data-fold="hist"]')) return openFold(body, "hist");
    const el = body.querySelector('[data-sc="hist"]'); if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  };
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
  // карточка «Сдать отчёт о смене» наверху, когда тренировки закончились; до этого внизу, под тренировками
  const shiftOver = !d.today.length || d.today.every((g) => lessonOver(d.date, g.time));
  const fact = (n, label, cls = "") => n ? `<div class="cfact ${cls}"><b>${n}</b><span>${label}</span></div>` : "";
  page.innerHTML = `${viewBar}
    <div class="page-head"><h2 class="grow hello-ava">${avatar((d.coach && d.coach.id) || S.user.id, (d.coach && d.coach.name) || S.user.name, 44)}<span>Привет, ${esc(first)}!</span></h2><span class="muted small">${dShort(d.date)}, ${wd(d.date)}</span></div>
    <div class="muted" style="margin:-6px 0 14px">${line}</div>
    ${(d.missing_marks || []).map((m) => `<div class="alert" style="margin-bottom:10px"><b>Срочно отметь детей:</b> ${esc(m.group)}, ${dShort(m.date)} ${esc(m.time)}. Без отметки: ${m.missing}
      <button class="btn sm primary" data-j="${m.group_id}" data-d="${m.date}" style="margin-left:8px">Отметить</button></div>`).join("")}
    ${d.today.length ? `<section class="now coach-sum"><div class="now-head"><h3>Сегодня ${d.today.length} ${d.today.length === 1 ? "тренировка" : d.today.length < 5 ? "тренировки" : "тренировок"}, ждём ${t.total} ${kidsWord(t.total)}</h3></div>
      <div class="cfacts">${fact(t.paid, "по абонементу", "green")}${fact(t.trials, "на пробном", "blue")}${fact(t.single, "разовое")}${fact(t.makeups, "на отработке")}${fact(t.unpaid, "без оплаты", "yellow")}${fact(t.vacation, "в отпуске, не придут")}</div></section>` : ""}
    ${checkinBox(d, !!coachId)}
    ${coachId || !shiftOver ? "" : `<div id="rep-cards"></div>`}
    ${(d.tasks || []).length ? `<section class="now tasks-box" style="margin-bottom:12px"><div class="now-head"><h3>📌 Задачи на сегодня: ${d.tasks.length}</h3><span class="small muted">По каждой отчитаешься в отчёте о смене</span></div>${d.tasks.map(taskRow).join("")}</section>` : ""}
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
          ${all ? `<button class="btn sm" data-j="${g.id}" data-d="${d.date}" style="flex:0">Журнал</button>` : ""}</div>
        ${all && done < all ? `<button class="btn primary meet-open ${lessonOver(d.date, g.time) ? "late" : ""}" data-meet="${g.id}" data-d="${d.date}">${meetLabel(d.date, g.time, lessonOver(d.date, g.time))}</button>` : ""}
      </div>`; }).join("")}</div>` : `<div class="panel center" style="padding:30px">Сегодня тренировок нет. Отдыхай 😎</div>`}
    ${up.covering.length || up.covered.length ? `<section class="now calm" style="margin-top:16px"><div class="now-head"><h3>Замены на ближайшие 2 недели</h3></div>
      ${up.covering.map((x) => `<div class="small">🔁 ${dShort(x.date)}, ${wd(x.date)}, ${esc(x.time || "")}: <b>ты выходишь</b> в ${esc(x.group_name)} (${esc(x.branch)}) вместо ${esc(x.own_name || "другого тренера")}</div>`).join("")}
      ${up.covered.map((x) => `<div class="small muted">↪ ${dShort(x.date)}, ${wd(x.date)}, ${esc(x.time || "")}: ${esc(x.group_name)} ведёт ${esc(x.sub_name)}, у тебя выходной</div>`).join("")}</section>` : ""}
    ${coachId || shiftOver ? "" : `<div id="rep-cards" style="margin-top:14px"></div>`}
    ${merchShiftBox(d, !!coachId)}
    ${coachId ? "" : `<div style="margin-top:12px">${envelopeButton()}</div>`}
    ${coachId ? "" : `<details class="panel" id="my-pay" style="margin-top:14px"><summary><b>💰 Моя зарплата за месяц</b></summary><div class="my-pay-body small muted">Считаю…</div></details>`}
    <div class="small muted" style="margin-top:14px">Отмечай детей сразу после тренировки: от этого зависят отработки, перерасчёт абонементов и твоё место в таблице тренеров</div>`;
  page.querySelectorAll("[data-j]").forEach((b) => (b.onclick = () => { S.journal.group = +b.dataset.j; S.journal.date = b.dataset.d; go("journal"); }));
  page.querySelectorAll("[data-meet]").forEach((b) => (b.onclick = () => openMeet(+b.dataset.meet, b.dataset.d)));
  wireCheckin(page);
  if (!coachId) wireTasks(page);
  if ($("#rep-cards")) coachReportCards($("#rep-cards"));
  wireMerchShift(page, d);
  wireEnvelope(page, ((d.checkins || [])[0] || {}).branch_id);
  d.today.forEach((g) => wireStaffPicker(page, "c" + g.id, g.id, d.date, render));
  const mp = page.querySelector("#my-pay");
  if (mp) mp.ontoggle = async () => {
    if (!mp.open || mp.dataset.done) return;
    mp.dataset.done = "1";
    try { payBox(mp.querySelector(".my-pay-body"), await api("GET", "/api/salary"), true); } catch (e) { mp.querySelector(".my-pay-body").textContent = e.message; }
  };
}

/* ---------- настройки задач (админ) ---------- */
async function renderTaskSettings(box, users) {
  const d = await api("GET", "/api/tasks/settings");
  const v = d.values;
  const staff = users.filter((u) => u.role !== "coach" && u.active);
  const yn = (k) => `<select data-t="${k}"><option value="1" ${v[k] === "1" ? "selected" : ""}>Отправлять</option><option value="0" ${v[k] !== "1" ? "selected" : ""}>Не отправлять</option></select>`;
  box.innerHTML = `
    <h3>Скрипты: сообщения родителям и автосообщения</h3>
    <div class="alert ${d.auto_live ? "" : "red"}" style="margin-bottom:12px;display:flex;gap:12px;align-items:center;flex-wrap:wrap">
      <div class="grow"><b>${d.auto_live ? "✅ Автосообщения клиентам включены" : "⛔ Автосообщения клиентам выключены"}</b>
        <div class="small">${d.auto_live ? "CRM сама пишет родителям: после записи на пробное, напоминания, после пробного, автоцепочки, автоответ бота" : "Клиентам автоматически ничего не уходит: ни напоминания, ни сообщения после записи, ни автоцепочки, ни автоответ бота. Сотрудники пишут сами"}</div></div>
      <button class="btn sm ${d.auto_live ? "" : "primary"}" id="auto-live">${d.auto_live ? "Выключить" : "Запустить"}</button></div>
    <div class="field"><label>Кто отвечает за задачи по пробным</label>
      <select data-t="task_owner_trials">${staff.map((u) => `<option value="${u.id}" ${String(u.id) === v.task_owner_trials ? "selected" : ""}>${esc(u.name)}</option>`).join("")}</select></div>
    <div class="small muted" style="margin:-4px 0 12px">На этого сотрудника встают все задачи по пробным, и при переносе из ERP задачи со статусами: ${d.erp_statuses.map((x) => `«${esc(x)}»`).join(", ")}</div>
    <div class="bk-places">
      <div class="bk-head"><b>💬 Чат с администратором</b>
        <div class="small muted">В любом тексте ниже напишите {admin}: CRM подставит ссылку под мессенджер родителя (в MAX ссылку MAX, в остальных Telegram)</div></div>
      <div class="row"><div class="field"><label>В Telegram</label><input data-t="admin_tg" value="${esc(v.admin_tg)}" placeholder="@footbolika_admin"></div>
        <div class="field"><label>В MAX</label><input data-t="admin_max" value="${esc(v.admin_max)}" placeholder="https://max.ru/u/..."></div></div></div>
    <div class="field"><label>Пропущенный звонок: через 2 минуты, если не перезвонили, клиенту в мессенджер (9:00-21:00, раз в день на номер)</label>${yn("auto_missed")}</div>
    <div class="field"><label>Текст после пропущенного звонка. {hello}: «Анна, здравствуйте!» или «Здравствуйте!»</label><textarea data-t="msg_missed" rows="3">${esc(v.msg_missed)}</textarea></div>
    <div class="bk-places" style="margin-bottom:12px">
      <div class="bk-head"><b>⚽ Напоминание перед каждым занятием</b>
        <div class="small muted">Тем, кто ходит по абонементу, на разовые и отработки. У пробных своя цепочка (ниже). Одно сообщение на семью. Не пишем предупредившим, что не придут, в отпуске и не оплатившим</div></div>
      <div class="row" style="margin-top:8px"><div class="field"><label>Напоминание перед занятием</label>${yn("auto_lesson")}</div>
        <div class="field" style="flex:0 0 160px"><label>За сколько часов</label><input data-t="lesson_hours_before" type="number" min="1" max="12" value="${esc(v.lesson_hours_before)}"></div></div>
      <div class="field"><label>Текст. {parent}: «, Анна» (имя родителя), {date}, {time}, {place}: место филиала</label><textarea data-t="msg_lesson" rows="9">${esc(v.msg_lesson || "")}</textarea></div></div>
    <div class="field"><label>Сразу после записи на пробное (дата, время, место, что взять)</label>${yn("auto_booked")}</div>
    <div class="field"><label>Текст после записи. {date_time}: «Понедельник, 2 ноября, в 17:30», {place}: место филиала, {chat_line}: строка с чатом родителей</label><textarea data-t="msg_booked" rows="9">${esc(v.msg_booked)}</textarea></div>
    <div class="bk-places">
      <div class="bk-head"><b>📍 Место и чат родителей по филиалам</b>
        <div class="small muted">Это подставляется в сообщение «Записан на пробное»: где проходит занятие и ссылка на чат родителей филиала</div></div>
      ${Object.entries(d.booked_places).map(([id, p]) => `<div class="bk-place" data-bk="${id}" style="margin-top:10px">
        <div class="small"><b>${esc(p.name)}</b></div>
        <div class="field"><label>Где проходит и как найти</label><input data-bf="place" value="${esc(p.place)}"></div>
        <div class="row"><div class="field" style="flex:0 0 170px"><label>Район (в Солнцево…)</label><input data-bf="area" value="${esc(p.area)}"></div>
          <div class="field"><label>Чат родителей в Telegram</label><input data-bf="tg" value="${esc(p.tg)}" placeholder="https://t.me/..."></div>
          <div class="field"><label>Чат родителей в MAX</label><input data-bf="max" value="${esc(p.max)}" placeholder="Если пусто, в MAX строки с чатом не будет"></div></div>
      </div>`).join("")}</div>
    <div class="row">
      <div class="field"><label>Напоминание за день</label>${yn("auto_day_before")}</div>
      <div class="field" style="flex:0 0 120px"><label>Во сколько, ч</label><input data-t="day_before_hour" type="number" min="8" max="21" value="${esc(v.day_before_hour)}"></div>
    </div>
    <div class="field"><label>Текст за день</label><textarea data-t="msg_day_before" rows="3">${esc(v.msg_day_before)}</textarea></div>
    <div class="field"><label>Напоминание за 2 часа</label>${yn("auto_2h_before")}</div>
    <div class="field"><label>Текст за 2 часа</label><textarea data-t="msg_2h_before" rows="3">${esc(v.msg_2h_before)}</textarea></div>
    <div class="field"><label>Через 10 минут после пробного</label>${yn("auto_after")}</div>
    <div class="field"><label>Текст после пробного</label><textarea data-t="msg_after" rows="2">${esc(v.msg_after)}</textarea></div>
    <div class="field"><label>Предложение ближайших пробных (кнопка «📅 Предложить ближайшие ПУ» в переписке). {slots}: список дат</label><textarea data-t="msg_trial_offer" rows="4">${esc(v.msg_trial_offer || "")}</textarea></div>
    <div class="small muted" style="margin-bottom:10px">В текст подставятся: {child} имя ребёнка, {date} дата, {time} время, {place} филиал, {admin} чат с администратором. Сообщение уходит туда, где семья уже переписывалась со школой (Telegram, MAX или WhatsApp). Если переписки нет, в задаче будет видно «не ушло», и Оля напишет вручную</div>
    <button class="btn primary" id="t-save">Сохранить</button>`;
  $("#auto-live").onclick = async () => {
    if (!d.auto_live && !confirm("Запустить автосообщения клиентам? С этой минуты CRM сама будет писать родителям")) return;
    try { await api("PUT", "/api/auto-live", { on: !d.auto_live }); renderTaskSettings(box, users); } catch (e) { fail(e); }
  };
  $("#t-save").onclick = async () => {
    const body = Object.fromEntries([...box.querySelectorAll("[data-t]")].map((i) => [i.dataset.t, i.value]));
    body.booked_places = Object.fromEntries([...box.querySelectorAll("[data-bk]")].map((el) => [el.dataset.bk,
      Object.fromEntries([...el.querySelectorAll("[data-bf]")].map((i) => [i.dataset.bf, i.value]))]));
    try { await api("PUT", "/api/tasks/settings", body); toast("Сохранено"); }
    catch (e) { fail(e); }
  };
}
