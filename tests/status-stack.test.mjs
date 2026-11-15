import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

test('collapsed overview keeps actual status and current task count, expanded view restores cards',()=>{
  const source=readFileSync(new URL('../static/app.js',import.meta.url),'utf8');
  const code=source.slice(source.indexOf('function renderStatusStack('),source.indexOf('function syncState('));
  const attributes={},classes=new Set(),toggle={setAttribute:(key,value)=>attributes[key]=value};
  const stack={dataset:{},classList:{toggle:(key,value)=>value?classes.add(key):classes.delete(key)}};
  const strip={hidden:false,querySelector:()=>({textContent:'2'})},summary={},title={textContent:'等待云端结果'};
  const elements={'#research-status-stack':stack,'#toggle-research-stack':toggle,'#session-runs':strip,'#research-stack-summary':summary,'#research-overview [data-title]':title,'#research-overview':{dataset:{tone:'warning'}}};
  let preference='1';
  const render=new Function('$','localStorage','icon',code+';return renderStatusStack;')(selector=>elements[selector],{getItem:()=>preference},()=>'<svg></svg>');
  render();assert.equal(summary.textContent,'等待云端结果 · 2个任务');assert.equal(attributes['aria-expanded'],'false');assert(classes.has('is-collapsed'));
  assert.equal(stack.dataset.tone,'warning');assert.match(toggle.innerHTML,/展开/);
  strip.hidden=true;render();assert.equal(summary.textContent,'等待云端结果');
  title.textContent='结果回收中';render();assert.equal(summary.textContent,'结果回收中');
  preference='0';render();assert.equal(summary.textContent,'研究进展');assert.equal(attributes['aria-expanded'],'true');assert(!classes.has('is-collapsed'));
});
