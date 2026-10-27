/* Аналитика бизнеса: вкладки дашборда «Филиалы и группы», «Тренеры», «Отток и продления», «Источники» */

const AN_TABS = [["overview", "Обзор"], ["groups", "Филиалы и группы"], ["coaches", "Тренеры"], ["churn", "Отток и продления"], ["sources", "Источники"]];

function anTabsHtml() {
  return `<div class="seg an-tabs">${AN_TABS.map(([k, t]) => `<button data-antab="${k}" class="${(S.dashTab || "overview") === k ? "active" : ""}">${t}</button>`).join("")}</div>`;
}

function wireAnTabs(page) {
  page.querySelectorAll("[data-antab]").forEach((b) => (b.onclick = () => { S.dashTab = b.dataset.antab; render(); }));
}

const anPct = (v) => (v == null ? "—" : v + "%");
function anFillCell(v, kids, cap) {
  const cls = v == null ? "" : v >= 90 ? "hi" : v < 50 ? "lo" : "";
  return `<div class="an-fill ${cls}"><span>${kids}/${cap}</span><i><b style="width:${Math.min(100, v || 0)}%"></b></i></div>`;
}
const anGood = (v, good, bad) => (v == null ? "" : v >= good ? "s-good" : v < bad ? "s-bad" : "");

async function renderAnalytics(box) {
  const part = S.dashTab;
  const q = `/api/analytics?part=${part}&month=${S.dashMonth}${S.dashBranch ? "&branch_id=" + S.dashBranch : ""}`;
  box.innerHTML = `<div class="muted" style="padding:20px">Считаю…</div>`;
  const d = await api("GET", q);
  if (part === "groups") return anGroups(box, d);
  if (part === "coaches") return anCoaches(box, d);
  if (part === "churn") return anChurn(box, d);
  if (part === "sources") return anSources(box, d);
}

function anGroups(box, d) {
  box.innerHTML = `
    <div class="an-cards">${d.branches.map((b) => `<div class="panel an-card">
      <div class="an-card-h">${esc(b.branch)}</div>
      <div class="an-big">${b.kids} <small>из ${b.capacity} мест</small></div>
      ${anFillCell(b.fill, b.fill + "%", "")}
      <div class="an-kv"><span>Выручка</span><b>${rub(b.revenue)}</b></div>
      <div class="an-kv"><span>Продлили с прошлого месяца</span><b class="${anGood(b.retention, 90, 80)}">${anPct(b.retention)}</b></div>
      <div class="an-kv"><span>Посещаемость</span><b class="${anGood(b.attendance, 88, 80)}">${anPct(b.attendance)}</b></div>
      <div class="an-kv"><span>Групп</span><b>${b.groups}</b></div></div>`).join("")}</div>
    <section class="panel"><div class="panel-head">Группы · ${esc(d.month_label)}</div>
      <div class="an-scroll"><table class="tbl an-tbl"><thead><tr><th>Группа</th><th>Тренер</th><th>Учеников</th><th>Продлили</th><th>Посещаемость</th><th>Ушли</th><th class="r">Выручка</th></tr></thead>
      <tbody>${d.groups.map((g) => `<tr><td><b>${esc(g.name)}</b>${S.dashBranch ? "" : `<div class="small muted">${esc(g.branch)}</div>`}</td><td>${esc(g.coach || "—")}</td>
        <td>${anFillCell(g.fill, g.kids, g.capacity)}</td><td class="${anGood(g.retention, 90, 80)}">${anPct(g.retention)}</td>
        <td class="${anGood(g.attendance, 88, 80)}">${anPct(g.attendance)}</td><td>${g.left || ""}</td><td class="r">${rub(g.revenue)}</td></tr>`).join("")}</tbody></table></div>
      <div class="small muted" style="margin-top:8px">Зелёная полоса — группа почти полная (90%+), красная — меньше половины мест. «Продлили» — кто занимался в прошлом месяце и оплатил этот</div>
    </section>`;
}

