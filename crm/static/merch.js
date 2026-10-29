"use strict";
/* Абонемент Плюс — мерч: продажи, итог смены тренера, раздел для админа. */

const MERCH_METHODS = { cash: "💵 Наличные", card: "💳 Карта", qr: "📱 QR" };

/* форма продажи: товары с количеством, способ оплаты, для QR — отметка про чек */
function merchSaleForm(items, opts = {}) {
  return `<div class="merch-form">
    ${opts.branches ? `<div class="field"><label>Филиал</label><select data-mb>${opts.branches.map((b) => `<option value="${b.id}">${esc(b.name)}</option>`).join("")}</select></div>` : ""}
    <div class="merch-items">${items.map((it) => `<div class="merch-it"><span class="grow">${esc(it.name)} <span class="small muted">${rub(it.price)}</span></span>
      <button type="button" class="btn sm" data-mq="${it.id}" data-d="-1">−</button><b data-mqv="${it.id}">0</b><button type="button" class="btn sm" data-mq="${it.id}" data-d="1">+</button></div>`).join("")}</div>
    <div class="field"><label>Как оплатили</label><div class="seg">${Object.entries(MERCH_METHODS).map(([k, t]) => `<button type="button" data-mm="${k}">${t}</button>`).join("")}</div></div>
    <div data-qr hidden class="alert" style="margin:6px 0">По QR чек на кассе пробивается вручную. <label class="check" style="margin-top:6px"><input type="checkbox" data-rc> Чек пробит</label></div>
    <div class="field"><label>Комментарий (кому продали, если важно)</label><input data-mn placeholder="Например: мама Миши, размер 128"></div>
    <div class="row" style="align-items:center"><b class="grow" data-mt>Итого: 0 ₽</b><button type="button" class="btn primary" data-msave style="flex:0">Сохранить продажу</button></div>
  </div>`;
}

function wireSaleForm(box, items, onSaved, branchId) {
  const qty = {}, price = Object.fromEntries(items.map((i) => [i.id, i.price]));
  let method = "";
  const total = () => { box.querySelector("[data-mt]").textContent = "Итого: " + rub(Object.entries(qty).reduce((s, [k, v]) => s + v * price[k], 0)); };
  box.querySelectorAll("[data-mq]").forEach((b) => (b.onclick = () => {
    const id = b.dataset.mq; qty[id] = Math.max(0, (qty[id] || 0) + +b.dataset.d);
    box.querySelector(`[data-mqv="${id}"]`).textContent = qty[id];
    box.querySelector(`[data-mqv="${id}"]`).closest(".merch-it").classList.toggle("on", qty[id] > 0);
    total();
  }));
  box.querySelectorAll("[data-mm]").forEach((b) => (b.onclick = () => {
    method = b.dataset.mm;
    box.querySelectorAll("[data-mm]").forEach((x) => x.classList.toggle("active", x === b));
    box.querySelector("[data-qr]").hidden = method !== "qr";
  }));
  box.querySelector("[data-msave]").onclick = async () => {
    const lines = Object.entries(qty).filter(([, v]) => v > 0).map(([k, v]) => ({ item_id: +k, qty: v }));
    if (!lines.length) return toast("Добавьте товар");
    if (!method) return toast("Выберите, как оплатили");
    const bsel = box.querySelector("[data-mb]");
    try {
      await api("POST", "/api/merch/sales", { branch_id: bsel ? +bsel.value : branchId, method, lines,
        receipt_done: method === "qr" && box.querySelector("[data-rc]").checked, note: box.querySelector("[data-mn]").value });
      toast("Продажа записана"); onSaved();
    } catch (e) { fail(e); }
  };
}

