/* «Встречаю детей»: тренер с телефона отмечает пришедших крупными плитками в начале тренировки.
   Одно нажатие — пришёл (зелёная), ещё раз — снять отметку. В конце «Готово»: кого не отметили — не пришли. */

/* тренировка уже прошла: дата в прошлом или прошло больше часа от начала */
function lessonOver(date, time) {
  if (date < S.meta.today) return true;
  if (date > S.meta.today || !time) return false;
  const [h, m] = time.split(":").map(Number), now = new Date();
  return now.getHours() * 60 + now.getMinutes() >= h * 60 + m + 60;
}
function meetLabel(date, time, overdue) {
  return lessonOver(date, time) ? (overdue ? "🔴 Отметить, кто был: тренировка прошла" : "✅ Отметить, кто был") : "👋 Встречаю детей";
}

function openMeet(groupId, date) {
  S.meet = { group: groupId, date: date || S.meta.today };
  go("meet");
}

const MEET_TAG = { trial: ["blue", "пробное"], makeup: ["", "отработка"], unpaid: ["red", "не оплачен"] };

function meetState(r) {
  // единое состояние плитки: yes | no | none
  if (r.status === "trial") return r.trial_came === 1 ? "yes" : r.trial_came === 0 ? "no" : "none";
  if (r.status === "makeup") return r.mk_status === "done" ? "yes" : r.mk_status === "missed" ? "no" : "none";
  if (r.makeup_id) return r.status === "done" ? "yes" : r.status === "missed" ? "no" : "none"; // группа отработок
  return r.status === "present" ? "yes" : r.status === "absent" && !r.warned ? "no" : "none";
}

async function meetSend(r, want, g, date) {
  // want: yes | no | none
  if (r.status === "trial") {
    return api("POST", `/api/students/${r.id}/trial`, { came: want === "yes" ? true : want === "no" ? false : null });
  }
  if (r.status === "makeup" || r.makeup_id) {
    return api("POST", `/api/makeups/${r.makeup_id}/status`, { status: want === "yes" ? "done" : want === "no" ? "missed" : "booked" });
  }
  return api("POST", "/api/attendance", { student_id: r.id, group_id: g.id, date, status: want === "yes" ? "present" : want === "no" ? "absent" : "clear" });
}

