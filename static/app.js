import {installSlashMenu} from './slash-menu.js';
import {installNameEditor} from './name-editor.js';
import {installBrand} from './brand.js';
import {homeContent} from './home.js';
import {installWorkbenchUI,resizeComposer} from './workbench-ui.js';
import {installProjectRemoval} from './project-removal.js';
import {installProjectSwitcher} from './project-switcher.js';
import {rolePolicy,defaultModels} from './model-config.js';
import {escapeHTML as esc,markdown} from './markdown.js';
import {installCompanyUI} from './company.js';
import {isProjectRuntimeEvent} from './runtime-events.js';
import {installResearchUI} from './research.js';
import {createDeliverySender,sameComposition} from './delivery.js';
import {createConversationView} from './conversation.js';
import {installResearchOverview,currentObject,focusGraph} from './research-overview.js';
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const paths={plus:'M12 5v14M5 12h14',sun:'M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5M17.5 17.5 19 19M5 19l1.5-1.5M17.5 6.5 19 5M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0',model:'m12 3 9 5v8l-9 5-9-5V8l9-5M3 8l9 5 9-5M12 13v8',settings:'M4 7h16M4 17h16M9 4v6M15 14v6',sidebar:'M4 3h16a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1M9 3v18',panel:'M4 3h16a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1M15 3v18',refresh:'M20 8a8 8 0 0 0-14-3L3 8m0-5v5h5m-4 8a8 8 0 0 0 14 3l3-3m0 5v-5h-5',arrow:'M4 12h16m-6-6 6 6-6 6','arrow-up':'M12 19V5m-6 6 6-6 6 6',chevron:'m8 10 4 4 4-4',close:'m6 6 12 12M6 18 18 6',stop:'M6 6h12v12H6z',folder:'M3 7V5a1 1 0 0 1 1-1h6l2 3h8a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7z',file:'M14 2H5v20h14V7l-5-5M14 2v5h5',terminal:'M3 4h18v16H3zM7 9l3 3-3 3m6 0h4',archive:'M3 4h18v4H3zM5 8v12h14V8M10 12h4',branch:'M6 3v12a4 4 0 0 0 4 4h8M6 8h8a4 4 0 0 0 4-4V3'};
const icon=name=>`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name]||paths.file}"></path></svg>`;
paths.pin='m9 3 6 0-1 5 4 4v2H6v-2l4-4-1-5M12 14v7';
function icons(root=document){root.querySelectorAll('[data-icon]').forEach(el=>el.innerHTML=icon(el.dataset.icon));}
installBrand(document);
icons();
let token=$('meta[name=workbench-token]').content;
const assetRevision=$('meta[name=workbench-assets]')?.content||'';
let state={projects:[],sessions:[],kernel:{}},current=null,detail=null,attachments=[],graph=null,fullGraph=null,graphLoad=null,graphSignature='',currentTab='graph',draftRevision=0,draftTimer=null,reloadTimer=null,toastTimer=null,eventCursor=0,approvalId=null;
let draftDirty=false,sessionGeneration=0,compositionRevision=0,detailRequest=0;
let historyFloor=null,historyLoading=false,pendingHomeStart=null;
const expandedTurns=new Map(),messagesByItem=new Map();
const selection=()=>({sid:current,pid:detail?.session.project_id,version:sessionGeneration});
const composition=()=>({sid:current,selection:sessionGeneration,revision:compositionRevision,prompt:$('#prompt').value,attachments:attachments.map(a=>({...a}))});
const sessionNameEditor=installNameEditor({id:'edit-session-dialog',heading:'重命名会话',label:'会话名称',onSave:(session,title)=>api(`/api/sessions/${session.id}/rename`,{title,expected_title:session.title}),onSaved:async session=>{await refreshState();if(current===session.id)await loadCurrent();toast('会话名称已更新');}});
const conversationView=createConversationView({host:$('#messages'),markdown,escapeHTML:esc,icon,date,onOpenRecord:id=>safe(()=>openGraph(id))(),onLoadOlder:()=>safe(loadOlder)(),expandedTurns});
const deliverySender=createDeliverySender({api,snapshot:composition,commit:commitSentComposition,onChange:()=>{updateSendButton();renderQueue();}});
const companyUI=installCompanyUI({api,current:()=>current,toast,refresh:()=>loadCurrent(),onRunsChange:()=>renderStatusStack(),openRuns:()=>{document.body.classList.remove('inspector-hidden');switchTab('runs');}});
const researchUI=installResearchUI({api,current:()=>current,selection,toast,refresh:()=>loadCurrent()});
let pageSync={state:'connecting',lastAt:null};
const overview=installResearchOverview({host:$('#research-overview'),selection,onAction:safe(openTodo),onGraphMode:()=>{graphSignature='';safe(renderGraph)();},onLocate:id=>{document.body.classList.remove('inspector-hidden');switchTab('graph');graph?.clearSelection();overview.browse(null);graph?.frameContext(id);},onOpenRecord:id=>safe(()=>openGraph(id))(),onModels:openSettings});
function renderOverview(){let attempt=null;try{attempt=deliverySender.pending(current);}catch{}overview.render(detail,state.kernel,pageSync,attempt);$('#sync-label').textContent=pageSync.state==='connected'?'页面已连接':pageSync.state==='disconnected'?'同步中断':'页面连接中';const indicator=$('#model-connection-indicator');if(indicator){indicator.dataset.connected=String(Boolean(state.kernel?.connected));$('#models').title=state.kernel?.connected?'模型连接 · 已连接':'模型连接 · 尚未连接';}renderStatusStack();}
function renderStatusStack(){const stack=$('#research-status-stack');if(!stack)return;const collapsed=localStorage.getItem('iterlab:status-stack:collapsed')==='1';stack.classList.toggle('is-collapsed',collapsed);const toggle=$('#toggle-research-stack');toggle.setAttribute('aria-expanded',String(!collapsed));toggle.setAttribute('aria-label',collapsed?'展开研究进展与任务':'收起研究进展与任务');toggle.innerHTML=icon('chevron')+`<span>${collapsed?'展开':'收起'}</span>`;const strip=$('#session-runs');const count=strip&&!strip.hidden?strip.querySelector('.session-run-heading strong span')?.textContent:null;const title=$('#research-overview [data-title]')?.textContent||'研究进展';$('#research-stack-summary').textContent=collapsed?title+(count?' · '+count+'个任务':''):'研究进展';const tone=$('#research-overview')?.dataset.tone;stack.dataset.tone=tone||'neutral';}
function syncState(value){pageSync={state:value,lastAt:value==='connected'?Date.now():pageSync.lastAt};renderOverview();}
async function openTodo(item){
  if(item.type==='environment'){document.body.classList.remove('inspector-hidden');switchTab('execution');return;}
  if(item.type==='plan')return researchUI.open(item.id);
  if(item.type==='stop')return researchUI.openRoundStop(item.id);
  if(item.type==='approval'){approvalId=null;renderApproval(item.id);return;}
  if(item.type==='interrupt'){
    const sid=current,generation=sessionGeneration;
    await api(`/api/sessions/${sid}/restore`,{});
    if(current===sid&&generation===sessionGeneration){await loadCurrent();toast(detail?.delivery_controls?.barrier?'原会话仍待核对':'原会话已核对，可继续处理方案');}
    return;
  }
  if(item.type==='request'){const queue=$('#queue');queue.scrollIntoView({block:'nearest'});const target=[...queue.querySelectorAll('[data-delivery-id]')].find(r=>r.dataset.deliveryId===item.id)||queue;target.tabIndex=-1;target.focus();return;}
  const object=currentObject(detail);if((detail?.records||[]).some(r=>r.id===item.id))return openGraph(item.id);if(object)return openGraph(object.id);document.body.classList.remove('inspector-hidden');switchTab('runs');
}
$('#graph-host').addEventListener('input',event=>{if(event.target.matches('input[type=search]')&&event.target.value.trim()&&overview.mode==='focus')$('[data-graph-mode=full]').click();});
const unknownDialog=document.createElement('dialog');unknownDialog.id='unknown-delivery-dialog';
unknownDialog.innerHTML='<div class="dialog-header"><h2>保留原未知请求并暂停</h2><button type="button" class="icon-button" data-close aria-label="关闭">×</button></div><p data-identity></p><p>这不会把原请求标为失败，也不会重发、解除未知屏障或停止云端任务。原错误和原ID保留，后续仍须凭确切原生身份核对。</p><form><label>暂停理由<textarea name="reason" rows="3" required></textarea></label><label class="check-label"><input name="confirmed" type="checkbox" required>我确认保留这条未知请求，仅暂停自动推进</label><div class="dialog-actions"><button type="submit" class="primary">确认保留未知并暂停</button></div></form>';
  document.body.append(unknownDialog);let pausePreview=null,pauseSubmitting=false;
