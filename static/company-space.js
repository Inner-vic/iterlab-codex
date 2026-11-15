const unresolved=new Set(['requested','submitted','pending_approval','unknown']);
const phaseLabels={running:'运行中',ready:'已就绪',stopped:'已停止',terminated:'已停止',pending:'等待资源',starting:'启动中',creating:'创建中',stopping:'停止中',pending_approval:'待平台审批',not_found:'平台未找到',unknown:'未确认'};

export function spacePhaseLabel(binding){return phaseLabels[String(binding?.observation?.phase||'unknown').toLowerCase()]||'未确认';}

export function spaceRuntimeSummary(context,binding){
  const operation=context?.devspace_operation,action=operation?.action==='lpai_stop'?'停止':'启动',state=operation?.state;
  if(state==='pending_approval')return action+'请求待审批。';
  if(state==='requested')return action+'请求提交中。';
  if(state==='submitted')return action+'请求正在核对。';
  if(state==='unknown')return action+'结果未知，'+(context?.devspace_observation?.state==='waiting'?'正在核对。':'请读回核对。');
  if(String(binding?.observation?.phase).toLowerCase()==='not_found')return '原空间未找到，请核对绑定。';
  if(context?.devspace_stop?.blockers?.length)return '有执行或训练待核对，暂不可停止。';
  return '';
}

export function spacePhaseText(binding,readback=false){
  const phase=binding?.observation?.phase||'unknown';
  const label=phaseLabels[String(phase).toLowerCase()]||phase;
  return `${readback?'平台读回':'平台记录'}：${label} · ${binding.scope}`;
}

export function spaceResourceText(binding,compact=false){
  const resources=binding?.observation?.resources||{};
  const quantity=(value,pattern)=>{
    if(typeof value==='number')return Number.isFinite(value)&&value>=0?String(value):null;
    if(typeof value!=='string')return null;
    const text=value.trim();return pattern.test(text)?text:null;
  };
  const cpu=quantity(resources.cpu,/^\d+(?:\.\d+)?m?$/),gpu=quantity(resources.gpu,/^\d+$/);
  const memory=quantity(resources.memory,/^\d+(?:\.\d+)?(?:[KMGTPE]i?B?)?$/i);
  const parts=[];
  if(cpu!==null)parts.push(cpu+' CPU');
  if(gpu!==null)parts.push(gpu+' GPU');
  if(memory!==null)parts.push('内存 '+memory);
  if(!parts.length)return '';
  if(compact)return parts.join(' · ')+(gpu!==null&&Number(gpu)===0?'（CPU 空间）':'');
  return '官方资源：'+parts.join(' · ')+(gpu!==null&&Number(gpu)===0?'；未配置 GPU，不能作为 GPU 训练环境。':'');
}

export function spaceOperationText(operation){
  if(!operation)return '尚无本项目的启停请求记录。';
  const action=operation.action==='lpai_stop'?'停止':'启动';
  const labels={requested:'正在提交，等待平台确认',submitted:'平台已接收，等待读回确认',pending_approval:'待平台审批',unknown:'结果未知，先读回原空间；不会重复发送',already_stopped:'平台原本已停止',observed_stopped:'平台读回已停止',already_active:'平台原本已运行或正在启动',observed_active:'平台读回运行中',failed:'未完成'};
  const label=labels[operation.state]||'状态待核对';
  return `${action}请求：${label}${operation.message?'。'+operation.message:''}`;
}

export function spaceObservationText(observation){
  if(!observation)return '';
  const labels={waiting:'正在后台只读核对原请求，平台状态确认后更新。',observed_active:'后台已读回原空间运行中。',observed_stopped:'后台已读回原空间停止。',not_found:'当前账号或工作空间未找到原空间，请核对绑定。',read_failed:'最近一次后台读取未完成，原请求保持待核对；不会重发启停。',authority_changed:'连接或绑定已变化，后台核对已停止；请核对原空间。',owner_unavailable:'原请求所属项目不可用，后台核对已停止。',legacy_manual_only:'此历史请求需手动读回原空间核对。'};
  const text=labels[observation.state]||'原请求的后台核对状态待确认。';
  const checked=observation.last_checked_at||observation.last_success_at;
  const time=checked&&new Date(checked);
  return text+(time&&!Number.isNaN(time.getTime())?' 最近读取：'+time.toLocaleString():'');
}

export function spaceStopReason(context,binding){
  const protection=context?.devspace_stop;
  if(String(binding?.observation?.phase).toLowerCase()==='not_found')return '当前账号或工作空间未找到原绑定空间；可发现可用空间后换绑，或解除绑定。';
  if(!context?.connections?.config?.lpai_enabled)return 'LPAI 连接未启用，暂不可停止。';
  if(!protection)return '停止条件尚未确认，请读回状态。';
  if(protection.blockers?.length)return protection.blockers.map(item=>item.message||'仍有执行、训练或未决请求需核对，暂不可停止。').join('；');
  if(unresolved.has(context?.devspace_operation?.state))return '原启停请求尚未核对，暂不可再次停止；请读回原空间状态。';
  if(protection.available!==true)return '停止条件尚未通过，请读回状态并核对执行与训练。';
  const phase=String(binding?.observation?.phase||'unknown').toLowerCase();
  if(['stopped','terminated'].includes(phase))return '平台记录已停止，无需再次停止。';
  if(!['running','ready'].includes(phase))return '仅在平台状态为运行中或已就绪时可停止，请先读回状态。';
  return '';
}

export function spaceStartDisabled(context,binding){
  return !context?.connections?.config?.lpai_enabled||String(binding?.observation?.phase).toLowerCase()==='not_found'
    ||unresolved.has(context?.devspace_operation?.state);
}

export function spaceSSHState(binding,profile){
  const missing=String(binding?.observation?.phase).toLowerCase()==='not_found';
  const verified=!!profile?.verified_at&&(!profile.state||profile.state==='verified')&&!missing;
  return {disabled:missing,verified,label:missing?'待核对':verified?'已验证':'待验证',message:missing?'当前未找到原绑定空间；已保留 SSH 配置，请先核对或换绑后再验证。'
    :verified?'已验证 · 端口 '+profile.port+' · 提交前会再次验证':profile?.message||'尚未验证 SSH'};
}

export async function requestManualSpaceStop({api,current,loadContext,binding,confirm:confirmStop,operationId}){
  const sessionId=current();
  if(!sessionId)throw Error('先创建或选择研究会话');
  const context=await loadContext();
  if(sessionId!==current()||!context)throw Error('当前会话已变化，请重新核对停止目标');
  const bound=context.bindings.find(item=>item.kind==='lpai');
  if(!bound||bound.uid!==binding.uid||bound.scope!==binding.scope||bound.name!==binding.name)throw Error('本项目的开发空间绑定已变化，请刷新后重新核对');
  const reason=spaceStopReason(context,bound);
  if(reason)throw Error(reason);
  const config=context.connections.config;
  if(!confirmStop(`停止 LPAI 开发空间\n空间：${bound.name}\n资源范围：${bound.scope}\nUID：${bound.uid}\n连接：LPAI 已启用 · 配置版本 ${config.revision}\n\n将停止整个云空间并释放资源。这与停止 Codex 对话是独立操作。\n确认停止以上确切空间？`))return null;
  if(sessionId!==current())throw Error('当前会话已变化，请重新核对停止目标');
  return api('/api/company/stop-space',{session_id:sessionId,uid:bound.uid,id:operationId(),confirmed:true,expected_authority:context.authority});
}