/* ---------- у тренера: итог смены ---------- */
function merchShiftBox(d, viewOnly) {
  const m = d.merch, shifts = d.checkins || [];
  if (!m || !shifts.length) return "";
  const sales = m.sales || [];
  return `<section class="now merch-box" style="margin-top:16px"><div class="now-head"><h3>👕 Итог смены: мерч</h3></div>
    ${shifts.map((s) => {
      const rep = m.reports[s.branch_id];
      const mine = sales.filter((x) => x.branch_id === s.branch_id);
      return `<div class="checkin-row" style="flex-direction:column;align-items:stretch">
        <div class="row" style="align-items:center;flex-wrap:wrap"><b class="grow">${esc(s.branch)}</b>
          ${rep === undefined ? `<span class="pill yellow">не отмечено</span>` : rep ? `<span class="pill green">продажи были</span>` : `<span class="pill">продаж не было</span>`}</div>
        ${mine.length ? `<div class="small" style="margin:4px 0">${mine.map((x) => `${esc(x.item_name)} × ${x.qty}, ${rub(x.amount)}, ${MERCH_METHODS[x.method]}${x.method === "qr" && !x.receipt_done ? " <b style='color:var(--red)'>чек не пробит</b>" : ""}`).join("<br>")}</div>` : ""}
        ${viewOnly ? "" : `<div class="row" style="flex-wrap:wrap;margin-top:6px">
          ${rep === undefined ? `<button class="btn sm" data-nosale="${s.branch_id}" style="flex:0">Продаж не было</button>` : ""}
          <button class="btn sm primary" data-sale="${s.branch_id}" style="flex:0">+ Продажа мерча</button></div>
          <div data-saleform="${s.branch_id}"></div>`}
      </div>`;
    }).join("")}
    ${viewOnly ? "" : `<div class="small muted" style="margin-top:6px">В конце смены обязательно отметь: были продажи или нет</div>`}
  </section>`;
}

function wireMerchShift(page, d) {
  page.querySelectorAll("[data-nosale]").forEach((b) => (b.onclick = async () => {
    try { await api("POST", "/api/shift/report", { branch_id: +b.dataset.nosale, sold: false }); toast("Отмечено"); render(); } catch (e) { fail(e); }
  }));
  page.querySelectorAll("[data-sale]").forEach((b) => (b.onclick = () => {
    const box = page.querySelector(`[data-saleform="${b.dataset.sale}"]`);
    if (box.innerHTML) { box.innerHTML = ""; return; }
    box.innerHTML = merchSaleForm(d.merch.items);
    wireSaleForm(box, d.merch.items, render, +b.dataset.sale);
  }));
}

/* ---------- на рабочем столе админа ---------- */
function merchAlerts(a) {
  if (!a) return "";
  const n = a.qr_no_receipt.length + a.missing_reports.length + a.cash_not_handed.length;
  if (!n) return "";
  return `<section class="now" style="margin-bottom:14px"><div class="now-head"><h3>👕 Мерч: проверить</h3><button class="btn sm" style="margin-left:auto" data-go-merch>Открыть раздел</button></div>
    ${a.qr_no_receipt.length ? `<div class="small">📱 Оплачено по QR, чек не пробит: <b>${a.qr_no_receipt.length}</b> (${a.qr_no_receipt.map((x) => `${dShort(x.date)} ${esc(x.item_name)} ${rub(x.amount)}`).join(", ")})</div>` : ""}
    ${a.cash_not_handed.length ? `<div class="small">💵 Наличные у тренеров, не сданы: ${a.cash_not_handed.map((x) => `${esc(x.name)} ${rub(x.cash_not_handed)}`).join(", ")}</div>` : ""}
    ${a.missing_reports.length ? `<div class="small">❓ Вчера не отметили итог смены по мерчу: ${a.missing_reports.map((x) => `${esc(x.name)} (${esc(x.branch)})`).join(", ")}</div>` : ""}
  </section>`;
}
function wireMerchAlerts(page) { const b = page.querySelector("[data-go-merch]"); if (b) b.onclick = () => go("merch"); }

