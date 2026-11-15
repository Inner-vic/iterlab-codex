// Display geometry only: no topology, record state or selection changes.
const valid=b=>b&&[b.x,b.y,b.width,b.height].every(Number.isFinite)&&b.width>0&&b.height>0;
export function unionBounds(boxes=[]){
  const values=boxes.filter(valid);if(!values.length)return null;
  const x=Math.min(...values.map(b=>b.x)),y=Math.min(...values.map(b=>b.y));
  return {x,y,width:Math.max(...values.map(b=>b.x+b.width))-x,height:Math.max(...values.map(b=>b.y+b.height))-y};
}
export function safeArea(width,height,{top=0,bottom=0,padding=14}={}){
  if(!Number.isFinite(width)||!Number.isFinite(height)||width<=0||height<=0)return null;
  const box={x:padding,y:Math.max(padding,top+padding),width:width-2*padding,height:height-Math.max(padding,top+padding)-Math.max(padding,bottom+padding)};
  return valid(box)?box:null;
}
export function frameBounds(box,safe,{minScale=.04,maxScale=1}={}){
  if(!valid(box)||!valid(safe))return null;
  const scale=Math.min(maxScale,safe.width/box.width,safe.height/box.height);
  // Never clamp above the actual fit: even a small canvas must not clip the target.
  if(!Number.isFinite(scale)||scale<.04)return null;
  return {scale,tx:safe.x+safe.width/2-(box.x+box.width/2)*scale,ty:safe.y+safe.height/2-(box.y+box.height/2)*scale};
}
export function contextFrame(boxes,target,safe,{minReadable=.72,maxScale=1}={}){
  const combined=frameBounds(unionBounds(boxes),safe,{maxScale});if(!valid(target)||!valid(safe)||!boxes.some(valid))return null;
  if(combined&&combined.scale>=minReadable)return {...combined,limited:false};
  const primary=frameBounds(target,safe,{maxScale});
  return primary?{...primary,limited:boxes.length>1,small:primary.scale<minReadable}:null;
}
