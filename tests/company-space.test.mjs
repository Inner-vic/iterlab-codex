import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {spacePhaseText,spacePhaseLabel,spaceResourceText,spaceOperationText,spaceRuntimeSummary,spaceObservationText,spaceStopReason,spaceStartDisabled,spaceSSHState,requestManualSpaceStop} from '../static/company-space.js';

const binding=()=>({kind:'lpai',uid:'space-uid',name:'research-space',scope:'team-space',observation:{phase:'Running'}});
function context(){return {bindings:[binding()],connections:{config:{lpai_enabled:true,revision:7}},authority:{revision:7,bindings_hash:'bound-space'},devspace_operation:null,devspace_stop:{operation:null,blockers:[],available:true}};}
function stopHarness(value=context()){
  const calls=[],prompts=[];let session='session-one',accepted=true,ids=0;
  const options={api:async(path,data)=>{calls.push({path,data});return {action:'lpai_stop',state:'pending_approval'};},current:()=>session,loadContext:async()=>value,binding:binding(),confirm:text=>{prompts.push(text);return accepted;},operationId:()=>{ids++;return 'manual-stop-id';}};
  return {options,calls,prompts,session:value=>{session=value;},cancel:()=>{accepted=false;},ids:()=>ids};
}

test('manual stop confirms the exact bound space and sends only the dedicated endpoint',async()=>{
  const h=stopHarness(),result=await requestManualSpaceStop(h.options);
  assert.equal(result.state,'pending_approval');
  assert.equal(h.prompts.length,1);
  for(const text of ['research-space','team-space','space-uid','配置版本 7','整个云空间','停止 Codex 对话是独立操作'])assert.ok(h.prompts[0].includes(text));
  assert.deepEqual(h.calls,[{path:'/api/company/stop-space',data:{session_id:'session-one',uid:'space-uid',id:'manual-stop-id',confirmed:true,expected_authority:{revision:7,bindings_hash:'bound-space'}}}]);
});

test('cancelled manual confirmation sends no stop and does not allocate an operation id',async()=>{
  const h=stopHarness();h.cancel();
  assert.equal(await requestManualSpaceStop(h.options),null);
  assert.equal(h.calls.length,0);assert.equal(h.ids(),0);
});

test('active or unknown training and unresolved start requests block stopping before confirmation',async()=>{
  for(const state of ['running','requested','unknown']){
    const value=context();value.devspace_stop={available:false,blockers:[{kind:'training',state,message:'原训练仍在执行或尚未核对'}]};
    const h=stopHarness(value);
    await assert.rejects(requestManualSpaceStop(h.options),/原训练仍在执行或尚未核对/);
    assert.equal(h.calls.length,0);assert.equal(h.prompts.length,0);
  }
  const value=context();value.devspace_operation={action:'lpai_start',state:'pending_approval'};
  const h=stopHarness(value);
  await assert.rejects(requestManualSpaceStop(h.options),/原启停请求尚未核对/);
  assert.equal(h.calls.length,0);assert.equal(h.prompts.length,0);
});

test('an old card cannot stop a changed binding or another session',async()=>{
  for(const field of ['uid','scope','name']){
    const value=context();value.bindings[0][field]='changed';const h=stopHarness(value);
    await assert.rejects(requestManualSpaceStop(h.options),/绑定已变化/);
    assert.equal(h.calls.length,0);assert.equal(h.prompts.length,0);
  }
  const h=stopHarness();h.options.loadContext=async()=>{h.session('other-session');return context();};
  await assert.rejects(requestManualSpaceStop(h.options),/当前会话已变化/);
  assert.equal(h.calls.length,0);
  const duringConfirmation=stopHarness();duringConfirmation.options.confirm=()=>{duringConfirmation.session('other-session');return true;};
  await assert.rejects(requestManualSpaceStop(duringConfirmation.options),/当前会话已变化/);
  assert.equal(duringConfirmation.calls.length,0);
});

