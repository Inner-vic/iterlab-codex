import test from 'node:test';
import assert from 'node:assert/strict';
import {homeContent} from '../static/home.js';

test('home keeps a quiet identity and recent research without project forms or extra CTAs',()=>{
  const markup=homeContent([{id:'p',title:'研究项目',workspace:'D:/private'}],[{id:'s',project_id:'p',title:'继续故障诊断',updated_at:'2026-11-05T00:00:00Z'}]);
  assert.match(markup,/让好奇，长出新知/);assert.match(markup,/最近研究/);assert.match(markup,/data-home-session="s"/);
  assert.equal((markup.match(/<button/g)||[]).length,1);
  assert.doesNotMatch(markup,/<select|新会话所属项目|welcome-create|home-examples|D:\/private/);
  assert.match(markup,/继续故障诊断/);
});
test('home recent titles are escaped and empty state does not invent project history',()=>{
  assert.match(homeContent([],[]),/第一项研究/);assert.doesNotMatch(homeContent([],[]),/<button/);
  const markup=homeContent([],[{id:'one',title:'<script>bad</script>'}]);
  assert.match(markup,/&lt;script&gt;/);assert.doesNotMatch(markup,/<script>/);
});
