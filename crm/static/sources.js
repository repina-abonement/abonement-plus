"use strict";
/* Абонемент Плюс — рекламные каналы и источники. */

function sourceSelect(name, value, legacy) {
  const ad = S.meta.ad || { channels: [], sources: [] };
  const groups = ad.channels.map((c) => {
    const list = ad.sources.filter((x) => x.channel_id === c.id);
    return list.length ? `<optgroup label="${esc(c.name)}">${list.map((x) => `<option value="${x.id}" ${x.id === value ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</optgroup>` : "";
  }).join("");
  const previous=value&&!ad.sources.some(x=>x.id===value)?`<option value="${value}" selected>${esc(legacy||'Ранее выбранный источник (архив)')}</option>`:'';
  return `<select ${name.startsWith("f-") ? `id="${name}"` : `name="${name}"`}><option value="">${legacy && !value ? esc(legacy) : "— не указано —"}</option>${previous}${groups}</select>`;
}

async function renderSourceSettings(box) {
  const d=await api('GET','/api/sources?all=1');
  const archived=box.dataset.showArchived==='1',channels=d.channels.filter(c=>archived||c.active);
  const channel=channels.find(c=>c.id===+box.dataset.sourceChannel)||channels[0];
  if(channel)box.dataset.sourceChannel=channel.id;
  const list=channel?d.sources.filter(x=>x.channel_id===channel.id&&(archived||x.active)):[];
  box.innerHTML=`<h3>Рекламные каналы и источники</h3><div class="sources-topline"><span class="small muted">Выберите канал и измените его источники.</span><label class="check small"><input type="checkbox" data-show-archived ${archived?'checked':''}> Показать архив</label></div><div class="sources-editor"><aside class="sources-nav"><h4>Каналы</h4><nav aria-label="Рекламные каналы">${channels.map(c=>`<button type="button" class="source-channel ${c.id===channel?.id?'on':''}" data-channel="${c.id}" aria-pressed="${c.id===channel?.id}">${esc(c.name)}${c.active?'':' · архив'}</button>`).join('')}</nav><form data-channel-add class="sources-add"><label>Новый канал<input name="name" required placeholder="Название"></label><button class="btn sm" type="submit">Добавить канал</button><span class="need-err" role="alert"></span></form></aside><div class="sources-content">${channel?`<form data-channel-edit class="sources-channel-edit"><label>Название канала<input name="name" value="${esc(channel.name)}" required></label><button class="btn sm" type="submit">Сохранить название</button><button class="btn sm" type="button" data-channel-active>${channel.active?'Убрать канал':'Вернуть канал'}</button><span class="need-err" role="alert"></span></form><h4>Источники</h4><div class="sources-rows">${list.map(x=>`<form data-source-row="${x.id}" class="source-edit-row"><input name="name" value="${esc(x.name)}" required aria-label="Название источника"><span class="small muted">${x.active?'':'В архиве'}</span><div class="sources-row-actions"><button class="btn sm" type="submit">Сохранить</button><button class="btn sm" type="button" data-source-move>Перенести</button><button class="btn sm" type="button" data-source-active>${x.active?'Убрать':'Вернуть'}</button></div><div data-source-destination hidden><span class="small muted">В какой канал перенести?</span>${d.channels.filter(c=>c.active&&c.id!==channel.id).map(c=>`<label class="check"><input type="radio" name="destination" value="${c.id}"> ${esc(c.name)}</label>`).join('')}<button class="btn sm" type="button" data-source-transfer>Перенести в выбранный канал</button></div><div class="need-err" role="alert"></div></form>`).join('')||'<p class="small muted">Пока нет источников.</p>'}</div>${channel.active?'<form data-source-add class="sources-add"><label>Новый источник<input name="name" required placeholder="Название источника"></label><button class="btn sm" type="submit">Добавить источник</button><span class="need-err" role="alert"></span></form>':''}`:'<p>Добавьте первый канал.</p>'}</div></div><button type="button" class="linkish" data-source-import-toggle>Вставить список из ERP</button><form data-source-import hidden><label>По строке «Канал;Источник»<textarea name="text" rows="5" required></textarea></label><button class="btn sm" type="submit">Добавить список</button><span class="need-err" role="alert"></span></form>`;
  const refresh=async()=>{S.meta.ad=await api('GET','/api/sources');await renderSourceSettings(box)};
  const action=async(form,method,url,payload)=>{const buttons=[...form.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);form.querySelector('.need-err').textContent='';try{const r=await api(method,url,payload);await refresh();return r}catch(e){form.querySelector('.need-err').textContent=e.message;buttons.forEach(b=>b.disabled=false)}};
  box.querySelectorAll('[data-channel]').forEach(b=>b.onclick=()=>{box.dataset.sourceChannel=b.dataset.channel;renderSourceSettings(box).catch(fail)});
  box.querySelector('[data-show-archived]').onchange=e=>{box.dataset.showArchived=e.target.checked?'1':'0';renderSourceSettings(box).catch(fail)};
  const addChannel=box.querySelector('[data-channel-add]');addChannel.onsubmit=async e=>{e.preventDefault();if(addChannel.reportValidity())await action(addChannel,'POST','/api/ad-channels',{name:addChannel.elements.name.value})};
  if(channel){
    const form=box.querySelector('[data-channel-edit]'),expected={name:channel.name,active:channel.active};
    form.onsubmit=e=>{e.preventDefault();if(form.reportValidity())action(form,'PATCH',`/api/ad-channels/${channel.id}`,{name:form.elements.name.value,expected})};
    form.querySelector('[data-channel-active]').onclick=()=>action(form,'PATCH',`/api/ad-channels/${channel.id}`,{active:!channel.active,expected});
    box.querySelectorAll('[data-source-row]').forEach(row=>{const source=d.sources.find(x=>x.id===+row.dataset.sourceRow),expected={name:source.name,channel_id:source.channel_id,active:source.active};const save=extra=>action(row,'PATCH',`/api/sources/${source.id}`,{name:row.elements.name.value,expected,...extra});
      row.onsubmit=e=>{e.preventDefault();if(row.reportValidity())save({})};
      row.querySelector('[data-source-active]').onclick=()=>save({active:!source.active});
      row.querySelector('[data-source-move]').onclick=()=>{row.querySelector('[data-source-destination]').hidden=!row.querySelector('[data-source-destination]').hidden};
      row.querySelector('[data-source-transfer]').onclick=()=>{const selected=row.querySelector('[name=destination]:checked');if(!selected){row.querySelector('.need-err').textContent='Выберите канал';return}save({channel_id:+selected.value})};
    });
    const add=box.querySelector('[data-source-add]');if(add)add.onsubmit=e=>{e.preventDefault();if(add.reportValidity())action(add,'POST',`/api/ad-channels/${channel.id}/sources`,{name:add.elements.name.value})};
  }
  const imp=box.querySelector('[data-source-import]');box.querySelector('[data-source-import-toggle]').onclick=()=>imp.hidden=!imp.hidden;
  imp.onsubmit=e=>{e.preventDefault();if(imp.reportValidity())action(imp,'POST','/api/sources/import',{text:imp.elements.text.value})};
}

async function renderSourceReport(box, month) {
  if (S.user.role !== "admin") return;
  const [y, m] = month.split("-").map(Number);
  const last = new Date(y, m, 0).getDate();
  const d = await api("GET", `/api/reports/sources?from=${month}-01&to=${month}-${String(last).padStart(2, "0")}`);
  const row = (x, name) => `<tr><td>${name}</td><td>${x.leads}</td><td>${x.trial}</td><td>${x.came}</td><td><b>${x.bought}</b></td><td>${x.leads ? Math.round(100 * x.bought / x.leads) + "%" : "—"}</td></tr>`;
  box.innerHTML = `<section class="panel" style="margin-top:14px"><h3>Откуда пришли лиды · ${esc(monthLabel(month))}</h3>
    ${d.sources.length ? `<div class="table-wrap"><table class="optable"><thead><tr><th>Канал</th><th>Лидов</th><th>Записаны на пробное</th><th>Пришли</th><th>Купили</th><th>Из лида в покупку</th></tr></thead>
      <tbody>${d.channels.map((c) => row(c, `<b>${esc(c.channel)}</b>`)).join("")}</tbody></table></div>
      <details style="margin-top:10px"><summary class="small muted">По каждому источнику</summary><div class="table-wrap"><table class="optable"><thead><tr><th>Источник</th><th>Лидов</th><th>Пробное</th><th>Пришли</th><th>Купили</th><th>Конверсия</th></tr></thead>
      <tbody>${d.sources.map((x) => row(x, `${esc(x.source)} <span class="small muted">${esc(x.channel)}</span>`)).join("")}</tbody></table></div></details>`
    : `<div class="small muted">В этом месяце лидов пока нет</div>`}</section>`;
}
