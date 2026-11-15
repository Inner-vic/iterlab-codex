import test from 'node:test';
import assert from 'node:assert/strict';
import {installSlashMenu} from '../static/slash-menu.js';

test('slash keyboard navigation, filtering, Enter and Escape',()=>{
  class Element{
    constructor(){this.children=[];this.dataset={};this.attributes={};this.hidden=true;}
    replaceChildren(){this.children=[];}
    setAttribute(k,v){this.attributes[k]=v;}
    append(...children){this.children.push(...children);}
  }
  const previous=globalThis.document;globalThis.document={createElement:()=>new Element()};
  try{
    const host=new Element(),input={value:'/'},calls=[];
    const menu=installSlashMenu({input,host,commands:[['/compact','compact'],['/skills','skills'],['/mcp','mcp']],onCommand:c=>calls.push(c)});
    const key=key=>({key,preventDefault(){this.prevented=true;}});
    menu.update();assert.equal(host.children.length,3);
    assert.equal(menu.keydown(key('ArrowDown')),true);
    assert.equal(host.children[1].attributes['aria-selected'],'true');
    menu.keydown(key('Enter'));assert.deepEqual(calls,['/skills']);assert.equal(host.hidden,true);
    input.value='/co';menu.update();assert.equal(host.children.length,1);
    menu.keydown(key('Enter'));assert.equal(calls.at(-1),'/compact');
    input.value='/';menu.update();menu.keydown(key('ArrowUp'));menu.keydown(key('Enter'));
    assert.equal(calls.at(-1),'/mcp');
    menu.update();menu.keydown(key('Escape'));assert.equal(host.hidden,true);
    menu.update();assert.equal(menu.keydown({...key('Enter'),isComposing:true}),false);
  }finally{globalThis.document=previous;}
});
