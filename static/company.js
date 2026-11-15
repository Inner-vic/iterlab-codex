import {escapeHTML as esc} from './markdown.js';
import {connectionSummary,installSessionRuns} from './session-runs.js';
import {componentResourcePanel} from './component-resources.js';
import {spacePhaseText,spacePhaseLabel,spaceResourceText,spaceOperationText,spaceRuntimeSummary,spaceObservationText,spaceStopReason,spaceStartDisabled,spaceSSHState,requestManualSpaceStop} from './company-space.js';
import {runtimeView,runtimeChoicePanel,componentOverview,componentResourceDirectory} from './runtime-ui.js';
import {loginPanel,createLoginWatcher} from './platform-login.js';

export function installCompanyUI({api,current,toast,refresh,openRuns,onRunsChange}){
  const $=s=>document.querySelector(s);
  let config=null,context=null,spaces=[],repositories=[];
  const sshDrafts=new Map();
  const resourceDirectories=new Map();
  const runUI=installSessionRuns({api,current,toast,openRuns,onChange:onRunsChange});
  const safe=fn=>async(...args)=>{try{return await fn(...args);}catch(error){toast(error.message);}};
  const labels={lpai_cli:'开发空间／任务 CLI',lpai_sdk:'训练 SDK',gitlab_api:'GitLab 账号',git_transport:'Git 源码传输'};
  const pendingChecks=new Map();
  const loginWatcher=createLoginWatcher({readState:()=>api('/api/company/login'),isOpen:()=>Boolean($('#company-dialog')?.open),onComplete:async platform=>{
    if(!config?.[platform+'_enabled'])return;
    const revision=config.revision,result=await checkConnection(platform,revision,current()||null);
    if(config.revision===revision&&$('#company-dialog').open){showHealth(platform,result.checks);await showLogin(platform);}
  }});
  async function showLogin(platform){
    const options=await api('/api/company/login'),host=$(`#company-auth-${platform}`);
    host.hidden=false;host.innerHTML=loginPanel(platform,options);
    for(const button of host.querySelectorAll('[data-platform-login]'))button.onclick=safe(async()=>{
      button.disabled=true;
      try{
        const result=await api('/api/company/login',{kind:button.dataset.platformLogin});
        const message=host.querySelector('[data-login-message]');
        message.textContent=result.manual_required?'请在服务电脑终端运行：'+result.command.join(' '):(result.message||'登录窗口已打开，完成后会自动验证。');
        if(result.started)loginWatcher.add(platform,button.dataset.platformLogin);
        else if(result.checks){const status=await api('/api/company');showHealth(platform,[...(status.health||[])].reverse());await showLogin(platform);}
      }finally{button.disabled=false;}
    });
  }
  globalThis.window?.addEventListener?.('focus',()=>void loginWatcher.refresh());
  $('#company-dialog')?.addEventListener?.('close',()=>loginWatcher.pause());
  function checkConnection(platform,revision,sessionId=null){
    const key=JSON.stringify([platform,revision,sessionId]);
    if(!pendingChecks.has(key))pendingChecks.set(key,api('/api/company/check',{platform,...(sessionId?{session_id:sessionId}:{})}).finally(()=>pendingChecks.delete(key)));
    return pendingChecks.get(key);
  }
  function showHealth(platform,checks){
    const host=$(`#company-${platform}-diagnostics`);host.replaceChildren();
    $(`#company-${platform}-status`).textContent=config?.[platform+'_enabled']?connectionSummary(platform,checks):'未启用';
    const relevant=checks.filter(c=>platform==='lpai'?c.kind.startsWith('lpai'):c.kind.startsWith('git'));
    const order=Object.keys(labels);
    const latest=[...new Map(relevant.map(c=>[c.kind,c])).values()].sort((a,b)=>order.indexOf(a.kind)-order.indexOf(b.kind));
    if(!latest.length){host.textContent='尚未实际检查连接。检查只读取平台，不启动训练。';return;}
    for(const c of latest){const row=document.createElement('span');row.style.display='block';
      row.textContent=`${labels[c.kind]}：${c.stale?'需重新检查':c.state==='passed'?'读取通过':'检查失败'} · ${new Date(c.checked_at).toLocaleString()}\n${c.target}\n${c.authentication_source?'登录来源：'+c.authentication_source+'\n':''}${c.message}`;
      row.className='platform-diagnostic';host.append(row);}
    if(platform==='gitlab'&&!latest.some(c=>c.kind==='git_transport'))host.append(document.createTextNode('尚未检查源码传输；绑定项目仓库后可单独检查。'));
    if(latest.some(c=>c.category==='authentication')&&$(`#company-auth-${platform}`)?.hidden)showLogin(platform).catch(error=>toast(error.message));
  }
  async function open(){
    const status=await api('/api/company');config=status.config;
    const f=$('#company-form');f.elements.lpai_enabled.checked=config.lpai_enabled;f.elements.gitlab_enabled.checked=config.gitlab_enabled;
    f.elements.namespace.value=config.namespace;f.elements.gitlab_host.value=config.gitlab_host;
    f.elements.git_protocol.value=config.git_protocol;
    $('#connection-defaults').innerHTML=`<p>LPAI 资源范围：${esc(config.namespace)}</p><p>GitLab：${esc(config.gitlab_host)}</p><p>源码传输：${esc(config.git_protocol.toUpperCase())}</p>`;
    // status is newest first; preserve the most recent observation per kind.
    showHealth('lpai',[...(status.health||[])].reverse());showHealth('gitlab',[...(status.health||[])].reverse());
    $('#company-dialog').showModal();
    loginWatcher.resume();
    // No remote task enumeration, configuration writes, or model call on open.
    // Reuse current official authentication, and only refresh enabled stale reads.
    for(const platform of ['lpai','gitlab']){
      const relevant=(status.health||[]).filter(c=>platform==='lpai'?c.kind.startsWith('lpai'):c.kind==='gitlab_api');
      const latest=[...new Map([...relevant].reverse().map(c=>[c.kind,c])).values()];
      if(config[platform+'_enabled']&&(!latest.length||latest.some(c=>c.stale))){
        const label=$(`#company-${platform}-status`),revision=config.revision;
        label.textContent=latest.some(item=>item.state==='passed')?'显示上次检查结果，正在后台更新…':'正在确认本机已有登录…';
        checkConnection(platform,revision).then(result=>{
          if(config.revision===revision&&$('#company-dialog').open)showHealth(platform,result.checks);
        }).catch(error=>{if(config.revision===revision&&$('#company-dialog').open)label.textContent=error.message;});
      }
    }
  }
  async function save(){const f=$('#company-form');const result=await api('/api/company/config',{revision:config.revision,
    lpai_enabled:f.elements.lpai_enabled.checked,gitlab_enabled:f.elements.gitlab_enabled.checked,namespace:f.elements.namespace.value.trim(),gitlab_host:f.elements.gitlab_host.value.trim(),git_protocol:f.elements.git_protocol?.value||config.git_protocol});config=result.config;return result;}
  async function loadContext(){const sid=current();if(!sid)return null;const result=await api('/api/company/context',{session_id:sid});if(sid!==current())return null;context=result;return context;}
  async function action(request){
    if(!current())return toast('先创建或选择研究会话');
    const sessionId=current();
    await loadContext();
    if(sessionId!==current())throw Error('当前会话已变化，请重新核对操作目标');
    const names={lpai_start:'启动本项目已绑定的开发空间',lpai_submit:'通过官方SDK提交固定组件任务（不是只读操作）',git_clone:'克隆已绑定仓库到source目录',git_branch:'新建并切换分支',git_switch:'切换分支',git_commit:'提交所列本地文件',git_pull:'拉取并仅快进合并',git_merge:'合并本地分支',git_push:'推送到已绑定GitLab仓库'};
    const submitted=context.training?.submissions.find(s=>s.id===request.submission_id),preview=submitted?.preview;
    const trainingText=preview?`\n组件：${preview.component.name}\n任务：${preview.task_name}\nGPU：${preview.component.gpu_count}\n问题：${preview.task_information.problem_statement}\n方案：${preview.task_information.solution_approach}\n预期：${preview.task_information.expected_result}\n预计：${preview.task_information.expected_duration}分钟\n依据：${preview.task_information.expected_duration_desc}\n改动：${preview.change_summary||'首次提交'}\n固定摘要：${preview.component.digest}\n`:'';
    const target=context.bindings.map(b=>b.name).join('；');
    const repo=context.bindings.find(b=>b.kind==='gitlab')?.observation;
    const source=request.action==='git_clone'&&repo?`源码传输：${context.connections.config.git_protocol==='ssh'?repo.ssh_url_to_repo:repo.http_url_to_repo}\n`:'';
    if(!confirm(`${names[request.action]}\n项目绑定：${target||'当前本地工作区'}\n${source}${trainingText}${request.branch?'分支：'+request.branch+'\n':''}${request.message?'说明：'+request.message+'\n':''}${request.files?'文件：'+request.files.join(', ')+'\n':''}确认本次操作？`))return;
    const authority=context.authority;
    const result=await api('/api/company/action',{session_id:sessionId,id:crypto.randomUUID(),request,confirmed:true,expected_authority:authority});
    toast(result.message||('操作状态：'+result.state));await render();
  }
  async function render(){
    const host=$('#tab-execution');
    if(!current()){host.innerHTML='<div class="empty-note">选择项目后，在这里绑定开发空间、源码仓库和运行任务。</div>';return;}
    const sessionId=current();if(!await loadContext()||sessionId!==current())return;config=context.connections.config;
    const previousSpaceKey=host.dataset.spaceKey,active=document.activeElement;
    const focusField=['data-ssh-command','data-ssh-identity'].find(name=>active&&host.querySelector('['+name+']')===active);
    const selection=focusField?[active.selectionStart,active.selectionEnd]:null;
    host.innerHTML=`<div class="company-section-header"><h3>运行环境</h3><button class="text-button" data-settings>平台连接</button></div>`;
    host.querySelector('[data-settings]').onclick=safe(open);
    const lp=context.bindings.find(b=>b.kind==='lpai'),git=context.bindings.find(b=>b.kind==='gitlab');
    const spaceKey=JSON.stringify([sessionId,lp?.scope,lp?.uid]);host.dataset.spaceKey=spaceKey;
    const runtime=runtimeView(context),selectedExecution=runtime.execution;
    const runConfig=$('#run-config');if(runConfig.dataset.sessionId!==sessionId){runConfig.dataset.sessionId=sessionId;runConfig.open=runtime.needsAttention;}
    runConfig.querySelector('summary').textContent='运行配置 · '+runtime.label;
    host.append(runtimeChoicePanel({context,sessionId,current,api,refresh,toast}));
    const lpBox=document.createElement('section');lpBox.className='record-card company-card devspace-runtime';
    lpBox.innerHTML=lp?`<header class="devspace-heading"><strong>${esc(lp.name)}</strong><span class="runtime-status-pill" data-space-pill role="status"></span></header><p class="small devspace-resources" data-space-resources></p><p class="small devspace-pending" data-space-summary role="status" hidden></p><div class="company-actions runtime-actions"><button class="secondary" data-start>启动</button><button class="secondary" data-stop>停止</button><button class="secondary" data-refresh>刷新</button><a class="text-button" target="_blank" rel="noopener" href="${esc(lp.observation.url)}">打开 LPAI ↗</a></div><details class="devspace-binding"><summary>详情与管理</summary><p class="small" data-space-phase></p><p class="small" data-space-operation></p><p class="small space-observation" data-space-observation></p><p class="small" data-space-stop-reason></p><p class="small">空间：${esc(lp.scope)} · <code>${esc(lp.uid)}</code></p><p class="small">启停需平台确认，刷新只读取原请求。停止对话与停止空间是独立操作。</p><div class="company-actions"><button class="text-button" data-spaces>更换绑定空间</button><button class="text-button" data-unbind>解除绑定</button></div><div class="company-choices" data-space-list></div></details>`:`<header><strong>未绑定开发空间</strong></header><div class="company-actions"><button class="secondary" data-spaces>发现可用空间</button>${config.lpai_enabled?`<a class="text-button" target="_blank" rel="noopener" href="${esc(context.connections.lpai_create_space_url||context.connections.lpai_url)}">前往 LPAI 创建 ↗</a>`:''}</div><div class="company-choices" data-space-list></div>`;
    let stopping=false;
    let starting=false;
    function updateSpaceControls(value=context,readback=false){
      if(!lp)return;
      lpBox.querySelector('[data-space-phase]').textContent=spacePhaseText(lp,readback);
      lpBox.querySelector('[data-space-pill]').textContent=spacePhaseLabel(lp);
      lpBox.querySelector('[data-space-resources]').textContent=spaceResourceText(lp,true);
      lpBox.querySelector('[data-space-resources]').hidden=!spaceResourceText(lp,true);
      const summary=spaceRuntimeSummary(value,lp);lpBox.querySelector('[data-space-summary]').textContent=summary;lpBox.querySelector('[data-space-summary]').hidden=!summary;
      lpBox.querySelector('[data-space-operation]').textContent=spaceOperationText(value.devspace_operation);
      lpBox.querySelector('[data-space-observation]').textContent=spaceObservationText(value.devspace_observation);
      const reason=spaceStopReason(value,lp);
      lpBox.querySelector('[data-space-stop-reason]').textContent=reason;
      lpBox.querySelector('[data-stop]').disabled=stopping||starting||!!reason;
      lpBox.querySelector('[data-start]').disabled=stopping||starting||spaceStartDisabled(value,lp);
      const sshStatus=lpBox.querySelector('[data-ssh-status]'),sshSave=lpBox.querySelector('[data-ssh-save]');
      if(sshStatus&&sshSave){const ssh=spaceSSHState(lp,value.devspace_ssh);sshStatus.textContent=ssh.message;sshSave.disabled=ssh.disabled;lpBox.querySelector('[data-ssh-auto]').disabled=ssh.disabled;lpBox.querySelector('[data-ssh-summary]').textContent=ssh.label;}
    }
    updateSpaceControls();
    lpBox.querySelector('[data-spaces]').onclick=safe(async()=>{spaces=(await api('/api/company/lpai',{operation:'spaces'})).items;if(sessionId!==current()||!lpBox.isConnected)return;renderSpaces(lpBox.querySelector('[data-space-list]'),sessionId,lp);});
    lpBox.querySelector('[data-start]')?.addEventListener('click',safe(async()=>{
      if(starting||stopping||sessionId!==current()||!lpBox.isConnected||spaceStartDisabled(context,lp))return;
      starting=true;updateSpaceControls();
      try{await action({action:'lpai_start'});}
      finally{starting=false;if(sessionId===current()&&lpBox.isConnected)updateSpaceControls();}
    }));
    lpBox.querySelector('[data-stop]')?.addEventListener('click',async()=>{
      if(stopping||starting||sessionId!==current()||!lpBox.isConnected)return;
      stopping=true;
      const button=lpBox.querySelector('[data-stop]');button.disabled=true;
      lpBox.querySelector('[data-start]').disabled=true;
      let sent=false;
      try{
        const result=await requestManualSpaceStop({api:(...args)=>{sent=true;return api(...args);},current,loadContext,binding:lp,confirm:message=>confirm(message),operationId:()=>crypto.randomUUID()});
        if(sessionId!==current()||!lpBox.isConnected)return;
        if(!result){stopping=false;updateSpaceControls();return;}
        stopping=false;
        updateSpaceControls({...context,devspace_operation:result});
        toast(result.message||spaceOperationText(result));await render();
      }catch(error){
        if(sessionId!==current()||!lpBox.isConnected)return;
        stopping=false;
        if(sent){
          lpBox.querySelector('[data-space-summary]').textContent='停止结果未知，请刷新核对。';lpBox.querySelector('[data-space-summary]').hidden=false;
          lpBox.querySelector('[data-space-operation]').textContent='停止请求结果未知，请读回原空间核对；不会自动重发。';
          lpBox.querySelector('[data-space-stop-reason]').textContent='先核对原停止请求，暂不可再次启停。';
        }else await render();
        toast(error.message);
      }
    });
    const refreshButton=lpBox.querySelector('[data-refresh]');
    if(refreshButton)refreshButton.onclick=safe(async()=>{
      refreshButton.disabled=true;
      try{
        const {item}=await api('/api/company/refresh-space',{session_id:sessionId,uid:lp.uid});
        if(sessionId!==current()||!lpBox.isConnected)return;
        if(item.uid!==lp.uid||item.namespace!==lp.scope)throw Error('官方返回的空间与本项目绑定不符，请核对绑定');
        lp.observation=item;
        if(item.phase==='not_found')updateSpaceControls(context,true);
        const value=await loadContext();
        if(sessionId!==current()||!lpBox.isConnected||!value)return;
        const bound=value.bindings.find(item=>item.kind==='lpai');
        if(!bound||bound.uid!==lp.uid||bound.scope!==lp.scope||bound.name!==lp.name){await render();return;}
        updateSpaceControls(value,true);
        toast(item.phase==='not_found'?'平台未找到原空间。可解除绑定，或发现可用空间并换绑。':spacePhaseText(lp,true));
      }catch(error){
        if(sessionId!==current()||!lpBox.isConnected)return;
        lpBox.querySelector('[data-space-phase]').textContent='当前状态未确认 · 上次记录 '+lp.observation.phase+' · '+lp.scope;
        lpBox.querySelector('[data-space-pill]').textContent='状态待核对';lpBox.querySelector('[data-space-summary]').textContent='状态读取未完成，请刷新核对。';lpBox.querySelector('[data-space-summary]').hidden=false;
        lpBox.querySelector('[data-start]').disabled=true;lpBox.querySelector('[data-stop]').disabled=true;
        lpBox.querySelector('[data-space-stop-reason]').textContent='状态读回未完成，先核对原空间与启停请求。';
        toast(error.message+'。请核对当前账号、工作空间与绑定，或发现可用空间并换绑。');
      }finally{refreshButton.disabled=false;}
    });
    lpBox.querySelector('[data-unbind]')?.addEventListener('click',safe(async()=>{if(!confirm('解除本项目绑定？不会停止或删除云端空间。'))return;await api('/api/company/unbind',{session_id:current(),kind:'lpai',uid:lp.uid});await render();}));
    if(lp){
      const ssh=document.createElement('details');ssh.className='devspace-ssh';
      ssh.innerHTML='<summary><strong>SSH</strong><span data-ssh-summary></span></summary><div class="devspace-ssh-body"><p class="small" data-ssh-status role="status"></p><button type="button" class="secondary" data-ssh-auto>自动识别并验证</button><details class="ssh-manual"><summary>手动配置</summary><label>LPAI 官方 SSH 连接命令<input data-ssh-command autocomplete="off" spellcheck="false" placeholder="自动读取官方端点，通常无需填写"></label><details class="ssh-key-options"><summary>密钥选项</summary><label>已有私钥文件（可选）<input data-ssh-identity autocomplete="off" spellcheck="false" placeholder="服务电脑上的文件路径，不粘贴私钥"></label><p class="small">复用已有密钥，仅保存文件路径。</p></details><button type="button" class="secondary" data-ssh-save>保存并验证 SSH</button></details></div>';
      const status=ssh.querySelector('[data-ssh-status]'), input=ssh.querySelector('input'), button=ssh.querySelector('[data-ssh-save]'),autoButton=ssh.querySelector('[data-ssh-auto]');
      const profile=context.devspace_ssh;
      const initialSSH=spaceSSHState(lp,profile);status.textContent=initialSSH.message;button.disabled=autoButton.disabled=initialSSH.disabled;
      ssh.querySelector('[data-ssh-summary]').textContent=initialSSH.label;
      if(profile?.port)input.value='ssh -p '+profile.port+' root@ssh-d.lpai.lixiang.com';
      const draftKey=JSON.stringify([sessionId,lp.scope,lp.uid]),draft=sshDrafts.get(draftKey),identity=ssh.querySelector('[data-ssh-identity]');
      if(draft){input.value=draft.command;identity.value=draft.identity;}
      ssh.open=draft?.open??false;
      const keyOptions=ssh.querySelector('.ssh-key-options');keyOptions.open=draft?.keyOpen??false;
      const rememberDraft=()=>sshDrafts.set(draftKey,{command:input.value,identity:identity.value,open:ssh.open,keyOpen:keyOptions.open});
      input.oninput=identity.oninput=rememberDraft;
      ssh.ontoggle=()=>{if(ssh.isConnected)rememberDraft();};
      keyOptions.ontoggle=()=>{if(ssh.isConnected)rememberDraft();};
      autoButton.onclick=async()=>{
        if(button.disabled||autoButton.disabled||spaceSSHState(lp,context.devspace_ssh).disabled||sessionId!==current()||!lpBox.isConnected)return;
        button.disabled=autoButton.disabled=true;status.textContent='正在读取官方端点并验证本机已有密钥…';
        try{
          const result=await api('/api/company/devspace-ssh',{session_id:sessionId,space_uid:lp.uid,auto:true});
          if(sessionId!==current()||!lpBox.isConnected)return;
          context.devspace_ssh=result;const state=spaceSSHState(lp,result);status.textContent=state.message;
          if(state.verified){input.value='';identity.value='';ssh.open=false;rememberDraft();await render();}
        }catch(error){if(sessionId===current()&&lpBox.isConnected)status.textContent=error.message;}
        finally{button.disabled=autoButton.disabled=spaceSSHState(lp,context.devspace_ssh).disabled;}
      };
      button.onclick=async()=>{
        if(button.disabled||spaceSSHState(lp,context.devspace_ssh).disabled||sessionId!==current()||!lpBox.isConnected)return;
        button.disabled=true;
        status.textContent='正在验证 SSH…';
        try{
          const result=await api('/api/company/devspace-ssh',{session_id:sessionId,space_uid:lp.uid,command:input.value.trim()||null,identity_path:identity.value.trim()||null});
          if(sessionId!==current()||!lpBox.isConnected)return;
          context.devspace_ssh=result;
          const state=spaceSSHState(lp,result);status.textContent=state.message;
          ssh.querySelector('[data-ssh-summary]').textContent=state.label;
          if(state.verified){input.value='';identity.value='';ssh.open=false;rememberDraft();await render();}
        }catch(error){const state=spaceSSHState(lp,context.devspace_ssh);status.textContent=state.disabled?state.message:error.message;}
        finally{button.disabled=spaceSSHState(lp,context.devspace_ssh).disabled;}
      };
      lpBox.querySelector('.devspace-binding').before(ssh);
    }
    lpBox.hidden=selectedExecution!=='lpai_devspace';host.append(lpBox);
    if(focusField&&previousSpaceKey===spaceKey&&selectedExecution==='lpai_devspace'){
      const field=lpBox.querySelector('['+focusField+']');field?.focus({preventScroll:true});
      if(selection.every(Number.isInteger))field?.setSelectionRange(...selection);
    }
    if(selectedExecution==='lpai')host.append(componentOverview({context,onRuns:()=>$('#project-run-results').scrollIntoView({block:'start',behavior:'smooth'}),onWatch:()=>{const details=$('.session-run-bind');details.open=true;details.scrollIntoView({block:'nearest',behavior:'smooth'});}}));
    if(runtime.showResources){
      const preference=context.component_resource_preference,key=JSON.stringify([sessionId,config.revision,preference?.queue_key,preference?.account_scope]);
      if(!resourceDirectories.has(key))resourceDirectories.set(key,componentResourceDirectory({live:()=>sessionId===current(),createPanel:()=>componentResourcePanel({api,sessionId,current,preference,toast})}));
      host.append(resourceDirectories.get(key));
    }
    const gitBox=document.createElement('section');gitBox.className='record-card company-card';
    gitBox.innerHTML=`<header><span>GitLab 源码仓库</span><span>${config.gitlab_enabled?'已启用':'未启用'}</span></header>${git?`<h3>${esc(git.name)}</h3><div class="company-actions"><button class="secondary" data-clone>克隆源码</button><a class="secondary" href="${esc(git.observation.web_url)}" target="_blank" rel="noopener">打开仓库</a></div>`:'<p>使用官方glab已有授权发现仓库，绑定后再克隆与管理分支。</p>'}<button class="secondary" data-repositories>发现授权仓库</button><div data-repo-list></div><details class="git-controls"><summary>Git 状态与操作</summary><div class="company-actions"><button class="secondary" data-git-read="status">状态</button><button class="secondary" data-git-read="branches">分支</button><button class="secondary" data-git-read="log">提交历史</button><button class="secondary" data-git-read="diff">变更摘要</button></div><pre class="git-output" data-git-output hidden></pre><label>分支名称<input data-branch placeholder="例如：experiment/baseline"></label><div class="company-actions"><button class="secondary" data-git-action="git_branch">新建分支</button><button class="secondary" data-git-action="git_switch">切换</button><button class="secondary" data-git-action="git_merge">合并</button><button class="secondary" data-git-action="git_pull">拉取</button><button class="secondary" data-git-action="git_push">推送</button></div><label>本次提交文件 <span>每行一个相对路径</span><textarea data-files rows="2"></textarea></label><label>提交说明<input data-commit-message></label><button class="secondary" data-commit>提交所列文件</button></details>`;
    gitBox.querySelector('[data-repositories]').onclick=safe(async()=>{repositories=(await api('/api/company/gitlab',{operation:'repositories'})).items;renderRepositories(gitBox.querySelector('[data-repo-list]'));});
    gitBox.querySelector('[data-clone]')?.addEventListener('click',safe(()=>action({action:'git_clone'})));
    gitBox.querySelectorAll('[data-git-read]').forEach(button=>button.onclick=safe(async()=>{const result=await api('/api/company/git',{session_id:current(),operation:button.dataset.gitRead});const box=gitBox.querySelector('[data-git-output]');box.hidden=false;box.textContent=result.text||'无待显示记录';}));
    gitBox.querySelectorAll('[data-git-action]').forEach(button=>button.onclick=safe(()=>action({action:button.dataset.gitAction,branch:gitBox.querySelector('[data-branch]').value.trim()})));
    gitBox.querySelector('[data-commit]').onclick=safe(()=>action({action:'git_commit',message:gitBox.querySelector('[data-commit-message]').value.trim(),files:gitBox.querySelector('[data-files]').value.split('\n').map(s=>s.trim()).filter(Boolean)}));
    const sourceSettings=document.createElement('details');sourceSettings.className='run-source-settings';sourceSettings.innerHTML='<summary>源码连接与 Git 操作</summary>';sourceSettings.append(gitBox);host.append(sourceSettings);
    if(git){const button=document.createElement('button');button.className='secondary';button.textContent='检查账号与源码传输';button.onclick=safe(async()=>{button.disabled=true;try{const result=await api('/api/company/check',{platform:'gitlab',session_id:current()});toast(result.checks.map(c=>`${labels[c.kind]}：${c.state==='passed'?'读取通过':c.message}`).join('；'));}finally{button.disabled=false;}});gitBox.querySelector('.company-actions').append(button);}
    const runsHost=$('#project-run-results');runsHost.replaceChildren();
    const heading=document.createElement('h3');heading.textContent='项目任务与历史';runsHost.append(heading);
    const list=document.createElement('div');list.id='session-run-list';runsHost.append(list);
    const watch=document.createElement('section');watch.className='record-card company-card';watch.innerHTML='<details class="session-run-bind"><summary>绑定已有任务</summary><p>仅将选中的运行绑定到当前会话，平台管理请在LPAI中进行。</p><button class="secondary" data-discover-tasks>选择排队或运行中的任务</button><label data-task-select-label hidden>选择一次运行<select data-task-select></select></label><details><summary>手动填写任务 UID</summary><label>任务UID<input data-task-uid placeholder="一次任务的UID，不是组件UID"></label></details><p class="small" data-selected-task>尚未选择任务</p><label class="check-label"><input type="checkbox" data-auto-resume checked>完成后分析结果</label><button class="secondary" data-watch>绑定本会话</button></details>';
    watch.querySelector('[data-discover-tasks]').onclick=safe(async()=>{
      const result=await api('/api/company/lpai',{operation:'tasks'}),select=watch.querySelector('[data-task-select]');
      select.innerHTML='<option value="">选择一个任务</option>';
      for(const item of result.items.filter(item=>!['succeeded','completed','success','failed','error','stopped','terminated','cancelled','canceled'].includes(item.phase.toLowerCase()))){const option=document.createElement('option');option.value=item.uid;option.textContent=`${item.name} · ${item.phase} · ${item.uid.slice(-8)}`;select.append(option);}
      watch.querySelector('[data-task-select-label]').hidden=false;
      watch.querySelector('[data-selected-task]').textContent=result.items.length?'仅显示查询范围内尚未结束的任务；绑定前核对确切UID，不会监控其他任务。':'未返回任务；可在高级入口填写已有UID。';
    });
    watch.querySelector('[data-task-select]').onchange=e=>{
      watch.querySelector('[data-task-uid]').value=e.target.value;
      watch.querySelector('[data-selected-task]').textContent=e.target.value?e.target.selectedOptions[0].textContent:'尚未选择任务';
    };
    watch.querySelector('[data-watch]').onclick=safe(async()=>{await api('/api/company/watch',{session_id:current(),uid:watch.querySelector('[data-task-uid]').value.trim(),auto_resume:watch.querySelector('[data-auto-resume]').checked});await render();});
    const audit=document.createElement('details');audit.className='session-run-history';audit.innerHTML='<summary>通知与诊断证据</summary>';watch.append(audit);
    for(const notice of context.training?.notices||[]){const note=document.createElement('p');note.className='connection-status';note.textContent=notice.text;audit.append(note);}
    for(const proof of context.training?.diagnostics||[]){const card=document.createElement('details');const heading=document.createElement('summary');heading.textContent=`失败诊断 · ${proof.summary.task.name}`;card.append(heading);const body=document.createElement('p');body.style.whiteSpace='pre-wrap';body.textContent=proof.summary.signals.map(s=>s.text).join('\n')+`\n证据文件：${proof.path}\n${proof.summary.partial?'取证不完整，结论须保留边界。':'仅为日志证据，根因及修复须结合代码核实。'}`;card.append(body);audit.append(card);}
    const submissions=document.createElement('section');submissions.className='component-submissions';
    const retained=document.createElement('details');retained.className='component-preview-history';
    const retainedTitle=document.createElement('summary');retained.append(retainedTitle);let retainedCount=0;
    const submissionLabels={prepared:'待审核',requested:'提交结果待核对',unknown:'结果未知',pending_approval:'待平台审批'};
    for(const prepared of (context.training?.submissions||[]).filter(s=>Object.hasOwn(submissionLabels,s.state))){
      const card=document.createElement('article');card.className='company-choice';const p=prepared.preview;
      card.innerHTML=`<strong>${esc(p.task_name)} · ${submissionLabels[prepared.state]}</strong><p>固定组件 ${esc(p.component.name)} · ${p.component.gpu_count} GPU · 预计 ${p.task_information.expected_duration} 分钟</p><p>${esc(p.change_summary||p.reason)}</p>`;
      if(p.patches?.length){const changes=document.createElement('details');const title=document.createElement('summary');title.textContent='仅本任务的源码与入口改动';const text=document.createElement('pre');text.textContent=p.patches.map(patch=>`${patch.path}\n${patch.value}`).join('\n\n');changes.append(title,text);card.append(changes);}
      if(prepared.state==='prepared'&&selectedExecution==='lpai'){
        const button=document.createElement('button');button.className='secondary';button.textContent='审核并提交这次任务';button.onclick=safe(()=>action({action:'lpai_submit',submission_id:prepared.id}));card.append(button);
      }
      if(['unknown','requested'].includes(prepared.state)){
        const note=document.createElement('p');note.textContent='先选择原任务 UID，再核对本次提交；结果未知时不会重新提交。';card.append(note);
        const button=document.createElement('button');button.className='secondary';button.textContent='核对已发现的原任务';button.onclick=safe(async()=>{const taskUid=watch.querySelector('[data-task-uid]').value.trim();if(!taskUid){watch.querySelector('.session-run-bind').open=true;return toast('先从上方任务列表选择原运行，再核对；不会重新提交。');}await api('/api/company/reconcile-training',{session_id:current(),submission_id:prepared.id,task_uid:taskUid});await render();});card.append(button);
      }
      if(prepared.state==='prepared'&&selectedExecution!=='lpai'){
        const note=document.createElement('p');note.textContent='保留的组件任务预览。当前运行位置不使用组件库；切换并核对后续方案后才可审核提交。';card.append(note);retained.append(card);retainedCount++;
      }else submissions.append(card);
    }
    if(submissions.children.length){const title=document.createElement('h4');title.textContent='组件任务提交与核对';watch.append(title,submissions);}
    if(retainedCount){retainedTitle.textContent='保留的组件任务预览 · '+retainedCount;watch.append(retained);}
    runsHost.append(watch);await runUI.render();
  }
  function renderSpaces(host,sessionId,bound){host.innerHTML=spaces.length?'':'<p>未发现授权可见的开发空间。</p>';for(const item of spaces){const row=document.createElement('article');row.className='company-choice';row.innerHTML=`<strong>${esc(item.name)}</strong><p>${esc(item.phase)} · ${esc(item.uid)}</p><button class="secondary">${bound?(bound.uid===item.uid?'已绑定':'换绑到此空间'):'绑定本项目'}</button>`;const button=row.querySelector('button');button.disabled=bound?.uid===item.uid;button.onclick=safe(async()=>{if(sessionId!==current()||!host.isConnected)return;if(bound&&!confirm('将本项目从 '+bound.name+' 换绑到 '+item.name+'？'))return;await api('/api/company/bind-space',{session_id:sessionId,name:item.name,uid:item.uid});toast('已核对UID并绑定；没有启动训练');await render();});host.append(row);}}
  function renderRepositories(host){host.innerHTML=repositories.length?'':'<p>没有返回成员权限仓库。</p>';for(const item of repositories){const row=document.createElement('article');row.className='company-choice';row.innerHTML=`<strong>${esc(item.path_with_namespace)}</strong><p>默认分支：${esc(item.default_branch||'未设置')}</p><button class="secondary">绑定本项目</button>`;row.querySelector('button').onclick=safe(async()=>{if(!current())return toast('请先选择研究会话');await api('/api/company/bind-repository',{session_id:current(),project_id:item.id});toast('仓库已绑定，尚未克隆或修改');await render();});host.append(row);}}
  $('#company-form').onsubmit=safe(async e=>{e.preventDefault();await save();toast('公司连接已保存');$('#company-dialog').close();await render();});
  for(const [selector,platform] of [['#company-check-lpai','lpai'],['#company-check-gitlab','gitlab']]){
    $(selector).onclick=safe(async()=>{const button=$(selector);button.disabled=true;button.setAttribute('aria-busy','true');try{$('#company-form').elements[platform+'_enabled'].checked=true;await save();const revision=config.revision,result=await checkConnection(platform,revision,current()||null);if(config.revision===revision&&$('#company-dialog').open)showHealth(platform,result.checks);}finally{button.disabled=false;button.removeAttribute('aria-busy');}});
  }
  async function connectPlatform(platform){
    const button=$('#company-login-'+platform);button.disabled=true;
    try{
      if(!config[platform+'_enabled']){$('#company-form').elements[platform+'_enabled'].checked=true;await save();}
      $(`#company-${platform}-status`).textContent='正在自动检查并复用本机登录…';
      const revision=config.revision,result=await checkConnection(platform,revision,current()||null);
      if(config.revision!==revision||!$('#company-dialog').open)return;
      showHealth(platform,result.checks);await showLogin(platform);
    }finally{button.disabled=false;}
  }
  $('#company-login-lpai')?.addEventListener('click',safe(()=>connectPlatform('lpai')));
  $('#company-login-gitlab')?.addEventListener('click',safe(()=>connectPlatform('gitlab')));
  return {open:safe(open),render:safe(render),renderRuns:safe(runUI.render)};
}
