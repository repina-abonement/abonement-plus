/* Разовые занятия (Юлия, 03.11): запись на дату в карточке, задача накануне в 10:00 с готовым текстом,
   после задачи выбор следующей даты: календарь, «пока не знаем» (спросить через 7 дней) или ближайший набор */
function singleDates(g, n = 28) {
  const wd = String(g.weekdays || "").split(",").filter((x) => x !== "").map(Number), out = [];
  const t = new Date(S.meta.today + "T12:00");
  for (let i = 0; i <= n; i++) { const x = new Date(t); x.setDate(t.getDate() + i); if (wd.includes((x.getDay() + 6) % 7)) out.push(isoDate(x)); }
  return out;
}
const singleGroups = () => (S.meta.groups || []).filter((g) => !g.is_makeup && g.active !== 0);

function singleForm(prefix, groupId) {
  const gs = singleGroups();
  const g0 = gs.find((g) => g.id === groupId) || gs[0];
  return `<div class="row" style="flex-wrap:wrap;align-items:flex-end;gap:8px">
    <div class="field"><label>Группа</label><select id="${prefix}-g">${gs.map((g) => `<option value="${g.id}" ${g === g0 ? "selected" : ""}>${esc(g.branch)} · ${esc(groupNoBranch(g))}</option>`).join("")}</select></div>
    <div class="field" style="flex:0 0 170px"><label>Дата</label><select id="${prefix}-d"></select></div></div>`;
}
function wireSingleForm(prefix) {
  const gSel = $(`#${prefix}-g`), dSel = $(`#${prefix}-d`);
  const fill = () => {
    const g = singleGroups().find((x) => x.id === +gSel.value);
    dSel.innerHTML = singleDates(g || {}).map((d) => `<option value="${d}">${dShort(d)} ${WD[(new Date(d + "T12:00").getDay() + 6) % 7]}</option>`).join("") || `<option value="">нет занятий</option>`;
  };
  gSel.onchange = fill; fill();
  return () => ({ group_id: +gSel.value, date: dSel.value });
}

function singlesBlock(d) {
  const v = d.singles || [];
  const up = v.filter((x) => x.date >= S.meta.today).reverse(), past = v.filter((x) => x.date < S.meta.today);
  const row = (x) => `<li><b>${dShort(x.date)}</b> ${x.time ? esc(x.time) + " · " : ""}${esc(groupNoBranch({ name: x.group_name, branch: x.branch }))}
    ${x.date < S.meta.today ? `<span class="pill ${x.came === "present" ? "green" : x.came === "absent" ? "red" : ""}">${x.came === "present" ? "был" : x.came === "absent" ? "не пришёл" : "не отмечен"}</span>`
      : `<button class="btn sm" data-sgcancel="${x.id}">Отменить</button>`}</li>`;
  return `<div class="cm-box" id="sg-box">
    <div class="small"><b>1️⃣ Разовые занятия</b>${d.single_now ? ` <span class="pill">сейчас ходит на разовые</span>` : ""}</div>
    ${up.length ? `<ul class="history visits" style="margin-top:6px">${up.map(row).join("")}</ul>` : ""}
    ${past.length ? `<details class="small" style="margin-top:4px"><summary class="muted">Прошедшие: ${past.length}</summary><ul class="history visits">${past.map(row).join("")}</ul></details>` : ""}
    <div id="sg-form" hidden style="margin-top:8px">${singleForm("sg", (d.enrollments[0] || {}).group_id || (d.student || {}).group_id)}
      <button class="btn primary sm" id="sg-save">Записать</button> <button class="btn sm" id="sg-x">Отмена</button>
      <div class="small muted" style="margin-top:4px">Накануне в 10:00 администратору придёт задача с готовым текстом для родителя</div></div>
    <button class="btn sm" id="sg-add" style="margin-top:8px">+ Записать на разовое</button></div>`;
}
function wireSingles(sid, reload) {
  if (!$("#sg-box")) return;
  let get = null;
  $("#sg-add").onclick = () => { $("#sg-form").hidden = false; $("#sg-add").hidden = true; get = get || wireSingleForm("sg"); };
  $("#sg-x").onclick = () => { $("#sg-form").hidden = true; $("#sg-add").hidden = false; };
  $("#sg-save").onclick = async () => {
    const b = get();
    if (!b.date) return toast("У группы нет занятий в ближайшие 4 недели", true);
    try { await api("POST", `/api/students/${sid}/singles`, b); toast(`Записан на разовое ${dShort(b.date)}`); reload(); } catch (e) { fail(e); }
  };
  document.querySelectorAll("[data-sgcancel]").forEach((x) => (x.onclick = async () => {
    if (!confirm("Отменить разовое занятие?")) return;
    try { await api("POST", `/api/singles/${x.dataset.sgcancel}/cancel`, {}); toast("Отменено"); reload(); } catch (e) { fail(e); }
  }));
}

