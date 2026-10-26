"use strict";
/* Абонемент Плюс — рекламные каналы и источники. */

function sourceSelect(name, value, legacy) {
  const ad = S.meta.ad || { channels: [], sources: [] };
  const groups = ad.channels.map((c) => {
    const list = ad.sources.filter((x) => x.channel_id === c.id);
    return list.length ? `<optgroup label="${esc(c.name)}">${list.map((x) => `<option value="${x.id}" ${x.id === value ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</optgroup>` : "";
  }).join("");
  return `<select ${name.startsWith("f-") ? `id="${name}"` : `name="${name}"`}><option value="">${legacy && !value ? esc(legacy) : "— не указано —"}</option>${groups}</select>`;
}

async function renderSourceSettings(box) {
  const d = await api("GET", "/api/sources?all=1");
  const byCh = d.channels.map((c) => ({ c, list: d.sources.filter((x) => x.channel_id === c.id) }));
  box.innerHTML = `
    <h3>Рекламные каналы и источники</h3>
    <div class="small muted" style="margin-bottom:10px">Источник выбирают при создании клиента. По ним строится отчёт на дашборде: сколько лидов, пробных и покупок дал каждый</div>
    ${byCh.map(({ c, list }) => `<details class="src-ch" ${list.length && list.length < 6 ? "open" : ""}><summary><b>${esc(c.name)}</b> <span class="muted small">${list.filter((x) => x.active).length}</span></summary>
      <div class="chips" style="margin:8px 0 4px">${list.map((x) => `<button class="chip ${x.active ? "" : "struck"}" data-src="${x.id}" data-active="${x.active ? 0 : 1}" title="${x.active ? "Нажмите, чтобы убрать в архив" : "Нажмите, чтобы вернуть"}">${esc(x.name)}${x.active ? "" : " · архив"}</button>`).join("") || `<span class="small muted">Пока нет источников</span>`}</div></details>`).join("")}
    <div class="row" style="margin-top:12px">
      <div class="field" style="flex:0 0 160px"><label>Канал</label><select id="s-ch">${d.channels.map((c) => `<option>${esc(c.name)}</option>`).join("")}</select></div>
      <div class="field"><label>Новый источник</label><input id="s-name" placeholder="Например, Промоутер Аня ноябрь 2026"></div>
      <div class="field" style="flex:0 0 auto"><label>&nbsp;</label><button class="btn primary" id="s-add">Добавить</button></div>
    </div>
    <details><summary class="small muted">Перенести список из ERP</summary>
      <div class="small muted" style="margin:8px 0">Вставьте строки вида «Канал;Источник», по одной на строку. Дубли пропустятся</div>
      <textarea id="s-imp" rows="5" placeholder="Сайт;Заявка через форму с сайта Футболики"></textarea>
      <button class="btn sm" id="s-impbtn" style="margin-top:8px">Загрузить</button></details>`;
  const refresh = async () => { S.meta = await api("GET", "/api/meta"); renderSourceSettings(box); };
  box.querySelectorAll("[data-src]").forEach((b) => (b.onclick = async () => { await api("POST", `/api/sources/${b.dataset.src}/archive`, { active: b.dataset.active === "1" }).catch(fail); refresh(); }));
  $("#s-add").onclick = async () => { try { await api("POST", "/api/sources", { channel: $("#s-ch").value, name: $("#s-name").value }); toast("Источник добавлен"); refresh(); } catch (e) { fail(e); } };
  $("#s-impbtn").onclick = async () => { try { const r = await api("POST", "/api/sources/import", { text: $("#s-imp").value }); toast(`Добавлено источников: ${r.added}`); refresh(); } catch (e) { fail(e); } };
}

async function renderSourceReport(box, month) {
  if (S.user.role !== "admin") return;
  const [y, m] = month.split("-").map(Number);
  const last = new Date(y, m, 0).getDate();
  const d = await api("GET", `/api/reports/sources?from=${month}-01&to=${month}-${String(last).padStart(2, "0")}`);
  const row = (x, name) => `<tr><td>${name}</td><td>${x.leads}</td><td>${x.trial}</td><td>${x.came}</td><td><b>${x.bought}</b></td><td>${x.leads ? Math.round(100 * x.bought / x.leads) + "%" : "—"}</td></tr>`;
  box.innerHTML = `<section class="panel" style="margin-top:14px"><h3>Откуда пришли лиды · ${esc(monthLabel(month))}</h3>
    ${d.sources.length ? `<div class="table-wrap"><table class="optable"><thead><tr><th>Канал</th><th>Лидов</th><th>Записаны на пробное</th><th>Пришли</th><th>Купили</th><th>Из лида в покупку</th></tr></thead>
      <tbody>${d.channels.map((c) => row(c, `<b>${esc(c.channel)}</b>`)).join("")}</tbody></table></div>
      <details style="margin-top:10px"><summary class="small muted">По каждому источнику</summary><div class="table-wrap"><table class="optable"><thead><tr><th>Источник</th><th>Лидов</th><th>Пробное</th><th>Пришли</th><th>Купили</th><th>Конверсия</th></tr></thead>
      <tbody>${d.sources.map((x) => row(x, `${esc(x.source)} <span class="small muted">${esc(x.channel)}</span>`)).join("")}</tbody></table></div></details>`
    : `<div class="small muted">В этом месяце лидов пока нет</div>`}</section>`;
}
