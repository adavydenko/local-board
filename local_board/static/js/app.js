import {store, canWrite, statusesSorted, findIssueRef, milestoneName, labelCatalog, activityEntityName} from './store.js';
import {$, $$, esc, initials, humanize, tomlString, relativeTime, markdown, notify, safeExternalUrl} from './dom.js';
import {api, DEFAULT_LABEL_COLOR} from './api.js';
import {render, reloadBoard, openDetail, setView} from './main.js';

export function renderShell(){
  const board=store.data.board||{};
  boardName.textContent=board.name||'Repository';boardPrefix.textContent=board.prefix?`${board.prefix} board`:'Local workspace';
  boardMark.textContent=(board.prefix||'LB').slice(0,3);headerBoard.textContent=board.name||'Board';
  issueCount.textContent=(store.data.issues||[]).length;
  actorName.textContent=store.identity?.name||'Not connected';actorAvatar.textContent=initials(store.identity?.name);
  actorKind.textContent=store.identity?`${store.identity.kind} · ${store.identity.role}`:'Actor';
}

export function renderFilters(){
  const previousMilestone=milestoneFilter.value,previousAssignee=assigneeFilter.value;
  milestoneFilter.innerHTML='<option value="">All milestones</option>'+(store.data.board?.milestones||[]).map(item=>`<option value="${item.id}" ${String(item.id)===previousMilestone?'selected':''}>${esc(item.name)}</option>`).join('');
  assigneeFilter.innerHTML='<option value="">All assignees</option>'+(store.data.actors||[]).map(actor=>`<option value="${actor.id}" ${String(actor.id)===previousAssignee?'selected':''}>${esc(actor.name)}</option>`).join('');
}

export function filteredIssues(){
  const query=(searchInput.value||'').trim().toLowerCase(),milestone=milestoneFilter.value,assignee=assigneeFilter.value;
  return (store.data.issues||[]).filter(issue=>(!query||issue.title.toLowerCase().includes(query)||issue.identifier.toLowerCase().includes(query)||(issue.labels||[]).some(label=>String(label).toLowerCase().includes(query)))&&(!milestone||String(issue.milestone_id)===milestone)&&(!assignee||String(issue.assignee_id)===assignee));
}

export function hasActiveIssueFilters(){return Boolean(searchInput.value.trim()||milestoneFilter.value||assigneeFilter.value)}

export function clearIssueFilters(){searchInput.value='';milestoneFilter.value='';assigneeFilter.value='';renderIssues()}

export function statusIndicator(status,label=''){
  const category=status?.category||'unstarted',accessibility=label?`role="img" aria-label="${esc(label)} status" title="${esc(label)}"`:'aria-hidden="true"';
  return `<span class="status-indicator ${esc(category)}" ${accessibility}></span>`;
}

export function priorityMark(priority){return `<span class="priority-mark ${esc(priority)}" title="${esc(humanize(priority))} priority" aria-label="${esc(humanize(priority))} priority"><i></i><i></i><i></i></span>`}

export function listLabelChips(issue){
  return (issue.labels||[]).slice(0,2).map(name=>{const label=labelCatalog(name);return `<span class="label-chip" title="${esc(label?.name||name)}"><span class="label-dot" style="--label-color:${esc(label?.color||'#8d8d95')}"></span>${esc(label?.name||name)}</span>`}).join('');
}

export function issueRow(issue,status){
  const assignee=issue.assignee?`<span>${esc(issue.assignee)}</span><span class="mini-avatar">${esc(initials(issue.assignee))}</span>`:'<span>Unassigned</span>';
  const signal=issue.blocked?'<span class="row-signal blocked" title="Blocked" aria-label="Blocked">!</span>':issue.claim_expires_at?'<span class="row-signal" title="Claimed" aria-label="Claimed">●</span>':'<span></span>';
  return `<button class="issue-row" type="button" data-ref="${esc(issue.identifier)}">${priorityMark(issue.priority)}<span class="issue-id">${esc(issue.identifier)}</span>${statusIndicator(status,issue.status)}<span class="issue-title">${esc(issue.title)}</span><span class="row-labels">${listLabelChips(issue)}</span><span class="assignee-cell">${assignee}</span>${signal}</button>`;
}

