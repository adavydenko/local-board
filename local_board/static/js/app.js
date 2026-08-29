import {store, canWrite, statusesSorted} from './store.js';
import {$, $$, esc, initials, humanize, tomlString, notify} from './dom.js';
import {api, DEFAULT_LABEL_COLOR} from './api.js';
import {reloadBoard} from './main.js';
import {statusIndicator} from './views/issues.js';
import {statusForName} from './views/issue-detail.js';

export function renderShell(){
  const board=store.data.board||{};
  boardName.textContent=board.name||'Repository';boardPrefix.textContent=board.prefix?`${board.prefix} board`:'Local workspace';
  boardMark.textContent=(board.prefix||'LB').slice(0,3);headerBoard.textContent=board.name||'Board';
  issueCount.textContent=(store.data.issues||[]).length;
  actorName.textContent=store.identity?.name||'Not connected';actorAvatar.textContent=initials(store.identity?.name);
  actorKind.textContent=store.identity?`${store.identity.kind} · ${store.identity.role}`:'Actor';
}

export function projectToml(){
  const board=store.data.board||{},defaults=board.defaults||{},policy=board.agent_policy||{};
  const lines=['schema_version = 2','','[project]',`prefix = ${tomlString(board.prefix||'')}`,`name = ${tomlString(board.name||'')}`,`description = ${tomlString(board.description||'')}`,'','[defaults]',`priority = ${tomlString(defaults.priority||'medium')}`,'','[agent_policy]',`require_assignee_before_start = ${policy.require_assignee_before_start!==false}`];
  statusesSorted().forEach(status=>lines.push('','[[statuses]]',`name = ${tomlString(status.name)}`,`category = ${tomlString(status.category)}`));
  (board.milestones||[]).forEach(milestone=>{
    lines.push('','[[milestones]]');
    if(milestone.key)lines.push(`key = ${tomlString(milestone.key)}`);
    lines.push(`name = ${tomlString(milestone.name)}`,`description = ${tomlString(milestone.description||'')}`);
    if(milestone.due_at)lines.push(`due_at = ${tomlString(milestone.due_at)}`);
  });
  (board.labels||[]).forEach(label=>{
    lines.push('','[[labels]]');
    if(label.key)lines.push(`key = ${tomlString(label.key)}`);
    lines.push(`name = ${tomlString(label.name)}`,`color = ${tomlString(label.color||'#64748b')}`);
  });
  return lines.join('\n');
}

export function catalogSummary(items){
  if(!items.length)return 'None configured';
  return items.map(item=>item.name).join(', ');
}

export function settingsStatusItems(statuses){
  return statuses.map(status=>`<span class="catalog-status">${statusIndicator(status)}<span>${esc(status.name)}</span></span>`).join('')||'<span class="muted">None configured</span>';
}

export function settingsStatusFlow(statuses){
  if(!statuses.length)return '<span class="muted">None configured</span>';
  return statuses.map((status,index)=>`${index?'<span class="status-flow-arrow" aria-hidden="true">→</span>':''}<span class="status-flow-node">${statusIndicator(status)}<span>${esc(status.name)}</span></span>`).join('');
}

export function settingsLabelItems(labels){
  return labels.map(label=>`<span class="label-chip"><span class="label-dot" style="--label-color:${esc(label.color||'#8d8d95')}"></span>${esc(label.name)}</span>`).join('')||'<span class="muted">None configured</span>';
}

export function milestoneProgress(milestone){
  const issues=(store.data.issues||[]).filter(issue=>issue.milestone_id===milestone.id),total=issues.length,completed=issues.filter(issue=>statusForName(issue.status).category==='completed').length;
  return {total,completed,percent:total?Math.round(completed/total*100):0,state:total&&completed===total?'completed':completed?'started':'empty'};
}

export function milestoneProgressCopy(progress){return progress.total?`${progress.percent}% · ${progress.completed} of ${progress.total}`:'No issues yet'}

