// Collapsed navigation is a view of existing identities, never a research action.
const nameOf=project=>String(project.title||'未命名项目').trim()||'未命名项目';
export function projectMonogram(title){
  const text=String(title||'').normalize('NFKC').trim(),han=text.match(/\p{Script=Han}/gu);
  if(han?.length)return han.slice(0,2).join('');
  const words=text.match(/[\p{L}\p{N}]+/gu)||[];
  return (words.length>1?words.slice(0,2).map(w=>[...w][0]).join(''):[...(words[0]||'项目')].slice(0,2).join('')).toLocaleUpperCase();
}
export function projectMarkers(projects=[]){
  const groups=new Map();
  for(const p of projects){const key=projectMonogram(nameOf(p));if(!groups.has(key))groups.set(key,[]);groups.get(key).push(String(p.id));}
  return projects.map(p=>{
    const title=nameOf(p),monogram=projectMonogram(title),ids=groups.get(monogram),id=String(p.id);let suffix='';
    if(ids.length>1){let length=2;while(length<Math.max(...ids.map(v=>v.length))&&new Set(ids.map(v=>v.slice(-length))).size<ids.length)length++;suffix=id.slice(-length);}
    return {id:p.id,title,monogram,suffix,caption:[...title].slice(0,5).join('')};
  });
}
export function resolveProjectSession(state,projectId,sessionId){
  if(!state.projects.some(p=>p.id===projectId))return null;
  return state.sessions.find(s=>s.id===sessionId&&s.project_id===projectId)||null;
}
const text=(element,value)=>{if(element.textContent!==String(value))element.textContent=String(value);};
function reorder(host,nodes){let cursor=host.firstChild;for(const node of nodes){if(node===cursor)cursor=cursor.nextSibling;else host.insertBefore(node,cursor);}}
function moveFocus(event,buttons){
  if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key)||!buttons.length)return;
  const index=buttons.indexOf(document.activeElement),next=event.key==='Home'?0:event.key==='End'?buttons.length-1:index<0?(event.key==='ArrowDown'?0:buttons.length-1):(index+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length;
  event.preventDefault();buttons[next].focus();
}

