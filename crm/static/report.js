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
  tasks: ["📌", "Задания на сегодня", "Задания старшего администратора и задачи из «Планера». По каждой: сделал или почему нет. Без этого отчёт не уйдёт"],
};
/* Форма оплаты в отчёте (шаг «Оплаты» и «Купили на месте» на пробном): кто (поиск по CRM или вручную), сумма, способ,
   за что, тариф; наличные только с чеком и в конверте */
function repPayForm(v, trial) {
  const f = S.rep.pf || (S.rep.pf = {});
  if (trial && f.trial_id !== trial.id) Object.assign(f, { trial_id: trial.id, name: trial.name, student_id: trial.id });
  if (!trial && f.trial_id) { S.rep.pf = {}; return repPayForm(v); }
  const chip = (attr, val, label, on) => `<button class="rep-big sm ${on ? "on" : ""}" ${attr}="${val}">${label}</button>`;
  const abon = f.what === "month" || f.what === "month2";
  return `<div class="rep-form rep-pay">
    ${trial ? `<div class="small"><b>💰 Оплата: ${esc(trial.name)}</b></div>` : `<div class="rep-search">
      <input id="rp-name" placeholder="Фамилия или имя ребёнка" value="${esc(f.name || "")}" autocomplete="off">
      ${f.student_id ? `<div class="small rep-found">✅ Нашли в CRM: ${esc(f.name)} · ${esc(f.label || "")} <button class="linkish small" id="rp-unpick">другой</button></div>`
        : f.manual ? `<div class="small rep-found warn">⚠️ Нет в CRM, администратор разберёт вручную <button class="linkish small" id="rp-unpick">искать</button></div>` : `<div id="rp-hits"></div>`}</div>`}
    <input id="rp-sum" type="number" inputmode="numeric" placeholder="Сумма, ₽" value="${f.amount || ""}">
    <div class="rep-btns">${Object.entries(v.pay_methods).map(([k, t]) => chip("data-rpm", k, t, f.method === k)).join("")}</div>
    ${f.method === "cash" ? `<div class="rep-btns">${chip("data-rpc", "check", `${f.check ? "✅" : "⬜"} 🧾 Чек пробил на кассе`, f.check)}${chip("data-rpc", "env", `${f.env ? "✅" : "⬜"} ✉️ Деньги в конверте`, f.env)}</div>` : ""}
    <div class="rep-btns">${Object.entries(v.pay_for).filter(([k]) => !trial || k !== "indiv").map(([k, t]) => chip("data-rpw", k, t, f.what === k)).join("")}</div>
    ${f.what === "other" ? `<input id="rp-other" placeholder="За что? Например: абонемент на декабрь" value="${esc(f.what_text || "")}">` : ""}
    ${abon ? `<div class="rep-btns">${Object.entries(v.tariffs).map(([k, t]) => chip("data-rpt", k, t, f.tariff === k)).join("")}</div>` : ""}
    <button class="rep-big go" id="rp-add">${trial ? "Сохранить оплату" : "+ Добавить оплату"}</button></div>`;
}

function repFirstBought(v) {  // форма оплаты открыта у одного пробного за раз
  const D = v.data, ed = S.rep.pf && S.rep.pf.edit ? S.rep.pf.trial_id : null;
  if (ed) return ed;
  const t = v.trials.find((x) => x.came === 1 && (D.trials || {})[x.id] === "bought" && !(D.pays || []).some((p) => p.trial_id === x.id));
  return t ? t.id : null;
}

function repPayWhat(v, p) {
  const w = p.what === "other" ? (p.what_text || "") : (v.pay_for[p.what] || p.what || "");
  return w + ((p.what === "month" || p.what === "month2") && v.tariffs[p.tariff] ? `, ${v.tariffs[p.tariff]}` : "");
}

