import assert from 'node:assert/strict';
import test from 'node:test';
import {createConversationView} from '../static/conversation.js';
import {markdown as renderMarkdown,escapeHTML} from '../static/markdown.js';

// A local DOM with real parent/child identity, selection invalidation on removal,
// HTML parsing, and deterministic layout. No browser, service, or package needed.
const decode=value=>String(value).replace(/&(#\d+|#x[\da-f]+|amp|lt|gt|quot|apos);/gi,(_,name)=>name[0]==='#'?String.fromCodePoint(name[1].toLowerCase()==='x'?parseInt(name.slice(2),16):Number(name.slice(1))):({amp:'&',lt:'<',gt:'>',quot:'"',apos:"'"}[name]));
const dataName=name=>'data-'+name.replace(/[A-Z]/g,c=>'-'+c.toLowerCase());
class NodeStub {
  constructor(doc,type){this.ownerDocument=doc;this.nodeType=type;this.parentNode=null;this.childNodes=[];}
  get firstChild(){return this.childNodes[0]||null;}
  get nextSibling(){if(!this.parentNode)return null;return this.parentNode.childNodes[this.parentNode.childNodes.indexOf(this)+1]||null;}
  get children(){return this.childNodes.filter(node=>node.nodeType===1);}
  get lastElementChild(){return this.children.at(-1)||null;}
  get isConnected(){return this.nodeType===9||!!this.parentNode?.isConnected;}
  contains(node){for(let current=node;current;current=current.parentNode)if(current===this)return true;return false;}
  append(...nodes){for(const node of nodes)this.insertBefore(typeof node==='string'?this.ownerDocument.createTextNode(node):node,null);}
  insertBefore(node,before){
    if(node===before)return node;
    assert.ok(before===null||before.parentNode===this);
    if(node.parentNode)node.parentNode.removeChild(node);
    const index=before===null?this.childNodes.length:this.childNodes.indexOf(before);
    this.childNodes.splice(index,0,node);node.parentNode=this;
    this.ownerDocument?.moves.push({parent:this,node});return node;
  }
  removeChild(node){
    const index=this.childNodes.indexOf(node);assert.notEqual(index,-1);
    const selection=this.ownerDocument?.selection;
    if(selection)for(const end of ['anchor','focus'])if(node.contains(selection[end+'Node'])){selection[end+'Node']=this;selection[end+'Offset']=index;}
    this.childNodes.splice(index,1);node.parentNode=null;return node;
  }
  replaceChildren(...nodes){while(this.firstChild)this.removeChild(this.firstChild);this.append(...nodes);}
  get textContent(){return this.nodeType===3?this.value:this.childNodes.map(node=>node.textContent).join('');}
  set textContent(value){
    this.ownerDocument?.textWrites.push(this);
    if(this.nodeType===3)this.value=String(value);
    else this.replaceChildren(...(String(value)?[this.ownerDocument.createTextNode(String(value))]:[]));
  }
}
class ElementStub extends NodeStub {
  constructor(doc,tag){
    super(doc,1);this.tagName=tag.toUpperCase();this.attributes=new Map();this.onclick=null;this.listeners=new Map();
    this.dataset=new Proxy({},{get:(_,name)=>this.getAttribute(dataName(name))??undefined,set:(_,name,value)=>{this.setAttribute(dataName(name),value);return true;}});
  }
  setAttribute(name,value){this.attributes.set(name,String(value));}
  getAttribute(name){return this.attributes.get(name)??null;}
  get className(){return this.getAttribute('class')||'';}
  set className(value){this.setAttribute('class',value);}
  get hidden(){return this.attributes.has('hidden');}
  set hidden(value){if(value)this.setAttribute('hidden','');else this.attributes.delete('hidden');}
  get open(){return this.attributes.has('open');}
  set open(value){if(value)this.setAttribute('open','');else this.attributes.delete('open');}
  matches(selector){
    const attr=/^\[([^=\]]+)(?:=["']?([^"'\]]*)["']?)?\]$/.exec(selector);
    if(attr)return this.attributes.has(attr[1])&&(attr[2]===undefined||this.getAttribute(attr[1])===attr[2]);
    if(selector.startsWith('.'))return selector.slice(1).split('.').every(name=>this.className.split(/\s+/).includes(name));
    return this.tagName.toLowerCase()===selector.toLowerCase();
  }
  querySelectorAll(selector){
    const result=[];
    const walk=node=>{for(const child of node.children){if(child.matches(selector))result.push(child);walk(child);}};
    walk(this);return result;
  }
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  closest(selector){for(let node=this;node?.nodeType===1;node=node.parentNode)if(node.matches(selector))return node;return null;}
  addEventListener(type,callback){if(!this.listeners.has(type))this.listeners.set(type,[]);this.listeners.get(type).push(callback);}
  click(){
    const event={target:this,defaultPrevented:false,stopped:false,preventDefault(){this.defaultPrevented=true;},stopPropagation(){this.stopped=true;}};
    for(let node=this;node?.nodeType===1;node=node.parentNode){node.onclick?.(event);for(const callback of node.listeners.get('click')||[])callback(event);if(event.stopped)break;}
    return event;
  }
  get innerHTML(){return this.childNodes.map(serialize).join('');}
  set innerHTML(html){
    this.ownerDocument.htmlWrites.push(this);this.replaceChildren();const stack=[this];
    for(const token of String(html).match(/<!--[\s\S]*?-->|<\/?[\w:-]+\b[^>]*>|[^<]+|</g)||[]){
      if(token.startsWith('<!--'))continue;
      if(token.startsWith('</')){stack.pop();continue;}
      if(token.startsWith('<')&&token.length>1){
        const tag=/^<([\w:-]+)/.exec(token)[1],node=this.ownerDocument.createElement(tag);
        const attributes=token.slice(tag.length+1,token.length-1).replace(/\/$/,'');
        for(const match of attributes.matchAll(/([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s]+)))?/g))node.setAttribute(match[1],decode(match[2]??match[3]??match[4]??''));
        stack.at(-1).append(node);if(!['br','hr','img','input','meta','link'].includes(tag)&&!token.endsWith('/>'))stack.push(node);
      }else stack.at(-1).append(this.ownerDocument.createTextNode(decode(token)));
    }
  }
  getBoundingClientRect(){const top=this.ownerDocument.top(this),height=this.ownerDocument.height(this);return {top,bottom:top+height,height,left:0,right:700,width:700};}
}
function serialize(node){
  if(node.nodeType===3)return escapeHTML(node.textContent);
  const tag=node.tagName.toLowerCase(),attrs=[...node.attributes].map(([name,value])=>` ${name}="${escapeHTML(value)}"`).join('');
  return `<${tag}${attrs}>${node.innerHTML}${['br','hr','img','input','meta','link'].includes(tag)?'':`</${tag}>`}`;
}
class HostStub extends ElementStub {
  constructor(doc){super(doc,'div');this.clientHeight=200;this.clientTop=0;this._scrollTop=0;}
  get scrollHeight(){return this.children.reduce((sum,node)=>sum+this.ownerDocument.height(node),0);}
  get scrollTop(){return this._scrollTop;}
  set scrollTop(value){this._scrollTop=Math.max(0,Math.min(Number(value),Math.max(0,this.scrollHeight-this.clientHeight)));this.ownerDocument.scrollWrites.push(this._scrollTop);}
}
class DocumentStub extends NodeStub {
  constructor(){
    super(null,9);this.ownerDocument=this;this.htmlWrites=[];this.textWrites=[];this.moves=[];this.scrollWrites=[];
    this.selection={rangeCount:0,anchorNode:null,focusNode:null,anchorOffset:0,focusOffset:0,restores:0,setBaseAndExtent(a,ao,f,fo){this.rangeCount=1;this.anchorNode=a;this.anchorOffset=ao;this.focusNode=f;this.focusOffset=fo;this.restores++;}};
    this.host=new HostStub(this);this.append(this.host);
  }
  createElement(tag){return new ElementStub(this,tag);}
  createTextNode(value){const node=new NodeStub(this,3);node.value=String(value);return node;}
  getSelection(){return this.selection;}
  height(node){
    if(node===this.host)return this.host.clientHeight;
    if(node.hidden)return 0;
    if(node._height!==undefined)return typeof node._height==='function'?node._height():node._height;
    if(node.parentNode===this.host){
      if(node.matches('.turn-activity')){const body=node.querySelector('.activity-body');return 32+(body.hidden?0:body.children.reduce((sum,child)=>sum+this.height(child),0));}
      return 90+Math.ceil((node.querySelector('.message-body')?.textContent.length||0)/24)*20;
    }
    if(node.parentNode?.matches?.('.activity-body'))return 40+Math.ceil(node.textContent.length/40)*12;
    return 20;
  }
  top(node){
    if(node===this.host)return 0;
    const parent=node.parentNode;if(!parent||parent===this)return 0;
    const preceding=parent.children.slice(0,parent.children.indexOf(node)).reduce((sum,child)=>sum+this.height(child),0);
    if(parent===this.host)return preceding-this.host.scrollTop;
    if(node.matches('.activity-body'))return this.top(parent)+32;
    if(parent.matches('.activity-body'))return this.top(parent.parentNode)+32+preceding;
    return this.top(parent);
  }
  clearWrites(){this.htmlWrites=[];this.textWrites=[];this.moves=[];this.scrollWrites=[];}
}
function frames(){
  let next=0;const waiting=new Map(),all=new Map(),cancelled=[];
  return {waiting,all,cancelled,request(callback){const id=++next;waiting.set(id,callback);all.set(id,callback);return id;},cancel(id){cancelled.push(id);waiting.delete(id);},flush(){const callbacks=[...waiting.values()];waiting.clear();for(const callback of callbacks)callback();},fire(id){all.get(id)?.();}};
}
const timestamp=seconds=>new Date(Date.UTC(2026,9,30)+seconds*1000).toISOString();
const message=(id,options={})=>({id,role:'assistant',kind:'agentMessage',text:id,status:'completed',turn_id:'turn',created_at:timestamp(1),...options});
const record=(id,options={})=>({id,session_id:'s1',kind:'plan',title:'研究方案',summary:'记录摘要',version:'v1',created_at:timestamp(5),...options});
const detail=(messages=[],records=[],session={})=>({messages,records,session:{id:'s1',thread_id:'thread',active_turn:null,project_title:'独立研究',workspace:'D:/workspace',...session}});
const clone=value=>structuredClone(value);
function harness(options={}){
  const doc=new DocumentStub(),clock=frames(),calls=[],opened=[];
  const view=createConversationView({host:doc.host,markdown:input=>{calls.push(input);return renderMarkdown(input);},escapeHTML,icon:name=>`<svg aria-hidden="true"><path d="${name}"></path></svg>`,date:value=>'date:'+value,onOpenRecord:id=>opened.push(id),requestFrame:clock.request,cancelFrame:clock.cancel,...options});
  return {doc,host:doc.host,clock,calls,opened,view,outer:id=>doc.host.children.find(node=>node.dataset.message===id),inner:id=>doc.host.querySelectorAll('[data-item]').find(node=>node.dataset.item===id)};
}

