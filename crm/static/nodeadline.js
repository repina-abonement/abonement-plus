/* «Воронки без дедлайнов» (Юлия, 03.11, 04.11): клиенты без задач по сроку. Первая воронка «Ждут акцию».
   Попадают сами («Ушёл» или отказ после пробного с причиной «Дорого») и вручную из карточки.
   Выходят сами после оплаты или новой записи на пробное. Когда появилась акция: задача всем одной кнопкой */
async function renderNoDeadline(page) {
  const d = await api("GET", "/api/nodeadline");
  page.innerHTML = `<div class="page-head"><h2 class="grow">Воронки без дедлайнов</h2></div>
    <div class="muted small" style="margin:-6px 0 14px">Здесь звоним не по сроку, а по поводу: когда появилась акция, удобная группа и т.п.</div>
    ${d.funnels.map((f) => `<section class="nd-funnel" data-ndf="${f.key}">
      <div class="now-head"><h3>${esc(f.title)} <span class="muted small">${f.cards.length}</span></h3>
        ${f.cards.length ? `<button class="btn sm primary" data-ndtask="${f.key}" style="margin-left:auto">📣 Появилась акция: задача всем</button>` : ""}</div>
      <div data-ndbox="${f.key}"></div>
      ${f.cards.length ? `<div class="list">${f.cards.map((c) => `<div class="list-row"><div class="grow click" data-open="${c.id}" style="cursor:pointer">
          <b>${esc(c.name)}</b> <span class="small muted">${esc(c.parent)}${c.phone ? ", " + esc(c.phone) : ""}</span>
          <div class="small muted">${esc(c.note || "")}${c.note ? " · " : ""}с ${dShort(c.since)}${c.has_task ? " · <b>задача уже стоит</b>" : ""}</div></div>
          <button class="btn sm" data-ndout="${c.id}" title="Убрать из воронки">Убрать</button></div>`).join("")}</div>`
        : `<div class="muted small">Пока никого. Сюда сами попадают ушедшие и отказавшиеся с причиной «Дорого», а ещё можно добавить из карточки клиента: «⚙️ Этап и источник» → «⏸ В «Ждут акцию»»</div>`}
    </section>`).join("")}`;
  page.querySelectorAll("[data-open]").forEach((x) => (x.onclick = () => openStudent(+x.dataset.open)));
  page.querySelectorAll("[data-ndout]").forEach((b) => (b.onclick = async () => {
    try { await api("POST", "/api/nodeadline/remove", { student_id: +b.dataset.ndout, why: "вручную" }); toast("Убран из воронки"); render(); } catch (e) { fail(e); }
  }));
  page.querySelectorAll("[data-ndtask]").forEach((b) => (b.onclick = () => {
    const box = page.querySelector(`[data-ndbox="${b.dataset.ndtask}"]`);
    box.innerHTML = `<form class="cm-box" id="nd-form">
      <div class="row" style="flex-wrap:wrap;align-items:flex-end">
        <div class="field"><label>Задача</label><input name="title" value="Предложить акцию"></div>
        <div class="field" style="flex:0 0 170px"><label>Когда</label><input type="date" name="due" min="${S.meta.today}" value="${S.meta.today}" required></div></div>
      <div class="field"><label>Что за акция (увидят администраторы в задаче)</label><input name="note" placeholder="Например: −15% на абонемент до 30.11"></div>
      <button class="btn primary sm">Поставить задачу всем</button> <button class="btn sm" type="button" id="nd-cancel">Отмена</button></form>`;
    $("#nd-cancel").onclick = () => (box.innerHTML = "");
    $("#nd-form").onsubmit = async (e) => {
      e.preventDefault();
      const f = Object.fromEntries(new FormData(e.target));
      try { const r = await api("POST", "/api/nodeadline/tasks", { funnel: b.dataset.ndtask, title: f.title, due: f.due + "T11:00", note: f.note }); toast(`Задач поставлено: ${r.made}`); render(); } catch (err) { fail(err); }
    };
  }));
}
