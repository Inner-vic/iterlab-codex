import {escapeHTML as esc} from './markdown.js';
import {installNameEditor} from './name-editor.js';
const paths={pin:'m9 3 6 0-1 5 4 4v2H6v-2l4-4-1-5M12 14v7',edit:'m15 4 5 5M4 20l4-1L20 7a2 2 0 0 0-4-4L4 15v5',archive:'M3 4h18v4H3zM5 8v12h14V8M10 12h4',remove:'m6 6 12 12M6 18 18 6'};
const icon=name=>`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name]}"></path></svg>`;
export const projectMenuItems=project=>[
  {action:'pin',label:project.pinned?'取消置顶':'置顶',icon:'pin'},
  {action:'rename',label:'编辑',icon:'edit'},
  {action:'archive_chats',label:'归档聊天',icon:'archive'},
  {action:'remove',label:'删除项目',icon:'remove'}
];

export function installProjectRemoval({api,toast,beforeRemove,onRemoved,onRestored,onChanged=onRestored}){
  const menu=document.createElement('div');menu.className='project-popup';menu.setAttribute('popover','auto');menu.setAttribute('role','menu');menu.setAttribute('aria-label','项目选项');
  const edit=installNameEditor({id:'edit-project-dialog',heading:'编辑项目',label:'项目名称',onSave:(project,title)=>manage(project,'rename',{title,expected_title:project.title}),onSaved:async()=>{await onChanged();toast('项目名称已更新');}});
  const confirm=document.createElement('dialog');confirm.id='project-action-dialog';confirm.innerHTML='<div class="dialog-header"><h2 data-heading></h2></div><strong data-title></strong><p data-description></p><label data-delete-choice hidden>删除方式<select data-delete-mode><option value="recycle">移入回收站（可恢复）</option><option value="permanent">永久删除本地记录（不可恢复）</option></select></label><label data-purge-label hidden>输入“确认”或“确定”<input data-purge-name autocomplete="off" placeholder="确认"></label><div class="dialog-actions"><button class="secondary" data-cancel>取消</button><button class="primary" data-confirm>确认</button></div>';
  const recovery=document.createElement('dialog');recovery.id='project-recovery-dialog';recovery.innerHTML='<div class="dialog-header"><h2>归档与回收站</h2></div><p class="muted">归档与回收站可恢复；永久删除不可恢复。工作文件、Codex原生历史及云任务不在删除范围内。</p><h3>已归档的聊天</h3><div data-chats></div><h3>已移除的项目</h3><div data-projects></div>';
  document.body.append(menu,confirm,recovery);let selected=null,pending=null;
  for(const dialog of [confirm,recovery]){const error=document.createElement('p');error.dataset.error='';error.className='project-dialog-error';error.hidden=true;error.setAttribute('role','alert');dialog.querySelector('.dialog-header').after(error);}
  confirm.querySelector('[data-cancel]').onclick=()=>confirm.close();
  const safe=(fn,dialog=null)=>async(...args)=>{if(dialog)dialog.querySelector('[data-error]').hidden=true;try{await fn(...args);}catch(error){if(dialog?.open){const status=dialog.querySelector('[data-error]');status.textContent=error.message;status.hidden=false;status.scrollIntoView({block:'nearest'});}else toast(error.message);}};
  function hide(){if(menu.matches(':popover-open'))menu.hidePopover();}
  async function manage(project,action,extra={}){return api(`/api/projects/${project.id}/manage`,{action,...extra});}
  function updateDeleteChoice(){
    if(pending?.action!=='delete_choice')return;
    const permanent=confirm.querySelector('[data-delete-mode]').value==='permanent';
    confirm.querySelector('[data-heading]').textContent=permanent?'永久删除本地记录？':pending.project.kind==='project'?'删除项目？':'删除会话？';
    confirm.querySelector('[data-description]').textContent=permanent?'不可恢复，将删除本地对话、研究记录和关联索引。工作目录文件、Codex原生历史、已有备份和云端任务不删除。':'移入回收站后从正常列表消失，可恢复；自动研究暂停，不取消云端任务。归档只用于收纳聊天，与删除无关。';
    confirm.querySelector('[data-purge-label]').hidden=!permanent;
    confirm.querySelector('[data-confirm]').textContent=permanent?'永久删除':'移入回收站';
    confirm.querySelector('[data-error]').hidden=true;
  }
  function openDelete(value,kind){
    pending={project:{...value,kind},action:'delete_choice'};
    confirm.querySelector('[data-title]').textContent=value.title;
    confirm.querySelector('[data-delete-choice]').hidden=false;
    confirm.querySelector('[data-delete-mode]').value='recycle';
    confirm.querySelector('[data-purge-name]').value='';
    updateDeleteChoice();confirm.showModal();
  }
  confirm.querySelector('[data-delete-mode]').onchange=updateDeleteChoice;
  async function act(action){
    const project=selected;hide();
    if(action==='pin'){await manage(project,'pin',{pinned:!project.pinned});await onChanged();return;}
    if(action==='rename'){
      edit.open(project);return;
    }
    if(action==='remove'){openDelete(project,'project');return;} pending={project:{...project},action};confirm.querySelector('[data-delete-choice]').hidden=true;
    confirm.querySelector('[data-purge-label]').hidden=true;
    confirm.querySelector('[data-error]').hidden=true;
    confirm.querySelector('[data-heading]').textContent=action==='remove'?'删除项目？':'归档项目中的聊天？';
    confirm.querySelector('[data-title]').textContent=project.title;
    confirm.querySelector('[data-description]').textContent=action==='remove'?'仅从侧栏移除，保留工作目录、聊天和研究记录。可在“归档与回收站”找回项目；恢复后研究仍保持暂停。':'归档该项目中的所有聊天，项目仍保留。可在“归档与回收站”找回聊天，不删除任何文件。有未完成任务时需先核对。';
    confirm.querySelector('[data-confirm]').textContent=action==='remove'?'删除项目':'归档聊天';confirm.showModal();
  }
  confirm.querySelector('[data-confirm]').onclick=safe(async()=>{
    const {project,action}=pending,button=confirm.querySelector('[data-confirm]');button.disabled=true;
    try{await beforeRemove();
      if(action==='delete_choice'){const permanent=confirm.querySelector('[data-delete-mode]').value==='permanent';await api(permanent?'/api/delete/permanent':'/api/trash',{kind:project.kind,id:project.id,...(permanent?{confirmation:confirm.querySelector('[data-purge-name]').value}:{})});confirm.close();if(project.kind==='project')await onRemoved(project.id);else await onChanged();toast(permanent?'本地记录已永久删除；文件与云任务未删除':'已移入回收站，可恢复');return;}
      if(action==='purge'){await api('/api/trash/purge',{kind:project.kind,id:project.object_id,confirmation:confirm.querySelector('[data-purge-name]').value});confirm.close();await onRestored();await showRecovery();toast('本地记录已永久删除；文件与云任务未删除');return;}
      if(action==='remove_session'){await api('/api/trash',{kind:'session',id:project.id});confirm.close();await onChanged();toast('会话已删除，可在回收站恢复');return;}
      const result=await manage(project,action,{confirmed:true});confirm.close();
      if(action==='remove'){await onRemoved(project.id);toast('项目已移除，目录和记录保留');}
      else{await onChanged();toast(`已归档 ${result.count} 个聊天，项目保留`);}
    }finally{button.disabled=false;}
  },confirm);
  async function showRecovery(){
    const [archived,removed]=await Promise.all([api('/api/archived-sessions'),api('/api/trash')]);
    for(const [host,items,kind] of [[recovery.querySelector('[data-chats]'),archived.items,'chat'],[recovery.querySelector('[data-projects]'),removed.items,'project']]){
      host.replaceChildren();if(!items.length){host.innerHTML='<p class="muted">暂无记录</p>';continue;}
      for(const item of items){const row=document.createElement('article');row.className='recovery-row';row.innerHTML=`<div><strong>${esc(item.title)}</strong>${kind==='chat'?`<small>${esc(item.project_title)}${item.project_removed?' · 请先恢复项目':''}</small>`:''}</div><button class="secondary">${kind==='chat'?'恢复聊天':'恢复项目'}</button>`;
        const button=row.querySelector('button');if(kind!=='chat')button.textContent=item.kind==='session'?'恢复会话':'恢复项目';button.disabled=Boolean(item.project_removed);button.onclick=safe(async()=>{button.disabled=true;try{
          if(kind==='chat')await api(`/api/sessions/${item.id}/unarchive`,{});else await api('/api/trash/restore',{kind:item.kind,id:item.object_id});
          await onRestored();await showRecovery();toast('已恢复，自动研究保持暂停');
        }catch(error){button.disabled=false;throw error;}},recovery);
        if(kind!=='chat'){const purge=document.createElement('button');purge.className='text-button';purge.textContent='永久删除';purge.onclick=()=>{pending={project:{...item},action:'purge'};confirm.querySelector('[data-delete-choice]').hidden=true;confirm.querySelector('[data-heading]').textContent='永久删除本地记录？';confirm.querySelector('[data-title]').textContent=item.title;confirm.querySelector('[data-description]').textContent='不可从回收站恢复，将删除该对象的本地对话、研究记录及关联索引。工作目录文件、Codex原生历史、已有备份和LPAI任务不删除。';confirm.querySelector('[data-purge-label]').hidden=false;confirm.querySelector('[data-purge-name]').value='';confirm.querySelector('[data-error]').hidden=true;confirm.querySelector('[data-confirm]').textContent='永久删除';confirm.showModal();};row.append(purge);}
        host.append(row);}
    }
    if(!recovery.open)recovery.showModal();
  }
  document.querySelector('#removed-projects').onclick=safe(showRecovery);document.querySelector('#removed-projects').textContent='归档与回收站';
  document.querySelector('#project-history').onclick=safe(showRecovery);document.querySelector('#project-history').setAttribute('aria-label','归档与回收站');document.querySelector('#project-history').title='归档与回收站';
  menu.addEventListener('keydown',event=>{
    const buttons=[...menu.querySelectorAll('button')],index=buttons.indexOf(document.activeElement);
    if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){event.preventDefault();buttons[event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length].focus();}
  });
  recovery.querySelectorAll('h3')[1].textContent='回收站（项目与会话）';
  return {deleteSession(session){openDelete(session,'session');},open(project,anchor){
    selected=project;hide();menu.replaceChildren();
    for(const item of projectMenuItems(project)){
      if(item.action==='remove'){const separator=document.createElement('hr');menu.append(separator);}
      const button=document.createElement('button');button.type='button';button.setAttribute('role','menuitem');button.dataset.action=item.action;
      button.innerHTML=icon(item.icon)+`<span>${item.label}</span>`;if(item.action==='remove')button.className='remove-project';
      button.onclick=safe(()=>act(item.action));menu.append(button);
    }
    const rect=anchor.getBoundingClientRect();menu.style.left=Math.max(8,Math.min(rect.right-12,innerWidth-232))+'px';menu.style.top=Math.max(8,Math.min(rect.bottom+4,innerHeight-184))+'px';
    menu.showPopover();menu.querySelector('button').focus();
  }};
}