const researchId='a'.repeat(32),foreignId='b'.repeat(32);

test('history pages keep the existing reading anchor and do not insert old milestones prematurely',()=>{
  let loads=0;const h=harness({onLoadOlder:()=>{loads++;}});
  const current=detail([message('now',{text:'current',created_at:timestamp(20)})],[record(researchId,{created_at:timestamp(1)})]);
  current.history={has_more:true,start_at:timestamp(15),start_position:120};h.view.render(current,'s1');
  assert.equal(h.outer(researchId),undefined);
  const button=h.host.querySelector('.history-loader').querySelector('button');button.click();assert.equal(loads,1);
  const node=h.outer('now');current.messages.unshift(message('older',{turn_id:'older-turn',created_at:timestamp(10)}));current.history.start_at=timestamp(5);h.view.render(current,'s1');
  assert.equal(h.outer('now'),node);assert.ok(h.outer('older'));assert.equal(h.outer(researchId),undefined);
  current.history.has_more=false;h.view.render(current,'s1');assert.equal(h.host.querySelector('.history-loader'),null);assert.ok(h.outer(researchId));
});

test('internal role handoffs are absent even from an older cached response',()=>{
  const h=harness();
  h.view.render(detail([message('handoff',{role:'system',kind:'role_handoff',text:'正在整理结果并分析下一步。',label:'private role mapping'})]),'s1');
  assert.equal(h.outer('handoff'),undefined);
  assert(!h.host.textContent.includes('private role mapping'));
});
test('matches grouping, chronology, compaction, current-session milestones, and tool labels',()=>{
  const h=harness(),messages=[
    message('envelope',{role:'user',kind:'method_envelope',created_at:timestamp(0)}),
    message('user',{role:'user',text:'问题',created_at:timestamp(1)}),
    message('early',{text:'过程说明',created_at:timestamp(2)}),
    message('tool',{role:'tool',kind:'dynamicToolCall',text:'research_run',created_at:timestamp(3)}),
    message('output',{role:'tool',kind:'output',created_at:timestamp(4)}),
    message('tool-envelope',{role:'tool',kind:'method_envelope',created_at:timestamp(4)}),
    message('compact',{role:'tool',kind:'contextCompaction',text:'已压缩 <安全>',created_at:timestamp(6)}),
    message('orphan',{role:'tool',turn_id:null,created_at:timestamp(7)}),
    message('final',{text:'最终回答',created_at:timestamp(8)})
  ];
  h.view.render(detail(messages,[record(researchId),record(foreignId,{session_id:'another',created_at:timestamp(0)})]),'s1');
  assert.deepEqual(h.host.children.map(node=>node.dataset.message),['user','activity:turn',researchId,'compact','final']);
  assert.deepEqual(h.outer('activity:turn').querySelector('.activity-body').children.map(node=>node.dataset.item),['early','tool']);
  assert.equal(h.inner('tool').querySelector('summary').children.at(-2).textContent,'执行受控实验');
  assert.equal(h.inner('tool').querySelector('time').textContent,'date:'+timestamp(3));
  assert.equal(h.outer('activity:turn').querySelector('.activity-heading').children[1].textContent,'已处理 6秒 · 1 项工具活动');
  assert.equal(h.outer('activity:turn').querySelector('.activity-body').hidden,true);
  assert.equal(h.outer('compact').className,'message compaction-note');
  assert.equal(h.outer('compact').lastElementChild.textContent,'已压缩 <安全>');
  assert.equal(h.outer(researchId).querySelector('small').textContent,'研究方案 · v1');
  h.outer(researchId).querySelector('button').click();assert.deepEqual(h.opened,[researchId]);
  assert.deepEqual(h.calls,['问题','过程说明','最终回答']);
});

