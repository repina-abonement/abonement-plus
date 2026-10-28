/* Аватарки сотрудников и карточка сотрудника (фото, анкета тренера) */

const AVA_COLORS = ["#e7f6ee", "#e9e6fb", "#fdf1d6", "#e3f0fb", "#fde6ea", "#e6f4f4"];
const AVA_INK = ["#1d8a57", "#4b3fb0", "#9a6b00", "#1f6fb2", "#b3324a", "#1f7a7a"];

function staffById(id) { return ((S.meta && S.meta.staff) || []).find((u) => u.id === +id); }

/* круглая аватарка: фото, а если его нет - инициалы на мягком цвете */
function avatar(id, name, size = 36) {
  const u = staffById(id) || { id, name: name || "" };
  const nm = name || u.name || "";
  const st = `width:${size}px;height:${size}px;font-size:${Math.round(size * 0.38)}px`;
  if (u.photo_at) return `<img class="ava" src="/api/users/${u.id}/photo?v=${encodeURIComponent(u.photo_at)}" alt="" style="${st}" loading="lazy">`;
  const ini = nm.split(" ").filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";
  const c = (+id || 0) % AVA_COLORS.length;
  return `<span class="ava ava-ini" style="${st};background:${AVA_COLORS[c]};color:${AVA_INK[c]}">${esc(ini)}</span>`;
}

const ROLE_LABEL = { admin: "руководитель", operator: "администратор", coach: "тренер" };

async function openStaffProfile(uid) {
  let p;
  try { p = await api("GET", `/api/users/${uid}/profile`); } catch (e) { return fail(e); }
  const b = p.bio || {}, admin = S.user.role === "admin", mine = S.user.id === p.id;
  const row = (k) => b[k] ? `<div class="sp-row"><div class="small muted">${esc(p.fields[k])}</div><div>${esc(b[k])}</div></div>` : "";
  openDrawer(`
    <div class="sp-head">${avatar(p.id, p.name, 96)}<div><h2>${esc(p.name)}</h2><div class="muted">${ROLE_LABEL[p.role] || ""}${p.phone ? " · " + esc(p.phone) : ""}${p.tg_username ? " · @" + esc(p.tg_username) : ""}</div></div></div>
    ${p.groups.length ? `<div class="section"><h3>Группы</h3>${p.groups.map((g) => `<div class="small">${esc(g.name)} <span class="muted">· ${esc(g.branch)}, ${g.role}</span></div>`).join("")}</div>` : ""}
    ${["education", "experience", "achievements", "branches"].some((k) => b[k]) || b.quote ? `<div class="section">
      ${row("education")}${row("experience")}${row("achievements")}${row("branches")}
      ${b.quote ? `<div class="sp-quote">${b.quote_title ? `<b>${esc(b.quote_title)}</b>` : ""}<div>${esc(b.quote)}</div></div>` : ""}
      ${b.site_url ? `<a class="small" href="${esc(b.site_url)}" target="_blank" rel="noopener">Страница на сайте Футболики</a>` : ""}</div>` : ""}
    ${admin || mine ? `<details class="section"><summary><b>Изменить</b></summary>
      ${admin ? `<div class="field" style="margin-top:10px"><label>Фото</label><div class="row" style="gap:8px;flex-wrap:wrap">
        <label class="btn sm">Загрузить фото<input type="file" accept="image/*" id="sp-file" hidden></label>
        ${p.photo_at ? `<button class="btn sm" id="sp-del">Убрать фото</button>` : ""}</div></div>` : ""}
      <form id="sp-form">${Object.entries(p.fields).map(([k, t]) => `<div class="field"><label>${esc(t)}</label>${k === "quote" || k === "achievements"
        ? `<textarea name="${k}" rows="3">${esc(b[k] || "")}</textarea>` : `<input name="${k}" value="${esc(b[k] || "")}">`}</div>`).join("")}
      <button class="btn primary">Сохранить</button></form></details>` : ""}`);
  const refresh = async () => { S.meta = await api("GET", "/api/meta"); openStaffProfile(uid); render(); };
  if ($("#sp-form")) $("#sp-form").onsubmit = async (e) => {
    e.preventDefault();
    try { await api("PUT", `/api/users/${uid}/profile`, { bio: Object.fromEntries(new FormData(e.target)) }); toast("Сохранено"); refresh(); } catch (err) { fail(err); }
  };
  if ($("#sp-file")) $("#sp-file").onchange = (e) => {
    const f = e.target.files[0]; if (!f) return;
    if (f.size > 4 * 1024 * 1024) return toast("Фото до 4 МБ", true);
    const r = new FileReader();
    r.onload = async () => { try { await api("POST", `/api/users/${uid}/photo`, { data: r.result }); toast("Фото сохранено"); refresh(); } catch (err) { fail(err); } };
    r.readAsDataURL(f);
  };
  if ($("#sp-del")) $("#sp-del").onclick = async () => { try { await api("POST", `/api/users/${uid}/photo`, { remove: true }); refresh(); } catch (err) { fail(err); } };
}
