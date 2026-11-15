import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const app=readFileSync(new URL('../static/app.js',import.meta.url),'utf8');
const extract=(from,to)=>app.slice(app.indexOf(from),app.indexOf(to,app.indexOf(from)));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
function view(known=[]){
  const option=(text,value)=>({text,value,disabled:false});
  function select(){return {value:known[0]?.model||'',disabled:false,options:known.map(m=>option(m.model,m.model)),
    add(item){this.options.push(item);},get selectedOptions(){return this.options.filter(o=>o.value===this.value);}};}
  const submit={},form={dataset:{},elements:{channel:{value:'ept'},planning_model:select(),execution_model:select()},querySelector:()=>submit};
  const label={textContent:''},calls=[],api=()=>{const request=deferred();calls.push(request);return request.promise;};
  const code=extract('async function refreshModelCatalog(','function openSettings(');
  const save=extract('function updateModelSaveButton(','const modelCatalogs=');
  const create=new Function('$','api','state','Option',`
    const modelCatalogs=new Map(),modelRequests=new Map();let modelLoadGeneration=0;
    ${save}
    function populateRoleModels(channel){
      const models=modelCatalogs.get(channel)||[];
      for(const field of ['planning_model','execution_model']){const select=$('#settings-form').elements[field];select.options=models.map(m=>new Option(m.model,m.model));select.value=models[0]?.model||'';}
    }
    const populateEfforts=updateModelSaveButton;
    ${code}
    return refreshModelCatalog;
  `);
  function Option(text,value){return option(text,value);}
  const refresh=create(key=>key==='#settings-form'?form:label,api,{kernel:{settings:{channel:'ept'},models:known}},Option);
  return {refresh,form,label,submit,calls};
}

test('cached model choices are immediately readable, but saving waits for current catalog validation',async()=>{
  const h=view([{model:'first'},{model:'second'}]);
  const pending=h.refresh();assert.equal(h.form.elements.planning_model.disabled,false);assert.equal(h.submit.disabled,true);
  h.form.elements.execution_model.value='second';
  h.calls[0].resolve({models:[{model:'first'},{model:'second'},{model:'new'}]});await pending;
  assert.equal(h.form.elements.execution_model.value,'second');assert.equal(h.submit.disabled,false);
  assert.equal(h.form.elements.planning_model.options.length,3);
});

test('failed refresh preserves readable cached options and cannot save an unverified selection',async()=>{
  const h=view([{model:'existing'}]);const pending=h.refresh();h.calls[0].reject(Error('network unavailable'));await pending;
  assert.equal(h.form.elements.planning_model.disabled,false);assert.equal(h.submit.disabled,true);
  assert.match(h.label.textContent,/读取失败/);
});

test('cold catalog stays disabled until loaded, and a delisted user choice needs explicit reselection',async()=>{
  const cold=view();const loading=cold.refresh();assert.equal(cold.form.elements.planning_model.disabled,true);
  cold.calls[0].resolve({models:Array.from({length:20},(_,i)=>({model:'model-'+i}))});await loading;
  assert.equal(cold.form.elements.planning_model.options.length,20);assert.equal(cold.submit.disabled,false);
  const h=view([{model:'kept'},{model:'removed'}]);const pending=h.refresh();h.form.elements.execution_model.value='removed';
  h.calls[0].resolve({models:[{model:'kept'}]});await pending;
  assert.equal(h.form.elements.execution_model.value,'removed');assert.equal(h.submit.disabled,true);
  assert.equal(h.form.elements.execution_model.selectedOptions[0].disabled,true);
});

test('duplicate platform checks share the same read only within the same revision and session',async()=>{
  const source=readFileSync(new URL('../static/company.js',import.meta.url),'utf8');
  const code=source.slice(source.indexOf('function checkConnection('),source.indexOf('function showHealth('));
  const calls=[],api=(path,data)=>{const d=deferred();calls.push({path,data,...d});return d.promise;};
  const check=new Function('api','const pendingChecks=new Map();'+code+';return checkConnection;')(api);
  const a=check('lpai',1),again=check('lpai',1),other=check('lpai',2),bound=check('lpai',1,'session');
  assert.equal(a,again);assert.equal(calls.length,3);
  for(const call of calls)call.resolve({checks:[]});await Promise.all([a,again,other,bound]);
  const later=check('lpai',1);assert.equal(calls.length,4);calls[3].resolve({checks:[]});await later;
});
