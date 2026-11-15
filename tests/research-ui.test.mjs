import assert from 'node:assert/strict';
import test from 'node:test';
import {installResearchUI} from '../static/research.js';

// Only the DOM surface used by installResearchUI: no browser, dependencies or network.
const decode=text=>text.replace(/&(amp|lt|gt|quot|#39);/g,(_,key)=>({amp:'&',lt:'<',gt:'>',quot:'"','#39':"'"}[key]));
const encode=text=>String(text).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
class TextNode{
  constructor(text){this.textContent=text;this.parentElement=null;}
  get innerHTML(){return encode(this.textContent);}
}
class Element{
  constructor(tag){this.tagName=tag.toLowerCase();this.children=[];this.attributes={};this.listeners={};this.disabled=false;this.checked=false;this.hidden=false;this.open=false;this._value=undefined;}
  setAttribute(name,value=''){this.attributes[name]=String(value);if(['disabled','checked','hidden'].includes(name))this[name]=true;}
  getAttribute(name){return this.attributes[name]??null;}
  get id(){return this.attributes.id||'';}set id(value){this.attributes.id=value;}
  get name(){return this.attributes.name||'';}
  get type(){return this.attributes.type||'';}
  get className(){return this.attributes.class||'';}set className(value){this.attributes.class=value;}
  get value(){
    if(this.tagName==='select'){const options=this.querySelectorAll('option');return options.find(option=>option.value===this._value)?.value??options[0]?.value??'';}
    return String(this._value??this.attributes.value??(this.tagName==='textarea'?this.textContent:''));
  }
  set value(value){this._value=String(value??'');}
  get elements(){return Object.fromEntries(this.querySelectorAll('input,select,textarea,button').filter(node=>node.name).map(node=>[node.name,node]));}
  append(...nodes){for(let node of nodes){if(typeof node==='string')node=new TextNode(node);if(node.parentElement){const siblings=node.parentElement.children;siblings.splice(siblings.indexOf(node),1);}node.parentElement=this;this.children.push(node);}}
  insertBefore(node,before){if(node.parentElement){const siblings=node.parentElement.children;siblings.splice(siblings.indexOf(node),1);}node.parentElement=this;this.children.splice(this.children.indexOf(before),0,node);}
  before(node){this.parentElement.insertBefore(node,this);}
  get textContent(){return this.children.map(node=>node.textContent).join('');}
  set textContent(value){this.children=[];this.append(new TextNode(String(value??'')));}
  get innerHTML(){return this.children.map(node=>node instanceof TextNode?node.innerHTML:`<${node.tagName}${Object.entries(node.attributes).map(([key,value])=>` ${key}="${encode(value)}"`).join('')}>${node.innerHTML}</${node.tagName}>`).join('');}
  set innerHTML(html){
    this.children=[];this._value=undefined;const stack=[this];
    for(const token of html.match(/<\/?[\w-]+[^>]*>|[^<]+/g)||[]){
      if(token.startsWith('</')){stack.pop();continue;}
      if(!token.startsWith('<')){stack.at(-1).append(new TextNode(decode(token)));continue;}
      const [,tag,attrs]=/^<([\w-]+)([^>]*)>$/.exec(token),node=new Element(tag);
      for(const match of attrs.matchAll(/([\w-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s'"=<>]+)))?/g))node.setAttribute(match[1],decode(match[2]??match[3]??match[4]??''));
      stack.at(-1).append(node);if(!['input','br','hr','img','meta','link'].includes(tag))stack.push(node);
    }
  }
  matches(selector){
    if(selector.startsWith('#'))return this.id===selector.slice(1);
    if(selector.startsWith('.'))return this.className.split(/\s+/).includes(selector.slice(1));
    const attribute=/^\[([\w-]+)(?:=["']?([^\]"']+)["']?)?\]$/.exec(selector);
    return attribute?(attribute[2]===undefined?this.getAttribute(attribute[1])!==null:this.getAttribute(attribute[1])===attribute[2]):this.tagName===selector;
  }
  querySelectorAll(selector){
    const selectors=selector.split(',').map(part=>part.trim()),found=[];
    const visit=node=>{for(const child of node.children){if(!(child instanceof Element))continue;if(selectors.some(part=>child.matches(part)))found.push(child);visit(child);}};
    visit(this);return found;
  }
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  closest(selector){for(let node=this;node;node=node.parentElement)if(node.matches(selector))return node;return null;}
  addEventListener(type,listener){(this.listeners[type]??=[]).push(listener);}
  async fire(type){const event={type,target:this,preventDefault(){this.defaultPrevented=true;}};const handlers=[this['on'+type],...(this.listeners[type]||[])].filter(Boolean);await Promise.all(handlers.map(handler=>handler(event)));return event;}
  showModal(){assert.equal(this.open,false,'showModal must not run twice on an open dialog');this.open=true;}
  close(){if(!this.open)return;this.open=false;queueMicrotask(()=>this.fire('close'));}
}
const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const plan=(id='plan-a',digest='digest-'+id)=>({id,digest,version:'1',title:'Synthetic saved plan'});
const status=(plans=[plan()],extra={})=>({plans,reviews:[],grants:[],cloud_scopes:[],attempts:{runs:2,fits:1},default_python:'synthetic-python',method:{mode:'builtin',version:'1',digest:'method-digest'},method_update:{available:true,version:'2'},...extra});
const record=(saved=plan(),extra={})=>({record_id:saved.id,project_id:'project-a',plan_digest:saved.digest,session_id:'other-author-session',document:{content:'The same synthetic saved text'},integrity:{valid:true},...extra});
function harness(t,{legacy=false,onApi}={}){
  const original={document:globalThis.document,MutationObserver:globalThis.MutationObserver},observers=[];
  const document={body:new Element('body'),createElement:tag=>new Element(tag),createTextNode:text=>new TextNode(text)};document.querySelector=selector=>document.body.querySelector(selector);
  globalThis.document=document;globalThis.MutationObserver=class{constructor(callback){observers.push(callback);}observe(){}};
  t.after(()=>Object.assign(globalThis,original));
  let selected={sid:'session-a',pid:'project-a',version:1};const calls=[],toasts=[];let refreshes=0;
  const api=(path,payload)=>{const wait=deferred();calls.push({path,payload,...wait});onApi?.(path,payload);return wait.promise;};
  const ui=installResearchUI({api,current:()=>selected.sid,toast:text=>toasts.push(text),refresh:async()=>{refreshes++;},...(!legacy?{selection:()=>selected}:{})});
  const dialog=document.querySelector('#research-review-dialog'),method=document.querySelector('#research-method-dialog'),stop=document.querySelector('#research-round-stop-dialog'),autonomy=document.querySelector('#research-autonomy-dialog');
  const h={ui,document,dialog,form:dialog.querySelector('form'),method,stop,autonomy,calls,toasts,
    get refreshes(){return refreshes;},
    select(sid,pid='project-a',notify=true,advance=true){selected={sid,pid,version:selected.version+Number(advance)};if(notify)for(const observer of observers)observer();},
    requests(path){return calls.filter(call=>call.path===path);},
    last(path){const call=this.requests(path).at(-1);assert.ok(call,'Expected fake API call '+path);return call;},
    async resolve(call,value){call.resolve(value);await flush();},
    async ready(plans=[plan()],extra={},content=record(plans[0])){
      const opening=ui.open();await this.resolve(this.last('/api/research/status'),status(plans,extra));
      if(legacy)await this.resolve(this.last('/api/sessions/session-a'),{session:{id:'session-a',project_id:'project-a'}});
      await this.resolve(this.last('/api/records/'+plans[0].id),content);await opening;return opening;
    },
    async readyMethod(extra={}){const opening=ui.openMethod();await this.resolve(this.last('/api/research/status'),status([plan()],extra));await opening;},
    async readyStop(id='stop-a',extra={}){const opening=ui.openRoundStop(id);await this.resolve(this.last('/api/records/'+id),record(plan(id),{plan_digest:null,...extra}));await opening;},
    openAutonomy(){const host=document.createElement('section');host.innerHTML='<div class="company-section-header"></div>';ui.decorateExperiments(host);return host.querySelector('button').fire('click');},
    async readyAutonomy(plans=[plan()],extra={}){const opening=this.openAutonomy();await this.resolve(this.last('/api/research/status'),status(plans,{autonomy:{mode:'autonomous',authority_id:'authority-a',scope:{objective:'Objective',evaluation:'Evaluation',data_boundary:'Data',allowed_changes:'Changes'},branches:[],scheduler:{reason:'Synthetic pending continuation'}},agent_roles:{no_progress_guard:{blocked:false,consecutive_unchanged:1,limit:3}},...extra}));await opening;},
  };
  return h;
}
const submitButton=h=>h.dialog.querySelector('[type=submit]');
const rejectButton=h=>h.dialog.querySelector('[data-reject]');
const previewText=h=>h.dialog.querySelector('[data-content]').textContent;
const reviewPath='/api/research/review';

test('first approval captures only explicitly selected task variations and UID change clears them',async t=>{
  const h=harness(t);await h.ready([plan()],{task_policy_schema:1});h.form.elements.execution.value='lpai';await h.form.elements.execution.fire('change');
  const control=h.form.elements.allow_task_changes;assert.equal(control.checked,false);assert.equal(control.disabled,true);
  h.form.elements.component_uid.value='component-a';await h.form.elements.component_uid.fire('input');
  const probing=h.dialog.querySelector('[data-probe-component]').fire('click');
  const paths=['/lpai_git/0/commit_id','/lpai_task/cmd'];
  await h.resolve(h.last('/api/company/lpai'),{uid:'component-a',digest:'verified-digest',gpu_count:2,name:'Fixture',namespace:'Fixture',supported_patch_paths:paths});await probing;
  assert.equal(control.disabled,false);assert.equal(control.checked,false);
  control.checked=true;h.form.elements.allow_cloud.checked=true;h.form.elements.reason.value='Approve exact fixture scope';
  const submitting=h.form.fire('submit');const call=h.last(reviewPath);
  assert.deepEqual(call.payload.task_policy,{schema:1,allowed_patch_paths:paths});
  await h.resolve(call,{});await submitting;
  await h.ready([plan()],{task_policy_schema:1});h.form.elements.execution.value='lpai';await h.form.elements.execution.fire('change');
  h.form.elements.component_uid.value='component-a';await h.form.elements.component_uid.fire('input');
  const next=h.dialog.querySelector('[data-probe-component]').fire('click');
  await h.resolve(h.last('/api/company/lpai'),{uid:'component-a',digest:'verified-digest',gpu_count:2,name:'Fixture',namespace:'Fixture',supported_patch_paths:paths});await next;
  h.form.elements.allow_task_changes.checked=true;h.form.elements.component_uid.value='component-b';await h.form.elements.component_uid.fire('input');
  assert.equal(h.form.elements.allow_task_changes.checked,false);assert.equal(h.form.elements.allow_task_changes.disabled,true);
});

test('old component response cannot silently enable task variation',async t=>{
  const h=harness(t);await h.ready();h.form.elements.execution.value='lpai';await h.form.elements.execution.fire('change');
  h.form.elements.component_uid.value='component-a';await h.form.elements.component_uid.fire('input');
  const probing=h.dialog.querySelector('[data-probe-component]').fire('click');
  await h.resolve(h.last('/api/company/lpai'),{uid:'component-a',digest:'legacy-digest',gpu_count:2,name:'Fixture',namespace:'Fixture',supported_patch_paths:['/lpai_task/cmd']});await probing;
  assert.equal(h.form.elements.allow_task_changes.disabled,true);assert.equal(h.form.elements.allow_task_changes.checked,false);
  h.form.elements.reason.value='Keep legacy exact scope';const submitting=h.form.fire('submit');const call=h.last(reviewPath);
  assert.equal(call.payload.task_policy,undefined);await h.resolve(call,{});await submitting;
});

test('todo plan target opens exact version rather than first item and rejects missing target',async t=>{
  const h=harness(t),a=plan('a'),b=plan('b');
  const opening=h.ui.open('b');await h.resolve(h.last('/api/research/status'),status([a,b]));
  assert.equal(h.form.elements.plan_id.value,'b');assert.equal(h.requests('/api/records/a').length,0);
  await h.resolve(h.last('/api/records/b'),record(b));await opening;assert.equal(h.requests(reviewPath).length,0);
  await h.dialog.querySelector('[data-close]').fire('click');
  const missing=h.ui.open('absent');await h.resolve(h.last('/api/research/status'),status([a,b]));await missing;
  assert.equal(submitButton(h).disabled,true);assert.match(previewText(h),/未替换为其他方案/);
});

test('latest plan preview wins B-before-A, with loading text and both actions blocked',async t=>{
  const h=harness(t),a=plan('a'),b=plan('b');await h.ready([a,b]);
  const select=h.form.elements.plan_id;select.value='a';const older=select.fire('change'),old=h.last('/api/records/a');
  select.value='b';const newer=select.fire('change'),fresh=h.last('/api/records/b');
  assert.match(previewText(h),/正在载入/);assert.equal(submitButton(h).disabled,true);assert.equal(rejectButton(h).disabled,true);
  h.form.elements.reason.value='Synthetic reason';await rejectButton(h).fire('click');assert.equal(h.requests(reviewPath).length,0);
  await h.resolve(fresh,record(b,{document:{content:'Visible B'}}));await newer;
  await h.resolve(old,record(a,{document:{content:'Late A'}}));await older;
  assert.equal(previewText(h),'Visible B');assert.equal(select.value,'b');assert.equal(submitButton(h).disabled,false);
  const submission=h.form.fire('submit');assert.equal(h.last(reviewPath).payload.plan_id,'b');assert.equal(h.last(reviewPath).payload.expected_digest,b.digest);
  await h.resolve(h.last(reviewPath),{});await submission;
});

test('a pending newest preview stays loading when the older response arrives first, then ignores old failure',async t=>{
  const h=harness(t),a=plan('a'),b=plan('b');await h.ready([a,b]);
  h.form.elements.plan_id.value='a';const older=h.form.elements.plan_id.fire('change'),old=h.last('/api/records/a');
  h.form.elements.plan_id.value='b';const newer=h.form.elements.plan_id.fire('change'),fresh=h.last('/api/records/b');
  await h.resolve(old,record(a,{document:{content:'Older A arriving first'}}));await older;
  assert.match(previewText(h),/正在载入/);assert.equal(submitButton(h).disabled,true);assert.equal(rejectButton(h).disabled,true);
  await h.resolve(fresh,record(b,{document:{content:'Newest B arriving second'}}));await newer;
  assert.equal(previewText(h),'Newest B arriving second');
  h.form.elements.plan_id.value='a';const failing=h.form.elements.plan_id.fire('change'),failure=h.last('/api/records/a');
  h.form.elements.plan_id.value='b';const latest=h.form.elements.plan_id.fire('change');await h.resolve(h.last('/api/records/b'),record(b,{document:{content:'Newest B remains'}}));await latest;
  const count=h.toasts.length;failure.reject(Error('old synthetic failure'));await failing;
  assert.equal(previewText(h),'Newest B remains');assert.equal(h.toasts.length,count);
});

test('a loading preview clears the old plan and never submits a differently selected plan',async t=>{
  const h=harness(t),a=plan('a'),b=plan('b');await h.ready([a,b]);h.form.elements.reason.value='Reason';
  h.form.elements.plan_id.value='b';const changing=h.form.elements.plan_id.fire('change');
  await h.form.fire('submit');await rejectButton(h).fire('click');assert.equal(h.requests(reviewPath).length,0);assert.match(previewText(h),/正在载入/);
  await h.resolve(h.last('/api/records/b'),record(b));await changing;
  h.form.elements.plan_id.value='a';await h.form.fire('submit');await rejectButton(h).fire('click');assert.equal(h.requests(reviewPath).length,0);
});

test('latest open status wins, and a late status cannot replace its list or preview',async t=>{
  const h=harness(t),a=plan('a'),b=plan('b');const older=h.ui.open(),old=h.last('/api/research/status');
  const newer=h.ui.open(),fresh=h.last('/api/research/status');await h.resolve(fresh,status([b]));
  await h.resolve(h.last('/api/records/b'),record(b,{document:{content:'New open B'}}));await newer;
  await h.resolve(old,status([a]));await older;
  assert.equal(h.form.elements.plan_id.value,'b');assert.equal(previewText(h),'New open B');assert.equal(h.requests('/api/records/a').length,0);
});

test('A -> B -> A selection generation invalidates a pending preview even with identical IDs and text',async t=>{
  const h=harness(t),opening=h.ui.open();await h.resolve(h.last('/api/research/status'),status());const old=h.last('/api/records/plan-a');
  h.select('session-b','project-a',false);h.select('session-a','project-a',false);
  await h.resolve(old,record());await opening;assert.equal(h.dialog.open,false);assert.equal(submitButton(h).disabled,true);
  h.form.elements.reason.value='Reason';await h.form.fire('submit');await rejectButton(h).fire('click');assert.equal(h.requests(reviewPath).length,0);
  await h.ready();assert.equal(h.dialog.open,true);assert.equal(submitButton(h).disabled,false);
});

test('a native close is respected even before its queued close event invalidates a pending status',async t=>{
  const h=harness(t),opening=h.ui.open(),old=h.last('/api/research/status');
  h.dialog.close=()=>{h.dialog.open=false;};h.dialog.close();await h.resolve(old,status());await opening;
  assert.equal(h.dialog.open,false);assert.equal(h.requests('/api/records/plan-a').length,0);
});

test('selection change invalidates a displayed preview, including same session with a new project/version',async t=>{
  const h=harness(t);await h.ready();h.select('session-a','project-b');assert.equal(h.dialog.open,false);
  await h.form.fire('submit');assert.equal(h.requests(reviewPath).length,0);
  h.select('session-a','project-a');await h.ready();h.select('session-b');h.select('session-a');
  await h.form.fire('submit');assert.equal(h.requests(reviewPath).length,0);
});

test('explicit selectionChanged hook immediately invalidates review/method/round previews without DOM notifications',async t=>{
  const h=harness(t);await h.ready();await h.readyMethod();await h.readyStop();h.select('session-b','project-a',false);h.ui.selectionChanged();
  for(const dialog of [h.dialog,h.method,h.stop]){assert.equal(dialog.open,false);assert.equal(dialog.querySelector('[type=submit]').disabled,true);}
  h.select('session-a','project-a',false);h.ui.selectionChanged();await h.form.fire('submit');await h.method.querySelector('form').fire('submit');await h.stop.querySelector('form').fire('submit');
  assert.equal(h.requests(reviewPath).length,0);assert.equal(h.requests('/api/research/method-upgrade').length,0);assert.equal(h.requests('/api/research/round-stop').length,0);
});

test('close/cancel invalidate pending previews; a queued old close event cannot invalidate a reopen',async t=>{
  const h=harness(t),opening=h.ui.open();await h.resolve(h.last('/api/research/status'),status());const old=h.last('/api/records/plan-a');
  await h.dialog.fire('cancel');await h.resolve(old,record());await opening;assert.equal(h.dialog.open,false);
  await h.ready();h.dialog.querySelector('[data-close]').onclick();const reopened=h.ui.open();
  await h.resolve(h.last('/api/research/status'),status());await h.resolve(h.last('/api/records/plan-a'),record());await reopened;
  assert.equal(h.dialog.open,true);assert.equal(submitButton(h).disabled,false);
});

for(const [name,patch] of [
  ['different record_id',{record_id:'other-record'}],
  ['different project_id',{project_id:'other-project'}],
  ['different plan_digest',{plan_digest:'different-digest'}],
  ['missing record_id',{record_id:undefined}],
  ['missing project_id',{project_id:undefined}],
  ['missing plan_digest',{plan_digest:undefined}],
])test('both decisions require exact saved identity: '+name,async t=>{
  const h=harness(t);await h.ready([plan()],{},record(plan(),patch));assert.match(previewText(h),/预览载入失败/);
  assert.equal(submitButton(h).disabled,true);assert.equal(rejectButton(h).disabled,true);h.form.elements.reason.value='Reason';
  await h.form.fire('submit');await rejectButton(h).fire('click');assert.equal(h.requests(reviewPath).length,0);
});

test('a legitimate same-project plan from another author session submits its verified record and digest',async t=>{
  const h=harness(t);await h.ready();assert.equal(submitButton(h).disabled,false);h.form.elements.reason.value='Review another project member plan';
  const submitting=h.form.fire('submit'),call=h.last(reviewPath);assert.equal(call.payload.session_id,'session-a');assert.equal(call.payload.plan_id,'plan-a');assert.equal(call.payload.expected_digest,plan().digest);
  await h.resolve(call,{});await submitting;assert.equal(h.dialog.open,false);assert.equal(h.refreshes,1);
});

test('current-only callers resolve project identity from the selected session, not record authorship',async t=>{
  const h=harness(t,{legacy:true});await h.ready();assert.equal(submitButton(h).disabled,false);assert.equal(h.requests('/api/sessions/session-a').length,1);
  h.form.elements.reason.value='Legacy review';const submitting=rejectButton(h).fire('click');assert.equal(h.last(reviewPath).payload.decision,'reject');await h.resolve(h.last(reviewPath),{});await submitting;
});

test('legacy project lookup rejects a record outside the selected session project',async t=>{
  const h=harness(t,{legacy:true});await h.ready([plan()],{},record(plan(),{project_id:'other-project'}));
  assert.match(previewText(h),/预览载入失败/);h.form.elements.reason.value='Reason';await h.form.fire('submit');await rejectButton(h).fire('click');assert.equal(h.requests(reviewPath).length,0);
});

test('status/record failures are visible, block both decisions and can be explicitly reloaded',async t=>{
  const h=harness(t),opening=h.ui.open();assert.match(previewText(h),/正在载入/);h.last('/api/research/status').reject(Error('synthetic status failure'));await opening;
  assert.match(previewText(h),/预览载入失败.*synthetic status failure/);assert.equal(submitButton(h).disabled,true);assert.equal(rejectButton(h).disabled,true);
  const retry=h.ui.open();await h.resolve(h.last('/api/research/status'),status());h.last('/api/records/plan-a').reject(Error('synthetic record failure'));await retry;
  assert.match(previewText(h),/预览载入失败.*synthetic record failure/);await h.ready();assert.equal(submitButton(h).disabled,false);
});

test('a saved plan without a digest and a changed saved digest cannot authorize either decision',async t=>{
  const h=harness(t),saved=plan();await h.ready([saved]);saved.digest='changed-after-preview';h.form.elements.reason.value='Reason';
  await h.form.fire('submit');await rejectButton(h).fire('click');assert.equal(h.requests(reviewPath).length,0);
  const opening=h.ui.open();await h.resolve(h.last('/api/research/status'),status([{...plan(),digest:undefined}]));await opening;
  assert.match(previewText(h),/方案摘要缺失/);assert.equal(submitButton(h).disabled,true);assert.equal(rejectButton(h).disabled,true);
});

test('component UID input invalidates immediately, including UID -> other -> same while readback is pending',async t=>{
  const h=harness(t);await h.ready();h.form.elements.execution.value='lpai';await h.form.elements.execution.fire('change');
  const uid=h.form.elements.component_uid;uid.value='component-a';await uid.fire('input');const probing=h.dialog.querySelector('[data-probe-component]').fire('click'),call=h.last('/api/company/lpai');
  assert.match(h.dialog.querySelector('[data-component-preview]').textContent,/正在读回/);
  uid.value='component-b';await uid.fire('input');uid.value='component-a';await uid.fire('input');
  await h.resolve(call,{uid:'component-a',digest:'stale-component-digest',gpu_count:2,name:'Fake',namespace:'Synthetic'});await probing;
  assert.match(h.dialog.querySelector('[data-component-preview]').textContent,/UID已变化/);assert.equal(submitButton(h).disabled,true);assert.equal(h.form.elements.allow_cloud.checked,false);
  h.form.elements.reason.value='Reason';h.form.elements.allow_cloud.checked=true;await h.form.fire('submit');assert.equal(h.requests(reviewPath).length,0);
  const rejecting=rejectButton(h).fire('click');assert.equal(h.last(reviewPath).payload.component_digest,undefined);await h.resolve(h.last(reviewPath),{});await rejecting;
});

test('latest component readback wins and a subsequent UID input discards an already shown digest',async t=>{
  const h=harness(t);await h.ready();h.form.elements.execution.value='lpai';await h.form.elements.execution.fire('change');h.form.elements.component_uid.value='component-a';await h.form.elements.component_uid.fire('input');
  const probe=h.dialog.querySelector('[data-probe-component]'),older=probe.fire('click'),old=h.last('/api/company/lpai'),newer=probe.fire('click'),fresh=h.last('/api/company/lpai');
  await h.resolve(fresh,{uid:'component-a',digest:'fresh-digest',gpu_count:3,name:'Fresh',namespace:'Fake'});await newer;
  await h.resolve(old,{uid:'component-a',digest:'old-digest',gpu_count:7,name:'Old',namespace:'Fake'});await older;
  assert.match(h.dialog.querySelector('[data-component-preview]').textContent,/Fresh.*fresh-digest/);assert.equal(h.form.elements.max_gpus.value,'3');assert.equal(submitButton(h).disabled,false);
  h.form.elements.component_uid.value='component-b';await h.form.elements.component_uid.fire('input');assert.equal(submitButton(h).disabled,true);assert.equal(h.form.elements.allow_cloud.checked,false);
});

test('component readback cannot cross plan identity or an A -> B -> A selection generation',async t=>{
  const h=harness(t),a=plan('a'),b=plan('b');await h.ready([a,b]);h.form.elements.component_uid.value='component-a';
  const probing=h.dialog.querySelector('[data-probe-component]').fire('click'),old=h.last('/api/company/lpai');
  h.form.elements.plan_id.value='b';const selecting=h.form.elements.plan_id.fire('change');await h.resolve(h.last('/api/records/b'),record(b));await selecting;
  h.form.elements.component_uid.value='component-a';await h.form.elements.component_uid.fire('input');await h.resolve(old,{uid:'component-a',digest:'old-plan-component',gpu_count:9,name:'Old',namespace:'Fake'});await probing;
  assert.doesNotMatch(h.dialog.querySelector('[data-component-preview]').textContent,/old-plan-component/);assert.equal(h.form.elements.max_gpus.value,'0');
  const stale=h.dialog.querySelector('[data-probe-component]').fire('click'),staleCall=h.last('/api/company/lpai');h.select('session-b','project-a',false);h.select('session-a','project-a',false);
  await h.resolve(staleCall,{uid:'component-a',digest:'old-selection-component',gpu_count:9,name:'Old',namespace:'Fake'});await stale;assert.equal(h.dialog.open,false);
});

test('component readback cannot cross a same-ID plan reload with a changed saved digest',async t=>{
  const h=harness(t),original=plan('same-plan','first-plan-digest');await h.ready([original]);h.form.elements.component_uid.value='component-a';
  const probing=h.dialog.querySelector('[data-probe-component]').fire('click'),old=h.last('/api/company/lpai');
  const replacement=plan('same-plan','second-plan-digest');await h.ready([replacement]);h.form.elements.execution.value='lpai';await h.form.elements.execution.fire('change');h.form.elements.component_uid.value='component-a';await h.form.elements.component_uid.fire('input');
  await h.resolve(old,{uid:'component-a',digest:'old-plan-component',gpu_count:8,name:'Old',namespace:'Fake'});await probing;
  assert.doesNotMatch(h.dialog.querySelector('[data-component-preview]').textContent,/old-plan-component/);assert.equal(submitButton(h).disabled,true);
  h.form.elements.reason.value='Reloaded same plan';const rejecting=rejectButton(h).fire('click');assert.equal(h.last(reviewPath).payload.expected_digest,'second-plan-digest');assert.equal(h.last(reviewPath).payload.component_digest,undefined);await h.resolve(h.last(reviewPath),{});await rejecting;
});

test('a component readback with a different returned UID fails visibly and cannot authorize cloud execution',async t=>{
  const h=harness(t);await h.ready();h.form.elements.execution.value='lpai';await h.form.elements.execution.fire('change');h.form.elements.component_uid.value='component-a';
  const probing=h.dialog.querySelector('[data-probe-component]').fire('click');await h.resolve(h.last('/api/company/lpai'),{uid:'component-b',digest:'wrong-uid-digest',gpu_count:1,name:'Fake',namespace:'Fake'});await probing;
  assert.match(h.dialog.querySelector('[data-component-preview]').textContent,/读回失败/);assert.equal(submitButton(h).disabled,true);
});

test('verified cloud payload is captured once; duplicate accept/reject and plan/UID edits are guarded',async t=>{
  const h=harness(t),saved=plan();await h.ready([saved],{autonomy:{scope:{objective:'objective',evaluation:'evaluation',data_boundary:'data',allowed_changes:'changes',frozen_files:[{path:'fixed.fake'}]}}});
  h.form.elements.execution.value='lpai';await h.form.elements.execution.fire('change');h.form.elements.component_uid.value='component-a';await h.form.elements.component_uid.fire('input');
  const probing=h.dialog.querySelector('[data-probe-component]').fire('click');await h.resolve(h.last('/api/company/lpai'),{uid:'component-a',digest:'verified-component-digest',gpu_count:2,name:'Fake',namespace:'Fake'});await probing;
  h.form.elements.reason.value='Captured review reason';h.form.elements.allow_cloud.checked=true;
  const submitting=h.form.fire('submit'),call=h.last(reviewPath);await h.form.fire('submit');await rejectButton(h).fire('click');assert.equal(h.requests(reviewPath).length,1);
  assert.ok(h.form.querySelectorAll('input,select,textarea,button').every(control=>control.disabled));
  h.form.elements.plan_id.value='not-visible';await h.form.elements.plan_id.fire('change');assert.equal(h.form.elements.plan_id.value,saved.id);
  h.form.elements.component_uid.value='other-component';await h.form.elements.component_uid.fire('input');assert.equal(h.form.elements.component_uid.value,'component-a');
  h.form.elements.reason.value='Later reason';h.form.elements.scope_objective.value='Later scope';h.form.elements.max_gpus.value='99';
  assert.equal(call.payload.reason,'Captured review reason');assert.equal(call.payload.research_scope.objective,'objective');assert.equal(call.payload.component_uid,'component-a');assert.equal(call.payload.component_digest,'verified-component-digest');assert.equal(call.payload.max_gpus,2);assert.equal(call.payload.plan_id,saved.id);assert.equal(call.payload.expected_digest,saved.digest);
  await h.resolve(call,{});await submitting;assert.equal(h.refreshes,1);assert.equal(h.dialog.open,false);
});

test('an old review submission cannot close a newer dialog or post twice across close/reopen',async t=>{
  const h=harness(t),a=plan('a'),b=plan('b');await h.ready([a]);h.form.elements.reason.value='Old reason';const submitting=h.form.fire('submit'),old=h.last(reviewPath);
  await h.dialog.querySelector('[data-close]').fire('click');await h.ready([b],{},record(b,{document:{content:'New dialog B'}}));
  assert.equal(h.dialog.open,true);assert.equal(previewText(h),'New dialog B');assert.equal(h.form.elements.plan_id.disabled,true);
  h.form.elements.reason.value='New reason';await h.form.fire('submit');assert.equal(h.requests(reviewPath).length,1);
  await h.resolve(old,{});await submitting;assert.equal(h.dialog.open,true);assert.equal(previewText(h),'New dialog B');assert.equal(submitButton(h).disabled,false);assert.equal(h.refreshes,0);
  const next=h.form.fire('submit');assert.equal(h.last(reviewPath).payload.plan_id,'b');await h.resolve(h.last(reviewPath),{});await next;
});

test('an A -> B -> A switch suppresses review completion even when the new preview has the same text/id',async t=>{
  const h=harness(t);await h.ready();h.form.elements.reason.value='Old reason';const submitting=h.form.fire('submit'),old=h.last(reviewPath);
  h.select('session-b');h.select('session-a');await h.ready();await h.resolve(old,{});await submitting;
  assert.equal(h.dialog.open,true);assert.equal(submitButton(h).disabled,false);assert.equal(h.refreshes,0);assert.equal(h.toasts.some(text=>text.includes('审核已保存')),false);
});

test('reentrant API dispatch cannot double-submit and reopening cannot reset the synchronous lock',async t=>{
  let h;const reentrant=[];
  h=harness(t,{onApi:path=>{if(path===reviewPath){reentrant.push(h.form.fire('submit'),rejectButton(h).fire('click'));}}});await h.ready();h.form.elements.reason.value='Reason';
  const submitting=h.form.fire('submit'),call=h.last(reviewPath);await Promise.all(reentrant);const reopening=h.ui.open();await h.form.fire('submit');await rejectButton(h).fire('click');await reopening;
  assert.equal(h.requests(reviewPath).length,1);assert.equal(h.requests('/api/research/status').length,1);assert.equal(h.form.elements.plan_id.disabled,true);
  await h.resolve(call,{});await submitting;
});

test('a late failed review does not overwrite or toast against a newer dialog and releases only its own lock',async t=>{
  const h=harness(t);await h.ready();h.form.elements.reason.value='Old reason';const submitting=h.form.fire('submit'),old=h.last(reviewPath);
  await h.dialog.querySelector('[data-close]').fire('click');await h.ready([plan('b')],{},record(plan('b'),{document:{content:'New preview B'}}));const count=h.toasts.length;
  old.reject(Error('old synthetic submission failure'));await submitting;
  assert.equal(h.dialog.open,true);assert.equal(previewText(h),'New preview B');assert.equal(h.toasts.length,count);assert.equal(h.dialog.querySelector('[data-status]').textContent,'');assert.equal(submitButton(h).disabled,false);
});

test('submission failure preserves the verified preview, releases lock and never automatically resends',async t=>{
  const h=harness(t);await h.ready();h.form.elements.reason.value='Reason';const submitting=h.form.fire('submit');h.last(reviewPath).reject(Error('synthetic uncertain delivery'));await submitting;
  assert.equal(h.requests(reviewPath).length,1);assert.equal(h.dialog.open,true);assert.equal(submitButton(h).disabled,false);assert.match(h.dialog.querySelector('[data-status]').textContent,/提交未确认.*不会自动重试/);assert.equal(h.refreshes,0);
});

test('stale evidence still allows rejection only; an existing grant keeps its execution and authority scope immutable',async t=>{
  const h=harness(t);await h.ready([plan()],{},record(plan(),{integrity:{valid:false,message:'synthetic evidence changed'}}));
  assert.match(previewText(h),/引用证据已变化/);assert.equal(submitButton(h).disabled,true);assert.equal(rejectButton(h).disabled,false);h.form.elements.reason.value='Evidence needs revision';
  await h.form.fire('submit');assert.equal(h.requests(reviewPath).length,0);const rejecting=rejectButton(h).fire('click');await h.resolve(h.last(reviewPath),{});await rejecting;
  await h.ready([plan()],{grants:[{id:'grant-a',plan_id:'plan-a',execution:'lpai',python:'fixed-python',max_runs:5,max_fits:4,timeout:600}],cloud_scopes:[{grant_id:'grant-a',component_uid:'fixed-component',component_digest:'fixed-digest',max_gpus:2}],autonomy:{authority_id:'authority-a',scope:{}}});
  for(const name of ['python','max_runs','max_fits','timeout','execution','allow_local','component_uid','allow_cloud','max_gpus','autonomous'])assert.equal(h.form.elements[name].disabled,true,name+' must remain fixed');
  assert.equal(h.form.elements.autonomous.checked,false);assert.equal(submitButton(h).disabled,true);assert.equal(rejectButton(h).disabled,false);assert.equal(h.dialog.querySelector('[data-probe-component]').disabled,true);assert.match(h.dialog.querySelector('[data-component-preview]').textContent,/fixed-digest/);
});

const frozenFields=()=>({autonomy:{scope:{objective:'Synthetic objective',evaluation:'Synthetic evaluation',data_boundary:'Synthetic data boundary',allowed_changes:'Synthetic allowed changes',frozen_files:[{path:'protocol.fake'}]}}});

test('an ungranted plan proposal populates delegated scope without weakening project authority',async t=>{
  const h=harness(t),saved={...plan(),scope_proposal:{objective:'Proposed objective',evaluation:'Proposed evaluation',data_boundary:'Proposed data',allowed_changes:'Proposed changes',frozen_files:['proposed.fake']}};
  await h.ready([saved],frozenFields());assert.equal(h.form.elements.autonomous.disabled,false);assert.equal(h.form.elements.autonomous.checked,true);assert.equal(h.dialog.querySelector('[data-scope]').hidden,true);
  assert.match(h.dialog.querySelector('[data-scope-summary]').textContent,/Proposed objective/);
  assert.equal(h.form.elements.scope_objective.value,'Proposed objective');assert.equal(h.form.elements.scope_frozen_files.value,'proposed.fake');h.form.elements.reason.value='Delegation reason';
  const submitting=h.form.fire('submit'),call=h.last(reviewPath);assert.equal(call.payload.autonomous,true);assert.equal(call.payload.research_scope.evaluation,'Proposed evaluation');assert.deepEqual(call.payload.research_scope.frozen_files,['proposed.fake']);await h.resolve(call,{});await submitting;
});

test('local and cloud consent/scope remain separate and the local review keeps cumulative limits',async t=>{
  const h=harness(t);await h.ready();assert.equal(h.form.elements.autonomous.disabled,true);assert.equal(h.form.elements.max_runs.value,'102');assert.equal(h.form.elements.max_fits.value,'102');
  h.form.elements.execution.value='lpai';await h.form.elements.execution.fire('change');assert.equal(h.dialog.querySelector('[data-cloud]').hidden,false);assert.equal(h.form.elements.allow_local.closest('label').hidden,true);
  h.form.elements.execution.value='local';await h.form.elements.execution.fire('change');assert.equal(h.dialog.querySelector('[data-cloud]').hidden,true);assert.equal(h.form.elements.allow_local.closest('label').hidden,false);
  h.form.elements.reason.value='Local reason';h.form.elements.allow_local.checked=true;const submitting=h.form.fire('submit'),call=h.last(reviewPath);
  assert.equal(call.payload.execution,'local');assert.equal(call.payload.allow_local,true);assert.equal(call.payload.allow_cloud,false);assert.equal(call.payload.component_digest,undefined);assert.equal(call.payload.max_runs,102);assert.equal(call.payload.max_fits,102);assert.equal(call.payload.autonomous,false);assert.equal(call.payload.research_scope,null);
  await h.resolve(call,{});await submitting;
});

test('LPAI opens with a read-only exact preview and confirms without a component UID',async t=>{
  const h=harness(t),saved=plan();await h.ready([saved],{...frozenFields(),lpai_available:true,auto_component_available:true});
  const preview=h.last('/api/research/component-preview');assert.equal(submitButton(h).disabled,true);
  assert.deepEqual(preview.payload,{session_id:'session-a',plan_id:saved.id,expected_digest:saved.digest});
  assert.equal(h.requests(reviewPath).length,0);
  await h.resolve(preview,{id:'cpu-preview',plan_id:saved.id,plan_digest:saved.digest,cpu:1,memory_gib:4,gpu_count:0});
  assert.equal(submitButton(h).disabled,false);assert.equal(h.form.elements.component_uid.value,'');
  const confirming=h.form.fire('submit'),call=h.last(reviewPath);
  assert.equal(call.payload.component_preview_id,'cpu-preview');assert.equal(call.payload.allow_cloud,true);assert.equal(call.payload.component_uid,'');
  assert.equal(call.payload.autonomous,true);assert.equal(call.payload.max_gpus,0);
  await h.resolve(call,{});await confirming;
});

test('saved budget suggestion keeps existing attempts and a legacy plan can request scope without manual fields',async t=>{
  const h=harness(t),saved={...plan(),scope_proposal:{...frozenFields().autonomy.scope,frozen_files:[],
    budget:{additional_runs:3,additional_fits:2,single_attempt_seconds:240}}};
  await h.ready([saved],frozenFields());
  assert.equal(h.form.elements.max_runs.value,'5');assert.equal(h.form.elements.max_fits.value,'5');assert.equal(h.form.elements.timeout.value,'240');
  await h.ready([plan()],{lpai_available:true});assert.equal(rejectButton(h).textContent,'请 IterLab 补齐');
  const requesting=rejectButton(h).fire('click'),call=h.last(reviewPath);
  assert.equal(call.payload.decision,'reject');assert.match(call.payload.reason,/数据边界/);assert.equal(call.payload.component_preview_id,undefined);
  await h.resolve(call,{});await requesting;
});

test('ordinary task budget needs no fit field and special zero-fit proposal stays diagnostic',async t=>{
  const h=harness(t),scope=frozenFields().autonomy.scope;
  await h.ready([{...plan(),scope_proposal:{...scope,frozen_files:[],budget:{additional_runs:5,single_attempt_seconds:240}}}],frozenFields());
  assert.equal(h.form.elements.max_runs.value,'7');assert.equal(h.form.elements.max_fits.value,'7');
  assert.equal(h.form.elements.max_fits.type,'hidden');
  assert.doesNotMatch(h.dialog.querySelector('[data-budget]').textContent,/已用|拟合|\d+\/\d+/);
  await h.ready([{...plan(),scope_proposal:{...scope,frozen_files:[],budget:{additional_runs:5,additional_fits:0,single_attempt_seconds:240}}}],frozenFields());
  assert.equal(h.form.elements.max_fits.value,'1');
  h.form.elements.max_runs.value='10';await h.form.elements.max_runs.fire('input');
  assert.equal(h.form.elements.max_fits.value,'1');
});

test('interactive GPU review waits for exact bound space and never defaults to local',async t=>{
  const h=harness(t),saved={...plan(),scope_proposal:{compute:{gpu_count:1,workflow:'interactive'}}};
  await h.ready([saved],{lpai_available:true,auto_component_available:true,devspace_execution_supported:true});
  assert.equal(h.form.elements.execution.value,'lpai_devspace');
  assert.equal(submitButton(h).disabled,true);
  assert.equal(h.requests('/api/research/component-preview').length,0);
  await h.ready([saved],{lpai_available:true,devspace_execution_supported:true,devspace_binding:{uid:'gpu-space',name:'gpu-research'}});
  assert.equal(submitButton(h).disabled,true);
  await h.ready([saved],{lpai_available:true,devspace_execution_supported:true,devspace_binding:{uid:'gpu-space',name:'gpu-research',ssh_port:2222}});
  assert.equal(submitButton(h).disabled,false);
  const sending=h.form.fire('submit'),call=h.last(reviewPath);
  assert.equal(call.payload.execution,'lpai_devspace');
  assert.equal(call.payload.space_uid,'gpu-space');
  assert.equal(call.payload.ssh_port,undefined);
  assert.equal(call.payload.allow_cloud,true);
  assert.equal(call.payload.allow_local,false);
  await h.resolve(call,{});await sending;
});

test('batch GPU review reads an exact GPU preview and rejects a CPU response',async t=>{
  const h=harness(t),saved={...plan(),scope_proposal:{compute:{gpu_count:2,workflow:'batch'}}};
  await h.ready([saved],{lpai_available:true,auto_component_available:true,devspace_binding:{uid:'gpu-space',name:'gpu-research'}});
  assert.equal(h.form.elements.execution.value,'lpai');
  assert.equal(h.requests('/api/research/component-preview').length,1);
  await h.resolve(h.last('/api/research/component-preview'),{id:'cpu-preview',plan_id:saved.id,plan_digest:saved.digest,gpu_count:0});
  assert.match(h.dialog.querySelector('[data-auto-component]').textContent,/与当前方案不符/);
  assert.equal(submitButton(h).disabled,true);
});

test('GPU configuration review confirms the recommended count, full configuration and all worker paths',async t=>{
  const h=harness(t),saved={...plan(),scope_proposal:{...frozenFields().autonomy.scope,frozen_files:[],compute:{gpu_count:32,workflow:'batch'},component_config:'research/run.json'}};
  await h.ready([saved],{...frozenFields(),lpai_available:true,auto_component_available:true});
  const paths=['/lpai_task/cmd','/lpai_task/roles/0/cmd','/lpai_task/roles/1/cmd'];
  await h.resolve(h.last('/api/research/component-preview'),{id:'gpu-preview',plan_id:saved.id,plan_digest:saved.digest,gpu_count:32,
    gpu_type:'A800',workers:4,cpu:416,memory_gib:3488,image:'registry.example/torch:fixed',volume:'results',queue_name:'Actual account queue',
    command_paths:paths,mounts:[{name:'data',version:'v1',env_value:'/mnt/data'}],training_parameters:{batch_size:64},rationale:'Measured memory',compatibility:'Code and image verified'});
  assert.equal(submitButton(h).disabled,false);assert.match(h.dialog.querySelector('[data-auto-component]').textContent,/32 GPU/);
  assert.match(h.dialog.querySelector('[data-auto-component]').textContent,/batch_size：64/);
  const sending=h.form.fire('submit'),call=h.last(reviewPath);
  assert.equal(call.payload.component_preview_id,'gpu-preview');assert.equal(call.payload.max_gpus,32);
  assert.deepEqual(call.payload.task_policy.allowed_patch_paths,paths);
  await h.resolve(call,{});await sending;
});

test('quick budget extension is tied to the active grant and fits within the saved hard limit',async t=>{
  const h=harness(t),extra={round:{state:'active'},resources:{used_runs:24,used_fits:20,
    budget:{plan_id:'plan-a',grant_id:'grant-a',revision:2,max_runs:24,max_fits:20,auto:true,hard_runs:27,hard_fits:22}}};
  await h.readyAutonomy([plan()],extra);
  const button=h.autonomy.querySelector('[data-budget-batch]');assert.ok(button,h.toasts.join(' '));assert.match(button.textContent,/3 次任务/);
  const submitting=button.fire('click'),call=h.last('/api/research/autonomy');
  assert.deepEqual(call.payload,{session_id:'session-a',action:'budget',confirmed:true,plan_id:'plan-a',grant_id:'grant-a',revision:2,
    add_runs:3,auto:true,hard_runs:27});
  await h.resolve(call,{paused:false});await h.resolve(h.last('/api/research/status'),status());await submitting;
});

test('method previews are latest-wins and a late/closed selection preview cannot become actionable',async t=>{
  const h=harness(t),older=h.ui.openMethod(),old=h.last('/api/research/status'),newer=h.ui.openMethod(),fresh=h.last('/api/research/status');
  assert.equal(h.method.querySelector('[type=submit]').disabled,true);await h.resolve(fresh,status([plan()],{method:{mode:'builtin',version:'new',digest:'new-method'}}));await newer;
  await h.resolve(old,status([plan()],{method:{mode:'builtin',version:'old',digest:'old-method'}}));await older;assert.match(h.method.querySelector('[data-method]').textContent,/new/);
  const opening=h.ui.openMethod(),late=h.last('/api/research/status');h.select('session-b','project-a',false);h.select('session-a','project-a',false);await h.resolve(late,status());await opening;
  assert.equal(h.method.open,false);await h.method.querySelector('form').fire('submit');assert.equal(h.requests('/api/research/method-upgrade').length,0);
  const closed=h.ui.openMethod(),closedCall=h.last('/api/research/status');await h.method.querySelector('[data-close]').fire('click');await h.resolve(closedCall,status());await closed;assert.equal(h.method.open,false);
});

test('method upgrades stay blocked when unavailable or missing the saved method digest',async t=>{
  const h=harness(t);await h.readyMethod({method_update:{available:false,error:'Synthetic contract unavailable'}});assert.equal(h.method.querySelector('[type=submit]').disabled,true);assert.match(h.method.querySelector('[data-method]').textContent,/Synthetic contract unavailable/);
  h.method.querySelector('form').elements.reason.value='Reason';await h.method.querySelector('form').fire('submit');assert.equal(h.requests('/api/research/method-upgrade').length,0);
  await h.readyMethod({method:{mode:'builtin',version:'1'}});assert.match(h.method.querySelector('[data-method]').textContent,/预览载入失败.*方法摘要缺失/);await h.method.querySelector('form').fire('submit');assert.equal(h.requests('/api/research/method-upgrade').length,0);
});

test('superseded method response arriving first keeps the newest method preview loading',async t=>{
  const h=harness(t),older=h.ui.openMethod(),old=h.last('/api/research/status'),newer=h.ui.openMethod();
  await h.resolve(old,status([plan()],{method:{mode:'builtin',version:'old',digest:'old-method'}}));await older;
  assert.match(h.method.querySelector('[data-method]').textContent,/正在载入/);assert.equal(h.method.querySelector('[type=submit]').disabled,true);
  await h.resolve(h.last('/api/research/status'),status([plan()],{method:{mode:'builtin',version:'new',digest:'new-method'}}));await newer;assert.match(h.method.querySelector('[data-method]').textContent,/new/);
});

test('method duplicate actions are locked and a late submission cannot close a newer method dialog',async t=>{
  const h=harness(t);await h.readyMethod();const form=h.method.querySelector('form');form.elements.reason.value='Captured method reason';
  const submitting=form.fire('submit'),old=h.last('/api/research/method-upgrade');assert.equal(old.payload.expected_digest,'method-digest');assert.equal(old.payload.reason,'Captured method reason');await form.fire('submit');assert.equal(h.requests('/api/research/method-upgrade').length,1);assert.equal(form.elements.reason.disabled,true);
  await h.method.querySelector('[data-close]').fire('click');await h.readyMethod({method:{mode:'builtin',version:'new',digest:'new-method-digest'}});await form.fire('submit');assert.equal(h.requests('/api/research/method-upgrade').length,1);
  await h.resolve(old,{changed:true});await submitting;assert.equal(h.method.open,true);assert.match(h.method.querySelector('[data-method]').textContent,/new/);assert.equal(h.refreshes,0);
  form.elements.reason.value='New method reason';const next=form.fire('submit');assert.equal(h.last('/api/research/method-upgrade').payload.expected_digest,'new-method-digest');await h.resolve(h.last('/api/research/method-upgrade'),{changed:true});await next;assert.equal(h.method.open,false);assert.equal(h.refreshes,1);
});

test('method and round-stop submissions ignore completion after same-ID/text A -> B -> A selection changes',async t=>{
  const h=harness(t);await h.readyMethod();h.method.querySelector('form').elements.reason.value='Old method reason';
  const methodSubmitting=h.method.querySelector('form').fire('submit'),oldMethod=h.last('/api/research/method-upgrade');h.select('session-b');h.select('session-a');await h.readyMethod();
  await h.resolve(oldMethod,{changed:true});await methodSubmitting;assert.equal(h.method.open,true);assert.equal(h.method.querySelector('[type=submit]').disabled,false);assert.equal(h.refreshes,0);
  await h.readyStop();h.stop.querySelector('form').elements.reason.value='Old stop reason';const stopSubmitting=h.stop.querySelector('form').fire('submit'),oldStop=h.last('/api/research/round-stop');h.select('session-b');h.select('session-a');await h.readyStop();
  await h.resolve(oldStop,{});await stopSubmitting;assert.equal(h.stop.open,true);assert.equal(h.stop.querySelector('[type=submit]').disabled,false);assert.equal(h.refreshes,0);
});

test('method and round-stop failed previews/actions are visible, unlock and never automatically retry',async t=>{
  const h=harness(t),opening=h.ui.openMethod();h.last('/api/research/status').reject(Error('synthetic method load failure'));await opening;
  assert.match(h.method.querySelector('[data-method]').textContent,/预览载入失败/);assert.equal(h.method.querySelector('[type=submit]').disabled,true);
  await h.readyMethod();h.method.querySelector('form').elements.reason.value='Reason';const methodSubmitting=h.method.querySelector('form').fire('submit');h.last('/api/research/method-upgrade').reject(Error('synthetic uncertain method delivery'));await methodSubmitting;
  assert.equal(h.requests('/api/research/method-upgrade').length,1);assert.equal(h.method.open,true);assert.equal(h.method.querySelector('[type=submit]').disabled,false);assert.match(h.method.querySelector('[data-status]').textContent,/提交未确认/);
  const stopOpening=h.ui.openRoundStop('stop-a');h.last('/api/records/stop-a').reject(Error('synthetic stop load failure'));await stopOpening;
  assert.match(h.stop.querySelector('article').textContent,/预览载入失败/);assert.equal(h.stop.querySelector('[data-reject]').disabled,true);
  await h.readyStop();h.stop.querySelector('form').elements.reason.value='Reason';const stopSubmitting=h.stop.querySelector('[data-reject]').fire('click');h.last('/api/research/round-stop').reject(Error('synthetic uncertain stop delivery'));await stopSubmitting;
  assert.equal(h.requests('/api/research/round-stop').length,1);assert.equal(h.stop.open,true);assert.equal(h.stop.querySelector('[data-reject]').disabled,false);assert.match(h.stop.querySelector('[data-status]').textContent,/提交未确认/);assert.equal(h.refreshes,0);
});

test('round-stop previews are latest-wins, verify exact record/project and reject a late A -> B -> A preview',async t=>{
  const h=harness(t),older=h.ui.openRoundStop('stop-a'),old=h.last('/api/records/stop-a'),newer=h.ui.openRoundStop('stop-b'),fresh=h.last('/api/records/stop-b');
  assert.equal(h.stop.querySelector('[data-reject]').disabled,true);await h.resolve(fresh,record(plan('stop-b'),{document:{content:'Visible stop B'}}));await newer;
  await h.resolve(old,record(plan('stop-a'),{document:{content:'Late stop A'}}));await older;assert.equal(h.stop.querySelector('article').textContent,'Visible stop B');
  await h.readyStop('stop-wrong',{record_id:'other-stop'});assert.match(h.stop.querySelector('article').textContent,/预览载入失败/);await h.stop.querySelector('[data-reject]').fire('click');assert.equal(h.requests('/api/research/round-stop').length,0);
  await h.readyStop('stop-wrong-project',{project_id:'other-project'});assert.equal(h.stop.querySelector('[type=submit]').disabled,true);
  const opening=h.ui.openRoundStop('stop-same'),late=h.last('/api/records/stop-same');h.select('session-b','project-a',false);h.select('session-a','project-a',false);await h.resolve(late,record(plan('stop-same')));await opening;assert.equal(h.stop.open,false);
});

test('superseded round-stop response arriving first keeps the newest suggestion loading',async t=>{
  const h=harness(t),older=h.ui.openRoundStop('stop-a'),old=h.last('/api/records/stop-a'),newer=h.ui.openRoundStop('stop-b');
  await h.resolve(old,record(plan('stop-a'),{document:{content:'Old suggestion first'}}));await older;
  assert.match(h.stop.querySelector('article').textContent,/正在载入/);assert.equal(h.stop.querySelector('[type=submit]').disabled,true);assert.equal(h.stop.querySelector('[data-reject]').disabled,true);
  await h.resolve(h.last('/api/records/stop-b'),record(plan('stop-b'),{document:{content:'Newest suggestion second'}}));await newer;assert.equal(h.stop.querySelector('article').textContent,'Newest suggestion second');
});

test('closing a pending round-stop preview prevents a late record from reopening it',async t=>{
  const h=harness(t),opening=h.ui.openRoundStop('stop-a'),old=h.last('/api/records/stop-a');await h.stop.fire('cancel');await h.resolve(old,record(plan('stop-a')));await opening;
  assert.equal(h.stop.open,false);assert.equal(h.stop.querySelector('[type=submit]').disabled,true);assert.equal(h.stop.querySelector('[data-reject]').disabled,true);
});

test('round-stop duplicate accept/reject is locked and late completion cannot close a newer suggestion',async t=>{
  const h=harness(t);await h.readyStop();const form=h.stop.querySelector('form');form.elements.reason.value='Captured stop reason';
  const submitting=form.fire('submit'),old=h.last('/api/research/round-stop');assert.equal(old.payload.review_id,'stop-a');assert.equal(old.payload.reason,'Captured stop reason');assert.equal(old.payload.decision,'accept_stop');await form.fire('submit');await h.stop.querySelector('[data-reject]').fire('click');assert.equal(h.requests('/api/research/round-stop').length,1);assert.equal(form.elements.reason.disabled,true);
  await h.stop.querySelector('[data-close]').fire('click');await h.readyStop('stop-b',{document:{content:'New stop suggestion'}});await form.fire('submit');assert.equal(h.requests('/api/research/round-stop').length,1);
  await h.resolve(old,{});await submitting;assert.equal(h.stop.open,true);assert.equal(h.stop.querySelector('article').textContent,'New stop suggestion');assert.equal(h.refreshes,0);
  form.elements.reason.value='New stop reason';const next=h.stop.querySelector('[data-reject]').fire('click');assert.equal(h.last('/api/research/round-stop').payload.review_id,'stop-b');assert.equal(h.last('/api/research/round-stop').payload.decision,'reject_stop');await h.resolve(h.last('/api/research/round-stop'),{});await next;assert.equal(h.stop.open,false);assert.equal(h.refreshes,1);
});

test('autonomous closed review blocks both human stop actions without an extra status query',async t=>{
  const h=harness(t);await h.readyStop('auto-closed',{closure:{stop_reason:'budget',target_verified:false,requires_human_confirmation:false}});
  assert.match(h.stop.querySelector('article').textContent,/已自主收束/);
  assert.equal(h.stop.querySelector('[type=submit]').disabled,true);
  assert.equal(h.stop.querySelector('[data-reject]').disabled,true);
  h.stop.querySelector('form').elements.reason.value='Plain input does not reopen the round';
  await h.stop.querySelector('form').fire('submit');await h.stop.querySelector('[data-reject]').fire('click');
  assert.equal(h.requests('/api/research/round-stop').length,0);
  assert.equal(h.requests('/api/research/status').length,0);
});

test('manual limited stop retains exact human confirmation while autonomous closure cannot enable it',async t=>{
  const h=harness(t);await h.readyStop('manual-limited',{closure:{stop_reason:'blocked',requires_human_confirmation:true}});
  assert.equal(h.stop.querySelector('[type=submit]').disabled,false);
  h.stop.querySelector('form').elements.reason.value='Acknowledge missing evidence, not target achievement';
  const pending=h.stop.querySelector('form').fire('submit'),request=h.last('/api/research/round-stop');
  assert.equal(request.payload.review_id,'manual-limited');assert.equal(request.payload.confirmed,true);
  await h.resolve(request,{});await pending;
  assert.equal(h.requests('/api/research/round-stop').length,1);
});

const autonomyBody=h=>h.autonomy.querySelector('[data-body]');
const autonomyForm=h=>h.autonomy.querySelector('form');
const autonomyPath='/api/research/autonomy';

for(const newestFirst of [true,false])test('exported autonomy open is visibly loading and latest-wins, newest first: '+newestFirst,async t=>{
  const h=harness(t),older=h.openAutonomy(),old=h.last('/api/research/status'),newer=h.openAutonomy(),fresh=h.last('/api/research/status');
  assert.equal(h.autonomy.open,true);assert.match(autonomyBody(h).textContent,/正在载入/);assert.equal(autonomyForm(h),null);
  if(!newestFirst){await h.resolve(old,status([],{autonomy:null}));await older;assert.match(autonomyBody(h).textContent,/正在载入/);}
  await h.resolve(fresh,status([],{autonomy:{mode:'manual',authority_id:null,branches:[]}}));await newer;
  if(newestFirst){await h.resolve(old,status([],{autonomy:null}));await older;}
  assert.match(autonomyBody(h).textContent,/逐版人工审核/);assert.equal(h.autonomy.open,true);
});

for(const close of ['button','cancel','native'])test('exported autonomy pending status cannot reopen after '+close,async t=>{
  const h=harness(t),opening=h.openAutonomy(),old=h.last('/api/research/status');assert.equal(h.autonomy.open,true);
  if(close==='button')await h.autonomy.querySelector('[data-close]').fire('click');
  else if(close==='cancel'){const event=await h.autonomy.fire('cancel');assert.equal(event.defaultPrevented,true);}
  else h.autonomy.close=()=>{h.autonomy.open=false;};
  if(close==='native')h.autonomy.close();
  await h.resolve(old,status([],{autonomy:{mode:'manual',branches:[]}}));await opening;
  assert.equal(h.autonomy.open,false);assert.equal(autonomyForm(h),null);assert.equal(h.refreshes,0);
});

for(const close of ['button','native'])test('queued old autonomy '+close+' close does not invalidate the reopened panel',async t=>{
  const h=harness(t);await h.readyAutonomy();
  if(close==='button')h.autonomy.querySelector('[data-close]').onclick();else h.autonomy.close();
  await h.readyAutonomy();assert.equal(h.autonomy.open,true);
  const mutation=h.autonomy.querySelector('[data-mode]').fire('click');assert.equal(h.last(autonomyPath).payload.action,'mode');
  await h.resolve(h.last(autonomyPath),{});await h.resolve(h.last('/api/research/status'),status());await mutation;assert.equal(h.refreshes,1);
});

test('researchUI selectionChanged hook invalidates autonomy status across A -> B -> A with identical selection IDs',async t=>{
  const h=harness(t),opening=h.openAutonomy(),old=h.last('/api/research/status');
  h.select('session-b','project-a',false,false);h.ui.selectionChanged();h.select('session-a','project-a',false,false);h.ui.selectionChanged();
  await h.resolve(old,status());await opening;assert.equal(h.autonomy.open,false);
  await h.readyAutonomy();const form=autonomyForm(h),reading=h.autonomy.querySelector('[data-read]').fire('click'),pending=h.last('/api/records/plan-a');
  h.select('session-a','project-b',false,false);h.ui.selectionChanged();h.select('session-a','project-a',false,false);h.ui.selectionChanged();
  await h.resolve(pending,record());await reading;assert.equal(form.elements.content.value,'');assert.equal(h.autonomy.open,false);
  await form.fire('submit');assert.equal(h.requests(autonomyPath).length,0);
});

test('autonomy late status failures are ignored and current failures remain visible',async t=>{
  const h=harness(t),older=h.openAutonomy(),old=h.last('/api/research/status');await h.readyAutonomy();const count=h.toasts.length;
  old.reject(Error('old synthetic failure'));await older;assert.equal(h.toasts.length,count);assert.equal(h.autonomy.open,true);
  const opening=h.openAutonomy();h.last('/api/research/status').reject(Error('current synthetic failure'));await opening;
  assert.match(autonomyBody(h).textContent,/载入失败.*current synthetic failure/);assert.equal(autonomyForm(h),null);
});

test('autonomy original-body edit then identical editback cannot be overwritten by a pending read',async t=>{
  const h=harness(t);await h.readyAutonomy();const content=autonomyForm(h).elements.content;content.value='Original draft';
  const reading=h.autonomy.querySelector('[data-read]').fire('click'),pending=h.last('/api/records/plan-a');
  content.value='Edited draft';await content.fire('input');content.value='Original draft';await content.fire('input');
  await h.resolve(pending,record());await reading;assert.equal(content.value,'Original draft');
  const fresh=h.autonomy.querySelector('[data-read]').fire('click');await h.resolve(h.last('/api/records/plan-a'),record());await fresh;assert.equal(content.value,record().document.content);
});

for(const [name,patch] of [
  ['different record_id',{record_id:'other-record'}],['missing record_id',{record_id:undefined}],
  ['different project_id',{project_id:'other-project'}],['missing project_id',{project_id:undefined}],
  ['different plan_digest',{plan_digest:'other-digest'}],['missing plan_digest',{plan_digest:undefined}],
])test('autonomy original-body copy rejects '+name,async t=>{
  const h=harness(t);await h.readyAutonomy();const content=autonomyForm(h).elements.content;content.value='Preserved draft';
  const reading=h.autonomy.querySelector('[data-read]').fire('click');await h.resolve(h.last('/api/records/plan-a'),record(plan(),patch));await reading;
  assert.equal(content.value,'Preserved draft');assert.match(h.toasts.at(-1),/身份、项目或摘要已变化/);
});

test('autonomy original-body copy requires a selected digest and rejects a digest changed during read',async t=>{
  const h=harness(t),saved=plan();await h.readyAutonomy([{...saved,digest:undefined}]);await h.autonomy.querySelector('[data-read]').fire('click');
  assert.equal(h.requests('/api/records/'+saved.id).length,0);assert.match(h.toasts.at(-1),/方案摘要缺失/);
  await h.readyAutonomy([saved]);const content=autonomyForm(h).elements.content;content.value='Draft';
  const reading=h.autonomy.querySelector('[data-read]').fire('click'),pending=h.last('/api/records/'+saved.id),original=record(saved);saved.digest='changed-digest';
  await h.resolve(pending,original);await reading;assert.equal(content.value,'Draft');assert.match(h.toasts.at(-1),/摘要已变化/);
});

test('autonomy original-body copy requires a selected project even for current-only callers',async t=>{
  const h=harness(t,{legacy:true});await h.readyAutonomy();const content=autonomyForm(h).elements.content;content.value='Draft';
  const reading=h.autonomy.querySelector('[data-read]').fire('click');await h.resolve(h.last('/api/records/plan-a'),record());await reading;
  assert.equal(content.value,'Draft');assert.match(h.toasts.at(-1),/身份、项目或摘要已变化/);
});

test('autonomy original-body latest read wins and plan A -> B -> A invalidates an older read',async t=>{
  const h=harness(t),a=plan('a'),b=plan('b');await h.readyAutonomy([a,b]);const form=autonomyForm(h),button=h.autonomy.querySelector('[data-read]');
  const older=button.fire('click'),old=h.last('/api/records/a'),newer=button.fire('click'),fresh=h.last('/api/records/a');
  await h.resolve(fresh,record(a,{document:{content:'Newest body'}}));await newer;await h.resolve(old,record(a,{document:{content:'Older body'}}));await older;assert.equal(form.elements.content.value,'Newest body');
  const reading=button.fire('click'),pending=h.last('/api/records/a');form.elements.from_plan_id.value='b';await form.elements.from_plan_id.fire('change');form.elements.from_plan_id.value='a';await form.elements.from_plan_id.fire('change');
  await h.resolve(pending,record(a));await reading;assert.equal(form.elements.content.value,'Newest body');
});

for(const action of ['enable','fork'])test('autonomy '+action+' globally guards duplicate forms and other controls through reload',async t=>{
  const h=harness(t);await h.readyAutonomy([plan()],action==='enable'?{autonomy:{mode:'manual',authority_id:null,branches:[]},grants:[{plan_id:'plan-a'}]}:{});
  const form=autonomyForm(h),pause=h.autonomy.querySelector('[data-pause]');form.elements.reason.value='Captured reason';if(action==='fork'){form.elements.title.value='Captured title';form.elements.content.value='Captured content';}
  const submitting=form.fire('submit'),pending=h.last(autonomyPath);await form.fire('submit');await pause.fire('click');await h.openAutonomy();
  assert.equal(pending.payload.action,action);assert.equal(h.requests(autonomyPath).length,1);assert.equal(h.requests('/api/sessions/session-a/interrupt').length,0);assert.equal(h.requests('/api/research/status').length,1);
  assert.ok(h.autonomy.querySelectorAll('button,input,select,textarea').every(control=>control.disabled));form.elements.reason.value='Later reason';assert.equal(pending.payload.reason,'Captured reason');
  await h.resolve(pending,{version:'2'});assert.match(autonomyBody(h).textContent,/正在载入/);await form.fire('submit');await pause.fire('click');assert.equal(h.requests(autonomyPath).length,1);
  await h.resolve(h.last('/api/research/status'),status());await submitting;assert.equal(h.refreshes,1);
});

test('autonomy reentrant API dispatch cannot duplicate a mutation or bypass the global lock by opening',async t=>{
  let h;const reentrant=[];h=harness(t,{onApi:path=>{if(path===autonomyPath)reentrant.push(autonomyForm(h).fire('submit'),h.autonomy.querySelector('[data-pause]').fire('click'),h.openAutonomy());}});await h.readyAutonomy();
  const submitting=autonomyForm(h).fire('submit');await Promise.all(reentrant);assert.equal(h.requests(autonomyPath).length,1);assert.equal(h.requests('/api/research/status').length,1);
  await h.resolve(h.last(autonomyPath),{version:'2'});await h.resolve(h.last('/api/research/status'),status());await submitting;assert.equal(h.refreshes,1);
});

for(const boundary of ['operation','reload'])test('autonomy mutation suppresses stale refresh after A -> B -> A during '+boundary,async t=>{
  const h=harness(t);await h.readyAutonomy();const submitting=h.autonomy.querySelector('[data-mode]').fire('click'),pending=h.last(autonomyPath);
  if(boundary==='reload')await h.resolve(pending,{});
  h.select('session-b','project-a',false);h.ui.selectionChanged();h.select('session-a','project-a',false);h.ui.selectionChanged();
  if(boundary==='operation')await h.resolve(pending,{});else await h.resolve(h.last('/api/research/status'),status());
  await submitting;assert.equal(h.refreshes,0);assert.equal(h.autonomy.open,false);assert.equal(h.requests('/api/research/status').length,boundary==='operation'?1:2);
  await h.readyAutonomy();assert.equal(h.autonomy.open,true);
});

for(const close of ['button','cancel','native'])test('closing autonomy mutation reload via '+close+' prevents reopening and refresh',async t=>{
  const h=harness(t);await h.readyAutonomy();const submitting=h.autonomy.querySelector('[data-mode]').fire('click');await h.resolve(h.last(autonomyPath),{});const reload=h.last('/api/research/status');
  assert.match(autonomyBody(h).textContent,/正在载入/);if(close==='button')await h.autonomy.querySelector('[data-close]').fire('click');else if(close==='cancel')await h.autonomy.fire('cancel');else h.autonomy.close();
  await h.resolve(reload,status());await submitting;assert.equal(h.autonomy.open,false);assert.equal(h.refreshes,0);
});

test('autonomy mutation rechecks original selection after reload even without a selectionChanged notification',async t=>{
  const h=harness(t);await h.readyAutonomy();const submitting=h.autonomy.querySelector('[data-mode]').fire('click');await h.resolve(h.last(autonomyPath),{});
  h.select('session-a','project-b',false);await h.resolve(h.last('/api/research/status'),status());await submitting;
  assert.equal(h.refreshes,0);assert.equal(autonomyForm(h),null);assert.match(autonomyBody(h).textContent,/正在载入/);
});

test('autonomy mutation failure releases its synchronous lock without automatic retry',async t=>{
  const h=harness(t);await h.readyAutonomy();const button=h.autonomy.querySelector('[data-mode]'),submitting=button.fire('click');h.last(autonomyPath).reject(Error('synthetic uncertain mutation'));await submitting;
  assert.equal(h.requests(autonomyPath).length,1);assert.equal(h.requests('/api/research/status').length,1);assert.equal(button.disabled,false);assert.equal(h.refreshes,0);assert.match(h.toasts.at(-1),/synthetic uncertain mutation/);
  const retry=button.fire('click');assert.equal(h.requests(autonomyPath).length,2);await h.resolve(h.last(autonomyPath),{});await h.resolve(h.last('/api/research/status'),status());await retry;
});
