"use strict";
/* Отчёт тренера о смене (coachreport.py): один шаг на экран, большие кнопки, сдать можно только когда всё готово.
   Тренеру по-свойски и с юмором. Плюс блок на столе старшего администратора и руководителя. */

const REP_STEPS = {
  marks: ["📋", "Все дети отмечены?", "Пробежимся по группам смены. Пока кто-то не отмечен, дальше не пустим, сорян"],
  absent: ["🤒", "Кто не пришёл", "Обзвонил прогульщиков? Тыкни, что с каждым"],
  trials: ["🎯", "Пробные", "Как прошли смотрины? Отметь, кто пришёл и чем закончилось"],
  unlisted: ["🕵️", "Пришли, но не записаны", "Кто-то просочился на поле без записи? Пиши, админы перебьют"],
  pays: ["💸", "Индивы и оплаты на месте", "Если деньги брал ты, а не через администратора, запиши сюда"],
  cash: ["🧾", "Касса", "Четыре кнопки, и касса спит спокойно"],
  merch: ["👕", "Мерч", "Сегодня кто-нибудь стал модным?"],
  inventory: ["⚽", "Инвентарь", "Мячи, фишки, ворота. Всё живое?"],
  photos: ["📸", "Фото филиала", "Восемь кадров, как в старом добром отчёте. Камера откроется сама"],
  tasks: ["📌", "Задания на сегодня", "Это от старшего администратора. Без них отчёт не уйдёт"],
};
const PAY_WHAT = ["Абонемент", "Индив", "Разовое", "Мерч"];
S.rep = null;

/* ---------- стол тренера: большие кнопки отчёта ---------- */
async function coachReportCards(box) {
  const d = await api("GET", "/api/coach-shifts").catch(() => ({ shifts: [] }));
  if (!d.shifts.length) { box.innerHTML = ""; return; }
  const now = new Date();
  box.innerHTML = d.shifts.map((s) => {
    const end = s.end ? new Date(s.end) : null, over = !end || now >= end;
    const done = s.status === "done";
    return `<button class="rep-card ${done ? "done" : over ? "go" : ""}" data-rep="${s.branch_id}" data-rd="${s.date}">
      <span class="rep-ico">${done ? "✅" : "📝"}</span>
      <span class="grow"><b>${done ? "Отчёт о смене сдан" : "Сдать отчёт о смене"}</b>
        <small>${esc(s.branch)}${s.date !== S.meta.today ? `, вчера (${dShort(s.date)})` : ""} · ${done ? "красавчик, можно домой 🏠"
          : over ? "смена закончилась, жми и заполняй" : `после последней тренировки${end ? `, в ${s.end.slice(11, 16)}` : ""}`}</small></span></button>`;
  }).join("");
  box.querySelectorAll("[data-rep]").forEach((b) => (b.onclick = () => openCoachReport(+b.dataset.rep, b.dataset.rd)));
}

/* ---------- мастер ---------- */
async function openCoachReport(branchId, date, mainId) {
  try {
    const v = await api("GET", `/api/coach-report?branch_id=${branchId}&date=${date}${mainId ? "&main_id=" + mainId : ""}`);
    const first = v.steps.findIndex((x) => !x.done);
    S.rep = { v, i: v.status === "done" ? v.steps.length : first < 0 ? v.steps.length : first, readonly: v.status === "done" || !!mainId };
    repRender();
  } catch (e) { fail(e); }
}

function repBox() {
  let box = document.querySelector(".rep-modal");
  if (!box) {
    box = document.createElement("div");
    box.className = "rep-modal";
    document.body.appendChild(box);
  }
  return box;
}

function repClose() { const b = document.querySelector(".rep-modal"); if (b) b.remove(); S.rep = null; if (S.view === "today") render(); }

async function repSave(patch) {
  try { S.rep.v = await api("POST", `/api/coach-report/${S.rep.v.id}`, patch); repRender(); } catch (e) { fail(e); }
}

