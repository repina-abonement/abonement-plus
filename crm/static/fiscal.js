"use strict";
/* Абонемент Плюс — Чеки 54-ФЗ: каждое поступление на счёт должно иметь чек. */

S.fx = { tab: "list", filter: "problems" };
const fxRub = (k) => rub(Math.round(k) / 100);
const FX_SRC = { tochka: "Точка", paykeeper: "PayKeeper", ofd: "Платформа ОФД" };

async function renderFiscal(page) {
  const C = S.fx, admin = S.user.role === "admin";
  let d;
  try { d = await api("GET", "/api/fiscal"); } catch (e) { page.innerHTML = `<p>${esc(e.message)}</p>`; return; }
  page.innerHTML = `<div class="page-head"><h2 class="grow">Чеки</h2>
      ${admin ? `<button class="btn sm" id="fx-run">Проверить сейчас</button>` : ""}</div>
    <div class="seg" style="margin-bottom:14px"><button data-ft="list" class="${C.tab === "list" ? "active" : ""}">Поступления</button>
      ${admin ? `<button data-ft="setup" class="${C.tab === "setup" ? "active" : ""}">Подключение</button>` : ""}</div>
    <div id="fx-body"></div>`;
  page.querySelectorAll("[data-ft]").forEach((b) => (b.onclick = () => step(() => { C.tab = b.dataset.ft; })));
  const run = $("#fx-run");
  if (run) run.onclick = async () => { run.disabled = true; try { const r = await api("POST", "/api/fiscal/run", {}); toast(r.skipped ? "Сверка выключена в «Подключении»" : (r.errors || []).length ? "Есть ошибки: " + r.errors.join("; ") : "Проверено"); render(); } catch (e) { fail(e); run.disabled = false; } };
  if (C.tab === "setup" && admin) return fxSetup($("#fx-body"), d);
  fxList($("#fx-body"), d);
}

function fxList(box, d) {
  const C = S.fx;
  const runs = Object.entries(d.runs).filter(([k]) => d.configured[k]).map(([k, r]) =>
    `<span class="chip ${r && !r.ok ? "red" : ""}">${FX_SRC[k]}: ${r ? (r.ok ? "ок " + r.ts.slice(11, 16) : "ошибка") : "ещё не было"}</span>`).join(" ");
  const rows = C.filter === "problems" ? d.rows.filter((r) => r.status === "missing" || r.status === "wait" || (r.need === -1 && r.status !== "skip")) : d.rows;
  box.innerHTML = `<div class="chips" style="margin-bottom:10px">${runs || "Сверка ещё не подключена"}</div>
    <div class="seg" style="margin-bottom:12px"><button data-ff="problems" class="${C.filter === "problems" ? "active" : ""}">Требуют внимания (${d.problems})</button>
      <button data-ff="all" class="${C.filter === "all" ? "active" : ""}">Все за 30 дней</button></div>
    ${rows.length ? `<div class="fx-list">${rows.map((r) => `<div class="fx-item ${r.status === "missing" ? "bad" : ""}">
      <div class="fx-top"><b>${fxRub(r.amount)}</b><span class="chip ${r.status === "missing" ? "red" : r.status === "ok" ? "green" : ""}">${r.need === -1 && r.status !== "skip" ? "Нужен ли чек?" : esc(r.status_label)}</span></div>
      <div class="small">${dShort(r.seen_at)} ${r.seen_at.slice(11, 16)} · ${esc(r.kind_label)} · ${r.source === "tochka" ? "Точка" : "PayKeeper"} №${esc(r.ext_id.slice(-8))}</div>
      ${r.note ? `<div class="small">${esc(r.note)}${r.resolved_by ? ` (${esc(r.resolved_by)})` : ""}</div>` : ""}
      ${["wait", "missing"].includes(r.status) ? `<div class="fx-act">
        ${r.need !== -1 ? `<button class="btn sm primary" data-fxa="done" data-id="${r.id}">Чек пробит</button>` : `<button class="btn sm primary" data-fxa="need" data-id="${r.id}">Чек нужен</button>`}
        ${S.user.role === "admin" ? `<button class="btn sm" data-fxa="skip" data-id="${r.id}">Чек не нужен</button>` : ""}</div>` : ""}
    </div>`).join("")}</div>` : `<p class="small">${C.filter === "problems" ? "Всё в порядке: по всем поступлениям есть чеки" : "Поступлений пока нет"}</p>`}`;
  box.querySelectorAll("[data-ff]").forEach((b) => (b.onclick = () => step(() => { C.filter = b.dataset.ff; })));
  box.querySelectorAll("[data-fxa]").forEach((b) => (b.onclick = async () => {
    const a = b.dataset.fxa;
    const note = a === "done" ? prompt("Комментарий (например: пробили 04.11 на Эвоторе)", "") : a === "skip" ? prompt("Почему чек не нужен?", "") : "";
    if (note === null) return;
    try { await api("POST", `/api/fiscal/${b.dataset.id}/resolve`, { action: a, note }); toast("Записано"); render(); } catch (e) { fail(e); }
  }));
}

