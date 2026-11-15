"""Bounded, durable result collection. No model, training, or platform writes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import threading
import time

from .store import dumps, now, uid

LOCAL_TERMINAL={'completed','failed','timed_out','cancelled','invalid_inputs','artifacts_missing'}
WEIGHTS={'.pt','.pth','.ckpt','.safetensors','.onnx'}
MAX_FILE=16*1024*1024
MAX_TOTAL=64*1024*1024
LOG_TAIL=256*1024
MAX_AUTOMATIC_ATTEMPTS=3


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def checksum(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()


def relative(value):
    if not isinstance(value,str) or not value or len(value)>700 or any(c in value for c in '\x00\r\n:'):
        raise ValueError('证据需要明确的运行内相对文件名')
    path=PurePosixPath(value.replace('\\','/'))
    if path.is_absolute() or '..' in path.parts or str(path)=='.':raise ValueError('证据路径不能越过原运行目录')
    return path.as_posix()


class RunEvidence:
    def __init__(self,store):
        self.store=store;self.locks={};self.lock=threading.RLock();self.verified={}
        with store.lock,store.db:
            store.db.executescript('''
            CREATE TABLE IF NOT EXISTS run_evidence(
              id TEXT PRIMARY KEY,location TEXT NOT NULL,run_id TEXT NOT NULL,
              project_id TEXT NOT NULL REFERENCES projects(id),session_id TEXT NOT NULL REFERENCES sessions(id),
              plan_id TEXT NOT NULL REFERENCES records(id),identity TEXT NOT NULL,
              state TEXT NOT NULL,analysis_ready INTEGER NOT NULL DEFAULT 0,analysis_scope TEXT,
              manifest_path TEXT,manifest_digest TEXT,summary_path TEXT,attempts INTEGER NOT NULL DEFAULT 0,
              next_check REAL NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
              UNIQUE(location,run_id));
            INSERT OR IGNORE INTO schema_versions VALUES(15,datetime('now'));
            ''')

    def _lock(self,id):
        with self.lock:return self.locks.setdefault(id,threading.RLock())

    def register_local(self,run):
        """Opt in only new runs (or explicit recovery), never backfill old studies."""
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,159}',run['id']):raise ValueError('运行身份无效')
        session=self.store.session(run['session_id'])
        plan=self.store.one('SELECT * FROM records WHERE id=? AND project_id=?',(run['plan_id'],run['project_id']))
        if not plan or session['project_id']!=run['project_id']:raise ValueError('证据与原项目/方案身份不符')
        identity={'version':1,'location':'local','run_id':run['id'],'project_id':run['project_id'],
            'session_id':run['session_id'],'plan_id':run['plan_id'],'plan_version':plan['version'],
            'plan_snapshot_sha256':fingerprint({k:plan[k] for k in ['id','version','content','evidence']}),
            'operation_id':run['operation_id'],'request_hash':run['request_hash'],'directory':relative(run['directory'])}
        id=hashlib.sha256(('local:'+run['id']).encode()).hexdigest()
        prior=self.store.one('SELECT * FROM run_evidence WHERE id=?',(id,))
        if prior and json.loads(prior['identity'])!=identity:raise ValueError('原回收身份不可改写')
        self.store.execute('''INSERT OR IGNORE INTO run_evidence
            (id,location,run_id,project_id,session_id,plan_id,identity,state,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)''',(id,'local',run['id'],run['project_id'],run['session_id'],run['plan_id'],dumps(identity),'waiting_run',now(),now()))
        return id

    def summary(self,location,run_id):
        row=self.store.one('SELECT * FROM run_evidence WHERE location=? AND run_id=?',(location,run_id))
        if not row:return None
        return {key:row[key] for key in ['id','location','run_id','state','analysis_scope','manifest_path','summary_path','attempts']}|{'analysis_ready':bool(row['analysis_ready'])}

    def _directory(self,row):
        return self.store.safe_path(row['project_id'],'research-evidence/'+row['location']+'/'+row['run_id'])

    def _read_manifest(self,row):
        path=self.store.safe_path(row['project_id'],row['manifest_path'])
        if not path.is_file() or path.is_symlink() or path.stat().st_size>256*1024 or checksum(path)!=row['manifest_digest']:
            raise ValueError('已保存的证据清单缺失或内容改变，未覆盖原快照')
        value=json.loads(path.read_text(encoding='utf-8'))
        if value['identity']!=json.loads(row['identity']):raise ValueError('证据清单与原运行身份不符')
        return value

    def verify(self,location,run_id):
        row=self.store.one('SELECT * FROM run_evidence WHERE location=? AND run_id=?',(location,run_id))
        if not row:raise ValueError('此运行尚未采用统一证据回收')
        # Published manifests/objects never change. Readers need no writer mutex:
        # some callers hold the DB lock, while collectors later need that lock.
        try:
            manifest=self._read_manifest(row);root=self._directory(row)
            for entry in manifest['files']:
                path=root/relative(entry['object'])
                if (path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file()
                        or path.stat().st_size!=entry['size'] or checksum(path)!=entry['sha256']):
                    raise ValueError('归档文件缺失或内容改变：'+entry['name'])
            paths=[self.store.safe_path(row['project_id'],row['manifest_path'])]+[root/entry['object'] for entry in manifest['files']]
            self.verified[row['id']]=(row['manifest_digest'],[(path,self._stamp(path)) for path in paths])
        except (ValueError,OSError,KeyError,json.JSONDecodeError):
            if row['state']!='corrupt':
                self.store.execute("UPDATE run_evidence SET state='corrupt',analysis_ready=0,updated_at=? WHERE id=?",(now(),row['id']))
                self.store.event(row['session_id'],'research.evidence_corrupt',{'run_id':run_id,'location':location,'manifest_path':row['manifest_path']})
            raise ValueError('原归档证据未通过校验，已阻止自动分析；原文件保留，不自动覆盖')
        return manifest

    @staticmethod
    def _stamp(path):
        stat=path.stat()
        return stat.st_size,stat.st_mtime_ns,stat.st_ino,path.is_symlink()

    def _log_tail(self,root,source,name):
        if source.is_symlink() or not source.is_file():raise ValueError('日志原件缺失或为链接')
        size=source.stat().st_size
        with source.open('rb') as stream:
            stream.seek(max(0,size-LOG_TAIL));data=stream.read(LOG_TAIL)
        if len(data)!=min(size,LOG_TAIL) or source.stat().st_size!=size:raise ValueError('日志在回收时改变，保留原身份等待核对')
        digest=hashlib.sha256(data).hexdigest();target=root/'objects'/digest[:24]
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.is_symlink() or not target.resolve().is_relative_to(root):raise ValueError('归档路径无效')
        copied=0
        if target.exists():
            if checksum(target)!=digest:raise ValueError('已保存日志片段被改动，未覆盖')
        else:
            temporary=target.with_name(uid()+'.tmp')
            try:
                temporary.write_bytes(data);temporary.replace(target);copied=len(data)
            finally:
                if temporary.exists():temporary.unlink()
        return {'name':name+'.tail.txt','object':'objects/'+digest[:24],'sha256':digest,'size':len(data),
                'excerpt':True,'source_size':size,'source_offset':max(0,size-LOG_TAIL)},copied

    def _copy(self,root,source,name,expected=None):
        if source.is_symlink() or not source.is_file():raise ValueError('源文件缺失或为链接：'+name)
        size=source.stat().st_size
        if size>MAX_FILE:raise ValueError('超过单文件自动回收范围：'+name)
        digest=expected or checksum(source)
        # Keep filenames short on Windows. Full SHA-256 is always checked;
        # a prefix collision is an error, never permission to overwrite.
        target=root/'objects'/digest[:24]
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.is_symlink() or not target.resolve().is_relative_to(root):raise ValueError('归档路径无效')
        if target.exists():
            if target.stat().st_size!=size or checksum(target)!=digest:raise ValueError('原归档对象已改变，未覆盖')
            return {'name':name,'object':'objects/'+digest[:24],'sha256':digest,'size':size},0
        temporary=target.with_name(uid()+'.tmp');actual=hashlib.sha256();copied=0
        try:
            with source.open('rb') as inp,temporary.open('xb') as out:
                for block in iter(lambda:inp.read(1024*1024),b''):
                    copied+=len(block)
                    if copied>MAX_FILE:raise ValueError('源文件在回收时增长超过范围')
                    actual.update(block);out.write(block)
            if copied!=size or actual.hexdigest()!=digest:raise ValueError('源文件与冻结结果不符：'+name)
            temporary.replace(target)
        finally:
            if temporary.exists():temporary.unlink()
        return {'name':name,'object':'objects/'+digest[:24],'sha256':digest,'size':size},copied

    def collect_local(self,session_id,run_id):
        run=self.store.one('SELECT * FROM research_runs WHERE id=? AND session_id=?',(run_id,session_id))
        if not run:raise ValueError('运行不属于当前会话')
        id=self.register_local(run)
        with self._lock(id):
            row=self.store.one('SELECT * FROM run_evidence WHERE id=?',(id,))
            if row['state']=='corrupt':raise ValueError('原证据损坏，需明确核对，不能覆盖后冒充原始结果')
            if row['state']=='complete':
                self.verify('local',run_id)
                return self.summary('local',run_id)
            if row['analysis_ready']:
                self.verify('local',run_id)
                return self.summary('local',run_id)
            if run['state'] not in LOCAL_TERMINAL or not run['receipt']:return self.summary('local',run_id)
            if row['manifest_path']:self.verify('local',run_id)
            attempt=row['attempts']+1
            self.store.execute("UPDATE run_evidence SET state='collecting',analysis_ready=0,attempts=?,next_check=?,updated_at=? WHERE id=?",(attempt,time.time()+60,now(),id))
            identity=json.loads(row['identity']);root=self._directory(row);root.mkdir(parents=True,exist_ok=True)
            receipt=json.loads(run['receipt']);request=json.loads(run['request'])
            source_root=self.store.safe_path(run['project_id'],run['directory'])
            files=[];issues=[];deferred=[];missing=[];copied=0
            try:
                if receipt.get('run_id')!=run_id or receipt.get('plan_id')!=run['plan_id'] or receipt.get('state')!=run['state']:
                    raise ValueError('原收据与运行身份/终态不一致')
                original=source_root/'receipt.json'
                if original.is_symlink() or original.stat().st_size>256*1024 or json.loads(original.read_text(encoding='utf-8'))!=receipt:
                    raise ValueError('磁盘收据与已保存的原收据不一致')
                candidates=[('receipt.json','receipt',None),('input-manifest.json','inputs',None),('stdout.log','stdout',None),('stderr.log','stderr',None)]
                for name,meta in receipt.get('inputs',{}).items():
                    name=relative(name)
                    if not re.fullmatch('[a-f0-9]{64}',meta.get('sha256','')) or type(meta.get('size')) is not int or meta['size']<0:
                        raise ValueError('冻结输入摘要不完整')
                    if Path(name).suffix.lower() in {'.json','.yaml','.yml','.toml','.ini','.cfg','.py','.md','.txt'} and meta['size']<=256*1024:
                        candidates.append((name,'frozen_input',meta['sha256']))
                prefix=relative(run['directory'])+'/'
                known={}
                for output in receipt.get('outputs',[]):
                    path=relative(output['path'])
                    if not path.startswith(prefix):raise ValueError('产物不在原运行目录')
                    name=path[len(prefix):]
                    if name in known or name in {'receipt.json','input-manifest.json','stdout.log','stderr.log'}:raise ValueError('产物名称重复或覆盖运行收据')
                    if not re.fullmatch('[a-f0-9]{64}',output.get('sha256','')) or type(output.get('size')) is not int or output['size']<0:raise ValueError('原输出摘要不完整')
                    known[name]=output
                for output in request.get('outputs',[]):
                    name=relative(output)
                    if name not in known:missing.append({'name':name,'reason':'original_run_did_not_collect_output'})
                for name,output in known.items():
                    if Path(name).suffix.lower() in WEIGHTS:
                        source=source_root/relative(name)
                        deferred.append({'name':name,'sha256':output['sha256'],'size':output['size'],'source':prefix+name,
                            'source_available':source.resolve().is_relative_to(source_root) and not source.is_symlink() and source.is_file() and source.stat().st_size==output['size'],
                            'verification':'original_receipt_metadata_only','reason':'checkpoint_index_only',
                            'meaning':'原收据记录的定位和摘要，按需核对；本次未复制或重新校验权重内容'})
                    else:candidates.append((name,'output',output['sha256']))
                for name,role,expected in candidates:
                    path=source_root/relative(name)
                    try:
                        if not path.resolve().is_relative_to(source_root):raise ValueError('源文件越过运行目录')
                        if role=='inputs' and (path.is_symlink() or path.stat().st_size>256*1024
                                or json.loads(path.read_text(encoding='utf-8'))!=receipt.get('inputs',{})):
                            raise ValueError('冻结输入清单与原收据不一致')
                        if role in {'stdout','stderr'} and path.stat().st_size>LOG_TAIL:
                            entry,count=self._log_tail(root,path,name);copied+=count
                            files.append({**entry,'role':role,'source':prefix+name});continue
                        if path.stat().st_size+sum(f['size'] for f in files)>MAX_TOTAL:raise ValueError('本次证据超过自动回收范围')
                        entry,count=self._copy(root,path,name,expected);copied+=count
                        if name in known and entry['size']!=known[name]['size']:raise ValueError('原输出大小不一致')
                        files.append({**entry,'role':role,'source':prefix+name})
                    except (ValueError,OSError) as exc:issues.append({'name':name,'reason':str(exc)[:240]})
            except (ValueError,OSError,KeyError,TypeError,json.JSONDecodeError) as exc:
                issues.append({'name':'run_receipt','reason':str(exc)[:240]})
            names={f['name'] for f in files}
            invalid_outputs=[entry['name'] for entry in files if entry['role']=='output' and entry['size']==0]+[entry['name'] for entry in deferred if entry['size']==0]
            successful=run['state'] in {'completed','artifacts_missing'} and not invalid_outputs
            ready=not issues and not missing if successful else ('receipt.json' in names and
                bool(receipt.get('error') or any(f['role'] in {'stdout','stderr'} for f in files)))
            scope='result_analysis' if successful else 'failure_diagnosis'
            state='complete' if not issues and not missing else 'partial'
            if 'receipt.json' not in names:ready=False;state='failed'
            value={'version':1,'identity':identity,'execution_state':run['state'],'execution_receipt_sha256':fingerprint(receipt),
                'state':state,'analysis_ready':bool(ready),'analysis_scope':scope,'files':files,'missing':missing,'issues':issues,
                'deferred':deferred,'invalid_outputs':invalid_outputs,'frozen_inputs':receipt.get('inputs',{}),'trial_id':receipt.get('trial_id'),
                'attempt':attempt,'copied_bytes':copied,'created_at':now(),
                'meaning':'文件回收/校验状态，不认证指标语义、比较公平或研究目标达成'}
            digest=fingerprint(value);folder=root/'manifests';folder.mkdir(exist_ok=True)
            manifest=folder/(str(attempt).zfill(4)+'-'+digest[:16]+'.json')
            body=json.dumps(value,ensure_ascii=False,indent=2).encode('utf-8')
            if len(body)>256*1024:raise ValueError('证据清单超过读取范围，保留原运行并等待缩小回收范围')
            with manifest.open('xb') as stream:stream.write(body)
            summary=manifest.with_suffix('.md')
            label={'completed':'已完成','failed':'执行失败','timed_out':'超时','cancelled':'已取消','invalid_inputs':'输入发生变化','artifacts_missing':'声明产物不完整'}[run['state']]
            text='# 运行证据\n\n'+f"原方案：{identity['plan_version']}；计算状态：{label}。\n\n"
            text+=('已回收结果，可按原评价协议分析。' if ready and successful else '仅支持失败/中断取证，不能据此评价模型有效性。' if ready else '证据尚未齐备，自动分析保持等待。')
            text+='\n\n已保存：'+str(len(files))+'个文件；缺失：'+str(len(missing))+'项；回收问题：'+str(len(issues))+'项。\n'
            if invalid_outputs:text+='\n声明结果为空：'+', '.join(invalid_outputs)+'。文件已留证，但仅用于工程故障诊断，不能分析性能或认证目标。\n'
            for entry in files:text+='\n- ['+entry['name']+'](../'+entry['object']+')'
            if deferred:text+='\n\n检查点仅保留原文件定位、大小和摘要，未复制权重。'
            if any(f.get('excerpt') for f in files):text+='\n\n较大日志仅保存末尾片段，清单记录原件位置和字节范围，不代表覆盖全部训练过程。'
            for entry in missing+issues:
                reason='原运行没有保存这份声明产物，不能把后来补写的文件当作原结果' if entry['reason']=='original_run_did_not_collect_output' else entry['reason']
                text+='\n\n待核对：'+entry['name']+' — '+reason
            text+='\n\n'+value['meaning']+'。\n'
            with summary.open('x',encoding='utf-8') as stream:stream.write(text)
            workspace=Path(self.store.session(session_id)['workspace'])
            self.store.execute('''UPDATE run_evidence SET state=?,analysis_ready=?,analysis_scope=?,manifest_path=?,
                manifest_digest=?,summary_path=?,next_check=?,updated_at=? WHERE id=?''',
                (state,int(ready),scope,manifest.relative_to(workspace).as_posix(),hashlib.sha256(body).hexdigest(),
                 summary.relative_to(workspace).as_posix(),time.time()+60,now(),id))
            self.store.event(session_id,'research.evidence_collected',{'run_id':run_id,'state':state,'analysis_ready':bool(ready),
                'analysis_scope':scope,'manifest_path':manifest.relative_to(workspace).as_posix(),'copied_bytes':copied})
            return self.summary('local',run_id)

    def recover_local(self,session_id=None):
        if not self.store.one("SELECT name FROM sqlite_master WHERE name='research_runs'"):return
        for row in self.store.rows('''SELECT e.id,e.run_id,e.session_id FROM run_evidence e JOIN research_runs r ON r.id=e.run_id
            WHERE e.location='local' AND (? IS NULL OR e.session_id=?) AND e.analysis_ready=0 AND e.state<>'corrupt'
              AND e.attempts<? AND e.next_check<=? AND r.receipt IS NOT NULL
              AND r.state IN ('completed','failed','timed_out','cancelled','invalid_inputs','artifacts_missing')
              ORDER BY e.rowid LIMIT 2''',(session_id,session_id,MAX_AUTOMATIC_ATTEMPTS,time.time())):
            # The timer and a session tick may select the same row. Recheck
            # under the existing collector lock; do not consume two attempts.
            with self._lock(row['id']):
                current=self.store.one('SELECT attempts,state,analysis_ready,next_check FROM run_evidence WHERE id=?',(row['id'],))
                if (current['analysis_ready'] or current['state']=='corrupt'
                        or current['attempts']>=MAX_AUTOMATIC_ATTEMPTS or current['next_check']>time.time()):continue
                try:self.collect_local(row['session_id'],row['run_id'])
                except (ValueError,OSError,KeyError,TypeError):
                    # Even an exception before collect_local's reservation is
                    # an attempt. Preserve any later successful/corrupt state.
                    self.store.execute("""UPDATE run_evidence SET state='failed',analysis_ready=0,
                        attempts=MAX(attempts,?),next_check=?,updated_at=?
                        WHERE id=? AND state<>'corrupt' AND analysis_ready=0""",
                        (current['attempts']+1,time.time()+300,now(),row['id']))

    def blocking(self,project_id,verify_ready=False):
        if verify_ready:
            for row in self.store.rows('SELECT * FROM run_evidence WHERE project_id=? AND analysis_ready=1',(project_id,)):
                # An analysis link is not proof that the archive still exists.
                # Cached file stamps keep unchanged snapshots cheap to recheck.
                cached=self.verified.get(row['id'])
                try:
                    if not cached or cached[0]!=row['manifest_digest'] or any(self._stamp(path)!=stamp for path,stamp in cached[1]):
                        self.verify(row['location'],row['run_id'])
                except OSError:
                    try:self.verify(row['location'],row['run_id'])
                    except ValueError:pass
                except ValueError:pass # verify records corruption; fail closed below.
        return self.store.one("SELECT run_id,state,manifest_path FROM run_evidence WHERE project_id=? AND analysis_ready=0 ORDER BY rowid LIMIT 1",(project_id,))
