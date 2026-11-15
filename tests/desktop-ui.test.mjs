import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
const source=readFileSync(new URL('../static/app.js',import.meta.url),'utf8');
function extract(name,next){return source.slice(source.indexOf('function '+name+'('),source.indexOf(next,source.indexOf('function '+name+'(')));}
test('late file response cannot replace a newer file, session or project preview',async()=>{
  const code=extract('openFile','function switchTab');
  const pending=[];let rendered=0;const host={append(){rendered++;},set innerHTML(value){rendered++;}};
  const api=()=>new Promise(resolve=>pending.push(resolve));
  const create=new Function('api','$','icon','esc','document',`let detail={session:{project_id:'p'}},current='s',sessionGeneration=1,filePreviewRequest=0,currentTab='files';const switchTab=()=>{};async ${code};return {openFile,session:()=>{sessionGeneration++;}};`);
  const view=create(api,()=>host,()=>'',x=>x,{createElement:()=>({})});
  const first=view.openFile('alpha');const next=view.openFile('beta');
  pending[1]({text:'beta'});await next;const stable=rendered;
  pending[0]({text:'alpha'});await first;assert.equal(rendered,stable);
  const late=view.openFile('old-session');view.session();pending[2]({text:'old-session'});await late;assert.equal(rendered,stable);
});
test('model save tracks catalog loading, empty and invalid options',()=>{
  const code=extract('updateModelSaveButton','const modelCatalogs');
  const submit={},model=()=>({disabled:false,value:'valid',selectedOptions:[{disabled:false}]});
  const form={elements:{planning_model:model(),execution_model:model()},querySelector:()=>submit};
  const update=new Function('$',code+';return updateModelSaveButton;')(()=>form);
  update();assert.equal(submit.disabled,false);
  form.elements.planning_model.disabled=true;update();assert.equal(submit.disabled,true);
  form.elements.planning_model.disabled=false;form.elements.execution_model.selectedOptions[0].disabled=true;update();assert.equal(submit.disabled,true);
  form.elements.execution_model.value='';update();assert.equal(submit.disabled,true);
});
