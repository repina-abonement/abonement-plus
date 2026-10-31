"use strict";
/* Абонемент Плюс — реестр платежей и скидки (для администратора). */

S.pay = { from: null, to: null, method: "", unconfirmed: false };

/* быстрые периоды: одной кнопкой вместо двух дат */
function payPeriods(today) {
  const d = new Date(today + "T12:00:00"), iso = (x) => x.toISOString().slice(0, 10);
  const y = new Date(d); y.setDate(d.getDate() - 1);
  const w = new Date(d); w.setDate(d.getDate() - ((d.getDay() + 6) % 7));
  const pm1 = new Date(d.getFullYear(), d.getMonth() - 1, 1, 12), pm2 = new Date(d.getFullYear(), d.getMonth(), 0, 12);
  return [["Сегодня", today, today], ["Вчера", iso(y), iso(y)], ["Неделя", iso(w), today],
    ["Этот месяц", today.slice(0, 8) + "01", today], ["Прошлый месяц", iso(pm1), iso(pm2)]];
}
const DAY_RU = ["воскресенье", "понедельник", "вторник", "среда", "четверг", "пятница", "суббота"];
const MON_RU = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
function dayTitle(iso, today) {
  const dt = new Date(iso + "T12:00:00");
  const y = new Date(today + "T12:00:00"); y.setDate(y.getDate() - 1);
  const pre = iso === today ? "Сегодня, " : iso === y.toISOString().slice(0, 10) ? "Вчера, " : "";
  return `${pre}${dt.getDate()} ${MON_RU[dt.getMonth()]}, ${DAY_RU[dt.getDay()]}`;
}

async function renderPayments(page) {
  const P = S.pay;
  const today = S.meta.today;
  if (!P.from) { P.from = today.slice(0, 8) + "01"; P.to = today; }
  P.openDays = P.openDays || {}; P.allDays = P.allDays || {};
  const qs = `from=${P.from}&to=${P.to}&method=${P.method}${P.unconfirmed ? "&unconfirmed=1" : ""}`;
  const d = await api("GET", "/api/payments?" + qs);
  const days = {};
  d.rows.forEach((r) => { const k = (r.paid_at || "").slice(0, 10); (days[k] = days[k] || []).push(r); });
  const dayKeys = Object.keys(days).sort().reverse();
  const isOpen = (k, i) => P.openDays[k] ?? i === 0;
  const per = payPeriods(today);
  page.innerHTML = `
    <div class="page-head"><h2 class="grow">Оплаты</h2>
      ${can("export") ? `<a class="btn sm" href="/api/payments?${qs}&csv=1">Выгрузить в Excel</a>` : ""}</div>
    <div class="pay-filters">
      <div class="seg">${per.map(([t, a, b]) => `<button data-per="${a}|${b}" class="${P.from === a && P.to === b ? "active" : ""}">${t}</button>`).join("")}</div>
      <span class="pay-dates"><input type="date" id="p-from" value="${P.from}"> <span class="muted">по</span> <input type="date" id="p-to" value="${P.to}"></span>
    </div>
    <div class="pay-sum">
      <div class="pay-total"><span>${P.method ? esc(d.methods[P.method] || "") : "Всего за период"}</span><b>${rub(d.total)}</b><span class="muted small">${d.rows.length} ${plural(d.rows.length, "платёж", "платежа", "платежей")}</span></div>
      <div class="chips">
        <button class="chip ${!P.method ? "on" : ""}" data-m="">Все способы</button>
        ${Object.entries(d.methods).filter(([k, t]) => d.totals[t] || P.method === k).map(([k, t]) => `<button class="chip ${P.method === k ? "on" : ""}" data-m="${k}">${esc(t)}${d.totals[t] ? ` · ${rub(d.totals[t])}` : ""}</button>`).join("")}
        <button class="chip ${P.unconfirmed ? "on warn" : d.unconfirmed.count ? "warn" : ""}" id="p-unc">Ждут подтверждения${d.unconfirmed.count ? ` · ${d.unconfirmed.count} · ${rub(d.unconfirmed.sum)}` : ""}</button>
      </div>
    </div>
    ${d.debtors.length ? `<details class="panel fold" style="margin-top:12px"><summary><h3>Должники · ${d.debtors.length}</h3><span class="muted small">${rub(d.debtors.reduce((a, x) => a + x.debt, 0))}</span></summary>
      <div class="chips" style="margin-top:8px">${d.debtors.slice(0, 60).map((x) => `<button class="chip" data-open="${x.id}">${esc(x.name)} · ${rub(x.debt)}</button>`).join("")}</div></details>` : ""}
    ${d.corrections.length ? `<details class="panel fold" style="margin-top:12px"><summary><h3>Корректировки цен · ${d.corrections.length}</h3><span class="muted small">исключения из правил: кто, на сколько и почему</span></summary>
      <div class="table-wrap" style="margin-top:8px"><table class="optable"><thead><tr><th>Когда</th><th>Ученик</th><th>Месяц</th><th>Было</th><th>Стало</th><th>Причина</th><th>Кто</th></tr></thead>
      <tbody>${d.corrections.map((c) => `<tr><td>${esc(c.created_at.replace("T", " "))}</td><td><a href="#" data-open="${c.student_id}">${esc(c.student)}</a></td><td>${esc(monthLabel(c.month))}</td>
        <td>${rub(c.old_price)}</td><td><b>${rub(c.new_price)}</b> <span class="small muted">${c.new_price < c.old_price ? "−" : "+"}${rub(Math.abs(c.new_price - c.old_price))}</span></td><td>${esc(c.comment)}</td><td>${esc(c.user_name)}</td></tr>`).join("")}</tbody></table></div></details>` : ""}
    <div class="pay-days">
    ${dayKeys.length ? dayKeys.map((k, i) => { const rows = days[k], sum = rows.filter((r) => !r.cancelled).reduce((a, r) => a + r.amount, 0);
      return `<details class="panel fold pay-day" data-day="${k}" ${isOpen(k, i) ? "open" : ""}><summary><h3>${dayTitle(k, today)}</h3>
        <span class="muted small">${rows.length} ${plural(rows.length, "платёж", "платежа", "платежей")}</span><b class="pay-day-sum">${rub(sum)}</b></summary>
        <div class="table-wrap"><table class="optable pay-tbl"><tbody>${rows.map((r, j) => `<tr class="pay-row ${r.cancelled ? "struck" : ""}${j >= 15 && !P.allDays[k] ? " pay-more-hide" : ""}" data-prow="${r.student_id}">
          <td class="muted small" style="width:48px">${esc((r.paid_at || "").slice(11, 16))}</td>
          <td><b class="pay-name">${esc(r.student)}</b><div class="small muted">${esc(r.group_name || "")}</div></td>
          <td style="white-space:nowrap"><b>${rub(r.amount)}</b></td><td class="small">${esc(r.method_label)}</td>
          <td class="small muted">${esc(r.comment)}${r.user_name ? `<div>${esc(r.user_name)}</div>` : ""}</td>
          <td style="text-align:right">${r.cancelled ? `<span class="pill">отменён</span>` : r.confirmed ? "" : `<button class="btn sm" data-pconf="${r.id}">Подтвердить</button>`}</td></tr>`).join("")}</tbody></table></div>
        ${rows.length > 15 && !P.allDays[k] ? `<button class="btn sm" data-pmore="${k}" style="margin-top:8px">Показать ещё ${rows.length - 15}</button>` : ""}</details>`; }).join("")
      : `<div class="panel muted">За этот период платежей нет</div>`}
    </div>`;
  page.querySelectorAll("[data-per]").forEach((b) => (b.onclick = () => { [P.from, P.to] = b.dataset.per.split("|"); P.openDays = {}; render(); }));
  const upd = () => { P.from = $("#p-from").value; P.to = $("#p-to").value; render(); };
  ["#p-from", "#p-to"].forEach((x) => ($(x).onchange = upd));
  page.querySelectorAll("[data-pmore]").forEach((b) => (b.onclick = () => { P.allDays[b.dataset.pmore] = true; render(); }));
  page.querySelectorAll("[data-m]").forEach((b) => (b.onclick = () => { P.method = b.dataset.m; render(); }));
  $("#p-unc").onclick = () => { P.unconfirmed = !P.unconfirmed; render(); };
  page.querySelectorAll(".pay-day").forEach((x) => x.addEventListener("toggle", () => { P.openDays[x.dataset.day] = x.open; }));
  page.querySelectorAll("[data-open]").forEach((b) => (b.onclick = (e) => { e.preventDefault(); openStudent(+b.dataset.open); }));
  page.querySelectorAll("[data-prow]").forEach((tr) => (tr.onclick = (e) => { if (!e.target.closest("button")) openStudent(+tr.dataset.prow); }));
  page.querySelectorAll("[data-pconf]").forEach((b) => (b.onclick = async () => { await api("POST", `/api/payments/${b.dataset.pconf}/confirm`, {}).catch(fail); render(); }));
}