/* ---------- раздел «Мерч» ---------- */
S.merch = { from: null, to: null };
async function renderMerch(page) {
  const C = S.merch;
  if (!C.to) { C.to = S.meta.today; C.from = S.meta.today.slice(0, 8) + "01"; }
  const [r, it] = await Promise.all([api("GET", `/api/merch/sales?from=${C.from}&to=${C.to}`), api("GET", "/api/merch/items" + (S.user.role === "admin" ? "?all=1" : ""))]);
  const ok = r.rows.filter((x) => x.status === "ok");
  page.innerHTML = `<div class="page-head"><h2 class="grow">Мерч</h2><button class="btn primary" id="m-new">+ Продажа</button></div>
    <div id="m-form"></div>
    <div class="row" style="max-width:420px;margin-bottom:12px"><div class="field"><label>С</label><input type="date" id="m-from" value="${C.from}"></div><div class="field"><label>По</label><input type="date" id="m-to" value="${C.to}"></div></div>
    <div class="cfacts" style="margin-bottom:14px"><div class="cfact green"><b>${rub(r.total)}</b><span>всего за период</span></div>
      ${Object.entries(r.by_method).map(([k, v]) => `<div class="cfact"><b>${rub(v)}</b><span>${MERCH_METHODS[k]}</span></div>`).join("")}</div>
    ${r.qr_no_receipt.length ? `<div class="alert" style="margin-bottom:12px">По QR не пробит чек: ${r.qr_no_receipt.length}. Пробейте на кассе и нажмите «Чек пробит» в строке</div>` : ""}
    <div class="row" style="align-items:flex-start;flex-wrap:wrap;gap:14px">
      <section class="panel" style="padding:14px;flex:1 1 280px"><h3 style="margin-top:0">По товарам</h3>${r.by_item.length ? r.by_item.map((x) => `<div class="kid-row"><span class="grow">${esc(x.name)}</span><span class="small muted">${x.qty} шт.</span><b>${rub(x.amount)}</b></div>`).join("") : `<div class="muted small">Продаж нет</div>`}</section>
      <section class="panel" style="padding:14px;flex:1 1 280px"><h3 style="margin-top:0">По продавцам</h3>${r.by_seller.length ? r.by_seller.map((x) => `<div class="kid-row"><span class="grow">${esc(x.name)}</span><b>${rub(x.amount)}</b>${x.cash_not_handed ? `<span class="pill yellow">наличные не сданы ${rub(x.cash_not_handed)}</span>` : ""}</div>`).join("") : `<div class="muted small">Продаж нет</div>`}</section>
    </div>
    ${can("merch", "full") ? merchStockHtml(it) : ""}
    <h3 style="margin:18px 0 8px">Все продажи</h3>
    <div class="list">${r.rows.length ? r.rows.map((x) => `<div class="list-row ${x.status === "cancelled" ? "muted" : ""}">
      <div class="grow"><b>${esc(x.item_name)}</b> × ${x.qty} · ${rub(x.amount)} · ${MERCH_METHODS[x.method]}${x.status === "cancelled" ? " · <b>отменена</b>" : ""}
        <div class="small muted">${dShort(x.date)}, ${esc(x.branch || "")}, продал ${esc(x.seller)}${x.note ? `, ${esc(x.note)}` : ""}</div></div>
      ${x.status === "ok" && x.method === "qr" ? (x.receipt_done ? `<span class="pill green">чек пробит</span>` : `<button class="btn sm primary" data-flag="${x.id}" data-f="receipt_done">Чек пробит</button>`) : ""}
      ${x.status === "ok" && x.method === "cash" ? (x.cash_handed ? `<span class="pill green">деньги сданы</span>` : `<button class="btn sm" data-flag="${x.id}" data-f="cash_handed">Деньги сданы</button>`) : ""}
      ${x.status === "ok" && S.user.role === "admin" ? `<button class="btn sm" data-mcancel="${x.id}" title="Отменить продажу">✕</button>` : ""}
    </div>`).join("") : `<div class="list-row muted">За этот период продаж нет</div>`}</div>
    ${S.user.role === "admin" ? `<h3 style="margin:18px 0 8px">Прайс</h3><div class="list">${it.items.map((i) => `<div class="list-row"><input data-in="${i.id}" value="${esc(i.name)}" style="flex:1"><input data-ip="${i.id}" type="number" value="${i.price}" style="width:110px;flex:0 0 110px"><label class="check" style="flex:0 0 auto"><input type="checkbox" data-ia="${i.id}" ${i.active ? "checked" : ""}> в продаже</label><button class="btn sm" data-isave="${i.id}">Сохранить</button></div>`).join("")}
      <div class="list-row"><input id="i-new-n" placeholder="Новый товар" style="flex:1"><input id="i-new-p" type="number" placeholder="Цена" style="width:110px;flex:0 0 110px"><button class="btn sm primary" id="i-add">Добавить</button></div></div>` : ""}`;
  wireMerchStock(page, it);
  $("#m-from").onchange = (e) => { C.from = e.target.value; render(); };
  $("#m-to").onchange = (e) => { C.to = e.target.value; render(); };
  $("#m-new").onclick = () => {
    const box = $("#m-form");
    if (box.innerHTML) { box.innerHTML = ""; return; }
    box.innerHTML = `<div class="panel" style="padding:14px;margin-bottom:14px">${merchSaleForm(it.items.filter((i) => i.active), { branches: S.meta.branches })}</div>`;
    wireSaleForm(box, it.items.filter((i) => i.active), render);
  };
  page.querySelectorAll("[data-flag]").forEach((b) => (b.onclick = async () => {
    try { await api("POST", `/api/merch/sales/${b.dataset.flag}/flag`, { flag: b.dataset.f, value: true }); render(); } catch (e) { fail(e); }
  }));
  page.querySelectorAll("[data-mcancel]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Отменить продажу? Она останется в списке зачёркнутой")) return;
    try { await api("POST", `/api/merch/sales/${b.dataset.mcancel}/cancel`, {}); render(); } catch (e) { fail(e); }
  }));
  page.querySelectorAll("[data-isave]").forEach((b) => (b.onclick = async () => {
    const id = b.dataset.isave;
    try { await api("PUT", `/api/merch/items/${id}`, { name: page.querySelector(`[data-in="${id}"]`).value, price: page.querySelector(`[data-ip="${id}"]`).value, active: page.querySelector(`[data-ia="${id}"]`).checked }); toast("Сохранено"); }
    catch (e) { fail(e); }
  }));
  if ($("#i-add")) $("#i-add").onclick = async () => {
    try { await api("POST", "/api/merch/items", { name: $("#i-new-n").value, price: $("#i-new-p").value }); render(); } catch (e) { fail(e); }
  };
}

