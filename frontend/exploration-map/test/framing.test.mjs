import test from 'node:test';
import assert from 'node:assert/strict';
import {safeArea,unionBounds,frameBounds,contextFrame} from '../framing.mjs';
const target={x:40,y:160,width:232,height:104};
const safe=safeArea(450,650,{top:32,bottom:42});
function contained(b,v,s){assert(b.x*v.scale+v.tx>=s.x-.01);assert((b.x+b.width)*v.scale+v.tx<=s.x+s.width+.01);assert(b.y*v.scale+v.ty>=s.y-.01);assert((b.y+b.height)*v.scale+v.ty<=s.y+s.height+.01);}
test('safe area reserves measured overlay bounds and excludes invisible dimensions',()=>{assert.deepEqual(safe,{x:14,y:46,width:422,height:548});assert.equal(safeArea(0,640),null);assert.equal(safeArea(450,50,{top:30,bottom:40}),null);});
test('current plus immediate group fits without clipping or moving scientific structure',()=>{const boxes=[target,{x:40,y:340,width:232,height:104}],original=JSON.stringify(boxes),v=contextFrame(boxes,target,safe);assert.equal(v.limited,false);boxes.forEach(b=>contained(b,v,safe));assert.equal(JSON.stringify(boxes),original);});
test('large related group keeps the exact target readable rather than shrinking endlessly',()=>{const v=contextFrame([target,{x:4000,y:9000,width:232,height:104}],target,safe);assert.equal(v.limited,true);assert(v.scale>=.72);contained(target,v,safe);});
test('tiny safe canvas fits the target and explicitly reports small presentation',()=>{const s=safeArea(140,180),v=contextFrame([target],target,s);assert.equal(v.small,true);contained(target,v,s);});
test('invalid boxes and zero-size frames do not manufacture viewports',()=>{assert.equal(unionBounds([]),null);assert.equal(frameBounds(target,null),null);assert.equal(contextFrame([],target,safe),null);assert.equal(frameBounds({...target,width:NaN},safe),null);});
