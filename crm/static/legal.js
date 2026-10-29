"use strict";
/* Абонемент Плюс — юридические документы и согласия. */

const CONSENT_WAYS = { form: "Отметка в форме заявки", bot: "Кнопка в чат-боте", call: "Устно, звонок записан", paper: "Подпись на бумаге", message: "Ответ в мессенджере" };

function consentHtml(f) {
  const pd = f.consent_pd_at, ads = f.consent_ads_at && !f.consent_ads_revoked_at;
  return `<div class="consents">
    <span class="pill ${pd ? "green" : "red"}">${pd ? `Согласие на данные: ${dShort(pd)}, ${esc((CONSENT_WAYS[f.consent_pd_how] || f.consent_pd_how || "").toLowerCase())}` : "Нет согласия на обработку данных"}</span>
    <span class="pill ${ads ? "green" : ""}">${ads ? "Согласен на рассылки" : f.consent_ads_revoked_at ? "Отказался от рассылок" : "Нет согласия на рассылки"}</span>
    <button class="linkish small" id="consent-edit">Изменить</button>
    <div id="consent-form" hidden class="quote" style="margin-top:8px">
      <div class="field"><label>Согласие на обработку данных</label><select id="c-pd"><option value="">Нет согласия</option>${Object.entries(CONSENT_WAYS).map(([k, t]) => `<option value="${k}" ${pd && f.consent_pd_how === k ? "selected" : ""}>${t}</option>`).join("")}</select></div>
      <label class="check"><input type="checkbox" id="c-ads" ${ads ? "checked" : ""}> Согласен получать рекламные сообщения</label>
      <button class="btn sm primary" id="c-save" style="margin-top:8px">Сохранить</button>
      <div class="small muted" style="margin-top:6px">Тексты согласий: <a href="/legal/consent" target="_blank">на обработку данных</a>, <a href="/legal/marketing" target="_blank">на рассылки</a>. Без согласия на рассылки акции и скидки этой семье не отправляем</div>
    </div></div>`;
}

function wireConsent(f, reload) {
  const btn = $("#consent-edit");
  if (!btn) return;
  btn.onclick = () => { $("#consent-form").hidden = !$("#consent-form").hidden; };
  $("#c-save").onclick = async () => {
    try {
      const how = $("#c-pd").value;
      if (how !== (f.consent_pd_at ? f.consent_pd_how : "")) await api("POST", `/api/families/${f.id}/consent`, { kind: "pd", value: !!how, how });
      const ads = $("#c-ads").checked, had = !!(f.consent_ads_at && !f.consent_ads_revoked_at);
      if (ads !== had) await api("POST", `/api/families/${f.id}/consent`, { kind: "ads", value: ads });
      toast("Сохранено"); reload();
    } catch (e) { fail(e); }
  };
}

