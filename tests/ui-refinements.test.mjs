import assert from 'node:assert/strict';
import test from 'node:test';
import {resizeComposer,installWorkbenchUI,prepareDialogLayout} from '../static/workbench-ui.js';
import {readFileSync} from 'node:fs';
test('Composer expands to viewport-bound height and then scrolls',()=>{
  globalThis.innerHeight=600;
  const field={style:{},scrollHeight:200};resizeComposer(field);
  assert.equal(field.style.height,'200px');assert.equal(field.style.overflowY,'hidden');
  field.scrollHeight=900;resizeComposer(field);assert.equal(field.style.height,'270px');assert.equal(field.style.overflowY,'auto');
  field.scrollHeight=24;resizeComposer(field);assert.equal(field.style.height,'64px');
});
test('Repeated context and draft chrome is removed; file expansion and slash compaction remain',()=>{
  const html=readFileSync(new URL('../static/index.html',import.meta.url),'utf8');
  assert.doesNotMatch(html,/id="draft-status"|data-tab="context"|id="connect"/);
  assert.match(html,/id="expand-files"/);assert.match(html,/id="file-dialog"/);
  const app=readFileSync(new URL('../static/app.js',import.meta.url),'utf8');
  assert.match(app,/refreshModelCatalog\(\)/);assert.match(app,/\/compact/);
  assert.doesNotMatch(app,/\$\('#draft-status'\)/);
});

function element(tag='div'){
  const node={tagName:tag.toUpperCase(),className:'',style:{},children:[],listeners:{},open:false,
    get childNodes(){return this.children;},get firstChild(){return this.children[0];},
    append(...nodes){for(const child of nodes){if(child.parentElement){const siblings=child.parentElement.children;siblings.splice(siblings.indexOf(child),1);}this.children.push(child);child.parentElement=this;}},
    prepend(child){if(child.parentElement){const siblings=child.parentElement.children;siblings.splice(siblings.indexOf(child),1);}this.children.unshift(child);child.parentElement=this;},
    contains(child){return child===this||this.children.some(node=>node.contains(child));},
    setAttribute(){},addEventListener(name,fn){this.listeners[name]=fn;},
    querySelector(selector){for(const child of this.children){if(selector==='button'&&child.tagName==='BUTTON'||selector.startsWith('.')&&child.className.split(' ').includes(selector.slice(1)))return child;const match=child.querySelector(selector);if(match)return match;}return null;},
    showModal(){this.open=true;},close(){this.open=false;this.listeners.close?.();}};
  node.classList={add(...classes){node.className=[...new Set([...node.className.split(' ').filter(Boolean),...classes])].join(' ');}};
  return node;
}

test('Separate header handles existing and new dialogs; file enlargement preserves nodes',()=>{
  const original={document:globalThis.document,window:globalThis.window,MutationObserver:globalThis.MutationObserver};
  let notify;
  const body=element(),dock=element(),expanded=element(),fileDialog=element(),expand=element(),prompt={...element(),scrollHeight:24};
  const map={'#file-dialog':fileDialog,'#tab-files':dock,'#files-expanded':expanded,'#expand-files':expand,'#prompt':prompt};
  const header=element(),approvalHeader=element(),existing=element('button');header.className='dialog-header';approvalHeader.className='dialog-header';header.append(existing);existing.onclick=()=>fileDialog.close();
  fileDialog.id='file-dialog';fileDialog.append(header,expanded);
  const approval=element('dialog');approval.id='approval-dialog';approval.append(approvalHeader);const dialogs=[fileDialog,approval];body.append(fileDialog,approval);
  globalThis.document={body,createElement:element,querySelector:selector=>map[selector],querySelectorAll:()=>dialogs};
  globalThis.window={addEventListener(){}};globalThis.innerWidth=1000;globalThis.innerHeight=800;
  globalThis.MutationObserver=class{constructor(callback){notify=callback;}observe(){}};
  try{
    const child=element();child.onclick=()=>42;dock.append(child);
    installWorkbenchUI();assert.equal(header.firstChild,existing);assert.equal(approvalHeader.children.length,0);assert.equal(body.children.length,2);
    assert.equal(fileDialog.children[0],header);assert.equal(expanded.parentElement,fileDialog.children[1]);assert.equal(fileDialog.children[1].className,'dialog-body');
    const lateHeader=element(),late=element('dialog');lateHeader.className='dialog-header';late.open=true;late.append(lateHeader);dialogs.push(late);notify();
    assert.equal(lateHeader.children.length,1);lateHeader.firstChild.onclick();assert.equal(late.open,false);notify();assert.equal(lateHeader.children.length,1);
    expand.onclick();assert.equal(fileDialog.open,true);assert.equal(expanded.firstChild,child);assert.equal(dock.firstChild,undefined);
    existing.onclick();assert.equal(fileDialog.open,false);assert.equal(dock.firstChild,child);assert.equal(child.onclick(),42);assert.equal(expanded.firstChild,undefined);
  }finally{Object.assign(globalThis,original);}
});

test('Lifting a form header preserves input, submission and a single scroll body',()=>{
  const original=globalThis.document;globalThis.document={createElement:element};
  try{
    const dialog=element('dialog'),form=element('form'),header=element(),input=element('input');header.className='dialog-header';input.value='unsaved';form.onsubmit=()=>input.value;form.append(header,input);dialog.append(form);
    const body=prepareDialogLayout(dialog);
    assert.deepEqual(dialog.children,[header,body]);assert.equal(body.firstChild,form);assert.equal(form.firstChild,input);assert.equal(form.onsubmit(),'unsaved');
    assert.equal(prepareDialogLayout(dialog),body);assert.equal(dialog.children.length,2);
  }finally{globalThis.document=original;}
});