function repPayWire(box, v) {
  const f = S.rep.pf || (S.rep.pf = {}), $$ = (q) => box.querySelectorAll(q);
  const keep = () => {
    if (box.querySelector("#rp-sum")) f.amount = +box.querySelector("#rp-sum").value || "";
    if (box.querySelector("#rp-other")) f.what_text = box.querySelector("#rp-other").value;
    if (box.querySelector("#rp-name") && !f.student_id && !f.manual) f.name = box.querySelector("#rp-name").value;
  };
  const pick = (patch) => { keep(); Object.assign(f, patch); repRender(); };
  $$("[data-rpm]").forEach((b) => (b.onclick = () => pick({ method: b.dataset.rpm })));
  $$("[data-rpc]").forEach((b) => (b.onclick = () => pick({ [b.dataset.rpc]: !f[b.dataset.rpc] })));
  $$("[data-rpw]").forEach((b) => (b.onclick = () => pick({ what: b.dataset.rpw })));
  $$("[data-rpt]").forEach((b) => (b.onclick = () => pick({ tariff: b.dataset.rpt })));
  if (box.querySelector("#rp-unpick")) box.querySelector("#rp-unpick").onclick = () => pick({ student_id: null, manual: false, label: "" });
  const inp = box.querySelector("#rp-name"), hits = box.querySelector("#rp-hits");
  if (inp && hits) {
    let timer;
    const show = async () => {
      const q = inp.value.trim(); f.name = inp.value;
      if (q.length < 2) { hits.innerHTML = ""; return; }
      const r = await api("GET", "/api/report-search?q=" + encodeURIComponent(q)).catch(() => ({ items: [] }));
      hits.innerHTML = r.items.map((x) => `<button class="rep-hit" data-hit="${x.id}" data-n="${esc(x.name)}" data-l="${esc(x.label)}"><b>${esc(x.name)}</b> <small>${esc(x.label)}</small></button>`).join("")
        + `<button class="rep-hit manual" data-hitman="1">Нет в списке, записать как есть: «${esc(q)}»</button>`;
      hits.querySelectorAll("[data-hit]").forEach((b) => (b.onclick = () => pick({ student_id: +b.dataset.hit, name: b.dataset.n, label: b.dataset.l, manual: false })));
      hits.querySelector("[data-hitman]").onclick = () => pick({ student_id: null, manual: true, name: q });
    };
    inp.oninput = () => { clearTimeout(timer); timer = setTimeout(show, 250); };
    if (inp.value.trim().length >= 2) show();
  }
  if (box.querySelector("#rp-add")) box.querySelector("#rp-add").onclick = () => {
    keep();
    if (!(f.name || "").trim()) return toast("Напиши, за кого оплата", true);
    if (!f.student_id && !f.manual && !f.trial_id) return toast("Выбери ребёнка из списка или «Нет в списке»", true);
    if (!f.amount) return toast("Напиши сумму", true);
    if (!f.method) return toast("Как оплатили: карта, QR или наличные?", true);
    if (f.method === "cash" && !(f.check && f.env)) return toast("Наличные: пробей чек и положи деньги в конверт", true);
    if (!f.what) return toast("За что заплатили?", true);
    if (f.what === "other" && (f.what_text || "").trim().length < 2) return toast("Напиши, за что", true);
    if ((f.what === "month" || f.what === "month2") && !f.tariff) return toast("Какой тариф: Базовый или Гибкий?", true);
    const p = { name: f.name.trim(), student_id: f.student_id || null, amount: f.amount, method: f.method, what: f.what,
      what_text: f.what_text || "", tariff: f.tariff || "", check: !!f.check, env: !!f.env };
    if (f.trial_id) p.trial_id = f.trial_id;
    const rest = (v.data.pays || []).filter((x) => !f.trial_id || x.trial_id !== f.trial_id);
    S.rep.pf = {};
    repSave({ pays: [...rest, p], pays_none: false });
  };
}
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
          : over ? `смена закончилась, сдай до ${new Date(end.getTime() + 30 * 60000).toTimeString().slice(0, 5)}` : `не позднее 30 минут после последней тренировки${end ? ` (до ${new Date(end.getTime() + 30 * 60000).toTimeString().slice(0, 5)})` : ""}`}</small></span></button>`;
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
      ${t.came === 1 ? `<div class="rep-btns">${Object.entries(v.trial_outcomes).map(([k, x]) => big(`data-rtr="${t.id}" data-k="${k}"`, x, (D.trials || {})[t.id] === k)).join("")}</div>` : ""}
      ${t.came === 1 && (D.trials || {})[t.id] === "bought" ? (() => { const p = (D.pays || []).find((x) => x.trial_id === t.id);
        return p && !(S.rep.pf && S.rep.pf.trial_id === t.id && S.rep.pf.edit) ? `<div class="rep-row ok"><span class="grow"><b>💰 ${rub(p.amount)} · ${esc(v.pay_methods[p.method] || "")}</b><small>${esc(repPayWhat(v, p))}</small></span>${ro ? "" : `<button class="linkish small" data-rtpedit="${t.id}">изменить</button>`}</div>`
          : ro ? "" : t.id === repFirstBought(v) ? repPayForm(v, t) : `<div class="small muted">Сначала сохрани оплату выше</div>`; })() : ""}</div>`).join("")
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
    const prev = v.env_prev, need = prev ? prev.amount + v.cash_sum : null, env = D.envelope;
    return `${list.map((p, i) => `<div class="rep-row ${p.student_id ? "" : "warn"}"><span class="grow"><b>${esc(p.name)} · ${rub(p.amount)}</b>
        <small>${esc(v.pay_methods[p.method] || "")}${p.method === "cash" ? " (чек ✅, конверт ✅)" : ""} · ${esc(repPayWhat(v, p))}${p.trial_id ? " · пробное" : ""}${p.student_id ? "" : " · ⚠️ нет в CRM, админ разберёт"}</small></span>
        ${ro || p.trial_id ? "" : `<button class="icon-btn" data-rpay="${i}">✕</button>`}</div>`).join("")}
      ${v.cash_sum ? `<div class="rep-item rep-env"><b>✉️ Остаток в конверте</b>
        <small>${prev ? `Было ${rub(prev.amount)} (отчёт ${dShort(prev.date)}${prev.taken ? `, потом забрали ${rub(prev.taken)}` : ""}) + наличные сегодня ${rub(v.cash_sum)} = должно быть <b>${rub(need)}</b>` : `Наличные сегодня: ${rub(v.cash_sum)}. Посчитай, сколько всего в конверте`}</small>
        <input id="rp-env" type="number" inputmode="numeric" placeholder="Сколько денег в конверте сейчас, ₽" value="${env ?? ""}" ${ro ? "disabled" : ""}>
        ${env != null && prev && !v.env_match ? `<div class="rep-bad">⚠️ Не сходится: должно быть ${rub(need)}, а в конверте ${rub(env)}. Пересчитай или объясни</div>
          <input id="rp-envnote" placeholder="Почему не сходится? Обязательно" value="${esc(D.env_note || "")}" ${ro ? "disabled" : ""}>` : env != null && prev ? `<div class="rep-good">✅ Сходится</div>` : ""}
        <div class="small muted">Напиши остаток на конверте. Всё уйдёт в чат «ДЕНЬГИ»</div></div>` : ""}
      ${ro ? "" : repPayForm(v)}
      ${ro || list.length ? "" : big('id="rp-none"', "Не было ни индивов, ни оплат", D.pays_none)}`;
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
  if (st.key === "tasks") {
    const ans = (kind, t) => `<div class="rep-two">${big(`data-ra="${kind}" data-id="${t.id}" data-k="done"`, "✅ Сделал", t.answer === "done")}${big(`data-ra="${kind}" data-id="${t.id}" data-k="not"`, "❌ Не сделал", t.answer === "not")}</div>
      ${t.answer ? `<textarea class="rep-why" rows="2" data-raw="${kind}" data-id="${t.id}" placeholder="${t.answer === "not" ? "Почему не сделал? Обязательно" : "Комментарий, если есть что сказать"}" ${ro ? "disabled" : ""}>${esc(t.why || "")}</textarea>` : ""}`;
    return v.tasks.map((t) => `<div class="rep-item"><b>${esc(t.text)}</b><small>от ${esc(t.created_by)}</small>
      <div class="rep-ph">${repThumbs(t.photos, false)}</div>${ans("rtasks", t)}</div>`).join("")
    + (v.crm_tasks || []).map((t) => `<div class="rep-item"><b>${esc(t.title)}</b><small>${t.due_at.slice(0, 10) < v.date ? "⏰ просрочено с " + dShort(t.due_at) + " · " : ""}${esc(t.note || "")}</small>
      ${ans("crm_tasks", t)}</div>`).join("")
    + `<div class="small muted" style="margin-top:6px">Ответы увидит администратор на смене, Варя проверит и закроет</div>`;
  }
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
  $$("[data-rtr]").forEach((b) => (b.onclick = () => {
    const id = +b.dataset.rtr, patch = { trials: { [id]: b.dataset.k } };
    if (b.dataset.k !== "bought" && (v.data.pays || []).some((p) => p.trial_id === id)) patch.pays = (v.data.pays || []).filter((p) => p.trial_id !== id);
    if (S.rep.pf && S.rep.pf.trial_id === id) S.rep.pf = {};
    repSave(patch);
  }));
  $$("[data-rtc]").forEach((b) => (b.onclick = async () => {
    try { await api("POST", `/api/students/${b.dataset.rtc}/trial`, { came: b.dataset.came === "1" }); repSave({}); } catch (e) { fail(e); }
  }));
  $$("[data-rcash]").forEach((b) => (b.onclick = () => repSave({ cash: { [b.dataset.rcash]: !(v.data.cash || {})[b.dataset.rcash] } })));
  $$("[data-rmerch]").forEach((b) => (b.onclick = () => repSave({ merch: b.dataset.rmerch })));
  $$("[data-rinv]").forEach((b) => (b.onclick = () => repSave({ inv: b.dataset.rinv })));
  if (box.querySelector("#ri-note")) box.querySelector("#ri-note").onchange = (e) => repSave({ inv_note: e.target.value });
  $$("[data-ra]").forEach((b) => (b.onclick = () => { const k = b.dataset.ra, cur = ((v.data[k] || {})[b.dataset.id]) || {}; repSave({ [k]: { [b.dataset.id]: { ...cur, state: b.dataset.k } } }); }));
  $$("[data-raw]").forEach((i) => (i.onchange = () => { const k = i.dataset.raw, cur = ((v.data[k] || {})[i.dataset.id]) || {}; repSave({ [k]: { [i.dataset.id]: { ...cur, why: i.value } } }); }));
  // пришли без записи
  if (box.querySelector("#ru-add")) box.querySelector("#ru-add").onclick = () => {
    const name = box.querySelector("#ru-name").value.trim();
    if (!name) return toast("Напиши имя ребёнка", true);
    repSave({ unlisted: [...(v.data.unlisted || []), { name, group: box.querySelector("#ru-group").value }], unlisted_none: false });
  };
  if (box.querySelector("#ru-none")) box.querySelector("#ru-none").onclick = () => repSave({ unlisted_none: true, unlisted: [] });
  $$("[data-runl]").forEach((b) => (b.onclick = () => repSave({ unlisted: (v.data.unlisted || []).filter((_, i) => i !== +b.dataset.runl) })));
  // оплаты
  repPayWire(box, v);
  if (box.querySelector("#rp-env")) box.querySelector("#rp-env").onchange = (e) => repSave({ envelope: e.target.value });
  if (box.querySelector("#rp-envnote")) box.querySelector("#rp-envnote").onchange = (e) => repSave({ env_note: e.target.value });
  $$("[data-rtpedit]").forEach((b) => (b.onclick = () => { const p = (v.data.pays || []).find((x) => x.trial_id === +b.dataset.rtpedit) || {};
    S.rep.pf = { ...p, edit: true, trial_id: +b.dataset.rtpedit }; repRender(); }));
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

