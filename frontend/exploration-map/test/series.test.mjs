import test from 'node:test';
import assert from 'node:assert/strict';
import {layoutData,normalizeData,visibleData,researchMainline,geometrySignature,upstreamPath,selectionFocus,nodeKindLabel,versionLabel,displayStatus,relationLabel} from '../model.mjs';

const fixture=()=>normalizeData({plan_series:{schema:'iterlab-plan-series/v1',families:[
  {id:'a',number:1,predecessor_id:null,plan_ids:['a','b','c']},
  {id:'d',number:2,predecessor_id:'a',plan_ids:['d','e']},
]},nodes:[
  {id:'q',kind:'alignment'},
  {id:'series:a',kind:'series',series_id:'a'},
  ...['a','b','c'].map((id,i)=>({id,kind:'plan',plan_id:id,series_id:'a',version_label:`1.${i}`})),
  {id:'t1',kind:'trial',plan_id:'b',series_id:'a'},
  {id:'t2',kind:'trial',plan_id:'b',series_id:'a'},
  {id:'result',kind:'result',plan_id:'b',series_id:'a'},
  {id:'review',kind:'review',plan_id:'b',series_id:'a'},
  {id:'series:d',kind:'series',series_id:'d'},
  ...['d','e'].map((id,i)=>({id,kind:'plan',plan_id:id,series_id:'d',version_label:`2.${i}`})),
].map(n=>({...n,milestone:true})),edges:[
  {id:'root',source:'q',target:'series:a',primary:true},
  ...['a','b','c'].map(id=>({id:'s'+id,source:'series:a',target:id,kind:'contains_plan',primary:true})),
  ...['t1','t2','review'].map(id=>({id:'m'+id,source:'b',target:id,kind:'organizes',primary:true})),
  {id:'r',source:'t1',target:'result',primary:true},
  {id:'next-series',source:'review',target:'series:d',kind:'series_progression',primary:true},
  ...['d','e'].map(id=>({id:'s'+id,source:'series:d',target:id,kind:'contains_plan',primary:true})),
  {id:'revision',source:'b',target:'c',kind:'revises',primary:false},
  {id:'trigger',source:'review',target:'c',kind:'motivates',primary:false},
]});

for(const compact of [false,true])test(`same-series minors share a rank; major successor follows all previous work (${compact})`,()=>{
  const data=visibleData(fixture()),layout=layoutData(data,{compact}),byId=new Map(layout.nodes.map(n=>[n.id,n]));
  assert.equal(byId.get('a').y,byId.get('b').y);assert.equal(byId.get('b').y,byId.get('c').y);
  assert.equal(byId.get('d').y,byId.get('e').y);
  assert.equal(byId.get('t1').y,byId.get('t2').y);
  const oldBottom=Math.max(...layout.nodes.filter(n=>n.series_id==='a').map(n=>n.y+n.height));
  assert(byId.get('series:d').y>oldBottom,'series 2 must be below every result and review in series 1');
  for(const [i,a] of layout.nodes.entries())for(const b of layout.nodes.slice(i+1)){
    const overlap=a.x<b.x+b.width&&a.x+a.width>b.x&&a.y<b.y+b.height&&a.y+a.height>b.y;
    assert.equal(overlap,false,`cards overlap: ${a.id}/${b.id}`);
  }
  assert.equal(data.plan_series.schema,'iterlab-plan-series/v1');
  assert.equal(layout.nodes.length,fixture().nodes.length);
});

test('evidence overlays do not move cards or become ancestry; series ownership remains highlightable',()=>{
  const data=fixture(),before=layoutData(data),extra={id:'extra',source:'result',target:'c',kind:'motivates',primary:false};
  const after={...data,edges:[...data.edges,extra]};
  assert.equal(geometrySignature(data),geometrySignature(after));
  assert.deepEqual(before.nodes,layoutData(after).nodes);
  const path=upstreamPath('c',after.edges);
  assert(path.nodes.has('series:a'));assert(path.nodes.has('q'));
  assert(!path.nodes.has('result'));assert(!path.nodes.has('b'));
});

