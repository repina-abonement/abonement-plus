"use strict";
/* Абонемент Плюс — мои платежи и реклама в пабликах. */

S.spend = { tab: "bills" };
const leftText = (n) => n < 0 ? `просрочено на ${-n} дн.` : n === 0 ? "сегодня" : n === 1 ? "завтра" : `через ${n} дн.`;

/* ---------- на рабочем столе ---------- */
function spendDeskBox(d) {
  const b = d.bills_due || [], a = d.ads_alerts || [];
  if (!b.length && !a.length) return "";
  return `<section class="now" style="margin-bottom:14px"><div class="now-head"><h3>💳 Платежи и реклама</h3><button class="btn sm" style="margin-left:auto" data-go-spend>Все расходы</button></div>
    <div class="now-list">
    ${b.map((x) => `<div class="now-item ${x.level}"><span class="ref-card ${x.level}"></span>
      <div class="grow"><b>${esc(x.title)}</b> · ${rub(x.amount)}<div class="small">${esc(x.category_label)}, оплатить ${dShort(x.next_due)}: <b>${leftText(x.left)}</b>${x.pay_where ? `, ${esc(x.pay_where)}` : ""}</div></div>
      <div class="now-act"><button class="btn sm ${x.level === "red" ? "primary" : ""}" data-bpay="${x.id}" data-amt="${x.amount}">Оплачено</button></div></div>`).join("")}
    ${a.map((x) => `<div class="now-item ${x.level}"><span class="ref-card ${x.level}"></span>
      <div class="grow"><b>📣 ${esc(x.place)}</b><div class="small">${esc(x.text)}${x.contact ? `, админ: ${esc(x.contact)}` : ""}</div></div>
      <div class="now-act">${x.kind === "post" ? `<button class="btn sm primary" data-apost="${x.post_id}">Вышел</button><button class="btn sm" data-amove="${x.post_id}">Перенести</button>` : `<button class="btn sm" data-go-spend-ads>Открыть</button>`}</div></div>`).join("")}
    </div></section>`;
}

function wireSpend(page) {
  page.querySelectorAll("[data-go-spend]").forEach((b) => (b.onclick = () => { S.spend.tab = "bills"; go("spend"); }));
  page.querySelectorAll("[data-go-spend-ads]").forEach((b) => (b.onclick = () => { S.spend.tab = "ads"; go("spend"); }));
  page.querySelectorAll("[data-bpay]").forEach((b) => (b.onclick = async () => {
    const amt = prompt("Сколько оплатили, ₽?", b.dataset.amt);
    if (amt === null) return;
    try { const r = await api("POST", `/api/bills/${b.dataset.bpay}/pay`, { amount: amt }); toast(r.next_due ? `Записано. Следующий платёж ${dShort(r.next_due)}` : "Записано"); render(); } catch (e) { fail(e); }
  }));
  page.querySelectorAll("[data-apost]").forEach((b) => (b.onclick = async () => {
    const link = prompt("Ссылка на пост (можно оставить пустым)", "");
    if (link === null) return;
    try { await api("POST", `/api/ads/posts/${b.dataset.apost}`, { status: "posted", link }); toast("Отмечено: пост вышел"); render(); } catch (e) { fail(e); }
  }));
  page.querySelectorAll("[data-amove]").forEach((b) => (b.onclick = async () => {
    const d = prompt("На какую дату перенести? В формате ГГГГ-ММ-ДД", addDays(S.meta.today, 1));
    if (!d) return;
    try { await api("POST", `/api/ads/posts/${b.dataset.amove}`, { planned: d }); toast("Перенесено"); render(); } catch (e) { fail(e); }
  }));
}

/* ---------- раздел «Расходы» ---------- */
async function renderSpend(page) {
  const C = S.spend;
  page.innerHTML = `<div class="page-head"><h2 class="grow">Расходы</h2></div>
    <div class="seg" style="margin-bottom:14px"><button data-st="bills" class="${C.tab === "bills" ? "active" : ""}">Мои платежи</button><button data-st="ads" class="${C.tab === "ads" ? "active" : ""}">Реклама в пабликах</button></div>
    <div id="sp-body"></div>`;
  page.querySelectorAll("[data-st]").forEach((b) => (b.onclick = () => { step(() => { C.tab = b.dataset.st; }); }));
  if (C.tab === "bills") await renderBills($("#sp-body"));
  else await renderAds($("#sp-body"));
  wireSpend(page);
}