async function fxSetup(box, d) {
  const v = d.settings;
  let users = [];
  try { users = (await api("GET", "/api/users")).filter((u) => u.active && u.role !== "coach"); } catch (e) { /* без списка */ }
  const opt = (sel) => `<option value="">-</option>` + users.map((u) => `<option value="${u.id}" ${String(u.id) === String(sel) ? "selected" : ""}>${esc(u.name)}</option>`).join("");
  const f = (k, label, ph = "", type = "text") => `<div class="field"><label>${label}</label><input data-fx="${k}" type="${type}" value="${esc(v[k] || "")}" placeholder="${esc(ph)}" autocomplete="off"></div>`;
  box.innerHTML = `<div class="sub-form" style="max-width:640px;padding-bottom:90px">
    <label class="fx-check"><input type="checkbox" data-fxc="fx_enabled" ${v.fx_enabled === "1" ? "checked" : ""}><span><b>Сверка включена</b>, проверяет каждые 10 минут</span></label>
    <h3>Точка</h3>${f("fx_tochka_token", "JWT-токен (только чтение счетов и выписок)", "вставьте токен")}
    ${f("fx_inn", "Ваш ИНН (переводы себе не требуют чека)", "12 цифр")}
    <h3>PayKeeper</h3>${f("fx_pk_server", "Адрес кабинета", "https://repina-erp.server.paykeeper.ru")}
    ${f("fx_pk_user", "Логин отдельного пользователя для API")}${f("fx_pk_password", "Пароль этого пользователя", "", "password")}
    <label class="fx-check"><input type="checkbox" data-fxc="fx_auto_retry" ${v.fx_auto_retry === "1" ? "checked" : ""}><span>Если касса PayKeeper не пробила чек - отправлять повторно автоматически (до 2 раз)</span></label>
    <h3>Платформа ОФД</h3>${f("fx_ofd_token", "API-токен", "выдаёт поддержка ОФД")}
    <h3>Кого дёргать</h3>
    <div class="field"><label>Контролёр (первое напоминание через ${esc(v.fx_remind_hours || "2")} ч без чека)</label><select data-fx="fx_controller">${opt(v.fx_controller)}</select></div>
    <div class="field"><label>Владелец (если чек не пробит до 12:00 следующего дня, сбои связи, непонятные поступления)</label><select data-fx="fx_owner">${opt(v.fx_owner)}</select></div>
    <p class="small">Сообщения приходят в чат «Служба заботы» с отметкой человека и на рабочий стол CRM. Ключи хранятся только на сервере, на экране видны последние 4 знака</p>
    <div class="row" style="gap:8px;flex-wrap:wrap"><button class="btn primary" id="fx-save">Сохранить</button><button class="btn" id="fx-test">Проверить подключение</button></div>
    <div id="fx-test-res" class="small" style="margin-top:10px"></div></div>`;
  const collect = () => {
    const o = {};
    box.querySelectorAll("[data-fx]").forEach((i) => (o[i.dataset.fx] = i.value));
    box.querySelectorAll("[data-fxc]").forEach((i) => (o[i.dataset.fxc] = i.checked ? "1" : "0"));
    return o;
  };
  $("#fx-save").onclick = async () => { try { await api("PUT", "/api/fiscal/settings", collect()); toast("Сохранено"); render(); } catch (e) { fail(e); } };
  $("#fx-test").onclick = async () => {
    try {
      await api("PUT", "/api/fiscal/settings", collect());
      const r = await api("POST", "/api/fiscal/test", {});
      $("#fx-test-res").innerHTML = Object.keys(r).length ? Object.entries(r).map(([k, t]) => `${FX_SRC[k]}: ${esc(t)}`).join("<br>") : "Ничего не подключено";
    } catch (e) { fail(e); }
  };
}
