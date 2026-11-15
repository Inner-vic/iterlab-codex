import {escapeHTML as esc} from './markdown.js';

export function loginPanel(platform,options){
  const installed=kind=>options.items.find(row=>row.kind===kind)?.installed;
  const item=kind=>options.items.find(row=>row.kind===kind);
  const action=(kind,label)=>`<button type="button" class="secondary" data-platform-login="${kind}" ${installed(kind)?'':'disabled'}>${label}</button>`;
  const missing=(kind)=>installed(kind)?'':kind==='gitlab'?'<p class="small">请先在服务电脑安装 GitLab 官方 CLI（glab），然后重启 IterLab。</p>':'<p class="small">此工具尚未安装，请在服务电脑运行 <code>scripts/setup.ps1</code> 后重启服务。</p>';
  const section=(kind,title,description,url,label)=>{
    if(item(kind)?.login_state==='connected')return `<p class="small"><strong>${title}</strong> · 已验证，自动复用本机登录</p>`;
    if(item(kind)?.login_state==='attention')return `<p class="small"><strong>${title}</strong> · ${esc(item(kind).message||'连接待核对')}。先处理连接问题，无需重新填写凭据。</p>`;
    return `<section><strong>${title}</strong><p class="small">${description}</p><div class="company-actions"><a class="text-button" target="_blank" rel="noopener" href="${esc(url)}">${label} ↗</a>${action(kind,item(kind)?.login_state==='needs_login'?'补充登录':'自动检查并连接')}</div>${missing(kind)}</section>`;
  };
  const sections=platform==='lpai'?
    section('lpai_cli','LPAI Agent','用于开发空间与任务状态读取。缺少认证时，在官方页面获取自己的 Agent Token。',options.lpai_agent_url,'打开官方 Agent 页面')+
    section('lpai_sdk','训练 SDK','用于组件创建、训练提交和产物回收。缺少认证时，在个人中心获取训练 Token，不能使用上面的 Agent Token。',options.lpai_url,'打开 LPAI'):
    section('gitlab','GitLab','使用官方 glab 登录，浏览器授权由你完成，本项目不接收密码或 Token。',options.gitlab_url,'打开 GitLab');
  return `<div class="platform-onboarding">${sections}<p class="small">已有登录自动复用；仅缺项需要在本机独立窗口完成一次授权。程序自动保存配置并验证，无需编辑文件。</p><p class="small" data-login-message role="status"></p></div>`;
}

// Poll only local launcher state while its settings dialog is open. Never
// poll cloud jobs, invoke a model, or request credentials from a timer.
export function createLoginWatcher({readState,onComplete,isOpen,schedule=setTimeout,cancel=clearTimeout}){
  const pending=new Map();let timer=null,busy=false;
  function pause(){if(timer!==null)cancel(timer);timer=null;}
  function resume(){if(timer===null&&pending.size&&isOpen())timer=schedule(()=>{timer=null;void refresh();},1500);}
  async function refresh(){
    if(busy||!pending.size||!isOpen())return;
    pause();busy=true;
    try{
      const state=await readState();
      for(const [platform,{kinds,revision}] of [...pending]){
        if([...kinds].some(kind=>state.items.some(row=>row.kind===kind&&row.running)))continue;
        // Remove only after verification succeeded, so transient HTTP failures
        // remain retryable without launching the login window again.
        await onComplete(platform);if(pending.get(platform)?.revision===revision)pending.delete(platform);
      }
    }catch{/* keep pending and retry local state; no repeated login launch */}
    finally{busy=false;resume();}
  }
  return {add(platform,kind){const previous=pending.get(platform);pending.set(platform,{kinds:new Set([...(previous?.kinds||[]),kind]),revision:(previous?.revision||0)+1});resume();},refresh,resume,pause};
}