function billForm(v, cats, pers) {
  return `<div class="sub-form"><div class="row" style="flex-wrap:wrap;align-items:flex-end">
    <div class="field" style="flex:2 1 220px"><label>За что</label><input data-bf="title" value="${esc(v.title || "")}" placeholder="Аренда Небо, Sipuni, сервер"></div>
    <div class="field" style="flex:0 0 130px"><label>Сумма, ₽</label><input data-bf="amount" type="number" value="${esc(v.amount ?? "")}"></div>
    <div class="field" style="flex:0 0 160px"><label>Категория</label><select data-bf="category">${Object.entries(cats).map(([k, t]) => `<option value="${k}" ${k === v.category ? "selected" : ""}>${t}</option>`).join("")}</select></div>
    <div class="field" style="flex:0 0 160px"><label>Как часто</label><select data-bf="period">${Object.entries(pers).map(([k, t]) => `<option value="${k}" ${k === (v.period || "monthly") ? "selected" : ""}>${t[0].toUpperCase() + t.slice(1)}</option>`).join("")}</select></div>
    <div class="field" style="flex:0 0 160px"><label>Ближайшая оплата</label><input data-bf="next_due" type="date" value="${esc(v.next_due || S.meta.today)}"></div>
    <div class="field" style="flex:0 0 150px"><label>Напомнить за, дней</label><input data-bf="remind_days" type="number" min="0" max="30" value="${esc(v.remind_days ?? 3)}"></div>
    <div class="field" style="flex:1 1 220px"><label>Куда платить</label><input data-bf="pay_where" value="${esc(v.pay_where || "")}" placeholder="Сбер Бизнес, по счёту, карта"></div>
    <div class="field" style="flex:1 1 220px"><label>Заметка</label><input data-bf="note" value="${esc(v.note || "")}"></div>
    <button class="btn primary" data-bsave style="flex:0">Сохранить</button></div></div>`;
}

async function renderBills(box) {
  const d = await api("GET", "/api/bills");
  box.innerHTML = `<div class="cfacts" style="margin-bottom:14px"><div class="cfact"><b>${rub(d.per_month_total)}</b><span>в месяц регулярно</span></div>
      ${Object.entries(d.per_month).map(([k, v]) => `<div class="cfact"><b>${rub(v)}</b><span>${esc(k)}</span></div>`).join("")}
      <div class="cfact green"><b>${rub(d.paid_this_month)}</b><span>оплачено в этом месяце</span></div></div>
    <button class="btn primary" id="b-new" style="margin-bottom:10px">+ Платёж</button><div id="b-form"></div>
    <div class="list">${d.bills.length ? d.bills.map((b) => `<div class="list-row ${b.level ? "lvl-" + b.level : ""}">
      <span class="ref-card ${b.level}"></span>
      <div class="grow"><b>${esc(b.title)}</b> · ${rub(b.amount)} <span class="pill">${esc(b.category_label)}</span>
        <div class="small muted">${esc(b.period_label)}, следующий ${dShort(b.next_due)} (${leftText(b.left)})${b.pay_where ? `, ${esc(b.pay_where)}` : ""}${b.last_paid ? `. Последний раз ${dShort(b.last_paid.paid_at)}` : ""}</div></div>
      <button class="btn sm ${b.level === "red" ? "primary" : ""}" data-bpay="${b.id}" data-amt="${b.amount}">Оплачено</button>
      <button class="btn sm" data-bedit="${b.id}">✎</button><button class="btn sm" data-barch="${b.id}" title="Больше не платим">✕</button></div>
      <div data-bef="${b.id}"></div>`).join("") : `<div class="list-row muted">Добавьте аренду, подписки и сервисы: CRM будет напоминать заранее и подсветит просрочку</div>`}</div>
    ${d.history.length ? `<h3 style="margin:18px 0 8px">История оплат</h3><div class="list">${d.history.map((h) => `<div class="list-row"><span class="grow">${esc(h.title)}</span><span class="small muted">${dShort(h.paid_at)}</span><b>${rub(h.amount)}</b></div>`).join("")}</div>` : ""}`;
  const save = (wrap, id) => wrap.querySelector("[data-bsave]").onclick = async () => {
    const data = Object.fromEntries([...wrap.querySelectorAll("[data-bf]")].map((i) => [i.dataset.bf, i.value]));
    try { await api(id ? "PUT" : "POST", id ? `/api/bills/${id}` : "/api/bills", data); toast("Сохранено"); render(); } catch (e) { fail(e); }
  };
  $("#b-new").onclick = () => { const w = $("#b-form"); if (w.innerHTML) { w.innerHTML = ""; return; } w.innerHTML = billForm({}, d.categories, d.periods); save(w); };
  box.querySelectorAll("[data-bedit]").forEach((b) => (b.onclick = () => {
    const w = box.querySelector(`[data-bef="${b.dataset.bedit}"]`); if (w.innerHTML) { w.innerHTML = ""; return; }
    w.innerHTML = billForm(d.bills.find((x) => x.id === +b.dataset.bedit), d.categories, d.periods); save(w, +b.dataset.bedit);
  }));
  box.querySelectorAll("[data-barch]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Больше не платим? Платёж уйдёт из списка, история останется")) return;
    try { await api("POST", `/api/bills/${b.dataset.barch}/archive`, {}); render(); } catch (e) { fail(e); }
  }));
}

