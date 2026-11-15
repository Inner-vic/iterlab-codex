import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import * as model from '../model.mjs';
import * as framing from '../framing.mjs';

// Run the real component lifecycle with deterministic DOM dimensions and X6's
// documented scale/translation geometry. No browser or live research is used.
const source=(await readFile(new URL('../index.mjs',import.meta.url),'utf8')).replace(/^import .*;\r?\n/gm,'');
const data=(project='first')=>({project_id:project,root_id:project+':root',revision:1,nodes:[
  {id:project+':root',kind:'project',status:'active',title:'Research origin'},
  {id:project+':question',kind:'question',status:'defined',title:'A saved question'},
],edges:[{id:project+':edge',source:project+':root',target:project+':question',primary:true}]});

function fixture({visible=true,graphData=data()}={}){
  const world={visible,width:340,height:424,observers:[]};
  class Element {
    constructor(tag){this.tagName=tag;this.children=[];this.dataset={};this.attrs={};this.events={};this.classList={toggle(){},add(){},remove(){}};this.hidden=false;}
    get clientWidth(){return world.visible?world.width:0;}
    get clientHeight(){return world.visible?world.height:0;}
    append(...nodes){for(const node of nodes){node.parentNode=this;this.children.push(node);}}
    appendChild(node){this.append(node);return node;}
    replaceChildren(...nodes){this.children=[];this.append(...nodes);}
    setAttribute(name,value){this.attrs[name]=value;}
    addEventListener(name,handler){this.events[name]=handler;}
    querySelector(){return null;}
    remove(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(node=>node!==this);}
  }
  class Graph {
    constructor(options){this.width=options.width;this.height=options.height;this.scale=1;this.tx=0;this.ty=0;this.nodes=[];this.edges=[];this.handlers={};this.clearCount=0;}
    on(name,handler){this.handlers[name]=handler;}
    clearCells(){this.nodes=[];this.edges=[];this.clearCount++;}
    addNode(value){const cell={...value,getBBox:()=>({x:value.x,y:value.y,width:value.width,height:value.height}),getData:()=>value.data,setData:data=>{value.data=data;}};this.nodes.push(cell);return cell;}
    addEdge(value){const edge={...value,getData:()=>value.data,setData:data=>{value.data=data;},setSource:source=>{edge.source=source;},setTarget:target=>{edge.target=target;},setLabels:labels=>{edge.labels=labels;},attr(){}};this.edges.push(edge);return edge;}
    removeCell(value){this.nodes=this.nodes.filter(cell=>cell!==value);this.edges=this.edges.filter(cell=>cell!==value);}
    getNodes(){return this.nodes;}
    getEdges(){return this.edges;}
    getCellById(id){return [...this.nodes,...this.edges].find(cell=>cell.id===id);}
    bounds(){const boxes=this.nodes.map(node=>node.getBBox()),x=Math.min(...boxes.map(box=>box.x)),y=Math.min(...boxes.map(box=>box.y));return{x,y,width:Math.max(...boxes.map(box=>box.x+box.width))-x,height:Math.max(...boxes.map(box=>box.y+box.height))-y};}
    zoom(){return this.scale;}
    zoomTo(scale){this.scale=scale;this.handlers.scale?.();}
    zoomToFit({padding,minScale,maxScale}){const box=this.bounds();this.zoomTo(Math.max(minScale,Math.min(maxScale,(this.width-2*padding)/box.width,(this.height-2*padding)/box.height)));}
    centerContent(){const box=this.bounds();this.positionPoint({x:box.x+box.width/2,y:box.y+box.height/2},'50%','50%');}
    centerCell(cell){const box=cell.getBBox();this.positionPoint({x:box.x+box.width/2,y:box.y+box.height/2},'50%','50%');}
    positionPoint(point,x,y){const pixel=(value,size)=>typeof value==='string'?parseFloat(value)*size/100:value;this.tx=pixel(x,this.width)-point.x*this.scale;this.ty=pixel(y,this.height)-point.y*this.scale;}
    translate(tx,ty){if(arguments.length){this.tx=tx;this.ty=ty;}return{tx:this.tx,ty:this.ty};}
    resize(width,height){this.width=width;this.height=height;}
    dispose(){}
  }
  const context={...model,...framing,Graph,Shape:{HTML:{register(){}}},document:{createElement:tag=>new Element(tag),createElementNS:(_namespace,tag)=>new Element(tag),addEventListener(){},removeEventListener(){}},window:{},console,
    ResizeObserver:class{constructor(callback){world.observers.push(callback);}observe(){}disconnect(){}}};
  vm.runInNewContext(source,context);
  const selections=[],details=[],map=context.window.IterLabExplorationMap.mount(new Element('div'),{compact:true,data:graphData,onSelect:node=>selections.push(node.id),loadDetail:id=>{details.push(id);return{};}});
  const resize=()=>{for(const callback of world.observers)callback();};
  const rootBounds=()=>{const box=map.graph.getCellById(map.data.root_id).getBBox();return{left:box.x*map.graph.scale+map.graph.tx,right:(box.x+box.width)*map.graph.scale+map.graph.tx,top:box.y*map.graph.scale+map.graph.ty};};
  return{map,world,selections,details,resize,rootBounds,show(){world.visible=true;resize();},hide(){world.visible=false;resize();}};
}