test('all existing tool kind and dynamic research name labels are retained',()=>{
  const cases=[
    ['commandExecution','pwd','运行项目命令'],['fileChange','x','修改项目文件'],['mcpToolCall','x','调用扩展工具'],['plan','x','更新执行计划'],['other','x','执行工具'],
    ...Object.entries({research_snapshot:'读取研究记录',research_record:'保存研究记录',research_status:'核对研究状态',research_focus:'更新研究焦点',research_write:'写入研究代码',research_run:'执行受控实验',company_context:'核对公司连接',unknown:'调用研究工具'}).map(([name,label])=>['dynamicToolCall',name,label])
  ];
  const h=harness();h.view.render(detail(cases.map(([kind,text],i)=>message('tool'+i,{role:'tool',kind,text,created_at:timestamp(i+1)}))),'s1');
  cases.forEach(([,text,label],i)=>{const row=h.inner('tool'+i);assert.equal(row.querySelector('summary').children.at(-2).textContent,label);assert.equal(row.querySelector('pre').textContent,text);});
  assert.equal(h.calls.length,0);
});

test('unchanged snapshots retain message, record, tool, Markdown descendants, expansion, and selection',()=>{
  const h=harness(),snapshot=detail([
    message('user',{role:'user',text:'用户选择的文字',created_at:timestamp(0)}),message('early'),
    message('tool',{role:'tool',kind:'commandExecution',text:'echo <safe>',created_at:timestamp(2)}),message('final',{created_at:timestamp(4)})
  ],[record(researchId)],{active_turn:'turn'});
  h.view.render(snapshot,'s1');
  const nodes=[...h.host.children],body=h.outer('user').querySelector('.message-body'),paragraph=body.firstChild,text=paragraph.firstChild;
  const tool=h.inner('tool'),details=tool.querySelector('details'),summary=tool.querySelector('summary'),pre=tool.querySelector('pre'),comment=h.inner('early'),commentParagraph=comment.firstChild;
  details.open=true;h.outer('activity:turn').querySelector('button').click();
  h.doc.selection.setBaseAndExtent(text,2,text,6);h.doc.selection.restores=0;
  const parseCount=h.calls.length;h.doc.clearWrites();h.view.render(clone(snapshot),'s1');
  assert.deepEqual(h.host.children,nodes);nodes.forEach((node,i)=>assert.equal(h.host.children[i],node));
  assert.equal(h.outer('user').querySelector('.message-body'),body);assert.equal(body.firstChild,paragraph);
  assert.equal(h.inner('early'),comment);assert.equal(comment.firstChild,commentParagraph);
  assert.equal(h.inner('tool'),tool);assert.equal(tool.querySelector('details'),details);assert.equal(tool.querySelector('summary'),summary);assert.equal(tool.querySelector('pre'),pre);
  assert.equal(details.open,true);assert.equal(h.outer('activity:turn').querySelector('.activity-body').hidden,true);
  assert.equal(h.outer('activity:turn').querySelector('button').getAttribute('aria-expanded'),'false');
  assert.equal(h.calls.length,parseCount);assert.deepEqual(h.doc.htmlWrites,[]);assert.deepEqual(h.doc.textWrites,[]);assert.deepEqual(h.doc.moves,[]);
  assert.equal(h.doc.selection.anchorNode,text);assert.equal(h.doc.selection.focusOffset,6);assert.equal(h.doc.selection.restores,0);
});

