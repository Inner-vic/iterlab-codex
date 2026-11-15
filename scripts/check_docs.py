"""Check local Markdown links against the distributable source, not private disk files."""
import json
import posixpath
import re
import sys
from pathlib import Path
from urllib.parse import unquote,urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.source_bundle import source_files

def audit(root,names):
    available=set(names);issues=[];checked=0
    for name in sorted(available):
        if not name.endswith('.md'):continue
        text=(root/name).read_text(encoding='utf-8-sig')
        text=re.sub(r'```[^\n]*\n.*?```',lambda m:'\n'*m[0].count('\n'),text,flags=re.S)
        for match in re.finditer(r'\[[^\]\n]*\]\(([^\n]*?)\)',text):
            target=match[1].strip().split(' "',1)[0].strip('<>')
            url=urlsplit(target)
            if not target or target.startswith('#') or url.scheme or url.netloc:continue
            checked+=1
            path=posixpath.normpath(posixpath.join(posixpath.dirname(name),unquote(url.path)))
            if path not in available and not any(n.startswith(path.rstrip('/')+'/') for n in available):
                issues.append({'file':name,'line':text.count('\n',0,match.start())+1,'target':target,'reason':'missing_or_not_distributed'})
    return {'markdown_files':sum(n.endswith('.md') for n in available),'local_links':checked,'issues':issues}

if __name__=='__main__':
    result=audit(ROOT,source_files(ROOT))
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(bool(result['issues']))