unknownDialog.querySelector('[data-close]').onclick=()=>unknownDialog.close();unknownDialog.addEventListener('close',()=>{if(!unknownDialog.open)pausePreview=null;});
unknownDialog.querySelector('form').onsubmit=safe(async e=>{e.preventDefault();if(pauseSubmitting)return;const target=pausePreview,form=e.target;
  if(!target||target.sid!==current||target.generation!==sessionGeneration)throw Error('会话已变化，请重新核对原请求');
  const reason=form.elements.reason.value.trim();if(!form.elements.confirmed.checked||!reason)throw Error('请明确确认并填写暂停理由');
  pauseSubmitting=true;form.querySelector('[type=submit]').disabled=true;
  try{await api(`/api/sessions/${target.sid}/deliveries/${encodeURIComponent(target.id)}/pause`,{confirmed:true,reason});
    if(target===pausePreview){unknownDialog.close();toast('原未知身份和错误已保留；自动推进已暂停，未重发或停止云任务');if(current===target.sid&&sessionGeneration===target.generation)await loadCurrent();}
  }finally{pauseSubmitting=false;form.querySelector('[type=submit]').disabled=false;}
});
installWorkbenchUI();
const projectRemoval=installProjectRemoval({api,toast,beforeRemove:saveDraft,onRestored:refreshState,onChanged:async()=>{await refreshState();if(current)await loadCurrent();},onRemoved:async pid=>{
  if(detail?.session.project_id===pid)clearSelectedSession('项目已移除，可以在“归档与恢复”中找回。');
  await refreshState();
}});
const projectSwitcher=installProjectSwitcher({host:$('#project-rail'),expandButton:$('#toggle-sidebar'),getState:()=>({projects:state.projects,sessions:state.sessions,current,version:sessionGeneration}),onSelect:selectSession,onCreate:createSession,onManage:(project,anchor)=>projectRemoval.open(project,anchor),onError:toast});
function setSidebarCollapsed(collapsed){
  document.body.classList.toggle('sidebar-collapsed',collapsed);localStorage.setItem('iterlab-codex:collapsed',collapsed?'1':'0');
  updateSidebarToggle();projectSwitcher.setCollapsed();
  if(!collapsed&&document.activeElement===$('#expand-project-rail'))$('#toggle-sidebar').focus();
}
function updateSidebarToggle(){const collapsed=document.body.classList.contains('sidebar-collapsed'),button=$('#toggle-sidebar');button.setAttribute('aria-expanded',String(!collapsed));button.setAttribute('aria-controls','project-list');button.title=collapsed?'展开项目侧栏':'收起项目侧栏';button.setAttribute('aria-label',button.title);}
async function refreshLocalToken(){const response=await fetch('/',{cache:'no-store'});const text=await response.text(),match=text.match(/name="workbench-token" content="([^"]+)"/);if(!match||!text.includes('Codex 研究工作台'))throw Error('本地服务身份已变化，请刷新页面核对');token=match[1];}
async function api(path,data,revalidated=false){const response=await fetch(path,{method:data===undefined?'GET':'POST',headers:{'X-Workbench-Token':token,...(assetRevision?{'X-Workbench-Assets':assetRevision}:{}),...(data===undefined?{}:{'Content-Type':'application/json'})},body:data===undefined?undefined:JSON.stringify(data)});const result=await response.json();if(response.status===403&&!revalidated&&result.error?.includes('刷新页面')){await refreshLocalToken();return api(path,data,true);}if(!response.ok){const error=Error(result.error||`请求失败 ${response.status}`);error.status=response.status;error.delivery=result.delivery;throw error;}return result;}
function toast(message){$('#toast').textContent=message;$('#toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').hidden=true,5500);}
function safe(fn){return async(...args)=>{try{return await fn(...args);}catch(error){toast(error.message);console.error(error);}};}
function date(value){return new Date(value).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});}
const labels={idle:'就绪',starting:'正在开始',running:'执行中',waiting_approval:'等待确认',compacting:'压缩中',interrupted:'已中断',failed:'执行失败',unknown:'待核对',disconnected:'连接已断开',completed:'已完成',proposed:'提案',recorded:'已记录',review_approved:'审核通过',request_changes:'退回修改'};
const kindLabels={question:'研究问题',alignment:'需求共识',research:'资料调研',plan:'研究方案',branch:'探索分支',trial:'实验',result:'结果',decision:'研究决策',review:'轮次评审',delivery:'交付'};
function renderNavigation(){
  projectSwitcher.render();
  const nav=$('#project-list');nav.innerHTML='';
  for(const project of state.projects){const group=document.createElement('section');group.innerHTML=`<div class="project-row" title="${esc(project.title)}">${icon('folder')}<strong>${esc(project.title)}</strong>${project.pinned?`<span class="project-pin" title="已置顶">${icon('pin')}</span>`:''}<button title="新建会话" aria-label="新建会话">${icon('plus')}</button></div>`;
    group.querySelector('button').onclick=safe(()=>createSession(project.id));
    const menu=document.createElement('button');menu.className='project-menu';menu.title='项目选项';menu.setAttribute('aria-label','项目选项：'+project.title);menu.textContent='⋯';menu.onclick=e=>{e.stopPropagation();projectRemoval.open(project,menu);};const projectRow=group.querySelector('.project-row');projectRow.insertBefore(menu,projectRow.querySelector('button'));projectRow.oncontextmenu=e=>{e.preventDefault();projectRemoval.open(project,projectRow);};
    group.querySelector('.project-row').onclick=e=>{if(!e.target.closest('button')){const session=state.sessions.find(s=>s.project_id===project.id);safe(()=>session?selectSession(session.id):createSession(project.id))();}};
    for(const session of state.sessions.filter(s=>s.project_id===project.id)){const row=document.createElement('div');row.className='session-row'+(session.id===current?' selected':'');row.tabIndex=0;row.title=session.title;row.innerHTML=`<span>${esc(session.title)}</span>${['running','starting','waiting_approval'].includes(session.status)?'<i></i>':''}<button data-rename title="重命名会话" aria-label="重命名会话">···</button><button data-delete title="删除会话" aria-label="删除会话">×</button><button data-archive title="归档会话" aria-label="归档会话">${icon('archive')}</button>`;row.onclick=safe(()=>selectSession(session.id));row.onkeydown=e=>{if(e.target===row&&e.key==='Enter')row.click();};row.querySelector('[data-rename]').onclick=e=>{e.stopPropagation();sessionNameEditor.open(session);};row.querySelector('[data-delete]').onclick=e=>{e.stopPropagation();projectRemoval.deleteSession(session);};row.querySelector('[data-archive]').onclick=safe(async e=>{e.stopPropagation();if(!confirm('归档此会话？原始记录会保留。'))return;await saveDraft();await api(`/api/sessions/${session.id}/archive`,{});await refreshState();});group.append(row);}nav.append(group);
  }
}
function clearSelectedSession(note){
  clearTimeout(draftTimer);clearTimeout(reloadTimer);sessionGeneration++;compositionRevision++;detailRequest++;researchUI.selectionChanged?.();if(unknownDialog.open)unknownDialog.close();conversationView.reset();current=null;detail=null;draftDirty=false;draftRevision=0;attachments=[];location.hash='';
  for(const id of ['file-dialog','graph-dialog','execution-dialog'])if($('#'+id).open)$('#'+id).close();
  graph?.destroy();fullGraph?.destroy();graph=null;fullGraph=null;graphSignature='';$('#graph-host').replaceChildren();
  $('#prompt').value='';resizeComposer($('#prompt'));renderAttachments();$('#messages').innerHTML=`<p class="empty-note">${esc(note)}</p>`;$('#session-title').textContent='新的研究';$('#session-status').textContent='就绪';$('#session-status').classList.remove('busy');$('#composer-hint').textContent='Enter 发送 · Shift+Enter 换行';$('#stop').hidden=true;
  for(const id of ['session-runs','project-run-results','tab-execution','tab-experiments','file-tree','file-preview','queue'])$('#'+id).replaceChildren();
  $('#session-runs').hidden=true;$('#selected-record').hidden=true;$('#file-preview').hidden=true;$('#file-root').textContent='工作区文件';updateSendButton();
}
async function refreshState(){state=await api('/api/state');if(current&&!state.sessions.some(s=>s.id===current))clearSelectedSession('当前会话已归档，或项目已移除，可在“归档与恢复”中找回。');renderNavigation();renderConnection();if(!current)renderHome();}
async function createSession(projectId){const session=await api('/api/sessions',{project_id:projectId});await refreshState();await selectSession(session.id);}
async function selectSession(id){
  if(current===id&&detail)return;
  document.body.classList.remove('home-page');
  $('#research-status-stack').hidden=false;
  const generation=++sessionGeneration;projectSwitcher.close();researchUI.selectionChanged?.();if(unknownDialog.open)unknownDialog.close();clearTimeout(reloadTimer);
  await saveDraft();if(generation!==sessionGeneration)return;
  current=id;historyFloor=null;historyLoading=false;detail=null;draftDirty=false;draftRevision=0;compositionRevision++;attachments=[];conversationView.reset();$('#prompt').value='';renderAttachments();renderQueue();updateSendButton();graphSignature='';location.hash=`session/${id}`;
  $('#session-runs').hidden=true;$('#session-runs').replaceChildren();$('#project-run-results').replaceChildren();$('#tab-execution').replaceChildren();
  await loadCurrent(true);if(generation!==sessionGeneration||current!==id)return;
  renderNavigation();if(!projectSwitcher.isOpen())$('#prompt').focus();if(innerWidth<1000)document.body.classList.add('inspector-hidden');
}
async function loadCurrent(initial=false){
  if(!current)return;const id=current,generation=sessionGeneration,request=++detailRequest,result=await api(`/api/sessions/${id}${historyFloor?'?history_start='+historyFloor:''}`);
  if(current!==id||generation!==sessionGeneration||request!==detailRequest)return;
  const cursor=result.cursor;
  if(detail?.cursor!=null&&cursor!=null&&detail.cursor>cursor)return;
  detail=result;messagesByItem.clear();for(const message of result.messages)messagesByItem.set(message.item_id,message);
  $('#session-title').textContent=result.session.title;$('#file-root').textContent=result.session.project_title.toUpperCase();
  const controls=result.delivery_controls,busy=['running','starting','compacting','waiting_approval'].includes(result.session.status),life=result.research_lifecycle;
  $('#session-status').textContent=life&&!['idle','working'].includes(life.state)?life.label:labels[result.session.status]||result.session.status;
  $('#session-status').title=life?.reason||'';$('#session-status').classList.toggle('busy',busy);$('#stop').hidden=!(busy||controls?.requested_delivery_ids?.length);
  $('#composer-hint').textContent=controls?.barrier?'原请求／中断待核对 · 不重发':busy?'Enter 排队 · Ctrl+Enter 引导':'Enter 发送 · Shift+Enter 换行';
  if(initial){
    const local=localStorage.getItem(`iterlab-codex:draft:${id}`),attempt=deliverySender.pending(id);
    $('#prompt').value=local??attempt?.prompt??result.draft.text;draftRevision=result.draft.revision;draftDirty=$('#prompt').value!==result.draft.text;
    if(attempt&&$('#prompt').value===attempt.prompt){attachments=(attempt.attachments||[]).map(a=>({...a}));renderAttachments();}
  }else if(!draftDirty&&document.activeElement!==$('#prompt')){if($('#prompt').value!==result.draft.text)compositionRevision++;$('#prompt').value=result.draft.text;draftRevision=result.draft.revision;}
  renderMessages();renderQueue();updateSendButton();renderExperiments();researchUI.decorateExperiments($('#tab-experiments'));resizeComposer($('#prompt'));await renderGraph();
  if(current!==id||generation!==sessionGeneration||request!==detailRequest)return;
  renderApproval();
  if(['execution','runs'].includes(currentTab))await companyUI.render();else await companyUI.renderRuns();
}
function scheduleReload(){clearTimeout(reloadTimer);reloadTimer=setTimeout(()=>safe(loadCurrent)(),200);}
function renderMessages(){if(detail)conversationView.render(detail,current);}
async function loadOlder(){
  if(!detail?.history?.has_more||historyLoading)return;
  const sid=current,generation=sessionGeneration,before=detail.history.start_position;
  historyLoading=true;detail.history.loading=true;renderMessages();
  try{
    const page=await api(`/api/sessions/${sid}?before=${before}`);
    if(sid!==current||generation!==sessionGeneration)return;
    const messages=new Map(page.messages.map(item=>[item.id,item]));
    for(const item of detail.messages)messages.set(item.id,item);
    detail.messages=[...messages.values()];detail.history=page.history;historyFloor=page.history.start_position;
    detailRequest++; // An older in-flight refresh must not discard the loaded page.
    renderMessages();
    if(page.cursor>detail.cursor)scheduleReload();
  }finally{if(sid===current&&generation===sessionGeneration){historyLoading=false;if(detail?.history)detail.history.loading=false;renderMessages();}}
}
$('#messages').addEventListener('scroll',()=>{if($('#messages').scrollTop<100&&detail?.history?.has_more&&!historyLoading)safe(loadOlder)();},{passive:true});

