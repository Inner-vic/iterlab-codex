import assert from 'node:assert/strict';
import test from 'node:test';
import {connectionSummary,phaseOf,runLabels} from '../static/session-runs.js';
test('Partial authentication does not pretend training is ready',()=>{
  assert.equal(connectionSummary('lpai',[{kind:'lpai_cli',state:'passed'},{kind:'lpai_sdk',state:'failed'}]),'可监控，训练接口待就绪');
  assert.equal(connectionSummary('gitlab',[{kind:'gitlab_api',state:'passed'}]),'账号访问正常');
  assert.equal(connectionSummary('lpai',[{kind:'lpai_cli',state:'passed',stale:true}]),'连接状态待更新');
});
test('Queued tasks do not show invented numerical progress',()=>{
  assert.equal(phaseOf('Queued'),'waiting');assert.equal(runLabels.queued,'排队中');
  assert.equal(phaseOf('Running'),'running');assert.equal(phaseOf('unknown'),'unknown');
  assert.equal(phaseOf('Succeeded'),'terminal');
});
