"use strict";
// name остаётся совместимым с ERP/историей: «Имя Фамилия». В интерфейсе — фамилия первой.
function childNameParts(value) {
  const raw=String(typeof value==='object'?value?.name||'':value||'').trim().replace(/\s+/g,' ');
  const parts=raw.split(' ');
  if(parts.length<2||/\d|не указан|реб[её]нок\s*\(/i.test(raw))return {first:raw,last:''};
  return {first:parts[0],last:parts.slice(1).join(' ')};
}
function displayChildName(value) {const p=childNameParts(value);return [p.last,p.first].filter(Boolean).join(' ')}
function splitChildNameFields(form,name='child_name',requireSurname=false) {
  const original=form.elements[name];if(!original||original.dataset.splitName)return;
  original.dataset.splitName='1';const parts=childNameParts(original.value),required=original.required;
  original.type='hidden';original.required=false;
  const field=original.closest('.field');if(!field)return;
  field.querySelector('label')?.remove();field.classList.add('split-client-name');
  const make=(key,label,value,need)=>{const wrap=document.createElement('label');wrap.textContent=label;const input=document.createElement('input');input.name=name+'_'+key;input.type='text';input.autocomplete=key==='last'?'family-name':'given-name';input.value=value;input.required=need;wrap.append(input);field.append(wrap);return input};
  const last=make('last','Фамилия ребёнка',parts.last,requireSurname),first=make('first','Имя ребёнка',parts.first,required);
  const sync=()=>original.value=[first.value.trim(),last.value.trim()].filter(Boolean).join(' ');
  first.addEventListener('input',sync);last.addEventListener('input',sync);first.addEventListener('change',sync);last.addEventListener('change',sync);
  form.addEventListener('submit',sync,true);sync();
}
function formatContactPhone(value) {
  const raw=String(value||''),digits=raw.replace(/\D/g,'');if(!digits)return '';
  if(raw.trim().startsWith('+'))return '+'+digits;
  if(digits.startsWith('8'))return '+7'+digits.slice(1);
  if(digits.startsWith('9'))return '+7'+digits;
  if(digits.startsWith('7'))return '+'+digits;
  return raw.startsWith('+')?'+'+digits:digits;
}
function formatPhoneField(input) {
  if(!(input instanceof HTMLInputElement)||!input.matches('input[type=tel],input[name=phone],input[data-contact=phone]'))return;
  const old=input.value,next=formatContactPhone(old);if(next===old)return;
  const caret=input.selectionStart,atEnd=caret===old.length;input.value=next;
  if(caret!==null){const offset=atEnd?next.length:Math.max(0,caret+next.length-old.length);try{input.setSelectionRange(offset,offset)}catch(_){}}
}
document.addEventListener('input',event=>formatPhoneField(event.target),true);
document.addEventListener('change',event=>formatPhoneField(event.target),true);
document.addEventListener('focusin',event=>formatPhoneField(event.target),true);