export function milestoneForm(milestone=null){
  const creating=!milestone,action=creating?'Create milestone':'Save changes';
  return `<form class="milestone-form" data-form="${creating?'create-milestone':'edit-milestone'}" ${milestone?`data-milestone="${milestone.id}"`:''}><input class="control" ${creating?'id="newMilestoneName"':''} name="name" value="${esc(milestone?.name||'')}" placeholder="Milestone name" aria-label="Milestone name" maxlength="120" required><input class="control" type="date" name="due_at" value="${esc(milestone?.due_at||'')}" aria-label="Target date"><textarea class="control" name="description" placeholder="Description (optional)" aria-label="Milestone description">${esc(milestone?.description||'')}</textarea><div class="milestone-form-actions"><button class="button quiet small" type="button" data-action="cancel-milestone">Cancel</button><button class="button primary small" type="submit">${action}</button></div></form>`;
}

export function milestoneRow(milestone){
  const progress=milestoneProgress(milestone),content=`<span class="milestone-glyph ${progress.state}" aria-hidden="true"></span><span class="milestone-name">${esc(milestone.name)}</span><span class="milestone-meta">${esc(milestoneProgressCopy(progress))}${milestone.due_at?` · ${esc(milestone.due_at)}`:''}</span>`;
  if(milestone.managed_by==='config')return `<div class="milestone-static">${content}<span class="managed-badge">TOML</span></div>`;
  if(!canWrite())return `<div class="milestone-static">${content}<span></span></div>`;
  return `<details class="milestone-editor" data-milestone-editor="${milestone.id}"><summary aria-label="Edit milestone ${esc(milestone.name)}">${content}<span class="milestone-more" aria-hidden="true">···</span></summary>${milestoneForm(milestone)}</details>`;
}

export function labelForm(label=null,{source='settings'}={}){
  const creating=!label,action=creating?'Create label':'Save changes',formName=source==='issue'?'create-label':creating?'create-label':'edit-label';
  const actionName=source==='issue'?'cancel-issue-label':'cancel-label';
  return `<form class="${source==='issue'?'label-quick-create':'label-form'}" data-form="${formName}" data-source="${source}" ${label?`data-label="${label.id}"`:''}><input class="control" ${source==='issue'?'':creating?'id="newLabelName"':''} name="name" value="${esc(label?.name||'')}" placeholder="Label name" aria-label="Label name" maxlength="80" required><input class="control" type="color" name="color" value="${esc(label?.color||DEFAULT_LABEL_COLOR)}" aria-label="Label color"><div class="${source==='issue'?'label-quick-create-actions':'label-form-actions'}"><button class="button quiet small" type="button" data-action="${actionName}">Cancel</button><button class="button primary small" type="submit">${action}</button></div></form>`;
}

