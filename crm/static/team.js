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
        ${ids("control").length ? `<i class="tm-line"></i>${node(ids("control")[0], "Старший администратор")}` : ""}
        <i class="tm-line"></i>
        <div class="tm-depts">
          ${dept("admins", "Администрирование", "#d99a0b", "Администратор")}
          ${dept("coaches", "Тренеры", "#23a26d", "Тренер")}
          ${dept("promoters", "Промоутеры", "#2c6fd1", "Промоутер")}
          ${dept("lawyers", "Юридический отдел", "#2a9d9b", "Юрист")}
        </div>
      </div>
    </section>

    <section class="tm-sec"><div class="tm-h"><div><b>Зоны ответственности</b><small>к кому идти с вопросом</small></div></div>
      <div class="tm-zones">${t.zones.map((z) => `<div class="tm-zone" style="--zc:${z.color}">
        <div class="tm-zone-h"><i></i><b>${esc(z.title)}</b>${stack(z.key)}</div>
        <ul>${z.items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>`).join("")}</div>
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
          ${admin ? `<div class="tm-example"><b>Пример: 10 детей, 1100 ₽ за занятие</b>
            <div><span>выручка за тренировку</span><b>${rub(ex)}</b></div>
            <div><span>главному · ${pc(cp.pct.main)} %</span><b>${rub(Math.round(ex * cp.pct.main / 100))}</b></div>
            <div><span>помощнику · ${pc(cp.pct.assistant)} %</span><b>${rub(Math.round(ex * cp.pct.assistant / 100))}</b></div>
            <div><span>если один · ${pc(cp.pct.solo)} %</span><b>${rub(Math.round(ex * cp.pct.solo / 100))}</b></div></div>` : ""}
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