function anCoaches(box, d) {
  box.innerHTML = `<section class="panel"><div class="panel-head">Тренеры · ${esc(d.month_label)}</div>
    <div class="an-scroll"><table class="tbl an-tbl"><thead><tr><th>Тренер</th><th>Групп</th><th>Учеников</th><th>Продлили</th><th>Посещаемость</th><th>Ушли</th><th class="r">Выручка групп</th></tr></thead>
    <tbody>${d.coaches.map((c) => `<tr><td><b>${esc(c.coach)}</b></td><td>${c.groups}</td><td>${anFillCell(c.fill, c.kids, c.capacity)}</td>
      <td class="${anGood(c.retention, 90, 80)}">${anPct(c.retention)}</td><td class="${anGood(c.attendance, 88, 80)}">${anPct(c.attendance)}</td>
      <td>${c.left || ""}</td><td class="r">${rub(c.revenue)}</td></tr>`).join("")}</tbody></table></div>
    <div class="small muted" style="margin-top:8px">Считается по группам, где тренер указан главным. Удержание и посещаемость показывают, как дети держатся у тренера</div></section>`;
}

function anChurn(box, d) {
  const r = d.renewal;
  const st = { debt: ["Не оплатил", "bad"], left: ["Ушёл", "mute"], silent: ["Пропал", "warn"], invoiced: ["Счёт выставлен", "warn"], none: ["Нет счёта", "bad"] };
  const row = (x) => `<div class="list-row click" data-sid="${x.id}"><div class="grow"><b>${esc(x.name)}</b> <span class="small muted">${esc(x.group)}</span>
      <div class="small muted">с нами ${x.months} мес. · принёс ${rub(x.ltv)}${x.debt ? ` · к оплате ${rub(x.debt)}` : ""}</div></div>
      <span class="an-chip ${st[x.status][1]}">${st[x.status][0]}</span></div>`;
  box.innerHTML = `
    <div class="kpis">
      <div class="kpi"><div class="kpi-head"><span>Отток за ${esc(d.month_label)}</span></div><div class="kpi-value">${anPct(d.churn)}</div>
        <div class="kpi-note">не продлили ${d.left_count} из ${d.prev_holders}, кто занимался в прошлом месяце</div></div>
      <div class="kpi"><div class="kpi-head"><span>Продление на ${esc(d.next_label)}</span></div><div class="kpi-value">${anPct(d.renewal_pct)}</div>
        <div class="kpi-note">оплатили ${r.paid} на ${rub(r.paid_rub)} · ждём оплату ${r.invoiced} на ${rub(r.invoiced_rub)}${r.none ? ` · без счёта ${r.none}` : ""}</div></div>
      <div class="kpi"><div class="kpi-head"><span>Средний срок с нами</span></div><div class="kpi-value">${d.avg_months ?? "—"} <small>мес.</small></div>
        <div class="kpi-note">сколько месяцев в среднем оплачивает ученик</div></div>
      <div class="kpi"><div class="kpi-head"><span>Средний доход с ученика</span></div><div class="kpi-value">${d.avg_ltv != null ? fmtNum(d.avg_ltv) : "—"} <small>₽</small></div>
        <div class="kpi-note">за всё время, пока занимается</div></div>
    </div>
    <div class="chart-grid">
      <section class="panel"><div class="panel-head">Отток по месяцам, %</div>${lineChart(d.labels, d.series, d.prev_series, (t) => Math.round(t))}</section>
      ${d.reasons.length ? `<section class="panel"><div class="panel-head">Причины ухода</div>${d.reasons.map(([k, n]) => `<div class="an-kv"><span>${esc(k)}</span><b>${n}</b></div>`).join("")}</section>` : ""}
    </div>
    <div class="an-two">
      <section class="panel"><div class="panel-head">Не продлили в ${esc(d.month_label)} · ${d.left.length}</div>
        <div class="small muted" style="margin-bottom:8px">Первыми те, у кого счёт выставлен, но не оплачен: их проще вернуть звонком</div>
        ${anList(d.left, row, "Все продлили")}</section>
      <section class="panel"><div class="panel-head">Ещё не оплатили ${esc(d.next_label)} · ${d.waiting.length}</div>
        ${anList(d.waiting, row, "Все оплатили")}</section>
    </div>`;
  box.querySelectorAll("[data-sid]").forEach((el) => (el.onclick = () => openStudent(+el.dataset.sid)));
  box.querySelectorAll("[data-more]").forEach((b) => (b.onclick = () => { b.previousElementSibling.querySelectorAll("[hidden]").forEach((x) => (x.hidden = false)); b.remove(); }));
}