test('snapshot status, duration, and changed tool text update without rebuilding tool details',()=>{
  const h=harness(),snapshot=detail([
    message('tool',{role:'tool',kind:'dynamicToolCall',text:'research_run',created_at:timestamp(1)}),message('final',{created_at:timestamp(70)})
  ],[],{active_turn:'turn'});
  h.view.render(snapshot,'s1');const group=h.outer('activity:turn'),tool=h.inner('tool'),details=tool.querySelector('details'),summary=tool.querySelector('summary'),glyph=summary.firstChild;
  details.open=true;group.querySelector('button').click();
  const next=clone(snapshot);next.session.active_turn=null;next.messages[0].text='research_status';h.doc.clearWrites();h.view.render(next,'s1');
  assert.equal(h.outer('activity:turn'),group);assert.equal(h.inner('tool'),tool);assert.equal(tool.querySelector('details'),details);assert.equal(details.open,true);
  assert.equal(tool.querySelector('summary'),summary);assert.equal(summary.firstChild,glyph);assert.equal(summary.children.at(-2).textContent,'核对研究状态');
  assert.equal(tool.querySelector('pre').textContent,'research_status');assert.equal(group.className,'message turn-activity');
  assert.equal(group.querySelector('button').children[1].textContent,'已处理 1分9秒 · 1 项工具活动');
  assert.equal(group.querySelector('.activity-body').hidden,true);assert.equal(group.querySelector('small').textContent,'展开过程');
  assert.ok(!h.doc.htmlWrites.includes(tool));
  group.querySelector('button').click();h.view.render(clone(next),'s1');assert.equal(group.querySelector('.activity-body').hidden,false);
});

