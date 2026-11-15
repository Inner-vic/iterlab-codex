import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeData,visibleData,descendants,upstreamPath,layoutData,searchNodes,currentNode,versionLabel,displayStatus,statusLabel,kindLabel,nodeKindLabel,executionLocation,researchSpine,geometrySignature,primaryEdge,relationLabel} from '../model.mjs';

const data=normalizeData({project_id:'p',nodes:[
  {id:'q',kind:'question',title:'未见设备故障诊断'},
  {id:'p',kind:'plan',title:'方案 1.0',revision:1},
  {id:'a',kind:'branch',title:'频域特征'},
  {id:'b',kind:'branch',title:'设备差异',status:'parked'},
  {id:'c',kind:'branch',title:'频带消融'},
  {id:'d',kind:'trial',title:'低信噪比验证',status:'running'},
  {id:'p2',kind:'plan',title:'方案 1.1',revision:2},
],edges:[
  {id:'e1',source:'q',target:'p',kind:'evolves'},
  {id:'e2',source:'p',target:'a',kind:'branches'},
  {id:'e3',source:'p',target:'b',kind:'branches'},
  {id:'e4',source:'a',target:'c',kind:'branches'},
  {id:'e5',source:'c',target:'d',kind:'executes'},
  {id:'e6',source:'p',target:'p2',kind:'revises'},
  {id:'ref',source:'d',target:'p2',kind:'informed_by',primary:false},
]});

test('formal plan review is a readable milestone and cannot imply execution or scientific success',()=>{
  assert.equal(kindLabel('plan_review'),'方案审核');assert.equal(statusLabel('request_changes'),'退回修改');assert.equal(statusLabel('awaiting_review'),'等待审核');assert.equal(statusLabel('review_approved'),'审核通过');
  assert.equal(statusLabel('review_stale'),'历史待审已失效');assert.equal(statusLabel('review_unknown'),'审核范围待核对');
  const data=normalizeData({nodes:[{id:'p',kind:'plan',version_label:'1.0'},{id:'review',kind:'plan_review',status:'request_changes'}],edges:[{id:'r',source:'p',target:'review',kind:'reviewed'}]});
  assert.deepEqual(researchSpine(data).nodes.map(node=>node.id),['p','review']);assert.equal(relationLabel(data.edges[0]),'审核此版本');assert.equal(currentNode(data.nodes,{state:'paused',active_node_ids:[]}),null);
});

test('key view retains diagnostic and training result identities without counting fits',()=>{
  const source={basis:'record_result_declaration'},nodes=[{id:'root',kind:'question'},
    {id:'trial',kind:'trial',title:'One research candidate'},
    {id:'d1',kind:'result',title:'诊断结果',milestone:true,run_kind:'diagnostic',result_source:source},
    {id:'d2',kind:'result',title:'诊断结果',milestone:true,run_kind:'diagnostic',result_source:source},
    {id:'fit',kind:'result',title:'训练结果',milestone:true,run_kind:'training',result_source:source}];
  const full=normalizeData({root_id:'root',nodes,edges:nodes.slice(1).map((node,index)=>({id:'e'+index,source:index?'trial':'root',target:node.id}))});
  const key=researchSpine(full);
  assert.deepEqual(key.nodes.filter(node=>node.kind==='result').map(node=>[node.id,nodeKindLabel(node)]),[['d1','诊断登记'],['d2','诊断登记'],['fit','训练登记']]);
  assert.equal(nodeKindLabel({kind:'result',run_kind:'training'}),'结果记录','unbound type does not become a training declaration');
  assert.equal(nodeKindLabel({kind:'result',run_kind:'evaluation',result_source:source}),'评估登记');
  assert.equal(searchNodes(key.nodes,'训练登记')[0].id,'fit');
  assert.equal(full.nodes.filter(node=>node.kind==='trial').length,1);
});

