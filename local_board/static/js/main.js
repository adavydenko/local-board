import {store, canWrite, defaultNewIssueAssignee} from './store.js';
import {$, $$, esc, notify} from './dom.js';
import {api} from './api.js';
import {
  renderShell, renderSettings,
  handleSettingsTabs, handleSettingsAction, handleSettingsSubmit, handleSettingsTabKeydown, handleMilestoneEditorDismissal,
} from './app.js';
import {renderFilters, renderIssues, clearIssueFilters, updateBoardScrollHint} from './views/issues.js';
import {renderActivity} from './views/activity.js';
import {
  renderDetail,
  handleIssueAction, handleDetailSubmit, handleCommentShortcut, handleInlineEditShortcut, handlePropertyPickerDismissal,
  refreshDetail, actorOptions, milestoneOptions,
} from './views/issue-detail.js';

export async function loadAll(){
  const [me,dashboard]=await Promise.all([api('/api/me'),api('/api/dashboard')]);
  store.identity=me;store.data=dashboard;
  login.classList.add('hidden');
  render();
  await restoreLocation();
}

export async function reloadBoard(){
  store.data=await api('/api/dashboard');
  render();
}

export function render(){
  renderShell();renderFilters();renderIssues();renderActivity();renderSettings();
  if(store.currentIssue)renderDetail();
}

export function setView(view,{updateHistory=true,focusContent=true}={}){
  store.currentView=view;
  $$('.nav-item').forEach(button=>{const active=button.dataset.view===view||(view==='issue'&&button.dataset.view==='issues');button.classList.toggle('active',active);button.setAttribute('aria-current',active?'page':'false')});
  issuesView.classList.toggle('hidden',view!=='issues');activityView.classList.toggle('hidden',view!=='activity');settingsView.classList.toggle('hidden',view!=='settings');issueView.classList.toggle('hidden',view!=='issue');
  newIssueBtn.classList.toggle('hidden',!canWrite()||view!=='issues');
  if(updateHistory&&view!=='issue')history.pushState({view},'',location.pathname);
  if(focusContent)mainContent.focus({preventScroll:true});window.scrollTo({top:0,behavior:'instant'});
}

export function setLayout(layout){
  store.currentLayout=layout;
  issueList.classList.toggle('hidden',layout!=='list');issueBoard.classList.toggle('hidden',layout!=='board');
  $$('.layout-button').forEach(button=>{const active=button.dataset.layout===layout;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active))});
  updateBoardScrollHint();
}

export async function openDetail(ref,{updateHistory=true,focusContent=true}={}){
  try{
    store.currentIssue=await api('/api/issues/'+encodeURIComponent(ref));store.editingIssue=false;store.editingCommentId=null;renderDetail();setView('issue',{updateHistory:false,focusContent});
    if(updateHistory)history.pushState({view:'issue',issue:ref,fromApp:true},'',`#issue/${encodeURIComponent(ref)}`);
  }catch(error){notify(error.message)}
}

