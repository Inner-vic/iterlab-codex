export function installSlashMenu({input,host,commands,onCommand}){
  let selected=0,items=[];
  function paint(){
    host.replaceChildren();host.setAttribute('role','listbox');
    for(const [index,[command,note]] of items.entries()){
      const button=document.createElement('button');button.type='button';button.dataset.command=command;
      button.setAttribute('role','option');button.setAttribute('aria-selected',String(index===selected));
      const title=document.createElement('strong'),description=document.createElement('small');
      title.textContent=command;description.textContent=note;button.append(title,description);
      button.onmousedown=e=>e.preventDefault();button.onclick=()=>{host.hidden=true;onCommand(command);};host.append(button);
    }
    host.hidden=!items.length;
  }
  function update(){
    const text=input.value.trim();selected=0;
    if(!/^\/[a-z]*$/i.test(text)){host.hidden=true;items=[];return;}
    items=commands.filter(([command])=>command.startsWith(text.toLowerCase()));paint();
  }
  function keydown(e){
    if(host.hidden||e.isComposing||!items.length)return false;
    if(e.key==='Escape'){host.hidden=true;e.preventDefault();return true;}
    if(e.key==='ArrowDown'||e.key==='ArrowUp'){
      selected=(selected+(e.key==='ArrowDown'?1:items.length-1))%items.length;paint();e.preventDefault();return true;
    }
    if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();host.hidden=true;onCommand(items[selected][0]);return true;}
    return false;
  }
  return {update,keydown};
}
