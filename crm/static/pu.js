/* «Не дошёл до ПУ»: итог звонка по задаче, отмена записи, красные флаги по отметкам */

document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-rebook]");
  if (b) { e.preventDefault(); e.stopPropagation(); openRebook(+b.dataset.rebook); }
});

async function openRebook(tid) {
  let d;
  try { d = await api("GET", `/api/pu/task/${tid}`); } catch (e) { return fail(e); }
  const t = d.task, groups = S.meta.groups.filter((g) => !g.is_makeup && !g.program_id && g.trial_ok !== 0);
  const kidHtml = (k) => {
    const n = k.misses.length, last = k.misses[0];
    return `<div class="rb-kid"><b>${esc(k.name)}</b> <button class="linkish small" data-open="${k.id}">карточка</button>
      ${last ? `<div class="small muted">Пропущено ПУ ${dShort(last.trial_at.slice(0, 10))} ${last.trial_at.slice(11, 16)}, ${esc(last.group_name || "")}: ${esc(last.kind_label)}${last.reason ? ", " + esc(last.reason) : ""}</div>` : ""}
      ${n >= 3 ? `<div class="alert" style="margin-top:6px">Не доходит уже ${n}-й раз. Перед новой записью подтвердите звонком, что точно придут</div>` : n ? `<div class="small muted">Всего не доходил: ${n}</div>` : ""}
      <div class="row rb-new" data-kid="${k.id}" style="margin-top:6px;flex-wrap:wrap">
        <div class="field"><label>Группа</label><select data-rg>${groups.map((g) => `<option value="${g.id}" ${g.id === k.group_id ? "selected" : ""}>${esc(g.name)}</option>`).join("")}</select></div>
        <div class="field"><label>Новая дата и время ПУ</label><input type="datetime-local" data-rt></div></div>
      <div data-rslots="${k.id}"></div></div>`;
  };
  openDrawer(`<div class="small muted">Задача${t.attempt ? ` · недозвонов подряд: ${t.attempt}` : ""} · срок ${dtWhen(t.due_at)}</div>
    <h2>${esc(t.title)}</h2>${d.family ? `<div class="muted">Семья ${esc(d.family)}</div>` : ""}
    <div class="section">${d.kids.map(kidHtml).join("")}</div>
    <div class="section"><h3>Итог</h3>
      <div class="seg rb-tabs" style="flex-wrap:wrap">${[["rebooked", "Перезаписан на ПУ"], ["no_answer", "Не дозвонились"], ["later", "Перезвонить позже"], ["refused", "Отказ"]].map(([k, v], i) => `<button data-rtab="${k}" class="${i ? "" : "active"}">${v}</button>`).join("")}</div>
      <div data-rpane="rebooked" style="margin-top:10px"><div class="small muted">Выберите новую дату и группу выше. Дальше пойдут обычные напоминания</div></div>
      <div data-rpane="no_answer" hidden style="margin-top:10px"><div class="small muted">${["Следующая попытка через 2 часа: позвонить и написать ещё раз", "Следующая попытка завтра", "Следующая попытка через 7 дней", "Следующая попытка через 14 дней", "Следующая попытка через месяц"][t.attempt || 0] || "Следующая попытка в ближайшее окно набора (август или январь)"}</div></div>
      <div data-rpane="later" hidden style="margin-top:10px"><div class="row"><div class="field"><label>Когда перезвонить (не позже ${dShort(d.max_later)})</label><input type="datetime-local" id="rb-later" max="${d.max_later}T21:00"></div>
        <div class="field"><label>Комментарий</label><input id="rb-lnote" placeholder="Например: после отпуска"></div></div></div>
      <div data-rpane="refused" hidden style="margin-top:10px"><div class="row"><div class="field"><label>Причина</label><select id="rb-reason">${d.refusals.map((r) => `<option>${esc(r)}</option>`).join("")}</select></div>
        <div class="field"><label>Комментарий</label><input id="rb-comment" placeholder="Обязательно, если «Другое»"></div></div>
        <div class="small muted" id="rb-rhint"></div></div>
      <button class="btn primary" id="rb-save" style="margin-top:12px">Сохранить итог</button></div>
    <div class="section"><h3>Передать задачу</h3><div class="row"><select id="rb-to">${d.staff.map((u) => `<option value="${u.id}" ${u.id === t.assignee_id ? "selected" : ""}>${esc(u.name)}</option>`).join("")}</select>
      <button class="btn sm" id="rb-pass" style="flex:0">Передать</button></div></div>`);
  const body = $("#drawer-body");
  let tab = "rebooked";
  body.querySelectorAll("[data-rtab]").forEach((b) => (b.onclick = () => {
    tab = b.dataset.rtab;
    body.querySelectorAll("[data-rtab]").forEach((x) => x.classList.toggle("active", x === b));
    body.querySelectorAll("[data-rpane]").forEach((p) => (p.hidden = p.dataset.rpane !== tab));
    body.querySelectorAll(".rb-new,[data-rslots]").forEach((x) => (x.style.display = tab === "rebooked" ? "" : "none"));
  }));
  const hint = () => { const r = $("#rb-reason").value; $("#rb-rhint").textContent = r === "Попросили не звонить" ? "Карточка уйдёт в архив, больше не звоним и не пишем. Все данные сохранятся" : "Ребёнок уйдёт в «Пригласим позже»: CRM сама напомнит пригласить его через 3 месяца или в августе/январе"; };
  $("#rb-reason").onchange = hint; hint();
  d.kids.forEach((k) => loadSlots(body.querySelector(`[data-rslots="${k.id}"]`), (x) => {
    const row = body.querySelector(`[data-kid="${k.id}"]`);
    row.querySelector("[data-rg]").value = x.group_id; row.querySelector("[data-rt]").value = `${x.date}T${x.time || "18:00"}`;
    toast(`Выбрано: ${dayLabel(x.date)} ${x.time}`);
  }, "Свободные места (нажмите, чтобы подставить):", k.birth));
  body.querySelectorAll("[data-open]").forEach((b) => (b.onclick = () => openStudent(+b.dataset.open)));
  $("#rb-save").onclick = async () => {
    const data = { result: tab };
    if (tab === "rebooked") data.kids = [...body.querySelectorAll(".rb-new")].map((r) => ({ student_id: +r.dataset.kid, group_id: +r.querySelector("[data-rg]").value, trial_at: r.querySelector("[data-rt]").value })).filter((k) => k.trial_at);
    if (tab === "later") { data.date = $("#rb-later").value; data.note = $("#rb-lnote").value; }
    if (tab === "refused") { data.reason = $("#rb-reason").value; data.comment = $("#rb-comment").value; }
    try { const r = await api("POST", `/api/tasks/${tid}/rebook`, data); toast(r.label ? `Следующая попытка ${r.label}` : r.again ? `Пригласим снова ${dShort(r.again.slice(0, 10))}` : "Сохранено"); closeDrawer(); render(); } catch (e) { fail(e); }
  };
  $("#rb-pass").onclick = async () => { try { await api("POST", `/api/tasks/${tid}/assign`, { user_id: +$("#rb-to").value }); toast("Задача передана"); closeDrawer(); render(); } catch (e) { fail(e); } };
}