function assertRootVisible(f){const box=f.rootBounds();assert(box.left>=12&&box.right<=f.world.width-12,'research origin must fit the revealed canvas: '+JSON.stringify({box,view:f.map.getViewport(),width:f.world.width}));}

test('a long investigation with no plan families opens at a readable origin rather than a miniature overview',()=>{
  const graph=data();graph.plan_series={schema:'iterlab-plan-series/v1',families:[]};
  for(let i=0;i<7;i++){
    const previous=i?'step-'+(i-1):'first:question';
    graph.nodes.push({id:'step-'+i,kind:'investigation',milestone:true,story_context:{actor:'agent'}});
    graph.edges.push({id:'step-edge-'+i,source:previous,target:'step-'+i,kind:'research_step',primary:true});
  }
  const f=fixture({graphData:graph});
  assert.equal(f.map.graph.zoom(),.8);assertRootVisible(f);
  assert.equal(f.map.selected,null);assert.equal(f.details.length,0);
});

test('first hidden mount frames only after its canvas receives nonzero dimensions',()=>{
  const f=fixture({visible:false});assert.equal(f.map.graph.width,1);f.show();assertRootVisible(f);
  assert.equal(f.map.initialized,true);assert.equal(f.map.selected,null);assert.deepEqual(f.selections,[]);assert.deepEqual(f.details,[]);
});

test('ordinary hide/reveal and resizing preserve an already initialized user viewport',()=>{
  const f=fixture();f.map.setViewport({scale:.73,tx:44,ty:55});const before={...f.map.getViewport()};
  f.hide();f.show();f.world.width=390;f.resize();assert.deepEqual({...f.map.getViewport()},before);
  f.map.update({...f.map.data,revision:2});assert.deepEqual({...f.map.getViewport()},before);
});

test('another project arriving while hidden gets its own first visible frame',()=>{
  const f=fixture();f.map.setViewport({scale:.73,tx:-180,ty:87});f.hide();f.world.width=250;f.map.update(data('second'));f.show();assertRootVisible(f);
  assert.equal(f.map.selected,null);assert.deepEqual(f.selections,[]);assert.deepEqual(f.details,[]);
});

test('a hidden presentation roundtrip restores an existing user viewport',()=>{
  const f=fixture();f.map.setViewport({scale:.73,tx:44,ty:55});const before={...f.map.getViewport()};
  f.hide();f.map.setCompact(false);f.map.setCompact(true);f.show();assert.deepEqual({...f.map.getViewport()},before);
});

test('an unseen presentation does not save a one-pixel fallback viewport',()=>{
  const f=fixture({visible:false});f.map.setCompact(false);f.map.setCompact(true);assert.equal(f.map.viewports.size,0);f.show();assertRootVisible(f);
});

test('empty loading state does not initialize a viewport before the first nodes arrive',()=>{
  const f=fixture({graphData:{project_id:'first',nodes:[],edges:[]}});f.map.setCompact(false);assert.equal(f.map.initialized,false);f.map.update(data());assertRootVisible(f);
});

