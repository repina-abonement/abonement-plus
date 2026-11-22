"use strict";

/* Owner-only recovery of a reviewed ERP sale set; never guesses payment links. */
function erpSingleRecoverySetup(box) {
  if (!box || !S.user || S.user.role !== "admin") return;
  const panel = document.createElement("details");
  panel.className = "erp-step"; panel.id = "erp-single-recovery";
  panel.innerHTML = `<summary><b>Восстановить разовые занятия</b></summary>
    <p class="small">Сверьте отдельные продажи ERP с уже внесёнными покупками и оплатами CRM. Проверка сначала покажет изменения; восстановление выполняется отдельной кнопкой</p>
    <div class="row" style="gap:8px;align-items:end;flex-wrap:wrap"><div class="field"><label for="esr-erp-id">ID клиента ERP</label><input id="esr-erp-id" inputmode="numeric" value="133513" autocomplete="off"></div><button type="button" class="btn" data-esr-load>Загрузить данные для сверки</button></div>
    <div data-esr-history></div><div data-esr-context></div><div data-esr-plan></div><div class="small" data-esr-status role="status" aria-live="polite"></div>`;
  const full = box.querySelector("#erp-full");
  if (full) full.after(panel); else box.append(panel);
  const historyBox = panel.querySelector("[data-esr-history]"), contextBox = panel.querySelector("[data-esr-context]"), planBox = panel.querySelector("[data-esr-plan]"), status = panel.querySelector("[data-esr-status]");
  const load = panel.querySelector("[data-esr-load]"), input = panel.querySelector("#esr-erp-id");
  let context = null, checked = null, lastRun = null, busy = false;
  const text = value => esc(String(value ?? ""));
  const money = value => Number.isFinite(Number(value)) ? rub(Number(value)) : "не подтверждено";
  const value = (obj, keys) => { for (const key of keys) if (obj && obj[key] !== undefined && obj[key] !== null) return obj[key]; return undefined; };
  const date = raw => raw ? text(String(raw).slice(0, 10).split("-").reverse().join(".")) : "не указана";
  const week = raw => String(raw || "").split(",").map(x => ({0:"понедельник",1:"вторник",2:"среда",3:"четверг",4:"пятница",5:"суббота",6:"воскресенье"})[x.trim()] || x.trim()).join(", ");
  const issueText = issue => typeof issue === "string" ? issue : issue?.reason || issue?.message || "Нужно проверить исходные данные";
  const issuesHtml = issues => issues?.length ? `<ul class="small">${issues.map(x => `<li>${text(issueText(x))}</li>`).join("")}</ul>` : "";
  const setBusy = state => {
    busy = state;
    panel.querySelectorAll("button,input,select,textarea").forEach(el => { el.disabled = state; });
    if (!state) updateApply();
  };
  const updateApply = () => {
    const button = planBox.querySelector("[data-esr-apply]"), confirm = planBox.querySelector("[data-esr-confirm]");
    if (button) button.disabled = busy || !checked || !confirm?.checked;
    const rollbackButton = historyBox.querySelector("[data-esr-rollback]"), rollbackConfirm = historyBox.querySelector("[data-esr-rollback-confirm]");
    if (rollbackButton) rollbackButton.disabled = busy || !lastRun?.can_rollback || !lastRun?.rollback_token || !rollbackConfirm?.checked;
  };
  const invalidate = () => { checked = null; planBox.innerHTML = ""; status.textContent = "Выбор изменён. Сначала проверьте план восстановления ещё раз"; };
  const summaryHtml = summary => {
    if (!summary) return '<p class="small">Данные не подтверждены</p>';
    const entries = [
      ["Покупок", value(summary, ["sales_count", "purchase_count", "subscriptions_count"])],
      ["Занятий", value(summary, ["lessons_count", "lessons"])],
      ["Стоимость покупок", value(summary, ["sales_total", "purchase_total", "price_total", "total"])],
      ["Оплат", value(summary, ["payment_count"])],
      ["Учтено оплат", value(summary, ["payment_total", "payments_total", "paid_total"])],
      ["Общий долг карточки", value(summary, ["debt", "debt_total"])],
      ["Баланс карточки", value(summary, ["deposit"])],
    ].filter(([, v]) => v !== undefined);
    const dates = value(summary, ["calendar_dates", "dates"]), events = summary.calendar_events;
    const eventLabel = kind => ({single:"Разовое",subscription:"Абонемент",visit:"Посещение",trial:"Пробное",makeup:"Отработка"})[kind] || "Вид занятия не определён";
    return `<dl class="small">${entries.map(([label, v]) => `<dt>${label}</dt><dd>${label.includes("Стоимость") || label.includes("оплат") || (label === "Общий долг карточки" || label === "Баланс карточки") ? money(v) : text(v)}</dd>`).join("")}</dl>${Array.isArray(dates) ? `<p class="small">Даты в календаре затронутых групп: ${dates.length ? dates.map(d => date(typeof d === "object" ? d.date : d)).join(", ") : "дат нет"}</p>` : ""}${Array.isArray(events) ? `<details data-esr-calendar><summary>Занятия в календаре · ${events.length}</summary><ul class="small">${events.map(e => `<li data-esr-calendar-kind="${text(e.kind || "")}">${date(e.date)} · ${text(eventLabel(e.kind))} · ${text(e.time || "время не указано")}${e.group_id ? " · группа №" + text(e.group_id) : ""}</li>`).join("")}</ul></details>` : ""}`;
  };
  const runHtml = r => {
    const canRollback = r.state === "applied" && r.can_rollback === true && !!r.rollback_token;
    return `<section class="panel"><h4>${r.state === "rolled_back" ? "Последнее восстановление отменено" : "Последнее восстановление"}</h4><p class="small">Номер операции: ${text(r.run_id)}${r.created_at ? " · " + text(r.created_at.replace("T", " ")) : ""}${r.user_name ? " · " + text(r.user_name) : ""}${r.backup_created ? ". Резервная копия сохранена" : ""}</p>
      ${r.state === "applied" ? `<details><summary>Состав до восстановления</summary>${summaryHtml(r.before_summary)}</details><b>После восстановления</b>${summaryHtml(r.after_summary)}` : '<p class="small">Для новой проверки используйте перечитанные данные ниже</p>'}
      ${canRollback ? '<details><summary>Отменить это восстановление</summary><p class="small">Отмена относится только к этой операции. CRM проверит, что записи после неё не были изменены</p><label class="check"><input type="checkbox" data-esr-rollback-confirm> Отменить именно это восстановление</label><button type="button" class="btn" data-esr-rollback disabled>Вернуть состояние до восстановления</button></details>' : r.state === "applied" ? '<p class="small">Автоматическая отмена недоступна: после восстановления записи изменились либо подтверждение отмены не получено. Последующие правки сохраняются</p>' : ''}</section>`;
  };
  const drawRun = r => {
    lastRun = r || null; historyBox.innerHTML = r ? runHtml(r) : "";
    const confirm = historyBox.querySelector("[data-esr-rollback-confirm]"), button = historyBox.querySelector("[data-esr-rollback]");
    if (confirm && button) { confirm.onchange = updateApply; button.onclick = rollback; }
  };
  const drawContext = d => {
    const sales = Array.isArray(d.sales) ? d.sales : [], payments = Array.isArray(d.payments) ? d.payments : [];
    if (!sales.length) throw new Error("Не получен подтверждённый состав продаж ERP. Восстановление пока недоступно");
    contextBox.innerHTML = `<h4>${text(d.student?.name || "Карточка клиента")} · ERP ${text(d.student?.erp_id || d.erp_id || input.value)}</h4>
      <p class="small">Группа для этого случая: «Небо», среда 18:45. Время в назначении платежа не используется для выбора группы</p>
      ${d.case?.payment_date ? `<p class="small">Оплаты в источнике: ${date(d.case.payment_date)}${d.case.payment_method === "card" ? " · картой" : ""}. Связи с оплатами ниже проверяются по их идентификаторам</p>` : ''}
      ${d.provenance ? `<details><summary>Источник данных и версия CRM</summary><p class="small">Работающий процесс: ${text(d.provenance.running_commit || "не определён")}<br>Код на сервере: ${text(d.provenance.checkout_commit || "не определён")}<br>Данные прочитаны: ${text(d.provenance.loaded_at || "не указано")}</p></details>` : ''}
      ${issuesHtml(d.issues)}
      <div class="field"><label for="esr-group">Подтверждённая группа</label><select id="esr-group"><option value="">Выберите группу</option>${(d.groups || []).map(g => `<option value="${text(g.id)}">${text([g.branch_name, g.name, g.weekdays_label || week(g.weekdays), g.time].filter(Boolean).join(" · "))} · №${text(g.id)}</option>`).join("")}</select></div>
      <div class="field"><label for="esr-aggregate">Прежняя объединённая покупка CRM</label><select id="esr-aggregate"><option value="">Выберите запись для восстановления</option>${(d.aggregates || []).map(a => `<option value="${text(a.id)}">№${text(a.id)} · ${text(a.month)} · ${money(a.price)} · ${text(a.lessons)} занятий · ${text(({paid:"оплачено",invoiced:"к оплате",cancelled:"отменено"})[a.status] || a.status)}</option>`).join("")}</select></div>
      <div style="overflow:auto"><table class="tbl"><thead><tr><th>Продажа ERP</th><th>Дата в источнике → сейчас</th><th>Сумма</th><th>Уже внесённая оплата CRM</th></tr></thead><tbody>${sales.map(s => `<tr><td><b>${text(s.sale_id)}</b><div class="small">Счёт: ${text(s.invoice_id || "не указан")}<br>Операция ERP: ${text(s.operation_id || "не указана")}</div></td><td>${date(s.source_date)} → ${date(s.effective_date || s.source_date)}</td><td>${money(s.amount)}</td><td><select data-esr-payment="${text(s.sale_id)}" aria-label="Оплата для продажи ${text(s.sale_id)}"><option value="">Выберите подтверждённую оплату</option>${payments.map(p => `<option value="${text(p.id)}" ${p.cancelled ? "disabled" : ""}>№${text(p.id)} · ${money(p.amount)} · ${date(p.paid_at)}${p.operation_id ? " · ERP " + text(p.operation_id) : ""}${p.source_sale_id ? " · продажа " + text(p.source_sale_id) : ""}${p.cancelled ? " · отменена" : ""}</option>`).join("")}</select></td></tr>`).join("")}</tbody></table></div>
      <p class="small muted">Одинаковые суммы и даты не доказывают связь оплат с продажами. Выберите каждую связь по подтверждённым сведениям; новых платежей CRM не создаёт</p>
      <details><summary>Сведения об оплатах для сопоставления</summary>${payments.map(p => `<p class="small"><b>Оплата CRM №${text(p.id)}</b> · ${money(p.amount)} · ${date(p.paid_at)} · ${text(p.method || "")}<br>${text(p.comment || "Без комментария")}</p>`).join("") || '<p class="small">Оплаты не найдены</p>'}</details>
      <div class="field"><label for="esr-reason">Основание восстановления</label><textarea id="esr-reason" rows="2" placeholder="Укажите проверенный источник и основание переноса даты">${text(d.case?.reason || "")}</textarea></div>
      <label class="check"><input type="checkbox" data-esr-mapping-reviewed> Проверила группу, состав продаж и связи с уже учтёнными оплатами</label>
      <button type="button" class="btn" data-esr-check style="margin-top:10px">Проверить план восстановления</button>`;
    contextBox.querySelectorAll("input,select,textarea").forEach(el => { el.addEventListener("input", invalidate); el.addEventListener("change", invalidate); });
    contextBox.querySelector("[data-esr-check]").onclick = check;
  };
  const requestBody = () => {
    if (!context) throw new Error("Сначала загрузите данные для сверки");
    const group_id = Number(contextBox.querySelector("#esr-group").value), aggregate_id = Number(contextBox.querySelector("#esr-aggregate").value);
    const mapping_reviewed = contextBox.querySelector("[data-esr-mapping-reviewed]").checked, reason = contextBox.querySelector("#esr-reason").value.trim(), payment_mapping = {};
    if (!group_id || !aggregate_id) throw new Error("Выберите подтверждённую группу и прежнюю покупку CRM");
    contextBox.querySelectorAll("[data-esr-payment]").forEach(select => { if (select.value) payment_mapping[select.dataset.esrPayment] = Number(select.value); });
    if (Object.keys(payment_mapping).length !== context.sales.length) throw new Error("Выберите уже учтённую оплату для каждой продажи ERP");
    if (new Set(Object.values(payment_mapping)).size !== Object.keys(payment_mapping).length) throw new Error("Одна оплата выбрана для разных продаж. Проверьте связи");
    if (!mapping_reviewed) throw new Error("Подтвердите, что проверили состав продаж и связи оплат");
    if (!reason) throw new Error("Укажите основание восстановления");
    return {erp_id: String(context.student?.erp_id || context.erp_id || input.value.trim()), group_id, aggregate_id, payment_mapping, mapping_reviewed, reason};
  };
  async function check() {
    checked = null; planBox.innerHTML = "";
    try {
      const body = requestBody(); setBusy(true); status.textContent = "Проверяем план без изменения рабочей карточки…";
      const r = await api("POST", "/api/erp/singles/recovery/plan", body);
      const ready = r.can_apply === true && r.calendar_verified !== false && typeof r.token === "string" && !!r.token && r.before_summary && r.after_summary;
      planBox.innerHTML = `<h4>План восстановления</h4><div class="row" style="gap:24px;align-items:start;flex-wrap:wrap"><section><b>Сейчас</b>${summaryHtml(r.before_summary)}</section><section><b>После восстановления</b>${summaryHtml(r.after_summary)}</section></div>${r.calendar_verified === true ? '<p class="small pill green">Календарь и учтённая оплата в журнале проверены на копии</p>' : r.calendar_verified === false ? '<p class="small">Проверка календаря и учтённой оплаты не пройдена. Восстановление заблокировано</p>' : ""}${issuesHtml(r.issues)}${ready ? '<p class="small">Перед изменением CRM сохранит резервную копию. Проверьте план выше</p><label class="check"><input type="checkbox" data-esr-confirm> Подтверждаю восстановление по этому плану</label><button type="button" class="btn primary" data-esr-apply disabled>Восстановить проверенные разовые</button>' : '<p class="small">План требует разбора. Рабочая карточка не изменена</p>'}`;
      if (ready) {
        checked = {body, token: r.token};
        planBox.querySelector("[data-esr-confirm]").onchange = updateApply;
        planBox.querySelector("[data-esr-apply]").onclick = apply;
      }
      status.textContent = ready ? "План проверен. Для изменения карточки подтвердите его выше." : "Есть расхождения. Восстановление недоступно до их проверки.";
    } catch (e) { status.textContent = e.message || "Не удалось проверить план"; }
    finally { setBusy(false); }
  }
  async function apply() {
    if (!checked || !planBox.querySelector("[data-esr-confirm]")?.checked || busy) return;
    const selected = checked; checked = null; setBusy(true); status.textContent = "Сохраняем резервную копию и восстанавливаем проверенные записи…";
    try {
      const r = await api("POST", "/api/erp/singles/recovery/apply", {...selected.body, token: selected.token});
      if (!r.run_id || !r.after_summary) throw new Error("CRM не вернула полный результат восстановления. Проверьте рабочую карточку перед повтором");
      contextBox.innerHTML = ""; planBox.innerHTML = "";
      drawRun({...r, state:"applied", can_rollback:!!r.rollback_token});
      status.textContent = "Готово. Новые оплаты не создавались. Проверьте состав покупок и календарь в карточке клиента";
    } catch (e) { status.textContent = e.message || "Не удалось восстановить записи. Перед повтором снова проверьте план"; }
    finally { setBusy(false); }
  }
  async function rollback() {
    if (!lastRun?.run_id || !lastRun.can_rollback || !lastRun.rollback_token || !historyBox.querySelector("[data-esr-rollback-confirm]")?.checked || busy) return;
    setBusy(true); status.textContent = "Проверяем возможность отмены этого восстановления…";
    let rolledBack = false;
    try {
      const body = {run_id:lastRun.run_id}; if (lastRun.rollback_token) body.token = lastRun.rollback_token;
      const r = await api("POST", `/api/erp/singles/recovery/${encodeURIComponent(lastRun.run_id)}/rollback`, body);
      if (r.state !== "rolled_back") throw new Error("Отмена восстановления не подтверждена");
      if (!r.after_summary) throw new Error("CRM не вернула полный результат отмены. Проверьте карточку перед повтором");
      rolledBack = true; checked = null; context = null; contextBox.innerHTML = ""; planBox.innerHTML = ""; drawRun({...r,can_rollback:false});
      context = await api("GET", "/api/erp/singles/recovery/context?erp_id=" + encodeURIComponent(input.value.trim()));
      drawContext(context); drawRun(context.last_run || context.runs?.[0] || {...r,can_rollback:false});
      status.textContent = "Состояние до этой операции восстановлено. Данные перечитаны — можно проверить новый план. Новые платежи не создавались";
    } catch (e) { status.textContent = rolledBack ? "Отмена выполнена, но данные не удалось перечитать. Нажмите «Загрузить данные для сверки»." : e.message || "Отмена недоступна: записи могли измениться. Нужна повторная сверка"; }
    finally { setBusy(false); }
  }
  load.onclick = async () => {
    if (busy) return;
    const erp = input.value.trim(); if (!/^\d+$/.test(erp)) { status.textContent = "Укажите числовой ID клиента ERP"; return; }
    checked = null; context = null; lastRun = null; contextBox.innerHTML = ""; planBox.innerHTML = ""; historyBox.innerHTML = ""; setBusy(true); status.textContent = "Читаем существующие записи CRM…";
    try { context = await api("GET", "/api/erp/singles/recovery/context?erp_id=" + encodeURIComponent(erp)); drawContext(context); drawRun(context.last_run || context.runs?.[0]); status.textContent = "Выберите группу, прежнюю покупку и подтверждённые связи оплат"; }
    catch (e) { context = null; status.textContent = e.message || "Не удалось получить данные для сверки"; }
    finally { setBusy(false); }
  };
  input.oninput = () => { checked = null; context = null; lastRun = null; contextBox.innerHTML = ""; planBox.innerHTML = ""; historyBox.innerHTML = ""; status.textContent = "Загрузите данные для указанного ID ERP"; };
}
