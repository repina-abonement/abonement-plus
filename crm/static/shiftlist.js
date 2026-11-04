/* Списки детей на всю смену (Юлия, 04.11): «📋 Скопировать» текстом и «🖨 Лист для печати» тренерам перед тренировками.
   Сверху вводники: фамилия имя, возраст, группа, родитель. Дальше группы по времени с нумерацией и пометками */
async function shiftData(branchId, day) {
  return api("GET", `/api/journal/shift?branch_id=${branchId || 0}&date=${day}`);
}

const shiftWd = (day) => ["вс", "пн", "вт", "ср", "чт", "пт", "сб"][new Date(day + "T12:00").getDay()];
const shiftGroupName = (g, branch) => groupNoBranch({ name: g.name, branch });
/* «Ср/Вс 16:45 3–4»: время и возраст уже в названии, не повторяем */
const shiftTitle = (g, branch) => { const n = shiftGroupName(g, branch); return (g.time && !n.includes(g.time) ? g.time + " " : "") + n; };
const shiftAge = (g, branch) => g.age && !shiftGroupName(g, branch).includes(g.age) ? g.age + " лет" : "";

function shiftTextAll(all) {
  return all.branches.length ? all.branches.map(shiftText).join("\n\n") : `${dShort(all.date)}: тренировок нет`;
}

function shiftText(d) {
  const L = [`📋 ${d.branch}, ${dShort(d.date)} (${shiftWd(d.date)})`];
  if (d.trials.length) {
    L.push("", `🆕 ВВОДНИКИ: ${d.trials.length}`);
    d.trials.forEach((t, i) => L.push(`${i + 1}. ${t.name}${t.age ? ", " + t.age : ""}, группа ${shiftTitle({ name: t.group, time: t.time }, d.branch)}${t.parent ? ", родитель: " + t.parent : ""}`));
  }
  d.groups.forEach((g) => {
    const away = g.kids.filter((k) => ["warned", "vacation"].includes(k.tag));
    const come = g.kids.filter((k) => !["warned", "vacation"].includes(k.tag));
    L.push("", `⚽ ${shiftTitle(g, d.branch)}${shiftAge(g, d.branch) ? " (" + shiftAge(g, d.branch) + ")" : ""}${g.coach ? ", тренер " + g.coach : ""}`, `Ждём: ${come.length}`);
    come.forEach((k, i) => L.push(`${i + 1}. ${k.name}${k.age ? ", " + k.age : ""}: ${k.label}${k.note ? " (" + k.note + ")" : ""}`));
    if (away.length) L.push(`Не придут: ${away.map((k) => k.name + (k.tag === "vacation" ? " (отпуск)" : k.note ? " (" + k.note + ")" : "")).join(", ")}`);
  });
  if (!d.groups.length) L.push("", "В этот день тренировок нет");
  return L.join("\n");
}

async function shiftCopy(branchId, day) {
  try {
    const t = shiftTextAll(await shiftData(branchId, day));
    try { await navigator.clipboard.writeText(t); toast("Списки смены скопированы"); } catch (_) { window.prompt("Скопируйте", t); }
  } catch (e) { fail(e); }
}

async function shiftPrint(branchId, day) {
  const w = window.open("", "_blank");
  if (!w) return toast("Разрешите всплывающие окна для печати");
  let all;
  try { all = await shiftData(branchId, day); } catch (e) { w.close(); return fail(e); }
  const body = all.branches.map((d) => shiftPrintBranch(d)).join("") || `<p>${dShort(all.date)}: тренировок нет</p>`;
  w.document.write(shiftPrintPage(`Смена ${dShort(all.date)}`, body));
  w.document.close();
}

