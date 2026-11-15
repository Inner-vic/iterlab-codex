import test from 'node:test';
import assert from 'node:assert/strict';
import {researchDisplay,researchMainline,primaryEdge} from '../model.mjs';

const fixture=()=>({plan_series:{schema:'iterlab-plan-series/v1',families:[]},nodes:[
  {id:'plan',kind:'plan',plan_id:'plan',milestone:true},
  {id:'trial',kind:'trial',plan_id:'plan',milestone:true},
  {id:'retry',kind:'trial',plan_id:'plan',milestone:true,execution_detail_only:true,execution_trial_id:'trial'},
  {id:'proof',kind:'research',plan_id:'plan',milestone:true,execution_detail_only:true,execution_trial_id:'trial'},
  {id:'negative',kind:'result',status:'failed',plan_id:'plan',milestone:true},
],edges:[
  {id:'p',source:'plan',target:'trial',primary:true},
  {id:'r',source:'trial',target:'retry',kind:'retries',primary:true},
  {id:'d',source:'retry',target:'proof',primary:true},
  {id:'n',source:'retry',target:'negative',primary:true},
],execution_location:{state:'waiting',focus_node_id:'proof',active_node_ids:[],retained_node_ids:['proof']}});

test('execution details disappear from both graph modes but negative research results stay',()=>{
  const data=fixture(),before=JSON.stringify(data),view=researchDisplay(data);
  assert.deepEqual(view.nodes.map(n=>n.id),['plan','trial','negative']);
  assert.equal(view.execution_location.focus_node_id,'trial');
  assert(view.edges.some(e=>e.target==='negative'&&e.source==='trial'&&!primaryEdge(e)));
  assert(view.edges.every(e=>e.source!==e.target));
  const expanded=researchMainline(data,{expandedPlans:new Set(['plan'])});
  assert(expanded.nodes.some(n=>n.id==='negative'));
  assert(!expanded.nodes.some(n=>['retry','proof'].includes(n.id)));
  assert.equal(JSON.stringify(data),before);
});

test('no task-owner guess and no state or title heuristic',()=>{
  const data=fixture();data.nodes.push({id:'unbound',kind:'research',execution_detail_only:true,execution_trial_id:null},
    {id:'real-failure',kind:'research',title:'运行失败与模型发散',status:'failed',milestone:true});
  data.edges.push({id:'u',source:'unbound',target:'real-failure',primary:true});
  const view=researchDisplay(data);
  assert(view.nodes.some(n=>n.id==='real-failure'));
  assert(!view.nodes.some(n=>n.id==='unbound'));
  assert(!view.edges.some(e=>e.id==='u'));
});
