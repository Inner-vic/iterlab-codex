import {escapeHTML as esc} from './markdown.js';

export function resourceLabel(item){
  if(item.gpu_count===null)return `${item.gpu_type} · 共享规格（需单独核对）`;
  const kind=item.gpu_count?`${item.gpu_type} · ${item.gpu_count} GPU`:'CPU';
  return `${kind} · ${item.cpu} CPU / ${item.memory_gib} GiB`;
}

export function accountQueues(value){
  return value?.live===true&&value.source==='official_account_resources'&&value.account_scope?.subject
    &&Array.isArray(value.queues)?value.queues:[];
}
export function sameResourceAccount(a,b){
  return Boolean(a?.subject&&b?.subject&&a.subject===b.subject&&a.namespace===b.namespace
    &&a.connection_revision===b.connection_revision);
}

export function componentResourcePanel({api,sessionId,current,preference,toast}){
  const host=document.createElement('section');host.className='record-card company-card component-resources';
  host.innerHTML='<header><div><strong>组件任务资源</strong><p>当前账号的队列余量 · 用于后续组件方案的资源选择</p></div><button type="button" class="text-button" data-refresh>刷新余量</button></header><div class="queue-options" role="radiogroup" aria-label="选择组件任务资源"></div><p class="queue-observation-note" role="status">正在读取资源…</p><div class="queue-preference-footer"><span data-summary></span><button type="button" class="secondary" data-save disabled>用于后续方案</button></div>';
  const summary=host.querySelector('[data-summary]'),list=host.querySelector('.queue-options'),status=host.querySelector('[role=status]'),save=host.querySelector('[data-save]');
  summary.textContent='';
  let selected=preference?.queue_key,accountScope=null,loadGeneration=0;
  const footer=host.querySelector('.queue-preference-footer');footer.hidden=true;
  const live=()=>sessionId===current()&&host.isConnected;
  async function load(refresh=false){
    const generation=++loadGeneration;
    try{
      const value=await api('/api/company/resources',refresh?{refresh:true}:{});if(!live()||generation!==loadGeneration)return;
      const queues=accountQueues(value);
      accountScope=queues.length?value.account_scope:null;
      if(!sameResourceAccount(preference?.account_scope,value.account_scope)||!queues.some(q=>q.key===selected))selected=undefined;
      list.innerHTML=queueCards(queues,selected);status.textContent=value.message;
      summary.textContent=selected?'已选 · '+preference.queue_name:'尚未选择资源';
      for(const input of list.querySelectorAll('input'))input.onchange=()=>{selected=input.value;save.disabled=false;summary.textContent='将在后续方案中使用此队列偏好';};
      save.hidden=!queues.length;
      footer.hidden=!queues.length;
      if(!queues.length){summary.textContent='';save.disabled=true;}
    }catch(error){if(live()&&generation===loadGeneration)status.textContent='队列读取未完成：'+error.message;}
  }
  host.querySelector('[data-refresh]').onclick=()=>load(true);
  save.onclick=async()=>{save.disabled=true;try{
    const value=await api('/api/company/resource-queue-preference',{session_id:sessionId,queue_key:selected,expected_account_scope:accountScope});
    if(live()){summary.textContent='已选 · '+value.queue_name;toast('当前账号的资源选择已保存；未提交任务或扩大GPU授权');}
  }catch(error){if(live())toast(error.message);}finally{save.disabled=false;}};
  load();
  return host;
}

export function queueCards(queues=[],selected){
  return queues.map(queue=>{
    const resources=queue.resources.map(resource=>`<span class="queue-resource-row"><span>${esc(resource.label)}</span><span class="queue-observed-amount ${resource.available===0?'empty':''}">${resource.available==null?'余量未返回':'可用 '+esc(resource.available)+' '+(resource.unit==='card'?'卡':'台')}</span></span>`).join('');
    return `<label class="resource-queue-card"><input type="radio" name="component-resource-queue" value="${esc(queue.key)}" ${selected===queue.key?'checked':''}><span class="queue-card-body"><span class="queue-card-heading"><strong>${esc(queue.name)}</strong></span><span class="queue-cluster">${esc(queue.cluster_label||'')}</span><span class="queue-card-resources">${resources||'<span class="queue-resource-row">CPU 资源</span>'}</span></span></label>`;
  }).join('');
}
