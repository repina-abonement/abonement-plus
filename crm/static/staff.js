"use strict";
/* Абонемент Плюс — приход тренеров на филиал и кто вёл тренировку. */

/* ---------- у тренера на столе ---------- */
function checkinBox(d, viewOnly) {
  const list = d.checkins || [];
  if (!list.length) return "";
  return `<section class="now checkin-box" style="margin-bottom:12px"><div class="now-head"><h3>Приход на филиал</h3></div>
    ${list.map((s) => {
      const c = s.checkin;
      const st = c ? (c.late_min ? `<span class="pill red">Отмечен в ${esc(c.at)}, опоздание ${c.late_min} мин${c.fine ? `, штраф ${c.fine} ₽${c.fine_status === "cancelled" ? " снят" : ""}` : ""}</span>`
                               : `<span class="pill green">Отмечен в ${esc(c.at)} ✅ вовремя</span>`)
        : (viewOnly ? `<span class="pill">ещё не отмечен</span>` : `<button class="btn primary" data-checkin="${s.branch_id}">📍 Я на филиале</button>`);
      return `<div class="checkin-row"><div class="grow"><b>${esc(s.branch)}</b><div class="small muted">Первая тренировка в ${esc(s.first)}, быть на месте до ${esc(s.due)}</div></div>${st}</div>`;
    }).join("")}
    ${viewOnly ? "" : `<div class="small muted" style="margin-top:6px">Нажимай, когда зашёл на филиал. Опоздание после указанного времени = штраф</div>`}
  </section>`;
}

function wireCheckin(page) {
  page.querySelectorAll("[data-checkin]").forEach((b) => (b.onclick = async () => {
    b.disabled = true; b.textContent = "Отмечаю…";
    const send = async (pos) => {
      const c = pos && pos.coords;
      try {
        const r = await api("POST", "/api/checkin", { branch_id: +b.dataset.checkin, lat: c ? c.latitude : null, lng: c ? c.longitude : null, accuracy: c ? c.accuracy : null });
        toast(r.late_min ? `Отмечено в ${r.at}. Опоздание ${r.late_min} мин` : `Отмечено в ${r.at}. Хорошей тренировки!`);
      } catch (e) { fail(e); }
      render();
    };
    if (navigator.geolocation) navigator.geolocation.getCurrentPosition(send, () => send(null), { timeout: 8000, maximumAge: 60000 });
    else send(null);
  }));
}

/* кто вёл тренировку: блок для карточки на столе тренера и для журнала */
function staffPicker(st, coaches, key) {
  const opt = (sel) => coaches.map((c) => `<option value="${c.id}" ${c.id === sel ? "selected" : ""}>${esc(c.name)}</option>`).join("");
  return `<div class="staff-pick" data-sp="${key}">
    <div class="small"><b>Кто вёл тренировку</b>${st.set ? ` <span class="pill green">записано</span>` : ` <span class="pill yellow">не указано</span>`}</div>
    <div class="row" style="flex-wrap:wrap;align-items:flex-end;margin-top:6px">
      <div class="field"><label>Главный</label><select data-main>${opt(st.main_id)}</select></div>
      <div class="field"><label>Помощник</label><select data-asst><option value="">Был один</option>${opt(st.solo ? null : st.assistant_id)}</select></div>
      <button class="btn sm ${st.set ? "" : "primary"}" data-spsave style="flex:0">${st.set ? "Изменить" : "Сохранить"}</button></div></div>`;
}

function wireStaffPicker(root, key, groupId, date, done) {
  const box = root.querySelector(`[data-sp="${key}"]`);
  if (!box) return;
  box.querySelector("[data-spsave]").onclick = async () => {
    try {
      await api("POST", "/api/lessons/staff", { group_id: groupId, date, main_id: +box.querySelector("[data-main]").value, assistant_id: box.querySelector("[data-asst]").value || null });
      toast("Записано"); done();
    } catch (e) { fail(e); }
  };
}