test('status, summary, execution and semantic overlay updates preserve existing cells and viewport',()=>{
  const f=fixture(),before=f.map.graph.getCellById('first:question'),clears=f.map.graph.clearCount;
  f.map.setViewport({scale:.73,tx:44,ty:55});const viewport={...f.map.getViewport()};
  f.map.update({...f.map.data,revision:2,nodes:f.map.data.nodes.map(node=>({...node,summary:'新证据已经登记',status:'running'})),execution_location:{state:'running',active_node_ids:['first:question'],focus_node_id:'first:question',summary:'正在训练'}});
  assert.equal(f.map.graph.clearCount,clears);assert.equal(f.map.graph.getCellById('first:question'),before);assert.deepEqual({...f.map.getViewport()},viewport);
  assert.equal(before.getData().node.summary,'新证据已经登记');assert.equal(before.getData().processing,true);assert.equal(f.map.locationBar.dataset.state,'running');
  f.map.select('first:root');assert.equal(f.map.graph.getCellById('first:question').getData().processing,true,'historical selection cannot move current execution');
  f.map.showReferences=true;f.map.update({...f.map.data,edges:[...f.map.data.edges,{id:'proof',source:'first:question',target:'first:root',kind:'supports',primary:false}]});
  assert.equal(f.map.graph.clearCount,clears);assert.equal(f.map.graph.getEdges().length,2);assert.deepEqual({...f.map.getViewport()},viewport);
  const proof=f.map.graph.getCellById('proof');f.map.update({...f.map.data,edges:f.map.data.edges.map(edge=>edge.id==='proof'?{...edge,kind:'refutes'}:edge)});assert.equal(f.map.graph.getCellById('proof'),proof);assert.equal(proof.labels[0].attrs.label.text,'反驳');assert.equal(f.map.graph.clearCount,clears);
  f.map.update({...f.map.data,edges:f.map.data.edges.map(edge=>edge.id==='proof'?{...edge,source:'first:root',target:'first:question'}:edge)});assert.equal(proof.source.cell,'first:root');assert.equal(proof.target.cell,'first:question');assert.equal(f.map.graph.getCellById('proof'),proof);assert.equal(f.map.graph.clearCount,clears);
  f.map.showReferences=false;f.map.render();assert.equal(f.map.graph.getEdges().length,1);assert.equal(f.map.graph.clearCount,clears);
});

test('no real location does not focus the last node or call conversation selection',()=>{
  const f=fixture();f.map.focusCurrent();assert.equal(f.map.selected,null);assert.equal(f.map.locationButton.disabled,true);assert.deepEqual(f.selections,[]);
  f.map.update({...f.map.data,execution_location:{state:'paused',retained_node_ids:['first:question'],active_node_ids:[],reason:'需要重新规划'}});
  assert.equal(f.map.locationBar.dataset.state,'paused');assert.equal(f.map.graph.getCellById('first:question').getData().retained,true);assert.equal(f.map.graph.getCellById('first:question').getData().processing,false);assert.equal(f.map.locationButton.disabled,true);
});

test('live location updates are consumed even when the research revision is unchanged',()=>{
  const f=fixture(),revision=f.map.data.revision;
  f.map.update({...f.map.data,execution_location:{state:'running',active_node_ids:['first:question'],focus_node_id:'first:question'}});
  assert.equal(f.map.data.revision,revision);assert.equal(f.map.graph.getCellById('first:question').getData().processing,true);
  f.map.update({...f.map.data,execution_location:{state:'completed',active_node_ids:[],focus_node_id:null}});
  assert.equal(f.map.locationBar.dataset.state,'completed');assert.equal(f.map.graph.getCellById('first:question').getData().processing,false);assert.equal(f.map.selected,null);
});

test('key spine and a collapsed path open the original saved process without changing source graph',()=>{
  const graph={...data(),nodes:[...data().nodes,{id:'hidden',kind:'run',milestone:false},{id:'finding',kind:'finding',milestone:true}],edges:[...data().edges,{id:'to-hidden',source:'first:question',target:'hidden'},{id:'to-finding',source:'hidden',target:'finding'}]};
  const f=fixture({graphData:graph}),original=JSON.stringify(f.map.data);f.map.setSpineOnly(true);
  assert.equal(f.map.spineButton.attrs['aria-label'],'关键脉络');assert.equal(f.map.spineButton.attrs['aria-pressed'],'true');
  assert.equal(f.map.graph.getCellById('hidden'),undefined);const path=f.map.graph.getEdges().find(edge=>edge.getData().kind==='collapsed_path');assert(path);assert.deepEqual([...path.getData().original_edge_ids],['to-finding','to-hidden']);
  f.map.selected='finding';f.map.refreshCards();assert.equal(f.map.edgeStyle(path.getData()).stroke,'var(--em-wire)');assert.equal(f.map.edgeStyle(path.getData()).opacity,1);
  f.map.graph.handlers['edge:click']({edge:path});assert.equal(f.map.spineOnly,false);assert(f.map.graph.getCellById('hidden'));assert.equal(JSON.stringify(f.map.data),original);assert.equal(f.map.selected,'finding');
  assert.equal(f.map.fullButton.attrs['aria-label'],'完整过程');assert.equal(f.map.fullButton.attrs['aria-pressed'],'true');
});

