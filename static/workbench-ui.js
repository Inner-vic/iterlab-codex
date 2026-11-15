export function resizeComposer(field){
  const top=field.scrollTop||0;
  field.style.height='0px';
  const max=Math.min(innerHeight*.45,360),height=Math.max(64,Math.min(field.scrollHeight,max));
  field.style.height=height+'px';field.style.overflowY=field.scrollHeight>max?'auto':'hidden';
  field.scrollTop=top;
}

const dialogBodies=new WeakMap();
export function prepareDialogLayout(dialog,header=dialog.querySelector('.dialog-header')){
  if(dialogBodies.has(dialog))return dialogBodies.get(dialog);
  if(!header)return null;
  const active=document.activeElement,body=document.createElement('div');body.className='dialog-body';
  // Lift headers out of forms, keeping the original form/input nodes and their
  // listeners intact. Only the body scrolls; the header owns a separate row.
  dialog.prepend(header);
  for(const child of [...dialog.childNodes])if(child!==header)body.append(child);
  dialog.append(body);dialog.classList.add('structured-dialog');dialogBodies.set(dialog,body);
  if(dialog.open&&active&&dialog.contains(active))active.focus({preventScroll:true});
  return body;
}

export function installDialogHeaders(root=document){
  const decorated=new WeakSet();
  function update(){for(const dialog of root.querySelectorAll('dialog')){
    if(decorated.has(dialog))continue;
    const header=dialog.querySelector('.dialog-header');if(!header)continue;
    decorated.add(dialog);
    prepareDialogLayout(dialog,header);
    if(dialog.id==='approval-dialog')continue; // Native approval requires an explicit decision.
    let close=header.querySelector('button');
    if(!close){close=document.createElement('button');header.append(close);close.onclick=()=>dialog.close();}
    close.type='button';close.classList.add('icon-button','modal-close');close.setAttribute('aria-label','关闭');close.title='关闭';
    close.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6"/></svg>';
  }}
  update();new MutationObserver(update).observe(document.body,{childList:true,subtree:true});
}

export function installWorkbenchUI(){
  installDialogHeaders();
  const fileDialog=document.querySelector('#file-dialog'),dock=document.querySelector('#tab-files'),expanded=document.querySelector('#files-expanded');
  document.querySelector('#expand-files').onclick=()=>{while(dock.firstChild)expanded.append(dock.firstChild);fileDialog.showModal();};
  fileDialog.addEventListener('close',()=>{while(expanded.firstChild)dock.append(expanded.firstChild);});
  const prompt=document.querySelector('#prompt');prompt.addEventListener('input',()=>resizeComposer(prompt));window.addEventListener('resize',()=>resizeComposer(prompt));
  resizeComposer(prompt);
}