test('missing protection, stopped space and unknown platform state do not permit stopping',()=>{
  const value=context();delete value.devspace_stop;
  assert.match(spaceStopReason(value,binding()),/尚未确认/);
  const stopped=binding();stopped.observation.phase='Stopped';
  assert.match(spaceStopReason(context(),stopped),/无需再次停止/);
  const unknown=binding();unknown.observation.phase='Unknown';
  assert.match(spaceStopReason(context(),unknown),/请先读回状态/);
  assert.equal(spaceStopReason(context(),binding()),'');
});

test('a lost response is surfaced once without retrying the stop endpoint',async()=>{
  const h=stopHarness();h.options.api=async(path,data)=>{h.calls.push({path,data});throw Error('response lost');};
  await assert.rejects(requestManualSpaceStop(h.options),/response lost/);
  assert.equal(h.calls.length,1);assert.equal(h.ids(),1);
});

test('request approval and uncertainty remain separate from the platform observation',()=>{
  const active=binding();
  assert.equal(spacePhaseText(active),'平台记录：运行中 · team-space');
  assert.match(spaceOperationText({action:'lpai_stop',state:'pending_approval'}),/^停止请求：待平台审批/);
  assert.match(spaceOperationText({action:'lpai_start',state:'unknown'}),/^启动请求：结果未知/);
  assert.match(spaceOperationText({action:'lpai_stop',state:'submitted'}),/等待读回确认/);
  active.observation.phase='Stopped';
  assert.equal(spacePhaseText(active,true),'平台读回：已停止 · team-space');
  assert.match(spaceOperationText({action:'lpai_start',state:'pending_approval'}),/^启动请求：待平台审批/);
  for(const state of ['requested','submitted','pending_approval','unknown']){
    const value=context();value.devspace_operation={action:'lpai_stop',state};
    assert.equal(spaceStartDisabled(value,binding()),true);
  }
  assert.equal(spaceStartDisabled(context(),binding()),false);
});

function controlsHarness(value=context(),readbackPhase='Running',startAction=()=>{}){
  const source=readFileSync(new URL('../static/company.js',import.meta.url),'utf8');
  const code=source.slice(source.indexOf('let stopping=false;'),source.indexOf("lpBox.querySelector('[data-unbind]')"));
  const elements=new Map();
  const lpBox={isConnected:true,querySelector(selector){if(!elements.has(selector))elements.set(selector,{disabled:false,textContent:'',addEventListener(event,handler){this[event]=handler;}});return elements.get(selector);}};
  const calls=[];let completeStop,refreshes=0;
  const env={lpBox,lp:value.bindings[0],context:value,sessionId:'session-one',current:()=> 'session-one',
    api:async(path,data)=>{calls.push({path,data});if(path==='/api/company/refresh-space')return {item:{uid:'space-uid',namespace:'team-space',phase:readbackPhase}};if(path==='/api/company/lpai')return {items:[]};return new Promise(resolve=>{completeStop=resolve;});},
    loadContext:async()=>value,confirm:()=>true,crypto:{randomUUID:()=> 'click-stop-id'},render:async()=>{refreshes++;},toast:()=>{},safe:fn=>fn,action:startAction,renderSpaces:()=>{},
    spacePhaseText,spacePhaseLabel,spaceResourceText,spaceOperationText,spaceRuntimeSummary,spaceObservationText,spaceStopReason,spaceStartDisabled,spaceSSHState,requestManualSpaceStop};
  const controls=new Function('env',`const {${Object.keys(env).join(',')}}=env;let spaces=[];${code};return {start:()=>lpBox.querySelector('[data-start]').click(),stop:()=>lpBox.querySelector('[data-stop]').click(),readback:()=>lpBox.querySelector('[data-refresh]').onclick(),discover:()=>lpBox.querySelector('[data-spaces]').onclick()};`)(env);
  return {controls,elements,calls,value,complete:result=>completeStop(result),refreshes:()=>refreshes};
}

