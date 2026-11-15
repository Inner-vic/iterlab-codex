// Small, escaped Markdown renderer. Raw HTML is always text; no CDN or eval.
export const escapeHTML=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function fileReference(value){
  if(typeof value!=='string'||!value||/[\\\x00-\x1f]/.test(value))return null;
  const match=/^(.*?)(?::([1-9]\d*)(?::([1-9]\d*))?)?$/.exec(value);
  const path=match?.[1];
  if(!path||path.startsWith('/')||path.includes(':')||path.split('/').some(part=>!part||part==='.'||part==='..'))return null;
  const known=/\.(?:md|txt|log|json|jsonl|csv|tsv|ya?ml|toml|ini|py|sh|ps1|js|ts|xml|html|sql)$/i.test(path)
    ||/(?:^|\/)(?:README|LICENSE|NOTICE)$/i.test(path)||/\/[a-f0-9]{64}$/i.test(path);
  return known?{path,line:match[2]?Number(match[2]):null,column:match[3]?Number(match[3]):null}:null;
}
function inline(value){
  let text=escapeHTML(value);const code=[];
  text=text.replace(/`([^`]+)`/g,(_,s)=>{
    const reference=fileReference(s);
    code.push(reference?`<a href="#" data-file="${reference.path}"><code>${s}</code></a>`:`<code>${s}</code>`);
    return `\u0001${code.length-1}\u0001`;
  });
  text=text.replace(/\[([^\]]+)\]\(([^\s)]+)\)/g,(all,label,url)=>{
    if(/^research:[a-f0-9]{32}$/.test(url))return `<a href="#" data-record="${url.slice(9)}">${label}</a>`;
    if(/^https?:\/\//i.test(url))return `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    if(!/^[a-z]+:/i.test(url)&&!url.startsWith('//'))return `<a href="#" data-file="${fileReference(url)?.path||url}">${label}</a>`;
    return label;
  });
  text=text.replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>').replace(/(?<!\*)\*([^*]+)\*(?!\*)/g,'<em>$1</em>');
  return text.replace(/\u0001(\d+)\u0001/g,(_,n)=>code[Number(n)]);
}
export function markdown(content){
  const lines=String(content??'').replace(/\r/g,'').split('\n');let out=[],paragraph=[],lists=[],code=null,blank=false;
  const flushParagraph=()=>{if(paragraph.length){out.push(`<p>${paragraph.map(inline).join('<br>')}</p>`);paragraph=[];}};
  const closeList=()=>{const list=lists.pop();out.push(`</li></${list.type}>`);};
  const flush=()=>{flushParagraph();while(lists.length)closeList();blank=false;};
  const indentation=line=>(/^[ \t]*/.exec(line)[0].replace(/\t/g,'    ').length);
  const insideList=line=>{const indent=indentation(line);while(lists.length&&indent<=lists.at(-1).indent)closeList();return lists.length>0;};
  for(let i=0;i<lines.length;i++){
    const line=lines[i];
    if(/^\s*```/.test(line)){if(code!==null){out.push(`<pre><code>${escapeHTML(code.join('\n'))}</code></pre>`);code=null;}else{if(!insideList(line))flush();code=[];}continue;}
    if(code!==null){code.push(line);continue;}
    if(!line.trim()){flushParagraph();blank=true;continue;}
    const heading=/^(#{1,6})\s+(.+)$/.exec(line);if(heading){flush();const n=Math.min(heading[1].length+1,6);out.push(`<h${n}>${inline(heading[2])}</h${n}>`);continue;}
    if(i+1<lines.length&&line.includes('|')&&/^\s*\|?\s*:?-{3,}/.test(lines[i+1])){
      flush();const cells=s=>s.trim().replace(/^\||\|$/g,'').split('|').map(v=>v.trim());
      out.push('<table><thead><tr>'+cells(line).map(c=>`<th>${inline(c)}</th>`).join('')+'</tr></thead><tbody>');i++;
      while(i+1<lines.length&&lines[i+1].includes('|'))out.push('<tr>'+cells(lines[++i]).map(c=>`<td>${inline(c)}</td>`).join('')+'</tr>');out.push('</tbody></table>');continue;
    }
    const item=/^([ \t]*)(?:([-*+])|(\d{1,9})[.)])[ \t]+(.*)$/.exec(line);if(item){
      flushParagraph();const indent=indentation(line),type=item[2]?'ul':'ol';
      while(lists.length&&lists.at(-1).indent>indent)closeList();
      if(lists.length&&lists.at(-1).indent===indent&&lists.at(-1).type!==type)closeList();
      if(!lists.length||lists.at(-1).indent<indent){
        // Iterative bounded stack: arbitrary model text cannot exhaust the JS
        // call stack. Keep excessive indentation as text, never raw markup.
        if(lists.length>=32){out.push('<br>'+inline(line.trimStart()));continue;}
        const start=type==='ol'&&Number(item[3])!==1?` start="${Number(item[3])}"`:'';
        out.push(`<${type}${start}>`);lists.push({indent,type});
      }else out.push('</li>');
      out.push(`<li>${inline(item[4])}`);blank=false;continue;
    }
    if(line.startsWith('>')){flush();out.push(`<blockquote>${inline(line.replace(/^>\s?/,''))}</blockquote>`);continue;}
    if(lists.length&&insideList(line)){
      out.push(blank?`<p>${inline(line.trimStart())}</p>`:'<br>'+inline(line.trimStart()));blank=false;continue;
    }
    if(lists.length)flush();paragraph.push(line);blank=false;
  }
  if(code!==null)out.push(`<pre><code>${escapeHTML(code.join('\n'))}</code></pre>`);flush();return out.join('');
}
