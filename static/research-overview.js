import {escapeHTML as esc} from './markdown.js';

const activeStates=new Set(['working','running','preparing','finalizing']);
const reviewStates=new Set(['waiting_review','waiting_plan_review']);
const sourceLabels={exact_plan_review:'具体版本人工审核',program_checked_research_delegation:'程序核对研究授权',exact_run_link:'明确关联的本地运行',exact_cloud_run_link:'明确关联的云端运行',historical_record:'保存时记录'};
export function recordState(record={}){
  return ({recorded:'已记录',not_verified:'尚未验证',proposed:'待评审',running:'运行状态见研究总览',completed:'保存为已完成',review_approved:'审核通过',approved:'授权已核对',request_changes:'退回修改',failed:'运行失败',blocked:'需核对',unknown:'状态未确认'})[record.current_status||record.status]||'已记录';
}
export function objectProvenance(node,record){
  const c=node.story_context;
  if(!c)return recordState(record||node)+' · '+(sourceLabels[record?.status_source]||'图谱保存记录');
  const actor=({agent:'Agent分析',human:'人工输入',evaluator:'验收者复核'})[c.actor]||'来源待核实';
  const origin=({user:'人工输入触发',review:'人工评审触发',background:'系统自动续接'})[c.trigger_origin]||'触发来源未核实';
  return [recordState({...node,current_status:null}),c.retrospective?'历史梳理':'原记录保留',actor,
    c.actor==='agent'?(c.trigger_label||origin):''].filter(Boolean).join(' · ');
}
export function currentObject(detail){
  const nodes=detail?.graph?.nodes||[],life=detail?.research_lifecycle;
  const id=detail?.graph?.execution_location?.focus_node_id||(['plan','review'].includes(life?.subject_kind)?life.subject_id:null);
  return nodes.find(n=>n.id===id)||null;
}
export function overviewModel(detail,kernel={},sync={state:'connecting'},attempt=null){
  const life=detail?.research_lifecycle,items=[],seen=new Set();
  const add=(type,id,title,note='')=>{if(!id)return;const key=type+':'+id;if(seen.has(key))return;seen.add(key);items.push({key,type,id,title,note});};
  if(detail?.session){
    if(attempt?.id)add('request',attempt.id,'核对原发送请求','保留原 ID 与草稿，不会重发');
    for(const d of detail.deliveries||[])if(['unknown','requested'].includes(d.state))add('request',d.id,'原请求待核对',d.error||'未知不等于失败，不开启替代轮次');
    for(const intent of detail.delivery_controls?.interrupt_intents||[])if(intent.state!=='completed')add('interrupt',intent.id,'原中断待核对','不重放中断，不取消云端任务');
    for(const approval of detail.approvals||[])add('approval',approval.id,'操作需要确认','查看原请求后明确答复');
    const record=(detail.records||[]).find(r=>r.id===life?.subject_id&&r.project_id===detail.session.project_id);
    if(reviewStates.has(life?.state)&&record?.kind==='plan')add('plan',record.id,'审核具体方案',record.title);
    if(life?.state==='waiting_stop_review'&&record?.kind==='review'&&life.requires_human_confirmation!==false)add('stop',record.id,'审核本轮结束建议',record.title);
    if(['awaiting_assessment','awaiting_round_review'].includes(life?.state))add('evidence',life.subject_id,'查看结果与评审依据','这里只打开已有记录，不自动启动分析');
    if(life?.state==='blocked'&&!items.length){
      if(life.allowed_actions?.includes('configure_environment'))add('environment',detail.session.project_id,'配置开发空间 SSH','在环境页保存并验证连接；尚未开始训练');
      else add('evidence',life.subject_id,'查看受阻对象',life.reason||'先核对原始证据与权限');
    }
  }
  const closure=life?.state==='assessment_complete';
  const reasonLabels={budget:'预算受限',no_progress:'无有效进展',blocked:'证据或权限受限',user_stop:'按用户要求',target_achieved:'目标评审完成'};
  const title=closure?`本轮已收束 · ${reasonLabels[life.stop_reason]||'查看结束评审'}`:life?.label||(detail?'研究状态尚未确认':'选择一个研究会话');
  const tone=life?.state==='blocked'?'warning':items.length?'attention':activeStates.has(life?.state)?'active':'neutral';
  return {title,tone,reason:closure?(life.target_verified===true?'后端已记录目标核验；适用范围见结束评审。':'收束不等于达标；尚无目标通过的确认。'):life?.reason||'研究状态来自后台记录，不由最后一条聊天推断。',
    items,object:currentObject(detail),modelConnected:kernel.connected===true,
    modelLabel:kernel.connected===true?'模型已连接':'模型未连接',syncLabel:sync.state==='connected'?'页面已连接':sync.state==='disconnected'?'同步中断 · 恢复中':'页面连接中',syncState:sync.state,
    activity:sync.state==='disconnected'?'页面数据可能已过期；正在恢复同步，原请求不会重发。':life?.state==='waiting_external'?'外部任务等待中，不是人工待办':activeStates.has(life?.state)?'后台正在处理当前研究对象':life?.state==='paused'?'已暂停自动推进；已提交任务可能仍在运行':items.length?'需要核对下方确切对象':'当前没有明确的人工待办'};
}

