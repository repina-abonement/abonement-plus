"use strict";
/* Абонемент Плюс — результаты тренеров. Просто, крупно и с огоньком. */

S.coaches = { month: null, as: null };
const MEDAL = { 1: "🥇", 2: "🥈", 3: "🥉" };
const kidsWord = (n) => n % 10 === 1 && n % 100 !== 11 ? "ребёнок" : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? "ребёнка" : "детей";
const kidsGen = (n) => n % 10 === 1 && n % 100 !== 11 ? "ребёнка" : "детей";
const lowerFirst = (t) => t ? t[0].toLowerCase() + t.slice(1) : t;
const firstName = (name) => String(name || "").replace(/^Тренер\s+/i, "").replace(/\s*\(.*\)$/, "").split(" ")[0];

function ofText(o, empty = "пока нет данных") {
  return o ? `${o.raw[0]} из ${o.raw[1]}` : empty;
}

async function renderCoaches(page) {
  const C = S.coaches;
  const cur = S.meta.today.slice(0, 7);
  if (!C.month) C.month = cur;
  const prevM = (() => { const [y, m] = cur.split("-").map(Number); return m === 1 ? `${y - 1}-12` : `${y}-${String(m - 1).padStart(2, "0")}`; })();
  const d = await api("GET", `/api/coaches/board?month=${C.month}${!isCoach() && C.as ? "&as_coach=" + C.as : ""}`);
  const me = d.rows.find((r) => r.is_me);
  const h = d.hero;

  page.innerHTML = `
    <div class="page-head"><h2 class="grow">${isCoach() ? "Мои результаты" : "Тренеры"}</h2>
      <div class="seg">${[[cur, "Этот месяц"], [prevM, "Прошлый"]].map(([m, t]) => `<button data-m="${m}" class="${C.month === m ? "active" : ""}">${t}</button>`).join("")}</div>
      ${isCoach() ? "" : `<select id="as-coach" style="width:auto"><option value="">Как видит тренер…</option>${d.rows.map((r) => `<option value="${r.id}" ${+C.as === r.id ? "selected" : ""}>${esc(r.name)}</option>`).join("")}</select>`}
    </div>

    ${!d.rows.length ? `<div class="panel center" style="padding:40px">У тренеров пока нет групп. Администратор закрепляет группы в Настройках</div>` : ""}

    ${h && me ? `
    <section class="hero">
      <div class="hero-place">${MEDAL[h.place] || "⚽"}<span>${h.place} место из ${h.of}</span></div>
      <h3>${esc(firstName(me.name))}, ${esc(lowerFirst(h.phrase))}</h3>
      ${h.gap ? `<div class="hero-sub">До места выше не хватает всего ${h.gap} ${kidsGen(h.gap)}. Приведи друзей своих учеников и обгонишь 😉</div>` : ""}
      ${h.badges.length ? `<div class="hero-badges">${h.badges.map((b) => `<span>🏅 ${esc(b)}</span>`).join("")}</div>` : ""}
    </section>
    <div class="tiles">
      ${tile("🧒", "Детей в твоих группах", me.kids, me.delta > 0 ? `+${me.delta} к прошлому месяцу, так держать!` : me.delta < 0 ? `${me.delta} к прошлому месяцу, давай вернём` : "Столько же, сколько в прошлом месяце", me.phrases.fill, `${me.groups} ${me.groups === 1 ? "группа" : me.groups < 5 ? "группы" : "групп"} · заполнено ${me.kids} из ${me.seats} мест`, me.fill)}
      ${tile("🎯", "Остались после пробного", ofText(me.trial_of, "—"), me.came ? `Пришли на пробное ${me.came}, купили абонемент ${me.bought}` : "В этом месяце пробных не было", me.phrases.trial, "", me.trial_conv)}
      ${tile("🛡️", "Продлились", ofText(me.stay_of, "—"), me.kids_prev ? `В прошлом месяце ${me.kids_prev} ${kidsWord(me.kids_prev)}, из них остались ${me.stayed}` : "Сравнивать пока не с чем", me.phrases.stay, "", me.stay)}
      ${tile("❤️", "Приходят на тренировки", me.att_of ? `${me.att_of.num} из ${me.att_of.den}` : "—", me.att_of ? `Из ${me.att_of.den === 10 ? "каждых 10" : me.att_of.den} отметок в журнале ${me.att_of.num} это «был»` : "Отмечай детей в журнале, и здесь появится цифра", me.phrases.att, "", me.attendance)}
    </div>` : ""}

    ${d.rows.length ? `
    <section class="panel league">
      <h3>🏆 Турнирная таблица тренеров · ${esc(d.month_label)}</h3>
      <div class="small muted" style="margin-bottom:10px">Места по количеству детей. Чем больше детей ходит и продлевается, тем выше ты в таблице</div>
      <div class="league-rows">${d.rows.map((r) => leagueRow(r, d.rows[0].kids)).join("")}</div>
    </section>` : ""}

    ${d.nominations.length ? `
    <h3 style="margin:18px 0 10px">Номинации месяца</h3>
    <div class="noms">${d.nominations.map((n) => `
      <div class="nom ${n.me ? "mine" : ""}"><div class="nom-emoji">${n.emoji}</div>
        <div><div class="nom-title">${esc(n.title)}</div><div class="nom-who">${n.winners.map((w) => esc(firstName(w))).join(", ")}</div>
        <div class="small muted">${nomValue(n, d.rows)}</div></div></div>`).join("")}</div>` : ""}

    <details class="panel how" style="margin-top:16px"><summary>Как считаются цифры?</summary>
      <ul>
        <li><b>Дети</b> это все, у кого есть абонемент в твои группы на этот месяц. Ребёнок в двух твоих группах считается один раз</li>
        <li><b>Остались после пробного</b> это сколько детей из пришедших на пробное в твою группу купили абонемент</li>
        <li><b>Продлились</b> это сколько детей прошлого месяца взяли абонемент и на этот месяц</li>
        <li><b>Приходят на тренировки</b> считается по отметкам «был» и «пропуск» в журнале. Не отметил, значит не посчиталось 😉</li>
      </ul></details>`;

  page.querySelectorAll("[data-m]").forEach((b) => (b.onclick = () => { C.month = b.dataset.m; render(); }));
  if ($("#as-coach")) $("#as-coach").onchange = (e) => { C.as = e.target.value || null; render(); };
}

