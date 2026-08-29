import {store} from './store.js';

export const DEFAULT_LABEL_COLOR='#5e6ad2';

export async function api(path,options={}){
  const response=await fetch(path,{...options,headers:{Authorization:'Bearer '+store.token,'Content-Type':'application/json',...(options.headers||{})}});
  let body={};
  if(response.status!==204){try{body=await response.json()}catch{}}
  if(!response.ok){
    const info=body.error||{};
    throw Object.assign(new Error(info.message||`HTTP ${response.status}`),{code:info.code,retryable:info.retryable});
  }
  return body;
}
