import test from 'node:test';
import assert from 'node:assert/strict';
import {installCompanyUI} from '../static/company.js';
import {runtimeView} from '../static/runtime-ui.js';
import {spaceResourceText,spaceObservationText,spaceSSHState} from '../static/company-space.js';

// The renderers' DOM surface only: all platform reads and writes use synthetic fixtures.
const decode=text=>text.replace(/&(amp|lt|gt|quot|#39);/g,(_,key)=>({amp:'&',lt:'<',gt:'>',quot:'"','#39':"'"}[key]));
class TextNode{constructor(text){this.textContent=String(text);}}
class Element{
  constructor(tag){this.tagName=tag;this.children=[];this.attributes={};this.listeners={};this.disabled=false;this.hidden=false;this.open=false;this.checked=false;this.dataset={};}
  setAttribute(name,value=''){this.attributes[name]=String(value);if(['disabled','hidden','checked'].includes(name))this[name]=true;if(name.startsWith('data-'))this.dataset[name.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=String(value);}
  getAttribute(name){return this.attributes[name]??null;}
  get id(){return this.attributes.id||'';}set id(value){this.attributes.id=value;}
  get className(){return this.attributes.class||'';}set className(value){this.attributes.class=value;}
  get isConnected(){for(let node=this;node;node=node.parentElement)if(node.tagName==='body')return true;return false;}
  append(...nodes){for(let node of nodes){if(typeof node==='string')node=new TextNode(node);if(node.parentElement)node.parentElement.children.splice(node.parentElement.children.indexOf(node),1);node.parentElement=this;this.children.push(node);}}
  replaceChildren(...nodes){for(const node of this.children)node.parentElement=null;this.children=[];this.append(...nodes);}
  insertBefore(node,before){if(node.parentElement)node.parentElement.children.splice(node.parentElement.children.indexOf(node),1);node.parentElement=this;this.children.splice(this.children.indexOf(before),0,node);}
  before(node){this.parentElement.insertBefore(node,this);}
  after(node){const next=this.parentElement.children[this.parentElement.children.indexOf(this)+1];if(next)this.parentElement.insertBefore(node,next);else this.parentElement.append(node);}
  get textContent(){return this.children.map(node=>node.textContent).join('');}set textContent(value){this.replaceChildren(new TextNode(value??''));}
  get value(){return String(this._value??this.attributes.value??(this.tagName==='select'?this.querySelector('option')?.value:'')??'');}set value(value){this._value=String(value??'');}
  get selectedOptions(){return this.querySelectorAll('option').filter(option=>option.value===this.value);}
  get innerHTML(){return this.textContent;}
  set innerHTML(html){
    this.replaceChildren();const stack=[this];
    for(const token of html.match(/<\/?[\w-]+[^>]*>|[^<]+/g)||[]){
      if(token.startsWith('</')){stack.pop();continue;}
      if(!token.startsWith('<')){stack.at(-1).append(new TextNode(decode(token)));continue;}
      const [,tag,attrs]=/^<([\w-]+)([^>]*)>$/.exec(token),node=new Element(tag);
      for(const match of attrs.matchAll(/([\w-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s'"=<>]+)))?/g))node.setAttribute(match[1],decode(match[2]??match[3]??match[4]??''));
      stack.at(-1).append(node);if(!['input','br','hr'].includes(tag))stack.push(node);
    }
  }
  matches(selector){if(selector.startsWith('#'))return this.id===selector.slice(1);if(selector.startsWith('.'))return this.className.split(/\s+/).includes(selector.slice(1));const match=/^\[([\w-]+)(?:=["']?([^\]"']+)["']?)?\]$/.exec(selector);return match?(match[2]===undefined?this.getAttribute(match[1])!==null:this.getAttribute(match[1])===match[2]):this.tagName===selector;}
  querySelectorAll(selector){const found=[],selectors=selector.split(',');const visit=node=>{for(const child of node.children||[]){if(!(child instanceof Element))continue;if(selectors.some(part=>child.matches(part.trim())))found.push(child);visit(child);}};visit(this);return found;}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  addEventListener(type,handler){(this.listeners[type]??=[]).push(handler);}
  async fire(type){await Promise.all([this['on'+type],...(this.listeners[type]||[])].filter(Boolean).map(handler=>handler({target:this,preventDefault(){}})));}
  scrollIntoView(){this.scrolled=true;}
  focus(){globalThis.document.activeElement=this;}
  setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end;}
}
const space=()=>({kind:'lpai',uid:'space-a',name:'research-space',scope:'workspace-a',observation:{phase:'Running',url:'https://lpai.lixiang.com/devspace'}});
function context(execution='lpai_devspace'){
  return {connections:{config:{lpai_enabled:true,gitlab_enabled:false},lpai_url:'https://lpai.lixiang.com'},bindings:[space()],
    execution_request:execution?{execution,state:'selected'}:null,devspace_ssh:{port:23456,state:'verified',verified_at:'2026-10-01T00:00:00Z'},
    devspace_stop:{available:true,blockers:[]},training:{submissions:[],notices:[],diagnostics:[]}};
}
const preview=()=>({task_name:'synthetic-task',component:{name:'fixed-component',gpu_count:1},task_information:{expected_duration:5},reason:'fixture'});
function harness(t,value=context()){
  const original=globalThis.document,body=new Element('body');
  body.innerHTML='<details id="run-config"><summary>运行配置</summary><section id="tab-execution"></section></details><div id="project-run-results"></div><section id="session-runs"></section><form id="company-form"></form><button id="company-check-lpai"></button><button id="company-check-gitlab"></button>';
  const document={body,createElement:tag=>new Element(tag),createTextNode:text=>new TextNode(text),querySelector:selector=>body.querySelector(selector)};
  globalThis.document=document;t.after(()=>{globalThis.document=original;});
  const calls=[],toasts=[];let sid='session-a',saveResult=Promise.resolve({}),refreshes=0;
  const api=async(path,payload)=>{
    calls.push({path,payload});
    if(path==='/api/company/context')return structuredClone(value);
    if(path==='/api/company/session-runs')return {active:[],history:[]};
    if(path==='/api/company/resources')return {live:false,queues:[],message:'Synthetic account has no queues'};
    if(path==='/api/company/execution-location'||path==='/api/company/devspace-ssh')return saveResult;
    throw Error('Unexpected API request: '+path);
  };
  const ui=installCompanyUI({api,current:()=>sid,toast:message=>toasts.push(message),refresh:async()=>{refreshes++;},openRuns(){},onRunsChange(){}});
  return {ui,value,calls,toasts,document,query:selector=>document.querySelector(selector),session:next=>{sid=next;},save:promise=>{saveResult=promise;},refreshes:()=>refreshes};
}

test('an unset or invalid runtime stays unset; explicit devspace cannot fall back to a previous library grant',()=>{
  const value=context(null);assert.equal(runtimeView(value).execution,'');assert.equal(runtimeView(value).showResources,false);
  value.approved_execution='lpai';assert.equal(runtimeView(value).execution,'lpai');
  value.execution_request={execution:'lpai_devspace',state:'needs_ssh'};
  assert.equal(runtimeView(value).execution,'lpai_devspace');assert.equal(runtimeView(value).showResources,false);
  value.execution_request.execution='obsolete';assert.equal(runtimeView(value).execution,'');
});

test('initial runtime rendering performs no cloud writes or resource reads; component resources load once on explicit expansion',async t=>{
  const h=harness(t);
  for(const execution of ['lpai_devspace','local',null,'lpai']){
    h.value.execution_request=execution?{execution,state:'selected'}:null;const offset=h.calls.length;
    await h.ui.render();
    assert.deepEqual(h.toasts,[]);
    const calls=h.calls.slice(offset).map(call=>call.path);
    assert.deepEqual(calls,['/api/company/context','/api/company/session-runs','/api/company/session-runs']);
    assert.equal(!!h.query('.component-runtime'),execution==='lpai');
    assert.equal(!!h.query('.component-resources'),false);
    assert.equal(!!h.query('.component-resource-directory'),execution==='lpai');
    if(execution==='lpai'){assert.match(h.query('.component-runtime').textContent,/按方案自动创建组件.*高级配置可复用已有组件/);assert.equal(h.query('.component-runtime').querySelector('ol'),null);}
    assert.equal(h.query('#session-run-list')!==null,true);
  }
  const directory=h.query('.component-resource-directory');assert.equal(directory.open,false);
  directory.open=true;await directory.fire('toggle');
  assert.equal(h.calls.filter(call=>call.path==='/api/company/resources').length,1);assert.equal(!!h.query('.component-resources'),true);
  directory.open=false;await directory.fire('toggle');directory.open=true;await directory.fire('toggle');await h.ui.render();
  assert.equal(h.calls.filter(call=>call.path==='/api/company/resources').length,1);assert.equal(h.query('.component-resource-directory'),directory);
  assert.deepEqual(h.toasts,[]);
});

test('space defaults show concise status and pending blocker while SSH and management stay folded',async t=>{
  const value=context();value.devspace_ssh=null;value.execution_request.state='needs_ssh';
  value.devspace_operation={action:'lpai_stop',state:'pending_approval',message:'Long original approval evidence retained only in details'};
  value.devspace_observation={state:'waiting',last_checked_at:'2026-10-01T00:00:00Z'};
  const h=harness(t,value);await h.ui.render();
  assert.equal(h.query('.execution-choice').querySelectorAll('p').length,1);
  assert.equal(h.query('.devspace-status-grid'),null);assert.equal(h.query('.devspace-binding').open,false);assert.equal(h.query('.devspace-ssh').open,false);
  assert.equal(h.query('[data-space-summary]').textContent,'停止请求待审批。');assert.equal(h.query('[data-space-summary]').hidden,false);
  assert.equal(h.query('[data-space-pill]').textContent,'运行中');assert.equal(h.query('[data-ssh-summary]').textContent,'待验证');
  assert.match(h.query('.devspace-binding').textContent,/Long original approval evidence/);
  assert.equal(h.query('[data-stop]').disabled,true);assert.equal(h.query('[data-start]').disabled,true);
  h.value.devspace_operation.state='unknown';await h.ui.render();
  assert.match(h.query('[data-space-summary]').textContent,/结果未知.*正在核对/);assert.equal(h.query('[data-space-summary]').hidden,false);
});

test('a verified SSH form starts collapsed and a background render preserves edits and key paths per bound space',async t=>{
  const h=harness(t);await h.ui.render();
  const ssh=h.query('.devspace-ssh');assert.equal(ssh.open,false);ssh.open=true;
  const command=h.query('[data-ssh-command]'),identity=h.query('[data-ssh-identity]');
  command.value='ssh -p 34567 root@ssh-d.lpai.lixiang.com';await command.fire('input');
  identity.value='D:/keys/research-key';await identity.fire('input');
  h.query('.ssh-key-options').open=true;await h.query('.ssh-key-options').fire('toggle');
  identity.focus();identity.setSelectionRange(3,9);
  h.value.devspace_observation={state:'waiting'};await h.ui.render();
  assert.equal(h.query('.devspace-ssh').open,true);assert.equal(h.query('[data-ssh-command]').value,command.value);assert.equal(h.query('[data-ssh-identity]').value,identity.value);
  assert.equal(h.query('.ssh-key-options').open,true);assert.equal(h.document.activeElement,h.query('[data-ssh-identity]'));
  assert.deepEqual([h.document.activeElement.selectionStart,h.document.activeElement.selectionEnd],[3,9]);
  assert.match(h.query('[data-space-observation]').textContent,/后台只读核对/);
  h.value.bindings[0].scope='different-workspace';await h.ui.render();
  assert.equal(h.query('[data-ssh-identity]').value,'');assert.equal(h.query('.devspace-ssh').open,false);
  assert.equal(h.calls.some(call=>call.path==='/api/company/devspace-ssh'),false);
});

test('prepared component previews in another runtime are folded and cannot submit, while unknown submissions remain visible',async t=>{
  const value=context();value.training.submissions=[{id:'prepared',state:'prepared',preview:preview()},{id:'uncertain',state:'unknown',preview:preview()}];
  const h=harness(t,value);await h.ui.render();
  assert.equal(h.query('.component-preview-history').open,false);
  assert.match(h.query('.component-preview-history').textContent,/保留的组件任务预览/);
  assert.equal(h.query('.component-preview-history').querySelectorAll('button').length,0);
  assert.match(h.query('.component-submissions').textContent,/结果未知.*核对已发现的原任务/s);
  assert.equal(h.query('#project-run-results').querySelectorAll('button').some(button=>button.textContent==='审核并提交这次任务'),false);
  h.value.execution_request.execution='lpai';await h.ui.render();
  assert.equal(h.query('.component-submissions').querySelectorAll('button').filter(button=>button.textContent==='审核并提交这次任务').length,1);
  assert.equal(h.calls.some(call=>call.path==='/api/company/action'),false);
});

test('confirming a changed binding saves only the requested location once and ignores stale session controls',async t=>{
  const value=context();value.execution_request.state='needs_binding';const h=harness(t,value);await h.ui.render();
  const confirm=h.query('[data-confirm-location]');assert.match(h.query('.runtime-reconfirm').textContent,/research-space/);
  let resolve;h.save(new Promise(done=>{resolve=done;}));
  const first=confirm.fire('click');await confirm.fire('click');
  assert.equal(h.calls.filter(call=>call.path==='/api/company/execution-location').length,1);
  assert.equal(h.calls.at(-1).payload.execution,'lpai_devspace');assert.equal(confirm.disabled,true);
  resolve({});await first;assert.equal(h.refreshes(),1);
  h.session('other-session');await confirm.fire('click');
  assert.equal(h.calls.filter(call=>call.path==='/api/company/execution-location').length,1);
  assert.match(h.toasts[0],/未启动、停止或提交任务/);
});

test('failed SSH evidence and background read failures do not claim verified or completed operations',()=>{
  assert.equal(spaceSSHState(space(),{state:'failed',verified_at:'old',message:'SSH read failed'}).verified,false);
  assert.match(spaceSSHState(space(),{state:'failed',verified_at:'old',message:'SSH read failed'}).message,/SSH read failed/);
  const message=spaceObservationText({state:'read_failed',operation_id:'private-operation-id',last_checked_at:'2026-10-01T00:00:00Z'});
  assert.match(message,/待核对.*不会重发/);assert.doesNotMatch(message,/private-operation-id/);
  assert.match(spaceObservationText({state:'legacy_manual_only'}),/历史请求需手动读回/);
});

test('official space resource formatting preserves zero GPU and never invents unknown resource quantities',()=>{
  const value=space();value.observation.resources={cpu:1,gpu:0,memory:'4Gi'};
  assert.equal(spaceResourceText(value),'官方资源：1 CPU · 0 GPU · 内存 4Gi；未配置 GPU，不能作为 GPU 训练环境。');
  value.observation.resources={cpu:'1',gpu:'0'};
  assert.equal(spaceResourceText(value),'官方资源：1 CPU · 0 GPU；未配置 GPU，不能作为 GPU 训练环境。');
  value.observation.resources={gpu:2};assert.equal(spaceResourceText(value),'官方资源：2 GPU');
  value.observation.resources={cpu:1,gpu:null,memory:undefined};assert.equal(spaceResourceText(value),'官方资源：1 CPU');
  for(const resources of [{},{cpu:null,gpu:undefined,memory:''},{cpu:-1,gpu:'unknown',memory:false}]){
    value.observation.resources=resources;assert.equal(spaceResourceText(value),'');
  }
  assert.doesNotMatch(spaceResourceText({observation:{resources:{gpu:1}}}),/CUDA|验证通过/);
});

test('automatic SSH setup reads the official endpoint without submitting pasted shell text or repeating a pending request',async t=>{
  const value=context();value.devspace_ssh=null;const h=harness(t,value);await h.ui.render();
  assert.equal(h.query('.ssh-manual').open,false);
  h.query('[data-ssh-command]').value='arbitrary pasted text; must not execute';
  let complete;h.save(new Promise(resolve=>{complete=resolve;}));
  const button=h.query('[data-ssh-auto]'),first=button.fire('click');await button.fire('click');
  const requests=h.calls.filter(call=>call.path==='/api/company/devspace-ssh');
  assert.deepEqual(requests,[{path:'/api/company/devspace-ssh',payload:{session_id:'session-a',space_uid:'space-a',auto:true}}]);
  assert.equal(h.query('[data-ssh-save]').disabled,true);
  complete({state:'saved',message:'空间等待资源'});await first;
  assert.equal(button.disabled,false);
  h.session('another-session');await button.fire('click');
  assert.equal(h.calls.filter(call=>call.path==='/api/company/devspace-ssh').length,1);
});
