import test from 'node:test';
import assert from 'node:assert/strict';
import {projectMonogram,projectMarkers,resolveProjectSession} from '../static/project-switcher.js';

test('project labels retain readable Chinese, English and unnamed identities',()=>{
  assert.equal(projectMonogram('振动诊断'),'振动');assert.equal(projectMonogram('River Flow'),'RF');
  assert.equal(projectMonogram('iterlab'),'IT');assert.equal(projectMonogram('  '),'项目');
  assert.equal(projectMonogram('📁 电池寿命'),'电池');assert.equal(projectMonogram('ＡＩ Labs'),'AL');
});
test('same-name and same-monogram projects are visibly distinct and reorder-stable',()=>{
  const values=[{id:'project-8b',title:'振动诊断'},{id:'project-9b',title:'振动诊断'},{id:'project-7b',title:'振动特征'},{id:'project-6a',title:'电池寿命'}];
  const first=projectMarkers(values),reordered=projectMarkers([...values].reverse());
  assert.equal(new Set(first.map(p=>p.monogram+p.suffix)).size,4);
  for(const marker of first)assert.deepEqual(marker,reordered.find(p=>p.id===marker.id));
  assert.equal(first[3].suffix,'');assert(first[0].suffix);
});
test('shared identifier tails are extended without choosing by list position',()=>{
  const values=[{id:'abc000001',title:'重名'},{id:'abc100001',title:'重名'}];
  const markers=projectMarkers(values);assert.notEqual(markers[0].suffix,markers[1].suffix);
  assert.deepEqual(projectMarkers(values.map(p=>({...p,pinned:true}))),markers);
});
test('captions are bounded while full original names remain available',()=>{
  const title='振动诊断 · 频域特征与跨工况泛化验证',marker=projectMarkers([{id:'a',title}])[0];
  assert.equal(marker.title,title);assert([...marker.caption].length<=5);
});
test('session lookup requires the exact existing project and session identity',()=>{
  const state={projects:[{id:'a'},{id:'b'}],sessions:[{id:'x',project_id:'a',title:'同名会话'},{id:'y',project_id:'b',title:'同名会话'}]};
  assert.equal(resolveProjectSession(state,'a','x'),state.sessions[0]);
  assert.equal(resolveProjectSession(state,'a','y'),null);assert.equal(resolveProjectSession(state,'a','missing'),null);
  assert.equal(resolveProjectSession({...state,projects:[{id:'b'}]},'a','x'),null);
  assert.equal(resolveProjectSession({...state,sessions:[]},'a','x'),null);
});
test('empty projects do not yield an implicit session',()=>{
  assert.equal(resolveProjectSession({projects:[{id:'empty'}],sessions:[]},'empty',null),null);
});