/* ---------- стол администраторов: ответы тренеров по заданиям ---------- */
function coachAnswersBox(list) {
  if (!list || !list.length) return "";
  return `<section class="now"><div class="now-head"><h3>Ответы тренеров по заданиям</h3><span class="small muted">Примите или верните тренеру. Варя следит, чтобы всё закрыли</span></div>
    <div class="now-list">${list.map((a) => `<div class="now-item ${a.state === "done" ? "" : "red"}">${a.state === "done" ? "" : `<span class="ref-card red"></span>`}
      <div class="grow"><b>${a.state === "done" ? "✅ Сделал" : "❌ Не сделал"}: ${esc(a.title)}</b>
        <div class="small">${esc(a.coach)} · ${dShort(a.date)}${a.student ? ` · <button class="linkish" data-open-st="${a.student_id}">${esc(a.student)}</button>` : ""}</div>
        ${a.comment ? `<div class="small">💬 ${esc(a.comment)}</div>` : ""}</div>
      <div class="now-act"><button class="btn sm primary" data-ans-ok="${a.id}">Принять</button><button class="btn sm" data-ans-back="${a.id}">Вернуть тренеру</button></div></div>`).join("")}</div></section>`;
}

function wireCoachAnswers(page) {
  page.querySelectorAll("[data-ans-ok]").forEach((b) => { const st = b.closest(".now-item").querySelector("[data-open-st]"); if (st) st.onclick = () => openStudent(+st.dataset.openSt); });
  page.querySelectorAll("[data-ans-ok]").forEach((b) => (b.onclick = async () => {
    try { await api("POST", `/api/task-answers/${b.dataset.ansOk}`, { decision: "accepted" }); toast("Принято, задание закрыто"); render(); } catch (e) { fail(e); }
  }));
  page.querySelectorAll("[data-ans-back]").forEach((b) => (b.onclick = () => {
    openDrawer(`<h2>Вернуть тренеру</h2><div class="small muted" style="margin-bottom:10px">Задание снова придёт тренеру на «Сегодня» и в отчёт о смене</div>
      <div class="field"><label>Что не так</label><textarea id="ans-note" rows="3" placeholder="Например: мама не ответила, спроси ещё раз в четверг"></textarea></div>
      <div class="field"><label>На какой день</label><input type="date" id="ans-date" value="${S.meta.today}"></div>
      <button class="btn primary" id="ans-save">Вернуть</button>`);
    $("#ans-save").onclick = async () => {
      try { await api("POST", `/api/task-answers/${b.dataset.ansBack}`, { decision: "returned", note: $("#ans-note").value, date: $("#ans-date").value }); toast("Вернули тренеру"); closeDrawer(); render(); }
      catch (e) { fail(e); }
    };
  }));
}