function repPhotoBtn(slot, label) {
  return `<label class="btn rep-photo-btn">${label}<input type="file" accept="image/*" capture="environment" data-rphoto="${slot}" hidden></label>`;
}

function repThumbs(ids, removable) {
  return (ids || []).map((pid) => `<span class="rep-thumb"><img src="/api/report-photo/${pid}" data-rbig="${pid}" alt="">${removable ? `<button data-rdel="${pid}" aria-label="Убрать">✕</button>` : ""}</span>`).join("");
}

function repShrink(file) {  // фото с телефона ужимаем до 1280 px: быстро грузится и влезает в сервер
  return new Promise((ok, bad) => {
    const url = URL.createObjectURL(file), img = new Image();
    img.onerror = () => { URL.revokeObjectURL(url); bad(new Error("Не открылось фото. Сфоткай ещё раз")); };
    img.onload = () => {
      const k = Math.min(1, 1280 / Math.max(img.width, img.height));
      const c = document.createElement("canvas"); c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url); ok(c.toDataURL("image/jpeg", 0.75));
    };
    img.src = url;
  });
}

function repStepHtml(st, v) {
  const D = v.data, ro = S.rep.readonly;
  const big = (attrs, label, on) => `<button class="rep-big ${on ? "on" : ""}" ${attrs} ${ro ? "disabled" : ""}>${label}</button>`;
  if (st.key === "marks") return v.groups.map((g) => `<div class="rep-row ${g.unmarked ? "bad" : "ok"}"><span class="grow"><b>${esc(g.name.includes(g.time || "-") ? g.name : `${g.time || ""} ${g.name}`)}</b>
      <small>${g.unmarked ? `не отмечено: ${g.unmarked}` : `пришли ${g.came} из ${g.plan}`}</small></span>
      ${g.unmarked && !ro ? `<button class="btn primary" data-rmeet="${g.id}">Отметить</button>` : `<span class="rep-ok">✅</span>`}</div>`).join("");
  if (st.key === "absent") return v.absent.length ? v.absent.map((a) => `<div class="rep-item"><b>${esc(a.name)}</b><small>${esc(a.group)}</small>
      <div class="rep-btns">${Object.entries(v.absent_reasons).map(([k, t]) => big(`data-rabs="${a.id}" data-k="${k}"`, t, (D.absent || {})[a.id] === k)).join("")}</div></div>`).join("")
    : `<div class="rep-empty">Все пришли! Сегодня без прогульщиков 🎉</div>`;
  if (st.key === "trials") return v.trials.length ? v.trials.map((t) => `<div class="rep-item"><b>${esc(t.name)}</b><small>${esc(t.group)}</small>
      <div class="rep-btns">${big(`data-rtc="${t.id}" data-came="1"`, "Пришёл", t.came === 1)}${big(`data-rtc="${t.id}" data-came="0"`, "Не пришёл", t.came === 0)}</div>
      ${t.came === 1 ? `<div class="rep-btns">${Object.entries(v.trial_outcomes).map(([k, x]) => big(`data-rtr="${t.id}" data-k="${k}"`, x, (D.trials || {})[t.id] === k)).join("")}</div>` : ""}</div>`).join("")
    : `<div class="rep-empty">Пробных сегодня не было</div>`;
  if (st.key === "unlisted") {
    const list = D.unlisted || [];
    return `${list.map((u, i) => `<div class="rep-row"><span class="grow"><b>${esc(u.name)}</b><small>${esc(u.group || "")}</small></span>${ro ? "" : `<button class="icon-btn" data-runl="${i}">✕</button>`}</div>`).join("")}
      ${ro ? "" : `<div class="rep-form"><input id="ru-name" placeholder="Имя и фамилия ребёнка"><select id="ru-group">${v.groups.map((g) => `<option>${esc(g.name)}</option>`).join("")}</select>
        <button class="rep-big" id="ru-add">+ Добавить ребёнка</button></div>
        ${list.length ? "" : big('id="ru-none"', "Таких нет, все по записи", D.unlisted_none)}`}`;
  }
  if (st.key === "pays") {
    const list = D.pays || [];
    return `${list.map((p, i) => `<div class="rep-row"><span class="grow"><b>${esc(p.name)} · ${rub(p.amount)}</b><small>${esc(v.pay_methods[p.method] || "")} · ${esc(p.what || "")}</small></span>${ro ? "" : `<button class="icon-btn" data-rpay="${i}">✕</button>`}</div>`).join("")}
      ${ro ? "" : `<div class="rep-form"><input id="rp-name" placeholder="Фамилия"><input id="rp-sum" type="number" inputmode="numeric" placeholder="Сумма, ₽">
        <div class="rep-btns">${Object.entries(v.pay_methods).map(([k, t]) => `<button class="rep-big sm" data-rpm="${k}">${t}</button>`).join("")}</div>
        <div class="rep-btns">${PAY_WHAT.map((t) => `<button class="rep-big sm" data-rpw="${t}">${t}</button>`).join("")}</div>
        <button class="rep-big" id="rp-add">+ Добавить оплату</button></div>
        ${list.length ? "" : big('id="rp-none"', "Не было ни индивов, ни оплат", D.pays_none)}`}`;
  }
  if (st.key === "cash") return v.cash_items.map(([k, t, hint]) => big(`data-rcash="${k}"`, `${(D.cash || {})[k] ? "✅" : "⬜"} ${t}<small>${hint}</small>`, (D.cash || {})[k])).join("");
  if (st.key === "merch") return `<div class="rep-btns">${big('data-rmerch="yes"', "Продавал 🤑", D.merch === "yes")}${big('data-rmerch="no"', "Не продавал", D.merch === "no")}</div>
    ${D.merch === "yes" ? `<div class="small muted" style="margin-top:8px">Не забудь внести продажи в блоке «Мерч» на экране «Сегодня»</div>` : ""}`;
  if (st.key === "inventory") return `<div class="rep-btns">${big('data-rinv="ok"', "Всё исправно 👍", D.inv === "ok")}${big('data-rinv="broken"', "Есть поломка 🛠", D.inv === "broken")}</div>
    ${D.inv === "ok" ? `<div class="rep-ph">${repThumbs(v.photos.inv_ok, !ro)}${ro ? "" : repPhotoBtn("inv_ok", "📷 Фото инвентаря")}</div>` : ""}
    ${D.inv === "broken" ? `<textarea id="ri-note" rows="2" placeholder="Что сломалось? Например: порвана сетка на воротах" ${ro ? "disabled" : ""}>${esc(D.inv_note || "")}</textarea>
      <div class="rep-ph">${repThumbs(v.photos.inv_broken, !ro)}${ro ? "" : repPhotoBtn("inv_broken", "📷 Фото поломки")}</div>
      <div class="small muted">Старший администратор и руководитель увидят красную плашку с фото</div>` : ""}`;
  if (st.key === "photos") return `<div class="rep-slots">${v.slots.map(([k, t]) => `<div class="rep-slot ${(v.photos[k] || []).length ? "ok" : ""}"><b>${esc(t)}</b>
      <div class="rep-ph">${repThumbs(v.photos[k], !ro)}${ro ? "" : repPhotoBtn(k, (v.photos[k] || []).length ? "📷 Ещё" : "📷 Снять")}</div></div>`).join("")}</div>`;
  if (st.key === "tasks") return v.tasks.map((t) => `<div class="rep-item"><b>${esc(t.text)}</b><small>от ${esc(t.created_by)}</small>
      <div class="rep-ph">${repThumbs(t.photos, false)}</div>${big(`data-rtask="${t.id}"`, t.done_at ? "✅ Сделано" : "Сделал!", !!t.done_at)}</div>`).join("");
  return "";
}

