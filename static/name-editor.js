// Shared name-only editor. The target is captured when opened, not inferred
// from whichever chat happens to be selected when the save returns.
export function installNameEditor({id,heading,label,onSave,onSaved=()=>{}}){
  const dialog=document.createElement('dialog');dialog.id=id;dialog.className='name-editor';
  dialog.innerHTML='<div class="dialog-header"><h2></h2></div><form><label><span></span><input name="title" required maxlength="160" autocomplete="off"></label><p class="project-dialog-error" role="alert" hidden></p><div class="dialog-actions"><button type="button" class="secondary" data-cancel>取消</button><button type="submit" class="primary">保存</button></div></form>';
  dialog.querySelector('h2').textContent=heading;dialog.querySelector('label span').textContent=label;
  document.body.append(dialog);
  const form=dialog.querySelector('form'),input=dialog.querySelector('input'),error=dialog.querySelector('[role=alert]'),save=dialog.querySelector('[type=submit]');
  let target=null,saving=false;
  dialog.querySelector('[data-cancel]').onclick=()=>dialog.close();
  form.onsubmit=async event=>{
    event.preventDefault();if(saving||!target)return;
    const title=input.value.trim();error.hidden=true;
    if(!title||title.length>160){error.textContent='名称需要填写1至160个字符。';error.hidden=false;input.focus();return;}
    const captured=target;
    if(title===captured.title){dialog.close();return;}
    saving=true;save.disabled=true;
    try{await onSave(captured,title);if(target===captured)dialog.close();await onSaved(captured);}
    catch(cause){if(target===captured){error.textContent=cause.message;error.hidden=false;}}
    finally{saving=false;save.disabled=false;}
  };
  return {open(value){if(saving)return;target={id:value.id,title:value.title};input.value=target.title;error.hidden=true;dialog.showModal();input.focus();input.select();}};
}