test('keyed insertion and reorder retain nodes; a final assistant moves to activity without reparsing its Markdown',()=>{
  const h=harness(),first=message('a',{text:'相同的说明',created_at:timestamp(1)}),t1=message('t1',{role:'tool',kind:'commandExecution',created_at:timestamp(2)}),t2=message('t2',{role:'tool',kind:'fileChange',created_at:timestamp(3)});
  h.view.render(detail([first,t1,t2]),'s1');const group=h.outer('activity:turn'),one=h.inner('t1'),two=h.inner('t2');one.querySelector('details').open=true;
  const oldParses=h.calls.length,next=detail([clone(t2),clone(first),clone(t1),message('b',{text:'新的最终回答',created_at:timestamp(6)})]);
  h.view.render(next,'s1');
  assert.equal(h.outer('activity:turn'),group);assert.equal(h.inner('t1'),one);assert.equal(h.inner('t2'),two);assert.equal(one.querySelector('details').open,true);
  assert.deepEqual(group.querySelector('.activity-body').children.map(row=>row.dataset.item),['t2','a','t1']);assert.equal(h.outer('a'),undefined);
  assert.deepEqual(h.calls.slice(oldParses),['新的最终回答']);
  assert.deepEqual(h.host.children.map(row=>row.dataset.message),['activity:turn','b']);
});

test('outer keys include role as well as ID, retaining a colliding message and research record',()=>{
  const h=harness(),snapshot=detail([message(researchId,{turn_id:null})],[record(researchId)]);h.view.render(snapshot,'s1');
  const nodes=[...h.host.children];assert.equal(nodes.length,2);assert.notEqual(nodes[0],nodes[1]);
  h.view.render(clone(snapshot),'s1');assert.equal(h.host.children[0],nodes[0]);assert.equal(h.host.children[1],nodes[1]);
  assert.equal(h.calls.length,1);assert.equal(nodes[1].className,'message research-milestone');
});

test('research references are safely replaced only in visible Markdown; labels refresh only affected bodies',()=>{
  const h=harness(),snapshot=detail([
    message('early',{text:'`'+researchId+'`',created_at:timestamp(1)}),
    message('final',{text:'阅读 `'+researchId+'` 和 `'+foreignId+'`\n\n<img src=x onerror=alert(1)>',created_at:timestamp(6)}),
    message('other',{turn_id:'other',text:'不变的回答',created_at:timestamp(7)})
  ],[record(researchId,{title:'[<img src=x onerror=alert(1)>]',summary:'<script>plain</script>'}),record(foreignId,{session_id:'foreign',title:'另一会话'})]);
  h.view.render(snapshot,'s1');
  const body=h.outer('final').querySelector('.message-body'),link=body.querySelector('[data-record]');
  assert.equal(link.dataset.record,researchId);assert.equal(link.textContent,'<img src=x onerror=alert(1)> v1');
  assert.equal(body.querySelector('img'),null);assert.equal(body.querySelectorAll('[data-record]')[1].dataset.record,foreignId);
  assert.equal(h.inner('early').querySelector('[data-record]'),null);assert.equal(h.inner('early').querySelector('code').textContent,researchId);
  assert.equal(h.outer(researchId).querySelector('.milestone-content').children[2].textContent,'<script>plain</script>');assert.equal(h.outer(researchId).querySelector('script'),null);
  const event=link.click();assert.equal(event.defaultPrevented,true);assert.equal(event.stopped,true);assert.deepEqual(h.opened,[researchId]);
  const oldParses=h.calls.length,next=clone(snapshot),oldOther=h.outer('other').querySelector('.message-body').firstChild;next.records[0].title='新标题';h.view.render(next,'s1');
  assert.equal(h.calls.length,oldParses+1);assert.equal(h.outer('other').querySelector('.message-body').firstChild,oldOther);assert.equal(h.outer('final').querySelector('[data-record]').textContent,'新标题 v1');
});