export async function restoreLocation(state=history.state){
  const match=location.hash.match(/^#issue\/(.+)$/);
  if(match)await openDetail(decodeURIComponent(match[1]),{updateHistory:false,focusContent:false});
  else setView(state?.view==='settings'?'settings':state?.view==='activity'?'activity':'issues',{updateHistory:false,focusContent:false});
}

export function openNewIssue(status=''){
  if(!canWrite())return;
  issueForm.reset();issueError.textContent='';issueMilestone.innerHTML=milestoneOptions(null);issueAssignee.innerHTML=actorOptions(defaultNewIssueAssignee(status));
  issueLabelPicker.innerHTML=(store.data.board?.labels||[]).map(label=>`<label class="label-choice"><input type="checkbox" value="${label.id}"><span class="label-dot" style="--label-color:${esc(label.color)}"></span>${esc(label.name)}</label>`).join('')||'<span class="muted">No labels configured</span>';
  issueDialog.dataset.status=status;newIssueHeading.textContent=status?`Create issue in ${status}`:'Create issue';issueDialog.showModal();requestAnimationFrame(()=>issueTitle.focus());
}

$$('.nav-item').forEach(button=>button.addEventListener('click',()=>setView(button.dataset.view)));
$$('.layout-button').forEach(button=>button.addEventListener('click',()=>setLayout(button.dataset.layout)));
$$('[data-close]').forEach(button=>button.addEventListener('click',()=>$('#'+button.dataset.close).close()));
issueList.addEventListener('click',event=>{if(event.target.closest('[data-action="clear-filters"]'))return clearIssueFilters();const issue=event.target.closest('[data-ref]');if(issue)openDetail(issue.dataset.ref);const add=event.target.closest('[data-new-status]');if(add)openNewIssue(add.dataset.newStatus)});
issueBoard.addEventListener('click',event=>{if(event.target.closest('[data-action="clear-filters"]'))return clearIssueFilters();const issue=event.target.closest('[data-ref]');if(issue)openDetail(issue.dataset.ref);const add=event.target.closest('[data-new-status]');if(add)openNewIssue(add.dataset.newStatus)});
issueBoard.addEventListener('scroll',updateBoardScrollHint);window.addEventListener('resize',updateBoardScrollHint);
issueView.addEventListener('click',handleIssueAction);issueView.addEventListener('submit',handleDetailSubmit);issueView.addEventListener('keydown',handleCommentShortcut);issueView.addEventListener('keydown',handleInlineEditShortcut);issueView.addEventListener('keydown',handlePropertyPickerDismissal);
issueView.addEventListener('input',event=>{const composer=event.target.closest('.comment-composer');if(composer)composer.classList.toggle('has-draft',Boolean(composer.querySelector('textarea')?.value.trim()))});
settingsView.addEventListener('click',event=>{if(!handleSettingsTabs(event))handleSettingsAction(event)});settingsView.addEventListener('submit',handleSettingsSubmit);settingsView.addEventListener('keydown',handleSettingsTabKeydown);settingsView.addEventListener('keydown',handleMilestoneEditorDismissal);
window.addEventListener('popstate',event=>restoreLocation(event.state));

loginForm.addEventListener('submit',async event=>{
  event.preventDefault();store.token=tokenInput.value.trim();loginError.textContent='';
  try{await loadAll();try{localStorage.setItem('localBoardToken',store.token)}catch{}}
  catch(error){loginError.textContent=error.message}
});
refreshBtn.addEventListener('click',async()=>{try{await reloadBoard();if(store.currentView==='issue')await refreshDetail();notify('Board refreshed')}catch(error){notify(error.message)}});
newIssueBtn.addEventListener('click',()=>openNewIssue());
[searchInput,milestoneFilter,assigneeFilter].forEach(control=>control.addEventListener(control===searchInput?'input':'change',renderIssues));

issueForm.addEventListener('submit',async event=>{
  event.preventDefault();issueError.textContent='';
  try{
    const labels=[...issueLabelPicker.querySelectorAll('input:checked')].map(input=>+input.value),milestone=issueMilestone.value,assignee=issueAssignee.value;
    const created=await api('/api/issues',{method:'POST',body:JSON.stringify({title:issueTitle.value.trim(),description:issueDescription.value,priority:issuePriority.value,status:issueDialog.dataset.status||undefined,milestone_id:milestone?+milestone:null,assignee_id:assignee?+assignee:null,labels})});
    issueDialog.close();await reloadBoard();notify(`${created.identifier} created`);await openDetail(created.identifier);
  }catch(error){issueError.textContent=error.message}
});

issueDialog.addEventListener('click',event=>{if(event.target===issueDialog)issueDialog.close()});
setLayout('list');
if(!history.state?.view){
  const directIssue=location.hash.match(/^#issue\/(.+)$/);
  if(directIssue)history.replaceState({view:'issue',issue:decodeURIComponent(directIssue[1]),fromApp:false},'',location.href);
  else history.replaceState({view:'issues'},'',location.href);
}
if(store.token){loadAll().catch(()=>{try{localStorage.removeItem('localBoardToken')}catch{}login.classList.remove('hidden')})}