/* Скидки: живут в Настройках → Цены и правила */
async function renderDiscounts(box) {
  const disc = await api("GET", "/api/discounts?all=1");
  box.innerHTML = `<h3>Скидки</h3>
      <div class="list" style="box-shadow:none;margin:8px 0">${disc.map((x) => `<div class="list-row ${x.active ? "" : "struck"}"><div class="grow"><b>${esc(x.name)}</b> · ${x.percent}%
        <div class="small muted">${x.rule === "first_on_trial_day" ? "Сама на первый абонемент, если его оформляют в день пробного" : x.rule === "sibling" ? "Сама второму и следующим детям в семье, если старший ещё занимается" : "Выбирается вручную при выставлении счёта"}. Скидки не суммируются: действует самая большая</div></div>
        <button class="btn sm" data-darch="${x.id}" data-active="${x.active ? 0 : 1}">${x.active ? "В архив" : "Вернуть"}</button></div>`).join("")}</div>
      <div class="row"><div class="field"><label>Название</label><input id="d-name" placeholder="Например, Многодетным"></div>
        <div class="field" style="flex:0 0 110px"><label>Скидка, %</label><input id="d-pct" type="number" min="1" max="100"></div>
        <div class="field" style="flex:0 0 auto"><label>&nbsp;</label><button class="btn primary" id="d-add">Добавить</button></div></div>`;
  const reload = () => renderDiscounts(box).catch(fail);
  box.querySelectorAll("[data-darch]").forEach((b) => (b.onclick = async () => { await api("POST", `/api/discounts/${b.dataset.darch}/archive`, { active: b.dataset.active === "1" }).catch(fail); reload(); }));
  box.querySelector("#d-add").onclick = async () => { try { await api("POST", "/api/discounts", { name: box.querySelector("#d-name").value, percent: +box.querySelector("#d-pct").value }); toast("Скидка добавлена"); reload(); } catch (e) { fail(e); } };
}