/* блок в карточке лида */
function puCardBlock(d) {
  const s = d.student, p = d.pu || {};
  const canCancel = s.funnel === "sales" && s.trial_at && ["trial_booked", "trial_reminder"].includes(s.stage);
  if (!canCancel && !p.count && !p.task) return "";
  return `<div class="section pu-box"><h3>Пробное занятие</h3>
    ${p.count >= 3 ? `<div class="alert">Не доходит до ПУ уже ${p.count}-й раз. Перед записью подтвердите звонком</div>` : ""}
    ${p.misses && p.misses.length ? `<ul class="history">${p.misses.map((m) => `<li>${dShort(m.trial_at.slice(0, 10))} ${m.trial_at.slice(11, 16)}, ${esc(m.group_name || "")}: <b>${esc(m.kind_label)}</b>${m.reason ? ", " + esc(m.reason) : ""}</li>`).join("")}</ul>` : ""}
    ${p.task ? `<div class="row" style="align-items:center;margin-top:8px"><div class="grow small">Задача «${esc(p.task.title)}», срок ${dtWhen(p.task.due_at)}</div><button class="btn sm primary" data-rebook="${p.task.id}" style="flex:0">Итог звонка</button></div>` : ""}
    ${canCancel ? `<div class="row" style="margin-top:8px"><input id="pu-reason" placeholder="Причина отмены: заболел, уехали…"><button class="btn sm danger" id="pu-cancel" style="flex:0">Отменить запись на ПУ</button></div>` : ""}
  </div>`;
}