function repRender() {
  const R = S.rep, v = R.v, box = repBox(), n = v.steps.length;
  const final = R.i >= n;
  const st = v.steps[Math.min(R.i, n - 1)];
  const head = `<div class="rep-top"><button class="icon-btn" data-rclose aria-label="Закрыть">✕</button>
      <div class="grow"><b>Отчёт о смене</b><small>${esc(v.branch)} · ${dShort(v.date)} · ${esc(v.main)}${v.assistants.length ? " и " + v.assistants.map(esc).join(", ") : ""}</small></div></div>
    <div class="rep-dots">${v.steps.map((x, i) => `<button class="${x.done ? "ok" : ""} ${i === R.i ? "cur" : ""}" data-rgo="${i}" aria-label="${esc(x.title)}"></button>`).join("")}<button class="${v.ready ? "ok" : ""} ${final ? "cur" : ""}" data-rgo="${n}" aria-label="Сдать"></button></div>`;
  const left = v.steps.map((x, i) => [x, i]).filter(([x]) => !x.done);
  const body = final ? `<div class="rep-h"><span>${v.status === "done" ? "✅" : "🏁"}</span><h2>${v.status === "done" ? "Отчёт сдан" : "Финиш!"}</h2>
      <p>${v.status === "done" ? `Сдал ${esc(v.submitted_by)} в ${esc((v.submitted_at || "").slice(11, 16))}` : left.length ? "Осталось:" : "Всё готово. Жми и свободен ⚽"}</p></div>
      ${left.length && v.status !== "done" ? left.map(([x, i]) => `<button class="rep-big" data-rgo="${i}">🔴 ${esc(x.title)} <small>нажми, чтобы доделать</small></button>`).join("") : ""}
      ${v.status === "done" || R.readonly ? "" : `<button class="rep-big go" id="rep-submit" ${v.ready ? "" : "disabled"}>Сдать отчёт</button>`}`
    : `<div class="rep-h"><span>${REP_STEPS[st.key][0]}</span><h2>${REP_STEPS[st.key][1]}</h2><p>${REP_STEPS[st.key][2]}</p></div>${repStepHtml(st, v)}`;
  box.innerHTML = `<div class="rep-sheet">${head}<div class="rep-body">${body}</div>
    <div class="rep-nav"><button class="btn" data-rprev ${R.i === 0 ? "disabled" : ""}>Назад</button>
      ${final ? "" : `<button class="btn ${st.done ? "primary" : ""}" data-rnext>${st.done ? "Дальше ✓" : "Пропустить пока"}</button>`}</div></div>`;
  const $$ = (s) => box.querySelectorAll(s);
  box.querySelector("[data-rclose]").onclick = repClose;
  $$("[data-rgo]").forEach((b) => (b.onclick = () => { R.i = +b.dataset.rgo; repRender(); }));
  if (box.querySelector("[data-rprev]")) box.querySelector("[data-rprev]").onclick = () => { R.i = Math.max(0, R.i - 1); repRender(); };
  if (box.querySelector("[data-rnext]")) box.querySelector("[data-rnext]").onclick = () => { R.i += 1; repRender(); };
  $$("[data-rbig]").forEach((im) => (im.onclick = () => window.open(`/api/report-photo/${im.dataset.rbig}`, "_blank")));
  if (R.readonly) return;
  $$("[data-rmeet]").forEach((b) => (b.onclick = () => { const d = v.date; repClose(); openMeet(+b.dataset.rmeet, d); }));
  $$("[data-rabs]").forEach((b) => (b.onclick = () => repSave({ absent: { [b.dataset.rabs]: b.dataset.k } })));
  $$("[data-rtr]").forEach((b) => (b.onclick = () => repSave({ trials: { [b.dataset.rtr]: b.dataset.k } })));
  $$("[data-rtc]").forEach((b) => (b.onclick = async () => {
    try { await api("POST", `/api/students/${b.dataset.rtc}/trial`, { came: b.dataset.came === "1" }); repSave({}); } catch (e) { fail(e); }
  }));
  $$("[data-rcash]").forEach((b) => (b.onclick = () => repSave({ cash: { [b.dataset.rcash]: !(v.data.cash || {})[b.dataset.rcash] } })));
  $$("[data-rmerch]").forEach((b) => (b.onclick = () => repSave({ merch: b.dataset.rmerch })));
  $$("[data-rinv]").forEach((b) => (b.onclick = () => repSave({ inv: b.dataset.rinv })));
  if (box.querySelector("#ri-note")) box.querySelector("#ri-note").onchange = (e) => repSave({ inv_note: e.target.value });
  $$("[data-rtask]").forEach((b) => (b.onclick = async () => { try { await api("POST", `/api/report-tasks/${b.dataset.rtask}/done`, {}); repSave({}); } catch (e) { fail(e); } }));
  // пришли без записи
  if (box.querySelector("#ru-add")) box.querySelector("#ru-add").onclick = () => {
    const name = box.querySelector("#ru-name").value.trim();
    if (!name) return toast("Напиши имя ребёнка", true);
    repSave({ unlisted: [...(v.data.unlisted || []), { name, group: box.querySelector("#ru-group").value }], unlisted_none: false });
  };
  if (box.querySelector("#ru-none")) box.querySelector("#ru-none").onclick = () => repSave({ unlisted_none: true, unlisted: [] });
  $$("[data-runl]").forEach((b) => (b.onclick = () => repSave({ unlisted: (v.data.unlisted || []).filter((_, i) => i !== +b.dataset.runl) })));
  // оплаты
  let pm = "", pw = "";
  $$("[data-rpm]").forEach((b) => (b.onclick = () => { pm = b.dataset.rpm; $$("[data-rpm]").forEach((x) => x.classList.toggle("on", x === b)); }));
  $$("[data-rpw]").forEach((b) => (b.onclick = () => { pw = b.dataset.rpw; $$("[data-rpw]").forEach((x) => x.classList.toggle("on", x === b)); }));
  if (box.querySelector("#rp-add")) box.querySelector("#rp-add").onclick = () => {
    const name = box.querySelector("#rp-name").value.trim(), amount = +box.querySelector("#rp-sum").value;
    if (!name || !amount || !pm || !pw) return toast("Фамилия, сумма, способ и за что: всё четыре", true);
    repSave({ pays: [...(v.data.pays || []), { name, amount, method: pm, what: pw }], pays_none: false });
  };
  if (box.querySelector("#rp-none")) box.querySelector("#rp-none").onclick = () => repSave({ pays_none: true, pays: [] });
  $$("[data-rpay]").forEach((b) => (b.onclick = () => repSave({ pays: (v.data.pays || []).filter((_, i) => i !== +b.dataset.rpay) })));
  // фото
  $$("[data-rphoto]").forEach((inp) => (inp.onchange = async () => {
    const f = inp.files[0]; if (!f) return;
    inp.closest("label").classList.add("busy");
    try { S.rep.v = await api("POST", `/api/coach-report/${v.id}/photo`, { slot: inp.dataset.rphoto, data: await repShrink(f) }); repRender(); }
    catch (e) { fail(e); inp.closest("label").classList.remove("busy"); }
  }));
  $$("[data-rdel]").forEach((b) => (b.onclick = async () => {
    try { S.rep.v = await api("POST", `/api/coach-report/${v.id}/photo`, { remove: +b.dataset.rdel }); repRender(); } catch (e) { fail(e); }
  }));
  if (box.querySelector("#rep-submit")) box.querySelector("#rep-submit").onclick = async (e) => {
    e.target.disabled = true;
    try { S.rep.v = await api("POST", `/api/coach-report/${v.id}/submit`, {}); toast("Отчёт сдан! Можно домой 🏠"); repRender(); }
    catch (err) { fail(err); e.target.disabled = false; }
  };
}

