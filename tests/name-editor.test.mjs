import test from 'node:test';
import assert from 'node:assert/strict';
import {installNameEditor} from '../static/name-editor.js';

test('name editor captures the target, prevents duplicate saves and retains failed input',async()=>{
  const prior=globalThis.document,parts=new Map();
  const part=key=>{if(!parts.has(key))parts.set(key,{value:'',textContent:'',focus(){},select(){}});return parts.get(key);};
  const dialog={querySelector:part,showModal(){this.open=true;},close(){this.open=false;}};
  let markup='';Object.defineProperty(dialog,'innerHTML',{set:v=>{markup=v;}});
  globalThis.document={createElement:()=>dialog,body:{append(){}}};
  try{
    let resolve;const calls=[],saved=[];
    const editor=installNameEditor({id:'edit-chat',heading:'重命名会话',label:'会话名称',onSave:(target,title)=>{calls.push({target,title});return new Promise(r=>{resolve=r;});},onSaved:target=>saved.push(target.id)});
    const event={preventDefault(){}};
    editor.open({id:'original',title:'旧名称',workspace:'D:/private'});
    part('input').value='新名称';const pending=part('form').onsubmit(event);
    editor.open({id:'different',title:'其他会话'});await part('form').onsubmit(event);
    assert.equal(calls.length,1);assert.deepEqual(calls[0],{target:{id:'original',title:'旧名称'},title:'新名称'});
    assert.equal(part('[type=submit]').disabled,true);assert(!markup.includes('workspace'));assert(!markup.includes('D:/private'));
    resolve();await pending;assert.deepEqual(saved,['original']);assert.equal(dialog.open,false);
    editor.open({id:'original',title:'新名称'});part('[data-cancel]').onclick();assert.equal(calls.length,1);
    editor.open({id:'original',title:'新名称'});part('input').value='  ';await part('form').onsubmit(event);assert.equal(part('[role=alert]').hidden,false);assert.equal(calls.length,1);
  }finally{globalThis.document=prior;}
});
