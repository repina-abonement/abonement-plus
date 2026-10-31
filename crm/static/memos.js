"use strict";
/* Памятки «Правила абонемента» для родителей: CRM рисует их сама из текущих цен и правил (crm/memos.py).
   Рисует браузер любого сотрудника: если цены поменялись, при следующем входе памятка перерисуется и уйдёт на сервер */

const MEMO = { W: 853, H: 1280, S: 1.5, navy: "#13234a", text: "#2c3d6b", orange: "#f2621f", teal: "#1e9b8f", bg: "#fbf6ea" };

const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
function memoLines(d) {
  const rub = (n) => Number(n).toLocaleString("ru-RU") + " ₽";
  const late = d.late_pct ? `После ${d.deadline_day} числа стоимость выше на ${String(d.late_pct).replace(".", ",")}%, скидки не действуют` : "";
  const mk = d.makeups && d.makeups.length ? d.makeups : [];
  if (d.kind === "program") {
    return [
      { h: `ОПЛАТА ДО ${d.deadline_day} ЧИСЛА`, t: [`Продлевайте абонемент на следующий месяц до ${d.deadline_day} числа`, late || "Покупка абонемента закрепляет место в группе"] },
      { h: "ДВА ТАРИФА", tariffs: [
        { name: d.ofp ? "АРКТИКА + ОФП" : "АРКТИКА", price: rub(d.arena), unit: "/ занятие", t: d.ofp ? [`ОФП ${rub(d.ofp)} за занятие`, "День ОФП выбираете при продлении"] : [] },
        { name: "ТОЛЬКО АРКТИКА", price: rub(d.arena_only), unit: "/ занятие", t: ["ОФП в счёт отработки не идёт"] }],
        t: [...(d.next ? [`${cap(d.next.from)}: ${rub(d.next.arena)}, ОФП ${rub(d.next.ofp)}, только Арктика ${rub(d.next.arena_only)}`] : []), "Второму ребёнку в семье скидка 20% на любом тарифе"] },
      ...(d.arena_sched || d.ofp_sched ? [{ h: "РАСПИСАНИЕ", t: [d.arena_sched ? `Арктика: ${d.arena_sched}` : "", d.ofp_sched ? `ОФП: ${d.ofp_sched}` : ""].filter(Boolean) }] : []),
      { h: "ПРОПУСКИ И ОТРАБОТКИ", t: ["Переносов нет, в том числе по справке", ...(mk.length ? [`Отработки по записи: ${mk.join("; ")}`] : ["Отработки по предварительной записи"])] },
      { h: "ВОЗВРАТЫ", t: ["Возвратов за состоявшиеся занятия нет", "Абонемент на полный месяц, сменить тариф можно со следующего месяца"] },
    ];
  }
  return [
    { h: `ОПЛАТА ДО ${d.deadline_day} ЧИСЛА`, t: [`Продлевайте абонемент на следующий месяц до ${d.deadline_day} числа`, late || "Покупка абонемента закрепляет место в группе"] },
    { h: "ДВА ТАРИФА", tariffs: [
      { name: "БЕЗ ПЕРЕСЧЁТОВ", price: rub(d.fixed), unit: "/ занятие", t: [`8 занятий = ${rub(d.fixed8)}`, "Пропуски можно отработать", "в текущем месяце"] },
      { name: "С ПЕРЕСЧЁТАМИ", price: rub(d.flex), unit: "/ занятие", t: [`8 занятий = ${rub(d.flex8)}`, "Пропуски переносим", `до ${d.credit} занятий на следующий месяц`] }],
      t: d.next ? [`${cap(d.next.from)}: ${rub(d.next.fixed)} и ${rub(d.next.flex)} за занятие`] : [] },
    { h: "ОТРАБОТКИ", t: [...(mk.length ? mk.slice(0, 3) : ["По субботам в специальных группах"]), "Только по предварительной записи и при наличии мест"] },
    { h: "ОТПУСК ИЛИ ГОСПИТАЛИЗАЦИЯ", t: ["Для обоих тарифов: предупредите нас до оплаты абонемента", "За этот период оплату не берём"] },
    { h: "ВОЗВРАТЫ", t: ["Возвратов за состоявшиеся занятия нет", "Абонемент закрепляет место в группе"] },
  ];
}

