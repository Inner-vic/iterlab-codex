import {escapeHTML as esc} from './markdown.js';
export const runLabels={pending:'排队中',queued:'排队中',scheduling:'排队中',waiting:'排队中',submitted:'已提交',requested:'提交中',running:'运行中',starting:'启动中',creating:'准备中',initializing:'准备中',image_pulling:'准备中',succeeded:'已完成',completed:'已完成',success:'已完成',failed:'失败',error:'失败',stopped:'已停止',terminated:'已停止',cancelled:'已取消',canceled:'已取消',unknown:'待核对',timeout:'超时',timed_out:'超时',artifacts_missing:'产物待补齐'};
export function phaseOf(state){const s=String(state).toLowerCase();return ['pending','queued','scheduling','waiting','submitted'].includes(s)?'waiting':s==='unknown'?'unknown':s==='running'?'running':s==='requested'||['starting','creating','initializing','image_pulling'].includes(s)?'preparing':'terminal';}
export function connectionSummary(platform,checks){
  const latest=new Map(checks.map(c=>[c.kind,c]));
  const core=latest.get(platform==='lpai'?'lpai_cli':'gitlab_api');
  if(!core)return '待确认本机登录';
  if(core.stale)return '连接状态待更新';
  if(core.state!=='passed')return core.message||'连接尚未就绪';
  if(platform==='gitlab')return '账号访问正常';
  const training=latest.get('lpai_sdk');
  return training?.state==='passed'&&!training.stale?'任务查询与训练接口可用':'可监控，训练接口待就绪';
}

export function installSessionRuns({api,current,toast,openRuns,onChange=()=>{}}){
  const expanded=new Map();
  const wake={dispatched:'结果分析已排队（尚不代表分析完成）',research_scheduler:'已交给研究接续调度',already_terminal:'仅保留历史',finished:'已结束',authority_changed:'连接已变化，自动分析已停止',owner_unavailable:'所属项目已移除',not_attached:'监控尚未绑定，按原任务核对'};
  function card(run,compact=false){
    const box=document.createElement('details');box.className='session-run-card';box.open=!!expanded.get((compact?'strip:':'detail:')+run.id);
    box.ontoggle=()=>expanded.set((compact?'strip:':'detail:')+run.id,box.open);
    const state=String(run.state).toLowerCase(),phase=phaseOf(state),label=runLabels[state]||run.state;
    box.innerHTML=`<summary><span class="run-heading"><span class="run-name" title="${esc(run.name)}">${esc(run.name)}</span><span class="run-state ${phase}">${esc(label)}</span><span class="run-chevron" aria-hidden="true">⌄</span></span>${run.active?`<span class="run-track ${phase}" role="status" aria-label="${esc(label)}，未返回可核实的百分比"><span></span></span>`:''}</summary><div class="run-details"><p>${run.kind==='lpai'?'LPAI 组件库':run.kind==='lpai_devspace'?'LPAI GPU 开发空间':'本地'} · ${esc(wake[run.wake_state]|| (run.auto_resume?'完成后分析结果':'仅通知'))}</p><p>状态更新：${esc(new Date(run.updated_at).toLocaleString())}</p>${run.seconds!=null?`<p>实际耗时：${run.seconds} 秒</p>`:''}${run.directory?`<p>证据目录：<code>${esc(run.directory)}</code></p>`:''}<div class="run-actions"></div></div>`;
    const actions=box.querySelector('.run-actions');
    const detail=box.querySelector('.run-details');
    const note=text=>{const p=document.createElement('p');p.textContent=text;detail.insertBefore(p,actions);};
    if(run.queue_seconds!=null)note(`排队及启动等待 · ${run.queue_seconds} 秒`);
    if(run.execution_seconds!=null)note(`平台运行时长 · ${run.execution_seconds} 秒`);
    if(run.monitor?.failures)note(`状态查询暂未确认，连续失败 ${run.monitor.failures} 次；不会据此重投或停止训练。`);
    if(run.monitor?.last_success)note(`最近查询成功：${new Date(run.monitor.last_success*1000).toLocaleString()}`);
    if(run.collection){const labels={collecting:'正在回收',waiting_outputs:'等待必要结果到齐',ready:'结果已归档，等待分析',diagnostic_only:'仅可分析异常或证据缺口'};note(`结果回收：${labels[run.collection.state]||run.collection.state} · 已读取 ${run.collection.attempts||0} 次`);if(run.collection.result_check?.missing_files?.length)note(`缺少：${run.collection.result_check.missing_files.join('、')}`);}
    if(run.continuation)note(`研究接续：${run.continuation.reason||run.continuation.state}`);
    if(run.url){const url=new URL(run.url,location.href);if(url.protocol==='https:'&&url.hostname==='lpai.lixiang.com'){const link=document.createElement('a');link.href=url.href;link.target='_blank';link.rel='noopener';link.className='secondary';link.textContent='在 LPAI 查看';actions.append(link);}}
    if(run.session_title)note('所属会话：'+run.session_title);
    if(run.active&&run.watch_id){const label=document.createElement('label');label.className='check-label';const input=document.createElement('input');input.type='checkbox';input.checked=run.auto_resume;label.append(input,document.createTextNode('完成后分析结果'));input.onchange=async()=>{input.disabled=true;try{const sid=run.session_id||current();await api('/api/company/watch-preference',{session_id:sid,watch_id:run.watch_id,auto_resume:input.checked});await render();}catch(error){input.checked=!input.checked;toast(error.message);}finally{input.disabled=false;}};actions.append(label);}
    return box;
  }
  async function render(){
    const strip=document.querySelector('#session-runs'),sid=current();
    if(strip.dataset.sessionId!==sid){strip.replaceChildren();strip.hidden=true;strip.dataset.sessionId=sid||'';onChange();}
    if(!sid){strip.hidden=true;return;}
    let data=await api('/api/company/session-runs',{session_id:sid});if(sid!==current())return;
    strip.replaceChildren();strip.hidden=!data.active.length;
    if(data.active.length){const heading=document.createElement('div');heading.className='session-run-heading';heading.innerHTML=`<strong>本会话运行 <span>${data.active.length}</span></strong><button class="text-button">查看运行</button>`;heading.querySelector('button').onclick=openRuns;strip.append(heading);const list=document.createElement('div');list.className='session-run-strip-list';data.active.forEach(run=>list.append(card(run,true)));strip.append(list);}
    onChange();
    const host=document.querySelector('#session-run-list');if(!host)return data;
    data=await api('/api/company/session-runs',{session_id:sid,scope:'project'});if(sid!==current())return;
    host.replaceChildren();
    if(!data.active.length){const empty=document.createElement('p');empty.className='empty-note';empty.textContent='本项目暂无进行中的任务。提交或绑定任务后会显示在这里，已结束的运行保留在下方。';host.append(empty);}
    data.active.forEach(run=>host.append(card(run)));
    const history=document.createElement('details');history.className='session-run-history';history.innerHTML=`<summary>已结束的运行 · ${data.history.length}</summary>`;let offset=0;const more=document.createElement('button');more.className='text-button';more.textContent='显示更早记录';function appendHistory(){data.history.slice(offset,offset+20).forEach(run=>history.insertBefore(card(run),more));offset+=20;more.hidden=offset>=data.history.length;}more.onclick=appendHistory;history.append(more);history.ontoggle=()=>{if(history.open&&offset===0)appendHistory();};host.append(history);
    return data;
  }
  return {render};
}