function anList(items, row, empty, n = 10) {
  if (!items.length) return `<div class="empty">${empty}</div>`;
  const html = items.map((x, i) => (i < n ? row(x) : row(x).replace('<div class="list-row', '<div hidden class="list-row'))).join("");
  return `<div class="list" style="box-shadow:none">${html}</div>${items.length > n ? `<button class="btn sm" data-more style="margin-top:8px">Показать ещё ${items.length - n}</button>` : ""}`;
}

function anSources(box, d) {
  const t = d.total;
  const money = (v) => (v ? rub(v) : "—");
  box.innerHTML = `
    <div class="kpis">
      <div class="kpi"><div class="kpi-head"><span>Лидов</span></div><div class="kpi-value">${t.leads}</div><div class="kpi-note">купили ${t.bought}, конверсия ${anPct(t.conv)}</div></div>
      <div class="kpi"><div class="kpi-head"><span>Потрачено на рекламу</span></div><div class="kpi-value">${fmtNum(t.spend)} <small>₽</small></div><div class="kpi-note">из раздела «Расходы» → «Реклама»</div></div>
      <div class="kpi"><div class="kpi-head"><span>Цена лида</span></div><div class="kpi-value">${t.spend && t.leads ? fmtNum(Math.round(t.spend / t.leads)) : "—"} <small>₽</small></div><div class="kpi-note">все траты делим на всех лидов</div></div>
      <div class="kpi"><div class="kpi-head"><span>Цена нового ученика</span></div><div class="kpi-value">${t.spend && t.bought ? fmtNum(Math.round(t.spend / t.bought)) : "—"} <small>₽</small></div><div class="kpi-note">новые принесли ${rub(t.revenue)}</div></div>
    </div>
    <section class="panel"><div class="panel-head">Источники · ${esc(d.month_label)}</div>
      <div class="an-scroll"><table class="tbl an-tbl"><thead><tr><th>Источник</th><th>Лиды</th><th>Пробное</th><th>Пришли</th><th>Купили</th><th>Конверсия</th><th class="r">Траты</th><th class="r">Цена лида</th><th class="r">Цена ученика</th><th class="r">Принесли</th></tr></thead>
      <tbody>${d.sources.map((x) => `<tr><td><b>${esc(x.source)}</b><div class="small muted">${esc(x.channel)}</div></td><td>${x.leads}</td><td>${x.trial}</td><td>${x.came}</td><td><b>${x.bought}</b></td>
        <td class="${anGood(x.conv, 25, 12)}">${anPct(x.conv)}</td><td class="r">${money(x.spend)}</td><td class="r">${money(x.per_lead)}</td><td class="r">${money(x.per_buy)}</td><td class="r">${money(x.revenue)}</td></tr>`).join("")}
      <tr class="an-total"><td>Итого</td><td>${t.leads}</td><td>${t.trial}</td><td>${t.came}</td><td>${t.bought}</td><td>${anPct(t.conv)}</td><td class="r">${money(t.spend)}</td><td></td><td></td><td class="r">${money(t.revenue)}</td></tr></tbody></table></div>
      <div class="small muted" style="margin-top:8px">Лиды, пришедшие в этом месяце. «Принесли» — оплаченные абонементы тех, кто купил впервые в этом месяце</div>
    </section>`;
}
