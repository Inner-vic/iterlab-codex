import test from 'node:test';
import assert from 'node:assert/strict';
import {fileReference,markdown} from '../static/markdown.js';

test('common project files with line/column suffix open the file, retaining readable citation text',()=>{
  for(const value of ['REPORT.md:1','hydraulic_data_audit.json:12','research/lpai-outputs/session/attempt-3/REPORT.md:24:6','结果/诊断.txt:3']){
    const ref=fileReference(value);assert.ok(ref,value);
    assert.match(markdown('`'+value+'`'),new RegExp('data-file="'+ref.path.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'"'));
    assert.ok(markdown('`'+value+'`').includes('<code>'+value+'</code>'));
    assert.equal(ref.path.includes(':'),false);
  }
  assert.match(markdown('[报告](research/REPORT.md:4)'),/data-file="research\/REPORT.md"/);
  assert.match(markdown('`README.md`'),/data-file="README.md"/);
  assert.ok(fileReference('research/files/'+'a'.repeat(64)+':1'));
});

test('file recognition does not turn commands, remote URLs, local absolute paths or traversal into code links',()=>{
  for(const value of ['numpy.mean','pip install numpy','https://example.com/x.md','C:\\private\\report.md','/etc/report.txt','../report.md','research/../report.md','//server/report.md']){
    assert.equal(fileReference(value),null,value);
    assert.doesNotMatch(markdown('`'+value+'`'),/data-file=/);
  }
  const text=markdown('`research/a" onclick="bad.md:1`');
  assert.doesNotMatch(text,/onclick="bad/);assert.match(text,/&quot;/);
});