async function renderAds(box) {
  const d = await api("GET", "/api/ads");
  const brs = S.meta.branches;
  box.innerHTML = `<div class="cfacts" style="margin-bottom:14px"><div class="cfact"><b>${rub(d.spent_total)}</b><span>потрачено на паблики</span></div>
      <div class="cfact"><b>${d.places.length}</b><span>пабликов</span></div>
      <div class="cfact ${d.alerts.length ? "yellow" : ""}"><b>${d.alerts.length}</b><span>требуют внимания</span></div></div>
    <button class="btn primary" id="ap-new" style="margin-bottom:10px">+ Паблик</button><div id="ap-form"></div>
    ${d.places.length ? d.places.map((p) => `<section class="panel" style="padding:14px;margin-bottom:12px">
      <div class="team-head"><b>📣 ${p.link ? `<a href="${esc(p.link)}" target="_blank">${esc(p.name)}</a>` : esc(p.name)}</b>
        ${p.branch ? `<span class="pill">${esc(p.branch)}</span>` : ""}<span class="pill">потрачено ${rub(p.spent)}</span>
        <span class="pill ${p.leads ? "green" : ""}">заявок ${p.leads}${p.bought ? `, купили ${p.bought}` : ""}</span>${p.per_lead ? `<span class="pill">заявка ${rub(p.per_lead)}</span>` : ""}</div>
      <div class="small muted" style="margin:4px 0 8px">${p.contact ? `Админ: ${esc(p.contact)}. ` : ""}${p.price ? `Цена поста ${rub(p.price)}. ` : ""}${esc(p.note || "")}</div>
      ${p.deals.map((dl) => `<div class="deal"><div class="small"><b>Оплата ${dShort(dl.paid_at)}: ${rub(dl.amount)}</b>, постов ${dl.posted} из ${dl.posts_total}${dl.valid_until ? `, действует до ${dShort(dl.valid_until)}` : ""}${dl.note ? `, ${esc(dl.note)}` : ""}</div>
        <div class="chips" style="margin-top:4px">${dl.posts.map((x) => x.status === "posted" ? `<span class="pill green">${dShort(x.planned)} ✓${x.link ? ` <a href="${esc(x.link)}" target="_blank">пост</a>` : ""}</span>`
          : x.status === "cancelled" ? `<span class="pill">${dShort(x.planned)} отменён</span>`
          : `<span class="pill ${x.planned < S.meta.today ? "red" : x.planned === S.meta.today ? "yellow" : ""}">${dShort(x.planned)} <button class="linkish small" data-apost="${x.id}">вышел</button> · <button class="linkish small" data-amove="${x.id}">перенести</button></span>`).join("")}</div></div>`).join("")}
      <button class="btn sm" data-adeal="${p.id}" style="margin-top:8px">+ Оплатили размещение</button><div data-adf="${p.id}"></div>
    </section>`).join("") : `<div class="muted">Добавьте паблики, с которыми договорились. Каждый станет рекламным источником, и вы увидите, сколько заявок он принёс</div>`}`;
  $("#ap-new").onclick = () => {
    const w = $("#ap-form"); if (w.innerHTML) { w.innerHTML = ""; return; }
    w.innerHTML = `<div class="sub-form"><div class="row" style="flex-wrap:wrap;align-items:flex-end">
      <div class="field" style="flex:1 1 200px"><label>Название</label><input data-pf="name" placeholder="Солнцево Онлайн"></div>
      <div class="field" style="flex:1 1 220px"><label>Ссылка</label><input data-pf="link" placeholder="https://vk.com/..."></div>
      <div class="field" style="flex:1 1 180px"><label>Админ паблика</label><input data-pf="contact" placeholder="Имя, телефон или @ник"></div>
      <div class="field" style="flex:0 0 160px"><label>Филиал</label><select data-pf="branch_id"><option value="">Все</option>${brs.map((b) => `<option value="${b.id}">${esc(b.name)}</option>`).join("")}</select></div>
      <div class="field" style="flex:0 0 130px"><label>Цена поста, ₽</label><input data-pf="price" type="number"></div>
      <div class="field" style="flex:1 1 200px"><label>Условия</label><input data-pf="note" placeholder="Закреп на сутки, выход до 12:00"></div>
      <button class="btn primary" data-psave style="flex:0">Сохранить</button></div></div>`;
    w.querySelector("[data-psave]").onclick = async () => {
      try { await api("POST", "/api/ads/places", Object.fromEntries([...w.querySelectorAll("[data-pf]")].map((i) => [i.dataset.pf, i.value]))); toast("Паблик добавлен"); render(); } catch (e) { fail(e); }
    };
  };
  box.querySelectorAll("[data-adeal]").forEach((b) => (b.onclick = () => {
    const w = box.querySelector(`[data-adf="${b.dataset.adeal}"]`); if (w.innerHTML) { w.innerHTML = ""; return; }
    w.innerHTML = `<div class="sub-form"><div class="row" style="flex-wrap:wrap;align-items:flex-end">
      <div class="field" style="flex:0 0 150px"><label>Когда оплатили</label><input type="date" data-df="paid_at" value="${S.meta.today}"></div>
      <div class="field" style="flex:0 0 130px"><label>Сумма, ₽</label><input type="number" data-df="amount"></div>
      <div class="field" style="flex:0 0 120px"><label>Постов</label><input type="number" min="1" data-df="posts_total" value="1"></div>
      <div class="field" style="flex:0 0 160px"><label>Действует до</label><input type="date" data-df="valid_until"></div>
      <div class="field" style="flex:1 1 200px"><label>Даты постов через запятую</label><input data-df="planned" placeholder="2026-11-03, 2026-11-10"></div>
      <div class="field" style="flex:1 1 200px"><label>Заметка</label><input data-df="note"></div>
      <button class="btn primary" data-dsave style="flex:0">Сохранить</button></div>
      <div class="small muted">Даты постов можно не указывать: CRM разложит их равномерно до конца срока и будет напоминать</div></div>`;
    w.querySelector("[data-dsave]").onclick = async () => {
      const v = Object.fromEntries([...w.querySelectorAll("[data-df]")].map((i) => [i.dataset.df, i.value]));
      v.planned = v.planned.split(",").map((x) => x.trim()).filter(Boolean); v.place_id = +b.dataset.adeal;
      try { await api("POST", "/api/ads/deals", v); toast("Оплата записана, напоминания поставлены"); render(); } catch (e) { fail(e); }
    };
  }));
}
