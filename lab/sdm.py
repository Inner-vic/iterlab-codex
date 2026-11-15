"""Pinned built-in research contract, with explicit native Skill compatibility."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil

from .store import dumps, now, uid
from .capabilities import catalog, REVISION
from .research_guidance import INSTRUCTIONS as RESEARCH_GUIDANCE

SOURCE=Path(__file__).resolve().parents[1]/'skills/sdm-research'
BUILTIN_SOURCE=Path(__file__).resolve().parents[1]/'methods/research'


def io_path(value):
    """Windows I/O spelling only; keep public paths and content identities stable."""
    path=Path(value)
    text=str(path)
    if os.name=='nt' and path.is_absolute() and not text.startswith('\\\\?\\'):
        return Path('\\\\?\\UNC\\'+text[2:] if text.startswith('\\\\') else '\\\\?\\'+text)
    return path


def files_manifest(directory):
    directory=io_path(directory)
    return {p.relative_to(directory).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob('*')) if p.is_file()}


def publish_snapshot(source,target,manifest,extra=None):
    """Publish only a complete validated directory; concurrent readers never see half a copy."""
    source,target=io_path(source),io_path(target)
    if target.exists():
        if files_manifest(target)!=manifest:raise ValueError('固定方法快照不一致')
        return
    target.parent.mkdir(parents=True,exist_ok=True)
    staging=target.with_name(target.name+'.'+uid()+'.tmp')
    try:
        shutil.copytree(source,staging)
        for name,content in (extra or {}).items():(staging/name).write_bytes(content)
        if files_manifest(staging)!=manifest:raise ValueError('待发布方法快照不一致')
        try:staging.rename(target)
        except OSError:
            # Another writer may have won. Never replace an existing snapshot.
            if not target.exists() or files_manifest(target)!=manifest:raise
    finally:
        if staging.exists():
            if not staging.resolve().is_relative_to(target.parent.resolve()):raise ValueError('拒绝清理方法根目录以外的路径')
            shutil.rmtree(staging)


class SDM:
    def __init__(self,store,default_mode='builtin'):
        if default_mode not in {'builtin','native_skill'}:
            raise ValueError('未知研究方法承载方式')
        self.store=store
        self.default_mode=default_mode
        self.prepared=set()
        self.native_roots={}
        with store.lock,store.db:
            store.db.executescript('''
            CREATE TABLE IF NOT EXISTS research_methods(project_id TEXT PRIMARY KEY REFERENCES projects(id),
              name TEXT NOT NULL,digest TEXT NOT NULL,path TEXT NOT NULL,manifest TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS method_invocations(delivery_id TEXT PRIMARY KEY REFERENCES deliveries(id),
              project_id TEXT NOT NULL,digest TEXT NOT NULL,path TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS research_method_bindings(project_id TEXT PRIMARY KEY REFERENCES projects(id),
              mode TEXT NOT NULL,name TEXT NOT NULL,version TEXT NOT NULL,digest TEXT NOT NULL,
              path TEXT NOT NULL,manifest TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS research_method_changes(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,
              previous TEXT,new_binding TEXT NOT NULL,reason TEXT NOT NULL,authority TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS method_invocation_contracts(delivery_id TEXT PRIMARY KEY REFERENCES deliveries(id),
              mode TEXT NOT NULL,version TEXT NOT NULL,digest TEXT NOT NULL,created_at TEXT NOT NULL);
            INSERT OR IGNORE INTO schema_versions VALUES(3,datetime('now'));
            INSERT OR IGNORE INTO schema_versions VALUES(5,datetime('now'));
            ''')

    def binding(self,session_id):
        session=self.store.session(session_id)
        with self.store.lock:
            row=self.store.one('SELECT * FROM research_method_bindings WHERE project_id=?',(session['project_id'],))
            legacy=self.store.one('SELECT * FROM research_methods WHERE project_id=?',(session['project_id'],))
            if row:
                pass
            elif legacy:
                # Original rows and snapshots remain byte-for-byte historical evidence.
                row={**legacy,'mode':'native_skill','version':'sdm-source-r1'}
            elif self.default_mode=='builtin':
                row=self._builtin(session['project_id'])
                self._save_builtin(row)
            if not row:
                manifest=files_manifest(SOURCE)
                if 'SKILL.md' not in manifest:
                    raise ValueError('SDM原生Skill缺失，未静默降级为普通聊天')
                digest=hashlib.sha256(dumps(manifest).encode()).hexdigest()
                target=self.store.root/'methods'/digest/'sdm-research'
                publish_snapshot(SOURCE,target,manifest)
                if files_manifest(target)!=manifest:
                    raise ValueError('SDM固定方法快照不一致')
                self.store.execute('INSERT INTO research_methods VALUES(?,?,?,?,?,?)',
                    (session['project_id'],'sdm-research',digest,str(target/'SKILL.md'),dumps(manifest),now()))
                row=self.store.one('SELECT * FROM research_methods WHERE project_id=?',(session['project_id'],))
                row={**row,'mode':'native_skill','version':'sdm-source-r1'}
        if files_manifest(Path(row['path']).parent)!=json.loads(row['manifest']):
            raise ValueError('项目已固定的SDM方法被改动；保留原身份，请显式审查升级，不自动换版本')
        return row

    def _builtin(self,project_id):
        manifest=files_manifest(BUILTIN_SOURCE)
        if 'contract.md' not in manifest:
            raise ValueError('内置研究合同缺失，不降级为无约束研究')
        compiled=dumps(catalog())+'\n'
        manifest['capabilities.json']=hashlib.sha256(compiled.encode('utf-8')).hexdigest()
        from . import research_modules
        engine=(dumps({'revision':research_modules.ENGINE_REVISION,
                       'sha256':research_modules.ENGINE_SHA256})+'\n').encode('utf-8')
        manifest['module-engine.json']=hashlib.sha256(engine).hexdigest()
        from .inquiry import policy_bytes
        inquiry=policy_bytes()
        manifest['inquiry-engine.json']=hashlib.sha256(inquiry).hexdigest()
        from .study_integrity import policy_bytes as integrity_policy
        integrity=integrity_policy()
        manifest['integrity-engine.json']=hashlib.sha256(integrity).hexdigest()
        digest=hashlib.sha256(dumps(manifest).encode()).hexdigest()
        target=self.store.root/'methods'/digest/'research'
        publish_snapshot(BUILTIN_SOURCE,target,manifest,{'capabilities.json':compiled.encode('utf-8'),'module-engine.json':engine,'inquiry-engine.json':inquiry,'integrity-engine.json':integrity})
        if files_manifest(target)!=manifest:
            raise ValueError('内置研究合同快照不一致')
        return {'project_id':project_id,'mode':'builtin','name':'iterlab-research','version':REVISION,
                'digest':digest,'path':str(target/'contract.md'),'manifest':dumps(manifest),'created_at':now()}

    def _save_builtin(self,row):
        self.store.execute('''INSERT INTO research_method_bindings VALUES(?,?,?,?,?,?,?,?)
          ON CONFLICT(project_id) DO UPDATE SET mode=excluded.mode,name=excluded.name,version=excluded.version,
          digest=excluded.digest,path=excluded.path,manifest=excluded.manifest,created_at=excluded.created_at''',tuple(row.values()))

    def upgrade(self,session_id,expected_digest,reason,authority):
        if not isinstance(reason,str) or not reason.strip() or not isinstance(authority,str) or not authority.strip():
            raise ValueError('方法升级需要具体理由和授权来源')
        with self.store.lock,self.store.db:
            previous=self.binding(session_id);pid=previous['project_id']
            if previous['digest']!=expected_digest:
                raise ValueError('当前方法版本已变化，请重新核对')
            if self.store.one("SELECT id FROM sessions WHERE project_id=? AND (active_turn IS NOT NULL OR status!='idle')",(pid,)):
                raise ValueError('项目所有会话必须空闲后才能升级研究合同')
            if self.store.one("SELECT d.id FROM deliveries d JOIN sessions s ON s.id=d.session_id WHERE s.project_id=? AND d.state IN ('queued','requested','unknown')",(pid,)):
                raise ValueError('有排队或未知投递，不能热改其方法')
            if self.store.one("SELECT a.id FROM approvals a JOIN sessions s ON s.id=a.session_id WHERE s.project_id=? AND a.state='pending'",(pid,)):
                raise ValueError('有待审批请求，不能升级方法')
            if self.store.one("SELECT name FROM sqlite_master WHERE type='table' AND name='research_runs'") and self.store.one("SELECT id FROM research_runs WHERE project_id=? AND state IN ('requested','running','unknown')",(pid,)):
                raise ValueError('原研究运行未决，不能升级方法')
            current=self._builtin(pid)
            if previous['mode']=='builtin' and previous['digest']==current['digest']:
                return {'changed':False,'binding':previous}
            columns=','.join(current)
            self.store.db.execute('INSERT INTO research_method_bindings('+columns+') VALUES('+','.join('?' for _ in current)+') '
                'ON CONFLICT(project_id) DO UPDATE SET '+','.join(key+'=excluded.'+key for key in current if key!='project_id'),tuple(current.values()))
            change_id=uid()
            self.store.db.execute('INSERT INTO research_method_changes VALUES(?,?,?,?,?,?,?)',
                (change_id,pid,dumps(previous),dumps(current),reason.strip(),authority.strip(),now()))
        self.prepared.clear()
        self.store.event(session_id,'research.method_upgraded',{'id':change_id,'from':previous['digest'],'to':current['digest'],'reason':reason})
        return {'changed':True,'change_id':change_id,'binding':current}

    def instructions(self,session_id,base):
        row=self.binding(session_id)
        if row['mode']=='builtin':
            return base+'\n\n'+io_path(row['path']).read_text(encoding='utf-8')+'\n\n'+RESEARCH_GUIDANCE
        return base+'\n当前项目显式保留原生 $sdm-research 方法；按其参考开展研究，不启动旧协调器。\n\n'+RESEARCH_GUIDANCE

    def available_update(self,session_id):
        current=self.binding(session_id)
        try:
            candidate=self._builtin(current['project_id'])
            return {'available':candidate['digest']!=current['digest'] or current['mode']!='builtin',
                    'mode':'builtin','version':candidate['version'],'digest':candidate['digest']}
        except ValueError as exc:
            return {'available':False,'error':str(exc)}

    def prepare(self,session_id,rpc):
        row=self.binding(session_id)
        key=(rpc.generation,row['project_id'],row['digest'])
        if key not in self.prepared:
            if row['mode']=='builtin':
                if rpc.generation in self.native_roots:
                    roots=self._managed_roots()
                    if roots!=self.native_roots[rpc.generation]:
                        rpc.request('skills/extraRoots/set',{'extraRoots':roots})
                        self.native_roots[rpc.generation]=roots
                self.prepared.add(key)
                self.store.event(session_id,'research.builtin_bound',{'name':row['name'],'version':row['version'],'digest':row['digest'],'skill_required':False})
                return row
            roots=self._managed_roots()
            rpc.request('skills/extraRoots/set',{'extraRoots':roots})
            self.native_roots[rpc.generation]=roots
            response=rpc.request('skills/list',{'cwds':[self.store.session(session_id)['workspace']],'forceReload':True})
            entries=[skill for entry in response.get('data',[]) for skill in entry.get('skills',[])]
            exact=next((s for s in entries if s.get('name')==row['name'] and
                        Path(s.get('path','')).resolve()==Path(row['path']).resolve() and s.get('enabled',True)),None)
            if not exact:
                raise ValueError('Codex没有发现本项目固定的SDM Skill；未发起研究轮次')
            self.prepared.add(key)
            self.store.event(session_id,'sdm.native_bound',{'name':row['name'],'digest':row['digest'],'path':row['path'],'native_discovered':True})
        return row

    def _managed_roots(self):
        # Old rows are immutable history, not proof they remain active bindings.
        rows=self.store.rows("SELECT m.path FROM research_methods m LEFT JOIN research_method_bindings b ON b.project_id=m.project_id WHERE b.project_id IS NULL OR b.mode='native_skill'")
        return sorted({str(Path(row['path']).parent.parent) for row in rows})

    def input(self,session_id,delivery):
        from .autonomy import policy
        p=policy(self.store,self.store.session(session_id)['project_id'])
        policy_note=('\n[当前研究授权策略] 自主模式已由用户显式启用。此策略优先于旧固定方法中的逐版人工审核条款：'
                     '每轮证据评审仍必须完成；后继方案引用research_status中的authority_id/scope_digest，保存后用research_adopt核验。'
                     '人工实质修订按真实输入建立探索分支，普通讨论不分叉。边界外、暂停、未知、缺产物与安全审批不得越过。') if p and p['mode']=='autonomous' else ''
        if policy_note:
            self.store.execute('INSERT OR IGNORE INTO research_delivery_policies VALUES(?,?,?,?)',(delivery['id'],p['authority_id'],policy_note,now()))
            pinned=self.store.one('SELECT suffix FROM research_delivery_policies WHERE delivery_id=?',(delivery['id'],))
            delivery={**delivery,'text':delivery['text']+pinned['suffix']}
        row=self.binding(session_id)
        prior=self.store.one('SELECT * FROM method_invocations WHERE delivery_id=?',(delivery['id'],))
        if prior and (prior['digest']!=row['digest'] or prior['project_id']!=row['project_id']):
            raise ValueError('原投递方法已固定，不能用新版本重放')
        self.store.execute('INSERT OR IGNORE INTO method_invocations VALUES(?,?,?,?,?)',
            (delivery['id'],row['project_id'],row['digest'],row['path'],now()))
        self.store.execute('INSERT OR IGNORE INTO method_invocation_contracts VALUES(?,?,?,?,?)',
            (delivery['id'],row['mode'],row['version'],row['digest'],now()))
        if row['mode']=='builtin':
            return [{'type':'text','text':delivery['text']}]
        return [{'type':'text','text':'$sdm-research\n'+delivery['text']},
                {'type':'skill','name':row['name'],'path':row['path']}]