async function renderMeet(page) {
  if (!S.meet || !S.meet.group) { go("today"); return; }
  const j = await api("GET", `/api/journal?group_id=${S.meet.group}&date=${S.meet.date}`);
  const g = j.group, date = j.date;
  const rows = j.rows.map((r, i) => ({ ...r, _i: i }));
  const away = rows.filter((r) => r.status === "vacation" || (r.warned && r.status !== "present"));
  const kids = rows.filter((r) => !away.includes(r));
  const tags = (r) => {
    const t = [];
    if (r.status === "trial") t.push(MEET_TAG.trial);
    if (r.status === "makeup" || r.makeup_id) t.push(MEET_TAG.makeup);
    if (r.unpaid) t.push(MEET_TAG.unpaid);
    if (r.resolved) t.push(["", r.resolved === "credit" ? "в перерасчёте" : "отработан"]);
    return t.map(([c, l]) => `<span class="pill ${c}">${l}</span>`).join("");
  };
  const tile = (r) => `<button class="meet-tile ${meetState(r)}" data-mi="${r._i}" ${r.resolved ? "disabled" : ""}>
      <span class="meet-check">✓</span><span class="meet-name">${esc(r.name)}</span><span class="meet-tags">${tags(r)}</span></button>`;
  page.innerHTML = `
    <div class="meet">
      <div class="meet-head">
        <div class="grow"><div class="meet-title">${esc(g.name)}</div>
          <div class="small muted">${dShort(date)}, ${esc(g.branch)}${g.time ? ", " + esc(g.time) : ""}</div></div>
        <div class="meet-count"><b id="m-yes">0</b><span>из ${kids.length}</span></div>
      </div>
      ${!j.has_lesson ? `<div class="alert">${j.closed ? "Сегодня праздник, тренировки нет" : "В этот день у группы нет тренировки"}</div>` : ""}
      ${kids.length ? `<div class="small muted" style="margin:0 2px 10px">${lessonOver(date, g.time) ? "Тренировка прошла. Нажмите на тех, кто был, остальных отметит кнопка внизу" : "Нажмите на ребёнка, когда он пришёл"}. Ошиблись - нажмите ещё раз</div>
        <div class="meet-grid">${kids.map(tile).join("")}</div>` : `<div class="panel center" style="padding:24px">На эту тренировку никто не записан</div>`}
      ${away.length ? `<h3 class="meet-sub">Не придут</h3><div class="meet-grid away">${away.map((r) => `<button class="meet-tile none away" data-mi="${r._i}">
          <span class="meet-check">✓</span><span class="meet-name">${esc(r.name)}</span>
          <span class="meet-tags"><span class="pill yellow">${r.status === "vacation" ? "отпуск" : "предупредил"}</span></span></button>`).join("")}</div>
        <div class="small muted" style="margin:6px 2px 0">Если всё-таки пришёл - нажмите</div>` : ""}
      <div class="meet-foot"><button class="btn primary meet-done" id="m-done"></button></div>
    </div>`;
  const byI = Object.fromEntries(rows.map((r) => [r._i, r]));
  const recount = () => {
    const yes = kids.filter((r) => meetState(r) === "yes").length + away.filter((r) => meetState(r) === "yes").length;
    const left = kids.filter((r) => meetState(r) === "none" && !r.resolved).length;
    $("#m-yes").textContent = yes;
    const b = $("#m-done");
    b.dataset.left = left;
    b.dataset.armed = "";
    b.textContent = left ? `Готово: остальные ${left} не пришли` : "Готово, все отмечены 👍";
  };
  const apply = (r, st) => {
    if (r.status === "trial") r.trial_came = st === "yes" ? 1 : st === "no" ? 0 : null;
    else if (r.status === "makeup") r.mk_status = st === "yes" ? "done" : st === "no" ? "missed" : "booked";
    else if (r.makeup_id) r.status = st === "yes" ? "done" : st === "no" ? "missed" : "booked";
    else { r.status = st === "yes" ? "present" : st === "no" ? "absent" : null; if (st === "yes") r.warned = false; }
  };
  page.querySelectorAll("[data-mi]").forEach((el) => (el.onclick = async () => {
    const r = byI[el.dataset.mi];
    if (S.meet.date > S.meta.today) return toast("Эта тренировка ещё не наступила");
    const before = meetState(r), prev = JSON.stringify(r);
    const want = before === "yes" ? "none" : "yes";
    if (want === "yes" && r.unpaid) toast(`${r.name}: абонемент не оплачен, к тренировке не допускать`);
    apply(r, want);
    el.className = el.className.replace(/\b(yes|no|none)\b/, want === "yes" ? "yes" : "none");
    if (navigator.vibrate) navigator.vibrate(15);
    recount();
    try { await meetSend(JSON.parse(prev), want, g, date); }
    catch (e) {
      Object.assign(r, JSON.parse(prev));
      el.className = el.className.replace(/\b(yes|no|none)\b/, before);
      recount(); fail(e);
    }
  }));
  const done = $("#m-done");
  if (done) done.onclick = async () => {
    const left = kids.filter((r) => meetState(r) === "none" && !r.resolved);
    if (!left.length) { history.length > 1 ? history.back() : go("today"); return; }
    if (!done.dataset.armed) { done.dataset.armed = "1"; done.textContent = `Точно? Не пришли: ${left.map((r) => r.name.split(" ")[0]).join(", ")}. Нажмите ещё раз`; return; }
    done.disabled = true;
    try {
      for (const r of left) { await meetSend(r, "no", g, date); apply(r, "no"); }
      toast("Отмечено. Спасибо!");
      history.length > 1 ? history.back() : go("today");
    } catch (e) { done.disabled = false; fail(e); render(); }
  };
  recount();
}
