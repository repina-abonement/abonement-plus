/* Лист ожидания: в карточке ребёнка и воронка на рабочем столе с 26 числа */

function waitlistCard(d) {
  const items = (d.waitlist || []).filter((w) => w.status === "queued" || w.status === "offered");
  const groups = S.meta.groups.filter((g) => !g.is_makeup && g.lesson_kind !== "ofp");
  const nm = d.next_month;
  return `<div class="wl-card">
    ${items.map((w) => `<div class="small">⏳ Лист ожидания: <b>${esc(w.group_name)}</b> с ${monthLabel(w.month)} · ${esc(w.status_label)}</div>`).join("")}
    <button class="linkish small" id="wl-open">+ В лист ожидания</button>
    <div class="row" id="wl-form" hidden style="margin-top:6px;flex-wrap:wrap">
      <div class="field"><label>Группа</label><select id="wl-group">${groups.map((g) => `<option value="${g.id}">${esc(g.name)}</option>`).join("")}</select></div>
      <div class="field" style="flex:0 0 150px"><label>С месяца</label><select id="wl-month">${[d.cur_month, nm, shiftMonth(nm, 1)].map((m) => `<option value="${m}" ${m === nm ? "selected" : ""}>${monthLabel(m)}</option>`).join("")}</select></div>
      <div class="field"><label>Комментарий</label><input id="wl-note" placeholder="Например: хочет Чт ОФП"></div>
      <button class="btn sm primary" id="wl-add" style="flex:0;align-self:flex-end">Поставить в очередь</button></div></div>`;
}

function shiftMonth(ym, n) { const [y, m] = ym.split("-").map(Number); const d = new Date(y, m - 1 + n, 1); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`; }

function wireWaitlistCard(body, sid, reload) {
  const o = body.querySelector("#wl-open");
  if (!o) return;
  o.onclick = () => { body.querySelector("#wl-form").hidden = false; o.hidden = true; };
  body.querySelector("#wl-add").onclick = async () => {
    try { await api("POST", `/api/students/${sid}/waitlist`, { group_id: +$("#wl-group").value, month: $("#wl-month").value, note: $("#wl-note").value }); toast("Поставлен в лист ожидания"); reload(); } catch (e) { fail(e); }
  };
}

function waitlistBox(w) {
  if (!w) return "";
  if (!w.active) return w.waiting ? `<div class="small muted" style="margin:0 0 12px">⏳ В листе ожидания ${w.waiting} ${kidsWord(w.waiting)}. Распределяем с 26 числа, когда пройдёт срок продления</div>` : "";
  if (!w.groups.length) return "";
  return `<section class="now wl-box" style="margin-bottom:14px"><div class="now-head"><h3>⏳ Лист ожидания на ${esc(w.month_label)}</h3>
      <span class="small muted">срок продления прошёл: места тех, кто не оплатил, можно отдавать по очереди</span></div>
    ${w.groups.map((g) => `<div class="wl-group">
      <div class="wl-head"><b>${esc(g.group)}</b> <span class="small muted">${esc(g.branch)}</span>
        <span class="pill ${g.free ? "green" : ""}">свободно ${g.free} из ${g.capacity}</span></div>
      <div class="wl-cols">
        <div><div class="small muted">Не продлили (${g.not_renewed.length})</div>
          ${g.not_renewed.map((x) => `<div class="wl-row click" data-wlopen="${x.id}">${esc(x.name)}${x.amount ? ` <span class="small muted">${rub(x.amount)}</span>` : ""}</div>`).join("") || `<div class="small muted">все продлили</div>`}</div>
        <div><div class="small muted">Ждут (${g.queue.length})</div>
          ${g.queue.map((q, i) => `<div class="wl-row"><span class="grow click" data-wlopen="${q.student_id}">${i + 1}. ${esc(q.name)}${q.note ? ` <span class="small muted">${esc(q.note)}</span>` : ""}</span>
            ${q.status === "offered" ? `<span class="pill yellow">предложили</span><button class="btn sm" data-wl="${q.id}" data-st="declined">Отказался</button>`
              : `<button class="btn sm primary" data-wl="${q.id}" data-st="offered">Предложить место</button>`}</div>`).join("")}</div>
      </div></div>`).join("")}
    <div class="small muted">Когда ребёнка из очереди запишут в группу, он сам уйдёт из листа</div></section>`;
}

function wireWaitlist(page) {
  page.querySelectorAll("[data-wlopen]").forEach((el) => (el.onclick = () => openStudent(+el.dataset.wlopen)));
  page.querySelectorAll("[data-wl]").forEach((b) => (b.onclick = async () => {
    try { await api("POST", `/api/waitlist/${b.dataset.wl}/status`, { status: b.dataset.st }); render(); } catch (e) { fail(e); }
  }));
}
