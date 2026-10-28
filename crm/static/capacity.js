/* «Предупредил, что не придёт» — прямо в переписке и в карточке; свободные места для отработок и пробных */

const WD_SH = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
const dayLabel = (iso) => { const d = new Date(iso + "T00:00"); return `${WD_SH[d.getDay()]} ${iso.slice(8, 10)}.${iso.slice(5, 7)}`; };

async function loadWarnBox(fid, box, onlySid) {
  if (!box || !fid) return;
  let d;
  try { d = await api("GET", `/api/families/${fid}/lessons`); } catch (_) { box.innerHTML = ""; return; }
  const kids = d.kids.filter((k) => !onlySid || k.id === onlySid);
  const trials = kids.filter((k) => k.trial);
  if (!kids.some((k) => k.lessons.length) && !trials.length) { box.innerHTML = ""; return; }
  const warnedN = kids.reduce((n, k) => n + k.lessons.filter((l) => l.warned).length, 0);
  const open = onlySid || box.dataset.open === "1";
  box.innerHTML = `<details class="warn-box" ${open ? "open" : ""}>
    <summary class="warn-head">🤒 Не придёт на тренировку?${warnedN ? ` <span class="pill red">отмечено: ${warnedN}</span>` : ""}</summary>
    <div class="small muted" style="margin-bottom:6px">Нажмите на дату: ребёнка не ждём, место освободится для отработки или пробного</div>
    ${kids.filter((k) => k.lessons.length).map((k) => `<div class="warn-kid">${onlySid ? "" : `<b>${esc(k.name.split(" ")[0])}</b>`}
      <div class="chips">${k.lessons.map((l) => `<button class="chip warn-chip ${l.warned ? "off" : ""}" ${l.vacation ? "disabled" : ""}
        data-wsid="${k.id}" data-wdate="${l.date}" data-wgid="${l.group_id}" data-won="${l.warned ? 1 : 0}"
        title="${esc(l.group)}${l.note ? ": " + esc(l.note) : ""}">${l.warned ? "✕ " : ""}${dayLabel(l.date)}${l.time ? " " + l.time : ""}${l.vacation ? " · отпуск" : ""}</button>`).join("")}</div></div>`).join("")}
    ${trials.map((k) => `<div class="warn-kid"><b>${esc(k.name.split(" ")[0])}</b><span class="small">пробное ${dShort(k.trial.at.slice(0, 10))} ${k.trial.at.slice(11, 16)} ${esc(k.trial.group)}</span>
      <button class="btn sm danger" data-tcancel-open="${k.id}">Отменить запись на ПУ</button>
      <div class="row" data-tcancel-row="${k.id}" hidden style="width:100%"><input placeholder="Причина: заболел, уехали…"><button class="btn sm danger" data-tcancel-sid="${k.id}" style="flex:0">Отменить</button></div></div>`).join("")}
    <div class="warn-note row" hidden><input placeholder="Причина, например: заболел (можно пусто)"><button class="btn sm primary" style="flex:0">Отметить</button></div>
  </details>`;
  box.querySelector("details").ontoggle = (e) => { box.dataset.open = e.target.open ? "1" : ""; };
  const note = box.querySelector(".warn-note");
  box.querySelectorAll("[data-tcancel-open]").forEach((b) => (b.onclick = () => { box.querySelector(`[data-tcancel-row="${b.dataset.tcancelOpen}"]`).hidden = false; b.hidden = true; }));
  box.querySelectorAll("[data-tcancel-sid]").forEach((b) => (b.onclick = async () => {
    const r = b.previousElementSibling.value;
    if (!r || !r.trim()) return toast("Напишите причину отмены", true);
    try { await api("POST", `/api/students/${b.dataset.tcancelSid}/cancel-trial`, { reason: r }); toast("Запись отменена: этап «Не дошёл до ПУ», задача перезаписать сегодня"); loadWarnBox(fid, box, onlySid); } catch (e) { fail(e); }
  }));
  box.querySelectorAll("[data-wsid]").forEach((b) => (b.onclick = async () => {
    const body = { date: b.dataset.wdate, group_id: +b.dataset.wgid };
    if (b.dataset.won === "1") {
      if (!confirm(`Ребёнок всё-таки придёт ${dayLabel(b.dataset.wdate)}?`)) return;
      try { await api("POST", `/api/students/${b.dataset.wsid}/warn`, { ...body, undo: true }); toast("Отметка снята, ждём на тренировке"); loadWarnBox(fid, box, onlySid); } catch (e) { fail(e); }
      return;
    }
    note.hidden = false;
    box.querySelectorAll(".warn-chip").forEach((x) => x.classList.toggle("sel", x === b));
    const inp = note.querySelector("input"); inp.value = ""; inp.focus();
    const send = async () => {
      try {
        const r = await api("POST", `/api/students/${b.dataset.wsid}/warn`, { ...body, note: inp.value });
        const o = r.occupancy;
        toast(`${r.name.split(" ")[0]} не придёт ${dayLabel(r.date)}` + (o.limit ? `. На тренировке ${o.count} из ${o.limit}, свободно мест: ${o.free}` : ""));
        loadWarnBox(fid, box, onlySid);
      } catch (e) { fail(e); }
    };
    note.querySelector("button").onclick = send;
    inp.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); send(); } };
  }));
}

/* Свободные места (в филиалах с лимитом детей): для отработки или пробного. onPick(slot) */
function ageFits(ageTxt, years) {
  if (years == null) return true;
  const n = (ageTxt || "").match(/\d+/g);
  if (!n) return true;
  return years >= +n[0] - 0 && years <= +n[n.length - 1];
}

async function loadSlots(box, onPick, label, birth) {
  if (!box) return;
  let d;
  try { d = await api("GET", "/api/free-slots?days=14"); } catch (_) { return; }
  const years = birth ? parseInt(ageText(birth)) : null;
  const fit = d.slots.filter((x) => ageFits(x.age, isFinite(years) ? years : null));
  d.slots = fit.length ? fit : d.slots;
  if (!d.slots.length) { box.innerHTML = ""; return; }
  const many = new Set(d.slots.map((x) => x.branch)).size > 1;
  const byDay = {};
  d.slots.forEach((x) => (byDay[x.date] = byDay[x.date] || []).push(x));
  box.innerHTML = `<div class="small muted" style="margin:10px 0 6px">${esc(label)}</div>
    <div class="slots">${Object.entries(byDay).slice(0, 7).map(([day, xs]) => `<div class="slot-day"><b>${dayLabel(day)}</b>
      <div class="chips">${xs.map((x, i) => `<button class="chip slot ${x.warned.length ? "hot" : ""}" data-slot="${day}|${i}" title="${esc(x.branch)}${x.warned.length ? ". Предупредили: " + esc(x.warned.join(", ")) : ""}">
        ${esc(x.time)} ${esc(x.age)}${many ? " · " + esc(x.branch.replace(/^ТЦ\s*/, "").replace(/[«»]/g, "")) : ""} · <b>${x.free}</b> ${x.free === 1 ? "место" : x.free < 5 ? "места" : "мест"}</button>`).join("")}</div></div>`).join("")}</div>`;
  box.querySelectorAll("[data-slot]").forEach((b) => (b.onclick = () => { const [day, i] = b.dataset.slot.split("|"); onPick(byDay[day][+i]); }));
}