// Display-only subset: original edges remain original edges; no inferred causal links.
export function focusGraph(data={},objectId){
  const known=new Map((data.nodes||[]).map(n=>[n.id,n]));if(!known.has(objectId))return data;
  const keep=new Set([objectId]),queue=[objectId],edges=data.edges||[];
  const structural=e=>e.primary!==false&&!['supports','refutes','contradicts','tests','uses','compares','related_to','revisits','recorded_after','motivates','leads_to','informed_by','depends_on','assessed_in'].includes(e.kind);
  while(queue.length){const id=queue.pop();for(const e of edges)if(e.target===id&&structural(e)&&!keep.has(e.source)){keep.add(e.source);queue.push(e.source);}}
  const owner=known.get(objectId).plan_id|| (known.get(objectId).kind==='plan'?objectId:null);
  if(owner)for(const n of known.values())if(n.id===owner||n.plan_id===owner)keep.add(n.id);
  for(const e of edges)if(e.source===objectId||e.target===objectId){keep.add(e.source);keep.add(e.target);}
  const nodes=[...known.values()].filter(n=>keep.has(n.id)).map(n=>owner&&n.plan_id===owner?{...n,milestone:true}:n);
  const family=data.plan_series?{...data.plan_series,families:(data.plan_series.families||[]).map(f=>({...f,plan_ids:f.plan_ids.filter(id=>keep.has(id))})).filter(f=>f.plan_ids.length)}:undefined;
  return {...data,nodes,edges:edges.filter(e=>keep.has(e.source)&&keep.has(e.target)),...(family?{plan_series:family}:{}),projection:{kind:'current-object-display',original_node_count:known.size}};
}

