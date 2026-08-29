export const store = {
  token: '',
  data: {},
  identity: null,
  currentIssue: null,
  currentView: 'issues',
  currentLayout: 'list',
  editingIssue: false,
  editingCommentId: null,
  creatingMilestone: false,
  creatingLabel: false,
  creatingIssueLabel: false,
  activeSettingsTab: 'overview',
};
try{store.token=localStorage.getItem('localBoardToken')||''}catch{}

export function canWrite(){return store.identity?.role!=='viewer'}

export function statusesSorted(){return [...(store.data.board?.statuses||[])].sort((a,b)=>a.position-b.position)}

export function statusCategory(name){return statusesSorted().find(status=>status.name===name)?.category||''}

export function defaultNewIssueAssignee(status){return statusCategory(status)==='started'?store.identity?.id:null}

export function findIssueRef(id){return (store.data.issues||[]).find(issue=>issue.id===id)}

export function milestoneName(id){return (store.data.board?.milestones||[]).find(item=>item.id===id)?.name||''}

export function labelCatalog(name){return (store.data.board?.labels||[]).find(label=>label.name===name||label.key===name)}

export function activityEntityName(item){
  if(item.entity_type==='label')return (store.data.board?.labels||[]).find(label=>label.id===item.entity_id)?.name;
  if(item.entity_type==='milestone')return (store.data.board?.milestones||[]).find(milestone=>milestone.id===item.entity_id)?.name;
  if(item.entity_type==='actor')return (store.data.actors||[]).find(actor=>actor.id===item.entity_id)?.name;
  return null;
}
