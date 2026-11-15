import assert from 'node:assert/strict';
import test from 'node:test';
import {markdown} from '../static/markdown.js';

test('Markdown emphasis and paragraphs are rendered',()=>{
  const html=markdown('**结论**\n\n下一段');
  assert.match(html,/<strong>结论<\/strong>/);
  assert.equal((html.match(/<p>/g)||[]).length,2);
});
test('Raw HTML, code, and unsafe link schemes cannot become executable markup',()=>{
  const html=markdown('<img src=x onerror=alert(1)>\n\n[x](javascript:alert(1))\n\n```\n<script>alert(1)</script>\n```');
  assert.doesNotMatch(html,/<img|<script|href="javascript:/);
  assert.match(html,/&lt;script&gt;/);
});
test('Tables, lists, and relative evidence links are readable',()=>{
  const html=markdown('|指标|数值|\n|---|---|\n|F1|0.6|\n\n- 已执行\n\n[结果](metrics.json)');
  assert.match(html,/<table>/);
  assert.match(html,/<li>已执行<\/li>/);
  assert.match(html,/data-file="metrics.json"/);
});

test('research findings keep nested facts under their bold parent item',()=>{
  const text='关键结果：\n\n- **M2 复算与日志一致**  \n  - Baseline MAE：9.657108 cm\n  - Trial MAE：8.862558 cm\n- **机制判断更新**\n  - 原因仍未知。\n\n下一段';
  const html=markdown(text);
  assert.match(html,/<li><strong>M2 复算与日志一致<\/strong>\s*<ul><li>Baseline MAE：9\.657108 cm<\/li><li>Trial MAE：8\.862558 cm<\/li><\/ul><\/li><li><strong>机制判断更新<\/strong><ul>/);
  assert.ok(html.endsWith('</ul></li></ul><p>下一段</p>'));
});

test('mixed ordered lists preserve starts, nested levels, wrapped text and sibling boundaries',()=>{
  const html=markdown('3. 先检查\n   - 连接\n     1. 端口\n     2. 密钥\n   - 环境\n     接续说明\n4. 再分析');
  assert.match(html,/^<ol start="3"><li>先检查<ul><li>连接<ol><li>端口<\/li><li>密钥<\/li><\/ol><\/li><li>环境<br>接续说明<\/li><\/ul><\/li><li>再分析<\/li><\/ol>$/);
});

test('blank lines and indented fenced code do not flatten or escape their list item',()=>{
  const html=markdown('- 准备\n\n  - 验证\n\n  ```\n  <img onerror=bad>\n  ```\n- 下一项');
  assert.match(html,/<li>准备<ul><li>验证<\/li><\/ul><pre><code>/);
  assert.doesNotMatch(html,/<img/);
  assert.ok(html.endsWith('</code></pre></li><li>下一项</li></ul>'));
});

test('deep generated indentation stays bounded and escaped',()=>{
  const input=Array.from({length:500},(_,i)=>' '.repeat(i*2)+'- <script>'+i+'</script>').join('\n');
  const html=markdown(input);
  assert.equal((html.match(/<ul>/g)||[]).length,32);
  assert.equal((html.match(/<ul>/g)||[]).length,(html.match(/<\/ul>/g)||[]).length);
  assert.doesNotMatch(html,/<script>/);
  assert.match(html,/499/);
});