test('deltas batch by ID in one frame and touch only affected message bodies, without reading historical messages',()=>{
  const h=harness(),history=Array.from({length:180},(_,i)=>message('old'+i,{turn_id:'old-turn'+i,created_at:timestamp(i)}));
  const early=message('early',{text:'过程',created_at:timestamp(181)}),tool=message('tool',{role:'tool',kind:'commandExecution',created_at:timestamp(182)}),final=message('final',{text:'回答',created_at:timestamp(183)}),snapshot=detail([...history,early,tool,final],[],{active_turn:'turn'});
  h.view.render(snapshot,'s1');const oldNodes=history.map(item=>h.outer(item.id).querySelector('.message-body').firstChild),finalBody=h.outer('final').querySelector('.message-body'),earlyBody=h.inner('early'),details=h.inner('tool').querySelector('details');details.open=true;
  snapshot.messages=new Proxy([],{get(){throw Error('delta must not read the historical message collection');}});
  const before=h.calls.length;h.doc.clearWrites();
  final.text+='一';final.status='streaming';h.view.queueDelta(final);final.text+='二';h.view.queueDelta(final);
  early.text+='一';h.view.queueDelta(early);early.text+='二';h.view.queueDelta(early);
  h.view.queueDelta({...final});h.view.queueDelta(message('unknown'));h.view.queueDelta(null);
  assert.equal(h.clock.waiting.size,1);assert.equal(h.calls.length,before);assert.equal(finalBody.textContent,'回答');
  h.clock.flush();assert.deepEqual(h.calls.slice(before),['回答一二','过程一二']);assert.deepEqual(h.doc.htmlWrites,[finalBody,earlyBody]);assert.deepEqual(h.doc.textWrites,[]);
  assert.equal(h.outer('final').className,'message assistant streaming');assert.equal(earlyBody.className,'activity-comment');
  history.forEach((item,i)=>assert.equal(h.outer(item.id).querySelector('.message-body').firstChild,oldNodes[i]));assert.equal(details.open,true);
  assert.ok(!h.doc.moves.some(move=>move.parent===h.host||move.parent===h.outer('activity:turn').querySelector('.activity-body')));
  h.doc.clearWrites();h.view.queueDelta(final);h.clock.flush();assert.equal(h.calls.length,before+2);assert.deepEqual(h.doc.htmlWrites,[]);
});

test('full snapshots, session switches, and reset cancel pending frames and reject stale item objects',()=>{
  const h=harness(),old=message('same',{text:'old'}),snapshot=detail([old]);h.view.render(snapshot,'s1');
  old.text+=' delta';h.view.queueDelta(old);const oldFrame=[...h.clock.waiting.keys()][0];
  const fresh=message('same',{text:'snapshot'});h.view.render(detail([fresh]),'s1');assert.ok(h.clock.cancelled.includes(oldFrame));
  h.view.queueDelta(old);assert.equal(h.clock.waiting.size,0);
  fresh.text+=' new';h.view.queueDelta(fresh);h.clock.fire(oldFrame);assert.equal(h.outer('same').querySelector('.message-body').textContent,'snapshot');assert.equal(h.clock.waiting.size,1);
  h.clock.flush();assert.equal(h.outer('same').querySelector('.message-body').textContent,'snapshot new');
  fresh.text+=' pending';h.view.queueDelta(fresh);const switchFrame=[...h.clock.waiting.keys()][0],oldNode=h.outer('same');
  const other=message('same',{text:'another session'});h.view.render(detail([other],[],{id:'s2'}),'s2');assert.notEqual(h.outer('same'),oldNode);
  h.view.queueDelta(fresh);h.clock.fire(switchFrame);assert.equal(h.outer('same').querySelector('.message-body').textContent,'another session');
  other.text+=' pending';h.view.queueDelta(other);const resetFrame=[...h.clock.waiting.keys()][0];h.view.reset();assert.equal(h.host.children.length,0);assert.equal(h.host.scrollTop,0);
  h.view.queueDelta(other);h.clock.fire(resetFrame);assert.equal(h.clock.waiting.size,0);assert.equal(h.host.children.length,0);
  h.view.render(detail([message('same',{text:'after reset'})]),'s1');h.view.queueDelta(old);assert.equal(h.clock.waiting.size,0);assert.equal(h.outer('same').querySelector('.message-body').textContent,'after reset');
});

