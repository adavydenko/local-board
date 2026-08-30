import {store, canWrite} from '../../store.js';
import {$$, esc, labelColorStyle} from '../../dom.js';
import {DEFAULT_LABEL_COLOR} from '../../api.js';

export function labelForm(label=null,{source='settings'}={}){
  const creating=!label,action=creating?'Create label':'Save changes',formName=source==='issue'?'create-label':creating?'create-label':'edit-label';
  const actionName=source==='issue'?'cancel-issue-label':'cancel-label';
  return `<form class="${source==='issue'?'label-quick-create':'label-form'}" data-form="${formName}" data-source="${source}" ${label?`data-label="${label.id}"`:''}><input class="control" ${source==='issue'?'':creating?'id="newLabelName"':''} name="name" value="${esc(label?.name||'')}" placeholder="Label name" aria-label="Label name" maxlength="80" required><input class="control" type="color" name="color" value="${esc(label?.color||DEFAULT_LABEL_COLOR)}" aria-label="Label color"><div class="${source==='issue'?'label-quick-create-actions':'label-form-actions'}"><button class="button quiet small" type="button" data-action="${actionName}">Cancel</button><button class="button primary small" type="submit">${action}</button></div></form>`;
}

export function labelRow(label){
  const content=`<span class="label-swatch"${labelColorStyle(label.color)} aria-hidden="true"></span><span class="milestone-name">${esc(label.name)}</span>`;
  if(label.managed_by==='config')return `<div class="label-static">${content}<span class="managed-badge">TOML</span></div>`;
  if(!canWrite())return `<div class="label-static">${content}<span></span></div>`;
  return `<details class="label-settings-editor" data-label-editor="${label.id}"><summary aria-label="Edit label ${esc(label.name)}">${content}<span class="milestone-more" aria-hidden="true">···</span></summary>${labelForm(label)}</details>`;
}

export function dismissLabelEditors(except=null){
  $$('#settingsLabels details.label-settings-editor[open]').forEach(editor=>{if(editor!==except)editor.open=false});
}

export function renderLabels(){
  const labels=store.data.board?.labels||[];
  settingsLabelActions.innerHTML=canWrite()?'<button class="button small" type="button" data-action="create-label">Add label</button>':'';
  settingsLabelCreate.classList.toggle('hidden',!store.creatingLabel);
  settingsLabelCreate.innerHTML=store.creatingLabel?labelForm():'';
  settingsLabels.innerHTML=labels.length?labels.map(labelRow).join(''):'<div class="milestone-unassigned"><span class="label-swatch" aria-hidden="true"></span><span class="milestone-name">No labels yet</span><span></span></div>';
  $$('#settingsLabels details.label-settings-editor').forEach(editor=>editor.addEventListener('toggle',()=>{
    if(editor.open){dismissLabelEditors(editor);requestAnimationFrame(()=>editor.querySelector('input[name="name"]')?.focus())}
  }));
}