export function installProjectSwitcher({host,expandButton,getState,onSelect,onCreate,onManage,onExpand,onError}){
  const panel=document.createElement('div');panel.id='project-session-switcher';panel.className='project-switcher';panel.setAttribute('popover','auto');panel.setAttribute('role','dialog');panel.setAttribute('aria-labelledby','switcher-project-name');
  panel.innerHTML='<header class="switcher-header"><div><span>选择会话</span><h2 id="switcher-project-name"></h2></div><button type="button" class="icon-button" data-close aria-label="关闭会话切换">×</button></header><p class="switcher-workspace" data-workspace></p><label class="switcher-search"><input type="search" aria-label="筛选此项目会话" placeholder="查找会话名称…" autocomplete="off"></label><p class="switcher-count" role="status" data-count></p><div class="switcher-sessions" aria-label="项目中的会话"></div><p class="switcher-empty" data-empty hidden></p><p class="switcher-error" role="alert" data-error hidden></p><footer><button type="button" class="secondary" data-create>＋ 新建会话</button><button type="button" class="text-button" data-manage>项目选项</button></footer>';
  const hint=document.createElement('div');hint.id='project-rail-hint';hint.className='project-rail-hint';hint.setAttribute('popover','manual');hint.setAttribute('role','tooltip');
  document.body.append(panel,hint);
  const projects=new Map(),sessions=new Map(),search=panel.querySelector('input'),list=panel.querySelector('.switcher-sessions'),error=panel.querySelector('[data-error]');
  let opened=null,version=null,creating=false,hintFor=null,previousCurrent=null,openGeneration=0;
  const isOpen=()=>panel.matches(':popover-open');
  function hideHint(){if(hint.matches(':popover-open'))hint.hidePopover();if(hintFor)projects.get(hintFor)?.removeAttribute('aria-describedby');hintFor=null;}
  function close(restore=false){const anchor=projects.get(opened);openGeneration++;opened=null;if(isOpen())panel.hidePopover();for(const b of projects.values())b.setAttribute('aria-expanded','false');hideHint();if(restore)(anchor?.isConnected?anchor:expandButton).focus({preventScroll:true});}
  function place(){
    const anchor=projects.get(opened);if(!anchor||!isOpen())return;
    const r=anchor.getBoundingClientRect(),box=panel.getBoundingClientRect(),viewport=host.getBoundingClientRect();
    if(r.bottom<=viewport.top||r.top>=viewport.bottom){close();return;}
    panel.style.left=Math.max(8,Math.min(r.right+12,innerWidth-box.width-8))+'px';panel.style.top=Math.max(8,Math.min(r.top-10,innerHeight-box.height-8))+'px';
  }
  function showHint(id){
    if(isOpen()||!document.body.classList.contains('sidebar-collapsed'))return;
    const p=getState().projects.find(p=>p.id===id),button=projects.get(id);if(!p||!button)return;
    hideHint();hint.replaceChildren();const title=document.createElement('strong'),note=document.createElement('span');title.textContent=nameOf(p);note.textContent='点击选择会话';hint.append(title,note);
    if(getState().projects.filter(v=>nameOf(v)===nameOf(p)).length>1){const note=document.createElement('small');note.textContent='同名项目 · '+String(p.id).slice(-6);hint.append(note);}
    hint.showPopover();hintFor=id;button.setAttribute('aria-describedby',hint.id);const r=button.getBoundingClientRect(),box=hint.getBoundingClientRect();hint.style.left=Math.max(8,Math.min(r.right+12,innerWidth-box.width-8))+'px';hint.style.top=Math.max(8,Math.min(r.top,innerHeight-box.height-8))+'px';
  }
  function fail(message){error.hidden=false;text(error,message);onError?.(message);}
  async function choose(projectId,sessionId,capturedVersion,capturedOpen){
    if(!isOpen()||opened!==projectId||openGeneration!==capturedOpen)return;
    const state=getState();if(state.version!==capturedVersion||!resolveProjectSession(state,projectId,sessionId)){fail('项目或会话已变化，请重新选择；未打开其他会话。');render();return;}
    close();try{await onSelect(sessionId);}catch(e){onError?.(e.message);}
  }
  function renderSessions(){
    const state=getState(),project=state.projects.find(p=>p.id===opened);if(!project)return close();
    text(panel.querySelector('h2'),nameOf(project));panel.querySelector('[data-workspace]').hidden=true;
    const values=state.sessions.filter(s=>s.project_id===opened),query=search.value.trim().toLocaleLowerCase(),matching=values.filter(s=>String(s.title||'未命名会话').toLocaleLowerCase().includes(query));
    const desired=new Set(values.map(s=>s.id)),focused=document.activeElement,scroll=list.scrollTop;
    for(const [id,row] of sessions)if(!desired.has(id)){row.remove();sessions.delete(id);}
    for(const session of values){
      let row=sessions.get(session.id);if(!row){row=document.createElement('button');row.type='button';row.className='switcher-session';row.dataset.sessionId=session.id;row.innerHTML='<span class="switcher-session-copy"><strong></strong><small></small></span><span class="switcher-current">当前</span>';sessions.set(session.id,row);}
      const active=session.id===state.current,title=session.title||'未命名会话',duplicate=values.filter(s=>s.title===session.title).length>1;
      text(row.querySelector('strong'),title);text(row.querySelector('small'),duplicate?'会话标识 · '+session.id.slice(-8):'打开已有会话');row.querySelector('small').hidden=!duplicate;
      row.querySelector('.switcher-current').hidden=!active;row.setAttribute('aria-current',active?'page':'false');row.setAttribute('aria-label',title+(duplicate?'，会话 '+session.id.slice(-8):'')+(active?'，当前会话':''));
      row.hidden=!matching.some(s=>s.id===session.id);const pid=opened,captured=version,capturedOpen=openGeneration;row.onclick=()=>choose(pid,session.id,captured,capturedOpen);
    }
    reorder(list,values.map(s=>sessions.get(s.id)));list.scrollTop=scroll;
    if(focused&&list.contains(focused)&&document.activeElement!==focused)focused.focus({preventScroll:true});else if(focused?.classList?.contains('switcher-session')&&!focused.isConnected)search.focus({preventScroll:true});
    const empty=panel.querySelector('[data-empty]');empty.hidden=matching.length>0;text(empty,values.length?'没有匹配会话，可修改关键词。':'尚无会话；点击“新建会话”开始，不会自动创建。');
    text(panel.querySelector('[data-count]'),query?`${matching.length} / ${values.length} 个会话`:`${values.length} 个会话 · 选择后切换`);
    panel.querySelector('[data-create]').disabled=creating;place();
  }
  function open(id){
    const state=getState();if(!state.projects.some(p=>p.id===id))return;
    if(isOpen()&&opened===id){close(true);return;}
    close();opened=id;version=state.version;search.value='';error.hidden=true;list.replaceChildren();sessions.clear();renderSessions();
    panel.showPopover();projects.get(id)?.setAttribute('aria-expanded','true');place();search.focus({preventScroll:true});
  }
  function render(){
    const state=getState(),markers=projectMarkers(state.projects),ids=new Set(markers.map(p=>p.id)),focused=document.activeElement,scroll=host.scrollTop;
    const activeProject=state.sessions.find(s=>s.id===state.current)?.project_id||null;
    for(const [id,b] of projects)if(!ids.has(id)){b.remove();projects.delete(id);}
    for(const marker of markers){
      let button=projects.get(marker.id);if(!button){button=document.createElement('button');button.type='button';button.className='rail-project';button.dataset.projectId=marker.id;button.setAttribute('aria-haspopup','dialog');button.setAttribute('aria-controls',panel.id);button.innerHTML='<span class="rail-monogram"><span></span><small class="rail-disambiguator"></small></span><span class="rail-project-caption"></span>';projects.set(marker.id,button);
        button.onclick=()=>open(marker.id);button.addEventListener('pointerenter',()=>showHint(marker.id));button.addEventListener('pointerleave',()=>{if(document.activeElement!==button)hideHint();});button.addEventListener('focus',()=>showHint(marker.id));button.addEventListener('blur',hideHint);
      }
      const active=marker.id===activeProject;text(button.querySelector('.rail-monogram>span'),marker.monogram);text(button.querySelector('.rail-disambiguator'),marker.suffix);button.querySelector('.rail-disambiguator').hidden=!marker.suffix;text(button.querySelector('.rail-project-caption'),marker.caption);
      button.setAttribute('aria-label',(active?'当前项目：':'项目：')+marker.title+(marker.suffix?'，标识 '+marker.suffix:'')+'，点击选择会话');button.title=marker.title+' · 点击选择会话';button.setAttribute('aria-current',active?'true':'false');button.setAttribute('aria-expanded',String(isOpen()&&opened===marker.id));
    }
    reorder(host,markers.map(p=>projects.get(p.id)));host.scrollTop=scroll;
    if(focused&&host.contains(focused)&&document.activeElement!==focused)focused.focus({preventScroll:true});else if(focused?.classList?.contains('rail-project')&&!focused.isConnected)expandButton.focus({preventScroll:true});
    if(previousCurrent!==state.current&&document.body.classList.contains('sidebar-collapsed'))projects.get(activeProject)?.scrollIntoView({block:'nearest'});previousCurrent=state.current;
    if(opened){if(version!==state.version||!ids.has(opened))close();else{projects.get(opened)?.scrollIntoView({block:'nearest'});renderSessions();}}
  }
  panel.querySelector('[data-close]').onclick=()=>close(true);
  panel.querySelector('[data-create]').onclick=async()=>{
    const state=getState(),pid=opened;if(!isOpen()||creating||state.version!==version||!state.projects.some(p=>p.id===pid))return;
    creating=true;error.hidden=true;panel.querySelector('[data-create]').disabled=true;
    try{close();await onCreate(pid);}catch(e){onError?.(e.message);}finally{creating=false;if(isOpen())renderSessions();}
  };
  panel.querySelector('[data-manage]').onclick=()=>{const p=getState().projects.find(p=>p.id===opened),anchor=projects.get(opened);if(!p||getState().version!==version)return;close();onManage(p,anchor);};
  search.addEventListener('input',renderSessions);
  panel.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close(true);return;}if(event.target===search&&!['ArrowDown','ArrowUp'].includes(event.key))return;moveFocus(event,[...sessions.values()].filter(b=>!b.hidden));});
  panel.addEventListener('focusout',event=>{if(event.relatedTarget&&panel.contains(event.relatedTarget))return;setTimeout(()=>{if(isOpen()&&!panel.contains(document.activeElement))close();},0);});
  panel.addEventListener('toggle',()=>{if(!isOpen()){opened=null;for(const b of projects.values())b.setAttribute('aria-expanded','false');}});
  host.addEventListener('keydown',event=>{if(event.key==='Escape'){hideHint();return;}moveFocus(event,[...host.querySelectorAll('.rail-project')]);});
  host.addEventListener('scroll',()=>{hideHint();place();});window.addEventListener('resize',()=>{hideHint();place();});
  return {render,close,isOpen,setCollapsed(){close();render();}};
}