/* ---------- стол старшего администратора и руководителя ---------- */
function shiftReportsBox(r) {
  if (!r) return "";
  const late = r.late.map((o) => `<div class="now-item red"><span class="ref-card red"></span><div class="grow"><b>${esc(o.coach)}</b>
      <div class="small">${esc(o.branch)}${o.date !== S.meta.today ? `, ${dShort(o.date)}` : ""}: отчёт не сдан, тренировки закончились в ${esc(o.end)}, прошло ${o.minutes >= 60 ? `${Math.floor(o.minutes / 60)} ч ${o.minutes % 60} мин` : `${o.minutes} мин`}</div></div></div>`).join("");
  const issues = r.issues.map((i) => `<div class="now-item red"><span class="ref-card red"></span><div class="grow"><b>🛠 ${esc(i.branch)}: неисправный инвентарь</b>
      <div class="small">${esc(i.note)} · ${dShort(i.date)}</div><div class="rep-ph">${repThumbs(i.photos, false)}</div></div>
      <div class="now-act"><button class="btn sm" data-invclose="${i.id}">Починили</button></div></div>`).join("");
  return `<section class="now ${late || issues ? "" : "calm"}"><div class="now-head"><h3>Отчёты тренеров о смене</h3>
      <button class="btn sm" id="rt-add">+ Задание тренеру</button></div>
    ${late || issues ? `<div class="now-list">${late}${issues}</div>` : `<div class="small muted">Несданных отчётов и поломок нет</div>`}
    ${r.tasks.length ? `<div class="small" style="margin-top:8px"><b>Задания на отчёт:</b>${r.tasks.map((t) => `<div>${t.done_at ? "✅" : "⬜"} ${dShort(t.date)} ${esc(t.coach)}: ${esc(t.text)}${t.done_at ? "" : ` <button class="linkish small" data-rtdel="${t.id}">убрать</button>`}</div>`).join("")}</div>` : ""}
    ${r.done.length ? `<details style="margin-top:8px"><summary class="small">Сданные за вчера и сегодня: ${r.done.length}</summary>${r.done.map((x) => `<div class="small"><button class="linkish" data-ropen="${x.branch_id}" data-rd="${x.date}" data-rm="${x.main_id}">${dShort(x.date)} ${esc(x.branch)}, ${esc(x.coach)}</button> · сдан в ${esc((x.submitted_at || "").slice(11, 16))}</div>`).join("")}</details>` : ""}
  </section>`;
}