function wirePuCard(body, sid, reload) {
  const b = body.querySelector("#pu-cancel");
  if (b) b.onclick = async () => {
    const r = body.querySelector("#pu-reason").value.trim();
    if (!r) return toast("Напишите причину отмены", true);
    try { await api("POST", `/api/students/${sid}/cancel-trial`, { reason: r }); toast("Запись отменена: ребёнок на этапе «Не дошёл до ПУ», задача перезаписать сегодня"); reload(); } catch (e) { fail(e); }
  };
}

/* стол админа: красные флаги */
function marksBox(d) {
  const m = d.missing_marks || [], al = (d.alerts || []).filter((a) => a.kind !== "no_marks");
  if (!m.length && !al.length) return "";
  return `<section class="now red-box" style="margin-bottom:14px">
    ${m.length ? `<div class="now-head"><h3>🔴 Нет отметок в журнале: ${m.length}</h3></div>
      ${m.map((x) => `<div class="now-item red"><span class="ref-card red"></span><div class="grow"><b>${esc(x.group)}</b> <span class="small">${esc(x.branch)} · ${dShort(x.date)} ${esc(x.time)} · тренер ${esc(x.coach || "—")}</span>
        <div class="small">Без отметки: ${x.missing} · прошло ${x.minutes >= 120 ? Math.floor(x.minutes / 60) + " ч" : x.minutes + " мин"} после тренировки${x.minutes >= 120 ? " · руководитель уведомлён, пробные ушли на «Не дошёл до ПУ»" : ""}</div></div>
        <div class="now-act"><button class="btn sm" data-j="${x.group_id}" data-d="${x.date}">Журнал</button></div></div>`).join("")}` : ""}
    ${al.map((a) => `<div class="now-item red"><span class="ref-card red"></span><div class="grow small">${esc(a.text)} <span class="muted">${dtWhen(a.created_at)}</span></div>
      <div class="now-act">${a.student_id ? `<button class="btn sm" data-open-st="${a.student_id}">Карточка</button>` : ""}<button class="btn sm" data-alert="${a.id}">Понятно</button></div></div>`).join("")}
  </section>`;
}

function wireMarks(page) {
  page.querySelectorAll(".red-box [data-j]").forEach((b) => (b.onclick = () => step(() => { S.view = "journal"; S.journal = { group: +b.dataset.j, date: b.dataset.d }; })));
  page.querySelectorAll("[data-open-st]").forEach((b) => (b.onclick = () => openStudent(+b.dataset.openSt)));
  page.querySelectorAll("[data-alert]").forEach((b) => (b.onclick = async () => { try { await api("POST", `/api/alerts/${b.dataset.alert}/close`, {}); render(); } catch (e) { fail(e); } }));
}

/* Настройки: ответственные по филиалам и рабочие чаты */
async function renderResponsibles(box) {
  const d = await api("GET", "/api/responsibles");
  box.innerHTML = `<h3>Ответственные по филиалам</h3>
    <div class="small muted">На них встают задачи «Перезаписать на ПУ». Видят и могут взять задачу все админы и операторы</div>
    ${d.items.map((x) => `<div class="row" style="align-items:center;margin-top:8px"><div class="grow">${esc(x.branch)}</div>
      <select data-resp="${x.branch_id}" style="flex:0 0 220px"><option value="">— ответственный за пробные —</option>${d.staff.map((u) => `<option value="${u.id}" ${u.id === x.user_id ? "selected" : ""}>${esc(u.name)}</option>`).join("")}</select></div>`).join("")}
    <div class="small" style="margin-top:12px"><b>Рабочие чаты в Telegram.</b> Чтобы CRM писала в чаты «ТРЕНЕРЫ» и «Служба заботы» о неотмеченных тренировках:
      добавьте бота школы в чат и отправьте там команду <code>/crm_coaches</code> (в чате тренеров) или <code>/crm_care</code> (в Службе заботы).
      Ники сотрудников для отметок впишите в списке «Сотрудники» ниже</div>`;
  box.querySelectorAll("[data-resp]").forEach((s) => (s.onchange = async () => {
    try { await api("POST", "/api/responsibles", { branch_id: +s.dataset.resp, user_id: s.value ? +s.value : null }); toast("Сохранено"); } catch (e) { fail(e); }
  }));
}