export function boardCard(issue,status){
  const milestone=issue.milestone_id?`<span class="card-milestone"><span class="milestone-marker" aria-hidden="true">◇</span>${esc(milestoneName(issue.milestone_id))}</span>`:'';
  const blocked=issue.blocked?'<span class="card-blocked">Blocked</span>':'';
  return `<button class="board-card" type="button" data-ref="${esc(issue.identifier)}"><div class="board-card-head"><span class="issue-id">${esc(issue.identifier)}</span>${statusIndicator(status,issue.status)}</div><div class="board-card-title">${esc(issue.title)}</div>${listLabelChips(issue)}<div class="board-card-meta">${priorityMark(issue.priority)}${milestone}<span class="card-meta-spacer"></span>${blocked}${issue.assignee?`<span class="mini-avatar" title="${esc(issue.assignee)}">${esc(initials(issue.assignee))}</span>`:''}</div></button>`;
}

export function renderIssues(){
  const issues=filteredIssues(),statuses=statusesSorted();
  const visibleStatuses=hasActiveIssueFilters()?statuses.filter(status=>issues.some(issue=>issue.status===status.name)):statuses;
  const filters=[];if(searchInput.value.trim())filters.push(`matching “${searchInput.value.trim()}”`);if(milestoneFilter.value)filters.push(milestoneName(+milestoneFilter.value));if(assigneeFilter.value)filters.push((store.data.actors||[]).find(actor=>String(actor.id)===assigneeFilter.value)?.name);
  filterSummary.textContent=`${issues.length} ${issues.length===1?'issue':'issues'}${filters.length?' · '+filters.filter(Boolean).join(' · '):''}`;
  const noMatches=hasActiveIssueFilters()&&!issues.length;
  issueList.innerHTML=!statuses.length?'<div class="zero-state"><div><strong>No statuses configured</strong>Add statuses in project.toml to organize issues.</div></div>':noMatches?'<div class="zero-state"><div><strong>No matching issues</strong>No issues match the active filters.<div class="zero-state-actions"><button class="button small" type="button" data-action="clear-filters">Clear filters</button></div></div></div>':visibleStatuses.map(status=>{
    const items=issues.filter(issue=>issue.status===status.name);
    const add=canWrite()?`<button class="status-add" type="button" data-new-status="${esc(status.name)}" aria-label="Create issue in ${esc(status.name)}">+</button>`:'';
    return `<section class="status-group" aria-labelledby="status-${status.position}"><div class="status-heading">${statusIndicator(status)}<span class="status-name" id="status-${status.position}">${esc(status.name)}</span><span class="status-count">${items.length}</span>${add}</div>${items.map(issue=>issueRow(issue,status)).join('')||'<div class="row-empty">No issues here yet.</div>'}</section>`;
  }).join('');
  issueBoard.innerHTML=!statuses.length?'<div class="zero-state"><div><strong>No statuses configured</strong>Add statuses in project.toml to organize issues.</div></div>':noMatches?'<div class="zero-state"><div><strong>No matching issues</strong>No issues match the active filters.<div class="zero-state-actions"><button class="button small" type="button" data-action="clear-filters">Clear filters</button></div></div></div>':visibleStatuses.map(status=>{
    const items=issues.filter(issue=>issue.status===status.name);
    const add=canWrite()?`<button class="status-add" type="button" data-new-status="${esc(status.name)}" aria-label="Create issue in ${esc(status.name)}">+</button>`:'';
    return `<section class="board-column"><div class="column-heading">${statusIndicator(status)}<span>${esc(status.name)}</span><span class="status-count">${items.length}</span>${add}</div>${items.map(issue=>boardCard(issue,status)).join('')||'<div class="row-empty">No issues here yet.</div>'}</section>`;
  }).join('');
  updateBoardScrollHint();
}

export function updateBoardScrollHint(){issueBoard.classList.toggle('can-scroll-right',issueBoard.scrollLeft+issueBoard.clientWidth<issueBoard.scrollWidth-1)}