function wireShiftReports(page, d) {
  page.querySelectorAll("[data-invclose]").forEach((b) => (b.onclick = async () => { try { await api("POST", `/api/inventory/${b.dataset.invclose}/close`, {}); toast("Отмечено: починили"); render(); } catch (e) { fail(e); } }));
  page.querySelectorAll("[data-rbig]").forEach((im) => (im.onclick = () => window.open(`/api/report-photo/${im.dataset.rbig}`, "_blank")));
  page.querySelectorAll("[data-rtdel]").forEach((b) => (b.onclick = async () => { await api("DELETE", `/api/report-tasks/${b.dataset.rtdel}`).catch(fail); render(); }));
  page.querySelectorAll("[data-ropen]").forEach((b) => (b.onclick = () => openCoachReport(+b.dataset.ropen, b.dataset.rd, +b.dataset.rm)));
  const add = page.querySelector("#rt-add");
  if (add) add.onclick = () => {
    openDrawer(`<h2>Задание тренеру на отчёт</h2><div class="small muted" style="margin-bottom:10px">Тренер увидит его в отчёте о смене и не сдаст отчёт, пока не отметит «Сделал»</div>
      <div class="field"><label>Тренер</label><select id="rt-coach">${(d.coaches || []).map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("")}</select></div>
      <div class="field"><label>На какой день</label><input type="date" id="rt-date" value="${S.meta.today}"></div>
      <div class="field"><label>Задание</label><textarea id="rt-text" rows="3" placeholder="Например: убери стаканчики с ресепшена"></textarea></div>
      <div class="field"><label>Фото (необязательно)</label><input type="file" accept="image/*" id="rt-photo"></div>
      <button class="btn primary" id="rt-save">Поставить задание</button>`);
    $("#rt-save").onclick = async () => {
      try {
        const f = $("#rt-photo").files[0];
        await api("POST", "/api/report-tasks", { coach_id: +$("#rt-coach").value, date: $("#rt-date").value, text: $("#rt-text").value, photo: f ? await repShrink(f) : null });
        toast("Задание поставлено"); closeDrawer(); render();
      } catch (e) { fail(e); }
    };
  };
}
