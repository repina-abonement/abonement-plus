"use strict";
/* Абонемент Плюс — Чеки 54-ФЗ: каждое поступление на счёт должно иметь чек. */

S.fx = { tab: typeof location !== "undefined" && new URLSearchParams(location.search).get("sber") === "connected" ? "setup" : "list", filter: "problems" };
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
  // три разных состояния, чтобы не путать: не подключено / подключено, но проверки ещё не было / проверено (или ошибка)
  const ent = Object.entries(d.runs);
  const runs = ent.map(([k, r]) => !d.configured[k] ? `<span class="chip muted">${FX_SRC[k]}: не подключено</span>`
    : `<span class="chip ${r ? (r.ok ? "green" : "red") : "yellow"}">${FX_SRC[k]}: ${r ? (r.ok ? "проверено в " + r.ts.slice(11, 16) : "ошибка проверки") : "подключено, проверки ещё не было"}</span>`).join(" ");
  const on = ent.filter(([k]) => d.configured[k]);
  const empty = !on.length ? "Сверка не подключена: проверить чеки пока не по чему"
    : on.some(([, r]) => r && !r.ok) ? "Список пуст, но последняя проверка прошла с ошибкой: данные могут быть неполными"
    : on.every(([, r]) => !r) ? "Проверок ещё не было: данных пока нет"
    : "В текущем списке нет выявленных проблем. Полнота банковской сверки и наличие чеков в ОФД этим не подтверждены";
  const rows = C.filter === "problems" ? d.rows.filter((r) => r.status === "missing" || r.status === "wait" || (r.need === -1 && r.status !== "skip")) : d.rows;
  box.innerHTML = `<div class="chips" style="margin-bottom:10px">${runs}</div>
    <div class="seg" style="margin-bottom:12px"><button data-ff="problems" class="${C.filter === "problems" ? "active" : ""}">Требуют внимания (${d.problems})</button>
      <button data-ff="all" class="${C.filter === "all" ? "active" : ""}">Все за 30 дней</button></div>
    ${rows.length ? `<div class="fx-list">${rows.map((r) => `<div class="fx-item ${r.status === "missing" ? "bad" : ""}">
      <div class="fx-top"><b>${fxRub(r.amount)}</b><span class="chip ${r.status === "missing" ? "red" : r.status === "ok" ? "green" : ""}">${r.need === -1 && r.status !== "skip" ? "Нужен ли чек?" : esc(r.status_label)}</span></div>
      <div class="small">${dShort(r.seen_at)} ${r.seen_at.slice(11, 16)} · ${esc(r.kind_label)} · ${r.source === "tochka" ? "Точка" : "PayKeeper"} №${esc(r.ext_id.slice(-8))}</div>
      ${r.note ? `<div class="small">${esc(r.note)}${r.resolved_by ? ` (${esc(r.resolved_by)})` : ""}</div>` : ""}
      ${["wait", "missing"].includes(r.status) ? `<div class="fx-act">
        ${r.need !== -1 ? `<button class="btn sm primary" data-fxa="done" data-id="${r.id}">Чек пробит</button>` : `<button class="btn sm primary" data-fxa="need" data-id="${r.id}">Чек нужен</button>`}
        ${S.user.role === "admin" ? `<button class="btn sm" data-fxa="skip" data-id="${r.id}">Чек не нужен</button>` : ""}</div>` : ""}
    </div>`).join("")}</div>` : `<p class="small">${C.filter === "problems" ? empty : "Поступлений пока нет"}</p>`}`;
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
  box.innerHTML = `<div id="fx-sber-setup" style="max-width:640px;margin-bottom:18px"></div><div class="sub-form" style="max-width:640px;padding-bottom:90px">
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
  await fxSberSetup(box.querySelector("#fx-sber-setup"));
}


async function fxSberSetup(box) {
  if (!box || S.user.role !== "admin") return;
  let d;
  try { d = await api("GET", "/api/sber/settings"); }
  catch (e) { box.innerHTML = '<p class="small">Сбер Бизнес: не удалось загрузить настройки подключения. Обновите страницу.</p>'; return; }
  const date = value => { const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? "дата не получена" : esc(parsed.toLocaleString("ru-RU", {timeZone:"Europe/Moscow"})) + " МСК"; };
  const masked = Array.isArray(d.accounts_masked) ? d.accounts_masked.map(x => esc(x)).join(", ") : "";
  box.innerHTML = `<details class="panel" id="fx-sber-details"><summary><b>Сбер Бизнес</b> · ${d.connected ? "разрешение на чтение получено" : "подключить выписку"}</summary>
    <p class="small">CRM будет читать поступления по расписанию при закрытом браузере. При подключении выбирайте только чтение выписки и сведений о счетах.</p>
    ${d.connected ? '<p class="small">Разрешение получено. Фактическое чтение выписки проверяется отдельно.</p>' : ''}
    ${(d.maintenance||[]).map(note=>`<p class="small" role="status">${esc(note)}</p>`).join('')}
    <ol class="small"><li>В карточке активированного сервиса Sber API откройте параметры подключения и перенесите client_id и client_secret в поля ниже.</li>
      <li>В разделе Сбера «Сертификаты шифрования» создайте сертификат, задайте пароль и скачайте файл .p12. Выберите его ниже.</li>
      <li>CRM читает все счета, доступные вашему разрешению. При желании укажите ожидаемые номера — CRM проверит, что они доступны. Сохраните настройки и нажмите «Разрешить чтение в Сбере».</li></ol>
    <div class="field"><label for="sber-client-id">client_id из карточки сервиса</label><input id="sber-client-id" type="text" value="${esc(d.client_id || '')}" autocomplete="off" spellcheck="false"></div>
    <div class="field"><label for="sber-client-secret">client_secret</label><input id="sber-client-secret" type="password" value="" autocomplete="new-password" placeholder="${d.client_secret_set ? 'Уже сохранён · оставьте пустым, чтобы сохранить' : 'Вставьте из карточки сервиса'}"></div>
    <div class="field"><label for="sber-accounts">Ожидаемые расчётные счета (необязательно)</label><textarea id="sber-accounts" rows="2" autocomplete="off" placeholder="${d.accounts_set ? 'Оставьте пустым, если список счетов не меняется' : 'Каждый номер счёта с новой строки · пусто = доступные счета'}"></textarea>${masked ? `<span class="small muted">Сохранены счета, заканчивающиеся на: ${masked}</span>` : ''}</div>
    <div class="field"><label for="sber-certificate">Сертификат .p12</label><input id="sber-certificate" type="file" accept=".p12,.pfx,application/x-pkcs12">${d.certificate_set ? `<span class="small muted">Сертификат сохранён${d.certificate_expires_at ? ' · действует до ' + date(d.certificate_expires_at) : ''}. Новый файл выбирайте только для замены.</span>` : ''}</div>
    <div class="field"><label for="sber-certificate-password">Пароль сертификата</label><input id="sber-certificate-password" type="password" value="" autocomplete="new-password" placeholder="Пароль, заданный при создании выбранного файла"></div>
    <details><summary class="small">Адрес возврата для карточки сервиса Сбера</summary><p class="small">Укажите в Сбере этот redirect_uri:</p><input aria-label="Адрес возврата" readonly value="${esc(d.redirect_uri || '')}" style="width:100%"></details>
    <p class="small muted">Секрет и пароль не показываются после сохранения. Файл и реквизиты передаются на сервер CRM через защищённое соединение.</p>
    <div class="row" style="gap:8px;flex-wrap:wrap;margin-top:12px"><button type="button" class="btn" id="sber-save">Сохранить</button><button type="button" class="btn primary" id="sber-authorize">Разрешить чтение в Сбере</button><button type="button" class="btn" id="sber-test">Проверить выписку</button></div>
    <div class="small" role="status" aria-live="polite" id="sber-result" style="margin-top:10px"></div>
  </details>`;
  const el = id => box.querySelector("#" + id);
  const result = el("sber-result");
  const buttons = () => box.querySelectorAll("button");
  const collect = async () => {
    const body = {}, client = el("sber-client-id").value.trim(), secret = el("sber-client-secret").value;
    if (client) body.client_id = client;
    if (secret) body.client_secret = secret;
    const accounts = el("sber-accounts").value.trim();
    if (accounts) body.accounts = accounts;
    const file = el("sber-certificate").files[0], password = el("sber-certificate-password").value;
    if (file) {
      if (!/\.(p12|pfx)$/i.test(file.name) || file.size > 2 * 1024 * 1024) throw new Error("Выберите сертификат .p12 или .pfx размером до 2 МБ");
      body.certificate_base64 = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(",")[1]);
        reader.onerror = () => reject(new Error("Не удалось прочитать сертификат. Выберите файл ещё раз"));
        reader.readAsDataURL(file);
      });
      body.certificate_password = password;
    } else if (password) throw new Error("Выберите файл сертификата, к которому относится пароль");
    return body;
  };
  const run = async action => {
    buttons().forEach(b => b.disabled = true); result.textContent = "Проверяем…";
    try {
      const body = await collect();
      if (Object.keys(body).length) await api("PUT", "/api/sber/settings", body);
      // Secret fields are cleared even when the next step fails; the saved values stay on the server.
      el("sber-client-secret").value = ""; el("sber-certificate-password").value = ""; el("sber-certificate").value = "";
      if (action === "authorize") {
        const r = await api("POST", "/api/sber/authorize", {}), url = new URL(r.url);
        if (url.origin !== "https://sbi.sberbank.ru:9443" || url.pathname !== "/ic/sso/api/v2/oauth/authorize" || url.username || url.password || url.hash) throw new Error("CRM не получила корректный адрес входа в Сбер");
        window.location.assign(url.href);
      } else if (action === "test") {
        const r = await api("POST", "/api/sber/test", {});
        result.textContent = r.note || (r.ok ? "Выписка прочитана" : "Выписка пока не получена");
      } else {
        await fxSberSetup(box); box.querySelector("details").open = true;
        box.querySelector("#sber-result").textContent = "Настройки сохранены. Теперь разрешите чтение в Сбере";
      }
    } catch (e) { result.textContent = e.message || "Не удалось завершить подключение"; }
    finally { buttons().forEach(b => b.disabled = false); }
  };
  el("sber-save").onclick = () => run("save");
  el("sber-authorize").onclick = () => run("authorize");
  el("sber-test").onclick = () => run("test");
}