function shiftPrintBranch(d) {
  const tagCls = { paid: "t-paid", single: "t-single", unpaid: "t-unpaid", makeup: "t-makeup", trial: "t-trial" };
  const trials = d.trials.length ? `<section class="trials"><h2>🆕 Вводники: ${d.trials.length}</h2>
    <table><thead><tr><th>№</th><th>Фамилия, имя</th><th>Возраст</th><th>Группа</th><th>Родитель</th></tr></thead><tbody>
    ${d.trials.map((t, i) => `<tr><td class="n">${i + 1}</td><td><b>${esc(t.name)}</b></td><td>${esc(t.age)}</td><td>${esc(shiftTitle({ name: t.group, time: t.time }, d.branch))}</td><td>${esc(t.parent)}</td></tr>`).join("")}
    </tbody></table></section>` : "";
  const groups = d.groups.map((g) => {
    const come = g.kids.filter((k) => !["warned", "vacation"].includes(k.tag));
    const away = g.kids.filter((k) => ["warned", "vacation"].includes(k.tag));
    return `<section class="grp"><div class="gh"><div><span class="tm">${esc(g.time)}</span> ${esc(shiftGroupName(g, d.branch).replace(g.time, "").replace(/\s+/g, " ").trim())}${shiftAge(g, d.branch) ? ` <span class="mut">${esc(shiftAge(g, d.branch))}</span>` : ""}</div>
      <div class="mut">${g.coach ? "Тренер: " + esc(g.coach) + " · " : ""}ждём <b>${come.length}</b></div></div>
      <table><thead><tr><th>№</th><th>Ребёнок</th><th>Возраст</th><th>Пометка</th><th class="mk">Был</th></tr></thead><tbody>
      ${come.map((k, i) => `<tr><td class="n">${i + 1}</td><td>${esc(k.name)}${k.note ? `<div class="mut sm">${esc(k.note)}</div>` : ""}</td><td>${esc(k.age)}</td><td><span class="tag ${tagCls[k.tag] || ""}">${esc(k.label)}</span></td><td class="mk"><span class="box"></span></td></tr>`).join("") || `<tr><td colspan="5" class="mut">Никого не ждём</td></tr>`}
      </tbody></table>
      ${away.length ? `<div class="away">Не придут: ${away.map((k) => esc(k.name) + (k.tag === "vacation" ? " (отпуск)" : k.note ? ` (${esc(k.note)})` : "")).join(", ")}</div>` : ""}</section>`;
  }).join("");
  return `<div class="branch"><header><h1>${esc(d.branch)}</h1><div style="font-size:16px;font-weight:700">${dShort(d.date)}, ${shiftWd(d.date)}</div></header>
${trials}${groups}</div>`;
}

function shiftPrintPage(title, body) {
  return `<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>${esc(title)}</title>
<style>
@font-face { font-family: "Nunito"; src: url("/fonts/nunito-cyrillic.woff2") format("woff2"); font-weight: 200 1000; unicode-range: U+0400-045F, U+2116; }
@font-face { font-family: "Nunito"; src: url("/fonts/nunito-latin.woff2") format("woff2"); font-weight: 200 1000; unicode-range: U+0000-00FF, U+2000-206F; }
@page { size: A4; margin: 12mm; }
* { box-sizing: border-box; }
body { font-family: "Nunito", Arial, sans-serif; color: #14211b; margin: 0; padding: 16px; font-size: 13px; }
header { display: flex; justify-content: space-between; align-items: flex-end; border-bottom: 3px solid #1d8a57; padding-bottom: 8px; margin-bottom: 14px; }
h1 { font-size: 22px; margin: 0; }
h2 { font-size: 16px; margin: 0 0 6px; }
.mut { color: #5d6a62; font-weight: 400; } .sm { font-size: 11px; }
.trials { background: #fdf3d3; border: 1px solid #f2bf1d; border-radius: 10px; padding: 10px 12px; margin-bottom: 14px; }
.grp { border: 1px solid #d5ddd6; border-radius: 10px; padding: 10px 12px; margin-bottom: 12px; break-inside: avoid; }
.gh { display: flex; justify-content: space-between; align-items: baseline; font-size: 15px; font-weight: 700; margin-bottom: 6px; }
.tm { background: #1d8a57; color: #fff; border-radius: 6px; padding: 1px 7px; margin-right: 4px; }
table { width: 100%; border-collapse: collapse; }
th { text-align: left; font-size: 11px; color: #5d6a62; font-weight: 600; border-bottom: 1px solid #d5ddd6; padding: 3px 6px; }
td { padding: 5px 6px; border-bottom: 1px solid #eef1ec; vertical-align: top; }
tr:last-child td { border-bottom: 0; }
.n { width: 26px; color: #5d6a62; text-align: right; }
.mk { width: 40px; text-align: center; }
.box { display: inline-block; width: 15px; height: 15px; border: 1.5px solid #14211b; border-radius: 3px; }
.tag { font-size: 11px; font-weight: 700; border-radius: 99px; padding: 1px 8px; white-space: nowrap; border: 1px solid #d5ddd6; }
.t-paid { background: #e2f3ea; border-color: #9fd3b6; } .t-single { background: #e6eefc; border-color: #a9c0ef; }
.t-unpaid { background: #fde6e6; border-color: #f0a9ab; } .t-makeup { background: #efe6fb; border-color: #c6aef0; }
.t-trial { background: #fdf3d3; border-color: #f2bf1d; }
.away { margin-top: 6px; font-size: 12px; color: #5d6a62; }
.noprint { margin-bottom: 12px; }
.noprint button { font: inherit; font-weight: 700; background: #1d8a57; color: #fff; border: 0; border-radius: 10px; padding: 8px 16px; cursor: pointer; }
.branch + .branch { break-before: page; margin-top: 24px; }
@media print { .noprint { display: none; } body { padding: 0; } }
</style></head><body>
<div class="noprint"><button onclick="window.print()">🖨 Печать</button></div>
${body}
</body></html>`;
}

