import test from 'node:test';
import assert from 'node:assert/strict';
import {resourceLabel,queueCards,accountQueues,sameResourceAccount} from '../static/component-resources.js';
test('resource label distinguishes machine type from card quantity and CPU resources',()=>{
  assert.equal(resourceLabel({gpu_type:'A800',gpu_count:2,cpu:16,memory_gib:128}),'A800 · 2 GPU · 16 CPU / 128 GiB');
  assert.equal(resourceLabel({gpu_count:0,cpu:1,memory_gib:4}),'CPU · 1 CPU / 4 GiB');
  assert.equal(resourceLabel({gpu_type:'L20X-SHARE',gpu_count:null}),'L20X-SHARE · 共享规格（需单独核对）');
});
test('queue cards render live quantities without snapshot wording or global package selectors',()=>{
  const markup=queueCards([{key:'one',name:'专属训练队列',kind:'private',cluster_label:'训练集群',resources:[{label:'A10',available:4,unit:'card'},{label:'A100 SHARE',available:0,unit:'instance'}]}],'one');
  assert.equal((markup.match(/type="radio"/g)||[]).length,1);assert.match(markup,/专属训练队列/);assert.match(markup,/checked/);
  assert.match(markup,/可用 4 卡/);assert.match(markup,/可用 0 台/);assert.doesNotMatch(markup,/截图|80 种|计算规格<select/);
});
test('resource view rejects screenshots and unverified lists, without fixing a resource count',()=>{
  const queues=Array.from({length:5},(_,i)=>({key:String(i)}));
  assert.deepEqual(accountQueues({queues,source:'user_platform_screenshot',live:false}),[]);
  assert.deepEqual(accountQueues({queues,source:'official_account_resources',live:true}),[]);
  assert.equal(accountQueues({queues,source:'official_account_resources',live:true,account_scope:{subject:'a'}}).length,5);
  assert.equal(accountQueues({queues:queues.slice(0,2),source:'official_account_resources',live:true,account_scope:{subject:'b'}}).length,2);
});
test('same queue key does not reuse another account, workspace or connection revision',()=>{
  const a={subject:'a',namespace:'workspace',connection_revision:1};
  assert.equal(sameResourceAccount(a,{...a}),true);
  assert.equal(sameResourceAccount(a,{...a,subject:'b'}),false);
  assert.equal(sameResourceAccount(a,{...a,namespace:'other'}),false);
  assert.equal(sameResourceAccount(a,{...a,connection_revision:2}),false);
  assert.equal(sameResourceAccount(undefined,a),false);
});
