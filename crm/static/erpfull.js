/* Complete source retention and conservative native reconciliation. Owner only. */
function erpFullSummary(r) {
  return `<p>Строк в файлах: <b>${r.source_rows}</b>. Связано с клиентами: <b>${r.linked_rows || 0}</b>. Сверено с рабочими записями: <b>${r.native_rows || 0}</b></p>
    <p>Требуют разбора: <b>${r.issue_count || 0}</b>. Сохранение исходной строки не означает, что все её значения перенесены в рабочие поля CRM</p>
    <p class="small">${Object.entries(r.counts || {}).map(([k,v])=>`${esc(({linked_ids:'Связано ID ERP',filled_births:'Заполнено дат рождения',filled_contacts:'Дополнено контактов',added_visits:'Добавлено посещений',matched_payments:'Сверено оплат',added_payments:'Добавлено оплат',added_tasks:'Добавлено задач',added_subscriptions:'Добавлено абонементов'})[k] || k)}: ${v}`).join(' · ')}</p>
    ${r.issues?.length ? `<details><summary>Список для разбора (${r.issues.length})</summary><p class="small">На экране первые 200 строк. После переноса полный список доступен в отчёте сверки</p><div style="max-height:360px;overflow:auto"><table class="tbl"><thead><tr><th>Файл, строка</th><th>Карточка</th><th>Причина</th></tr></thead><tbody>${r.issues.slice(0,200).map(x=>`<tr><td>${esc(x.file)}: ${x.row_num}</td><td>${x.student_id || ''}</td><td>${esc(x.reason)}</td></tr>`).join('')}</tbody></table></div></details>` : ''}`;
}

function erpFullSetup(box, data) {
  const el = document.createElement('div');
  el.className = 'erp-step'; el.id = 'erp-full';
  el.innerHTML = `<h3>Полная сверка и сохранение исходных данных</h3>
    <p class="small">Выберите нужные выгрузки. Все колонки останутся в CRM вместе с файлом и номером строки. Однозначные посещения, оплаты и история задач будут перенесены, расхождения попадут в список. Этапы клиентов и отметки тренеров не перезаписываются</p>
    <div style="max-height:230px;overflow:auto">${data.uploads.map(u=>`<label class="check"><input type="checkbox" data-full-file="${u.id}">${esc(u.filename)} (${u.count} строк)</label>`).join('')}</div>
    <button class="btn" id="erp-full-plan">Проверить выбранные файлы</button>
    <button class="btn primary" id="erp-full-apply" disabled>Сохранить и перенести проверенное</button>
    <button class="btn" id="erp-full-last">Последний результат</button>
    <a class="btn" href="/api/erp/full/report?download=1" download>Скачать полный отчёт сверки</a><div id="erp-full-out"></div>`;
  box.prepend(el);
  const out = el.querySelector('#erp-full-out'), apply = el.querySelector('#erp-full-apply'), plan = el.querySelector('#erp-full-plan');
  let checked = null;
  el.querySelectorAll('[data-full-file]').forEach(c=>c.onchange=()=>{checked=null;apply.disabled=true;});
  const ids = () => [...el.querySelectorAll('[data-full-file]:checked')].map(c=>+c.dataset.fullFile);
  plan.onclick = async () => {
    checked=null;apply.disabled=true;plan.disabled=true;out.textContent='Проверяю на отдельной копии базы…';
    try { const selected=ids(), r=await api('POST','/api/erp/full/plan',{ids:selected}); checked={ids:selected,token:r.token};out.innerHTML=erpFullSummary(r);apply.disabled=false; }
    catch(e){out.textContent=e.message;fail(e);}finally{plan.disabled=false;}
  };
  apply.onclick = async () => {
    if(!checked)return; apply.disabled=true;plan.disabled=true;
    try {const r=await api('POST','/api/erp/full/apply',checked);out.innerHTML=`<b>Сохранено ${esc(r.at)}</b>`+erpFullSummary(r);checked=null;}
    catch(e){fail(e);out.textContent=e.message;}finally{plan.disabled=false;}
  };
  el.querySelector('#erp-full-last').onclick=async()=>{try{out.innerHTML=erpFullSummary(await api('GET','/api/erp/full/report'));}catch(e){fail(e);}};
}

function erpFullClient(body, sid) {
  const button = body.querySelector('#erp-full-client');
  if(!button)return;
  button.onclick=async()=>{
    const out=body.querySelector('#erp-full-client-out');
    const page=async(offset)=>{
      const r=await api('GET',`/api/erp/full/rows?sid=${sid}&offset=${offset}`);
      out.innerHTML=`<p>Исходных строк ERP: ${r.total}. Показаны ${r.items.length ? offset+1 : 0}-${offset+r.items.length}</p>`+r.items.map(x=>`<details><summary>${esc(x.filename)}, строка ${x.row_num} · ${esc(x.reason)}</summary><table class="tbl">${x.fields.map(([k,v])=>`<tr><th>${esc(k)}</th><td style="white-space:pre-wrap">${esc(String(v))}</td></tr>`).join('')}</table></details>`).join('')+`${offset?'<button class="btn" data-full-prev>Назад</button>':''}${offset+r.items.length<r.total?'<button class="btn" data-full-next>Далее</button>':''}`;
      if(out.querySelector('[data-full-next]'))out.querySelector('[data-full-next]').onclick=()=>page(offset+100).catch(fail);
      if(out.querySelector('[data-full-prev]'))out.querySelector('[data-full-prev]').onclick=()=>page(Math.max(0,offset-100)).catch(fail);
    };
    try{await page(0);}catch(e){fail(e);}
  };
}