test('folding keeps family metadata but never leaves hidden cards in geometry',()=>{
  const full=fixture(),filtered=visibleData(full,{collapsed:new Set(['series:d'])});
  const layout=layoutData(filtered);
  assert(layout.nodes.some(n=>n.id==='series:d'));
  assert(!layout.nodes.some(n=>n.id==='d'||n.id==='e'));
  assert.equal(layout.nodes.length,filtered.nodes.length);
});

test('new evidence and deep local experiments do not turn minor versions into a chain',()=>{
  const data=fixture();data.nodes.push({id:'deep',kind:'result',plan_id:'a',series_id:'a'});
  data.edges.push({id:'deep-edge',source:'a',target:'deep',primary:true});
  const layout=layoutData(data),byId=new Map(layout.nodes.map(n=>[n.id,n]));
  assert.equal(byId.get('a').y,byId.get('c').y);
});

test('outline actually groups process history without deleting records, findings or reviews',()=>{
  const data=fixture(),original=JSON.stringify(data),outline=researchMainline(data);
  assert(outline.nodes.length<data.nodes.length);
  assert(outline.nodes.some(n=>n.id==='review'));
  const group=outline.nodes.find(n=>n.kind==='process_group');assert(group);
  assert.deepEqual(group.hidden_node_ids,['t1','t2','result']);
  assert.equal(group.plan_id,'b');assert.equal(group.projection_only,true);
  assert.equal(JSON.stringify(data),original);
  const expanded=researchMainline(data,{expandedPlans:new Set(['b'])});
  assert.equal(expanded.nodes.length,data.nodes.length);assert(!expanded.nodes.some(n=>n.kind==='process_group'));
  const byId=new Map(layoutData(outline).nodes.map(n=>[n.id,n]));
  assert.equal(byId.get('a').y,byId.get('c').y);
});

test('current execution and its real ancestry remain visible inside outline',()=>{
  const data=fixture();data.execution_location={state:'running',focus_node_id:'result',active_node_ids:['result']};
  const outline=researchMainline(data);
  assert(outline.nodes.some(n=>n.id==='result'));assert(outline.nodes.some(n=>n.id==='t1'));
  assert(outline.nodes.some(n=>n.kind==='process_group'&&n.hidden_node_ids.includes('t2')));
});

test('reference focus includes direct related cards only when enabled and never changes ancestry',()=>{
  const data=fixture(),raw=upstreamPath('c',data.edges),hidden=selectionFocus('c',data.edges),shown=selectionFocus('c',data.edges,{showReferences:true});
  assert.deepEqual([...hidden.nodes],[...raw.nodes]);assert(!hidden.nodes.has('review'));
  assert(shown.nodes.has('review'));assert(shown.nodes.has('b'));
  assert(shown.edges.has('revision'));assert(shown.edges.has('trigger'));
  assert(!shown.nodes.has('t1'));assert(!shown.nodes.has('t2'));
  assert(!upstreamPath('c',data.edges).nodes.has('review'));
});

function candidate(data,id,parent,{edge=true,...extra}={}){
  data.nodes.push({id,kind:'plan',plan_id:id,plan_candidate:true,candidate_parent_id:parent,previous_plan_id:parent,status:'proposed',title:id,...extra});
  if(edge)data.edges.push({id:'candidate:'+id,source:parent,target:id,kind:'candidate_revision',primary:true});
}