test('default mainline gives an explicit folded-route count and search reveals complete saved history',()=>{
  const graph={...data(),research_path:{schema:'iterlab-research-path/v1',retained_branch_path:[],deferred_branch_ids:['unused']},
    nodes:[...data().nodes,{id:'unused',kind:'branch',status:'active',milestone:true,title:'Unexpanded route',research_path_role:'no_execution_records'}],
    edges:[...data().edges,{id:'alternative',source:'first:question',target:'unused',kind:'branches'}]};
  const f=fixture({graphData:graph}),original=JSON.stringify(f.map.data);
  assert.equal(f.map.spineOnly,true);assert.equal(f.map.graph.getCellById('unused'),undefined);
  assert.equal(f.map.count.textContent,'未展开 1 条路线');
  assert(f.map.spineButton.attrs['aria-description'].includes('尚无过程记录'));
  f.map.reveal('unused');assert.equal(f.map.spineOnly,false);assert(f.map.graph.getCellById('unused'));
  assert.equal(f.map.selected,'unused');assert.equal(JSON.stringify(f.map.data),original);
});

test('record-order display links are dashed and arrowless, and cannot become selected scientific ancestry',()=>{
  const graph={...data(),research_path:{schema:'iterlab-research-path/v1',retained_branch_path:['b'],deferred_branch_ids:[]},
    nodes:[...data().nodes,{id:'b',kind:'branch',milestone:true},...['t1','t2'].map((id,index)=>({id,kind:'trial',milestone:false,record_order:index,recorded_work:{run_count:1,result_count:0}}))],
    edges:[...data().edges,{id:'qb',source:'first:question',target:'b'},{id:'b1',source:'b',target:'t1',kind:'executes'},{id:'b2',source:'b',target:'t2',kind:'executes'}]};
  const f=fixture({graphData:graph}),order=f.map.graph.getEdges().find(edge=>edge.getData().kind==='record_order');
  assert(order);assert.equal(f.map.edgeStyle(order.getData()).targetMarker,null);assert.equal(f.map.edgeStyle(order.getData()).strokeDasharray,'5 5');
  assert.equal(order.labels[0].attrs.label.text,'记录顺序');
  f.map.select('t2');assert(!f.map.focus.nodes.has('t1'),'record ordering is not scientific parentage');
  assert(f.map.data.edges.some(edge=>edge.source==='b'&&edge.target==='t2'&&edge.kind==='executes'));
});

test('research sections translate known display states without changing saved evidence',()=>{
  const f=fixture(),value={status:'not_applicable',description:'这一设计任务没有数值对比。'};let rendered;
  f.map.options.renderRecord=record=>{rendered=record;return '';};
  f.map.proseFields(f.map.detail,[['可比性',value]]);
  assert.equal(rendered.status,'不适用');assert.equal(value.status,'not_applicable');
});

test('formal review details render the original comment and link exact evidence without raw JSON',()=>{
  const f=fixture(),opened=[];f.map.options.onOpenSource=(source)=>opened.push(source);
  const node={id:'review',kind:'plan_review',status:'request_changes',origin_session_id:'original-session',summary:'小差值不夸大…'};
  const review={origin_session_id:'review-owner-session',status:'request_changes',status_label:'方案退回，待修订',comment:'小差值不夸大，不额外fit。',plan_version_label:'1.0',requested_at:'2026-10-23T05:00:00Z',decided_at:'2026-10-23T05:01:00Z',source_event_seqs:[60,62],execution_target:{kind:'local'},notice:'不增加执行许可或预算。'};
  f.map.renderDetailBody(node,{node,plan_review:review});
  const descendants=node=>[node,...node.children.flatMap(descendants)],elements=descendants(f.map.detail),text=elements.map(node=>node.textContent||'').join('\n');
  assert(text.includes(review.comment));assert.equal(text.split('小差值不夸大').length-1,1,'the review preview must not repeat the full comment above its readable section');assert(text.includes('方案 1.0'));assert(text.includes('本地运行环境'));assert(text.includes('不增加执行许可或预算。'));assert(!text.includes('"plan_ref"'));
  const source=elements.find(node=>node.attrs['aria-label']==='查看审核原始证据 #62');assert(source);source.events.click({});assert.equal(opened[0].kind,'event');assert.equal(opened[0].seq,62);assert.equal(opened[0].origin_session_id,'review-owner-session');
});