/* «✉️ Взял из конверта»: любой сотрудник, CRM пишет в чат «ДЕНЬГИ» и уменьшает остаток (Юлия, 01.11) */
function envelopeButton() {
  return `<button class="btn env-take-btn" data-env-take>✉️ Взял из конверта</button>`;
}

async function openEnvelopeTake(branchId) {
  let d;
  try { d = await api("GET", "/api/envelope"); } catch (e) { return fail(e); }
  let reason = "";
  openDrawer(`<h2>✉️ Взял из конверта</h2><div class="small muted" style="margin-bottom:10px">CRM сама напишет в чат «ДЕНЬГИ», и у следующего тренера остаток сойдётся</div>
    <div class="field"><label>Филиал</label><select id="env-br">${d.items.map((x) => `<option value="${x.branch_id}" ${x.branch_id === branchId ? "selected" : ""}>${esc(x.branch)}${x.amount != null ? ` · в конверте ${rub(x.amount)}` : ""}</option>`).join("")}</select></div>
    <div class="field"><label>Сколько взял, ₽</label><input id="env-sum" type="number" inputmode="numeric" placeholder="Например: 10000"></div>
    <div class="field"><label>Зачем</label><div class="rep-btns">${[["bank", "Сдать в банк"], ["change", "Сдача"], ["other", "Другое"]].map(([k, t]) => `<button class="rep-big sm" data-envr="${k}">${t}</button>`).join("")}</div>
      <input id="env-note" placeholder="Зачем взял?" style="display:none;margin-top:8px"></div>
    <button class="btn primary" id="env-save">Записать</button>`);
  document.querySelectorAll("[data-envr]").forEach((b) => (b.onclick = () => {
    reason = b.dataset.envr;
    document.querySelectorAll("[data-envr]").forEach((x) => x.classList.toggle("on", x === b));
    $("#env-note").style.display = reason === "other" ? "" : "none";
  }));
  $("#env-save").onclick = async () => {
    try {
      const r = await api("POST", "/api/envelope/take", { branch_id: +$("#env-br").value, amount: $("#env-sum").value, reason, note: $("#env-note").value });
      toast(r.left != null ? `Записано. В конверте осталось ${rub(r.left)}` : "Записано, ушло в чат «ДЕНЬГИ»"); closeDrawer(); render();
    } catch (e) { fail(e); }
  };
}

function wireEnvelope(page, branchId) {
  page.querySelectorAll("[data-env-take]").forEach((b) => (b.onclick = () => openEnvelopeTake(branchId)));
}
