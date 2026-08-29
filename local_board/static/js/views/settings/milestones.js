import {store, canWrite} from '../../store.js';
import {$$, esc} from '../../dom.js';
import {statusForName} from '../issue-detail.js';

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

export function dismissMilestoneEditors(except=null){
  $$('#settingsMilestones details.milestone-editor[open]').forEach(editor=>{if(editor!==except)editor.open=false});
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