export function renderActivity(){
  activityFeed.innerHTML=(store.data.activity||[]).map(item=>{
    const ref=item.identifier||activityEntityName(item)||`${item.entity_type} #${item.entity_id}`;
    return `<div class="activity-row"><span class="mini-avatar">${esc(initials(item.actor||'system'))}</span><div class="activity-copy"><strong>${esc(item.actor||'System')}</strong> ${esc(humanize(item.action).toLowerCase())} <span class="activity-ref">${esc(ref)}</span></div><time class="activity-time" datetime="${esc(item.created_at)}" title="${esc(new Date(item.created_at).toLocaleString('en'))}">${esc(relativeTime(item.created_at))}</time></div>`;
  }).join('')||'<div class="zero-state"><div><strong>No activity yet</strong>Changes to issues will appear here.</div></div>';
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

export function statusOptions(selected){return statusesSorted().map(status=>`<option value="${esc(status.name)}" ${status.name===selected?'selected':''}>${esc(status.name)}</option>`).join('')}

export function actorOptions(selected){return '<option value="">Unassigned</option>'+(store.data.actors||[]).map(actor=>`<option value="${actor.id}" ${selected===actor.id?'selected':''}>${esc(actor.name)}</option>`).join('')}

export function priorityOptions(selected){return ['none','low','medium','high','urgent'].map(priority=>`<option value="${priority}" ${priority===selected?'selected':''}>${humanize(priority==='none'?'no priority':priority)}</option>`).join('')}

export function milestoneOptions(selected){return '<option value="">No milestone</option>'+(store.data.board?.milestones||[]).map(item=>`<option value="${item.id}" ${selected===item.id?'selected':''}>${esc(item.name)}</option>`).join('')}

export function statusForName(name){return statusesSorted().find(status=>status.name===name)||{category:'unstarted'}}

export function propertyPicker(field,label,value,options){
  return `<details class="property-picker" data-property-picker="${esc(field)}"><summary data-action="property-trigger"><span>${esc(label)}</span><span class="property-value">${value}</span></summary><div class="property-menu"><div class="property-menu-heading">Change ${esc(label).toLowerCase()}</div>${options}</div></details>`;
}

export function propertyOption(field,value,content,selected){
  return `<button class="property-option ${selected?'selected':''}" type="button" data-action="set-property" data-field="${esc(field)}" data-value="${esc(value)}">${content}${selected?'<span class="option-check" aria-label="Selected">✓</span>':''}</button>`;
}

export function statusPropertyPicker(issue){
  const current=statusForName(issue.status);
  const options=statusesSorted().map(status=>propertyOption('status',status.name,`${statusIndicator(status)}<span>${esc(status.name)}</span>`,status.name===issue.status)).join('');
  return propertyPicker('status','Status',`${statusIndicator(current)}<span>${esc(issue.status)}</span>`,options);
}

export function priorityPropertyPicker(issue){
  const options=['none','low','medium','high','urgent'].map(priority=>propertyOption('priority',priority,`${priorityMark(priority)}<span>${esc(humanize(priority==='none'?'no priority':priority))}</span>`,priority===issue.priority)).join('');
  return propertyPicker('priority','Priority',`${priorityMark(issue.priority)}<span>${esc(humanize(issue.priority==='none'?'no priority':issue.priority))}</span>`,options);
}

export function assigneePropertyPicker(issue){
  const noAssignee=propertyOption('assignee_id','',`<span class="mini-avatar">—</span><span>Unassigned</span>`,!issue.assignee_id);
  const options=noAssignee+(store.data.actors||[]).map(actor=>propertyOption('assignee_id',actor.id,`<span class="mini-avatar">${esc(initials(actor.name))}</span><span>${esc(actor.name)}</span>`,actor.id===issue.assignee_id)).join('');
  const value=issue.assignee?`<span class="mini-avatar">${esc(initials(issue.assignee))}</span><span>${esc(issue.assignee)}</span>`:'<span>Unassigned</span>';
  return propertyPicker('assignee_id','Assignee',value,options);
}

export function milestonePropertyPicker(issue){
  const milestones=store.data.board?.milestones||[];
  const none=propertyOption('milestone_id','',`<span class="milestone-marker">◇</span><span>No milestone</span>`,!issue.milestone_id);
  const options=none+milestones.map(item=>propertyOption('milestone_id',item.id,`<span class="milestone-marker">◇</span><span>${esc(item.name)}</span>`,item.id===issue.milestone_id)).join('');
  const name=milestoneName(issue.milestone_id);
  const value=name?`<span class="milestone-marker">◇</span><span>${esc(name)}</span>`:'<span>No milestone</span>';
  return propertyPicker('milestone_id','Milestone',value,options);
}

export function detailLabels(issue){
  const active=new Set((issue.labels||[]).map(label=>label.id));
  const available=(store.data.board?.labels||[]).filter(label=>!active.has(label.id));
  const empty=available.length?'No labels yet — add one to make this issue easier to find.':'No labels attached yet.';
  const selected=(issue.labels||[]).map(label=>`<button class="assigned-label" type="button" data-action="toggle-label" data-label="${label.id}" aria-label="Remove label ${esc(label.name)}"><span class="label-dot" style="--label-color:${esc(label.color)}"></span>${esc(label.name)} <span aria-hidden="true">×</span></button>`).join('')||`<span class="empty-inline">${empty}</span>`;
  const options=available.map(label=>`<button class="label-button" type="button" data-action="toggle-label" data-label="${label.id}"><span class="label-dot" style="--label-color:${esc(label.color)}"></span>${esc(label.name)}</button>`).join('')||'<span class="empty-inline">All existing labels are attached.</span>';
  const creator=store.creatingIssueLabel?labelForm(null,{source:'issue'}):`<button class="label-button label-create-action" type="button" data-action="start-create-issue-label">+ Create label…</button>`;
  const picker=canWrite()?`<details class="label-editor" data-property-picker="labels"><summary data-action="property-trigger">Add label</summary><div class="label-options">${options}${creator}</div></details>`:'';
  return `<div class="label-picker">${selected}</div>${picker}`;
}

export function issueLink(issue){return `<button class="issue-link" type="button" data-action="open-issue" data-ref="${esc(issue.identifier)}"><span class="issue-id">${esc(issue.identifier)}</span> ${esc(issue.title)}</button>`}

export function sidebarIssueLink(issue,{remove=false}={}){
  const removeButton=remove&&canWrite()?`<button class="relation-remove" type="button" data-action="remove-dependency" data-depends="${issue.id}" aria-label="Remove blocking issue ${esc(issue.identifier)}" title="Remove blocking issue">×</button>`:'';
  return `<div class="side-relation">${statusIndicator(statusForName(issue.status),issue.status)}${issueLink(issue)}${removeButton}</div>`;
}

export function structureMarkup(issue,parent){
  const children=(issue.children||[]).map(item=>sidebarIssueLink(item)).join('');
  if(!parent&&!children)return '';
  return `<section class="sidebar-section"><h2 class="sidebar-section-heading">Structure</h2>${parent?`<div class="sidebar-subheading">Parent</div><div class="side-list">${sidebarIssueLink(parent)}</div>`:''}${children?`<div class="sidebar-subheading">Sub-issues</div><div class="side-list">${children}</div>`:''}</section>`;
}

export function blockingMarkup(issue){
  const blockedBy=(issue.blocked_by||[]).map(item=>sidebarIssueLink(item,{remove:true})).join('');
  const blocks=(issue.blocks||[]).map(item=>sidebarIssueLink(item)).join('');
  const dependencyOptions=(store.data.issues||[]).filter(item=>item.id!==issue.id&&!((issue.blocked_by||[]).some(blocker=>blocker.id===item.id))).map(item=>`<option value="${item.id}">${esc(item.identifier)} — ${esc(item.title)}</option>`).join('');
  const empty=!blockedBy&&!blocks?'<div class="empty-inline">No blockers — this issue can move forward.</div>':'';
  const add=canWrite()&&dependencyOptions?`<details class="sidebar-disclosure"><summary>Add blocking issue</summary><form class="side-form" data-form="dependency"><select class="control" name="depends_on" required><option value="">Choose an issue…</option>${dependencyOptions}</select><button class="button small" type="submit">Add</button></form></details>`:'';
  return `<section class="sidebar-section"><h2 class="sidebar-section-heading">Blocking</h2>${blockedBy?`<div class="sidebar-subheading">Blocked by</div><div class="side-list">${blockedBy}</div>`:''}${blocks?`<div class="sidebar-subheading">Blocks</div><div class="side-list">${blocks}</div>`:''}${empty}${add}</section>`;
}

export function gitLinksMarkup(issue){
  const links=(issue.git_links||[]).map(link=>{
    const safeUrl=safeExternalUrl(link.url);
    return `<div class="git-link"><span class="issue-id">${esc(humanize(link.kind))}</span><span class="issue-id">${esc(link.ref)}</span>${safeUrl?`<a href="${esc(safeUrl)}" target="_blank" rel="noopener noreferrer" aria-label="Open ${esc(humanize(link.kind))} ${esc(link.ref)}">Open</a>`:''}</div>`;
  }).join('');
  const add=canWrite()?`<details class="sidebar-disclosure"><summary>Add Git link</summary><form class="side-form git-form" data-form="gitlink"><select class="control" name="kind"><option value="commit">Commit</option><option value="pr">Pull request</option><option value="mr">Merge request</option></select><input class="control" name="ref" placeholder="Reference" required><input class="control" name="url" type="url" placeholder="URL (optional)"><button class="button small" type="submit">Add</button></form></details>`:'';
  return `<section class="sidebar-section"><h2 class="sidebar-section-heading">Git links</h2><div class="side-list">${links||'<div class="empty-inline">No linked Git work yet — add a commit or pull request when it exists.</div>'}</div>${add}</section>`;
}

export function readOnlyProperties(issue){
  const labels=(issue.labels||[]).map(label=>`<span class="label-chip"><span class="label-dot" style="--label-color:${esc(label.color)}"></span>${esc(label.name)}</span>`).join('')||'<span class="muted">None</span>';
  return `<h2 class="properties-heading">Properties</h2>
    <div class="property"><span>Status</span><strong class="property-value">${statusIndicator(statusForName(issue.status))}${esc(issue.status)}</strong></div>
    <div class="property"><span>Assignee</span><strong>${esc(issue.assignee||'Unassigned')}</strong></div>
    <div class="property"><span>Priority</span><strong class="property-value">${priorityMark(issue.priority)}${esc(humanize(issue.priority==='none'?'no priority':issue.priority))}</strong></div>
    <div class="property"><span>Milestone</span><strong>${esc(milestoneName(issue.milestone_id)||'No milestone')}</strong></div>
    <div class="property-labels"><span class="muted">Labels</span><div class="label-picker">${labels}</div></div>`;
}

export function propertyMarkup(issue){
  if(!canWrite())return readOnlyProperties(issue);
  const mine=store.identity&&issue.assignee_id===store.identity.id;
  return `<h2 class="properties-heading">Properties</h2>
    ${statusPropertyPicker(issue)}
    ${assigneePropertyPicker(issue)}
    ${priorityPropertyPicker(issue)}
    ${milestonePropertyPicker(issue)}
    <div class="property-labels"><span class="muted">Labels</span><div class="label-picker">${detailLabels(issue)}</div></div>
    <button class="button ${mine?'':'primary'} claim-action" type="button" data-action="${mine?'release':'claim'}">${mine?'Release issue':'Claim issue'}</button>`;
}

export function canEditComment(comment){return canWrite()&&(comment.author_id===store.identity?.id||store.identity?.role==='admin')}

export function issueNarrativeMarkup(issue){
  if(store.editingIssue)return `<form class="issue-editor" data-form="edit-issue"><input class="issue-editor-title" id="editIssueTitle" name="title" value="${esc(issue.title)}" aria-label="Issue title" maxlength="200" required><textarea class="issue-editor-description" id="editIssueDescription" name="description" aria-label="Issue description">${esc(issue.description)}</textarea><div class="editor-footer"><span class="editor-hint">Markdown supported · ⌘↵ to save · Esc to cancel</span><span class="editor-actions"><button class="button quiet small" type="button" data-action="cancel-edit-issue">Cancel</button><button class="button primary small" type="submit">Save changes</button></span></div></form>`;
  const editTitle=canWrite()?'<button class="inline-edit" type="button" data-action="edit-issue" data-focus="title" aria-label="Edit issue title">Edit</button>':'';
  const editDescription=canWrite()?'<button class="inline-edit" type="button" data-action="edit-issue" data-focus="description" aria-label="Edit issue description">Edit description</button>':'';
  return `<div class="issue-title-wrap"><h1 class="issue-title-heading" id="issueTitleHeading">${esc(issue.title)}</h1>${editTitle}</div><div class="description-wrap">${markdown(issue.description)}${editDescription}</div>`;
}

export function commentMarkup(comment){
  const editable=canEditComment(comment),edited=comment.updated_at!==comment.created_at;
  const edit=editable?`<button class="inline-edit comment-edit" type="button" data-action="edit-comment" data-comment="${comment.id}" aria-label="Edit your comment">Edit</button>`:'';
  const header=action=>`<div class="comment-head"><strong>${esc(comment.author)}</strong><time datetime="${esc(comment.created_at)}">${esc(relativeTime(comment.created_at))}</time>${edited?'<span class="comment-edited">Edited</span>':''}${action}</div>`;
  if(store.editingCommentId===comment.id)return `<article class="comment"><span class="mini-avatar">${esc(initials(comment.author))}</span><form class="comment-editor" data-form="edit-comment" data-comment="${comment.id}">${header('')}<textarea id="editComment${comment.id}" name="body" aria-label="Edit comment" required>${esc(comment.body)}</textarea><div class="editor-footer"><span class="editor-hint">Markdown supported · ⌘↵ to save · Esc to cancel</span><span class="editor-actions"><button class="button quiet small" type="button" data-action="cancel-edit-comment">Cancel</button><button class="button primary small" type="submit">Save</button></span></div></form></article>`;
  return `<article class="comment"><span class="mini-avatar">${esc(initials(comment.author))}</span><div>${header(edit)}<div class="comment-body">${markdown(comment.body)}</div></div></article>`;
}

export function renderDetail(){
  const issue=store.currentIssue,parent=issue.parent_id?findIssueRef(issue.parent_id):null;
  const comments=(issue.comments||[]).map(comment=>commentMarkup(comment)).join('');
  const propertyContent=propertyMarkup(issue);
  const sidebarContent=`${propertyContent}${structureMarkup(issue,parent)}${blockingMarkup(issue)}${gitLinksMarkup(issue)}`;
  const commentForm=canWrite()?`<form class="comment-composer" data-form="comment"><div class="composer-header"><span class="mini-avatar">${esc(initials(store.identity?.name))}</span><span>Leave a comment</span></div><textarea class="control comment-input" name="body" rows="4" placeholder="Write a comment…" aria-label="Comment" required></textarea><div class="composer-footer"><span class="composer-hint">Markdown supported · ⌘↵ to send</span><span class="composer-actions"><button class="button quiet small" type="button" data-action="cancel-comment">Cancel</button><button class="button primary small" type="submit">Send</button></span></div></form>`:'';
  issueToolbarId.textContent=issue.identifier;
  issueDocument.innerHTML=`<div class="issue-kicker">${statusIndicator({category:issue.category})}<span>${esc(issue.status)}</span>${issue.blocked?'<span class="label-chip" style="color:var(--danger)">Blocked</span>':''}</div>
    ${issueNarrativeMarkup(issue)}
    <details class="mobile-properties"><summary>Issue details</summary><div class="issue-sidebar">${sidebarContent}</div></details>
    <section class="detail-section"><div class="section-heading"><h2>Activity</h2><span class="section-count">${issue.comments_total||0} ${(issue.comments_total||0)===1?'comment':'comments'}</span></div><div class="comments">${comments||'<div class="empty-inline">No comments yet — add context, a decision, or a handoff.</div>'}</div>${commentForm}</section>`;
  issueSidebar.innerHTML=sidebarContent;
}

export async function patchIssue(fields){
  try{
    store.currentIssue=await api(`/api/issues/${encodeURIComponent(store.currentIssue.identifier)}`,{method:'PATCH',body:JSON.stringify({expected_revision:store.currentIssue.revision,...fields})});
    store.data=await api('/api/dashboard');render();notify('Issue updated');
  }catch(error){
    if(error.code==='conflict')notify('This issue changed elsewhere. The latest version is now loaded.');else notify(error.message);
    store.currentIssue=await api(`/api/issues/${encodeURIComponent(store.currentIssue.identifier)}`);store.data=await api('/api/dashboard');render();throw error;
  }
}

export async function mutateClaim(action,message){
  try{
    store.currentIssue=await api(`/api/issues/${encodeURIComponent(store.currentIssue.identifier)}/${action}`,{method:'POST',body:JSON.stringify({expected_revision:store.currentIssue.revision})});
    store.data=await api('/api/dashboard');render();notify(message);
  }catch(error){
    if(error.code==='conflict'){
      notify('This issue changed elsewhere. The latest version is now loaded.');
      await refreshDetail();
      return;
    }
    throw error;
  }
}

export async function claimIssue(){return mutateClaim('claim','Issue claimed')}

export async function releaseIssue(){return mutateClaim('release','Issue released')}

export async function toggleLabel(labelId){
  const active=new Set((store.currentIssue.labels||[]).map(label=>label.id));active.has(labelId)?active.delete(labelId):active.add(labelId);await patchIssue({labels:[...active]});
}

export async function refreshDetail(){
  store.currentIssue=await api(`/api/issues/${encodeURIComponent(store.currentIssue.identifier)}`);store.data=await api('/api/dashboard');render();
}

export function dismissPropertyPickers(except=null){
  $$('details.property-picker[open],details.label-editor[open]').forEach(picker=>{if(picker!==except)picker.open=false});
  if(!except)store.creatingIssueLabel=false;
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

export function focusPropertyPicker(field){
  const summary=$$(`details[data-property-picker="${field}"] > summary`).find(item=>item.getClientRects().length);
  summary?.focus();
}

export function focusOpenPropertyPicker(picker){
  const option=picker?.querySelector('.property-option.selected,.property-option,.label-button');
  option?.focus();
}

export async function handleIssueAction(event){
  const actionTarget=event.target.closest('[data-action]');
  if(!actionTarget){
    if(!event.target.closest('details.property-picker,details.label-editor'))dismissPropertyPickers();
    return;
  }
  if(!actionTarget.closest('details.property-picker,details.label-editor'))dismissPropertyPickers();
  try{
    const action=actionTarget.dataset.action;
    if(action==='back-to-issues'){
      if(history.state?.fromApp)history.back();
      else{history.replaceState({view:'issues'},'',location.pathname);setView('issues',{updateHistory:false})}
      return;
    }
    if(actionTarget.dataset.action==='open-issue')return await openDetail(actionTarget.dataset.ref);
    if(action==='cancel-comment'){
      const form=actionTarget.closest('[data-form="comment"]');form?.reset();return;
    }
    if(action==='edit-issue'){
      if(!canWrite())return;
      store.editingIssue=true;store.editingCommentId=null;renderDetail();
      requestAnimationFrame(()=>$(actionTarget.dataset.focus==='description'?'#editIssueDescription':'#editIssueTitle')?.focus());
      return;
    }
    if(action==='cancel-edit-issue'){
      store.editingIssue=false;renderDetail();return;
    }
    if(action==='edit-comment'){
      const comment=(store.currentIssue.comments||[]).find(item=>item.id===+actionTarget.dataset.comment);
      if(!comment||!canEditComment(comment))return;
      store.editingIssue=false;store.editingCommentId=comment.id;renderDetail();
      requestAnimationFrame(()=>$('#editComment'+comment.id)?.focus());
      return;
    }
    if(action==='cancel-edit-comment'){
      store.editingCommentId=null;renderDetail();return;
    }
    if(action==='property-trigger'){
      const picker=actionTarget.closest('.property-picker,.label-editor');
      setTimeout(()=>{if(picker?.open){dismissPropertyPickers(picker);focusOpenPropertyPicker(picker)}});
      return;
    }
    if(!canWrite())return;
    if(action==='start-create-issue-label'){
      store.creatingIssueLabel=true;renderDetail();
      requestAnimationFrame(()=>{const picker=$$('details[data-property-picker="labels"]').find(item=>item.getClientRects().length);if(picker){picker.open=true;picker.querySelector('input[name="name"]')?.focus()}});
      return;
    }
    if(action==='cancel-issue-label'){
      store.creatingIssueLabel=false;renderDetail();
      requestAnimationFrame(()=>focusPropertyPicker('labels'));
      return;
    }
    if(action==='set-property'){
      const field=actionTarget.dataset.field;
      let value=actionTarget.dataset.value;
      if(field==='assignee_id'||field==='milestone_id')value=value?+value:null;
      await patchIssue({[field]:value});requestAnimationFrame(()=>focusPropertyPicker(field));return;
    }
    if(actionTarget.dataset.action==='claim')return await claimIssue();
    if(actionTarget.dataset.action==='release')return await releaseIssue();
    if(actionTarget.dataset.action==='toggle-label'){
      await toggleLabel(+actionTarget.dataset.label);requestAnimationFrame(()=>focusPropertyPicker('labels'));return;
    }
    if(actionTarget.dataset.action==='remove-dependency'){
      await api(`/api/issues/${encodeURIComponent(store.currentIssue.identifier)}/dependencies`,{method:'DELETE',body:JSON.stringify({depends_on:+actionTarget.dataset.depends})});await refreshDetail();notify('Dependency removed');
    }
  }catch(error){notify(error.message)}
}

export async function handleDetailSubmit(event){
  const form=event.target.closest('[data-form]');if(!form)return;event.preventDefault();
  if(!canWrite())return;
  try{
    if(form.dataset.form==='comment'){
      const body=form.body.value.trim();if(!body)return;await api(`/api/issues/${encodeURIComponent(store.currentIssue.identifier)}/comments`,{method:'POST',body:JSON.stringify({body})});
    }else if(form.dataset.form==='create-label'&&form.dataset.source==='issue'){
      const name=form.elements.name.value.trim();if(!name)return;
      const label=await api('/api/labels',{method:'POST',body:JSON.stringify({name,color:form.elements.color.value})});
      store.creatingIssueLabel=false;await toggleLabel(label.id);notify(`Label “${label.name}” created and added`);return;
    }else if(form.dataset.form==='edit-issue'){
      const title=form.elements.title.value.trim();if(!title)return;
      store.editingIssue=false;await patchIssue({title,description:form.elements.description.value});return;
    }else if(form.dataset.form==='edit-comment'){
      const body=form.body.value.trim();if(!body)return;
      store.editingCommentId=null;await api(`/api/comments/${form.dataset.comment}`,{method:'PATCH',body:JSON.stringify({body})});await refreshDetail();notify('Comment updated');return;
    }else if(form.dataset.form==='dependency'){
      if(!form.depends_on.value)return;await api(`/api/issues/${encodeURIComponent(store.currentIssue.identifier)}/dependencies`,{method:'POST',body:JSON.stringify({depends_on:+form.depends_on.value})});
    }else if(form.dataset.form==='gitlink'){
      const ref=form.ref.value.trim();if(!ref)return;await api(`/api/issues/${encodeURIComponent(store.currentIssue.identifier)}/git-links`,{method:'POST',body:JSON.stringify({kind:form.kind.value,ref,url:form.url.value.trim()||null})});
    }
    form.reset();await refreshDetail();notify('Issue updated');
  }catch(error){notify(error.message)}
}

export function handleCommentShortcut(event){
  if(!(event.metaKey||event.ctrlKey)||event.key!=='Enter'||event.target?.name!=='body'||event.target?.tagName!=='TEXTAREA')return;
  const form=event.target.closest('[data-form="comment"]');if(!form)return;
  event.preventDefault();form.requestSubmit();
}

export function handleInlineEditShortcut(event){
  const form=event.target.closest('[data-form="edit-issue"],[data-form="edit-comment"]');if(!form)return;
  if(event.key==='Escape'){
    event.preventDefault();store.editingIssue=false;store.editingCommentId=null;renderDetail();return;
  }
  if((event.metaKey||event.ctrlKey)&&event.key==='Enter'){
    event.preventDefault();form.requestSubmit();
  }
}

export function handlePropertyPickerDismissal(event){
  if(event.key!=='Escape')return;
  const picker=$$('details.property-picker[open],details.label-editor[open]').find(item=>item.getClientRects().length);
  if(!picker)return;
  event.preventDefault();const field=picker.dataset.propertyPicker;const wasCreating=store.creatingIssueLabel;dismissPropertyPickers();if(wasCreating)renderDetail();requestAnimationFrame(()=>focusPropertyPicker(field));
}
