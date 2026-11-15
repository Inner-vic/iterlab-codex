const states=new Set(['queued','requested','accepted','unknown','rejected','cancelled']);
const knownStates=new Set(['queued','requested','accepted','rejected','cancelled']);

export function sameComposition(a,b){
  return a.sid===b.sid&&a.selection===b.selection&&a.revision===b.revision&&a.prompt===b.prompt&&
    JSON.stringify(a.attachments)===JSON.stringify(b.attachments);
}

export function createDeliverySender({api,snapshot,commit,onChange=()=>{},storage=localStorage,uuid=()=>crypto.randomUUID()}){
  const attempts=new Map(),captures=new Map(),prefix='iterlab-codex:send:';let inFlight=false;
  function pending(sid){
    if(!sid)return null;
    if(attempts.has(sid))return attempts.get(sid);
    const raw=storage.getItem(prefix+sid);if(!raw)return null;
    let saved;try{saved=JSON.parse(raw);}catch{throw Error('原发送记录无法读取；请保留浏览器数据并核对原会话，不要重发');}
    if(saved.sid!==sid||typeof saved.id!=='string'||!saved.id||typeof saved.text!=='string'||!['queue','steer'].includes(saved.mode))
      throw Error('原发送身份不完整；请核对原会话，不要用新ID替代');
    attempts.set(sid,saved);return saved;
  }
  function remember(attempt,required=false){
    attempts.set(attempt.sid,attempt);
    try{storage.setItem(prefix+attempt.sid,JSON.stringify(attempt));}
    catch(error){if(required){attempts.delete(attempt.sid);throw Error('无法保存原发送ID；本次尚未提交，请检查浏览器存储');}}
    onChange();
  }
  function forget(attempt){
    // Remove durable tracking before permitting another identity. A storage
    // failure keeps the original attempt blocked in this page too.
    storage.removeItem(prefix+attempt.sid);attempts.delete(attempt.sid);captures.delete(attempt.id);onChange();
  }
  function exact(row,attempt){
    if(!row||row.id!==attempt.id||row.session_id!==attempt.sid||row.text!==attempt.text||row.mode!==attempt.mode||!states.has(row.state))
      throw Error('返回记录与原消息身份不一致；保留原ID和草稿，不重发');
  }
  async function observe(attempt,row){
    exact(row,attempt);attempt.native_state=row.state;attempt.state=knownStates.has(row.state)?'registered':'unknown';
    remember(attempt);
    let cleared=false;
    if(['queued','requested','accepted'].includes(row.state)&&captures.has(attempt.id))
      cleared=await commit(captures.get(attempt.id));
    if(cleared)forget(attempt);
    return {delivery:row,cleared};
  }
  async function send(mode='queue'){
    if(inFlight)return {duplicate:true};
    const captured=snapshot();if(!captured.sid)return {empty:true};
    if(pending(captured.sid))throw Error('有尚待核对的原发送记录；先只读核对原请求，不要重复发送');
    if(!['queue','steer'].includes(mode))throw Error('未知投递方式');
    let text=captured.prompt.trim();if(!text&&!captured.attachments.length)return {empty:true};
    if(captured.attachments.length)text+='\n\n用户提供的项目附件（工作区相对路径）：\n'+captured.attachments.map(a=>`- ${a.path}`).join('\n');
    const attempt={sid:captured.sid,id:uuid(),text,mode,prompt:captured.prompt,attachments:captured.attachments,state:'unconfirmed'};
    // The guard and original identity are established before the first await.
    inFlight=true;captures.set(attempt.id,captured);
    try{
      remember(attempt,true);
      const row=await api(`/api/sessions/${attempt.sid}/send`,{id:attempt.id,text,mode});
      return await observe(attempt,row);
    }catch(error){
      const proof=error.delivery;
      if(proof?.id===attempt.id&&proof.session_id===attempt.sid&&proof.state==='not_registered'){
        forget(attempt);throw error;
      }
      // A missing lookup, HTTP status, or timeout alone cannot prove rejection.
      if(attempts.has(attempt.sid)){
        remember({...attempt,state:'unconfirmed',error:error.message});
        throw Error('发送结果尚未确认；原请求ID和草稿已保留，请只读核对原请求，不要重发',{cause:error});
      }
      captures.delete(attempt.id);throw error;
    }finally{inFlight=false;onChange();}
  }
  async function inspect(sid){
    if(inFlight)throw Error('原HTTP请求仍在等待，暂不发起其他核对');
    const attempt=pending(sid);if(!attempt)return null;
    inFlight=true;onChange();
    try{return await observe(attempt,await api(`/api/sessions/${sid}/deliveries/${encodeURIComponent(attempt.id)}`));}
    finally{inFlight=false;onChange();}
  }
  function release(sid,confirmed){
    if(inFlight)throw Error('原请求尚在等待');
    const attempt=pending(sid);if(!attempt)return;
    if(confirmed!==true||!knownStates.has(attempt.native_state)||attempt.state!=='registered')
      throw Error('没有确切登记状态，不能结束未知请求跟踪或把它当失败');
    forget(attempt);
  }
  return {send,inspect,release,pending,busy:()=>inFlight};
}
