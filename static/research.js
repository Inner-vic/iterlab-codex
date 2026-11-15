import {escapeHTML as esc,markdown} from './markdown.js';
import {installAutonomyUI,scopeForm,readScope} from './autonomy.js';

export function installResearchUI({api,current,toast,refresh,selection}){
  const autonomyUI=installAutonomyUI({api,current,selection,toast,refresh});
  const safe=fn=>async(...args)=>{try{await fn(...args);}catch(e){toast(e.message);}};
  const stopDialog=document.createElement('dialog');stopDialog.id='research-round-stop-dialog';
  stopDialog.innerHTML='<div class="dialog-header"><h2>审核本轮结束建议</h2><button type="button" class="icon-button" data-close aria-label="关闭">×</button></div><article class="markdown-body"></article><p>确认结束仅收束研究轮次，不代表云端资源已停止或模型性能达标。退回后继续完善评审及下一方案，未经批准不开新一轮。</p><p data-status role="status"></p><form><label>审核理由<textarea name="reason" rows="2" required></textarea></label><div class="dialog-actions"><button type="button" class="secondary" data-reject>退回完善</button><button type="submit" class="primary">确认本轮结束</button></div></form>';
  document.body.append(stopDialog);
  const methodDialog=document.createElement('dialog');methodDialog.id='research-method-dialog';
  methodDialog.innerHTML='<div class="dialog-header"><h2>本项目研究方法</h2><button type="button" class="icon-button" data-close aria-label="关闭">×</button></div><p data-method></p><p>更新仅改变本项目后续轮次的研究合同，保留原生会话、旧方法和全部实验记录；不重置执行额度，不启动模型或训练。项目有在途请求时不能更新。</p><p data-status role="status"></p><form><label>更新理由<textarea name="reason" rows="2" required placeholder="说明采用当前内置合同的原因"></textarea></label><div class="dialog-actions"><button type="submit" class="primary">更新本项目研究方法</button></div></form>';
  document.body.append(methodDialog);
  const dialog=document.createElement('dialog');dialog.id='research-review-dialog';
  dialog.innerHTML='<div class="dialog-header"><h2>方案审核与运行范围</h2><button type="button" class="icon-button" data-close aria-label="关闭">×</button></div><form><label>具体方案版本<select name="plan_id"></select></label><article class="markdown-body" data-content></article><p class="muted">确认研究思路和执行位置；任务上限可在高级选项调整。</p><p data-budget></p><p data-status role="status"></p><label>审核备注（可选）<textarea name="reason" rows="2"></textarea></label><label class="check-label"><input type="checkbox" name="allow_local">明确允许本方案使用本地CPU（不会自动替代公司云端配置）</label><details><summary>任务上限与超时</summary><label>已安装的Python<input name="python"></label><label>累计任务提交上限<input name="max_runs" type="number" value="100" min="1" max="1000"></label><input name="max_fits" type="hidden" value="100"><label>单次超时（秒）<input name="timeout" type="number" value="120" min="1" max="86400"></label></details><div class="dialog-actions"><button type="button" class="secondary" data-reject>退回修改</button><button type="submit" class="primary">确认并开始</button></div></form>';
  document.body.append(dialog);const form=dialog.querySelector('form');
  const delegation=document.createElement('section');delegation.innerHTML=`<div data-scope-summary></div><details data-scope-options><summary>自主探索设置</summary><label class="check-label"><input type="checkbox" name="autonomous" checked>允许 IterLab 在已确认范围内自主探索</label><div data-scope hidden>${scopeForm('scope_')}</div></details>`;
  form.insertBefore(delegation,form.elements.reason.closest('label'));
  const environment=document.createElement('section');environment.innerHTML='<label>执行位置<select name="execution"><option value="local">本地 CPU</option><option value="lpai">LPAI 组件库</option></select></label><div data-cloud hidden><details data-component-options><summary>高级选项 · 复用已有组件</summary><label>组件 UID<input name="component_uid" placeholder="留空则在确认后自动创建"></label><button type="button" class="secondary" data-probe-component>核对已有组件</button><p data-component-preview></p><label>GPU 数量上限<input name="max_gpus" type="number" min="0" max="128" value="0"></label><label class="check-label"><input type="checkbox" name="allow_cloud">允许 IterLab 使用该组件提交任务</label></details></div>';
  environment.querySelector('select').innerHTML+='<option value="lpai_devspace">LPAI GPU 开发空间</option>';
  const spacePanel=document.createElement('div');
  spacePanel.innerHTML='<p data-space-description></p><p class="muted">确认后只在已绑定的开发空间运行。连接或 GPU 检查失败时会停止，不回到本地。</p>';
  environment.append(spacePanel);
  form.insertBefore(environment,form.querySelector('[data-budget]'));
  const automatic=document.createElement('p');automatic.setAttribute('data-auto-component','');automatic.setAttribute('role','status');environment.querySelector('[data-cloud]').insertBefore(automatic,environment.querySelector('details'));
  const resourceInfo=document.createElement('p');resourceInfo.className='small';environment.querySelector('[data-cloud]').insertBefore(resourceInfo,automatic);
  const taskChanges=document.createElement('label');taskChanges.className='check-label';
  taskChanges.innerHTML='<input type="checkbox" name="allow_task_changes">允许AI在本组件中修改训练入口/参数和固定源码提交；每次冻结实际工作包，GPU、镜像、挂载及环境权限保持不变';
  environment.querySelector('[data-component-options]').append(taskChanges);

  const boxes=[];let review=null,planGeneration=0,componentGeneration=0,cloudComponent=null,componentRequest=null,autoComponent=null;
  let selectionKey,legacySid,legacyVersion=0;
  // A monotonic caller version also invalidates A -> B -> A, even when IDs/text are identical.
  const readSelection=selection||(()=>{const sid=current();if(sid!==legacySid){legacySid=sid;legacyVersion++;}return {sid,version:legacyVersion};});
  function syncSelection(){
    const selected=readSelection()||{};
    const snapshot={sid:selected.sid??null,pid:selected.pid??null,version:selected.version??0};
    snapshot.key=JSON.stringify([snapshot.sid,snapshot.pid,snapshot.version]);
    if(selectionKey!==snapshot.key){selectionKey=snapshot.key;for(const box of boxes)invalidate(box,true);}
    return Object.freeze(snapshot);
  }
  function disabled(box,control,value){box.disabled.set(control,Boolean(value));control.disabled=Boolean(value)||Boolean(box.pending);}
  function lockControls(box){
    for(const control of box.controls)control.disabled=box.disabled.get(control)||Boolean(box.pending);
    const message=box.pending?'正在提交，请勿重复操作；关闭窗口不会撤回已提交的请求。':box.message;
    if(box.notice.textContent!==message)box.notice.textContent=message;
  }
  function invalidate(box,close=false){
    box.generation++;box.token=null;box.state=null;box.projectId=null;box.preview=null;box.message='';
    if(box===review){planGeneration++;componentGeneration++;cloudComponent=null;componentRequest=null;autoComponent=null;automatic.textContent='';environment.querySelector('[data-component-preview]').textContent='组件读回已失效，请重新核对方案与UID';}
    for(const control of box.controls)disabled(box,control,true);
    lockControls(box);
    if(close&&box.dialog.open)box.dialog.close();
  }
  function dismiss(box){invalidate(box);if(box.dialog.open)box.dialog.close();}
  function makeBox(target){
    const box={dialog:target,form:target.querySelector('form'),notice:target.querySelector('[data-status]'),generation:0,token:null,state:null,projectId:null,preview:null,pending:null,message:''};
    box.controls=[...box.form.querySelectorAll('input,select,textarea,button')];box.disabled=new Map(box.controls.map(control=>[control,control.disabled]));
    target.querySelector('[data-close]').onclick=()=>dismiss(box);
    target.addEventListener('cancel',event=>{event.preventDefault();dismiss(box);});
    // Native close events are queued: an old close event must not invalidate a reopened dialog.
    target.addEventListener('close',()=>{if(!target.open)invalidate(box);});
    boxes.push(box);return box;
  }
  review=makeBox(dialog);const methodBox=makeBox(methodDialog),stopBox=makeBox(stopDialog);
  function valid(box,token){
    const selected=syncSelection();
    return Boolean(token&&box.dialog.open&&box.token===token&&box.generation===token.generation&&selected.key===token.selection.key);
  }
  function begin(box){
    const selected=syncSelection();if(!selected.sid)return null;
    if(box.pending&&box.dialog.open&&valid(box,box.pending.token)){toast('正在提交，请等待结果后再操作');return null;}
    invalidate(box);const token=Object.freeze({generation:box.generation,selection:selected});box.token=token;
    if(!box.dialog.open)box.dialog.showModal();return token;
  }
  async function projectFor(box,token,status={}){
    if(token.selection.pid){
      if(status.project_id&&status.project_id!==token.selection.pid)throw Error('项目已变化，请重新打开核对');
      return token.selection.pid;
    }
    // Legacy callers only supply current(). Resolve the project from the selected session,
    // never from the record's authorship session or its unverified project assertion.
    const detail=await api('/api/sessions/'+encodeURIComponent(token.selection.sid));
    if(!valid(box,token))return null;
    if(detail.session?.id!==token.selection.sid||!detail.session?.project_id)throw Error('无法核对当前项目，请重新打开核对');
    return detail.session.project_id;
  }
  function previewMatches(){
    const preview=review.preview;
    if(!preview||!valid(review,preview.token)||preview.planGeneration!==planGeneration||form.elements.plan_id.value!==preview.recordId)return false;
    const plan=review.state?.plans.find(item=>item.id===preview.recordId);
    return Boolean(plan&&plan.digest===preview.digest&&preview.projectId===review.projectId);
  }
  function componentMatches(){
    const preview=review.preview,component=cloudComponent;
    return Boolean(previewMatches()&&component&&component.componentGeneration===componentGeneration&&component.token===preview.token&&
      component.planGeneration===preview.planGeneration&&component.recordId===preview.recordId&&component.planDigest===preview.digest&&
      component.projectId===preview.projectId&&component.uid===form.elements.component_uid.value.trim()&&component.digest);
  }
  function requiredGpus(){return review.state?.plans.find(item=>item.id===review.preview?.recordId)?.scope_proposal?.compute?.gpu_count??(form.elements.execution.value==='lpai_devspace'?1:0);}
  function readySpace(){
    return Boolean(review.state?.devspace_execution_supported&&review.state.devspace_binding&&review.state.devspace_binding.ssh_port);
  }
  function updateReviewButtons(){
    const ready=previewMatches(),preview=review.preview;
    if(ready&&review.state.review_blocked){
      const submit=form.querySelector('[type=submit]');
      disabled(review,submit,true);
      disabled(review,dialog.querySelector('[data-reject]'),true);
      submit.textContent='请先在会话待办核对原状态';
      return;
    }
    const needsComponent=form.elements.execution.value==='lpai'&&!componentMatches()&&!autoMatches();
    const needsScope=form.elements.execution.value==='lpai'&&autoMatches()&&!form.elements.autonomous.checked;
    const badGpu=requiredGpus()>0&&(form.elements.execution.value==='local'||form.elements.execution.value==='lpai'&&(autoMatches()?autoComponent.value.gpu_count:cloudComponent?.gpu_count)!==requiredGpus());
    const needsSpace=form.elements.execution.value==='lpai_devspace'&&!readySpace();
    disabled(review,form.querySelector('[type=submit]'),!ready||Boolean(preview?.grant)||Boolean(preview?.stale)||needsComponent||needsScope||badGpu||needsSpace);
    disabled(review,dialog.querySelector('[data-reject]'),!ready);
    disabled(review,environment.querySelector('[data-probe-component]'),!ready||Boolean(preview?.grant));
    form.querySelector('[type=submit]').textContent=review.pending?'正在提交审核…':!ready?'预览待核对':preview.stale?'证据待核对':preview.grant?'该版本已有执行范围':needsSpace?'开发空间待核对':needsComponent?'组件配置待核对':badGpu?'GPU 配置与方案不符':needsScope?'自主探索范围待确认':'确认并开始';
  }
  function autoMatches(){return autoComponent?.preview===review.preview&&Boolean(autoComponent?.value)&&!form.elements.component_uid.value.trim()&&previewMatches();}
  async function loadAutomatic(){
    const preview=review.preview;
    if(!previewMatches()||preview.grant||preview.stale||form.elements.component_uid.value.trim()||form.elements.execution.value!=='lpai'||autoComponent?.preview===preview)return;
    if(!review.state.auto_component_available){automatic.textContent='此连接没有自动创建配置。可在高级选项复用已有组件。';return;}
    const pending={preview};autoComponent=pending;automatic.textContent='正在只读核对新建组件的配置…';updateReviewButtons();
    try{
      const value=await api('/api/research/component-preview',{session_id:preview.token.selection.sid,plan_id:preview.recordId,expected_digest:preview.digest});
      if(autoComponent!==pending||!previewMatches())return;
      if(!value.id||value.plan_id!==preview.recordId||value.plan_digest!==preview.digest||value.gpu_count!==requiredGpus())throw Error('新建组件预览与当前方案不符');
      pending.value=value;
      const lines=[['运行资源',`${value.gpu_type||'CPU'} · ${value.gpu_count} GPU · ${value.workers||1} 个 worker`],
        ['资源队列',value.queue_name||value.queue],['CPU / 内存',`${value.cpu} CPU / ${value.memory_gib} GiB`],['镜像',value.image],['结果保存',value.volume]];
      if(value.mounts)lines.push(['数据挂载',value.mounts.map(item=>`${item.name}${item.version?' · '+item.version:''} → ${item.env_value}`).join('；')]);
      if(value.command)lines.push(['训练入口',value.command]);
      if(value.training_parameters)lines.push(['训练参数',Object.entries(value.training_parameters).map(([key,val])=>`${key}：${typeof val==='object'?'见方案说明':val}`).join('；')]);
      if(value.rationale)lines.push(['推荐理由',value.rationale]);
      if(value.compatibility)lines.push(['环境与分布式依据',value.compatibility]);
      automatic.innerHTML='<strong>确认后自动创建并提交</strong><dl class="execution-recommendation">'+lines.map(([label,text])=>`<dt>${esc(label)}</dt><dd>${esc(text)}</dd>`).join('')+'</dl>';
      if(!form.elements.component_uid.value.trim()){form.elements.max_gpus.value=value.gpu_count;form.elements.allow_cloud.checked=true;}
    }catch(error){if(autoComponent===pending&&previewMatches()){autoComponent=null;automatic.textContent='新建配置未核对：'+error.message+'。可重新打开审核页核对。';}}
    updateReviewButtons();updateBudget();
  }
  function clearComponent(message='尚未读回组件配置'){
    componentGeneration++;cloudComponent=null;componentRequest=null;
    form.elements.allow_task_changes.checked=false;disabled(review,form.elements.allow_task_changes,true);
    environment.querySelector('[data-component-preview]').textContent=message;
  }
  function scopeEnabled(){
    for(const field of delegation.querySelectorAll('textarea'))disabled(review,field,true);
  }
  function showExecution(){
    const value=form.elements.execution.value,cloud=value==='lpai';
    const preferred=review.state?.component_resource_preference;
    resourceInfo.textContent=preferred?.queue_name?`资源偏好：${preferred.queue_name}；执行时仍核对当前账号及已批准的GPU上限。`:preferred?`项目资源偏好：${preferred.cluster} · ${preferred.package.gpu_type} · 合计 ${preferred.gpu_count} GPU；实际组件与队列须在提交前核对。`:'';
    resourceInfo.hidden=!preferred;
    environment.querySelector('[data-cloud]').hidden=!cloud;spacePanel.hidden=value!=='lpai_devspace';
    form.elements.allow_local.closest('label').hidden=value!=='local'||Boolean(review.state?.local_device);
    form.elements.python.closest('label').hidden=value!=='local';form.elements.timeout.max=cloud?'604800':'86400';
    automatic.hidden=Boolean(form.elements.component_uid.value.trim());
  }
  function updateBudget(){
    const cloud=form.elements.execution.value==='lpai';
    const timeLabel=cloud?(autoMatches()?'单次执行超时（任务启动后计时，排队另计）':'单次运行范围（已接入结果执行器时强制限时）'):'单次超时';
    if(!review.zeroFit&&!review.state?.grants?.some(g=>g.plan_id===form.elements.plan_id.value))form.elements.max_fits.value=form.elements.max_runs.value;
    dialog.querySelector('[data-budget]').textContent=`${timeLabel} ${form.elements.timeout.value} 秒；任务提交上限可展开调整。`;
  }
  function planLoading(message){
    review.preview=null;autoComponent=null;automatic.textContent='';clearComponent();
    for(const control of review.controls)disabled(review,control,true);
    disabled(review,form.elements.plan_id,!review.state?.plans.length);
    dialog.querySelector('[data-content]').textContent=message;review.message='';lockControls(review);updateReviewButtons();
  }
  function renderPlan(preview,record,plan,state){
    const reviewRecord=state.reviews?.find(item=>item.plan_id===plan.id),grant=preview.grant;
    const banner=reviewRecord?`<p class="connection-status">${reviewRecord.decision==='accept'?'该版本审核通过':'该版本已退回'} · ${esc(reviewRecord.actor)}<br>${esc(reviewRecord.reason)}<br>以下为保存时的原始方案，历史状态文字保持原样。</p>`:'';
    const warning=preview.stale?`<p class="connection-status">引用证据已变化，暂不能批准执行。${esc(record.integrity.message)}。原方案正文保留，可退回修订；不要直接把变更后的文件当原证据。</p>`:'';
    dialog.querySelector('[data-content]').innerHTML=warning+banner+markdown(record.document.content);
    for(const control of review.controls)disabled(review,control,false);
    const proposal=plan.scope_proposal;
    const scopeFields=[['objective','研究目标'],['evaluation','达标标准与评价方法'],['data_boundary','数据使用边界'],['allowed_changes','确认后 AI 可调整的范围']];
    for(const [key] of scopeFields)form.elements['scope_'+key].value=proposal?.[key]||state.autonomy?.scope?.[key]||'';
    form.elements.scope_frozen_files.value=proposal?.frozen_files?.join('\n')||(state.autonomy?.scope?.frozen_files||[]).map(item=>item.path).join('\n');
    const complete=Boolean(state.autonomy&&scopeFields.every(([key])=>form.elements['scope_'+key].value.trim()));
    delegation.querySelector('[data-scope-summary]').innerHTML=complete?scopeFields.map(([key,label])=>`<p><strong>${label}</strong><br>${esc(form.elements['scope_'+key].value)}</p>`).join(''):'<p>这份方案尚缺完整的自主探索范围。可填写修改建议并退回，由 IterLab 补齐。</p>';
    disabled(review,form.elements.autonomous,Boolean(grant)||!complete);form.elements.autonomous.checked=!grant&&complete;scopeEnabled();
    const preferred=state.resources?.execution_scope?.execution;
    const gpu=proposal?.compute?.gpu_count>0;
    form.elements.execution.querySelector('[value=lpai_devspace]').hidden=!state.devspace_execution_supported;
    form.elements.execution.querySelector('[value=lpai_devspace]').disabled=!state.devspace_execution_supported;
    form.elements.execution.value=grant?.execution||state.execution_request?.execution||(gpu?(proposal.compute.workflow==='interactive'?'lpai_devspace':'lpai'):
      preferred||(state.devspace_binding?'lpai_devspace':state.lpai_available?'lpai':'local'));
    disabled(review,form.elements.execution,Boolean(grant));
    const space=grant?.devspace||state.devspace_binding;
    spacePanel.querySelector('[data-space-description]').textContent=!state.devspace_execution_supported?'当前宿主还未启用开发空间训练；请先更新服务，不会替换成其他环境。':space?'已绑定：'+space.name+
      (grant?.devspace?' · '+space.gpus+' GPU':gpu?' · 方案要求 '+proposal.compute.gpu_count+' GPU':' · 本次使用上限 1 GPU（旧方案未声明数量）')+(state.devspace_binding?.ssh_port?' · SSH 已验证':' · 请先在项目执行页配置并验证 SSH'):
      '尚未绑定开发空间。请在研究工作区的执行页绑定已配置 GPU 的开发空间。';
    const cloud=form.elements.execution.value==='lpai',budget=proposal?.budget;
    review.timeoutEdited=false;
    const validBudget=Number.isInteger(budget?.additional_runs)&&budget.additional_runs>=1&&budget.additional_runs<=1000&&
      (budget.additional_fits===undefined||Number.isInteger(budget.additional_fits)&&budget.additional_fits>=0&&budget.additional_fits<=budget.additional_runs)&&
      Number.isInteger(budget.single_attempt_seconds)&&budget.single_attempt_seconds>=1&&budget.single_attempt_seconds<=(cloud?604800:86400);
    const maxRuns=Math.min(1000,(state.attempts?.runs||0)+(validBudget?budget.additional_runs:100));
    review.zeroFit=Boolean(validBudget&&budget.additional_fits===0);
    const defaults={python:state.default_python,max_runs:maxRuns,max_fits:review.zeroFit?(state.attempts?.fits||0):maxRuns,
      timeout:validBudget?budget.single_attempt_seconds:cloud?21600:3600};
    review.requestScope=Boolean(state.lpai_available&&!complete&&!grant);
    dialog.querySelector('[data-reject]').textContent=review.requestScope?'请 IterLab 补齐':'退回修改';
    for(const name of ['python','max_runs','max_fits','timeout']){form.elements[name].value=grant?.[name]??defaults[name];disabled(review,form.elements[name],Boolean(grant));}
    form.elements.allow_local.checked=Boolean(grant||state.local_device);disabled(review,form.elements.allow_local,Boolean(grant));
    const localOption=form.elements.execution.querySelector('option');localOption.textContent=state.local_device?.kind==='gpu'?'本地 GPU · '+state.local_device.name:'本地 CPU';
    const cloudScope=state.cloud_scopes?.find(item=>item.grant_id===grant?.id);
    form.elements.component_uid.value=cloudScope?.component_uid||'';disabled(review,form.elements.component_uid,Boolean(grant));
    form.elements.allow_cloud.checked=Boolean(cloudScope);disabled(review,form.elements.allow_cloud,Boolean(grant));
    form.elements.max_gpus.value=cloudScope?.max_gpus||0;disabled(review,form.elements.max_gpus,Boolean(grant));
    form.elements.allow_task_changes.checked=Boolean(cloudScope?.task_policy);
    disabled(review,form.elements.allow_task_changes,true);
    cloudComponent=cloudScope?{token:preview.token,planGeneration:preview.planGeneration,componentGeneration,recordId:preview.recordId,planDigest:preview.digest,projectId:preview.projectId,digest:cloudScope.component_digest,uid:cloudScope.component_uid}:null;
    environment.querySelector('[data-component-preview]').textContent=cloudScope?'固定配置摘要：'+cloudScope.component_digest:'尚未读回组件配置';
    showExecution();updateReviewButtons();updateBudget();void loadAutomatic();
  }
  async function showPlan(){
    const token=review.token;if(!valid(review,token)||review.pending?.token===token||!review.state)return;
    const state=review.state,id=form.elements.plan_id.value,plan=state.plans.find(item=>item.id===id),digest=plan?.digest;
    const generation=++planGeneration;planLoading('正在载入并核对方案预览…');
    const isLatest=()=>valid(review,token)&&generation===planGeneration&&review.state===state&&form.elements.plan_id.value===id;
    try{
      if(!plan||typeof digest!=='string'||!digest)throw Error('保存的方案摘要缺失，请重新载入核对');
      const record=await api('/api/records/'+encodeURIComponent(id));if(!isLatest())return;
      if(record.record_id!==id||record.project_id!==review.projectId||record.plan_digest!==digest||plan.digest!==digest||typeof record.document?.content!=='string')throw Error('方案预览的记录、项目或摘要与保存版本不一致，请重新载入核对');
      const preview=Object.freeze({token,planGeneration:generation,recordId:record.record_id,projectId:record.project_id,digest:record.plan_digest,grant:state.grants?.find(item=>item.plan_id===id),stale:record.integrity?.valid===false});
      review.preview=preview;renderPlan(preview,record,plan,state);
    }catch(e){if(isLatest()){planLoading('方案预览载入失败：'+e.message);toast(e.message);}}
  }
  async function open(targetPlanId){
    if(typeof targetPlanId!=='string')targetPlanId=null;
    const token=begin(review);if(!token)return;
    form.elements.reason.value='';form.elements.plan_id.innerHTML='<option value="">正在载入方案列表…</option>';planLoading('正在载入研究状态与方案预览…');
    try{
      const state=await api('/api/research/status',{session_id:token.selection.sid});if(!valid(review,token))return;
      const projectId=await projectFor(review,token,state);if(!valid(review,token))return;
      if(!Array.isArray(state.plans))throw Error('研究状态未返回保存的方案列表');
      review.state=state;review.projectId=projectId;
      form.elements.plan_id.innerHTML=state.plans.map(plan=>`<option value="${esc(plan.id)}">方案 ${esc(plan.version)} · ${esc(plan.title)}</option>`).join('');
      if(!state.plans.length){planLoading('先让Codex保存具体方案，再审核');toast('先让Codex保存具体方案，再审核');return;}
      if(targetPlanId){if(!state.plans.some(plan=>plan.id===targetPlanId))throw Error('原待办方案不在当前项目列表中，请重新核对；未替换为其他方案');form.elements.plan_id.value=targetPlanId;}
      await showPlan();
    }catch(e){if(valid(review,token)){planLoading('方案预览载入失败：'+e.message);toast(e.message);}}
  }
  async function probeComponent(){
    syncSelection();if(review.pending)return;
    if(!previewMatches())throw Error('请先载入并核对具体方案预览');
    const preview=review.preview;if(preview.grant)return;
    const uid=form.elements.component_uid.value.trim();clearComponent('正在读回固定组件配置…');form.elements.allow_cloud.checked=false;updateReviewButtons();
    if(!uid){environment.querySelector('[data-component-preview]').textContent='组件读回失败：请填写组件UID';throw Error('请填写组件UID');}
    const generation=componentGeneration;componentRequest={generation,preview,uid};
    const isLatest=()=>valid(review,preview.token)&&previewMatches()&&!review.pending&&generation===componentGeneration&&review.preview===preview&&form.elements.component_uid.value.trim()===uid;
    try{
      const component=await api('/api/company/lpai',{operation:'component_sdk',uid});if(!isLatest())return;
      if(component.uid!==uid||typeof component.digest!=='string'||!component.digest||!Number.isInteger(component.gpu_count)||component.gpu_count<0)throw Error('组件UID或固定配置摘要无法核对，请重新读回');
      cloudComponent={...component,token:preview.token,planGeneration:preview.planGeneration,componentGeneration:generation,recordId:preview.recordId,planDigest:preview.digest,projectId:preview.projectId};componentRequest=null;
      form.elements.max_gpus.value=component.gpu_count;
      const paths=component.supported_patch_paths;
      disabled(review,form.elements.allow_task_changes,review.state?.task_policy_schema!==1||!Array.isArray(paths)||!paths.length||paths.length>12);
      environment.querySelector('[data-component-preview]').textContent=`${component.name} · ${component.namespace} · ${component.gpu_count} GPU · 固定摘要 ${component.digest.slice(0,12)}`;updateReviewButtons();
    }catch(e){if(isLatest()){componentRequest=null;environment.querySelector('[data-component-preview]').textContent='组件读回失败：'+e.message;updateReviewButtons();toast(e.message);}}
  }
  async function postAction(box,token,path,payload,success){
    if(box.pending||!valid(box,token))return;
    // Set the lock before calling api: even synchronous/reentrant submit events cannot post twice.
    const request={token,payload};box.pending=request;box.message='';
    if(box===review&&componentRequest)clearComponent('组件读回已失效，请重新读回');
    lockControls(box);if(box===review)updateReviewButtons();
    let result,error;
    try{result=await api(path,payload);}catch(e){error=e;}
    finally{
      if(box.pending===request)box.pending=null;
      if(error&&valid(box,token)){box.message='提交未确认：'+error.message+'。请核对已保存状态，不会自动重试。';toast(box.message);}
      lockControls(box);if(box===review)updateReviewButtons();
    }
    if(error||!valid(box,token))return;
    dismiss(box);const closedGeneration=box.generation;toast(success(result));
    try{await refresh();}catch(e){if(syncSelection().key===token.selection.key&&box.generation===closedGeneration)toast(e.message);}
  }
  async function submit(decision){
    syncSelection();if(review.pending)return;
    if(review.state?.review_blocked)throw Error('请先从会话待办核对原执行状态，再重新打开审核');
    if(!dialog.open||!previewMatches()){updateReviewButtons();throw Error('当前所选方案尚无已核对的完整预览，请重新载入核对');}
    const preview=review.preview;
    if(decision==='accept'&&preview.grant)throw Error('该版本已有执行范围');
    if(decision==='accept'&&preview.stale)throw Error('引用证据已变化，暂不能批准执行；可退回修订');
    const note=form.elements.reason.value.trim();
    if(decision==='reject'&&!note&&!review.requestScope)throw Error('请填写希望修改的内容');
    const reason=note||(decision==='reject'?'请基于已知证据保存一份新的完整方案，写明目标、评价、数据边界、自主调整范围和预算建议，提交给我确认。':'用户确认当前方案与运行范围');
    const component=componentMatches()?cloudComponent:null,execution=form.elements.execution.value,automaticPreview=autoMatches()?autoComponent.value:null;
    if(decision==='accept'&&execution==='lpai'&&!component&&!automaticPreview)throw Error('请先核对自动新建的配置，或读回要复用的组件');
    if(decision==='accept'&&execution==='lpai_devspace'&&!readySpace())throw Error('请在项目执行页绑定 GPU 开发空间并验证 SSH');
    if(decision==='accept'&&requiredGpus()>0&&(execution==='local'||execution==='lpai'&&(automaticPreview?.gpu_count??component?.gpu_count)!==requiredGpus()))throw Error('此方案需要核对匹配的 LPAI GPU 资源');
    const autonomous=form.elements.autonomous.checked&&!review.disabled.get(form.elements.autonomous);
    if(decision==='accept'&&execution==='lpai'&&automaticPreview&&!autonomous)throw Error('自动新建并提交需要方案给出完整自主探索范围。可退回让 IterLab 补齐。');
    const payload={session_id:preview.token.selection.sid,plan_id:preview.recordId,expected_digest:preview.digest,decision,reason,
      execution,allow_local:form.elements.allow_local.checked,python:form.elements.python.value,
      allow_cloud:form.elements.allow_cloud.checked,component_uid:form.elements.component_uid.value.trim(),component_digest:component?.digest,max_gpus:Number(form.elements.max_gpus.value),
      max_runs:Number(form.elements.max_runs.value),max_fits:Number(form.elements.max_fits.value),timeout:Number(form.elements.timeout.value),
      autonomous,research_scope:autonomous?readScope(form,'scope_'):null,continue:true};
    if(execution==='local'&&review.state.local_device)payload.local_device=review.state.local_device.kind;
    if(decision==='accept'&&execution==='lpai_devspace'){
      payload.allow_cloud=true;payload.allow_local=false;payload.space_uid=review.state.devspace_binding.uid;
      payload.max_gpus=requiredGpus();
    }
    if(decision==='accept'&&execution==='lpai'&&automaticPreview){payload.allow_cloud=true;payload.max_gpus=automaticPreview.gpu_count;payload.component_preview_id=automaticPreview.id;payload.task_policy={schema:1,allowed_patch_paths:automaticPreview.command_paths||['/lpai_task/cmd','/lpai_task/roles/0/cmd']};}
    if(decision==='accept'&&execution==='lpai'&&form.elements.allow_task_changes.checked){
      const paths=component?.supported_patch_paths;
      if(review.state?.task_policy_schema!==1||!Array.isArray(paths)||!paths.length||paths.length>12)throw Error('当前宿主或组件尚未返回可授权的训练入口/源码位置，请重新载入核对');
      payload.task_policy={schema:1,allowed_patch_paths:paths};
    }
    await postAction(review,preview.token,'/api/research/review',payload,()=>decision==='accept'?'审核已保存，已通知原会话继续':'已保存退回理由，原会话将修订方案');
  }
  form.onsubmit=safe(async event=>{event.preventDefault();await submit('accept');});
  dialog.querySelector('[data-reject]').onclick=safe(()=>submit('reject'));
  form.elements.plan_id.onchange=safe(async()=>{syncSelection();if(review.pending){form.elements.plan_id.value=review.preview?.recordId||'';return;}await showPlan();});
  form.elements.component_uid.addEventListener('input',()=>{
    syncSelection();if(review.pending){form.elements.component_uid.value=review.preview&&review.pending.token===review.preview.token?review.pending.payload.component_uid:cloudComponent?.uid||'';return;}
    clearComponent('组件UID已变化，请重新读回固定组件配置');form.elements.allow_cloud.checked=autoMatches();showExecution();updateReviewButtons();updateBudget();void loadAutomatic();
  });
  environment.querySelector('[data-probe-component]').onclick=safe(probeComponent);
  form.elements.execution.onchange=()=>{syncSelection();if(review.pending){if(review.pending.token===review.token)form.elements.execution.value=review.pending.payload.execution;return;}if(!previewMatches())return;
    if(!review.timeoutEdited){
      const cloud=form.elements.execution.value==='lpai',suggested=review.state.plans.find(plan=>plan.id===review.preview.recordId)?.scope_proposal?.budget?.single_attempt_seconds;
      form.elements.timeout.value=Number.isInteger(suggested)&&suggested>=1&&suggested<=(cloud?604800:86400)?suggested:cloud?21600:3600;
    }
    showExecution();updateReviewButtons();updateBudget();void loadAutomatic();};
  form.elements.autonomous.onchange=()=>{syncSelection();if(!review.pending&&previewMatches())updateReviewButtons();};
  for(const name of ['max_runs','max_fits','timeout'])form.elements[name].addEventListener('input',()=>{syncSelection();if(!review.pending&&previewMatches()){if(name==='timeout')review.timeoutEdited=true;updateBudget();}});

  async function openMethod(){
    const token=begin(methodBox);if(!token)return;
    methodBox.form.elements.reason.value='';methodDialog.querySelector('[data-method]').textContent='正在载入并核对本项目研究方法…';
    try{
      const state=await api('/api/research/status',{session_id:token.selection.sid});if(!valid(methodBox,token))return;
      const projectId=await projectFor(methodBox,token,state);if(!valid(methodBox,token))return;
      const method=state.method,update=state.method_update||{available:false,error:'本地服务尚未部署方法升级能力'};
      if(typeof method?.digest!=='string'||!method.digest)throw Error('研究方法摘要缺失，请重新载入核对');
      methodBox.state=state;methodBox.projectId=projectId;methodBox.preview=Object.freeze({token,projectId,digest:method.digest,available:Boolean(update.available)});
      methodDialog.querySelector('[data-method]').textContent='当前：'+(method.mode==='builtin'?'内置研究合同 '+method.version:'原SDM Skill固定版本')+
        (update.available?'；可更新为内置研究合同 '+update.version:update.error?'；新合同暂不可用：'+update.error:'；已是当前内置版本');
      disabled(methodBox,methodBox.form.elements.reason,false);disabled(methodBox,methodDialog.querySelector('[type=submit]'),!update.available);
    }catch(e){if(valid(methodBox,token)){methodBox.preview=null;methodDialog.querySelector('[data-method]').textContent='研究方法预览载入失败：'+e.message;toast(e.message);}}
  }
  methodBox.form.onsubmit=safe(async event=>{
    event.preventDefault();syncSelection();if(methodBox.pending)return;
    const preview=methodBox.preview;
    if(!methodDialog.open||!preview||!valid(methodBox,preview.token)||!preview.available||preview.projectId!==methodBox.projectId||preview.digest!==methodBox.state?.method.digest)throw Error('请先载入并核对可更新的本项目研究方法');
    const reason=methodBox.form.elements.reason.value;if(!reason.trim())throw Error('请填写具体更新理由');
    await postAction(methodBox,preview.token,'/api/research/method-upgrade',{session_id:preview.token.selection.sid,expected_digest:preview.digest,reason,confirmed:true},result=>result.changed?'项目研究合同已更新，旧方法和额度保持':'没有修改，已是当前版本');
  });
  async function openRoundStop(id){
    if(!id)return;const token=begin(stopBox);if(!token)return;
    stopBox.form.elements.reason.value='';stopDialog.querySelector('article').textContent='正在载入并核对本轮结束建议…';
    try{
      const record=await api('/api/records/'+encodeURIComponent(id));if(!valid(stopBox,token))return;
      const projectId=await projectFor(stopBox,token);if(!valid(stopBox,token))return;
      if(record.record_id!==id||record.project_id!==projectId||typeof record.document?.content!=='string')throw Error('结束建议的记录或项目不匹配，请重新载入核对');
      if(record.closure?.requires_human_confirmation===false)throw Error('本轮已自主收束，无需人工确认；请查看评审与结束原因');
      stopBox.projectId=projectId;stopBox.preview=Object.freeze({token,projectId,recordId:record.record_id});
      stopDialog.querySelector('article').innerHTML=markdown(record.document.content);
      for(const control of stopBox.controls)disabled(stopBox,control,false);
    }catch(e){if(valid(stopBox,token)){stopBox.preview=null;stopDialog.querySelector('article').textContent='结束建议预览载入失败：'+e.message;toast(e.message);}}
  }
  async function reviewStop(decision){
    syncSelection();if(stopBox.pending)return;
    const preview=stopBox.preview;
    if(!stopDialog.open||!preview||!valid(stopBox,preview.token)||preview.projectId!==stopBox.projectId)throw Error('请先载入并核对本项目的结束建议');
    const reason=stopBox.form.elements.reason.value;if(!reason.trim())throw Error('请填写具体理由');
    await postAction(stopBox,preview.token,'/api/research/round-stop',{session_id:preview.token.selection.sid,review_id:preview.recordId,decision,reason,confirmed:true},()=>decision==='accept_stop'?'已记录用户确认结束':'结束建议已退回');
  }
  stopDialog.querySelector('[data-reject]').onclick=safe(()=>reviewStop('reject_stop'));
  stopBox.form.onsubmit=safe(async event=>{event.preventDefault();await reviewStop('accept_stop');});

  // Session selection changes update workbench DOM. Observe those changes without polling
  // or network requests; all event/async boundaries independently recheck the same key.
  if(typeof MutationObserver==='function')new MutationObserver(()=>{if(boxes.some(box=>box.token))syncSelection();}).observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['open']});
  return {open:safe(open),openMethod:safe(openMethod),openRoundStop:safe(openRoundStop),selectionChanged(){syncSelection();autonomyUI.selectionChanged();},decorateExperiments(host){
    syncSelection();
    const button=document.createElement('button');button.className='secondary';button.textContent='自主推进与分支';button.onclick=autonomyUI.open;host.querySelector('.company-section-header')?.append(button);
    const method=document.createElement('button');method.className='text-button';method.textContent='研究设置';method.onclick=safe(openMethod);host.querySelector('.company-section-header')?.append(method);
  }};
}
