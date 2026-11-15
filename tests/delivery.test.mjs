import assert from 'node:assert/strict';
import test from 'node:test';
import {createDeliverySender,sameComposition} from '../static/delivery.js';

function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
function fixture(api){
  const values=new Map(),storage={getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};
  let view={sid:'a',selection:1,revision:1,prompt:'hello',attachments:[]},counter=0;
  const commits=[],calls=[];
  const sender=createDeliverySender({storage,uuid:()=>`original-${++counter}`,snapshot:()=>({...view,attachments:view.attachments.map(a=>({...a}))}),
    api:async(path,data)=>{calls.push({path,data});return api(path,data);},
    commit:async captured=>{if(!sameComposition(captured,view))return false;commits.push(captured);view={...view,prompt:'',attachments:[],revision:view.revision+1};return true;}});
  return {sender,storage,values,calls,commits,get view(){return view;},set view(v){view=v;}};
}
const row=(data,state='queued',sid='a')=>({id:data.id,session_id:sid,text:data.text,mode:data.mode,state});

test('synchronous send guard reserves one original ID before two Enter calls',async()=>{
  const response=deferred(),f=fixture(()=>response.promise);
  const first=f.sender.send(),second=await f.sender.send();
  assert.deepEqual(second,{duplicate:true});assert.equal(f.calls.length,1);
  assert.match(f.storage.getItem('iterlab-codex:send:a'),/original-1/);
  response.resolve(row(f.calls[0].data));assert.equal((await first).cleared,true);
  assert.equal(f.commits.length,1);assert.equal(f.sender.pending('a'),null);
});

test('old-session completion cannot clear equal text or attachments in a new selection',async()=>{
  const response=deferred(),f=fixture(()=>response.promise),first=f.sender.send();
  f.view={...f.view,sid:'b',selection:2};response.resolve(row(f.calls[0].data));
  assert.equal((await first).cleared,false);assert.equal(f.view.prompt,'hello');assert.equal(f.commits.length,0);
  assert.equal(f.sender.pending('a').native_state,'queued');
});

test('edit and change back, A-B-A selection, or attachment changes do not clear newer draft',async()=>{
  for(const mutate of [v=>({...v,revision:3}),v=>({...v,selection:3}),v=>({...v,attachments:[{path:'new.txt',name:'new'}]})]){
    const response=deferred(),f=fixture(()=>response.promise),first=f.sender.send();
    f.view=mutate(f.view);response.resolve(row(f.calls[0].data));
    assert.equal((await first).cleared,false);assert.equal(f.view.prompt,'hello');
  }
});

test('HTTP uncertainty persists identity across reload and uses read-only lookup, never resend',async()=>{
  const f=fixture(()=>{throw Error('response lost');});await assert.rejects(f.sender.send(),/原请求ID和草稿已保留/);
  assert.equal(f.sender.pending('a').id,'original-1');assert.equal(f.view.prompt,'hello');
  await assert.rejects(f.sender.send(),/只读核对/);assert.equal(f.calls.length,1);
  const calls=[],sender=createDeliverySender({api:async path=>{calls.push(path);return row(f.calls[0].data,'unknown');},
    storage:f.storage,snapshot:()=>f.view,commit:async()=>{throw Error('reload must not guess draft ownership');}});
  await sender.inspect('a');assert.deepEqual(calls,['/api/sessions/a/deliveries/original-1']);
  assert.equal(sender.pending('a').id,'original-1');assert.equal(sender.pending('a').native_state,'unknown');
  assert.throws(()=>sender.release('a',true),/确切登记状态/);
});

test('missing read-only lookup is not evidence that original request was rejected',async()=>{
  const f=fixture(()=>{throw Error('no row yet');});await assert.rejects(f.sender.send());
  await assert.rejects(f.sender.inspect('a'),/no row yet/);
  assert.equal(f.sender.pending('a').id,'original-1');assert.equal(f.calls.length,2);
  assert.equal(f.calls[1].data,undefined);
});

test('only exact explicit not-registered receipt clears a rejected HTTP attempt',async()=>{
  const f=fixture((path,data)=>{const error=Error('preflight denied');error.delivery={id:data.id,session_id:'a',state:'not_registered'};throw error;});
  await assert.rejects(f.sender.send(),/preflight denied/);assert.equal(f.sender.pending('a'),null);assert.equal(f.view.prompt,'hello');
  const conflict=fixture((path,data)=>{const error=Error('conflict');error.delivery={id:'another',session_id:'a',state:'not_registered'};throw error;});
  await assert.rejects(conflict.sender.send(),/尚未确认/);assert.equal(conflict.sender.pending('a').id,'original-1');
});

test('exact registered response can be closed explicitly without changing or resending draft',async()=>{
  const f=fixture((path,data)=>row(data));f.view={...f.view,attachments:[{path:'a.txt',name:'a'}]};
  const response=deferred();const captured=f.view;
  const g=fixture(()=>response.promise),first=g.sender.send();g.view={...captured,revision:10};response.resolve(row(g.calls[0].data));await first;
  assert.throws(()=>g.sender.release('a',false),/确切登记状态/);g.sender.release('a',true);
  assert.deepEqual(g.view,{...captured,revision:10});assert.equal(g.calls.length,1);assert.equal(g.sender.pending('a'),null);
});

test('mismatched native record cannot clear draft or resolve original identity',async()=>{
  const f=fixture((path,data)=>({...row(data),text:'another text'}));
  await assert.rejects(f.sender.send(),/尚未确认/);assert.equal(f.view.prompt,'hello');assert.equal(f.sender.pending('a').id,'original-1');
});

test('storage failure before write prevents all native submission',async()=>{
  let calls=0;
  const sender=createDeliverySender({api:async()=>{calls++;},snapshot:()=>({sid:'a',prompt:'hello',attachments:[]}),
    commit:async()=>false,storage:{getItem:()=>null,setItem:()=>{throw Error('quota');},removeItem:()=>{}},uuid:()=> 'id'});
  await assert.rejects(sender.send(),/本次尚未提交/);assert.equal(calls,0);assert.equal(sender.busy(),false);
});

test('corrupted saved attempt remains fail-closed and cannot generate a fresh identity',async()=>{
  let calls=0;
  const sender=createDeliverySender({api:async()=>{calls++;},snapshot:()=>({sid:'a',prompt:'hello',attachments:[]}),commit:async()=>false,
    storage:{getItem:()=>'{broken',setItem:()=>{},removeItem:()=>{}},uuid:()=> 'fresh'});
  await assert.rejects(sender.send(),/不要重发/);assert.equal(calls,0);
});
