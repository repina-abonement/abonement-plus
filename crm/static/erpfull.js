/* Complete source retention and conservative native reconciliation. Owner only. */
function erpFullSummary(r) {
  return `<p>Строк в файлах: <b>${r.source_rows}</b>. Связано с клиентами: <b>${r.linked_rows || 0}</b>. Сверено с рабочими записями: <b>${r.native_rows || 0}</b></p>
    <p>Требуют разбора: <b>${r.issue_count || 0}</b>. Сохранение исходной строки не означает, что все её значения перенесены в рабочие поля CRM</p>
    <p class="small">${Object.entries(r.counts || {}).map(([k,v])=>`${esc(({linked_ids:'Связано ID ERP',linked_source_identities:'Раздельно сопоставлено заявок и учеников',corrected_task_links:'Исправлено связей истории задач',quarantined_task_events:'Снято ошибочных связей истории задач',filled_births:'Заполнено дат рождения',filled_contacts:'Дополнено контактов',added_visits:'Добавлено посещений',matched_payments:'Сверено оплат',added_payments:'Добавлено оплат',added_tasks:'Добавлено задач',added_subscriptions:'Добавлено абонементов'})[k] || k)}: ${v}`).join(' · ')}</p>
    ${r.issues?.length ? `<details><summary>Список для разбора (${r.issues.length})</summary><p class="small">На экране первые 200 строк. После переноса полный список доступен в отчёте сверки</p><div style="max-height:360px;overflow:auto"><table class="tbl"><thead><tr><th>Файл, строка</th><th>Карточка</th><th>Причина</th></tr></thead><tbody>${r.issues.slice(0,200).map(x=>`<tr><td>${esc(x.file)}: ${x.row_num}</td><td>${x.student_id || ''}</td><td>${esc(x.reason)}</td></tr>`).join('')}</tbody></table></div></details>` : ''}`;
}

function erpFullSetup(box, data) {
  const el = document.createElement('div');
  el.className = 'erp-step'; el.id = 'erp-full';
  el.innerHTML = `<h3>Полная сверка и сохранение исходных данных</h3>
    <p class="small">Выберите нужные выгрузки. Все колонки останутся в CRM вместе с файлом и номером строки. Однозначные посещения, оплаты и история задач будут перенесены, расхождения попадут в список. Этапы клиентов и отметки тренеров не перезаписываются</p>
    <details>
      <summary>Вставить CSV посещений</summary>
      <label>Данные CSV<textarea data-visit-csv rows="5"></textarea></label>
      <button type="button" class="btn" data-visit-upload>Добавить CSV посещений</button>
      <div class="small" data-visit-upload-status role="status"></div>
    </details>
    <div style="max-height:230px;overflow:auto">${data.uploads.map(u=>`<label class="check"><input type="checkbox" data-full-file="${u.id}">${esc(u.filename)} · ${esc(u.kind_label || ({visits:'Посещения',tasks:'Задачи',clients:'Клиенты',subs:'Абонементы',payments:'Оплаты'}[u.kind] || u.kind || ''))} (${u.count} строк)</label>`).join('')}</div>
    <button class="btn" id="erp-full-plan">Проверить выбранные файлы</button>
    <button class="btn primary" id="erp-full-apply" disabled>Сохранить и перенести проверенное</button>
    <button class="btn" id="erp-full-last">Последний результат</button>
    <a class="btn" href="/api/erp/full/report?download=1" download>Скачать полный отчёт сверки</a><div id="erp-full-out"></div>`;
  const quick=box.querySelector('#erp-task-transfer');
  if(quick)quick.after(el);else box.prepend(el);
  const review=document.createElement('details');
  review.innerHTML='<summary>Сверка существующих задач ERP по ID (9-15 ноября 2026)</summary><textarea id="erp-task-review-input" placeholder="Проверенный JSON задач" rows="6"></textarea><button class="btn" id="erp-task-review-plan">Рассчитать исправления задач</button><button class="btn primary" id="erp-task-review-apply" disabled>Применить проверенные исправления задач</button><pre id="erp-task-review-out" style="max-height:500px;overflow:auto;white-space:pre-wrap"></pre>';
  el.append(review);
  let taskPlan=null;
  const ti=review.querySelector('textarea'),tp=review.querySelector('#erp-task-review-plan'),ta=review.querySelector('#erp-task-review-apply'),to=review.querySelector('pre');
  ti.oninput=()=>{taskPlan=null;ta.disabled=true;};
  tp.onclick=async()=>{ta.disabled=true;taskPlan=null;try{const tasks=JSON.parse(ti.value);const r=await api('POST','/api/erp/tasks/review-plan',{tasks});taskPlan={tasks,token:r.token};to.textContent=JSON.stringify(r,null,2);ta.disabled=false;}catch(e){to.textContent=e.message;}};
  ta.onclick=async()=>{if(!taskPlan)return;ta.disabled=true;try{const r=await api('POST','/api/erp/tasks/review-apply',taskPlan);to.textContent=JSON.stringify(r,null,2);taskPlan=null;}catch(e){to.textContent=e.message;}};

  const csv = el.querySelector('[data-visit-csv]');
  const upload = el.querySelector('[data-visit-upload]');
  const uploadStatus = el.querySelector('[data-visit-upload-status]');
  upload.onclick = async () => {
    const content = csv.value;
    if (!content.trim()) { uploadStatus.textContent = 'Вставьте CSV с заголовками'; return; }
    upload.disabled = true;
    uploadStatus.textContent = 'Добавляю CSV посещений…';
    try {
      const data = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result.split(',')[1]);
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(new Blob([content], {type: 'text/csv;charset=utf-8'}));
      });
      const added = await api('POST', '/api/erp/upload', {filename: 'erp-visits-' + Date.now() + '.csv', data, kind: 'visits'});
      await renderErp(box);
      const status=box.querySelector('[data-visit-upload-status]');
      if(status){status.textContent='CSV добавлен как «Посещения». Выберите файл ниже и нажмите «Проверить выбранные файлы».';status.closest('details').open=true;}
      const item=box.querySelector(`[data-full-file="${added.id}"]`);
      if(item)item.closest('label').scrollIntoView({block:'nearest'});
    } catch (e) {
      const input=box.querySelector('[data-visit-csv]')||csv;input.value=content;
      const status=box.querySelector('[data-visit-upload-status]')||uploadStatus;
      status.textContent=e.message||'Не удалось добавить CSV';status.closest('details').open=true;
    } finally {
      upload.disabled = false;
    }
  };

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


