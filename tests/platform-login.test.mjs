import test from 'node:test';
import assert from 'node:assert/strict';
import {loginPanel,createLoginWatcher} from '../static/platform-login.js';
const options={items:['lpai_cli','lpai_sdk','gitlab'].map(kind=>({kind,installed:true})),lpai_agent_url:'https://lpai.example/agent',lpai_url:'https://lpai.example',gitlab_url:'https://gitlab.example/users/sign_in'};
test('first LPAI setup distinguishes independent credentials and keeps tokens outside the page',()=>{
  const html=loginPanel('lpai',options);
  assert.match(html,/lpai_cli/);assert.match(html,/lpai_sdk/);assert.match(html,/不能使用上面的 Agent Token/);
  assert.doesNotMatch(html,/<input/);assert.match(html,/本机独立窗口/);
});

test('only missing authentication receives a login action; network errors do not request credentials',()=>{
  const value={...options,items:[{kind:'lpai_cli',installed:true,login_state:'connected'},{kind:'lpai_sdk',installed:true,login_state:'needs_login'}]};
  const html=loginPanel('lpai',value);
  assert.doesNotMatch(html,/data-platform-login="lpai_cli"/);assert.match(html,/data-platform-login="lpai_sdk"/);assert.match(html,/自动复用本机登录/);
  value.items[1]={kind:'lpai_sdk',installed:true,login_state:'attention',message:'网络连接失败'};
  assert.doesNotMatch(loginPanel('lpai',value),/data-platform-login=/);assert.match(loginPanel('lpai',value),/无需重新填写凭据/);
});

test('local completion automatically verifies without a focus event; close suspends polling and reopen resumes',async()=>{
  let callback=null,open=true,running=true,reads=0,completed=0;
  const watcher=createLoginWatcher({readState:async()=>{reads++;return {items:[{kind:'lpai_sdk',running}]};},onComplete:async()=>{completed++;},isOpen:()=>open,
    schedule:fn=>{callback=fn;return 1;},cancel:()=>{callback=null;}});
  watcher.add('lpai','lpai_sdk');assert.equal(typeof callback,'function');
  await watcher.refresh();assert.equal(completed,0);assert.equal(reads,1);
  open=false;watcher.pause();assert.equal(callback,null);await watcher.refresh();assert.equal(reads,1);
  open=true;running=false;watcher.resume();assert.equal(typeof callback,'function');
  await watcher.refresh();assert.equal(completed,1);assert.equal(callback,null);
  await watcher.refresh();assert.equal(completed,1);
});

test('transient completion verification failure retries without relaunching login',async()=>{
  let completed=0,scheduled=0;
  const watcher=createLoginWatcher({readState:async()=>({items:[{kind:'gitlab',running:false}]}),onComplete:async()=>{if(++completed===1)throw Error('temporary failure');},isOpen:()=>true,schedule:()=>++scheduled,cancel:()=>{}});
  watcher.add('gitlab','gitlab');await watcher.refresh();assert.equal(completed,1);
  await watcher.refresh();assert.equal(completed,2);await watcher.refresh();assert.equal(completed,2);
});
test('GitLab uses official user-driven login and disables absent tooling',()=>{
  assert.match(loginPanel('gitlab',options),/官方 glab/);
  const absent={...options,items:options.items.map(row=>({...row,installed:false}))};
  assert.match(loginPanel('gitlab',absent),/disabled/);assert.match(loginPanel('gitlab',absent),/安装 GitLab 官方 CLI/);
  assert.match(loginPanel('lpai',absent),/scripts\/setup.ps1/);
});