/* Пересчёт склада (общий): сколько есть, норма, сколько заказать. Сохранение ставит задачу руководителю в Планер */
function merchStockHtml(it) {
  const items = it.items.filter((i) => i.active);
  const last = items.filter((i) => i.stock_at).sort((a, b) => (a.stock_at < b.stock_at ? 1 : -1))[0];
  return `<section class="panel" style="padding:14px;margin-top:14px"><h3 style="margin-top:0">Пересчёт и заказ</h3>
    <div class="small muted" style="margin-bottom:8px">${last ? `Последний пересчёт ${dShort(last.stock_at)} ${esc(last.stock_by)}. ` : ""}Впишите, сколько есть на складе. Что заказать, CRM посчитает сама и поставит задачу руководителю</div>
    <table class="tbl"><thead><tr><th>Товар</th><th>Есть</th><th>Норма</th><th>Заказать</th></tr></thead><tbody>
    ${items.map((i) => `<tr><td>${esc(i.name)}</td><td><input type="number" min="0" inputmode="numeric" data-mst="${i.id}" value="${i.stock ?? ""}" style="width:80px"></td>
      <td>${it.can_norm ? `<input type="number" min="0" inputmode="numeric" data-mno="${i.id}" value="${i.norm || ""}" style="width:80px">` : i.norm || "—"}</td>
      <td><b data-mor="${i.id}"></b></td></tr>`).join("")}</tbody></table>
    <button class="btn primary" id="mst-save" style="margin-top:10px">Сохранить пересчёт</button></section>`;
}

function wireMerchStock(page, it) {
  const btn = page.querySelector("#mst-save");
  if (!btn) return;
  const calc = () => it.items.forEach((i) => {
    const st = page.querySelector(`[data-mst="${i.id}"]`), no = page.querySelector(`[data-mno="${i.id}"]`), out = page.querySelector(`[data-mor="${i.id}"]`);
    if (!st || !out) return;
    const norm = no ? +no.value || 0 : i.norm || 0, n = norm - (+st.value || 0);
    out.textContent = st.value !== "" && n > 0 ? `${n} шт.` : st.value !== "" ? "хватает" : "";
  });
  page.querySelectorAll("[data-mst],[data-mno]").forEach((x) => (x.oninput = calc));
  calc();
  btn.onclick = async () => {
    const items = it.items.filter((i) => i.active).map((i) => ({ id: i.id, stock: page.querySelector(`[data-mst="${i.id}"]`).value,
      norm: page.querySelector(`[data-mno="${i.id}"]`) ? page.querySelector(`[data-mno="${i.id}"]`).value : undefined }));
    try {
      const r = await api("POST", "/api/merch/stock", { items });
      toast(r.order.length ? `Заказать: ${r.order.map((o) => `${o.name} ${o.qty}`).join(", ")}. Задача руководителю в Планере` : "Всего хватает, заказывать нечего 👍");
      render();
    } catch (e) { fail(e); }
  };
}