/* ---------- у проверяющего на рабочем столе ---------- */
function checkinReport(r) {
  if (!r || (!r.rows.length && !r.pending_old.length)) return "";
  const pill = { ok: "green", late: "red", absent: "red", wait: "" };
  const label = (x) => x.state === "ok" ? `пришёл в ${esc(x.checkin.at)}` : x.state === "late" ? `опоздал на ${x.checkin.late_min} мин (${esc(x.checkin.at)})` : x.state === "absent" ? "не отметился" : "ещё ждём";
  const geo = (c) => c && c.lat ? ` <a class="small" target="_blank" href="https://yandex.ru/maps/?pt=${c.lng},${c.lat}&z=17&l=map">📍 где отметился</a>` : "";
  const fineBtns = (c) => c && c.fine_status === "pending" ? `<button class="btn sm" data-fine="${c.id}" data-act="confirm">Штраф ${c.fine} ₽</button><button class="btn sm" data-fine="${c.id}" data-act="cancel">Простить</button>`
    : c && c.fine ? `<span class="small muted">${c.fine_status === "confirmed" ? `штраф ${c.fine} ₽ подтверждён` : "штраф снят"}</span>` : "";
  return `<details class="now checkin-rep" open style="margin-bottom:14px"><summary class="now-head"><h3>Приход тренеров сегодня</h3>
    <span class="small muted">${r.rows.filter((x) => x.state === "ok").length} вовремя · ${r.rows.filter((x) => x.state === "late").length} опоздали · ${r.rows.filter((x) => x.state === "absent").length} не отметились</span></summary>
    ${r.rows.map((x) => `<div class="checkin-row"><span class="pill ${pill[x.state]}">${label(x)}</span>
      <div class="grow"><b>${esc(x.name)}</b> <span class="small muted">${esc(x.branch)}, быть до ${esc(x.due)}</span>${geo(x.checkin)}</div>${fineBtns(x.checkin)}</div>`).join("")}
    ${r.pending_old.length ? `<div class="small" style="margin:8px 0 4px"><b>Не разобраны штрафы прошлых дней</b></div>${r.pending_old.map((c) => `<div class="checkin-row"><div class="grow">${dShort(c.date)} <b>${esc(c.name)}</b> <span class="small muted">${esc(c.branch)}, опоздание ${c.late_min} мин</span>${geo(c)}</div>${fineBtns(c)}</div>`).join("")}` : ""}
  </details>`;
}

function wireCheckinReport(page) {
  page.querySelectorAll("[data-fine]").forEach((b) => (b.onclick = async () => {
    try { await api("POST", `/api/checkins/${b.dataset.fine}/review`, { action: b.dataset.act }); toast(b.dataset.act === "confirm" ? "Штраф подтверждён" : "Штраф снят"); render(); }
    catch (e) { fail(e); }
  }));
}

/* ---------- настройки (админ) ---------- */
async function renderStaffSettings(box, users) {
  const v = await api("GET", "/api/staff/settings");
  const staffU = users.filter((u) => u.role !== "coach" && u.active);
  box.innerHTML = `<h3>Тренеры: приход и оплата</h3>
    <div class="row">
      <div class="field"><label>Кто проверяет приход тренеров</label><select data-s="checkin_reviewer"><option value="">Только администраторы</option>${staffU.map((u) => `<option value="${u.id}" ${String(u.id) === v.checkin_reviewer ? "selected" : ""}>${esc(u.name)}</option>`).join("")}</select></div>
      <div class="field" style="flex:0 0 170px"><label>Быть на месте за, мин</label><input data-s="checkin_before_min" type="number" min="0" max="120" value="${esc(v.checkin_before_min)}"></div>
      <div class="field" style="flex:0 0 150px"><label>Штраф за опоздание, ₽</label><input data-s="late_fine" type="number" min="0" value="${esc(v.late_fine)}"></div></div>
    <div class="row">
      <div class="field"><label>Главный тренер, %</label><input data-s="pay_main_pct" value="${esc(v.pay_main_pct)}"></div>
      <div class="field"><label>Помощник, %</label><input data-s="pay_assistant_pct" value="${esc(v.pay_assistant_pct)}"></div>
      <div class="field"><label>Тренер один, %</label><input data-s="pay_solo_pct" value="${esc(v.pay_solo_pct)}"></div>
      <div class="field"><label>Отработки, ₽ за тренировку</label><input data-s="pay_makeup_fixed" type="number" value="${esc(v.pay_makeup_fixed)}"></div></div>
    <div class="small muted" style="margin-bottom:10px">Проценты и фикс пригодятся для расчёта зарплаты, его сделаем отдельно. За отработки платим, только если пришёл хотя бы один ребёнок</div>
    <button class="btn primary" id="st-save">Сохранить</button>`;
  $("#st-save").onclick = async () => {
    try { await api("PUT", "/api/staff/settings", Object.fromEntries([...box.querySelectorAll("[data-s]")].map((i) => [i.dataset.s, i.value]))); toast("Сохранено"); }
    catch (e) { fail(e); }
  };
}
