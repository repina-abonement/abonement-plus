"use strict";
/* Посты в каналы филиалов (Telegram и MAX): текст, фото, публикация сейчас или по расписанию. Каналы привязываются к филиалам ниже */

S.post = S.post || { text: "", targets: [], photo: null, when: "" };

async function renderPosts(page) {
  const d = await api("GET", "/api/channels");
  const P = S.post;
  const known = (k) => d.known.filter((x) => x.kind === k);
  const statusTxt = { scheduled: "⏰ запланирован", sent: "✅ опубликован", partial: "⚠️ опубликован не везде", failed: "❌ не опубликован", cancelled: "отменён" };
  const fmtDT = (s) => `${dShort(s)} ${s.slice(11, 16)}`;
  page.innerHTML = `
    <div class="page-head"><h2 class="grow">📣 Посты в каналы</h2></div>
    <div style="max-width:760px">
    <section class="section">
      ${d.targets.length ? `
      <div class="field"><label>Текст поста</label><textarea id="ps-text" rows="9" placeholder="Например: 🎉 Сегодня день рождения у нашего тренера…">${esc(P.text)}</textarea>
        <div class="small muted" id="ps-count"></div></div>
      <div class="field"><label>Картинка (необязательно)</label>
        ${P.photo ? `<div class="ps-photo"><img src="${P.photo}" alt=""><button class="btn sm" id="ps-photo-del">Убрать картинку</button></div>` : `<input type="file" accept="image/*" id="ps-photo">`}</div>
      <div class="field"><label>Куда</label><div class="ps-targets">${d.targets.map((t) => `<label class="check"><input type="checkbox" data-pt="${esc(t.key)}" ${P.targets.includes(t.key) ? "checked" : ""}> ${esc(t.label)}${t.title ? ` <span class="small muted">«${esc(t.title)}»</span>` : ""}</label>`).join("")}</div></div>
      <div class="field"><label>Когда</label><div class="row" style="gap:8px;align-items:center;flex-wrap:wrap">
        <label class="check" style="flex:0 0 auto"><input type="radio" name="ps-when" value="" ${P.when ? "" : "checked"}> Сейчас</label>
        <label class="check" style="flex:0 0 auto"><input type="radio" name="ps-when" value="later" ${P.when ? "checked" : ""}> Запланировать</label>
        <input type="datetime-local" id="ps-at" value="${esc(P.when)}" style="flex:0 0 220px;${P.when ? "" : "display:none"}"></div></div>
      <button class="btn primary" id="ps-send">${P.when ? "Запланировать" : "Опубликовать"}</button>
      <div class="small muted" style="margin-top:6px">Публикует бот школы. Пост уйдёт всем подписчикам выбранных каналов</div>`
      : `<div class="alert">Пока ни один канал не привязан к филиалу. Подключите каналы ниже, это делается один раз</div>`}
    </section>

    <details class="section" id="ps-setup" ${d.targets.length ? "" : "open"}><summary><b>⚙️ Каналы филиалов</b> <span class="small muted">подключить и привязать</span></summary>
      <div class="small" style="margin:8px 0 12px">
        <b>Бот Telegram:</b> ${d.tg_ok ? `✅ ${esc(d.tg_bot || "")}` : "❌ не подключён (Настройки → Связь с клиентами)"} ·
        <b>Бот MAX:</b> ${d.max_ok ? `✅ ${esc(d.max_bot || "")}` : "❌ не подключён (Настройки → Связь с клиентами)"}</div>
      ${d.branches.map((b) => `<div class="row ps-link" style="align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px"><b style="flex:1 1 140px">${esc(b.name)}</b>
        <select data-pl="${b.id}" data-k="tg" style="flex:1 1 200px"><option value="">Telegram: не выбран</option>${known("tg").map((c) => `<option value="${esc(c.chat_id)}" ${c.chat_id === b.tg ? "selected" : ""}>Telegram: ${esc(c.title || c.chat_id)}</option>`).join("")}</select>
        <select data-pl="${b.id}" data-k="max" style="flex:1 1 200px"><option value="">MAX: не выбран</option>${known("max").map((c) => `<option value="${esc(c.chat_id)}" ${c.chat_id === b.max ? "selected" : ""}>MAX: ${esc(c.title || c.chat_id)}</option>`).join("")}</select></div>`).join("")}
      <details style="margin-top:10px"><summary class="small"><b>Как подключить канал (один раз на каждый канал)</b></summary><div class="small" style="margin-top:6px">
        <b>Telegram:</b><ol><li>Откройте канал филиала в Telegram → нажмите на название канала вверху.</li>
          <li>«Администраторы» → «Добавить администратора» → в поиске ${esc(d.tg_bot || "бот школы")} → выберите его.</li>
          <li>Оставьте включённым право «Публикация сообщений», остальные можно выключить → «Сохранить».</li>
          <li>Вернитесь сюда и обновите страницу: канал появится в списке «Telegram» у филиалов. Если не появился, опубликуйте в канале любое сообщение и обновите ещё раз.</li></ol>
        <b>MAX:</b><ol><li>Откройте канал филиала в MAX → нажмите на название канала → «Администраторы» (или «Участники»).</li>
          <li>«Добавить администратора» → найдите бота школы ${d.max_bot ? `«${esc(d.max_bot)}»` : ""} → дайте право публиковать.</li>
          <li>Вернитесь сюда и обновите страницу: канал появится в списке «MAX».</li></ol>
        Потом выберите канал в строке своего филиала, CRM запомнит сразу.</div></details>
    </details>

    ${d.history.length ? `<section class="section"><h3>Последние посты</h3>${d.history.map((h) => `<div class="list-row" style="align-items:flex-start">
      <div class="grow"><div class="small">${statusTxt[h.status] || h.status} · ${fmtDT(h.status === "scheduled" ? h.publish_at : (h.sent_at || h.created_at))} · ${esc(h.created_by)}</div>
        <div style="white-space:pre-wrap;max-height:4.2em;overflow:hidden">${esc(h.text.slice(0, 220))}</div>
        <div class="small muted">${h.targets.map((t) => esc(t.label)).join(", ")}${h.photo ? " · с картинкой" : ""}</div>
        ${h.results.filter((r) => !r.ok).map((r) => `<div class="small" style="color:var(--red)">${esc(r.label)}: ${esc(r.error || "")}</div>`).join("")}</div>
      ${h.status === "scheduled" ? `<button class="btn sm" data-pcancel="${h.id}">Отменить</button>` : ""}</div>`).join("")}</section>` : ""}
    </div>`;
  const keep = () => { if ($("#ps-text")) P.text = $("#ps-text").value; };
  const count = () => { if ($("#ps-count")) { const n = $("#ps-text").value.length; $("#ps-count").textContent = `${n} знаков${n > 1024 && P.photo ? " · в Telegram картинка уйдёт отдельно, текст следующим сообщением" : ""}`; } };
  if ($("#ps-text")) { $("#ps-text").oninput = () => { keep(); count(); }; count(); }
  page.querySelectorAll("[data-pt]").forEach((c) => (c.onchange = () => { P.targets = [...page.querySelectorAll("[data-pt]:checked")].map((x) => x.dataset.pt); }));
  page.querySelectorAll('[name="ps-when"]').forEach((r) => (r.onchange = () => {
    const later = r.value === "later" && r.checked;
    $("#ps-at").style.display = later ? "" : "none"; P.when = later ? ($("#ps-at").value || "") : "";
    $("#ps-send").textContent = later ? "Запланировать" : "Опубликовать";
  }));
  if ($("#ps-at")) $("#ps-at").onchange = () => { P.when = $("#ps-at").value; };
  if ($("#ps-photo")) $("#ps-photo").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    try { keep(); P.photo = await repShrinkTo(f, 1600); renderPosts(page); } catch (err) { fail(err); }
  };
  if ($("#ps-photo-del")) $("#ps-photo-del").onclick = () => { keep(); P.photo = null; renderPosts(page); };
  if ($("#ps-send")) $("#ps-send").onclick = async (e) => {
    keep();
    const later = page.querySelector('[name="ps-when"][value="later"]').checked;
    if (later && !$("#ps-at").value) return toast("Выберите дату и время", true);
    if (!P.targets.length) return toast("Отметьте, в какие каналы", true);
    const names = d.targets.filter((t) => P.targets.includes(t.key)).map((t) => t.label).join(", ");
    if (!confirm(`${later ? "Запланировать" : "Опубликовать сейчас"} в: ${names}?`)) return;
    e.target.disabled = true;
    try {
      const r = await api("POST", "/api/posts", { text: P.text, photo_data: P.photo, targets: P.targets, publish_at: later ? $("#ps-at").value : "" });
      const bad = (r.results || []).filter((x) => !x.ok);
      toast(r.status === "scheduled" ? "Пост запланирован" : bad.length ? `Не везде: ${bad.map((x) => x.label).join(", ")}` : "Опубликовано 🎉", bad.length > 0);
      if (!bad.length) S.post = { text: "", targets: [], photo: null, when: "" };
      renderPosts(page);
    } catch (err) { fail(err); e.target.disabled = false; }
  };
  page.querySelectorAll("[data-pl]").forEach((s) => (s.onchange = async () => {
    try { await api("POST", "/api/channels/link", { branch_id: +s.dataset.pl, kind: s.dataset.k, chat_id: s.value }); toast("Сохранено"); renderPosts(page); } catch (e) { fail(e); }
  }));
  page.querySelectorAll("[data-pcancel]").forEach((b) => (b.onclick = async () => { await api("POST", `/api/posts/${b.dataset.pcancel}/cancel`, {}).catch(fail); renderPosts(page); }));
}

function repShrinkTo(file, max) {  // картинку для поста ужимаем до max px по большей стороне
  return new Promise((ok, bad) => {
    const url = URL.createObjectURL(file), img = new Image();
    img.onerror = () => { URL.revokeObjectURL(url); bad(new Error("Не открылась картинка")); };
    img.onload = () => {
      const k = Math.min(1, max / Math.max(img.width, img.height));
      const c = document.createElement("canvas"); c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url); ok(c.toDataURL("image/jpeg", 0.88));
    };
    img.src = url;
  });
}
