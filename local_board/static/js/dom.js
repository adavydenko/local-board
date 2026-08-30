export const $=selector=>document.querySelector(selector);
export const $$=selector=>[...document.querySelectorAll(selector)];

export function esc(value){return String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]))}

export function initials(name){return String(name||'?').split(/\s+/).map(part=>part[0]).join('').slice(0,2)}

export function safeExternalUrl(value){
  try{const url=new URL(value);return ['http:','https:'].includes(url.protocol)?url.href:''}catch{return ''}
}

export function relativeTime(value){
  const date=new Date(value),seconds=Math.round((date-Date.now())/1000),abs=Math.abs(seconds);
  const formatter=new Intl.RelativeTimeFormat('en',{numeric:'auto'});
  if(abs<60)return formatter.format(seconds,'second');
  if(abs<3600)return formatter.format(Math.round(seconds/60),'minute');
  if(abs<86400)return formatter.format(Math.round(seconds/3600),'hour');
  if(abs<604800)return formatter.format(Math.round(seconds/86400),'day');
  return date.toLocaleDateString('en',{month:'short',day:'numeric',year:date.getFullYear()===new Date().getFullYear()?undefined:'numeric'});
}

export function humanize(value){return String(value||'updated').replaceAll('_',' ').replace(/^./,char=>char.toUpperCase())}

export function tomlString(value){return `"${String(value??'').replace(/\\/g,'\\\\').replace(/"/g,'\\"').replace(/\n/g,'\\n')}"`}

export function notify(message){
  toast.textContent=message;toast.classList.remove('hidden');
  clearTimeout(notify._timer);notify._timer=setTimeout(()=>toast.classList.add('hidden'),3200);
}

export function markdown(value){
  if(!value)return '<div class="markdown empty-copy">No description.</div>';
  let output=esc(value);
  output=output.replace(/`([^`]+)`/g,'<code>$1</code>');
  output=output.replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');
  output=output.replace(/^### (.+)$/gm,'<h3>$1</h3>');
  output=output.replace(/^## (.+)$/gm,'<h2>$1</h2>');
  output=output.replace(/^# (.+)$/gm,'<h1>$1</h1>');
  output=output.replace(/^- (.+)$/gm,'<div class="bullet"><span>$1</span></div>');
  output=output.replace(/\n/g,'<br>');
  return `<div class="markdown">${output}</div>`;
}

// Label colors come from board data, so they are actor-controlled text landing
// inside a style="" value. esc() stops it from escaping the attribute, but not
// from injecting further CSS within it ("red;background:…"), so only a literal
// hex color is emitted; anything else drops the attribute entirely and lets the
// stylesheet's own var(--label-color, var(--label-default)) fallback apply.
export function labelColorStyle(color){
  const value=String(color??'').trim();
  return /^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/.test(value)
    ? ` style="--label-color:${value}"` : '';
}