test('stale and unresolved pending reviews preserve history without telling users to approve them',()=>{
  for(const [status,label,reason] of [['review_stale','历史待审已失效','当前需求尚未确认，原请求不能批准。'],['review_unknown','审核范围待核对','SDK绑定尚未核对。']]){
    const f=fixture(),node={id:'old-request',kind:'plan_review',status};
    f.map.renderDetailBody(node,{node,plan_review:{status,status_label:label,applicability:{state:status==='review_stale'?'stale':'unknown',reason,can_review:false}}});
    const descendants=node=>[node,...node.children.flatMap(descendants)],text=descendants(f.map.detail).map(item=>item.textContent||'').join('\n');
    assert(text.includes(label));assert(text.includes(reason));assert(text.includes('此处保留原请求记录'));assert(!text.includes('等待用户审核此版本'));
  }
});

test('resolved split evidence opens each exact source owner and leaves prose unlinked',()=>{
  const f=fixture(),opened=[];f.map.options.onOpenSource=source=>opened.push(source);
  const node={id:'branch',kind:'branch',origin_session_id:'branch-owner'};
  for(const [seq,owner] of [[172,'first-owner'],[160,'second-owner'],[166,'first-owner']]){
    const source=f.map.evidenceSource({ref:'event:'+seq,source_ref:'event:172; event:160; event:166',resolved:true,record:{seq,study_id:owner,kind:'tool_result'}},node);
    assert(source);source.events.click({});
  }
  assert.deepEqual(opened.map(value=>[value.kind,value.seq,value.origin_session_id]),[['event',172,'first-owner'],['event',160,'second-owner'],['event',166,'first-owner']]);
  assert.equal(f.map.evidenceSource({ref:'event:1; suggests a better result',resolved:false},node),null);
  const message=f.map.evidenceSource({ref:'message:9',resolved:true,record:{seq:9,study_id:'user-owner',role:'user'}},node);message.events.click({});
  assert.equal(opened[3].kind,'message');assert.equal(opened[3].origin_session_id,'user-owner');
});

test('native blank clicks clear focus even when the X6 blank event is missing',()=>{
  const f=fixture();let cleared=0;f.map.options.onDeselect=()=>cleared++;
  f.map.select('first:question');assert.equal(f.map.detail.hidden,false);
  f.map.canvas.events.click({button:0,target:{closest:()=>null}});
  assert.equal(f.map.selected,null);assert.equal(f.map.detail.hidden,true);assert.equal(cleared,1);
  for(const cell of f.map.graph.getNodes())assert.equal(cell.getData().focusDimmed,false);
});

test('dragging the canvas preserves selection and the next real blank click clears it',()=>{
  const f=fixture(),target={closest:()=>null};f.map.select('first:question');
  f.map.panPointerDown({button:0,pointerId:1,clientX:10,clientY:20,target});
  f.map.panPointerMove({pointerId:1,clientX:35,clientY:60,preventDefault(){}});
  f.map.panPointerUp({pointerId:1});
  f.map.canvas.events.click({button:0,target,preventDefault(){},stopImmediatePropagation(){}});
  assert.equal(f.map.selected,'first:question');
  f.map.panPointerDown({button:0,pointerId:2,clientX:10,clientY:20,target});f.map.panPointerUp({pointerId:2});
  f.map.canvas.events.click({button:0,target});assert.equal(f.map.selected,null);
});

test('visible references and their peer cards receive focus without recolouring every path blue',()=>{
  const graph={...data(),nodes:[...data().nodes,{id:'evidence',kind:'finding',milestone:true},{id:'other',kind:'finding',milestone:true}],
    edges:[...data().edges,{id:'ref',source:'evidence',target:'first:question',kind:'informed_by',primary:false}]};
  const f=fixture({graphData:graph});f.map.select('first:question');
  assert.equal(f.map.graph.getCellById('evidence').getData().focusDimmed,true);
  f.map.referenceButton.events.click();
  assert.equal(f.map.graph.getCellById('evidence').getData().focusDimmed,false);
  assert.equal(f.map.graph.getCellById('other').getData().focusDimmed,true);
  const edge=f.map.graph.getCellById('ref').getData();assert.equal(f.map.edgeStyle(edge).opacity,1);assert.equal(f.map.edgeStyle(edge).stroke,'var(--em-reference)');
  const cell=f.map.graph.getCellById('first:root');assert.equal(cell.getData().ancestry,true);
  assert.equal(f.map.edgeStyle(f.map.data.edges[0]).stroke,'var(--em-wire)');
});