export function installResearchOverview({host,selection,onAction,onGraphMode,onLocate,onOpenRecord,onModels}){
  host.innerHTML=`<div class="overview-connections"><span data-sync role="status"></span><button type="button" data-model></button><span data-sync-time></span></div><div class="overview-main"><div class="overview-copy"><span class="overview-eyebrow">当前研究</span><strong data-title role="status" aria-live="polite"></strong><p data-reason></p></div><button type="button" class="todo-trigger" aria-haspopup="dialog" aria-controls="research-todo-dialog" data-todo>待你处理 <span>0</span></button></div><p class="overview-footnote" data-activity></p>`;
  const dialog=document.createElement('dialog');dialog.id='research-todo-dialog';dialog.className='todo-dialog';dialog.innerHTML='<div class="dialog-header"><h2>当前会话 · 待你处理</h2><button type="button" class="icon-button" data-close aria-label="关闭待办">×</button></div><p class="muted">入口仅打开原对象，不会直接批准、重发或启动实验。</p><div data-items></div>';document.body.append(dialog);
  const objectHost=document.querySelector('#current-research-object'),listHost=document.querySelector('#research-record-list');
  let latest=null,detail=null,key='',mode='focus',browseId=null,signature='',listSignature='',objectSignature='';
  const setText=(selector,text)=>{const e=host.querySelector(selector);if(e.textContent!==text)e.textContent=text;};
  const close=()=>{dialog.close();host.querySelector('[data-todo]').focus();};dialog.querySelector('[data-close]').onclick=close;dialog.addEventListener('cancel',e=>{e.preventDefault();close();});
  dialog.addEventListener('close',()=>host.querySelector('[data-todo]').setAttribute('aria-expanded','false'));
  host.querySelector('[data-todo]').onclick=()=>{renderTodos();dialog.showModal();host.querySelector('[data-todo]').setAttribute('aria-expanded','true');};
  host.querySelector('[data-model]').onclick=onModels;
  function renderTodos(){
    const box=dialog.querySelector('[data-items]'),sig=JSON.stringify([key,latest?.items]);if(sig===signature)return;signature=sig;
    box.innerHTML=latest?.items.length?'':'<p class="empty-note">当前快照没有明确的人工待办。外部等待、历史提案不计入待办。</p>';
    for(const item of latest?.items||[]){const row=document.createElement('button');row.type='button';row.className='todo-item';row.dataset.todoKey=item.key;row.innerHTML=`<span><strong>${esc(item.title)}</strong><small>${esc(item.note)}</small></span><span aria-hidden="true">→</span>`;
      const captured=key;row.onclick=()=>{if(JSON.stringify(selection())!==captured||!latest?.items.some(x=>x.key===item.key))return;dialog.close();onAction(item);};box.append(row);}
  }
  function renderObject(){
    const current=latest?.object,selected=(detail?.graph?.nodes||[]).find(n=>n.id===browseId),shown=selected||current;
    objectHost.hidden=!shown;
    const identity=JSON.stringify([key,shown?.id]);
    if(identity!==objectSignature){
      const returnFocus=objectHost.contains(document.activeElement)?document.activeElement?.dataset:null;
      objectSignature=identity;
      if(!shown){delete objectHost.dataset.objectId;objectHost.innerHTML='<span class="overview-eyebrow">当前对象</span><strong>暂无明确关联的研究对象</strong><p>完整历史仍可浏览，不根据最近节点猜测执行位置。</p>';return;}
      objectHost.innerHTML='<div class="object-heading"><span class="overview-eyebrow" data-object-role></span><span class="object-kind" data-object-kind></span></div><strong data-object-title></strong><div class="object-actions"><details class="object-description"><summary>摘要</summary><div><p data-object-summary></p><small data-object-source></small></div></details><button type="button" class="text-button" data-open title="查看原记录" aria-label="查看原记录">查看</button><button type="button" class="text-button" data-locate title="回到当前研究对象" aria-label="回到当前研究对象">当前</button></div>';
      const captured=key,id=shown.id;
      objectHost.querySelector('[data-open]').onclick=()=>{if(JSON.stringify(selection())===captured&&(detail?.graph?.nodes||[]).some(n=>n.id===id))onOpenRecord(id);};
      objectHost.querySelector('[data-locate]').onclick=()=>{if(JSON.stringify(selection())!==captured||!latest?.object)return;const id=latest.object.id;browseId=null;renderObject();onLocate(id);objectHost.querySelector('[data-locate]')?.focus();};
      const description=objectHost.querySelector('.object-description');description.addEventListener('keydown',e=>{if(e.key==='Escape'&&description.open){e.preventDefault();e.stopPropagation();description.open=false;description.querySelector('summary').focus();}});
      if(returnFocus&&Object.hasOwn(returnFocus,'locate'))objectHost.querySelector('[data-locate]').focus();
    }
    if(!shown)return;
    const record=(detail?.records||[]).find(r=>r.id===shown.id);
    const text=(selector,value)=>{const e=objectHost.querySelector(selector);if(e.textContent!==value)e.textContent=value;};
    objectHost.dataset.objectId=shown.id;
    text('[data-object-role]',shown.id===current?.id?'当前对象':'正在浏览历史');
    text('[data-object-kind]',(({project:'研究起点',investigation:'资料核查',finding:'研究发现',correction:'结论修订',question:'未解决问题',plan:'方案',trial:'实验',result:'结果',review:'评审',series:'系列'})[shown.kind]||'研究记录')+(shown.version_label?' · '+shown.version_label:''));
    text('[data-object-title]',shown.title||'未命名记录');
    text('[data-object-summary]',shown.summary||'摘要未记录；打开原件查看完整内容。');
    text('[data-object-source]',objectProvenance(shown,record));
    objectHost.querySelector('[data-locate]').disabled=!current;
  }
  document.addEventListener('pointerdown',e=>{const d=objectHost.querySelector('.object-description[open]');if(d&&!d.contains(e.target))d.open=false;});
  function renderList(){
    const records=detail?.records||[],sig=JSON.stringify([key,records]);if(sig===listSignature)return;listSignature=sig;
    listHost.innerHTML=records.length?'':'<p class="empty-note">尚无研究记录。</p>';
    for(const r of records){const row=document.createElement('button');row.type='button';row.className='research-list-row';row.dataset.recordId=r.id;row.innerHTML=`<small>${esc(({plan:'方案',trial:'实验',result:'结果',review:'评审',question:'问题',decision:'决策'})[r.kind]||'记录')} ${r.version?'· '+esc(r.version):''} · ${esc(recordState(r))}</small><strong>${esc(r.title)}</strong><span>${esc(r.summary)}</span>`;const captured=key;row.onclick=()=>{if(JSON.stringify(selection())===captured)onOpenRecord(r.id);};listHost.append(row);}
  }
  for(const b of document.querySelectorAll('[data-graph-mode]'))b.onclick=()=>{mode=b.dataset.graphMode;document.querySelectorAll('[data-graph-mode]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));listHost.hidden=mode!=='list';document.querySelector('#graph-host').hidden=mode==='list';onGraphMode(mode);};
  return {render(next,kernel,sync,attempt){
    const nextKey=JSON.stringify(selection());if(nextKey!==key){key=nextKey;browseId=null;signature='';if(dialog.open)dialog.close();objectSignature='';listSignature='';}
    detail=next;latest=overviewModel(next,kernel,sync,attempt);host.dataset.tone=latest.tone;
    setText('[data-title]',latest.title);setText('[data-reason]',latest.reason);setText('[data-activity]',latest.activity);setText('[data-sync]',latest.syncLabel);host.querySelector('[data-sync]').dataset.state=latest.syncState;
    setText('[data-model]',latest.modelLabel);host.querySelector('[data-model]').dataset.connected=String(latest.modelConnected);
    setText('[data-sync-time]',sync.lastAt?'页面数据 '+new Date(sync.lastAt).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false}):'');
    host.querySelector('[data-todo] span').textContent=latest.items.length;host.querySelector('[data-todo]').dataset.empty=String(!latest.items.length);
    if(dialog.open)renderTodos();renderObject();renderList();
  },browse(id){browseId=id;renderObject();},get mode(){return mode;}};
}
