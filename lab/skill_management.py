"""Project-scoped local Skill imports; Codex still discovers and executes them."""
import hashlib
import json
from pathlib import Path
import re
import shutil

from .store import now,uid


def location(store,sid):
    return Path(store.session(sid)['workspace'])/'.agents/skills'


def quiet(store,sid):
    project=store.session(sid)['project_id']
    if store.one("SELECT id FROM sessions WHERE project_id=? AND (active_turn IS NOT NULL OR status IN ('starting','running','compacting','waiting_approval','unknown'))",(project,)):
        raise ValueError('先结束或核对当前研究轮次，再修改项目 Skills；不会热改正在执行的方法')


def import_local(store,sid,source):
    quiet(store,sid)
    raw=Path(source).expanduser()
    if not raw.is_absolute() or raw.is_symlink() or getattr(raw,'is_junction',lambda:False)():
        raise ValueError('请选择真实的本机 Skill 目录绝对路径')
    root=(raw.parent if raw.name=='SKILL.md' else raw).resolve()
    if not (root/'SKILL.md').is_file():raise ValueError('目录中需要 SKILL.md')
    text=(root/'SKILL.md').read_text(encoding='utf-8-sig')
    header=re.match(r'\A---\s*\n(.*?)\n---',text,re.S)
    name=re.search(r'^name:\s*[\'"]?([a-zA-Z0-9_-]{1,64})[\'"]?\s*$',header[1],re.M) if header else None
    if not name or not re.search(r'^description:\s*\S',header[1],re.M):raise ValueError('SKILL.md 需要合法的 name 和 description 元数据')
    directory=location(store,sid);directory.mkdir(parents=True,exist_ok=True)
    if any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in [directory,*directory.parents]):
        raise ValueError('安装目录不能经过链接')
    destination=directory/name[1]
    if destination.exists():raise ValueError('项目中已有同名 Skill，先停用旧副本；不会覆盖历史方法')
    files=[];total=0
    for path in root.rglob('*'):
        if path.is_symlink() or getattr(path,'is_junction',lambda:False)():raise ValueError('Skill 中不能包含链接')
        if not path.is_file():continue
        relative=path.relative_to(root)
        if any(p in {'.git','node_modules','__pycache__','.venv'} for p in relative.parts):continue
        if path.name.lower() in {'.env','auth.json','credentials.json','secrets.json'} or path.suffix.lower() in {'.key','.pem','.pfx','.p12'}:
            raise ValueError('Skill 目录包含凭据或私钥文件，请先整理源目录')
        if len(files)>=512 or total+path.stat().st_size>50*1024*1024:raise ValueError('Skill 超过512文件或50MiB，请精简本机导入目录')
        content=path.read_bytes();total+=len(content)
        files.append((relative,content))
    staging=directory.parent/'skill-staging'/uid();staging.mkdir(parents=True)
    for relative,content in files:
        target=staging/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
    staging.rename(destination)
    receipt={'name':name[1],'directory':str(destination),'path':str(destination/'SKILL.md'),
             'files':len(files),'bytes':total,'sha256':hashlib.sha256(b''.join(str(p).encode()+hashlib.sha256(c).digest() for p,c in files)).hexdigest()}
    store.event(sid,'skill.installed',receipt)
    return receipt


def disable(store,sid,name):
    quiet(store,sid)
    if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',name):raise ValueError('Skill 名称无效')
    raw=location(store,sid)
    if any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in [raw,*raw.parents]):
        raise ValueError('项目 Skills 目录经过链接，不能停用外部方法')
    base=raw.resolve();path=base/name
    if not path.is_dir() or path.is_symlink() or getattr(path,'is_junction',lambda:False)() or path.resolve().parent!=base:
        raise ValueError('只能停用此项目自行导入的 Skill，不能修改全局或平台方法')
    archive=store.root/'disabled-skills'/uid()/name;archive.parent.mkdir(parents=True)
    path.rename(archive)
    store.event(sid,'skill.disabled',{'name':name,'archive':str(archive)})
    return {'disabled':True,'original_preserved':True}