test('outline groups expand locally and a view toggle restores the compact outline without modifying source',()=>{
  const graph={...data(),plan_series:{schema:'iterlab-plan-series/v1',families:[{id:'p',number:1,plan_ids:['p']}]},
    nodes:[...data().nodes,{id:'series:p',kind:'series',series_id:'p'},...[
      {id:'p',kind:'plan'},{id:'a',kind:'trial'},{id:'b',kind:'trial'},{id:'review',kind:'review'}
    ].map(n=>({...n,series_id:'p',plan_id:'p',milestone:true}))],
    edges:[...data().edges,{id:'series',source:'first:question',target:'series:p'},
      {id:'version',source:'series:p',target:'p'},...['a','b','review'].map(id=>({id:'p-'+id,source:'p',target:id,kind:id==='review'?'assesses':'executes'}))]};
  const f=fixture({graphData:graph}),original=JSON.stringify(f.map.data);
  assert.equal(f.map.graph.getCellById('a'),undefined);assert(f.map.graph.getCellById('process:p'));
  f.map.select('process:p');assert(f.map.graph.getCellById('a'));assert.equal(f.map.spineOnly,true);assert.equal(f.details.length,0);
  f.map.setSpineOnly(false);assert.equal(f.map.fullButton.attrs['aria-pressed'],'true');
  f.map.setSpineOnly(true);assert.equal(f.map.graph.getCellById('a'),undefined);
  f.map.reveal('a');assert.equal(f.map.selected,'a');assert.equal(f.map.spineOnly,true);assert(f.map.graph.getCellById('a'));
  assert.equal(JSON.stringify(f.map.data),original);
});

test('explicit context framing leaves selection, details and original data untouched',()=>{
  const f=fixture(),original=JSON.stringify(f.map.data);assert.equal(f.map.frameContext('first:question'),true);
  assert.equal(f.map.selected,null);assert.deepEqual(f.selections,[]);assert.deepEqual(f.details,[]);assert.equal(JSON.stringify(f.map.data),original);
  const view={...f.map.getViewport()};f.map.update({...f.map.data,revision:4});assert.deepEqual({...f.map.getViewport()},view);
  assert.equal(f.map.frameContext('missing'),false);assert.deepEqual({...f.map.getViewport()},view);
});

test('hidden explicit context waits for real dimensions and cannot leak to another project',()=>{
  const f=fixture({visible:false});assert.equal(f.map.frameContext('first:question'),false);assert.equal(f.map.pendingFrame,'first:question');
  f.show();assert.equal(f.map.pendingFrame,null);assert(f.map.initialized);assert.equal(f.map.selected,null);
  f.hide();f.map.frameContext('first:question');f.map.update(data('second'));assert.equal(f.map.pendingFrame,null);f.show();assertRootVisible(f);
});

test('host compact summary can replace the duplicate detail while full details stay available',()=>{
  const f=fixture();f.map.options.compactDetails=false;f.map.select('first:question');assert(f.map.detail.hidden);assert.equal(f.map.selected,'first:question');
  f.map.setCompact(false);assert.equal(f.map.detail.hidden,false);
});

test('folded execution bookmarks and current location reveal the original trial only',()=>{
  const graph=data();graph.nodes.push({id:'proof',kind:'research',execution_detail_only:true,execution_trial_id:'first:question'});
  graph.edges.push({id:'proof-edge',source:'first:question',target:'proof',primary:true});
  graph.execution_location={state:'waiting',focus_node_id:'proof',active_node_ids:[],retained_node_ids:['proof']};
  const f=fixture({graphData:graph});f.map.setSpineOnly(false);
  assert(!f.map.graph.getCellById('proof'));assert.equal(f.map.location.focus_node_id,'first:question');
  assert.equal(f.map.frameContext('proof'),true);assert.equal(f.map.selected,null);
  assert.equal(f.map.reveal('proof'),true);assert.equal(f.map.selected,'first:question');
  assert(!f.map.graph.getCellById('proof'));
});
