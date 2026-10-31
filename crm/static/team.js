/* Раздел «Команда»: оргструктура, зоны ответственности, модели оплаты */

const BR_DOT = ["#23a26d", "#2c6fd1", "#d99a0b", "#7a5bc7", "#d1553f", "#2a9d9b"];

async function renderTeam(page) {
  const t = await api("GET", "/api/team");
  const U = t.users, admin = can("team", "full");
  const brIdx = Object.fromEntries(t.branches.map((b, i) => [b.id, i]));
  const brName = Object.fromEntries(t.branches.map((b) => [b.id, b.name]));
  const ids = (k) => (t.members[k] || []).filter((id) => U[id]);
  const sub = (u, fallback) => u.nickname || fallback;
  const dots = (u) => u.branches.map((b) => `<i class="tm-dot" style="background:${BR_DOT[brIdx[b] % BR_DOT.length]}" title="${esc(brName[b] || "")}"></i>`).join("");
  const person = (id, role) => { const u = U[id]; return `<button class="tm-person" data-prof="${id}">${avatar(id, u.name, 34)}
      <span class="grow"><b>${esc(u.name)}</b><small>${esc(sub(u, role))}</small></span><span class="tm-dots">${dots(u)}</span></button>`; };
  const node = (id, role, dark) => id && U[id] ? `<button class="tm-node ${dark ? "dark" : ""}" data-prof="${id}">${avatar(id, U[id].name, 36)}<span><b>${esc(U[id].name)}</b><small>${esc(role)}</small></span></button>` : "";
  const dept = (key, title, color, role) => ids(key).length ? `<div class="tm-dept"><div class="tm-dept-h"><i style="background:${color}"></i>${esc(title)}<span>${ids(key).length}</span></div>
      ${ids(key).map((id) => person(id, role)).join("")}</div>` : "";
  const stack = (key) => { const l = ids(key); return `<span class="tm-stack">${l.slice(0, 4).map((id) => avatar(id, U[id].name, 28)).join("")}${l.length > 4 ? `<span class="tm-more">+${l.length - 4}</span>` : ""}</span>`; };
  const cp = t.coach_pay, pc = (v) => String(v).replace(".", ",");
  const ex = 11000;
  const payLines = (txt) => (txt || "").split("\n").filter(Boolean).map((l) => `<div class="tm-pay-line">${esc(l)}</div>`).join("");

  page.innerHTML = `
    <div class="page-head"><h2 class="grow">Команда</h2>${admin ? `<button class="btn sm" id="tm-edit">Изменить</button>` : ""}</div>

    <section class="tm-sec"><div class="tm-h"><div><b>Оргструктура</b><small>кто кому подчиняется</small></div></div>
      <div class="tm-tree">
        ${node(ids("owner")[0], "Руководитель", true)}
        <i class="tm-line"></i>
        <div class="tm-level">
          <div class="tm-col tm-main">
            ${ids("control").length ? `${node(ids("control")[0], "Старший администратор")}<i class="tm-line"></i>` : ""}
            <div class="tm-depts">
              ${dept("admins", "Администрирование", "#d99a0b", "Администратор")}
              ${dept("coaches", "Тренеры", "#23a26d", "Тренер")}
              ${dept("promoters", "Промоутеры", "#2c6fd1", "Промоутер")}
            </div>
          </div>
          ${ids("lawyers").length ? `<div class="tm-col">${ids("lawyers").map((id) => node(id, "Юрист")).join("")}</div>` : ""}
        </div>
      </div>
    </section>

    <section class="tm-sec"><div class="tm-h"><div><b>Зоны ответственности</b><small>к кому идти с вопросом</small></div></div>
      <div class="tm-zones">${t.zones.map((z) => `<div class="tm-zone" style="--zc:${z.color}">
        <div class="tm-zone-h"><i></i><b>${esc(z.title)}</b>${stack(z.key)}</div>
        <ul>${z.items.slice(0, 3).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
        ${z.items.length > 3 ? `<details class="tm-zmore"><summary>ещё ${z.items.length - 3}</summary><ul>${z.items.slice(3).map((x) => `<li>${esc(x)}</li>`).join("")}</ul></details>` : ""}</div>`).join("")}</div>
    </section>

    ${cp || t.pay.admins || t.pay.promoters ? `<section class="tm-sec"><div class="tm-h"><div><b>${admin ? "Модели оплаты" : "Моя модель оплаты"}</b><small>как считается вознаграждение</small></div></div>
      <div class="tm-paygrid">
        ${cp ? `<div class="tm-paycard wide"><div class="tm-pay-h">Тренеры ${stack("coaches")}</div>
          <div class="tm-tiles">
            <div class="tm-tile"><b>${pc(cp.pct.main)} %</b><small>главный тренер</small></div>
            <div class="tm-tile"><b>${pc(cp.pct.assistant)} %</b><small>помощник</small></div>
            <div class="tm-tile alt"><b>${pc(cp.pct.solo)} %</b><small>если тренер один</small></div>
          </div>
          <div class="small muted center">от выручки группы за месяц, поделённой на тренировки</div>
          ${admin ? `<details class="tm-example-fold"><summary>Пример расчёта</summary><div class="tm-example"><b>Пример: 10 детей, 1100 ₽ за занятие</b>
            <div><span>выручка за тренировку</span><b>${rub(ex)}</b></div>
            <div><span>главному · ${pc(cp.pct.main)} %</span><b>${rub(Math.round(ex * cp.pct.main / 100))}</b></div>
            <div><span>помощнику · ${pc(cp.pct.assistant)} %</span><b>${rub(Math.round(ex * cp.pct.assistant / 100))}</b></div>
            <div><span>если один · ${pc(cp.pct.solo)} %</span><b>${rub(Math.round(ex * cp.pct.solo / 100))}</b></div></div></details>` : ""}
          <div class="tm-tiles">
            <div class="tm-tile"><b>${rub(cp.makeup)}</b><small>группа отработок, если кто-то пришёл</small></div>
            ${cp.program ? `<div class="tm-tile alt"><b>${pc(cp.program.pct)} %</b><small>${esc(cp.program.name)}: от выручки направления</small></div>` : ""}
            <div class="tm-tile warn"><b>−${rub(cp.late_fine)}</b><small>опоздание на смену</small></div>
          </div></div>` : ""}
        ${t.pay.admins ? `<div class="tm-paycard"><div class="tm-pay-h">Администраторы ${stack("admins")}</div>${payLines(t.pay.admins)}</div>` : ""}
        ${t.pay.promoters ? `<div class="tm-paycard"><div class="tm-pay-h">Промоутеры ${stack("promoters")}</div>${payLines(t.pay.promoters)}</div>` : ""}
      </div></section>` : ""}`;
  page.querySelectorAll("[data-prof]").forEach((el) => (el.onclick = () => openStaffProfile(+el.dataset.prof)));
  if ($("#tm-edit")) $("#tm-edit").onclick = () => editTeam(t);
  if (!admin) {  // сотрудник: «Моя зарплата» сверху, над оргструктурой
    page.querySelector(".page-head").insertAdjacentHTML("afterend", `<section class="tm-sec" id="my-pay"><div class="muted small">Считаю зарплату…</div></section>`);
    renderMyPay($("#my-pay")).catch((e) => { $("#my-pay").remove(); });
  }
}

/* «Моя зарплата» (mypay.py): переключатель «Где я сейчас / Весь месяц», полоса месяца, цель */
async function renderMyPay(box, mode) {
  const d = await api("GET", "/api/my-pay");
  mode = mode || box.dataset.mode || "now";
  box.dataset.mode = mode;
  const P = d.parts, big = mode === "now" ? d.now : d.forecast;
  const pos = Math.round(100 * d.day / d.days);
  const rows = [];
  if (P.coach) rows.push(["Тренировки", mode === "now" ? `${P.coach.lessons_done} из ${P.coach.lessons}` : `${P.coach.lessons} за месяц`, mode === "now" ? P.coach.now : P.coach.month]);
  if (P.coach && P.coach.fines) rows.push(["Штрафы за опоздание", "", -P.coach.fines]);
  if (P.admin) rows.push(["Смены", mode === "now" ? `${P.admin.shifts_done} из ${P.admin.shifts}` : `${P.admin.shifts} по графику`, (mode === "now" ? P.admin.shifts_done : P.admin.shifts) * P.admin.rates.shift],
    ["Записи на пробное", String(P.admin.trials), P.admin.trials * P.admin.rates.trial], ["Покупки после пробного", String(P.admin.sales), P.admin.sales * P.admin.rates.sale]);
  if (P.promoter) rows.push(["Контакты", String(P.promoter.contacts), P.promoter.now]);
  const n = d.need || {};
  const goalTxt = !d.goal ? "" : n.gap <= 0 && !(n.contacts > 0) ? `<div class="mp-goal ok">Цель ${rub(d.goal)} уже в кармане по прогнозу 🎉</div>` : `<div class="mp-goal">
      До цели ${rub(d.goal)} не хватает <b>${rub(n.gap || Math.max(0, d.goal - d.now))}</b>.
      ${n.kids ? `<div>Это ещё <b>${n.kids}</b> ${n.kids % 10 === 1 && n.kids % 100 !== 11 ? "ребёнок" : [2, 3, 4].includes(n.kids % 10) && ![12, 13, 14].includes(n.kids % 100) ? "ребёнка" : "детей"} в твои группы. Свободные места: ${n.groups.filter((g) => g.free).map((g) => `${esc(g.group)} - ${g.free}`).join(", ") || "пока нет"}</div>` : ""}
      ${n.contacts ? `<div>Это ещё <b>${n.contacts}</b> контактов, примерно <b>${n.per_day}</b> в день до конца месяца</div>` : ""}
      ${n.trials ? `<div>Это примерно ещё <b>${n.trials}</b> записей на пробное или продаж</div>` : ""}</div>`;
  box.innerHTML = `<div class="tm-h"><div><b>Моя зарплата</b><small>${esc(d.month_label.toLowerCase())}</small></div></div>
    <div class="seg" style="margin-bottom:12px"><button data-mpm="now" class="${mode === "now" ? "active" : ""}">Где я сейчас</button><button data-mpm="month" class="${mode === "month" ? "active" : ""}">Весь месяц</button></div>
    <div class="mp-big">${rub(big)}</div>
    <div class="small muted">${mode === "now" ? `заработано с 1 числа по сегодня` : `прогноз на весь месяц по графику и оплаченным абонементам`}</div>
    <div class="mp-bar"><i style="width:${mode === "now" ? pos : 100}%"></i><span style="${pos > 75 ? "right:0;transform:none" : pos < 25 ? "left:0;transform:none" : `left:${pos}%`}">вы здесь · ${d.day} из ${d.days}</span></div>
    <div class="list" style="box-shadow:none;margin-top:6px">${rows.map(([a, b, c]) => `<div class="list-row"><div class="grow">${a}${b ? ` <span class="muted small">${b}</span>` : ""}</div><b>${rub(c)}</b></div>`).join("")}</div>
    ${P.promoter ? `<div class="row" style="margin-top:10px;align-items:flex-end"><div class="field"><label>Собрал контактов в этом месяце</label><input type="number" min="0" id="mp-c" value="${P.promoter.contacts}"></div><button class="btn sm" id="mp-cs" style="flex:0 0 auto">Сохранить</button></div>` : ""}
    <div class="row" style="margin-top:10px;align-items:flex-end"><div class="field"><label>Сколько хочу получить в этом месяце, ₽</label><input type="number" min="0" step="1000" id="mp-g" value="${d.goal || ""}" placeholder="например, 40000"></div><button class="btn sm primary" id="mp-gs" style="flex:0 0 auto">Посчитать</button></div>
    ${goalTxt}`;
  box.querySelectorAll("[data-mpm]").forEach((b) => (b.onclick = () => renderMyPay(box, b.dataset.mpm)));
  $("#mp-gs").onclick = async () => { try { await api("POST", "/api/my-pay", { goal: +$("#mp-g").value || 0 }); renderMyPay(box); } catch (e) { fail(e); } };
  if ($("#mp-cs")) $("#mp-cs").onclick = async () => { try { await api("POST", "/api/my-pay", { contacts: +$("#mp-c").value || 0 }); toast("Сохранено"); renderMyPay(box); } catch (e) { fail(e); } };
}

function editTeam(t) {
  openDrawer(`<h2>Команда: изменить</h2>
    <div class="small muted" style="margin-bottom:12px">Отдел человека зависит от его должности. Должность меняется в Настройках → Сотрудники</div>
    <div class="section"><h3>Зоны ответственности</h3><div class="small muted" style="margin-bottom:8px">Каждый пункт с новой строки</div>
      ${t.zones.map((z, i) => `<div class="field"><label>${esc(z.title)}</label><textarea rows="5" data-zone="${i}">${esc(z.items.join("\n"))}</textarea></div>`).join("")}</div>
    <div class="section"><h3>Оплата</h3><div class="small muted" style="margin-bottom:8px">Проценты тренеров меняются в Настройках, здесь - администраторы и промоутеры</div>
      <div class="field"><label>Администраторы</label><textarea rows="4" data-pay="admins">${esc(t.pay.admins || "")}</textarea></div>
      <div class="field"><label>Промоутеры</label><textarea rows="3" data-pay="promoters">${esc(t.pay.promoters || "")}</textarea></div></div>
    <button class="btn primary" id="tm-save">Сохранить</button>`);
  $("#tm-save").onclick = async () => {
    const zones = t.zones.map((z, i) => ({ ...z, items: document.querySelector(`[data-zone="${i}"]`).value.split("\n").map((x) => x.trim()).filter(Boolean) }));
    const pay = Object.fromEntries([...document.querySelectorAll("[data-pay]")].map((x) => [x.dataset.pay, x.value]));
    try { await api("PUT", "/api/team", { zones, pay }); toast("Сохранено"); closeDrawer(); render(); } catch (e) { fail(e); }
  };
}