async function drawMemo(d) {
  try { await Promise.all(["900 60px Nunito", "800 26px Nunito", "700 20px Nunito", "400 18px Nunito"].map((f) => document.fonts.load(f))); } catch (_) {}
  const { W, H, S } = MEMO;
  const c = document.createElement("canvas"); c.width = W * S; c.height = H * S;
  const x = c.getContext("2d"); x.scale(S, S);
  const font = (w, size) => (x.font = `${w} ${size}px Nunito, "Segoe UI", Arial, sans-serif`);
  const circle = (cx, cy, r, color) => { x.fillStyle = color; x.beginPath(); x.arc(cx, cy, r, 0, Math.PI * 2); x.fill(); };
  const rrect = (rx, ry, rw, rh, r, fill, stroke) => {
    x.beginPath(); x.moveTo(rx + r, ry); x.arcTo(rx + rw, ry, rx + rw, ry + rh, r); x.arcTo(rx + rw, ry + rh, rx, ry + rh, r);
    x.arcTo(rx, ry + rh, rx, ry, r); x.arcTo(rx, ry, rx + rw, ry, r); x.closePath();
    if (fill) { x.fillStyle = fill; x.fill(); } if (stroke) { x.strokeStyle = stroke; x.lineWidth = 2; x.stroke(); }
  };
  const wrap = (text, maxW) => {
    const words = String(text).split(" "), out = []; let line = "";
    for (const w of words) { const t = line ? line + " " + w : w; if (x.measureText(t).width > maxW && line) { out.push(line); line = w; } else line = t; }
    if (line) out.push(line); return out;
  };
  const center = (text, y) => { x.textAlign = "center"; x.fillText(text, W / 2, y); x.textAlign = "left"; };
  // фон и круги, как у дизайнерской памятки
  x.fillStyle = MEMO.bg; x.fillRect(0, 0, W, H);
  circle(20, 70, 110, "#dfeedd"); circle(W + 10, 210, 100, "#fbe3c5"); circle(20, H - 210, 90, "#dfeedd"); circle(W - 10, H - 160, 80, "#fbe3c5");
  x.textBaseline = "alphabetic";
  x.fillStyle = MEMO.navy; font(900, 64); center("ФУТБОЛИКА 💚", 92);
  font(800, 26); center(d.title, 132);
  x.fillStyle = MEMO.orange; font(900, 56); center("ПРАВИЛА АБОНЕМЕНТА", 200);
  x.fillStyle = MEMO.teal; font(700, 22); center("Сохраните эту памятку", 240);
  // блоки 01-05: высоту считаем по тексту и ужимаем, если не помещается
  const blocks = memoLines(d);
  const left = 46, width = W - 92, textX = left + 104, textW = width - 104 - 22;
  const measure = (b, sz) => {
    font(400, sz);
    let h = 22 + 34 + b.t.reduce((a, s) => a + wrap(s, textW).length * (sz * 1.4), 0) + 18;
    if (b.tariffs) h += 150;
    return h;
  };
  let sz = 18, gap = 14;
  const avail = H - 270 - 70;
  while (sz > 14 && blocks.reduce((a, b) => a + measure(b, sz), 0) + gap * 4 > avail) sz -= 1;
  let y = 262;
  blocks.forEach((b, i) => {
    const h = measure(b, sz);
    x.save(); x.shadowColor = "rgba(0,0,0,.05)"; x.shadowBlur = 8; rrect(left, y, width, h, 20, "#ffffff"); x.restore();
    circle(left + 51, y + 51, 29, MEMO.teal);
    x.fillStyle = "#fff"; font(900, 24); x.textAlign = "center"; x.fillText(String(i + 1).padStart(2, "0"), left + 51, y + 60); x.textAlign = "left";
    x.fillStyle = MEMO.navy; font(900, 28); x.fillText(b.h, textX, y + 48);
    let ty = y + 56;
    if (b.tariffs) {
      const tw = (width - 104 - 22 - 14) / 2;
      b.tariffs.forEach((t, k) => {
        const tx = textX + k * (tw + 14);
        rrect(tx, ty + 6, tw, 140, 14, "#ffffff", MEMO.teal);
        x.save(); x.beginPath(); x.rect(tx, ty + 6, tw, 32); x.clip(); rrect(tx, ty + 6, tw, 60, 14, MEMO.teal); x.restore();
        x.fillStyle = "#fff"; font(800, 16); x.textAlign = "center"; x.fillText(t.name, tx + tw / 2, ty + 28);
        x.fillStyle = MEMO.orange; font(900, 30); const pw = x.measureText(t.price).width; font(800, 15); const uw = x.measureText(" " + t.unit).width;
        const px = tx + tw / 2 - (pw + uw) / 2; x.textAlign = "left"; font(900, 30); x.fillText(t.price, px, ty + 72);
        x.fillStyle = MEMO.navy; font(800, 15); x.fillText(" " + t.unit, px + pw, ty + 72);
        x.fillStyle = MEMO.text; font(400, 14); x.textAlign = "center";
        t.t.forEach((s, j) => x.fillText(s, tx + tw / 2, ty + 96 + j * 18));
        x.textAlign = "left";
      });
      ty += 150;
    }
    x.fillStyle = MEMO.text; font(400, sz);
    b.t.forEach((s) => wrap(s, textW).forEach((line) => { ty += sz * 1.4; x.fillText(line, textX, ty); }));
    y += h + gap;
  });
  x.fillStyle = MEMO.navy; font(800, 20); center("С любовью, Футболика ⚽", H - 30);
  x.fillStyle = "#9aa3b5"; font(400, 12); x.textAlign = "right";
  const t = new Date(); x.fillText(`цены на ${String(t.getDate()).padStart(2, "0")}.${String(t.getMonth() + 1).padStart(2, "0")}.${t.getFullYear()}`, W - 16, H - 10);
  x.textAlign = "left";
  return c.toDataURL("image/png");
}

