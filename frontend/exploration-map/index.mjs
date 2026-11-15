import {Graph,Shape} from '@antv/x6';
import {kindLabel,nodeKindLabel,statusLabel,displayStatus,versionLabel,primaryEdge,visibleEdge,storyLabel,studySections,relationKind,relationLabel,normalizeData,researchDisplay,visibleData,descendants,upstreamPath,selectionFocus,searchNodes,currentNode,executionLocation,researchSpine,researchMainline,geometrySignature,layoutData} from './model.mjs';
import {unionBounds,safeArea,frameBounds,contextFrame} from './framing.mjs';

const SVG_NS='http://www.w3.org/2000/svg';
const ICONS={search:'M21 21l-4.5-4.5M19 10.5a8.5 8.5 0 1 1-17 0 8.5 8.5 0 0 1 17 0',plus:'M12 5v14M5 12h14',minus:'M5 12h14',fit:'M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M8 8h8v8H8z',focus:'M12 2v3m0 14v3M2 12h3m14 0h3M18 12a6 6 0 1 1-12 0 6 6 0 0 1 12 0',home:'m3 10 9-7 9 7M5 9v12h14V9M9 21v-7h6v7',chevron:'m8 10 4 4 4-4',expand:'M9 3H3v6m12-6h6v6M3 15v6h6m12-6v6h-6',close:'m6 6 12 12M6 18 18 6',arrow:'M5 12h14m-5-5 5 5-5 5',branch:'M6 3v12a4 4 0 0 0 4 4h8M6 8h8a4 4 0 0 0 4-4V3',history:'M3 4v5h5M3.8 8a9 9 0 1 1-.3 7M12 7v5l3 2'};
const element=(tag,cls,text)=>{const value=document.createElement(tag);if(cls)value.className=cls;if(text!=null)value.textContent=String(text);return value;};
function icon(name){const svg=document.createElementNS(SVG_NS,'svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('fill','none');svg.setAttribute('stroke','currentColor');svg.setAttribute('stroke-width','1.65');svg.setAttribute('stroke-linecap','round');svg.setAttribute('stroke-linejoin','round');svg.setAttribute('aria-hidden','true');const path=document.createElementNS(SVG_NS,'path');path.setAttribute('d',ICONS[name]||ICONS.branch);svg.append(path);return svg;}
function button(label,name,handler,cls='em-icon-button'){const value=element('button',cls);value.type='button';value.title=label;value.setAttribute('aria-label',label);if(name)value.append(icon(name));value.addEventListener('click',handler);return value;}
function dateLabel(value){if(!value)return '';const date=new Date(value);return Number.isNaN(date.getTime())?String(value):date.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});}
function plain(value){if(value==null)return '';return typeof value==='string'?value:JSON.stringify(value,null,2);}
const FIELD_LABELS={title:'标题',summary:'摘要',conclusion:'结论',finding:'研究发现',reason:'依据',rationale:'判断依据',limitations:'适用限制',next_action:'下一步',nextAction:'下一步',status:'记录状态',kind:'记录类型',scope:'适用范围',question:'研究问题',decision:'决策',hypothesis:'假设',evidence_refs:'证据引用',evidence:'证据',run_refs:'对应执行',result_refs:'对应结果',evaluation_protocol_digest:'评估协议摘要',content:'正文',payload:'记录内容',source:'来源',origin_session_id:'来源会话',source_session_id:'来源会话',created_at:'记录时间',updated_at:'更新时间',supersedes:'替代的旧评审',superseded_by:'后继评审',change_reason:'修改原因',assessment_id:'评审编号',review_id:'评审编号',revision:'记录版本',event_seq:'原始事件',message_seq:'来源对话',claim_status:'证据状态',importance_reason:'重要性依据',target_ref:'研究归属',branch_id:'探索分支',strategy_id:'研究方向',trial_id:'实验任务'};
function readableFields(value,depth=0){
  if(value===null)return element('span','','未提供');if(value===undefined)return element('span','','未记录');if(typeof value==='boolean')return element('span','',value?'是':'否');if(typeof value!=='object')return element('span','',String(value));
  if(depth>=8)return element('span','','内容层级较深，请通过原始证据入口查看完整记录。');
  const entries=Array.isArray(value)?value.map((item,index)=>[String(index+1),item]):Object.entries(value),box=element('dl','em-readable-fields');
  if(!entries.length)return element('span','','暂无条目');
  for(const [key,item] of entries.slice(0,80)){const content=element('dd');content.append(readableFields(item,depth+1));box.append(element('dt','',FIELD_LABELS[key]||key.replaceAll('_',' ')),content);}
  if(entries.length>80)box.append(element('dd','',`另有 ${entries.length-80} 条，请通过原始证据入口查看完整记录。`));return box;
}

Shape.HTML.register({shape:'iteralab-exploration-card',width:264,height:116,effect:['data'],html(cell){
  const data=cell.getData()||{},node=data.node||{},status=data.processing?'running':node.status==='running'?'unknown':displayStatus(node);
  const card=element('div',`em-node em-kind-${node.kind||'record'}${data.processing?' em-current':''}${data.retained?' em-retained':''}${data.selected?' em-selected':''}${data.ancestry?' em-ancestor':''}${data.focusDimmed?' em-focus-dimmed':''}${data.match?' em-match':''}${data.dimmed?' em-dimmed':''}`);
  card.dataset.nodeId=node.id;card.setAttribute('role','button');card.tabIndex=0;
  card.setAttribute('aria-label',`${nodeKindLabel(node)}：${node.title}，${statusLabel(status)}`);
  if(data.selected)card.setAttribute('aria-pressed','true');
  const top=element('div','em-node-meta');const kind=element('span','em-node-kind',nodeKindLabel(node));if(node.result_source?.notice)kind.title=node.result_source.notice;top.append(kind);
  if(versionLabel(node))top.append(element('span','em-node-version',versionLabel(node)));
  top.append(element('span',`em-status em-status-${status}`,statusLabel(status)));
  const title=element('div','em-node-title',node.title),summary=element('div','em-node-summary',node.summary||'点击查看对应的方案、对话与证据');
  card.append(top,title,summary);
  if(node.story_context){const attribution=element('div','em-node-attribution',storyLabel(node));attribution.title=storyLabel(node);card.append(attribution);}
  if(node.conclusion&&node.conclusion!==node.summary){card.classList.add('em-has-conclusion');card.append(element('div','em-node-conclusion',`结论：${node.conclusion}`));}
  if(data.processing)card.setAttribute('aria-label',`${nodeKindLabel(node)}：${node.title}，正在执行`);
  const select=event=>{event.stopPropagation();data.select?.(node.id);};
  card.addEventListener('click',select);card.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();select(event);}});
  if(data.childCount){const fold=button(data.collapsed?'展开后续探索':'收起后续探索','chevron',event=>{event.stopPropagation();data.fold?.(node.id);},'em-fold');fold.classList.toggle('em-folded',Boolean(data.collapsed));fold.append(element('span','',data.childCount));card.append(fold);}
  return card;
}});

