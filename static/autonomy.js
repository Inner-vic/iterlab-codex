import {escapeHTML as esc} from './markdown.js';

const scopeFields=[['objective','研究目标'],['evaluation','固定评价协议'],['data_boundary','数据边界'],['allowed_changes','允许AI调整的范围']];
export function scopeForm(prefix=''){
  return scopeFields.map(([key,label])=>`<label>${label}<textarea name="${prefix+key}" rows="2" required></textarea></label>`).join('')+`<label>固定协议文件（可选，必须已在方案证据中；每行一个）<textarea name="${prefix}frozen_files" rows="2"></textarea></label>`;
}
export function readScope(form,prefix=''){
  return Object.fromEntries([...scopeFields.map(([key])=>[key,form.elements[prefix+key].value.trim()]),['frozen_files',form.elements[prefix+'frozen_files'].value.split('\n').map(s=>s.trim()).filter(Boolean)]]);
}
export function installAutonomyUI({api,current,selection=()=>({sid:current()}),toast,refresh}){
  const dialog=document.createElement('dialog');dialog.id='research-autonomy-dialog';
  dialog.innerHTML='<div class="dialog-header"><h2>研究推进与人工干预</h2><button class="icon-button" data-close aria-label="关闭">×</button></div><div data-body></div>';
  document.body.append(dialog);let generation=0,reading=0,mutating=false;
  const safe=fn=>async(...args)=>{try{await fn(...args);}catch(e){toast(e.message);}};
  const key=()=>JSON.stringify(selection());
  const valid=ctx=>dialog.open&&ctx.sid===current()&&ctx.key===key()&&ctx.generation===generation;
  const assert=ctx=>{if(!valid(ctx))throw Error('当前会话或面板已变化，请重新核对');};
  async function mutate(ctx,operation){
    if(mutating)return;assert(ctx);mutating=true;
    const controls=[...dialog.querySelectorAll('button,input,select,textarea')].map(e=>[e,e.disabled]);controls.forEach(([e])=>e.disabled=true);
    try{const result=await operation();if(valid(ctx)){const loading=open(true),reloaded={...ctx,generation};await loading;if(valid(reloaded))await refresh();}return result;}
    finally{mutating=false;controls.forEach(([e,disabled])=>e.disabled=disabled);}
  }
  const post=(ctx,action,fields)=>{assert(ctx);return api('/api/research/autonomy',{session_id:ctx.sid,action,confirmed:true,...fields});};
  async function open(afterMutation=false){
    if(mutating&&afterMutation!==true)return;
    const ctx={sid:current(),pid:selection().pid,key:key(),generation:++generation};if(!ctx.sid)return;
    const host=dialog.querySelector('[data-body]');host.textContent='正在载入研究推进与人工干预…';if(!dialog.open)dialog.showModal();
    let state;try{state=await api('/api/research/status',{session_id:ctx.sid});}catch(e){if(!valid(ctx))return;host.textContent='研究推进载入失败：'+e.message;throw e;}if(!valid(ctx))return;
    const a=state.autonomy;
    if(!a){host.textContent='服务版本尚不支持自主研究，请等待后端更新；现有审核规则保持。';return;}
    host.innerHTML=`<p><strong>${a.mode==='autonomous'?'AI自主推进':'逐版人工审核'}</strong> · 每轮保留证据分析与评审；自主模式的方案修订在授权内继续。</p><p>暂停或切换模式不会取消云任务，历史累计由后台保留。</p><div class="company-actions"><button class="secondary" data-pause>暂停研究</button>${a.authority_id?`<button class="secondary" data-mode>${a.mode==='autonomous'?'切换为逐版审核':'恢复授权内自主推进'}</button>`:''}</div>${a.last_continuation?.state==='no_progress'?'<p class="connection-status">上次续接未产生实质推进，已停止重复唤醒。可在对话中说明方向后继续。</p>':''}<section data-scope></section><section data-branches></section>`;
    const guard=state.agent_roles?.no_progress_guard;
    if(guard){const p=document.createElement('p');p.className='connection-status';p.textContent=(guard.blocked?'自动角色链已停止：':'自动角色链保护：')+`连续无变化 ${guard.consecutive_unchanged}／${guard.limit} 次。`+(guard.blocked?guard.reason+' 待交接已保留；新的人工指导不增加或重置训练预算。':'重复正文、空评审与新ID不会重置计数。');host.querySelector('[data-scope]').before(p);}
    host.querySelector('[data-pause]').onclick=safe(()=>mutate(ctx,async()=>{await api(`/api/sessions/${ctx.sid}/interrupt`,{});toast('已保存暂停与原轮次中断意图；云端任务按原身份保留');}));
    host.querySelector('[data-mode]')?.addEventListener('click',safe(()=>mutate(ctx,()=>post(ctx,'mode',{mode:a.mode==='autonomous'?'manual':'autonomous',reason:'用户在研究推进面板明确切换模式'}))));
    const area=host.querySelector('[data-scope]');
    if(a.scheduler){const status=document.createElement('p');status.className='connection-status';status.textContent='续接状态：'+a.scheduler.reason;host.querySelector('[data-branches]').before(status);}
    const budget=state.resources?.budget;
    if(budget&&state.round?.state==='active'){
      const edit=document.createElement('details');edit.setAttribute('data-budget-controls','');edit.open=a.scheduler?.state==='budget_paused';
      edit.innerHTML=`<summary>任务提交上限</summary><p>当前累计提交上限 ${budget.max_runs} 次。可追加任务次数，或在你指定的累计硬上限内自动续额。GPU上限和单次超时保持；特殊零拟合方案仍只能诊断。</p><form><label>增加任务次数<input type="number" name="add_runs" min="0" max="1000" value="0" required></label><label class="check-label"><input type="checkbox" name="auto" ${budget.auto?'checked':''}>允许在累计硬上限内自动续额</label><label>累计任务硬上限<input type="number" name="hard_runs" min="1" max="1000" value="${esc(budget.hard_runs??'')}"></label><button class="primary" type="submit">保存上限</button></form>`;
      area.before(edit);
      const form=edit.querySelector('form');
      const optional=name=>form.elements[name].value===''?null:Number(form.elements[name].value);
      const addRuns=Math.min(6,(budget.hard_runs??1000)-budget.max_runs);
      const save=fields=>mutate(ctx,async()=>{const result=await post(ctx,'budget',{plan_id:budget.plan_id,grant_id:budget.grant_id,revision:budget.revision,...fields});
        toast(result.paused?'预算已保存，人工暂停仍有效':'预算已保存，已用次数保留');});
      if(addRuns){
        const quick=document.createElement('button');quick.className='secondary';quick.setAttribute('type','button');quick.setAttribute('data-budget-batch','');
        quick.textContent=`追加 ${addRuns} 次任务`;
        quick.onclick=safe(()=>save({add_runs:addRuns,auto:Boolean(budget.auto),hard_runs:budget.hard_runs}));
        form.before(quick);
      }
      form.onsubmit=safe(async e=>{e.preventDefault();assert(ctx);
        await save({add_runs:Number(form.elements.add_runs.value),auto:form.elements.auto.checked,hard_runs:optional('hard_runs')});
      });
    }
    if(a.authority_id){area.innerHTML='<h3>当前研究授权</h3>';for(const [field,label] of scopeFields){const p=document.createElement('p');const b=document.createElement('strong');b.textContent=label+'：';p.append(b,document.createTextNode(a.scope[field]));area.append(p);}}
    else{
      area.innerHTML=`<details><summary>为已审核方案启用自主研究</summary><p>先在方案审核中确认执行位置与累计范围。这里不增加额度，也不会批准其他待审方案。</p><form><label>已审核方案<select name="plan_id">${state.plans.filter(p=>state.grants.some(g=>g.plan_id===p.id)).map(p=>`<option value="${esc(p.id)}">${esc(p.version)} · ${esc(p.title)}</option>`).join('')}</select></label>${scopeForm()}<label>授权理由<textarea name="reason" required rows="2"></textarea></label><button class="primary" type="submit">确认授权并启用自主研究</button></form></details>`;
      const form=area.querySelector('form');form.onsubmit=safe(async e=>{e.preventDefault();assert(ctx);const plan=state.plans.find(p=>p.id===form.elements.plan_id.value);if(!plan)throw Error('先明确审核初始方案与执行范围');const fields={plan_id:plan.id,expected_digest:plan.digest,scope:readScope(form),reason:form.elements.reason.value};await mutate(ctx,()=>post(ctx,'enable',fields));});
    }
    const branches=host.querySelector('[data-branches]');branches.innerHTML='<h3>探索分支</h3>';
    for(const b of a.branches||[]){const row=document.createElement('article');row.className='company-choice';const plan=state.plans.find(p=>p.id===b.head_plan_id);row.innerHTML=`<strong>${b.id===a.active_branch?.id?'当前主线 · ':''}${esc(plan?`方案 ${plan.version} · ${plan.title}`:'历史方案分支')}</strong><p>${esc(b.reason)}</p><small>${b.origin==='initial'?'研究主线':b.origin==='human_ui'?'人工修订':'依据用户消息由AI整理'}</small>`;if(b.id!==a.active_branch?.id){const button=document.createElement('button');button.className='secondary';button.textContent='切换到此分支';button.onclick=safe(()=>mutate(ctx,()=>post(ctx,'switch',{branch_id:b.id,reason:'用户明确选择探索分支'})));row.append(button);}branches.append(row);}
    if(a.authority_id){
      const edit=document.createElement('details');edit.innerHTML=`<summary>从具体方案创建人工探索分支</summary><p>原方案和实验保留。执行中请先暂停；普通讨论直接在会话中进行。</p><form><label>分叉起点<select name="from_plan_id">${state.plans.map(p=>`<option value="${esc(p.id)}">${esc(p.version)} · ${esc(p.title)}</option>`).join('')}</select></label><button class="secondary" type="button" data-read>载入原方案正文</button><label>修订方式<select name="revision_scope"><option value="incremental">同系列修订</option><option value="major">总体路线调整 · 新系列</option></select></label><label>修订方案名称<input name="title" required></label><label>修订后的完整方案<textarea name="content" rows="9" required></textarea></label><label>改动理由<textarea name="reason" rows="2" required></textarea></label><label class="check-label"><input name="activate" type="checkbox">保存后切换到此分支并按授权继续</label><button class="primary" type="submit">保存人工探索分支</button></form>`;
      const form=edit.querySelector('form');let inputRevision=0;form.elements.content.oninput=()=>inputRevision++;form.elements.from_plan_id.onchange=()=>reading++;
      edit.querySelector('[data-read]').onclick=safe(async()=>{assert(ctx);const request=++reading,id=form.elements.from_plan_id.value,plan=state.plans.find(p=>p.id===id),digest=plan?.digest,revision=inputRevision,original=form.elements.content.value;if(!digest)throw Error('方案摘要缺失，请重新核对');const record=await api('/api/records/'+id);if(!valid(ctx)||request!==reading||form.elements.from_plan_id.value!==id||revision!==inputRevision||form.elements.content.value!==original)return;if(state.plans.find(p=>p.id===id)?.digest!==digest||record.record_id!==id||!ctx.pid||record.project_id!==ctx.pid||record.plan_digest!==digest)throw Error('原方案身份、项目或摘要已变化，请重新核对');form.elements.content.value=record.document.content;});
      form.onsubmit=safe(async e=>{e.preventDefault();assert(ctx);reading++;const fields={from_plan_id:form.elements.from_plan_id.value,title:form.elements.title.value,content:form.elements.content.value,reason:form.elements.reason.value,activate:form.elements.activate.checked,revision_scope:form.elements.revision_scope.value};await mutate(ctx,async()=>{const result=await post(ctx,'fork',fields);toast('已保存方案 '+result.version+'；历史记录保留');});});branches.append(edit);
    }
  }
  function dismiss(){generation++;reading++;if(dialog.open)dialog.close();}
  function selectionChanged(){dismiss();}
  dialog.addEventListener('cancel',event=>{event.preventDefault();dismiss();});
  // Native close is queued and may belong to the previous opening.
  dialog.addEventListener('close',()=>{if(!dialog.open){generation++;reading++;}});dialog.querySelector('[data-close]').onclick=dismiss;
  return {open:safe(open),selectionChanged,decorate(host){const section=document.createElement('section');section.className='context-section';section.innerHTML='<h3>研究推进与探索分支</h3><p>管理自主授权、暂停、活动分支及人工修订。已有项目保持原审核策略，启用后才自主推进。</p><button class="secondary">研究推进与人工干预</button>';section.querySelector('button').onclick=safe(open);host.append(section);}};
}