test('normalizes untrusted graph endpoint and duplicate ids',()=>{
  const value=normalizeData({nodes:[{id:'a',title:'<script>alert(1)</script>'},{id:'a'},{id:'b'},null],edges:[{id:'good',source:'a',target:'b'},{id:'bad',source:'a',target:'missing'},{id:'self',source:'a',target:'a'}]});
  assert.equal(value.nodes.length,2);assert.equal(value.edges.length,1);assert.equal(value.nodes[0].title,'<script>alert(1)</script>');
});
test('nested descendants ignore reference edge to revised plan',()=>{
  assert.deepEqual([...descendants('a',data.edges)].sort(),['c','d']);
  const visible=visibleData(data,{collapsed:new Set(['a'])});
  assert(visible.nodes.some(node=>node.id==='p2'));assert(!visible.nodes.some(node=>node.id==='d'));
});
test('folding cycle terminates and never hides folded origin',()=>{
  assert.deepEqual([...descendants('a',[{source:'a',target:'b'},{source:'b',target:'a'}])],['b']);
});
test('upstream focus includes only the selected node and recorded primary ancestry',()=>{
  const focused=upstreamPath('d',data.edges);
  assert.deepEqual([...focused.nodes].sort(),['a','c','d','p','q']);
  assert.deepEqual([...focused.edges].sort(),['e1','e2','e4','e5']);
  const revised=upstreamPath('p2',data.edges);
  assert.deepEqual([...revised.nodes].sort(),['p','p2','q'],'evidence from d is not a parent');
  assert.deepEqual([...upstreamPath(null,data.edges).nodes],[]);
});
test('upstream focus handles multiple parents and malformed cycles without repeated visits',()=>{
  const focused=upstreamPath('c',[{id:'ab',source:'a',target:'b'},{id:'bc',source:'b',target:'c'},{id:'dc',source:'d',target:'c'},{id:'ca',source:'c',target:'a'}]);
  assert.deepEqual([...focused.nodes].sort(),['a','b','c','d']);
  assert.equal(focused.edges.size,4);
});
test('hide parked retains viable descendant nodes',()=>{
  const value=normalizeData({nodes:[{id:'a',status:'parked'},{id:'b',status:'running'}],edges:[{source:'a',target:'b'}]});
  assert.deepEqual(visibleData(value,{hideParked:true}).nodes.map(node=>node.id),['b']);
});
test('dagre places all nested descendants below their parent without overlap',()=>{
  const result=layoutData(data);const nodes=new Map(result.nodes.map(node=>[node.id,node]));
  assert(nodes.get('c').y>nodes.get('a').y);assert(nodes.get('d').y>nodes.get('c').y);
  for(let i=0;i<result.nodes.length;i++)for(let j=i+1;j<result.nodes.length;j++){
    const a=result.nodes[i],b=result.nodes[j];assert(a.x+a.width<=b.x||b.x+b.width<=a.x||a.y+a.height<=b.y||b.y+b.height<=a.y,`overlap ${a.id} ${b.id}`);
  }
});
test('reference edges do not change dagre hierarchy geometry',()=>{
  assert.deepEqual(layoutData(data).nodes,layoutData({...data,edges:data.edges.filter(edge=>edge.kind!=='informed_by')}).nodes);
});
test('search accepts titles, summaries and semantic plan versions',()=>{
  assert.equal(searchNodes(data.nodes,'频带')[0].id,'c');assert.equal(searchNodes(data.nodes,'1.1')[0].id,'p2');assert.equal(versionLabel(data.nodes.find(node=>node.id==='p')),'1.0');
  assert.equal(versionLabel({kind:'alignment',revision:9}),'','alignment source revisions are not plan versions');
});
test('current position comes only from the explicit host location, never saved cursor or ordering',()=>{
  assert.equal(currentNode(data.nodes),null);assert.equal(currentNode([]),null);
  assert.equal(currentNode([{id:'cursor',active:true},{id:'other',status:'running'}]),null);
  assert.equal(currentNode(data.nodes,{focus_node_id:'d'}).id,'d');
  assert.equal(currentNode(data.nodes,{focus_node_id:'missing'}),null);
});
test('organizational project lifecycle does not display or focus as ongoing research',()=>{
  const root={id:'project-root',kind:'project',status:'active',organizational:true},closed={id:'closed-result',kind:'result',status:'completed'};
  assert.equal(displayStatus(root),'recorded');assert.equal(statusLabel(displayStatus(root)),'已记录');
  assert.equal(root.status,'active','display projection preserves the original project lifecycle');
  assert.equal(currentNode([root,closed]),null);
  assert.equal(displayStatus({kind:'question',status:'active'}),'defined');
  assert.equal(displayStatus({kind:'question',status:'awaiting_input'}),'awaiting_input');
  assert.equal(displayStatus({kind:'trial',status:'active'}),'active','real lineage and running trial statuses remain exact');
  assert.equal(statusLabel(displayStatus({kind:'project',status:'archived'})),'已归档');
});

test('live execution is separate from retained directions and an explicit empty activity snapshot',()=>{
  const nodes=[{id:'plan',active:true},{id:'trial',status:'running'}];
  const live=executionLocation({nodes,execution_location:{state:'running',focus_node_id:'trial',active_node_ids:['trial','missing','trial'],retained_node_ids:['plan']}});
  assert.deepEqual(live.active_node_ids,['trial']);assert.deepEqual(live.retained_node_ids,['plan']);assert.equal(live.focus_node_id,'trial');
  const paused=executionLocation({nodes,execution_location:{state:'paused',focus_node_id:'plan',active_node_ids:['trial'],reason:'等待用户'}});
  assert.deepEqual(paused.active_node_ids,[]);assert.equal(paused.focus_node_id,'plan');assert.equal(paused.label,'已暂停');
  assert.equal(executionLocation({nodes}).state,'unknown');assert.equal(executionLocation({nodes}).focus_node_id,null);
  assert.equal(executionLocation({nodes,execution_location:{state:'completed',focus_node_id:'trial',active_node_ids:['trial']}}).focus_node_id,null);
  const unbound=executionLocation({nodes,execution_location:{state:'running',active_node_ids:[],focus_node_id:null,summary:'正在执行未关联的命令'}});
  assert.equal(unbound.state,'running');assert.equal(currentNode(nodes,unbound),null,'activity without a node does not invent one');
  const remote=executionLocation({nodes,execution_location:{state:'unknown',focus_node_id:'trial',active_node_ids:['trial'],summary:'最近平台回执显示运行中，待核对'}});
  assert.equal(currentNode(nodes,remote).id,'trial');assert.deepEqual(remote.active_node_ids,[],'last-observed platform state can be inspected without pretending live execution');
});