function erpTaskTransferSetup(box,data) {
  const root=document.createElement('section');root.className='erp-step';root.id='erp-task-transfer';
  root.innerHTML=`<h3>Перенести клиентские задачи ERP</h3><p>Загрузите актуальный реестр задач из раздела «Задачи» ERP. Можно несколько файлов и любые даты: просроченные, текущие, будущие и архивные. История событий не заменяет реестр задач.</p><p class="small muted">Нужны отдельные ID задачи и ID посетителя/заявки, название, срок и явное состояние выполнения. Названия и комментарии сохраняются. Оплаты, абонементы и журнал этот перенос не меняет.</p><label class="btn">Загрузить CSV или XLSX<input type="file" data-task-files accept=".csv,.xlsx" multiple hidden></label><div data-task-files-list style="max-height:220px;overflow:auto;margin:12px 0"></div><div class="sc-actions"><button class="btn" data-task-check>Проверить перенос</button><button class="btn primary" data-task-apply disabled>Перенести проверенные задачи</button><button class="btn" data-task-last>Последний результат</button><a class="btn" href="/api/erp/tasks/transfer-report?download=1" download>Скачать отчёт</a></div><div data-task-result style="margin-top:12px"></div>`;
  box.prepend(root);
  const files=(data.uploads||[]).filter(f=>f.kind==='tasks').slice(), selected=new Set();let checked=null;
  const output=root.querySelector('[data-task-result]'),apply=root.querySelector('[data-task-apply]'),check=root.querySelector('[data-task-check]');
  const invalidate=()=>{checked=null;apply.disabled=true};
  const drawFiles=()=>{
    root.querySelector('[data-task-files-list]').innerHTML=files.length?files.map(f=>`<label class="check"><input type="checkbox" data-task-file="${f.id}" ${selected.has(f.id)?'checked':''}>${esc(f.filename)} · ${f.count??f.rows??0} строк</label>`).join(''):'<p class="small muted">Загрузите выгрузку задач ERP.</p>';
    root.querySelectorAll('[data-task-file]').forEach(c=>c.onchange=()=>{c.checked?selected.add(+c.dataset.taskFile):selected.delete(+c.dataset.taskFile);invalidate()});
  };
  root.querySelector('[data-task-files]').onchange=async e=>{
    invalidate();check.disabled=true;
    try{for(const f of e.target.files){const encoded=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=reject;reader.readAsDataURL(f)});const r=await api('POST','/api/erp/upload',{filename:f.name,data:encoded,kind:'tasks'});files.push({...r,filename:f.name,kind:'tasks'});selected.add(r.id)}drawFiles();output.textContent='Файлы загружены. Нажмите «Проверить перенос».'}
    catch(err){output.textContent=err.message}finally{check.disabled=false;e.target.value=''}
  };
  const summary=r=>`<div class="finance-numbers"><div><small>Строк ERP</small><b>${r.source_rows||0}</b></div><div><small>Новые задачи</small><b>${r.counts?.add||0}</b></div><div><small>Обновления по ID</small><b>${r.counts?.update||0}</b></div><div><small>Уже совпадают</small><b>${r.counts?.same||0}</b></div></div><p>Повторных строк файлов: ${r.duplicates_in_files||0}. Исключений: ${r.issues?.length||0}. ${r.verified_tasks!==undefined?`Проверено после переноса: ${r.verified_tasks}.`:''}</p><details><summary>Сверка по датам и состояниям</summary><pre>${esc(JSON.stringify({dates:r.by_date,states:r.by_state},null,2))}</pre></details>${r.issues?.length?`<details open><summary>Не будут перенесены: ${r.issues.length} строк</summary><p class="small">Это частичный перенос. Исключения нужно разобрать отдельно.</p>${r.issues.slice(0,100).map(x=>`<p class="small">${esc(x.file)}:${x.row} · ${esc(x.erp_task_id||'')} · ${esc(x.reason)}</p>`).join('')}</details>`:''}${r.not_in_selected_export?.length?`<details><summary>Ранее перенесены, но отсутствуют в выбранных файлах: ${r.not_in_selected_export.length}</summary><p>Они сохранены. Отсутствие в выбранной выгрузке не доказывает закрытие: сначала проверьте фильтры и полный список ERP.</p></details>`:''}<details><summary>Изменения задач</summary><div style="max-height:400px;overflow:auto"><table class="tbl"><thead><tr><th>ID ERP</th><th>Действие</th><th>Задача</th><th>Дата и состояние</th></tr></thead><tbody>${(r.actions||[]).slice(0,300).map(a=>`<tr><td>${esc(a.erp_task_id)}</td><td>${esc({add:'Добавить',update:'Обновить',same:'Совпадает'}[a.action])}</td><td>${esc(a.after.title)}</td><td>${esc(a.after.due_at.slice(0,10))} · ${esc(a.after.status)}</td></tr>`).join('')}</tbody></table></div></details>`;
  check.onclick=async()=>{invalidate();check.disabled=true;output.textContent='Проверяю задачи без изменения данных…';try{const ids=[...selected];const r=await api('POST','/api/erp/tasks/transfer-plan',{ids});checked={ids,token:r.token};output.innerHTML=summary(r);apply.disabled=!r.unique_tasks;apply.textContent=`Перенести проверенные задачи (${r.unique_tasks||0})`}catch(e){output.textContent=e.message}finally{check.disabled=false}};
  apply.onclick=async()=>{if(!checked)return;apply.disabled=true;check.disabled=true;root.querySelector('[data-task-files]').disabled=true;try{const r=await api('POST','/api/erp/tasks/transfer-apply',checked);output.innerHTML=`<p><b>${r.issues?.length?'Проверенные задачи перенесены; остались исключения':'Перенос выбранного реестра проверен'}</b></p>`+summary(r);checked=null}catch(e){output.textContent=e.message}finally{check.disabled=false;root.querySelector('[data-task-files]').disabled=false}};
  root.querySelector('[data-task-last]').onclick=async()=>{try{output.innerHTML=summary(await api('GET','/api/erp/tasks/transfer-report'))}catch(e){output.textContent=e.message}};
  drawFiles();
}
