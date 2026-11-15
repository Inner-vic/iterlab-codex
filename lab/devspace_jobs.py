"""Durable SSH jobs. Polling reads one original job, never repeats a launch."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid

from . import devspace
from .store import dumps,now


class DevspaceJobs:
    def __init__(self,research):
        self.research=research;self.store=research.store;self.lock=threading.RLock()
        with self.store.lock,self.store.db:
            self.store.db.executescript('''
            CREATE TABLE IF NOT EXISTS devspace_jobs(
              run_id TEXT PRIMARY KEY REFERENCES research_runs(id),scope TEXT NOT NULL,job_digest TEXT NOT NULL,
              context TEXT NOT NULL,state TEXT NOT NULL,next_check REAL NOT NULL,failures INTEGER NOT NULL,
              message TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            INSERT OR IGNORE INTO schema_versions VALUES(21,datetime('now'));
            ''')

    def _transport(self,args,directory,mode,run_id,scope,fingerprint):
        options={'stdin':subprocess.DEVNULL}
        source=None
        try:
            if mode=='submit':
                source=(directory/'.iterlab-input.tar').open('rb');options['stdin']=source
            # Only this short transport is owned locally. The remote supervisor
            # persists beyond SSH, Codex interruption, and host service restart.
            response=subprocess.run([*args,devspace.remote_command(mode,run_id,scope['uid'],fingerprint)],
                capture_output=True,timeout=self.upload_timeout(directory) if mode=='submit' else 25,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,**options)
            if response.returncode:raise ValueError(devspace.ssh_error(response))
            if len(response.stdout)>devspace.PROTOCOL_LIMIT:raise ValueError('远端回执超过自动回收范围')
            value=json.loads(response.stdout)
            if (value.get('run_id')!=run_id or value.get('space_uid')!=scope['uid'] or value.get('job_digest')!=fingerprint
                or value.get('state') not in {'running','unknown','completed','failed','timed_out','invalid_inputs'}):
                raise ValueError('原远端任务身份或回执未确认')
            return value
        finally:
            if source:source.close()

    @staticmethod
    def upload_timeout(directory):
        # Transfer is distinct from the approved remote command clock. A fixed
        # two-minute timeout is too short for real sensor datasets over VPN.
        return min(3600,max(120,int((directory/'.iterlab-input.tar').stat().st_size/(512*1024))+60))

    def launch(self,run,scope,manifest,context,args):
        directory=self.store.safe_path(run['project_id'],run['directory']);request=json.loads(run['request'])
        fingerprint=devspace.stage(directory,run['id'],scope,manifest,request['script'],request.get('args',[]),
            request.get('outputs',[]),context['timeout'],detached=True)
        stamp=now()
        with self.research.lock:
            if self.research.closing or self.research.workflow.paused(run['session_id']):raise InterruptedError('尚未登记远端提交，Codex已暂停')
            self.store.execute('INSERT INTO devspace_jobs VALUES(?,?,?,?,?,?,?,?,?,?)',
                (run['id'],dumps(scope),fingerprint,dumps(context),'launching',time.time()+self.upload_timeout(directory)+30,0,'原提交已登记，结果未知时仅查原身份',stamp,stamp))
        self.store.event(run['session_id'],'research.run_started',{'run_id':run['id'],'plan_id':run['plan_id'],
            'purpose':request['purpose'],'timeout':context['timeout'],'execution':'lpai_devspace'})
        try:
            value=self._transport(args,directory,'submit',run['id'],scope,fingerprint)
            with self.lock:self._observe(run,value)
        except (OSError,ValueError,KeyError,subprocess.SubprocessError):
            self._defer(run['id'],'SSH提交回执未确认；后台只核对原远端运行，不重新提交')
        self.research.workflow.publish(run['session_id'])
        current=self.store.one('SELECT state,receipt FROM research_runs WHERE id=?',(run['id'],))
        return json.loads(current['receipt']) if current['receipt'] else {'run_id':run['id'],'state':current['state'],
            'execution':'lpai_devspace','remote_directory':'/lpai/iterlab-runs/'+run['id'],
            'message':'后台跟踪原运行；结束后回收产物并接续原会话，无需模型轮询。'}

    def _defer(self,id,message):
        row=self.store.one('SELECT failures FROM devspace_jobs WHERE run_id=?',(id,))
        failures=row['failures']+1
        self.store.execute("UPDATE devspace_jobs SET state='unknown',next_check=?,failures=?,message=?,updated_at=? WHERE run_id=?",
            (time.time()+min(300,30*2**min(failures-1,4)),failures,message,now(),id))
        self.store.execute("UPDATE research_runs SET state='unknown',updated_at=? WHERE id=? AND receipt IS NULL",(now(),id))

    def poll(self):
        if self.research.closing or not self.lock.acquire(blocking=False):return
        try:
            # Bound work per pass. Original intent is durable before any SSH.
            for job in self.store.rows("SELECT * FROM devspace_jobs WHERE state<>'collected' AND next_check<=? ORDER BY next_check LIMIT 1",(time.time(),)):
                run=self.store.one('SELECT * FROM research_runs WHERE id=?',(job['run_id'],))
                try:
                    if run['receipt']:
                        self._finish(run,json.loads(run['receipt']));continue
                    scope=json.loads(job['scope'])
                    args=devspace.connection(self.research.company,run['project_id'],scope)
                    value=self._transport(args,self.store.safe_path(run['project_id'],run['directory']),
                        'status',run['id'],scope,job['job_digest'])
                    self._observe(run,value)
                except (OSError,ValueError,KeyError,subprocess.SubprocessError):
                    self._defer(run['id'],'原远端运行或产物暂未核对成功；保留身份并延后只读查询，不重投')
        finally:self.lock.release()

    def _observe(self,run,value):
        id=run['id'];state=value['state']
        if state in {'running','unknown'}:
            self.store.execute('UPDATE devspace_jobs SET state=?,next_check=?,failures=0,message=?,updated_at=? WHERE run_id=?',
                (state,time.time()+(30 if state=='running' else 60),'后台等待原运行' if state=='running' else '原远端进程终态未知，不重放',now(),id))
            self.store.execute('UPDATE research_runs SET state=?,updated_at=? WHERE id=?',(state,now(),id));return
        job=self.store.one('SELECT * FROM devspace_jobs WHERE run_id=?',(id,));scope=json.loads(job['scope']);context=json.loads(job['context'])
        directory=self.store.safe_path(run['project_id'],run['directory']);request=json.loads(run['request'])
        response=directory/'.iterlab-response.json'
        self._immutable(response,json.dumps(value,separators=(',',':')).encode())
        state,exit_code=devspace.collect(directory,id,scope['uid'],request.get('outputs',[]),idempotent=True)
        outputs=[];missing=[];empty=[];error=None
        for name in request.get('outputs',[]):
            path=directory/name
            if path.is_symlink() or path.resolve()!=path or not path.is_file():missing.append(name);continue
            size=path.stat().st_size
            outputs.append({'path':run['directory']+'/'+name,'size':size,'sha256':self._hash(path)})
            if not size:empty.append(name)
        if state=='completed' and (missing or empty):
            state='artifacts_missing';error='远端退出0，但声明产物缺失或为空'
        if any((directory/name).is_symlink() or not (directory/name).is_file() or self._hash(directory/name)!=item['sha256'] for name,item in context['inputs'].items()):
            state='invalid_inputs';error='本地冻结快照改变，不能用于性能结论'
        receipt={'run_id':id,'plan_id':run['plan_id'],'plan_version':context['plan_version'],'state':state,'exit_code':exit_code,
            'directory':run['directory'],'purpose':request['purpose'],'seconds':value.get('training_seconds'),
            'outputs':outputs,'inputs':context['inputs'],'python':'remote python3','threads':1,'trial_id':context.get('trial_id'),
            'error':error,'missing_outputs':missing,'empty_outputs':empty,'artifact_errors':[],'ownership_errors':[],
            'verified_dependencies':context['dependencies'],'research_quality':'未判定；退出0不等于研究目标通过',
            'execution':'lpai_devspace','devspace':{k:scope[k] for k in ('uid','name','namespace','gpus')},
            'remote_directory':'/lpai/iterlab-runs/'+id,'job_digest':job['job_digest'],
            'timing':{'wait_limit_seconds':context['timeout'],'timeout_observed':state=='timed_out',
                'limit_scope':'remote_training_only; SSH and artifact transfers are separate'}}
        if context.get('basis_record_id'):receipt['basis_record_id']=context['basis_record_id']
        # Store the immutable terminal receipt first. Recovery after a crash
        # reuses these exact bytes, not a new status read or training attempt.
        self.store.execute('UPDATE research_runs SET state=?,receipt=?,updated_at=? WHERE id=?',
            (state,dumps(receipt),now(),id))
        self._finish(run,receipt)

    @staticmethod
    def _hash(path):
        value=hashlib.sha256()
        with path.open('rb') as f:
            for chunk in iter(lambda:f.read(1024*1024),b''):value.update(chunk)
        return value.hexdigest()

    @staticmethod
    def _immutable(path,body):
        if path.is_symlink() or path.resolve()!=path:raise ValueError('回收路径包含链接')
        if path.exists():
            if path.read_bytes()!=body:raise ValueError('已回收原件改变；不覆盖、不重投')
            return
        # Publish complete bytes without overwriting an existing result, even
        # if another collector or a user writes between the existence checks.
        tmp=path.with_name('.iterlab-collect-'+uuid.uuid4().hex+'.tmp')
        try:
            with tmp.open('xb') as f:f.write(body);f.flush();os.fsync(f.fileno())
            try:os.link(tmp,path)
            except FileExistsError:
                if path.is_symlink() or path.read_bytes()!=body:raise ValueError('原件已变化，不覆盖')
        finally:
            if tmp.exists():tmp.unlink()

    def _finish(self,run,receipt):
        id=run['id'];sid=run['session_id'];directory=self.store.safe_path(run['project_id'],run['directory'])
        self._immutable(directory/'receipt.json',json.dumps(receipt,ensure_ascii=False,indent=2).encode())
        self.store.evidence.collect_local(sid,id)
        with self.store.lock,self.store.db:
            prior=self.store.db.execute("SELECT state FROM devspace_jobs WHERE run_id=?",(id,)).fetchone()
            if prior['state']=='collected':return
            self.store.db.execute("UPDATE devspace_jobs SET state='collected',message='原终态与结果已保存',updated_at=? WHERE run_id=?",(now(),id))
            self.store.db.execute('INSERT INTO events(session_id,kind,payload,created_at) VALUES(?,?,?,?)',
                (sid,'research.run_finished',dumps({'run_id':id,'state':receipt['state'],'receipt':run['directory']+'/receipt.json'}),now()))
        with self.store.changed:self.store.changed.notify_all()
        if not self.research.workflow.paused(sid):
            self.research.focus(sid,'assess','原开发空间运行终态与本地产物已保存，需分析实际结果',receipt.get('trial_id') or run['plan_id'])
        self.research.workflow.publish(sid)