test('support and refutation never become parents, fold descendants or layout constraints',()=>{
  const values=normalizeData({nodes:[{id:'q'},{id:'r'},{id:'x'}],edges:[{id:'struct',source:'q',target:'r',kind:'branches'},{id:'support',source:'r',target:'x',kind:'supports',primary:true},{id:'refute',source:'x',target:'q',relation:'refutes'}]});
  assert.equal(primaryEdge(values.edges[1]),false);assert.deepEqual([...descendants('q',values.edges)],['r']);
  assert.deepEqual([...upstreamPath('x',values.edges).nodes],['x']);
  assert.deepEqual(layoutData(values).nodes,layoutData({...values,edges:[values.edges[0]]}).nodes);
  assert.equal(primaryEdge({kind:'contradicts'}),false);assert.equal(relationLabel({kind:'contradicts'}),'反驳');assert.equal(primaryEdge({kind:'leads_to'}),false);assert.equal(relationLabel({kind:'leads_to'}),'引出探索');
  assert.equal(statusLabel(displayStatus({kind:'hypothesis',status:'proposed',claim_status:'candidate'})),'尚未验证');
});

test('key-spine compression retains complete original subgraph IDs without inventing support',()=>{
  const graph=normalizeData({root_id:'q',nodes:[{id:'q',milestone:true},{id:'a',milestone:false},{id:'b',milestone:false},{id:'f',milestone:true}],edges:[{id:'qa',source:'q',target:'a'},{id:'qb',source:'q',target:'b'},{id:'af',source:'a',target:'f'},{id:'bf',source:'b',target:'f'},{id:'relation',source:'f',target:'q',kind:'supports',primary:false}]});
  const before=JSON.stringify(graph),view=researchSpine(graph),proxy=view.edges.find(edge=>edge.kind==='collapsed_path');
  assert.deepEqual(view.nodes.map(node=>node.id),['q','f']);assert.deepEqual(proxy.original_edge_ids,['af','bf','qa','qb']);assert.deepEqual(proxy.hidden_node_ids,['a','b']);assert.equal(proxy.basis,'unknown');assert.equal(proxy.projection_only,true);
  assert(view.edges.some(edge=>edge.id==='relation'));assert.equal(JSON.stringify(graph),before);
});

test('spine retains an active nonmilestone, handles cycles and does not merge a semantic path',()=>{
  const graph=normalizeData({root_id:'q',nodes:[{id:'q',milestone:true},{id:'a',milestone:false},{id:'b',milestone:false},{id:'f',milestone:true}],edges:[{id:'qa',source:'q',target:'a'},{id:'ab',source:'a',target:'b'},{id:'ba',source:'b',target:'a'},{id:'bf',source:'b',target:'f',kind:'supports',primary:false}],execution_location:{state:'running',active_node_ids:['b'],focus_node_id:'b'}});
  const view=researchSpine(graph);assert(view.nodes.some(node=>node.id==='b'));assert(!view.edges.some(edge=>edge.kind==='collapsed_path'&&edge.target==='f'));
});

test('content, revisions, live observations and semantic overlays reuse geometry',()=>{
  const changed={...data,revision:9,nodes:data.nodes.map(node=>({...node,status:'completed',summary:'更新后的说明',active:true})),execution_location:{state:'paused'},edges:[...data.edges,{id:'new-proof',source:'d',target:'p',kind:'refutes'}]};
  assert.equal(geometrySignature(data),geometrySignature(changed));
  assert.notEqual(geometrySignature(data),geometrySignature(data,{compact:true}));
  assert.notEqual(geometrySignature(data),geometrySignature({...data,edges:[...data.edges,{id:'branch',source:'b',target:'d'}]}));
});
test('handles a 200-node research hierarchy',()=>{
  const nodes=Array.from({length:200},(_,i)=>({id:`n${i}`,kind:'branch',title:`路线 ${i}`}));
  const edges=nodes.slice(1).map((node,i)=>({source:`n${Math.floor(i/3)}`,target:node.id,id:`e${i}`}));
  const result=layoutData({nodes,edges});assert.equal(result.nodes.length,200);assert(result.nodes.every(node=>Number.isFinite(node.x)&&Number.isFinite(node.y)));
});
