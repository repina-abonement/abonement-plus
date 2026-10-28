/* Направления со своими правилами (Сборная ПРО): тариф и день ОФП в карточке, расчёт по дате оплаты, цены, праздники */

function programBlock(d) {
  const ps = (d.program && d.program.programs) || [];
  if (!ps.length || d.student.funnel !== "members") return "";
  return ps.map((p) => {
    const pl = p.plan;
    const ofpName = (gid) => (p.ofp_groups.find((g) => g.id === gid) || {}).name || "";
    const form = (t, og) => `<div class="row prog-form" data-pform="${p.id}" ${pl ? "hidden" : ""} style="margin-top:8px">
        <div class="field"><label>Тариф</label><select data-ptar>${Object.entries(p.tariffs).map(([k, v]) => `<option value="${k}" ${k === t ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></div>
        <div class="field" data-pofpbox><label>День ОФП</label><select data-pofp>${p.ofp_groups.map((g) => `<option value="${g.id}" ${g.id === og ? "selected" : ""}>${esc(g.name)}</option>`).join("")}</select></div>
        <button class="btn primary sm" data-psave="${p.id}" style="flex:0;align-self:flex-end">${pl ? "Сохранить" : "Записать"}</button></div>`;
    if (!pl) return `<div class="prog-box"><b>${esc(p.name)}</b> <button class="btn sm" data-popen="${p.id}">Записать в ${esc(p.name)}</button>${form("pkg", (p.ofp_groups[0] || {}).id)}</div>`;
    const nx = pl.next;
    return `<div class="prog-box"><div class="row" style="align-items:center"><div class="grow"><b>${esc(p.name)}</b>: ${esc(p.tariffs[pl.tariff])}${pl.ofp_group_id ? `, ${esc(ofpName(pl.ofp_group_id))}` : ""}
        ${nx ? `<div class="small" style="color:var(--accent)">С ${monthLabel(nx.from)}: ${esc(p.tariffs[nx.tariff])}${nx.ofp_group_id ? `, ${esc(ofpName(nx.ofp_group_id))}` : ""}</div>` : ""}
        <div class="small muted">Тариф и день ОФП меняются только со следующего месяца. Разовых и переносов нет</div></div>
        <button class="btn sm" data-popen="${p.id}" style="flex:0">Сменить тариф</button></div>${form(nx ? nx.tariff : pl.tariff, nx ? nx.ofp_group_id : pl.ofp_group_id)}</div>`;
  }).join("");
}

function wireProgramBlock(body, sid, reload) {
  body.querySelectorAll("[data-popen]").forEach((b) => (b.onclick = () => { const f = body.querySelector(`[data-pform="${b.dataset.popen}"]`); f.hidden = !f.hidden; }));
  body.querySelectorAll("[data-pform]").forEach((f) => {
    const t = f.querySelector("[data-ptar]"), box = f.querySelector("[data-pofpbox]");
    const sync = () => { box.style.visibility = t.value === "pkg" ? "visible" : "hidden"; };
    t.onchange = sync; sync();
    f.querySelector("[data-psave]").onclick = async () => {
      try {
        const r = await api("POST", `/api/students/${sid}/program`, { program_id: +f.dataset.pform, tariff: t.value, ofp_group_id: t.value === "pkg" ? +f.querySelector("[data-pofp]").value : null });
        toast(r.applied === "next" ? `Сохранено: действует с ${monthLabel(r.from)}` : "Записан в сборную"); reload();
      } catch (e) { fail(e); }
    };
  });
}

function programQuoteHtml(q) {
  const p = q.program;
  return `<div class="total">${rub(q.total)}</div>
    ${p.lines.map((l) => `<div class="small">${esc(l.label)}: ${l.n} × ${rub(l.price)} = ${rub(l.sum)}</div>`).join("")}
    ${q.discount ? `<div class="small">Скидка «${esc(q.discount.name)}» ${q.discount.percent}% от цены до срока: −${rub(q.discount.amount)}</div>` : ""}
    ${p.newcomer ? `<div class="small">Новичок: только оставшиеся занятия этого месяца, без надбавки</div>` : `
    <div class="prog-alt small"><span class="${p.late ? "" : "on"}">При оплате до ${dShort(p.deadline)}: <b>${rub(p.on_time_total)}</b></span>
      <span class="${p.late ? "on" : ""}">после: <b>${rub(p.late_total)}</b>${p.late_total !== p.on_time_total ? " (без скидок)" : ""}</span></div>
    <div class="small muted">Сумма выбирается сама по дате оплаты</div>`}
    ${p.note ? `<div class="small" style="color:#b42318">${esc(p.note)}</div>` : ""}
    ${p.late && p.waitlist ? `<div class="alert" style="margin-top:6px">Оплата после срока: место не гарантировано, в листе ожидания ${p.waitlist}</div>` : ""}
    <div class="small muted">Даты: ${q.dates.map(dShort).join(", ")}</div>`;
}

/* Настройки: цены направлений и дни без занятий */
async function renderProgramSettings(box) {
  const [d, c] = await Promise.all([api("GET", "/api/programs"), api("GET", "/api/closures")]);
  const admin = S.user.role === "admin";
  box.innerHTML = d.programs.map((p) => `<div class="section"><h3>${esc(p.name)}</h3>
      <div class="small muted">Группы: ${p.groups.map((g) => `${esc(g.name)} (${esc(g.branch)})`).join(", ") || "нет"}</div>
      <table class="tbl" style="margin-top:10px"><thead><tr><th>С месяца</th><th>Арктика</th><th>ОФП</th><th>Только Арктика</th><th>После ${p.deadline_day} числа</th></tr></thead>
        <tbody>${p.prices.map((x) => `<tr><td>${monthLabel(x.valid_from)}</td><td>${rub(x.arena)}</td><td>${rub(x.ofp)}</td><td>${rub(x.arena_only)}</td><td>${x.late_pct ? "+" + x.late_pct + "%" : "без надбавки"}</td></tr>`).join("")}</tbody></table>
      ${admin ? `<div class="row" style="margin-top:10px;flex-wrap:wrap" data-pprice="${p.id}">
        <div class="field"><label>С месяца</label><input type="month" name="valid_from"></div>
        <div class="field"><label>Арктика</label><input type="number" name="arena"></div><div class="field"><label>ОФП</label><input type="number" name="ofp"></div>
        <div class="field"><label>Только Арктика</label><input type="number" name="arena_only"></div><div class="field"><label>Надбавка, %</label><input type="number" name="late_pct" value="5"></div>
        <button class="btn sm" style="flex:0;align-self:flex-end">Добавить цены</button></div>
      <div class="small muted">Старые цены остаются для своих месяцев: с 20 числа продаёте следующий месяц по новым, новичкам текущий по старым</div>
      <div class="row" style="margin-top:12px;flex-wrap:wrap" data-pparams="${p.id}">
        <div class="field"><label>Тренеру, % выручки</label><input name="coach_pct" type="number" value="${p.coach_pct}"></div>
        <div class="field"><label>Аренда Арктики за тренировку</label><input name="arena_rent" type="number" value="${p.arena_rent}"></div>
        <div class="field"><label>Роялти, %</label><input name="royalty_pct" type="number" value="${p.royalty_pct}"></div>
        <div class="field"><label>Эквайринг, %</label><input name="acquiring_pct" type="number" step="0.1" value="${p.acquiring_pct}"></div>
        <button class="btn sm" style="flex:0;align-self:flex-end">Сохранить</button></div>` : ""}
    </div>`).join("") + `<div class="section"><h3>Дни без занятий</h3>
      <div class="small muted">Праздники и другие даты: их нет в расчёте абонемента, журнале и на столе тренера</div>
      <ul class="history" style="margin-top:8px">${c.items.map((x) => `<li>${dShort(x.date_from)}${x.date_to !== x.date_from ? " — " + dShort(x.date_to) : ""} · ${x.grp ? esc(x.grp) : x.branch ? esc(x.branch) : "вся школа"}${x.note ? " · " + esc(x.note) : ""}
        ${admin ? `<button class="btn sm danger" data-cdel="${x.id}">Удалить</button>` : ""}</li>`).join("") || "<li class='muted'>Пока нет</li>"}</ul>
      ${admin ? `<div class="row" style="flex-wrap:wrap" id="cl-form"><div class="field"><label>С</label><input type="date" name="date_from"></div><div class="field"><label>По</label><input type="date" name="date_to"></div>
        <div class="field"><label>Где</label><select name="where"><option value="">Вся школа</option>${S.meta.branches.map((b) => `<option value="b${b.id}">${esc(b.name)}</option>`).join("")}${S.meta.groups.map((g) => `<option value="g${g.id}">${esc(g.name)}</option>`).join("")}</select></div>
        <div class="field"><label>Причина</label><input name="note" placeholder="Праздник"></div><button class="btn sm" style="flex:0;align-self:flex-end">Добавить</button></div>` : ""}</div>`;
  const val = (row, n) => row.querySelector(`[name=${n}]`).value;
  box.querySelectorAll("[data-pprice]").forEach((row) => (row.querySelector("button").onclick = async () => {
    try { await api("POST", `/api/programs/${row.dataset.pprice}/prices`, { valid_from: val(row, "valid_from"), arena: val(row, "arena"), ofp: val(row, "ofp"), arena_only: val(row, "arena_only"), late_pct: val(row, "late_pct") }); toast("Цены добавлены"); renderProgramSettings(box); } catch (e) { fail(e); }
  }));
  box.querySelectorAll("[data-pparams]").forEach((row) => (row.querySelector("button").onclick = async () => {
    const data = {}; row.querySelectorAll("input").forEach((i) => (data[i.name] = i.value));
    try { await api("PATCH", `/api/programs/${row.dataset.pparams}`, data); toast("Сохранено"); } catch (e) { fail(e); }
  }));
  if ($("#cl-form")) $("#cl-form").querySelector("button").onclick = async () => {
    const f = $("#cl-form"), w = val(f, "where");
    try { await api("POST", "/api/closures", { date_from: val(f, "date_from"), date_to: val(f, "date_to") || val(f, "date_from"), note: val(f, "note"),
      branch_id: w.startsWith("b") ? +w.slice(1) : null, group_id: w.startsWith("g") ? +w.slice(1) : null }); toast("Добавлено"); renderProgramSettings(box); } catch (e) { fail(e); }
  };
  box.querySelectorAll("[data-cdel]").forEach((b) => (b.onclick = async () => { try { await api("DELETE", `/api/closures/${b.dataset.cdel}`); renderProgramSettings(box); } catch (e) { fail(e); } }));
}
