import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeData,researchMainline,researchSpine,layoutData,relationLabel,nodeKindLabel} from '../model.mjs';

function fixture(){
  return normalizeData({root_id:'q',nodes:[
    {id:'q',kind:'question',title:'Question',milestone:true},
    {id:'p',kind:'plan',title:'Approved plan',milestone:true},
    {id:'b',kind:'branch',title:'Retained route',milestone:true,research_path_role:'retained_path'},
    {id:'t1',kind:'trial',title:'Baseline',milestone:false,branch_id:'b',record_order:1,recorded_work:{run_count:1,result_count:1}},
    {id:'r1',kind:'result',title:'First result',milestone:true},
    {id:'t2',kind:'trial',title:'Evidence-driven next experiment',milestone:false,branch_id:'b',record_order:2,recorded_work:{run_count:1,result_count:1}},
    {id:'r2',kind:'result',title:'Next result',milestone:true},
    {id:'unused',kind:'branch',title:'Unchosen alternative',milestone:true,research_path_role:'no_execution_records'},
    {id:'candidate',kind:'trial',title:'Only proposed',milestone:false,recorded_work:{run_count:0,result_count:0}},
  ],edges:[
    {id:'qp',source:'q',target:'p'}, {id:'pb',source:'p',target:'b',kind:'branches'},
    {id:'b1',source:'b',target:'t1',kind:'executes'}, {id:'1r',source:'t1',target:'r1',kind:'produces'},
    {id:'b2',source:'b',target:'t2',kind:'executes'}, {id:'2r',source:'t2',target:'r2',kind:'produces'},
    {id:'pu',source:'p',target:'unused',kind:'branches'},{id:'uc',source:'unused',target:'candidate',kind:'executes'},
  ],research_path:{schema:'iterlab-research-path/v1',retained_branch_path:['b'],deferred_branch_ids:['unused']}});
}

test('mainline defers unused alternatives with counts and serialises recorded iterations without rewriting sources',()=>{
  const graph=fixture(),before=JSON.stringify(graph),view=researchMainline(graph);
  assert.deepEqual(view.projection.deferred_branch_ids,['unused']);assert.equal(view.projection.deferred_node_count,2);
  assert.deepEqual(view.nodes.map(node=>node.id),['q','p','b','t1','r1','t2','r2']);
  const sequence=view.edges.find(edge=>edge.kind==='record_order');
  assert.equal(sequence.source,'r1');assert.equal(sequence.target,'t2');assert.deepEqual(sequence.original_edge_ids,['b2']);
  assert.equal(sequence.projection_only,true);assert(sequence.basis.includes('not scientific causality'));
  assert.equal(relationLabel(sequence),'同一主线的记录顺序');
  assert(graph.edges.some(edge=>edge.id==='b2'&&edge.source==='b'&&edge.target==='t2'));
  assert.equal(JSON.stringify(graph),before);assert.equal(graph.nodes.find(node=>node.id==='t1').milestone,false);
  const layout=layoutData(view),nodes=new Map(layout.nodes.map(node=>[node.id,node]));
  const ids=['q','p','b','t1','r1','t2','r2'];
  for(let index=1;index<ids.length;index++){assert(nodes.get(ids[index]).y>nodes.get(ids[index-1]).y);assert.equal(nodes.get(ids[index]).x,nodes.get(ids[index-1]).x);}
});

test('a retained candidate, recorded outcome or failed historical branch is never silently deferred',()=>{
  for(const mutation of [
    graph=>{graph.research_path.retained_branch_path.push('unused');},
    graph=>{graph.execution_location={state:'running',focus_node_id:'candidate',active_node_ids:['candidate']};},
    graph=>{graph.nodes.find(node=>node.id==='unused').status='failed';},
    graph=>{graph.nodes.find(node=>node.id==='candidate').recorded_work={run_count:1,result_count:0};},
    graph=>{graph.nodes.push({id:'negative',kind:'result',milestone:true});graph.edges.push({id:'cn',source:'candidate',target:'negative',kind:'produces'});},
  ]){const graph=fixture();mutation(graph);const view=researchMainline(graph);assert(view.nodes.some(node=>node.id==='unused'));assert.deepEqual(view.projection.deferred_branch_ids,[]);}
});

test('record order never merges experiments under different saved plan parents',()=>{
  const graph=fixture();graph.nodes.push({id:'bp1',kind:'plan',target_type:'branch',milestone:true},{id:'bp2',kind:'plan',target_type:'branch',milestone:true});
  graph.edges=graph.edges.filter(edge=>!['b1','b2'].includes(edge.id));graph.edges.push({id:'bv1',source:'b',target:'bp1'}, {id:'vv',source:'bp1',target:'bp2',kind:'revises'}, {id:'v1t',source:'bp1',target:'t1',kind:'executes'},{id:'v2t',source:'bp2',target:'t2',kind:'executes'});
  const view=researchMainline(graph);assert(!view.edges.some(edge=>edge.kind==='record_order'));
  assert(view.edges.some(edge=>edge.id==='v1t'));assert(view.edges.some(edge=>edge.id==='v2t'));assert(view.nodes.some(node=>node.id==='bp1'));assert(view.nodes.some(node=>node.id==='bp2'));
});

test('legacy graphs without host mainline facts keep existing key-spine semantics',()=>{
  const graph=fixture();delete graph.research_path;assert.deepEqual(researchMainline(graph),researchSpine(graph));
});

test('authored plans, real research reads and reported discussions survive without a training run',()=>{
  for(const kind of ['plan','plan_review','research','investigation','discussion','finding','correction','direction','limitation','decision']){
    const graph=fixture();graph.nodes.push({id:'meaningful',kind,milestone:false,claim_status:'reported'});
    graph.edges.push({id:'meaning',source:'unused',target:'meaningful',kind:'evolves'});
    const view=researchMainline(graph);assert(view.nodes.some(node=>node.id==='unused'),kind);
    assert.deepEqual(view.projection.deferred_branch_ids,[],kind);
    if(['discussion','finding','correction','direction','limitation'].includes(kind))assert(view.nodes.some(node=>node.id==='meaningful'),kind);
  }
});

test('exact proposal versions prevent merging otherwise shared branch records',()=>{
  const graph=fixture();
  graph.nodes.find(node=>node.id==='t1').proposal_plan_refs=[{target_type:'strategy',target_id:'root',revision:2,content_digest:'a'}];
  graph.nodes.find(node=>node.id==='t2').proposal_plan_refs=[{target_type:'strategy',target_id:'root',revision:3,content_digest:'b'}];
  const view=researchMainline(graph);assert(!view.edges.some(edge=>edge.kind==='record_order'));
  assert(view.edges.some(edge=>edge.id==='b1'));assert(view.edges.some(edge=>edge.id==='b2'));
  graph.nodes.find(node=>node.id==='t1').proposal_plan_refs.push({target_type:'strategy',target_id:'root',revision:3,content_digest:'b'});
  assert(!researchMainline(graph).edges.some(edge=>edge.kind==='record_order'),'mixed proposal histories are retained as separate records');
});

test('only explicitly recorded mainline policy receives a mainline label, without approval or execution claims',()=>{
  assert.equal(nodeKindLabel({kind:'branch',branch_policy:{role:'mainline',root_plan_ref:{revision:1}}}),'方案主线');
  assert.equal(nodeKindLabel({kind:'branch',branch_policy:{role:'evidence_driven_fork'}}),'探索分支');
  assert.equal(nodeKindLabel({kind:'branch',research_path_role:'retained_path',status:'active'}),'探索分支','legacy cursor does not establish branch purpose');
});