test('near-bottom snapshots autoscroll; reading keeps its keyed anchor through earlier growth and lower appends',()=>{
  const h=harness(),snapshot=detail(Array.from({length:12},(_,i)=>message('m'+i,{turn_id:'t'+i,text:i===0?'short':'row '+i,created_at:timestamp(i)})));
  h.view.render(snapshot,'s1');assert.equal(h.host.scrollTop,h.host.scrollHeight-h.host.clientHeight);
  const first=h.outer('m0');first._height=()=>100+first.querySelector('.message-body').textContent.length;h.host.children.slice(1).forEach(row=>row._height=120);
  h.host.scrollTop=300;const anchor=h.outer('m2'),offset=anchor.getBoundingClientRect().top,oldTop=h.host.scrollTop;
  const next=clone(snapshot);next.messages[0].text+='x'.repeat(100);h.view.render(next,'s1');
  assert.equal(h.host.scrollTop,oldTop+100);assert.equal(anchor.getBoundingClientRect().top,offset);
  const appended=clone(next);appended.messages.push(message('new',{turn_id:'new',created_at:timestamp(20)}));const readTop=h.host.scrollTop;h.view.render(appended,'s1');assert.equal(h.host.scrollTop,readTop);
  h.host.scrollTop=h.host.scrollHeight-h.host.clientHeight-90;const thresholdTop=h.host.scrollTop;
  const more=clone(appended);more.messages.push(message('new2',{turn_id:'new2',created_at:timestamp(21)}));h.view.render(more,'s1');assert.equal(h.host.scrollTop,thresholdTop);
  h.host.scrollTop=h.host.scrollHeight-h.host.clientHeight-89;const bottom=clone(more);bottom.messages.push(message('new3',{turn_id:'new3',created_at:timestamp(22)}));h.view.render(bottom,'s1');
  assert.equal(h.host.scrollTop,h.host.scrollHeight-h.host.clientHeight);
});

test('delta scroll policy uses the position at frame time, not the position when queued',()=>{
  const h=harness(),snapshot=detail(Array.from({length:15},(_,i)=>message('m'+i,{turn_id:'t'+i,text:'row '+i,created_at:timestamp(i)})));h.view.render(snapshot,'s1');
  const final=snapshot.messages.at(-1);final.text+='x'.repeat(200);h.view.queueDelta(final);h.host.scrollTop=200;h.clock.flush();assert.equal(h.host.scrollTop,200);
  h.host.scrollTop=h.host.scrollHeight-h.host.clientHeight;final.text+='y'.repeat(200);h.view.queueDelta(final);h.clock.flush();assert.equal(h.host.scrollTop,h.host.scrollHeight-h.host.clientHeight);
});

test('reading inside an expanded turn anchors the inner tool when an earlier assistant grows',()=>{
  const h=harness(),early=message('early',{text:'start',created_at:timestamp(1)}),tools=Array.from({length:10},(_,i)=>message('t'+i,{role:'tool',kind:'commandExecution',text:'command',created_at:timestamp(i+2)}));
  h.view.render(detail([early,...tools,message('final',{created_at:timestamp(20)})],[],{active_turn:'turn'}),'s1');
  const comment=h.inner('early');comment._height=()=>30+comment.textContent.length;tools.forEach(item=>h.inner(item.id)._height=80);
  const anchor=h.inner('t4'),details=anchor.querySelector('details');details.open=true;h.host.scrollTop=anchor.getBoundingClientRect().top+h.host.scrollTop+10;
  const oldTop=h.host.scrollTop,offset=anchor.getBoundingClientRect().top;early.text+='x'.repeat(150);h.view.queueDelta(early);h.clock.flush();
  assert.equal(h.host.scrollTop,oldTop+150);assert.equal(anchor.getBoundingClientRect().top,offset);assert.equal(anchor.querySelector('details'),details);assert.equal(details.open,true);
});

test('manual expansion is shared with the supplied map while default expansion follows active state',()=>{
  const expandedTurns=new Map([['turn',true]]),h=harness({expandedTurns}),snapshot=detail([message('tool',{role:'tool',kind:'plan'})]);
  h.view.render(snapshot,'s1');const group=h.outer('activity:turn'),body=group.querySelector('.activity-body');assert.equal(body.hidden,false);
  group.querySelector('button').click();assert.equal(expandedTurns.get('turn'),false);assert.equal(body.hidden,true);
  const active=clone(snapshot);active.session.active_turn='turn';h.view.render(active,'s1');assert.equal(body.hidden,true);
  expandedTurns.delete('turn');h.view.render(clone(active),'s1');assert.equal(body.hidden,false);
  h.view.render(clone(snapshot),'s1');assert.equal(body.hidden,true);
});