/* При входе сотрудника: перерисовать памятки, у которых поменялись цены или правила */
let memoSyncing = false;
async function memoSync(force = false) {
  if (memoSyncing || !S.user || !can("members")) return;
  memoSyncing = true;
  try {
    const d = await api("GET", "/api/memos");
    for (const m of d.items) {
      if (m.fresh && !force) continue;
      const png = await drawMemo(m.data);
      await api("POST", "/api/memos/save", { key: m.key, hash: m.hash, png });
    }
  } catch (_) { /* тихо: попробуем при следующем входе */ }
  memoSyncing = false;
}

/* Настройки → Цены и правила: какие памятки сейчас уходят родителям */
async function renderMemosSettings(box) {
  await memoSync();
  const d = await api("GET", "/api/memos");
  box.innerHTML = `<h3>Памятки для родителей</h3>
    <div class="small muted" style="margin-bottom:10px">CRM рисует их сама из цен и правил на этой странице. Поменяли цену, и памятка перерисуется сама. Уходит родителям после первой оплаты</div>
    <div class="memo-grid">${d.items.map((m) => `<div class="memo-card"><b>${esc(m.data.title)}</b>
      ${m.drawn_at ? `<a href="/api/memos/png?key=${m.key}&h=${m.hash}" target="_blank"><img src="/api/memos/png?key=${m.key}&h=${m.hash}" alt="Памятка ${esc(m.data.title)}" loading="lazy"></a>` : `<div class="small muted">Рисую…</div>`}
      <div class="small muted">${m.fresh ? `✅ актуальная, нарисована ${esc((m.drawn_at || "").replace("T", " "))}` : "⏳ перерисовывается"}</div>
      <a class="btn sm" href="/api/memos/png?key=${m.key}&h=${m.hash}" download="Памятка ${esc(m.data.title)}.png">Скачать</a></div>`).join("") || `<div class="small muted">Нет филиалов с группами</div>`}</div>`;
}