async function renderLegalSettings(box) {
  const d = await api("GET", "/api/legal");
  const r = d.requisites;
  box.innerHTML = `<h3>Юридические документы</h3>
    <div class="small muted" style="margin-bottom:10px">Документы открываются по ссылкам для всех, без входа в CRM. Ссылку на согласие бот сам добавляет в приветствие, а кнопка «Поделиться номером» фиксирует согласие в карточке семьи</div>
    ${d.missing.length ? `<div class="alert" style="margin-bottom:12px">Чтобы документы были действительными, заполните: ${d.missing.map(esc).join(", ")}. Пока пустые места выделены жёлтым на страницах</div>` : ""}
    <div class="row">${["legal_ip_name", "legal_inn", "legal_ogrnip"].map((k) => `<div class="field"><label>${esc(d.labels[k])}</label><input data-l="${k}" value="${esc(r[k])}"></div>`).join("")}</div>
    <div class="row">${["legal_address", "legal_email", "legal_phone"].map((k) => `<div class="field"><label>${esc(d.labels[k])}</label><input data-l="${k}" value="${esc(r[k])}"></div>`).join("")}</div>
    <div class="row">${["legal_account", "legal_bank"].map((k) => `<div class="field"><label>${esc(d.labels[k])}</label><input data-l="${k}" value="${esc(r[k])}"></div>`).join("")}</div>
    <div class="row">${["legal_bik", "legal_corr"].map((k) => `<div class="field"><label>${esc(d.labels[k])}</label><input data-l="${k}" value="${esc(r[k])}"></div>`).join("")}</div>
    <div class="small muted" style="margin:-4px 0 10px">В поле ФИО пишите без «ИП»: Фамилия Имя Отчество. Слова «Индивидуальный предприниматель» документ добавит сам</div>
    <div class="row">${["legal_school", "legal_site", "legal_date"].map((k) => `<div class="field"><label>${esc(d.labels[k])}</label><input data-l="${k}" value="${esc(r[k])}"></div>`).join("")}</div>
    <div class="list" style="box-shadow:none;margin:6px 0 10px">${d.docs.map((x) => `<div class="list-row"><div class="grow"><b>${esc(x.title)}</b>${x.custom ? ` <span class="pill">изменён</span>` : ""}
      <div class="small"><a href="${esc(x.url)}" target="_blank">${esc(x.url)}</a></div></div>
      <button class="btn sm" data-copy="${esc(x.url)}">Копировать</button><button class="btn sm" data-ledit="${x.slug}">Текст</button></div>
      <div class="ledit" data-lbox="${x.slug}" hidden><textarea rows="14" data-l="text_${x.slug}">${esc(x.text)}</textarea>
        <div class="small muted">Фигурные скобки {legal_inn} и подобные заменяются реквизитами сами</div></div>`).join("")}</div>
    <button class="btn primary" id="l-save">Сохранить</button>`;
  box.querySelectorAll("[data-ledit]").forEach((b) => (b.onclick = () => { const x = box.querySelector(`[data-lbox="${b.dataset.ledit}"]`); x.hidden = !x.hidden; }));
  box.querySelectorAll("[data-copy]").forEach((b) => (b.onclick = async () => { try { await navigator.clipboard.writeText(b.dataset.copy); toast("Скопировано"); } catch (_) {} }));
  $("#l-save").onclick = async () => {
    const data = Object.fromEntries([...box.querySelectorAll("[data-l]")].map((i) => [i.dataset.l, i.value]));
    try { await api("PUT", "/api/legal", data); toast("Сохранено"); renderLegalSettings(box); } catch (e) { fail(e); }
  };
}

/* ---------- оферты: все редакции по программам ---------- */
const dFull = (iso) => iso ? `${iso.slice(8, 10)}.${iso.slice(5, 7)}.${iso.slice(0, 4)}` : "";
const OFFER_STATE = { current: ["green", "действует"], upcoming: ["yellow", "вступит в силу"], past: ["", "прежняя"], archived: ["", "в архиве"] };