/* Дашборд: вкладка «ПУ и отметки» */
async function anPu(box, month) {
  const d = await api("GET", `/api/pu/report?month=${month}${S.dashBranch ? "&branch_id=" + S.dashBranch : ""}`);
  const k = d.by_kind;
  box.innerHTML = `<div class="kpis">
      <div class="kpi"><div class="kpi-head"><span>Записано на ПУ</span></div><div class="kpi-value">${d.booked}</div><div class="kpi-note">пришли ${d.came} · явка ${anPct(d.attendance)}</div></div>
      <div class="kpi"><div class="kpi-head"><span>Не дошли</span></div><div class="kpi-value">${d.missed}</div><div class="kpi-note">отменили заранее ${k.cancel} · не пришли ${k.no_show} · нет отметки ${k.no_mark}</div></div>
      <div class="kpi"><div class="kpi-head"><span>Из не дошедших</span></div><div class="kpi-value">${d.rebooked}</div><div class="kpi-note">перезаписаны · потом пришли ${d.came_after} · купили ${d.bought}</div></div>
    </div>
    <div class="an-two">
      <section class="panel"><div class="panel-head">Отказы после пропуска ПУ</div>${Object.entries(d.refused).map(([r, n]) => `<div class="an-kv"><span>${esc(r)}</span><b>${n}</b></div>`).join("") || `<div class="small muted">Нет</div>`}
        <div class="panel-head" style="margin-top:12px">По группам</div>${Object.entries(d.by_group).map(([r, n]) => `<div class="an-kv"><span>${esc(r)}</span><b>${n}</b></div>`).join("") || `<div class="small muted">Нет</div>`}
        <div class="panel-head" style="margin-top:12px">По ответственным</div>${Object.entries(d.by_resp).map(([r, x]) => `<div class="an-kv"><span>${esc(r)}</span><b>${x.tasks} задач${x.overdue ? `, просрочено ${x.overdue}` : ""}</b></div>`).join("") || `<div class="small muted">Нет</div>`}</section>
      <section class="panel"><div class="panel-head">Без задачи или задача просрочена · ${d.stuck.length}</div>
        <div class="list" style="box-shadow:none">${d.stuck.map((x) => `<div class="list-row click" data-sid="${x.id}"><div class="grow">${esc(x.name)}<div class="small" style="color:#b42318">${esc(x.problem)}${x.assignee ? " · " + esc(x.assignee) : ""}</div></div></div>`).join("") || `<div class="empty">Все дети под контролем</div>`}</div></section>
    </div>
    <section class="panel" style="margin-top:14px"><div class="panel-head">Отметки в журнале: тренеры и филиалы</div>
      <div class="an-scroll"><table class="tbl an-tbl"><thead><tr><th>Тренер</th><th>Филиал</th><th>Вовремя (до 30 мин)</th><th>С опозданием</th><th>Не отмечено</th></tr></thead>
      <tbody>${d.marks.rows.map((r) => `<tr><td><b>${esc(r.coach)}</b></td><td>${esc(r.branch)}</td><td class="s-good">${r.on_time}</td><td class="${r.late ? "s-mid" : ""}">${r.late}</td><td class="${r.missing ? "s-bad" : ""}">${r.missing}</td></tr>`).join("") || `<tr><td colspan="5" class="muted">Тренировок пока нет</td></tr>`}</tbody></table></div></section>`;
  box.querySelectorAll("[data-sid]").forEach((el) => (el.onclick = () => openStudent(+el.dataset.sid)));
}