/* задача «Разовое завтра» или «Разовые: договориться»: текст для родителя и «что дальше» */
function singleTaskDialog(t, done) {
  const sid = t.student && t.student.id;
  const remind = t.kind === "single_remind";
  const y = +S.meta.today.slice(0, 4);
  const intake = [`${y}-01-05`, `${y}-08-01`, `${y + 1}-01-05`, `${y + 1}-08-01`].find((d) => d > S.meta.today);
  openDrawer(`<h2>1️⃣ ${esc(t.title)}</h2>
    <div class="small muted">${esc((t.student && t.student.name) || "")}${t.student && t.student.phone ? ", " + esc(t.student.phone) : ""}</div>
    ${remind && t.note ? `<div class="section"><div class="small"><b>Текст для родителя</b> (ссылку на оплату создайте в карточке: «💳 Ссылка на оплату»)</div>
      <textarea id="sd-msg" rows="6" style="width:100%;margin-top:6px">${esc(t.note)}</textarea>
      <div class="row" style="gap:8px;margin-top:6px"><button class="btn sm" id="sd-copy" style="flex:0 0 auto">📋 Скопировать текст</button>
        <button class="btn sm" id="sd-card" style="flex:0 0 auto">Открыть карточку</button></div></div>` : `<div class="row" style="margin-top:8px"><button class="btn sm" id="sd-card" style="flex:0 0 auto">Открыть карточку</button></div>`}
    <div class="section"><div class="small"><b>Что дальше?</b></div>
      <div style="display:grid;gap:8px;margin-top:8px">
        <button class="btn primary" id="sd-date">📅 Записать на следующее разовое</button>
        <div id="sd-dbox" hidden>${singleForm("sd", null)}<button class="btn primary sm" id="sd-dsave">Записать</button></div>
        <button class="btn" id="sd-unknown">🤷 Пока не знают: спросить через 7 дней</button>
        <button class="btn" id="sd-intake">🗓 Пригласить в ближайший набор, ${dShort(intake)}</button>
        <button class="btn" id="sd-close">✓ Просто закрыть задачу</button></div></div>`);
  const next = async (body, msg) => {
    try { await api("POST", `/api/students/${sid}/singles/next`, { ...body, task_id: t.id }); toast(msg); closeDrawer(); done && done(); } catch (e) { fail(e); }
  };
  if ($("#sd-copy")) $("#sd-copy").onclick = () => navigator.clipboard.writeText($("#sd-msg").value).then(() => toast("Скопировано"), () => window.prompt("Скопируйте", $("#sd-msg").value));
  $("#sd-card").onclick = () => { closeDrawer(); if (sid) openStudent(sid); };
  let get = null;
  $("#sd-date").onclick = () => { $("#sd-dbox").hidden = false; get = get || wireSingleForm("sd"); };
  $("#sd-dsave").onclick = () => { const b = get(); if (!b.date) return toast("Нет дат", true); next({ how: "date", ...b }, `Записан на разовое ${dShort(b.date)}`); };
  $("#sd-unknown").onclick = () => next({ how: "unknown" }, "Спросим через 7 дней");
  $("#sd-intake").onclick = () => next({ how: "intake" }, `Пригласим ${dShort(intake)}`);
  $("#sd-close").onclick = async () => { try { await api("POST", `/api/tasks/${t.id}/done`, { result: "" }); toast("Задача выполнена"); closeDrawer(); done && done(); } catch (e) { fail(e); } };
}
