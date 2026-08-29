import {store, canWrite, statusesSorted, milestoneName, labelCatalog} from '../store.js';
import {esc, initials, humanize} from '../dom.js';

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
