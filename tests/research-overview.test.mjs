import test from 'node:test';
import assert from 'node:assert/strict';
import {overviewModel,currentObject,focusGraph,recordState,objectProvenance} from '../static/research-overview.js';

test('missing SSH opens environment setup without pretending research approval is missing',()=>{
  const result=overviewModel({session:{id:'s',project_id:'p'},research_lifecycle:{state:'blocked',label:'等待开发空间 SSH 配置',allowed_actions:['configure_environment']}});
  assert.equal(result.items.length,1);assert.equal(result.items[0].type,'environment');assert.equal(result.items[0].id,'p');
});
const plan={id:'plan-a',kind:'plan',project_id:'p',title:'方案A',status:'proposed'};
const detail=(life,more={})=>({session:{id:'s',project_id:'p'},records:[plan],graph:{nodes:[plan],edges:[],execution_location:{}},research_lifecycle:life,...more});
test('missing data never invents current object or model connection',()=>{const x=overviewModel(null,{settings:{model:'configured'}},{state:'disconnected'});assert.equal(x.object,null);assert.equal(x.modelConnected,false);assert.match(x.syncLabel,/中断/);assert.deepEqual(x.items,[]);});
for(const state of ['waiting_review','waiting_plan_review'])test(state+' navigates only exact pending plan',()=>{const x=overviewModel(detail({state,subject_kind:'plan',subject_id:'plan-a'}));assert.deepEqual(x.items.map(i=>[i.type,i.id]),[['plan','plan-a']]);assert.equal(x.object.id,'plan-a');});
test('historical proposed records are not a pending-review inventory',()=>{assert.equal(overviewModel(detail({state:'idle'})).items.length,0);assert.equal(overviewModel(detail({state:'waiting_review',subject_id:'missing'})).items.length,0);});
test('waiting external and paused are not human todo or target success',()=>{for(const state of ['waiting_external','paused'])assert.equal(overviewModel(detail({state,subject_id:'watch'})).items.length,0);});
test('budget closure cannot imply target verified',()=>{const x=overviewModel(detail({state:'assessment_complete',stop_reason:'budget',target_verified:false}));assert.match(x.title,/预算/);assert.match(x.reason,/不等于达标/);assert.equal(x.items.length,0);});
test('only explicit target_verified enables verification wording',()=>{assert.match(overviewModel(detail({state:'assessment_complete',target_verified:true})).reason,/已记录目标核验/);});
test('request ID dedup keeps original unknown and approval distinct',()=>{const x=overviewModel(detail({state:'blocked'},{deliveries:[{id:'d',state:'unknown'},{id:'d',state:'requested'}],approvals:[{id:'approve'}]}),{}, {},{id:'d'});assert.deepEqual(x.items.map(i=>i.type),['request','approval']);assert.equal(x.items[0].id,'d');});
test('stop review only when explicitly waiting for human',()=>{const r={id:'review-a',kind:'review',project_id:'p'};let d=detail({state:'waiting_stop_review',subject_id:r.id},{records:[r]});assert.equal(overviewModel(d).items[0].type,'stop');d.research_lifecycle.requires_human_confirmation=false;assert.equal(overviewModel(d).items.length,0);});
test('different project record cannot become pending approval',()=>{assert.equal(overviewModel(detail({state:'waiting_review',subject_id:plan.id},{records:[{...plan,project_id:'other'}]})).items.length,0);});
test('explicit graph focus beats historical record order and lifecycle fallback',()=>{const d=detail({state:'waiting_review',subject_kind:'plan',subject_id:plan.id});d.graph.nodes.push({id:'history-last',kind:'result'});assert.equal(currentObject(d).id,plan.id);d.graph.execution_location.focus_node_id='history-last';assert.equal(currentObject(d).id,'history-last');});
test('focused projection retains original relations and exact membership without mutation',()=>{const data={nodes:[{id:'q'},plan,{id:'t',plan_id:plan.id},{id:'history'},{id:'missing'}],edges:[{id:'e',source:'q',target:plan.id,primary:true},{id:'trial',source:plan.id,target:'t',primary:true},{id:'ref',source:'history',target:plan.id,kind:'informed_by',primary:false}],plan_series:{families:[{id:'f',plan_ids:[plan.id,'missing']} ]}};const original=JSON.stringify(data),x=focusGraph(data,plan.id);assert.equal(JSON.stringify(data),original);assert(x.nodes.some(n=>n.id==='t'));assert(!x.nodes.some(n=>n.id==='missing'));assert.deepEqual(x.edges,data.edges);assert.deepEqual(x.plan_series.families[0].plan_ids,[plan.id]);assert.equal(focusGraph(data,'unknown'),data);});
test('stored running status is not proof of live execution',()=>{assert.match(recordState({status:'running'}),/见研究总览/);});

test('historical narrative status and human intervention survive the compact summary',()=>{
  const shown={status:'not_verified',story_context:{retrospective:true,actor:'evaluator'}};
  assert.match(objectProvenance(shown,{status:'completed'}),/尚未验证.*历史梳理.*验收者复核/);
  shown.story_context={actor:'agent',trigger_origin:'background'};
  assert.match(objectProvenance(shown),/系统自动续接/);
  assert(!objectProvenance(shown).includes('人工'));
});
