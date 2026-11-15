import {escapeHTML as esc} from './markdown.js';

export const executionLabels={lpai_devspace:'LPAI 开发空间',lpai:'LPAI 组件库',local:'本地'};

export function runtimeView(context){
  const requested=context.execution_request?.execution;
  const candidate=requested??context.approved_execution;
  const execution=Object.hasOwn(executionLabels,candidate)?candidate:'';
  const binding=context.bindings?.find(item=>item.kind==='lpai');
  const state=context.execution_request?.state;
  const ssh=context.devspace_ssh,verifiedSSH=!!ssh?.verified_at&&(!ssh.state||ssh.state==='verified');
  const gap=!execution?'请选择运行位置。':execution!=='local'&&!context.connections?.config?.lpai_enabled?'LPAI 连接未启用。'
    :execution==='lpai_devspace'&&(!binding||state==='needs_binding')?(binding?'请确认使用当前绑定空间。':'请先绑定开发空间。')
      :execution==='lpai_devspace'&&(state==='needs_ssh'||!verifiedSSH)?'SSH 待验证，请展开下方 SSH 配置。':'';
  return {execution,label:executionLabels[execution]||'尚未选择运行位置',
    gap,
    needsAttention:['needs_binding','needs_ssh'].includes(state)||!execution,
    canReconfirm:execution==='lpai_devspace'&&state==='needs_binding'&&!!binding,
    binding,showResources:execution==='lpai'&&context.connections?.config?.lpai_enabled===true};
}

export function runtimeChoicePanel({context,sessionId,current,api,refresh,toast}){
  const view=runtimeView(context),host=document.createElement('section');
  host.className='record-card company-card execution-choice';
  host.innerHTML=`<div class="execution-choice-row"><label>运行位置<select aria-label="后续运行位置"><option value="">选择运行位置</option><option value="lpai_devspace">LPAI 开发空间</option><option value="lpai">LPAI 组件库</option><option value="local">本地工作区</option></select></label></div>${view.gap?`<p class="small runtime-choice-note ${view.canReconfirm?'runtime-reconfirm':''}" role="status">${esc(view.gap)}${view.canReconfirm?` <button type="button" class="text-button" data-confirm-location>使用 ${esc(view.binding.name)}</button>`:''}</p>`:''}`;
  const select=host.querySelector('select'),confirm=host.querySelector('[data-confirm-location]');
  select.value=view.execution;
  let saving=false;
  async function save(execution){
    if(saving||sessionId!==current()||!host.isConnected)return;
    if(!Object.hasOwn(executionLabels,execution)){select.value=view.execution;return;}
    saving=true;select.disabled=true;if(confirm)confirm.disabled=true;
    try{
      await api('/api/company/execution-location',{session_id:sessionId,execution,reason:view.canReconfirm&&execution===view.execution?'用户确认当前绑定的开发空间用于后续方案':'用户在运行页选择后续运行位置'});
      if(sessionId!==current()||!host.isConnected)return;
      await refresh();toast('后续运行位置已保存；未启动、停止或提交任务');
    }catch(error){if(sessionId===current()&&host.isConnected){select.value=view.execution;toast(error.message);}}
    finally{saving=false;select.disabled=false;if(confirm)confirm.disabled=false;}
  }
  select.onchange=()=>save(select.value);
  if(confirm)confirm.onclick=()=>save(view.execution);
  return host;
}

export function componentOverview({context,onRuns,onWatch}){
  const host=document.createElement('section');host.className='record-card company-card component-runtime';
  const submissions=context.training?.submissions||[];
  const prepared=submissions.filter(item=>item.state==='prepared').length;
  const unresolved=submissions.filter(item=>['requested','unknown','pending_approval'].includes(item.state)).length;
  host.innerHTML=`<header><strong>LPAI 组件库</strong></header><p>按方案自动创建组件并提交任务；高级配置可复用已有组件。</p>${prepared||unresolved?`<p class="small" role="status">${prepared?`${prepared} 个任务待审核。`:''}${unresolved?`${unresolved} 个原提交待核对。`:''}</p>`:''}<div class="company-actions"><button type="button" class="text-button" data-view-runs>查看任务与历史</button><button type="button" class="text-button" data-bind-task>绑定已有任务</button></div>`;
  host.querySelector('[data-view-runs]').onclick=onRuns;
  host.querySelector('[data-bind-task]').onclick=onWatch;
  return host;
}

export function componentResourceDirectory({createPanel,live}){
  const host=document.createElement('details');host.className='component-resource-directory';
  host.innerHTML='<summary>资源目录</summary>';
  let loaded=false;
  host.ontoggle=()=>{if(host.open&&!loaded&&host.isConnected&&live()){loaded=true;host.append(createPanel());}};
  return host;
}
