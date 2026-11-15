import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeData,researchMainline,layoutData,primaryEdge,visibleEdge,selectionFocus,storyLabel,geometrySignature} from '../model.mjs';

test('a historical investigation keeps its root, explicit revisions and unresolved questions without inventing plans',()=>{
  const context={actor:'agent',retrospective:true,trigger_delivery_id:'actual-input',trigger_label:'人工要求核查'};
  const graph=normalizeData({project_id:'other-project',research_story:{schema:'iterlab-research-story/v1'},
    plan_series:{schema:'iterlab-plan-series/v1',families:[]},nodes:[
      {id:'root',kind:'project',milestone:true,story_context:{actor:'human',retrospective:true}},
      {id:'r1',kind:'investigation',milestone:true,story_context:context},
      {id:'r2',kind:'correction',milestone:true,story_context:context},
      {id:'q',kind:'question',milestone:true,status:'not_verified',story_context:{actor:'evaluator',retrospective:true}},
    ],edges:[
      {id:'a',source:'root',target:'r1',kind:'research_step',primary:true,story_visible:true},
      {id:'b',source:'r1',target:'r2',kind:'research_step',primary:true,story_visible:true},
      {id:'c',source:'r1',target:'r2',kind:'claim_revision',primary:false,story_visible:true},
      {id:'d',source:'r2',target:'q',kind:'raises_question',primary:true,story_visible:true},
      {id:'e',source:'r1',target:'r2',kind:'recorded_after',primary:false},
    ]});
  const original=JSON.stringify(graph),view=researchMainline(graph),layout=layoutData(view);
  assert.deepEqual(view.nodes.map(n=>n.id),['root','r1','r2','q']);
  assert(!primaryEdge(graph.edges[2]));assert(visibleEdge(graph.edges[2]));assert(!visibleEdge(graph.edges[4]));
  const positions=new Map(layout.nodes.map(n=>[n.id,n]));
  assert(positions.get('root').y<positions.get('r1').y);
  assert(positions.get('r1').y<positions.get('r2').y);
  assert(positions.get('r2').y<positions.get('q').y);
  assert(selectionFocus('r2',graph.edges).edges.has('c'));
  assert.match(storyLabel(graph.nodes[1]),/历史梳理.*Agent 分析.*人工要求核查/);
  assert.match(storyLabel(graph.nodes[3]),/验收者复核/);
  assert.equal(JSON.stringify(graph),original);
  assert.notEqual(geometrySignature(graph),geometrySignature({...graph,nodes:graph.nodes.map(({story_context,...n})=>n)}));
});