export function labelRow(label){
  const content=`<span class="label-swatch" style="--label-color:${esc(label.color||'#8d8d95')}" aria-hidden="true"></span><span class="milestone-name">${esc(label.name)}</span>`;
  if(label.managed_by==='config')return `<div class="label-static">${content}<span class="managed-badge">TOML</span></div>`;
  if(!canWrite())return `<div class="label-static">${content}<span></span></div>`;
  return `<details class="label-settings-editor" data-label-editor="${label.id}"><summary aria-label="Edit label ${esc(label.name)}">${content}<span class="milestone-more" aria-hidden="true">···</span></summary>${labelForm(label)}</details>`;
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

export function renderMilestones(){
  const milestones=store.data.board?.milestones||[],unassigned=(store.data.issues||[]).filter(issue=>!issue.milestone_id);
  settingsMilestoneActions.innerHTML=canWrite()?'<button class="button small" type="button" data-action="create-milestone">Add milestone</button>':'';
  settingsMilestoneCreate.classList.toggle('hidden',!store.creatingMilestone);
  settingsMilestoneCreate.innerHTML=store.creatingMilestone?milestoneForm():'';
  settingsMilestones.innerHTML=`${milestones.map(milestoneRow).join('')}<div class="milestone-unassigned"><span class="milestone-glyph empty" aria-hidden="true"></span><span class="milestone-name">No milestone</span><span class="milestone-meta">${unassigned.length} ${unassigned.length===1?'issue':'issues'}</span></div>`;
  $$('#settingsMilestones details.milestone-editor').forEach(editor=>editor.addEventListener('toggle',()=>{
    if(editor.open){dismissMilestoneEditors(editor);requestAnimationFrame(()=>editor.querySelector('input')?.focus())}
  }));
}

export function renderSettings(){
  const board=store.data.board||{},statuses=statusesSorted(),labels=board.labels||[];
  settingsProject.innerHTML=`
    <div class="settings-row"><span>Prefix</span><strong>${esc(board.prefix||'Not configured')}</strong></div>
    <div class="settings-row"><span>Name</span><strong>${esc(board.name||'Repository')}</strong></div>
    <div class="settings-row"><span>Description</span><strong>${esc(board.description||'No description')}</strong></div>
    <div class="settings-row"><span>Default priority</span><strong>${esc(humanize(board.defaults?.priority||'medium'))}</strong></div>
    <div class="settings-row"><span>Start policy</span><strong>${board.agent_policy?.require_assignee_before_start!==false?'Assignee required':'Unassigned starts allowed'}</strong></div>`;
  settingsCatalog.innerHTML=`
    <div class="catalog-row"><span class="catalog-icon"><svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><circle cx="8" cy="8" r="5" stroke="currentColor"/><path d="M5.5 8h5" stroke="currentColor" stroke-linecap="round"/></svg></span><span class="catalog-copy"><strong>Statuses · ${statuses.length}</strong><span class="status-flow">${settingsStatusFlow(statuses)}</span></span><span class="managed-badge">TOML</span></div>
    <div class="catalog-row"><span class="catalog-icon"><svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m8.2 2.5 5.3 5.3-5.7 5.7-5.3-5.3V2.5h5.7Z" stroke="currentColor" stroke-linejoin="round"/><circle cx="5.3" cy="5.3" r=".8" fill="currentColor"/></svg></span><span class="catalog-copy"><strong>Labels · ${labels.length}</strong><span class="catalog-items">${settingsLabelItems(labels)}</span></span><span class="managed-badge">TOML</span></div>`;
  configPreview.textContent=projectToml();
  renderMilestones();
  renderLabels();
  setSettingsTab(store.activeSettingsTab);
}

export function setSettingsTab(tab){
  store.activeSettingsTab=['milestones','labels'].includes(tab)?tab:'overview';
  const overview=store.activeSettingsTab==='overview';
  settingsOverviewPanel.classList.toggle('hidden',!overview);settingsMilestonesPanel.classList.toggle('hidden',store.activeSettingsTab!=='milestones');settingsLabelsPanel.classList.toggle('hidden',store.activeSettingsTab!=='labels');settingsManagedNote.classList.toggle('hidden',overview);settingsView.querySelector('.settings-intro').classList.toggle('milestones-active',!overview);
  $$('[data-settings-tab]').forEach(button=>{
    const active=button.dataset.settingsTab===store.activeSettingsTab;
    button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;
  });
}

export function handleSettingsTabs(event){
  const tab=event.target.closest('[data-settings-tab]');
  if(!tab)return false;
  setSettingsTab(tab.dataset.settingsTab);return true;
}

export function handleSettingsTabKeydown(event){
  const tab=event.target.closest('[data-settings-tab]');
  if(!tab||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
  const tabs=$$('[data-settings-tab]');let index=tabs.indexOf(tab);
  if(event.key==='Home')index=0;else if(event.key==='End')index=tabs.length-1;else index=(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
  event.preventDefault();tabs[index].focus();setSettingsTab(tabs[index].dataset.settingsTab);
}

export function dismissMilestoneEditors(except=null){
  $$('#settingsMilestones details.milestone-editor[open]').forEach(editor=>{if(editor!==except)editor.open=false});
}

export function dismissLabelEditors(except=null){
  $$('#settingsLabels details.label-settings-editor[open]').forEach(editor=>{if(editor!==except)editor.open=false});
}

export async function handleSettingsAction(event){
  const target=event.target.closest('[data-action]');
  if(!target){
    const milestoneEditor=event.target.closest('details.milestone-editor'),labelEditor=event.target.closest('details.label-settings-editor');
    dismissMilestoneEditors(milestoneEditor);dismissLabelEditors(labelEditor);
    return;
  }
  try{
    if(target.dataset.action==='create-milestone'){
      if(!canWrite())return;
      store.creatingMilestone=true;dismissMilestoneEditors();renderSettings();
      requestAnimationFrame(()=>$('#newMilestoneName')?.focus());
    }else if(target.dataset.action==='cancel-milestone'){
      store.creatingMilestone=false;
      const editor=target.closest('details.milestone-editor'),milestoneId=editor?.dataset.milestoneEditor;if(editor)editor.open=false;
      renderSettings();
      requestAnimationFrame(()=>milestoneId?$(`details[data-milestone-editor="${milestoneId}"] summary`)?.focus():$('[data-action="create-milestone"]')?.focus());
    }else if(target.dataset.action==='create-label'){
      if(!canWrite())return;
      store.creatingLabel=true;dismissLabelEditors();renderSettings();
      requestAnimationFrame(()=>$('#newLabelName')?.focus());
    }else if(target.dataset.action==='cancel-label'){
      store.creatingLabel=false;
      const editor=target.closest('details.label-settings-editor'),labelId=editor?.dataset.labelEditor;if(editor)editor.open=false;
      renderSettings();
      requestAnimationFrame(()=>labelId?$(`details[data-label-editor="${labelId}"] summary`)?.focus():$('[data-action="create-label"]')?.focus());
    }
  }catch(error){notify(error.message)}
}

export async function handleSettingsSubmit(event){
  const form=event.target.closest('[data-form="create-milestone"],[data-form="edit-milestone"],[data-form="create-label"],[data-form="edit-label"]');
  if(!form)return;
  event.preventDefault();
  if(!canWrite())return;
  const name=form.elements.name.value.trim();if(!name)return;
  try{
    if(form.dataset.form.includes('milestone')){
      const body={name,description:form.elements.description.value,due_at:form.elements.due_at.value||null};
      const milestone=form.dataset.form==='create-milestone'
        ?await api('/api/milestones',{method:'POST',body:JSON.stringify(body)})
        :await api(`/api/milestones/${form.dataset.milestone}`,{method:'PATCH',body:JSON.stringify(body)});
      store.creatingMilestone=false;await reloadBoard();notify(form.dataset.form==='create-milestone'?'Milestone added':'Milestone updated');
      requestAnimationFrame(()=>$(`details[data-milestone-editor="${milestone.id}"] summary`)?.focus());
    }else{
      const body={name,color:form.elements.color.value};
      const label=form.dataset.form==='create-label'
        ?await api('/api/labels',{method:'POST',body:JSON.stringify(body)})
        :await api(`/api/labels/${form.dataset.label}`,{method:'PATCH',body:JSON.stringify(body)});
      store.creatingLabel=false;await reloadBoard();notify(form.dataset.form==='create-label'?'Label added':'Label updated');
      requestAnimationFrame(()=>$(`details[data-label-editor="${label.id}"] summary`)?.focus());
    }
  }catch(error){notify(error.message)}
}

export function handleMilestoneEditorDismissal(event){
  if(event.key!=='Escape')return;
  const milestoneEditor=$$('#settingsMilestones details.milestone-editor[open]').find(item=>item.getClientRects().length),labelEditor=$$('#settingsLabels details.label-settings-editor[open]').find(item=>item.getClientRects().length);
  if(!milestoneEditor&&!labelEditor&&!store.creatingMilestone&&!store.creatingLabel)return;
  event.preventDefault();const milestoneId=milestoneEditor?.dataset.milestoneEditor,labelId=labelEditor?.dataset.labelEditor;store.creatingMilestone=false;store.creatingLabel=false;
  if(milestoneEditor)milestoneEditor.open=false;if(labelEditor)labelEditor.open=false;
  renderSettings();
  requestAnimationFrame(()=>milestoneId?$(`details[data-milestone-editor="${milestoneId}"] summary`)?.focus():labelId?$(`details[data-label-editor="${labelId}"] summary`)?.focus():store.activeSettingsTab==='labels'?$('[data-action="create-label"]')?.focus():$('[data-action="create-milestone"]')?.focus());
}
