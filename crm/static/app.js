"use strict";
/* Абонемент Плюс — интерфейс без сборки и зависимостей. */

/* Календарь по нажатию на любую часть поля, включая динамические формы. */
document.addEventListener('click', event => {
  const input=event.target;
  if(!(input instanceof HTMLInputElement)||!['date','month','datetime-local'].includes(input.type)
      ||input.disabled||input.readOnly||typeof input.showPicker!=='function')return;
  try {
    input.focus({preventScroll:true});
    input.showPicker();
    event.preventDefault();
  } catch (_) {
    // Браузеры без разрешения на showPicker сохраняют обычное поведение поля.
  }
});

/* Новый дизайн (проба): хранится в этом браузере, по умолчанию выключен */
function applyTheme(t) { document.documentElement.classList.toggle("theme-soft", t === "soft"); }
try { applyTheme(localStorage.getItem("crm.theme")); } catch (_) {}
const S = { meta: null, user: null, view: "sales", journal: { group: null, date: null }, search: "" };
const $ = (sel, el = document) => el.querySelector(sel);
const app = $("#app");

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const KEYBOARD_EN = "`qwertyuiop[]asdfghjkl;'zxcvbnm,.";
const KEYBOARD_RU = "ёйцукенгшщзхъфывапролджэячсмитьбю";
function keyboardLayoutVariants(value) {
  const source = String(value || "").trim(), swap = (from, to) => [...source].map((ch) => {
    const lower = ch.toLowerCase(), index = from.indexOf(lower);
    if (index < 0) return ch;
    const result = to[index]; return ch === lower ? result : result.toUpperCase();
  }).join("");
  // Пустой запрос означает «показать все». Без этого варианта поисковые
  // выпадающие списки групп и филиалов открывались пустыми до первого ввода.
  return source ? [...new Set([source, swap(KEYBOARD_EN, KEYBOARD_RU), swap(KEYBOARD_RU, KEYBOARD_EN)].filter(Boolean))] : [""];
}
function searchTextMatches(text, query) {
  const haystack = String(text || "").toLocaleLowerCase("ru").replace(/ё/g, "е");
  return keyboardLayoutVariants(query).some((value) => haystack.includes(value.toLocaleLowerCase("ru").replace(/ё/g, "е")));
}
const rub = (n) => Number(n || 0).toLocaleString("ru-RU") + " ₽";
const MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];
const monthLabel = (ym) => { const [y, m] = ym.split("-").map(Number); return `${MONTHS[m - 1]} ${y}`; };
const dShort = (iso) => { if (!iso) return ""; const [y, m, d] = iso.slice(0, 10).split("-"); return `${d}.${m}`; };
const dtShort = (iso) => iso ? `${dShort(iso)} ${iso.slice(11, 16)}` : "";
const birthDisplay = (value) => /^\d{4}-\d{2}-\d{2}$/.test(value || "") ? value.split("-").reverse().join(".") : (value || "");
function birthIso(value) {
  const match = String(value || "").trim().match(/^(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})$/);
  if (!match) return null;
  const day = +match[1], month = +match[2], year = +match[3], candidate = new Date(year, month - 1, day);
  if (year < 1900 || candidate.getFullYear() !== year || candidate.getMonth() !== month - 1 || candidate.getDate() !== day) return null;
  const iso = `${year}-${String(month).padStart(2,"0")}-${String(day).padStart(2,"0")}`;
  return iso <= S.meta.today ? iso : null;
}
const WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const PAY_METHODS = {cash:"Наличные",paykeeper:"По ссылке PayKeeper",paykeeper_manual:"По ссылке PayKeeper",card_branch:"Картой на филиале через терминал Сбера",sber_qr_branch:"По QR Сбера на филиале",tochka_qr_link:"По QR/ссылке Точки от администратора",card:"Картой (канал не уточнён)",sbp:"СБП (банк не уточнён)",online:"Онлайн-оплата (канал не уточнён)",transfer:"Перевод",unknown:"Перенесено",opening_balance:"Исторический остаток — оплачено ранее",balance:"Перенос с баланса",refund:"Возврат",refund_pending:"Ожидает возврата"};
const PAY_MANUAL_METHODS = ['cash','paykeeper_manual','card_branch','sber_qr_branch','tochka_qr_link','transfer'];
const PAY_INTERNAL = ["unknown", "opening_balance", "balance", "refund", "refund_pending"];  // ставит сама CRM, при приёме оплаты не выбираются

async function api(method, url, body) {
  const opt = { method, headers: {}, credentials: "same-origin" };
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(url, opt);
  let data = null;
  try { data = await r.json(); } catch (_) { /* пусто */ }
  if (r.status === 401 && url !== "/api/login") { renderLogin(); throw new Error("Нужно войти"); }
  if (r.status === 422 && data && data.need && !opt.noNeed) {
    // не хватает обязательных данных (перед пробным или продажей): спросим и повторим то же действие
    if (await askRequired(data.need)) return api(method, url, body);
    throw Object.assign(new Error("Действие не выполнено: не хватает данных"), { status: 422, silent: true });
  }
  if (!r.ok) throw Object.assign(new Error((data && data.error) || `Не удалось выполнить запрос (HTTP ${r.status})`) , { status: r.status });
  if(method!=='GET'&&/^(\/api\/(settings|prices|programs|groups|branches|closures|discounts)(\/|$)|\/api\/tasks\/settings$)/.test(url))window.dispatchEvent(new Event('crm:rules-changed'));
  return data;
}

/* Окошко «Не хватает данных»: имя и фамилия ребёнка, дата рождения, имя и телефон родителя, для продажи ещё почта */
function askRequired(need) {
  return new Promise((resolve) => {
    const types = { child_name: "text", birth: "text", parent_name: "text", phone: "tel", email: "email" };
    const ph = { child_name: "Например: Петя Иванов", parent_name: "Например: Анна", phone: "+7 900 123-45-67", email: "Например: anna@mail.ru" };
    const wrap = document.createElement("div");
    wrap.className = "need-wrap";
    wrap.innerHTML = `<form class="need-card"><h3>${need.kind === "sale" ? "Перед продажей абонемента" : "Перед записью на пробное"}</h3>
      <p class="small muted">${need.kind === "sale" ? "Заполните данные клиента. Если почты пока нет, её можно добавить позже" : "Уточните у родителя и заполните. Без этого на пробное не записать"}</p>
      ${need.fields.map((k) => `<div class="field ${need.missing.includes(k)&&!need.optional?.includes(k) ? "need-miss" : ""}"><label>${esc(need.labels[k])}${need.optional?.includes(k) ? " · можно позже" : need.missing.includes(k) ? " · нужно заполнить" : ""}</label>
        ${k === "birth" ? `<div class="need-date-row"><input name="birth" type="text" inputmode="numeric" autocomplete="bday" value="${esc(birthDisplay(need.values[k] || ""))}" placeholder="ДД.ММ.ГГГГ" required><input type="date" class="need-date-picker" data-birth-picker max="${esc(S.meta.today)}" aria-label="Выбрать дату рождения в календаре"></div>` : `<input name="${k}" type="${types[k]}" value="${esc(need.values[k] || "")}" placeholder="${esc(ph[k] || "")}" ${k === "email" ? 'autocomplete="off"' : ""} ${need.optional?.includes(k)?"":"required"}>`}</div>`).join("")}
      <div class="need-err small"></div>
      <div class="row" style="gap:10px;margin-top:6px"><button class="btn primary" style="flex:1">Сохранить и продолжить</button><button type="button" class="btn" data-ncancel style="flex:0 0 auto">Отмена</button></div>
      <button type="button" class="btn ghost" data-nask style="width:100%;margin-top:8px">💬 Данных нет: попросить у родителя</button></form>`;
    document.body.appendChild(wrap);
    const f = wrap.querySelector("form");
    splitChildNameFields(f,"child_name",true);
    const birth = f.elements.birth, birthPicker = f.querySelector("[data-birth-picker]");
    if (birth && birthPicker) {
      birthPicker.onchange = () => { birth.value = birthDisplay(birthPicker.value); birth.focus(); };
      birth.onblur = () => { const iso = birthIso(birth.value); if (iso) birth.value = birthDisplay(iso); };
    }
    const first = f.querySelector(".need-miss input:not([type=hidden])") || f.querySelector("input:not([type=hidden])"); if (first) setTimeout(() => first.focus(), 50);
    const done = (v) => { wrap.remove(); resolve(v); };
    wrap.querySelector("[data-ncancel]").onclick = () => done(false);
    // данных нет: готовим вежливый запрос родителю ровно о том, чего не хватает, и вставляем его в поле ответа
    wrap.querySelector("[data-nask]").onclick = async () => {
      const miss = need.fields.filter((k) => need.missing.includes(k) || !f.elements[k].value.trim());
      // выбрано время: держим место 10 минут, пока родитель присылает данные
      const sl = S.pendingSlot;
      if (sl && sl.group_id) {
        try { const h = await api("POST", "/api/trial-hold", { student_id: need.student_id, group_id: sl.group_id, date: sl.date, time: sl.time }); sl.until = h.until; sl.minutes = h.minutes; }
        catch (e) { return fail(e); }
      }
      const nm = f.elements.child_name ? f.elements.child_name.value.trim() : "";
      const text = askRequiredText(need.kind, miss, nm && !nm.includes(" "));
      const box = [...document.querySelectorAll("#chat-text, #cm-text")].find((x) => x.offsetParent);
      if (box) {
        // после выбора времени прошлый текст (варианты дат) уже ушёл родителю: заменяем, а не дописываем
        box.value = S.pendingSlot || !box.value.trim() ? text : box.value.trim() + "\n\n" + text;
        box.rows = Math.min(8, box.value.split("\n").length + 1);
        done(false); box.focus(); box.scrollIntoView({ block: "center" });
        toast(sl && sl.until ? `Место держим до ${sl.until.slice(11, 16)}. Запрос вставлен в поле ответа: нажмите «Отправить»` : "Запрос вставлен в поле ответа: проверьте и нажмите «Отправить»");
      } else {
        try { await copyCrmText(text); toast("Текст запроса скопирован: вставьте его в переписку с родителем"); }
        catch (_) { prompt("Скопируйте текст запроса родителю", text); }
        done(false);
      }
    };
    f.onsubmit = async (e) => {
      e.preventDefault();
      const body = Object.fromEntries(need.fields.map((k) => [k, f.elements[k].value.trim()]));
      if (body.birth) {
        const iso = birthIso(body.birth);
        if (!iso) { wrap.querySelector(".need-err").textContent = "Введите дату рождения в формате ДД.ММ.ГГГГ"; f.elements.birth.focus(); return; }
        body.birth = iso;
      }
      try {
        const r = await fetch(`/api/students/${need.student_id}/required`, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) { wrap.querySelector(".need-err").textContent = d.error || "Ошибка"; return; }
        toast("Данные сохранены"); done(true);
      } catch (_) { wrap.querySelector(".need-err").textContent = "Нет связи, попробуйте ещё раз"; }
    };
  });
}

function askRequiredText(kind, miss, onlySurname = false) {
  const ask = { child_name: onlySurname ? "фамилию ребёнка" : "имя и фамилию ребёнка", birth: "дату рождения ребёнка", parent_name: "как к вам можно обращаться",
    phone: "номер телефона для связи", email: "электронную почту: на неё придёт чек об оплате" };
  const list = (miss.length ? miss : ["child_name", "birth"]).map((k) => "• " + (ask[k] || k)).join("\n");
  const sl = kind !== "sale" && S.pendingSlot;
  // подробности о пробном (дата, место, что взять) уйдут отдельным сообщением сами, когда админ запишет ребёнка
  if (sl) return `Отлично! Место за вами держим ${sl.minutes || 10} минут. Чтобы записать ребёнка на пробное, пришлите, пожалуйста:\n${list}\nСпасибо! 😊`;
  return kind === "sale"
    ? `Здравствуйте! Чтобы оформить абонемент, пришлите, пожалуйста:\n${list}\nСпасибо!`
    : `Здравствуйте! Чтобы записать ребёнка на пробное занятие, пришлите, пожалуйста:\n${list}\nСпасибо! Как только получим, сразу запишем 😊`;
}

/* Руководитель: всё красное с рабочего стола лично в Telegram (crm/ownerbot.py) */
async function renderOwnerTg(box) {
  let d; try { d = await api("GET", "/api/me/owner-tg"); } catch (e) { box.innerHTML = `<h3>🔴 Красное в Telegram</h3><div class="error">${esc(e.message)}</div>`; return; }
  const head = `<h3>🔴 Красное в Telegram</h3><div class="small muted" style="margin-bottom:8px">Всё, что горит красным на вашем рабочем столе, бот школы пришлёт вам лично. Пропущенные звонки и заявки: только если админы не обработали их за 30 минут. Ночью с 22:00 до 8:00 бот молчит, утром присылает сводку</div>`;
  if (!d.bot) { box.innerHTML = head + `<div class="small">Сначала подключите бота школы: Настройки → «Связь с клиентами» → Telegram</div>`; return; }
  if (d.linked) {
    box.innerHTML = head + `<div>✅ Подключено: пишет бот @${esc(d.bot)}</div><button class="btn sm" id="otg-off" style="margin-top:8px">Отключить</button>`;
    $("#otg-off").onclick = async () => { await api("POST", "/api/me/owner-tg/unlink", {}).catch(fail); renderOwnerTg(box); };
    return;
  }
  box.innerHTML = head + `<button class="btn primary" id="otg-on">Подключить</button><div id="otg-code"></div>`;
  $("#otg-on").onclick = async () => {
    try {
      const r = await api("POST", "/api/me/owner-tg/code", {});
      $("#otg-code").innerHTML = `<ol class="small" style="padding-left:18px;line-height:1.7;margin-top:10px">
        <li>Откройте в Telegram бота школы: <a href="https://t.me/${esc(d.bot)}" target="_blank" rel="noopener">@${esc(d.bot)}</a></li>
        <li>Отправьте ему сообщение: <b>/crm_me ${esc(r.code)}</b> <button class="btn sm" data-copy="/crm_me ${esc(r.code)}">Копировать</button></li>
        <li>Бот ответит «✅ Готово». Код действует 30 минут</li>
        <li>Вернитесь сюда и нажмите <button class="btn sm" id="otg-check">Проверить</button></li></ol>`;
      $("#otg-code").querySelector("[data-copy]").onclick = (e) => { copyCrmText(e.target.dataset.copy).then(() => toast("Скопировано"), () => {}); };
      $("#otg-check").onclick = () => renderOwnerTg(box);
    } catch (e) { fail(e); }
  };
}

/* Старая переписка из Telegram школы: файл экспорта Telegram Desktop (JSON). Браузер ужимает его до текста и шлёт в CRM */
function renderTgImport(box) {
  box.innerHTML = `<h3>💬 Старая переписка из Telegram</h3>
    <div class="small muted" style="margin-bottom:8px">Переписка школьного Telegram до подключения к CRM. В карточки попадут только чаты с теми, кто есть в CRM: по номеру телефона из контактов Telegram, а если номера нет, по фамилии и имени ребёнка в названии чата. Фото и файлы не переносятся, только текст. Повторная загрузка ничего не задвоит</div>
    <details style="margin-bottom:8px"><summary class="small"><b>Как сделать файл (Telegram на компьютере)</b></summary><ol class="small" style="padding-left:18px;line-height:1.6">
      <li>Telegram → Настройки → Продвинутые настройки → «Экспорт данных Telegram»</li>
      <li>Оставьте галочки «Список контактов» и «Личные чаты», остальное выключите (фото, видео, файлы не нужны)</li>
      <li>Внизу «Формат»: «Машиночитаемый JSON» → «Экспортировать»</li>
      <li>Когда закончится, нажмите «Показать мои данные»: в папке будет файл <b>result.json</b></li></ol></details>
    <label class="btn primary">Выбрать result.json<input type="file" id="tg-file" accept=".json,application/json" hidden></label>
    <div id="tg-res" class="small" style="margin-top:8px"></div>`;
  $("#tg-file").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const res = $("#tg-res");
    res.textContent = `Читаю файл (${Math.round(f.size / 1e6)} МБ)…`;
    try {
      const raw = JSON.parse(await f.text());
      const me = String((raw.personal_information || {}).user_id || "");
      const txt = (t) => typeof t === "string" ? t : Array.isArray(t) ? t.map((x) => typeof x === "string" ? x : x.text || "").join("") : "";
      const contacts = (((raw.contacts || {}).list) || []).map((c) => ({ name: [c.first_name, c.last_name].filter(Boolean).join(" "), phone: c.phone_number || "" }));
      const chats = (((raw.chats || {}).list) || []).filter((c) => c.type === "personal_chat").map((c) => ({
        id: c.id, name: c.name || "",
        msgs: (c.messages || []).filter((m) => m.type === "message").map((m) => [m.id, m.date, String(m.from_id || "") === "user" + me, txt(m.text),
          m.media_type || (m.photo ? "фото" : m.file ? "файл" : "")]),
      }));
      if (!chats.length) { res.innerHTML = `<span class="error">В файле нет личных чатов. Проверьте, что выбран формат «Машиночитаемый JSON» и галочка «Личные чаты»</span>`; return; }
      res.textContent = `Загружаю ${chats.length} чатов в CRM…`;
      const r = await api("POST", "/api/import/telegram", { contacts, chats });
      res.innerHTML = `✅ Готово. Чатов в файле: ${r.chats}. Перенесено в карточки: <b>${r.clients}</b> (из них по имени ребёнка: ${r.by_name}), сообщений ${r.messages}.<br>
        Не клиенты CRM: ${r.not_clients}. Не нашли ни по номеру, ни по имени: ${r.no_phone}. Подходят разные семьи (не перенесены, чтобы не перепутать): ${r.ambiguous}`;
    } catch (err) { res.innerHTML = `<span class="error">${esc(err.message || "Не получилось прочитать файл")}</span>`; }
  };
}

function renderChatDayImport(box) {
  box.innerHTML = `<h3>📥 Проверенные факты из СЗ и отчётов тренеров</h3>
    <div class="small muted" style="margin-bottom:8px">Загружает проверенные факты одного дня. Подтверждённые в СЗ или отчёте тренеров продажи оформляются вместе с абонементом и платежом; сведения об оплатах из ERP не используются. Существующие карточки ищутся по полному имени и телефону; неоднозначные или отсутствующие совпадения пропускаются. Повторная загрузка не создаёт записи повторно</div>
    <div class="row"><div class="field"><label>Дата</label><input type="date" data-chat-day-date value="2026-11-22"></div><div class="field"><label>Файлы</label><label class="btn primary">Выбрать оба result.json<input type="file" data-chat-day-file accept=".json,application/json" multiple hidden></label></div></div>
    <div data-chat-day-result class="small" style="margin-top:10px"></div>`;
  box.querySelector('[data-chat-day-file]').onchange=async event=>{
    const files=[...event.target.files],out=box.querySelector('[data-chat-day-result]');if(!files.length)return;
    out.textContent='Переношу проверенные факты…';
    try{
      const raw=await Promise.all(files.map(async file=>JSON.parse(await file.text()))),day=box.querySelector('[data-chat-day-date]').value;
      let payload=raw.find(x=>Array.isArray(x.events));
      if(!payload){if(raw.length!==2)throw new Error('Выберите одновременно два result.json: СЗ и ТРЕНЕРЫ');payload=chatDayPayload(raw,day)}
      const r=await api('POST','/api/import/chat-day',payload);
      const rows=(r.applied||[]).map(x=>`<div class="list-row"><div><b>${esc(x.name)}</b><div>${esc(x.added)}</div><div class="muted">${(x.tasks||[]).length?`Задача: ${esc(x.tasks[0].title)} · ${esc(dShort(x.tasks[0].due_at))}`:'Открытая задача создана автоматически'}</div></div></div>`).join('');
      out.innerHTML=`<p>✅ Внесено: <b>${r.applied.length}</b> · уже было: ${r.already.length} · не найдено: ${r.unmatched.length} · неоднозначно: ${r.ambiguous.length} · ошибки: ${r.errors.length}</p>${rows}${r.unmatched.length?`<p class="error">Не создавались новые карточки: ${r.unmatched.map(x=>esc(x.name)).join(', ')}</p>`:''}${r.ambiguous.length?`<p class="error">Нужно сверить совпадения: ${r.ambiguous.map(x=>esc(x.name)).join(', ')}</p>`:''}${r.errors.length?`<p class="error">Ошибки: ${r.errors.map(x=>esc(x.name+': '+x.error)).join('; ')}</p>`:''}`;
    }catch(error){out.innerHTML=`<span class="error">${esc(error.message||'Не удалось выполнить перенос')}</span>`}
  };
}

function renderChatBulkImport(box) {
  box.innerHTML = `<h3>📚 Автоматический перенос СЗ и ТРЕНЕРЫ с 1 ноября</h3>
    <div data-chat-server-status class="note small">Читаю фактический результат серверного переноса…</div>
    <details style="margin-top:10px"><summary>Добавить новые выгрузки после 24 ноября</summary>
    <div class="small muted" style="margin:8px 0">Этот выбор нужен только для ещё не загруженных новых сообщений. Повторно выбирать уже обработанные файлы 01–24 ноября не нужно. CRM объединит пересекающиеся выгрузки, уберёт повторы и сначала покажет предпросмотр. Неоднозначные фамилии не угадываются.</div>
    <label class="btn primary">Выбрать новые result.json<input type="file" data-chat-bulk-files accept=".json,application/json" multiple hidden></label></details>
    <div data-chat-bulk-result class="small" style="margin-top:10px"></div>`;
  let exports=[],plan=null;
  const input=box.querySelector('[data-chat-bulk-files]'),out=box.querySelector('[data-chat-bulk-result]'),server=box.querySelector('[data-chat-server-status]');
  api('GET','/api/erp/care-migration/report').then(r=>{const t=r.totals||{},tr=r.trainer||{},left=(r.unresolved||[]);server.innerHTML=`<b>${r.state==='complete'?'✅ Серверный перенос завершён':'🔄 Серверный перенос продолжает сверку'}</b><div>Факты СЗ: внесено ${t.applied||0} · уже было ${t.already||0} · осталось без точной карточки ${t.unmatched||0} · неоднозначно ${t.ambiguous||0} · ошибки ${t.errors||0}</div><div>Остаток ТРЕНЕРЫ: добавлено ${tr.added||0} · уже было ${tr.already||0} · требует точного имени ${(tr.unresolved||[]).length}</div>${left.length?`<details><summary>Что ещё требует точных данных (${left.length})</summary>${left.slice(0,100).map(x=>`<div>${esc(x.name||x.source_id)}${x.reason?' — '+esc(x.reason):''}</div>`).join('')}</details>`:''}`}).catch(error=>{server.innerHTML=`<span class="error">Не удалось прочитать серверный отчёт: ${esc(error.message||'ошибка')}</span>`});
  const summary=r=>{const ready=(r.attendance||[]).filter(x=>!x.already);return `<div class="note"><b>Период ${dShort(r.period.from)}–${dShort(r.period.to)}</b><div>Сообщений: СЗ <b>${r.messages?.care ?? '—'}</b> · ТРЕНЕРЫ <b>${r.messages?.trainers ?? '—'}</b></div><div>Строк посещений: готово <b>${r.trainer.ready}</b> · уже было ${r.trainer.already} · требуют сверки ${r.trainer.unresolved.length}</div><div>Посещений привязано через ERP: <b>${r.trainer.erp_attendance_rows || 0}</b> · новых карточек будет создано не более <b>${r.trainer.create_from_erp || 0}</b> · доступно профилей ERP: ${r.erp?.profiles || 0} из ${r.erp?.files || 0} файлов</div><div>Клиентских сообщений СЗ: есть хотя бы одно перенесённое событие <b>${r.care.with_events}</b> · пока без событий <b>${r.care.without_events}</b></div>${ready.length?`<details><summary>Что именно будет перенесено (${ready.length})</summary>${ready.map(x=>`<div>№${esc(x.message_id)} · ${esc(dShort(x.date))} ${esc(x.time)} · ${esc(x.name)} → <b>${esc(x.student_name)}</b>${x.create_from_erp?' · новая карточка из ERP':''}</div>`).join('')}</details>`:''}${r.trainer.unresolved.length?`<details><summary>Кого нельзя привязать автоматически</summary>${r.trainer.unresolved.slice(0,100).map(x=>`<div>№${esc(x.message_id)} · ${esc(dShort(x.date))} ${esc(x.time)} · ${esc(x.name)} — ${esc(x.reason||'нужна сверка')}</div>`).join('')}</details>`:''}</div>`};
  input.onchange=async event=>{exports=[];plan=null;const files=[...event.target.files];if(!files.length)return;out.textContent='Читаю и объединяю выгрузки…';try{exports=await Promise.all(files.map(async f=>JSON.parse(await f.text())));plan=await api('POST','/api/import/chat-bulk/plan',{exports});out.innerHTML=summary(plan)+`<div class="actions" style="margin-top:10px"><button class="btn" data-chat-bulk-download>Скачать отчёт проверки</button><button class="btn primary" data-chat-bulk-apply ${plan.trainer.ready?'':'disabled'}>Перенести ${plan.trainer.ready} подтверждённых посещений</button></div><p class="small muted">Кнопка переносит только однозначные посещения. Неразобранные сообщения СЗ не помечаются завершёнными</p>`;const download=out.querySelector('[data-chat-bulk-download]');if(download)download.onclick=()=>{const report={period:plan.period,messages:plan.messages,trainer:plan.trainer,erp:plan.erp,attendance:(plan.attendance||[]).map(x=>({message_id:x.message_id,date:x.date,time:x.time,source_name:x.name,student_id:x.student_id,student_name:x.student_name,already:x.already,create_from_erp:x.create_from_erp?{erp_id:x.create_from_erp.erp_id,name:x.create_from_erp.name,group:x.create_from_erp.group}:null}))};const blob=new Blob([JSON.stringify(report,null,2)],{type:'application/json'}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`Проверка-переноса-${plan.period.from}-${plan.period.to}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)};const apply=out.querySelector('[data-chat-bulk-apply]');if(apply)apply.onclick=async()=>{apply.disabled=true;try{const r=await api('POST','/api/import/chat-bulk/apply',{exports,token:plan.token});toast(`Добавлено посещений: ${r.added}`);plan=await api('POST','/api/import/chat-bulk/plan',{exports});out.innerHTML=summary(plan)+`<p>✅ Однозначные посещения перенесены. Остаток СЗ и спорные строки сохранены в контроле</p>`}catch(error){fail(error);apply.disabled=false}}}catch(error){out.innerHTML=`<span class="error">${esc(error.message||'Не удалось проверить выгрузки')}</span>`}};
}

function chatDayPayload(exports,day){
  const flat=value=>typeof value==='string'?value:Array.isArray(value)?value.map(x=>typeof x==='string'?x:(x&&x.text)||'').join(''):'';
  const allChats=exports.map(x=>({name:x.name||'',messages:(x.messages||[]).map(m=>({...m,text:flat(m.text)}))}));
  const chats=allChats.map(x=>({...x,messages:x.messages.filter(m=>String(m.date||'').startsWith(day))}));
  const care=chats.find(x=>/служба заботы/i.test(x.name));if(!care)throw new Error('Не найден экспорт чата «Служба заботы»');
  const allCare=allChats.find(x=>/служба заботы/i.test(x.name));
  const word=value=>String(value||'').toLocaleLowerCase('ru').replace(/ё/g,'е').replace(/[^а-яa-z]/g,''),aliases={миша:'михаил',женя:'евгений',даня:'даниил',саша:'александр',дима:'дмитрий',кисилев:'киселев'},forms={михаил:'(?:михаил|миша)',евгений:'(?:евгений|женя)',даниил:'(?:даниил|даня)',александр:'(?:александр|саша)',дмитрий:'(?:дмитрий|дима)',киселев:'(?:киселев|кисилев)'};
  const identityFor=name=>{const wanted=String(name||'').split(/\s+/).map(word).map(x=>aliases[x]||x).filter(Boolean);if(wanted.length===1){const candidates=[];for(const m of (allCare?.messages||[])){for(const hit of String(m.text||'').matchAll(/([А-ЯЁ][а-яё]+)\s+([А-ЯЁ][а-яё]+)\s+(?=\d{1,2}\.\d{1,2}\.(?:\d{2}|\d{4}))/g)){if(![word(hit[1]),word(hit[2])].map(x=>aliases[x]||x).includes(wanted[0]))continue;const full=hit[1]+' '+hit[2],found=identityFor(full);if(found)candidates.push({...found,canonical_name:full})}}const unique=[...new Map(candidates.map(x=>[x.phone,x])).values()];return unique.length===1?unique[0]:null}if(wanted.length<2)return null;const a=forms[wanted[0]]||wanted[0],b=forms[wanted[1]]||wanted[1],namePattern=new RegExp(`(?:^|[^а-яa-z])(?:${a}\\s+${b}|${b}\\s+${a})(?=$|[^а-яa-z])`,'i');
    for(const m of (allCare?.messages||[])){const raw=String(m.text||''),normalized=raw.toLocaleLowerCase('ru').replace(/ё/g,'е'),found=namePattern.exec(normalized);if(!found)continue;const anchor=found.index,near=raw.slice(Math.max(0,anchor-40),anchor+500);
      const birth=near.match(/\b(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})\b/),phone=near.match(/(?:\+?7|8)[\s()\-]*\d{3}[\s()\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}/);if(!birth||!phone)continue;
      const year=birth[3].length===2?'20'+birth[3]:birth[3],iso=`${year}-${birth[2].padStart(2,'0')}-${birth[1].padStart(2,'0')}`,parent=(near.match(/мама\s+([А-ЯЁ][а-яё]+)/i)||near.match(/(?:\+?7|8)[\d\s()\-]{9,20}\s+(?:мама\s+)?([А-ЯЁ][а-яё]+)/i)||[])[1];if(!parent)continue;
      const digits=phone[0].replace(/\D/g,'').replace(/^8/,'7'),source=(near.match(/источник\s*:\s*([^\n]+)/i)||[])[1]?.trim()||'';return {birth:iso,parent_name:parent,phone:'+'+digits,...(source?{source}:{}),create_if_missing:true};}return null};
  const events=[],dateOf=value=>day.slice(0,5)+value.split('.').reverse().join('-'),plus=(value,n)=>{const d=new Date(value+'T00:00:00Z');d.setUTCDate(d.getUTCDate()+n);return d.toISOString().slice(0,10)};
  const add=(m,event)=>{const suffix=event.suffix||'';delete event.suffix;const identity=identityFor(event.student_name),time=String(event.note||'').match(/\b(\d{1,2}:\d{2})\b/);if(identity){const original=String(event.student_name||'').trim();event={...identity,...(time?{trial_time:time[1].padStart(5,'0')}:{}) ,...event};if(!original.includes(' ')&&identity.canonical_name)event.student_name=identity.canonical_name;delete event.canonical_name}if(/Сергиевск/i.test(event.student_name||''))event={...event,student_name:'Евгений Сергиевский',erp_id:'182330',erp_create_if_missing:true,erp_group_name:'Небо НУП ср/вс 16:45 возраст 3-4',erp_group_time:'16:45',erp_branch_hint:'Небо',clarification_task_title:'Заполнить данные семьи Сергиевского из ERP',clarification_task_note:'Карточка восстановлена по ERP ID 182330. Добавить дату рождения, имя приоритетного родителя и телефон из карточки ERP или рабочего чата, не создавая второго клиента'};events.push({source_id:'sz:'+m.id+suffix,source_chat:care.name,source_at:String(m.date||'').slice(0,19),...event})};
  for(const m of care.messages){const t=m.text.replace(/\r/g,'');let q;
    if(/Оплата абонемента (?:на|за) ноябрь и декабрь/i.test(t)){
      const sale=/(?:Мастердеев\s+Денис|Денис\s+Мастердеев)/i.test(t)?{name:'Мастердеев Денис',time:'17:45',tariff:'fixed',amount:11880,parts:[['2026-11',2160,['2026-11-25','2026-11-29']],['2026-12',9720,['2026-12-02','2026-12-06','2026-12-09','2026-12-13','2026-12-16','2026-12-20','2026-12-23','2026-12-27','2026-12-30']]]}:/(?:Микаладзе\s+Леон|Леон\s+Микаладзе)/i.test(t)?{name:'Микаладзе Леон',time:'16:45',tariff:'flex',amount:9920,parts:[['2026-11',2480,['2026-11-25','2026-11-29']],['2026-12',7440,['2026-12-02','2026-12-06','2026-12-09','2026-12-13','2026-12-16','2026-12-20']]]}:null;
      if(sale)add(m,{suffix:':subscription-sale',student_name:sale.name,event_kind:'subscription_sale',sale_date:day,paid_at:String(m.date||'').slice(0,19),lesson_time:sale.time,weekdays:[2,6],tariff:sale.tariff,discount_percent:20,amount:sale.amount,payment_method:'card_branch',components:sale.parts.map(([month,price,dates])=>({month,price,dates,dates_reason:'Даты и скидка 20% подтверждены сообщением СЗ'})),note:`Оплачен абонемент за ноябрь и декабрь, группа Ср/Вс ${sale.time}, ${sale.amount} ₽ картой в филиале`});
    }
    if((q=t.match(/ПУ\s+([^\n]+?)\s+продажа разовое\s+(\d+)\s+карта[\s\S]*?запись на\s+(\d{2}\.\d{2})\s+в\s+(\d{2}:\d{2})/i))){
      add(m,{suffix:':trial-v2',student_name:q[1],event_kind:'trial_result',event_date:day,came:true,note:`Пробное ${dShort(day)}: пришёл и занимался. Семья пока не готова к месячному абонементу и выбрала разовые занятия`});
      add(m,{suffix:':single',student_name:q[1],event_kind:'single_sale',event_date:dateOf(q[3]),lesson_time:q[4],amount:Number(q[2]),payment_method:'card_branch',paid_at:String(m.date||'').slice(0,19),note:`Оплачено разовое занятие ${q[3]} в ${q[4]}, ${q[2]} ₽ картой`});
    }
    if((q=t.match(/^([^\n]+)\n(?:\+?\d[^\n]*\n\n)?Перезаписан на\s+(\d{2}\.\d{2})\s+в\s+(\d{2}:\d{2})/im)))add(m,{student_name:q[1],event_kind:'trial_booking',event_date:dateOf(q[2]),note:`Пробное перенесено на ${q[2]} в ${q[3]}`,task_title:`Подтвердить пробное ${q[2]} в ${q[3]}`,task_due:dateOf(q[2])+'T09:00'});
    if((q=t.match(/^([^\n]+) записан на пу на\s+(\d{2}\.\d{2})\s+в\s+(\d{2}:\d{2})[^\n]*\n(\+?\d[^\n]*)/im)))add(m,{student_name:q[1],phone:q[4],event_kind:'trial_booking',event_date:dateOf(q[2]),note:`Записан на пробное ${q[2]} в ${q[3]}`,task_title:`Подтвердить пробное ${q[2]} в ${q[3]}`,task_due:dateOf(q[2])+'T09:00'});
    if((q=t.match(/^([^\n]+)\nОтработка\s+(\d{2}\.\d{2})\s+в\s+(\d{2}:\d{2})/im)))add(m,{student_name:q[1],event_kind:'makeup_booking',event_date:dateOf(q[2]),note:`Отработка назначена на ${q[2]} в ${q[3]}. Родитель просил обязательно прислать напоминание`,task_title:`Напомнить об отработке ${q[2]} в ${q[3]}`,task_due:dateOf(q[2])+'T09:00'});
    if((q=t.match(/Запись на ПУ\s+(\d{2}\.\d{2})\s+в\s+(\d{2}:\d{2})\n([^\n]+?)\s+\d{2}\.\d{2}\.\d{4}\n[^\n]*?(\d{10,})/i)))add(m,{student_name:q[3],phone:q[4],event_kind:'trial_booking',event_date:dateOf(q[1]),note:`Записан на пробное ${q[1]} в ${q[2]}`,task_title:`Проверить результат пробного ${q[1]}`,task_due:dateOf(q[1])+'T12:00'});
    if(/Хвостов Саша\s*-\s*не состоялось ПУ/i.test(t))add(m,{suffix:':хвостов-результат',student_name:'Хвостов Александр',event_kind:'trial_result',event_date:day,came:false,outcome:'reinvite',note:'Пробное не состоялось: ребёнок не смог отойти от родителей и войти в тренировку. Предложено адаптационное посещение вместе с папой без давления',followup_due:plus(day,7)+'T11:00',followup_title:'Предложить адаптационное повторное ПУ с родителем'});
    if(/Дюссек Миша\s*-\s*ПУ\.\s*Купили абонемент на декабрь[\s\S]*?10240/i.test(t)){
      add(m,{suffix:':дюссек-продажа',student_name:'Дюссек Михаил',event_kind:'subscription_sale',sale_date:day,paid_at:String(m.date||'').slice(0,19),lesson_time:'10:30',weekdays:[0,2],tariff:'fixed',discount_percent:20,amount:10240,payment_method:'cash',components:[{month:'2026-12',price:10240,dates:['2026-12-02','2026-12-07','2026-12-09','2026-12-14','2026-12-16','2026-12-21','2026-12-23','2026-12-28'],dates_reason:'Группа Пн/Ср 10:30, все занятия декабря кроме 30.12; скидка 20% подтверждена СЗ'}],note:'После ПУ куплен абонемент на декабрь: группа Пн/Ср 10:30, все занятия кроме 30.12, стоимость 10 240 ₽. Получено наличными 10 500 ₽, сдача 260 ₽ возвращена клиенту переводом, чек пробит'});
      add(m,{suffix:':дюссек-результат',student_name:'Дюссек Михаил',event_kind:'trial_result',event_date:day,came:true,outcome:'purchased',subscription_months:['2026-12'],note:'Пришёл на ПУ 23.11 в 10:30 и купил декабрьский абонемент'});
    }
    if(/Оплата Решетняк Матвей 5120 за ноябрь[\s\S]*?Оплата картой/i.test(t)){
      add(m,{suffix:':решетняк-ноябрь',student_name:'Решетняк Матвей',event_kind:'subscription_sale',sale_date:day,paid_at:String(m.date||'').slice(0,19),weekdays:[0,2],tariff:'fixed',discount_percent:20,discount_rule:'sibling',amount:5120,payment_method:'card_branch',components:[{month:'2026-11',price:5120,dates:['2026-11-18','2026-11-23','2026-11-25','2026-11-30'],dates_reason:'После ПУ 16.11 проданы оставшиеся четыре занятия ноября Пн/Ср со скидкой 20% за второго ребёнка'}],note:'После ПУ куплен абонемент на ноябрь: четыре занятия, 5 120 ₽ со скидкой 20% за второго ребёнка, оплата картой в филиале'});
      add(m,{suffix:':решетняк-пу',student_name:'Решетняк Матвей',event_kind:'trial_result',event_date:day,came:true,outcome:'purchased',subscription_months:['2026-11'],note:'Пришёл на ПУ 16.11 в 10:00 и купил ноябрьский абонемент'});
    }
    if(/Решетняк Матвей[\s\S]*?Продление на декабрь\s*,?\s*9 тренировок\s*-\s*11520[\s\S]*?Оплата через точку банк/i.test(t)){
      add(m,{suffix:':решетняк-декабрь',student_name:'Решетняк Матвей',event_kind:'subscription_sale',sale_date:day,paid_at:String(m.date||'').slice(0,19),lesson_time:'10:30',weekdays:[0,2],tariff:'fixed',discount_percent:20,discount_rule:'sibling',amount:11520,payment_method:'tochka_qr_link',components:[{month:'2026-12',price:11520,dates:['2026-12-02','2026-12-07','2026-12-09','2026-12-14','2026-12-16','2026-12-21','2026-12-23','2026-12-28','2026-12-30'],dates_reason:'Девять занятий декабря Пн/Ср 10:30; скидка 20% за второго ребёнка подтверждена СЗ'}],note:'Продление на декабрь: группа Пн/Ср 10:30, девять занятий, 11 520 ₽ со скидкой 20% за второго ребёнка. Оплачено по ссылке Точки'});
    }
    if(/5120 пробить чек[\s\S]*?Оплата абонемента ноябрь[\s\S]*?Кисел[её]в Илья/i.test(t)){
      add(m,{suffix:':киселев-ноябрь',student_name:'Киселёв Илья',event_kind:'subscription_sale',sale_date:day,paid_at:String(m.date||'').slice(0,19),weekdays:[0,2],tariff:'fixed',discount_percent:20,amount:5120,payment_method:'card',components:[{month:'2026-11',price:5120,dates:['2026-11-18','2026-11-23','2026-11-25','2026-11-30'],dates_reason:'После ПУ 16.11 проданы четыре оставшихся занятия ноября Пн/Ср; сумма подтверждена СЗ'}],note:'После ПУ куплен абонемент на ноябрь: четыре занятия, 5 120 ₽. Чек пробит; способ оплаты в сообщениях не уточнён'});
      add(m,{suffix:':киселев-пу',student_name:'Киселёв Илья',event_kind:'trial_result',event_date:day,came:true,outcome:'purchased',subscription_months:['2026-11'],note:'Пришёл на ПУ 16.11 в 10:00. После обсуждения с семьёй в тот же день купили ноябрьский абонемент'});
    }
    if(/Оплата Никитина Софья[\s\S]*?ноябрь и декабрь до 14 числа включительно[\s\S]*?10240[\s\S]*?qr-коду/i.test(t)){
      add(m,{suffix:':никитина-продажа',student_name:'Никитина Софья',event_kind:'subscription_sale',sale_date:day,paid_at:String(m.date||'').slice(0,19),weekdays:[0,2],tariff:'fixed',discount_percent:20,amount:10240,payment_method:'online',payment_method_needs_confirmation:true,clarification_task_title:'Уточнить способ оплаты по QR: ссылка Точки или терминал Сбера',clarification_task_note:'В СЗ указано только «оплата по QR-коду». Уточнить: QR/ссылка Точки от администратора или QR терминала Сбера на филиале. После уточнения исправить способ у существующей оплаты, новую оплату не создавать',components:[{month:'2026-11',price:5120,dates:['2026-11-18','2026-11-23','2026-11-25','2026-11-30'],dates_reason:'После ПУ оплачены четыре оставшихся занятия ноября Пн/Ср со скидкой 20%'},{month:'2026-12',price:5120,dates:['2026-12-02','2026-12-07','2026-12-09','2026-12-14'],dates_reason:'Оплачены четыре занятия декабря до отъезда семьи 15.12; скидка 20% подтверждена СЗ'}],note:'После ПУ куплены восемь занятий: остаток ноября и декабрь до 14.12 включительно, 10 240 ₽ со скидкой 20%. Оплата по QR, точный канал не указан. С 15.12 семья уезжает на месяц'});
      add(m,{suffix:':никитина-пу',student_name:'Никитина Софья',event_kind:'trial_result',event_date:day,came:true,outcome:'purchased',subscription_months:['2026-11','2026-12'],note:'Пришла на ПУ 16.11 в 10:00 и купила занятия на ноябрь и первую половину декабря'});
    }
    if(/Отчет\s+22\.11/i.test(t))for(const line of t.split('\n').filter(x=>/^•\s*/.test(x))){const p=line.match(/^•\s*([^\d]+?)\s+(\d{2}:\d{2})\s+(.+)$/);if(!p)continue;const detail=p[3];if(/разов/i.test(detail)||!/ПРОДАЖА|подождать|не готов|подумает/i.test(detail))continue;const came=!/ушли|не смог|не зашел/i.test(detail);let due='2026-12-01T12:00',title='Уточнить решение семьи после пробного',outcome='',extra={};if(/ПРОДАЖА НОЯ\+ДЕК/i.test(detail)){due='';title='';outcome='purchased';extra={subscription_months:['2026-11','2026-12']}}else if(/весн|апрел/i.test(detail)){due='';title='Связаться с семьёй весной и предложить подходящую группу';outcome='later';extra={followup_title:title}}else if(/перезаписаться и пробовать пока не готовы/i.test(detail)){due=plus(day,91)+'T11:00';title='Пригласить снова на ПУ: ребёнок будет готов';outcome='later';extra={followup_due:due,followup_title:title,refusal_reason:'Ребёнок пока не готов войти в тренировку, семья не готова повторять попытку сейчас'}}else if(/пусть подраст|секци[яи] в сад/i.test(detail)){due=plus(day,91)+'T11:00';title='Пригласить снова на ПУ: ребёнок подрос';outcome='later';extra={followup_due:due,followup_title:title,refusal_reason:'Ребёнок пока не готов: занимается в саду, семья изначально не планировала покупку'}}add(m,{suffix:':'+p[1].trim().toLocaleLowerCase('ru').replace(/\s+/g,'-'),student_name:p[1].trim(),event_kind:'trial_result',event_date:day,came,note:`Пробное ${dShort(day)} в ${p[2]}: ${detail}`,...(title&&due?{task_title:title,task_due:due}:{}),...(outcome?{outcome,...extra}:{})});}
  }
  if(!events.length)throw new Error('За выбранную дату не найдено поддерживаемых клиентских событий');return {date:day,events};
}

let toastTimer;
function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast" + (err ? " err" : ""); t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), 3200);
}
const fail = (e) => { if (!e || !e.silent) toast((e && e.message) || "Ошибка", true); };

/* ---------- быстрый вход по коду ---------- */
function deviceToken(create = false) {
  try {
    let t = localStorage.getItem("crm.device");
    if (!t && create) {
      const a = new Uint8Array(32); crypto.getRandomValues(a);
      t = [...a].map((x) => x.toString(16).padStart(2, "0")).join("");
      localStorage.setItem("crm.device", t);
    }
    return t || "";
  } catch (_) { return ""; }
}

/* экран с цифровой клавиатурой; onCode(code, pad) вызывается после 4 цифр */
function pinPad({ title, sub, onCode, left }) {
  closeDrawer();
  app.innerHTML = `<div class="pin-wrap">
    <div class="pin-logo">Абонемент <span>Плюс</span></div>
    <div class="pin-mid"><h1 class="pin-title">${esc(title)}</h1><div class="pin-sub" id="pin-sub">${esc(sub)}</div>
      <div class="pin-dots" id="pin-dots">${"<i></i>".repeat(4)}</div><div class="pin-err" id="pin-err"></div></div>
    <div class="pin-keys">${[1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => `<button data-k="${n}">${n}</button>`).join("")}
      <button class="pin-link" id="pin-left">${esc(left.label)}</button><button data-k="0">0</button>
      <button id="pin-del" aria-label="Стереть"><svg viewBox="0 0 24 24" width="30" height="30" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M9 5h11v14H9l-6-7 6-7Z"/><path d="m12 9 5 6m0-6-5 6"/></svg></button></div>
  </div>`;
  let code = "";
  const pad = {
    draw() { app.querySelectorAll("#pin-dots i").forEach((d, i) => d.classList.toggle("on", i < code.length)); },
    reset(err = "") { code = ""; pad.draw(); $("#pin-err").textContent = err; if (err) { const d = $("#pin-dots"); d.classList.remove("shake"); void d.offsetWidth; d.classList.add("shake"); if (navigator.vibrate) navigator.vibrate(120); } },
    sub(t) { $("#pin-sub").textContent = t; },
    busy: false,
  };
  const press = async (k) => {
    if (pad.busy || code.length >= 4) return;
    code += k; pad.draw(); $("#pin-err").textContent = "";
    if (code.length === 4) { pad.busy = true; try { await onCode(code, pad); } finally { pad.busy = false; } }
  };
  app.querySelectorAll("[data-k]").forEach((b) => (b.onclick = () => press(b.dataset.k)));
  $("#pin-del").onclick = () => { code = code.slice(0, -1); pad.draw(); };
  $("#pin-left").onclick = left.fn;
  document.onkeydown = (e) => { if (!$("#pin-dots")) { document.onkeydown = null; return; } if (/^\d$/.test(e.key)) press(e.key); if (e.key === "Backspace") $("#pin-del").click(); };
  return pad;
}

async function renderPinLogin(name) {
  pinPad({
    title: `Здравствуйте, ${name}!`, sub: "Введите код быстрого входа",
    left: { label: "Войти по паролю", fn: () => renderLogin(true) },
    onCode: async (code, pad) => {
      const r = await fetch("/api/pin/login", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device: deviceToken(), pin: code }) }).then((x) => x.json()).catch(() => ({ error: "Нет связи, попробуйте ещё раз" }));
      if (r.ok) { document.onkeydown = null; return boot(); }
      if (r.need_password) { toast(r.error, true); return renderLogin(true); }
      pad.reset(r.error || "Неверный код");
    },
  });
}

/* после входа по паролю: предложить придумать код (один раз на устройстве, можно пропустить) */
async function offerPin() {
  const dev = deviceToken(true);
  try { if (localStorage.getItem("crm.pin_skip") === "1") return; } catch (_) {}
  try { if ((await api("GET", "/api/pin?device=" + dev)).has_pin) return; } catch (_) { return; }
  await new Promise((resolve) => {
    let first = "";
    const done = () => { document.onkeydown = null; resolve(); };
    pinPad({
      title: "Быстрый вход", sub: "Придумайте код из 4 цифр. В следующий раз войдёте по нему, без пароля",
      left: { label: "Пропустить", fn: () => { try { localStorage.setItem("crm.pin_skip", "1"); } catch (_) {} done(); } },
      onCode: async (code, pad) => {
        if (!first) { first = code; pad.reset(); pad.sub("Повторите код"); return; }
        if (code !== first) { first = ""; pad.reset("Коды не совпали"); pad.sub("Придумайте код из 4 цифр"); return; }
        try { await api("POST", "/api/pin", { device: dev, pin: code }); toast("Готово! Теперь можно входить по коду"); done(); }
        catch (e) { first = ""; pad.reset(e.message); pad.sub("Придумайте код из 4 цифр"); }
      },
    });
  });
}

/* ---------- вход ---------- */
async function renderLogin(force = false) {
  try {
    if (sessionStorage.getItem("crm.force_password_login") === "1") {
      force = true; sessionStorage.removeItem("crm.force_password_login");
    }
  } catch (_) {}
  closeDrawer();
  if (!force && deviceToken()) {
    const h = await fetch("/api/pin/hello?device=" + encodeURIComponent(deviceToken())).then((r) => r.json()).catch(() => ({}));
    if (h && h.name) return renderPinLogin(h.name);
  }
  app.innerHTML = `
  <div class="login-wrap"><div class="login">
    <div class="login-mark" aria-hidden="true">⚽</div>
    <h1>Абонемент <span>Плюс</span></h1>
    <div class="login-sub">Вход для участников команды школы</div>
    <form id="login-form">
      <div class="field"><label>Логин</label><input name="login" autocomplete="username" required></div>
      <div class="field"><label>Пароль</label><div class="pw-wrap"><input name="password" type="password" autocomplete="current-password" required>
        <button type="button" class="pw-eye" aria-label="Показать пароль" title="Показать пароль">👁</button></div></div>
      <button class="btn primary" style="width:100%">Войти</button>
      <div class="error" id="login-err"></div>
    </form>
    <div class="login-sub small" style="margin-top:14px"><a href="/legal" style="color:#7fe0ad">Документы и политика конфиденциальности</a></div>
  </div></div>`;
  $("#login-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try { await api("POST", "/api/login", { login: f.get("login"), password: f.get("password") }); await offerPin(); boot(); }
    catch (err) { $("#login-err").textContent = err.message; }
  };
}

/* ---------- каркас ---------- */
const VIEWS = [
  ["today", "Сегодня"], ["sales", "Рабочий стол"], ["inbox", "Чаты и звонки"], ["search", "Клиенты"], ["planner", "Планер"], ["nodeadline", "Без дедлайнов"],
  ["journal", "Журнал"], ["schedule", "Расписание"], ["coaches", "Тренеры"], ["team", "Команда"], ["activity", "История действий"], ["posts", "Посты в каналы"], ["payments", "Оплаты"], ["fiscal", "Чеки"], ["merch", "Мерч"], ["spend", "Расходы"], ["staffexpenses", "Покупки и возмещения"], ["dashboard", "Дашборд"], ["revenue-losses", "Выручка и потери"], ["docs", "Документы"], ["settings", "Настройки"],
];
const NAV_ICONS = {
  team: '<circle cx="12" cy="6" r="2.5"/><circle cx="5.5" cy="17.5" r="2.5"/><circle cx="18.5" cy="17.5" r="2.5"/><path d="M12 8.5v3.5M5.5 15v-3h13v3"/>',
  sales: '<path d="M4 5h16v14H4z"/><path d="M9 5v14M15 5v14"/>',
  today: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  schedule: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4M8 14h3M13 14h3M8 17h3"/>',
  inbox: '<path d="M4 6h16v10H8l-4 3z"/><path d="M8 10h8M8 13h5"/>',
  nodeadline: '<circle cx="12" cy="12" r="8"/><path d="M10 9v6M14 9v6"/>',
  members: '<circle cx="9" cy="8" r="3"/><path d="M3.5 19c.8-3 3-4.5 5.5-4.5s4.7 1.5 5.5 4.5"/><circle cx="17" cy="9" r="2.3"/><path d="M16 14.6c2.2.2 3.7 1.6 4.4 4.4"/>',
  skips: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4M10 14l4 4M14 14l-4 4"/>',
  journal: '<rect x="5" y="3.5" width="14" height="17" rx="2"/><path d="M9 9l1.5 1.5L13 8M9 14.5l1.5 1.5L13 13.5M15 9.5h1M15 15h1"/>',
  coaches: '<path d="M8 4h8v4a4 4 0 0 1-8 0z"/><path d="M8 5H5v1.5A3 3 0 0 0 8 9.5M16 5h3v1.5a3 3 0 0 1-3 3M12 12v4M9 20h6M10 16h4v4h-4z"/>',
  "revenue-losses": '<path d="M4 19V8m5 11v-5m5 5V5m5 14v-9M3 19h18"/>',
  dashboard: '<path d="M4 19V10M10 19V5M16 19v-7M21 19H3"/>',
  manual: '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M8 8h8M8 12h8M8 16h5"/><path d="M16 15l1.5 1.5L20 14"/>',
  planner: '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M8 9l1.5 1.5L12 8M8 15l1.5 1.5L12 14M14 9.5h3M14 15.5h3"/>',
  spend: '<rect x="3" y="6" width="18" height="13" rx="2"/><path d="M3 10h18M16 15h2M7 3h10"/>',
  fiscal: '<path d="M6 3h12v18l-2-1.5L14 21l-2-1.5L10 21l-2-1.5L6 21z"/><path d="M9 8h6M9 11.5h6M9 15h3"/>',
  merch: '<path d="M8 4l-4 3 2 3 2-1v11h8V9l2 1 2-3-4-3c-.5 1.5-2 2.5-4 2.5S8.5 5.5 8 4z"/>',
  payments: '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M3 10h18M7 15h3"/>',
  search: '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
  docs: '<path d="M7 3h7l4 4v14H7z"/><path d="M14 3v4h4M10 11h5M10 14.5h5M10 18h3"/>',
  posts: '<path d="M4 10v4l3 .5V9.5zM7 9.5l10-4.5v14l-10-4.5"/><path d="M9 15l1 4h2.5l-1-3.6M19 10.5a2.5 2.5 0 0 1 0 3"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8"/>',
  more: '<circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/>',
};
const navIcon = (k) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${NAV_ICONS[k] || ""}</svg>`;

const isCoach = () => S.user && S.user.role === "coach";
/* права по разделам приходят с сервера (perms.py): own - своё, view - смотреть, full - всё */
const LEVELS = { own: 1, view: 2, full: 3 };
const can = (sec, lvl = "own") => (LEVELS[((S.user && S.user.perms) || {})[sec]] || 0) >= LEVELS[lvl];
const isOwner = () => can("settings", "full");
const visibleViews = () => VIEWS.filter(([k]) => (k === "activity" ? S.user?.activity_history : can(k === "manual" ? "planner" : k === "nodeadline" ? "sales" : k === "revenue-losses" ? "dashboard" : k)) && (k !== "today" || !isOwner())
  && (k !== "manual" || !["coach", "trainee"].includes(S.user.role)))  // тренеру задачи приходят на «Сегодня» и в отчёт, отдельная вкладка не нужна  // руководитель смотрит стол тренера через «Как видит»
  .map(([k, t]) => [k, k === "today" && S.user.coach_nebo_only ? "Мой дашборд" : k === "coaches" && !can("coaches", "view") ? "Мои результаты" : t]);
const SHORT = { today: "Сегодня", sales: "Стол", inbox: "Чаты", coaches: isCoachShort(), journal: "Журнал" };
function isCoachShort() { return "Результаты"; }
const badge = (k) => k === "inbox" ? `<span class="nbadge" data-badge hidden></span>` : "";

/* Боковое меню: главное сверху, остальное по папкам. Открыта папка с текущим разделом и те, что участник команды раскрыл сам */
const NAV_TOP = ["today", "sales", "inbox", "planner", "search"];
const NAV_FOLDERS = [["Занятия", ["journal", "schedule", "coaches"]],
  ["Деньги", ["payments", "fiscal", "merch", "spend", "staffexpenses", "dashboard", "revenue-losses"]], ["Школа", ["team", "activity", "posts", "docs", "settings"]]];
const navOpen = () => { try { return JSON.parse(localStorage.getItem("crm.nav_open") || "{}"); } catch (_) { return {}; } };
function sideNav(views) {
  const item = ([k, t]) => `<button data-view="${k}" class="${S.view === k ? "active" : ""}">${navIcon(k)}<span>${t}</span>${badge(k)}</button>`;
  if (views.length <= 8) return views.map(item).join("");
  const has = (k) => views.find(([v]) => v === k);
  const search = has("search") ? `<form id="client-search-form" role="search" autocomplete="off"><textarea class="nav-q" id="nav-q" role="searchbox" rows="1" name="crm-customer-query" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" enterkeyhint="search" data-lpignore="true" data-1p-ignore="true" data-bwignore="true" data-form-type="other" placeholder="🔍 Найти клиента   /" aria-label="Найти клиента" title="Имя, фамилия или телефон. Быстро открыть поиск: клавиша /"></textarea></form>` : "";
  const placed = new Set();
  const top = NAV_TOP.filter(has).map((k) => { placed.add(k); return item(has(k)); }).join("");
  const o = navOpen();
  const folders = NAV_FOLDERS.map(([title, keys]) => {
    const list = keys.filter(has); list.forEach((k) => placed.add(k));
    if (!list.length) return "";
    if (list.length === 1) return item(has(list[0]));
    const open = o[title] || list.includes(S.view);
    return `<div class="nav-fold ${open ? "open" : ""}"><button class="nav-fold-h" data-fold="${title}" aria-expanded="${!!open}" aria-controls="nav-folder-${keys[0]}"><span class="nav-arrow">▸</span><span>${title}</span></button>
      <div class="nav-fold-b" id="nav-folder-${keys[0]}">${list.map((k) => item(has(k))).join("")}</div></div>`;
  }).join("");
  const rest = views.filter(([k]) => !placed.has(k)).map(item).join("");
  return search + `<div class="nav-main">${top}</div>` + folders + rest;
}

/* Поиск в меню: клиенты появляются прямо под полем, пока печатаешь. ↑↓ выбрать, Enter открыть, Esc закрыть */
function wireNavSearch(nq) {
  const box = document.createElement("div");
  box.className = "nav-res"; box.hidden = true;
  nq.after(box);
  let t, rows = [], cur = -1, historyMode = false, generation = 0;
  const close = () => { box.hidden = true; cur = -1; };
  const paint = () => {
    box.innerHTML = (historyMode ? `<div class="nav-recent-head"><b>Недавно открывали</b>${rows.length ? `<button type="button" data-clear-recent>Очистить</button>` : ""}</div>` : "") + (rows.length ? rows.slice(0, 8).map((r, i) => `<button class="${i === cur ? "on" : ""}" data-nr="${r.id}"><b>${esc(displayChildName(r.name))}</b>
      <small>${esc([r.parent, fmtPhone(r.phone)].filter(Boolean).join(" · "))}${r.funnel === "members" ? "" : " · лид"}</small></button>`).join("")
      + (rows.length > 8 ? `<button data-nall><small>Все результаты: ${rows.length} →</small></button>` : "")
      : `<div class="small" style="padding:8px 10px;opacity:.7">${historyMode ? "Здесь появятся последние карточки клиентов" : "Никого не нашли"}</div>`);
    box.style.top = nq.offsetTop + nq.offsetHeight + 4 + "px";
    box.hidden = false;
    box.querySelectorAll("[data-nr]").forEach((b) => (b.onmousedown = (e) => { if (e.button !== 0) return; e.preventDefault(); nq.value = ""; close(); openStudent(+b.dataset.nr); }));
    const clear = box.querySelector("[data-clear-recent]");
    if (clear) {
      clear.onmousedown = e => e.preventDefault();
      clear.onclick = async () => {
        try { await api("DELETE", "/api/students/recent"); if (!nq.value.trim()) { rows = []; cur = -1; paint(); } }
        catch (err) { fail(err); }
      };
    }
    const all = box.querySelector("[data-nall]"); if (all) all.onmousedown = (e) => { if (e.button !== 0) return; e.preventDefault(); goAll(); };
  };
  const goAll = () => { S.search = nq.value.trim(); nq.value = ""; close(); go("search"); setTimeout(() => { const q = $("#q"); if (q) { q.focus(); q.dispatchEvent(new Event("input")); } }, 50); };
  const showHistory = async () => {
    const request = ++generation;
    try {
      const recent = await api("GET", "/api/students/recent");
      if (request !== generation || nq.value.trim() || document.activeElement !== nq) return;
      rows = recent; historyMode = true; cur = rows.length ? 0 : -1; paint();
    } catch (_) { close(); }
  };
  nq.addEventListener("focus", () => { if (!nq.value.trim()) showHistory(); });
  nq.oninput = () => {
    clearTimeout(t); generation++;
    nq.value = nq.value.replace(/[\r\n]+/g, " ");
    const v = nq.value.trim();
    if (!v) return showHistory();
    historyMode = false;
    if (v.length < 2) return close();
    t = setTimeout(async () => { try { rows = await api("GET", "/api/students?q=" + encodeURIComponent(v)); cur = rows.length ? 0 : -1; if (nq.value.trim() === v && document.activeElement === nq) paint(); } catch (_) { if (nq.value.trim() === v && document.activeElement === nq) { rows = []; cur = -1; box.innerHTML = '<div class="small" style="padding:8px 10px">Поиск временно недоступен. Повторите ещё раз</div>'; box.hidden = false; } } }, 200);
  };
  nq.onkeydown = (e) => {
    if (e.key === "Escape") { nq.value = ""; close(); nq.blur(); }
    else if (e.key === "ArrowDown" && !box.hidden) { e.preventDefault(); cur = Math.min(cur + 1, Math.min(rows.length, 8) - 1); paint(); }
    else if (e.key === "ArrowUp" && !box.hidden) { e.preventDefault(); cur = Math.max(cur - 1, 0); paint(); }
    else if (e.key === "Enter") { e.preventDefault(); if (!box.hidden && rows[cur]) { const id = rows[cur].id; nq.value = ""; close(); openStudent(id); } else goAll(); }
  };
  nq.onblur = () => setTimeout(close, 150);
}

/* Горячие клавиши на компьютере: «/» поиск клиента, «N» новый клиент (работают и в русской раскладке) */
document.addEventListener("keydown", (e) => {
  if (e.ctrlKey || e.metaKey || e.altKey || !S.user) return;
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
  if (document.querySelector(".tour") || !$("#drawer") || !$("#drawer").hidden) return;
  if (e.code === "Slash" || e.key === "/") {
    const nq = $("#nav-q");
    if (nq && nq.offsetParent) { e.preventDefault(); nq.focus(); }
    else if (can("search")) { e.preventDefault(); go("search"); setTimeout(() => $("#q") && $("#q").focus(), 80); }
  } else if (e.code === "KeyN" && $("#new-client")) { e.preventDefault(); openNewClient(); }
});

/* Праздничное оформление по календарю: цвета, значок в меню, украшения в углу и поздравление на столе.
   Выключается в Настройки → Мой профиль (только руководитель, для всех сразу) */
const FESTIVE = [
  { key: "halloween", from: "10-25", to: "11-01", icon: "🎃", deco: "🦇 🎃 👻", title: "С Хэллоуином!",
    text: "Пусть сегодня на тренировках будут только сладости, а гадости пусть достанутся соперникам 🎃⚽" },
  { key: "newyear", from: "12-15", to: "01-10", icon: "🎄", deco: "❄️ 🎄 ✨", title: "С наступающим Новым годом!",
    text: "Пусть в новом году будет полный зал, все продлятся, а мяч всегда летит в девятку 🎄⚽" },
  { key: "feb23", from: "02-22", to: "02-23", icon: "🎖", deco: "⭐ 🎖", title: "С Днём защитника Отечества!",
    text: "Защитникам на поле и в жизни: крепкой обороны и быстрых контратак ⭐" },
  { key: "mar8", from: "03-07", to: "03-08", icon: "🌷", deco: "🌷 💐 🌸", title: "С 8 Марта!",
    text: "Девушки нашей команды, вы лучшие игроки в составе 💐" },
  { key: "june1", from: "06-01", to: "06-01", icon: "🎈", deco: "🎈 🎉", title: "С Днём защиты детей!",
    text: "Сегодня главный праздник наших футболистов 🎈⚽" },
  { key: "sep1", from: "09-01", to: "09-01", icon: "🍁", deco: "🍁 📚 ⚽", title: "С новым сезоном!",
    text: "1 сентября: новые группы, новые дети, новые голы 🍁" },
];
function festiveNow(today) {
  const md = today.slice(5, 10);
  return FESTIVE.find((f) => (f.from <= f.to ? md >= f.from && md <= f.to : md >= f.from || md <= f.to)) || null;
}
function applyFestive() {
  const f = S.meta && S.meta.festive !== false ? festiveNow(S.meta.today) : null;
  document.documentElement.dataset.festive = f ? f.key : "";
  document.querySelectorAll(".festive-deco").forEach((x) => x.remove());
  if (!f) return;
  const brand = document.querySelector(".brand i"); if (brand) brand.textContent = f.icon;
  const d = document.createElement("div"); d.className = "festive-deco"; d.setAttribute("aria-hidden", "true"); d.textContent = f.deco;
  document.body.appendChild(d);
}
function festiveBanner(page) {
  const f = S.meta && S.meta.festive !== false ? festiveNow(S.meta.today) : null;
  if (!f || !page) return;
  const k = `crm.festive.${f.key}.${S.meta.today}`;
  try { if (localStorage.getItem(k)) return; } catch (_) {}
  const head = page.querySelector(".page-head"); if (!head) return;
  head.insertAdjacentHTML("afterend", `<div class="festive-banner"><span class="festive-ic">${f.icon}</span><div class="grow"><b>${esc(f.title)}</b><div class="small">${esc(f.text)}</div></div><button class="icon-btn" data-fclose title="Скрыть до завтра">✕</button></div>`);
  page.querySelector("[data-fclose]").onclick = (e) => { try { localStorage.setItem(k, "1"); } catch (_) {} e.target.closest(".festive-banner").remove(); };
}

function shell() {
  const views = visibleViews();
  if (S.view !== "meet" && !views.some(([k]) => k === S.view)) S.view = views[0][0];
  document.body.classList.toggle("no-copy", !can("export"));
  const tabs = views.length <= 5 ? views : [...views.slice(0, 4), ["more", "Ещё"]];
  app.innerHTML = `
  <header class="topbar">
    <div class="brand"><i aria-hidden="true">⚽</i><div>Абонемент <span>Плюс</span></div></div>
    ${can("client_create", "full") ? `<button class="btn" id="new-client">+ Клиент</button>` : ""}
    <nav class="nav">${sideNav(views)}</nav>
    <div class="sidebar-account">
      <div class="sidebar-account-text"><b>${esc(S.user.name)}</b><small>${esc(S.user.title || "")}</small></div>
      <button type="button" class="btn sm" data-account-logout aria-label="Выйти из аккаунта">Выйти</button>
    </div>
  </header>
  <main class="page" id="page"></main>
  <nav class="tabbar">${tabs.map(([k, t]) => `<button data-${k === "more" ? "more" : "view"}="${k}" class="${S.view === k ? "active" : ""}">${navIcon(k)}<span>${SHORT[k] || t}</span>${badge(k)}</button>`).join("")}</nav>
  <div class="more-sheet" hidden><div class="sheet">${views.slice(4).map(([k, t]) => `<button data-view="${k}" class="${S.view === k ? "active" : ""}">${navIcon(k)}${t}</button>`).join("")}</div></div>`;
  app.querySelectorAll("[data-account-logout]").forEach(b => b.onclick = logoutCurrentAccount);
  app.querySelectorAll("[data-view]").forEach((b) => (b.onclick = () => { $(".more-sheet").hidden = true; go(b.dataset.view); }));
  app.querySelectorAll("[data-fold]").forEach((b) => (b.onclick = () => {
    const f = b.closest(".nav-fold"), open = f.classList.toggle("open");
    b.setAttribute("aria-expanded", String(open));
    try { const o = navOpen(); o[b.dataset.fold] = open; localStorage.setItem("crm.nav_open", JSON.stringify(o)); } catch (_) {}
  }));
  const nq = $("#nav-q");
  if (nq) {
    $("#client-search-form").onsubmit = e => e.preventDefault();
    wireNavSearch(nq);
  }
  const more = $("[data-more]");
  if (more) more.onclick = () => { $(".more-sheet").hidden = !$(".more-sheet").hidden; };
  $(".more-sheet").onclick = (e) => { if (e.target.classList.contains("more-sheet")) e.currentTarget.hidden = true; };
  if ($("#new-client")) $("#new-client").onclick = openNewClient;
}

/* Колесо мыши открывает переходы CRM в новой вкладке, как обычную ссылку.
   Кнопки, которые сохраняют, удаляют или проводят деньги, сюда намеренно не входят. */
function crmNavigationUrl(target) {
  if (!target) return "";
  const route = (view, params = {}) => {
    const query = new URLSearchParams(params).toString();
    return `${location.origin}${location.pathname}#${view}${query ? "?" + query : ""}`;
  };
  if (target.dataset.view) return route(target.dataset.view);
  if (target.dataset.tile) return route("settings", { settings_tab: target.dataset.tile });
  for (const key of ["open", "aopen", "who", "nr", "wlopen", "openSt", "newCard", "etopen", "freshLead"]) {
    const id = Number(target.dataset[key]);
    if (id > 0) return route("members", { student: id });
  }
  for (const key of ["conv", "oqchat"]) {
    const id = Number(target.dataset[key]);
    if (id > 0) return route("inbox", { conversation: id });
  }
  return "";
}
let crmMiddleOpened = null;
function openCrmNavigationByMiddleClick(event) {
  if (event.button !== 1 || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
  const target = event.target.closest("[data-view],[data-tile],[data-open],[data-aopen],[data-who],[data-nr],[data-wlopen],[data-open-st],[data-new-card],[data-etopen],[data-fresh-lead],[data-conv],[data-oqchat]");
  const url = crmNavigationUrl(target);
  if (!url) return;
  event.preventDefault(); event.stopPropagation();
  if (crmMiddleOpened && crmMiddleOpened.target === target && Date.now() - crmMiddleOpened.at < 1000) return;
  crmMiddleOpened = { target, at: Date.now() };
  window.open(url, "_blank", "noopener");
}
function openCrmNavigationByModifiedClick(event) {
  if (event.button !== 0 || !(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey) return;
  const target = event.target.closest("[data-view],[data-tile],[data-open],[data-aopen],[data-who],[data-nr],[data-wlopen],[data-open-st],[data-new-card],[data-etopen],[data-fresh-lead],[data-conv],[data-oqchat]");
  const url = crmNavigationUrl(target);
  if (!url) return;
  event.preventDefault(); event.stopPropagation();
  window.open(url, "_blank", "noopener");
}
// Edge надёжнее передаёт среднюю кнопку через mousedown; auxclick оставлен
// запасным вариантом для браузеров, где mousedown на кнопке не приходит.
document.addEventListener("mousedown", openCrmNavigationByMiddleClick, true);
document.addEventListener("auxclick", openCrmNavigationByMiddleClick, true);
document.addEventListener("click", openCrmNavigationByModifiedClick, true);

/* ---------- навигация «назад»: каждый переход пишется в историю браузера ---------- */
const navSnap = () => JSON.parse(JSON.stringify({ meet: S.meet, journal: S.journal, sched: S.sched, planner: S.planner, spend: S.spend, fx: S.fx,
  inbox: S.inbox && { tab: S.inbox.tab, filter: S.inbox.filter } }));
function navRestore(sub) {
  if (!sub) return;
  if (sub.journal) S.journal = sub.journal;
  if (sub.meet) S.meet = sub.meet;
  if (sub.sched) S.sched = sub.sched;
  if (sub.planner) S.planner = sub.planner;
  if (sub.spend) S.spend = sub.spend;
  if (sub.fx) S.fx = sub.fx;
  if (sub.inbox && S.inbox) Object.assign(S.inbox, sub.inbox);
}
const navDepth = () => (history.state && history.state.depth) || 0;
function pushNav(menu = false) {
  try { history.pushState({ view: S.view, sub: navSnap(), depth: navDepth() + 1, menu }, "", "#" + S.view); } catch (_) {}
}
function step(fn) { fn(); pushNav(); window.scrollTo(0, 0); render(); }
let drawerInHistory = false, skipPop = false;
const mayRestoreStudent = card => card && card.userId === S.user?.id && !S.meta?.staff_docs_pending &&
  ["admin", "operator"].includes(S.user?.role) && ["members", "sales", "search", "inbox"].some(section => can(section));
function studentCardHash(card) {
  const [view, query = ""] = (location.hash.slice(1) || S.view).split("?");
  const params = new URLSearchParams(query);
  params.delete("student");
  params.delete("student_tab");
  if (card) {
    params.set("student", card.id);
    if (card.tab !== "feed") params.set("student_tab", card.tab);
  }
  return "#" + view + (params.size ? "?" + params : "");
}
function saveStudentCardRoute(card) {
  try { history.replaceState({ ...history.state, studentCard: card }, "", studentCardHash(card)); } catch (_) {}
}
const forgetStudentCard = () => {
  const state = { ...history.state }; delete state.studentCard;
  try { history.replaceState(state, "", studentCardHash()); } catch (_) {}
};
window.addEventListener("popstate", (e) => {
  if (skipPop) { skipPop = false; return; }
  const st = e.state;
  if (drawerInHistory) {
    drawerInHistory = false; hideDrawer();
    if (!st?.studentCard) return;
  }
  if (!st || !st.view) return;
  if (st.view === "settings") S.setTab = st.settingsTab || null;
  navRestore(st.sub);
  go(st.view, true);
  if (st.drawer && mayRestoreStudent(st.studentCard)) {
    drawerInHistory = true;
    openStudent(st.studentCard.id, { tab: st.studentCard.tab, restoring: true });
  }
});

function clientDirectoryView(view) {
  return view === "members" && can("search") ? "search" : view;
}

function go(view, fromHistory = false) {
  view = clientDirectoryView(view);
  if (view === "settings" && !fromHistory) S.setTab = null;  // из меню: сначала плитки
  S.view = view;
  if (!fromHistory) pushNav(true);  // переход из меню: кнопку «Назад» не показываем
  try { localStorage.setItem("crm.view", view); } catch (_) {}
  app.querySelectorAll("[data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  app.querySelectorAll(".nav-fold").forEach((f) => { if (f.querySelector(`[data-view="${view}"]`)) { f.classList.add("open"); f.querySelector(".nav-fold-h").setAttribute("aria-expanded", "true"); } });
  const more = $("[data-more]");
  if (more) more.classList.toggle("active", !visibleViews().slice(0, 4).some(([k]) => k === view));
  window.scrollTo(0, 0);
  render();
}

async function render() {
  if(S.view!=="inbox")document.body.classList.remove("chat-fullscreen");
  const page = $("#page");
  if (!page) return;
  page.classList.remove("readonly");
  if (S.meta && S.meta.staff_docs_pending) return renderStaffDocs(page);
  if (S.activityView !== S.view) {
    S.activityView = S.view;
    recordCrmActivity("view");
  }
  await renderView(page);
  if (["sales", "today"].includes(S.view) && !S.viewAs) festiveBanner(page);
  if (S.meta && S.meta.tour_pending && !S.tourOn) startTour();
  if (navDepth() > 0 && !(history.state && history.state.menu) && !page.querySelector(".back-btn")) {
    page.insertAdjacentHTML("afterbegin", `<button class="back-btn" type="button">← Назад</button>`);
    page.querySelector(".back-btn").onclick = () => history.back();
  }
}

async function renderView(page) {
  try {
    if (S.view === "activity" && S.user.activity_history) return await renderActivityHistory(page);
    if (S.view === "sales") return await renderWorkdesk(page);
    if (S.view === "today") return await renderCoachToday(page);
    if (S.view === "payments") return await renderPayments(page);
    if (S.view === "merch") return await renderMerch(page);
    if (S.view === "planner") return await renderPlanner(page);
    // «Пропуски» и «Ручные задачи» теперь на рабочем столе, отдельных разделов нет (Юлия, 04.11)
    if (["manual", "skips"].includes(S.view)) { S.view = "sales"; return render(); }
    if (S.view === "staffexpenses") return await renderStaffExpenses(page);
    if (S.view === "spend") return await renderSpend(page);
    if (S.view === "fiscal") return await renderFiscal(page);
    if (S.view === "schedule") return await renderSchedule(page);
    if (S.view === "members" && !can("members", "full")) return await renderCoachKids(page);
    if (["members", "skips"].includes(S.view)) return await renderBoard(page, S.view);
    if (S.view === "nodeadline") return await renderNoDeadline(page);
    if (S.view === "dashboard") return await renderDashboard(page);
    if (S.view === "revenue-losses") return await renderRevenueLosses(page);
    if (S.view === "inbox") return await renderInbox(page);
    if (S.view === "coaches") return await renderCoaches(page);
    if (S.view === "journal") return await renderJournal(page);
    if (S.view === "meet") return await renderMeet(page);
    if (S.view === "team") return await renderTeam(page);
    if (S.view === "posts") return await renderPosts(page);
    if (S.view === "search") return await renderSearch(page);
    if (S.view === "settings") return await renderSettings(page);
    if (S.view === "docs") return await renderDocs(page);
  } catch (e) {
    // часть файлов CRM не догрузилась (например, открыли страницу, пока сервер перезапускался): один раз перезагружаем сами
    if (e instanceof ReferenceError) {
      let again = false;
      try { again = sessionStorage.getItem("crm.reloaded") === "1"; sessionStorage.setItem("crm.reloaded", "1"); } catch (_) {}
      if (!again) { location.reload(); return; }
      page.innerHTML = `<div class="error">CRM загрузилась не полностью. Обновите страницу: потяните её вниз или нажмите ⟳ в адресной строке</div>`;
      return;
    }
    if (e.message !== "Нужно войти") page.innerHTML = `<div class="error">${esc(e.message)}</div>`;
  }
}

/* ---------- дашборд ---------- */
const fmtNum = (n) => Number(n || 0).toLocaleString("ru-RU");
const ICONS = {
  wallet: '<path d="M3 7h15a3 3 0 0 1 3 3v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Zm0 0 12-3v3"/><circle cx="17" cy="14" r="1.2"/>',
  cart: '<path d="M3 4h2l2.5 11h10L20 8H6.2"/><circle cx="9" cy="19" r="1.4"/><circle cx="17" cy="19" r="1.4"/>',
  calc: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h3M9.5 6.5v3M13 8h3M8 15l3 3M11 15l-3 3M13 15.5h3M13 17.5h3"/>',
  pie: '<path d="M12 3v9h9A9 9 0 1 1 12 3Z"/><path d="M15 3.5A9 9 0 0 1 20.5 9H15V3.5Z"/>',
  trend: '<path d="M3 17l5-5 4 3 7-7"/><circle cx="3" cy="17" r="1.2"/><circle cx="8" cy="12" r="1.2"/><circle cx="12" cy="15" r="1.2"/><circle cx="19" cy="8" r="1.2"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1-4 4-6 8-6s7 2 8 6"/>',
};
const icon = (k) => `<svg class="kpi-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${ICONS[k]}</svg>`;
const delta = (v, label) => v === null || v === undefined
  ? `<div class="delta"><span>${label}</span><b class="muted">—</b></div>`
  : `<div class="delta"><span>${label}</span><b class="${v >= 0 ? "up" : "down"}">${v >= 0 ? "▲" : "▼"} ${v}%</b></div>`;

function kpiCard(ic, title, value, k, extra = "") {
  return `<div class="kpi"><div class="kpi-head">${icon(ic)}<span>${esc(title)}</span></div>
    <div class="kpi-body"><div class="kpi-value">${value}</div>
    ${k ? `<div class="kpi-deltas">${delta(k.vs_month, "мес. назад")}${k.vs_year !== undefined ? delta(k.vs_year, "год назад") : ""}</div>` : ""}</div>${extra}</div>`;
}

function lineChart(labels, cur, prev, fmt) {
  const W = 640, H = 230, L = 58, R = 12, T = 14, B = 44;
  const vals = [...cur, ...prev].filter((v) => v !== null && v !== undefined);
  let lo = Math.min(0, ...vals), hi = Math.max(1, ...vals);
  const step = Math.pow(10, Math.floor(Math.log10(hi - lo || 1))) / 2;
  hi = Math.ceil(hi / step) * step;
  const x = (i) => L + (i * (W - L - R)) / (labels.length - 1);
  const y = (v) => T + (H - T - B) * (1 - (v - lo) / (hi - lo || 1));
  const segs = (arr) => { const out = []; let cur = null; arr.forEach((v, i) => { if (v === null || v === undefined) { cur = null; return; } if (!cur) out.push(cur = []); cur.push(i); }); return out; };
  const line = (arr) => segs(arr).map((g) => g.map((i, n) => `${n ? "L" : "M"}${x(i).toFixed(1)},${y(arr[i]).toFixed(1)}`).join("")).join("");
  const area = (arr) => segs(arr).map((g) => g.map((i, n) => `${n ? "L" : "M"}${x(i).toFixed(1)},${y(arr[i]).toFixed(1)}`).join("") + `L${x(g.at(-1))},${y(lo)}L${x(g[0])},${y(lo)}Z`).join("");
  const ticks = [0, 1, 2, 3, 4].map((k) => lo + ((hi - lo) * k) / 4);
  return `<div class="chart-legend"><span class="lg cur"></span>Последние 12 месяцев <span class="lg prev"></span>Год назад</div>
  <svg viewBox="0 0 ${W} ${H}" class="line-chart" role="img">
    ${ticks.map((t) => `<line x1="${L}" x2="${W - R}" y1="${y(t)}" y2="${y(t)}" class="grid"/><text x="${L - 8}" y="${y(t) + 4}" text-anchor="end" class="tick">${fmt(t)}</text>`).join("")}
    ${labels.map((l, i) => `<text x="${x(i)}" y="${H - B + 18}" text-anchor="end" transform="rotate(-30 ${x(i)} ${H - B + 18})" class="tick">${esc(l)}</text>`).join("")}
    <path d="${area(prev)}" class="area prev"/><path d="${line(prev)}" class="ln prev"/>
    <path d="${area(cur)}" class="area cur"/><path d="${line(cur)}" class="ln cur"/>
    ${cur.map((v, i) => v === null || v === undefined ? "" : `<circle cx="${x(i)}" cy="${y(v)}" r="3.2" class="pt cur"><title>${esc(labels[i])}: ${fmt(v)}</title></circle>`).join("")}
    ${prev.map((v, i) => v === null || v === undefined ? "" : `<circle cx="${x(i)}" cy="${y(v)}" r="2.6" class="pt prev"><title>${esc(labels[i])}, прошлый год: ${fmt(v)}</title></circle>`).join("")}
  </svg>`;
}

function funnelChart(rows) {
  const top = Math.max(1, rows[0][1]);
  const colors = ["#8cc9a4", "#4fa576", "#2a8a58", "#1b6b42"];
  return `<div class="funnel2">${rows.map(([l, v], i) => {
    const w = Math.max(6, Math.round((v / top) * 100));
    const p = i === 0 ? 100 : Math.round((v / Math.max(1, rows[i - 1][1])) * 100);
    return `<div class="fn-row"><div class="fn-track"><div class="fn-bar" style="width:${w}%;background:${colors[i % 4]}"></div></div>
      <div class="fn-label">${esc(l)}: <b>${rows[i - 1] && !rows[i - 1][1] ? "—" : p + "%"}</b> (${v})</div></div>`;
  }).join("")}</div>`;
}

function donut(parts) {
  const total = parts.reduce((a, p) => a + p.v, 0);
  const C = 2 * Math.PI * 40;
  let off = 0;
  const arcs = total ? parts.map((p) => { const len = (p.v / total) * C; const a = `<circle r="40" cx="60" cy="60" fill="none" stroke="${p.c}" stroke-width="22" stroke-dasharray="${len} ${C - len}" stroke-dashoffset="${-off}" transform="rotate(-90 60 60)"><title>${esc(p.l)}</title></circle>`; off += len; return a; }).join("") : `<circle r="40" cx="60" cy="60" fill="none" stroke="var(--surface-2)" stroke-width="22"/>`;
  return `<div class="donut-wrap"><svg viewBox="0 0 120 120" class="donut">${arcs}</svg>
    <ul class="donut-legend">${parts.map((p) => `<li><span style="background:${p.c}"></span>${esc(p.l)}: ${total ? Math.round((p.v / total) * 100) : 0}% (${p.fmt ? p.fmt(p.v) : p.v})</li>`).join("")}</ul></div>`;
}

function dashboardMonths() {
  const months = [];
  for (let offset = 3; offset >= -17; offset--) {
    const dt = new Date(`${S.meta.today.slice(0, 7)}-01T12:00:00`);
    dt.setMonth(dt.getMonth() + offset);
    months.push(isoDate(dt).slice(0, 7));
  }
  return months;
}

async function renderRevenueLosses(page) {
  const filters = S.revenueLosses ||= {month: S.meta.today.slice(0, 7), branch_id: "", group_id: ""};
  const options = (rows, selected) => rows.map(row =>
    `<option value="${row.id}" ${String(row.id) === selected ? "selected" : ""}>${esc(row.name)}</option>`).join("");
  const groups = S.meta.groups.filter(group => !group.is_makeup &&
    (!filters.branch_id || String(group.branch_id) === filters.branch_id));
  page.innerHTML = `<section class="panel"><h2>Выручка и потери</h2>
    <form class="dash-filters" id="revenue-loss-filters">
      <label>Месяц абонемента <input type="month" name="month" value="${esc(filters.month)}" required></label>
      <label>Филиал <select name="branch_id"><option value="">Все филиалы</option>${options(S.meta.branches, filters.branch_id)}</select></label>
      <label>Группа <select name="group_id"><option value="">Все группы</option>${options(groups, filters.group_id)}</select></label>
      <button class="btn primary" type="submit">Сформировать</button>
    </form></section><section class="panel" id="revenue-loss-results" aria-live="polite">Загрузка…</section>`;
  const form = page.querySelector("#revenue-loss-filters");
  form.onsubmit = event => {
    event.preventDefault();
    Object.assign(filters, Object.fromEntries(new FormData(form)));
    render();
  };
  form.elements.branch_id.onchange = () => {
    form.elements.group_id.value = "";
    form.requestSubmit();
  };

  const host = page.querySelector("#revenue-loss-results");
  try {
    const report = await api("GET", "/api/revenue-losses?" + new URLSearchParams(filters));
    if (!host.isConnected) return;
    const t = report.totals;
    const rows = [
      ["Потенциальная выручка по оформленным продажам", t.potential],
      ["Фактическая выручка по продажам", t.actual],
      ["Всего потерь", t.losses],
      ...report.discounts.map(([name, amount]) => ["Скидка · " + name, amount]),
      ["Бонусы за рекомендации", t.bonuses],
      ["Перерасчёт пропусков", t.recalculations],
      ["Неполные абонементы", t.incomplete],
      ["Наценки", t.surcharge],
      ["Продано абонементов", t.subscriptions, true],
      ["Продано разовых", t.singles, true],
      ["Уникальные покупатели", t.customers, true],
      ["Средний чек", t.average, false, true],
    ];
    const share = (n, number, average) => !t.potential || number || average ? "" :
      (n / t.potential).toLocaleString("ru-RU", {style: "percent", maximumFractionDigits: 2});
    host.innerHTML = `<div class="panel-head"><h3>${esc(monthLabel(report.month))}</h3><button class="btn sm" data-revenue-export>Скачать CSV</button></div>
      <div class="client-history-table-wrap"><table class="client-history-table">
      <thead><tr><th>Показатель</th><th>Значение</th><th>Доля от потенциала</th></tr></thead>
      <tbody>${rows.map(([label, n, number, average]) => `<tr><td>${esc(label)}</td><td><b>${number ? fmtNum(n) : rub(n)}</b></td><td>${share(n, number, average)}</td></tr>`).join("")}</tbody>
      </table></div><p class="small muted">Отчёт по оплаченным продажам за месяц действия абонемента. Бонус учтён в месяце покупки, на которую его зачли.</p>
      ${t.incomplete_unknown ? `<p class="small muted">Для ${fmtNum(t.incomplete_unknown)} прошлых абонементов оценка неполного месяца ограничена сохранёнными данными.</p>` : ""}`;
    host.querySelector("[data-revenue-export]").onclick = () => {
      const csvText = value => '"' + String(value).replaceAll('"', '""') + '"';
      const csv = [["Месяц", "Показатель", "Значение", "Доля от потенциала"],
        ...rows.map(([label, n, number, average]) => [report.month, label, n, share(n, number, average)])];
      const url = URL.createObjectURL(new Blob(["\ufeff" + csv.map(row => row.map(csvText).join(";")).join("\r\n")],
        {type: "text/csv;charset=utf-8"}));
      const link = document.createElement("a");
      link.href = url;
      link.download = `revenue-${report.month}.csv`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    };
  } catch (error) {
    if (host.isConnected) host.textContent = error.message;
  }
}

async function renderDashboard(page) {
  if (!S.dashMonth) S.dashMonth = S.meta.today.slice(0, 7);
  if (S.dashBranch === undefined) S.dashBranch = "";
  if(S.dashGroup===undefined)S.dashGroup='';
  const dashGroups=(S.meta.groups||[]).filter(g=>!g.is_makeup&&(!S.dashBranch||String(g.branch_id)===String(S.dashBranch)));
  if(S.dashGroup&&!dashGroups.some(g=>String(g.id)===String(S.dashGroup)))S.dashGroup='';
  if (!S.dashSeg) S.dashSeg = "count";
  if (S.dashTab && S.dashTab !== "overview") {
    const ms = dashboardMonths();
    page.innerHTML = `<div class="dash-filters">
      <select id="dash-month">${ms.map((x) => `<option value="${x}" ${x === S.dashMonth ? "selected" : ""}>${monthLabel(x)}</option>`).join("")}</select>
      ${S.dashTab === "sources" || S.dashTab === "program" ? "" : `<select id="dash-branch" data-search-select="off"><option value="">Все филиалы</option>${S.meta.branches.map((b) => `<option value="${b.id}" ${String(b.id) === String(S.dashBranch) ? "selected" : ""}>${esc(b.name)}</option>`).join("")}</select>`}
      </div>${anTabsHtml()}<div id="an-box"></div>`;
    $("#dash-month").onchange = (e) => { S.dashMonth = e.target.value; render(); };
    if ($("#dash-branch")) $("#dash-branch").onchange = (e) => { S.dashBranch = e.target.value; render(); };
    wireAnTabs(page);
    return renderAnalytics($("#an-box"));
  }
  const d = await api("GET", `/api/dashboard?month=${S.dashMonth}${S.dashBranch ? "&branch_id=" + S.dashBranch : ""}${S.dashGroup?"&group_id="+S.dashGroup:""}`);
  const months = dashboardMonths();
  const k = d.kpi, sr = d.series;
  const kRub = (t) => t >= 1e6 ? (t / 1e6).toFixed(1).replace(".0", "") + " млн" : t >= 1000 ? Math.round(t / 1000) + " тыс" : fmtNum(t);
  const segColors = { new: "#8cc9a4", regular: "#1f7a4d", returned: "#d9a441" };
  const seg = d.segments[S.dashSeg];
  page.innerHTML = `
    <div class="dash-filters">
      <select id="dash-month">${months.map((x) => `<option value="${x}" ${x === S.dashMonth ? "selected" : ""}>${monthLabel(x)}</option>`).join("")}</select>
      <select id="dash-branch" data-search-select="off"><option value="">Все филиалы</option>${S.meta.branches.map((b) => `<option value="${b.id}" ${String(b.id) === String(S.dashBranch) ? "selected" : ""}>${esc(b.name)}</option>`).join("")}</select>
      <select id="dash-group" data-search-select="off" aria-label="Группа"><option value="">Все группы</option>${dashGroups.map(g=>`<option value="${g.id}" ${String(g.id)===String(S.dashGroup)?'selected':''}>${esc(g.name)}</option>`).join('')}</select>
    </div>
    ${anTabsHtml()}

    <div class="kpis">
      ${kpiCard("wallet", "Выручка", `${fmtNum(k.revenue.value)} <small>₽</small>`, k.revenue)}
      ${kpiCard("cart", "Уникальных держателей абонементов", fmtNum(k.holders.value), k.holders, `<div class="kpi-note">ребёнок с абонементами в две группы считается один раз</div>`)}
      ${kpiCard("calc", "Средний чек", `${fmtNum(k.avg.value)} <small>₽</small>`, k.avg, `<div class="kpi-note">средняя сумма одного абонемента</div>`)}
      ${kpiCard("pie", "Заполненность", `<span class="fill2"><span><em>по абонементам</em>${k.fill.subs ?? "—"} <small>%</small></span><span><em>по уникальным</em>${k.fill.value ?? "—"} <small>%</small></span></span>`, { vs_month: k.fill.vs_month }, `<div class="kpi-note">${k.fill.subs_count} абонементов и ${k.holders.value} учеников на ${k.seats} мест</div>`)}
      ${kpiCard("trend", "Конверсия из месяца в месяц", `${k.conversion.value ?? "—"} <small>%</small>`, k.conversion, `<div class="kpi-note">продлили из тех, кто занимался в прошлом месяце</div>`)}
      ${kpiCard("user", "Количество мест", `${fmtNum(k.seats)} <small>шт</small>`, null, `<div class="kpi-note">сумма мест во всех группах</div>`)}
    </div>

    <div class="dash-row2">
      <div class="panel">
        <table class="day-table"><thead><tr><th></th><th>За вчера</th><th>За сегодня</th></tr></thead><tbody>
          <tr><td>Получено лидов</td><td>${d.days.yesterday.leads} шт.</td><td>${d.days.today.leads} шт.</td></tr>
          <tr><td>Записаны на пробное</td><td>${d.days.yesterday.trial_booked} шт.</td><td>${d.days.today.trial_booked} шт.</td></tr>
          <tr><td>Купили впервые</td><td>${d.days.yesterday.bought_first} шт.</td><td>${d.days.today.bought_first} шт.</td></tr>
        </tbody></table>
      </div>
      <div class="panel center">
        <div class="kpi-head" style="justify-content:center"><span>Записи на пробное на будущие дни</span></div>
        <div class="kpi-value" style="margin-top:8px">${d.future_trials} <small>шт</small></div>
        <div class="kpi-note">Отработки: можно отработать ${d.makeups.can_make_up}${d.makeups.at_risk_rub ? `, ≈ ${rub(d.makeups.at_risk_rub)} могут уйти в перерасчёт` : ""}</div>
      </div>
    </div>

    <div class="chart-grid">
      <section class="panel"><div class="panel-head">Выручка по месяцам</div>${lineChart(d.months, sr.revenue.cur, sr.revenue.prev, kRub)}</section>
      <section class="panel"><div class="panel-head">Количество уникальных держателей абонементов</div>${lineChart(d.months, sr.holders.cur, sr.holders.prev, fmtNum)}</section>
      <section class="panel"><div class="panel-head">Средний чек по месяцам</div>${lineChart(d.months, sr.avg.cur, sr.avg.prev, fmtNum)}</section>
      <section class="panel"><div class="panel-head">Конверсия из месяца в месяц, %</div>${lineChart(d.months, sr.conversion.cur, sr.conversion.prev, (t) => Math.round(t))}</section>
      <section class="panel"><div class="panel-head">Воронка продаж по лидам · ${esc(d.month_label)}</div>${funnelChart(d.funnel_leads)}</section>
      <section class="panel"><div class="panel-head">Воронка продаж по записанным на пробное · ${esc(d.month_label)}</div>${funnelChart(d.funnel_trials)}</section>
      <section class="panel">
        <div class="panel-tabs"><button data-seg="count" class="${S.dashSeg === "count" ? "active" : ""}">Абонементы</button><button data-seg="revenue" class="${S.dashSeg === "revenue" ? "active" : ""}">Выручка</button></div>
        ${donut([
          { l: "Новые посетители", v: seg.new, c: segColors.new, fmt: S.dashSeg === "revenue" ? rub : null },
          { l: "Постоянные посетители", v: seg.regular, c: segColors.regular, fmt: S.dashSeg === "revenue" ? rub : null },
          { l: "Вернувшиеся посетители", v: seg.returned, c: segColors.returned, fmt: S.dashSeg === "revenue" ? rub : null },
        ])}
      </section>
    </div>`;
  $("#dash-month").onchange = (e) => { S.dashMonth = e.target.value; render(); };
  $("#dash-branch").onchange = (e) => { S.dashBranch = e.target.value; S.dashGroup=""; render(); };
  page.querySelectorAll("[data-seg]").forEach((b) => (b.onclick = () => { S.dashSeg = b.dataset.seg; render(); }));
  $("#dash-group").onchange=e=>{S.dashGroup=e.target.value;render()};
  if(typeof pastGroupSearch==='function')pastGroupSearch($("#dash-group"));
  wireAnTabs(page);
}

/* ---------- воронки ---------- */
const BOARD_TITLES = { sales: "Воронка продаж", members: "Действующие клиенты", skips: "Пропуски и отработки" };

function boardHtml(data) {
  let hidden = 0, later = 0;
  if (data.funnel === "sales" && data.today_end) {
    data = { ...data, columns: data.columns.map((c) => {
      const cards = S.boardAll ? c.cards : c.cards.filter((k) => !k.due || k.due <= data.today_end);
      hidden += c.cards.length - cards.length;
      if (c.key === "later") later = c.cards.length;
      return { ...c, cards };
    }).filter((c) => S.boardAll || c.key !== "later" || c.cards.length) };
  }
  const bar = data.funnel === "sales" && data.today_end ? `<div class="board-bar small">
      ${S.boardAll ? "Показаны все карточки" : "Показаны задачи на сегодня"}${!S.boardAll && hidden ? ` · отложено на другие дни: <b>${hidden}</b>` : ""}${later ? ` · «Пригласим позже»: ${later}` : ""}
      <button class="linkish small" data-boardall>${S.boardAll ? "Только на сегодня" : "Показать все"}</button></div>` : "";
  S.boardCardDetails=S.boardCardDetails||{};
  data.columns.forEach(column=>column.cards.forEach(card=>{const student=card.student||card;card._board_detail_key=`${data.funnel}:${student.id||card.id}:${card.task_id||card.id}`;S.boardCardDetails[card._board_detail_key]=card}));
  return `${bar}<div class="board">${data.columns.map((c) => `
      <section class="col" data-stage="${c.key}">
        <div class="col-head"><h3>${esc(c.title)}</h3>${c.auto ? `<span class="auto">сам</span>` : ""}<span class="count">${c.cards.length}</span></div>
        <div class="col-body">${c.cards.length ? c.cards.map((k) => cardHtml(k, data.draggable && !c.auto)).join("") : `<div class="empty">Пусто</div>`}</div>
      </section>`).join("")}
    </div>`;
}

function wireBoard(page, funnel, data) {
  page.querySelectorAll("[data-boardall]").forEach((b) => (b.onclick = () => { S.boardAll = !S.boardAll; render(); }));
  page.querySelectorAll(".card").forEach((el) => (el.onclick = () => openStudent(+el.dataset.id)));
  wireBoardCardPreview(page);
  if (data.draggable) enableDrag(page, funnel, data.columns);
}

function wireBoardCardPreview(page){
  const popup=document.createElement("aside");popup.className="board-card-preview";popup.hidden=true;popup.setAttribute("role","tooltip");document.body.appendChild(popup);
  let current=null,timer;
  const hide=()=>{clearTimeout(timer);popup.hidden=true;current=null};
  const position=()=>{if(!current?.isConnected)return hide();const r=current.getBoundingClientRect(),w=Math.min(380,innerWidth-24);popup.style.width=w+"px";popup.style.left=Math.max(12,Math.min(innerWidth-w-12,r.right+10))+"px";const h=popup.getBoundingClientRect().height;popup.style.top=Math.max(12,Math.min(innerHeight-h-12,r.top))+"px"};
  const show=card=>{clearTimeout(timer);current=card;const item=S.boardCardDetails?.[card.dataset.detailKey];if(!item)return hide();const student=item.student||item,title=item.task_title||item.title||item.due_title||"Текущая задача",due=item.due||item.due_at||"",note=item.task_note||item.note||"Подробное описание пока не добавлено";const extra=[student.group,student.branch?`Филиал: ${student.branch}`:"",student.source?`Источник: ${student.source}`:"",student.tariff?`Тариф: ${student.tariff}`:""].filter(Boolean);popup.innerHTML=`<div class="board-card-preview-label">Текущая задача</div><strong>${esc(title)}</strong><div class="board-card-preview-client"><span>${esc(displayChildName(student.name||""))}${student.parent?` · ${esc(student.parent)}`:""}</span>${student.phone?`<span class="board-card-preview-phone">${esc(fmtPhone(student.phone))}</span>`:""}</div>${due?`<div class="board-card-preview-due">Срок: ${dShort(due)}${due.slice(11,16)?` · ${esc(due.slice(11,16)==="23:59"?"в течение дня":due.slice(11,16))}`:""}</div>`:""}${extra.length?`<div class="board-card-preview-extra">${extra.map(esc).join(" · ")}</div>`:""}<div class="board-card-preview-note">${esc(note)}</div>`;popup.hidden=false;position()};
  const later=()=>{clearTimeout(timer);timer=setTimeout(hide,160)};
  page.querySelectorAll(".card").forEach(card=>{card.addEventListener("mouseenter",()=>{if(matchMedia("(hover:hover)").matches)show(card)});card.addEventListener("mouseleave",later);card.addEventListener("focus",()=>show(card));card.addEventListener("blur",later)});
  const controller=new AbortController(),signal=controller.signal;window.addEventListener("resize",position,{signal});window.addEventListener("scroll",()=>{if(!popup.hidden)position()},{capture:true,passive:true,signal});
  const observer=new MutationObserver(()=>{if(!page.isConnected){hide();popup.remove();controller.abort();observer.disconnect()}});observer.observe(document.body,{childList:true,subtree:true});
}

async function renderBoard(page, funnel) {
  const data = await api("GET", "/api/board/" + funnel);
  if (funnel === "members") return renderMembersList(page, data);
  page.innerHTML = `
    <div class="page-head"><h2 class="grow">${BOARD_TITLES[funnel]}</h2>
      ${funnel === "members" && can("members", "full") ? `<button class="btn sm" id="arch-btn">🗄 Архив +</button>` : ""}
</div>
    <div class="section arch-box" id="arch-box" hidden></div>
    ${data.note ? `<div class="note">${esc(data.note)}</div>` : ""}
    ${boardHtml(data)}`;
  if ($("#arch-btn")) membersArchive(page);
  wireBoard(page, funnel, data);
}

/* «Ученики»: список с поиском, фильтрами и группировкой по филиалам и группам */
S.mf = (() => { try { return JSON.parse(localStorage.getItem("crm.mf") || "null") || {}; } catch (_) { return {}; } })();
const M_STATUS = [["", "Все"], ["single", "Разовые"], ["unpaid_cur", "Не оплатили"], ["renew", "Продлить"], ["invoiced", "Счёт выставлен. Ждём"], ["paid", "Оплачено"], ["left", "Ушли"]];
function renderMembersList(page, data) {
  const cols = Object.fromEntries(data.columns.map((c) => [c.key, c.title]));
  const all = data.columns.flatMap((c) => c.cards);
  const f = S.mf;
  const save = () => { try { localStorage.setItem("crm.mf", JSON.stringify(f)); } catch (_) {} };
  const branches = [...new Set(all.flatMap((k) => k.groups.map((g) => g.branch)))].sort();
  const groups = [...new Map(all.flatMap((k) => k.groups).filter((g) => !f.branch || g.branch === f.branch).map((g) => [String(g.id), g])).values()]
    .sort((a, b) => (a.branch + a.name).localeCompare(b.branch + b.name));
  if (f.group && !groups.some((g) => String(g.id) === f.group)) f.group = "";
  const inPlace = (k) => (!f.branch || k.groups.some((g) => g.branch === f.branch)) && (!f.group || k.groups.some((g) => String(g.id) === f.group));
  const q = f.q || "", qd = q.replace(/\D/g, "");
  const hit = (k) => !q || searchTextMatches(k.name + " " + k.parent + " " + k.family, q) || (qd.length > 2 && (k.phone || "").replace(/\D/g, "").includes(qd));
  const base = all.filter((k) => inPlace(k) && hit(k));
  const cnt = (s) => base.filter((k) => (s ? k.status === s : k.status !== "left")).length;
  const rows = base.filter((k) => (f.status ? k.status === f.status : k.status !== "left"));
  const pill = { single: "", unpaid_cur: "red", renew: "", invoiced: "", paid: "green", left: "" };
  const row = (k, g) => `<div class="m-row ${k.color === "red" ? "m-red" : k.color === "yellow" ? "m-yellow" : ""}" data-open="${k.id}" role="button" tabindex="0">
    <span class="m-name"><b>${esc(displayChildName(k.name))}</b></span>
    <span class="small muted m-parent">${esc(k.parent)}${k.phone ? ", " + esc(fmtPhone(k.phone)) : ""}</span>
    ${g ? "" : `<span class="small m-group">${esc(k.groups.map((x) => x.branch + " · " + x.name).join(", ") || k.group)}</span>`}
    <span class="m-st"><span class="pill ${pill[k.status]}">${esc(k.badge || cols[k.status] || "")}</span></span></div>`;
  let body;
  if (!rows.length) body = `<div class="muted small" style="padding:12px">Никого не нашли</div>`;
  else if (f.grouped) {
    const by = new Map();
    for (const k of rows) for (const g of (k.groups.length ? k.groups : [{ id: 0, name: "Без группы", branch: "" }])) {
      if (f.group && String(g.id) !== f.group) continue;
      if (f.branch && g.branch !== f.branch) continue;
      const key = g.branch + "|" + g.name;
      if (!by.has(key)) by.set(key, { g, list: [] });
      by.get(key).list.push(k);
    }
    let lastBr = null;
    body = [...by.values()].sort((a, b) => (a.g.branch + a.g.name).localeCompare(b.g.branch + b.g.name)).map(({ g, list }) => {
      const brHead = g.branch !== lastBr ? `<h3 class="m-branch">${esc(g.branch || "Без филиала")}</h3>` : "";
      lastBr = g.branch;
      return `${brHead}<details class="m-grp" open><summary><b>${esc(g.name)}</b> <span class="muted small">${list.length}</span></summary>${list.map((k) => row(k, true)).join("")}</details>`;
    }).join("");
  } else body = rows.map((k) => row(k, false)).join("");
  page.innerHTML = `
    <div class="page-head"><h2 class="grow">${BOARD_TITLES.members}</h2>
      ${can("members", "full") ? `<button class="btn sm" id="dup-btn" hidden>👯 Дубли</button><button class="btn sm" id="arch-btn">🗄 Архив +</button>` : ""}
</div>
    <div class="section arch-box" id="arch-box" hidden></div>
    <div class="m-views" id="m-views"></div>
    <div class="m-filters">
      <input id="mf-q" placeholder="Поиск: ребёнок, родитель или телефон" value="${esc(f.q || "")}">
      <select id="mf-branch"><option value="">Все филиалы</option>${branches.map((b) => `<option ${f.branch === b ? "selected" : ""}>${esc(b)}</option>`).join("")}</select>
      <select id="mf-group"><option value="">Все группы</option>${groups.map((g) => `<option value="${g.id}" ${f.group === String(g.id) ? "selected" : ""}>${esc((f.branch ? "" : g.branch + " · ") + g.name)}</option>`).join("")}</select>
      <label class="check small"><input type="checkbox" id="mf-grouped" ${f.grouped ? "checked" : ""}> По филиалам и группам</label>
    </div>
    <div class="chips" style="margin:10px 0">${M_STATUS.map(([k, t]) => `<button class="chip ${(f.status || "") === k ? "on" : ""}" data-mst="${k}">${t} <span class="muted">${cnt(k)}</span></button>`).join("")}</div>
    ${data.note ? `<div class="note">${esc(data.note)}</div>` : ""}
    <div class="section m-list">${body}</div>`;
  const rerender = () => { save(); renderMembersList(page, data); };
  $("#mf-q").oninput = (e) => { f.q = e.target.value; const pos = e.target.selectionStart; rerender(); const i = $("#mf-q"); i.focus(); i.setSelectionRange(pos, pos); };
  $("#mf-branch").onchange = (e) => { f.branch = e.target.value; f.group = ""; rerender(); };
  $("#mf-group").onchange = (e) => { f.group = e.target.value; rerender(); };
  $("#mf-grouped").onchange = (e) => { f.grouped = e.target.checked; rerender(); };
  page.querySelectorAll("[data-mst]").forEach((b) => (b.onclick = () => { f.status = b.dataset.mst; rerender(); }));
  page.querySelectorAll(".m-list [data-open]").forEach((b) => { b.onclick = () => openStudent(+b.dataset.open); b.onkeydown = (e) => { if (e.key === "Enter") openStudent(+b.dataset.open); }; });
  if ($("#arch-btn")) membersArchive(page);
  membersViews(page, f, rerender);
  if ($("#dup-btn")) membersDups($("#dup-btn"));
}

/* Настройки → Подсказки по этапам: что сказать и что сделать, чтобы перейти дальше */
async function renderTipsSettings(box) {
  const d = await api("GET", "/api/stage-tips");
  box.innerHTML = `<h3>Подсказки по этапам</h3><div class="small muted" style="margin-bottom:10px">Видны в карточке лида на жёлтой плашке «💡 Подсказка». Пишите так, как хотите, чтобы говорил администратор</div>
    ${Object.entries(d.tips).map(([k, t]) => `<div class="section" style="box-shadow:none;padding:10px 0;border-top:1px solid var(--line)"><b>${esc(d.labels[k])}</b>
      <div class="field"><label>Что сказать</label><textarea rows="3" data-tsay="${k}">${esc(t.say)}</textarea></div>
      <div class="field"><label>Чтобы перейти дальше</label><input data-tnext="${k}" value="${esc(t.next)}"></div></div>`).join("")}
    <button class="btn primary" id="tips-save">Сохранить</button>`;
  $("#tips-save").onclick = async () => {
    const tips = Object.fromEntries(Object.keys(d.tips).map((k) => [k, { say: box.querySelector(`[data-tsay="${k}"]`).value, next: box.querySelector(`[data-tnext="${k}"]`).value }]));
    try { await api("PUT", "/api/stage-tips", { tips }); toast("Подсказки сохранены"); } catch (e) { fail(e); }
  };
}

function erpWireFiles(box, d) {
  $("#erp-file").onchange = async (e) => {
    for (const f of e.target.files) {
      const data = await new Promise((res) => { const r = new FileReader(); r.onload = () => res(r.result.split(",")[1]); r.readAsDataURL(f); });
      try { const r = await api("POST", "/api/erp/upload", { filename: f.name, data }); toast(`${f.name}: ${r.rows} строк`); } catch (err) { fail(err); }
    }
    renderErp(box);
  };
  box.querySelectorAll("[data-ukind]").forEach((sel) => (sel.onchange = async () => { await api("PUT", `/api/erp/uploads/${sel.dataset.ukind}`, { kind: sel.value }).catch(fail); renderErp(box); }));
  box.querySelectorAll("[data-umap]").forEach((sel) => (sel.onchange = async () => {
    const card = sel.closest("[data-up]"), mapping = {};
    card.querySelectorAll("[data-umap]").forEach((x) => { if (x.value !== "") mapping[x.dataset.field] = +x.value; });
    await api("PUT", `/api/erp/uploads/${card.dataset.up}`, { mapping }).catch(fail);
    const u = d.uploads.find((x) => x.id === +card.dataset.up); u.mapping = mapping;
    const ex = card.querySelector(`[data-ex="${sel.dataset.field}"]`); if (ex) ex.textContent = sel.value !== "" ? (u.preview[0] || [])[+sel.value] || "" : "";
  }));
  box.querySelectorAll("[data-udel]").forEach((b) => (b.onclick = async () => { if (!confirm("Убрать этот файл?")) return; await api("DELETE", `/api/erp/uploads/${b.dataset.udel}`).catch(fail); renderErp(box); }));
}

function erpLeadImportSetup(box, data) {
  const root = document.createElement("section");
  root.className = "erp-step";
  root.dataset.erpLeadImport = "1";
  root.innerHTML = `<h3>Импорт лидов из ERP</h3>
    <p>Выберите Excel с лидами. Проверка покажет карточки и филиалы. Перенос добавит недостающие филиалы и задачи.</p>
    <label>Файл лидов <input type="file" accept=".xlsx,.csv" data-lead-file></label>
    <label>Или выберите загруженный файл <select data-lead-upload><option value="">Выберите файл</option>${data.uploads.filter(f=>f.kind==="clients").map(f=>`<option value="${f.id}">${esc(f.filename)}</option>`).join("")}</select></label>
    <button class="btn" data-lead-preview>Проверить лиды</button>
    <button class="btn primary" data-lead-apply disabled>Перенести лиды</button>
    <div data-lead-result aria-live="polite"></div>`;
  box.querySelector(".erp-page-head").after(root);
  const file = root.querySelector("[data-lead-file]"), saved = root.querySelector("[data-lead-upload]");
  const preview = root.querySelector("[data-lead-preview]"), apply = root.querySelector("[data-lead-apply]");
  const output = root.querySelector("[data-lead-result]");
  let approved = null;
  const reset = () => { approved = null; apply.disabled = true; output.textContent = ""; };
  file.onchange = () => { saved.value = ""; reset(); };
  saved.onchange = () => { file.value = ""; reset(); };
  const run = async writing => {
    preview.disabled = apply.disabled = file.disabled = saved.disabled = true;
    try {
      let body = approved;
      if (!writing) {
        let id = Number(saved.value);
        if (file.files[0]) {
          const chosen = file.files[0];
          const encoded = await new Promise((resolve,reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result.split(",")[1]);
            reader.onerror = reject;
            reader.readAsDataURL(chosen);
          });
          id = (await api("POST", "/api/erp/upload", {filename:chosen.name,data:encoded,kind:"clients"})).id;
        }
        if (!id) throw new Error("Выберите файл лидов");
        body = {visitors:[], leads:[id]};
      }
      const r = await api("POST", "/api/erp/people/" + (writing ? "apply" : "plan"), body);
      const branches = r.lead_branches || [];
      output.innerHTML = `<p>${writing?"Перенос выполнен. ":""}Лидов: ${r.counts.leads||0}. Новых карточек: ${r.counts.new||0}. Строк на сверку: ${r.issues.length}.</p>
        <ul>${branches.map(b=>`<li>${esc(b.label)}: ${b.matches.length===1?esc(b.matches[0].name):b.matches.length?"несколько совпадений":writing?"добавлен":"будет создан"}${b.inactive?" · не работает":""}</li>`).join("")}</ul>
        ${r.issues.length?`<details><summary>Строки на сверку</summary><ul>${r.issues.map(([row,reason])=>`<li>${esc(row)}: ${esc(reason)}</li>`).join("")}</ul></details>`:""}`;
      approved = !writing && branches.every(b=>b.matches.length<=1) && (r.counts.same||r.counts.updated||r.counts.new)
        ? {...body, token:r.token} : null;
    } catch (error) { approved = null; output.textContent = error.message; }
    finally { preview.disabled = file.disabled = saved.disabled = false; apply.disabled = !approved; }
  };
  preview.onclick = () => run(false);
  apply.onclick = () => run(true);
}

function erpPeopleSetup(box, data) {
  const options = data.uploads.map((file) => `<option value="${file.id}">${esc(file.filename)} (${file.count})</option>`).join("");
  box.insertAdjacentHTML("beforeend", `
    <div class="erp-step" id="erp-people">
      <b>Карточки клиентов, статусы и лиды</b>
      <p class="small muted">Переносятся карточки без счетов и денежных операций. Обязательные поля проверки: имя и фамилия ребёнка, дата рождения, телефон, имя и роль родителя, почта, рекламный источник и комментарий. Уже созданные карточки, в том числе дети из сборной, сопоставляются без дублей: пустые поля дополняются, расхождения показываются для ручной сверки</p>
      <div class="field"><label for="erp-people-visitors">Посетители, включая архив</label><select id="erp-people-visitors" multiple>${options}</select></div>
      <div class="field"><label for="erp-people-leads">Лиды</label><select id="erp-people-leads" multiple>${options}</select></div>
      <div class="field"><label for="erp-people-ids">Только указанные ERP ID (необязательно)</label><textarea id="erp-people-ids" rows="2" placeholder="Например: 64928, 65188, 79204"></textarea><div class="small muted">Если поле заполнено, остальные строки полных файлов полностью пропускаются и новые карточки по ним не создаются</div></div>
      <label><input type="checkbox" id="erp-people-create-visitors"> Создать отсутствующие карточки посетителей и дополнить пустые поля существующих</label>
      <p class="small muted">Для этого режима выберите только выгрузку посетителей и оставьте поле ERP ID пустым. Заполненные статус и филиал сохраняются.</p>
      <button class="btn" id="erp-people-plan" ${options ? "" : "disabled"}>Проверить</button>
      <button class="btn primary" id="erp-people-apply" disabled>Перенести</button>
      <div id="erp-people-out" aria-live="polite"></div>
    </div>`);
  const root = box.querySelector("#erp-people");
  const visitors = root.querySelector("#erp-people-visitors"), leads = root.querySelector("#erp-people-leads");
  const createVisitors = root.querySelector("#erp-people-create-visitors");
  const ids = root.querySelector("#erp-people-ids");
  const check = root.querySelector("#erp-people-plan"), apply = root.querySelector("#erp-people-apply");
  const out = root.querySelector("#erp-people-out");
  const selected = (select) => Array.from(select.selectedOptions, (option) => +option.value);
  let approved = null;
  function show(report, applied) {
    const n = report.counts, p = report.profile || {}, missing = Object.entries(p.source_missing || {}).filter(([, count]) => count), fill = Object.entries(p.fill || {}).filter(([, count]) => count);
    const filter = report.filter || {};
    out.innerHTML = `<p>${applied ? "Перенос выполнен. " : ""}Строк посетителей ${n.visitors || 0}, лидов ${n.leads || 0}. Совпадают ${n.same || 0}, обновление статуса ${n.updated || 0}, новые карточки ${n.new || 0}, повторные строки ${n.repeated_rows || 0}</p>
      ${(filter.requested || []).length ? `<div class="note"><b>Фильтр ERP ID:</b> найдено ${(filter.found || []).length} из ${filter.requested.length}${(filter.missing || []).length ? `. Не найдены: ${filter.missing.map(esc).join(", ")}` : ""}</div>` : ""}
      ${fill.length ? `<div class="note"><b>${applied ? "Дополнены" : "Будут дополнены"} пустые поля</b><ul>${fill.map(([field, count]) => `<li>${esc(field)}: ${count}</li>`).join("")}</ul></div>` : ""}
      ${missing.length ? `<details><summary>Нет значения в исходной ERP: ${missing.reduce((total, [, count]) => total + count, 0)}</summary><ul>${missing.map(([field, count]) => `<li>${esc(field)}: ${count}</li>`).join("")}</ul><p class="small muted">CRM не придумывает отсутствующие сведения. Такие поля нужно заполнить позднее по подтверждённому источнику</p></details>` : ""}
      ${!createVisitors.checked && (p.conflicts || []).length ? `<details open><summary>Расхождения карточки и ERP: ${p.conflicts.length}</summary><ul>${p.conflicts.map((item) => `<li>ERP ID ${esc(item.erp_id)} · ${esc(item.field)}: в CRM «${esc(item.crm)}», в ERP «${esc(item.erp)}». Значение CRM сохранится до ручной сверки</li>`).join("")}</ul></details>` : ""}
      ${report.issues.length ? `<details><summary>Требуют сверки: ${report.issues.length}</summary><ul>${report.issues.map(([row, reason]) => `<li>${esc(row)}: ${esc(reason)}</li>`).join("")}</ul></details>` : ""}`;
  }
  createVisitors.onchange = visitors.onchange = leads.onchange = ids.oninput = () => { approved = null; apply.disabled = true; out.replaceChildren(); };
  async function run(applyChanges) {
    const erp_ids = ids.value.split(/[^0-9]+/).filter(Boolean);
    const files = { visitors: selected(visitors), leads: selected(leads), erp_ids, visitor_cards: createVisitors.checked };
    if (applyChanges && !approved) return;
    if (applyChanges) files.token = approved;
    approved = null;
    check.disabled = apply.disabled = visitors.disabled = leads.disabled = createVisitors.disabled = true;
    out.textContent = applyChanges ? "Переношу…" : "Проверяю…";
    try {
      const report = await api("POST", `/api/erp/people/${applyChanges ? "apply" : "plan"}`, files);
      show(report, applyChanges);
      if (!applyChanges && (report.counts.same || report.counts.updated || report.counts.new)) {
        approved = report.token;
        apply.disabled = false;
      }
    } catch (error) {
      out.textContent = error.message;
    } finally {
      check.disabled = visitors.disabled = leads.disabled = createVisitors.disabled = false;
    }
  }
  check.onclick = () => run(false);
  apply.onclick = () => run(true);
}

/* Simple owner-facing path for a small, explicit ERP-ID list. */
function erpPriorityPeopleSetup(box) {
  const primary = box.querySelector("#erp-history-transfer");
  if (!primary || box.querySelector("[data-erp-priority]")) return;
  const section = document.createElement("section");
  section.className = "erp-step";
  section.dataset.erpPriority = "1";
  section.innerHTML = `<div class="erp-step-title"><span class="erp-step-number">2</span><div><h3>Точечно перенести карточки по ERP ID</h3>
    <p>Выберите два исходных Excel и вставьте ID нужных детей. Все остальные строки файлов будут полностью пропущены. Платежи и баланс не переносятся</p></div></div>
    <div class="field"><label for="erp-priority-visitors">Посетители из ERP</label><input id="erp-priority-visitors" type="file" accept=".xlsx,.csv"><div class="small muted">Например, «Посетители (39).xlsx»</div></div>
    <div class="field"><label for="erp-priority-leads">Лиды из ERP</label><input id="erp-priority-leads" type="file" accept=".xlsx,.csv"><div class="small muted">Например, «Leads (3).xlsx»</div></div>
    <div class="field"><label for="erp-priority-ids">ERP ID нужных детей</label><textarea id="erp-priority-ids" rows="4" placeholder="По одному ID в строке"></textarea></div>
    <div class="row" style="gap:8px;flex-wrap:wrap"><button class="btn" id="erp-priority-check">1. Проверить</button><button class="btn primary" id="erp-priority-apply" disabled>2. Перенести</button></div>
    <div id="erp-priority-out" aria-live="polite"></div>`;
  primary.after(section);
  const visitors = section.querySelector("#erp-priority-visitors"), leads = section.querySelector("#erp-priority-leads");
  const ids = section.querySelector("#erp-priority-ids"), check = section.querySelector("#erp-priority-check");
  const apply = section.querySelector("#erp-priority-apply"), out = section.querySelector("#erp-priority-out");
  let approved = null, uploaded = null;
  const reset = () => { approved = null; uploaded = null; apply.disabled = true; out.replaceChildren(); };
  visitors.onchange = leads.onchange = ids.oninput = reset;
  const base64 = (file) => new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result.split(",")[1]); reader.onerror = reject; reader.readAsDataURL(file); });
  async function upload(file) {
    const result = await api("POST", "/api/erp/upload", { filename: file.name, data: await base64(file) });
    await api("PUT", `/api/erp/uploads/${result.id}`, { kind: "clients" });
    return result.id;
  }
  function show(report, applied) {
    const n = report.counts || {}, filter = report.filter || {}, problems = report.issues || [], conflicts = report.profile?.conflicts || [];
    out.innerHTML = `<div class="note" style="margin-top:12px"><b>${applied ? "✅ Перенос выполнен" : "Результат проверки"}</b>
      <div>Найдено ID: <b>${(filter.found || []).length} из ${(filter.requested || []).length}</b></div>
      <div>Совпали с CRM: <b>${n.same || 0}</b> · обновятся: <b>${n.updated || 0}</b> · новые карточки: <b>${n.new || 0}</b></div>
      ${(filter.missing || []).length ? `<div class="danger-text">Не найдены в файлах: ${filter.missing.map(esc).join(", ")}</div>` : ""}
      ${problems.length ? `<div class="danger-text">Требуют сверки: ${problems.length}</div>` : ""}
      ${conflicts.length ? `<div>Заполненные значения CRM сохранятся; расхождений с ERP: ${conflicts.length}</div>` : ""}</div>`;
  }
  async function run(applyChanges) {
    const erp_ids = [...new Set(ids.value.split(/[^0-9]+/).filter(Boolean))];
    if (!visitors.files[0] || !leads.files[0]) { out.textContent = "Выберите оба файла: посетителей и лидов"; return; }
    if (!erp_ids.length) { out.textContent = "Вставьте хотя бы один ERP ID"; return; }
    check.disabled = apply.disabled = true;
    out.textContent = applyChanges ? "Переношу…" : "Загружаю файлы и проверяю…";
    try {
      if (!uploaded) uploaded = { visitors: await upload(visitors.files[0]), leads: await upload(leads.files[0]) };
      const body = { visitors: [uploaded.visitors], leads: [uploaded.leads], erp_ids };
      if (applyChanges) body.token = approved;
      const report = await api("POST", `/api/erp/people/${applyChanges ? "apply" : "plan"}`, body);
      show(report, applyChanges);
      if (!applyChanges && !(report.filter?.missing || []).length && !(report.issues || []).length) {
        approved = report.token; apply.disabled = false;
      }
      if (applyChanges) approved = null;
    } catch (error) { out.textContent = error.message; }
    finally { check.disabled = false; if (!approved) apply.disabled = true; }
  }
  check.onclick = () => run(false);
  apply.onclick = () => run(true);
}

function erpPriorityHistorySetup(box) {
  const after = box.querySelector("[data-erp-priority]") || box.querySelector("#erp-history-transfer");
  if (!after || box.querySelector("[data-erp-priority-history]")) return;
  const section = document.createElement("section");
  section.className = "erp-step"; section.dataset.erpPriorityHistory = "1";
  section.innerHTML = `<div class="erp-step-title"><div><h3>Ноябрьская история абонементов</h3>
    <p>Добавляет в хронологию даты продажи, группу, период и количество занятий. Оплаты, долг и баланс из ERP не читаются</p></div></div>
    <div class="field"><label for="erp-priority-subs">Проданные абонементы</label><input id="erp-priority-subs" type="file" accept=".xlsx,.csv"><div class="small muted">Выберите один файл, например «Проданные абонементы (5).xlsx»</div></div>
    <div class="field"><label for="erp-priority-history-ids">ERP ID нужных детей</label><textarea id="erp-priority-history-ids" rows="4" placeholder="По одному ID в строке"></textarea></div>
    <div class="row" style="gap:8px;flex-wrap:wrap"><button class="btn" id="erp-priority-history-check">1. Проверить историю</button><button class="btn primary" id="erp-priority-history-apply" disabled>2. Перенести историю</button></div>
    <div id="erp-priority-history-out" aria-live="polite"></div>`;
  after.after(section);
  const file = section.querySelector("#erp-priority-subs"), ids = section.querySelector("#erp-priority-history-ids");
  const check = section.querySelector("#erp-priority-history-check"), apply = section.querySelector("#erp-priority-history-apply"), out = section.querySelector("#erp-priority-history-out");
  let uploaded = null, approved = null;
  const reset = () => { uploaded = approved = null; apply.disabled = true; out.replaceChildren(); };
  file.onchange = ids.oninput = reset;
  const base64 = (chosen) => new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result.split(",")[1]); reader.onerror = reject; reader.readAsDataURL(chosen); });
  async function upload(chosen) {
    const result = await api("POST", "/api/erp/upload", { filename: chosen.name, data: await base64(chosen), kind: "subs" });
    await api("PUT", `/api/erp/uploads/${result.id}`, { kind: "subs" }); return result.id;
  }
  function show(report, applied) {
    out.innerHTML = `<div class="note" style="margin-top:12px"><b>${applied ? "✅ История перенесена" : "Результат проверки истории"}</b>
      <div>Найдено ID в отчёте: <b>${report.found.length} из ${report.requested.length}</b></div>
      <div>Новых записей хронологии: <b>${report.new}</b> · уже были: <b>${report.already}</b></div>
      ${report.missing.length ? `<div class="danger-text">Нет строк абонементов: ${report.missing.map(esc).join(", ")}. Это не отменяет более приоритетные сведения Telegram</div>` : ""}
      ${report.issues.length ? `<div class="danger-text">Требуют сверки: ${report.issues.length}</div>` : ""}
      ${applied ? `<div>Добавлено сейчас: <b>${report.applied}</b></div>` : ""}</div>`;
  }
  async function run(applying) {
    const erp_ids = [...new Set(ids.value.split(/[^0-9]+/).filter(Boolean))];
    if (!file.files[0]) { out.textContent = "Выберите файл проданных абонементов"; return; }
    if (!erp_ids.length) { out.textContent = "Вставьте ERP ID"; return; }
    check.disabled = apply.disabled = true; out.textContent = applying ? "Переношу историю…" : "Проверяю историю…";
    try {
      if (!uploaded) uploaded = await upload(file.files[0]);
      const body = { file_id: uploaded, erp_ids, since: "2026-11-01" }; if (applying) body.token = approved;
      const report = await api("POST", `/api/erp/priority-history/${applying ? "apply" : "plan"}`, body); show(report, applying);
      if (!applying && !report.issues.length) { approved = report.token; apply.disabled = false; }
      if (applying) approved = null;
    } catch (error) { out.textContent = error.message; }
    finally { check.disabled = false; if (!approved) apply.disabled = true; }
  }
  check.onclick = () => run(false); apply.onclick = () => run(true);
}

function erpPriorityAttendanceSetup(box) {
  const after = box.querySelector("[data-erp-priority-history]") || box.querySelector("[data-erp-priority]");
  if (!after || box.querySelector("[data-erp-priority-attendance]")) return;
  const section = document.createElement("section");
  section.className = "erp-step"; section.dataset.erpPriorityAttendance = "1";
  section.innerHTML = `<div class="erp-step-title"><span class="erp-step-number">4</span><div><h3>Проверить посещения и задачи</h3>
    <p>Показывает фактические отметки за 1–22 ноября и задачу на сверку недостающих занятий. Ничего не изменяет</p></div></div>
    <button class="btn primary" id="erp-priority-attendance-check">Проверить 9 карточек</button>
    <div id="erp-priority-attendance-out" aria-live="polite"></div>`;
  after.after(section);
  const button = section.querySelector("#erp-priority-attendance-check"), out = section.querySelector("#erp-priority-attendance-out");
  button.onclick = async () => {
    button.disabled = true; out.textContent = "Проверяю рабочие карточки…";
    try {
      const report = await api("GET", "/api/erp/priority-attendance/report"), s = report.summary;
      const sale = report.sharipov_december_sale || {};
      const miller = report.miller || {}, ms = miller.student || {};
      const kononenko = report.kononenko_december_sale || {};
      const fresh = report.fresh_telegram || {}, freshCounts = fresh.counts || {};
      const freshAttendance = fresh.trainer_attendance || {};
      const saleTask = sale.manual_task ? (sale.manual_task.status === "open" ? "открыта" : "закрыта") : "нет";
      out.innerHTML = `<div class="note" style="margin-top:12px"><b>Результат проверки</b>
        <div>Карточки: <b>${s.cards} из 9</b> · отметки: <b>${s.marks}</b> · открытые задачи на сверку: <b>${s.open_tasks}</b></div>
        ${s.missing_tasks ? `<div class="danger-text">Нет задачи на сверку: ${s.missing_tasks}</div>` : ""}
        <div style="margin-top:10px" class="${sale.complete ? "" : "danger-text"}"><b>Шарипов · декабрь:</b> ${sale.complete ? "✅ оформлен" : "⚠️ не подтверждён"} · абонементов: ${sale.subscriptions ?? 0} · занятий: ${sale.lessons ?? "—"} · сумма: ${sale.price == null ? "—" : `${sale.price.toLocaleString("ru-RU")} ₽`} · покрыто: ${(sale.covered || 0).toLocaleString("ru-RU")} ₽ · платежей PayKeeper 23.11: ${sale.payments?.length || 0} · ручная задача: ${saleTask}</div>
        <div style="margin-top:10px"><b>Миллер:</b> карточек: ${miller.card_matches ?? 0} · ERP: ${esc(ms.erp_id || "—")} · дата рождения: ${esc(ms.birth || "—")} · ноябрьских абонементов ERP: ${miller.history_subscriptions ?? 0} · отметок/визитов: ${miller.history_visits ?? 0} · событий Telegram: ${miller.telegram_events ?? 0} · декабрьских счетов: ${miller.december_invoices?.length || 0} · открытых задач: ${miller.open_tasks?.length || 0} · задача «Передать вымпел»: ${miller.pennant_task ? "открыта" : "не найдена"}${miller.open_tasks?.length ? `<div class="small muted">Задачи: ${miller.open_tasks.map(t => `${esc(t.title)} — ${esc(t.due_at.slice(0,10))}`).join("; ")}</div>` : ""}</div>
        <div style="margin-top:10px" class="${kononenko.complete ? "" : "danger-text"}"><b>Кононенко · декабрь:</b> ${kononenko.complete ? "✅ оформлен" : "⚠️ не подтверждён"} · абонементов: ${kononenko.subscriptions ?? 0} · занятий: ${kononenko.lessons ?? "—"} · сумма: ${kononenko.price == null ? "—" : `${kononenko.price.toLocaleString("ru-RU")} ₽`} · покрыто: ${(kononenko.covered || 0).toLocaleString("ru-RU")} ₽ · PayKeeper 22.11: ${kononenko.payments?.length || 0} · исключено 29.12: ${kononenko.excluded_dates?.includes("2026-12-29") ? "да" : "нет"}${kononenko.open_tasks?.length ? `<div class="small muted">Задачи: ${kononenko.open_tasks.map(t => `${esc(t.title)} — ${esc(t.due_at.slice(0,10))}`).join("; ")}</div>` : ""}</div>
        <div style="margin-top:10px" class="${fresh.state === "complete" ? "" : "danger-text"}"><b>Свежий Telegram 23–24.11:</b> внесено: ${freshCounts.applied || 0} · из них созданы по ERP: ${freshCounts.created_from_erp || 0} · уже было: ${freshCounts.already || 0} · не найдено: ${freshCounts.unmatched || 0} · неоднозначно: ${freshCounts.ambiguous || 0} · ошибки: ${freshCounts.errors || 0}${fresh.unmatched?.length ? `<div class="small">Не найдены: ${fresh.unmatched.map(x => esc(x.name)).join(", ")}</div>` : ""}${fresh.ambiguous?.length ? `<div class="small">Неоднозначно: ${fresh.ambiguous.map(x => esc(x.name)).join(", ")}</div>` : ""}<div class="small">Посещения из отчёта тренера 23.11: добавлено ${freshAttendance.added || 0}, уже было ${freshAttendance.already || 0}${freshAttendance.unresolved?.length ? `; уточнить: ${freshAttendance.unresolved.map(x => esc(x.name)).join(", ")}` : ""}</div></div>
        <div style="margin-top:8px">${report.clients.map(c => `<div style="margin-top:7px"><b>${esc(c.name)}</b> — отметок: ${c.marks}; ${c.task ? `задача: ${esc(c.task.status === "open" ? "открыта" : c.task.status)} (${esc(c.task.due_at.slice(0,10))})` : "задачи нет"}${c.mark_dates?.length ? `<div class="small muted">Даты: ${c.mark_dates.map(dShort).join(", ")}</div>` : ""}</div>`).join("")}</div></div>`;
    } catch (error) { out.textContent = error.message; }
    finally { button.disabled = false; }
  };
}

/* Настройки → Перенос из ERP: файлы, колонки, группы, пробный прогон, перенос, сверка, отмена */
async function renderErp(box) {
  const d = await api("GET", "/api/erp");
  const done = d.last && !d.last.undone_at;
  box.innerHTML = `<header class="erp-page-head"><span class="erp-eyebrow">Настройки</span><h2>Перенос данных из ERP</h2>
    <p>Переносите данные поэтапно: сначала проверьте результат, затем подтвердите запись. Финансы не меняются без отдельного явного действия</p></header>
    ${done ? `<div class="alert" style="background:var(--accent-soft,#e7f6ee);color:inherit">✅ Перенос сделан ${esc(d.last.started_at.replace("T", " "))}. Ниже сверка с файлами</div><div id="erp-verify" class="small muted">Сверяю…</div>
      <div class="erp-step" style="margin-top:14px"><b>🔄 Обновить из ERP (каждый день, пока клиентов ведут в ERP)</b>
        <div class="small muted">Выгрузите из ERP свежие файлы клиентов, абонементов и оплат и загрузите сюда, потом нажмите «Обновить». Новые клиенты добавятся, у старых обновятся этапы, пробные, группы и оплаты. Ничего не удаляется: отметки тренеров, отчёты и задачи в CRM остаются${d.last_update ? `. Последнее обновление: ${esc(d.last_update.at.replace("T", " "))}` : ""}</div>
        <div class="row" style="gap:8px;align-items:center;margin-top:8px"><input type="file" id="erp-file" multiple accept=".xlsx,.csv,.txt" style="flex:1"></div>
        <div id="erp-ups">${d.uploads.map((u) => erpUploadHtml(u, d.kinds)).join("")}</div>
        <button class="btn primary" id="erp-update" style="margin-top:8px" ${d.uploads.length ? "" : "disabled"}>Обновить</button><div id="erp-upd-out" class="small"></div></div>
      <div class="erp-step"><b>📋 Итоги дня для ERP</b>
        <div class="small muted">Что тренеры отметили в CRM: кто был, кто не пришёл и почему, итоги пробных, оплаты на месте. Перенесите в ERP вечером</div>
        <div class="row" style="gap:8px;align-items:center;margin-top:8px"><input type="date" id="erp-day-d" value="${S.meta.today}" style="flex:0 0 170px"><button class="btn" id="erp-day-go" style="flex:0">Показать</button></div>
        <div id="erp-day-out"></div></div>
      <div class="erp-step" id="erp-ri" hidden><b>🔄 Были на пробном давно и не купили</b>
        <div class="small muted">Пробное было больше недели назад, а клиент до сих пор на этапе «Как прошло пробное?». Их правильное место: «Приглашение на ПУ повторно». Дата приглашения берётся из их задачи, если она есть, иначе сегодня: админ перенесёт на нужную</div>
        <button class="btn" id="erp-ri-go" style="margin-top:8px"></button></div>
      <div class="erp-step"><b>🏷 Статусы клиентов в ERP</b>
        <div class="small muted">Какой статус был у клиентов в ERP при переносе и сколько клиентов в каждом. Статус ERP виден и в карточке клиента</div>
        <div id="erp-st-out" class="small muted" style="margin-top:6px">Загружаю…</div></div>
      <details style="margin-top:12px"><summary class="small muted">Отменить перенос</summary><button class="btn danger" id="erp-undo" style="margin-top:8px">Отменить перенос</button></details>` : `
    <div class="erp-step"><b>1. Файлы</b>
      <div class="row" style="gap:8px;align-items:center;margin-top:8px"><input type="file" id="erp-file" multiple accept=".xlsx,.csv,.txt" style="flex:1"></div>
      <div id="erp-ups">${d.uploads.map((u) => erpUploadHtml(u, d.kinds)).join("") || `<div class="small muted" style="margin-top:6px">Файлов пока нет</div>`}</div></div>
    <div class="erp-step"><b>2. Группы</b><div id="erp-groups" class="small muted">${d.uploads.length ? "Загружаю…" : "Появятся после загрузки файлов"}</div></div>
    <div class="erp-step"><b>3. Пробный прогон</b><div class="small muted">Ничего не записывает: показывает, что будет перенесено, и все нестыковки</div>
      <button class="btn" id="erp-plan" style="margin-top:8px" ${d.uploads.length ? "" : "disabled"}>Проверить</button><div id="erp-plan-out"></div></div>
    <div class="erp-step"><b>4. Перенос</b><div class="small muted">Перед переносом CRM сделает резервную копию и удалит всех выдуманных клиентов. Команда, группы, настройки и переписки останутся. Перенос можно отменить целиком</div>
      <label class="check" style="margin-top:8px"><input type="checkbox" id="erp-rkn"> Изменения в Роскомнадзор поданы, политика обработки данных опубликована</label>
      <button class="btn primary" id="erp-run" style="margin-top:8px" disabled>Перенести</button></div>`}`;
  // The clean-start archive is independent of whether an earlier ERP import
  // has completed, so the owner must always see it in this section.
  if (typeof erpClientResetSetup === "function") erpClientResetSetup(box);
  // Chat recovery is also independent. Mount it before the legacy import
  // widgets: an old empty-import edge case must never hide the recovery tool.
  if (typeof chatArchiveRestoreSetup === "function") chatArchiveRestoreSetup(box);
  if (typeof erpHistorySetup === "function") erpHistorySetup(box);
  try { erpLeadImportSetup(box, d); erpPeopleSetup(box, d); } catch (error) { fail(error); }
  try { erpPriorityPeopleSetup(box); } catch (error) { fail(error); }
  try { erpPriorityHistorySetup(box); } catch (error) { fail(error); }
  try { erpPriorityAttendanceSetup(box); } catch (error) { fail(error); }
  try { erpTaskTransferSetup(box,d); } catch (error) { fail(error); }
  try { erpFullSetup(box, d); } catch (error) { fail(error); }
  if (done) {
    erpOrganizeTools(box);
    erpWireFiles(box, d);
    api("GET", "/api/erp/reinvite-old").then((r) => {
      if (!r.n) return;
      $("#erp-ri").hidden = false;
      $("#erp-ri-go").textContent = `Перевести ${r.n} в «Приглашение на ПУ повторно»`;
      $("#erp-ri-go").onclick = async () => {
        if (!confirm(`Перевести ${r.n} клиентов в «Приглашение на ПУ повторно»?`)) return;
        try { const x = await api("POST", "/api/erp/reinvite-old", {}); toast(`Переведено: ${x.n}`); renderErp(box); } catch (e) { fail(e); }
      };
    }).catch(() => {});
    api("GET", "/api/erp/statuses").then((r) => {
      $("#erp-st-out").innerHTML = r.items.length ? `<table class="tbl"><thead><tr><th>Статус в ERP</th><th>Клиентов</th><th>Сейчас в CRM: ученики</th><th>лиды</th><th>ушли</th></tr></thead>
        <tbody>${r.items.map((x) => `<tr><td>${esc(x.status)}</td><td><b>${x.n}</b></td><td>${x.members || 0}</td><td>${x.leads || 0}</td><td>${x.left_ || 0}</td></tr>`).join("")}</tbody></table>`
        : "Статусов ERP у клиентов не найдено";
    }).catch((e) => { $("#erp-st-out").textContent = e.message; });
    $("#erp-update").onclick = async () => {
      const b = $("#erp-update"); b.disabled = true; b.textContent = "Обновляю…";
      try {
        const r = await api("POST", "/api/erp/update", {}), t = r.stats;
        toast("Обновлено из ERP");
        $("#erp-upd-out").innerHTML = `✅ Новых клиентов: ${t.new}, сменили этап: ${t.stage}, обновлено карточек: ${t.updated}, пробных: ${t.trials}, изменений в группах: ${t.groups}, абонементов: ${t.subs}, оплат: ${t.payments}`;
        setTimeout(() => renderErp(box), 4000);
      } catch (e) { fail(e); b.disabled = false; b.textContent = "Обновить"; }
    };
    $("#erp-day-go").onclick = async () => {
      try {
        const r = await api("GET", "/api/erp/day?date=" + $("#erp-day-d").value);
        const txt = [`Итоги дня ${dShort(r.date)} для ERP`, ...r.groups.flatMap((g) => [``, `${g.branch} · ${g.group}`, ...g.rows.map((x) => `- ${x.name}${x.erp_id ? ` (ID ${x.erp_id})` : ""}: ${x.mark}`)]),
          ...(r.pays.length ? [``, `Оплаты на месте:`, ...r.pays.map((p) => `- ${p.branch}: ${p.name}, ${p.amount} ₽, ${p.what || ""}`)] : [])].join("\n");
        const task = `Перенеси в ERP Футболики (вкладка, где вы уже вошли) отметки посещений за ${dShort(r.date)} из списка ниже. Для каждой группы открой журнал за эту дату и проставь: «был», «не был» (с причиной, если указана), «отработка», итог пробного. Ищи ребёнка по ID ERP, если он указан, иначе по ФИО. Ничего другого в ERP не меняй и не удаляй. Если ребёнка или группы нет, пропусти и запиши в отчёт. В конце дай короткий отчёт: сколько отметок внесено и что не получилось (без телефонов).\n\n${txt}`;
        $("#erp-day-out").innerHTML = r.groups.length ? `<pre class="erp-day">${esc(txt)}</pre>
          <div class="row" style="gap:8px;flex-wrap:wrap"><button class="btn sm" id="erp-day-copy">Копировать итоги</button><button class="btn sm primary" id="erp-day-prompt">Копировать задание для агента в браузере</button></div>
          <div class="small muted" style="margin-top:6px">Задание вставьте агенту в облачном браузере, где вы вошли в ERP. Это передача данных клиентов внешнему сервису: учтите в уведомлении в Роскомнадзор</div>`
          : `<div class="small muted" style="margin-top:6px">За этот день отметок нет</div>`;
        const cp = (t) => copyCrmText(t).then(() => toast("Скопировано"), () => window.prompt("Скопируйте", t));
        if ($("#erp-day-copy")) { $("#erp-day-copy").onclick = () => cp(txt); $("#erp-day-prompt").onclick = () => cp(task); }
      } catch (e) { fail(e); }
    };
    $("#erp-undo").onclick = async () => {
      if (!confirm("Отменить перенос? Все перенесённые клиенты, абонементы и оплаты будут удалены. Выдуманные клиенты не вернутся")) return;
      try { await api("POST", "/api/erp/undo", {}); toast("Перенос отменён"); renderErp(box); } catch (e) { fail(e); }
    };
    const v = await api("GET", "/api/erp/verify").catch(() => null);
    if (!v) { $("#erp-verify").textContent = "Сверка не загрузилась"; return; }
    $("#erp-verify").innerHTML = `<table class="tbl" style="margin-top:6px"><thead><tr><th></th><th>В файлах ERP</th><th>В CRM</th><th></th></tr></thead><tbody>
      ${v.rows.map((r) => `<tr><td>${esc(r.label)}</td><td>${r.erp.toLocaleString("ru-RU")}</td><td>${r.crm.toLocaleString("ru-RU")}</td><td>${r.ok ? "✅" : "⚠️"}</td></tr>`).join("")}
      ${v.by_month.map((r) => `<tr><td class="muted">Оплаты за ${esc(monthLabel(r.month))}</td><td>${rub(r.erp)}</td><td>${rub(r.crm)}</td><td>${r.ok ? "✅" : "⚠️"}</td></tr>`).join("")}</tbody></table>
      <div style="margin-top:10px"><b>Занимаются по группам</b>${v.groups.map((g) => `<div>${esc(g.branch)} · ${esc(g.name)}: ${g.kids}</div>`).join("")}</div>`;
    return;
  }
  erpOrganizeTools(box);
  erpWireFiles(box, d);
  if (d.uploads.length) {
    const g = await api("GET", "/api/erp/groups");
    $("#erp-groups").innerHTML = g.erp.length ? `<div class="small muted" style="margin:4px 0 6px">Какой нашей группе соответствует группа из ERP. CRM подобрала сама, проверьте</div>
      ${g.erp.map((x) => `<div class="row erp-grow" style="align-items:center;gap:8px"><span style="flex:1">${esc(x.erp)} <span class="muted">· ${x.count}</span></span>
        <select data-gmap="${esc(x.erp)}" style="flex:1"><option value="">— не переносить в группу —</option>${g.ours.map((o) => `<option value="${o.id}" ${o.id === x.group_id ? "selected" : ""}>${esc(o.branch)} · ${esc(o.name)}</option>`).join("")}</select></div>`).join("")}
      <button class="btn sm" id="erp-gsave" style="margin-top:8px">Сохранить группы</button>` : "В файлах нет колонки с группой";
    if ($("#erp-gsave")) $("#erp-gsave").onclick = async () => {
      const map = {}; box.querySelectorAll("[data-gmap]").forEach((x) => (map[x.dataset.gmap] = x.value));
      try { await api("PUT", "/api/erp/groups", { map }); toast("Группы сохранены"); } catch (e) { fail(e); }
    };
  }
  if ($("#erp-plan")) $("#erp-plan").onclick = async () => {
    if ($("#erp-gsave")) $("#erp-gsave").click();
    const p = await api("GET", "/api/erp/plan").catch(fail); if (!p) return;
    const st = p.stats;
    $("#erp-plan-out").innerHTML = `<div class="erp-stats">
      <div><b>${st.families}</b> семей</div><div><b>${st.children}</b> детей и лидов</div><div><b>${st.members_active}</b> занимаются</div>
      <div><b>${st.members_left}</b> ушли</div><div><b>${st.leads}</b> лидов</div><div><b>${st.subs}</b> абонементов</div>
      <div><b>${st.payments}</b> оплат на ${rub(st.payments_sum)}</div><div><b>${st.visits}</b> посещений</div><div><b>${st.tasks}</b> задач</div></div>
      <div class="small" style="margin-top:6px">Статус и группа ребёнка - по проданным абонементам: занимается, если абонемент есть на текущий или будущий месяц
        (из «занимаются» не оплатили текущий месяц, но платили в прошлом: <b>${st.members_unpaid || 0}</b>), ушёл - если абонементы были раньше.
        Ходят в несколько групп по абонементам этого месяца: <b>${st.multi_group || 0}</b>.
        Лидов без имени ребёнка (сохранятся с пометкой): <b>${st.no_name || 0}</b>. Вводные и отработки не считаются абонементами: <b>${(st.subs_trials || 0) + (st.subs_makeups || 0)}</b>.
        Оплат с датой продажи вместо пустой даты оплаты: <b>${st.pay_date_from_sale || 0}</b></div>
      <div class="small" style="margin-top:6px">Будет удалено клиентов, которые сейчас в CRM (выдуманные): <b>${p.will_remove}</b></div>
      ${p.problems.length ? `<div class="small" style="margin-top:8px"><b>Нестыковки по видам:</b> ${Object.entries(p.problem_types || {}).map(([k, v]) => `${esc(k)}: <b>${v}</b>`).join(" · ")}</div>
        <details class="fold" style="margin-top:6px"><summary><b>Нестыковки: ${p.problems_total}</b>${p.problems_total > p.problems.length ? ` (показаны первые ${p.problems.length})` : ""}</summary><ul class="small">${p.problems.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></details>` : `<div class="small" style="margin-top:6px">✅ Нестыковок нет</div>`}`;
    $("#erp-run").disabled = !$("#erp-rkn").checked;
    S.erpPlanned = true;
  };
  if ($("#erp-rkn")) $("#erp-rkn").onchange = (e) => { $("#erp-run").disabled = !(e.target.checked && S.erpPlanned); };
  if ($("#erp-run")) $("#erp-run").onclick = async () => {
    if (!confirm("Перенести клиентов из ERP? Выдуманные клиенты будут удалены, перед этим CRM сделает резервную копию")) return;
    $("#erp-run").disabled = true; $("#erp-run").textContent = "Переношу…";
    try { const r = await api("POST", "/api/erp/run", { rkn: true }); toast(`Перенесено: ${r.stats.children} детей, ${r.stats.payments} оплат`); S.erpPlanned = false; renderErp(box); }
    catch (e) { fail(e); $("#erp-run").disabled = false; $("#erp-run").textContent = "Перенести"; }
  };
}

function erpOrganizeTools(box) {
  const head = box.querySelector(".erp-page-head");
  if (box.querySelector(".erp-transfer-workspace")) return;

  const workspace = document.createElement("div");
  workspace.className = "erp-transfer-workspace";
  const stages = [
    { key: "cards", label: "Карточки", description: "Карточки посетителей и лидов уже загружали. Здесь можно проверить оставшиеся строки и дополнить карточки.", selectors: ["[data-erp-lead-import]", "#erp-people"] },
    { key: "subscriptions", label: "Абонементы", description: "Выберите выгрузку проданных абонементов и проверьте, какие записи появятся в истории клиентов.", selectors: ["[data-erp-priority-history]"] },
    { key: "tasks", label: "Задачи", description: "Загрузите выгрузку задач и проверьте клиента, срок и состояние перед переносом.", selectors: ["#erp-task-transfer"] },
  ];
  workspace.innerHTML = `
    <nav class="erp-transfer-nav" aria-label="Этапы переноса из ERP">
      ${stages.map(({ key, label }) => `<button type="button" class="btn" data-erp-stage="${key}" aria-controls="erp-stage-${key}">${label}</button>`).join("")}
    </nav>
    ${stages.map(({ key, label, description }) => `<section class="erp-transfer-stage" id="erp-stage-${key}" aria-label="${label}" hidden>
      <p class="muted">${description}</p>
      <div data-erp-stage-tools></div>
    </section>`).join("")}
    <details class="erp-advanced"><summary><span><b>Дополнительные инструменты</b><small>История одного ребёнка, загруженные файлы и прежние операции</small></span><span aria-hidden="true">⌄</span></summary>
      <div data-erp-advanced-body></div>
    </details>`;
  head.after(workspace);
  for (const { key, selectors } of stages) {
    const tools = workspace.querySelector(`#erp-stage-${key} [data-erp-stage-tools]`);
    for (const selector of selectors) {
      const tool = box.querySelector(selector);
      if (tool) tools.append(tool);
    }
  }
  const advanced = workspace.querySelector("[data-erp-advanced-body]");
  for (const node of [...box.children]) {
    if (node !== head && node !== workspace) advanced.append(node);
  }
  const tabs = [...workspace.querySelectorAll("[data-erp-stage]")];
  function selectStage(key) {
    S.erpTransferStage = key;
    for (const tab of tabs) {
      const active = tab.dataset.erpStage === key;
      tab.setAttribute("aria-pressed", String(active));
      tab.classList.toggle("primary", active);
      workspace.querySelector(`#erp-stage-${tab.dataset.erpStage}`).hidden = !active;
    }
  }
  for (const tab of tabs) tab.onclick = () => selectStage(tab.dataset.erpStage);
  selectStage(S.erpTransferStage || "subscriptions");
}

function erpUploadHtml(u, kinds) {
  return `<div class="erp-up" data-up="${u.id}"><div class="row" style="align-items:center;gap:8px"><b style="flex:1">${esc(u.filename)} <span class="muted small">· ${u.count} строк</span></b>
      <select data-ukind="${u.id}" style="flex:0 0 auto;width:auto">${Object.entries(kinds).map(([k, t]) => `<option value="${k}" ${k === u.kind ? "selected" : ""}>${esc(t)}</option>`).join("")}</select>
      <button class="icon-btn" data-udel="${u.id}" title="Убрать файл">✕</button></div>
    <details class="fold"><summary class="small">Колонки: угадано ${Object.keys(u.mapping).length} из ${u.fields.length}. Проверить</summary>
      <table class="tbl erp-map"><thead><tr><th>Что это</th><th>Колонка в файле</th><th>Пример из первой строки</th></tr></thead><tbody>
      ${u.fields.map((f) => `<tr><td>${esc(f.label)}</td><td><select data-umap data-field="${f.key}"><option value="">— нет —</option>${u.headers.map((h, i) => `<option value="${i}" ${u.mapping[f.key] === i ? "selected" : ""}>${esc(h)}</option>`).join("")}</select></td>
        <td class="small muted" data-ex="${f.key}">${u.mapping[f.key] !== undefined ? esc((u.preview[0] || [])[u.mapping[f.key]] || "") : ""}</td></tr>`).join("")}</tbody></table></details></div>`;
}

/* Настройки → Автоцепочки: серии сообщений родителям, по умолчанию выключены */
async function renderChainsSettings(box) {
  const d = await api("GET", "/api/chains");
  box.innerHTML = `<h3>Автоцепочки сообщений</h3>
    <div class="small muted" style="margin-bottom:10px">CRM сама пишет родителю серию сообщений в мессенджер, с 10:00 до 20:00. Любой ответ родителя останавливает цепочку и ставит задачу ответить. Цепочка заканчивается сама, когда цель достигнута. В текстах можно писать {child} (имя ребёнка), {group} (группа), {month} (месяц)</div>
    ${Object.entries(d.chains).map(([k, c]) => { const st = d.stats[k]; return `<div class="section chain-box" data-chain="${k}" style="box-shadow:none;border:1px solid var(--line)">
      <div class="row" style="align-items:center;gap:10px"><b class="grow">${esc(c.title)}</b>
        <label class="check" style="flex:0 0 auto"><input type="checkbox" data-cen ${c.enabled ? "checked" : ""}> Включена</label></div>
      <div class="small muted">Когда: ${esc(c.when)}. Остановится сама: ${esc(c.goal)}</div>
      ${st.total ? `<div class="small" style="margin-top:4px">Сейчас идёт: ${st.active} · ответили родители: ${st.replied} · дошли до цели: ${st.goal}</div>` : ""}
      ${c.steps.map((x, i) => `<div class="chain-step"><div class="field" style="flex:0 0 120px"><label>Сообщение ${i + 1}: через, дней</label><input type="number" min="0" max="60" data-cdays value="${x.days}"></div>
        <div class="field" style="flex:1 1 auto"><label>Текст</label><textarea rows="3" data-ctext>${esc(x.text)}</textarea></div></div>`).join("")}
      </div>`; }).join("")}
    <button class="btn primary" id="chains-save">Сохранить</button>`;
  $("#chains-save").onclick = async () => {
    const chains = {};
    box.querySelectorAll("[data-chain]").forEach((el) => {
      chains[el.dataset.chain] = { enabled: el.querySelector("[data-cen]").checked,
        steps: [...el.querySelectorAll(".chain-step")].map((r) => ({ days: +r.querySelector("[data-cdays]").value, text: r.querySelector("[data-ctext]").value })) };
    });
    try { await api("PUT", "/api/chains", { chains }); toast("Сохранено"); renderChainsSettings(box); } catch (e) { fail(e); }
  };
}

/* Мои списки: сохранённые фильтры «Учеников», у каждого участника команды свои */
async function membersViews(page, f, rerender) {
  const box = $("#m-views"); if (!box) return;
  const items = (await api("GET", "/api/views?section=members").catch(() => ({ items: [] }))).items;
  const keys = ["q", "branch", "group", "status", "grouped"];
  const same = (v) => keys.every((k) => (v.data[k] || "") === (f[k] || ""));
  const hasFilter = keys.some((k) => f[k]);
  box.innerHTML = items.length || hasFilter ? `<span class="small muted">⭐ Мои списки:</span>
    ${items.map((v) => `<span class="chip ${same(v) ? "on" : ""}" data-view-id="${v.id}">${esc(v.name)}<button class="chip-x" data-view-del="${v.id}" title="Удалить список">×</button></span>`).join("")}
    ${hasFilter && !items.some(same) ? `<button class="chip" id="view-save">+ Сохранить этот список</button>` : ""}` : "";
  box.querySelectorAll("[data-view-id]").forEach((c) => (c.onclick = (e) => {
    if (e.target.closest("[data-view-del]")) return;
    const v = items.find((x) => x.id === +c.dataset.viewId);
    keys.forEach((k) => { f[k] = v.data[k] || ""; }); f.grouped = !!v.data.grouped;
    rerender();
  }));
  box.querySelectorAll("[data-view-del]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Удалить этот список? Сами ученики никуда не денутся")) return;
    await api("DELETE", `/api/views/${b.dataset.viewDel}`).catch(fail); membersViews(page, f, rerender);
  }));
  if ($("#view-save")) $("#view-save").onclick = async () => {
    const name = prompt("Как назвать список? Например: «Небо, не оплатили»");
    if (!name) return;
    try { await api("POST", "/api/views", { section: "members", name, data: Object.fromEntries(keys.map((k) => [k, f[k] || ""])) }); toast("Список сохранён"); membersViews(page, f, rerender); }
    catch (e) { fail(e); }
  };
}

/* Дубли семей: одна семья заведена дважды. Склеить одной кнопкой */
async function membersDups(btn) {
  const d = await api("GET", "/api/duplicates").catch(() => ({ items: [] }));
  if (!d.items.length) return;
  btn.hidden = false; btn.textContent = `👯 Дубли (${d.items.length})`;
  btn.onclick = () => openDups(d.items);
}
function openDups(items) {
  const fam = (x) => `<div class="dup-fam"><b>${esc(x.name || "Семья")}</b>
    <div class="small">${x.kids.map((k) => esc(displayChildName(k.name)) + (k.birth ? ` (${dShort(k.birth)})` : "") + (k.funnel === "sales" ? " · лид" : "")).join(", ") || "детей нет"}</div>
    <div class="small muted">${x.parents.map((p) => esc(p.name) + (p.phone ? " " + esc(fmtPhone(p.phone)) : "")).join(", ")}</div>
    <div class="small muted">оплат: ${x.payments}</div></div>`;
  openDrawer(`<h2>👯 Похоже на одну семью</h2>
    <p class="muted">Одна и та же семья заведена дважды. Выберите, какую карточку оставить: всё из второй (дети, родители, переписка, звонки, оплаты) переедет в неё. Отменить склейку нельзя</p>
    ${items.map((x, i) => `<div class="section dup-pair"><div class="small muted" style="margin-bottom:8px">Почему: ${esc(x.why)}</div>
      <div class="dup-grid">${fam(x.a)}${fam(x.b)}</div>
      <div class="row" style="gap:8px;flex-wrap:wrap;margin-top:10px">
        <button class="btn sm primary" data-merge="${i}" data-keep="a">Оставить левую</button>
        <button class="btn sm primary" data-merge="${i}" data-keep="b">Оставить правую</button>
        <button class="btn sm" data-dskip="${i}">Это разные семьи</button></div></div>`).join("")}`);
  const body = $("#drawer-body");
  body.querySelectorAll("[data-merge]").forEach((b) => (b.onclick = async () => {
    const x = items[+b.dataset.merge], keep = b.dataset.keep === "a" ? x.a : x.b, drop = b.dataset.keep === "a" ? x.b : x.a;
    if (!confirm(`Склеить? Останется семья «${keep.name}», всё из «${drop.name}» переедет в неё`)) return;
    try { await api("POST", "/api/families/merge", { keep: keep.id, drop: drop.id }); toast("Склеено"); closeDrawer(); render(); } catch (e) { fail(e); }
  }));
  body.querySelectorAll("[data-dskip]").forEach((b) => (b.onclick = async () => {
    const x = items[+b.dataset.dskip];
    await api("POST", "/api/duplicates/skip", { a: x.a.id, b: x.b.id }).catch(fail);
    b.closest(".dup-pair").remove(); toast("Больше не предложу");
  }));
}

/* Архив учеников и лидов: кнопка над доской «Ученики», список раскрывается под ней */
async function membersArchive(page) {
  const btn = $("#arch-btn"), box = $("#arch-box");
  let list = await api("GET", "/api/archive/students").catch(() => []);
  const label = () => { btn.textContent = `🗄 Архив (${list.length}) ${box.hidden ? "+" : "−"}`; };
  const draw = (q = "") => {
    const n = q.toLowerCase().replace(/\D/g, "") ? q.replace(/\D/g, "") : "";
    const rows = list.filter((x) => !q || searchTextMatches(x.name + " " + x.family, q) || (n && (x.phone || "").replace(/\D/g, "").includes(n)));
    $("#arch-rows").innerHTML = rows.length ? rows.map((x) => `<div class="arch-row"><button class="linkish grow" data-aopen="${x.id}"><b>${esc(displayChildName(x.name))}</b> <span class="muted small">${esc(x.family)}${x.funnel === "sales" ? " · лид" : ""}</span></button>
      <button class="btn sm" data-aback="${x.id}">Вернуть</button></div>`).join("") : `<div class="small muted">${list.length ? "Никого не нашли" : "В архиве пусто"}</div>`;
    box.querySelectorAll("[data-aopen]").forEach((b) => (b.onclick = () => openStudent(+b.dataset.aopen)));
    box.querySelectorAll("[data-aback]").forEach((b) => (b.onclick = async () => {
      try { await api("POST", `/api/students/${b.dataset.aback}/archive`, { archived: false }); toast("Вернули из архива"); render(); } catch (e) { fail(e); }
    }));
  };
  box.innerHTML = `<input id="arch-q" placeholder="Поиск по архиву: имя или телефон"><div id="arch-rows" style="margin-top:8px"></div>`;
  $("#arch-q").oninput = (e) => draw(e.target.value.trim());
  btn.onclick = () => { box.hidden = !box.hidden; label(); if (!box.hidden) $("#arch-q").focus(); };
  label(); draw();
}

/* ---------- рабочий стол: что горит + воронка ---------- */
const URG_ACTION = { missed_call: "Перезвонить", site_lead: "Открыть", message: "Ответить", trial: "Открыть", open_q: "Открыть диалог" };
const ago = (iso) => {
  if (!iso) return "";
  const m = Math.max(0, Math.round((Date.now() - new Date(iso.length <= 16 ? iso + ":00" : iso).getTime()) / 60000));
  return m < 1 ? "только что" : m < 60 ? `${m} мин назад` : m < 1440 ? `${Math.floor(m / 60)} ч назад` : dtShort(iso);
};

/* «Смотреть как»: админ выбирает участника команды и видит рабочий стол его глазами */
S.viewAs = (() => { try { return JSON.parse(localStorage.getItem("crm.viewAs") || "null"); } catch (_) { return null; } })();
async function viewAsBar() {
  if (S.user.role !== "admin") return "";
  if (!S.staff) S.staff = (await api("GET", "/api/users")).filter((u) => u.active);
  if(S.viewAs){const fresh=S.staff.find(u=>u.id===S.viewAs.id);S.viewAs=fresh?{id:fresh.id,name:fresh.name,role:fresh.role,is_senior:!!fresh.is_senior,is_offline_admin:!!fresh.is_offline_admin,is_promoter:!!fresh.is_promoter,is_journal:!!fresh.is_journal}:null;try{localStorage.setItem("crm.viewAs",JSON.stringify(S.viewAs))}catch(_){}}
  const cur = S.viewAs ? S.viewAs.id : S.user.id;
  const opt = (u) => `<option value="${u.id}" ${u.id === cur ? "selected" : ""}>${esc(u.name)}${u.id === S.user.id ? " (я)" : ""}</option>`;
  const grp = (role, title) => { const l = S.staff.filter((u) => u.role === role); return l.length ? `<optgroup label="${title}">${l.map(opt).join("")}</optgroup>` : ""; };
  return `<div class="viewas ${S.viewAs && S.viewAs.id !== S.user.id ? "on" : ""}"><span>👀 Как видит стол</span>
    <select id="view-as">${grp("admin", "Администраторы")}${grp("operator", "Операторы")}${grp("coach", "Тренеры")}</select>
    ${S.viewAs && S.viewAs.id !== S.user.id ? `<button class="btn sm" id="view-me">Вернуться к своему</button>` : ""}</div>`;
}
function wireViewAs(page) {
  const sel = page.querySelector("#view-as");
  if (!sel) return;
  sel.onchange = () => {
    const u = S.staff.find((x) => x.id === +sel.value);
    S.viewAs = u && u.id !== S.user.id ? { id: u.id, name: u.name, role: u.role, is_senior:!!u.is_senior, is_offline_admin:!!u.is_offline_admin, is_promoter:!!u.is_promoter, is_journal:!!u.is_journal } : null;
    try { localStorage.setItem("crm.viewAs", JSON.stringify(S.viewAs)); } catch (_) {}
    render();
  };
  const me = page.querySelector("#view-me");
  if (me) me.onclick = () => { sel.value = S.user.id; sel.onchange(); };
}

function todayTrialsPlaque(items) {
  const monthPart=(q)=>`<span><b>${esc(monthLabel(q.month))}</b><em>${q.lessons} ${plural(q.lessons,"занятие","занятия","занятий")}</em><strong>${rub(q.total)}</strong></span>`;
  const trialCard = child => {
    const groupParts = String(child.group || "").split("·").map(part => part.trim()).filter(Boolean);
    const location = [...new Set([child.branch, ...groupParts, child.age].filter(Boolean))].join(" · ");
    return `<article class="trial-desk-child">
      <div class="trial-desk-row">
        <time class="trial-desk-time">${esc(child.time || "—")}</time>
        <div class="trial-desk-info">
          <div class="trial-desk-name"><b>${esc(child.name)}</b><span class="pill">${child.came ? "ПУ отмечено" : "Ждём"}</span></div>
          ${location ? `<small>${esc(location)}</small>` : ""}
          <div class="trial-desk-contact">Родитель: <b>${esc(child.parent || "не указан")}</b>${child.phone ? ` · <a href="tel:${esc(child.phone)}">${esc(fmtPhone(child.phone))}</a>` : ""}</div>
        </div>
        <button type="button" class="btn sm" data-trial-card="${child.id}">Карточка</button>
      </div>
      ${child.offers.length ? `<details class="trial-desk-pricing"><summary>Расчёт абонемента</summary>
        <div class="trial-offers">${child.offers.map(offer => `<article><h3>${esc(offer.label)}</h3><div class="trial-offer-months">${monthPart(offer.current)}${monthPart(offer.next)}<span class="trial-offer-total"><b>Вместе</b><em>два месяца</em><strong>${rub(offer.bundle_total)}</strong></span></div>${offer.current.discount ? `<p>Скидка: ${esc(offer.current.discount)} · ${offer.current.discount_percent}%</p>` : ""}</article>`).join("")}</div>
        </details>` : `<p class="trial-desk-note">Расчёт недоступен. Проверьте группу в карточке</p>`}
    </article>`;
  };
  return `<section class="trial-desk" aria-label="Сегодня ждём на пробное">
    <div class="trial-desk-head"><h2>Сегодня ждём на пробное</h2><b class="trial-desk-count">${items.length}</b></div>
    ${items.length ? `<div class="trial-desk-list">${items.map(trialCard).join("")}</div>` : `<p class="trial-desk-empty">Сегодня записанных на пробное нет</p>`}
  </section>`;
}

function filterWorkdeskBoard(board, mode, selectedDay, today) {
  if (!board) return board;
  return { ...board, columns: board.columns.map(column => ({ ...column, cards: column.cards.filter(card => {
    const cardDate = String(card.due_at ?? (card.due || card.stage_changed_at || card.created_at || "")).slice(0, 10);
    if (mode === "overdue") return !!cardDate && cardDate < today;
    return cardDate === (mode === "date" ? selectedDay : today);
  }) })) };
}

async function renderWorkdesk(page) {
  if (S.user.role !== "admin") S.viewAs = null;
  const bar = await viewAsBar().catch(() => "");
  if (S.viewAs && S.viewAs.role === "coach") { await renderCoachToday(page, S.viewAs.id, bar); return wireViewAs(page); }
  const taskDeskUser = S.viewAs || S.user;
  const taskLayout = taskDeskUser.role === "operator" && !taskDeskUser.is_senior && !taskDeskUser.is_offline_admin ? "funnel" : "grid";
  S.workdeskDate = S.workdeskDate || S.meta.today;
  S.workdeskMode = S.workdeskMode || (S.workdeskDate === S.meta.today ? "today" : "date");
  const workdeskParams = new URLSearchParams();
  if (S.viewAs) workdeskParams.set("as_user", S.viewAs.id);
  if (S.workdeskMode === "date") workdeskParams.set("date", S.workdeskDate);
  const workdeskUrl = "/api/workdesk" + (workdeskParams.size ? "?" + workdeskParams.toString() : "");
  const workdeskData = api("GET", workdeskUrl).catch(async (error) => {
    // Во время автоматической публикации служба перезапускается на несколько секунд.
    // GET безопасно повторить: не оставляем вместо рабочего стола постоянную «Ошибка сервера».
    if (error.status && error.status < 500) throw error;
    await new Promise((resolve) => setTimeout(resolve, 1200));
    return api("GET", workdeskUrl);
  });
  S.etDay = S.workdeskMode === "overdue" ? "overdue" : S.workdeskMode === "date" ? S.workdeskDate : "today";
  const tasksHtmlRequest = tasksSection().catch((error) => `<section class="now red-box workdesk-part-error">
    <div class="now-head"><h3>Задачи временно не загрузились</h3><button type="button" class="btn sm" data-reload-workdesk>Повторить</button></div>
    <div class="small">Остальные разделы рабочего стола доступны. ${esc(error.message || "Ошибка сервера")}</div></section>`);
  const [d, tasksHtml, schedHtml, members] = await Promise.all([workdeskData, tasksHtmlRequest, scheduleWidget().catch(() => ""),
    can("members", "full") ? api("GET", "/api/board/members").catch(() => null) : Promise.resolve(null)]);
  // воронка «Продления» (Юлия, 04.11): разовые, не оплачен, продлить (с 20 числа), счёт выставлен, ушёл. Оплатившие не попадают
  const since30 = addDays(S.meta.today, -30);
  const renew = members ? { ...members, draggable: false, note: "", columns: members.columns.filter((c) => c.board !== false)
    .map((c) => c.key === "left" ? { ...c, title: "Ушёл (за 30 дней)", cards: c.cards.filter((k) => (k.stage_changed_at || "") >= since30) } : c) } : null;
  const wasOpen = page.querySelector(".sched-widget") ? page.querySelector(".sched-widget").open : null;
  const u = d.urgent, reds = u.filter((x) => x.level === "red").length;
  const scroll = page.querySelector(".board") ? page.querySelector(".board").scrollLeft : 0;
  // кто смотрит стол: руководитель, контролёр (проверяет приход тренеров) или администратор
  const deskUser = S.viewAs || S.user;
  const who = deskUser.role === "admin" ? "owner" : deskUser.is_offline_admin ? "offline" : deskUser.is_senior || d.checkins ? "control" : "operator";
  const W = {
    call: { html: callFocusBox(d.call_focus), icon: "📞", label: "Звонки" },
    spend: { html: spendDeskBox(d), icon: "💳", label: "Платежи", n: (d.bills_due || []).filter((x) => x.level).length, red: (d.bills_due || []).some((x) => x.level === "red") },
    checkins: { html: checkinReport(d.checkins), icon: "🧑‍🏫", label: "Приход тренеров", n: d.checkins ? d.checkins.rows.filter((x) => x.state === "late" || x.state === "absent").length : 0, red: true },
    merch: { html: merchAlerts(d.merch_alerts), icon: "👕", label: "Мерч", n: d.merch_alerts ? d.merch_alerts.qr_no_receipt.length + d.merch_alerts.missing_reports.length + d.merch_alerts.cash_not_handed.length + ((d.merch_alerts.stock?.weekly_due) ? 1 : 0) + (S.user.role === "admin" ? (d.merch_alerts.stock?.shortages?.length || 0) : 0) : 0 },
    marks: { html: marksBox(d), icon: "📋", label: "Отметки в журнале", n: (d.missing_marks || []).length, red: true },
    subs: { html: needsSubsBox(d), icon: "🔁", label: "Замены", n: (d.needs_subs || []).length, red: true },
    wait: { html: waitlistBox(d.waitlist), icon: "⏳", label: "Лист ожидания" },
    sched: { html: schedHtml, icon: "📅", label: "Сегодня в филиалах" },
    bday: { html: birthdaysBox(d.birthdays), icon: "🎂", label: "Дни рождения" },
    // Юлия, 04.11: задачи по клиентам у администраторов общие, они в «📋 Задачи»; «Мои задачи» на столе не нужны
    tasks: { html: tasksHtml, icon: "✅", label: "Прочие задачи" },
    answers: { html: coachAnswersBox(d.coach_answers), icon: "💬", label: "Ответы тренеров", n: (d.coach_answers || []).length },
    reports: { html: shiftReportsBox(d.shift_reports), icon: "📝", label: "Отчёты о смене",
      n: d.shift_reports ? d.shift_reports.late.length + d.shift_reports.issues.length : 0, red: true },
  };
  const ORDER = {
    owner: ["spend", "reports", "answers", "marks", "subs", "checkins", "tasks", "call", "wait", "merch", "sched", "bday"],
    control: ["reports", "answers", "marks", "checkins", "subs", "tasks", "wait", "sched", "bday", "call"],
    operator: ["tasks", "answers", "call", "wait", "sched", "bday", "marks", "subs", "checkins", "reports"],
    offline: ["tasks", "sched", "bday", "call"],
  }[who];
  const widgets = ORDER.filter((k) => W[k].html);
  // на виду только то, где есть что делать, и задачи; остальное свёрнуто строкой внизу (раскрывается по нажатию)
  S.dwOpen = S.dwOpen || {};
  const dwShown = (k) => k === "tasks" || !!W[k].n || (k === "call" && who === "operator");
  const tiles = widgets.filter((k) => W[k].n).map((k) => `<button class="dtile ${W[k].red ? "red" : "yellow"}" data-jump="dw-${k}"><span>${W[k].icon}</span><b>${W[k].n}</b><small>${W[k].label}</small></button>`);
  const hr = new Date().getHours();
  const hello = hr < 5 ? "Доброй ночи" : hr < 12 ? "Доброе утро" : hr < 18 ? "Добрый день" : "Добрый вечер";
  const firstName = (S.user.name || "").split(" ")[0];
  const dateTxt = new Date().toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" }).replace(/^./, (c) => c.toUpperCase());
  const freshLeads = (d.new_cards_today?.cards || []).filter((x) => x.needs_processing);
  const visibleFreshLeads=who==='offline'?[]:freshLeads;
  const newCardsBoard={funnel:'new',draggable:false,columns:[
    {key:'new_application',title:'Новая заявка',cards:visibleFreshLeads.map(card=>({...card,color:'red',badge:'Новая заявка',group:card.group||`Источник: ${card.source||'не указан'}`}))}
  ]};
  const freshLeadIds=new Set(freshLeads.map(card=>Number(card.id)));
  const leadCards=(d.board?.columns||[]).find(column=>column.key==='lead')?.cards||[];
  const noDateLeads=leadCards.filter(card=>!card.due && card.task_title==="Новый лид. Даты задачи нет, надо создать");
  const noDateIds=new Set(noDateLeads.map(card=>Number(card.id)));
  const otherLeadCards=leadCards.filter(card=>!freshLeadIds.has(Number(card.id)) && !noDateIds.has(Number(card.id)));
  const leadsBoard={funnel:'sales',draggable:false,columns:[{key:'lead',title:'Новые лиды',cards:otherLeadCards}]};
  const salesWithoutLeads=d.board?{...d.board,columns:d.board.columns.filter(column=>column.key!=='lead')}:d.board;
  let newCardsForDate=filterWorkdeskBoard(newCardsBoard,S.workdeskMode,S.workdeskDate,S.meta.today),leadsForDate=filterWorkdeskBoard(leadsBoard,S.workdeskMode,S.workdeskDate,S.meta.today),salesForDate=filterWorkdeskBoard(salesWithoutLeads,S.workdeskMode,S.workdeskDate,S.meta.today),renewForDate=filterWorkdeskBoard(renew,S.workdeskMode,S.workdeskDate,S.meta.today),paymentsForDate=filterWorkdeskBoard(d.manual_payment_checks,S.workdeskMode,S.workdeskDate,S.meta.today);
  const newCardsPanel=`<div id="dw-new-cards">${boardHtml(newCardsForDate)}</div>`;
  const taskIdsInBoard=(board,cardsAreTasks=false)=>new Set((board?.columns||[]).flatMap(column=>column.cards||[]).map(card=>Number(cardsAreTasks?card.id:card.task_id)).filter(Boolean));
  const paymentTaskIds=taskIdsInBoard(paymentsForDate,true);
  const withoutTaskIds=(board,ids)=>board?{...board,columns:board.columns.map(column=>({...column,cards:column.cards.filter(card=>!ids.has(Number(card.task_id)))}))}:board;
  // Одна и та же задача может быть показана только в одной воронке. Оплата Точки
  // приоритетнее новой заявки, продления и продажи; затем идёт новая заявка,
  // продление, продажа и только после них «Прочие задачи».
  const newTaskIds=taskIdsInBoard(newCardsForDate);
  renewForDate=withoutTaskIds(renewForDate,new Set([...paymentTaskIds,...newTaskIds]));
  const renewTaskIds=taskIdsInBoard(renewForDate);
  salesForDate=withoutTaskIds(salesForDate,new Set([...paymentTaskIds,...newTaskIds,...renewTaskIds]));
  leadsForDate=withoutTaskIds(leadsForDate,new Set([...paymentTaskIds,...newTaskIds,...renewTaskIds,...taskIdsInBoard(salesForDate)]));
  const specializedTaskIds=new Set([...noDateLeads.map(card=>Number(card.task_id)),...taskIdsInBoard(newCardsForDate),...taskIdsInBoard(leadsForDate),...taskIdsInBoard(salesForDate),...taskIdsInBoard(renewForDate),...paymentTaskIds]);
  const erpSection=(can("sales") || S.viewAs || S.user.is_offline_admin)
    ? await erpTasksSection(S.viewAs,taskLayout,[...specializedTaskIds]) : {html:"",count:0};
  const schoolRules = filterWorkdeskBoard(d.school_rules_tasks, S.workdeskMode, S.workdeskDate, S.meta.today);
  const rulesTasks = schoolRules?.columns[0]?.cards || [];
  const taskCount = mode => {
    if (!erpSection.taskCounts) return "";
    // Задачи оплаты Точки исключены из общей выдачи задач.
    const payments = filterWorkdeskBoard(d.manual_payment_checks, mode, null, S.meta.today);
    const rules = filterWorkdeskBoard(d.school_rules_tasks, mode, null, S.meta.today);
    const count = erpSection.taskCounts[mode] + (rules?.columns[0]?.cards.length || 0) + (payments?.columns || [])
      .reduce((sum, column) => sum + column.cards.length, 0);
    return ` <b class="pipeline-task-count">${count}</b>`;
  };
  const erpHtml=erpSection.html;
  const pipelineItems = [
    { group:"Лиды", key:"new", icon:"✨", label:"Свежие новые карточки", count:(newCardsForDate?.columns||[]).reduce((sum,column)=>sum+column.cards.length,0), html:newCardsPanel },
    { group:"Лиды", key:"leads", icon:"👤", label:"Остальные новые лиды", count:(leadsForDate?.columns||[]).reduce((sum,column)=>sum+column.cards.length,0), html:`<div id="dw-leads">${boardHtml(leadsForDate)}</div>` },
    { group:"Клиенты", key:"sales", icon:"🛒", label:"Продажи", count:(salesForDate?.columns||[]).reduce((sum,column)=>sum+column.cards.length,0), html:`<div id="dw-sales">${boardHtml(salesForDate)}</div>` },
    { group:"Клиенты", key:"school-rules", icon:"📋", label:"Отправить правила школы", count:rulesTasks.length,
      html:`<div class="et-task-grid" id="dw-school-rules">${rulesTasks.map(task=>`<article class="et-card">
        <button type="button" class="linkish" data-rules-student="${task.student_id}">${esc(displayChildName(task.student?.name || ""))}</button>
        <div class="small muted">${esc(task.note)} · ${dShort(task.due_at)}</div>
        <button type="button" class="btn sm primary" data-rules-done="${task.id}">Правила отправлены</button>
      </article>`).join("") || '<p class="small muted">Нет задач на выбранную дату</p>'}</div>` },
    ...(renewForDate ? [{ group:"Клиенты", key:"renew", icon:"🔁", label:"Продления", count:renewForDate.columns.reduce((sum,column)=>sum+column.cards.length,0), html:`<div id="dw-renew">${boardHtml(renewForDate)}</div>` }] : []),
    ...((paymentsForDate?.columns?.[0]?.cards||[]).length ? [{ group:"Контроль", key:"manual-payments", icon:"🧾", label:"Оплаты Точки", count:paymentsForDate.columns[0].cards.length, danger:true, html:`<div id="dw-manual-payment-checks"><p class="pipeline-note">Нужно подтвердить поступление на расчётный счёт и чек в ОФД</p>${boardHtml(paymentsForDate)}</div>` }] : []),
    ...(erpHtml ? [{ group:"Контроль", key:"tasks", icon:"✅", label:"Прочие задачи", count:erpSection.count, html:`<div id="dw-erptasks-hub">${erpHtml}</div>` }] : []),
    { key:"erp-visitor-pending", icon:"📥", label:"Карточки из ERP", count:(d.erp_visitor_pending || []).length,
      html:boardHtml({funnel:"erp-visitor-pending",draggable:false,columns:[{key:"pending",title:"Ждём задачи и абонементы",cards:d.erp_visitor_pending || []}]}) },
    { key:"leads-no-date", icon:"📅", label:"Новые лиды без даты", count:noDateLeads.length, html:boardHtml({funnel:"sales",draggable:false,columns:[{key:"lead",title:"Назначить дату задачи",cards:noDateLeads}]}) }
  ];
  if (!pipelineItems.some(item=>item.key===S.deskPipeline)) S.deskPipeline = (newCardsForDate?.columns||[]).some(column=>column.cards.length) ? "new" : "sales";
  const pipelineHub = `<section class="pipeline-hub" aria-label="Работа с клиентами">
    <div class="pipeline-hub-head"><div><h2>Работа с клиентами</h2><p>Все воронки в одном месте</p></div><div class="pipeline-date-filter" aria-label="Фильтр по дате"><button type="button" class="btn sm ${S.workdeskMode==='today'?'primary':''}" data-pipeline-today>Сегодня${taskCount("today")}</button><button type="button" class="btn sm ${S.workdeskMode==='overdue'?'danger':''}" data-pipeline-overdue>Просрок${taskCount("overdue")}</button><label><span>Дата</span><input type="date" value="${esc(S.workdeskDate)}" data-pipeline-date></label><span class="pipeline-hub-total">${pipelineItems.reduce((sum,item)=>sum+(item.count||0),0)} карточек</span></div></div>
    <nav class="pipeline-tabs" aria-label="Выберите воронку">${pipelineItems.map((item,index)=>`${item.group && (!index||pipelineItems[index-1].group!==item.group)?`<span class="pipeline-tab-group">${esc(item.group)}</span>`:''}<button type="button" class="pipeline-tab ${item.key===S.deskPipeline?'active':''} ${item.danger?'danger':''}" data-pipeline-tab="${item.key}" aria-selected="${item.key===S.deskPipeline}"><span>${item.icon}</span><b>${esc(item.label)}</b>${item.count!==null?`<em>${item.count}</em>`:''}</button>`).join('')}</nav>
    <div class="pipeline-panels">${pipelineItems.map(item=>`<section class="pipeline-panel" data-pipeline-panel="${item.key}" ${item.key===S.deskPipeline?'':'hidden'}><div class="pipeline-panel-title"><span>${item.icon}</span><h3>${esc(item.label)}</h3>${item.count!==null?`<span class="count">${item.count}</span>`:''}</div>${item.html}</section>`).join('')}</div>
  </section>`;
  const hot = `<section class="now ${u.length ? "" : "calm"}" id="dw-hot">
      <div class="now-head"><h3>${u.length ? (reds ? `🔥 Горит прямо сейчас: ${reds}` : "Ждут ответа") : "Всё обработано"}</h3>
        ${u.length ? `<span class="muted small">Красное сначала, потом сообщения и пробные</span>` : ""}</div>
      ${u.length ? `<div class="now-list">${u.slice(0, S.hotAll ? u.length : 3).map((x, i) => `
        <div class="now-item ${x.level}">
          <span class="ref-card ${x.level}" aria-hidden="true"></span>
          <div class="grow"><b>${esc(x.kind === "missed_call" ? fmtPhone(x.title) : x.title)}</b>${x.count ? ` <span class="nbadge static">${x.count}</span>` : ""}
            <div class="small ellipsis">${esc(x.text)}</div></div>
          <span class="small muted when">${ago(x.ts)}</span>
          <div class="now-act">
            <button class="btn sm ${x.level === "red" ? "primary" : ""}" data-u="${i}">${URG_ACTION[x.kind]}</button>
            ${x.kind === "missed_call" ? `<button class="btn sm" data-useen="${x.id}" title="Уже связались другим способом">Обработан</button>` : ""}
          </div>
        </div>`).join("")}</div>${u.length > 3 ? `<button class="btn sm ghost now-more" id="hot-more">${S.hotAll ? "Свернуть" : `Ещё ${u.length - 3}`}</button>` : ""}` : `<div class="muted small">Пропущенных звонков, новых заявок и непрочитанных сообщений нет. Можно выдохнуть ⚽</div>`}
    </section>`;
  // Временно оставляем на администраторском рабочем столе только единый центр
  // клиентских воронок. Остальные готовые виджеты не удаляем: они сохранены в
  // коде и очереди разработки до отдельного решения по составу нового стола.
  page.innerHTML = bar + todayTrialsPlaque(d.today_trials || []) + pipelineHub;
  page.querySelectorAll("[data-trial-card]").forEach(button=>button.onclick=event=>{event.preventDefault();event.stopPropagation();openStudent(+button.dataset.trialCard)});
  page.querySelectorAll("[data-rvclose]").forEach((b) => (b.onclick = async () => { try { await api("POST", `/api/reviews/${b.dataset.rvclose}/close`, {}); render(); } catch (e) { fail(e); } }));
  const dateInput=page.querySelector("[data-pipeline-date]");
  if(dateInput)dateInput.onchange=()=>{if(!dateInput.value)return;S.workdeskDate=dateInput.value;S.workdeskMode=dateInput.value===S.meta.today?'today':'date';S.etDay=S.workdeskMode==='today'?'today':dateInput.value;render()};
  const todayButton=page.querySelector("[data-pipeline-today]");
  if(todayButton)todayButton.onclick=()=>{S.workdeskDate=S.meta.today;S.workdeskMode='today';S.etDay='today';render()};
  const overdueButton=page.querySelector("[data-pipeline-overdue]");
  if(overdueButton)overdueButton.onclick=()=>{S.workdeskMode='overdue';S.etDay='overdue';render()};
  page.querySelectorAll("[data-pipeline-tab]").forEach(button=>button.onclick=()=>{
    S.deskPipeline=button.dataset.pipelineTab;
    page.querySelectorAll("[data-pipeline-tab]").forEach(tab=>{const active=tab===button;tab.classList.toggle("active",active);tab.setAttribute("aria-selected",active?"true":"false")});
    page.querySelectorAll("[data-pipeline-panel]").forEach(panel=>panel.hidden=panel.dataset.pipelinePanel!==S.deskPipeline);
    const board=page.querySelector(`[data-pipeline-panel="${S.deskPipeline}"] .board`);if(board)requestAnimationFrame(()=>board.scrollLeft=0);
  });
  wireRefundRequests(page,render);
  const hm = $("#hot-more"); if (hm) hm.onclick = () => { S.hotAll = !S.hotAll; render(); };
  page.querySelectorAll(".dwf").forEach((x) => x.addEventListener("toggle", () => { S.dwOpen[x.dataset.dwk] = x.open; }));
  page.querySelectorAll("[data-jump]").forEach((b) => (b.onclick = () => { const el = document.getElementById(b.dataset.jump); if (el) el.scrollIntoView({ behavior: "smooth", block: "start" }); }));
  if (page.querySelector(".board")) page.querySelector(".board").scrollLeft = scroll;
  const erpVisitorBoard=page.querySelector('[data-pipeline-panel="erp-visitor-pending"]');
  if(erpVisitorBoard)wireBoard(erpVisitorBoard,"erp-visitor-pending",{draggable:false});
  const newCardsBoardElement=page.querySelector("#dw-new-cards");if(newCardsBoardElement)wireBoard(newCardsBoardElement,"new",newCardsForDate);
  const leadsBoardElement=page.querySelector("#dw-leads");if(leadsBoardElement)wireBoard(leadsBoardElement,"sales",leadsForDate);
  const salesBoard=page.querySelector("#dw-sales");if(salesBoard)wireBoard(salesBoard,"sales",salesForDate);
  const renewBoard=page.querySelector("#dw-renew");if(renewForDate&&renewBoard)wireBoard(renewBoard,"members",renewForDate);
  const manualPaymentBoard=page.querySelector("#dw-manual-payment-checks");if(manualPaymentBoard)wireBoard(manualPaymentBoard,"manual_payment_checks",d.manual_payment_checks);
  page.querySelectorAll("[data-rules-student]").forEach(button => button.onclick = () => openStudent(+button.dataset.rulesStudent));
  page.querySelectorAll("[data-rules-done]").forEach(button => button.onclick = async () => {
    button.disabled = true;
    try {
      await api("POST", `/api/tasks/${button.dataset.rulesDone}/done`, {result:"Правила отправлены"});
      toast("Задача выполнена");
      await render();
    } catch (error) { button.disabled = false; fail(error); }
  });
  wireErpTasks(page);
  wireTasks(page);
  wireViewAs(page);
  page.querySelectorAll("[data-reload-workdesk]").forEach((button) => (button.onclick = render));
  wireNeedsSubs(page, d);
  wireWaitlist(page);
  wireMarks(page);
  wireCheckinReport(page);
  wireShiftReports(page, d);
  wireCoachAnswers(page);
  wireEnvelope(page);
  wireSpend(page);
  wireMerchAlerts(page);
  if (page.querySelector(".sched-widget")) { if (wasOpen === false) page.querySelector(".sched-widget").open = false; wireSchedule(page.querySelector(".sched-widget"), S.meta.today); }
  page.querySelectorAll(".bday-box [data-open]").forEach((b) => (b.onclick = () => openStudent(+b.dataset.open)));
  page.querySelectorAll("[data-u]").forEach((b) => (b.onclick = async () => {
    const x = u[+b.dataset.u];
    if (x.kind === "missed_call") { await dial(x.phone); if (x.student_id) openStudent(x.student_id); else openNewClient({ phone: x.phone }); }
    else if (x.kind === "message" || x.kind === "open_q") openChat(x.conversation_id, true);
    else if (x.student_id) openStudent(x.student_id);
  }));
  page.querySelectorAll("[data-useen]").forEach((b) => (b.onclick = async () => {
    await api("POST", `/api/calls/${b.dataset.useen}/seen`, {}).catch(fail); pollCounts(); render();
  }));
}

function cardHtml(k, draggable) {
  const student=k.student||k, due=k.due||k.due_at||"", taskTitle=k.task_title||k.title||k.due_title||"";
  const studentId=student.id||k.student_id||k.id;
  return `<article class="card ${k.color || ""}" data-id="${studentId}" data-detail-key="${esc(k._board_detail_key||"")}" ${draggable ? 'draggable="true"' : ""}>
    ${k.color ? `<span class="ref-card ${k.color}" title="${k.color === "red" ? "Срочно" : "Пора связаться"}"></span>` : ""}
    <div class="name">${esc(displayChildName(student.name||""))}</div>
    <div class="meta card-contact">${esc([student.parent,fmtPhone(student.phone)].filter(Boolean).join(", "))}</div>
    ${taskTitle ? `<div class="card-task">${esc(taskTitle)}</div>` : ""}
    ${due ? `<div class="due ${k.due_color || ""}" title="${esc(k.due_title || "")}">⏰ ${dueText(due)}</div>` : ""}
  </article>`;
}
function dueText(iso) {
  const t = S.meta.today;
  const d = iso.slice(0, 10), hm = iso.slice(11, 16);
  if (d < t) return `просрочено с ${dShort(d)} ${hm}`;
  if (d === t) return (iso.slice(0, 16) < new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16) ? "просрочено, " : "сегодня до ") + hm;
  return `${dShort(d)} ${hm}`;
}

function enableDrag(page, funnel, columns) {
  const auto = new Set(columns.filter((c) => c.auto).map((c) => c.key));
  if (funnel === "skips") auto.add("booked");
  let dragId = null;
  page.querySelectorAll(".card[draggable]").forEach((el) => {
    el.addEventListener("dragstart", (e) => { dragId = el.dataset.id; el.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; });
    el.addEventListener("dragend", () => el.classList.remove("dragging"));
  });
  page.querySelectorAll(".col").forEach((col) => {
    const stage = col.dataset.stage;
    if (auto.has(stage)) return;
    col.addEventListener("dragover", (e) => { e.preventDefault(); col.classList.add("drop"); });
    col.addEventListener("dragleave", () => col.classList.remove("drop"));
    col.addEventListener("drop", async (e) => {
      e.preventDefault(); col.classList.remove("drop");
      if (!dragId) return;
      try {
        if (funnel === "sales" && stage === "refused") return askRefusal(dragId, render);
        if (funnel === "sales" && stage === "reinvite_pu") return askReinvite(dragId, render);
        await api("POST", `/api/students/${dragId}/move`, { funnel, stage }); render();
      } catch (err) { fail(err); }
    });
  });
}

/* ---------- карточка ---------- */
/* Почему не купили после пробного: причина обязательна. «Думают» и «заболели/уезжают» — не отказ, а задача */
function askRefusal(sid, done) {
  const rs = S.meta.trial_refusals || [];
  openDrawer(`<h2>Почему не купили?</h2>
    <form class="section" id="rf">
      <div class="chips" style="flex-direction:column;align-items:stretch">${rs.map((r, i) => `<label class="check"><input type="radio" name="reason" value="${esc(r)}" ${i ? "" : "checked"}> ${esc(r)}</label>`).join("")}</div>
      <div class="small muted" style="margin:8px 0">«Думают» - CRM поставит задачу перезвонить через 2 дня. «Заболели или уезжают» - карточка уйдёт в «Пригласим позже» с задачей пригласить снова. Остальное - отказ</div>
      <div class="field"><label>Комментарий</label><input name="comment" placeholder="Что сказал родитель"></div>
      <button class="btn primary">Сохранить</button>
    </form>`);
  $("#rf").onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    try {
      const r = await api("POST", `/api/students/${sid}/move`, { funnel: "sales", stage: "refused", reason: f.reason, comment: f.comment });
      toast(r.stage === "how_was" ? "Задача: перезвонить " + dShort(r.due.slice(0, 10)) : r.stage === "later" ? "Пригласим снова " + dShort(r.due.slice(0, 10)) : "Отказ сохранён");
      closeDrawer(); done();
    } catch (err) { fail(err); }
  };
}

/* «Приглашение на ПУ повторно» (Юлия, 04.11): был на пробном, не сложилось. Админ выбирает, когда пригласить снова */
function askReinvite(sid, done) {
  const plus = (n) => addDays(S.meta.today, n);
  const y = +S.meta.today.slice(0, 4), aug = `${y}-08-01`, jan = `${y + 1}-01-05`, janNow = `${y}-01-05`;
  const intake = [janNow, aug, jan, `${y + 1}-08-01`].find((d) => d > S.meta.today);
  openDrawer(`<h2>🔄 Пригласить на пробное повторно</h2>
    <div class="small muted">Ребёнок уже был на пробном, но не сложилось. Когда пригласить снова? В этот день в 11:00 у админов появится задача</div>
    <form class="section" id="ri">
      <div class="chips" style="margin-bottom:10px">${[[7, "Через неделю"], [14, "Через 2 недели"], [30, "Через месяц"]].map(([n, t]) => `<button type="button" class="chip" data-rid="${plus(n)}">${t}</button>`).join("")}
        <button type="button" class="chip" data-rid="${intake}">Ближайший набор (${dShort(intake)})</button></div>
      <div class="field"><label>Дата</label><input type="date" name="due" min="${S.meta.today}" value="${plus(14)}" required></div>
      <div class="field"><label>Комментарий</label><input name="comment" placeholder="Почему не сложилось, что предложить"></div>
      <button class="btn primary">Сохранить</button>
    </form>`);
  $("#ri").querySelectorAll("[data-rid]").forEach((b) => (b.onclick = () => { $("#ri").elements.due.value = b.dataset.rid; }));
  $("#ri").onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    try {
      await api("POST", `/api/students/${sid}/move`, { funnel: "sales", stage: "reinvite_pu", due: f.due + "T11:00", comment: f.comment });
      toast("Пригласим снова " + dShort(f.due)); closeDrawer(); done();
    } catch (err) { fail(err); }
  };
}

/* Ушёл: причина обязательна. Что дальше, зависит от причины (Юлия, 03.11) */
function askLeftReason(sid, done) {
  const y = +S.meta.today.slice(0, 4);
  const intake = [`${y}-01-05`, `${y}-08-01`, `${y + 1}-01-05`, `${y + 1}-08-01`].find((d) => d > S.meta.today);
  const rs = [["Дорого", "В воронку «Ждут акцию»: позвоним, когда будет акция"], ["Неудобное расписание", "Пригласить вернуться через 2 недели"],
    ["Далеко ездить", `Пригласить в ближайший набор, ${dShort(intake)}`], ["Ребёнку не нравится", `Пригласить в ближайший набор, ${dShort(intake)}`],
    ["Перешли в другую секцию", `Пригласить в ближайший набор, ${dShort(intake)}`], ["Переезд", "Карточка уйдёт в архив, звонить не будем"],
    ["Болезнь или травма", "Уточните, когда пригласить, и выберите дату"], ["Сезон, каникулы", "Уточните, когда пригласить, и выберите дату"],
    ["Другое", "Пригласить вернуться через 2 месяца"]];
  openDrawer(`<h2>Почему ушёл?</h2>
    <form class="section" id="lf">
      <div class="chips" style="flex-direction:column;align-items:stretch">${rs.map(([r, h], i) => `<label class="check"><input type="radio" name="reason" value="${esc(r)}" ${i ? "" : "checked"}> ${esc(r)}<span class="small muted" style="margin-left:6px">${esc(h)}</span></label>`).join("")}</div>
      <div class="field" id="lf-due" hidden style="margin-top:8px"><label>Когда пригласить вернуться</label><input type="date" name="due" min="${S.meta.today}"></div>
      <div class="field"><label>Комментарий</label><input name="comment" placeholder="Что сказал родитель"></div>
      <button class="btn primary">Сохранить</button>
    </form>`);
  const admin = ["Болезнь или травма", "Сезон, каникулы"];
  const sync = () => { const r = $("#lf").reason.value, on = admin.includes(r); $("#lf-due").hidden = !on; $("#lf").due.required = on; };
  $("#lf").querySelectorAll("[name=reason]").forEach((x) => (x.onchange = sync)); sync();
  $("#lf").onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    try {
      const r = await api("POST", `/api/students/${sid}/move`, { funnel: "members", stage: "left", reason: f.reason, comment: f.comment, due: f.due ? f.due + "T11:00" : "" });
      toast(r && r.plan === "promo" ? "Ушёл. В воронке «Ждут акцию»" : r && r.plan === "archive" ? "Ушёл. Карточка в архиве" : r && r.due ? `Ушёл. Пригласить вернуться ${dShort(r.due)}` : "Сохранено: ушёл");
      closeDrawer(); done();
    } catch (err) { fail(err); }
  };
}

function openDrawer(html, size, options = {}) {
  if (!options.keepStudentRequest) S.studentCardRequest=(S.studentCardRequest||0)+1;
  $("#drawer-body").innerHTML = html; $("#drawer").hidden = false; document.body.style.overflow = "hidden";
  const panel = $("#drawer .drawer-panel"); if (panel) panel.classList.toggle("wide", size === "wide");
  const state = { ...(history.state || {}), drawer: true }; delete state.studentCard;
  try {
    if (!drawerInHistory) { history.pushState(state, "", studentCardHash()); drawerInHistory = true; }
    else history.replaceState(state, "", studentCardHash());
  } catch (_) {}
}
function hideDrawer() { S.studentCardRequest=(S.studentCardRequest||0)+1; const d = $("#drawer"); if (d) d.hidden = true; document.body.style.overflow = ""; }
/* закрыть карточку: крестиком, Esc, после сохранения или кнопкой/свайпом «назад» на телефоне */
function closeDrawer() {
  forgetStudentCard();
  hideDrawer();
  if (drawerInHistory) { drawerInHistory = false; skipPop = true; history.back(); }
}
document.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) { closeDrawer(); } });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
/* глазик в поле пароля: показать или скрыть, что вводишь */
document.addEventListener("click", (e) => {
  const b = e.target.closest(".pw-eye");
  if (!b) return;
  const i = b.previousElementSibling, show = i.type === "password";
  i.type = show ? "text" : "password";
  b.textContent = show ? "🙈" : "👁";
  b.setAttribute("aria-label", show ? "Скрыть пароль" : "Показать пароль");
  i.focus();
});

/* Карточка клиента: общение, абонементы и данные на отдельных вкладках. */
function layoutStudentCard(body, isMember) {
  const grid = body.querySelector(".sc-grid"), left = grid.querySelector(".sc-left"), right = grid.querySelector(".sc-right"), info = grid.querySelector(".sc-info");
  const take = (k) => body.querySelector(`[data-sc="${k}"]`);
  const fold = (k, pane, open = false) => {
    const sec = take(k); if (!sec) return;
    const det = document.createElement("details"); det.className = "sc-fold"; det.dataset.fold = k; det.open = open;
    det.innerHTML = `<summary>${sec.dataset.title || (k === "stage" ? "Этап и источник" : k === "balance" ? "Баланс и оплаты" : k === "subs" ? "Абонементы" : k)}</summary>`;
    det.appendChild(sec); pane.appendChild(det);
  };
  if (!isMember && take("pu")) left.appendChild(take("pu"));
  left.appendChild(body.querySelector("#comms-box"));
  ["subs", "balance"].forEach(k => { if (take(k)) right.appendChild(take(k)); });
  ["vac"].forEach(k => fold(k, right));
  if(take("visits")) grid.querySelector(".sc-attendance").appendChild(take("visits"));
  const stage=take("stage"),stagePlace=take("family")?.querySelector('[data-client-stage]');
  if(stage&&stagePlace){while(stage.firstChild)stagePlace.appendChild(stage.firstChild);stage.remove();}
  ["family", "hist"].forEach(k => fold(k, info, k === "family"));
  if (take("arch")) info.appendChild(take("arch"));
  body.querySelectorAll("[data-sctab]").forEach((b) => (b.onclick = () => {
    const card = history.state?.studentCard;
    if (card && card.userId === S.user.id) recordCrmActivity("card", {studentId: card.id, tab: b.dataset.sctab});
    grid.dataset.tab = b.dataset.sctab;
    if (history.state?.studentCard) {
      saveStudentCardRoute({ ...history.state.studentCard, tab: b.dataset.sctab });
    }
    body.querySelectorAll("[data-sctab]").forEach((x) => { x.classList.toggle("on", x === b); x.setAttribute("aria-selected", String(x === b)); });
  }));
}
const scTab = (tab) => { const b = document.querySelector(`#drawer-body [data-sctab="${tab}"]`); if (b) b.click(); };
const openFold = (body, k) => {
  if(["visits","skips"].includes(k)){scTab("visits");body.querySelector('[data-sc="visits"]')?.scrollIntoView({block:"start",behavior:"smooth"});return}
  scTab(["family","stage","hist"].includes(k)?"info":"abon");const f=body.querySelector(`[data-fold="${k}"]`);if(f){f.open=true;f.scrollIntoView({block:"start",behavior:"smooth"})}
};

function quoteHtml(q) {
  return `<div class="total">${rub(q.total)}</div>
    <div class="small">${q.lessons_paid} занятий × ${rub(q.per_lesson)} = ${rub(q.base)}${q.days_label ? ` · только ${esc(q.days_label)}` : ""}${q.vacation_lessons ? ` · отпуск: −${q.vacation_lessons} зан.` : ""}</div>
    ${q.credit_lessons ? `<div class="small">Перерасчёт пропусков (${q.credit_absence_dates.map(dShort).join(", ")}): −${rub(q.credit)}</div>` : ""}
    ${q.late_fee ? `<div class="small">Оплата после ${dShort(q.pricing.deadline)}: +${String(q.pricing.late_pct).replace(".", ",")}% (+${rub(q.late_fee)}), скидки не действуют</div>` : ""}
    ${q.pricing && q.pricing.newcomer ? `<div class="small">Новичок: только оставшиеся занятия этого месяца, без надбавки</div>` : ""}
    ${!q.individual_reason && q.pricing && !q.pricing.late && q.pricing.late_pct && q.pricing.late_total > q.total ? `<div class="small muted">При оплате после ${dShort(q.pricing.deadline)}: ${rub(q.pricing.late_total)} без скидок</div>` : ""}
    ${q.discount ? `<div class="small">Скидка «${esc(q.discount.name)}» ${q.discount.percent}%: −${rub(q.discount.amount)}${q.discount.auto ? ". Действует при оплате сегодня" : ""}</div>` : ""}
    ${q.waitlist ? `<div class="alert" style="margin-top:6px">Оплата после срока: место не гарантировано, в листе ожидания ${q.waitlist}</div>` : ""}`;
}

/* «Продать абонемент»: месяц, группа, тариф, скидка → цена по всем правилам → ссылка на оплату, оплата сейчас или только счёт */
/* перед продажей и ссылкой на оплату: все данные клиента и почта для чека */
async function ensureRequired(sid, kind) {
  const info = await api("GET", `/api/students/${sid}/required?for=${kind}`).catch(() => null);
  return !info || !info.missing.some(k=>!info.optional?.includes(k)) || await askRequired(info);
}
function saleCalendarHtml(q, ym, trialDates = []) {
  const trials = new Set(trialDates.filter(d=>/^\d{4}-\d{2}-\d{2}$/.test(d)&&d.startsWith(ym+"-")));
  const dates = new Set(q.calendar_dates || q.dates), selected = new Set(q.dates), excluded = new Set(q.excluded_dates || []);
  const [year, month] = ym.split('-').map(Number), offset = (new Date(year, month - 1, 1).getDay() + 6) % 7;
  const cells = Array(offset).fill('<span></span>');
  for (let day = 1; day <= new Date(year, month, 0).getDate(); day++) {
    const iso = `${ym}-${String(day).padStart(2,'0')}`, trial = trials.has(iso), badge = trial ? '<span class="sale-trial-marker">Пробное</span>' : '', checked = q.single ? selected.has(iso) : !excluded.has(iso);
    cells.push(dates.has(iso) ? `<label class="sale-cal-day ${trial?'has-trial':''} ${checked ? 'selected' : q.single ? 'available' : 'excluded'}"><span>${day}</span><input type="${q.single ? 'radio' : 'checkbox'}" name="sale-lesson" data-sale-date="${iso}" ${checked ? 'checked' : ''} ${q.read_only ? 'disabled' : ''} aria-label="Занятие ${dShort(iso)}"><small>${checked ? 'занятие' : q.single ? 'выбрать' : 'исключено'}</small>${badge}</label>` : `<span class="sale-cal-day off ${trial?'has-trial':''}">${day}${badge}</span>`);
  }
  return `<div class="sale-calendar" id="sl-calendar"><b>${esc(monthLabel(ym))}</b>${trials.size?`<div class="sale-trial-note">Пробное: ${[...trials].sort().map(dShort).join(", ")}</div>`:''}<div class="small muted">${q.read_only ? 'Отмечены занятия оформленного абонемента' : q.single ? 'Выберите одно занятие.' : 'Отмеченные дни входят в абонемент. Снимайте отметки только для согласованных исключений.'}</div><div class="sale-cal-grid">${WD.map(d=>`<b class="sale-cal-week">${d}</b>`).join('')}${cells.join('')}</div><div class="small">Выбрано занятий: <b>${q.dates.length}</b>${excluded.size ? ` · исключено: ${excluded.size}` : ''}</div></div>`;
}
function existingSaleCalendarHtml(sub, d) {
  const saved = JSON.parse(sub.lesson_schedule || '[]');
  const components = saved.length ? saved : (JSON.parse(sub.program_selection || '{}').contract?.components || []);
  const selected = [...new Set(components.flatMap(c=>c.dates||[]))].sort();
  if(sub.kind==='single'&&sub.single_date)selected.push(sub.single_date);
  const excluded = JSON.parse(sub.excluded_dates || '[]');
  if(!selected.length)return '<p class="small muted">Даты занятий этого абонемента не сохранены — требуется сверка</p>';
  return saleCalendarHtml({dates:selected,calendar_dates:[...new Set([...selected,...excluded])],excluded_dates:excluded,single:sub.kind==='single',read_only:true},clientSubscriptionMonth(sub),[...(d.trial_visits||[]).map(v=>v.date),(d.student.trial_at||'').slice(0,10)]);
}
const clientGroupLabel = e => e.lesson_kind === 'arena' || /сборная.*арктика/i.test(e.group_name || e.name || '') ? 'Сборная Арктика' : e.group_name || e.name || '';
function balanceLabelsHtml(labels) {
  const names=(Array.isArray(labels)?labels:[]).filter(label=>typeof label==='string'&&label.trim());
  return names.length?`<span class="small muted" data-balance-labels>${names.map(esc).join(' · ')}</span>`:'';
}
function invoiceBalancePreviewHtml(preview) {
  if(!preview||!['available_balance','apply_amount','invoice_total','payable','remaining_balance'].every(k=>Number.isFinite(preview[k])&&preview[k]>=0)||typeof preview.token!=='string'||!preview.token)throw Error('Не удалось проверить баланс. Обновите расчёт перед оформлением');
  return `<div class="finance-numbers" data-sale-balance-preview><div><small>Доступно на балансе</small><b data-balance-available>${rub(preview.available_balance)}</b>${balanceLabelsHtml(preview.labels)}</div><div><small>Зачтётся с баланса</small><b data-balance-apply>${rub(preview.apply_amount)}</b></div><div><small>К оплате</small><b data-balance-payable>${rub(preview.payable)}</b></div><div><small>Останется на балансе</small><b data-balance-remaining>${rub(preview.remaining_balance)}</b></div></div>${Number(preview.already_applied)>0?`<p class="small muted" data-balance-applied>Ранее зачтено с баланса: ${rub(preview.already_applied)}. Повторного списания не будет</p>`:'<p class="small muted">Зачёт произойдёт только после подтверждения оформления</p>'}`;
}

async function validatePaymentLinkForSharing(link,studentId,subscriptionId=null,fullPayment=false) {
  const bundleIds=Array.isArray(subscriptionId)?subscriptionId:null;
  const params=new URLSearchParams({student_id:studentId,require_full:fullPayment?'1':'0'});if(bundleIds)params.set('subscription_ids',bundleIds.join(','));else if(subscriptionId)params.set('subscription_id',subscriptionId);
  const state=await api('GET',`/api/paylinks/${link.id}/validate?${params}`);
  const returnedIds=(state.invoice_scope||[]).map(row=>Number(row.subscription_id));
  if(state.valid!==true||Number(state.link_id)!==Number(link.id)||Number(state.student_id)!==Number(studentId)||(bundleIds?(new Set(returnedIds).size!==new Set(bundleIds.map(Number)).size||!bundleIds.every(id=>returnedIds.includes(Number(id)))):(subscriptionId&&Number(state.subscription_id)!==Number(subscriptionId)))||state.url!==link.url||state.amount!==link.amount||!Number.isInteger(state.amount)||state.amount<=0||(fullPayment&&state.amount!==state.current_due)){
    const error=new Error(state.reason||'Сумма или состояние ссылки изменились. Обновите ссылку перед отправкой');error.linkState=state;throw error;
  }
  return state;
}
async function renewInvoicePaymentLink(studentId,subscriptionId,purpose='') {
  if(!subscriptionId)throw Error('Откройте оформление абонемента и создайте новую общую ссылку');
  const quote=await api('GET',`/api/subscriptions/${subscriptionId}/payment-quote?use_balance=0`),amount=quote.balance_preview?.payable??quote.total;
  if(!Number.isInteger(amount)||amount<=0)throw Error('По счёту сейчас нет суммы к оплате. Обновите карточку');
  const link=await api('POST',`/api/students/${studentId}/paylinks`,{amount,purpose,subscription_id:subscriptionId,full_payment:true,replace_open:true});
  await validatePaymentLinkForSharing(link,studentId,subscriptionId,true);return link;
}

async function openSell(d, reload) {
  if (!(await ensureRequired(d.student.id,'sale'))) return;
  const id=d.student.id;
  d=await api('GET',`/api/students/${id}`).catch(()=>d);
  const box=$('#sell-box'); if(!box)return;
  const session=String(S.saleSequence=(S.saleSequence||0)+1);box.dataset.saleSession=session;
  const saleRequestId=window.crypto?.randomUUID?.()||Date.now()+'-'+Math.random().toString(36).slice(2);
  const isCurrent=()=>box.isConnected&&box.dataset.saleSession===session&&$('#sell-box')===box;
  scTab('abon');
  const ens=d.enrollments.filter(e=>e.lesson_kind!=='ofp');
  const previous=(d.subscriptions||[]).filter(s=>s.status!=='cancelled'&&s.kind!=='single'&&s.month<d.next_month&&(!ens.length||ens.some(e=>e.id===s.enrollment_id))).sort((a,b)=>Number(b.status==='paid')-Number(a.status==='paid')||b.month.localeCompare(a.month)||b.id-a.id);
  const preferred=ens.find(e=>e.id===previous[0]?.enrollment_id)||ens.find(e=>e.group_id===d.student.group_id)||ens[0]||{id:null,tariff:previous[0]?.tariff||d.student.tariff||'fixed',group_id:previous[0]?.group_id,program_id:(S.meta.groups||[]).find(g=>g.id===previous[0]?.group_id)?.program_id};
  const choices=(S.meta.groups||[]).filter(g=>!g.is_makeup&&g.lesson_kind!=='ofp').map(g=>{
    const own=ens.find(e=>e.group_id===g.id);
    return {...preferred,...own,id:own?.id||preferred.id,group_id:g.id,group_name:g.name,program_id:g.program_id,lesson_kind:g.lesson_kind,fixed_only:g.fixed_only,new_group:!own};
  });
  ens.forEach(e=>{if(!choices.some(c=>c.group_id===e.group_id))choices.push(e)});
  if(!choices.length)return toast('Нет доступных групп для продажи',true);
  const months=[...new Set([d.cur_month,d.next_month,...d.subscriptions.filter(s=>s.status==='invoiced').map(s=>s.month)])].sort().reverse();
  const pendingSingles=d.subscriptions.filter(s=>s.kind==='single'&&s.status==='invoiced');
  const methods=Object.entries(PAY_METHODS).filter(([k])=>PAY_MANUAL_METHODS.includes(k));
  box.innerHTML=`<div class="section sell"><div class="row" style="align-items:center"><h3 class="grow" style="margin:0">Продать абонемент</h3><button class="icon-btn" id="sl-x" title="Закрыть">✕</button></div>
    <div class="field" style="margin-top:14px"><label>Что оформляем</label><select id="sl-kind"><option value="month">На месяц</option><option value="multi">На несколько месяцев</option><option value="single">Разовое занятие</option></select></div>
    <div class="field" id="sl-single-invoice-field" hidden><label>Разовое занятие</label><select id="sl-single-invoice"><option value="">Оформить новое занятие</option>${pendingSingles.map(s=>`<option value="${s.id}">Уже выставлено: ${s.single_date?dShort(s.single_date):monthLabel(s.month)} · ${rub(s.price)}</option>`).join('')}</select></div>
    <div class="row"><div class="field" id="sl-month-field"><label>Месяц</label><select id="sl-month">${months.map(m=>`<option value="${m}" ${m===d.next_month?'selected':''}>${monthLabel(m)}</option>`).join('')}</select></div><div class="field"><label>Группа</label><select id="sl-en">${choices.map(e=>`<option value="${e.group_id}" ${e.group_id===preferred.group_id?'selected':''}>${esc(e.group_name)}${e.new_group&&ens.length?' · другая группа':''}</option>`).join('')}</select></div></div>
    <div class="row"><div class="field" id="sl-tar-field"><label>Тариф</label><select id="sl-tar"></select></div><div class="field" id="sl-disc-field"><label>Скидка</label><select id="sl-disc"><option value="">По правилам</option>${(d.discounts||[]).map(x=>`<option value="${x.id}">${esc(x.name)}, ${x.percent}%</option>`).join('')}</select><small class="muted">Ручной выбор заменяет автоматическую скидку · «Многодетная семья» — только вручную</small></div></div>
    <div class="small muted" id="sl-admission" hidden>Допуск в сборную — по решению тренера. Разовых занятий нет</div>
    <div class="field" id="sl-ofp-box" hidden><label>День ОФП</label><select id="sl-ofp"></select></div>
    <div class="field" id="sl-group-action-box" hidden><label>Как изменить группу с начала месяца</label><select id="sl-group-action"><option value="transfer">Перевести из прежней группы</option><option value="add">Добавить вторую, сохранить прежнюю</option></select><small class="muted">Изменение сохранится при оформлении</small></div>
    <div class="field"><label>Причина исключения занятий (кроме дат до пробного)</label><input id="sl-exclusion-reason" maxlength="1000" placeholder="Например, согласованный отпуск"></div>
    <label class="sale-credit" id="sl-credit-box" hidden><input type="checkbox" id="sl-credit"> Клиент запросил перерасчёт пропусков прошлого месяца <span class="small muted" id="sl-credit-info"></span></label>
    <div id="sl-individual-box"><label class="check"><input type="checkbox" id="sl-individual"> Индивидуальные условия</label>
      <div id="sl-individual-fields" hidden>
        <div class="field"><label for="sl-individual-price">Согласованная стоимость, ₽</label><input id="sl-individual-price" type="number" min="1" max="10000000" step="1"></div>
        <div class="field"><label for="sl-individual-reason">Причина и с кем согласовали</label><input id="sl-individual-reason" maxlength="1000" placeholder="Согласовано с Юлией. Ошибка школы при выставлении счёта"></div>
      </div></div>
    <div class="field"><label>Фактическая дата оплаты</label><input type="date" id="sl-paid-date" value="${S.meta.today}" max="${S.meta.today}" required><small class="muted">Если оплата уже была, укажите день получения денег</small></div><div class="quote" id="sl-q">Считаю…</div><div id="sl-balance-box"><label class="check"><input type="checkbox" id="sl-use-balance"> Зачесть с баланса</label><div id="sl-balance-preview" aria-live="polite"></div></div><div class="sc-actions"><button class="btn primary" id="sl-link">Ссылка на оплату</button><button class="btn" id="sl-now">Отметить оплату</button><button class="btn" id="sl-invoice">Выставить счёт</button></div><div id="sl-res"></div></div>`;
  const en=()=>choices.find(e=>e.group_id===+$('#sl-en').value), single=()=>$('#sl-kind').value==='single';
  let total=0,version=0,singleDay=null,existing=null,autoDates=true,balancePreview=null,controlsLocked=false,linkBusy=false,savedInvoice=null,shareVersion=0;
  const exclusions=new Set();
  const individual = () => !single() && !existing && $('#sl-individual').checked;
  const agreedPrice = () => Number($('#sl-individual-price').value);
  const agreedReason = () => $('#sl-individual-reason').value.trim();
  const individualValid = () => !individual() || (
    Number.isInteger(agreedPrice()) && agreedPrice() > 0 && agreedPrice() <= 10000000 && agreedReason().length >= 5);
  const individualInfo = q => q.individual_reason ?
    `<div class="finance-note">Индивидуальные условия · Согласованная стоимость <b>${rub(q.total)}</b></div>` : '';
  const busy=on=>{box.querySelectorAll('.sc-actions button').forEach(b=>b.disabled=on);$('#sl-link').disabled=on||$('#sl-paid-date').value!==S.meta.today;$('#sl-now').disabled=on||total<=0;$('#sl-invoice').hidden=single()||!!existing;$('#sl-invoice').disabled=on};
  const lock=on=>{controlsLocked=on;box.querySelectorAll('select,input').forEach(e=>e.disabled=on);if(!on)syncFields()};
  const drawBalance=q=>{if(single())return;const html=invoiceBalancePreviewHtml(q.balance_preview);balancePreview=q.balance_preview;$('#sl-balance-preview').innerHTML=html;$('#sl-use-balance').disabled=controlsLocked||Number(balancePreview.already_applied)>0;if(Number(balancePreview.already_applied)>0)$('#sl-use-balance').checked=false;total=balancePreview.payable;$('#sl-link').textContent=total<=0&&balancePreview.apply_amount>0?'Зачесть с баланса':'Ссылка на оплату';};
  const syncFields=()=>{
    $('#sl-individual-box').hidden=single()||!!existing;
    $('#sl-individual-fields').hidden=!individual();
    $('#sl-balance-box').hidden=single();$('#sl-use-balance').disabled=controlsLocked||Number(balancePreview?.already_applied)>0;
    $('#sl-kind option[value=single]').disabled=!!en().program_id&&!pendingSingles.length;
    $('#sl-single-invoice option[value=""]').disabled=!!en().program_id;
    if(en().program_id&&single()&&!$('#sl-single-invoice').value){if(pendingSingles.length)$('#sl-single-invoice').value=String(pendingSingles[0].id);else $('#sl-kind').value='month'}
    $('#sl-single-invoice-field').hidden=!single()||!pendingSingles.length;
    $('#sl-month-field').hidden=single()&&!!$('#sl-single-invoice').value;
    $('#sl-tar-field').hidden=single();$('#sl-disc-field').hidden=single();
    if(en().fixed_only&&!existing){$('#sl-tar').value='fixed';$('#sl-tar').disabled=true}
    $('#sl-admission').hidden=!en().program_id;
    $('#sl-ofp-box').hidden=single()||!en().program_id||$('#sl-tar').value!=='pkg';
    const separateProgram=en().new_group&&ens.length&&(en().program_id||null)!==(preferred.program_id||null);
    $('#sl-group-action option[value=transfer]').disabled=!!separateProgram;
    if(separateProgram)$('#sl-group-action').value='add';
    $('#sl-group-action-box').hidden=single()||!en().new_group||!ens.length;
  };
  const recommend=()=>{
    const p=(d.program?.programs||[]).find(p=>p.id===en().program_id),plan=p?.plan;
    const effective=plan?.next&&$('#sl-month').value>=plan.next.from?plan.next:plan;
    const last=previous.find(s=>s.enrollment_id===en().id&&s.month<$('#sl-month').value&&(!en().new_group||(en().program_id||null)===(preferred.program_id||null)));
    $('#sl-tar').innerHTML=en().program_id?'<option value="arena_only">Только Арктика</option><option value="pkg">Арктика + ОФП</option>':en().fixed_only?'<option value="fixed">Негибкий</option>':'<option value="fixed">Негибкий</option><option value="flex">Гибкий</option>';
    $('#sl-tar').value=en().fixed_only?'fixed':en().program_id?effective?.tariff||last?.tariff||'arena_only':last?.tariff||(en().tariff==='flex'?'flex':'fixed');
    $('#sl-ofp').innerHTML=(p?.ofp_groups||[]).map(g=>`<option value="${g.id}" ${g.id===effective?.ofp_group_id?'selected':''}>${esc(g.name)}</option>`).join('');syncFields();
  };
  const quote=async(preserveCalendar=false)=>{
    const request=++version;shareVersion++;busy(true);existing=null;balancePreview=null;$('#sl-balance-preview').textContent='Считаю…';$('#sl-res').innerHTML='';if(preserveCalendar!==true||!$('#sl-calendar'))$('#sl-q').innerHTML='Считаю…';
    ['#sl-tar','#sl-disc','#sl-ofp'].forEach(s=>$(s).disabled=false);syncFields();$('#sl-credit-box').hidden=true;
    const own=ens.find(e=>e.group_id===en().group_id);
    const sub=single()?pendingSingles.find(s=>s.id===+$('#sl-single-invoice').value):d.subscriptions.find(s=>(s.group_id===en().group_id||own&&s.enrollment_id===own.id)&&s.month===$('#sl-month').value&&s.kind!=='single'&&s.status!=='cancelled');
    if(sub){
      $('#sl-exclusion-reason').closest('.field').hidden=true;existing=sub;syncFields();let current;try{current=await api('GET',`/api/subscriptions/${sub.id}/payment-quote?paid_date=${encodeURIComponent($('#sl-paid-date').value)}&use_balance=${!single()&&$('#sl-use-balance').checked?'1':'0'}`);if(request!==version||!isCurrent())return;if(!single())drawBalance(current)}catch(e){if(request===version&&isCurrent())$('#sl-q').textContent=e.message;return}if(request!==version||!isCurrent())return;const paid=current.paid;if(single())total=current.total;
      if(![...$('#sl-tar').options].some(o=>o.value===sub.tariff))$('#sl-tar').add(new Option(sub.tariff==='flex'?'Гибкий':'Негибкий',sub.tariff));$('#sl-tar').value=sub.tariff;syncFields();['#sl-tar','#sl-disc','#sl-ofp'].forEach(s=>$(s).disabled=true);
      $('#sl-q').innerHTML=`<b>${single()?(total?'Разовое занятие уже оформлено':'Разовое занятие оплачено'):(current.price>paid?'Абонемент уже оформлен':'Абонемент оплачен')}</b><div class="finance-numbers"><div><small>Стоимость</small><b>${rub(current.price)}</b></div><div><small>Оплачено</small><b>${rub(paid)}</b></div><div><small>Осталось оплатить</small><b>${rub(total)}</b></div></div><p class="small muted">На ${esc(monthLabel(sub.month))} в группу «${esc(clientGroupLabel(sub))}» уже есть оформленный абонемент — повторный не создаём</p>${existingSaleCalendarHtml(sub,d)}`;busy(total<=0&&!(balancePreview?.apply_amount>0));return;
    }
    try{
      $('#sl-exclusion-reason').closest('.field').hidden=false;
      if(!$('#sl-paid-date').value)throw Error('Укажите фактическую дату оплаты');
      const params=new URLSearchParams({month:$('#sl-month').value,group_id:en().group_id,dates_selected:'1',paid_date:$('#sl-paid-date').value,use_balance:!single()&&$('#sl-use-balance').checked?'1':'0'});
      if(single()){if(singleDay)params.set('date',singleDay)}else{
        params.set('enrollment_id',en().id||'');params.set('tariff',$('#sl-tar').value);params.set('ofp_group_id',$('#sl-ofp').value||'');params.set('excluded_dates',[...exclusions].join(','));params.set('credit_requested',$('#sl-credit').checked?'1':'0');if($('#sl-disc').value)params.set('discount_id',$('#sl-disc').value);
      }
      if (!individualValid()) throw Error('Укажите согласованную стоимость и причину');
      if (individual()) params.set('individual_price', agreedPrice());
      const q=await api('GET',`/api/students/${id}/${single()?'single-quote':'quote'}?${params}`);if(request!==version||!isCurrent())return;
      if(autoDates&&!q.single){autoDates=false;const defaults=(q.calendar_dates||q.dates).filter(day=>q.trial_cutoff&&day<=q.trial_cutoff);if(defaults.length){defaults.forEach(day=>exclusions.add(day));if(defaults.length===new Set(q.calendar_dates||q.dates).size){q.dates=[];q.excluded_dates=[...exclusions];q.total=0;q.lessons_paid=0;q.empty_after_trial=true}else return quote()}}
      total=q.total;if(q.single)singleDay=q.dates[0];else drawBalance(q);q.calendar_picker=true;
      updateSaleQuoteHtml($('#sl-q'),individualInfo(q)+(q.empty_after_trial?'<p>После пробного в этом месяце нет занятий. Выберите другой месяц или отметьте согласованные занятия вручную</p>':q.single?`<div class="total">${rub(q.total)}</div><div class="small">Разовое занятие · ${dShort(singleDay)}</div>`:q.program?programQuoteHtml(q):quoteHtml(q))+saleCalendarHtml(q,$('#sl-month').value,[...(d.trial_visits||[]).map(v=>v.date),(d.student.trial_at||'').slice(0,10)]),preserveCalendar===true);
      $('#sl-credit-box').hidden=!q.credit_available;$('#sl-credit-info').textContent=q.credit_available?`(до ${q.credit_available} занятий)`:'';
      $('#sl-calendar').onchange=e=>{const day=e.target.dataset.saleDate;if(!day)return;if(single()){singleDay=day;quote();return}if(!e.target.checked&&!$('#sl-calendar').querySelector('input:checked')){e.target.checked=true;return toast('Оставьте хотя бы одно занятие',true)}if(e.target.checked)exclusions.delete(day);else exclusions.add(day);quote(true)};busy(total<=0&&!(balancePreview?.apply_amount>0));
    }catch(e){if(request===version&&isCurrent())$('#sl-q').textContent=e.message}
  };
  const reset=()=>{$('#sl-individual').checked=false;$('#sl-individual-price').value='';$('#sl-individual-reason').value='';exclusions.clear();autoDates=true;singleDay=null;$('#sl-credit').checked=false;$('#sl-use-balance').checked=false};
  $('#sl-individual').onchange = $('#sl-individual-price').onchange = $('#sl-individual-reason').onchange = () => quote(true);
  $('#sl-use-balance').onchange=()=>quote(true);
  $('#sl-paid-date').onchange=()=>quote();
  $('#sl-single-invoice').onchange=()=>{reset();syncFields();quote()};
  $('#sl-kind').onchange=()=>{if($('#sl-kind').value==='multi'){version++;openMultiMonthSale(d,id,box,reload,{group_id:en().group_id,tariff:$('#sl-tar').value,ofp_group_id:$('#sl-ofp').value});return}reset();if(single())$('#sl-month').value=d.cur_month;syncFields();quote()};
  $('#sl-month').onchange=$('#sl-en').onchange=()=>{reset();recommend();quote()};
  $('#sl-tar').onchange=()=>{reset();syncFields();quote()};$('#sl-ofp').onchange=()=>{reset();quote()};$('#sl-disc').onchange=$('#sl-credit').onchange=quote;
  $('#sl-x').onclick=()=>{version++;box.dataset.saleSession='';box.innerHTML=''};
  const selection=()=>({...(individual()?{individual_price:agreedPrice(),individual_reason:agreedReason()}:{}),exclusion_reason:$('#sl-exclusion-reason').value,paid_date:$('#sl-paid-date').value,single:single(),month:$('#sl-month').value,date:existing?.single_date||singleDay,group_id:en().group_id,enrollment_id:en().id,group_action:$('#sl-group-action').value,tariff:$('#sl-tar').value,ofp_group_id:$('#sl-tar').value==='pkg'?+$('#sl-ofp').value:null,excluded_dates:[...exclusions],credit_requested:$('#sl-credit').checked,discount_id:$('#sl-disc').value||null,group:existing?.group_name?clientGroupLabel(existing):existing?.kind==='single'?'':clientGroupLabel(en()),tariffLabel:single()?'Разовое занятие':$('#sl-tar').selectedOptions[0]?.textContent,existing_id:existing?.id,use_balance:!single()&&$('#sl-use-balance').checked,expected_balance_token:balancePreview?.token,request_id:saleRequestId});
  const invoice=async order=>{
    let result=savedInvoice||(order.existing_id?{id:order.existing_id,quote:{}}:await api('POST',`/api/students/${id}/${order.single?'single':'invoice'}`,order));
    if(order.existing_id&&order.use_balance&&!savedInvoice)await api('POST',`/api/subscriptions/${result.id}/balance-apply`,{use_balance:true,expected_balance_token:order.expected_balance_token,request_id:saleRequestId+':balance',paid_date:order.paid_date});
    savedInvoice=result;
    const current=await api('GET',`/api/subscriptions/${result.id}/payment-quote?paid_date=${encodeURIComponent(order.paid_date)}&use_balance=0`);
    if(!order.single&&isCurrent())drawBalance(current);
    return {...result,payable:current.balance_preview?.payable??current.total,balance_preview:current.balance_preview};
  };
  $('#sl-invoice').onclick = async () => {
    if (!individualValid()) return toast('Укажите согласованную стоимость и причину', true);
    const order = selection();
    busy(true);
    lock(true);
    try {
      await invoice(order);
      toast('Счёт выставлен');
      reload();
    } catch (error) { fail(error); busy(false); lock(false); }
  };
  $('#sl-now').onclick=async()=>{
    if (!individualValid()) return toast('Укажите согласованную стоимость и причину', true);
    const order=selection();busy(true);lock(true);let prepared=null,manualVersion=0;
    const requestId=window.crypto?.randomUUID?.()||Date.now()+'-'+Math.random().toString(36).slice(2);
    $('#sl-res').innerHTML=`<div class="row" style="margin-top:14px;align-items:flex-end"><div class="field"><label>Сумма, ₽</label><input type="number" id="sl-amount" min="1" step="1" value="${total}"></div><div class="field"><label>Как оплатили</label><select id="sl-m">${methods.map(([k,t])=>`<option value="${k}">${t}</option>`).join('')}</select></div><div class="field"><label>Фактическая дата оплаты</label><input type="date" id="sl-payment-date" required max="${S.meta.today}" value="${esc(order.paid_date)}"></div></div><div id="sl-payment-date-info" class="small muted"></div><p id="sl-payment-error" class="need-err" role="alert"></p><button class="btn primary" id="sl-ok">Сохранить оплату ${rub(total)}</button><p class="small muted">Фиксируем уже полученные деньги. Можно указать частичную оплату, остаток останется к оплате</p>`;
    const updateAmount=()=>$('#sl-ok').textContent='Сохранить оплату '+rub($('#sl-amount').value);
    $('#sl-amount').oninput=updateAmount;
    $('#sl-payment-date').onchange=async()=>{
      const version=++manualVersion,day=$('#sl-payment-date').value;order.paid_date=day;$('#sl-paid-date').value=day;$('#sl-ok').disabled=true;$('#sl-amount').disabled=true;$('#sl-m').disabled=true;
      try{
        if(!day)throw Error('Укажите фактическую дату оплаты');
        const params=new URLSearchParams({month:order.month,group_id:order.group_id,dates_selected:'1',paid_date:day,enrollment_id:order.enrollment_id||'',tariff:order.tariff,excluded_dates:order.excluded_dates.join(','),credit_requested:order.credit_requested?'1':'0',ofp_group_id:order.ofp_group_id||'',discount_id:order.discount_id||'',use_balance:order.use_balance?'1':'0'});
        if(order.single)params.set('date',order.date);
        if(order.individual_price)params.set('individual_price',order.individual_price);
        const quoted=await api('GET',order.existing_id?`/api/subscriptions/${order.existing_id}/payment-quote?paid_date=${encodeURIComponent(day)}&use_balance=${order.use_balance?'1':'0'}`:`/api/students/${id}/${order.single?'single-quote':'quote'}?${params}`);
        if(version!==manualVersion||!isCurrent())return;
        if(!order.existing_id&&!order.single){$('#sl-q').innerHTML=(quoted.program?programQuoteHtml(quoted):quoteHtml(quoted))+saleCalendarHtml(quoted,order.month,[...(d.trial_visits||[]).map(v=>v.date),(d.student.trial_at||'').slice(0,10)]);$('#sl-q').querySelectorAll('input').forEach(input=>input.disabled=true)}
        if(!order.single){drawBalance(quoted);order.expected_balance_token=balancePreview.token}
        const payable=quoted.balance_preview?.payable??quoted.total;$('#sl-amount').value=payable;updateAmount();$('#sl-payment-date-info').textContent=`Расчёт на ${dShort(day)}: ${rub(payable)}`;$('#sl-ok').disabled=false;$('#sl-amount').disabled=false;$('#sl-m').disabled=false;
      }catch(e){if(version===manualVersion&&isCurrent()){$('#sl-payment-date-info').textContent=e.message;$('#sl-amount').disabled=false;$('#sl-m').disabled=false}}
    };
    $('#sl-ok').onclick=async()=>{
      if($('#sl-ok').disabled)return;
      const amount=Number($('#sl-amount').value),method=$('#sl-m').value,day=$('#sl-payment-date').value;
      if(!Number.isInteger(amount)||amount<=0)return toast('Укажите сумму в рублях',true);
      if(!day||day>S.meta.today)return toast('Укажите фактическую дату оплаты, не позднее сегодня',true);
      $('#sl-payment-error').textContent='';order.paid_date=day;$('#sl-ok').disabled=true;$('#sl-payment-date').disabled=true;$('#sl-amount').disabled=true;$('#sl-m').disabled=true;
      try{
        if(!prepared)prepared=await invoice(order);
        if(prepared.payable<=0){toast('Счёт уже оплачен с баланса');reload();return}
        await api('POST',`/api/subscriptions/${prepared.id}/pay`,{amount,method,paid_date:day,request_id:requestId});toast('Оплата сохранена');if(isCurrent())reload();
      }catch(e){fail(e);if(isCurrent()&&$('#sl-ok')){$('#sl-payment-error').textContent=e.message||'Не удалось подтвердить сохранение оплаты';$('#sl-ok').disabled=false;$('#sl-amount').disabled=false;$('#sl-m').disabled=false;if(!prepared)$('#sl-payment-date').disabled=false}}
    };
  };
  const showSaleLink=async(order,subscriptionId,link)=>{
    const generation=++shareVersion,current=()=>isCurrent()&&generation===shareVersion;
    const title=order.single?`Разовое занятие${order.date?' '+dShort(order.date):''}`:`Абонемент на ${monthLabel(order.month)}`;
    $('#sl-res').innerHTML=`<p class="small muted" id="sl-link-check" role="status">Проверяем актуальную сумму ссылки…</p><p class="need-err" id="sl-link-warning" role="alert" hidden></p><button type="button" class="btn" id="sl-refresh-link">Сформировать новую ссылку</button><div id="sl-link-content" hidden><p class="small muted">По ссылке родитель проверит email для чека. Перед копированием и вставкой ещё раз проверим сумму</p><label style="margin-top:14px">Сообщение родителю</label><textarea id="sl-msg" rows="9"></textarea><div class="sc-actions" style="margin-top:10px"><button type="button" class="btn primary" id="sl-send">Вставить в чат с семьёй</button><button type="button" class="btn" id="sl-copy">Скопировать</button><button type="button" class="btn" id="sl-done">Готово</button></div></div>`;
    const message=$('#sl-msg'),content=$('#sl-link-content'),status=$('#sl-link-check'),warning=$('#sl-link-warning'),refresh=$('#sl-refresh-link'),copy=$('#sl-copy'),send=$('#sl-send');
    let sharing=false,invalid=false;
    const setPending=value=>{copy.disabled=send.disabled=value||invalid;message.disabled=value||invalid;refresh.disabled=value;};
    const block=error=>{if(!current())return;invalid=true;message.value='';content.hidden=true;warning.hidden=false;warning.textContent=(error.message||'Не удалось проверить ссылку')+(Number.isInteger(error.linkState?.current_due)?` Сейчас к оплате: ${rub(error.linkState.current_due)}.`:'')+' Старая ссылка не скопирована и не вставлена в чат.';refresh.hidden=false;status.textContent='Ссылка требует обновления';setPending(false);};
    const share=async action=>{
      if(sharing||invalid||!current())return;sharing=true;setPending(true);status.textContent='Проверяем актуальную сумму ссылки…';
      try{
        await validatePaymentLinkForSharing(link,id,subscriptionId,true);if(!current())return;
        const paymentUrls=(message.value.match(/https?:\/\/[^\s<>"']+/gi)||[]).map(value=>value.replace(/[)\]},.;!?]+$/g,'')).filter(value=>{try{return new URL(value).pathname.startsWith('/pay/')}catch(_){return false}});
        if(paymentUrls.length!==1||paymentUrls[0]!==link.url)throw Error('Ссылка в сообщении изменена или добавлена другая ссылка на оплату. Обновите сообщение перед копированием или вставкой');
        if(action==='copy')await copyTextWithNotice(message.value,'Текст скопирован','payment-link');
        else{scTab('feed');const target=$('#cm-text');if(!target)return toast('Откройте чат в карточке или скопируйте сообщение',true);target.value=message.value;target.dispatchEvent(new Event('input',{bubbles:true}));target.focus();target.scrollIntoView({block:'center',behavior:'smooth'});toast('Сообщение вставлено. Проверьте и отправьте')}
        if(current())status.textContent='Сумма ссылки проверена';
      }catch(error){block(error)}finally{sharing=false;if(current())setPending(false)}
    };
    copy.onclick=()=>share('copy');send.onclick=()=>share('paste');$('#sl-done').onclick=reload;
    refresh.onclick=async()=>{
      if(sharing||!current())return;sharing=true;setPending(true);status.textContent='Обновляем расчёт и ссылку…';
      try{const fresh=await renewInvoicePaymentLink(id,subscriptionId,title);if(current())await showSaleLink(order,subscriptionId,fresh)}catch(error){block(error)}finally{sharing=false;if(current())setPending(false)}
    };
    setPending(true);
    try{
      await validatePaymentLinkForSharing(link,id,subscriptionId,true);if(!current())return;
      message.value=`${title}\n${displayChildName(d.student.name)}\n\n${order.group?'Группа: '+order.group+'\n':''}Тариф: ${order.tariffLabel}\n\nК оплате: ${rub(link.amount)}\n\nСсылка для оплаты:\n${link.url}`;
      invalid=false;content.hidden=false;status.textContent='Сумма ссылки проверена';resizeMessageInput(message);setPending(false);
    }catch(error){block(error)}
  };
  $('#sl-link').onclick=async()=>{
    if(linkBusy)return;if(!individualValid())return toast('Укажите согласованную стоимость и причину',true);const order=selection();if(order.paid_date!==S.meta.today)return toast('Для состоявшейся оплаты нажмите «Отметить оплату»',true);linkBusy=true;busy(true);lock(true);
    try{
      const balanceOnly=!order.single&&total<=0&&balancePreview?.apply_amount>0;
      const st=balanceOnly?{ready:true,links:[]}:await api('GET',`/api/students/${id}/paylinks`);if(!st.ready)throw Error('PayKeeper не подключён: Чеки → Подключение');
      const r=await invoice(order);if(!isCurrent())return;if(r.payable<=0){toast('Счёт уже оплачен с баланса');reload();return}
      const title=order.single?`Разовое занятие${order.date?' '+dShort(order.date):''}`:`Абонемент на ${monthLabel(order.month)}`;
      const previousLink=(st.links||[]).find(l=>l.status==='open'&&l.subscription_id===r.id&&l.amount===r.payable);
      const l=previousLink||await api('POST',`/api/students/${id}/paylinks`,{amount:r.payable,purpose:`${title}: ${displayChildName(d.student.name)}`,subscription_id:r.id,full_payment:true,replace_open:true});if(!isCurrent())return;
      await showSaleLink(order,r.id,l);
    }catch(e){fail(e);if(isCurrent()){lock(!!savedInvoice);busy(false)}}finally{linkBusy=false}
  };
  recommend();pastGroupSearch($('#sl-en'));pastGroupSearch($('#sl-ofp'));quote();box.scrollIntoView({block:'start',behavior:'smooth'});
}

async function copyTextWithNotice(text, notice='Текст скопирован', copyType='text') {
  try { await copyCrmText(text, copyType); }
  catch (_) {
    const input=document.createElement('textarea'),previous=document.activeElement,selection=window.getSelection();
    const ranges=selection?Array.from({length:selection.rangeCount},(_,i)=>selection.getRangeAt(i).cloneRange()):[];
    let copied=false;
    input.value=text;input.style.cssText='position:fixed;opacity:0;width:1px;height:1px';
    try {
      document.body.appendChild(input);input.focus({preventScroll:true});input.select();
      copied=document.execCommand('copy');
    } catch (_) { /* Останется обычное ручное копирование. */ }
    finally {
      input.remove();
      try { previous?.focus({preventScroll:true}); } catch (_) {}
      if(selection&&ranges.length){
        selection.removeAllRanges();
        for(const range of ranges)if(range.startContainer.isConnected&&range.endContainer.isConnected)selection.addRange(range);
      }
    }
    if(!copied){prompt('Скопируйте текст',text);return false}
  }
  toast(notice);
  return true;
}
function clientSubscriptionMonth(sub) {
  if (/^\d{4}-\d{2}$/.test(sub.display_month || '')) return sub.display_month;
  if (sub.kind === 'single' && /^\d{4}-\d{2}-\d{2}$/.test(sub.single_date || '')) return sub.single_date.slice(0, 7);
  return sub.month || '';
}
function clientSubscriptionPaid(sub, balance) {
  const covered = balance.covered || {}, raw = Object.prototype.hasOwnProperty.call(covered, sub.id) ? covered[sub.id] : sub.paid_amount;
  return Math.max(0, Math.min(Number(sub.price) || 0, Number.isFinite(Number(raw)) ? Number(raw) : 0));
}
function clientSubscriptionsHtml(d) {
  const tariffs={fixed:'Негибкий',flex:'Гибкий',pkg:'Арктика + ОФП',arena_only:'Только Арктика',single:'Разовое занятие'};
  const card=x=>{
    const manual = String(x.source_subscription_id || '').startsWith('manual:');
    if(x.history_import)return `<li class="subscription-card compact-subscription"><div class="subscription-overview"><div class="subscription-identity"><h4>${esc(monthLabel(x.month))}</h4><div class="subscription-group">${esc(clientGroupLabel(x))}</div><div class="small muted subscription-meta">${manual ? 'Прошлый абонемент · '+esc(tariffs[x.tariff]) : 'Исторический абонемент '+(String(x.source_subscription_id).startsWith('sz:')?'по данным СЗ':'ERP №'+esc(x.source_subscription_id))} · ${x.lessons} ${plural(x.lessons,'занятие','занятия','занятий')} · с ${dShort(x.valid_from)} по ${dShort(x.valid_until)}</div><span class="pill green">${manual ? 'Добавлен вручную' : 'История перенесена'}</span></div><div class="finance-numbers subscription-money"><div><small>Историческая стоимость</small><b>${rub(x.price)}</b></div>${x.revenue_carry_out?`<div><small>Перенесено в следующий месяц</small><b>−${rub(x.revenue_carry_out)}</b></div>`:''}<div class="finance-note">${manual ? 'Без записи об оплате' : 'Счета и оплаты не переносились'}</div></div></div></li>`;
    const cancellation=(d.subscription_cancellations||[]).find(c=>c.subscription_id===x.id);
    const paid=x.status==='cancelled'&&cancellation?.period_verified?cancellation.paid_before:clientSubscriptionPaid(x,d.balance),left=Math.max(0,x.price-paid),cancelled=x.status==='cancelled',carryIn=Number(x.revenue_carry_in)||0,carryOut=Number(x.revenue_carry_out)||0,carryPending=Number(x.revenue_carry_source_pending)||0;
    const bonus = cancelled ? 0 : d.balance.referral_by_invoice?.[x.id] || 0;
    const status=cancelled?'Отменён':!left?'Оплачен':paid?'Оплачен частично':'Ожидает оплаты';
    const own=d.enrollments.find(e=>e.id===x.enrollment_id),active=own&&x.kind!=='single'&&!cancelled&&x.month>=d.cur_month;
    const unpaidInvoice = !cancelled && x.kind !== 'single' && x.status === 'invoiced' && !paid && can('client_sales','full');
    const actions=[
      unpaidInvoice ? `<button type="button" class="btn sm" data-agreed-invoice="${x.id}" data-price="${x.price}">Индивидуальные условия</button><button type="button" class="btn sm danger" data-cancel-unpaid="${x.id}">Отменить неоплаченный счёт</button>` : '',
      !cancelled&&can('members','full')?`<button type="button" class="btn sm danger" data-pcanc="${x.id}">Удалить абонемент</button>`:'',
      S.user.role==='admin'&&x.kind!=='single'?`<button type="button" class="btn sm" data-sub-period="${x.id}">Уточнить срок действия</button>`:'',
      S.user.role==='admin'&&x.program_id?`<button type="button" class="btn sm" data-sub-composition="${x.id}">Уточнить состав Арктика + ОФП</button>`:'',
      active?`<button type="button" class="btn sm" data-sub-transfer="${x.id}">Поменять группу</button>`:''
    ].filter(Boolean).join('');
    return `<li class="subscription-card compact-subscription ${cancelled?'is-cancelled':''}">
      <div class="subscription-overview"><div class="subscription-identity"><h4>${x.kind==='single'?`Разовое занятие${x.single_date?' · '+dShort(x.single_date):''}${x.single_time?' · '+esc(x.single_time):''}`:monthLabel(x.month)}</h4><div class="subscription-group">${esc(clientGroupLabel(x))}</div><div class="small muted subscription-meta">${esc(x.kind==='single'?'Разовое занятие':tariffs[x.tariff]||'Абонемент')} · ${x.lessons} ${plural(x.lessons,'занятие','занятия','занятий')}${x.valid_from||x.valid_until?` · ${x.valid_from?'с '+dShort(x.valid_from):''}${x.valid_until?' по '+dShort(x.valid_until):''}`:''}</div><span class="pill ${!left&&!cancelled?'green':cancelled?'':'yellow'}">${status}</span>${x.kind==='single'&&x.date_needs_review?'<div class="small finance-note">Дата занятия требует сверки</div>':''}</div>
      <div class="finance-numbers subscription-money"><div><small>Стоимость</small><b>${rub(x.price)}</b></div>${carryIn?`<div><small>Перенесено из прошлого месяца</small><b>${rub(carryIn)}</b></div>`:''}${carryOut?`<div><small>Перенесено в следующий месяц</small><b>−${rub(carryOut)}</b></div>`:''}<div><small>Оплачено</small><b>${rub(paid - bonus)}</b></div>${bonus ? `<div><small>Зачтено бонусами</small><b>${rub(bonus)}</b></div>` : ""}<div><small>${cancelled?'Статус':'Остаток'}</small><b class="${left&&!cancelled?'finance-due':''}">${cancelled?'Отменён':rub(left)}</b></div>${carryPending?`<div class="finance-note">Исходная выручка ${rub(carryPending)} ещё не загружена — корректировка ждёт историю ERP</div>`:''}</div></div>
      <div class="subscription-card-footer"><details class="finance-detail"><summary>Подробнее</summary><div class="small muted">Оформил(а): ${esc(x.issued_by_name||'Автор не указан')}</div>${x.kind==='single'&&x.accounting_month&&x.accounting_month!==clientSubscriptionMonth(x)?`<div class="small muted">Исходный месяц покупки: ${esc(monthLabel(x.accounting_month))}</div>`:''}${x.kind==='single'&&x.source_lesson_date&&x.single_date&&x.source_lesson_date!==x.single_date?`<div class="small muted">Дата в источнике ERP: ${esc(dShort(x.source_lesson_date))}. Дата занятия после переноса: ${esc(dShort(x.single_date||''))}</div>`:''}${subscriptionPaymentDetailsHtml(x,d)}${x.credit_lessons?`<div>Перерасчёт пропусков: ${x.credit_lessons}</div>`:''}${x.late_fee?`<div>Надбавка: ${rub(x.late_fee)}</div>`:''}${x.discount_amount?`<div>Скидка: ${esc(x.discount_name||'Название не указано')} · ${rub(x.discount_amount)}</div>`:''}${x.calculation_reason?`<div>Причина: ${esc(x.calculation_reason)}</div>`:x.corrected&&!x.historical_sale?'<div>Сумма скорректирована · причина в ранней записи не указана</div>':''}${x.excluded_dates&&x.excluded_dates!=='[]'?`<div>Исключены: ${JSON.parse(x.excluded_dates).map(day=>esc(dShort(day))+': '+esc((JSON.parse(x.exclusion_reasons||'{}'))[day]||'Причина не указана')).join('<br>')}</div>`:''}${x.composition_corrected&&x.lesson_schedule?`<div class="small muted">${JSON.parse(x.lesson_schedule).map(c=>`${esc(c.group)}: ${c.dates.length} занятий · ${rub(c.amount??c.dates.length*c.rate)}${c.erp_subscription_id?' · ERP №'+esc(c.erp_subscription_id):''}`).join('<br>')}</div>`:''}</details>
      ${actions?`<details class="subscription-actions"><summary class="btn sm" aria-label="Действия с абонементом" title="Действия с абонементом">⋯</summary><div>${actions}</div></details>`:''}</div>
      ${!cancelled&&can('members','full')?`<div data-sub-cancel-box="${x.id}"></div>`:''}
      ${S.user.role==='admin'&&x.program_id?`<div data-composition-box="${x.id}"></div>`:''}
      ${active?`<div class="subscription-transfer" data-sub-transfer-box="${x.id}" hidden></div>`:''}</li>`;
  };
  const key=String(d.student?.id||'preview');
  S.subscriptionCalendars=S.subscriptionCalendars||{};
  const availableYears=[...new Set([d.cur_month.slice(0,4),...d.subscriptions.map(x=>clientSubscriptionMonth(x).slice(0,4))])].sort().reverse();
  let selected=S.subscriptionCalendars[key];
  if(!selected||!availableYears.includes(selected.slice(0,4))){selected=d.subscriptions.some(x=>clientSubscriptionMonth(x)===d.cur_month)?d.cur_month:d.subscriptions.map(clientSubscriptionMonth).filter(Boolean).sort().reverse()[0]||d.cur_month;S.subscriptionCalendars[key]=selected}
  const year = selected.slice(0, 4);
  const months = new Map();
  for (const sub of d.subscriptions) {
    const month = clientSubscriptionMonth(sub);
    if (!months.has(month)) months.set(month, []);
    months.get(month).push(sub);
  }
  const details = Array.from(months, ([month, entries]) => {
    const shown = entries.filter(sub => sub.status !== 'cancelled');
    const cancelled = entries.filter(sub => sub.status === 'cancelled');
    const visible = month === selected;
    return `<div data-sub-detail-month="${esc(month)}" ${visible ? '' : 'hidden'}>
      ${shown.length ? `<ul data-sub-live-list class="sub-list ${visible ? 'subscription-live-list' : ''}">${shown.map(card).join('')}</ul>` : '<p class="muted small">В этом месяце покупок нет</p>'}
      ${cancelled.length ? `<details class="subscription-cancelled-history"><summary>Отменённые покупки · ${cancelled.length}</summary><ul class="sub-list">${cancelled.map(card).join('')}</ul></details>` : ''}
    </div>`;
  }).join('');
  return `<div class="subscription-balance"><div><small>Свободный баланс</small><b>${rub(d.balance.deposit||0)}</b><span>Можно зачесть в оплату</span>${balanceLabelsHtml(d.balance.balance_labels)}</div><div><small>Долг</small><b class="${d.balance.debt?'finance-due':''}">${rub(d.balance.debt||0)}</b><span>Прошлые и текущий месяц</span></div><div><small>Будущие абонементы</small><b>${rub(d.balance.upcoming||0)}</b><span>Осталось оплатить</span></div></div><div class="subscription-year-head"><h3>Абонементы</h3><select id="subscription-year" aria-label="Год абонементов">${availableYears.map(y=>`<option ${y===year?'selected':''}>${y}</option>`).join('')}</select></div><div class="subscription-year-grid">${subscriptionMonthTiles(d,year,selected)}</div><h4 class="subscription-selected-month">${esc(monthLabel(selected))}</h4>${details}<p data-sub-empty class="muted small" ${!months.has(selected)?'':'hidden'}>В этом месяце покупок нет</p>${refundRequestsHtml(d.refund_requests||[])}${subscriptionRefundsHtml(d)}`;
}

function subscriptionMonthTiles(d, year, selected) {
  return Array.from({length:12},(_,i)=>{
    const month=year+'-'+String(i+1).padStart(2,'0'),all=d.subscriptions.filter(x=>clientSubscriptionMonth(x)===month),live=all.filter(x=>x.status!=='cancelled');
    const financial=live.filter(x=>!x.history_import),historical=live.filter(x=>x.history_import),historicalTotal=historical.reduce((sum,x)=>sum+(Number(x.price)||0),0),historicalLessons=historical.reduce((n,x)=>n+x.lessons,0),total=financial.reduce((sum,x)=>sum+x.price,0),paid=financial.reduce((sum,x)=>sum+clientSubscriptionPaid(x,d.balance),0),left=Math.max(0,total-paid);
    const state=!all.length?'empty':!live.length?'cancelled':!financial.length&&historical.length?'historical':!left?'paid':paid?'partial':'unpaid';
    const label={empty:'Нет абонемента',cancelled:'Отменён',historical:'История ERP',paid:'Оплачен',partial:'Частично',unpaid:'Ждёт оплаты'}[state];
    const singleCount=live.filter(x=>x.kind==='single').length,caption=singleCount&&singleCount===live.length?`${singleCount} ${plural(singleCount,'разовое','разовых','разовых')}`:label;
    return `<button type="button" class="subscription-month ${state} ${month===selected?'selected':''}" data-sub-month="${month}" aria-pressed="${month===selected}"><strong>${esc(MONTHS[i])}</strong><span class="subscription-month-status">${caption}</span>${live.length?historical.length&&!financial.length?`<b>${rub(historicalTotal)}</b><small>${historicalLessons} ${plural(historicalLessons,'занятие','занятия','занятий')} · без счетов и оплат</small>`:`<b>${rub(total)}</b>${left?`<small>Остаток ${rub(left)}</small>`:'<small>Всё оплачено</small>'}`:'<b>—</b><small>&nbsp;</small>'}</button>`;
  }).join('');
}

function wireSubscriptionCalendar(root, d) {
  const grid = root.querySelector('.subscription-year-grid');
  const year = root.querySelector('#subscription-year');
  const heading = root.querySelector('.subscription-selected-month');
  const empty = root.querySelector('[data-sub-empty]');
  if (!grid || !year || !heading || !empty) return;
  const details = root.querySelectorAll('[data-sub-detail-month]');
  const choose = month => {
    S.subscriptionCalendars[String(d.student.id)] = month;
    const active = Array.from(details).find(section => section.dataset.subDetailMonth === month);
    heading.textContent = monthLabel(month);
    empty.hidden = Boolean(active);
    details.forEach(section => {
      section.hidden = section !== active;
      section.querySelector('[data-sub-live-list]')?.classList.toggle('subscription-live-list', section === active);
    });
    grid.querySelectorAll('[data-sub-month]').forEach(button => {
      const selected = button.dataset.subMonth === month;
      button.classList.toggle('selected', selected);
      button.setAttribute('aria-pressed', String(selected));
    });
  };
  grid.onclick = event => {
    const button = event.target.closest('[data-sub-month]');
    if (button) choose(button.dataset.subMonth);
  };
  year.onchange = () => {
    const months = d.subscriptions.map(clientSubscriptionMonth)
      .filter(month => month.startsWith(year.value + '-')).sort().reverse();
    const selected = months[0] || year.value + '-01';
    grid.innerHTML = subscriptionMonthTiles(d, year.value, selected);
    choose(selected);
  };
}

function openSubscriptionPeriod(sub, box, reload) {
  const display=value=>value?value.split('-').reverse().join('.'):'';
  box.innerHTML=`<form class="subscription-period-form"><h4>Срок действия по ERP</h4><div class="row">${[['from','Дата начала',sub.valid_from],['until','Дата окончания',sub.valid_until]].map(([key,label,value])=>`<div class="field"><label for="period-${key}">${label}</label><div class="period-date-control"><input id="period-${key}" data-date-text="${key}" type="text" inputmode="numeric" autocomplete="off" placeholder="ДД.ММ.ГГГГ" maxlength="10" value="${esc(display(value))}" ${key==='from'?'required':''}><label class="btn period-calendar" title="Выбрать дату в календаре" aria-label="Выбрать дату в календаре">📅<input data-date-picker="${key}" type="date" value="${esc(value||'')}" aria-label="${label}: календарь"></label></div></div>`).join('')}</div><div class="small need-err" role="alert"></div><div class="sc-actions"><button class="btn primary" type="submit">Сохранить</button><button class="btn" type="button" data-period-close>Отмена</button></div></form>`;
  const form=box.querySelector('form');
  for(const key of ['from','until']) {
    const text=form.querySelector(`[data-date-text="${key}"]`),picker=form.querySelector(`[data-date-picker="${key}"]`);
    text.oninput=()=>{const digits=text.value.replace(/\D/g,'').slice(0,8);const caret=text.selectionStart;const count=text.value.slice(0,caret).replace(/\D/g,'').length;text.value=[digits.slice(0,2),digits.slice(2,4),digits.slice(4,8)].filter(Boolean).join('.');let at=count+(count>2?1:0)+(count>4?1:0);text.setSelectionRange(at,at);text.setCustomValidity('');picker.value=parse(text.value)||'';};
    picker.onchange=()=>{text.value=display(picker.value);text.setCustomValidity('');};
  }
  function parse(value) {
    if(!/^\d{2}\.\d{2}\.\d{4}$/.test(value))return null;
    const [day,month,year]=value.split('.'),iso=`${year}-${month}-${day}`,date=new Date(iso+'T12:00:00Z');
    return +year>=1&&!isNaN(date)&&date.toISOString().slice(0,10)===iso?iso:null;
  }
  form.querySelector('[data-period-close]').onclick=()=>box.remove();
  form.onsubmit=async event=>{
    event.preventDefault();const start=form.querySelector('[data-date-text="from"]'),end=form.querySelector('[data-date-text="until"]');
    const from=parse(start.value),until=end.value?parse(end.value):'';
    if(!from||(end.value&&!until)){const field=!from?start:end;field.setCustomValidity('Введите существующую дату: ДД.ММ.ГГГГ');field.reportValidity();return;}
    if(until&&until<from){end.setCustomValidity('Дата окончания не может быть раньше начала');end.reportValidity();return;}
    const save=form.querySelector('[type=submit]');save.disabled=true;
    try{await api('POST',`/api/subscriptions/${sub.id}/period`,{valid_from:from,valid_until:until,expected_from:sub.valid_from,expected_until:sub.valid_until});toast('Срок абонемента сохранён');reload();}
    catch(e){form.querySelector('.need-err').textContent=e.message||'Не удалось сохранить';save.disabled=false;}
  };
  form.querySelector('[data-date-text]').focus();
}

function subscriptionCancellationsHtml(d) {
  const rows=d.subscription_cancellations||[];if(!rows.length)return '';
  return `<h4>Расчёты после отмены</h4>${rows.map(c=>c.mode==='error'?`<article class="subscription-refund"><b>Абонемент №${c.subscription_id} · Ошибочное оформление отменено</b><p>Учтённая оплата ${rub(c.paid_before)} сохранена. На момент отмены: ${rub(c.balance_available)} свободно, ${rub(c.applied_to_invoices)} зачтено в другие абонементы</p><p class="small muted">Причина: ${esc(c.reason)}</p></article>`:`<article class="subscription-refund"><b>Абонемент №${c.subscription_id} · ${c.mode==='balance'?'Средства оставлены на балансе':'Возврат клиенту'}</b>${!c.period_verified?'<p class="pill yellow">Требуется сверка будущего периода: отмена оформлена по старому расчёту. Сумму пока нельзя считать проверенной</p>':`<p>Занятия прекращены с ${esc(c.effective_date)}</p><div class="finance-numbers"><div><small>Прошедший период</small><b>${rub(c.retained_amount)}</b></div><div><small>${c.mode==='balance'?'Средства оставлены на балансе':'Сумма к возврату'}</small><b>${rub(c.amount)}</b></div></div>${c.mode==='balance'?`<p>В момент отмены: ${rub(c.balance_available)} осталось свободно, ${rub(c.applied_to_invoices)} зачтено в другие абонементы</p>`:''}`}<p class="small muted">Свободный баланс сейчас: <b>${rub(d.balance.deposit)}</b>. Деньги, зачтённые в другие счета, уже отражены в их оплате</p></article>`).join('')}`;
}

function subscriptionRefundsHtml(d) {
  const rows=d.subscription_refunds||[];
  if(!rows.length)return '';
  return `<h4>Возвраты по отменённым абонементам</h4><div class="subscription-refunds">${rows.map(r=>`<article class="subscription-refund"><div class="finance-card-head"><b>${rub(r.amount)}</b><span class="pill ${r.status==='done'?'green':'yellow'}">${r.status==='done'?'Возврат завершён':r.status==='receipt_pending'?'Деньги возвращены · ждём чек':'Ожидает возврата'}</span></div><p>${r.method==='paykeeper'?`PayKeeper · исходный платёж №${esc(r.pk_payment_id)} · на исходный платёжный инструмент`:esc(PAY_METHODS[r.method]||r.method)}</p>${r.method==='paykeeper'&&r.status!=='done'&&r.paykeeper_url?`<a class="btn sm" href="${esc(r.paykeeper_url)}" target="_blank" rel="noopener noreferrer">Открыть PayKeeper</a><p class="small">Найдите исходный платёж по ID выше. Выполните возврат согласованной суммы на исходный платёжный инструмент и сформируйте фискальный чек «Возврат прихода». Затем нажмите «Проверить возврат и фискальный чек»</p>`:''}${r.status==='pending'?'<p class="small muted">Сумма зарезервирована и недоступна для новых занятий. Деньги ещё не отмечены как возвращённые</p>':''}${r.receipt_id?`<p class="small">Чек возврата: ${esc(r.receipt_id)}</p>`:''}${r.proof?`<p class="small">${esc(r.proof)}</p>`:''}${r.status!=='done'&&can('payments','view')?`<button class="btn sm" data-refund-confirm="${r.id}">${r.method==='paykeeper'?'Проверить возврат и фискальный чек':r.status==='receipt_pending'?'Внести фискальный чек':'Подтвердить фактический возврат'}</button><div data-refund-form="${r.id}"></div>`:''}</article>`).join('')}</div>`;
}

async function openSubscriptionCancel(subid,button,reload) {
  const box=document.querySelector(`[data-sub-cancel-box="${subid}"]`);
  if(!box)return;
  button.disabled=true;
  try {
    const q=await api('GET',`/api/subscriptions/${subid}/cancel-preview`);
    if(q.cancelled){toast('Абонемент уже отменён',true);reload();return}
    if(q.period_blocker&&q.can_cancel_error){await openErroneousSubscriptionCancel(subid,box,button,reload);return}
    let period=q;
    box.innerHTML=`<form class="subscription-cancel-form"><h4>Удаление абонемента</h4><p>Абонемент будет отменён и останется в истории. Оплачено: <b>${rub(q.paid)}</b></p><div class="field"><label>Прекратить будущие занятия с</label><input name="effective_date" type="date" required min="${esc(q.effective_date||addDays(S.meta.today,1))}" value="${esc(q.effective_date||addDays(S.meta.today,1))}"></div><div data-period-summary></div><p class="cancel-approval-warning">ПЕРЕД ЭТИМ ДЕЙСТВИЕМ СОГЛАСОВАТЬ С ЮЛИЕЙ</p><label class="check"><input name="approved" type="checkbox" required> Я согласовал(а) отмену и порядок расчёта с Юлией</label><div class="field"><label>Дата согласования</label><input name="approval" type="date" required></div><div class="field"><label>Причина отмены</label><textarea name="reason" required rows="2" maxlength="1000"></textarea></div><div class="field"><label>Как поступить с деньгами</label><select name="mode"><option value="balance">${q.paid?'Оставить средства на балансе':'Без возврата — оплата не учтена'}</option><option value="refund">Запросить возврат клиенту</option></select></div>${q.refund_blocker?`<p class="small">Возврат пока недоступен: ${esc(q.refund_blocker)}</p>`:''}<div data-return-info hidden><p>Возвращается только оплата будущего периода, указанная в расчёте выше. Прошедшие даты и сегодняшние занятия не возвращаются</p>${q.sources.map(p=>`<p>${rub(p.amount)} · ${p.method==='paykeeper'?`PayKeeper, исходный платёж №${esc(p.pk_payment_id)}. Возврат выполняется в кабинете на тот же платёжный инструмент. Обязательно сформируйте чек «Возврат прихода».`:esc(p.method_label||PAY_METHODS[p.method]||p.method)+'. Возврат исходным способом с документом и чеком.'}</p>`).join('')}<p class="small muted">После отмены сумма будет зарезервирована. Подтвердите фактический возврат и чек в блоке возвратов</p></div><div class="small need-err" data-cancel-error></div><div class="sc-actions"><button class="btn danger" type="submit">Отменить абонемент</button><button class="btn" type="button" data-cancel-close>Назад</button></div></form>`;
    const form=box.querySelector('form');
    const drawPeriod=()=>{
      const requestRefund=form.elements.mode.value==='refund';
      form.elements.approved.required=!requestRefund;form.elements.approved.closest('label').hidden=requestRefund;
      form.elements.approval.required=!requestRefund;form.elements.approval.closest('.field').hidden=requestRefund;
      form.querySelector('.cancel-approval-warning').textContent=requestRefund?'Возврат будет отправлен Юлии на личное одобрение. Деньги пока не возвращаются':'ПЕРЕД ЭТИМ ДЕЙСТВИЕМ СОГЛАСОВАТЬ С ЮЛИЕЙ';
      form.querySelector('[type=submit]').textContent=requestRefund?'Отправить заявку Юлии':'Отменить абонемент';
      box.querySelector('[data-period-summary]').innerHTML=period.period_blocker?`<p class="need-err">${esc(period.period_blocker)}</p>`:`<div class="finance-numbers"><div><small>Удержано за прошедший период</small><b>${rub(period.retained_amount)}</b></div><div><small>Стоимость будущих занятий</small><b>${rub(period.future_amount)}</b></div><div><small>Можно сохранить или вернуть</small><b>${rub(period.releasable)}</b></div></div><p class="small muted">Остаётся в расчёте ${period.retained_lessons} занятий; отменяется ${period.future_lessons}. При частичной оплате сначала покрывается прошедший период</p>`;
      const isRefund=form.elements.mode.value==='refund';
      box.querySelector('[data-return-info]').hidden=!isRefund;
      if(isRefund)box.querySelector('[data-return-info]').innerHTML=`<p>Сумма возврата за будущий период: <b>${rub(period.releasable||0)}</b></p>${(period.sources||[]).map(p=>`<p>${rub(p.amount)} · ${p.method==='paykeeper'?`PayKeeper, исходный платёж №${esc(p.pk_payment_id)}. Возврат на исходный платёжный инструмент с фискальным чеком «Возврат прихода».`:esc(p.method_label||PAY_METHODS[p.method]||p.method)}</p>`).join('')}${period.refund_blocker?`<p class="need-err">${esc(period.refund_blocker)}</p>`:''}`;
      form.querySelector('[data-cancel-error]').textContent=isRefund&&!period.releasable?'Возвращать нечего по данным CRM — выберите отмену без возврата или сначала проверьте учёт оплаты':'';
      form.querySelector('[type=submit]').disabled=!!period.period_blocker||(isRefund&&(!!period.refund_blocker||!period.releasable));
    };
    let generation=0;
    form.elements.effective_date.onchange=async()=>{
      const current=++generation;form.querySelector('[type=submit]').disabled=true;
      try{const next=await api('GET',`/api/subscriptions/${subid}/cancel-preview?effective_date=${encodeURIComponent(form.elements.effective_date.value)}`);if(current!==generation)return;period=next;drawPeriod()}
      catch(e){if(current===generation)box.querySelector('.need-err').textContent=e.message}
    };
    form.elements.mode.onchange=drawPeriod;drawPeriod();
    box.querySelector('[data-cancel-close]').onclick=()=>{box.innerHTML='';button.disabled=false};
    form.onsubmit=async e=>{
      e.preventDefault();const submit=form.querySelector('[type=submit]');submit.disabled=true;
      try {const result=await api('POST',`/api/subscriptions/${subid}/cancel`,{mode:form.elements.mode.value,approved_by_yulia:form.elements.approved.checked,approval:form.elements.approval.value,reason:form.elements.reason.value,effective_date:form.elements.effective_date.value,expected_releasable:period.releasable});toast(form.elements.mode.value==='refund'?'Заявка отправлена Юлии. Создана отдельная задача контроля возврата':`Средства оставлены на балансе: ${rub(result.amount||0)}. Свободно: ${rub(result.balance_available||0)}, зачтено в другие счета: ${rub(result.applied_to_invoices||0)}`);reload()}
      catch(err){box.querySelector('.need-err').textContent=err.message;submit.disabled=false}
    };
  }catch(e){fail(e);button.disabled=false}
}

async function confirmSubscriptionRefund(rid,button,d,reload) {
  const r=(d.subscription_refunds||[]).find(r=>r.id===rid);if(!r)return;
  if(r.method==='paykeeper') {
    button.disabled=true;
    try {const result=await api('POST',`/api/subscription-refunds/${rid}/confirm`,{});toast(result.message||'Возврат и фискальный чек подтверждены',result.status!=='done');reload()}
    catch(e){fail(e);button.disabled=false}return;
  }
  const box=document.querySelector(`[data-refund-form="${rid}"]`);
  box.innerHTML=`<form class="subscription-cancel-form">${r.status==='pending'?`<p>Верните ${rub(r.amount)} исходным способом: ${esc(PAY_METHODS[r.method]||r.method)}</p><label class="check"><input name="returned" type="checkbox" required> Деньги фактически возвращены</label><div class="field"><label>Документ или номер операции возврата</label><input name="proof" required maxlength="500"></div>`:''}<p>Сформируйте фискальный чек «Возврат прихода» и внесите его реквизиты. Если чек ещё не готов, можно сначала подтвердить возврат денег</p><div class="row"><div class="field"><label>ФН</label><input name="receipt_fn" inputmode="numeric"></div><div class="field"><label>ФД</label><input name="receipt_fd" inputmode="numeric"></div><div class="field"><label>ФП</label><input name="receipt_fp" inputmode="numeric"></div></div><div class="small need-err" data-cancel-error></div><button class="btn" type="submit">Сохранить подтверждение</button></form>`;
  box.querySelector('form').onsubmit=async e=>{e.preventDefault();const form=e.target,submit=form.querySelector('button');submit.disabled=true;try{const result=await api('POST',`/api/subscription-refunds/${rid}/confirm`,{actually_returned:form.elements.returned?.checked,proof:form.elements.proof?.value,receipt_fn:form.elements.receipt_fn.value,receipt_fd:form.elements.receipt_fd.value,receipt_fp:form.elements.receipt_fp.value});toast(result.message||'Подтверждение сохранено',result.status!=='done');reload()}catch(err){box.querySelector('.need-err').textContent=err.message;submit.disabled=false}};
}

function openReferralBonus(sid, reload) {
  const host = $("#pay-form");
  host.innerHTML = `<form class="quote">
    <div class="row"><b class="grow">Начислить 3 000 ₽ за рекомендацию</b><button type="button" class="icon-btn" data-bonus-close aria-label="Закрыть">✕</button></div>
    <div class="field"><label for="bonus-friend-query">Кого порекомендовали</label>
      <div class="row"><input id="bonus-friend-query" placeholder="Имя ребёнка или телефон родителя"><button type="button" class="btn" data-bonus-search>Найти</button></div>
    </div>
    <div class="field" hidden data-bonus-results><label for="bonus-friend">Выберите ребёнка</label><select id="bonus-friend" name="friend" required></select></div>
    <p class="small muted" aria-live="polite" data-bonus-message></p>
    <button class="btn primary" type="submit" disabled>Начислить бонус</button>
  </form>`;
  const form = host.querySelector("form");
  const input = form.querySelector("#bonus-friend-query");
  const message = form.querySelector("[data-bonus-message]");
  const results = form.querySelector("[data-bonus-results]");
  const submit = form.querySelector("[type=submit]");
  const search = form.querySelector("[data-bonus-search]");
  let generation = 0;
  input.oninput = () => {
    generation++;
    results.hidden = submit.disabled = true;
    message.textContent = "";
  };
  input.onkeydown = event => {
    if (event.key === "Enter") {
      event.preventDefault();
      search.click();
    }
  };
  form.querySelector("[data-bonus-close]").onclick = () => host.replaceChildren();
  search.onclick = async () => {
    const query = input.value.trim();
    if (query.length < 2) {
      message.textContent = "Введите хотя бы два символа";
      return;
    }
    const request = ++generation;
    results.hidden = submit.disabled = true;
    message.textContent = "Ищу клиента…";
    try {
      const found = await api("GET", "/api/students?" + new URLSearchParams({q: query}));
      if (request !== generation || !form.isConnected) return;
      const friends = found.filter(item => item.id !== sid);
      form.elements.friend.innerHTML = '<option value="">Выберите из списка</option>' + friends.map(item =>
        `<option value="${item.id}">${esc([item.name, item.parent, item.phone].filter(Boolean).join(" · "))}</option>`).join("");
      results.hidden = !friends.length;
      message.textContent = friends.length ? "" : "Клиент не найден";
    } catch (error) {
      if (request === generation && form.isConnected) message.textContent = error.message;
    }
  };
  form.elements.friend.onchange = () => { submit.disabled = !form.elements.friend.value; };
  form.onsubmit = async event => {
    event.preventDefault();
    submit.disabled = true;
    search.disabled = input.disabled = form.elements.friend.disabled = true;
    try {
      const result = await api("POST", `/api/students/${sid}/referral-bonus`, {friend_id: +form.elements.friend.value});
      toast(result.already_awarded ? "Этот бонус уже начислен" : "Начислено 3 000 ₽ за рекомендацию");
      reload();
    } catch (error) {
      message.textContent = error.message;
      submit.disabled = search.disabled = input.disabled = form.elements.friend.disabled = false;
    }
  };
  input.focus();
}

function clientBalanceHtml(d) {
  return `<h3>Оплаты и баланс</h3><div class="balance-summary"><div class="${d.balance.debt?'has-debt':''}"><small>Задолженность</small><b>${rub(d.balance.debt)}</b><span>По прошедшим и текущим счетам</span></div><div><small>Будущие занятия</small><b>${rub(d.balance.upcoming)}</b><span>К оплате за следующий месяц</span></div><div><small>Свободный баланс</small><b>${rub(d.balance.deposit)}</b><span>${d.balance.referral_balance ? `В том числе бонусы · ${rub(d.balance.referral_balance)}` : "Доступен для оплаты занятий"}</span>${balanceLabelsHtml(d.balance.balance_labels)}</div></div>
    ${(d.unpaid_cur||[]).length?`<div class="finance-note">Нужно проверить оплату текущих занятий: ${(d.unpaid_cur||[]).map(esc).join(', ')}</div>`:''}
    ${subscriptionCancellationsHtml(d)}<div class="sc-actions"><button class="btn primary sm" id="bal-topup">+ Пополнить баланс</button>${can('client_sales','full')?'<button class="btn sm" id="bal-referral">Начислить бонус за рекомендацию</button>':''}</div><div id="pay-form"></div><div id="pay-links"></div>
    <h4 class="finance-history-title">История денег</h4>${d.payments.length?`<ul class="pay-list ${d.payments.length>4?'folded':''}">${d.payments.map(p=>`<li class="payment-card ${p.cancelled?'is-cancelled':''}"><div class="finance-card-head"><div><b class="payment-amount ${p.amount<0?'refund':''}">${p.amount<0?'−':'+'}${rub(Math.abs(p.amount))}</b><div class="small muted">${p.method==='refund_pending'?'Зарезервировано для возврата':p.amount<0?'Возврат':'Поступление'} · ${esc(p.method_label||PAY_METHODS[p.method]||p.method)}</div></div><time>${dtShort(p.paid_at)}</time></div>${p.comment?`<div class="payment-comment">${esc(p.comment)}</div>`:''}<div class="small muted">${esc(p.user_name||'')}${p.cancelled?' · отменён':p.confirmed?'':' · ждёт подтверждения'}</div>${S.user.role==='admin'&&!p.cancelled&&!['unknown','refund_pending','refund'].includes(p.method)?`<details class="finance-detail"><summary>Действия с платежом</summary>${p.confirmed?'':`<button class="btn sm" data-pconf="${p.id}">Подтвердить</button>`}<button class="btn sm danger" data-pcancel="${p.id}">Отменить платёж</button></details>`:''}</li>`).join('')}</ul>`:'<p class="muted">Платежей пока нет</p>'}${d.payments.length>4?`<button class="linkish" id="pay-more">Показать всю историю (${d.payments.length})</button>`:''}
    ${d.family_kids.length||d.balance.deposit?`<details class="finance-detail"><summary>Другие операции</summary><div class="sc-actions">${d.family_kids.length?'<button class="btn sm" id="pay-fam">Оплата за семью</button>':''}${d.balance.cash_free_deposit>0&&d.family_kids.length?'<button class="btn sm" id="bal-move">Перенести баланс другому ребёнку</button>':''}${d.balance.cash_free_deposit>0&&can('payments','view')?'<button class="btn sm" id="bal-refund">Вернуть деньги с баланса</button>':''}</div></details>`:''}`;
}
function clientHistoryHtml(rows,journey=[]){
  const events=rows.filter(row=>!row.technical_only).slice().sort((a,b)=>String(b.ts||'').localeCompare(String(a.ts||''))||Number(b.id||0)-Number(a.id||0));
  const labels={payment:'Оплата',sale:'Абонемент',stage:'Статус',group:'Группа',task:'Задача',note:'Комментарий',message:'Сообщение',system:'CRM'};
  const editButton = row => S.user.role === "admin" && row.kind === "note" ? `<button type="button" class="btn sm" data-edit-history="${row.id}">Изменить комментарий</button>` : "";
  const dateParts = value => {
    const raw=String(value||''),date=(raw.slice(0,10)||'').split('-');
    return {date:date.length===3?`${date[2]}.${date[1]}.${date[0]}`:'—',time:raw.length>=16?raw.slice(11,16):'—'};
  };
  const item=row=>{
    const text=String(row.display_text||row.text||'').trim(),long=text.length>320||text.split('\n').length>4;
    const visible=long?text.slice(0,280).trimEnd()+'…':text;
    const when=dateParts(row.ts),author=row.display_author||row.user_name||'CRM';
    return `<tr class="client-history-event"><td data-label="Дата"><time datetime="${esc(row.ts||'')}">${esc(when.date)}</time></td><td data-label="Время">${esc(when.time)}</td><td data-label="Событие"><span class="client-history-kind">${esc(labels[row.kind]||'Событие')}</span></td><td data-label="Комментарий"><div class="client-history-text">${esc(visible||'—')}</div>${long?`<details class="client-history-full"><summary>Показать полностью</summary><div>${esc(text)}</div></details>`:''}${editButton(row)}</td><td data-label="Автор">${esc(author)}</td></tr>`;
  };
  const journeyRows=journey.slice().sort((a,b)=>String(b.ts||'').localeCompare(String(a.ts||''))||String(b.key||'').localeCompare(String(a.key||''))).map(row=>{const when=dateParts(row.ts);return `<tr class="client-history-event client-journey-event"><td data-label="Дата"><time datetime="${esc(row.ts||'')}">${esc(when.date)}</time></td><td data-label="Время">${esc(when.time)}</td><td data-label="Событие"><span class="client-history-kind">${esc(row.title||'Событие')}</span></td><td data-label="Что произошло"><div class="client-history-text">${esc(row.text||'—')}</div></td><td data-label="Источник">${esc(row.author||'CRM')}</td></tr>`}).join('');
  const primary=journeyRows?`<div class="client-journey-head"><b>Хронология клиента</b><span>От первого обращения до текущего следующего шага</span></div><div class="client-history-table-wrap"><table class="client-history-table"><thead><tr><th>Дата</th><th>Время</th><th>Этап</th><th>Что произошло</th><th>Источник</th></tr></thead><tbody>${journeyRows}</tbody></table></div>`:'<p class="muted">Хронология пока не заполнена</p>';
  const details=events.length?`<details class="client-history-details"><summary>Дополнительные записи и комментарии (${events.length})</summary><div class="client-history-table-wrap"><table class="client-history-table"><thead><tr><th>Дата</th><th>Время</th><th>Событие / задача</th><th>Комментарий</th><th>Автор</th></tr></thead><tbody>${events.map(item).join('')}</tbody></table></div></details>`:'';
  return primary+details;
}
async function loadClientPaymentLinks(sid) {
  const box=$('#pay-links');if(!box)return;
  const generation=String(Number(box.dataset.linkLoad||0)+1);box.dataset.linkLoad=generation;
  const data=await api('GET',`/api/students/${sid}/paylinks`).catch(()=>({links:[]}));if($('#pay-links')!==box||box.dataset.linkLoad!==generation)return;
  const links=data.links.filter(l=>l.status==='open');
  box.innerHTML=links.length?`<details class="finance-detail payment-links"><summary>Ссылки, по которым ждём оплату (${links.length})</summary>${links.map(l=>`<div class="payment-link-card"><b>${rub(l.amount)}</b><div>${esc(l.purpose)}</div><div class="small muted">Создана ${dtShort(l.created_at)}</div><button type="button" class="btn sm" data-plcopy="${l.id}">Скопировать ссылку</button><p class="need-err" data-pl-warning role="alert" hidden></p><button type="button" class="btn sm" data-pl-renew="${l.id}" hidden>${l.subscription_id?'Обновить ссылку':'Открыть оформление'}</button><details class="finance-detail"><summary>Другие действия</summary><button class="btn sm" data-plx="${l.id}">Отменить ссылку</button></details></div>`).join('')}</details>`:'';
  box.querySelectorAll('[data-plcopy]').forEach(b=>b.onclick=async()=>{
    if(b.disabled)return;const link=links.find(l=>Number(l.id)===Number(b.dataset.plcopy)),row=b.closest('.payment-link-card');b.disabled=true;
    try{await validatePaymentLinkForSharing(link,sid,link.subscription_id);if(b.isConnected&&$('#pay-links')===box&&box.dataset.linkLoad===generation)await copyTextWithNotice(link.url,'Ссылка скопирована','payment-link');if(b.isConnected&&box.dataset.linkLoad===generation)b.disabled=false}
    catch(error){if(!b.isConnected||box.dataset.linkLoad!==generation)return;const warning=row.querySelector('[data-pl-warning]');warning.hidden=false;warning.textContent=(error.message||'Не удалось проверить ссылку')+' Ссылка не скопирована.';row.querySelector('[data-pl-renew]').hidden=false;}
  });
  box.querySelectorAll('[data-pl-renew]').forEach(b=>b.onclick=async()=>{
    if(b.disabled)return;const link=links.find(l=>Number(l.id)===Number(b.dataset.plRenew));b.disabled=true;
    try{if(!link.subscription_id){await openStudent(sid,{tab:'abon'});$('#act-sell')?.click();return}await renewInvoicePaymentLink(sid,link.subscription_id,link.purpose);if($('#pay-links')===box)await loadClientPaymentLinks(sid)}catch(error){if(b.isConnected){b.closest('.payment-link-card').querySelector('[data-pl-warning]').textContent=error.message;b.disabled=false}}
  });
  box.querySelectorAll('[data-plx]').forEach(b=>b.onclick=async()=>{await api('POST',`/api/paylinks/${b.dataset.plx}/cancel`,{});loadClientPaymentLinks(sid)});
}

async function openStudent(id, options = {}) {
  const request=S.studentCardRequest=(S.studentCardRequest||0)+1;
  const previous = history.state?.studentCard;
  const requestedTab = options.tab || (previous?.id === +id && previous.userId === S.user?.id ? previous.tab : "feed");
  const tab = ["feed", "abon", "visits", "info"].includes(requestedTab) ? requestedTab : "feed";
  // Do not leave the previous child's name and subscriptions visible while the
  // newly selected card is loading.  The request number also makes a late
  // response from the previous card harmless.
  openDrawer(`<div class="section"><p class="muted">Загружаю карточку клиента…</p></div>`, "wide", { keepStudentRequest: true });
  let d;
  try { d = await api("GET", `/api/students/${id}`); }
  catch (e) {
    if(request!==S.studentCardRequest)return;
    const body=$("#drawer-body");
    if(body)body.innerHTML=`<div class="section"><h3>Карточка не открылась</h3><p class="error">${esc(e.message)}</p><button class="btn" data-student-retry>Повторить</button></div>`;
    body?.querySelector('[data-student-retry]')?.addEventListener('click',()=>openStudent(id,options));
    return;
  }
  if(request!==S.studentCardRequest)return;
  if(Number(d?.student?.id)!==Number(id)){
    const body=$("#drawer-body");
    if(body)body.innerHTML='<div class="section"><h3>Карточка не открылась</h3><p class="error">Сервер вернул данные другого клиента. Обновите страницу и повторите</p></div>';
    return;
  }
  const s = d.student, g = d.group, c = d.counters;
  const groups = S.meta.groups.filter((x) => !x.is_makeup && !x.program_id);
  const makeupGroups = S.meta.groups.filter((x) => x.is_makeup);
  const isMember = s.funnel === "members";



  const regular = d.enrollments.filter((e) => e.lesson_kind !== "ofp");
  const full = can("members", "full");
  const saleFull = can("client_sales", "full") || full;
  const alerts = [
    s.priority ? `<span class="pill red">🔥 Заявка с сайта: обработать первой</span>` : "",





    ...(d.trial_hold || []).map((h) => `<span class="pill yellow" title="Пока родитель присылает данные, другим это время не предлагается">⏳ Держим место на пробное ${esc(dShort(h.date))} в ${esc(h.time)} до ${esc(h.until.slice(11, 16))}</span>`),
    !s.birth ? `<span class="pill yellow">${s.reported_age != null ? `Возраст: ${esc(reportedAgeText(s))}; дата рождения не указана` : "Нет даты рождения"}</span>` : "",
    ...d.enrollments.filter((e) => e.age_warn).map((e) => `<span class="pill red">⚠️ ${esc(e.group_name)}: ${esc(e.age_warn)}</span>`),
    d.trial_age_warn ? `<span class="pill red">⚠️ Пробное ${esc(d.trial_age_warn)}</span>` : "",
    ...(d.chains || []).map((r) => `<span class="pill chain-pill">🔁 Автоцепочка «${esc(r.title)}»: отправлено ${r.step} из ${r.total} <button class="chip-x" data-chstop="${r.id}" title="Остановить цепочку">×</button></span>`),
  ].filter(Boolean).join("");
  const html = `
  <div class="sc-head">
    <div class="sc-title"><h2>${full?`<button type="button" class="profile-text-edit" data-profile-edit="name" title="Изменить имя">${esc(displayChildName(s.name))}</button>`:esc(displayChildName(s.name))}</h2></div>
    <div class="sc-status" ${full?'data-profile-open tabindex="0" role="button" title="Изменить данные клиента"':''}>${s.birth?esc(ageText(s.birth))+' · ':reportedAgeText(s)?esc(reportedAgeText(s))+' · ':''}${esc((s.erp_status||'').trim()||'Статус не указан')}${s.archived?' · в архиве':''}</div>
    ${d.siblings.length ? `<div class="chips sc-kids"><span class="chip on">${esc(displayChildName(s.name))}</span>${d.siblings.map((x) => `<button class="chip" data-open="${x.id}">${esc(displayChildName(x.name))}</button>`).join("")}</div>` : ""}
    <div class="sc-meta" ${full?'data-head-group tabindex="0" role="button" title="Изменить группу"':''}>${isMember ? (regular.length ? regular.map((e) => `<span class="pill">${esc(e.group_name)}</span>`).join(" ") : `<span class="muted small">Группа не выбрана</span>`)
      : `${s.trial_at ? `<span class="pill">Пробное ${dtShort(s.trial_at)}</span>` : ""}`}</div>
    <div class="sc-parents">${d.parents.map((p, parentIndex) => `<div class="sc-parent"><button type="button" class="profile-text-edit" data-profile-edit="name">${esc(p.name)}</button>${p.is_primary?'<span class="small muted">основной</span>':''}<span class="muted" aria-hidden="true">·</span><button class="rel-chip ${p.relation?'':'empty'}" data-profile-edit="name" title="Изменить родство">${p.relation?esc(p.relation):'Указать родство'}</button>${p.phone?`<span class="muted" aria-hidden="true">·</span><button class="linkish" data-profile-edit="name" title="Изменить телефон">${esc(fmtPhone(p.phone))}</button><button type="button" class="sc-copy-phone" data-copy-parent-phone="${parentIndex}" title="Скопировать номер" aria-label="Скопировать номер ${esc(p.name)}">⧉</button>`:''}${p.email?`<button type="button" class="profile-text-edit muted sc-parent-email" data-profile-edit="name">${esc(p.email)}</button>`:''}</div>`).join('')}</div>
    ${isMember && d.balance.debt ? `<div class="sc-debt-summary">Задолженность <b>${rub(d.balance.debt)}</b></div>` : ""}
    ${d.family.note ? `<div class="sc-staff-note" ${full?'data-profile-note tabindex="0" role="button" title="Изменить заметку"':''}><b>Заметка для команды</b><div>${esc(d.family.note)}</div></div>` : ""}
    ${alerts ? `<div class="sc-alerts">${alerts}</div>` : ""}
    <div class="sc-header-tools" role="toolbar" aria-label="Действия клиента">
      <button type="button" class="btn sc-tool" id="act-trial" title="Записать на пробное" aria-label="Записать на пробное">ПУ</button>
      ${saleFull?`<button type="button" class="btn sc-tool primary" id="act-sell" title="Продать абонемент или разовое занятие" aria-label="Продать абонемент или разовое занятие"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 4h2l3 12h11l2-8H6M9 20h.01M18 20h.01"/></svg></button>`:''}
      ${d.parents.some(p=>p.phone)?'<button type="button" class="btn sc-tool" id="act-call" title="Позвонить основному родителю" aria-label="Позвонить основному родителю"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3 4 5c-2 3 7 14 12 15l4-3-4-4-3 2-4-4 2-3Z"/></svg></button>':''}
      ${full?'<button type="button" class="btn sc-tool" data-profile-edit="name" title="Изменить данные клиента" aria-label="Изменить данные клиента"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 16 12-12 4 4L8 20H4Zm10-10 4 4"/></svg></button><button type="button" class="btn sc-tool" data-client-add title="Добавить члена семьи" aria-label="Добавить родителя или ребёнка"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="9" cy="7" r="3"/><path d="M3 20v-3c0-5 12-5 12 0v3M19 7v8M15 11h8"/></svg></button>':''}
      <details class="sc-extra-actions sc-tool-menu"><summary class="btn sc-tool" aria-label="Другие действия" title="Другие действия">⋯</summary><div>${isMember?'<button class="btn sm" id="act-vac">Отпуск</button>':''}${full?`<button type="button" class="btn sm" id="act-archive-card">${s.archived?"Вернуть из архива":"Перенести в архив"}</button>`:""}${d.can_delete_error?'<button type="button" class="btn sm danger" id="act-delete-error">Удалить ошибочную карточку</button>':''}</div></details>
    </div>
    <div id="sc-profile-edit" hidden></div>
    <div id="sc-trial-booking" hidden></div>
    <div id="sc-past-entry" hidden></div>
  </div>
  <div class="sc-tasks">${cardTasksBlock(d)}</div>
  <div class="sc-tabs" role="tablist" aria-label="Разделы карточки"><button class="on" role="tab" aria-selected="true" data-sctab="feed">Общение</button><button role="tab" aria-selected="false" data-sctab="abon">Абонементы и оплаты</button><button role="tab" aria-selected="false" data-sctab="visits">Посещения</button><button role="tab" aria-selected="false" data-sctab="info">Данные клиента</button></div>
  <div class="sc-grid" data-tab="feed"><div class="sc-left"></div><div class="sc-right"><div id="sell-box"></div></div><div class="sc-info"></div><div class="sc-attendance"></div></div>

  <div class="section" data-sc="stage">
    <div class="row">
      <div class="field"><label>Статус клиента</label><input readonly value="${esc((s.erp_status||'').trim()||'Статус не указан')}"></div>
      ${!isMember ? `<div class="field"><label>Тариф</label><select id="f-tariff"><option value="fixed" ${s.tariff === "fixed" ? "selected" : ""}>Негибкий</option><option value="flex" ${s.tariff === "flex" ? "selected" : ""}>Гибкий</option></select></div>` : ""}
    </div>
    ${!isMember ? `<div class="field"><label>Филиал</label><select id="f-branch"><option value="">— не выбран —</option>${S.meta.branches.filter((b) => b.active).map((b) => `<option value="${b.id}" ${b.id === s.branch_id ? "selected" : ""}>${esc(b.name)}</option>`).join("")}</select></div>` : ""}
    ${!isMember ? `<div class="field"><label>Группа для пробного</label><select id="f-group"><option value="">— не выбрана —</option>${groups.filter((x) => x.trial_ok !== 0 || x.id === s.group_id).map((x) => `<option value="${x.id}" ${x.id === s.group_id ? "selected" : ""}>${esc(x.name)} · ${esc(x.age)} лет</option>`).join("")}</select></div>
    ${d.trial_age_warn ? `<div class="alert" style="margin-bottom:8px">⚠️ Группа для пробного ${esc(d.trial_age_warn)}. Проверьте, точно ли сюда</div>` : ""}
    <div class="field"><label>Пробное занятие</label><input id="f-trial" type="datetime-local" value="${esc(s.trial_at || "")}"></div>
    <div id="trial-occ" class="small"></div><div id="trial-slots"></div>` : ""}
  </div>

  <div class="section" data-sc="family" data-title="Данные ребёнка и семьи">
    <div class="row"><h3 class="grow">Данные ребёнка</h3>${full ? '<button type="button" class="btn sm" data-edit-child>Редактировать</button>' : ""}</div>
    ${s.erp_branch_labels ? `<div class="field"><label>Филиалы из ERP</label><div>${esc(s.erp_branch_labels.split("; ").join(" · "))}</div></div>` : ""}

    <div class="row"><div class="field"><label>Имя и фамилия</label><div>${esc(displayChildName(s.name))}</div></div><div class="field"><label>Дата рождения</label><div>${s.birth?esc(s.birth.slice(0,10).split('-').reverse().join('.')):'Не указана'}</div></div>${reportedAgeText(s)?`<div class="field"><label>Возраст</label><div><b>${esc(reportedAgeText(s))}</b></div></div>`:''}</div>
    <fieldset data-client-parameters disabled style="border:0;padding:0;margin:0;min-width:0">
      <div class="field"><label>Рекламный источник</label>${sourceSelect("f-source", s.source_id, s.source)}</div>
      <div data-client-stage></div>
    </fieldset>
    ${full ? '<button type="button" class="btn sm" data-edit-client-parameters>Редактировать параметры</button>' : ''}

    <h3>Семья ${esc(d.family.name)}</h3>
    <div class="field"><label>Заметка для команды</label><div>${esc(d.family.note || "Не указана")}</div></div>
    ${full ? '<button type="button" class="btn sm" data-edit-family>Редактировать заметку</button>' : ''}
    <dl class="sc-family-contacts">${d.parents.map((p) => `<div class="sc-contact-line"><dt>${esc(p.name)}</dt><dd>${p.phone ? `<button class="linkish" data-copy-phone="${esc(p.phone)}" title="Скопировать номер">${esc(fmtPhone(p.phone))}</button>` : `<span class="muted">Телефон не указан</span>`}${p.messenger ? ` <span class="pill">${esc(p.messenger)}</span>` : ""}</dd></div>`).join("")}</dl>
    ${d.siblings.length ? `<div style="margin-top:10px" class="chips">${d.siblings.map((x) => `<button class="chip" data-open="${x.id}">${esc(displayChildName(x.name))}</button>`).join("")}</div>` : ""}
    <button class="btn sm" style="margin-top:10px" id="add-sibling">+ Брат или сестра</button>
  </div>

  <div data-sc="pu">${puCardBlock(d)}</div>
  <div class="section" id="comms-box"><h3>Связь с семьёй</h3><div class="muted small">Загружаю переписку…</div></div>

  <div class="section" data-sc="visits" data-title="Посещения и отработки">${clientAttendanceMarkup()}</div>

  <div class="section" data-sc="subs">${saleFull && d.enrollments.some(e=>!e.program_id && e.lesson_kind!=='ofp') ? '<button type="button" class="btn" id="add-previous-sub">Добавить прошлый абонемент</button><div id="previous-sub-form"></div>' : ''}${clientSubscriptionsHtml(d)}</div>
  <div class="section" data-sc="balance">${clientBalanceHtml(d)}</div>

  <div class="section" data-sc="vac" data-title="🌴 Отпуск">
    <h3>Отпуск</h3>
    <div class="row">
      <div class="field"><label>С</label><input id="v-start" type="date"></div>
      <div class="field"><label>По</label><input id="v-end" type="date"></div>
    </div>
    <button class="btn sm" id="v-add">Добавить отпуск</button>
    ${d.vacations.length ? `<ul class="history" style="margin-top:10px">${d.vacations.map((v) => `<li>${dShort(v.start)}–${dShort(v.end)} <button class="btn sm danger" data-vdel="${v.id}">Удалить</button></li>`).join("")}</ul>` : ""}
    <div class="small muted" style="margin-top:6px">Дни отпуска не входят в цену абонемента. Предупредить нужно до оплаты месяца</div>
  </div>

  <div class="section" data-sc="hist" data-title="🕘 История и комментарии">
    <h3>История</h3>
    ${isOwner() ? `<button class="btn" id="erp-full-client">Исходные данные ERP</button><div id="erp-full-client-out"></div>` : ''}
    <div class="row" style="margin-bottom:10px"><input id="note" placeholder="Комментарий, итог звонка…"><button class="btn sm" id="note-add" style="flex:0">Добавить</button></div>
    ${clientHistoryHtml(d.history,d.journey)}
  </div>
  <div style="margin-top:14px" data-sc="arch"><button class="btn sm ${s.archived ? "" : "danger"}" id="s-arch">${s.archived ? "Вернуть из архива" : "Перенести в архив"}</button></div>`;
  openDrawer(html, "wide", { keepStudentRequest: true });
  saveStudentCardRoute({ ...(previous?.id === +id ? previous : {}), id: +id, userId: S.user.id, tab });
  const body = $("#drawer-body");
  body.dataset.studentId = String(id);
  erpFullClient(body, id);
  layoutStudentCard(body, isMember);
  wireSubscriptionCalendar(body, d);
  scTab(tab);
  const reload = () => { openStudent(id); render(); };
  wireClientAttendance(body,d,reload);
  body.querySelectorAll("[data-sub-transfer]").forEach(b => b.onclick = () => {
    const sub=d.subscriptions.find(s=>s.id===+b.dataset.subTransfer), e=d.enrollments.find(e=>e.id===sub.enrollment_id), box=body.querySelector(`[data-sub-transfer-box="${sub.id}"]`);
    openTransfer(e.id,box,d,groups,reload,sub.month>d.cur_month?sub.month+'-01':S.meta.today);
  });
  const patch = async (data) => { try { await api("PATCH", `/api/students/${id}`, data); reload(); } catch (e) { fail(e); } };

  if ($("#f-tariff")) $("#f-tariff").onchange = (e) => patch({ tariff: e.target.value });
  if ($("#f-branch")) $("#f-branch").onchange = (e) => patch({ branch_id: e.target.value ? +e.target.value : "" });
  if ($("#f-group")) $("#f-group").onchange = (e) => patch({ group_id: e.target.value ? +e.target.value : "" });
  const enPatch = async (eid, data) => { try { await api("PATCH", `/api/enrollments/${eid}`, data); reload(); } catch (e) { fail(e); } };
  body.querySelectorAll("[data-enday]").forEach((b) => (b.onclick = () => {
    const eid = +b.dataset.enday;
    const days = [...body.querySelectorAll(`[data-enday="${eid}"]`)].filter((x) => (x === b ? !x.classList.contains("on") : x.classList.contains("on"))).map((x) => +x.dataset.wd);
    if (!days.length) return toast("Оставьте хотя бы один день", true);
    enPatch(eid, { weekdays: days });
  }));
  body.querySelectorAll("[data-entar]").forEach((sel) => (sel.onchange = () => enPatch(+sel.dataset.entar, { tariff: sel.value })));
  body.querySelectorAll("[data-entr]").forEach((b) => (b.onclick = () => openTransfer(+b.dataset.entr, body.querySelector(`[data-trbox="${b.dataset.entr}"]`), d, groups, reload)));
  body.querySelectorAll("[data-enoff]").forEach((b) => (b.onclick = () => { if (confirm("Ученик больше не ходит в эту группу?")) enPatch(+b.dataset.enoff, { active: false }); }));
  if ($("#en-add")) $("#en-add").onclick = async () => {
    if (!$("#en-group").value) return toast("Выберите группу", true);
    try { await api("POST", `/api/students/${id}/enrollments`, { group_id: +$("#en-group").value, tariff: $("#en-tariff").value }); toast("Группа добавлена"); reload(); } catch (e) { fail(e); }
  };
  if ($("#f-trial")) $("#f-trial").onchange = (e) => patch({ trial_at: e.target.value });
  $("#f-source").onchange = (e) => patch({ source_id: e.target.value ? +e.target.value : "" });
  body.querySelectorAll("[data-open]").forEach((b) => (b.onclick = () => openStudent(+b.dataset.open)));
  $("#add-sibling").onclick = () => openNewClient({ family_id: s.family_id, family_name: d.family.name });
  loadFamilyComms(s.family_id, d.parents, id, { id, name: s.name, funnel: s.funnel, birth: s.birth, onBooked: reload });
  if (document.querySelector(".sc-tasks")) wireCardTasks(document.querySelector(".sc-tasks"), reload, id);
  // кем приходится ребёнку: нажали на метку, выбрали из списка
  document.querySelectorAll("[data-rel]").forEach((b) => (b.onclick = () => {
    const REL = ["мама", "папа", "бабушка", "дедушка", "тётя", "дядя", "сестра", "брат", "няня", "опекун", "другое"];
    const sel = document.createElement("select");
    sel.className = "rel-chip";
    sel.innerHTML = `<option value="">кем приходится?</option>` + REL.map((r) => `<option ${b.textContent === r ? "selected" : ""}>${r}</option>`).join("");
    b.replaceWith(sel); sel.focus();
    sel.onchange = async () => { try { await api("PATCH", `/api/parents/${b.dataset.rel}/relation`, { relation: sel.value }); toast("Сохранено"); reload(); } catch (e) { fail(e); } };
  }));
  body.querySelectorAll(".sc-head [data-dial]").forEach((b) => (b.onclick = () => dial(b.dataset.dial)));
  const profile=(add=false,focus='name')=>{if(full)openClientProfile(d,body.querySelector('#sc-profile-edit'),reload,add,focus)};
  const editChild = body.querySelector('[data-edit-child]');
  if (editChild) editChild.onclick = () => profile();
  const editParameters = body.querySelector('[data-edit-client-parameters]');
  if (editParameters) editParameters.onclick = () => {
    const fields = body.querySelector('[data-client-parameters]');
    fields.disabled = !fields.disabled;
    editParameters.textContent = fields.disabled ? 'Редактировать параметры' : 'Закрыть редактирование';
  };
  const editFamily = body.querySelector('[data-edit-family]');
  if (editFamily) editFamily.onclick = () => profile(false, 'note');
  body.querySelectorAll('[data-profile-edit]').forEach(b=>b.onclick=()=>profile(false,b.dataset.profileEdit));
  body.querySelectorAll('[data-copy-parent-phone]').forEach(b=>b.onclick=async e=>{e.stopPropagation();const phone=d.parents[+b.dataset.copyParentPhone]?.phone;if(phone)await copyTextWithNotice(phone,'Номер скопирован','phone')});
  const bindEdit=(selector,callback)=>body.querySelectorAll(selector).forEach(el=>{el.onclick=callback;el.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();callback()}}});
  bindEdit('[data-profile-open]',()=>profile(false,'birth'));bindEdit('[data-profile-note]',()=>profile(false,'note'));
  body.querySelectorAll('[data-client-add]').forEach(b=>b.onclick=()=>{const root=body.querySelector('#sc-profile-edit');root.hidden=false;root.innerHTML='<div class="client-profile-form"><h4>Кого добавить в семью?</h4><div class="sc-actions"><button type="button" class="btn" data-add-contact>Родителя</button><button type="button" class="btn" data-add-family-child>Брата или сестру — клиента</button><button type="button" class="btn" data-add-close>Отмена</button></div></div>';root.querySelector('[data-add-contact]').onclick=()=>profile(true);root.querySelector('[data-add-family-child]').onclick=()=>$('#add-sibling').click();root.querySelector('[data-add-close]').onclick=()=>{root.hidden=true;root.replaceChildren()}});
  bindEdit('[data-head-group]',()=>{if(isMember){scTab('abon');const move=body.querySelector('[data-sub-detail-month]:not([hidden]) [data-sub-transfer]');if(move){move.click();move.scrollIntoView({block:'center'})}else toast('Сначала выберите действующий абонемент для перевода',true)}else{scTab('info');openFold(body,'family');body.querySelector('[data-edit-client-parameters]')?.focus()}});
  const firstPhone = (d.parents.find(p=>p.is_primary&&p.phone)||d.parents.find(p=>p.phone)||{}).phone;
  if ($("#act-call")) $("#act-call").onclick = () => dial(firstPhone);
  if ($("#act-sell")) $("#act-sell").onclick = () => openSell(d, reload);
  const previousButton = body.querySelector('#add-previous-sub');
  if (previousButton) previousButton.onclick = () => {
    const root = body.querySelector('#previous-sub-form');
    const groups = d.enrollments.filter(e => !e.program_id && e.lesson_kind !== 'ofp');
    previousButton.hidden = true;
    root.innerHTML = `<form class="client-profile-form">
      <h4>Прошлый абонемент</h4>
      <p class="small muted">Посещения отметьте в журнале. Платёж и чек не создаются</p>
      <label class="field">Месяц<input name="month" type="month" value="${esc(d.cur_month)}" max="${esc(d.cur_month)}" required></label>
      <label class="field">Группа<select name="group" required>${groups.map(e=>`<option value="${e.group_id}">${esc([e.branch_name,e.group_name].filter(Boolean).join(' · '))}</option>`).join('')}</select></label>
      <label class="field">Тариф<select name="tariff" required><option value="">Выберите тариф</option><option value="flex">Гибкий</option><option value="fixed">Негибкий</option></select></label>
      <label class="field">Количество занятий<input name="lessons" type="number" min="1" max="62" required></label>
      <label class="field">Стоимость абонемента, ₽<input name="price" type="number" min="0" max="1000000" required></label>
      <div class="sc-actions"><button class="btn primary" type="submit">Сохранить</button><button class="btn" type="button" data-close>Отмена</button></div>
      <div class="need-err" role="alert"></div></form>`;
    const form = root.querySelector('form');
    const save = form.querySelector('[type="submit"]');
    const close = form.querySelector('[data-close]');
    const syncTariff = () => {
      const group = groups.find(item => item.group_id === Number(form.elements.group.value));
      form.elements.tariff.options[1].disabled = Boolean(group?.fixed_only);
      if (group?.fixed_only) form.elements.tariff.value = 'fixed';
    };
    form.elements.group.onchange = syncTariff;
    syncTariff();
    close.onclick = () => { root.replaceChildren(); previousButton.hidden = false; previousButton.focus(); };
    form.elements.month.focus();
    form.onsubmit = async event => {
      event.preventDefault();
      save.disabled = close.disabled = true;
      const fields = new FormData(form);
      try {
        await api('POST', `/api/students/${id}/previous-subscription`, {
          month: fields.get('month'), group_id: Number(fields.get('group')), tariff: fields.get('tariff'),
          lessons: Number(fields.get('lessons')), price: Number(fields.get('price'))
        });
      } catch (error) {
        form.querySelector('[role="alert"]').textContent = error.message;
        save.disabled = close.disabled = false;
        return;
      }
      toast('Прошлый абонемент сохранён');
      await reload();
    };
  };
  if ($("#act-vac")) $("#act-vac").onclick = () => openFold(body, "vac");
  if ($("#act-move")) $("#act-move").onclick = () => { openFold(body, "abon"); const b = body.querySelector("[data-entr]"); if (b) { b.click(); b.scrollIntoView({ block: "center", behavior: "smooth" }); } };
  if ($("#act-trial")) $("#act-trial").onclick = () => openTrialBookingHub(d, body.querySelector("#sc-trial-booking"), reload);
  body.querySelectorAll('[data-sub-composition]').forEach(button=>button.onclick=()=>{
    const sub=d.subscriptions.find(x=>x.id===+button.dataset.subComposition),g=S.meta.groups.find(x=>x.id===sub.group_id),box=body.querySelector(`[data-composition-box="${sub.id}"]`);
    const groups=S.meta.groups.filter(x=>x.program_id===g.program_id),ofps=groups.filter(x=>x.lesson_kind==='ofp');
    const options=kind=>groups.filter(x=>x.lesson_kind===kind).map(x=>`<option value="${x.id}" ${x.id===g.id?'selected':''}>${esc(x.branch||'')} · ${esc(x.name)} · ${esc(x.time)}</option>`).join('');
    box.innerHTML=`<form class="subscription-period-form"><h4>Восстановить состав по ERP</h4><p class="small">Стоимость ${rub(sub.price)}, статус и платежи сохраняются. Указывайте подтверждённые даты ERP; фактические отметки не меняются</p><div class="field"><label>Способ сверки</label><select name="mode"><option value="exact">Точные данные ERP — скидки и индивидуальные даты</option><option value="full">Полный месяц по расписанию и базовому прайсу</option></select></div><div data-exact-parts>${['arena','ofp'].map((kind,i)=>`<fieldset><legend>${i?'ОФП':'Арктика'}</legend><div class="field"><label>Группа</label><select name="group_${i}">${options(kind)}</select></div><div class="field"><label>Точные даты ERP через запятую или новую строку</label><textarea name="dates_${i}" placeholder="ДД.ММ.ГГГГ"></textarea></div><div class="field"><label>Фактическая сумма части, ₽</label><input name="amount_${i}" inputmode="numeric"></div><div class="field"><label>Номер исходного абонемента ERP</label><input name="source_${i}" inputmode="numeric"></div></fieldset>`).join('')}</div><div class="field" data-full-part hidden><label>Оплаченная группа ОФП</label><select name="ofp">${ofps.map(x=>`<option value="${x.id}">${esc(x.branch||'')} · ${esc(x.name)} · ${esc(x.time)}</option>`).join('')}</select></div><div class="field"><label>Основание сверки</label><textarea name="reason" required minlength="5" maxlength="1000"></textarea></div><div data-composition-preview></div><p class="need-err" role="alert"></p><div class="sc-actions"><button type="button" class="btn" data-composition-check>Проверить состав</button><button type="submit" class="btn primary" disabled>Сохранить проверенный состав</button><button type="button" class="btn" data-composition-close>Отмена</button></div></form>`;
    const form=box.querySelector('form'),save=form.querySelector('[type=submit]'),error=form.querySelector('.need-err');let checked=null,generation=0;
    const invalidate=()=>{generation++;checked=null;save.disabled=true;form.querySelector('[data-composition-preview]').textContent=''};
    form.addEventListener('input',invalidate);form.addEventListener('change',invalidate);
    form.elements.mode.onchange=()=>{const exact=form.elements.mode.value==='exact';form.querySelector('[data-exact-parts]').hidden=!exact;form.querySelector('[data-full-part]').hidden=exact};
    form.querySelector('[data-composition-check]').onclick=async()=>{const version=++generation;checked=null;save.disabled=true;error.textContent='';const payload=form.elements.mode.value==='exact'?{components:[0,1].map(i=>({group_id:+form.elements[`group_${i}`].value,dates:form.elements[`dates_${i}`].value,amount:form.elements[`amount_${i}`].value,erp_subscription_id:form.elements[`source_${i}`].value}))}:{ofp_group_id:+form.elements.ofp.value};try{const q=await api('POST',`/api/subscriptions/${sub.id}/composition-preview`,payload);if(version!==generation||!form.isConnected)return;checked=q;form.querySelector('[data-composition-preview]').innerHTML=q.components.map(c=>`<p><b>${esc(c.group)}</b>: ${c.dates.length} занятий · ${rub(c.amount)}${c.erp_subscription_id?` · ERP №${esc(c.erp_subscription_id)}`:''}<br>${c.dates.map(dShort).join(', ')}</p>`).join('')+`<p>Было ${q.old_lessons}, будет ${q.lessons} занятий. Сумма: ${rub(q.price)} — совпадает с CRM</p>`;save.disabled=false}catch(e){if(version===generation)error.textContent=e.message}};
    form.onsubmit=async e=>{e.preventDefault();if(!checked||!form.reportValidity())return;save.disabled=true;try{await api('POST',`/api/subscriptions/${sub.id}/composition`,{...(checked.mode==='exact'?{components:checked.components}:{ofp_group_id:checked.ofp_group_id}),token:checked.token,reason:form.elements.reason.value});toast('Состав восстановлен. Сумма и платежи сохранены');reload()}catch(e){error.textContent=e.message;save.disabled=false}};
    form.querySelector('[data-composition-close]').onclick=()=>{generation++;box.replaceChildren()};
  });
  body.querySelectorAll("[data-sub-period]").forEach(button => {
    button.onclick = async () => {
      const sub = d.subscriptions.find(item => item.id === +button.dataset.subPeriod);
      body.querySelectorAll('[data-period-editor]').forEach(el=>el.remove());
      const box=document.createElement('div');box.dataset.periodEditor='';button.closest('.subscription-card').append(box);
      openSubscriptionPeriod(sub,box,reload);
    };
  });
  body.querySelectorAll("[data-chstop]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Остановить автоцепочку для этого клиента? Больше сообщений по ней не уйдёт")) return;
    try { await api("POST", `/api/chain-runs/${b.dataset.chstop}/stop`, {}); toast("Цепочка остановлена"); reload(); } catch (e) { fail(e); }
  }));

  if (isMember) loadWarnBox(s.family_id, body.querySelector("#warn-box"), id);
  if ($("#mk-slots")) loadSlots($("#mk-slots"), async (x) => {
    if (!confirm(`Записать ${s.name} на отработку ${dayLabel(x.date)} ${x.time} в группу ${x.group}?`)) return;
    try { await api("POST", `/api/students/${id}/makeups`, { group_id: x.group_id, date: x.date }); toast("Записан на отработку"); reload(); } catch (e) { fail(e); }
  }, "Или на свободное место в обычной группе (жёлтые освободились, потому что кто-то предупредил):", s.birth);
  if ($("#trial-slots")) {
    if (s.group_id && s.trial_at) api("GET", `/api/occupancy?group_id=${s.group_id}&date=${s.trial_at.slice(0, 10)}`).then((o) => {
      if (o.limit) $("#trial-occ").innerHTML = `На эту тренировку записано <b>${o.count} из ${o.limit}</b>${o.warned.length ? ` (предупредили, что не придут: ${esc(o.warned.join(", "))})` : ""}`;
    }).catch(() => {});
    loadSlots($("#trial-slots"), async (x) => {
      if (!confirm(`Записать на пробное ${dayLabel(x.date)} ${x.time}, группа ${x.group}?`)) return;
      patch({ group_id: x.group_id, trial_at: `${x.date}T${x.time || "18:00"}` });
    }, "Свободные места на пробное (подходят по возрасту):", s.birth);
  }
  if ($("#cm-group")) wireCardMark(id, reload);
  wireSingles(id, reload);
  if ($("#nd-in")) $("#nd-in").onclick = async () => {
    const note = window.prompt("Почему ждём акцию? (необязательно)", "");
    if (note === null) return;
    try { await api("POST", "/api/nodeadline/add", { student_id: id, funnel: "promo", note }); toast("В воронке «Ждут акцию»"); reload(); } catch (e) { fail(e); }
  };
  if ($("#nd-out")) $("#nd-out").onclick = async () => {
    try { await api("POST", "/api/nodeadline/remove", { student_id: id, why: "вручную" }); toast("Убран из воронки"); reload(); } catch (e) { fail(e); }
  };
  if ($("#mk-book")) $("#mk-book").onclick = async () => {
    try { await api("POST", `/api/students/${id}/makeups`, { group_id: +$("#mk-group").value, date: $("#mk-date").value }); toast("Записан на отработку"); reload(); } catch (e) { fail(e); }
  };
  body.querySelectorAll("[data-mk]").forEach((b) => (b.onclick = async () => {
    try { await api("POST", `/api/makeups/${b.dataset.mk}/status`, { status: b.dataset.st }); reload(); } catch (e) { fail(e); }
  }));

  const loadQuotes = () => body.querySelectorAll("[data-quote]").forEach(async (box) => {
    const eid = box.dataset.quote;
    try {
      const q = await api("GET", `/api/students/${id}/quote?month=${$("#q-month").value}&enrollment_id=${eid}${$("#q-disc").value ? "&discount_id=" + $("#q-disc").value : ""}`);
      box.classList.remove("muted");
      box.innerHTML = q.program ? programQuoteHtml(q) : quoteHtml(q);
    } catch (e) { box.textContent = e.message; }
  });
  wireProgramBlock(body, id, reload);
  wireWaitlistCard(body, id, reload);
  wirePuCard(body, id, reload);
  if ($("#q-month")) { $("#q-month").onchange = loadQuotes; $("#q-disc").onchange = loadQuotes; loadQuotes(); }
  body.querySelectorAll('[data-agreed-invoice]').forEach(button => button.onclick = async () => {
    const price = prompt('Согласованная стоимость, ₽', button.dataset.price);
    if (price === null) return;
    const reason = prompt('Причина и с кем согласовали условия');
    if (reason === null) return;
    button.disabled = true;
    try {
      await api('POST', `/api/subscriptions/${button.dataset.agreedInvoice}/individual-terms`, {price: Number(price), reason});
      toast('Индивидуальные условия сохранены');
      reload();
    } catch (error) { fail(error); button.disabled = false; }
  });
  body.querySelectorAll('[data-cancel-unpaid]').forEach(button => button.onclick = async () => {
    const reason = prompt('Укажите причину отмены неоплаченного счёта');
    if (reason === null) return;
    button.disabled = true;
    try {
      await api('POST', `/api/subscriptions/${button.dataset.cancelUnpaid}/cancel-unpaid`, {reason});
      toast('Неоплаченный счёт отменён');
      reload();
    } catch (error) { fail(error); button.disabled = false; }
  });
  body.querySelectorAll("[data-corr]").forEach((b) => (b.onclick = async () => {
    const price = prompt(`Корректировка цены (сейчас ${rub(+b.dataset.price)}). Новая сумма, ₽:`, b.dataset.price);
    if (price === null) return;
    const comment = prompt("Почему? Причина попадёт в отчёт руководителю");
    if (comment === null) return;
    try { await api("POST", `/api/subscriptions/${b.dataset.corr}/correct`, { price: +price, comment }); toast("Цена скорректирована"); reload(); } catch (e) { fail(e); }
  }));
  body.querySelectorAll("[data-inv]").forEach((b) => (b.onclick = async () => {
    if(b.disabled)return;b.disabled=true;
    b.dataset.invoiceRequestId ||= window.crypto?.randomUUID?.()||Date.now()+'-'+Math.random().toString(36).slice(2);
    try { const r = await api("POST", `/api/students/${id}/invoice`, { month: $("#q-month").value, enrollment_id: +b.dataset.inv, discount_id: $("#q-disc").value || null,use_balance:false,request_id:b.dataset.invoiceRequestId }); toast(`Счёт выставлен: ${rub(r.quote.total)}`); reload(); } catch (e) { fail(e);b.disabled=false; }
  }));
  const payForm = (title, amount, onSubmit) => {
    $("#pay-form").innerHTML = `<div class="quote" style="margin-top:10px"><b>${esc(title)}</b>
      <div class="row" style="margin-top:8px"><div class="field"><label>Сумма, ₽</label><input id="pf-sum" type="number" min="1" value="${amount || ""}"></div>
        <div class="field"><label>Способ</label><select id="pf-m">${Object.entries(PAY_METHODS).filter(([k]) => PAY_MANUAL_METHODS.includes(k)).map(([k, t]) => `<option value="${k}">${t}</option>`).join("")}</select></div></div>
      <div class="field"><label>Комментарий</label><input id="pf-c" placeholder="Необязательно"></div>
      <button class="btn primary sm" id="pf-ok">Принять ${amount ? rub(amount) : "оплату"}</button></div>`;
    $("#pf-sum").oninput = () => { $("#pf-ok").textContent = "Принять " + rub($("#pf-sum").value); };
    $("#pf-ok").onclick = async () => { try { await onSubmit(+$("#pf-sum").value, $("#pf-m").value, $("#pf-c").value); toast("Оплата принята"); reload(); } catch (e) { fail(e); } };
    $("#pay-form").scrollIntoView({ block: "center", behavior: "smooth" });
  };
  body.querySelectorAll("[data-pay]").forEach((b) => (b.onclick = () => payForm("Оплата счёта", +b.dataset.left,
    (amount, method) => api("POST", `/api/subscriptions/${b.dataset.pay}/pay`, { amount, method }))));
  wireRefundRequests(body,()=>openStudent(id,{tab:'abon'}));
  body.querySelectorAll('[data-fiscal-check]').forEach(b=>b.onclick=async()=>{
    b.disabled=true;try{const r=await api('POST',`/api/payments/${b.dataset.fiscalCheck}/fiscal-check`,{});const panel=b.parentElement,pill=panel.querySelector('.pill');pill.textContent=r.label;pill.className='pill '+(r.status==='confirmed'?'green':r.status==='failed'?'red':'yellow');panel.querySelector('[data-fiscal-last-check]').textContent=r.checked_at?'Проверено: '+subscriptionPaymentMoment(r.checked_at):'';panel.querySelector('[data-fiscal-note]').textContent=r.note||''}catch(e){fail(e)}finally{b.disabled=false}
  });
  body.querySelectorAll('[data-manual-payment-check]').forEach(b=>b.onclick=async()=>{
    b.disabled=true;const panel=b.parentElement.querySelector('[data-manual-check-panel]');
    try{
      const r=await api('POST',`/api/payments/${b.dataset.manualPaymentCheck}/manual-check/refresh`,{});
      const bank=(r.bank_candidates||[]),receipts=(r.receipt_candidates||[]);
      panel.innerHTML=`${(r.refresh_errors||[]).map(x=>`<p class="need-err small">${esc(x)}</p>`).join('')}
        <div class="manual-check-columns"><fieldset><legend>Поступление в Точку</legend>${bank.length?bank.map((x,i)=>`<label class="manual-check-option"><input type="radio" name="manual-bank-${r.payment_id}" value="${esc(x.id)}" ${bank.length===1?'checked':''}><b>${esc(dShort(x.day))} · ${rub(x.amount)}</b><span>${esc(x.payer||'Плательщик не указан')}</span><span>${esc(x.purpose||'Назначение не указано')}</span></label>`).join(''):'<p class="small muted">Подходящих поступлений пока не найдено</p>'}</fieldset>
        <fieldset><legend>Чек Эвотор в ОФД</legend>${receipts.length?receipts.map(x=>`<label class="manual-check-option"><input type="radio" name="manual-receipt-${r.payment_id}" value="${x.id}" ${receipts.length===1?'checked':''}><b>${esc(subscriptionPaymentMoment(x.at))} · ${rub(x.amount)}</b><span>ФН ${esc(x.fn)} · ФД ${esc(x.fd)} · ФП ${esc(x.fp)}</span></label>`).join(''):'<p class="small muted">Подходящих чеков пока не найдено</p>'}</fieldset></div>
        <p class="small muted">Выберите фактическое поступление и соответствующий чек. Одна сумма без вашего подтверждения не связывается автоматически</p><button type="button" class="btn sm primary" data-manual-bind ${!bank.length||!receipts.length?'disabled':''}>Подтвердить обе связи</button>`;
      const bind=panel.querySelector('[data-manual-bind]');if(bind)bind.onclick=async()=>{const bankId=panel.querySelector(`input[name="manual-bank-${r.payment_id}"]:checked`)?.value,receiptId=panel.querySelector(`input[name="manual-receipt-${r.payment_id}"]:checked`)?.value;if(!bankId||!receiptId)return fail(new Error('Выберите поступление и чек'));bind.disabled=true;try{await api('POST',`/api/payments/${r.payment_id}/manual-check/bind`,{bank_event_id:bankId,receipt_id:+receiptId});toast('Поступление Точки и чек Эвотор подтверждены');reload()}catch(e){fail(e);bind.disabled=false}};
    }catch(e){fail(e)}finally{b.disabled=false}
  });
  if ($("#sub-more")) $("#sub-more").onclick = (e) => { body.querySelector(".sub-list").classList.remove("folded"); e.target.remove(); };
  if ($("#pay-more")) $("#pay-more").onclick = (e) => { body.querySelector(".pay-list").classList.remove("folded"); e.target.remove(); };
  loadClientPaymentLinks(id);
  body.querySelectorAll("[data-pconf]").forEach((b) => (b.onclick = async () => { try { await api("POST", `/api/payments/${b.dataset.pconf}/confirm`, {}); reload(); } catch (e) { fail(e); } }));
  body.querySelectorAll("[data-pcancel]").forEach((b) => (b.onclick = async () => { if (!confirm("Отменить платёж? Он останется в реестре как отменённый.")) return; try { await api("POST", `/api/payments/${b.dataset.pcancel}/cancel`, {}); reload(); } catch (e) { fail(e); } }));
  body.querySelectorAll("[data-pcanc]").forEach(b=>b.onclick=()=>openSubscriptionCancel(+b.dataset.pcanc,b,reload));
  body.querySelectorAll('[data-refund-confirm]').forEach(b=>b.onclick=()=>confirmSubscriptionRefund(+b.dataset.refundConfirm,b,d,reload));
  body.querySelectorAll("[data-cert]").forEach((x) => (x.onchange = async () => {
    try { await api("POST", `/api/attendance/${x.dataset.cert}/cert`, { cert: x.checked }); toast(x.checked ? "Справка отмечена" : "Справка снята"); } catch (e) { fail(e); x.checked = !x.checked; }
  }));
  if ($("#bal-referral")) $("#bal-referral").onclick = () => openReferralBonus(id, reload);
  if ($("#bal-topup")) $("#bal-topup").onclick = () => {
    const requestKey=window.crypto?.randomUUID?.()||Date.now()+"-"+Math.random().toString(36).slice(2);
    $("#pay-form").innerHTML=`<form class="quote balance-topup-form" style="margin-top:10px">
      <div class="row"><b class="grow">Пополнение баланса</b><button type="button" class="icon-btn" data-balance-topup-close aria-label="Закрыть">✕</button></div>
      <div class="field"><label>Основание</label><select name="basis"><option value="payment">Новая оплата — деньги получены сейчас</option><option value="historical">Старые данные — было оплачено ранее</option></select></div>
      <div class="row"><div class="field"><label>Сумма, ₽</label><input name="amount" type="number" min="1" step="1" required></div>
        <div class="field"><label>Фактическая дата</label><input name="paid_at" type="date" value="${S.meta.today}" max="${S.meta.today}" required></div></div>
      <div class="field" data-topup-method><label>Как получены деньги</label><select name="method">${Object.entries(PAY_METHODS).filter(([k])=>PAY_MANUAL_METHODS.includes(k)).map(([k,v])=>`<option value="${k}">${esc(v)}</option>`).join("")}</select></div>
      <div class="field"><label>Комментарий</label><textarea name="comment" rows="2" maxlength="1000" placeholder="Для старых данных обязательно укажите источник информации"></textarea></div>
      <p class="small muted" data-topup-hint>Это новая оплата. Она попадёт в реестр на указанную дату и потребует обычного подтверждения/чека по выбранному способу</p>
      <p class="small">Вся сумма останется на свободном балансе. Выставленные счета не оплачиваются автоматически — зачесть баланс можно только отдельным явным действием</p>
      <p class="need-err small" data-topup-error></p><button class="btn primary" type="submit">Пополнить баланс</button></form>`;
    const form=$("#pay-form form"),basis=form.elements.basis,method=form.querySelector("[data-topup-method]"),hint=form.querySelector("[data-topup-hint]");
    const sync=()=>{const historical=basis.value==="historical";method.hidden=historical;form.elements.comment.required=historical;form.elements.paid_at.max=historical?addDays(S.meta.today,-1):S.meta.today;if(historical&&form.elements.paid_at.value>=S.meta.today)form.elements.paid_at.value=addDays(S.meta.today,-1);hint.textContent=historical?"Новая оплата и новый чек не создаются. Запись сохраняется прошедшей датой и не входит в сегодняшнюю выручку":"Это новая оплата. Она попадёт в реестр на указанную дату и потребует обычного подтверждения/чека по выбранному способу"};
    basis.onchange=sync;sync();form.querySelector("[data-balance-topup-close]").onclick=()=>$("#pay-form").replaceChildren();
    form.onsubmit=async e=>{e.preventDefault();const button=form.querySelector("[type=submit]"),error=form.querySelector("[data-topup-error]");button.disabled=true;error.textContent="";try{const r=await api("POST",`/api/students/${id}/balance-topup`,{basis:basis.value,amount:+form.elements.amount.value,paid_at:form.elements.paid_at.value,method:form.elements.method.value,comment:form.elements.comment.value,request_key:requestKey});toast(`Сохранено. Свободный баланс: ${rub(r.balance.deposit)}`);reload()}catch(err){error.textContent=err.message;button.disabled=false}};
    form.scrollIntoView({block:"center",behavior:"smooth"});form.elements.amount.focus();
  };
  if ($("#pay-fam")) $("#pay-fam").onclick = () => payForm("Оплата за семью: закроет счета всех детей, остаток на баланс", 0,
    async (amount, method, comment) => { const r = await api("POST", `/api/students/${id}/pay-family`, { amount, method, comment });
      toast(r.parts.map((p) => `${p.name} ${rub(p.amount)}`).join(", ")); });
  if ($("#bal-move")) $("#bal-move").onclick = async () => {
    const kids = d.family_kids, pick = kids.length === 1 ? kids[0] : kids.find((k) => k.name === prompt("Кому перенести? " + kids.map((k) => k.name).join(", ")));
    if (!pick) return;
    const sum = prompt(`Сколько перенести на ${pick.name}? На балансе ${rub(d.balance.deposit)}`, d.balance.deposit);
    if (!sum) return;
    try { await api("POST", `/api/students/${id}/move-balance`, { to: pick.id, amount: +sum }); toast("Перенесено"); reload(); } catch (e) { fail(e); }
  };
  if ($("#bal-refund")) $("#bal-refund").onclick = async () => {
    const sum = prompt(`Сколько вернуть родителю? На балансе ${rub(d.balance.deposit)}`, d.balance.deposit); if (!sum) return;
    const comment = prompt("Почему возврат и как вернули деньги?"); if (!comment) return;
    try { await api("POST", `/api/students/${id}/refund`, { amount: +sum, comment }); toast("Возврат записан. Пробейте чек возврата"); reload(); } catch (e) { fail(e); }
  };
  body.querySelectorAll("[data-cancel]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Отменить счёт? Пропуски из перерасчёта вернутся.")) return;
    try { await api("POST", `/api/subscriptions/${b.dataset.cancel}/cancel`, {}); reload(); } catch (e) { fail(e); }
  }));

  $("#v-add").onclick = async () => {
    try { await api("POST", `/api/students/${id}/vacations`, { start: $("#v-start").value, end: $("#v-end").value }); toast("Отпуск добавлен"); reload(); } catch (e) { fail(e); }
  };
  body.querySelectorAll("[data-vdel]").forEach((b) => (b.onclick = async () => {
    try { await api("DELETE", `/api/vacations/${b.dataset.vdel}`); reload(); } catch (e) { fail(e); }
  }));
  if($('#act-delete-error'))$('#act-delete-error').onclick=()=>openErroneousClientDelete(d,body.querySelector('#sc-profile-edit'));
  if($("#act-archive-card"))$("#act-archive-card").onclick=()=>$("#s-arch").click();
  $("#s-arch").onclick = async () => {
    if (!s.archived && !confirm("Перенести карточку в архив? Она пропадёт из воронок, вернуть можно в любой момент")) return;
    try { await api("POST", `/api/students/${id}/archive`, { archived: !s.archived }); toast(s.archived ? "Карточка возвращена" : "Карточка в архиве"); closeDrawer(); render(); } catch (e) { fail(e); }
  };
  for (const edit of body.querySelectorAll("[data-edit-history]")) {
    edit.onclick = () => {
      const note = d.history.find(item => item.id === Number(edit.dataset.editHistory));
      const form = document.createElement("form");
      form.innerHTML = `<label class="field">Текст комментария<textarea rows="6" required></textarea></label>
        <button class="btn primary" type="submit">Сохранить</button>
        <button class="btn" type="button">Отмена</button>
        <button class="btn" type="button" data-archive-note>Убрать из истории</button>`;
      const input = form.querySelector("textarea");
      input.value = note.text;
      edit.hidden = true;
      edit.after(form);
      input.focus();
      form.querySelector('[type="button"]').onclick = () => { form.remove(); edit.hidden = false; };
      form.querySelector("[data-archive-note]").onclick = async () => {
        if (!confirm("Убрать сохранённый комментарий из истории карточки?")) return;
        try {
          await api("POST", `/api/students/${id}/notes/${note.id}/archive`, {original: note.text});
          await openStudent(id);
          toast("Комментарий убран из истории");
        } catch (error) { fail(error); }
      };
      form.onsubmit = async event => {
        event.preventDefault();
        const save = form.querySelector('[type="submit"]');
        save.disabled = true;
        try {
          await api("PATCH", `/api/students/${id}/notes/${note.id}`, {text: input.value, original: note.text});
          await openStudent(id);
          toast("Комментарий сохранён");
        } catch (error) { save.disabled = false; fail(error); }
      };
    };
  }
  $("#note-add").onclick = async () => {
    const t = $("#note").value.trim(); if (!t) return;
    try { await api("POST", `/api/students/${id}/note`, { text: t }); openStudent(id); } catch (e) { fail(e); }
  };
}

/* «Мои ученики» тренера: только просмотр, телефон родителя - позвонить одним нажатием */
async function renderCoachKids(page) {
  const d = await api("GET", "/api/coach/kids");
  const n = d.groups.reduce((a, g) => a + g.kids.length, 0);
  page.innerHTML = `<div class="page-head"><h2 class="grow">Мои ученики</h2><span class="muted small">${n} ${n % 10 === 1 && n % 100 !== 11 ? "ребёнок" : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? "ребёнка" : "детей"}</span></div>
    ${d.groups.length ? d.groups.map((g) => `<div class="section"><h3>${esc(g.name)} <span class="muted small">${esc(g.branch)} · ${esc(g.age || "")}${/\d$/.test(g.age || "") ? " лет" : ""} · ${g.main ? "ты главный тренер" : "ты помощник"}</span></h3>
      <div class="list" style="box-shadow:none">${g.kids.map((k) => `<div class="list-row"><div class="grow"><b>${esc(displayChildName(k.name))}</b>${k.birth ? ` <span class="muted small">${ageText(k.birth)}</span>` : ""}</div>
        <div class="small" style="text-align:right">${k.parents.map((p) => `${esc(p.name || "Родитель")}${p.phone ? ` <a href="tel:${esc(p.phone)}">${esc(fmtPhone(p.phone))}</a>` : ""}`).join("<br>") || `<span class="muted">телефона нет</span>`}</div></div>`).join("")
        || `<div class="list-row muted small">В группе пока нет детей</div>`}</div></div>`).join("")
      : `<div class="panel center" style="padding:40px">За тобой пока не закреплено ни одной группы. Спроси у старшего администратора</div>`}`;
}

function ageText(birth) {
  return ageYM(birth);
}

function reportedAgeText(student) {
  const age = Number(student?.reported_age);
  return !student?.birth && Number.isInteger(age) && age >= 0 && age <= 17
    ? `${age} ${plural(age, "год", "года", "лет")} · со слов промоутера`
    : "";
}

function isoDate(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; }
function nextSaturdays(n) {
  const out = [], d = new Date(); d.setHours(0, 0, 0, 0);
  while (d.getDay() !== 6) d.setDate(d.getDate() + 1);
  for (let i = 0; i < n; i++) { out.push(isoDate(d)); d.setDate(d.getDate() + 7); }
  return out;
}

/* ---------- новый клиент ---------- */
function openNewClient(preset = {}) {
  openDrawer(`
  <h2>${preset.family_id ? "Ребёнок в семью " + esc(preset.family_name) : "Новый клиент"}</h2>
  <form id="nc" class="section">
    ${preset.family_id ? "" : `
      <div class="field"><label>Телефон родителя</label><input name="phone" type="tel" required placeholder="+7 (900) 000 00 00" value="${esc(preset.phone || "")}"></div>
      <div id="dup"></div>
      <div class="field"><label>Имя родителя</label><input name="parent_name" required value="${esc(preset.parent_name || "")}"></div>
      <div class="field"><label>Роль родителя</label><select name="parent_relation"><option value="">— выбрать —</option>${["мама", "папа", "бабушка", "дедушка", "тётя", "дядя", "сестра", "брат", "няня", "опекун", "другое"].map(r=>`<option value="${r}">${r[0].toUpperCase()+r.slice(1)}</option>`).join('')}</select></div>
      <div class="field"><label>Мессенджер</label><select name="messenger"><option>MAX</option><option>Telegram</option><option>WhatsApp</option><option value="">—</option></select></div>`}
    <div class="field"><label>Имя ребёнка</label><input name="name" required></div>
    <div class="row">
      <div class="field"><label>Дата рождения</label><input name="birth" type="date"></div>

    </div>
    <p class="muted small">Группу, пробное занятие и абонемент можно выбрать в карточке после создания клиента</p>
    <div class="field"><label>Откуда узнали</label>${sourceSelect("source_id", null)}</div>
    <div class="field"><label>Комментарий · необязательно</label><textarea name="comment" rows="3" maxlength="2000" placeholder="Что важно знать о клиенте">${esc(preset.comment||'')}</textarea></div>
    <button class="btn primary">Создать</button>
  </form>`);
  let familyId = preset.family_id || null;
  const form = $("#nc");
  splitChildNameFields(form,"name");
  const phone = form.elements.phone;
  if (phone) phone.onblur = async () => {
    const r = await api("GET", "/api/check-phone?phone=" + encodeURIComponent(phone.value)).catch(() => []);
    $("#dup").innerHTML = r.length ? `<div class="quote small">Этот телефон уже есть: ${r.map((f) => `<button type="button" class="chip" data-fam="${f.id}">Семья ${esc(f.name)} (${esc(f.children.join(", "))})</button>`).join(" ")}<div class="muted" style="margin-top:6px">Нажмите на семью, чтобы добавить ребёнка в неё</div></div>` : "";
    $("#dup").querySelectorAll("[data-fam]").forEach((b) => (b.onclick = () => {
      familyId = +b.dataset.fam;
      $("#dup").innerHTML = `<div class="quote small">Ребёнок будет добавлен в выбранную семью</div>`;
      form.elements.parent_name.required = false;
    }));
  };
  form.onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(form));
    if (f.group_id) f.group_id = +f.group_id;
    if (f.source_id) f.source_id = +f.source_id; else delete f.source_id;
    if (familyId) f.family_id = familyId;
    if (preset.conversation_id) f.conversation_id = preset.conversation_id;
    try { const r = await api("POST", "/api/students", f); toast("Клиент создан"); await render(); await openStudent(r.id); }
    catch (err) { fail(err); }
  };
}

/* ---------- журнал ---------- */
/* в списке групп внутри филиала название филиала лишнее (Юлия, 04.11): «Небо Ср/Вс 16:45» → «Ср/Вс 16:45» */
function groupNoBranch(x) {
  const w = (x.name || "").split(" ")[0];
  return w && (x.branch || "").includes(w) && x.name.length > w.length ? x.name.slice(w.length).trim() : x.name;
}
/* весь месяц по группе: сколько детей в каждую дату + крупная плашка «на абонементе» */
async function renderJournalMonth(page, groups) {
  const g = groups.find((x) => x.id === S.journal.group);
  const ym = S.journal.date.slice(0, 7);
  const m = await api("GET", `/api/journal/month?group_id=${g.id}&month=${ym}`);
  const today = S.meta.today;
  const tile = (d) => {
    const past = d.date <= today;
    const wd = WD[(new Date(d.date + "T12:00").getDay() + 6) % 7];
    if (d.closed) return `<div class="jm-day closed"><div class="jm-date">${dShort(d.date)} <span class="muted">${wd}</span></div><div class="muted small">Праздник, занятия нет</div></div>`;
    const unmarked = past ? d.list - d.present - d.absent : 0;
    return `<button class="jm-day ${d.date === today ? "today" : ""}" data-jmday="${d.date}">
      <div class="jm-date">${dShort(d.date)} <span class="muted">${wd}</span></div>
      <div class="jm-big">${past ? d.present + d.trials_came : d.list + d.trials}<span> ${past ? "были" : "ждём"}</span></div>
      <div class="small muted">${[past ? `по списку ${d.list}` : "", past && d.absent ? `пропуск ${d.absent}` : "", d.vacation ? `отпуск ${d.vacation}` : "",
        d.trials ? `пробных ${d.trials}` : "", d.makeups ? `отработка ${d.makeups}` : "", unmarked > 0 ? `<b class="red">не отмечено ${unmarked}</b>` : ""].filter(Boolean).join(" · ")}</div>
    </button>`;
  };
  const sb = m.subs;
  page.innerHTML = `${journalDayHead()}
    <div class="row" style="max-width:720px;margin-bottom:14px">
      ${jBranchSel(groups, g.branch_id)}
      <div class="field"><label>Группа</label><select id="j-group">${groups.filter((x) => x.branch_id === g.branch_id).map((x) => `<option value="${x.id}" ${x.id === S.journal.group ? "selected" : ""}>${esc(groupNoBranch(x))}</option>`).join("")}</select></div>
      <div class="field" style="flex:0 0 170px"><label>Месяц</label><input id="j-month" type="month" value="${ym}"></div>
    </div>
    <div class="jm-sub">
      <div class="jm-sub-n">${sb.paid}</div>
      <div><b>${plural(sb.paid, "ребёнок", "ребёнка", "детей")} на абонементе</b>
        ${sb.single ? `<div><b>${sb.single} ${plural(sb.single, "разовик", "разовика", "разовиков")}</b></div>` : ""}</div>
    </div>
    ${m.days.length ? `<div class="jm-grid">${m.days.map(tile).join("")}</div>` : `<div class="muted">В этом месяце у группы нет занятий</div>`}`;
  wireJournalDayHead(page);
  page.querySelector("#j-group").onchange = (e) => step(() => { S.journal.group = +e.target.value; });
  const jb = page.querySelector("#j-branch");
  if (jb) jb.onchange = (e) => step(() => { const f = groups.find((x) => x.branch_id === +e.target.value && !x.is_makeup) || groups.find((x) => x.branch_id === +e.target.value); if (f) S.journal.group = f.id; });
  page.querySelector("#j-month").onchange = (e) => { if (e.target.value) step(() => { S.journal.date = e.target.value + "-01"; }); };
  page.querySelectorAll("[data-jmday]").forEach((b) => (b.onclick = () => step(() => { S.journal.mode = "group"; S.journal.date = b.dataset.jmday; })));
}

function jBranchSel(groups, branchId) {
  const br = [...new Map(groups.map((x) => [x.branch_id, { id: x.branch_id, name: x.branch }])).values()];
  return br.length ? `<div class="field" style="flex:0 0 200px"><label>Филиал</label><select id="j-branch">${br.map((b) => `<option value="${b.id}" ${b.id === branchId ? "selected" : ""}>${esc(b.name)}</option>`).join("")}</select></div>` : "";
}

async function renderJournal(page) {
  const groups = S.meta.groups;
  if (!groups.length) { page.innerHTML = `<h2>Журнал посещений</h2><p class="muted">За вами пока не закреплено ни одной группы. Попросите администратора</p>`; return; }
  if (S.journal.group && !groups.some((x) => x.id === S.journal.group)) S.journal.group = null;
  if (S.journal.mode === "month" && !S.journal.group) S.journal.group = (groups.find((x) => x.branch_id === S.journal.branch) || groups[0]).id;
  if (!S.journal.date) S.journal.date = S.meta.today;
  if (S.journal.mode === "dayall") return renderJournalDay(page, groups);
  if (S.journal.mode === "month") return renderJournalMonth(page, groups);
  // в списке только группы, у которых в выбранный день есть занятие (Юлия, 04.11)
  const wdSel = (new Date(S.journal.date + "T12:00").getDay() + 6) % 7;
  const confirmedGroups=await api('GET',`/api/journal/lesson-groups?date=${S.journal.date}`).catch(()=>null);
  const dayGroups = groups.filter(x=>confirmedGroups?confirmedGroups.group_ids.includes(x.id):String(x.weekdays||'').split(',').map(Number).includes(wdSel));
  if (!groups.some(x=>x.branch_id===S.journal.branch)) S.journal.branch=groups[0].branch_id;
  if (!dayGroups.length) {
    page.innerHTML = `${journalDayHead()}<div class="row" style="max-width:640px;margin-bottom:14px">
      ${jBranchSel(groups,S.journal.branch)}
      <div class="field" style="flex:0 0 170px"><label>Дата</label><input id="j-date" type="date" value="${S.journal.date}"></div></div>
      <div class="muted">${dShort(S.journal.date)} занятий нет ни в одном филиале. Выберите другую дату</div>`;
    wireJournalDayHead(page);
    $("#j-branch").onchange=e=>step(()=>{S.journal.branch=+e.target.value;S.journal.group=null;});
    $("#j-date").onchange = (e) => { step(() => { S.journal.date = e.target.value; }); };
    return;
  }
  // у выбранной группы в этот день занятия нет или группа не выбрана: ничего не открываем, просим выбрать (Юлия, 04.11)
  if (!dayGroups.some((x) => x.id === S.journal.group)) {
    const was = groups.find((x) => x.id === S.journal.group);
    if (was) S.journal.branch = was.branch_id;
    S.journal.group = null;
    const brs = [...new Map(groups.map((x) => [x.branch_id, { id: x.branch_id, name: x.branch }])).values()];
    const bid = brs.some((b) => b.id === S.journal.branch) ? S.journal.branch : brs[0].id;
    const inBr = dayGroups.filter((x) => x.branch_id === bid);
    page.innerHTML = `${journalDayHead()}<div class="row" style="max-width:640px;margin-bottom:14px">
      ${brs.length ? `<div class="field" style="flex:0 0 200px"><label>Филиал</label><select id="j-branch">${brs.map((b) => `<option value="${b.id}" ${b.id === bid ? "selected" : ""}>${esc(b.name)}</option>`).join("")}</select></div>` : ""}
      <div class="field"><label>Группа</label><select id="j-group"><option value="">Выберите группу</option>${inBr.map((x) => `<option value="${x.id}">${esc(groupNoBranch(x))}</option>`).join("")}</select></div>
      <div class="field" style="flex:0 0 170px"><label>Дата</label><input id="j-date" type="date" value="${S.journal.date}"></div></div>
      <div class="muted">${was ? `У группы ${esc(groupNoBranch(was))} ${dShort(S.journal.date)} занятия нет. ` : ""}${inBr.length ? "Выберите группу" : "В выбранном филиале на эту дату занятий нет. Выберите другую дату или филиал."}</div>`;
    wireJournalDayHead(page);
    $("#j-group").onchange = (e) => { if (e.target.value) step(() => { S.journal.group = +e.target.value; }); };
    if ($("#j-branch")) $("#j-branch").onchange = (e) => step(() => { S.journal.branch = +e.target.value; S.journal.group = null; });
    $("#j-date").onchange = (e) => { step(() => { S.journal.date = e.target.value; }); };
    return;
  }
  const j = await api("GET", `/api/journal?group_id=${S.journal.group}&date=${S.journal.date}`);
  const g = j.group;
  if (g.branch_id == null) g.branch_id = (groups.find((x) => x.id === g.id) || {}).branch_id;
  const jBranches = [...new Map(groups.map((x) => [x.branch_id, { id: x.branch_id, name: x.branch }])).values()];
  // до начала тренировки вместо «Был / Пропуск» кнопка «Не придёт» (Юлия, 04.11)
  const before = j.has_lesson && (S.journal.date > S.meta.today || (S.journal.date === S.meta.today && new Date().toTimeString().slice(0, 5) < g.time));
  // нумерация: сначала те, кого ждём, потом предупредившие и отпуск; плашка «всего / ждём» (Юлия, 04.11)
  const away = (r) => r.warned || r.status === "vacation";
  j.rows.sort((a, b) => away(a) - away(b));
  j.rows.forEach((r, i) => (r.n = i + 1));
  const jTotal = j.rows.length, jAway = j.rows.filter(away).length;
  const st = j.has_lesson && S.journal.date <= S.meta.today ? await api("GET", `/api/lessons/staff?group_id=${g.id}&date=${S.journal.date}`).catch(() => null) : null;
  page.innerHTML = `
    ${journalDayHead()}${j.has_lesson?journalActionsHtml():''}
    <div class="row" style="max-width:640px;margin-bottom:14px">
      ${jBranches.length ? `<div class="field" style="flex:0 0 200px"><label>Филиал</label><select id="j-branch">${jBranches.map((b) => `<option value="${b.id}" ${b.id === g.branch_id ? "selected" : ""}>${esc(b.name)}</option>`).join("")}</select></div>` : ""}
      <div class="field"><label>Группа</label><select id="j-group">${dayGroups.filter((x) => x.branch_id === g.branch_id).map((x) => `<option value="${x.id}" ${x.id === S.journal.group ? "selected" : ""}>${esc(groupNoBranch(x))}</option>`).join("")}</select></div>
      <div class="field" style="flex:0 0 170px"><label>Дата</label><input id="j-date" type="date" value="${S.journal.date}"></div>
    </div>
    <div class="journal-meta small muted"><span>В списке: ${jTotal}${jAway?` · не придут ${jAway}`:''}${j.overdue_marks?` · не отмечено ${j.overdue_marks.missing}`:''}${j.closed?' · занятие отменено':j.has_lesson?'':' · занятия нет'}</span>
      ${st?staffPicker(st,st.coaches,"j",before,true):''}</div>
    <div class="list">${j.rows.length ? j.rows.map((r) => g.is_makeup && r.makeup_id ? makeupRow(r) : journalRow(r, before)).join("") : `<div class="list-row muted">${g.is_makeup ? "На эту дату никто не записан на отработку" : "В группе пока нет учеников"}</div>`}</div>
    ${(j.hidden_unpaid || []).length ? `<details class="small muted" style="margin-top:10px"><summary>Не оплатили: ${j.hidden_unpaid.length}. В журнал не попадают</summary>
      <div style="margin-top:6px">${j.hidden_unpaid.map((x) => `<span class="${isCoach() ? "" : "click"}" data-who="${x.id}" style="cursor:pointer">${esc(x.name)}</span>`).join(", ")}</div></details>` : ""}`;
  if (st) wireStaffPicker(page, "j", g.id, S.journal.date, render);
  wireJournalDayHead(page);
  if ($("#j-copy")) $("#j-copy").onclick = () => shiftCopy(g.branch_id, S.journal.date);
  if ($("#j-print")) $("#j-print").onclick = () => shiftPrint(g.branch_id, S.journal.date);
  $("#j-group").onchange = (e) => { step(() => { S.journal.group = +e.target.value; }); };
  if ($("#j-branch")) $("#j-branch").onchange = (e) => { step(() => { S.journal.branch = +e.target.value; S.journal.group = null; }); };
  $("#j-date").onchange = (e) => { step(() => { S.journal.date = e.target.value; }); };
  page.classList.toggle("readonly", !can("journal", "full") && !isCoach());
  if (!isCoach()) page.querySelectorAll("[data-who]").forEach((el) => (el.onclick = () => openStudent(+el.dataset.who)));
  page.querySelectorAll("[data-att]").forEach((b) => (b.onclick = async () => {
    try { await api("POST", "/api/attendance", { student_id: +b.dataset.sid, group_id: S.journal.group, date: S.journal.date, status: b.dataset.att }); render(); } catch (e) { fail(e); }
  }));
  page.querySelectorAll("[data-warn]").forEach((b) => (b.onclick = async () => {
    const note = window.prompt("Что сказали родители? (необязательно): заболел, уехали…", "");
    if (note === null) return;
    try { await api("POST", "/api/journal/warn", { student_id: +b.dataset.warn, group_id: S.journal.group, date: S.journal.date, note }); toast("Отмечено: не придёт. Место свободно для отработки"); render(); } catch (e) { fail(e); }
  }));
  page.querySelectorAll("[data-unwarn]").forEach((b) => (b.onclick = async () => {
    try { await api("POST", "/api/journal/warn", { student_id: +b.dataset.unwarn, group_id: S.journal.group, date: S.journal.date, undo: true }); toast("Ждём на тренировке"); render(); } catch (e) { fail(e); }
  }));
  page.querySelectorAll("[data-mk]").forEach((b) => (b.onclick = async () => {
    try { await api("POST", `/api/makeups/${b.dataset.mk}/status`, { status: b.dataset.st }); render(); } catch (e) { fail(e); }
  }));
  page.querySelectorAll("[data-trial]").forEach((b) => (b.onclick = async () => {
    if(b.getAttribute('aria-pressed')==='true'){toast(b.dataset.trial==='1'?'Уже отмечено: пришёл':'Уже отмечено: не пришёл');return;}
    const buttons=[...b.closest('.mark').querySelectorAll('button')];buttons.forEach(button=>button.disabled=true);
    try { await api("POST", `/api/students/${b.dataset.sid}/trial`, { came: b.dataset.trial === "1",expected_date:S.journal.date,expected_group_id:S.journal.group }); toast(b.dataset.trial==='1'?'Отмечено: пришёл':'Отмечено: не пришёл'); render(); } catch (e) { fail(e); } finally {buttons.forEach(button=>button.disabled=false);}
  }));
}

/* возраст рядом с именем (Юлия, 04.11) */
const J_TAGS = { historical: ["прошлый абонемент", ""], paid: ["абонемент", "jt-paid"], single: ["разовое", "jt-single"], unpaid: ["не оплачен", "jt-unpaid"] };
const jTag = (r) => { const t = J_TAGS[r.tag]; return t && !(r.tag === "unpaid" && r.unpaid) ? `<span class="pill ${t[1]}">${t[0]}</span>` : ""; };
const jNum = (r) => r.n ? `<span class="jn">${r.n}.</span>` : "";
/* возраст с месяцами: «3 года 5 мес.» (Юлия, 04.11) */
function ageYM(birth, on) {
  const today = on || new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Moscow" }).format(new Date());
  const b = new Date(String(birth).slice(0, 10) + "T12:00"), n = new Date(today + "T12:00");
  if (!isFinite(b) || !isFinite(n) || b > n) return "";
  const months = (n.getFullYear() - b.getFullYear()) * 12 + n.getMonth() - b.getMonth() - (n.getDate() < b.getDate() ? 1 : 0);
  const y = Math.floor(months / 12), m = months % 12;
  return `${y} ${plural(y, "год", "года", "лет")} ${m} ${plural(m, "месяц", "месяца", "месяцев")}`;
}
const jAge = (r) => r.birth ? ` <span class="small muted">· ${ageYM(r.birth, S.journal.date)}</span>` : "";

function journalRow(r, before) {
  if (r.status === "makeup" || r.makeup_id) return makeupRow({ ...r, status: r.attendance_id ? r.status : r.mk_status, home_group: "отработка" + (r.home_group ? ", из группы " + r.home_group : "") });
  if (r.erp_visit_kind) {
    const kind = r.erp_visit_kind === "makeup" ? "Отработка" : "Разовое занятие";
    return `<div class="list-row journal-entry"><div class="grow click" data-who="${r.id}">${jNum(r)}${esc(displayChildName(r.name))}${jAge(r)} <span class="pill">${kind}</span></div><span>${r.status === "present" ? "Был" : "Пропуск"}</span></div>`;
  }
  if (r.unpaid) return `<div class="list-row journal-entry"><div class="grow click" data-who="${r.id}">${jNum(r)}${esc(displayChildName(r.name))}${jAge(r)}</div><span class="pill unpaid-flag">не оплачен, не допускать</span>${r.status === "present" ? " Был" : r.status === "absent" ? " Пропуск" : ""}</div>`;
  if (r.warned) return `<div class="list-row journal-entry"><div class="grow click" data-who="${r.id}" style="cursor:pointer">${jNum(r)}${esc(displayChildName(r.name))}${jAge(r)} <span class="pill yellow">предупредил, не придёт</span>${r.note ? `<div class="small muted">${esc(r.note)}</div>` : ""}</div>
    <div class="mark">${before ? `<button data-unwarn="${r.id}">Отменить, придёт</button>` : `<button data-att="present" data-sid="${r.id}">Всё-таки был</button>`}</div></div>`;
  if (r.status === "vacation") return `<div class="list-row journal-entry"><div class="grow click" data-who="${r.id}">${jNum(r)}${esc(displayChildName(r.name))}${jAge(r)}</div><span class="pill yellow">Отпуск</span></div>`;
  if (r.historical_trial_id) return `<div class="list-row journal-entry"><div class="grow click" data-who="${r.id}">${jNum(r)}${esc(displayChildName(r.name))}${jAge(r)} <span class="pill jt-trial">ПУ ${esc(r.trial_at.slice(11,16))}</span></div><span>${r.trial_came ? 'Пришёл' : 'Не пришёл'}</span></div>`;
  if (r.status === "trial") return `<div class="list-row journal-entry"><div class="grow click" data-who="${r.id}">${jNum(r)}${esc(displayChildName(r.name))}${jAge(r)} <span class="pill jt-trial">ПУ ${r.trial_at.slice(11, 16)}</span></div>
    <div class="mark"><button data-trial="1" data-sid="${r.id}" aria-pressed="${r.trial_came === 1}" class="${r.trial_came === 1 ? "on-present" : ""}">Пришёл</button><button data-trial="0" data-sid="${r.id}" aria-pressed="${r.trial_came === 0}" class="${r.trial_came === 0 ? "on-absent" : ""}">Не пришёл</button></div></div>`;
  const locked = r.resolved ? `<span class="pill">${r.resolved === "credit" ? "в перерасчёте" : "отработан"}</span>` : "";
  if (before) return `<div class="list-row journal-entry"><div class="grow click" data-who="${r.id}" style="cursor:pointer">${jNum(r)}${esc(displayChildName(r.name))}${jAge(r)} ${jTag(r)}</div>
    <div class="mark"><button data-warn="${r.id}" title="Родители заранее сказали, что не придёт">🙅 Не придёт</button></div></div>`;
  return `<div class="list-row journal-entry"><div class="grow click" data-who="${r.id}" style="cursor:pointer">${jNum(r)}${esc(displayChildName(r.name))}${jAge(r)} ${jTag(r)} ${locked}${unpaidPill}</div>
    <div class="mark">
      <button data-att="present" data-sid="${r.id}" class="${r.status === "present" ? "on-present" : ""}">Был</button>
      <button data-att="absent" data-sid="${r.id}" class="${r.status === "absent" ? "on-absent" : ""}">Пропуск</button>
    </div></div>`;
}

function makeupRow(r) {
  const mark=r.status==='present'?'done':r.status==='absent'?'missed':r.status;
  return `<div class="list-row journal-entry"><div class="grow click" data-who="${r.id}" style="cursor:pointer">${jNum(r)}${esc(displayChildName(r.name))}${jAge(r)} <span class="pill jt-makeup">отработка</span>
      <div class="small muted">${esc(r.home_group || "")} · за пропуск ${dShort(r.absence_date)}</div></div>
    <div class="mark">
      <button data-mk="${r.makeup_id}" data-st="done" class="${mark === "done" ? "on-present" : ""}">Пришёл</button>
      <button data-mk="${r.makeup_id}" data-st="missed" class="${mark === "missed" ? "on-absent" : ""}">Не пришёл</button>
    </div></div>`;
}

/* история посещений в карточке: за этот месяц коротко цифрами, ниже список дат */
function visitsHtml(v, cur) {
  if (!v.length) return `<div class="small muted" style="margin-top:8px">Отметок за последние 3 месяца нет</div>`;
  const m = v.filter((x) => x.date.slice(0, 7) === cur);
  const st = (x) => x.warned ? ["предупредил, не пришёл", "yellow"] : x.status === "present" ? ["был", "green"] : ["пропуск", "red"];
  return `<div class="small" style="margin:10px 0 6px">В этом месяце: был <b>${m.filter((x) => x.status === "present").length}</b>, пропусков <b>${m.filter((x) => x.status === "absent").length}</b></div>
    <ul class="history visits">${v.map((x) => `<li><b>${dShort(x.date)}</b> ${WD[(new Date(x.date + "T12:00").getDay() + 6) % 7]} · ${esc(groupNoBranch({ name: x.group_name, branch: x.branch }))}
      <span class="pill ${st(x)[1]}">${st(x)[0]}</span>${x.resolved ? ` <span class="pill">${x.resolved === "credit" ? "в перерасчёте" : "отработан"}</span>` : ""}${x.note ? ` <span class="small muted">${esc(x.note)}</span>` : ""}</li>`).join("")}</ul>`;
}

/* отметка посещения прямо из карточки (Юлия, 04.11): ближайшие тренировки группы ребёнка, «Был / Пропуск» или «Не придёт» */
function wireCardMark(sid, reload) {
  const gSel = $("#cm-group"), dSel = $("#cm-date");
  const fillDates = () => {
    const wd = (gSel.selectedOptions[0].dataset.wd || "").split(",").filter((x) => x !== "").map(Number);
    const t = new Date(S.meta.today + "T12:00"), out = [];
    for (let i = -14; i <= 14; i++) { const x = new Date(t); x.setDate(t.getDate() + i); if (wd.includes((x.getDay() + 6) % 7)) out.push(isoDate(x)); }
    const near = out.filter((x) => x <= S.meta.today).pop() || out[0];
    dSel.innerHTML = out.map((x) => `<option value="${x}" ${x === near ? "selected" : ""}>${dShort(x)} ${WD[(new Date(x + "T12:00").getDay() + 6) % 7]}${x === S.meta.today ? " (сегодня)" : ""}</option>`).join("");
  };
  const show = async () => {
    const gid = +gSel.value, day = dSel.value;
    if (!day) { $("#cm-btns").innerHTML = ""; return; }
    let r = null;
    try { r = (await api("GET", `/api/journal?group_id=${gid}&date=${day}`)).rows.find((x) => x.id === sid); } catch (_) {}
    const st = !r ? "нет в журнале на эту дату (не оплачено или не его день)" : r.warned ? "предупредил, не придёт" : r.status === "vacation" ? "в отпуске"
      : r.status === "present" ? "был ✓" : r.status === "absent" ? "пропуск" : "пока не отмечен";
    $("#cm-state").textContent = "Сейчас: " + st;
    const future = day > S.meta.today;
    $("#cm-btns").innerHTML = future
      ? (r && r.warned ? `<button data-cm="unwarn">Отменить, придёт</button>` : `<button data-cm="warn">🙅 Не придёт</button>`)
      : `<button data-cm="present" class="${r && r.status === "present" ? "on-present" : ""}">Был</button><button data-cm="absent" class="${r && r.status === "absent" ? "on-absent" : ""}">Пропуск</button>`;
    $("#cm-btns").querySelectorAll("[data-cm]").forEach((b) => (b.onclick = async () => {
      const a = b.dataset.cm;
      try {
        if (a === "warn") { const note = window.prompt("Что сказали родители? (необязательно)", ""); if (note === null) return; await api("POST", "/api/journal/warn", { student_id: sid, group_id: gid, date: day, note }); }
        else if (a === "unwarn") await api("POST", "/api/journal/warn", { student_id: sid, group_id: gid, date: day, undo: true });
        else await api("POST", "/api/attendance", { student_id: sid, group_id: gid, date: day, status: a });
        toast("Отмечено"); if (reload) reload(); else show();
      } catch (e) { fail(e); }
    }));
  };
  gSel.onchange = () => { fillDates(); show(); };
  dSel.onchange = show;
  fillDates(); show();
}

/* ---------- поиск ---------- */
/* Единый список клиентов с фильтром по статусу ERP. */
S.pf = S.pf || { erp: "" };
async function renderSearch(page) {
  const f = S.pf;
  const d = await api("GET", `/api/people?erp=${encodeURIComponent(f.erp)}&q=${encodeURIComponent(S.search)}${S.searchArch ? "&archived=1" : ""}`);
  page.innerHTML = `
    <div class="page-head"><h2 class="grow">Клиенты</h2>
      <input class="search" id="q" placeholder="Имя, фамилия или телефон" value="${esc(S.search)}">
      <button class="btn sm ${S.searchArch ? "primary" : ""}" id="q-arch">Архив</button></div>
    ${d.erp.length ? `<div class="row" style="max-width:420px;margin-bottom:10px"><div class="field"><label>Статус клиента</label><select id="p-erp"><option value="">Все статусы</option>${d.erp.map(([e, n]) => `<option value="${esc(e)}" ${f.erp === e ? "selected" : ""}>${esc(e==='__erp_missing__'?'Статус не указан':e)} (${n})</option>`).join("")}</select></div></div>` : ""}
    <div class="small muted" style="margin-bottom:6px">Найдено: ${d.total}${d.total > d.limit ? `, показаны первые ${d.limit}. Уточните поиск или фильтр` : ""}</div>
    <div id="results" class="list">${d.rows.length ? d.rows.map((r) => `
      <div class="list-row click" data-who="${r.id}"><div class="grow"><b>${esc(displayChildName(r.name))}</b>
        <div class="small muted">${esc([r.parent, fmtPhone(r.phone), r.group].filter(Boolean).join(" · "))}</div></div>
        <span class="small muted">${esc(r.status_label)}</span></div>`).join("")
      : `<div class="list-row muted">Никого не нашли</div>`}</div>`;
  page.querySelectorAll("[data-who]").forEach((el) => (el.onclick = () => openStudent(+el.dataset.who)));
  if ($("#p-erp")) $("#p-erp").onchange = (e) => { f.erp = e.target.value; render(); };
  let t; $("#q").oninput = (e) => { S.search = e.target.value; clearTimeout(t); t = setTimeout(async () => { await render(); const q = $("#q"); if (q) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); } }, 350); };
  $("#q-arch").onclick = () => { S.searchArch = !S.searchArch; f.erp = ""; render(); };
}

/* ---------- настройки ---------- */
const SET_TABS = [
  ["users", "👥", "Команда", "Должности, телефоны, пароли"], ["prices", "💰", "Цены и правила", "Прайс, скидки, сборная ПРО, дни без занятий"],
  ["structure", "🏟", "Филиалы, группы и смены", "Расписание и лимиты"], ["resp", "🎯", "Ответственные по филиалам", "Кому задачи по пробным"],
  ["comms", "💬", "Связь с клиентами", "Telegram, MAX, WhatsApp, Sipuni, заявки"], ["trials", "📝", "Скрипты", "Сообщения родителям и автосообщения"],
  ["tips", "💡", "Подсказки по этапам", "Что сказать на каждом этапе воронки"], ["chains", "🔁", "Автоцепочки сообщений", "Серии сообщений родителям"],
  ["erp", "📦", "Перенос из ERP", "Клиенты, абонементы, оплаты, история"],
  ["coachpay", "⏱", "Тренеры: приход и оплата", "Отметка прихода, ставки"],
  ["sources", "📣", "Рекламные каналы", "Источники лидов"], ["offers", "📄", "Оферты", "Редакции и ссылки"],
  ["backup", "💾", "Резервные копии", "Яндекс Диск, копия сейчас"], ["archive", "🗄", "Архив", "Вернуть из архива"],
  ["test", "🧪", "Тестовые клиенты", "Выдуманные клиенты для проверки"], ["diag", "🩺", "Проверка соединений", "Почему у кого-то не открывается CRM"],
  ["profile", "👤", "Мой профиль", "Вид, код входа, пароль"],
];

function settingsTiles(page) {
  const parts = (k) => page.querySelectorAll(`[data-tab="${k}"]`);
  const tabs = SET_TABS.filter(([k]) => parts(k).length);
  if (S.setTab && !tabs.some(([k]) => k === S.setTab)) S.setTab = null;
  page.querySelectorAll("[data-tab]").forEach((el) => { el.hidden = el.dataset.tab !== S.setTab; });
  const box = $("#set-tiles");
  const cur = tabs.find(([k]) => k === S.setTab);
  box.innerHTML = cur
    ? `<div class="row" style="align-items:center;gap:10px;margin-bottom:12px"><button class="btn sm" id="set-back" style="flex:0 0 auto;width:auto">← Все настройки</button><b>${cur[1]} ${esc(cur[2])}</b></div>`
    : `<div class="set-tiles">${tabs.map(([k, e, t, d]) => `<button class="set-tile" data-tile="${k}"><span class="set-tile-emoji">${e}</span><b>${esc(t)}</b><span class="small muted">${esc(d)}</span></button>`).join("")}</div>`;
  const navigate = (tab) => {
    S.setTab = tab || null;
    const query = tab ? `?settings_tab=${encodeURIComponent(tab)}` : "";
    try { history.pushState({ view:"settings", sub:navSnap(), depth:navDepth()+1, menu:false, settingsTab:S.setTab }, "", `#settings${query}`); } catch (_) {}
    settingsTiles(page);window.scrollTo(0,0);
  };
  box.querySelectorAll("[data-tile]").forEach((b) => (b.onclick = () => navigate(b.dataset.tile)));
  if ($("#set-back")) $("#set-back").onclick = () => {
    if (history.state?.settingsTab && navDepth()>0) history.back();
    else navigate(null);
  };
}

async function renderSettings(page) {
  const st = S.meta.settings || {}, admin = isOwner();
  const users = admin || can("schedule", "full") ? (await api("GET", "/api/users")).filter((u) => u.active) : [];
  page.innerHTML = `
    <div class="page-head"><h2 class="grow">Настройки</h2><button class="btn sm" id="logout">Выйти</button></div>
    <div style="max-width:780px">
    <div id="set-tiles"></div>
    <div class="section" data-tab="profile"><h3>Внешний вид</h3>
      <label class="check"><input type="checkbox" id="theme-soft" ${document.documentElement.classList.contains("theme-soft") ? "checked" : ""}> Новый дизайн (проба)</label>
      <div class="small muted" style="margin-top:6px">Светлый спокойный вид. Включается только на этом устройстве, выключить можно здесь же в любой момент</div>
      ${can("inbox") ? `<label class="check" style="margin-top:12px"><input type="checkbox" id="sound-on" ${soundOn() ? "checked" : ""}> 🔔 Звук, когда приходит сообщение, пропущенный звонок или заявка</label>
      <div class="row" style="gap:8px;margin-top:6px"><button class="btn sm" id="sound-test" style="flex:0 0 auto">Проверить звук</button>
        ${"Notification" in window && Notification.permission !== "granted" ? `<button class="btn sm" id="notif-on" style="flex:0 0 auto">Показывать уведомления, когда CRM свёрнута</button>` : ""}</div>` : ""}</div>
    <div class="section" data-tab="profile"><h3>Быстрый вход по коду</h3><div id="pin-set" class="small muted">…</div></div>
    <div class="section" data-tab="profile"><h3>Обучение</h3><div class="small muted" style="margin-bottom:8px">Короткая экскурсия по твоим разделам CRM</div>
      <button class="btn sm" id="tour-again">🎓 Пройти обучение ещё раз</button></div>
    ${S.user.role === "admin" ? `<div class="section" data-tab="profile" id="owner-tg"><h3>🔴 Красное в Telegram</h3><div class="small muted">…</div></div>` : ""}
    ${S.user.role === "admin" ? `<div class="section" data-tab="profile"><h3>Праздничное оформление</h3>
      <label class="check"><input type="checkbox" id="festive-on" ${S.meta.festive !== false ? "checked" : ""}> 🎉 Украшать CRM к праздникам по календарю (у всех участников команды)</label>
      <div class="small muted" style="margin-top:4px">Хэллоуин, Новый год, 23 февраля, 8 марта, 1 июня, 1 сентября</div></div>` : ""}
    ${admin ? `
    <form class="section" id="set" data-tab="prices">
      <h3>Цены и правила</h3>
      <div id="price-list" class="small muted" style="margin-bottom:10px">Загружаю прайс…</div>
      <div class="row"><div class="field"><label>Разовое занятие, ₽</label><input name="price_single" type="number" value="${esc(st.price_single || "")}" placeholder="Уточнить цену"></div><div></div></div>
      <div class="row">
        <div class="field"><label>Продлить до числа</label><input name="renewal_deadline_day" type="number" min="1" max="28" value="${esc(st.renewal_deadline_day)}"></div>
        <div class="field"><label>Длительность пробного, мин</label><input name="trial_minutes" type="number" value="${esc(st.trial_minutes)}"></div>
      </div>
      <button class="btn primary">Сохранить</button>
    </form>
    ${can("payments", "full") ? `<div class="section" id="discount-settings" data-tab="prices"></div>` : ""}
    <div id="program-settings" data-tab="prices"></div>
    ${can("members", "full") ? `<div class="section" id="memo-settings" data-tab="prices"></div>` : ""}
    ${S.user.role === "admin" ? `<div class="section" id="tips-settings" data-tab="tips"></div>` : ""}
    ${S.user.role === "admin" ? `<div class="section" id="chains-settings" data-tab="chains"></div>` : ""}
    ${S.user.role === "admin" ? `<div class="section" id="erp-settings" data-tab="erp"></div>` : ""}
    ${S.user.role === "admin" ? `<div class="section" id="tg-import" data-tab="erp"></div>` : ""}
    ${S.user.role === "admin" ? `<div class="section" id="chat-bulk-import" data-tab="erp"></div><div class="section" id="chat-day-import" data-tab="erp"></div>` : ""}
    <div class="section" id="backup-settings" data-tab="backup"></div>
    ${isOwner() ? `<div class="section" id="diag-box" data-tab="diag"></div>` : ""}
    <div class="section" id="resp-settings" data-tab="resp"></div>
    <div class="section" data-tab="users">
      <h3>Команда</h3>
      <div class="list" style="box-shadow:none;margin-bottom:10px">${users.map((u) => `<div class="list-row staff-row"><div class="grow"><span class="who" data-prof="${u.id}">${avatar(u.id, u.name, 34)}<span>${esc(u.name)} <button type="button" class="linkish small muted" data-ulogin="${u.id}" data-login="${esc(u.login)}" data-name="${esc(u.name)}" title="Изменить логин">${esc(u.login)} ✎</button></span></span></div>
        <input data-uph="${u.id}" value="${esc(u.phone || "")}" placeholder="телефон" title="Телефон участника команды" inputmode="tel" style="flex:0 0 130px;padding:5px 8px">
        <input data-tgu="${u.id}" value="${esc(u.tg_username ? "@" + u.tg_username : "")}" placeholder="@telegram" title="Ник в Telegram: CRM отметит участника команды в рабочих чатах" style="flex:0 0 120px;padding:5px 8px">
        ${u.role === "coach" ? "" : `<input data-sip="${u.id}" value="${esc(u.sip_number || "")}" placeholder="вн. номер" title="Внутренний номер Sipuni: на него придёт звонок из карточки" inputmode="numeric" style="flex:0 0 96px;padding:5px 8px">`}
        <select data-upost="${u.id}" title="Должность: от неё зависят разделы и права" style="flex:0 0 auto;width:auto;padding:5px 8px" ${u.id === S.user.id ? "disabled" : ""}>${POSTS.map(([k, t]) => `<option value="${k}" ${postOf(u) === k ? "selected" : ""}>${t}</option>`).join("")}</select>
        <label class="check small" title="Промоутер: права промоутера добавляются к должности" style="flex:0 0 auto"><input type="checkbox" data-uprom="${u.id}" ${u.is_promoter ? "checked" : ""}> промоутер</label>
        ${["operator"].includes(u.role) ? `<label class="check small" title="Отмечает в журнале детей и тренеров, как тренер (например, администратор филиала)" style="flex:0 0 auto"><input type="checkbox" data-ujour="${u.id}" ${u.is_journal ? "checked" : ""}> журнал</label>` : ""}
        ${u.id !== S.user.id ? `<button class="icon-btn" data-ucopy="${u.id}" data-login="${esc(u.login)}" data-name="${esc(u.name)}" title="Скопировать вход: новый пароль и сообщение с логином">📋</button><button class="icon-btn" data-upw="${u.id}" title="Задать новый пароль">🔑</button><button class="icon-btn" data-uarch="${u.id}" title="В архив">🗄</button>` : ""}</div>`).join("")}</div>
      <div class="small muted" style="margin:-4px 0 10px">Внутренний номер Sipuni нужен, чтобы звонить клиенту прямо из карточки</div>
      <form id="nu">
        <div class="row"><div class="field"><label>Имя и фамилия</label><input name="name" autocomplete="off" required></div><div class="field"><label>Телефон</label><input name="phone" type="tel" placeholder="Необязательно"></div></div>
        <div class="field"><label>Должность</label><select name="post">${POSTS.map(([k, t]) => `<option value="${k}" ${k === "operator" ? "selected" : ""}>${t}</option>`).join("")}</select></div>
        <p class="small muted">Логин и случайный пароль CRM создаст сама и покажет после добавления участника команды</p>
        <label class="check small" style="margin-bottom:8px"><input type="checkbox" name="is_promoter" value="1"> Ещё и промоутер</label>
        <button class="btn sm">Добавить участника команды</button>
      </form>
      <details style="margin-top:12px"><summary class="linkish small">Завести команду списком (пароли создаст CRM)</summary>
        <div class="small muted" style="margin:6px 0">По строке на человека: Имя Фамилия; должность; телефон. Должность: администратор, старший администратор, тренер, промоутер, юрист или руководитель. Если человек уже есть, ему просто выдастся новый пароль</div>
        <textarea id="bulk-users" rows="6" placeholder="Ольга Авлахова; администратор; +7 911 831-27-83"></textarea>
        <button class="btn sm primary" id="bulk-go" style="margin-top:6px">Завести и выдать пароли</button>
        <div id="bulk-res"></div></details>
    </div>
    <div class="section" id="structure-settings" data-tab="structure"><h3>Филиалы, группы и смены</h3><div class="muted small">Загружаю…</div></div>
    <div class="section" id="archive-settings" data-tab="archive"><h3>Архив</h3><div class="muted small">Загружаю…</div></div>
    </div>` : can("schedule", "full") ? `<div class="section" id="structure-settings" data-tab="structure"><h3>Филиалы, группы и смены</h3><div class="muted small">Загружаю…</div></div>` : ""}
    <div class="section" id="offers-settings" data-tab="offers"><h3>Оферты</h3><div class="muted small">Загружаю…</div></div>
    ${admin ? `<div class="section" id="source-settings" data-tab="sources"><h3>Рекламные каналы и источники</h3><div class="muted small">Загружаю…</div></div>` : ""}
    ${admin ? `<div class="section" id="staff-settings" data-tab="coachpay"><h3>Тренеры: приход и оплата</h3><div class="muted small">Загружаю…</div></div>` : ""}
    ${admin ? `<div class="section" id="task-settings" data-tab="trials"><h3>Скрипты</h3><div class="muted small">Загружаю…</div></div>` : ""}
    ${admin ? `<div class="section" id="comms-settings" data-tab="comms"><h3>Связь с клиентами</h3><div class="muted small">Загружаю…</div></div>` : ""}
    ${admin ? `<div class="section" data-tab="test"><h3>Выдуманные клиенты для проверки</h3>
      <div class="small muted" style="margin-bottom:8px">Добавляют клиентов в настоящие группы: с абонементами, оплатами, посещениями, пропусками и лидами на разных этапах. Все помечены как выдуманные и удаляются одной кнопкой, настоящие клиенты не пострадают</div>
      <div class="row" style="align-items:center;flex-wrap:wrap"><input type="number" id="fake-n" value="150" min="1" max="500" style="flex:0 0 100px">
        <button class="btn sm primary" id="fake-add" style="flex:0">Добавить</button><button class="btn sm" id="fake-real" style="flex:0">Школа с историей за 1,5 года</button><button class="btn sm" id="fake-calls" style="flex:0">+ Звонки с разборами</button><button class="btn sm danger" id="fake-del" style="flex:0">Удалить всех выдуманных</button></div></div>` : ""}
    ${admin && S.meta.demo ? `<div class="section" data-tab="test"><h3>Тестовые данные</h3>
      <div class="small muted" style="margin-bottom:8px">Сейчас включён тестовый режим. Кнопка сотрёт всех клиентов и создаст выдуманных заново. Команда и настройки сохранятся</div>
      <button class="btn sm danger" id="demo-reset">Пересоздать тестовые данные</button></div>` : ""}
    <form class="section" id="pw" data-tab="profile">
      <h3>Мой пароль</h3>
      <div class="field"><label>Текущий пароль</label><input name="old" type="password" autocomplete="current-password" required></div>
      <div class="field"><label>Новый пароль</label><input name="new" type="password" autocomplete="new-password" minlength="8" required></div>
      <button class="btn sm">Сменить пароль</button>
    </form>
    <div class="small muted" data-tab="profile" style="margin-top:12px">Вы вошли как ${esc(S.user.name)}. Версия 0.2, тестовая</div>
    </div>`;
  $("#logout").onclick = logoutCurrentAccount;
  settingsTiles(page);
  if ($("#set")) $("#set").onsubmit = async (e) => {
    e.preventDefault();
    try { S.meta.settings = await api("PUT", "/api/settings", Object.fromEntries(new FormData(e.target))); toast("Сохранено"); } catch (err) { fail(err); }
  };
  if ($("#nu")) $("#nu").onsubmit = async (e) => {
    e.preventDefault();
    const button = e.target.querySelector("button[type=submit], button:not([type])");
    if (button) button.disabled = true;
    try {
      const r = await api("POST", "/api/users", Object.fromEntries(new FormData(e.target)));
      await render();
      const text = `${r.name}\nВход в CRM: ${location.origin}\nЛогин: ${r.login}\nПароль: ${r.password}`;
      openDrawer(`<h2>Участник команды добавлен</h2><p>${esc(r.name)}</p><dl class="kv"><dt>Логин</dt><dd><b>${esc(r.login)}</b></dd><dt>Пароль</dt><dd><code>${esc(r.password)}</code></dd></dl><p class="small muted">Скопируйте данные для входа перед закрытием этой карточки. Пароль показывается только сейчас</p><button type="button" class="btn primary" id="new-staff-copy">Скопировать данные для входа</button>`);
      $("#new-staff-copy").onclick = async () => {
        try { await copyCrmText(text, "login"); toast("Данные для входа скопированы"); }
        catch (_) { prompt("Скопируйте данные для входа", text); }
      };
    } catch (err) { fail(err); }
    finally { if (button) button.disabled = false; }
  };
  if ($("#tour-again")) $("#tour-again").onclick = () => startTour(true);
  if ($("#owner-tg")) renderOwnerTg($("#owner-tg"));
  if ($("#festive-on")) $("#festive-on").onchange = async (e) => {
    try { await api("PUT", "/api/settings", { festive: e.target.checked ? "1" : "0" }); S.meta.festive = e.target.checked; shell(); applyFestive(); render(); toast(e.target.checked ? "Праздники включены 🎉" : "Праздничное оформление выключено"); } catch (err) { fail(err); }
  };
  (async () => {
    const box = $("#pin-set"); if (!box) return;
    const dev = deviceToken(true);
    const has = (await api("GET", "/api/pin?device=" + dev).catch(() => ({}))).has_pin;
    box.innerHTML = `<div style="margin-bottom:8px">${has ? "На этом устройстве вход по коду из 4 цифр включён" : "Вход по коду на этом устройстве выключен"}</div>
      <div class="row" style="gap:8px;flex-wrap:wrap"><button class="btn sm primary" id="pin-new">${has ? "Сменить код" : "Включить"}</button>${has ? `<button class="btn sm" id="pin-off">Отключить</button>` : ""}</div>`;
    $("#pin-new").onclick = async () => { try { localStorage.removeItem("crm.pin_skip"); } catch (_) {} if (has) await api("POST", "/api/pin", { device: dev, remove: true }); await offerPin(); shell(); render(); };
    if ($("#pin-off")) $("#pin-off").onclick = async () => { await api("POST", "/api/pin", { device: dev, remove: true }); try { localStorage.setItem("crm.pin_skip", "1"); } catch (_) {} toast("Вход по коду отключён"); render(); };
  })();
  if ($("#sound-on")) $("#sound-on").onchange = (e) => { try { localStorage.setItem("crm.sound", e.target.checked ? "1" : "0"); } catch (_) {} toast(e.target.checked ? "Звук включён" : "Звук выключен"); };
  if ($("#sound-test")) $("#sound-test").onclick = ding;
  if ($("#notif-on")) $("#notif-on").onclick = async () => { const r = await Notification.requestPermission(); toast(r === "granted" ? "Уведомления включены" : "Браузер не разрешил уведомления"); render(); };
  if ($("#theme-soft")) $("#theme-soft").onchange = (e) => {
    const t = e.target.checked ? "soft" : "";
    try { localStorage.setItem("crm.theme", t); } catch (_) {}
    applyTheme(t); toast(t ? "Новый дизайн включён" : "Вернули прежний вид");
  };
  if ($("#price-list")) renderPriceList($("#price-list")).catch(fail);
  if ($("#comms-settings")) renderCommsSettings($("#comms-settings"), users).catch(fail);
  if ($("#task-settings")) renderTaskSettings($("#task-settings"), users).catch(fail);
  if ($("#staff-settings")) renderStaffSettings($("#staff-settings"), users).catch(fail);
  if ($("#source-settings")) renderSourceSettings($("#source-settings")).catch(fail);
  if ($("#legal-settings")) renderLegalSettings($("#legal-settings")).catch(fail);
  if ($("#offers-settings") && can("sales") && !can("docs")) renderOffers($("#offers-settings")).catch(fail);  // администраторам: ссылки на оферты для родителей
  if ($("#memo-settings")) renderMemosSettings($("#memo-settings")).catch(fail);
  if ($("#program-settings")) renderProgramSettings($("#program-settings")).catch(fail);
  if ($("#resp-settings")) renderResponsibles($("#resp-settings")).catch(fail);
  if ($("#diag-box")) renderDiag($("#diag-box"));
  if ($("#backup-settings")) renderBackup($("#backup-settings")).catch(() => { $("#backup-settings").remove(); $('[data-tile="backup"]')?.remove(); });
  else if ($("#offers-settings")) $("#offers-settings").remove();
  if ($("#erp-settings")) renderErp($("#erp-settings")).catch(fail);
  if ($("#tg-import")) renderTgImport($("#tg-import"));
  if ($("#chat-bulk-import")) renderChatBulkImport($("#chat-bulk-import"));
  if ($("#chat-day-import")) renderChatDayImport($("#chat-day-import"));
  if ($("#chains-settings")) renderChainsSettings($("#chains-settings")).catch(fail);
  if ($("#tips-settings")) renderTipsSettings($("#tips-settings")).catch(fail);
  if ($("#discount-settings")) renderDiscounts($("#discount-settings")).catch(() => $("#discount-settings").remove());
  if ($("#structure-settings")) renderStructure($("#structure-settings"), users).catch(fail);
  if ($("#archive-settings")) renderArchive($("#archive-settings")).catch(fail);
  page.querySelectorAll("[data-upw]").forEach((b) => (b.onclick = async () => {
    const pw = prompt("Новый пароль для участника команды (от 8 символов). Передайте его лично");
    if (!pw) return;
    try { await api("PATCH", `/api/users/${b.dataset.upw}`, { password: pw }); toast("Пароль задан"); } catch (e) { fail(e); }
  }));
  page.querySelectorAll("[data-ucopy]").forEach((b) => (b.onclick = async () => {
    const name = b.dataset.name.split(" ")[0];
    if (!confirm(`Создать ${name} новый пароль и скопировать вход?\n\nСтарый пароль перестанет работать. Если ${name} сейчас в CRM, его не выкинет, и код быстрого входа на его телефоне останется.`)) return;
    const abc = "abcdefghjkmnpqrstuvwxyz23456789", rnd = crypto.getRandomValues(new Uint32Array(10));
    const pw = [...rnd].map((x) => abc[x % abc.length]).join("").replace(/(.{5})/, "$1-");
    try { await api("PATCH", `/api/users/${b.dataset.ucopy}`, { password: pw, keep_sessions: true }); } catch (e) { return fail(e); }
    const text = `Вход в CRM «Абонемент Плюс»\nСсылка: ${location.origin}\nЛогин: ${b.dataset.login}\nПароль: ${pw}`;
    let ok = false;
    try { await copyCrmText(text); ok = true; } catch (_) {}
    if (ok) return toast(`Скопировано! Вставьте и отправьте ${name} лично`);
    openDrawer(`<h2>Вход для ${esc(name)}</h2><p class="muted">Браузер не дал скопировать сам: выделите текст и скопируйте</p>
      <textarea readonly rows="5" style="width:100%" onclick="this.select()">${esc(text)}</textarea>`);
  }));
  page.querySelectorAll("[data-uarch]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Перенести участника команды в архив? Он больше не сможет войти, вернуть можно в Архиве")) return;
    try { await api("PATCH", `/api/users/${b.dataset.uarch}`, { active: false }); toast("Участник команды в архиве"); render(); } catch (e) { fail(e); }
  }));
  if ($("#bulk-go")) $("#bulk-go").onclick = async () => {
    try {
      const r = await api("POST", "/api/users/bulk", { text: $("#bulk-users").value });
      const txt = r.users.map((u) => `${u.name}\nЛогин: ${u.login}\nПароль: ${u.password}`).join("\n\n");
      $("#bulk-res").innerHTML = `<div class="alert" style="margin-top:8px">Пароли показываются только сейчас. Скопируйте и раздайте участникам команды лично</div>
        <table class="tbl"><thead><tr><th>Участник команды</th><th>Логин</th><th>Пароль</th><th></th></tr></thead><tbody>${r.users.map((u) => `<tr><td>${esc(u.name)}</td><td><b>${esc(u.login)}</b></td><td><code>${esc(u.password)}</code></td><td class="small muted">${esc(u.status)}</td></tr>`).join("")}</tbody></table>
        <button class="btn sm" id="bulk-copy" style="margin-top:6px">Копировать всё</button>`;
      $("#bulk-copy").onclick = () => { copyCrmText(txt, "client-list").then(() => toast("Скопировано")); };
    } catch (e) { fail(e); }
  };
  page.querySelectorAll("[data-prof]").forEach((el) => (el.onclick = () => openStaffProfile(+el.dataset.prof)));
  page.querySelectorAll("[data-uph]").forEach((inp) => (inp.onchange = async () => {
    try { await api("PATCH", `/api/users/${inp.dataset.uph}`, { phone: inp.value }); toast("Телефон сохранён"); } catch (err) { fail(err); }
  }));
  page.querySelectorAll("[data-ulogin]").forEach(b => b.onclick = async e => {
    e.stopPropagation();
    const value = prompt(`Новый логин для ${b.dataset.name}\nЛатинские буквы, цифры, точка, дефис или подчёркивание. Пароль останется прежним.`, b.dataset.login);
    if (value === null || value.trim().toLowerCase() === b.dataset.login) return;
    b.disabled = true;
    try {
      await api("PATCH", `/api/users/${b.dataset.ulogin}`, {login:value});
      if (+b.dataset.ulogin === S.user.id) S.user.login = value.trim().toLowerCase();
      toast("Логин изменён. Пароль прежний"); await render();
    } catch (err) { fail(err); }
    finally { b.disabled = false; }
  });
  page.querySelectorAll("[data-upost]").forEach((sel) => (sel.onchange = async () => {
    const t = POSTS.find(([k]) => k === sel.value)[1];
    if (!confirm(`Сделать должность «${t}»? Разделы и права поменяются сразу, участнику команды нужно будет войти заново`)) { render(); return; }
    try { await api("PATCH", `/api/users/${sel.dataset.upost}`, { post: sel.value }); toast("Должность изменена"); } catch (err) { fail(err); render(); }
  }));
  page.querySelectorAll("[data-ujour]").forEach((c) => (c.onchange = async () => {
    try { await api("PATCH", `/api/users/${c.dataset.ujour}`, { is_journal: c.checked }); toast(c.checked ? "Теперь отмечает детей и тренеров в журнале" : "Журнал снова только для просмотра"); } catch (err) { fail(err); }
  }));
  page.querySelectorAll("[data-uprom]").forEach((c) => (c.onchange = async () => {
    try { await api("PATCH", `/api/users/${c.dataset.uprom}`, { is_promoter: c.checked }); toast(c.checked ? "Отмечен промоутером" : "Больше не промоутер"); } catch (err) { fail(err); }
  }));
  page.querySelectorAll("[data-tgu]").forEach((inp) => (inp.onchange = async () => {
    try { await api("PATCH", `/api/users/${inp.dataset.tgu}`, { tg_username: inp.value }); toast("Ник в Telegram сохранён"); } catch (err) { fail(err); }
  }));
  page.querySelectorAll("[data-sip]").forEach((inp) => (inp.onchange = async () => {
    try { await api("PATCH", `/api/users/${inp.dataset.sip}`, { sip_number: inp.value }); toast("Внутренний номер сохранён"); S.meta = await api("GET", "/api/meta"); S.user = S.meta.user; } catch (err) { fail(err); }
  }));
  page.querySelectorAll("[data-cap]").forEach((inp) => (inp.onchange = async () => {
    try { await api("PATCH", `/api/groups/${inp.dataset.cap}`, { capacity: +inp.value }); S.meta.groups.find((g) => g.id === +inp.dataset.cap).capacity = +inp.value; toast("Мест: " + inp.value); } catch (err) { fail(err); }
  }));
  page.querySelectorAll("[data-gc]").forEach((sel) => (sel.onchange = async () => {
    try {
      await api("PATCH", `/api/groups/${sel.dataset.gc}`, { coach_id: sel.value ? +sel.value : null });
      S.meta.groups.find((g) => g.id === +sel.dataset.gc).coach_id = sel.value ? +sel.value : null;
      toast("Тренер назначен");
    } catch (err) { fail(err); }
  }));
  if ($("#fake-add")) $("#fake-add").onclick = async (e) => {
    e.target.disabled = true; e.target.textContent = "Добавляю…";
    try { const r = await api("POST", "/api/fake-clients", { count: +$("#fake-n").value }); toast(`Добавлено: занимаются ${r.members}, лидов ${r.leads}`); }
    catch (err) { fail(err); }
    e.target.disabled = false; e.target.textContent = "Добавить";
  };
  if ($("#fake-real")) $("#fake-real").onclick = async (e) => {
    if (!confirm("Удалить прежних выдуманных и создать школу с историей за полтора года (лиды, пробные, покупки, отток, оплаты)? Настоящих клиентов это не трогает.")) return;
    e.target.disabled = true; e.target.textContent = "Создаю, до минуты…";
    try { const r = await api("POST", "/api/fake-clients/realistic", {}); toast(`Готово: занимаются ${r.members_now}, лидов за всё время ${r.leads}, ушли ${r.left}`); }
    catch (err) { fail(err); }
    e.target.disabled = false; e.target.textContent = "Школа с историей за 1,5 года";
  };
  if ($("#fake-calls")) $("#fake-calls").onclick = async () => {
    try { const r = await api("POST", "/api/fake-clients/calls", {}); toast(`Звонков: ${r.calls}, оператор ${r.operator}. Сводка дня в «Звонки» → «Сводки по дням»`); } catch (err) { fail(err); }
  };
  if ($("#fake-del")) $("#fake-del").onclick = async () => {
    if (!confirm("Удалить всех выдуманных клиентов вместе с их оплатами и посещениями?")) return;
    try { const r = await api("POST", "/api/fake-clients/remove", {}); toast(`Удалено: ${r.removed}`); } catch (err) { fail(err); }
  };
  if ($("#demo-reset")) $("#demo-reset").onclick = async () => {
    if (!confirm("Стереть всех клиентов и создать тестовых заново?")) return;
    try { await api("POST", "/api/demo/reset", {}); S.meta = await api("GET", "/api/meta"); toast("Тестовые данные пересозданы"); render(); } catch (err) { fail(err); }
  };
  $("#pw").onsubmit = async (e) => {
    e.preventDefault();
    try { await api("POST", "/api/me/password", Object.fromEntries(new FormData(e.target))); toast("Пароль изменён"); e.target.reset(); } catch (err) { fail(err); }
  };
}

/* Прайс обычных групп с датой начала (pricing.py): старая и новая цена живут параллельно */
async function renderPriceList(box) {
  const d = await api("GET", "/api/prices");
  const GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
  const ml = (ym) => ym <= "2000-01" ? "с самого начала" : `с 1 ${GEN[+ym.slice(5, 7) - 1]} ${ym.slice(0, 4)}`;
  box.classList.remove("muted");
  box.innerHTML = `<table class="tbl"><thead><tr><th>Для абонементов</th><th>Негибкий</th><th>Гибкий</th><th>После ${esc(String(S.meta.settings.renewal_deadline_day || 25))} числа</th></tr></thead>
    <tbody>${d.items.map((p) => `<tr><td>${ml(p.valid_from)}</td><td>${rub(p.fixed)}</td><td>${rub(p.flex)}</td><td>${p.late_pct ? `+${String(p.late_pct).replace(".", ",")}%, без скидок` : "без надбавки"}</td></tr>`).join("")}</tbody></table>
    <div class="row" style="margin-top:8px;align-items:flex-end">
      <div class="field"><label>С месяца</label><input type="month" id="pl-m"></div>
      <div class="field"><label>Негибкий, ₽</label><input type="number" id="pl-fx"></div>
      <div class="field"><label>Гибкий, ₽</label><input type="number" id="pl-fl"></div>
      <div class="field"><label>Надбавка, %</label><input type="number" id="pl-pct" value="5"></div>
      <button type="button" class="btn sm" id="pl-add" style="flex:0 0 auto">Добавить цены</button></div>
    <div class="small muted">Цена берётся по месяцу абонемента. При оплате после срока надбавка со всей суммы, скидки не действуют. Исключение только кнопкой «Корректировка» в карточке</div>`;
  $("#pl-add").onclick = async () => {
    try { await api("POST", "/api/prices", { valid_from: $("#pl-m").value, fixed: $("#pl-fx").value, flex: $("#pl-fl").value, late_pct: $("#pl-pct").value }); toast("Прайс сохранён"); renderPriceList(box); } catch (e) { fail(e); }
  };
}

/* должности для Настроек → Команда (perms.py) */
const POSTS = [["owner", "Руководитель"], ["senior", "Старший администратор"], ["operator", "Администратор"], ["offline_admin", "Офлайн-администратор"], ["coach", "Тренер"], ["lawyer", "Юрист"], ["trainee", "Стажёр (только Планер)"]];
const postOf = (u) => u.role === "admin" ? "owner" : u.role === "operator" ? (u.is_offline_admin ? "offline_admin" : u.is_senior ? "senior" : "operator") : u.role;

/* ---------- старт ---------- */
async function boot() {
  const previousState = history.state;
  try {
    S.meta = await api("GET", "/api/meta");
    S.user = S.meta.user;
  } catch (e) { if (e.message !== "Нужно войти") renderLogin(); return; }
  S.view = visibleViews()[0][0];
  const [routeView,routeQuery=''] = location.hash.slice(1).split('?');
  const requestedView = clientDirectoryView(routeView);
  const routeParams = new URLSearchParams(routeQuery);
  if(requestedView==='planner'){
    const q=routeParams,day=q.get('event_day');
    if(q.has('event_title')&&/^\d{4}-\d{2}-\d{2}$/.test(day||'')){
      S.pendingPlannerEvent={title:q.get('event_title').slice(0,200),day,start_time:q.get('event_start')||'',end_time:q.get('event_end')||'',participants:(q.get('event_participants')||'').slice(0,1000),branch_name:q.get('event_branch')||''};
      S.planner.start=day;S.planner.layout='day';
    }
  }
  if (requestedView === "settings" && routeParams.get("settings_tab")) S.setTab = routeParams.get("settings_tab");
  if (visibleViews().some(([k]) => k === requestedView)) S.view = requestedView;
  else try { const v = clientDirectoryView(localStorage.getItem("crm.view")); if (v && visibleViews().some(([k]) => k === v)) S.view = v; } catch (_) {}
  shell();
  applyFestive();
  const restoreCard = previousState?.drawer && mayRestoreStudent(previousState.studentCard) ? previousState.studentCard : null;
  const rawStudent = routeParams.get("student") || "";
  const routedStudent = /^\d+$/.test(rawStudent) && Number.isSafeInteger(+rawStudent) ? +rawStudent : 0;
  const routedConversation = Number(routeParams.get("conversation")) || 0;
  try { history.replaceState({ view: S.view, sub: navSnap(), depth: previousState?.depth || 0,
    ...(S.view === "settings" && S.setTab ? { settingsTab:S.setTab } : {}),
    ...(restoreCard ? { drawer: true, studentCard: restoreCard } : {}) }, "", studentCardHash(restoreCard)); } catch (_) {}
  await render();
  if (routedStudent && ["members", "sales", "search", "inbox"].some((section) => can(section))) {
    drawerInHistory = restoreCard?.id === routedStudent;
    await openStudent(routedStudent, { tab: routeParams.get("student_tab") || "feed" });
  } else if (routedConversation && S.view === "inbox" && can("inbox") && typeof openChat === "function") {
    await openChat(routedConversation, true);
  } else if (restoreCard) {
    drawerInHistory = true;
    await openStudent(restoreCard.id, { tab: restoreCard.tab, restoring: true });
  }
  setInterval(() => { if ($("#drawer").hidden && ["members", "skips"].includes(S.view)) render(); }, 60000);
  setInterval(() => { if ($("#drawer").hidden && S.view === "sales" && !document.hidden && !document.querySelector(".task-done:not([hidden])")) render(); }, 20000);
  setInterval(() => { if ($("#drawer").hidden && S.view === "payments" && !document.hidden && !document.querySelector(".payment-control-details[open]")) render(); }, 60000);
  if (can("inbox")) { pollCounts(); setInterval(pollCounts, 10000); }
  if (!S.meta.tour_pending && !restoreCard) offerAlerts();
  setTimeout(() => { if (typeof memoSync === "function") memoSync(); }, 4000);  // памятки для родителей: перерисовать, если поменялись цены
}
boot();

/* Руководителю и администраторам при каждом входе, пока не настроено: включить звук и уведомления.
   «Пропустить» откладывает до следующего входа. Настройка живёт в браузере, поэтому на каждом устройстве своя */
const notifReady = () => !("Notification" in window) || Notification.permission === "granted";
function offerAlerts() {
  if (!S.user || !["admin", "operator"].includes(S.user.role) || !can("inbox")) return;
  if (soundOn() && notifReady()) return;
  const draw = () => {
    const denied = "Notification" in window && Notification.permission === "denied";
    openDrawer(`<h2>🔔 Чтобы не пропустить клиента</h2>
      <p class="muted">Включите звук и уведомления: тогда CRM даст знать о новом сообщении, пропущенном звонке или заявке, даже если она свёрнута. Это займёт полминуты</p>
      <div class="section"><h3>1. Звук</h3>
        <label class="check"><input type="checkbox" id="oa-sound" ${soundOn() ? "checked" : ""}> Звук, когда приходит сообщение, пропущенный звонок или заявка</label>
        <button class="btn" id="oa-test" style="margin-top:10px">🔊 Проверить звук</button>
        <div class="small muted" style="margin-top:6px">Не слышно? Проверьте громкость на компьютере или телефоне</div></div>
      ${"Notification" in window ? `<div class="section"><h3>2. Уведомления</h3>
        ${notifReady() ? `<div>✅ Уведомления включены</div>`
          : denied ? `<div class="small">Браузер сейчас запрещает уведомления. Нажмите на значок слева от адреса сайта (замочек или настройки), найдите «Уведомления» и выберите «Разрешить». Потом обновите страницу</div>`
          : `<button class="btn primary" id="oa-notif">Разрешить уведомления</button>
             <div class="small muted" style="margin-top:6px">Браузер спросит разрешение: нажмите «Разрешить»</div>`}</div>` : ""}
      <div class="row" style="gap:10px;margin-top:18px">
        <button class="btn primary" id="oa-done" style="flex:1">Готово</button>
        <button class="btn" id="oa-skip" style="flex:1">Пропустить, напомнить в следующий раз</button></div>`);
    $("#oa-sound").onchange = (e) => { try { localStorage.setItem("crm.sound", e.target.checked ? "1" : "0"); } catch (_) {} };
    $("#oa-test").onclick = () => ding();
    if ($("#oa-notif")) $("#oa-notif").onclick = async () => { await Notification.requestPermission(); draw(); };
    $("#oa-skip").onclick = () => closeDrawer();
    $("#oa-done").onclick = () => {
      closeDrawer();
      toast(soundOn() && notifReady() ? "Готово! Теперь CRM даст знать о новых сообщениях" : "Не всё включено: напомню при следующем входе");
    };
  };
  draw();
}

/* Копирование списков ограничено; тексты задач, сообщений и поля ввода доступны отдельно. */
function crmCopyableSelection(selection) {
  if(!selection?.rangeCount)return false;
  const anchor=selection.anchorNode;
  const element=anchor?.nodeType===Node.ELEMENT_NODE?anchor:anchor?.parentElement;
  const region=element?.closest('.task-copyable,.chat-message-copyable');
  if(!region)return false;
  for(let index=0;index<selection.rangeCount;index++){
    const range=selection.getRangeAt(index);
    if(!region.contains(range.startContainer)||!region.contains(range.endContainer))return false;
  }
  return true;
}
document.addEventListener("copy", (e) => {
  if (!S.user || can("export")) return;
  const t = document.activeElement;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;
  if (crmCopyableSelection(window.getSelection())) return;
  e.preventDefault();
  toast("Копировать данные из CRM может только руководитель", true);
});

/* Документы: юридические документы, оферты, подписи участников команды (руководитель и юрист) */
async function renderDocs(page) {
  const tabs = [...(can("docs", "full") ? [["legal", "Юридические документы"], ["offers", "Оферты"]] : []), ...(can("staffdocs", "view") ? [["staff", "Документы команды"]] : [])];
  if (!tabs.some(([k]) => k === S.docTab)) S.docTab = tabs[0] && tabs[0][0];
  page.innerHTML = `<div class="page-head"><h2 class="grow">Документы</h2></div><div style="max-width:880px">
    ${tabs.length > 1 ? `<div class="seg" style="margin-bottom:14px">${tabs.map(([k, t]) => `<button data-dtab="${k}" class="${S.docTab === k ? "active" : ""}">${t}</button>`).join("")}</div>` : ""}
    <div class="section" id="doc-box"><div class="muted small">Загружаю…</div></div></div>`;
  page.querySelectorAll("[data-dtab]").forEach((b) => (b.onclick = () => { S.docTab = b.dataset.dtab; render(); }));
  const box = $("#doc-box");
  const fn = { legal: renderLegalSettings, offers: renderOffers, staff: renderStaffDocsAdmin }[S.docTab];
  if (fn) fn(box).catch(fail);
}


/* Перевод в другую группу вместе с абонементом: сначала показываем, что станет с оплатой, потом переводим */
function openTransfer(eid, box, d, groups, reload, initialDate) {
  if (!box.hidden) { box.hidden = true; return; }
  const e = d.enrollments.find((x) => x.id === eid);
  const t = new Date(), first = new Date(t.getFullYear(), t.getMonth() + 1, 1);
  const iso = (x) => `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}-${String(x.getDate()).padStart(2, "0")}`;
  const opts = groups.filter((g) => !g.program_id && !g.is_makeup && g.id !== e.group_id && !d.enrollments.some((x) => x.group_id === g.id));
  box.hidden = false;
  box.innerHTML = `<div class="row" style="margin-top:8px">
      <div class="field"><label>В группу</label><select data-trg><option value="">Выберите группу</option>${opts.map((g) => `<option value="${g.id}">${esc(g.name)}${g.age?' · '+esc(g.age)+' лет':''}</option>`).join("")}</select></div>
      <div class="field" style="flex:0 0 150px"><label>С какого дня</label><input type="date" data-trd value="${initialDate || iso(first)}" min="${iso(new Date(t.getFullYear(), t.getMonth(), 1))}"></div></div>
    ${e.program_id?'<div class="field"><label>Тариф новой группы</label><select data-trtar><option value="fixed">Негибкий</option><option value="flex">Гибкий</option></select></div>':""}
    <div data-trplan class="small muted">Выберите группу: покажу, что станет с абонементом и оплатой</div>`;
  const sel = box.querySelector("[data-trg]"), dt = box.querySelector("[data-trd]"), out = box.querySelector("[data-trplan]");
  let transferVersion=0;
  const calc = async () => {
    const request=++transferVersion;
    if (!sel.value) {out.textContent="Выберите группу";return;}
    const target=+sel.value,start=dt.value,tariff=box.querySelector("[data-trtar]")?.value||"fixed";
    out.textContent = "Считаю…";
    try {
      const p = await api("GET", `/api/enrollments/${eid}/transfer?group_id=${target}&date=${start}&tariff=${tariff}`);
      if(request!==transferVersion||!box.isConnected)return;
      const rows = p.months.map((m) => p.program_transfer?`<li><b>${esc(m.label)}</b><div>Остаток Арктики: ${rub(m.arena_credit)} · ОФП: ${rub(m.ofp_credit)}</div><div>Зачёт стоимости: ${rub(m.credit)} · Новая группа: ${m.new_lessons} занятий, ${rub(m.new_price)}</div></li>`:`<li><b>${esc(m.label)}</b>: было ${m.old_lessons} зан. за ${rub(m.old_price)} →
        ${m.keep_lessons ? `${m.keep_lessons} зан. в старой группе (${rub(m.keep_price)}) и ` : ""}${m.new_lessons} зан. в новой (${rub(m.new_price)})</li>`).join("");
      out.innerHTML = `<div style="margin:6px 0">С ${dShort(p.start)} в группу <b>${esc(p.to_group)}</b>. До этой даты ребёнок остаётся в журнале старой группы</div>
        ${p.program_transfer?'<p>Арктика и ОФП прекращаются с даты перевода. Остатки обоих направлений учитываются вместе. Если прежний абонемент оплачен частично, в балансе учитываются только реальные платежи</p>':""}
        ${rows ? `<ul style="margin:4px 0 6px 18px">${rows}</ul><div>${p.program_transfer?`После перевода по всем счетам клиента: к оплате ${rub(p.projected_due)}, остаток на балансе ${rub(p.projected_deposit)}`:p.diff > 0 ? `Доплата ${rub(p.diff)}: появится как долг` : p.diff < 0 ? `Разница ${rub(-p.diff)} уйдёт на депозит` : "По деньгам без изменений"}</div>`
          : `<div>Абонементов с этой даты нет, переносить нечего</div>`}
        <button class="btn primary sm" data-trgo style="margin-top:8px">Перевести</button>`;
      out.querySelector("[data-trgo]").onclick = async () => {
        try { out.querySelector("[data-trgo]").disabled=true; await api("POST", `/api/enrollments/${eid}/transfer`, { group_id: target, date: start, tariff, expected:{diff:p.diff,credit_total:p.credit_total??null,months:p.months.map(m=>[m.sub_id,m.old_price,m.keep_price,m.new_price,m.new_lessons])} }); toast("Ученик переведён"); reload(); } catch (err) { fail(err); if(out.querySelector("[data-trgo]"))out.querySelector("[data-trgo]").disabled=false; }
      };
    } catch (err) { if(request===transferVersion)out.innerHTML = `<span style="color:#b42318">${esc(err.message)}</span>`; }
  };
  sel.onchange = calc; dt.onchange = calc; if(box.querySelector("[data-trtar]"))box.querySelector("[data-trtar]").onchange=calc;
}

/* Первый вход участника команды: принять документы (NDA, правила работы с данными, согласие, журнал, ПЭП) */
async function renderStaffDocs(page) {
  const d = await api("GET", "/api/staff-docs");
  const need = d.docs.filter((x) => !x.accepted_at);
  page.innerHTML = `<div class="sd-wrap"><h2>Добро пожаловать в CRM, ${esc(d.name.split(" ")[0])}!</h2>
    <p class="muted">Перед началом работы прочитайте и примите документы. Они защищают данные наших клиентов и школу. Без этого CRM не откроется</p>
    ${need.map((x, i) => `<div class="section sd-doc"><label class="sd-check"><input type="checkbox" data-sd="${x.slug}"> <b>${esc(x.title)}</b></label>
      <details ${i === 0 ? "open" : ""}><summary class="linkish small">Прочитать полностью</summary><div class="legal-text">${x.html}</div></details></div>`).join("")}
    <div class="section"><div class="field"><label>Впишите свои фамилию и имя: это ваша подпись</label><input id="sd-name" placeholder="${esc(d.name)}" autocomplete="off"></div>
      <button class="btn primary" id="sd-ok" disabled>Принимаю и подписываю</button>
      <div class="small muted" style="margin-top:6px">Мы сохраним дату, время и IP-адрес подписания</div></div></div>`;
  const ok = $("#sd-ok");
  const sync = () => { ok.disabled = !([...page.querySelectorAll("[data-sd]")].every((c) => c.checked) && $("#sd-name").value.trim().length > 2); };
  page.querySelectorAll("[data-sd]").forEach((c) => (c.onchange = sync));
  $("#sd-name").oninput = sync;
  ok.onclick = async () => {
    try {
      await api("POST", "/api/staff-docs/accept", { docs: need.map((x) => x.slug), signed_name: $("#sd-name").value });
      S.meta.staff_docs_pending = 0; toast("Спасибо! Документы подписаны"); render();
    } catch (e) { fail(e); }
  };
}

async function renderStaffDocsAdmin(box) {
  const d = await api("GET", "/api/staff-docs/overview");
  box.innerHTML = `<h3>Документы команды</h3><div class="small muted">NDA, правила работы с данными, согласие, журнал действий, электронная подпись. Каждый подписывает при первом входе</div>
    ${d.items.map((u) => `<div class="list-row"><div class="grow">${esc(u.name)}</div>
      ${u.pending ? `<span class="pill red">не подписал${u.last ? " новую редакцию" : ""}</span>` : `<span class="pill green">подписал ${dtWhen(u.last)}</span>`}
      ${u.tour ? `<span class="pill green" title="Обучение в CRM">обучение ${dtWhen(u.tour)}</span>` : `<span class="pill">обучение не пройдено</span>`}
      ${u.last ? `<button class="btn sm" data-sdlog="${u.id}">Лист подписания</button>` : ""}</div>`).join("") || `<div class="small muted">Участников команды пока нет</div>`}`;
  box.querySelectorAll("[data-sdlog]").forEach((b) => (b.onclick = async () => {
    const l = await api("GET", `/api/staff-docs/log/${b.dataset.sdlog}`);
    openDrawer(`<h2>Лист подписания</h2><div class="muted">${esc(l.name)}</div>
      <table class="tbl" style="margin-top:10px"><thead><tr><th>Документ</th><th>Подпись</th><th>Когда</th><th>IP</th><th>Отпечаток</th></tr></thead>
      <tbody>${l.items.map((x) => `<tr><td>${esc(x.title)}</td><td>${esc(x.signed_name)}</td><td>${esc(x.accepted_at.replace("T", " "))}</td><td>${esc(x.ip)}</td><td><code>${esc(x.sha256.slice(0, 16))}</code></td></tr>`).join("")}</tbody></table>
      <div class="small muted" style="margin-top:8px">Отпечаток подтверждает, какой именно текст подписан. Для печати: меню браузера → Печать</div>`);
  }));
}

/* Настройки → Резервные копии */
/* Настройки → «🩺 Проверка соединений» (только владелец): кто и как подключался к CRM, по журналу сервера */
async function renderDiag(box, ip = "") {
  box.innerHTML = `<h3>🩺 Проверка соединений</h3>
    <div class="small muted">Попросите участника команды открыть ab-plus.ru и прислать скриншот с сайта 2ip.ru (там его IP-адрес). Впишите IP сюда и нажмите «Показать». Видно, дошёл ли он до сервера и на каком шаге оборвалось</div>
    <div class="row" style="gap:8px;margin-top:8px"><input id="dg-ip" placeholder="IP-адрес, например 95.24.1.2" value="${esc(ip)}">
      <button class="btn primary" id="dg-go" style="flex:0 0 auto">Показать</button><button class="btn" id="dg-all" style="flex:0 0 auto">Все за последнее время</button></div>
    <div id="dg-out" class="small muted" style="margin-top:10px">Загружаю…</div>`;
  const run = async (v) => {
    const out = $("#dg-out");
    try {
      const d = await api("GET", "/api/diag/conn?ip=" + encodeURIComponent(v || ""));
      if (!d.enabled) { out.textContent = "Журнал ещё не включился на сервере: подождите 5-10 минут и откройте снова"; return; }
      const verdict = v ? (d.rows.length ? `✅ Соединения с ${esc(v)} доходят до сервера: ${d.rows.length}` : d.errors.length ? `⚠️ С ${esc(v)} были попытки, но рукопожатие не завершилось (см. ниже)` : `❌ С ${esc(v)} ничего не дошло до сервера`) : "";
      out.innerHTML = `${verdict ? `<p><b>${verdict}</b></p>` : ""}
        ${!v && d.top_ips.length ? `<div>Чаще всего: ${d.top_ips.map(([a, n]) => `<button class="chip" data-dgip="${esc(a)}">${esc(a)} · ${n}</button>`).join(" ")}</div>` : ""}
        ${d.rows.length ? `<table class="tbl" style="margin-top:8px"><thead><tr><th>Когда</th><th>IP</th><th>TLS</th><th>Ключ</th><th>Ответ</th><th>Байт</th><th>Сек</th><th>Что</th></tr></thead>
          <tbody>${d.rows.map((r) => `<tr><td>${esc(r.at.slice(5))}</td><td>${esc(r.ip)}</td><td>${esc(r.tls)}</td><td>${esc(r.key)}</td><td>${r.status}</td><td>${r.bytes}</td><td>${esc(r.secs)}</td><td title="${esc(r.ua)}">${esc(r.req)}</td></tr>`).join("")}</tbody></table>` : ""}
        ${d.errors.length ? `<h4 style="margin:12px 0 4px">Обрывы и ошибки</h4><pre class="small" style="white-space:pre-wrap">${d.errors.map(esc).join("\n")}</pre>` : ""}`;
      out.querySelectorAll("[data-dgip]").forEach((b) => (b.onclick = () => renderDiag(box, b.dataset.dgip)));
    } catch (e) { out.textContent = e.message; }
  };
  // зелёной подсвечена та кнопка, чей результат сейчас на экране
  const mark = (all) => { $("#dg-all").classList.toggle("primary", all); $("#dg-go").classList.toggle("primary", !all); };
  $("#dg-go").onclick = () => { mark(false); run($("#dg-ip").value.trim()); };
  $("#dg-all").onclick = () => { mark(true); $("#dg-ip").value = ""; run(""); };
  mark(!ip);
  run(ip);
}

async function renderBackup(box) {
  const d = await api("GET", "/api/backup");
  box.innerHTML = `<h3>Резервные копии</h3>
    <div class="small">На сервере: каждый час, хранится ${d.local_count} ${d.local_count === 1 ? "копия" : "копий"}${d.last_local ? `, последняя ${dtWhen(d.last_local)}` : ""}</div>
    <div class="small">${d.yadisk ? `На Яндекс Диске (зашифрованные): ${d.last_remote ? "последняя " + esc(d.last_remote) : "ещё не было"}` : `<b style="color:#b42318">Яндекс Диск не подключён: копии только на этом сервере</b>`}</div>
    <div class="small">Excel со всей базой: каждый час${d.excel_last ? `, последний ${esc(d.excel_last.replace("T", " "))}` : ", ещё не было"}${d.yadisk ? " (на Диске в папке Excel)" : ""}</div>
    ${d.last_error ? `<div class="alert" style="margin-top:6px">${esc(d.last_error)}</div>` : ""}
    ${d.excel_error ? `<div class="alert" style="margin-top:6px">Excel: ${esc(d.excel_error)}</div>` : ""}
    <div class="field" style="margin-top:10px"><label>Токен Яндекс Диска</label>
      <div class="row"><input id="bk-token" type="password" autocomplete="off" placeholder="${d.yadisk ? "подключён, вставьте новый, чтобы заменить" : "вставьте токен"}"><button class="btn sm" id="bk-save" style="flex:0">Сохранить</button></div>
      <div class="small muted">Как получить: откройте yandex.ru/dev/disk/poligon под аккаунтом, где хотите хранить копии, нажмите «Получить OAuth-токен», скопируйте его и вставьте сюда</div></div>
    <div class="field"><label>Пароль шифрования копий</label><div class="row"><code id="bk-key" style="flex:1;padding:8px;background:var(--surface-2);border-radius:8px">${esc(d.key)}</code>
      <button class="btn sm" id="bk-copy" style="flex:0">Копировать</button></div>
      <div class="small" style="color:#b42318">Сохраните этот пароль отдельно от CRM, например в заметках телефона. Без него копию с Диска не открыть</div></div>
    <button class="btn sm primary" id="bk-run">Сделать копию сейчас</button>
    ${can("export") ? `<div class="field" style="margin-top:14px"><label>Копия, которую можно прочитать</label>
      <div class="row" style="gap:8px;flex-wrap:wrap"><a class="btn sm" style="flex:0 0 auto" href="/api/export/all?t=${Date.now()}">📥 Вся база в Excel</a>
        <a class="btn sm" style="flex:0 0 auto" href="/api/export/all?part=contacts&t=${Date.now()}">📞 Только контакты</a></div>
      <div class="small muted" style="margin-top:6px"><b>Вся база:</b> дети и родители с телефонами, абонементы, оплаты, посещения, группы, участники команды.<br>
        <b>Только контакты:</b> один лист для обзвона: ребёнок, родитель, телефон, группа, ученик или лид, пометка «не звонить». Без оплат.<br>
        Храните файлы в надёжном месте: в них персональные данные клиентов</div></div>` : ""}`;
  $("#bk-copy").onclick = () => copyCrmText(d.key, "backup").then(() => toast("Пароль скопирован"));
  $("#bk-save").onclick = async () => { try { await api("POST", "/api/backup", { yadisk_token: $("#bk-token").value }); toast("Сохранено"); renderBackup(box); } catch (e) { fail(e); } };
  $("#bk-run").onclick = async (e) => {
    e.target.disabled = true; e.target.textContent = "Делаю копию…";
    try { const r = await api("POST", "/api/backup", { run: true }); toast(r.result.error ? "Копия на сервере есть, на Диск не ушла" : r.result.remote ? "Копия сохранена и отправлена на Яндекс Диск" : "Копия сохранена на сервере"); renderBackup(box); } catch (err) { fail(err); }
  };
}

function normalizedCopyPhone(phone) {
  let digits = String(phone).replace(/\D/g, "");
  if (digits.length === 10) digits = "7" + digits;
  if (digits.length === 11 && digits[0] === "8") digits = "7" + digits.slice(1);
  if (!/^7\d{10}$/.test(digits)) throw new Error("Проверьте номер телефона");
  return "+" + digits;
}
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-copy-phone]");
  if (!b) return;
  e.preventDefault();
  try {
    const text = normalizedCopyPhone(b.dataset.copyPhone);
    try { await copyCrmText(text); }
    catch (_) {
      const input = document.createElement("textarea"); input.value = text;
      input.style.position = "fixed"; input.style.opacity = "0";
      document.body.appendChild(input); input.select();
      const ok = document.execCommand("copy"); input.remove();
      if (!ok) throw new Error("Не удалось скопировать номер");
    }
    toast("Номер скопирован");
  } catch (error) { fail(error); }
});


async function logoutCurrentAccount() {
  const buttons = [...document.querySelectorAll("[data-account-logout], #logout")];
  buttons.forEach(b => b.disabled = true);
  try {
    try { await api("POST", "/api/logout", {}); }
    catch (err) { if (err.message !== "Нужно войти") throw err; }
    try { sessionStorage.setItem("crm.force_password_login", "1"); } catch (_) {}
    // A full reload clears private in-memory data, timers and the previous user's drafts.
    location.reload();
  } catch (err) { buttons.forEach(b => b.disabled = false); fail(err); }
}

async function openMultiMonthSale(d,id,box,reload,preset={}) {
  const groups=(S.meta.groups||[]).filter(g=>g.active!==0&&!g.is_makeup&&g.lesson_kind!=='ofp');
  const months=Array.from({length:12},(_,i)=>{const dt=new Date(d.cur_month+'-01T12:00');dt.setMonth(dt.getMonth()+i);return isoDate(dt).slice(0,7)});
  const session='multi-'+String(S.saleSequence=(S.saleSequence||0)+1);box.dataset.saleSession=session;
  box.innerHTML=`<form class="section sell" data-multi-sale><div class="row"><h3 class="grow">Абонементы на несколько месяцев</h3><button type="button" class="icon-btn" data-multi-close aria-label="Закрыть">✕</button></div><p class="small muted">На каждый месяц — отдельный абонемент. Общую оплату распределим по месяцам, начиная с первого</p><div class="multi-month-choices">${months.map((m,i)=>`<label><input type="checkbox" name="months" value="${m}" ${i<2?'checked':''}>${esc(monthLabel(m))}</label>`).join('')}</div><div class="row"><div class="field"><label>Группа</label><select name="group_id">${groups.map(g=>`<option value="${g.id}" ${g.id===preset.group_id?'selected':''}>${esc(g.name)}</option>`).join('')}</select></div><div class="field"><label>Тариф</label><select name="tariff"></select></div></div><div class="field" data-multi-ofp hidden><label>Группа ОФП</label><select name="ofp_group_id"></select></div><div class="field"><label>Скидка</label><select name="discount_id"><option value="">По правилам</option>${(d.discounts||[]).map(x=>`<option value="${x.id}">${esc(x.name)}, ${x.percent}%</option>`).join('')}</select></div><p class="small muted">Ручная скидка заменяет автоматическую для каждого выбранного месяца</p><div class="field"><label>Фактическая дата оплаты</label><input name="paid_date" type="date" required value="${S.meta.today}" max="${S.meta.today}"></div><div class="field"><label>Причина исключения занятий</label><input name="exclusion_reason" maxlength="1000" placeholder="Если исключаете занятия по согласованию"></div><label class="check"><input type="checkbox" name="use_balance"> Зачесть с баланса</label><div data-multi-quote>Считаю…</div><div class="row"><div class="field"><label>Получено, ₽</label><input name="amount" type="number" min="1" step="1"></div><div class="field"><label>Как оплатили</label><select name="method">${Object.entries(PAY_METHODS).filter(([k])=>PAY_MANUAL_METHODS.includes(k)).map(([k,v])=>`<option value="${k}">${esc(v)}</option>`).join('')}</select></div></div><p class="small muted">«Отметить оплату» — только для денег, которые уже получены</p><p class="need-err" data-multi-error role="alert"></p><div class="sc-actions"><button type="button" class="btn" data-multi-save="invoice" disabled>Оформить без оплаты</button><button type="button" class="btn primary" data-multi-save="pay" disabled>Отметить оплату</button><button type="button" class="btn" data-multi-save="links" disabled>Общая ссылка на оплату</button></div><div data-multi-result></div></form>`;
  const form=box.querySelector('form'),f=form.elements,output=form.querySelector('[data-multi-quote]'),error=form.querySelector('[data-multi-error]'),result=form.querySelector('[data-multi-result]');
  const isCurrent=()=>box.isConnected&&form.isConnected&&box.dataset.saleSession===session;
  let checked=null,generation=0,busy=false,excluded={};
  const requestId=window.crypto?.randomUUID?.()||Date.now()+'-'+Math.random().toString(36).slice(2);
  const payload=()=>({months:[...form.querySelectorAll('[name=months]:checked')].map(x=>x.value),group_id:+f.group_id.value,tariff:f.tariff.value,ofp_group_id:f.tariff.value==='pkg'?+f.ofp_group_id.value:null,discount_id:f.discount_id.value||null,paid_date:f.paid_date.value,exclusion_reason:f.exclusion_reason.value,excluded_by_month:excluded,group_action:'add',use_balance:f.use_balance.checked});
  const buttons=()=>form.querySelectorAll('[data-multi-save]').forEach(b=>b.disabled=busy||!checked||(b.dataset.multiSave!=='invoice'&&checked.due<=0)||(b.dataset.multiSave==='links'&&f.paid_date.value!==S.meta.today));
  const tariffs=()=>{
    const g=groups.find(g=>g.id===+f.group_id.value),old=f.tariff.value||preset.tariff;
    f.tariff.innerHTML=g?.program_id?'<option value="arena_only">Только Арктика</option><option value="pkg">Арктика + ОФП</option>':g?.fixed_only?'<option value="fixed">Негибкий</option>':'<option value="fixed">Негибкий</option><option value="flex">Гибкий</option>';
    if([...f.tariff.options].some(o=>o.value===old))f.tariff.value=old;
    f.tariff.disabled=!!g?.fixed_only;
    const program=(d.program?.programs||[]).find(p=>p.id===g?.program_id);
    f.ofp_group_id.innerHTML=(program?.ofp_groups||[]).map(g=>`<option value="${g.id}" ${g.id===+preset.ofp_group_id?'selected':''}>${esc(g.name)}</option>`).join('');
    form.querySelector('[data-multi-ofp]').hidden=f.tariff.value!=='pkg';
  };
  const preview=async()=>{
    const version=++generation;checked=null;buttons();error.textContent='';output.textContent='Считаю…';
    try{
      const q=await api('POST',`/api/students/${id}/multi-month-preview`,payload());
      if(version!==generation||!form.isConnected)return;
      invoiceBalancePreviewHtml(q.balance_preview);checked=q;
      output.innerHTML='<div class="multi-month-calendars">'+q.months.map(r=>`<section class="multi-month-calendar"><h4>${esc(monthLabel(r.month))}</h4>${multiMonthCalendarHtml(r)}<div class="multi-month-calculation">${quoteHtml(r.quote)}</div></section>`).join('')+'</div>'+`<div class="quote"><b>Всего: ${rub(q.total)}</b>${invoiceBalancePreviewHtml(q.balance_preview)}</div>`;
      f.amount.value=q.due;
      output.querySelectorAll('[data-multi-day]').forEach(c=>c.onchange=()=>{
        const month=c.dataset.month;const row=q.months.find(r=>r.month===month);excluded[month]=[...row.excluded_dates];
        if(c.checked)excluded[month]=excluded[month].filter(day=>day!==c.dataset.multiDay);else excluded[month].push(c.dataset.multiDay);
        preview();
      });buttons();
    }catch(e){if(version===generation&&form.isConnected){output.textContent='';error.textContent=e.message}}
  };
  form.onsubmit=e=>e.preventDefault();
  form.querySelector('[data-multi-close]').onclick=()=>{if(!busy){generation++;box.replaceChildren()}};
  form.querySelectorAll('[name=months]').forEach(c=>c.onchange=preview);
  f.group_id.onchange=()=>{excluded={};tariffs();preview()};
  f.tariff.onchange=()=>{excluded={};form.querySelector('[data-multi-ofp]').hidden=f.tariff.value!=='pkg';preview()};
  f.discount_id.onchange=f.paid_date.onchange=f.ofp_group_id.onchange=f.use_balance.onchange=preview;
  form.querySelectorAll('[data-multi-save]').forEach(b=>b.onclick=async()=>{
    if(busy||!checked)return;const mode=b.dataset.multiSave;
    if(mode==='pay'&&(!Number.isInteger(+f.amount.value)||+f.amount.value<1||+f.amount.value>checked.due)){error.textContent='Проверьте полученную сумму';return}
    const order={...payload(),expected_token:checked.token,mode,request_id:requestId,...(mode==='pay'?{amount:+f.amount.value,method:f.method.value}:{})},saleGeneration=++generation;
    busy=true;buttons();form.querySelectorAll('input,select').forEach(x=>x.disabled=true);error.textContent='';
    try{
      const saved=await api('POST',`/api/students/${id}/multi-month-sale`,order);
      if(!isCurrent()||generation!==saleGeneration)return;
      const issuedLinks=[...(saved.link?[{link:saved.link,scope:saved.subscriptions.map(s=>s.id)}]:[]),...saved.subscriptions.filter(s=>s.link).map(s=>({link:s.link,scope:s.id}))];
      const validateLinks=()=>Promise.all(issuedLinks.map(item=>validatePaymentLinkForSharing(item.link,id,item.scope,true)));
      const msg=`Абонементы: ${displayChildName(d.student.name)}\nГруппа: ${groups.find(g=>g.id===order.group_id)?.name||''}\n\n`+saved.subscriptions.map(s=>`${monthLabel(s.month)} — ${rub(s.price)}${s.link?'\n'+s.link.url:''}`).join('\n\n')+`\n\nВсего: ${rub(saved.total)}\nОсталось оплатить: ${rub(saved.due)}${saved.link?'\n\nОплатить все месяцы по одной ссылке:\n'+saved.link.url:''}`;
      result.innerHTML='<h4>Абонементы оформлены</h4><p class="small muted" data-multi-link-status role="status">Проверяем сумму ссылки…</p><p class="need-err" data-multi-link-warning role="alert" hidden></p><textarea rows="8" hidden></textarea><div class="sc-actions"><button type="button" class="btn" data-copy disabled>Скопировать</button><button type="button" class="btn" data-chat disabled>Вставить в чат</button><button type="button" class="btn primary" data-done>Готово</button></div>';
      const message=result.querySelector('textarea'),copy=result.querySelector('[data-copy]'),chat=result.querySelector('[data-chat]'),status=result.querySelector('[data-multi-link-status]'),warning=result.querySelector('[data-multi-link-warning]');
      let sharing=false,invalid=false;
      const current=()=>isCurrent()&&generation===saleGeneration&&message.isConnected;
      const block=error=>{if(!current())return;invalid=true;copy.disabled=chat.disabled=true;message.disabled=true;warning.hidden=false;warning.textContent=(error.message||'Не удалось проверить общую ссылку')+(Number.isInteger(error.linkState?.current_due)?` Сейчас к оплате: ${rub(error.linkState.current_due)}.`:'')+' Обновите карточку и проверьте текущие суммы перед отправкой.';status.textContent='Ссылка не скопирована и не вставлена в чат';result.querySelector('[data-done]').textContent='Обновить карточку';};
      const share=async action=>{
        if(sharing||invalid||!current())return;sharing=true;copy.disabled=chat.disabled=true;message.disabled=true;status.textContent='Проверяем сумму ссылки…';
        try{
          await validateLinks();if(!current())return;
          if(issuedLinks.length){const expected=[...new Set(issuedLinks.map(item=>item.link.url))],urls=(message.value.match(/https?:\/\/[^\s<>"']+/gi)||[]).map(value=>value.replace(/[)\]},.;!?]+$/g,'')).filter(value=>{try{return new URL(value).pathname.startsWith('/pay/')}catch(_){return false}});if(urls.length!==expected.length||!expected.every(url=>urls.includes(url)))throw Error('Ссылка в сообщении изменена или добавлена другая ссылка на оплату')}
          if(action==='copy')await copyTextWithNotice(message.value,'Текст скопирован','payment-link');
          else{scTab('feed');const target=$('#cm-text');if(!target)return toast('Откройте чат в карточке',true);target.value=message.value;target.dispatchEvent(new Event('input',{bubbles:true}));target.focus()}
          if(current())status.textContent='Сумма ссылки проверена';
        }catch(error){block(error)}finally{sharing=false;if(current()&&!invalid){copy.disabled=chat.disabled=false;message.disabled=false}}
      };
      copy.onclick=()=>share('copy');chat.onclick=()=>share('paste');
      result.querySelector('[data-done]').onclick=reload;
      try{await validateLinks();if(current()){message.value=msg;message.hidden=false;copy.disabled=chat.disabled=false;status.textContent=issuedLinks.length?'Сумма ссылки проверена':''}}catch(error){block(error)}
    }catch(e){error.textContent=e.message;busy=false;form.querySelectorAll('input,select').forEach(x=>x.disabled=false);f.tariff.disabled=!!groups.find(g=>g.id===+f.group_id.value)?.fixed_only;buttons()}
  });
  tariffs();pastGroupSearch(f.group_id);await preview();box.scrollIntoView({block:'start',behavior:'smooth'});
}


function multiMonthCalendarHtml(row) {
  const first=new Date(row.month+'-01T12:00:00'),offset=(first.getDay()+6)%7;
  const count=new Date(first.getFullYear(),first.getMonth()+1,0).getDate();
  const available=new Set(row.quote.calendar_dates||row.quote.dates),excluded=new Set(row.excluded_dates);
  return '<div class="multi-month-grid">'+['Пн','Вт','Ср','Чт','Пт','Сб','Вс'].map(day=>`<span class="multi-weekday">${day}</span>`).join('')+
    Array.from({length:offset},()=>'<span aria-hidden="true"></span>').join('')+
    Array.from({length:count},(_,i)=>{
      const day=row.month+'-'+String(i+1).padStart(2,'0');
      return available.has(day)?`<label class="multi-calendar-choice" title="Занятие ${esc(dShort(day))}"><input type="checkbox" data-multi-day="${day}" data-month="${row.month}" aria-label="Занятие ${esc(dShort(day))}" ${excluded.has(day)?'':'checked'}><span>${i+1}</span></label>`:`<span class="multi-calendar-empty">${i+1}</span>`;
    }).join('')+'</div>';
}


async function openErroneousSubscriptionCancel(subid,box,button,reload) {
  try{
    const q=await api('GET',`/api/subscriptions/${subid}/error-cancel-preview`);
    let plan=null;try{plan=await api('POST',`/api/subscriptions/${subid}/error-lessons-preview`,{})}catch(_){}
    const initial=plan;
    box.innerHTML=`<form class="subscription-cancel-form"><h4>Исправить ошибочный / тестовый абонемент</h4><p>Снимите галочки с ошибочных занятий или удалите весь абонемент. Платежи и отметки посещения сохранятся</p>${plan?`<div class="multi-month-calendars">${plan.groups.map(g=>`<section data-error-group="${g.group_id}" class="multi-month-calendar"><h4>${esc(g.name)}</h4>${multiMonthCalendarHtml({month:plan.month,quote:{calendar_dates:g.dates},excluded_dates:[]})}</section>`).join('')}</div><button type="button" class="btn sm" data-error-all>Снять все — отменить целиком</button>`:'<p class="small muted">Частичная отмена недоступна без сверки точного состава. Можно отменить ошибочный абонемент целиком</p>'}<div class="quote" data-error-totals></div><div class="field"><label>Причина</label><textarea name="reason" required minlength="5" maxlength="1000" placeholder="Например, тестовые занятия добавлены ошибочно"></textarea></div><label class="check"><input name="confirmed" type="checkbox" required> Подтверждаю: выбранные занятия ошибочные или тестовые</label><p class="need-err" role="alert"></p><div class="sc-actions"><button type="submit" class="btn danger" ${plan?'disabled':''}>${plan?'Сохранить изменения':'Отменить абонемент целиком'}</button><button type="button" class="btn" data-error-close>Назад</button></div></form>`;
    const form=box.querySelector('form'),submit=form.querySelector('[type=submit]'),totals=form.querySelector('[data-error-totals]');
    let version=0,busy=false;
    const requestId=window.crypto?.randomUUID?.()||Date.now()+'-'+Math.random().toString(36).slice(2);
    const keep=()=>[...form.querySelectorAll('[data-multi-day]:checked')].map(c=>({group_id:+c.closest('[data-error-group]').dataset.errorGroup,date:c.dataset.multiDay}));
    const draw=()=>{const p=plan||q;totals.innerHTML=`${plan?`<p>Останется занятий: <b>${plan.remaining_lessons}</b> · Стоимость: <b>${rub(plan.new_price)}</b></p><p>Осталось оплатить: <b>${rub(plan.remaining_due)}</b></p>`:''}<p>Учтено оплаты: <b>${rub(p.paid)}</b></p><p>Свободный баланс после исправления: <b>${rub(p.balance_after)}</b></p>${p.applied_to_other_invoices?`<p>Зачтётся в другие абонементы: ${rub(p.applied_to_other_invoices)}</p>`:''}`};
    const preview=async()=>{const current=++version;submit.disabled=true;try{const next=await api('POST',`/api/subscriptions/${subid}/error-lessons-preview`,{keep:keep()});if(current!==version)return;plan=next;draw();submit.textContent=plan.remaining_lessons?'Сохранить выбранные занятия':'Отменить абонемент целиком';submit.disabled=!plan.removed_lessons}catch(err){if(current===version)form.querySelector('.need-err').textContent=err.message}};
    form.querySelectorAll('[data-multi-day]').forEach(c=>c.onchange=preview);
    if(initial)form.querySelector('[data-error-all]').onclick=()=>{form.querySelectorAll('[data-multi-day]').forEach(c=>c.checked=false);preview()};
    form.querySelector('[data-error-close]').onclick=()=>{if(!busy){version++;box.replaceChildren();button.disabled=false}};
    draw();
    form.onsubmit=async e=>{e.preventDefault();if(busy)return;busy=true;submit.disabled=true;form.querySelectorAll('input,button').forEach(c=>c.disabled=true);try{await api('POST',`/api/subscriptions/${subid}/${plan?'error-lessons':'error-cancel'}`,{token:plan?plan.token:q.token,keep:plan?.keep,request_id:requestId,reason:form.elements.reason.value,confirmed_error:form.elements.confirmed.checked});toast('Абонемент исправлен, платежи сохранены');reload()}catch(err){form.querySelector('.need-err').textContent=err.message;busy=false;form.querySelectorAll('input,button').forEach(c=>c.disabled=false)}};
  }catch(e){fail(e);button.disabled=false}
}

document.addEventListener('click',event=>{
  const button=event.target.closest('.subscription-actions button');
  if(button)button.closest('details').open=false;
});


function updateSaleQuoteHtml(container,html,preserveCalendar=false) {
  const calendar=preserveCalendar&&container.querySelector('#sl-calendar');
  if(!calendar){container.innerHTML=html;return}
  const next=document.createElement('div');next.innerHTML=html;
  const nextCalendar=next.querySelector('#sl-calendar');
  if(!nextCalendar){container.innerHTML=html;return}
  for(const input of calendar.querySelectorAll('[data-sale-date]')){
    const updated=nextCalendar.querySelector(`[data-sale-date="${input.dataset.saleDate}"]`);if(!updated)continue;
    input.checked=updated.checked;input.disabled=updated.disabled;
    const label=input.closest('.sale-cal-day'),newLabel=updated.closest('.sale-cal-day');
    label.className=newLabel.className;
    label.querySelector('small').textContent=newLabel.querySelector('small').textContent;
  }
  calendar.lastElementChild.innerHTML=nextCalendar.lastElementChild.innerHTML;
  for(const node of [...container.childNodes])if(node!==calendar)node.remove();
  nextCalendar.remove();calendar.before(...next.childNodes);
}


async function openErroneousClientDelete(d,root) {
  root.hidden=false;root.textContent='Проверяю связи карточки…';
  try{
    const q=await api('GET',`/api/students/${d.student.id}/error-delete-preview`);
    const blockers = (q.blockers || []).map(blocker => {
      const records = (blocker.records || []).map(record => {
        const date = record.date?.length === 10 ? dShort(record.date) : record.date;
        return `<li>${esc([record.id == null ? "" : `№${record.id}`, record.erp_id ? `ERP №${record.erp_id}` : "", date].filter(Boolean).join(" · "))}</li>`;
      });
      const label = blocker.label || blocker.table;
      return `<li><b>${esc(label)}</b> · ${blocker.count}${label === "Связанные записи" || label === "Связанные данные ERP" ? ` <small>(${esc(blocker.table)})</small>` : ""}
        ${records.length ? `<ul>${records.join("")}</ul>` : ""}
        ${records.length === 20 && blocker.count > 20 ? '<small>Показаны первые 20 записей</small>' : ""}</li>`;
    }).join("");
    const blocked = `<p class="need-err">Удаление недоступно. С этой карточкой связаны записи:</p>
      <ul class="history">${blockers}</ul>`;
    root.innerHTML=`<form class="client-profile-form"><h3>Удалить ошибочную карточку</h3><p><b>Только дубликат, тест или ошибка переноса / создания</b></p><p>Нормальные карточки нельзя удалять из-за отказа, паузы или прекращения занятий — для них используйте статус или архив</p>${!q.allowed?blocked:`<div class="field"><label>Основание</label><select name="category" required><option value="">Выберите причину</option>${Object.entries(q.categories || {}).map(([key, label]) => `<option value="${esc(key)}">${esc(label)}</option>`).join("")}</select></div><div class="field"><label>Пояснение к причине</label><textarea name="reason" required minlength="5" maxlength="1000"></textarea></div><label class="check"><input name="confirmed" type="checkbox" required> Подтверждаю, что это ошибочная карточка, а не отказавшийся или ушедший клиент</label>`}<p class="need-err" data-delete-error role="alert"></p><div class="sc-actions">${q.allowed?'<button type="submit" class="btn danger">Удалить карточку</button>':''}<button type="button" class="btn" data-delete-close>Отмена</button></div></form>`;
    const form=root.querySelector('form');root.querySelector('[data-delete-close]').onclick=()=>{root.replaceChildren();root.hidden=true};
    form.onsubmit=async e=>{e.preventDefault();if(!q.allowed)return;const submit=form.querySelector('[type=submit]');submit.disabled=true;try{await api('DELETE',`/api/students/${d.student.id}/error`,{token:q.token,category:form.elements.category.value,reason:form.elements.reason.value,confirmed_error:form.elements.confirmed.checked});toast('Ошибочная карточка удалена');closeDrawer();render()}catch(err){form.querySelector('[data-delete-error]').textContent=err.message;submit.disabled=false}};
    root.scrollIntoView({block:'nearest',behavior:'smooth'});
  }catch(e){root.textContent=e.message}
}


function subscriptionPaymentMoment(value) {
  if(!value)return 'Дата и время не указаны';
  if(/^\d{4}-\d{2}-\d{2}$/.test(value))return value.split('-').reverse().join('.')+' · время не указано';
  const iso=String(value).replace(' ','T');
  const date=new Date(/[Zz]|[+-]\d{2}:?\d{2}$/.test(iso)?iso:iso+'+03:00');
  return isNaN(date)?'Дата и время не указаны':new Intl.DateTimeFormat('ru-RU',{timeZone:'Europe/Moscow',day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'}).format(date)+' МСК';
}
function subscriptionPaymentDetailsHtml(sub,d) {
  const payments=d.subscription_payment_details?.[sub.id]||[];
  if(!payments.length)return '<p class="small muted">Подтверждённые сведения об источнике оплаты и чеке отсутствуют</p>';
  const paykeeperHint=payments.some(p=>!['balance_credit','historical_settlement'].includes(p.record_kind)&&p.method!=='tochka_qr_link')?'<p class="small muted">Проверка чеков PayKeeper выполняется автоматически. Оплата и зачисление на расчётный счёт проверяются отдельно</p>':'';
  return '<div class="subscription-payment-details"><b>Оплата и чеки</b>'+paykeeperHint+payments.map(p=>{
    if(['balance_credit','historical_settlement'].includes(p.record_kind))return `<div class="subscription-payment-line" data-payment-record="${p.record_kind}"><div><strong>${Number.isFinite(p.amount)?rub(p.amount):'Сумма требует сверки'}</strong> · ${esc(p.method_label||(p.record_kind==='balance_credit'?'Зачтено с баланса':'Оплата сохранена по сверке ERP'))}${p.fund_label?`<div class="small muted">${esc(p.fund_label)}</div>`:''}</div><div><span class="pill yellow" data-receipt-status="unverified">${esc(p.receipt?.label||'Чек исходной оплаты требует сверки')}</span></div></div>`;
    const manual=p.method==='tochka_qr_link';
    if(manual){const bank=p.reconciliation?.bank||{},receipt=p.reconciliation?.ofd||{},bankOk=bank.status==='confirmed',receiptOk=receipt.status==='confirmed',complete=bankOk&&receiptOk;return `<div class="subscription-payment-line manual-payment-line"><div><strong>${rub(p.amount)}</strong><div>${esc(p.method_label)}</div>${p.payment_total!==p.amount?`<div class="small muted">Зачтено из платежа ${rub(p.payment_total)}</div>`:''}<div class="small muted">${esc(subscriptionPaymentMoment(p.paid_at))}</div></div><div class="manual-payment-proof"><div class="manual-proof-row"><span>Поступление в Точку</span><span class="pill ${bankOk?'green':'yellow'}">${bankOk?'Подтверждено':'Не подтверждено'}</span>${bank.checked_at?`<small>${esc(subscriptionPaymentMoment(bank.checked_at))}</small>`:''}</div><div class="manual-proof-row"><span>Чек Эвотор</span><span class="pill ${receiptOk?'green':'yellow'}">${receiptOk?'Подтверждён в ОФД':'Не подтверждён'}</span>${p.receipt.receipt_id?`<small>Чек № ${esc(p.receipt.receipt_id)}</small>`:''}</div>${!complete&&can('payments','view')&&Number.isInteger(p.payment_id)&&p.payment_id>0?`<button type="button" class="btn sm" data-manual-payment-check="${p.payment_id}">Проверить оплату и чек</button><div data-manual-check-panel></div>`:''}</div></div>`}
    return `<div class="subscription-payment-line"><div><strong>${rub(p.amount)}</strong> · ${esc(p.method_label)}${p.payment_total!==p.amount?`<div class="small muted">Зачтено из платежа ${rub(p.payment_total)}</div>`:''}<div class="small muted">${esc(subscriptionPaymentMoment(p.paid_at))}</div></div><div><span class="pill ${p.receipt.status==='confirmed'?'green':p.receipt.status==='failed'||p.receipt.status==='missing'?'red':'yellow'}">${esc(p.receipt.label)}</span><div class="small muted" data-fiscal-last-check>${p.receipt.checked_at?'Проверено: '+esc(subscriptionPaymentMoment(p.receipt.checked_at)):''}</div><div class="small muted" data-fiscal-note>${esc(p.receipt.note||'')}</div>${can('payments','view')&&Number.isInteger(p.payment_id)&&p.payment_id>0?(manual?`<button type="button" class="btn sm" data-manual-payment-check="${p.payment_id}">Проверить Точку и чек Эвотор</button><div data-manual-check-panel></div>`:`<button type="button" class="btn sm" data-fiscal-check="${p.payment_id}">Обновить статус чека</button>`):''}${p.receipt.receipt_id?`<div class="small muted">Чек № ${esc(p.receipt.receipt_id)}</div>`:''}${p.reconciliation?`<div class="small"><span class="pill ${p.reconciliation.ofd.status==='confirmed'?'green':'yellow'}">${esc(p.reconciliation.ofd.label)}</span><div class="small muted">${esc(p.reconciliation.bank.label)}</div><div class="small muted">${esc(p.reconciliation.bank.note||'')}${p.reconciliation.bank.checked_at?' · Проверено: '+esc(subscriptionPaymentMoment(p.reconciliation.bank.checked_at)):''}</div></div>`:''}</div></div>`;}).join('')+'</div>';
}


function refundRequestsHtml(rows,desk=false) {
  if(!rows.length)return '';
  const labels={pending:'Ожидает личного решения Юлии',approved:'Одобрено — возврат ещё не выполнен',prepared:'Подготовлен — проверьте возврат и получение денег',rejected:'Отклонено Юлией',received:'Клиент подтвердил получение денег'};
  return `<section class="refund-approval-section"><h3>${desk?'Возвраты на моё согласование':'Заявки на возврат'}</h3>${rows.map(r=>`<article class="refund-request-card"><div><b>${esc(r.child)} · ${rub(r.amount)}</b><div class="small muted">${esc(r.group_name||'')} · ${esc(monthLabel(r.month))} · оформил(а) ${esc(r.created_name)}</div><p>${esc(r.reason)}</p><div class="small muted">${JSON.parse(r.sources_json||'[]').map(p=>`${rub(p.amount)} · ${esc(PAY_METHODS[p.method]||p.method)}${p.pk_payment_id?' · исходный платёж № '+esc(p.pk_payment_id):''}`).join('<br>')}</div><span class="pill ${r.state==='pending'?'yellow':r.state==='rejected'?'red':'green'}">${labels[r.state]||esc(r.state)}</span></div>${r.last_error?`<p class="need-err">${esc(r.last_error)}</p>`:''}<div class="sc-actions">${S.user.role==='admin'&&r.state==='pending'?`<button type="button" class="btn primary" data-refund-review="${r.id}" data-decision="approve">Одобрить ${rub(r.amount)}</button><button type="button" class="btn danger" data-refund-review="${r.id}" data-decision="reject">Отклонить</button>`:''}${r.state==='approved'?`<button type="button" class="btn" data-refund-prepare="${r.id}">Подготовить возврат исходным способом</button>`:''}${r.state==='prepared'?`<button type="button" class="btn" data-refund-received="${r.id}">Подтвердить получение клиентом</button>`:''}</div><p class="small muted">Одобрение не подтверждает возврат денег. Контроль получения ведётся отдельной задачей</p></article>`).join('')}</section>`;
}
function wireRefundRequests(root,reload) {
  root.querySelectorAll('[data-refund-review]').forEach(b=>b.onclick=async()=>{
    const decision=b.dataset.decision;let comment='';
    if(decision==='approve'){if(!confirm('Лично одобрить эту сумму возврата? Деньги этим действием не отправляются'))return}
    else{comment=prompt('Причина отклонения возврата')||'';if(!comment.trim())return}
    b.disabled=true;try{await api('POST',`/api/refund-requests/${b.dataset.refundReview}/review`,{decision,comment});reload()}catch(e){fail(e);b.disabled=false}
  });
  root.querySelectorAll('[data-refund-prepare]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{const r=await api('POST',`/api/refund-requests/${b.dataset.refundPrepare}/prepare`,{});if(!r.ok)toast(r.error,true);else toast('Возврат подготовлен. Выполните его в PayKeeper на исходный платёж и проверьте чек');reload()}catch(e){fail(e);b.disabled=false}});
  root.querySelectorAll('[data-refund-received]').forEach(b=>b.onclick=async()=>{const proof=prompt('Как клиент подтвердил получение денег? Укажите дату и подтверждение')||'';if(proof.trim().length<5)return;b.disabled=true;try{await api('POST',`/api/refund-requests/${b.dataset.refundReceived}/received`,{customer_received:true,proof});reload()}catch(e){fail(e);b.disabled=false}});
}