test('readback remains read-only and cannot unlock a stop already in flight',async()=>{
  const h=controlsHarness();
  await h.controls.readback();
  assert.deepEqual(h.calls.map(call=>call.path),['/api/company/refresh-space']);
  const stopping=h.controls.stop();
  for(let i=0;i<4;i++)await Promise.resolve();
  assert.equal(h.calls.filter(call=>call.path==='/api/company/stop-space').length,1);
  await h.controls.stop();await h.controls.readback();
  assert.equal(h.elements.get('[data-stop]').disabled,true);
  assert.equal(h.elements.get('[data-start]').disabled,true);
  assert.equal(h.calls.filter(call=>call.path==='/api/company/stop-space').length,1);
  h.complete({action:'lpai_stop',state:'pending_approval'});await stopping;
  assert.match(h.elements.get('[data-space-operation]').textContent,/待平台审批/);
  assert.match(h.elements.get('[data-space-phase]').textContent,/运行中/);
  assert.equal(h.refreshes(),1);
});

test('repeated start clicks and a readback cannot dispatch a second action while confirmation or submission is pending',async()=>{
  const actions=[];let complete;
  const h=controlsHarness(context(),'Running',request=>{actions.push(request);return new Promise(resolve=>{complete=resolve;});});
  const first=h.controls.start();await h.controls.start();await h.controls.readback();await h.controls.stop();
  assert.deepEqual(actions,[{action:'lpai_start'}]);
  assert.equal(h.elements.get('[data-start]').disabled,true);assert.equal(h.elements.get('[data-stop]').disabled,true);
  assert.equal(h.calls.some(call=>call.path==='/api/company/stop-space'),false);
  complete();await first;assert.equal(h.elements.get('[data-start]').disabled,false);
});

test('not-found readback blocks space and SSH actions while keeping discovery available',async()=>{
  const value=context();value.devspace_ssh={verified_at:'previous-verification',port:23456};
  const h=controlsHarness(value,'not_found');await h.controls.readback();
  assert.match(h.elements.get('[data-space-phase]').textContent,/平台未找到/);
  assert.match(h.elements.get('[data-space-stop-reason]').textContent,/当前账号或工作空间.*换绑.*解除绑定/);
  for(const selector of ['[data-start]','[data-stop]','[data-ssh-save]'])assert.equal(h.elements.get(selector).disabled,true);
  const sshStatus=h.elements.get('[data-ssh-status]').textContent;
  assert.match(sshStatus,/已保留 SSH 配置.*先核对或换绑/);
  assert.doesNotMatch(sshStatus,/已验证/);
  assert.deepEqual(value.devspace_ssh,{verified_at:'previous-verification',port:23456});
  assert.equal(h.elements.get('[data-spaces]').disabled,false);
  await h.controls.discover();
  assert.deepEqual(h.calls.map(call=>call.path),['/api/company/refresh-space','/api/company/lpai']);
});

test('a not-found card retains the saved SSH command and cannot verify the old endpoint',async()=>{
  const source=readFileSync(new URL('../static/company.js',import.meta.url),'utf8');
  const code=source.slice(source.indexOf('if(lp){'),source.indexOf('lpBox.hidden='));
  const status={},input={},button={},ssh={querySelector:selector=>selector==='[data-ssh-status]'?status:selector==='input'?input:selector==='[data-ssh-save]'?button:{value:''}};
  const value=context();value.bindings[0].observation.phase='not_found';value.devspace_ssh={verified_at:'previous-verification',port:23456};
  const original=structuredClone(value.devspace_ssh),calls=[];
  const env={lp:value.bindings[0],context:value,sessionId:'session-one',sshDrafts:new Map(),document:{createElement:()=>ssh},lpBox:{querySelector:()=>({before(){}})},spaceSSHState,api:async(...args)=>{calls.push(args);return {verified_at:'new-verification',port:23456};}};
  new Function('env',`const {${Object.keys(env).join(',')}}=env;${code}`)(env);
  assert.equal(input.value,'ssh -p 23456 root@ssh-d.lpai.lixiang.com');
  assert.equal(button.disabled,true);assert.doesNotMatch(status.textContent,/已验证/);
  await button.onclick();
  assert.equal(calls.length,0);assert.equal(button.disabled,true);
  assert.deepEqual(value.devspace_ssh,original);
});
