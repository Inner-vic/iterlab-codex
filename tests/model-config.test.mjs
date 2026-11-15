import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {rolePolicy,defaultModels,sharedEfforts} from '../static/model-config.js';

test('Two model choices map to responsibilities, same model can review',()=>{
  assert.deepEqual(rolePolicy('strong','fast').models,
    {planner:'strong',generator:'strong',critic:'strong',executor:'fast'});
  assert.deepEqual(defaultModels('codex'),{planning:'gpt-6-astra',execution:'gpt-6.1-sol'});
  assert.equal(defaultModels('ept').planning,'azure-gpt-6-astra');
});
test('Effort options come from both actual model capabilities',()=>{
  const catalog=[{model:'a',supportedReasoningEfforts:[{reasoningEffort:'high'},{reasoningEffort:'low'}]},
    {id:'b',supportedReasoningEfforts:[{reasoningEffort:'low'}]}];
  assert.deepEqual(sharedEfforts(catalog,'a','b'),['low']);
  assert.deepEqual(sharedEfforts(catalog,'a','unknown'),[]);
});
test('Connection form has only the two model slots and no legacy fields',()=>{
  const html=readFileSync(new URL('../static/index.html',import.meta.url),'utf8');
  const form=html.split('<form id="settings-form">')[1].split('</form>')[0];
  assert.match(form,/>Codex CLI</);assert.match(form,/>EPT Codex CLI</);
  for(const name of ['planning_model','execution_model','channel'])assert.match(form,new RegExp(`name="${name}"`));
  for(const name of ['effort','profile','capability_scope','role_routing','role_critic','role_generator','model'])assert.doesNotMatch(form,new RegExp(`name="${name}"`));
});
