/* «📋 Задачи» на рабочем столе, как в ERP (Юлия, 03.11): общий список для всех администраторов,
   колонки по статусам ERP, отдельно Просрочено / Сегодня / Завтра. Новые виды задач CRM в колонке «Другое»,
   ручные в «Ручных». «Выполнено» ставит следующую задачу по цепочке */
S.etDay = S.etDay || "today";

async function erpTasksSection() {
  let d;
  try { d = await api("GET", "/api/tasks/erp-board?day=" + S.etDay); } catch (_) { return ""; }
  S.taskById = S.taskById || {};
  d.columns.forEach((c) => c.cards.forEach((t) => (S.taskById[t.id] = t)));
  const tabs = [["overdue", "Просрочено"], ["today", "Сегодня"], ["tomorrow", "Завтра"]];
  const when = (t) => {
    const hm = (t.due_at || "").slice(11, 16);
    const tm = hm === "23:59" ? "в течение дня" : hm;
    return d.day === "overdue" ? `⏰ было на ${dShort(t.due_at)}${tm ? ", " + tm : ""}` : `⏰ ${tm}`;
  };
  const card = (t, col) => {
    const s = t.student || {};
    return `<article class="et-card ${d.day === "overdue" ? "late" : ""}" data-et="${t.id}" ${s.id ? `data-etopen="${s.id}" role="button" tabindex="0" title="Открыть карточку"` : ""}>
      ${["other", "manual"].includes(col) || col.startsWith("erp:") ? `<div class="et-what">${t.icon || "✅"} ${esc(t.title)}</div>` : ""}
      ${s.name ? `<div class="et-name">${esc(s.name)}</div>` : ""}
      ${s.parent ? `<div class="et-meta">${esc(s.parent)}</div>` : ""}${s.phone ? `<div class="et-meta et-phone">${esc(s.phone)}</div>` : ""}
      ${t.note ? `<div class="et-meta et-note">${esc(t.note)}</div>` : ""}
      <div class="et-meta ${d.day === "overdue" ? "et-late" : ""}">${when(t)}</div>
      <div class="et-act">
        <button class="btn sm primary" data-etdone="${t.id}">✓ Готово</button>
        <button class="btn sm" data-etmove="${t.id}" title="Перенести, передать, поправить">Перенести</button></div>
    </article>`;
  };
  const empty = { overdue: "Просроченных задач нет 👍", today: "На сегодня задач нет", tomorrow: "На завтра задач пока нет" }[d.day];
  return `<section class="et-box" id="dw-erptasks">
    <div class="now-head"><h3>📋 Задачи</h3>
      <div class="seg et-tabs">${tabs.map(([k, l]) => `<button data-etday="${k}" class="${d.day === k ? "active" : ""} ${k === "overdue" && d.counts.overdue ? "et-red" : ""}">${l} <b>${d.counts[k]}</b></button>`).join("")}</div>
      <button class="btn sm" id="et-add" style="margin-left:auto">+ Ручная задача</button></div>
    <div id="et-addbox"></div>
    ${d.columns.length ? `<div class="et-board">${d.columns.map((c) => `<section class="et-col ${c.key === "other" ? "et-other" : ""}">
        <div class="et-head"><span>${esc(c.title)}</span><span class="cnt">${c.cards.length}</span></div>
        <div class="et-cards">${c.cards.map((t) => card(t, c.key)).join("")}</div></section>`).join("")}</div>`
      : `<div class="muted small" style="padding:6px 2px">${empty}</div>`}
  </section>`;
}

function wireErpTasks(page) {
  const box = page.querySelector("#dw-erptasks");
  if (!box) return;
  box.querySelectorAll("[data-etday]").forEach((b) => (b.onclick = () => { S.etDay = b.dataset.etday; render(); }));
  const add = box.querySelector("#et-add");
  if (add) add.onclick = () => openTaskForm(box.querySelector("#et-addbox"), render);
  box.querySelectorAll("[data-etopen]").forEach((b) => (b.onclick = (e) => { if (!e.target.closest("button")) openStudent(+b.dataset.etopen); }));
  box.querySelectorAll("[data-etmove]").forEach((b) => (b.onclick = () => openTaskEdit(S.taskById[b.dataset.etmove], render)));
  box.querySelectorAll("[data-etdone]").forEach((b) => (b.onclick = async () => {
    const t = S.taskById[b.dataset.etdone];
    if (t && t.kind === "rebook" && typeof openRebook === "function") return openRebook(t.id);
    const result = window.prompt("Итог (необязательно): дозвонилась, подтвердили…", "");
    if (result === null) return;
    try { await api("POST", `/api/tasks/${t.id}/done`, { result }); toast("Задача выполнена"); render(); } catch (e) { fail(e); }
  }));
}
