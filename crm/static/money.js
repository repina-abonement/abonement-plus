"use strict";
/* Абонемент Плюс — реестр платежей и скидки (для администратора). */

S.pay = { from: null, to: null, method: "", unconfirmed: false };

async function renderPayments(page) {
  const P = S.pay;
  const today = S.meta.today;
  if (!P.from) { P.from = today.slice(0, 8) + "01"; P.to = today; }
  const qs = `from=${P.from}&to=${P.to}&method=${P.method}${P.unconfirmed ? "&unconfirmed=1" : ""}`;
  const d = await api("GET", "/api/payments?" + qs);
  const disc = await api("GET", "/api/discounts?all=1");
  page.innerHTML = `
    <div class="page-head"><h2 class="grow">Оплаты</h2>
      ${can("export") ? `<a class="btn sm" href="/api/payments?${qs}&csv=1">Выгрузить в Excel</a>` : ""}</div>
    <div class="row" style="max-width:820px;margin-bottom:12px">
      <div class="field"><label>С</label><input type="date" id="p-from" value="${P.from}"></div>
      <div class="field"><label>По</label><input type="date" id="p-to" value="${P.to}"></div>
      <div class="field"><label>Способ</label><select id="p-m"><option value="">Все</option>${Object.entries(d.methods).map(([k, t]) => `<option value="${k}" ${P.method === k ? "selected" : ""}>${t}</option>`).join("")}</select></div>
      <div class="field"><label>&nbsp;</label><button class="btn ${P.unconfirmed ? "primary" : ""}" id="p-unc">Неподтверждённые${d.unconfirmed.count ? ` · ${d.unconfirmed.count}` : ""}</button></div>
    </div>
    <div class="cstats" style="grid-template-columns:repeat(auto-fill,minmax(170px,1fr))">
      <div class="cstat"><span>Всего за период</span><b>${rub(d.total)}</b></div>
      ${Object.entries(d.totals).map(([k, v]) => `<div class="cstat"><span>${esc(k)}</span><b>${rub(v)}</b></div>`).join("")}
      ${d.unconfirmed.count ? `<div class="cstat warn"><span>Ждут подтверждения</span><b>${rub(d.unconfirmed.sum)}</b></div>` : ""}
    </div>
    ${d.debtors.length ? `<section class="panel" style="margin-top:14px"><h3>Должники</h3>
      <div class="chips" style="margin-top:8px">${d.debtors.slice(0, 40).map((x) => `<button class="chip" data-open="${x.id}">${esc(x.name)} · ${rub(x.debt)}</button>`).join("")}</div></section>` : ""}
    <section class="panel" style="margin-top:14px"><h3>Реестр платежей</h3>
      <div class="table-wrap"><table class="optable"><thead><tr><th>Дата</th><th>Ученик</th><th>Сумма</th><th>Способ</th><th>Комментарий</th><th>Принял</th><th></th></tr></thead>
      <tbody>${d.rows.length ? d.rows.map((r) => `<tr class="${r.cancelled ? "struck" : ""}">
        <td>${dtShort(r.paid_at)}</td><td><button class="linkish" data-open="${r.student_id}">${esc(r.student)}</button><div class="small muted">${esc(r.group_name || "")}</div></td>
        <td><b>${rub(r.amount)}</b></td><td>${esc(r.method_label)}</td><td class="small">${esc(r.comment)}</td><td class="small">${esc(r.user_name)}</td>
        <td>${r.cancelled ? `<span class="pill">отменён</span>` : r.confirmed ? `<span class="pill green">подтверждён</span>` : `<button class="btn sm" data-pconf="${r.id}">Подтвердить</button>`}</td></tr>`).join("")
        : `<tr><td colspan="7" class="muted">За этот период платежей нет</td></tr>`}</tbody></table></div></section>
    <section class="panel" style="margin-top:14px"><h3>Скидки</h3>
      <div class="list" style="box-shadow:none;margin:8px 0">${disc.map((x) => `<div class="list-row ${x.active ? "" : "struck"}"><div class="grow"><b>${esc(x.name)}</b> · ${x.percent}%
        <div class="small muted">${x.rule === "first_on_trial_day" ? "Сама на первый абонемент, если его оформляют в день пробного" : x.rule === "sibling" ? "Сама второму и следующим детям в семье, если старший ещё занимается" : "Выбирается вручную при выставлении счёта"}. Скидки не суммируются: действует самая большая</div></div>
        <button class="btn sm" data-darch="${x.id}" data-active="${x.active ? 0 : 1}">${x.active ? "В архив" : "Вернуть"}</button></div>`).join("")}</div>
      <div class="row"><div class="field"><label>Название</label><input id="d-name" placeholder="Например, Многодетным"></div>
        <div class="field" style="flex:0 0 110px"><label>Скидка, %</label><input id="d-pct" type="number" min="1" max="100"></div>
        <div class="field" style="flex:0 0 auto"><label>&nbsp;</label><button class="btn primary" id="d-add">Добавить</button></div></div>
    </section>`;
  const upd = () => { P.from = $("#p-from").value; P.to = $("#p-to").value; P.method = $("#p-m").value; render(); };
  ["#p-from", "#p-to", "#p-m"].forEach((x) => ($(x).onchange = upd));
  $("#p-unc").onclick = () => { P.unconfirmed = !P.unconfirmed; render(); };
  page.querySelectorAll("[data-open]").forEach((b) => (b.onclick = () => openStudent(+b.dataset.open)));
  page.querySelectorAll("[data-pconf]").forEach((b) => (b.onclick = async () => { await api("POST", `/api/payments/${b.dataset.pconf}/confirm`, {}).catch(fail); render(); }));
  page.querySelectorAll("[data-darch]").forEach((b) => (b.onclick = async () => { await api("POST", `/api/discounts/${b.dataset.darch}/archive`, { active: b.dataset.active === "1" }).catch(fail); render(); }));
  $("#d-add").onclick = async () => { try { await api("POST", "/api/discounts", { name: $("#d-name").value, percent: +$("#d-pct").value }); toast("Скидка добавлена"); render(); } catch (e) { fail(e); } };
}