function tile(emoji, title, big, line, joke, sub, pct) {
  return `<div class="tile">
    <div class="tile-head"><span class="tile-emoji">${emoji}</span>${esc(title)}</div>
    <div class="tile-big">${esc(big)}</div>
    ${pct != null ? `<div class="bar"><i style="width:${Math.min(100, pct)}%"></i></div>` : ""}
    <div class="tile-line">${esc(line)}</div>
    ${sub ? `<div class="small muted">${esc(sub)}</div>` : ""}
    <div class="tile-joke">${esc(joke)}</div>
  </div>`;
}

function leagueRow(r, top) {
  const w = top ? Math.max(6, Math.round(100 * r.kids / top)) : 0;
  return `<div class="lrow ${r.is_me ? "me" : ""}">
    <div class="lplace">${MEDAL[r.place] || r.place}</div>
    <div class="lmain">
      <div class="lname">${esc(r.name)}${r.is_me ? ` <span class="pill green">это ты</span>` : ""}</div>
      <div class="lbar"><i style="width:${w}%"></i><b>${r.kids} ${kidsWord(r.kids)}</b>${r.delta ? `<em class="${r.delta > 0 ? "up" : "down"}">${r.delta > 0 ? "▲ +" : "▼ "}${r.delta}</em>` : ""}</div>
      <div class="lfacts">
        <span>👥 ${r.groups} ${r.groups === 1 ? "группа" : r.groups < 5 ? "группы" : "групп"}</span>
        <span>🏟️ ${r.kids} из ${r.seats} мест</span>
        <span>🎯 пробные: ${ofText(r.trial_of, "не было")}</span>
        <span>🛡️ продлились: ${ofText(r.stay_of, "—")}</span>
        <span>❤️ ходят: ${r.att_of ? `${r.att_of.num} из ${r.att_of.den}` : "—"}</span>
      </div>
    </div></div>`;
}

function nomValue(n, rows) {
  if (n.key === "kids") return `${n.value} ${kidsWord(n.value)}`;
  if (n.key === "trial_conv") return `${Math.round(n.value / 10)} из 10 остаются после пробного`;
  if (n.key === "stay") return `${Math.round(n.value / 10)} из 10 детей продлились`;
  if (n.key === "attendance") return `${Math.round(n.value / 10)} из 10 тренировок дети на поле`;
  if (n.key === "fill") { const r = rows.find((x) => x.fill === n.value); return r ? `${r.kids} из ${r.seats} мест заняты` : ""; }
  return "";
}