test('reading a turn heading is not dragged to a new inner row inserted above its body',()=>{
  const h=harness(),early=message('early',{created_at:timestamp(1)}),tools=Array.from({length:12},(_,i)=>message('t'+i,{role:'tool',kind:'plan',created_at:timestamp(i+2)})),snapshot=detail([early,...tools,message('final',{created_at:timestamp(20)})],[],{active_turn:'turn'});
  h.view.render(snapshot,'s1');const group=h.outer('activity:turn');h.host.scrollTop=10;const oldOffset=group.getBoundingClientRect().top;
  const next=clone(snapshot);next.messages.unshift(message('inserted',{role:'tool',kind:'commandExecution',created_at:timestamp(0)}));h.view.render(next,'s1');
  assert.equal(h.host.scrollTop,10);assert.equal(group.getBoundingClientRect().top,oldOffset);
});

test('manual scrolling between queue and flush restores the current frame-time keyed anchor',()=>{
  const h=harness(),snapshot=detail(Array.from({length:15},(_,i)=>message('m'+i,{turn_id:'t'+i,text:'row '+i,created_at:timestamp(i)})));h.view.render(snapshot,'s1');
  const first=h.outer('m0');first._height=()=>100+first.querySelector('.message-body').textContent.length;h.host.children.slice(1).forEach(row=>row._height=100);
  snapshot.messages[0].text+='x'.repeat(100);h.view.queueDelta(snapshot.messages[0]);h.host.scrollTop=250;
  const anchor=h.outer('m2'),offset=anchor.getBoundingClientRect().top;h.clock.flush();assert.equal(h.host.scrollTop,350);assert.equal(anchor.getBoundingClientRect().top,offset);
});

test('selection offsets and direction survive reparsing a streaming body',()=>{
  const h=harness(),item=message('final',{text:'**hello** world'});h.view.render(detail([item]),'s1');
  const body=h.outer('final').querySelector('.message-body'),hello=body.querySelector('strong').firstChild,world=body.firstChild.childNodes[1];
  h.doc.selection.setBaseAndExtent(world,3,hello,2);h.doc.selection.restores=0;
  item.text+=' appended';h.view.queueDelta(item);h.clock.flush();
  assert.notEqual(h.doc.selection.anchorNode,world);assert.equal(h.doc.selection.anchorNode.textContent,' world appended');assert.equal(h.doc.selection.anchorOffset,3);
  assert.equal(h.doc.selection.focusNode.textContent,'hello');assert.equal(h.doc.selection.focusOffset,2);assert.equal(h.doc.selection.restores,1);
});

test('selection survives keyed outer movement while untouched Markdown nodes are retained',()=>{
  const h=harness(),snapshot=detail([message('a',{turn_id:'a',text:'selected',created_at:timestamp(1)}),message('b',{turn_id:'b',created_at:timestamp(2)})]);h.view.render(snapshot,'s1');
  const a=h.outer('a'),text=a.querySelector('.message-body').firstChild.firstChild;h.doc.selection.setBaseAndExtent(text,1,text,5);
  const next=clone(snapshot);next.messages[1].created_at=timestamp(0);h.view.render(next,'s1');
  assert.equal(h.host.children[1],a);assert.equal(a.querySelector('.message-body').firstChild.firstChild,text);assert.equal(h.doc.selection.anchorNode,text);assert.equal(h.doc.selection.anchorOffset,1);assert.equal(h.doc.selection.focusOffset,5);assert.equal(h.calls.length,2);
});

test('empty welcome and no-thread note match current semantics and retain unchanged nodes',()=>{
  const h=harness(),empty=detail([],[],{thread_id:null,project_title:'<unsafe>',workspace:'D:/<workspace>'});h.view.render(empty,'s1');
  const welcome=h.host.firstChild,title=welcome.querySelector('h2');assert.equal(welcome.className,'welcome');assert.equal(title.textContent,'<unsafe>');assert.equal(welcome.querySelector('unsafe'),null);assert.equal(h.host.querySelector('.empty-note'),null);
  h.doc.clearWrites();h.view.render(clone(empty),'s1');assert.equal(h.host.firstChild,welcome);assert.equal(welcome.querySelector('h2'),title);assert.deepEqual(h.doc.htmlWrites,[]);assert.deepEqual(h.doc.textWrites,[]);
  const onlyRecords=detail([],[record(researchId)],{thread_id:null});h.view.render(onlyRecords,'s1');const note=h.host.firstChild;
  assert.equal(note.className,'empty-note');assert.equal(note.textContent,'已有任务与程序取证已保存；尚未发起 Codex 研究轮次。可以结合这些资料继续讨论。');assert.equal(h.host.children.length,2);
  h.view.render(clone(onlyRecords),'s1');assert.equal(h.host.firstChild,note);assert.equal(h.calls.length,0);
  h.view.render(detail([message('hidden',{role:'user',kind:'method_envelope'})]),'s1');assert.equal(h.host.children.length,0);
  h.view.render(null,'s1');assert.equal(h.host.children.length,0);
});