class ExplorationMap {
  constructor(container,options={}){
    if(!container?.appendChild)throw TypeError('Research exploration map requires a DOM container.');
    this.container=container;this.options=options;this.compact=Boolean(options.compact);this.data=normalizeData();this.collapsed=new Set();this.expandedPlans=new Set();this.hideParked=false;this.spineOnly=true;this.showReferences=false;this.query='';this.selected=null;this.detailToken=0;this.destroyed=false;this.viewports=new Map();this.detailCache=new Map();this.initialized=false;this.layoutSignature='';
    this.root=element('section','exploration-map');this.root.classList.toggle('em-compact',this.compact);this.root.setAttribute('aria-label','研究探索图');
    this.buildToolbar();
    this.locationBar=element('section','em-location');this.locationBar.setAttribute('aria-label','当前执行位置');this.locationBar.setAttribute('role','status');this.locationBar.setAttribute('aria-live','polite');this.root.append(this.locationBar);
    this.main=element('div','em-main');this.canvasWrap=element('div','em-canvas-wrap');this.canvas=element('div','em-canvas');this.canvas.setAttribute('aria-label','研究探索图画布');this.canvas.tabIndex=0;this.canvas.title='滚轮缩放 · 拖拽空白处移动 · 点击空白处取消路径高亮';
    this.empty=element('div','em-empty');this.empty.append(icon('branch'),element('strong','','研究从一个问题开始'),element('p','','对齐需求、形成方案和开展实验后，研究路线会同步在这里。'));
    this.legend=element('div','em-legend');this.primaryLegend=element('span','em-legend-primary','研究展开');this.referenceLegend=element('span','em-legend-reference','参考已有发现');this.referenceLegend.hidden=true;this.legend.append(this.primaryLegend,this.referenceLegend,element('span','em-interaction-hint','滚轮缩放 · 拖拽移动'));
    this.frameNote=element('p','em-frame-note');this.frameNote.hidden=true;this.frameNote.setAttribute('role','status');
    this.canvasWrap.append(this.canvas,this.empty,this.legend,this.frameNote);this.detail=element('aside','em-detail');this.detail.setAttribute('aria-label','研究节点详情');this.detail.hidden=true;
    this.main.append(this.canvasWrap,this.detail);this.root.append(this.main);this.container.append(this.root);
    this.graph=new Graph({container:this.canvas,width:Math.max(1,this.canvas.clientWidth),height:Math.max(1,this.canvas.clientHeight),async:false,interacting:false,background:{color:'transparent'},grid:false,panning:{enabled:false},mousewheel:{enabled:true,modifiers:null,minScale:.04,maxScale:1.8,factor:1.12,zoomAtMousePosition:true},connecting:{allowBlank:false},embedding:false});
    this.bindCanvasPanning();
    this.graph.on('scale',()=>this.updateScale());
    this.graph.on('blank:click',()=>{if(!this.suppressPanClick)this.clearSelection();});
    this.graph.on('edge:click',({edge})=>{const value=edge.getData();if(relationKind(value)==='collapsed_path')this.expandPath(value);});
    this.canvas.addEventListener('keydown',event=>{if(event.key==='Escape'&&this.selected){event.preventDefault();event.stopPropagation();this.clearSelection();}});
    this.resizeObserver=typeof ResizeObserver==='function'?new ResizeObserver(()=>this.resize()):null;this.resizeObserver?.observe(this.root);
    this.update(options.data||{});
  }
  bindCanvasPanning(){
    // Pointer coordinates remain in viewport pixels at every zoom level.
    // X6's blank:mousedown was not emitted on this host's SVG/HTML background.
    this.panPointerDown=event=>{
      if(![0,1].includes(event.button)||event.target?.closest?.('.em-node,button,input,textarea,select,a'))return;
      this.suppressPanClick=false;
      this.panGesture={id:event.pointerId,x:event.clientX,y:event.clientY,...this.graph.translate(),moved:false};
    };
    this.panPointerMove=event=>{
      const start=this.panGesture;if(!start||event.pointerId!==start.id)return;
      const dx=event.clientX-start.x,dy=event.clientY-start.y;
      if(!start.moved&&Math.hypot(dx,dy)<4)return;
      start.moved=true;this.suppressPanClick=true;
      this.canvas.setPointerCapture?.(event.pointerId);this.canvas.classList.add('em-dragging');
      this.graph.translate(start.tx+dx,start.ty+dy);event.preventDefault();
    };
    this.panPointerUp=event=>{
      if(!this.panGesture||event.pointerId!==this.panGesture.id)return;
      try{this.canvas.releasePointerCapture?.(event.pointerId);}catch{}
      this.panGesture=null;this.canvas.classList.remove('em-dragging');
    };
    this.canvas.addEventListener('pointerdown',this.panPointerDown,true);
    this.canvas.addEventListener('click',event=>{
      if(this.suppressPanClick){event.preventDefault();event.stopImmediatePropagation();this.suppressPanClick=false;return;}
      if(event.button!=null&&event.button!==0||event.target?.closest?.('.em-node,.x6-node,.x6-edge,button,input,textarea,select,a'))return;
      this.clearSelection();
    },true);
    document.addEventListener('pointermove',this.panPointerMove,{passive:false});
    document.addEventListener('pointerup',this.panPointerUp);
    document.addEventListener('pointercancel',this.panPointerUp);
  }
  buildToolbar(){
    const toolbar=element('div','em-toolbar');const search=element('label','em-search');search.append(icon('search'));this.searchInput=element('input');this.searchInput.type='search';this.searchInput.placeholder='查找问题、方案或实验';this.searchInput.setAttribute('aria-label','搜索研究探索图');
    search.append(this.searchInput);toolbar.append(search);this.searchInput.addEventListener('input',()=>{this.query=this.searchInput.value;this.renderSearch();this.refreshCards();});
    this.searchInput.addEventListener('keydown',event=>{if(event.key==='Enter'){const first=searchNodes(this.data.nodes,this.query)[0];if(first){event.preventDefault();this.reveal(first.id);}}});
    this.filterButton=button('隐藏已搁置与已否定的节点','history',()=>{this.hideParked=!this.hideParked;this.filterButton.setAttribute('aria-pressed',String(this.hideParked));this.render();});this.filterButton.setAttribute('aria-pressed','false');
    this.referenceButton=button('显示支持、反驳与参考关系','branch',()=>{this.showReferences=!this.showReferences;this.referenceButton.setAttribute('aria-pressed',String(this.showReferences));this.referenceLegend.hidden=!this.showReferences;this.render();});this.referenceButton.setAttribute('aria-pressed','false');
    this.viewSwitch=element('div','em-view-switch');this.viewSwitch.setAttribute('role','group');this.viewSwitch.setAttribute('aria-label','图谱显示范围');
    this.spineButton=button('关键脉络',null,()=>this.setSpineOnly(true),'em-view-toggle');this.spineButton.textContent='关键脉络';
    this.fullButton=button('完整过程',null,()=>this.setSpineOnly(false),'em-view-toggle');this.fullButton.textContent='完整过程';
    this.viewSwitch.append(this.spineButton,this.fullButton);
    this.count=element('span','em-count','0 个节点');toolbar.append(this.viewSwitch,this.count);
    this.root.append(toolbar);this.toolbar=toolbar;
    // Reuse the host's refresh control so loading state and its event handler stay authoritative.
    if(this.options.refreshControl){
      const control=this.options.refreshControl;this.refreshHome=document.createComment('graph refresh control');control.before(this.refreshHome);toolbar.append(control);control.classList.add('em-icon-button');
      const update=()=>{control.title=`刷新研究探索图 · ${this.options.syncStatus?.textContent||'等待同步'}`;control.setAttribute('aria-description',this.options.syncStatus?.textContent||'等待同步');};
      update();this.syncObserver=new MutationObserver(update);if(this.options.syncStatus)this.syncObserver.observe(this.options.syncStatus,{childList:true,characterData:true,subtree:true});
    }
    this.searchResults=element('div','em-search-results');this.searchResults.hidden=true;this.root.append(this.searchResults);
    const controls=element('div','em-controls');controls.append(button('缩小','minus',()=>this.zoom(-.15)));this.scaleLabel=element('span','em-scale','100%');this.locationButton=button('定位当前执行或等待对象','focus',()=>this.focusCurrent());controls.append(this.scaleLabel,button('放大','plus',()=>this.zoom(.15)),element('span','em-control-divider'),button('显示全图','fit',()=>this.fit()),this.locationButton);
    if(this.options.onExpand){this.expandButton=button('放大阅读研究探索图','expand',()=>this.options.onExpand());controls.append(this.expandButton);}
    controls.append(element('span','em-control-divider'),this.filterButton,this.referenceButton,button('回到研究起点','home',()=>this.focusRoot()));
    this.controls=controls;
  }
  attachModeControl(control){
    if(!control||this.modeHome)return;
    this.modeHome=document.createComment('host graph display modes');control.before(this.modeHome);
    this.modeControl=control;control.classList.add('em-host-modes');this.toolbar.prepend(control);this.resize();
  }
  update(data){
    if(this.destroyed)return;
    const next=normalizeData(data);const signature=JSON.stringify([next.project_id,next.revision,next.nodes,next.edges,next.execution_location]);
    if(signature===this.signature)return;
    const changedProject=this.data.project_id&&next.project_id&&this.data.project_id!==next.project_id;
    if(changedProject){this.pendingFrame=null;this.frameNote.hidden=true;this.collapsed.clear();this.expandedPlans.clear();this.selected=null;this.viewports.clear();this.initialized=false;this.detail.hidden=true;this.layoutSignature='';this.locationSignature='';}
    this.signature=signature;this.data=next;this.detailCache.clear();this.detailToken++;
    if(this.selected&&!next.nodes.some(node=>node.id===this.selected)){this.selected=null;this.detail.hidden=true;}
    this.render();this.renderSearch();
    if(this.selected)this.showDetail(this.selected);
  }
  render(){
    if(this.destroyed)return;
    const researchData=researchDisplay(this.data);
    this.renderLocation(researchData);
    const viewport=this.initialized?this.getViewport():null;
    const projected=this.spineOnly?researchMainline(researchData,{expandedPlans:this.expandedPlans}):researchData,visible=visibleData(projected,this),signature=geometrySignature(visible,{compact:this.compact});
    this.projected=projected;
    const deferred=projected.projection?.deferred_branch_ids?.length||0,grouped=projected.projection?.grouped_record_count||0;
    this.spineButton.setAttribute('aria-pressed',String(this.spineOnly));this.fullButton.setAttribute('aria-pressed',String(!this.spineOnly));
    this.spineButton.setAttribute('aria-description',deferred?`${deferred} 条尚无过程记录的非当前路线已折叠；不据此推断它们从未执行`:'展示方案、评审与关键发现；实验过程按方案收拢，点击过程卡片可局部展开。');
    this.fullButton.setAttribute('aria-description','展示所有已保存实验、结果、异常和决策；不改变研究记录或执行状态。');
    const relayout=signature!==this.layoutSignature;
    if(relayout){this.layout=layoutData(visible,{compact:this.compact});this.layoutSignature=signature;}
    else{const current=new Map(visible.nodes.map(node=>[node.id,node]));this.layout={...this.layout,nodes:this.layout.nodes.map(node=>({...current.get(node.id),x:node.x,y:node.y,width:node.width,height:node.height})),edges:visible.edges};}
    this.visibleIds=new Set(visible.nodes.map(node=>node.id));this.count.textContent=grouped?`收拢 ${grouped} 条过程`:deferred?`未展开 ${deferred} 条路线`:`${visible.nodes.length} 个节点`;this.count.title=grouped?'过程记录保留在分组中；完整过程或搜索可查看原件':'当前显示范围；切换视图不改变历史';this.empty.hidden=Boolean(this.data.nodes.length);
    if(this.data.nodes.length&&!visible.nodes.length){this.empty.hidden=false;this.empty.replaceChildren(icon('history'),element('strong','','当前筛选没有节点'),element('p','','可关闭“隐藏已搁置”或展开已折叠的分支。'));}
    if(relayout)this.graph.clearCells();
    const matches=new Set(searchNodes(this.data.nodes,this.query).map(node=>node.id));
    this.focus=selectionFocus(this.selected,this.data.edges,{showReferences:this.showReferences});this.updateFocusLegend();
    for(const node of this.layout.nodes){const existing=this.graph.getCellById(node.id);if(existing)existing.setData(this.cardData(node,matches),{overwrite:true});else this.graph.addNode({id:node.id,shape:'iteralab-exploration-card',x:node.x,y:node.y,width:node.width,height:node.height,zIndex:2,data:this.cardData(node,matches)});}
    this.syncEdges();
    if(!this.controls.parentNode)this.canvasWrap.append(this.controls);
    this.resizingForRender=true;this.resize();this.resizingForRender=false;
    if(viewport)this.setViewport(viewport);else if(this.data.nodes.length&&!this.initialized)this.initialFrame();
    if(this.pendingFrame&&this.canvasWrap.clientWidth&&this.canvasWrap.clientHeight){const id=this.pendingFrame;this.pendingFrame=null;this.frameContext(id);}
    this.updateScale();
    if(this.toolbar?.clientHeight)this.root.style?.setProperty('--em-toolbar-height',this.toolbar.clientHeight+'px');
  }
  renderLocation(data=this.data){
    this.location=executionLocation(data);const value=this.location,signature=JSON.stringify(value);if(this.locationSignature===signature)return;this.locationSignature=signature;
    this.locationBar.replaceChildren();this.locationBar.dataset.state=value.state;
    const head=element('div','em-location-heading');head.append(element('strong',`em-location-state em-location-${value.state}`,value.label));if(value.phase_label)head.append(element('span','em-location-phase',value.phase_label));this.locationBar.append(head);
    const target=currentNode(this.data.nodes,value),summary=value.summary||target?.title||'没有可定位的执行任务。';
    const detail=element('details','em-location-info'),trigger=element('summary','','ⓘ');trigger.setAttribute('aria-label','查看执行状态说明');trigger.title='查看执行状态说明';
    const panel=element('div','em-location-popover');panel.append(element('p','',summary));
    if(value.reason&&value.reason!==summary)panel.append(element('p','',value.reason));
    if(value.next_action)panel.append(element('p','',`下一步：${value.next_action}`));
    if(value.observed_at)panel.append(element('small','',`状态更新：${dateLabel(value.observed_at)}`));
    detail.append(trigger,panel);head.append(detail);
    detail.addEventListener('keydown',event=>{if(event.key==='Escape'&&detail.open){event.preventDefault();event.stopPropagation();detail.open=false;trigger.focus();}});
    if(!this.dismissLocation){this.dismissLocation=event=>{const open=this.locationBar.querySelector('details[open]');if(open&&!open.contains(event.target))open.open=false;};document.addEventListener('pointerdown',this.dismissLocation);}
    this.locationButton.disabled=!target;this.locationButton.title=target?`定位：${target.title}`:'当前没有明确关联的执行或等待对象';
  }
  syncEdges(){
    const desired=this.layout.edges.filter(edge=>visibleEdge(edge,this.showReferences)),ids=new Set(desired.map(edge=>edge.id));
    for(const edge of this.graph.getEdges())if(!ids.has(edge.id))this.graph.removeCell(edge);
    for(const edge of desired){const existing=this.graph.getCellById(edge.id),reference=!primaryEdge(edge),collapsed=relationKind(edge)==='collapsed_path',ordered=relationKind(edge)==='record_order',label=collapsed||ordered?edge.label:edge.story_visible||reference&&relationKind(edge)!=='informed_by'?relationLabel(edge):'',labels=label?[{attrs:{label:{text:label,fill:'var(--em-muted)',fontSize:10},body:{fill:'var(--em-card)',stroke:'var(--em-line)',rx:4,ry:4}}}]:[];
      const route=this.edgeGeometry(edge,reference);
      const connector=relationKind(edge)==='assesses'?{name:'rounded',args:{radius:12}}:{name:'smooth'};
      if(existing){existing.setData(edge,{overwrite:true});existing.setSource?.(route.source);existing.setTarget?.(route.target);existing.setVertices?.(route.vertices);existing.setConnector?.(connector);existing.attr('line',this.edgeStyle(edge));existing.setLabels?.(labels);continue;}
      this.graph.addEdge({id:edge.id,zIndex:1,...route,connector,attrs:{line:this.edgeStyle(edge)},labels,data:edge});
    }
  }
  edgeGeometry(edge,reference){
    const route={source:{cell:edge.source,anchor:reference?'right':'bottom'},target:{cell:edge.target,anchor:reference?'right':'top'},vertices:[]};
    if(relationKind(edge)==='assesses'){
      const source=this.layout.nodes.find(n=>n.id===edge.source),target=this.layout.nodes.find(n=>n.id===edge.target);
      if(source&&target){const members=this.layout.nodes.filter(n=>n.plan_id===source.id),left=Math.min(...members.map(n=>n.x))-20;
        route.source.anchor='left';route.target.anchor='left';route.vertices=[{x:left,y:source.y+source.height/2},{x:left,y:target.y+target.height/2}];}
    }
    return route;
  }
  expandPath(edge){this.setSpineOnly(false);for(const id of edge.hidden_node_ids||[])for(const parent of [...this.collapsed])if(parent===id||descendants(parent,this.data.edges).has(id))this.collapsed.delete(parent);this.render();this.reveal(edge.target);}
  setSpineOnly(value){this.spineOnly=Boolean(value);if(this.spineOnly)this.expandedPlans.clear();if(this.selected&&!researchMainline(this.data).nodes.some(node=>node.id===this.selected)&&this.spineOnly)this.clearSelection();this.render();}
  cardData(node,matches){const inPath=this.focus?.nodes.has(node.id)||(node.hidden_node_ids||[]).some(id=>this.focus?.nodes.has(id)),processing=this.location?.active_node_ids.includes(node.id)===true;return {node,processing,retained:this.location?.retained_node_ids.includes(node.id)===true,selected:node.id===this.selected,ancestry:Boolean(inPath)&&node.id!==this.selected,focusDimmed:Boolean(this.selected)&&!inPath&&!processing,match:matches.has(node.id),dimmed:Boolean(this.query.trim())&&!matches.has(node.id)&&!inPath&&!processing,collapsed:this.collapsed.has(node.id),childCount:(this.projected?.edges||this.data.edges).filter(edge=>primaryEdge(edge)&&edge.source===node.id).length,select:id=>this.select(id),fold:id=>this.toggleCollapse(id)};}
  edgeStyle(edge){const reference=!primaryEdge(edge),collapsed=relationKind(edge)==='collapsed_path',ordered=relationKind(edge)==='record_order',focused=this.focus?.edges.has(edge.id)||(edge.original_edge_ids||[]).some(id=>this.focus?.edges.has(id));return {stroke:['refutes','contradicts'].includes(relationKind(edge))?'var(--em-refute)':reference||ordered?'var(--em-reference)':'var(--em-wire)',strokeWidth:focused?2.2:reference||ordered?1.15:1.5,strokeDasharray:reference||collapsed||ordered?'5 5':null,targetMarker:ordered?null:{name:'classic',size:5},opacity:this.selected?(focused?1:.16):(reference||ordered ? .7 : .9)};}
  updateFocusLegend(){this.root.classList.toggle('em-has-selection',Boolean(this.selected));this.primaryLegend.textContent=this.selected?'相关路径 · 点击空白取消':this.data.research_story?'历史研究脉络 · 实线为核查推进，虚线标明判断修订':this.data.plan_series?.families?.length?'同系列版本并列 · 新系列向下承接':this.spineOnly?'研究主线 · 虚线“记录顺序”不代表因果':'完整研究过程';}
  refreshCards(){if(!this.graph)return;this.focus=selectionFocus(this.selected,this.data.edges,{showReferences:this.showReferences});this.updateFocusLegend();const matches=new Set(searchNodes(this.data.nodes,this.query).map(node=>node.id)),nodes=new Map(this.data.nodes.map(node=>[node.id,node]));for(const cell of this.graph.getNodes())cell.setData(this.cardData(nodes.get(cell.id)||cell.getData().node,matches),{overwrite:true});for(const edge of this.graph.getEdges())edge.attr('line',this.edgeStyle(edge.getData()));}
  clearSelection({notify=true}={}){if(this.destroyed||!this.selected)return;this.detailToken++;this.selected=null;this.detail.hidden=true;this.refreshCards();this.resize();if(notify)this.options.onDeselect?.();}
  renderSearch(){
    this.searchResults.replaceChildren();const found=searchNodes(this.data.nodes,this.query);this.searchResults.hidden=!this.query.trim();
    if(!this.query.trim())return;
    if(!found.length){this.searchResults.append(element('p','','没有匹配的研究节点'));return;}
    for(const node of found.slice(0,16)){const row=button(node.title,null,()=>this.reveal(node.id),'em-search-result');row.append(element('span','',node.title),element('small','',nodeKindLabel(node)+(versionLabel(node)?` · ${versionLabel(node)}`:'')));this.searchResults.append(row);}
    if(found.length>16)this.searchResults.append(element('p','',`还有 ${found.length-16} 个结果，请缩小搜索范围。`));
  }
  toggleCollapse(id){this.collapsed.has(id)?this.collapsed.delete(id):this.collapsed.add(id);this.render();}
  reveal(id){
    const folded=this.data.nodes.find(node=>node.id===id&&node.execution_detail_only);
    if(folded)return this.data.nodes.some(n=>n.id===folded.execution_trial_id&&!n.execution_detail_only)?this.reveal(folded.execution_trial_id):false;
    const node=this.data.nodes.find(node=>node.id===id);if(!node)return false;
    if(this.spineOnly&&!researchMainline(this.data,{expandedPlans:this.expandedPlans}).nodes.some(item=>item.id===id)){
      if(this.data.plan_series&&node.plan_id)this.expandedPlans.add(node.plan_id);else this.setSpineOnly(false);
    }
    for(const parent of [...this.collapsed])if(descendants(parent,this.data.edges).has(id))this.collapsed.delete(parent);
    if(this.hideParked){this.hideParked=false;this.filterButton.setAttribute('aria-pressed','false');}
    this.render();this.searchResults.hidden=true;this.select(id);const cell=this.graph.getCellById(id);if(cell){if(this.graph.zoom()<.65)this.graph.zoomTo(.85);this.graph.centerCell(cell);}return true;
  }
  select(id,{notify=true}={}){
    const group=this.layout?.nodes.find(node=>node.id===id&&node.kind==='process_group');
    if(group){this.clearSelection();this.expandedPlans.add(group.plan_id);this.render();return true;}
    if(this.destroyed||!this.data.nodes.some(node=>node.id===id))return false;
    this.selected=id;this.refreshCards();this.showDetail(id);if(notify)this.options.onSelect?.(this.data.nodes.find(node=>node.id===id));return true;
  }
  async showDetail(id){
    const node=this.data.nodes.find(node=>node.id===id);if(!node)return;
    if(this.compact&&this.options.compactDetails===false){this.detailToken++;this.detail.hidden=true;return;}
    const token=++this.detailToken;this.detail.hidden=false;this.renderDetailHeader(node);
    if(this.compact){const body=element('div','em-detail-body');body.append(element('p','em-detail-summary',node.summary||'选择“放大阅读”查看完整方案、对应对话与结果。'));if(this.options.onExpand){const open=button('放大阅读',null,()=>this.options.onExpand(),'em-text-button');open.append(element('span','','放大阅读'),icon('expand'));body.append(open);}this.detail.append(body);this.resize();return;}
    this.resize();
    let detail=this.detailCache.get(id);
    if(!detail&&this.options.loadDetail){this.detail.append(element('p','em-detail-loading','正在读取保存的方案、对话和证据…'));try{detail=await this.options.loadDetail(id,node);if(this.destroyed||token!==this.detailToken||id!==this.selected)return;this.detailCache.set(id,detail);}catch(error){if(this.destroyed||token!==this.detailToken)return;this.detail.querySelector('.em-detail-loading')?.remove();const failure=element('div','em-detail-error');failure.append(element('p','',`读取详情失败：${error?.message||'请重试'}`));const retry=button('重新读取',null,()=>this.showDetail(id),'em-text-button');retry.textContent='重新读取';failure.append(retry);this.detail.append(failure);return;}}
    if(this.destroyed||token!==this.detailToken)return;
    this.detail.querySelector('.em-detail-loading')?.remove();this.renderDetailBody(node,detail||{node});
  }
  renderDetailHeader(node){
    this.detail.replaceChildren();const head=element('div','em-detail-header');const info=element('div');info.append(element('span','em-detail-eyebrow',`${nodeKindLabel(node)}${versionLabel(node)?` · ${versionLabel(node)}`:''}`),element('h3','',node.title));const close=button('关闭节点详情','close',()=>this.clearSelection());head.append(info,close);this.detail.append(head);
  }
  markdown(content,sessionId=this.detailSessionId){const box=element('div','em-markdown');if(this.options.renderMarkdown)box.innerHTML=this.options.renderMarkdown(String(content||''),{sessionId:sessionId||this.options.sessionId});else for(const paragraph of String(content||'').split(/\n\s*\n/))box.append(element('p','',paragraph));return box;}
  section(body,title){const block=element('section','em-detail-section');block.append(element('h4','',title));body.append(block);return block;}
  sourceButton(label,source,node){const action=button(label,null,()=>this.options.onOpenSource?.(source,node),'em-source-link');action.append(element('span','',label),icon('arrow'));return action;}
  recordView(value){const box=element('div','em-readable-record');if(this.options.renderRecord)box.innerHTML=this.options.renderRecord(value,this.detailSessionId||this.options.sessionId,true);else box.append(readableFields(value));return box;}
  evidenceSource(item,node){
    const saved=item.resolved===true?item.record:null,kind=saved?.role?'message':'event';
    const seq=saved?.seq??item.event_seq??(item.type==='event'?item.seq:null);
    return Number.isSafeInteger(Number(seq))&&Number(seq)>0?this.sourceButton(`${kind==='message'?'定位对话':'查看原始证据'} #${seq}`,{kind,seq:Number(seq),origin_session_id:saved?.study_id||item.study_id||item.origin_session_id||node?.origin_session_id||this.detailSessionId},node):null;
  }
  recordGroup(body,title,items,label){if(!Array.isArray(items)||!items.length)return;const group=element('details','em-record em-history-group');group.append(element('summary','',`${title} · ${items.length}`));for(const item of items){const entry=element('details','em-record');entry.append(element('summary','',label(item)),this.recordView(item));const source=this.evidenceSource(item);if(source)entry.append(source);group.append(entry);}body.append(group);}
  proseFields(body,fields){const statuses={not_applicable:'不适用',specified:'已明确',unknown:'尚未确认',deferred:'暂缓'};for(const [title,value] of fields){if(value===undefined||value===null||value==='')continue;const section=this.section(body,title),display=value&&typeof value==='object'&&!Array.isArray(value)&&statuses[value.status]?{...value,status:statuses[value.status]}:value;section.append(typeof display==='string'?this.markdown(display):this.recordView(display));}}
  researchDetails(body,detail,node=detail.node||{}){
    for(const group of studySections(detail.study_integrity)){const section=this.section(body,group.title);this.proseFields(section,group.fields);}
    const change=detail.research_change;
    if(change&&typeof change==='object'){
      const labels={candidate:'候选，尚未验证或采纳',unverified:'尚未验证',proposed:'待验证',hypothesis:'研究假设',reported:'已记录的说明，仍需核对证据',explicit_decision:'记录为明确决定，可核对下方用户原话；执行仍按现有审核与权限处理',observed:'已观测',supported:'证据支持',refuted:'证据反驳',inconclusive:'证据尚不足',uncertain:'仍有不确定性',confirmed:'已确认',withdrawn:'已撤回',superseded:'已有修订'};
      this.proseFields(body,[['研究判断',change.summary],['当前证据状态',labels[change.claim_status]||change.claim_status],['为何保留在研究脉络中',change.importance_reason]]);
      if(change.supersedes){const section=this.section(body,'本次修订关系');section.append(element('p','','当前记录是对已有研究判断的修订，原记录仍予保留。'));const previous=typeof change.supersedes==='object'?change.supersedes.id:change.supersedes,target=this.data.nodes.find(item=>item.id===previous||item.id===`change:${previous}`);if(target){const prior=button('查看修订前的记录',null,()=>this.reveal(target.id),'em-text-button');prior.textContent='查看修订前的记录';section.append(prior);}else section.append(this.recordView({supersedes:change.supersedes}));}
      if(change.target_ref){const section=this.section(body,'研究归属');section.append(this.recordView(change.target_ref));}
    }
    const review=detail.plan_review;
    if(review&&typeof review==='object'){
      const section=this.section(body,'此版本的方案审核');const target=review.execution_target||{};
      this.proseFields(section,[['审核结果',review.status_label||statusLabel(review.status)],['当前请求效力',review.applicability?.reason],['用户审核意见',review.comment||(review.status==='awaiting_review'?'尚无审核决定；等待用户审核此版本。':'尚无审核决定；此处保留原请求记录。')],['对应方案',review.plan_version_label?`方案 ${review.plan_version_label}`:'以保存的确切方案版本为准'],['申请时间',dateLabel(review.requested_at)],['决定时间',review.decided_at?dateLabel(review.decided_at):'尚未决定'],['审核执行环境',({local:'本地运行环境',ssh:'SSH运行环境',lpai:'LPAI作业环境'})[target.kind]||'以原审核范围为准']]);
      section.append(element('p','em-definition-note',review.notice||'方案审核不增加执行许可或预算，也不代表科学结论已通过。'));
      for(const seq of review.source_event_seqs||[])if(this.options.onOpenSource)section.append(this.sourceButton(`查看审核原始证据 #${seq}`,{kind:'event',seq,origin_session_id:review.origin_session_id||node.origin_session_id},node));
      if(Array.isArray(detail.plan_reviews)&&detail.plan_reviews.length>1){const history=element('details','em-record');history.append(element('summary','',`该版本审核记录 · ${detail.plan_reviews.length}`));for(const entry of detail.plan_reviews){const item=element('article','em-source-message');item.append(element('strong','',entry.status_label||statusLabel(entry.status)),this.markdown(entry.comment||'等待审核'));history.append(item);}section.append(history);}
    }
    const assessment=detail.assessment;
    if(assessment&&typeof assessment==='object'){
      this.proseFields(body,[['本轮结论',assessment.finding],['与之前结果的比较',assessment.comparison],['适用范围与限制',assessment.limitations],['后续研究建议',({next_trial:'开展下一项实验',repair:'先修复执行问题',inspect_evidence:'补查证据',replan:'重新制定方案',realign:'重新对齐需求',request_human:'等待人工确认',deliver:'整理研究交付',stop:'停止本轮研究'})[assessment.next_route]||assessment.next_route]]);
      const references={};for(const field of ['run_refs','result_refs','evaluation_protocol_digest','supersedes'])if(assessment[field]!==undefined)references[field]=assessment[field];if(Object.keys(references).length){const section=this.section(body,'评审依据');section.append(this.recordView(references));}
    }
    if(Array.isArray(detail.review_history)&&detail.review_history.length){const history=element('details','em-record em-history-group');history.append(element('summary','',`评审修订历史 · ${detail.review_history.length}`));for(const review of detail.review_history){const entry=element('details','em-record');entry.append(element('summary','',`评审 ${review.revision?`1.${Math.max(0,Number(review.revision)-1)}`:''}${review.created_at?' · '+dateLabel(review.created_at):''}`));entry.append(review.markdown?this.markdown(review.markdown):this.recordView(review.content||review));history.append(entry);}body.append(history);}
  }
  renderDetailBody(node,detail){
    this.detailSessionId=node.origin_session_id||detail.session_id||this.options.sessionId;
    const body=element('div','em-detail-body');const metadata=element('div','em-detail-meta'),status=displayStatus(node);metadata.append(element('span',`em-status em-status-${status}`,statusLabel(status)));if(node.created_at)metadata.append(element('time','',dateLabel(node.created_at)));body.append(metadata);
    body.append(element('p','em-browsing-note',this.location?.active_node_ids.includes(node.id)?'此节点关联当前执行；浏览其他节点不会改变执行位置。':'正在浏览保存的研究记录；当前执行位置以顶部状态栏为准。'));
    const story=detail.research_story||node.story_context;
    if(story){
      const section=this.section(body,story.retrospective?'历史脉络注解':'研究脉络');
      section.append(element('p','em-definition-note',storyLabel({...node,story_context:story})));
      if(story.retrospective)section.append(element('p','em-definition-note',`这是依据已有证据补充的历史梳理，未改写原文，也不代表 Agent 当时记录了这些节点。注解版本 ${story.revision} · ${dateLabel(story.annotated_at)}`));
      if(story.scope)section.append(element('p','em-definition-note',story.scope));
      if(story.content)section.append(this.markdown(story.content));
      if(node.original_title)section.append(element('p','em-definition-note',`原始记录：${node.original_title}`));
      for(const source of story.sources||[]){const entry=element('details','em-record');entry.append(element('summary','',`${({record:'原研究记录',delivery:'人工输入',document:'验收复核文档'})[source.kind]||'来源'} · ${source.id}`),element('p','',source.quote),element('code','em-evidence-path',`SHA256 ${source.sha256}`));if(source.kind==='record'&&source.id!==node.id){const open=button('查看原记录',null,()=>this.reveal(source.id),'em-text-button');open.textContent='查看原记录';entry.append(open);}section.append(entry);}
    }
    if(node.status_reason){const reason=element('div','em-status-reason');reason.append(element('strong','','状态说明'),this.markdown(node.status_reason));body.append(reason);}
    const specialized=new Set([detail.assessment?.finding,detail.research_change?.summary]);
    if(node.summary&&!specialized.has(node.summary)&&!(node.kind==='plan_review'&&detail.plan_review))body.append(this.markdown(node.summary));
    if(node.conclusion&&node.conclusion!==node.summary&&!specialized.has(node.conclusion)){const conclusion=element('div','em-node-conclusion-note');conclusion.append(element('strong','','已记录结论'),this.markdown(node.conclusion));body.append(conclusion);}
    this.researchDetails(body,detail,node);
    const document=detail.document;
    if(document){const snapshot=['lineage-definition-snapshot','legacy-definition-snapshot'].includes(document.source)||node.base_snapshot===true;const section=this.section(body,snapshot?'节点定义快照':node.kind==='plan'?'保存的方案':'原始研究记录');if(snapshot)section.append(element('p','em-definition-note','这是创建节点时保存的定义与约束，不代表已制定完整实施方案。'));const content=document.plan_content||document.content||document.markdown||'';section.append(typeof content==='string'?this.markdown(content):this.recordView(content));if(document.change_reason){const reason=element('div','em-change-reason');reason.append(element('strong','','本次调整'),this.markdown(document.change_reason));section.append(reason);}if(this.options.onOpenSource&&node.kind==='plan')section.append(this.sourceButton(snapshot?'打开定义快照':'打开完整方案',{kind:'plan',...node.ref,origin_session_id:node.origin_session_id},node));}
    const messages=Array.isArray(detail.messages)?detail.messages:[];
    if(messages.length){const temporal=messages.every(message=>message.relation==='preceding-context');const section=this.section(body,temporal?'邻近对话背景':'对应对话');if(temporal)section.append(element('p','em-definition-note','以下是时间相邻的对话背景，不等同于此节点的直接来源。'));for(const message of messages){const item=element('article','em-source-message');const meta=element('div','em-source-message-meta');meta.append(element('strong','',message.role==='user'?'你':message.role==='assistant'?'IterLab':'研究记录'));if(message.created_at)meta.append(element('time','',dateLabel(message.created_at)));item.append(meta,this.markdown(message.content||'',message.study_id||node.origin_session_id));if(this.options.onOpenSource&&message.seq!=null)item.append(this.sourceButton('定位到对话',{kind:'message',seq:message.seq,origin_session_id:message.study_id||node.origin_session_id},node));section.append(item);}}
    const results=Array.isArray(detail.results)?detail.results:[];
    if(results.length){const section=this.section(body,'结果与证据');for(const result of results){const evidence=result.evidence||result;const item=element('article','em-evidence');if(result.result_source){item.append(element('strong','',nodeKindLabel({...result,kind:'result'})),element('p','em-definition-note',result.result_source.notice));}const metrics=evidence.metrics||result.metrics;if(metrics&&typeof metrics==='object'){const dl=element('dl','em-metrics');for(const [key,value] of Object.entries(metrics)){dl.append(element('dt','',key),element('dd','',typeof value==='number'?Number(value.toPrecision(6)):plain(value)));}item.append(dl);}if(result.summary||evidence.summary)item.append(this.markdown(result.summary||evidence.summary));if(evidence.path)item.append(element('code','em-evidence-path',evidence.path));const source=result.event_seq!=null?`原始事件 #${result.event_seq}`:'已保存的结果';const details=element('details','em-record');details.append(element('summary','',`${source} · 完整记录`),this.recordView(result));item.append(details);const action=this.evidenceSource(result,node);if(action)item.append(action);section.append(item);}}
    const references=Array.isArray(detail.evidence)?detail.evidence:[];
    if(references.length){const section=this.section(body,'原始证据索引');for(const ref of references){const row=element('details','em-record');const label=ref.path||ref.title||ref.type||ref.event_type||(typeof ref.ref==='string'?`${ref.resolved?'已核对引用':'未解析引用'} · ${ref.ref}`:'证据记录');row.append(element('summary','',label),this.recordView(ref));const action=this.evidenceSource(ref,node);if(action)row.append(action);section.append(row);}}
    const connections=detail.connections||this.data.edges.filter(edge=>edge.source===node.id||edge.target===node.id);
    if(connections.length){const section=this.section(body,'关联路线');for(const edge of connections){const peer=this.data.nodes.find(value=>value.id===(edge.source===node.id?edge.target:edge.source));if(!peer)continue;const label=!primaryEdge(edge)?`${relationLabel(edge)} · ${edge.source===node.id?'指向':'来自'}`:edge.source===node.id?'后继探索':'来源';const link=button(peer.title,null,()=>this.reveal(peer.id),'em-relation');link.append(element('span','',peer.title),element('small','',label));section.append(link);}}
    this.recordGroup(body,'研究轨迹',detail.history,item=>{const payload=item.payload||{};return `${dateLabel(item.created_at)} · ${item.kind==='status_changed'?statusLabel(payload.status||payload.to||payload.to_status):'研究节点记录'}${payload.reason?' · '+payload.reason:''}`;});
    this.recordGroup(body,'运行详情与修复历史',detail.runs,item=>`${statusLabel(item.status)}${item.created_at?' · '+dateLabel(item.created_at):''}${item.event_seq!=null?' · 原始事件 #'+item.event_seq:''}`);
    this.recordGroup(body,'实验提案与评审',detail.artifacts,item=>`${({experiment_proposal:'实验提案',round_review:'轮次评审'})[item.kind]||'研究记录'}${item.created_at?' · '+dateLabel(item.created_at):''}`);
    if(detail.record){const section=element('details','em-record em-raw-record');section.append(element('summary','','完整节点记录'),this.recordView(detail.record));body.append(section);}
    if(!document&&!messages.length&&!results.length&&!references.length&&!node.summary)body.append(element('p','em-detail-empty','此节点已记录，后续产生的方案、对话与证据会保存在这里。'));
    this.detail.append(body);
  }
  fit(){
    if(this.destroyed||!this.graph.getNodes().length)return;
    const view=frameBounds(unionBounds(this.graph.getNodes().map(c=>c.getBBox())),this.safeViewport(),{maxScale:this.compact?.88:1});
    if(view){this.frameNote.hidden=true;this.setViewport(view);}
  }
  safeViewport(){
    const base=this.canvasWrap.getBoundingClientRect?.();
    const rect=e=>{const r=e?.getBoundingClientRect?.();return r&&base&&r.width&&r.height?r:null;};
    const legend=rect(this.legend),controls=rect(this.controls);
    return safeArea(this.canvasWrap.clientWidth,this.canvasWrap.clientHeight,{top:legend?Math.max(0,legend.bottom-base.top):0,bottom:controls?Math.max(0,base.bottom-controls.top):0,padding:this.compact?14:22});
  }
  frameContext(id){
    const folded=this.data.nodes.find(node=>node.id===id&&node.execution_detail_only);
    if(folded)return this.data.nodes.some(n=>n.id===folded.execution_trial_id&&!n.execution_detail_only)?this.frameContext(folded.execution_trial_id):false;
    if(this.destroyed||!this.data.nodes.some(n=>n.id===id))return false;
    if(!this.canvasWrap.clientWidth||!this.canvasWrap.clientHeight){this.pendingFrame=id;return false;}
    this.pendingFrame=null;
    const node=this.data.nodes.find(n=>n.id===id);
    if(this.spineOnly&&!researchMainline(this.data,{expandedPlans:this.expandedPlans}).nodes.some(n=>n.id===id)){
      if(this.data.plan_series&&node.plan_id)this.expandedPlans.add(node.plan_id);else this.setSpineOnly(false);
    }
    for(const parent of [...this.collapsed])if(descendants(parent,this.data.edges).has(id))this.collapsed.delete(parent);
    if(this.hideParked){this.hideParked=false;this.filterButton.setAttribute('aria-pressed','false');}
    this.render();
    const cell=this.graph.getCellById(id);if(!cell)return false;
    const ids=new Set([id]);for(const e of this.data.edges)if((e.source===id||e.target===id)&&(visibleEdge(e,this.showReferences))){ids.add(e.source);ids.add(e.target);}
    const boxes=[...ids].map(key=>this.graph.getCellById(key)?.getBBox()).filter(Boolean);
    const view=contextFrame(boxes,cell.getBBox(),this.safeViewport());if(!view)return false;
    this.frameNote.textContent=view.limited?'部分关联在画面外 · 拖拽或放大阅读':view.small?'画布空间不足 · 请放大阅读':'';this.frameNote.hidden=!this.frameNote.textContent;
    this.setViewport(view);return true;
  }
  rootNode(){return this.data.nodes.find(node=>node.id===this.data.root_id)||this.data.nodes.find(node=>node.kind==='project')||this.data.nodes[0]||null;}
  initialFrame(){if(!this.data.nodes.length||!this.canvasWrap.clientWidth||!this.canvasWrap.clientHeight){this.initialized=false;return;}this.initialized=true;this.fit();if(this.graph.zoom()>=.55||this.data.plan_series?.families?.length&&this.spineOnly)return;const node=this.data.nodes.find(node=>node.id===this.selected)||this.rootNode();if(!node)return;this.graph.zoomTo(.8);const cell=this.graph.getCellById(node.id);if(cell){if(this.selected)this.graph.centerCell(cell);else{const box=cell.getBBox();this.graph.positionPoint({x:box.x+box.width/2,y:box.y},'50%',this.compact?38:54);}}this.updateScale();}
  focusRoot(){const node=this.rootNode();if(!node)return;this.reveal(node.id);const cell=this.graph.getCellById(node.id);if(cell){const box=cell.getBBox();this.graph.positionPoint({x:box.x+box.width/2,y:box.y},'50%',this.compact?38:54);}}
  focusCurrent(){const node=currentNode(this.data.nodes,this.location);if(node){this.clearSelection();this.frameContext(node.id);}}
  zoom(delta){if(this.destroyed)return;this.graph.zoomTo(Math.max(.04,Math.min(1.8,this.graph.zoom()+delta)));this.updateScale();}
  updateScale(){if(this.graph)this.scaleLabel.textContent=`${Math.round(this.graph.zoom()*100)}%`;}
  getViewport(){return {scale:this.graph.zoom(),...this.graph.translate()};}
  setViewport(viewport){if(this.destroyed||!viewport)return;this.initialized=true;this.graph.zoomTo(Math.max(.04,Math.min(1.8,Number(viewport.scale)||1)));this.graph.translate(Number(viewport.tx)||0,Number(viewport.ty)||0);this.updateScale();}
  setCompact(value){const compact=Boolean(value);if(this.destroyed||compact===this.compact)return;if(this.initialized)this.viewports.set(this.compact?'compact':'expanded',this.getViewport());this.compact=compact;this.root.classList.toggle('em-compact',compact);if(this.expandButton)this.expandButton.hidden=!compact;this.render();if(this.selected)this.showDetail(this.selected);this.resize();const saved=this.viewports.get(compact?'compact':'expanded');if(saved)this.setViewport(saved);else this.initialFrame();}
  resize(){if(this.destroyed||!this.graph)return;if(this.toolbar?.clientHeight)this.root.style?.setProperty('--em-toolbar-height',this.toolbar.clientHeight+'px');const width=this.canvasWrap.clientWidth,height=this.canvasWrap.clientHeight;if(width>0&&height>0){this.graph.resize(width,height);if(this.pendingFrame&&!this.resizingForRender){const id=this.pendingFrame;this.pendingFrame=null;this.frameContext(id);}else if(!this.initialized&&this.data.nodes.length)this.initialFrame();}}
  destroy(){if(this.destroyed)return;this.destroyed=true;this.detailToken++;this.resizeObserver?.disconnect();this.syncObserver?.disconnect();document.removeEventListener('pointermove',this.panPointerMove);document.removeEventListener('pointerup',this.panPointerUp);document.removeEventListener('pointercancel',this.panPointerUp);if(this.dismissLocation)document.removeEventListener('pointerdown',this.dismissLocation);if(this.refreshHome){this.options.refreshControl.classList.remove('em-icon-button');this.refreshHome.replaceWith(this.options.refreshControl);}if(this.modeHome){this.modeControl.classList.remove('em-host-modes');this.modeHome.replaceWith(this.modeControl);}this.graph.dispose();this.root.remove();this.detailCache.clear();this.options={};}
}

window.IterLabExplorationMap=Object.freeze({version:'1.3.0',mount:(container,options)=>new ExplorationMap(container,options)});
