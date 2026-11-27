"use strict";
function openClientProfile(d,root,reload,addParent=false,focus='name') {
  if (!root) return;
  const current = root.querySelector('form[data-client-profile]');
  if (!root.hidden && current) {
    if (addParent) current.querySelector('[data-profile-add]').click();
    else current.elements[focus === 'name' ? 'name_last' : focus]?.focus();
    root.scrollIntoView({block:'nearest',behavior:'smooth'});
    return;
  }
  root.hidden=false;
  const s=d.student,rows=(d.parents||[]).map(p=>({...p}));
  if(!rows.some(p=>p.is_primary)&&rows.length)rows[0].is_primary=true;
  if(addParent)rows.push({name:'',phone:'',email:'',relation:'',messenger:'',is_primary:rows.length===0});
  root.innerHTML=`<form class="client-profile-form" data-client-profile novalidate><h3>Данные клиента</h3><div class="inline-trial-grid"><div class="field"><label>Имя и фамилия ребёнка</label><input name="name" required value="${esc(s.name)}"></div><div class="field"><label for="profile-birth">Дата рождения</label><div class="need-date-row"><input id="profile-birth" name="birth" type="text" inputmode="numeric" autocomplete="bday" placeholder="ДД.ММ.ГГГГ" value="${esc(birthDisplay((s.birth||'').slice(0,10)))}"><input class="need-date-picker" type="date" data-profile-birth-picker aria-label="Выбрать дату рождения в календаре" max="${esc(S.meta.today)}" value="${esc((s.birth||'').slice(0,10))}"></div></div><div class="field"><label>Статус клиента</label><input value="${esc((s.erp_status||'').trim()||'Статус не указан')}" readonly><input name="stage" type="hidden" value="${esc(s.stage)}"></div><div class="field" data-profile-stage-reason hidden><label>Причина изменения статуса</label><div data-stage-reason-options><input name="stage_reason"></div><label>Комментарий<input name="stage_comment"></label></div><div class="field" data-profile-stage-due hidden><label>Когда вернуться к клиенту</label><input name="stage_due" type="date"></div></div><div class="field"><label>Заметка для команды</label><textarea name="note" rows="2" maxlength="500">${esc(d.family.note||'')}</textarea></div><h4>Родители и основной контакт</h4><div data-profile-parents></div><button type="button" class="btn sm" data-profile-add>Добавить родителя</button><p class="small muted">Основной контакт первым предлагается для звонка и выбора диалога. Все переписки семьи сохраняются</p><div class="need-err" role="alert"></div><div class="sc-actions"><button type="submit" class="btn primary">Сохранить</button><button type="button" class="btn" data-profile-close>Отмена</button></div></form>`;
  splitChildNameFields(root.querySelector('form'),'name');
  const form=root.querySelector('form'),list=root.querySelector('[data-profile-parents]');let busy=false;
  const contact=(row,index)=>{const block=document.createElement('fieldset');block.dataset.profileParent=index;block.dataset.parentId=row.id||'';block.className='profile-parent';block.innerHTML=`<legend>Родитель ${index+1}</legend><div class="inline-trial-grid">${[['name','Имя'],['relation','Кем приходится'],['phone','Телефон'],['email','Почта'],['messenger','Мессенджер / username']].map(([key,title])=>`<div class="field"><label>${title}</label><input data-contact="${key}" type="${key==='phone'?'tel':key==='email'?'email':'text'}" value="${esc(row[key]||'')}" ${key==='name'?'required':''}></div>`).join('')}</div><label class="check"><input type="radio" name="primary_parent" value="${index}" ${row.is_primary?'checked':''} required> Основной для общения</label>`;list.append(block)};
  rows.forEach(contact);
  const readParents = () => [...list.children].map(block => ({
    id: +block.dataset.parentId || null,
    ...Object.fromEntries([...block.querySelectorAll('[data-contact]')].map(input => [input.dataset.contact,input.value])),
    is_primary: block.querySelector('[name=primary_parent]').checked,
  }));
  const initialParents = JSON.stringify(readParents().filter(parent => parent.id));
  const birth = form.elements.birth;
  const picker = form.querySelector('[data-profile-birth-picker]');
  picker.onchange = () => { birth.value = birthDisplay(picker.value); birth.focus(); };
  birth.onblur = () => {
    const iso = birthIso(birth.value);
    picker.value = iso || '';
    if (iso) birth.value = birthDisplay(iso);
  };
  root.querySelector('[data-profile-add]').onclick=()=>{const index=list.children.length;contact({},index);list.lastElementChild.querySelector('input').focus()};
  form.elements.stage.onchange=()=>{const stage=form.elements.stage.value,changed=stage!==s.stage,reasons=d.profile_stage_reasons?.[stage]||[];root.querySelector('[data-profile-stage-reason]').hidden=!changed;root.querySelector('[data-stage-reason-options]').innerHTML=reasons.length?reasons.map(reason=>`<label class="check"><input type="radio" name="stage_reason" value="${esc(reason)}" ${changed?'required':''}> ${esc(reason)}</label>`).join(''):'<input name="stage_reason">';root.querySelector('[data-profile-stage-due]').hidden=!['left','reinvite_pu'].includes(stage)};
  root.querySelector('[data-profile-close]').onclick=()=>{if(!busy){root.hidden=true;root.replaceChildren()}};
  form.onsubmit=async e=>{
    e.preventDefault();
    if (busy) return;
    const parents = readParents();
    const contactsChanged = JSON.stringify(parents) !== initialParents;
    if (!form.elements.name_first.reportValidity() || (contactsChanged && !form.reportValidity())) return;
    const iso = birthIso(birth.value);
    const err = form.querySelector('.need-err');
    if (birth.value.trim() && !iso) {
      err.textContent = 'Введите дату рождения в формате ДД.ММ.ГГГГ';
      birth.focus();
      return;
    }
    err.textContent = '';
    busy=true;const save=form.querySelector('[type=submit]');save.disabled=true;
    const payload = {name: form.elements.name.value, birth: iso || '', note: form.elements.note.value, expected: d.profile_token};
    if (contactsChanged || form.elements.stage.value !== s.stage) {
      Object.assign(payload, Object.fromEntries(['stage','stage_reason','stage_comment','stage_due'].map(key=>[key,form.elements[key].value])), {parents});
    } else {
      payload.scope = 'details';
    }
    try{await api('PATCH',`/api/students/${s.id}/profile`,payload);toast('Данные сохранены');reload()}
    catch(err){form.querySelector('.need-err').textContent=err.message;busy=false;save.disabled=false}
  };
  const field=addParent?list.lastElementChild.querySelector('input'):form.elements[focus==='name'?'name_last':focus];field?.focus();root.scrollIntoView({block:'nearest',behavior:'smooth'});
}