async function renderOffers(box) {
  const d = await api("GET", "/api/offers");
  const groups = S.meta.groups.filter((g) => !g.is_makeup);
  const progs = d.programs;
  box.innerHTML = `<h3>Оферты</h3>
    <div class="small muted" style="margin-bottom:10px">Все редакции хранятся, ничего не удаляется. У каждой программы постоянная ссылка на действующую оферту: её можно давать родителям и ставить на сайт, она сама переключится на новую редакцию в день вступления в силу</div>
    ${progs.length ? progs.map((p) => `<section class="panel" style="padding:12px 14px;margin-bottom:10px">
      <div class="team-head"><b>📄 ${esc(p.program)}</b><button class="btn sm" data-copy="${esc(p.link)}">Скопировать ссылку</button></div>
      <div class="small"><a href="${esc(p.link)}" target="_blank">${esc(p.link)}</a></div>
      <div class="list" style="box-shadow:none;margin-top:8px">${p.versions.map((v) => `<div class="list-row ${v.archived ? "muted" : ""}">
        <div class="grow"><b>Редакция от ${dFull(v.edition_date)}</b> <span class="pill ${OFFER_STATE[v.state][0]}">${OFFER_STATE[v.state][1]}${v.state !== "past" && v.state !== "archived" ? ` с ${dFull(v.effective_from)}` : ""}</span>
          <div class="small muted">${v.groups.length ? esc(v.groups.join(", ")) : "все группы"} · загрузил(а) ${esc(v.uploaded_by)} ${dShort(v.uploaded_at)} · ${Math.round(v.size / 1024)} КБ${v.note ? ` · ${esc(v.note)}` : ""}</div></div>
        <a class="btn sm" href="${esc(v.link)}" target="_blank">Открыть</a>
        ${can("docs", "full") ? `<button class="btn sm" data-oarch="${v.id}" data-v="${v.archived ? 0 : 1}" title="${v.archived ? "Вернуть" : "В архив, если загрузили по ошибке"}">${v.archived ? "↩" : "🗄"}</button>` : ""}</div>`).join("")}</div>
    </section>`).join("") : `<div class="muted small" style="margin-bottom:10px">Оферт пока нет</div>`}
    ${can("docs", "full") ? `<button class="btn primary" id="of-new">+ Загрузить оферту</button><div id="of-form"></div>` : ""}`;
  box.querySelectorAll("[data-copy]").forEach((b) => (b.onclick = async () => { try { await navigator.clipboard.writeText(b.dataset.copy); toast("Ссылка скопирована"); } catch (_) {} }));
  box.querySelectorAll("[data-oarch]").forEach((b) => (b.onclick = async () => {
    try { await api("POST", `/api/offers/${b.dataset.oarch}/archive`, { archived: b.dataset.v === "1" }); renderOffers(box); } catch (e) { fail(e); }
  }));
  const nb = box.querySelector("#of-new");
  if (!nb) return;
  nb.onclick = () => {
    const f = box.querySelector("#of-form");
    if (f.innerHTML) { f.innerHTML = ""; return; }
    f.innerHTML = `<div class="sub-form" style="margin-top:10px">
      <div class="field"><label>Программа</label><input data-of="program" list="of-progs" placeholder="Например: Футболика ПРО (сборная)">
        <datalist id="of-progs">${progs.map((p) => `<option value="${esc(p.program)}">`).join("")}</datalist></div>
      <div class="small muted" style="margin:-4px 0 8px">Для новой редакции выберите ту же программу из списка: она встанет в её историю</div>
      <div class="row" style="flex-wrap:wrap">
        <div class="field" style="flex:0 0 170px"><label>Редакция от</label><input type="date" data-of="edition_date" value="${S.meta.today}"></div>
        <div class="field" style="flex:0 0 170px"><label>Вступает в силу</label><input type="date" data-of="effective_from" value="${S.meta.today}"></div>
        <div class="field" style="flex:1 1 220px"><label>Файл PDF</label><input type="file" accept="application/pdf" id="of-file"></div></div>
      <div class="field"><label>Для каких групп</label><div class="chips">${groups.map((g) => `<label class="chip"><input type="checkbox" data-og="${g.id}"> ${esc(g.name)}</label>`).join("")}</div>
        <div class="small muted">Ничего не отмечено = для всех групп</div></div>
      <div class="field"><label>Что изменилось</label><input data-of="note" placeholder="Например: новые цены, ОФП в Небе"></div>
      <button class="btn primary" id="of-save">Сохранить</button></div>`;
    f.querySelector("#of-save").onclick = async () => {
      const file = f.querySelector("#of-file").files[0];
      if (!file) return toast("Выберите PDF");
      const b64 = await new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(file); });
      const data = Object.fromEntries([...f.querySelectorAll("[data-of]")].map((i) => [i.dataset.of, i.value]));
      data.group_ids = [...f.querySelectorAll("[data-og]:checked")].map((i) => +i.dataset.og);
      data.file_base64 = b64; data.file_name = file.name;
      try { await api("POST", "/api/offers", data); toast("Оферта загружена"); renderOffers(box); } catch (e) { fail(e); }
    };
  };
}
