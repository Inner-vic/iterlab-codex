import test from 'node:test';
import assert from 'node:assert/strict';
import {studySections,primaryEdge,visibleEdge,relationLabel} from '../model.mjs';

test('study details distinguish numeric support, confirmation limits and budget stop',()=>{
  const sections=studySections({scientific_assessment:{claim:'Improvement',judged_claim:'Frozen conditions',label:'证据不足',
    basis:'comparison_rules',level:'confirmatory',reason:'Still missing confirmation',limitations:'No independence claim',
    confirmation:{ready:false,issues:['Missing new evidence']},stop_reason:'budget',meaning:'Frozen numeric conditions only'},
    trust:{meaning:'Local trace, no independent signing'}});
  const fields=sections[0].fields;
  assert.deepEqual(fields.find(f=>f[0]==='证据状态'),['证据状态','证据不足']);
  assert(fields.some(f=>f[0]==='执行停止原因'&&f[1]==='预算边界'));
  assert(fields.some(f=>f[0]==='确认检查'&&f[1]==='Missing new evidence'));
  assert(sections.some(s=>s.title==='审议与留痕边界'));
});

test('baseline updates are visible references, never scientific parents',()=>{
  const edge={kind:'baseline_update',primary:true,story_visible:true};
  assert.equal(primaryEdge(edge),false);assert(visibleEdge(edge));assert.equal(relationLabel(edge),'更新基线参考');
  const sections=studySections({baseline_selection:{scope:'Current question',candidate:'A',reason:'Actual evidence',comparison_impact:'Recheck conditions',previous_selection_id:'old'}});
  assert.equal(sections[0].title,'基线选择与更新');assert(sections[0].fields.some(f=>f[1]==='old'));
});
