import {store, activityEntityName} from '../store.js';
import {esc, initials, humanize, relativeTime} from '../dom.js';

export function renderActivity(){
  activityFeed.innerHTML=(store.data.activity||[]).map(item=>{
    const ref=item.identifier||activityEntityName(item)||`${item.entity_type} #${item.entity_id}`;
    return `<div class="activity-row"><span class="mini-avatar">${esc(initials(item.actor||'system'))}</span><div class="activity-copy"><strong>${esc(item.actor||'System')}</strong> ${esc(humanize(item.action).toLowerCase())} <span class="activity-ref">${esc(ref)}</span></div><time class="activity-time" datetime="${esc(item.created_at)}" title="${esc(new Date(item.created_at).toLocaleString('en'))}">${esc(relativeTime(item.created_at))}</time></div>`;
  }).join('')||'<div class="zero-state"><div><strong>No activity yet</strong>Changes to issues will appear here.</div></div>';
}
