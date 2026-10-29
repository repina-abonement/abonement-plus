/* Аватарки сотрудников и карточка сотрудника (фото, анкета тренера) */

const AVA_COLORS = ["#e7f6ee", "#e9e6fb", "#fdf1d6", "#e3f0fb", "#fde6ea", "#e6f4f4"];
const AVA_INK = ["#1d8a57", "#4b3fb0", "#9a6b00", "#1f6fb2", "#b3324a", "#1f7a7a"];

function staffById(id) { return ((S.meta && S.meta.staff) || []).find((u) => u.id === +id); }

/* круглая аватарка: фото, а если его нет - инициалы на мягком цвете */
function avatar(id, name, size = 36) {
  const u = staffById(id) || { id, name: name || "" };
  const nm = name || u.name || "";
  const st = `width:${size}px;height:${size}px;font-size:${Math.round(size * 0.38)}px`;
  const stk = u.sticker && size >= 28 ? `<i class="ava-stk" style="font-size:${Math.max(12, Math.round(size * 0.36))}px">${esc(u.sticker)}</i>` : "";
  // фото во весь рост с сайта: приближаем на лицо; обрезанное при загрузке (photo_sq) показываем целиком
  if (u.photo_at) return `<span class="ava-wrap" style="width:${size}px;height:${size}px" title="${esc(u.nickname || "")}"><span class="ava-photo${u.photo_sq ? " sq" : ""}" style="background-image:url('/api/users/${u.id}/photo?v=${encodeURIComponent(u.photo_at)}')"></span>${stk}</span>`;
  const ini = nm.split(" ").filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";
  const c = (+id || 0) % AVA_COLORS.length;
  const ini_ = `<span class="ava ava-ini" style="${st};background:${AVA_COLORS[c]};color:${AVA_INK[c]}">${esc(ini)}</span>`;
  return stk ? `<span class="ava-wrap" style="width:${size}px;height:${size}px" title="${esc(u.nickname || "")}">${ini_}${stk}</span>` : ini_;
}

function fmtPhone(p) {
  const d = String(p || "").replace(/\D/g, "");
  return d.length === 11 ? `+7 ${d.slice(1, 4)} ${d.slice(4, 7)}-${d.slice(7, 9)}-${d.slice(9)}` : p;
}

const ROLE_LABEL = { admin: "Руководитель", operator: "Администратор", coach: "Тренер" };

async function openStaffProfile(uid) {
  let p;
  try { p = await api("GET", `/api/users/${uid}/profile`); } catch (e) { return fail(e); }
  const b = p.bio || {}, admin = S.user.role === "admin", mine = S.user.id === p.id;
  const row = (k) => b[k] ? `<div class="sp-row"><div class="small muted">${esc(p.fields[k])}</div><div>${esc(b[k])}</div></div>` : "";
  openDrawer(`
    <div class="sp-head">${avatar(p.id, p.name, 96)}<div><h2>${esc(p.name)}</h2>${b.nickname ? `<div class="sp-nick">«${esc(b.nickname)}»</div>` : ""}<div class="muted">${ROLE_LABEL[p.role] || ""}${p.phone ? `<br>${esc(fmtPhone(p.phone))}` : ""}${p.tg_username ? `<br>@${esc(p.tg_username)}` : ""}</div></div></div>
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
    cropFace(f, async (data) => { try { await api("POST", `/api/users/${uid}/photo`, { data, square: true }); toast("Фото сохранено"); refresh(); } catch (err) { fail(err); } });
    e.target.value = "";
  };
  if ($("#sp-del")) $("#sp-del").onclick = async () => { try { await api("POST", `/api/users/${uid}/photo`, { remove: true }); refresh(); } catch (err) { fail(err); } };
}

/* Кадрирование при загрузке: человек двигает фото и приближает, пока лицо не встанет в кружок.
   На сервер уходит готовый квадрат 480×480, поэтому новые аватарки сразу по центру. */
function cropFace(file, onDone) {
  const url = URL.createObjectURL(file), img = new Image();
  img.onerror = () => { URL.revokeObjectURL(url); toast("Не получилось открыть фото. Нужен JPG или PNG (фото с iPhone: сделайте скриншот)", true); };
  img.onload = () => {
    const V = 280;  // окно предпросмотра
    const base = Math.max(V / img.width, V / img.height);
    let zoom = 1.6, x = 0, y = 0;  // смещение центра фото от центра окна, в пикселях окна
    const box = document.createElement("div");
    box.className = "crop-modal";
    box.innerHTML = `<div class="crop-card"><b>Наведите кружок на лицо</b><div class="small muted">Двигайте фото пальцем или мышкой, ползунком приближайте</div>
      <div class="crop-view"><canvas width="${V * 2}" height="${V * 2}"></canvas><i class="crop-ring"></i></div>
      <input type="range" min="1" max="5" step="0.01" value="${zoom}" class="crop-zoom" aria-label="Приближение">
      <div class="row" style="gap:8px"><button class="btn" data-cx>Отмена</button><button class="btn primary" data-cok>Сохранить</button></div></div>`;
    document.body.appendChild(box);
    const cv = box.querySelector("canvas"), ctx = cv.getContext("2d");
    const draw = (c, size) => {
      const s = base * zoom * size / V;
      c.fillStyle = "#fff"; c.fillRect(0, 0, size, size);
      c.drawImage(img, size / 2 + x * size / V - img.width * s / 2, size / 2 + y * size / V - img.height * s / 2, img.width * s, img.height * s);
    };
    const clamp = () => {  // фото не должно уходить из кружка пустым краем
      const w = img.width * base * zoom, h = img.height * base * zoom;
      x = Math.max(-(w - V) / 2, Math.min((w - V) / 2, x)); y = Math.max(-(h - V) / 2, Math.min((h - V) / 2, y));
    };
    const render = () => { clamp(); draw(ctx, V * 2); };
    y = img.height > img.width ? img.height * base * zoom * 0.25 : 0;  // фото во весь рост: начинаем с верхней части
    render();
    let drag = null;
    cv.onpointerdown = (e) => { drag = { x: e.clientX - x, y: e.clientY - y }; cv.setPointerCapture(e.pointerId); };
    cv.onpointermove = (e) => { if (!drag) return; x = e.clientX - drag.x; y = e.clientY - drag.y; render(); };
    cv.onpointerup = () => { drag = null; };
    box.querySelector(".crop-zoom").oninput = (e) => { const k = +e.target.value / zoom; zoom = +e.target.value; x *= k; y *= k; render(); };
    const close = () => { box.remove(); URL.revokeObjectURL(url); };
    box.querySelector("[data-cx]").onclick = close;
    box.querySelector("[data-cok]").onclick = () => {
      const out = document.createElement("canvas"); out.width = out.height = 480;
      draw(out.getContext("2d"), 480);
      const data = out.toDataURL("image/jpeg", 0.9);
      close(); onDone(data);
    };
  };
  img.src = url;
}
