import {brandWelcome} from './brand.js';
const kindLabels={question:'研究问题',alignment:'需求共识',research:'资料调研',plan:'研究方案',branch:'探索分支',trial:'实验',result:'结果',decision:'研究决策',review:'轮次评审',delivery:'交付'};
const toolNames={research_snapshot:'读取研究记录',research_record:'保存研究记录',research_status:'核对研究状态',research_focus:'更新研究焦点',research_write:'写入研究代码',research_run:'执行受控实验',company_context:'核对公司连接'};
const toolType=item=>({commandExecution:'运行项目命令',fileChange:'修改项目文件',mcpToolCall:'调用扩展工具',dynamicToolCall:toolNames[item.text]||'调用研究工具',plan:'更新执行计划'}[item.kind]||'执行工具');
const rowKey=(role,id)=>JSON.stringify([role,id]);

export function createConversationView({host,markdown,escapeHTML,icon,date,onOpenRecord,onLoadOlder,expandedTurns=new Map(),requestFrame=requestAnimationFrame,cancelFrame=cancelAnimationFrame}){
  const doc=host.ownerDocument||globalThis.document;
  const htmlSources=new WeakMap(),markdownSources=new WeakMap(),revisions=new WeakMap(),selectionRoots=new WeakSet();
  let rows=new Map(),ordered=[],targets=new Map(),markdownCache=new Map(),references=[],sessionId;
  let generation=0,scheduled=null;
  const pending=new Map();

  const element=tag=>doc.createElement(tag);
  const changed=node=>revisions.set(node,(revisions.get(node)||0)+1);
  function setHTML(node,value){
    const html=String(value??'');selectionRoots.add(node);
    if(htmlSources.get(node)===html)return;
    node.innerHTML=html;htmlSources.set(node,html);changed(node);
  }
  function setText(node,value){
    const text=String(value??'');selectionRoots.add(node);
    if(node.textContent!==text){node.textContent=text;changed(node);}
  }
  function setClass(node,value){if(node.className!==value)node.className=value;}
  function readable(text){
    let value=String(text??'');
    for(const [from,to] of references)value=value.replaceAll(from,to);
    return value;
  }
  function renderMarkdown(node,text,id){
    const input=String(text??'');
    if(markdownSources.get(node)===input)return;
    let cached=markdownCache.get(id);
    if(!cached||cached.input!==input){cached={input,html:markdown(input)};markdownCache.set(id,cached);}
    setHTML(node,cached.html);markdownSources.set(node,input);
  }
  function reconcile(parent,nodes){
    let cursor=parent.firstChild;
    for(const node of nodes){
      if(node===cursor)cursor=cursor.nextSibling;
      else parent.insertBefore(node,cursor);
    }
    while(cursor){const next=cursor.nextSibling;parent.removeChild(cursor);cursor=next;}
  }

  // Geometry lookup is logarithmic in the keyed rows, including long activity groups.
  function firstAt(entries,top){
    let lo=0,hi=entries.length;
    while(lo<hi){const mid=(lo+hi)>>>1;if(entries[mid].node.getBoundingClientRect().bottom<=top)lo=mid+1;else hi=mid;}
    return entries[lo];
  }
  function captureScroll(){
    const state={nearBottom:host.scrollHeight-host.scrollTop-host.clientHeight<90,top:host.scrollTop,anchors:[]};
    if(state.nearBottom||!ordered.length||!host.getBoundingClientRect||!ordered[0].node.getBoundingClientRect)return state;
    const top=host.getBoundingClientRect().top+(host.clientTop||0),outer=firstAt(ordered,top);
    const remember=entry=>{if(entry){const rect=entry.node.getBoundingClientRect();if(rect.bottom>rect.top)state.anchors.push({node:entry.node,offset:rect.top-top});}};
    if(outer?.type==='activity'&&!outer.body.hidden&&outer.ordered.length&&outer.body.getBoundingClientRect().top<=top)remember(firstAt(outer.ordered,top));
    remember(outer);return state;
  }
  function restoreScroll(state){
    if(state.nearBottom){host.scrollTop=host.scrollHeight;return;}
    let top=state.top;
    for(const anchor of state.anchors){
      if(!host.contains(anchor.node))continue;
      const rect=anchor.node.getBoundingClientRect();
      if(rect.bottom<=rect.top)continue;
      top=host.scrollTop+rect.top-host.getBoundingClientRect().top-(host.clientTop||0)-anchor.offset;break;
    }
    if(host.scrollTop!==top)host.scrollTop=top;
  }

  // Retained nodes keep native selections. Text offsets also recover selections in
  // a changed Markdown body, without walking any other message's content.
  function textOffset(root,node,offset){
    let total=0,result=null;
    function walk(current){
      if(current===node){
        result=total+(current.nodeType===3?Math.min(offset,current.textContent.length):Array.from(current.childNodes).slice(0,offset).reduce((n,child)=>n+child.textContent.length,0));return true;
      }
      if(current.nodeType===3){total+=current.textContent.length;return false;}
      for(const child of current.childNodes)if(walk(child))return true;
      return false;
    }
    walk(root);return result;
  }
  function selectionPoint(node,offset){
    let root=node;
    while(root&&root!==host&&!selectionRoots.has(root))root=root.parentNode;
    if(root===host)root=null;
    return {node,offset,inside:host.contains(node),root,revision:root?revisions.get(root):null,text:root?textOffset(root,node,offset):null};
  }
  function captureSelection(){
    const selection=doc.getSelection?.()||doc.defaultView?.getSelection?.();
    if(!selection?.rangeCount||!host.contains(selection.anchorNode)&&!host.contains(selection.focusNode))return null;
    return {selection,anchor:selectionPoint(selection.anchorNode,selection.anchorOffset),focus:selectionPoint(selection.focusNode,selection.focusOffset)};
  }
  function textPoint(root,offset){
    let remaining=Math.max(0,offset),result,last;
    function walk(node){
      if(node.nodeType===3){last=node;if(remaining<=node.textContent.length){result={node,offset:remaining};return true;}remaining-=node.textContent.length;}
      else for(const child of node.childNodes)if(walk(child))return true;
      return false;
    }
    walk(root);return result||(last?{node:last,offset:last.textContent.length}:{node:root,offset:0});
  }
  function resolvePoint(point){
    const liveRoot=point.root&&host.contains(point.root);
    if(liveRoot&&point.text!==null&&revisions.get(point.root)!==point.revision)return textPoint(point.root,point.text);
    if(point.inside?host.contains(point.node):point.node?.isConnected){
      return {node:point.node,offset:Math.min(point.offset,point.node.nodeType===3?point.node.textContent.length:point.node.childNodes.length)};
    }
    return liveRoot&&point.text!==null?textPoint(point.root,point.text):null;
  }
  function restoreSelection(state){
    if(!state)return;
    const anchor=resolvePoint(state.anchor),focus=resolvePoint(state.focus),selection=state.selection;
    if(!anchor||!focus)return;
    if(selection.anchorNode===anchor.node&&selection.anchorOffset===anchor.offset&&selection.focusNode===focus.node&&selection.focusOffset===focus.offset)return;
    try{
      if(selection.setBaseAndExtent)selection.setBaseAndExtent(anchor.node,anchor.offset,focus.node,focus.offset);
      else if(selection.extend){selection.collapse(anchor.node,anchor.offset);selection.extend(focus.node,focus.offset);}
      else {const range=doc.createRange();range.setStart(anchor.node,anchor.offset);range.setEnd(focus.node,focus.offset);selection.removeAllRanges();selection.addRange(range);}
    }catch{/* A removed selection endpoint cannot always be restored. */}
  }
  function preserveView(update){
    const scroll=captureScroll(),selection=captureSelection();
    update();restoreSelection(selection);restoreScroll(scroll);
  }

  function initialize(entry,type,item){
    if(entry.type===type)return;
    const row=entry.node;row.replaceChildren();htmlSources.delete(row);markdownSources.delete(row);selectionRoots.delete(row);changed(row);entry.type=type;
    if(type==='message'){
      setHTML(row,'<div class="message-meta"><strong></strong><time></time></div><div class="message-body"></div>');
      entry.author=row.querySelector('strong');entry.time=row.querySelector('time');entry.body=row.querySelector('.message-body');
    }else if(type==='research'){
      setHTML(row,'<button><span class="milestone-icon"></span><span class="milestone-content"><small></small><strong></strong><span></span></span><span class="milestone-open">查看</span></button>');
      entry.glyph=row.querySelector('.milestone-icon');const content=row.querySelector('.milestone-content');
      entry.label=content.querySelector('small');entry.title=content.querySelector('strong');entry.summary=content.querySelector('span');
      row.querySelector('button').onclick=()=>onOpenRecord?.(entry.data.id);
    }else if(type==='compaction'){
      setHTML(row,`${icon('model')}<span>${escapeHTML(item.text)}</span>`);entry.text=row.lastElementChild;
    }else if(type==='activity'){
      setHTML(row,'<button class="activity-heading"><span class="activity-state"></span><span></span><small></small></button><div class="activity-body"></div>');
      entry.heading=row.querySelector('.activity-heading');entry.state=row.querySelector('.activity-state');entry.label=entry.heading.children[1];entry.small=row.querySelector('small');entry.body=row.querySelector('.activity-body');
      entry.items=new Map();entry.ordered=[];
      entry.heading.onclick=()=>{const open=entry.body.hidden;expandedTurns.set(entry.turnId,open);setOpen(entry,open);};
    }else if(type==='welcome'){
      setHTML(row,brandWelcome);
      entry.title=row.querySelector('h2');entry.workspace=row.querySelector('.welcome-note');
    }else if(type==='history'){
      setHTML(row,'<button type="button" class="text-button"></button>');entry.button=row.querySelector('button');entry.button.onclick=()=>onLoadOlder?.();
    }
  }
  function setOpen(entry,open){
    if(entry.body.hidden===open)entry.body.hidden=!open;
    const value=String(open);if(entry.heading.getAttribute('aria-expanded')!==value)entry.heading.setAttribute('aria-expanded',value);
    setText(entry.small,open?'收起':'展开过程');
  }
  function updateToolIcon(entry,name){
    const html=icon(name);if(entry.iconHTML===html)return;
    const holder=element('span');holder.innerHTML=html;
    for(const node of entry.iconNodes)entry.summary.removeChild(node);
    entry.iconNodes=Array.from(holder.childNodes);
    for(const node of entry.iconNodes)entry.summary.insertBefore(node,entry.label);
    entry.iconHTML=html;
  }
  function updateActivityItem(entry,item,nextTargets){
    const type=item.role==='assistant'?'assistant':'tool',row=entry.node;
    if(entry.type!==type){
      row.replaceChildren();htmlSources.delete(row);markdownSources.delete(row);selectionRoots.delete(row);changed(row);entry.type=type;
      if(type==='tool'){
        entry.iconHTML=icon(item.kind==='dynamicToolCall'?'branch':'terminal');
        setHTML(row,`<details><summary>${entry.iconHTML}<span></span><time></time></summary><pre></pre></details>`);
        entry.summary=row.querySelector('summary');entry.label=entry.summary.children[entry.summary.children.length-2];entry.time=entry.summary.lastElementChild;entry.pre=row.querySelector('pre');
        entry.iconNodes=Array.from(entry.summary.childNodes).filter(node=>node!==entry.label&&node!==entry.time);
      }
    }
    row.dataset.item=item.id;
    if(type==='assistant'){
      setClass(row,'activity-comment');renderMarkdown(row,item.text,item.id);
      nextTargets.set(item.id,{item,row,body:row,type:'activity'});
    }else{
      setClass(row,'activity-tool');updateToolIcon(entry,item.kind==='dynamicToolCall'?'branch':'terminal');
      setText(entry.label,toolType(item));setText(entry.time,date(item.created_at));setText(entry.pre,item.text);
    }
  }
  function updateActivity(entry,group,session,byId,lastAssistant,now,nextTargets){
    entry.turnId=group.turn_id;
    const active=session.active_turn===group.turn_id,final=byId.get(lastAssistant.get(group.turn_id));
    const end=active?now:new Date(final?.created_at||group.items.at(-1).created_at).getTime();
    const seconds=Math.max(0,Math.round((end-new Date(group.created_at).getTime())/1000));
    const duration=seconds>=60?`${Math.floor(seconds/60)}分${seconds%60}秒`:`${seconds}秒`;
    const count=group.items.filter(item=>item.role==='tool').length;
    setClass(entry.node,'message turn-activity'+(active?' active':''));
    setHTML(entry.state,active?'<span class="activity-dot"></span>':icon('chevron'));
    setText(entry.label,`${active?'正在执行':'已处理 '+duration} · ${count} 项工具活动`);
    setOpen(entry,expandedTurns.has(group.turn_id)?expandedTurns.get(group.turn_id):active);
    const nextItems=new Map(),nextOrdered=[];
    for(const item of group.items){
      const inner=entry.items.get(item.id)||{node:element('div')};
      updateActivityItem(inner,item,nextTargets);nextItems.set(item.id,inner);nextOrdered.push(inner);
    }
    reconcile(entry.body,nextOrdered.map(inner=>inner.node));entry.items=nextItems;entry.ordered=nextOrdered;
  }
  function invalidate(){
    generation++;const old=scheduled;scheduled=null;pending.clear();
    if(old?.id!=null)cancelFrame(old.id);
  }

  function render(detail,sid){
    if(!detail){reset();return;}
    invalidate();
    preserveView(()=>{
      const nextSid=sid??detail.session.id;
      if(sessionId!==nextSid){rows=new Map();markdownCache.clear();}
      sessionId=nextSid;
      const messages=(detail.messages||[]).filter(message=>message.kind!=='role_handoff'),records=detail.records||[],session=detail.session;
      references=records.map(record=>['`'+record.id+'`',`[${record.title.replace(/[\[\]]/g,'')}${record.version?' '+record.version:''}](research:${record.id})`]);
      const lastAssistant=new Map(),byId=new Map(),messageIds=new Set();
      for(const message of messages){
        messageIds.add(message.id);if(!byId.has(message.id))byId.set(message.id,message);
        if(message.role==='assistant')lastAssistant.set(message.turn_id,message.id);
      }
      const visible=messages.filter(message=>message.kind!=='method_envelope'&&(message.role==='user'||['role_handoff','research_finalization'].includes(message.kind)||message.role==='assistant'&&lastAssistant.get(message.turn_id)===message.id||message.kind==='contextCompaction')).map(item=>({role:item.role,id:item.id,created_at:item.created_at,item}));
      const activities=new Map();
      for(const item of messages){
        if(!item.turn_id||['method_envelope','output','contextCompaction'].includes(item.kind))continue;
        if(item.role==='tool'||item.role==='assistant'&&lastAssistant.get(item.turn_id)!==item.id){
          if(!activities.has(item.turn_id))activities.set(item.turn_id,[]);activities.get(item.turn_id).push(item);
        }
      }
      for(const [turnId,items] of activities)visible.push({role:'activity',id:'activity:'+turnId,created_at:items[0].created_at,item:{turn_id:turnId,created_at:items[0].created_at,items}});
      for(const item of records)if(item.session_id===nextSid&&(!detail.history?.has_more||!detail.history.start_at||item.created_at>=detail.history.start_at))visible.push({role:'research',id:item.id,created_at:item.created_at,item});
      visible.sort((a,b)=>a.created_at.localeCompare(b.created_at));
      const descriptors=[...(detail.history?.has_more?[{role:'history',id:'history-loader'}]:[]),...(!messages.length&&!records.length?[{role:'welcome',id:'welcome',item:session}]:[...(!session.thread_id?[{role:'note',id:'no-thread'}]:[]),...visible])];
      const nextRows=new Map(),nextOrdered=[],nextTargets=new Map(),now=Date.now();
      for(const descriptor of descriptors){
        const {role,id,item}=descriptor,key=rowKey(role,id);
        const entry=rows.get(key)||{node:element(role==='welcome'?'section':role==='note'?'p':'article')};
        const type=['activity','research','welcome','note','history'].includes(role)?role:item.kind==='contextCompaction'?'compaction':'message';
        initialize(entry,type,item);entry.data=item;
        if(!['welcome','note','history'].includes(role))entry.node.dataset.message=id;
        if(type==='message'){
          const progress=['role_handoff','research_finalization'].includes(item.kind);
          setClass(entry.node,`message ${role}`+(progress?' research-progress-note':'')+(item.status==='streaming'?' streaming':''));
          setText(entry.author,role==='user'?'你':progress?'IterLab · 进展':role==='system'?'IterLab · 系统通知':'Codex');setText(entry.time,date(item.created_at));
          if(item.kind==='role_handoff')setText(entry.body,item.text);
          else renderMarkdown(entry.body,readable(item.text),id);
          nextTargets.set(id,{item,row:entry.node,body:entry.body,type:'message'});
        }else if(type==='research'){
          setClass(entry.node,'message research-milestone');setHTML(entry.glyph,icon(item.kind==='plan'?'file':'branch'));
          setText(entry.label,(kindLabels[item.kind]||item.kind)+(item.version?' · '+item.version:''));setText(entry.title,item.title);setText(entry.summary,item.summary);
        }else if(type==='compaction'){
          setClass(entry.node,'message compaction-note');setText(entry.text,item.text);
        }else if(type==='activity')updateActivity(entry,item,session,byId,lastAssistant,now,nextTargets);
        else if(type==='welcome'){
          setClass(entry.node,'welcome');setText(entry.title,session.project_title);setText(entry.workspace,session.workspace);
        }else if(type==='history'){
          setClass(entry.node,'history-loader');setText(entry.button,detail.history.loading?'正在加载更早记录…':'查看更早的对话');entry.button.disabled=Boolean(detail.history.loading);
        }else{
          setClass(entry.node,'empty-note');setText(entry.node,'已有任务与程序取证已保存；尚未发起 Codex 研究轮次。可以结合这些资料继续讨论。');
        }
        nextRows.set(key,entry);nextOrdered.push(entry);
      }
      reconcile(host,nextOrdered.map(entry=>entry.node));rows=nextRows;ordered=nextOrdered;targets=nextTargets;
      for(const id of markdownCache.keys())if(!messageIds.has(id))markdownCache.delete(id);
    });
  }
  function queueDelta(item){
    const target=targets.get(item?.id);if(!target||target.item!==item)return;
    pending.set(item.id,target);if(scheduled)return;
    const ticket={generation,id:null};scheduled=ticket;
    ticket.id=requestFrame(()=>{
      if(scheduled!==ticket||ticket.generation!==generation)return;
      scheduled=null;const batch=Array.from(pending.values());pending.clear();
      preserveView(()=>{
        for(const target of batch){
          if(targets.get(target.item.id)!==target)continue;
          if(target.type==='message')setClass(target.row,`message ${target.item.role}`+(target.item.status==='streaming'?' streaming':''));
          renderMarkdown(target.body,target.type==='message'?readable(target.item.text):target.item.text,target.item.id);
        }
      });
    });
  }
  function reset(){
    invalidate();rows=new Map();ordered=[];targets=new Map();markdownCache.clear();references=[];sessionId=undefined;
    host.replaceChildren();host.scrollTop=0;
  }
  host.addEventListener('click',event=>{
    const link=event.target?.closest?.('[data-record]');if(!link||!host.contains(link))return;
    event.preventDefault();event.stopPropagation();onOpenRecord?.(link.dataset.record);
  });
  return {render,queueDelta,reset};
}
