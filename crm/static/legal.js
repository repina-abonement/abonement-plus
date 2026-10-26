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