for(const compact of [false,true])for(const outline of [false,true])test(`unpublished exact candidate chains follow their predecessor without becoming minor columns (${compact}/${outline})`,()=>{
  const source=fixture();let parent='b';
  for(let i=1;i<=7;i++){candidate(source,'candidate-'+i,parent);parent='candidate-'+i;}
  source.nodes.push({id:'candidate-trial',kind:'trial',plan_id:'candidate-3',status:'failed'},
    {id:'candidate-retry',kind:'trial',plan_id:'candidate-3',status:'completed'});
  source.edges.push({id:'candidate-run',source:'candidate-3',target:'candidate-trial',kind:'executes',primary:true},
    {id:'retry-run',source:'candidate-trial',target:'candidate-retry',kind:'retries',primary:true},
    {id:'retry-membership',source:'candidate-3',target:'candidate-retry',kind:'executes',primary:false});
  const original=JSON.stringify(source),view=outline?researchMainline(source):source;
  const layout=layoutData(view,{compact}),nodes=new Map(layout.nodes.map(node=>[node.id,node]));
  assert.equal(nodes.get('a').y,nodes.get('b').y);assert.equal(nodes.get('b').y,nodes.get('c').y);
  parent='b';for(let i=1;i<=7;i++){assert(nodes.get('candidate-'+i).y>nodes.get(parent).y);parent='candidate-'+i;}
  assert(nodes.get('series:d').y>Math.max(...layout.nodes.filter(node=>node.id.startsWith('candidate-')).map(node=>node.y+node.height)));
  assert.equal(layout.nodes.length,view.nodes.length);assert.deepEqual(layout.edges,view.edges);
  if(!outline)assert(nodes.get('candidate-retry').y>nodes.get('candidate-trial').y);
  else assert(view.nodes.some(node=>node.kind==='process_group'&&node.plan_id==='candidate-3'));
  for(const [index,a] of layout.nodes.entries())for(const b of layout.nodes.slice(index+1))assert(!(a.x<b.x+b.width&&a.x+a.width>b.x&&a.y<b.y+b.height&&a.y+a.height>b.y),`overlap ${a.id}/${b.id}`);
  assert.equal(JSON.stringify(source),original);assert.equal(source.plan_series.families.length,2);
  assert(source.nodes.filter(node=>node.plan_candidate).every(node=>versionLabel(node)===''&&displayStatus(node)==='proposed'));
});

test('candidate forks use exact parent facts; fields can constrain placement without inventing a displayed edge',()=>{
  const data=fixture();candidate(data,'later-written','a',{created_at:'2030-01-01'});candidate(data,'earlier-written','a',{created_at:'2000-01-01'});
  candidate(data,'field-only','later-written',{edge:false});
  const original=JSON.stringify(data),layout=layoutData(data),nodes=new Map(layout.nodes.map(node=>[node.id,node]));
  assert.equal(nodes.get('later-written').y,nodes.get('earlier-written').y);
  assert(nodes.get('field-only').y>nodes.get('later-written').y);
  assert.deepEqual(layout.edges,data.edges);assert.equal(JSON.stringify(data),original);
  assert.equal(nodeKindLabel(nodes.get('field-only')),'候选方案（未发版）');
  assert.equal(versionLabel({...nodes.get('field-only'),revision:99,revision_label:'9.9'}),'');
  assert.equal(relationLabel({kind:'candidate_revision'}),'候选方案修订');
  const changed=structuredClone(data);changed.nodes.find(node=>node.id==='field-only').previous_plan_id='a';
  assert.notEqual(geometrySignature(data),geometrySignature(changed));
});

test('ambiguous, unknown, cross-project and cyclic candidates remain unassigned instead of gaining inferred ancestry',()=>{
  const data=fixture();
  candidate(data,'conflicting','a',{previous_plan_id:'b'});
  candidate(data,'missing','not-present');
  candidate(data,'cycle-a','cycle-b');candidate(data,'cycle-b','cycle-a');
  data.nodes.find(node=>node.id==='b').project_id='project-one';candidate(data,'other-project','b',{project_id:'project-two'});
  const layout=layoutData(data),nodes=new Map(layout.nodes.map(node=>[node.id,node]));
  for(const id of ['conflicting','missing','cycle-a','cycle-b','other-project'])assert(nodes.get(id).y<nodes.get('series:a').y,id+' remains in context');
  assert.equal(layout.nodes.length,data.nodes.length);assert.deepEqual(layout.edges,data.edges);
});

test('a published plan is included by its exact family id even when the self plan_id is absent',()=>{
  const data=fixture();delete data.nodes.find(node=>node.id==='b').plan_id;
  const nodes=new Map(layoutData(data).nodes.map(node=>[node.id,node]));
  assert.equal(nodes.get('a').y,nodes.get('b').y);assert.equal(nodes.get('b').y,nodes.get('c').y);
});