function renderHome(){
  if(current)return;
  document.body.classList.add('home-page');
  const draft=$('#prompt').value||localStorage.getItem('iterlab-codex:home-draft')||'';
  $('#session-title').textContent='新的研究，从一个问题开始';$('#research-status-stack').hidden=true;
  $('#messages').innerHTML=homeContent(state.projects,state.sessions);
  for(const row of $$('[data-home-session]'))row.onclick=safe(()=>selectSession(row.dataset.homeSession));
  $('#prompt').value=draft;resizeComposer($('#prompt'));
}
function openExecutionLog(){const host=$('#execution-content');host.innerHTML='';for(const message of detail?.messages||[]){if(message.role!=='tool'||message.kind==='output')continue;const row=document.createElement('details');row.className='technical-record';const title={commandExecution:'终端命令',fileChange:'文件修改',mcpToolCall:'MCP 工具',dynamicToolCall:'研究工具',contextCompaction:'上下文压缩',plan:'执行计划'}[message.kind]||message.kind;row.innerHTML=`<summary>${esc(title)} <time>${date(message.created_at)}</time></summary><pre>${esc(message.text)}</pre>`;host.append(row);}$('#execution-dialog').showModal();}
function openUnknownPause(sid,item,generation){
  if(sid!==current||generation!==sessionGeneration||pauseSubmitting)return;
  pausePreview={sid,id:item.id,generation};unknownDialog.querySelector('[data-identity]').textContent=systemDelivery(item)?'系统投递待核对':'原投递ID：'+item.id;
  const form=unknownDialog.querySelector('form');form.reset();if(!unknownDialog.open)unknownDialog.showModal();
}
function updateSendButton(){
  let pending=false;try{pending=Boolean(deliverySender.pending(current));}catch{pending=true;}
  $('#send').disabled=deliverySender.busy()||pending||Boolean(detail?.delivery_controls?.barrier);
  renderOverview();
}
function systemDelivery(item){return ['background','review'].includes(detail?.delivery_origins?.[item.id]);}
function renderQueue(){
  const host=$('#queue'),sid=current,generation=sessionGeneration;host.replaceChildren();if(!sid)return;
  const button=(row,label,action)=>{const b=document.createElement('button');b.textContent=label;b.title=label;b.disabled=deliverySender.busy();b.onclick=safe(async()=>{if(sid!==current||generation!==sessionGeneration)return;await action(b);});row.append(b);return b;};
  let attempt=null;try{attempt=deliverySender.pending(sid);}catch(error){const note=document.createElement('p');note.className='connection-status';note.textContent=error.message;host.append(note);return;}
  if(attempt){
    const row=document.createElement('div');row.className='queue-row error';const text=document.createElement('span');
    text.textContent=attempt.state==='registered'?'原发送已登记 · 草稿仍保留，请核对后结束跟踪':'HTTP发送结果未确认 · 保留原ID与草稿，不重发';text.title='原ID：'+attempt.id+(attempt.error?'\n'+attempt.error:'');row.append(text);
    button(row,'核对原请求',async()=>{const result=await deliverySender.inspect(sid);if(sid!==current||generation!==sessionGeneration)return;toast(result?.delivery?.state==='unknown'?'原生结果仍未知；未重发':result?.cleared?'原请求已核对，原草稿已清空':'原请求状态：'+(result?.delivery?.state||'待核对')+'；草稿保留');await loadCurrent();});
    if(attempt.state==='registered')button(row,'结束已登记跟踪',async()=>{if(!confirm('原请求已确切登记。这只结束浏览器跟踪，保留当前草稿；不要将相同内容重复发送。'))return;deliverySender.release(sid,true);updateSendButton();renderQueue();});
    host.append(row);
  }
  const controls=detail?.delivery_controls;
  for(const intent of controls?.interrupt_intents||[]){if(intent.state==='completed')continue;const note=document.createElement('p');note.className='connection-status';note.textContent='定向中断已保存 · '+({pending:'等待原turn身份',ready:'等待发送原turn中断',requested:'等待原中断回执',acknowledged:'等待原turn结束',unknown:'原中断结果未知，不重放',failed:'原中断未完成，需核对'}[intent.state]||intent.state);note.title=intent.reason;host.append(note);}
  for(const item of detail?.deliveries||[]){
    const internal=systemDelivery(item);
    if(internal&&!['unknown','rejected'].includes(item.state))continue;
    const row=document.createElement('div');row.className='queue-row'+(['unknown','rejected'].includes(item.state)?' error':'');
    row.dataset.deliveryId=item.id;
    const label=({queued:'排队',requested:'已请求，等待确认',unknown:'状态未知，请核对',rejected:'确切拒绝'})[item.state]||item.state;
    row.innerHTML=`${icon('branch')}<span title="${esc(internal?'':item.error||item.text)}">${esc(internal?'系统投递 · '+label:label+' · '+item.text)}</span>`;
    if(item.state==='queued'&&!internal){button(row,'立即引导',async()=>{await api(`/api/sessions/${sid}/steer-queued`,{id:item.id});await loadCurrent();});}
    if(item.state==='rejected'){button(row,'移除提示',async()=>{await api(`/api/sessions/${sid}/dismiss-delivery`,{id:item.id});await loadCurrent();});}
    if(item.state==='queued'){const b=button(row,'删除排队',async button=>{button.disabled=true;try{await api(`/api/sessions/${sid}/cancel`,{id:item.id});if(current===sid&&generation===sessionGeneration)await loadCurrent();}finally{button.disabled=false;}});b.dataset.cancel=item.id;}
    if(item.state==='unknown'){
      button(row,'核对原生身份',async button=>{button.disabled=true;try{await api(`/api/sessions/${sid}/restore`,{});if(current===sid&&generation===sessionGeneration)await loadCurrent();}finally{button.disabled=false;}});
      const paused=controls?.human_pauses?.some(p=>p.delivery_id===item.id);
      button(row,paused?'原未知已保留并暂停':'保留未知并暂停',()=>openUnknownPause(sid,item,generation)).disabled=paused||pauseSubmitting;
    }
    host.append(row);
  }
}
async function saveDraft(){
  clearTimeout(draftTimer);if(!current||!draftDirty)return;const id=current,generation=sessionGeneration,text=$('#prompt').value,revision=draftRevision,composition=compositionRevision;
  try {const result=await api(`/api/sessions/${id}/draft`,{text,revision});if(id!==current||generation!==sessionGeneration)return;if(result.conflict){toast('另一页面更新了草稿，当前输入仍保存在此浏览器，请核对后再发送');return;}draftRevision=Math.max(draftRevision,result.revision);if(composition===compositionRevision&&$('#prompt').value===text){draftDirty=false;localStorage.removeItem(`iterlab-codex:draft:${id}`);}}
  catch{/* Local fallback already holds the unsaved draft. */}
}
async function commitSentComposition(captured){
  if(!sameComposition(captured,composition()))return false;
  $('#prompt').value='';attachments=[];compositionRevision++;draftDirty=true;
  localStorage.setItem(`iterlab-codex:draft:${captured.sid}`,'');renderAttachments();resizeComposer($('#prompt'));$('#slash-menu').hidden=true;
  await saveDraft();return true;
}
function renderAttachments(){$('#attachment-list').innerHTML='';attachments.forEach((a,i)=>{const row=document.createElement('span');row.className='attachment';row.innerHTML=`${icon('file')} ${esc(a.name)} <button aria-label="移除附件">${icon('close')}</button>`;row.querySelector('button').onclick=()=>{attachments.splice(i,1);compositionRevision++;renderAttachments();};$('#attachment-list').append(row);});}
async function send(mode='queue'){
  if(deliverySender.busy())return;
  if(!current){
    const text=$('#prompt').value.trim();if(!text)return;
    pendingHomeStart=text;$('#project-dialog').showModal();
    return;
  }
  const text=$('#prompt').value.trim();if((text==='/skills'||/^\/mcp$/i.test(text))&&!attachments.length){await nativePanel(text);return;}
  if(detail?.delivery_controls?.barrier)throw Error('原请求或中断尚待核对；不会开启替代轮次');
  const sid=current,generation=sessionGeneration;const result=await deliverySender.send(mode);
  if(!result.empty&&!result.duplicate&&current===sid&&generation===sessionGeneration)await loadCurrent();
}
async function ensureGraph(){if(window.IterLabExplorationMap)return;if(!graphLoad)graphLoad=new Promise((resolve,reject)=>{const script=document.createElement('script');script.src=(assetRevision?`/assets/${assetRevision}`:'')+'/vendor/exploration-map.js';script.onload=resolve;script.onerror=reject;document.head.append(script);});await graphLoad;}
let renderedGraphMode=null,renderedGraphGeneration=null;
async function renderGraph(){
  if(!detail)return;const selected=detail,generation=sessionGeneration;await ensureGraph();if(detail!==selected||generation!==sessionGeneration)return;
  const object=currentObject(detail),data=overview.mode==='focus'?focusGraph(detail.graph,object?.id):detail.graph,signature=JSON.stringify([data,overview.mode]);if(signature===graphSignature)return;graphSignature=signature;
  const first=!graph,options={compact:true,compactDetails:false,data,onSelect:node=>overview.browse(node.id),onDeselect:()=>overview.browse(null),onExpand:()=>openGraph()};
  if(first)graph=window.IterLabExplorationMap.mount($('#graph-host'),options);else graph.update(data);
  if(first)graph.attachModeControl(document.querySelector('.graph-mode-switch'));
  if(renderedGraphGeneration!==generation)graph.clearSelection();
  if(first||renderedGraphMode!==overview.mode||renderedGraphGeneration!==generation)graph.setSpineOnly(overview.mode!=='full');
  if(first||renderedGraphMode!==overview.mode||renderedGraphGeneration!==generation){if(object&&overview.mode==='focus'){graph.clearSelection();graph.frameContext(object.id);}else if(overview.mode==='full'){graph.clearSelection();graph.fit();}}
  renderedGraphMode=overview.mode;renderedGraphGeneration=generation;
  if(fullGraph)fullGraph.update(detail.graph);
}
async function openGraph(nodeId){const selected=detail,generation=sessionGeneration;if(!selected)return;await ensureGraph();if(detail!==selected||generation!==sessionGeneration)return;$('#graph-dialog').showModal();fullGraph?.destroy();fullGraph=window.IterLabExplorationMap.mount($('#graph-full'),{data:selected.graph,renderMarkdown:markdown,loadDetail:id=>api(`/api/records/${id}`),onOpenSource:(source,node)=>{if(generation!==sessionGeneration)return;const r=detail.records.find(r=>r.id===node.id);if(r){$('#graph-dialog').close();selectSession(r.session_id);}}});if(nodeId)fullGraph.reveal(nodeId);setTimeout(()=>fullGraph?.resize(),50);}
function renderExperiments(){const host=$('#tab-experiments');const records=detail?.records.filter(r=>['trial','result','decision','review'].includes(r.kind))||[];host.innerHTML='<div class="company-section-header"><h3>实验与方案审核</h3><button class="secondary" data-plan-review>审核具体方案</button></div>'+(records.length?'':'<div class="empty-note">实际实验、结果和评审会记录在这里。</div>');host.querySelector('[data-plan-review]').onclick=researchUI.open;for(const r of records){const card=document.createElement('article'),status=r.current_status||r.status;card.className='record-card';card.innerHTML=`<header><span>${esc(kindLabels[r.kind])}</span><span>${r.current_status?'当前：':'保存时：'}${esc(labels[status]||status)}</span></header><h3>${esc(r.title)}</h3><small class="muted">记录于 ${date(r.created_at)}</small><p>${esc(r.summary)}</p><button class="text-button">查看方案与证据 →</button>`;card.querySelector('button').onclick=()=>openGraph(r.id);if(r.kind==='review'&&JSON.parse(r.source).round_outcome==='stop_requested'&&JSON.parse(r.source).closure_mode!=='autonomous'&&!(detail.research_lifecycle?.state==='assessment_complete'&&detail.research_lifecycle.subject_id===r.id)){const button=document.createElement('button');button.className='secondary';button.textContent='审核结束建议';button.onclick=()=>researchUI.openRoundStop(r.id);card.append(button);}host.append(card);}}
async function compact(){if(!current)return;if(deliverySender.pending(current)||deliverySender.busy())throw Error('请先核对原发送请求');if(attachments.length||!/^\/[a-z]*$/i.test($('#prompt').value))throw Error('请先保留当前草稿和附件，再单独发送 /compact');$('#prompt').value='/compact';compositionRevision++;draftDirty=true;localStorage.setItem(`iterlab-codex:draft:${current}`,$('#prompt').value);await send();toast('已登记 Codex 原生压缩请求；执行中排队，不创建替代轮次');}
async function loadFiles(){if(!detail)return;$('#file-root').textContent=detail.session.project_title.toUpperCase();$('#file-tree').innerHTML='';await appendDirectory($('#file-tree'),'');}
async function appendDirectory(container,path){const result=await api(`/api/projects/${detail.session.project_id}/files?path=${encodeURIComponent(path)}`);for(const item of result.items){const row=document.createElement('button');row.className='file-row';row.innerHTML=`${icon(item.directory?'folder':'file')}<span>${esc(item.name)}</span>`;container.append(row);if(item.directory){const children=document.createElement('div');children.className='file-children';children.hidden=true;container.append(children);row.onclick=safe(async()=>{children.hidden=!children.hidden;if(!children.hidden&&!children.childNodes.length)await appendDirectory(children,item.path);});}else row.onclick=safe(()=>openFile(item.path));}}
let filePreviewRequest=0;
async function openFile(path){if(!detail)return;if(currentTab!=='files')switchTab('files');const projectId=detail.session.project_id,sid=current,generation=sessionGeneration,request=++filePreviewRequest,result=await api(`/api/projects/${projectId}/file?path=${encodeURIComponent(path)}`);if(detail?.session.project_id!==projectId||current!==sid||generation!==sessionGeneration||request!==filePreviewRequest)return;const host=$('#file-preview');host.hidden=false;host.innerHTML=`<header>${icon('file')} ${esc(path)}</header>`;if(result.binary){const note=document.createElement('p');note.className='file-note';note.textContent=`二进制文件 · ${result.size.toLocaleString()} 字节`;host.append(note);}else{const isMarkdown=/\.md$/i.test(result.name);const content=document.createElement(isMarkdown?'article':'pre');if(isMarkdown){content.className='markdown-body';content.innerHTML=markdown(result.text);}else content.textContent=result.text;host.append(content);}}
function switchTab(name){if(name==='execution'){name='runs';$('#run-config').open=true;}currentTab=name;$$('.tabs button').forEach(b=>b.classList.toggle('active',b.dataset.tab===name));$$('.tab-view').forEach(v=>v.hidden=v.id!==`tab-${name}`);if(name==='files')safe(loadFiles)();if(name==='runs')companyUI.render();if(name==='graph')setTimeout(()=>graph?.resize(),0);}
function renderConnection(){renderOverview();const k=state.kernel||{},config=k.settings||{};$('#composer-model').innerHTML=esc(config.agent_roles?.models?.planner||'规划 / 执行')+icon('chevron');$('#connection-status').textContent=k.connected?`已连接 · ${config.channel==='ept'?'EPT Codex CLI':'Codex CLI'}`:'尚未连接';}
function populateRoleModels(channel=state.kernel.settings?.channel){
  const form=$('#settings-form'),config=state.kernel.settings||{},defaults=defaultModels(channel);
  const policy=channel===config.channel?config.agent_roles:null;
  const models=modelCatalogs.get(channel)||(channel===config.channel?(state.kernel.models||[]):[]);
  for(const [field,role,fallback] of [['planning_model','planner',defaults.planning],['execution_model','executor',defaults.execution]]){
    const select=form.elements[field],saved=policy?.models?.[role]||fallback;
    select.innerHTML=models.map(m=>`<option value="${esc(m.model||m.id)}">${esc(m.displayName||m.model||m.id)}</option>`).join('');
    if(!models.some(m=>(m.model||m.id)===saved)){const option=new Option(saved+(modelCatalogs.has(channel)?'（当前不可用，请重新选择）':'（正在读取）'),saved);option.disabled=modelCatalogs.has(channel);select.add(option);}
    select.value=saved;
  }
}
function populateModels(){const form=$('#settings-form');form.elements.channel.value=state.kernel.settings?.channel||'codex';populateRoleModels();populateEfforts();}
function populateEfforts(){
  updateModelSaveButton();
}
function updateModelSaveButton(){
  const form=$('#settings-form');
  form.querySelector('[type=submit]').disabled=form.dataset?.catalogPending==='true'||['planning_model','execution_model'].some(field=>{const select=form.elements[field];return select.disabled||!select.value||!select.selectedOptions[0]||select.selectedOptions[0].disabled;});
}
const modelCatalogs=new Map(),modelRequests=new Map();let modelLoadGeneration=0;
async function refreshModelCatalog(channel=$('#settings-form').elements.channel.value){
  const generation=++modelLoadGeneration,form=$('#settings-form');
  let loaded=false;
  const known=modelCatalogs.get(channel)||(channel===state.kernel.settings?.channel?state.kernel.models:[])||[];
  form.dataset.catalogPending='true';
  $('#model-catalog-status').textContent=known.length?'先显示上次读取的列表，正在更新；核对完成后可保存。':'正在连接并更新模型列表…';
  for(const field of ['planning_model','execution_model'])form.elements[field].disabled=!known.length;
  updateModelSaveButton();
  if(!modelRequests.has(channel))modelRequests.set(channel,api('/api/models',{channel}).finally(()=>modelRequests.delete(channel)));
  try{const result=await modelRequests.get(channel);modelCatalogs.set(channel,result.models||[]);
    if(generation!==modelLoadGeneration||form.elements.channel.value!==channel)return;
    const choices=Object.fromEntries(['planning_model','execution_model'].map(field=>[field,form.elements[field].value]));
    loaded=true;populateRoleModels(channel);
    for(const [field,value] of Object.entries(choices))if(value){
      const select=form.elements[field];
      if(!(result.models||[]).some(model=>(model.model||model.id)===value)&&known.length){const option=new Option(value+'（当前不可用，请重新选择）',value);option.disabled=true;select.add(option);}
      if([...select.options].some(option=>option.value===value))select.value=value;
    }
    populateEfforts();$('#model-catalog-status').textContent=result.models?.length?`${result.stale?'暂用上次列表：':'当前 CLI 返回 '}${result.models.length} 个模型`:'此连接未返回可用模型';
  }catch(error){if(generation===modelLoadGeneration)$('#model-catalog-status').textContent='模型列表读取失败：'+(error.message==='接口不存在'?'当前页面与本地服务版本不匹配，请更新服务并刷新原页面；账号配置没有改变。':/timed out|超时/i.test(error.message)?'官方 CLI 启动或读取超时，请稍后重新打开。未创建或重复提交研究轮次，未切换渠道。':error.message);
  }finally{if(generation===modelLoadGeneration){form.dataset.catalogPending=String(!loaded);for(const field of ['planning_model','execution_model']){form.elements[field].disabled=!loaded&&!known.length;if(!loaded)for(const option of form.elements[field].options)option.text=option.text.replace('（正在读取）','（读取失败）');}updateModelSaveButton();}}
}
function openSettings(){populateModels();$('#settings-dialog').showModal();refreshModelCatalog();}
function renderApproval(targetId){const pending=typeof targetId==='string'?detail?.approvals?.find(item=>item.id===targetId):detail?.approvals?.[0];if(!pending){if($('#approval-dialog').open)$('#approval-dialog').close();approvalId=null;return;}if(approvalId===pending.id)return;approvalId=pending.id;const p=JSON.parse(pending.payload),questions=p.questions,body=$('#approval-body'),actions=$('#approval-actions');body.innerHTML='';actions.innerHTML='';$('#approval-title').textContent=questions?'Codex 需要补充信息':'Codex 请求操作确认';
  if(questions){for(const q of questions){const label=document.createElement('label');label.textContent=q.question;const input=document.createElement('textarea');input.dataset.question=q.id;label.append(input);body.append(label);for(const option of q.options||[]){const choice=document.createElement('button');choice.className='secondary';choice.textContent=option.label;choice.title=option.description||'';choice.onclick=()=>input.value=option.label;body.append(choice);}}const submit=document.createElement('button');submit.className='primary';submit.textContent='提交答复';submit.onclick=safe(async()=>{const answers=Object.fromEntries([...body.querySelectorAll('[data-question]')].map(i=>[i.dataset.question,i.value]));await api(`/api/approvals/${pending.id}/answer`,{answers});approvalId=null;await loadCurrent();});actions.append(submit);}
  else {const explanation=document.createElement('p');explanation.className='muted';explanation.textContent=p.reason||p.message||'请核对本次操作。此确认只回复当前 Codex 请求。';body.append(explanation);const command=document.createElement('pre');command.className='approval-command';command.textContent=p.command||p.proposedExecpolicyAmendment?.join(' ')||p.cwd||'工具请求扩展操作权限';body.append(command);for(const [decision,label] of [['decline','拒绝'],['accept','允许本次']]){const button=document.createElement('button');button.className=decision==='accept'?'primary':'secondary';button.textContent=label;button.onclick=safe(async()=>{await api(`/api/approvals/${pending.id}/answer`,{decision});approvalId=null;await loadCurrent();});actions.append(button);}}
  if(!$('#approval-dialog').open)$('#approval-dialog').showModal();
}
async function nativePanel(command){
  if(!current)return;const sid=current,generation=sessionGeneration,isSkills=command==='/skills',method=isSkills?'skills/list':'mcpServerStatus/list';
  $('#native-title').textContent=isSkills?'Skills':'Codex MCP 连接';const host=$('#native-content');host.innerHTML='<p class="muted">正在读取当前项目的原生配置…</p>';if(!$('#native-dialog').open)$('#native-dialog').showModal();
  const result=await api(`/api/sessions/${sid}/native`,{method});if(sid!==current||generation!==sessionGeneration)return;
  host.replaceChildren();
  if(isSkills){
    const note=document.createElement('p');note.className='muted';note.textContent='项目 Skills 安装到：'+result.project_skill_directory+'。全局 Skills 沿用当前 CLI 的配置。点击“使用”将 $名称 插入输入框，由 Codex 按需加载；不会自动执行。';host.append(note);
    const form=document.createElement('form');form.className='skill-import';
    form.innerHTML='<label>导入本机 Skill 目录<input name="source" placeholder="含 SKILL.md 的目录绝对路径" required></label><button class="secondary" type="submit">导入到此项目</button>';
    form.onsubmit=safe(async e=>{e.preventDefault();const button=form.querySelector('button');button.disabled=true;try{await api(`/api/sessions/${sid}/import-skill`,{source:form.elements.source.value});toast('已导入到项目；正在重新发现');await nativePanel('/skills');}finally{button.disabled=false;}});host.append(form);
  }
  const entries=isSkills?(result.data||[]).flatMap(row=>row.skills||[]):result.data||[];
  if(!entries.length){const note=document.createElement('p');note.className='muted';note.textContent=isSkills?'当前尚无可用 Skill，可导入本机已有 Skill 目录。':'当前原生配置未返回 MCP 连接。';host.append(note);}
  for(const entry of entries){
    const row=document.createElement('article');row.className='native-entry';row.innerHTML=`<strong>${esc(entry.name||entry.id)}</strong><p>${esc(entry.description||entry.authStatus||'')}</p>${entry.path?`<details><summary>来源位置</summary><code>${esc(entry.path)}</code></details>`:''}`;
    if(isSkills){
      const use=document.createElement('button');use.className='secondary';use.textContent='使用';use.disabled=entry.enabled===false;
      use.onclick=()=>{$('#native-dialog').close();const input=$('#prompt');input.value='$'+entry.name+' '+input.value;input.dispatchEvent(new Event('input'));resizeComposer(input);input.focus();};row.append(use);
      const normalize=value=>String(value||'').replaceAll('\\','/').toLowerCase();
      if(normalize(entry.path).startsWith(normalize(result.project_skill_directory)+'/')){
        const disable=document.createElement('button');disable.className='secondary';disable.textContent='停用项目副本';disable.onclick=safe(async()=>{await api(`/api/sessions/${sid}/disable-skill`,{name:entry.name});await nativePanel('/skills');});row.append(disable);
      }
    }host.append(row);
  }
}
async function eventLoop(){
  while(true){
    try{
      const response=await fetch(`/api/events?after=${eventCursor}`,{headers:{'X-Workbench-Token':token}});
      if(response.status===403){await refreshLocalToken();continue;}
      if(!response.ok)throw Error('事件流断开');
      syncState('connected');
      const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';
      while(true){
        const {value,done}=await reader.read();if(done)break;
        syncState('connected');
        buffer+=decoder.decode(value,{stream:true});let split;
        while((split=buffer.indexOf('\n\n'))>=0){
          const line=buffer.slice(0,split);buffer=buffer.slice(split+2);
          if(!line.startsWith('data: '))continue;
          const event=JSON.parse(line.slice(6));eventCursor=Math.max(eventCursor,event.seq);
          if(event.kind==='item/agentMessage/delta'&&event.session_id===current){
            if(detail&&event.seq>(detail.cursor??0)){
              const item=messagesByItem.get(event.payload.itemId);
              if(item){item.text+=event.payload.delta||'';item.status='streaming';detail.cursor=event.seq;conversationView.queueDelta(item);}else scheduleReload();
            }
          }else if(event.session_id===current&&!['projection.updated','item/commandExecution/outputDelta'].includes(event.kind))scheduleReload();
          if(['session.created','session.renamed','session.archived','session.unarchived','settings.changed','project.created','project.updated','workbench.deleted','workbench.restored','kernel.connected','kernel.disconnected','turn/completed'].includes(event.kind))safe(refreshState)();
          if(event.kind==='project.updated'&&detail?.session.project_id===event.payload.id)scheduleReload();
          if(event.session_id!==current&&isProjectRuntimeEvent(event,detail?.session.project_id))scheduleReload();
        }
      }
    }catch(error){
      syncState('disconnected');
      $('#session-status').title='同步暂时断开，原记录保留；正在重新连接';
      await new Promise(r=>setTimeout(r,2500));
    }
  }
}
$('#toggle-research-stack').onclick=()=>{const key='iterlab:status-stack:collapsed';localStorage.setItem(key,localStorage.getItem(key)==='1'?'0':'1');renderStatusStack();};
$('#prompt').addEventListener('input',()=>{compositionRevision++;draftDirty=true;localStorage.setItem(current?`iterlab-codex:draft:${current}`:'iterlab-codex:home-draft',$('#prompt').value);clearTimeout(draftTimer);draftTimer=setTimeout(saveDraft,650);slashMenu.update();});
$('#prompt').addEventListener('keydown',e=>{if(slashMenu.keydown(e))return;if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();safe(()=>send(e.ctrlKey||e.metaKey?'steer':'queue'))();}if(e.key==='Escape')$('#slash-menu').hidden=true;});
const slashMenu=installSlashMenu({input:$('#prompt'),host:$('#slash-menu'),commands:[['/compact','压缩原生会话上下文'],['/skills','管理和调用项目 Skills'],['/mcp','查看原生 MCP 连接']],onCommand:safe(async command=>{const input=$('#prompt'),before=input.value,sid=current;if(command==='/compact')await compact();else await nativePanel(command);if(current===sid&&input.value===before&&/^\/[a-z]*$/i.test(before)){input.value='';input.dispatchEvent(new Event('input'));resizeComposer(input);}})});
$('#send').onclick=safe(()=>send());let interruptSubmitting=false;$('#stop').onclick=safe(async()=>{if(interruptSubmitting||!current)return;const sid=current,generation=sessionGeneration;interruptSubmitting=true;$('#stop').disabled=true;try{await api(`/api/sessions/${sid}/interrupt`,{});toast('定向中断已保存，排队已取消；原请求未知时不会重发，云任务未停止');if(current===sid&&generation===sessionGeneration)await loadCurrent();}finally{interruptSubmitting=false;$('#stop').disabled=false;}});
$('#new-project').onclick=()=>{pendingHomeStart=null;$('#project-dialog').showModal();};
$('#project-form').onsubmit=safe(async e=>{e.preventDefault();const initial=pendingHomeStart;pendingHomeStart=null;const form=e.target,project=await api('/api/projects',{title:form.elements.title.value,workspace:form.elements.workspace.value||null});$('#project-dialog').close();form.reset();await createSession(project.id);if(initial){$('#prompt').value=initial;$('#prompt').dispatchEvent(new Event('input'));localStorage.removeItem('iterlab-codex:home-draft');await send();}});
$('#project-dialog').addEventListener('close',()=>{pendingHomeStart=null;});
const homeLink=$('.sidebar .brand');homeLink.setAttribute('role','button');homeLink.setAttribute('tabindex','0');homeLink.setAttribute('aria-label','研究首页');
homeLink.onclick=safe(async()=>{await saveDraft();clearSelectedSession('');historyFloor=null;historyLoading=false;renderHome();renderNavigation();});
homeLink.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();homeLink.click();}};
$$('.close-dialog').forEach(button=>button.onclick=()=>button.closest('dialog').close());
$('#theme').onclick=()=>{const theme=document.documentElement.dataset.theme==='dark'?'light':'dark';document.documentElement.dataset.theme=theme;localStorage.setItem('iterlab-codex:theme',theme);};
document.documentElement.dataset.theme=localStorage.getItem('iterlab-codex:theme')||'light';
$('#settings').onclick=companyUI.open;$('#models').onclick=openSettings;$('#composer-model').onclick=openSettings;
$('#settings-form').elements.channel.onchange=()=>{populateRoleModels($('#settings-form').elements.channel.value);populateEfforts();refreshModelCatalog();};
for(const field of ['planning_model','execution_model'])$('#settings-form').elements[field].onchange=populateEfforts;
$('#settings-form').onsubmit=safe(async e=>{e.preventDefault();const form=e.target;if(form.dataset.catalogPending==='true')throw new Error('模型列表仍在核对，请稍候再保存');for(const field of ['planning_model','execution_model'])if(form.elements[field].disabled||!form.elements[field].value||form.elements[field].selectedOptions[0]?.disabled)throw new Error('请等待模型列表读取完成，并选择可用模型');state.kernel=await api('/api/settings',{channel:form.elements.channel.value,model:null,effort:null,agent_roles:rolePolicy(form.elements.planning_model.value,form.elements.execution_model.value)});renderConnection();toast('已保存，后续轮次使用新配置');$('#settings-dialog').close();});
$('#toggle-sidebar').onclick=()=>setSidebarCollapsed(!document.body.classList.contains('sidebar-collapsed'));$('#toggle-inspector').onclick=()=>{document.body.classList.toggle('inspector-hidden');setTimeout(()=>graph?.resize(),0);};
if(localStorage.getItem('iterlab-codex:collapsed')==='1'||innerWidth<650)document.body.classList.add('sidebar-collapsed');if(innerWidth<1000)document.body.classList.add('inspector-hidden');updateSidebarToggle();
for(const [selector,variable,min,max] of [['.left-resize','--sidebar-width',180,360],['.right-resize','--inspector-width',310,850]]){$(selector).onpointerdown=e=>{if(selector==='.left-resize'&&document.body.classList.contains('sidebar-collapsed'))return;const start=e.clientX,original=parseFloat(getComputedStyle(document.documentElement).getPropertyValue(variable));e.target.setPointerCapture(e.pointerId);e.target.onpointermove=move=>{const size=Math.max(min,Math.min(max,original+(selector==='.left-resize'?1:-1)*(move.clientX-start)));document.documentElement.style.setProperty(variable,size+'px');localStorage.setItem('iterlab-codex:'+variable,String(size));};e.target.onpointerup=()=>{e.target.onpointermove=null;};};const saved=localStorage.getItem('iterlab-codex:'+variable);if(saved)document.documentElement.style.setProperty(variable,saved+'px');}
$$('.tabs button').forEach(button=>button.onclick=()=>switchTab(button.dataset.tab));$('#refresh-files').onclick=safe(loadFiles);
$('#attach').onclick=()=>$('#files-input').click();$('#files-input').onchange=safe(async e=>{
  if(!detail)return toast('请先创建研究项目');const sid=current,pid=detail.session.project_id,generation=sessionGeneration,files=[...e.target.files];e.target.value='';
  for(const file of files){if(current!==sid||generation!==sessionGeneration)return;if(file.size>16*1024*1024){toast(`${file.name} 超过16 MiB，请直接放入项目目录`);continue;}
    const binary=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});
    if(current!==sid||generation!==sessionGeneration)return;const a=await api(`/api/projects/${pid}/upload`,{name:file.name,content:binary});
    if(current!==sid||generation!==sessionGeneration){toast('附件已保存在原项目，未添加到新会话输入');return;}
    attachments.push(a);compositionRevision++;renderAttachments();
  }
});
document.addEventListener('click',safe(e=>{const record=e.target.closest('[data-record]');if(record){e.preventDefault();return openGraph(record.dataset.record);}const link=e.target.closest('[data-file]');if(link){e.preventDefault();link.closest('dialog')?.close();return openFile(link.dataset.file);}}));
window.addEventListener('hashchange',()=>{const id=location.hash.match(/^#session\/(.+)/)?.[1];if(id&&id!==current)safe(()=>selectSession(id))();});
$('#approval-dialog').addEventListener('cancel',e=>{e.preventDefault();toast('请明确答复本次请求，或中断会话');});
window.addEventListener('beforeunload',()=>{if(current&&draftDirty)localStorage.setItem(`iterlab-codex:draft:${current}`,$('#prompt').value);});
try{state=await api('/api/state');eventCursor=state.cursor;renderNavigation();renderConnection();const selected=location.hash.match(/^#session\/(.+)/)?.[1];if(selected)await selectSession(selected);else renderHome();eventLoop();}catch(error){syncState('disconnected');toast(error.message);}
