import test from 'node:test';
import assert from 'node:assert/strict';
import {projectMenuItems} from '../static/project-removal.js';
test('Project actions separate pin, edit, archive chats and recoverable removal',()=>{
  assert.deepEqual(projectMenuItems({pinned:false}).map(item=>item.label),['置顶','编辑','归档聊天','删除项目']);
  assert.equal(projectMenuItems({pinned:true})[0].label,'取消置顶');
  assert.deepEqual(projectMenuItems({}).map(item=>item.action),['pin','rename','archive_chats','remove']);
});