/* «Все занятия дня» в журнале: все филиалы или один, только смотреть + скопировать / распечатать */
async function renderJournalDay(page, groups) {
  const brs = [...new Map(groups.map((x) => [x.branch_id, { id: x.branch_id, name: x.branch }])).values()];
  const bid = S.journal.dayBranch || 0;
  const all = await shiftData(bid, S.journal.date);
  const tagCls = { paid: "jt-paid", single: "jt-single", unpaid: "jt-unpaid", makeup: "jt-makeup", trial: "jt-trial" };
  const kidRow = (k, n) => `<div class="list-row"><div class="grow"><span class="jn">${n ? n + "." : ""}</span>${esc(k.name)}${k.age ? ` <span class="small muted">· ${esc(k.age)}</span>` : ""}
    <span class="pill ${tagCls[k.tag] || "yellow"}">${esc(k.label)}</span>${k.note ? ` <span class="small muted">${esc(k.note)}</span>` : ""}</div></div>`;
  const totalCome = all.branches.reduce((a, b) => a + b.groups.reduce((x, g) => x + g.come, 0), 0);
  const totalAll = all.branches.reduce((a, b) => a + b.groups.reduce((x, g) => x + g.kids.length, 0), 0);
  page.innerHTML = `${journalDayHead()}
    <div class="row" style="max-width:640px;margin-bottom:14px">
      <div class="field"><label>Филиал</label><select id="jd-branch"><option value="0">Все филиалы</option>${brs.map((b) => `<option value="${b.id}" ${b.id === bid ? "selected" : ""}>${esc(b.name)}</option>`).join("")}</select></div>
      <div class="field" style="flex:0 0 170px"><label>Дата</label><input id="j-date" type="date" value="${S.journal.date}"></div></div>
    ${all.branches.length ? `<div class="jm-sub" style="max-width:none"><div class="jm-sub-n">${totalCome}</div><div><b>ждём за день</b> из ${totalAll} по списку
      <div class="small muted">${all.branches.map((b) => `${esc(b.branch)}: ${b.groups.reduce((x, g) => x + g.come, 0)}`).join(" · ")}</div></div></div>` : ""}
    <div class="row" style="gap:8px;margin:0 0 12px;flex-wrap:wrap"><button class="btn sm" id="j-copy" style="flex:0 0 auto">📋 Скопировать списки</button><button class="btn sm" id="j-print" style="flex:0 0 auto">🖨 Лист для печати</button></div>
    ${all.branches.map((b) => `<h3 style="margin:18px 0 8px">${esc(b.branch)}</h3>
      ${b.trials.length ? `<div class="panel" style="background:var(--yellow-soft);padding:10px 14px;margin-bottom:10px"><b>🆕 Вводники: ${b.trials.length}</b>
        ${b.trials.map((t, i) => `<div class="small">${i + 1}. ${esc(t.name)}${t.age ? ", " + esc(t.age) : ""}, ${esc(shiftTitle({ name: t.group, time: t.time }, b.branch))}${t.parent ? ", родитель: " + esc(t.parent) : ""}</div>`).join("")}</div>` : ""}
      ${b.groups.map((g) => { const come = g.kids.filter((k) => !["warned", "vacation"].includes(k.tag)), away = g.kids.filter((k) => ["warned", "vacation"].includes(k.tag));
        return `<div class="panel" style="padding:0;margin-bottom:10px"><div class="list-row" style="background:var(--surface-2)"><div class="grow"><b>${esc(shiftTitle(g, b.branch))}</b>${shiftAge(g, b.branch) ? ` <span class="small muted">${esc(shiftAge(g, b.branch))}</span>` : ""}${g.coach ? ` <span class="small muted">· ${esc(g.coach)}</span>` : ""}</div><span class="pill green">ждём ${come.length}</span></div>
          ${come.map((k, i) => kidRow(k, i + 1)).join("")}${away.map((k) => kidRow(k, 0)).join("")}</div>`; }).join("")}`).join("") || `<div class="muted">В этот день тренировок нет</div>`}`;
  wireJournalDayHead(page);
  page.querySelector("#jd-branch").onchange = (e) => step(() => { S.journal.dayBranch = +e.target.value; });
  page.querySelector("#j-date").onchange = (e) => step(() => { S.journal.date = e.target.value; });
  page.querySelector("#j-copy").onclick = () => shiftCopy(bid, S.journal.date);
  page.querySelector("#j-print").onclick = () => shiftPrint(bid, S.journal.date);
}

function journalDayHead() {
  const day = S.journal.mode === "dayall";
  return `<div class="page-head"><h2 class="grow">Журнал посещений</h2>
    <div class="seg"><button data-jmode="group" class="${!day && S.journal.mode !== "month" ? "active" : ""}">Группа</button><button data-jmode="month" class="${S.journal.mode === "month" ? "active" : ""}">Весь месяц</button><button data-jmode="dayall" class="${day ? "active" : ""}">Все занятия дня</button></div></div>`;
}
function wireJournalDayHead(page) {
  page.querySelectorAll("[data-jmode]").forEach((b) => (b.onclick = () => step(() => { S.journal.mode = b.dataset.jmode; })));
}
