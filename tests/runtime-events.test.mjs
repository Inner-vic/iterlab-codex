import test from 'node:test';
import assert from 'node:assert/strict';
import {isProjectRuntimeEvent} from '../static/runtime-events.js';

test('another session in the same project receives runtime observation changes',()=>{
  for(const kind of ['company.space_observed','company.space_observation','company.space_bound','company.unbound','research.execution_location_selected']){
    assert.equal(isProjectRuntimeEvent({kind,session_id:'session-A',payload:{project_id:'project-one'}},'project-one'),true);
    assert.equal(isProjectRuntimeEvent({kind,session_id:'session-A',payload:{project_id:'project-two'}},'project-one'),false);
    assert.equal(isProjectRuntimeEvent({kind,session_id:'session-A',payload:{}},'project-one'),false);
  }
});

test('project refresh does not treat model messages, task writes or names as runtime identity',()=>{
  for(const kind of ['item/agentMessage/delta','turn/completed','company.operation','company.wake_queued'])
    assert.equal(isProjectRuntimeEvent({kind,payload:{project_id:'project-one'}},'project-one'),false);
  assert.equal(isProjectRuntimeEvent({kind:'company.space_observed',payload:{project_id:'project-one'}},null),false);
});
